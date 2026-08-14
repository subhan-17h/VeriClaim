"""stream_question drives the graph and reports what it did, event by event."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.api.protocol import EvidenceEvent, Final, RunStarted, Stage
from vericlaim.evidence import Evidence, EvidenceSet, PolicyLocator, Provenance
from vericlaim.orchestrator.graph import stream_question
from vericlaim.orchestrator.state import GraphState, StageRecord


@dataclass
class FakeLedger:
    total_cost_usd: float = 0.0


@dataclass
class FakeGateway:
    ledger: FakeLedger = field(default_factory=FakeLedger)


@dataclass
class FakeGraph:
    """Yields the accumulated state after each node, as stream_mode='values' does."""

    values: list[dict[str, Any]]

    def stream(self, start: GraphState, **config: Any) -> Any:
        self.seen_start = start
        self.seen_config = config
        return iter(self.values)


def _evidence(document: str) -> Evidence:
    # EvidenceSet assigns the citation id on insertion, so none is passed here.
    return Evidence(
        source_type="policy",
        source_id=document,
        content="some text",
        locator=PolicyLocator(document=document, page=1),
        provenance=Provenance(tool="search_policy"),
    )


def _state(**fields: Any) -> dict[str, Any]:
    return dict(GraphState(question="a question", **fields))


def test_the_first_event_names_the_run_and_the_question() -> None:
    graph = FakeGraph([_state()])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))

    assert isinstance(events[0], RunStarted)
    assert events[0].question == "a question"
    assert events[0].trace_id


def test_each_new_stage_is_reported_once() -> None:
    first = StageRecord(name="understand")
    second = StageRecord(name="route")
    graph = FakeGraph([_state(stages=(first,)), _state(stages=(first, second))])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))
    stages = [event for event in events if isinstance(event, Stage)]

    assert [stage.name for stage in stages] == ["understand", "route"]


def test_new_evidence_is_reported_as_it_arrives() -> None:
    # Two distinct documents, because EvidenceSet deduplicates identical items.
    one = EvidenceSet([_evidence("one.pdf")])
    two = EvidenceSet([_evidence("one.pdf"), _evidence("two.pdf")])
    graph = FakeGraph([_state(evidence=one), _state(evidence=two)])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))
    evidence_events = [event for event in events if isinstance(event, EvidenceEvent)]

    assert sum(len(event.items) for event in evidence_events) == 2


def test_the_last_event_is_final_and_reports_the_ledger_cost() -> None:
    gateway = FakeGateway(FakeLedger(total_cost_usd=42.0))
    graph = FakeGraph(
        [_state(answer="an answer", stages=(StageRecord(name="x", cost_usd=1.0),))]
    )

    events = list(stream_question(graph, "a question", gateway=gateway))

    assert isinstance(events[-1], Final)
    assert events[-1].to_json()["cost_usd"] == 42.0


def test_a_blank_question_is_refused_before_the_graph_is_touched() -> None:
    graph = FakeGraph([])

    with pytest.raises(ValueError):
        list(stream_question(graph, "   ", gateway=FakeGateway()))


def test_the_graph_is_asked_for_accumulated_values() -> None:
    graph = FakeGraph([_state()])

    list(stream_question(graph, "a question", gateway=FakeGateway()))

    assert graph.seen_config["stream_mode"] == "values"
