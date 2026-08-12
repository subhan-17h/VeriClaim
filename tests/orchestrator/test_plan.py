"""Turning a routed question into one sub-goal per source, or declining it.

The plan node is the last place a question can be declined before tools run, and the
only place that says what each source is actually being asked for. Two failures it
exists to prevent:

* **A source routed but never asked.** The router said the question needs it; a plan
  that omits it produces an answer missing evidence nobody will notice is missing,
  because the gap is in what was never gathered.
* **A source asked for something it was not routed for.** A sub-goal naming a source the
  router did not choose is a tool call the routing decision never authorised.

Declining is a first-class outcome. A question whose sources between them cannot supply
what it asks for is better refused here, at the cost of one call, than answered from
whatever the four tools happen to return.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.gateway.providers import strictify_schema
from vericlaim.gateway.types import AllProvidersFailedError, BudgetExceededError
from vericlaim.orchestrator.nodes.plan import (
    PLAN_SCHEMA,
    PLAN_SYSTEM_PROMPT,
    PLAN_TASK,
    plan,
)
from vericlaim.orchestrator.sources import SOURCES_FILE, SourceCapability, load_capabilities
from vericlaim.orchestrator.state import GraphState, RoutingDecision
from vericlaim.sql.contexts import load_contexts

QUESTION = "What does the wording say about escape of water, and how often did it happen?"


def capability(name: str) -> SourceCapability:
    return SourceCapability(
        name=name,
        tool=f"tool_{name}",
        title=name.title(),
        holds=f"The {name} material.",
        answers=(f"questions about {name}",),
        cannot_answer=("anything else",),
        citation="somewhere",
    )


CAPABILITIES = {
    name: capability(name) for name in ("policy", "sql", "spreadsheet", "scanned_pdf")
}


def routed_state(*sources: str, **overrides: Any) -> GraphState:
    decision = RoutingDecision(
        sources=tuple(sources), confidence=0.9, reason="Because.", **overrides
    )
    return GraphState(question=QUESTION, routing=decision)


@dataclass
class _Completion:
    text: str
    parsed: Any
    task: str = PLAN_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    cost_usd: float = 0.0
    latency_ms: float = 900.0
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


def sub_goal(source: str, goal: str | None = None, **overrides: Any) -> dict[str, Any]:
    entry = {
        "source": source,
        "goal": goal or f"Find what {source} holds on the subject.",
        "expected_evidence": f"A passage from {source}.",
    }
    entry.update(overrides)
    return entry


def payload(sub_goals: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    base = {
        "answerable": True,
        "unanswerable_reason": "",
        "sub_goals": [sub_goal("policy")] if sub_goals is None else sub_goals,
        "expected_answer_shape": "A statement of the term with a supporting count.",
    }
    base.update(overrides)
    return base


def planned(gateway: FakeGateway, state: GraphState | None = None, **kwargs: Any):
    return plan(
        state or routed_state("policy"),
        capabilities=CAPABILITIES,
        gateway=gateway,
        **kwargs,
    )


def sent_payload(gateway: FakeGateway) -> dict[str, Any]:
    return json.loads(gateway.calls[0][1][-1]["content"])


# ------------------------------------------------------------------ the plan


def test_each_routed_source_gets_one_sub_goal() -> None:
    gateway = FakeGateway(payload([sub_goal("policy"), sub_goal("sql")]))

    state = planned(gateway, routed_state("policy", "sql"))

    assert state.plans["answerable"] is True
    assert list(state.plans["sub_goals"]) == ["policy", "sql"]
    assert state.plans["sub_goals"]["sql"]["goal"]


def test_the_sub_goals_are_ordered_the_way_the_routing_was() -> None:
    gateway = FakeGateway(payload([sub_goal("sql"), sub_goal("policy")]))

    state = planned(gateway, routed_state("policy", "sql"))

    assert list(state.plans["sub_goals"]) == ["policy", "sql"]


def test_the_routed_capabilities_reach_the_model_as_data() -> None:
    gateway = FakeGateway(payload())
    state = routed_state("policy")

    planned(gateway, state)

    sent = sent_payload(gateway)
    assert sent["question"] == QUESTION
    assert [source["name"] for source in sent["sources"]] == ["policy"]
    assert sent["routing_reason"] == "Because."


def test_only_the_routed_sources_are_described_to_the_planner() -> None:
    """Describing the others invites a sub-goal for a source the router excluded."""
    gateway = FakeGateway(payload())

    planned(gateway, routed_state("policy"))

    assert [source["name"] for source in sent_payload(gateway)["sources"]] == ["policy"]


def test_a_retry_hint_is_carried_into_the_next_attempt() -> None:
    gateway = FakeGateway(payload())

    planned(gateway, retry_hint="Nothing was found on the deductible.")

    assert sent_payload(gateway)["retry_hint"] == "Nothing was found on the deductible."


# ------------------------------------------------------------------ declining


def test_a_question_the_routed_sources_cannot_answer_is_declined() -> None:
    gateway = FakeGateway(
        payload(
            [],
            answerable=False,
            unanswerable_reason="No source holds a figure for staff pay.",
        )
    )

    state = planned(gateway)

    assert state.plans["answerable"] is False
    assert "staff pay" in state.plans["unanswerable_reason"]
    assert state.plans["sub_goals"] == {}


def test_declining_without_a_reason_is_a_broken_reply() -> None:
    gateway = FakeGateway(payload([], answerable=False, unanswerable_reason=" "))

    state = planned(gateway)

    assert state.plans == {}
    assert state.stages[-1].failed


def test_declining_while_planning_work_resolves_to_the_refusal() -> None:
    gateway = FakeGateway(
        payload(
            [sub_goal("policy")],
            answerable=False,
            unanswerable_reason="Nothing covers it.",
        )
    )

    state = planned(gateway)

    assert state.plans["answerable"] is False
    assert state.plans["sub_goals"] == {}
    assert state.stages[-1].detail["dropped_sub_goals"] == ["policy"]


# ------------------------------------------------------------------ what may be asked


def test_a_source_the_router_excluded_cannot_be_given_work() -> None:
    """The routing decision is what authorises a tool call. A sub-goal outside it is a
    call nobody decided to make."""
    gateway = FakeGateway(payload([sub_goal("policy"), sub_goal("scanned_pdf")]))

    state = planned(gateway, routed_state("policy"))

    assert state.plans == {}
    assert state.stages[-1].failed
    assert "scanned_pdf" in state.stages[-1].error


def test_a_routed_source_left_without_work_is_a_broken_plan() -> None:
    """The router said the question needs it. A plan that skips it produces an answer
    whose gap is in evidence that was never gathered, which nothing downstream can see."""
    gateway = FakeGateway(payload([sub_goal("policy")]))

    state = planned(gateway, routed_state("policy", "sql"))

    assert state.plans == {}
    assert state.stages[-1].failed
    assert "sql" in state.stages[-1].error


def test_a_source_given_two_sub_goals_is_a_broken_plan() -> None:
    gateway = FakeGateway(payload([sub_goal("policy"), sub_goal("policy", "Again.")]))

    state = planned(gateway)

    assert state.plans == {}
    assert state.stages[-1].failed


def test_a_sub_goal_with_nothing_to_ask_is_a_broken_plan() -> None:
    gateway = FakeGateway(payload([sub_goal("policy", goal="  ")]))

    state = planned(gateway)

    assert state.plans == {}
    assert state.stages[-1].failed


def test_planning_no_work_for_a_question_it_calls_answerable_is_a_broken_reply() -> None:
    gateway = FakeGateway(payload([]))

    state = planned(gateway)

    assert state.plans == {}
    assert state.stages[-1].failed


# ------------------------------------------------------------------ nothing to plan


def test_a_question_the_router_turned_away_is_never_planned() -> None:
    """No sources, nothing to ask. Calling the model here spends a request to be told
    what the router already said."""
    gateway = FakeGateway(payload())
    state = routed_state(out_of_scope=True, needs_clarification=True)

    after = plan(state, capabilities=CAPABILITIES, gateway=gateway)

    assert gateway.calls == []
    assert after.plans == {}
    assert after.stages[-1].detail["skipped"]
    assert not after.stages[-1].failed


def test_a_question_awaiting_clarification_is_never_planned() -> None:
    gateway = FakeGateway(payload())
    state = routed_state(needs_clarification=True, clarification_question="Which one?")

    after = plan(state, capabilities=CAPABILITIES, gateway=gateway)

    assert gateway.calls == []
    assert after.plans == {}


def test_planning_before_routing_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="rout"):
        plan(GraphState(question=QUESTION), capabilities=CAPABILITIES, gateway=FakeGateway())


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_the_work_it_planned() -> None:
    gateway = FakeGateway(payload([sub_goal("policy"), sub_goal("sql")]))

    state = planned(gateway, routed_state("policy", "sql"))

    stage = state.stages[-1]
    assert stage.name == "plan"
    assert stage.detail["sub_goals"] == ["policy", "sql"]
    assert stage.model == "gemini-3.5-flash"
    assert stage.latency_ms == 900.0


def test_a_provider_failure_is_recorded_rather_than_raised() -> None:
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            PLAN_TASK, [("gemini", "gemini-3.5-flash", RuntimeError("down"))]
        )
    )

    state = planned(gateway)

    assert state.plans == {}
    assert state.stages[-1].failed


def test_running_out_of_money_stops_the_run() -> None:
    with pytest.raises(BudgetExceededError):
        planned(FakeGateway(raises=BudgetExceededError("total", 5.0, 5.0)))


# ------------------------------------------------------------------ the contract


def test_the_schema_survives_strict_structured_output() -> None:
    assert strictify_schema(PLAN_SCHEMA) == PLAN_SCHEMA


def test_the_prompt_names_none_of_the_sources_it_plans_for() -> None:
    named = sorted(
        name
        for name in load_capabilities(SOURCES_FILE)
        if re.search(rf"\b{re.escape(name)}\b", PLAN_SYSTEM_PROMPT, re.I)
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
        if re.search(rf"\b{re.escape(identifier)}\b", PLAN_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_makes_declining_a_permitted_outcome() -> None:
    assert "answerable" in PLAN_SYSTEM_PROMPT
