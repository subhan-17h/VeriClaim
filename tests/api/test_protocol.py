"""The event protocol is the contract C-10 builds against, so its shape is pinned here."""

from __future__ import annotations

from vericlaim.api.protocol import (
    EVENT_NAMES,
    Error,
    EvidenceEvent,
    Final,
    RunStarted,
    Stage,
)
from vericlaim.evidence import Evidence, PolicyLocator, Provenance
from vericlaim.orchestrator.state import GraphState, StageRecord


def test_run_started_carries_the_trace_and_the_question() -> None:
    payload = RunStarted(trace_id="abc123", question="a question").to_json()

    assert payload == {
        "event": "run_started",
        "trace_id": "abc123",
        "question": "a question",
    }


def test_a_stage_carries_what_the_node_cost_and_whether_it_failed() -> None:
    record = StageRecord(
        name="understand",
        model="a-model",
        cost_usd=0.5,
        latency_ms=12.5,
        error="",
        detail={"key": "value"},
    )

    payload = Stage.from_record(record).to_json()

    assert payload == {
        "event": "stage",
        "name": "understand",
        "model": "a-model",
        "cost_usd": 0.5,
        "latency_ms": 12.5,
        "error": "",
        "detail": {"key": "value"},
    }


def test_an_error_event_carries_its_message() -> None:
    assert Error(message="it broke").to_json() == {
        "event": "error",
        "message": "it broke",
    }


def test_every_event_name_is_declared_and_ping_is_not_one_of_them() -> None:
    assert EVENT_NAMES == frozenset(
        {"run_started", "stage", "evidence", "final", "error"}
    )
    assert "ping" not in EVENT_NAMES


def test_a_final_event_reports_the_ledger_cost_not_the_states_own() -> None:
    # The state's cost comes from stage records only; tool-internal spend reaches none of
    # them. A final event that reported the state's total would under-report every
    # multi-source question, so the cost is supplied rather than derived.
    state = GraphState(
        question="a question",
        answer="an answer",
        trace_id="abc123",
        stages=(StageRecord(name="understand", cost_usd=1.0),),
    )

    payload = Final.from_state(state, cost_usd=99.0).to_json()

    assert payload["event"] == "final"
    assert payload["cost_usd"] == 99.0
    assert payload["answer"] == "an answer"
    assert payload["trace_id"] == "abc123"


def test_an_evidence_event_names_its_source_and_serializes_its_item() -> None:
    # id is assigned by EvidenceSet on insertion, never by the producing tool, so it is
    # left at its default here.
    item = Evidence(
        source_type="policy",
        source_id="a-document.pdf",
        content="some text",
        locator=PolicyLocator(document="a-document.pdf", page=1),
        provenance=Provenance(tool="search_policy"),
    )

    payload = EvidenceEvent(source="policy", items=[item.to_dict()]).to_json()

    assert payload["event"] == "evidence"
    assert payload["source"] == "policy"
    assert payload["items"] == [item.to_dict()]
