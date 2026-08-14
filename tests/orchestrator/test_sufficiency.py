"""Deciding whether what came back is enough, and whether to go round again.

Two judgements, and the order between them is the design. A gap that can be *counted* --
a source that was asked and said nothing, a source that could not be reached -- is found
by arithmetic over the collection account, and no model is consulted about it. Only when
every planned sub-goal produced something does the question of whether that something
actually answers the question go to a model.

That order is not only about cost. A model asked "is this enough" while looking at
evidence with a hole in it tends to answer from the part it can see, and the missing
source never comes up.

The loop back is bounded at two. A third pass is the graph arguing with itself at the
price of a full fan-out each time, and the honest outcome at the bound is an answer that
says what is missing -- not another attempt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

import vericlaim.orchestrator.nodes.sufficiency as sufficiency_module
from vericlaim.evidence import (
    Evidence,
    EvidenceSet,
    PolicyLocator,
    Provenance,
    SqlLocator,
)
from vericlaim.gateway.providers import strictify_schema
from vericlaim.gateway.types import AllProvidersFailedError, BudgetExceededError
from vericlaim.orchestrator.nodes.sufficiency import (
    SUFFICIENCY_SCHEMA,
    SUFFICIENCY_SYSTEM_PROMPT,
    SUFFICIENCY_TASK,
    _counted_gaps,
    sufficiency,
)
from vericlaim.orchestrator.sources import (
    SOURCES_FILE,
    SourceCapability,
    load_capabilities,
)
from vericlaim.orchestrator.state import (
    MAX_REPLANS,
    GraphState,
    RoutingDecision,
    StageRecord,
)
from vericlaim.sql.contexts import load_contexts

QUESTION = "What does the wording say, and how often did it happen?"


def policy_evidence(content: str = "Sudden escape of water is covered.") -> Evidence:
    return Evidence(
        source_type="policy",
        source_id="HomeSecure_Plus.pdf",
        content=content,
        locator=PolicyLocator(document="HomeSecure_Plus.pdf", page=7, section="4.2"),
        provenance=Provenance(tool="search_policy"),
    )


def sql_evidence(content: str = "398 rows.") -> Evidence:
    return Evidence(
        source_type="sql",
        source_id="ops.claims",
        content=content,
        locator=SqlLocator(tables=("ops.claims",), executed_sql="SELECT 1", row_count=398),
        provenance=Provenance(tool="query_claims_db"),
    )


@dataclass
class _Completion:
    text: str
    parsed: Any
    task: str = SUFFICIENCY_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    cost_usd: float = 0.0
    latency_ms: float = 640.0
    fallbacks: tuple[Any, ...] = ()

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallbacks)


@dataclass
class FakeGateway:
    payload: dict[str, Any] | None = None
    raises: Exception | None = None
    calls: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((task, messages, schema))
        if self.raises is not None:
            raise self.raises
        return _Completion(json.dumps(self.payload), self.payload)


def payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "sufficient": True,
        "gaps": [],
        "reason": "Both parts of the question are covered.",
    }
    base.update(overrides)
    return base


def state_with(
    evidence: list[Evidence] | None = None,
    *,
    sources: tuple[str, ...] = ("policy", "sql"),
    silent: list[str] | None = None,
    failed: list[str] | None = None,
    replans: int = 0,
) -> GraphState:
    evidence = [policy_evidence(), sql_evidence()] if evidence is None else evidence
    collected = EvidenceSet(evidence)
    return GraphState(
        question=QUESTION,
        routing=RoutingDecision(sources=sources, confidence=0.9, reason="Because."),
        plans={
            "answerable": True,
            "unanswerable_reason": "",
            "expected_answer_shape": "A statement with a count.",
            "sub_goals": {
                source: {"goal": f"Ask {source}.", "expected_evidence": "A passage."}
                for source in sources
            },
        },
        evidence=collected,
        collection={
            "by_source": {
                source: len(items) for source, items in collected.by_source().items()
            },
            "sources_used": list(collected.source_types()),
            "silent_sources": silent or [],
            "failed_sources": failed or [],
            "low_confidence": [],
            "duplicates_removed": 0,
        },
        replans=replans,
    )


def sent_payload(gateway: FakeGateway) -> dict[str, Any]:
    return json.loads(gateway.calls[0][1][-1]["content"])


# ------------------------------------------------------------------ counted gaps


def test_a_source_that_answered_nothing_is_a_gap_no_model_is_asked_about() -> None:
    """Arithmetic over what came back settles this. Asking a model to look at evidence
    with a hole in it invites an answer from the part it can see."""
    gateway = FakeGateway(payload())

    state = sufficiency(state_with([policy_evidence()], silent=["sql"]), gateway=gateway)

    assert gateway.calls == []
    assert state.sufficiency["sufficient"] is False
    assert "sql" in " ".join(state.sufficiency["gaps"])
    assert state.sufficiency["assessed_by"] == "deterministic"


def test_a_source_that_could_not_be_reached_is_a_gap_too() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with([policy_evidence()], failed=["sql"]), gateway=gateway)

    assert gateway.calls == []
    assert state.sufficiency["sufficient"] is False
    assert "sql" in " ".join(state.sufficiency["gaps"])


def test_nothing_at_all_coming_back_needs_no_assessment() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(
        state_with([], silent=["policy", "sql"]), gateway=gateway
    )

    assert gateway.calls == []
    assert state.sufficiency["sufficient"] is False


def test_a_silent_gap_names_the_sub_goal_it_was_given(monkeypatch: Any) -> None:
    source = "source_alpha"
    goal = "Find the requested synthetic measurement."
    monkeypatch.setattr(sufficiency_module, "load_capabilities", lambda: {}, raising=False)
    before = state_with([], sources=(source,), silent=[source])
    state = sufficiency(
        before.with_(
            plans={
                "sub_goals": {
                    source: {"goal": goal, "expected_evidence": "A measurement."}
                }
            }
        ),
        gateway=FakeGateway(payload()),
    )

    assert goal in state.sufficiency["gaps"][0]


def test_a_silent_gap_names_the_sources_declared_limits(monkeypatch: Any) -> None:
    source = "source_alpha"
    limits = ("synthetic event totals", "synthetic external records")
    capability = SourceCapability(
        name=source,
        tool="synthetic_tool",
        title="Synthetic source",
        holds="Synthetic facts.",
        answers=("synthetic facts",),
        cannot_answer=limits,
        citation="synthetic locator",
    )
    monkeypatch.setattr(
        sufficiency_module,
        "load_capabilities",
        lambda: {source: capability},
        raising=False,
    )

    state = sufficiency(
        state_with([], sources=(source,), silent=[source]),
        gateway=FakeGateway(payload()),
    )

    gap = state.sufficiency["gaps"][0]
    assert all(limit in gap for limit in limits)


def test_a_failed_gap_names_its_sub_goal_and_recorded_reason(monkeypatch: Any) -> None:
    source = "source_alpha"
    goal = "Find the requested synthetic total."
    failure_reason = "synthetic transport timeout"
    monkeypatch.setattr(sufficiency_module, "load_capabilities", lambda: {}, raising=False)
    before = state_with([], sources=(source,), failed=[source])
    state = sufficiency(
        before.with_(
            plans={
                "sub_goals": {
                    source: {"goal": goal, "expected_evidence": "A total."}
                }
            },
            stages=(StageRecord(name=f"source.{source}", error=failure_reason),),
        ),
        gateway=FakeGateway(payload()),
    )

    gap = state.sufficiency["gaps"][0]
    assert goal in gap
    assert failure_reason in gap


def test_a_failed_gap_names_the_sources_declared_limits(monkeypatch: Any) -> None:
    source = "source_alpha"
    limits = ("synthetic event totals", "synthetic external records")
    capability = SourceCapability(
        name=source,
        tool="synthetic_tool",
        title="Synthetic source",
        holds="Synthetic facts.",
        answers=("synthetic facts",),
        cannot_answer=limits,
        citation="synthetic locator",
    )
    monkeypatch.setattr(
        sufficiency_module,
        "load_capabilities",
        lambda: {source: capability},
        raising=False,
    )

    state = sufficiency(
        state_with([], sources=(source,), failed=[source]),
        gateway=FakeGateway(payload()),
    )

    gap = state.sufficiency["gaps"][0]
    assert all(limit in gap for limit in limits)


@pytest.mark.parametrize(
    ("has_sub_goal", "has_capability"),
    [(False, True), (True, False)],
)
def test_a_counted_gap_omits_missing_details_cleanly(
    monkeypatch: Any, has_sub_goal: bool, has_capability: bool
) -> None:
    source = "source_alpha"
    capability = SourceCapability(
        name=source,
        tool="synthetic_tool",
        title="Synthetic source",
        holds="Synthetic facts.",
        answers=("synthetic facts",),
        cannot_answer=("synthetic event totals",),
        citation="synthetic locator",
    )
    monkeypatch.setattr(
        sufficiency_module,
        "load_capabilities",
        lambda: {source: capability} if has_capability else {},
        raising=False,
    )
    plans = (
        {"sub_goals": {source: {"goal": "Find a synthetic fact."}}}
        if has_sub_goal
        else {"sub_goals": {"source_peer": {"goal": "Find a peer fact."}}}
    )
    before = state_with([], sources=("source_peer",), silent=[source])

    state = sufficiency(
        before.with_(plans=plans),
        gateway=FakeGateway(payload()),
    )

    assert len(state.sufficiency["gaps"]) == 1
    gap = state.sufficiency["gaps"][0]
    assert gap.startswith(f"{source} ")
    assert '""' not in gap
    assert "''" not in gap
    assert "None" not in gap
    if not has_sub_goal:
        assert "assigned sub-goal" not in gap
    if not has_capability:
        assert "declared cannot answer" not in gap


def test_a_source_that_returned_evidence_produces_no_counted_gap(
    monkeypatch: Any,
) -> None:
    source = "source_alpha"

    def unexpected_capability_load() -> None:
        raise AssertionError("capabilities are irrelevant when no source is silent")

    monkeypatch.setattr(
        sufficiency_module, "load_capabilities", unexpected_capability_load, raising=False
    )
    state = state_with([], sources=(source,)).with_(
        collection={
            "by_source": {source: 1},
            "sources_used": [source],
            "silent_sources": [],
            "failed_sources": [],
        }
    )

    assert _counted_gaps(state) == []


def test_counted_gaps_are_still_joined_into_the_retry_hint(monkeypatch: Any) -> None:
    first = "source_alpha"
    second = "source_beta"
    monkeypatch.setattr(sufficiency_module, "load_capabilities", lambda: {}, raising=False)
    before = state_with([], sources=(first, second), silent=[first], failed=[second])

    state = sufficiency(before, gateway=FakeGateway(payload()))

    assert len(state.sufficiency["gaps"]) == 2
    assert state.sufficiency["retry_hint"] == "; ".join(state.sufficiency["gaps"])
    failed_gap = state.sufficiency["gaps"][1]
    assert "failure reason" not in failed_gap
    assert '""' not in failed_gap
    assert "None" not in failed_gap


# ------------------------------------------------------------------ the assessment


def test_evidence_for_every_sub_goal_goes_to_the_model_to_judge() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with(), gateway=gateway)

    assert gateway.calls[0][0] == SUFFICIENCY_TASK
    assert state.sufficiency["sufficient"] is True
    assert state.sufficiency["assessed_by"] == "model"


def test_the_model_sees_only_the_evidence_never_raw_tool_output() -> None:
    """The same boundary synthesis has. A sufficiency check reading raw returns would
    be judging something the answer will never be built from."""
    gateway = FakeGateway(payload())

    sufficiency(state_with(), gateway=gateway)

    sent = sent_payload(gateway)
    assert "[E1]" in sent["evidence"]
    assert "[E2]" in sent["evidence"]
    assert sent["question"] == QUESTION
    assert sorted(sent["sub_goals"]) == ["policy", "sql"]


def test_the_model_finding_a_gap_makes_the_evidence_insufficient() -> None:
    gateway = FakeGateway(
        payload(sufficient=False, gaps=["No figure for the period asked about."])
    )

    state = sufficiency(state_with(), gateway=gateway)

    assert state.sufficiency["sufficient"] is False
    assert state.sufficiency["gaps"] == ["No figure for the period asked about."]


def test_calling_the_evidence_insufficient_without_naming_a_gap_is_a_broken_reply() -> None:
    """"Not enough" with nothing named cannot be planned against. The next pass would
    repeat the last one exactly."""
    gateway = FakeGateway(payload(sufficient=False, gaps=[]))

    state = sufficiency(state_with(), gateway=gateway)

    assert state.sufficiency == {}
    assert state.stages[-1].failed


# ------------------------------------------------------------------ going round again


def test_a_gap_that_can_still_be_planned_for_asks_for_another_pass() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with([policy_evidence()], silent=["sql"]), gateway=gateway)

    assert state.sufficiency["replan"] is True
    assert state.replans == 1


def test_the_gaps_become_the_hint_the_next_plan_is_given() -> None:
    gateway = FakeGateway(
        payload(sufficient=False, gaps=["Nothing on the deductible."])
    )

    state = sufficiency(state_with(), gateway=gateway)

    assert "deductible" in state.sufficiency["retry_hint"]


def test_the_loop_stops_at_the_bound_even_with_the_gap_still_open() -> None:
    """A third pass is the graph arguing with itself at the price of a full fan-out. The
    honest outcome here is an answer that says what is missing."""
    gateway = FakeGateway(payload())

    state = sufficiency(
        state_with([policy_evidence()], silent=["sql"], replans=MAX_REPLANS),
        gateway=gateway,
    )

    assert state.sufficiency["sufficient"] is False
    assert state.sufficiency["replan"] is False
    assert state.replans == MAX_REPLANS
    assert "gathered" in state.sufficiency["reason"].lower() or state.sufficiency["gaps"]


def test_enough_evidence_never_asks_for_another_pass() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with(), gateway=gateway)

    assert state.sufficiency["replan"] is False
    assert state.replans == 0


# ------------------------------------------------------------------ failure


def test_a_provider_failure_leaves_the_question_unassessed_rather_than_looping() -> None:
    """Neither answer is safe to invent. Calling it sufficient hides a gap; calling it
    insufficient spends another fan-out on a judgement nobody made."""
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            SUFFICIENCY_TASK, [("gemini", "gemini-3.5-flash", RuntimeError("down"))]
        )
    )

    state = sufficiency(state_with(), gateway=gateway)

    assert state.sufficiency == {}
    assert state.stages[-1].failed
    assert state.replans == 0


def test_running_out_of_money_stops_the_run() -> None:
    gateway = FakeGateway(raises=BudgetExceededError("total", 5.0, 5.0))

    with pytest.raises(BudgetExceededError):
        sufficiency(state_with(), gateway=gateway)


def test_assessing_before_planning_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="plan"):
        sufficiency(GraphState(question=QUESTION), gateway=FakeGateway())


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_the_verdict() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with(), gateway=gateway)

    stage = state.stages[-1]
    assert stage.name == "sufficiency"
    assert stage.detail["sufficient"] is True
    assert stage.latency_ms == 640.0


def test_a_counted_gap_costs_nothing() -> None:
    gateway = FakeGateway(payload())

    state = sufficiency(state_with([policy_evidence()], silent=["sql"]), gateway=gateway)

    assert state.stages[-1].cost_usd == 0.0
    assert state.stages[-1].model == ""


# ------------------------------------------------------------------ the contract


def test_the_schema_survives_strict_structured_output() -> None:
    assert strictify_schema(SUFFICIENCY_SCHEMA) == SUFFICIENCY_SCHEMA


def test_the_prompt_names_none_of_the_sources() -> None:
    named = sorted(
        name
        for name in load_capabilities(SOURCES_FILE)
        if re.search(rf"\b{re.escape(name)}\b", SUFFICIENCY_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_names_no_table_or_column_of_the_corpus() -> None:
    identifiers = set()
    for directory in ("contexts/sql", "contexts/sheets"):
        for context in load_contexts(directory).values():
            identifiers.add(context.table)
            identifiers.update(context.column_names)

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(rf"\b{re.escape(identifier)}\b", SUFFICIENCY_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_forbids_answering_the_question() -> None:
    assert "not answer" in SUFFICIENCY_SYSTEM_PROMPT.lower()
