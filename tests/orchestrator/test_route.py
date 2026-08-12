"""Choosing which of four sources a question needs, from their reviewed descriptions.

The router is where "calls only the tools it needs" is decided, and where an out-of-scope
question is turned away before anything is executed. Its decision is checked here rather
than trusted: a source name the model invented cannot be called, and a decision that both
refuses the question and names sources for it means something other than what it says.

The two properties the tests exist to hold:

* **Nothing is routed by recognising the question.** The decision comes from the
  capability descriptions supplied as data, which is why the same question routes
  differently when the descriptions change.
* **A contradiction is resolved deterministically and recorded**, never silently. Where
  the model refuses and routes at once, the refusal wins and the dropped sources are
  named on the stage, so the trace shows what was discarded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.gateway.providers import strictify_schema
from vericlaim.gateway.types import (
    AllProvidersFailedError,
    BudgetExceededError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.nodes.route import (
    ROUTE_SCHEMA,
    ROUTE_SYSTEM_PROMPT,
    ROUTE_TASK,
    route,
)
from vericlaim.orchestrator.sources import SOURCES_FILE, SourceCapability, load_capabilities
from vericlaim.orchestrator.state import GraphState
from vericlaim.sql.contexts import load_contexts

QUESTION = "What does the wording say about escape of water, and how often did it happen?"


def capability(name: str, **overrides: Any) -> SourceCapability:
    values: dict[str, Any] = {
        "name": name,
        "tool": f"tool_{name}",
        "title": name.title(),
        "holds": f"The {name} material.",
        "answers": (f"questions about {name}",),
        "cannot_answer": ("anything else",),
        "citation": "somewhere",
    }
    values.update(overrides)
    return SourceCapability(**values)


CAPABILITIES = {
    name: capability(name)
    for name in ("policy", "sql", "spreadsheet", "scanned_pdf")
}


@dataclass
class _Completion:
    text: str
    parsed: Any
    task: str = ROUTE_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    cost_usd: float = 0.0
    latency_ms: float = 180.0
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
        "sources": ["policy"],
        "confidence": 0.9,
        "reason": "The question asks what the wording states.",
        "out_of_scope": False,
        "needs_clarification": False,
        "clarification_question": "",
    }
    base.update(overrides)
    return base


def routed(gateway: FakeGateway, state: GraphState | None = None, **kwargs: Any):
    state = state or GraphState(question=QUESTION)
    return route(state, capabilities=CAPABILITIES, gateway=gateway, **kwargs)


def sent_payload(gateway: FakeGateway) -> dict[str, Any]:
    return json.loads(gateway.calls[0][1][-1]["content"])


# ------------------------------------------------------------------ the decision


def test_the_question_is_routed_to_the_named_sources() -> None:
    gateway = FakeGateway(payload(sources=["policy", "sql"]))

    state = routed(gateway)

    assert state.routing is not None
    assert state.routing.sources == ("policy", "sql")
    assert state.routing.confidence == 0.9
    assert not state.routing.out_of_scope


def test_the_routed_set_is_ordered_the_way_the_capabilities_are() -> None:
    """Two runs that chose the same sources must produce the same decision. Order that
    follows whatever the model typed makes an eval comparison depend on nothing."""
    gateway = FakeGateway(payload(sources=["scanned_pdf", "policy", "sql"]))

    state = routed(gateway)

    assert state.routing is not None
    assert state.routing.sources == ("policy", "sql", "scanned_pdf")


def test_the_capabilities_and_the_understanding_reach_the_model_as_data() -> None:
    gateway = FakeGateway(payload())
    state = GraphState(
        question=QUESTION, understanding={"query_type": "lookup", "entities": []}
    )

    routed(gateway, state)

    sent = sent_payload(gateway)
    assert sent["question"] == QUESTION
    assert sent["understanding"]["query_type"] == "lookup"
    assert [source["name"] for source in sent["sources"]] == [
        "policy",
        "sql",
        "spreadsheet",
        "scanned_pdf",
    ]
    assert "cannot_answer" in sent["sources"][0]


def test_the_same_question_routes_differently_when_the_sources_do() -> None:
    """The proof that nothing is routed by recognising the question: identical text,
    different capabilities, different decision -- and the node itself never inspects a
    word of the question."""
    gateway = FakeGateway(payload(sources=["policy"]))
    other = FakeGateway(payload(sources=["sql"]))

    first = routed(gateway)
    second = route(
        GraphState(question=QUESTION),
        capabilities={"sql": CAPABILITIES["sql"]},
        gateway=other,
    )

    assert first.routing is not None and second.routing is not None
    assert first.routing.sources == ("policy",)
    assert second.routing.sources == ("sql",)


def test_a_confidence_outside_the_range_is_brought_back_into_it() -> None:
    assert routed(FakeGateway(payload(confidence=1.7))).routing.confidence == 1.0
    assert routed(FakeGateway(payload(confidence=-0.2))).routing.confidence == 0.0


# ------------------------------------------------------------------ refusing


def test_a_question_no_source_covers_is_turned_away_before_any_tool_runs() -> None:
    gateway = FakeGateway(
        payload(
            sources=[],
            out_of_scope=True,
            needs_clarification=True,
            clarification_question="Which of the covered subjects did you mean?",
            reason="Nothing here holds staff salary information.",
        )
    )

    state = routed(gateway)

    assert state.routing is not None
    assert state.routing.out_of_scope
    assert state.routing.sources == ()
    assert "salary" in state.routing.reason


def test_a_refusal_that_names_nothing_missing_is_not_a_refusal() -> None:
    """"Out of scope" with no reason cannot be shown to anyone. It is a broken reply, and
    treating it as a refusal would put an empty explanation in front of the asker."""
    gateway = FakeGateway(payload(sources=[], out_of_scope=True, reason="  "))

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed


def test_refusing_and_routing_at_once_resolves_to_the_refusal() -> None:
    """A contradiction, resolved one way deterministically and recorded. Fanning out to
    sources the router has just said cannot cover the question spends four tool calls to
    produce evidence for an answer it will not give."""
    gateway = FakeGateway(
        payload(sources=["policy", "sql"], out_of_scope=True, reason="Nothing covers it.")
    )

    state = routed(gateway)

    assert state.routing is not None
    assert state.routing.sources == ()
    assert state.stages[-1].detail["dropped_sources"] == ["policy", "sql"]


def test_asking_for_clarification_stops_the_run_rather_than_guessing() -> None:
    gateway = FakeGateway(
        payload(
            sources=["policy"],
            needs_clarification=True,
            clarification_question="Which product did you mean?",
        )
    )

    state = routed(gateway)

    assert state.routing is not None
    assert state.routing.needs_clarification
    assert state.routing.sources == ()
    assert state.routing.clarification_question == "Which product did you mean?"


def test_asking_for_clarification_without_a_question_is_a_broken_reply() -> None:
    gateway = FakeGateway(
        payload(sources=[], needs_clarification=True, clarification_question=" ")
    )

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed


def test_routing_nowhere_without_saying_why_is_a_broken_reply() -> None:
    """No sources, no refusal, no question to put back to the asker. There is nothing
    the graph could do with this that is not made up."""
    gateway = FakeGateway(payload(sources=[]))

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed


# ------------------------------------------------------------------ what cannot be called


def test_a_source_that_does_not_exist_is_not_quietly_dropped() -> None:
    """Dropping it would leave a routing set that looks deliberate. The reply named a
    source nobody described, so the whole decision is untrustworthy."""
    gateway = FakeGateway(payload(sources=["policy", "emails"]))

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed
    assert "emails" in state.stages[-1].error


def test_the_same_source_twice_is_a_broken_reply() -> None:
    gateway = FakeGateway(payload(sources=["policy", "policy"]))

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_the_decision_and_what_it_cost() -> None:
    gateway = FakeGateway(payload(sources=["policy", "sql"]))

    state = routed(gateway)

    stage = state.stages[-1]
    assert stage.name == "route"
    assert stage.detail["sources"] == ["policy", "sql"]
    assert stage.model == "gemini-3.5-flash-lite"
    assert stage.latency_ms == 180.0
    assert not stage.failed


def test_a_provider_failure_is_recorded_rather_than_raised() -> None:
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            ROUTE_TASK, [("gemini", "gemini-3.5-flash-lite", RuntimeError("down"))]
        )
    )

    state = routed(gateway)

    assert state.routing is None
    assert state.stages[-1].failed


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceededError("request", 0.25, 0.25),
        QuotaExhaustedError("spent", provider="gemini", model="gemini-3.5-flash-lite"),
    ],
)
def test_running_out_of_money_or_quota_stops_the_run(error: Exception) -> None:
    with pytest.raises(type(error)):
        routed(FakeGateway(raises=error))


def test_routing_with_no_capabilities_at_all_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="capabilit"):
        route(GraphState(question=QUESTION), capabilities={}, gateway=FakeGateway())


# ------------------------------------------------------------------ the contract


def test_the_schema_survives_strict_structured_output() -> None:
    assert strictify_schema(ROUTE_SCHEMA) == ROUTE_SCHEMA


def test_the_prompt_names_none_of_the_sources_it_routes_to() -> None:
    """They arrive as data. A prompt that named them would route to the four it knows
    about and would have to be edited to add a fifth."""
    named = sorted(
        name
        for name in load_capabilities(SOURCES_FILE)
        if re.search(rf"\b{re.escape(name)}\b", ROUTE_SYSTEM_PROMPT, re.I)
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
        if re.search(rf"\b{re.escape(identifier)}\b", ROUTE_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_asks_for_the_minimum_sufficient_set() -> None:
    """Every extra source is a tool call, a set of evidence to reconcile, and latency the
    asker waits through."""
    assert "minimum" in ROUTE_SYSTEM_PROMPT.lower()


def test_the_prompt_makes_the_stated_limits_binding() -> None:
    assert "cannot_answer" in ROUTE_SYSTEM_PROMPT
