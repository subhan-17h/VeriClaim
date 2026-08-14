"""What the graph carries from node to node.

The state is the only thing every node shares, so it is the only place where a mistake
propagates silently to all of them. The reference implementation's was an unvalidated
`TypedDict`: a node writing a misspelled key added it, a node reading one that had never
been written got a KeyError three nodes later, and nothing said which node was at fault.

Here it is validated. A node that writes something the state does not declare fails where
it wrote it, and every accumulating field -- evidence, cost, the trace -- has one way to
grow rather than one per node.
"""

from __future__ import annotations

import pytest

from vericlaim.evidence import Evidence, PolicyLocator, Provenance
from vericlaim.orchestrator.state import GraphState, StageRecord


def evidence(content: str = "Section 4.2 covers sudden escape of water.") -> Evidence:
    return Evidence(
        source_type="policy",
        source_id="HomeSecure_Plus_2026.pdf",
        content=content,
        locator=PolicyLocator(
            document="HomeSecure_Plus_2026.pdf", page=7, section="4.2"
        ),
        provenance=Provenance(tool="search_policy"),
    )


# ------------------------------------------------------------------ shape


def test_a_question_is_all_a_run_needs_to_start() -> None:
    state = GraphState(question="Is a burst pipe covered?")

    assert state.question == "Is a burst pipe covered?"
    assert state.evidence.items == ()
    assert state.answer == ""


def test_a_run_without_a_question_is_a_mistake() -> None:
    with pytest.raises(ValueError, match="question"):
        GraphState(question="   ")


def test_a_field_the_state_does_not_declare_is_rejected() -> None:
    """A misspelled key in an unvalidated dict is a KeyError three nodes later, in a node
    that did nothing wrong."""
    with pytest.raises(ValueError):
        GraphState(question="Is it covered?", sorces=["policy"])


# ------------------------------------------------------------------ accumulation


def test_evidence_accumulates_across_the_sources() -> None:
    """Four tools run concurrently and each returns its own list. One set with stable ids
    is what makes a citation mean the same thing in synthesis and in verification."""
    state = GraphState(question="Is it covered?")

    state = state.with_evidence([evidence("first")])
    state = state.with_evidence([evidence("second")])

    assert [item.id for item in state.evidence.items] == ["E1", "E2"]


def test_the_same_finding_from_the_same_place_is_not_counted_twice() -> None:
    state = GraphState(question="Is it covered?").with_evidence(
        [evidence(), evidence()]
    )

    assert len(state.evidence.items) == 1


def test_latency_accumulates_rather_than_being_overwritten() -> None:
    state = GraphState(question="Is it covered?")

    state = state.with_stage(StageRecord(name="route", cost_usd=0.001, latency_ms=120))
    state = state.with_stage(StageRecord(name="plan", cost_usd=0.004, latency_ms=900))

    assert state.total_latency_ms == pytest.approx(1020)


def test_the_state_never_publishes_a_cost_it_cannot_know() -> None:
    """A stage records what its own model call cost, and that figure is right. Summing
    them is not: the model calls a source tool makes reach no stage, so the total omits
    most of a multi-source question's spend -- it read $0.00 on a run that cost $0.0024.
    The only true total is the gateway ledger's, so the state declines to publish one and
    every caller that has a ledger supplies it.
    """
    state = GraphState(question="Is it covered?")
    state = state.with_stage(StageRecord(name="route", cost_usd=0.001, latency_ms=120))

    assert "cost_usd" not in state.to_dict()
    assert not hasattr(state, "total_cost_usd")
    # The per-stage figure is accurate and stays.
    assert state.stages[0].cost_usd == pytest.approx(0.001)


def test_every_stage_stays_on_the_trace_in_the_order_it_ran() -> None:
    state = GraphState(question="Is it covered?")

    state = state.with_stage(StageRecord(name="understand"))
    state = state.with_stage(StageRecord(name="route"))

    assert [stage.name for stage in state.stages] == ["understand", "route"]


def test_a_stage_that_failed_says_so_without_ending_the_run() -> None:
    """One source being down is a gap in the evidence, not the end of the question."""
    state = GraphState(question="Is it covered?").with_stage(
        StageRecord(name="query_claims_db", error="Postgres is not running")
    )

    assert state.stages[0].failed is True
    assert state.failures == ("query_claims_db: Postgres is not running",)


# ------------------------------------------------------------------ the loop bound


def test_a_fresh_run_has_not_replanned() -> None:
    assert GraphState(question="Is it covered?").replans == 0


def test_replanning_is_counted_so_the_loop_can_be_stopped() -> None:
    state = GraphState(question="Is it covered?").replanned()

    assert state.replans == 1


def test_the_state_is_never_mutated_in_place() -> None:
    """Nodes run concurrently. A shared mutable state is a race that shows up as evidence
    going missing under load and never in a test."""
    original = GraphState(question="Is it covered?")

    updated = original.with_evidence([evidence()])

    assert original.evidence.items == ()
    assert len(updated.evidence.items) == 1


# ------------------------------------------------------------------ serialization


def test_the_whole_run_can_be_handed_to_the_api() -> None:
    state = (
        GraphState(question="Is it covered?")
        .with_evidence([evidence()])
        .with_stage(StageRecord(name="route", cost_usd=0.001))
    )

    payload = state.to_dict()

    assert payload["question"] == "Is it covered?"
    assert payload["evidence"][0]["citation"]
    assert payload["stages"][0]["name"] == "route"
    # The per-stage cost travels; the run total does not, because the state cannot know
    # it. Final.from_state supplies the ledger's figure to the client.
    assert payload["stages"][0]["cost_usd"] == pytest.approx(0.001)
    assert "cost_usd" not in payload
