"""Checking the answer before anyone reads it.

Two checks in a deliberate order. The deterministic one runs first and cannot be argued
with: every [En] in the answer must point at evidence that exists. A citation pointing at
nothing is indistinguishable, to a reader, from one pointing at something, which is why
it is a hard failure here rather than a warning.

Only an answer whose citations all resolve is worth spending a model on, and the second
check is the one arithmetic cannot make: does each statement actually rest on what it
cites, and does the answer stay on the right side of decision support.

There is exactly one regeneration. A second would be the system arguing with itself about
an answer it has twice failed to justify, and the honest outcome at that point is to say
so rather than publish the third attempt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.evidence import (
    Evidence,
    EvidenceSet,
    PolicyLocator,
    Provenance,
    SqlLocator,
)
from vericlaim.gateway.providers import strictify_schema
from vericlaim.gateway.types import AllProvidersFailedError, BudgetExceededError
from vericlaim.orchestrator.nodes.verify import (
    VERIFY_SCHEMA,
    VERIFY_SYSTEM_PROMPT,
    VERIFY_TASK,
    verify,
)
from vericlaim.orchestrator.sources import SOURCES_FILE, load_capabilities
from vericlaim.orchestrator.state import GraphState, RoutingDecision
from vericlaim.sql.contexts import load_contexts

QUESTION = "What does the wording say, and how often did it happen?"
GOOD = "The wording covers sudden escape of water [E1], and 398 were recorded [E2]."


def policy_evidence() -> Evidence:
    return Evidence(
        source_type="policy",
        source_id="HomeSecure_Plus.pdf",
        content="Sudden escape of water is covered.",
        locator=PolicyLocator(document="HomeSecure_Plus.pdf", page=7, section="4.2"),
        provenance=Provenance(tool="search_policy"),
    )


def sql_evidence() -> Evidence:
    return Evidence(
        source_type="sql",
        source_id="ops.claims",
        content="398 rows.",
        locator=SqlLocator(tables=("ops.claims",), executed_sql="SELECT 1", row_count=398),
        provenance=Provenance(tool="query_claims_db"),
    )


@dataclass
class _Completion:
    text: str
    parsed: Any
    task: str = VERIFY_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    cost_usd: float = 0.0
    latency_ms: float = 1100.0
    fallbacks: tuple[Any, ...] = ()

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallbacks)


@dataclass
class FakeGateway:
    payloads: list[dict[str, Any]] = field(default_factory=list)
    raises: Exception | None = None
    calls: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((task, messages, schema))
        if self.raises is not None:
            raise self.raises
        index = min(len(self.calls) - 1, len(self.payloads) - 1)
        payload = self.payloads[index]
        return _Completion(json.dumps(payload), payload)


def verdict(**overrides: Any) -> dict[str, Any]:
    base = {
        "faithful": True,
        "overclaims": False,
        "problems": [],
        "reason": "Every statement rests on what it cites.",
    }
    base.update(overrides)
    return base


def gateway_with(*verdicts: dict[str, Any]) -> FakeGateway:
    return FakeGateway(payloads=list(verdicts) or [verdict()])


class Regenerator:
    """Stands in for a second pass at synthesis, remembering what it was told."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.corrections: list[str] = []

    def __call__(self, state: GraphState, correction: str) -> GraphState:
        self.corrections.append(correction)
        answer = self.answers.pop(0) if self.answers else GOOD
        return state.with_(answer=answer)

    @property
    def called(self) -> bool:
        return bool(self.corrections)


def state_with(answer: str = GOOD, *, evidence: list[Evidence] | None = None) -> GraphState:
    items = [policy_evidence(), sql_evidence()] if evidence is None else evidence
    return GraphState(
        question=QUESTION,
        routing=RoutingDecision(
            sources=("policy", "sql"), confidence=0.9, reason="Because."
        ),
        plans={"answerable": True, "sub_goals": {}, "expected_answer_shape": ""},
        evidence=EvidenceSet(items),
        answer=answer,
    )


def sent_payload(gateway: FakeGateway, index: int = 0) -> dict[str, Any]:
    return json.loads(gateway.calls[index][1][-1]["content"])


# ------------------------------------------------------------------ citations


def test_an_answer_whose_citations_all_resolve_passes() -> None:
    gateway = gateway_with(verdict())

    state = verify(state_with(), gateway=gateway, regenerate=Regenerator())

    assert state.citations["ok"] is True
    assert state.citations["resolved"] == ["E1", "E2"]
    assert state.citations["verified"] is True


def test_a_citation_pointing_at_nothing_is_not_published() -> None:
    """The hard failure. A reader cannot tell [E9] from [E1] by looking, so one bad
    citation makes every other one in the answer worth less."""
    gateway = gateway_with(verdict())
    regenerate = Regenerator(GOOD)

    state = verify(
        state_with("The wording covers it [E9]."), gateway=gateway, regenerate=regenerate
    )

    assert regenerate.called
    assert "E9" in regenerate.corrections[0]
    assert state.answer == GOOD
    assert state.citations["regenerated"] is True


def test_a_malformed_marker_is_treated_the_same_way() -> None:
    gateway = gateway_with(verdict())
    regenerate = Regenerator(GOOD)

    verify(state_with("The wording covers it [E]."), gateway=gateway, regenerate=regenerate)

    assert regenerate.called


def test_a_second_bad_answer_is_not_published_either() -> None:
    """One regeneration, then the truth. A third attempt is the system arguing with
    itself about an answer it has twice failed to justify."""
    gateway = gateway_with(verdict())
    regenerate = Regenerator("Still wrong [E9].")

    state = verify(
        state_with("The wording covers it [E9]."), gateway=gateway, regenerate=regenerate
    )

    assert len(regenerate.corrections) == 1
    assert state.citations["degraded"] is True
    assert state.citations["verified"] is False
    assert "E9" in state.answer


def test_the_degraded_answer_does_not_read_as_an_answer() -> None:
    gateway = gateway_with(verdict())

    state = verify(
        state_with("The wording covers it [E9]."),
        gateway=gateway,
        regenerate=Regenerator("Still wrong [E9]."),
    )

    assert "could not" in state.answer.lower()


def test_an_answer_with_bad_citations_is_never_sent_to_the_guard() -> None:
    """Spending a model on an answer already known to be unpublishable is waste. The
    deterministic check runs first for that reason as much as for correctness."""
    gateway = gateway_with(verdict())
    verify(
        state_with("Covers it [E9]."),
        gateway=gateway,
        regenerate=Regenerator("Still wrong [E9]."),
    )

    assert gateway.calls == []


def test_the_evidence_the_answer_never_used_is_recorded() -> None:
    """Retrieving from a source and then ignoring it is a real failure mode, and the
    completeness score in the evaluation suite is computed from exactly this."""
    gateway = gateway_with(verdict())

    state = verify(state_with("Only the wording [E1]."), gateway=gateway, regenerate=Regenerator())

    assert state.citations["uncited"] == ["E2"]


def test_an_answer_that_cites_nothing_at_all_is_not_published() -> None:
    """Nothing resolves because nothing was cited. An uncited answer is exactly the
    fluent, unsourced paragraph this system exists to not produce."""
    gateway = gateway_with(verdict())
    regenerate = Regenerator(GOOD)

    state = verify(
        state_with("Water damage is generally covered."),
        gateway=gateway,
        regenerate=regenerate,
    )

    assert regenerate.called
    assert state.answer == GOOD


# ------------------------------------------------------------------ the guard


def test_a_statement_the_evidence_does_not_support_sends_it_back() -> None:
    gateway = gateway_with(
        verdict(faithful=False, problems=["The figure 398 is not in [E2]."]),
        verdict(),
    )
    regenerate = Regenerator(GOOD)

    state = verify(state_with(), gateway=gateway, regenerate=regenerate)

    assert regenerate.called
    assert "398" in regenerate.corrections[0]
    assert state.citations["verified"] is True


def test_an_answer_that_decides_the_claim_sends_it_back() -> None:
    """The system supports a decision. An answer that makes one is wrong however well
    it is cited."""
    gateway = gateway_with(
        verdict(overclaims=True, problems=["States the claim is approved."]), verdict()
    )
    regenerate = Regenerator(GOOD)

    state = verify(state_with(), gateway=gateway, regenerate=regenerate)

    assert regenerate.called
    assert state.citations["verified"] is True


def test_an_answer_that_fails_the_guard_twice_degrades() -> None:
    gateway = gateway_with(verdict(faithful=False, problems=["Unsupported."]))
    regenerate = Regenerator(GOOD)

    state = verify(state_with(), gateway=gateway, regenerate=regenerate)

    assert len(regenerate.corrections) == 1
    assert state.citations["degraded"] is True
    assert "Unsupported." in state.answer


def test_the_regenerated_answer_is_checked_again_from_the_start() -> None:
    """A regeneration that fixes the guard and breaks the citations is not an
    improvement."""
    gateway = gateway_with(verdict(faithful=False, problems=["Unsupported."]), verdict())
    regenerate = Regenerator("Now with a bad id [E9].")

    state = verify(state_with(), gateway=gateway, regenerate=regenerate)

    assert state.citations["degraded"] is True
    assert state.citations["ok"] is False


def test_the_guard_sees_the_answer_and_the_evidence_it_cites() -> None:
    gateway = gateway_with(verdict())

    verify(state_with(), gateway=gateway, regenerate=Regenerator())

    sent = sent_payload(gateway)
    assert sent["answer"] == GOOD
    assert "[E1]" in sent["evidence"]


def test_calling_an_answer_unfaithful_without_naming_a_problem_is_a_broken_reply() -> None:
    gateway = gateway_with(verdict(faithful=False, problems=[]))

    state = verify(state_with(), gateway=gateway, regenerate=Regenerator())

    assert state.stages[-1].failed


# ------------------------------------------------------------------ failure


def test_a_guard_that_cannot_be_reached_leaves_the_answer_unverified() -> None:
    """The citations still resolved, so the answer is not known to be wrong. It is
    known to be unchecked, and that is what gets recorded."""
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            VERIFY_TASK, [("gemini", "gemini-3.5-flash", RuntimeError("down"))]
        )
    )

    state = verify(state_with(), gateway=gateway, regenerate=Regenerator())

    assert state.answer == GOOD
    assert state.citations["ok"] is True
    assert state.citations["verified"] is False
    assert state.stages[-1].failed


def test_running_out_of_money_stops_the_run() -> None:
    gateway = FakeGateway(raises=BudgetExceededError("total", 5.0, 5.0))

    with pytest.raises(BudgetExceededError):
        verify(state_with(), gateway=gateway, regenerate=Regenerator())


def test_there_is_nothing_to_verify_without_an_answer() -> None:
    gateway = gateway_with(verdict())

    state = verify(state_with(answer=""), gateway=gateway, regenerate=Regenerator())

    assert gateway.calls == []
    assert state.citations == {}


def test_a_refusal_is_not_held_to_a_citation_it_could_not_have() -> None:
    """A question that gathered no evidence is answered by saying so. Demanding a
    citation from it would send a truthful refusal back to be rewritten."""
    gateway = gateway_with(verdict())
    regenerate = Regenerator()

    state = verify(
        state_with("No evidence was found for this question.", evidence=[]),
        gateway=gateway,
        regenerate=regenerate,
    )

    assert not regenerate.called
    assert gateway.calls == []
    assert state.citations["ok"] is True


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_the_verdict() -> None:
    gateway = gateway_with(verdict())

    state = verify(state_with(), gateway=gateway, regenerate=Regenerator())

    stage = state.stages[-1]
    assert stage.name == "verify"
    assert stage.detail["resolved"] == 2
    assert stage.detail["verified"] is True
    assert stage.latency_ms == 1100.0


def test_a_regeneration_is_visible_on_the_stage() -> None:
    gateway = gateway_with(verdict(faithful=False, problems=["Unsupported."]), verdict())

    state = verify(state_with(), gateway=gateway, regenerate=Regenerator(GOOD))

    assert state.stages[-1].detail["regenerated"] is True


# ------------------------------------------------------------------ the contract


def test_the_schema_survives_strict_structured_output() -> None:
    assert strictify_schema(VERIFY_SCHEMA) == VERIFY_SCHEMA


def test_the_prompt_asks_only_about_faithfulness_not_about_truth() -> None:
    assert "faithful" in VERIFY_SYSTEM_PROMPT.lower()


def test_the_prompt_names_the_determination_it_must_not_allow() -> None:
    assert "approved" in VERIFY_SYSTEM_PROMPT.lower()


def test_the_prompt_names_none_of_the_sources() -> None:
    named = sorted(
        name
        for name in load_capabilities(SOURCES_FILE)
        if re.search(rf"\b{re.escape(name)}\b", VERIFY_SYSTEM_PROMPT, re.I)
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
        if re.search(rf"\b{re.escape(identifier)}\b", VERIFY_SYSTEM_PROMPT, re.I)
    )

    assert named == []
