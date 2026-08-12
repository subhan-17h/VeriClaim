"""What the graph carries from node to node.

The state is the only thing every node shares, which makes it the only place where one
mistake propagates silently to all of them. The reference implementation's was an
unvalidated ``TypedDict``: a node writing a misspelled key simply added it, a node reading
one that had never been written raised a ``KeyError`` three nodes later, and nothing in the
error said which node was actually at fault.

Here it is a validated model, and three properties follow from that:

* **A node cannot invent a field.** Writing something the state does not declare fails
  where it was written.
* **Nothing is mutated in place.** Four source tools run concurrently; a shared mutable
  state is a race that shows up as evidence going missing under load and never in a test.
  Every update returns a new state.
* **Accumulating things have one way to grow.** Evidence, cost, latency and the stage
  trace each have a single method that appends, so a node cannot overwrite what a sibling
  contributed by assigning where it meant to add.

Evidence lives in an :class:`~vericlaim.evidence.EvidenceSet` rather than a list, so ids
are assigned once and never move. ``[E3]`` has to mean the same passage when synthesis
writes it and when verification checks it, and a list that each node re-sorted for its own
convenience would break that quietly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vericlaim.evidence import Evidence, EvidenceSet

# The bound on the sufficiency loop. Two replans is enough to recover from a router that
# missed a source; a third is the graph arguing with itself at the cost of a full fan-out
# each time round.
MAX_REPLANS = 2


class StageRecord(BaseModel):
    """One node's run: what it cost, how long it took, and whether it worked.

    A failed stage is recorded rather than raised. One source being unavailable is a gap
    in the evidence, not the end of the question, and the answer has to be able to say
    which source it could not reach.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    detail: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


class RoutingDecision(BaseModel):
    """Which sources the question needs, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    out_of_scope: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""


class GraphState(BaseModel):
    """Everything one question accumulates on its way to an answer."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", arbitrary_types_allowed=True
    )

    question: str
    understanding: dict[str, Any] = Field(default_factory=dict)
    routing: RoutingDecision | None = None
    plans: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceSet = Field(default_factory=EvidenceSet)
    sufficiency: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    citations: dict[str, Any] = Field(default_factory=dict)
    stages: tuple[StageRecord, ...] = ()
    replans: int = 0
    trace_id: str = ""

    @field_validator("question")
    @classmethod
    def _question_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A run needs a question to answer")
        return value.strip()

    # -- accumulation ------------------------------------------------------

    def with_evidence(self, evidence: Iterable[Evidence]) -> GraphState:
        """Return a state whose evidence set also holds ``evidence``.

        The set is copied rather than extended in place: two source tools finishing at the
        same moment must not race for the id counter.
        """
        merged = EvidenceSet(list(self.evidence.items))
        merged.extend(list(evidence))
        return self.model_copy(update={"evidence": merged})

    def with_stage(self, stage: StageRecord) -> GraphState:
        return self.model_copy(update={"stages": (*self.stages, stage)})

    def replanned(self) -> GraphState:
        return self.model_copy(update={"replans": self.replans + 1})

    def with_(self, **fields: Any) -> GraphState:
        """Return a state with ``fields`` replaced.

        Named awkwardly on purpose: appending is what nodes normally want, and a general
        setter that reads naturally would invite a node to assign where it meant to add.
        """
        return self.model_copy(update=fields)

    # -- reading -----------------------------------------------------------

    @property
    def total_cost_usd(self) -> float:
        return sum(stage.cost_usd for stage in self.stages)

    @property
    def total_latency_ms(self) -> float:
        return sum(stage.latency_ms for stage in self.stages)

    @property
    def failures(self) -> tuple[str, ...]:
        """Every stage that could not complete, named with its reason.

        Surfaced rather than buried: an answer built while one source was unreachable is
        not wrong, but it is incomplete in a way the reader has to be told about.
        """
        return tuple(
            f"{stage.name}: {stage.error}" for stage in self.stages if stage.failed
        )

    @property
    def can_replan(self) -> bool:
        return self.replans < MAX_REPLANS

    @property
    def sources_used(self) -> tuple[str, ...]:
        return tuple(self.evidence.source_types())

    def to_dict(self) -> dict[str, Any]:
        """The whole run, in the shape the API returns and the UI renders."""
        return {
            "question": self.question,
            "understanding": dict(self.understanding),
            "routing": self.routing.model_dump() if self.routing else None,
            "plans": dict(self.plans),
            "evidence": self.evidence.serialize(),
            "sources_used": list(self.sources_used),
            "sufficiency": dict(self.sufficiency),
            "answer": self.answer,
            "citations": dict(self.citations),
            "stages": [stage.model_dump() for stage in self.stages],
            "failures": list(self.failures),
            "replans": self.replans,
            "cost_usd": self.total_cost_usd,
            "latency_ms": self.total_latency_ms,
            "trace_id": self.trace_id,
        }


def merge_evidence(
    left: EvidenceSet, right: Sequence[Evidence]
) -> EvidenceSet:
    """Combine evidence from a concurrent branch into a set, preserving ids.

    LangGraph merges the states its parallel branches return; this is the reducer that
    does it for evidence, so two branches finishing together produce one set with stable
    ids rather than two lists whose ``[E1]`` mean different things.
    """
    merged = EvidenceSet(list(left.items))
    merged.extend(list(right))
    return merged
