"""Making one body of evidence out of what came back from several sources.

The fan-out leaves the state holding whatever each branch contributed, in whatever order
the branches happened to be scheduled. This node turns that into something the rest of
the run can rely on: one canonical ordering, ids assigned once, and -- the part that
matters most -- an explicit account of what is *not* there.

A source that was asked and returned nothing looks exactly like a source that was never
asked, once the evidence is in one pile. Only the plan knows the difference, and only
here is it still known. Everything downstream reads that account rather than rediscovering
it: sufficiency decides whether to replan from it, synthesis is told which evidence to
qualify, and the answer says which source it could not reach.
"""

from __future__ import annotations

import pytest

from vericlaim.evidence import (
    Evidence,
    EvidenceSet,
    PolicyLocator,
    Provenance,
    ScannedLocator,
    SqlLocator,
)
from vericlaim.orchestrator.graph import SOURCE_STAGE_PREFIX
from vericlaim.orchestrator.nodes.collect import collect
from vericlaim.orchestrator.state import GraphState, RoutingDecision, StageRecord

QUESTION = "What does the wording say, and how often did it happen?"


def policy_evidence(content: str = "Sudden escape of water is covered.", page: int = 7) -> Evidence:
    return Evidence(
        source_type="policy",
        source_id="HomeSecure_Plus.pdf",
        content=content,
        locator=PolicyLocator(document="HomeSecure_Plus.pdf", page=page, section="4.2"),
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


def scanned_evidence(confidence: float = 0.95) -> Evidence:
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


def state_with(
    evidence: list[Evidence],
    *,
    sources: tuple[str, ...] = ("policy", "sql"),
    stages: tuple[StageRecord, ...] = (),
) -> GraphState:
    state = GraphState(
        question=QUESTION,
        routing=RoutingDecision(sources=sources, confidence=0.9, reason="Because."),
        plans={
            "answerable": True,
            "unanswerable_reason": "",
            "sub_goals": {
                source: {"goal": f"Ask {source}.", "expected_evidence": ""}
                for source in sources
            },
        },
        evidence=EvidenceSet(evidence),
        stages=stages,
    )
    return state


def source_stage(source: str, returned: int = 1, error: str = "") -> StageRecord:
    return StageRecord(
        name=f"{SOURCE_STAGE_PREFIX}{source}",
        detail={} if error else {"goal": f"Ask {source}.", "evidence": returned},
        error=error,
    )


# ------------------------------------------------------------------ the account


def test_the_evidence_is_counted_by_source() -> None:
    state = collect(
        state_with([policy_evidence(), policy_evidence("Another.", page=8), sql_evidence()])
    )

    assert state.collection["by_source"] == {"policy": 2, "sql": 1}


def test_a_source_that_was_asked_and_answered_nothing_is_named() -> None:
    """Once the evidence is in one pile, a source that returned nothing is
    indistinguishable from one that was never asked. Only the plan knows, and only
    here is it still known."""
    state = collect(state_with([policy_evidence()]))

    assert state.collection["silent_sources"] == ["sql"]


def test_a_source_that_answered_is_not_reported_as_silent() -> None:
    state = collect(state_with([policy_evidence(), sql_evidence()]))

    assert state.collection["silent_sources"] == []


def test_a_source_whose_tool_failed_is_named_separately_from_one_that_was_silent() -> None:
    """These mean different things to the reader. One found nothing; the other was never
    reached, and an answer built without it is incomplete rather than negative."""
    state = collect(
        state_with(
            [sql_evidence()],
            stages=(source_stage("policy", error="index is missing"), source_stage("sql")),
        )
    )

    assert state.collection["failed_sources"] == ["policy"]
    assert state.collection["silent_sources"] == []


def test_the_sources_that_did_contribute_are_listed() -> None:
    state = collect(state_with([policy_evidence(), sql_evidence()]))

    assert state.collection["sources_used"] == ["policy", "sql"]


# ------------------------------------------------------------------ ordering and ids


def test_the_evidence_is_ordered_the_way_the_routing_was() -> None:
    """Two branches finishing in a different order must not change what [E1] means.
    Ids assigned by whichever thread got there first make an answer irreproducible."""
    state = collect(state_with([sql_evidence(), policy_evidence()]))

    assert [item.source_type for item in state.evidence.items] == ["policy", "sql"]
    assert state.evidence.items[0].id == "E1"


def test_evidence_from_one_source_keeps_the_order_that_source_returned_it_in() -> None:
    """Rank order within a source is information: it is the retrieval's own judgement."""
    first = policy_evidence("First.", page=1)
    second = policy_evidence("Second.", page=2)

    state = collect(state_with([first, second]))

    assert [item.content for item in state.evidence.items] == ["First.", "Second."]


def test_evidence_from_a_source_nobody_routed_is_kept_rather_than_discarded() -> None:
    """It was gathered; dropping it here would make an answer cite something the
    evidence set no longer contains. It sorts after the routed sources."""
    state = collect(state_with([scanned_evidence(), policy_evidence()], sources=("policy",)))

    assert [item.source_type for item in state.evidence.items] == ["policy", "scanned_pdf"]


def test_the_ids_run_from_one_without_gaps() -> None:
    state = collect(state_with([sql_evidence(), policy_evidence(), scanned_evidence()]))

    assert list(state.evidence.ids) == ["E1", "E2", "E3"]


# ------------------------------------------------------------------ duplicates


def test_the_same_passage_returned_twice_is_kept_once() -> None:
    state = collect(state_with([policy_evidence(), policy_evidence()]))

    assert len(state.evidence.items) == 1


def test_what_the_deduplication_removed_is_reported() -> None:
    """A count that silently shrinks is a retrieval problem nobody can see."""
    state = collect(
        state_with(
            [policy_evidence(), policy_evidence(), sql_evidence()],
            stages=(source_stage("policy", returned=2), source_stage("sql")),
        )
    )

    assert state.collection["duplicates_removed"] == 1


def test_the_same_words_from_a_different_page_are_two_pieces_of_evidence() -> None:
    state = collect(state_with([policy_evidence(page=7), policy_evidence(page=9)]))

    assert len(state.evidence.items) == 2


# ------------------------------------------------------------------ confidence


def test_evidence_read_with_low_confidence_is_flagged_for_qualification() -> None:
    state = collect(state_with([policy_evidence(), scanned_evidence(confidence=0.4)]))

    assert state.collection["low_confidence"] == ["E2"]


def test_evidence_read_confidently_is_not_flagged() -> None:
    state = collect(state_with([scanned_evidence(confidence=0.95)]))

    assert state.collection["low_confidence"] == []


def test_the_floor_can_be_set_by_the_caller() -> None:
    state = collect(state_with([scanned_evidence(confidence=0.8)]), low_confidence_floor=0.9)

    assert state.collection["low_confidence"] == ["E1"]


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_the_account_and_costs_nothing() -> None:
    """No model is involved. A node that spends nothing must not report that it did."""
    state = collect(state_with([policy_evidence(), sql_evidence()]))

    stage = state.stages[-1]
    assert stage.name == "collect"
    assert stage.detail["evidence"] == 2
    assert stage.cost_usd == 0.0
    assert stage.model == ""
    assert not stage.failed


def test_collecting_nothing_at_all_is_recorded_rather_than_failed() -> None:
    """Every source silent is a real outcome of a real run. The refusal is written
    later, from this account, not by throwing here."""
    state = collect(state_with([]))

    assert state.evidence.items == ()
    assert state.collection["silent_sources"] == ["policy", "sql"]
    assert not state.stages[-1].failed


def test_collecting_before_routing_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="rout"):
        collect(GraphState(question=QUESTION))
