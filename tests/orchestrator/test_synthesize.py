"""Writing the answer -- from the evidence, and from nothing else.

This is the node the whole system exists to make trustworthy, so most of these tests are
about what it must *not* do. It never sees raw tool output: the only view of the evidence
it is given is the rendered one, each block tagged with the id the answer has to cite.
And it does not write the refusals -- a question the router turned away, one awaiting
clarification, one the planner declined, and one that gathered no evidence are all
answered from what was already recorded, deterministically, because a model asked to
phrase a refusal will phrase something adjacent to an answer.

The prompt carries the three obligations that make an answer here different from a
fluent one: distinguish what the evidence states from what it suggests, qualify anything
read with low confidence rather than asserting it, and never say a claim is approved --
this system supports a decision, it does not make one.
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
    ScannedLocator,
    SqlLocator,
)
from vericlaim.gateway.types import AllProvidersFailedError, BudgetExceededError
from vericlaim.orchestrator.nodes.synthesize import (
    SYNTHESIZE_SYSTEM_PROMPT,
    SYNTHESIZE_TASK,
    synthesize,
)
from vericlaim.orchestrator.sources import SOURCES_FILE, load_capabilities
from vericlaim.orchestrator.state import GraphState, RoutingDecision
from vericlaim.sql.contexts import load_contexts

QUESTION = "What does the wording say, and how often did it happen?"
ANSWER = "The wording covers sudden escape of water [E1], and 398 were recorded [E2]."


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


def scanned_evidence(confidence: float = 0.4) -> Evidence:
    return Evidence(
        source_type="scanned_pdf",
        source_id="CLM-1088_INSPECTION.pdf",
        content="Pipe rupture beneath the kitchen sink.",
        locator=ScannedLocator(
            document="CLM-1088_INSPECTION.pdf", page=2, ocr_confidence=confidence
        ),
        provenance=Provenance(tool="search_scanned"),
        confidence=confidence,
    )


@dataclass
class _Completion:
    text: str
    task: str = SYNTHESIZE_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    cost_usd: float = 0.0
    latency_ms: float = 2100.0
    fallbacks: tuple[Any, ...] = ()
    parsed: Any = None

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallbacks)


@dataclass
class FakeGateway:
    text: str = ANSWER
    raises: Exception | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def complete(self, task: str, messages: Any, **kwargs: Any) -> Any:
        self.calls.append((task, messages))
        if self.raises is not None:
            raise self.raises
        return _Completion(self.text)


def state_with(
    evidence: list[Evidence] | None = None,
    *,
    sources: tuple[str, ...] = ("policy", "sql"),
    out_of_scope: bool = False,
    needs_clarification: bool = False,
    answerable: bool = True,
    silent: list[str] | None = None,
    failed: list[str] | None = None,
    gaps: list[str] | None = None,
) -> GraphState:
    evidence = [policy_evidence(), sql_evidence()] if evidence is None else evidence
    collected = EvidenceSet(evidence)
    routing = RoutingDecision(
        sources=() if (out_of_scope or needs_clarification) else sources,
        confidence=0.9,
        reason="Nothing here holds staff pay." if out_of_scope else "Because.",
        out_of_scope=out_of_scope,
        needs_clarification=needs_clarification,
        clarification_question="Which product did you mean?" if needs_clarification else "",
    )
    plans: dict[str, Any] = {
        "answerable": answerable,
        "unanswerable_reason": "" if answerable else "No source holds a figure for pay.",
        "expected_answer_shape": "A statement with a count.",
        "sub_goals": {
            source: {"goal": f"Ask {source}.", "expected_evidence": "A passage."}
            for source in routing.sources
        },
    }
    return GraphState(
        question=QUESTION,
        routing=routing,
        plans=plans,
        evidence=collected,
        collection={
            "by_source": {
                source: len(items) for source, items in collected.by_source().items()
            },
            "sources_used": list(collected.source_types()),
            "silent_sources": silent or [],
            "failed_sources": failed or [],
            "low_confidence": [
                item.id for item in collected.low_confidence(0.6)
            ],
            "duplicates_removed": 0,
        },
        sufficiency={
            "sufficient": not gaps,
            "gaps": gaps or [],
            "reason": "Scripted.",
            "assessed_by": "model",
            "replan": False,
            "retry_hint": "",
        },
    )


def sent_payload(gateway: FakeGateway) -> dict[str, Any]:
    return json.loads(gateway.calls[0][1][-1]["content"])


# ------------------------------------------------------------------ the answer


def test_the_answer_is_written_from_the_evidence() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with(), gateway=gateway)

    assert state.answer == ANSWER
    assert gateway.calls[0][0] == SYNTHESIZE_TASK


def test_the_model_is_given_the_evidence_and_not_the_tools_output() -> None:
    """The one view of the evidence anything downstream of the tools ever sees. Raw
    output reaching here is the invariant this boundary exists to hold."""
    gateway = FakeGateway()

    synthesize(state_with(), gateway=gateway)

    sent = sent_payload(gateway)
    assert "[E1]" in sent["evidence"]
    assert "[E2]" in sent["evidence"]
    assert sent["question"] == QUESTION
    assert sent["expected_answer_shape"] == "A statement with a count."


def test_evidence_read_with_low_confidence_is_marked_in_what_the_model_sees() -> None:
    gateway = FakeGateway()

    synthesize(state_with([policy_evidence(), scanned_evidence(0.4)]), gateway=gateway)

    assert "LOW CONFIDENCE" in sent_payload(gateway)["evidence"]


def test_a_source_that_could_not_be_reached_is_named_to_the_model() -> None:
    """An answer built while a source was down is incomplete in a way the reader has to
    be told about, and the model can only say so if it is told."""
    gateway = FakeGateway()

    synthesize(state_with(failed=["scanned_pdf"]), gateway=gateway)

    sent = sent_payload(gateway)
    assert sent["unreachable_sources"] == ["scanned_pdf"]


def test_what_the_evidence_still_lacks_is_named_to_the_model() -> None:
    gateway = FakeGateway()

    synthesize(state_with(gaps=["Nothing on the deductible."]), gateway=gateway)

    assert sent_payload(gateway)["known_gaps"] == ["Nothing on the deductible."]


# ------------------------------------------------------------------ refusals


def test_a_question_the_router_turned_away_is_answered_from_what_it_said() -> None:
    """A model asked to phrase a refusal phrases something adjacent to an answer. The
    reason was already written by the node that decided it."""
    gateway = FakeGateway()

    state = synthesize(state_with([], out_of_scope=True), gateway=gateway)

    assert gateway.calls == []
    assert "staff pay" in state.answer
    assert state.stages[-1].detail["refused"] == "out_of_scope"


def test_a_question_awaiting_clarification_is_answered_with_the_question() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], needs_clarification=True), gateway=gateway)

    assert gateway.calls == []
    assert "Which product did you mean?" in state.answer


def test_a_question_the_planner_declined_is_answered_with_its_reason() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], answerable=False), gateway=gateway)

    assert gateway.calls == []
    assert "figure for pay" in state.answer


def test_a_later_plan_decline_does_not_discard_collected_evidence() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with(answerable=False), gateway=gateway)

    assert gateway.calls
    assert state.answer == ANSWER
    assert "refused" not in state.stages[-1].detail


def test_a_later_plan_decline_is_preserved_as_a_known_gap() -> None:
    gateway = FakeGateway()

    synthesize(state_with(answerable=False), gateway=gateway)

    assert gateway.calls
    assert sent_payload(gateway)["known_gaps"] == ["No source holds a figure for pay."]


def test_a_plan_decline_without_evidence_still_refuses_without_a_model_call() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], answerable=False), gateway=gateway)

    assert gateway.calls == []
    assert state.stages[-1].detail == {"refused": "unanswerable", "cited": []}


def test_no_evidence_at_all_produces_a_refusal_rather_than_an_answer() -> None:
    """Nothing to cite means nothing to say. A model given an empty evidence block
    writes from what it already believes, which is precisely the failure mode this
    system exists to rule out."""
    gateway = FakeGateway()

    state = synthesize(state_with([], silent=["policy", "sql"]), gateway=gateway)

    assert gateway.calls == []
    assert state.answer
    assert state.stages[-1].detail["refused"] == "no_evidence"


def test_a_refusal_says_which_sources_were_consulted() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], silent=["policy", "sql"]), gateway=gateway)

    assert "policy" in state.answer and "sql" in state.answer


def test_a_refusal_distinguishes_a_source_that_was_down_from_one_that_was_silent() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], silent=["policy"], failed=["sql"]), gateway=gateway)

    assert "could not be reached" in state.answer


def test_a_refusal_costs_nothing() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with([], out_of_scope=True), gateway=gateway)

    assert state.stages[-1].cost_usd == 0.0
    assert state.stages[-1].model == ""


# ------------------------------------------------------------------ failure


def test_an_empty_reply_is_a_failure_not_an_empty_answer() -> None:
    gateway = FakeGateway(text="   ")

    state = synthesize(state_with(), gateway=gateway)

    assert state.answer == ""
    assert state.stages[-1].failed


def test_a_provider_failure_leaves_the_run_without_an_answer_rather_than_a_made_up_one() -> None:
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            SYNTHESIZE_TASK, [("gemini", "gemini-3.5-flash", RuntimeError("down"))]
        )
    )

    state = synthesize(state_with(), gateway=gateway)

    assert state.answer == ""
    assert state.stages[-1].failed


def test_running_out_of_money_stops_the_run() -> None:
    gateway = FakeGateway(raises=BudgetExceededError("total", 5.0, 5.0))

    with pytest.raises(BudgetExceededError):
        synthesize(state_with(), gateway=gateway)


def test_synthesizing_before_routing_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="rout"):
        synthesize(GraphState(question=QUESTION), gateway=FakeGateway())


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_what_the_answer_cost() -> None:
    gateway = FakeGateway()

    state = synthesize(state_with(), gateway=gateway)

    stage = state.stages[-1]
    assert stage.name == "synthesize"
    assert stage.model == "gemini-3.5-flash"
    assert stage.latency_ms == 2100.0
    assert stage.detail["cited"] == ["E1", "E2"]


def test_an_answer_citing_nothing_is_recorded_as_citing_nothing() -> None:
    """Not corrected here -- verification decides what to do about it -- but never
    invisible."""
    gateway = FakeGateway(text="Water damage is generally covered.")

    state = synthesize(state_with(), gateway=gateway)

    assert state.answer
    assert state.stages[-1].detail["cited"] == []


# ------------------------------------------------------------------ the prompt


def test_the_prompt_requires_the_answer_to_cite_the_evidence() -> None:
    assert "[E" in SYNTHESIZE_SYSTEM_PROMPT


def test_the_prompt_separates_what_the_evidence_states_from_what_it_suggests() -> None:
    assert "inference" in SYNTHESIZE_SYSTEM_PROMPT.lower()


def test_the_prompt_forbids_saying_a_claim_is_approved() -> None:
    """The system supports a decision; it does not make one. This is the sentence that
    keeps it on the right side of that line."""
    assert "approved" in SYNTHESIZE_SYSTEM_PROMPT.lower()


def test_the_prompt_requires_low_confidence_evidence_to_be_qualified() -> None:
    assert "low confidence" in SYNTHESIZE_SYSTEM_PROMPT.lower()


def test_the_prompt_names_none_of_the_sources() -> None:
    named = sorted(
        name
        for name in load_capabilities(SOURCES_FILE)
        if re.search(rf"\b{re.escape(name)}\b", SYNTHESIZE_SYSTEM_PROMPT, re.I)
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
        if re.search(rf"\b{re.escape(identifier)}\b", SYNTHESIZE_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_a_correction_from_verification_reaches_the_second_attempt() -> None:
    """The rewrite has to know what was wrong with the first answer, or it produces a
    different answer with the same fault."""
    gateway = FakeGateway()

    synthesize(state_with(), gateway=gateway, correction="[E9] does not exist.")

    assert sent_payload(gateway)["correction"] == "[E9] does not exist."
