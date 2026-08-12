"""The graph: understand, route, plan, and fan out to only the sources that were routed.

This is where "calls only the tools it needs" stops being a decision and becomes
behaviour. The tests assert on which fake tools were *invoked*, because that is the only
statement of the property that cannot be satisfied by a tool being called and its result
discarded.

The fan-out runs the routed sources concurrently, which makes the state's two reducers
load-bearing rather than decorative: without them two sources finishing in the same
superstep would either conflict or overwrite each other's evidence and stage records.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from vericlaim.evidence import (
    Evidence,
    PolicyLocator,
    Provenance,
    SqlLocator,
)
from vericlaim.orchestrator.graph import SOURCE_STAGE_PREFIX, build_graph, run_question
from vericlaim.orchestrator.sources import SourceCapability
from vericlaim.orchestrator.state import GraphState, RoutingDecision, StageRecord

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
        locator=SqlLocator(
            tables=("ops.claims",), executed_sql="SELECT 1", row_count=398
        ),
        provenance=Provenance(tool="query_claims_db"),
    )


class RecordingTool:
    """A source tool that remembers what it was asked and returns canned evidence."""

    def __init__(
        self,
        evidence: list[Evidence] | None = None,
        *,
        raises: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.evidence = evidence or []
        self.raises = raises
        self.delay = delay
        self.goals: list[str] = []
        self.entered_at: list[float] = []
        self.left_at: list[float] = []

    def __call__(self, goal: str) -> list[Evidence]:
        self.goals.append(goal)
        self.entered_at.append(time.perf_counter())
        if self.delay:
            time.sleep(self.delay)
        self.left_at.append(time.perf_counter())
        if self.raises is not None:
            raise self.raises
        return list(self.evidence)

    @property
    def called(self) -> bool:
        return bool(self.goals)


class ScriptedNodes:
    """Stand-ins for the three model-backed nodes, so the graph is tested on its wiring.

    Each returns the state it is given plus whatever the scenario needs, exactly as the
    real nodes do -- the graph cannot tell the difference, which is the point.
    """

    def __init__(
        self,
        *,
        sources: tuple[str, ...] = ("policy",),
        out_of_scope: bool = False,
        needs_clarification: bool = False,
        answerable: bool = True,
        goals: dict[str, str] | None = None,
    ) -> None:
        self.decision = RoutingDecision(
            sources=() if (out_of_scope or needs_clarification) else sources,
            confidence=0.9,
            reason="Because.",
            out_of_scope=out_of_scope,
            needs_clarification=needs_clarification,
            clarification_question="Which one?" if needs_clarification else "",
        )
        self.answerable = answerable
        self.goals = goals or {}
        self.planned = 0

    def understand(self, state: GraphState, **_: Any) -> GraphState:
        return state.with_(understanding={"query_type": "lookup"}).with_stage(
            StageRecord(name="understand")
        )

    def route(self, state: GraphState, **_: Any) -> GraphState:
        return state.with_(routing=self.decision).with_stage(StageRecord(name="route"))

    def plan(self, state: GraphState, **_: Any) -> GraphState:
        self.planned += 1
        state = state.with_stage(StageRecord(name="plan"))
        if not self.decision.sources:
            return state
        if not self.answerable:
            return state.with_(
                plans={
                    "answerable": False,
                    "unanswerable_reason": "Nothing holds it.",
                    "sub_goals": {},
                }
            )
        return state.with_(
            plans={
                "answerable": True,
                "unanswerable_reason": "",
                "sub_goals": {
                    source: {
                        "goal": self.goals.get(source, f"Ask {source}."),
                        "expected_evidence": "",
                    }
                    for source in self.decision.sources
                },
            }
        )


def run(
    tools: dict[str, Any],
    nodes: ScriptedNodes,
    question: str = QUESTION,
) -> GraphState:
    graph = build_graph(
        tools=tools,
        capabilities=CAPABILITIES,
        understand=nodes.understand,
        route=nodes.route,
        plan=nodes.plan,
    )
    return run_question(graph, question)


# ------------------------------------------------------------------ fan-out


def test_only_the_routed_source_is_called() -> None:
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy",)))

    assert tools["policy"].called
    assert not any(tools[name].called for name in ("sql", "spreadsheet", "scanned_pdf"))
    assert len(state.evidence.items) == 1


def test_two_routed_sources_both_contribute_to_one_set_of_evidence() -> None:
    tools = {
        "policy": RecordingTool([policy_evidence()]),
        "sql": RecordingTool([sql_evidence()]),
        "spreadsheet": RecordingTool(),
        "scanned_pdf": RecordingTool(),
    }

    state = run(tools, ScriptedNodes(sources=("policy", "sql")))

    assert [item.id for item in state.evidence.items] == ["E1", "E2"]
    assert set(state.evidence.source_types()) == {"policy", "sql"}
    assert not tools["spreadsheet"].called


def test_each_source_is_asked_its_own_sub_goal_not_the_question() -> None:
    """The tool never sees the original question. A source handed the whole thing
    answers the fraction it can and stays silent about the rest."""
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}
    nodes = ScriptedNodes(
        sources=("policy", "sql"),
        goals={"policy": "What does the wording state?", "sql": "How often?"},
    )

    run(tools, nodes)

    assert tools["policy"].goals == ["What does the wording state?"]
    assert tools["sql"].goals == ["How often?"]


def test_independent_sources_run_at_the_same_time() -> None:
    """Four sources one after another is four round trips the asker waits through. The
    overlap is what makes the fan-out worth having."""
    tools = {
        "policy": RecordingTool([policy_evidence()], delay=0.15),
        "sql": RecordingTool([sql_evidence()], delay=0.15),
        "spreadsheet": RecordingTool(),
        "scanned_pdf": RecordingTool(),
    }

    run(tools, ScriptedNodes(sources=("policy", "sql")))

    started_second = max(tools["policy"].entered_at[0], tools["sql"].entered_at[0])
    finished_first = min(tools["policy"].left_at[0], tools["sql"].left_at[0])
    assert started_second < finished_first


def test_the_evidence_of_four_concurrent_sources_all_survives() -> None:
    """The reducers are what make this true. Assignment would have the last branch to
    finish overwrite the rest, and nothing would look broken."""
    tools = {
        name: RecordingTool([policy_evidence(f"From {name}.")]) for name in CAPABILITIES
    }

    state = run(tools, ScriptedNodes(sources=tuple(CAPABILITIES)))

    assert len(state.evidence.items) == 4
    assert [item.id for item in state.evidence.items] == ["E1", "E2", "E3", "E4"]


def test_every_source_that_ran_is_recorded_as_its_own_stage() -> None:
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy", "sql")))

    names = [stage.name for stage in state.stages]
    assert names[:3] == ["understand", "route", "plan"]
    assert set(names[3:]) == {
        f"{SOURCE_STAGE_PREFIX}policy",
        f"{SOURCE_STAGE_PREFIX}sql",
    }


# ------------------------------------------------------------------ a source that fails


def test_one_source_failing_does_not_lose_the_others() -> None:
    """A source being unreachable is a gap in the evidence, not the end of the question.
    The answer has to be able to say which source it could not reach."""
    tools = {
        "policy": RecordingTool(raises=RuntimeError("index is missing")),
        "sql": RecordingTool([sql_evidence()]),
        "spreadsheet": RecordingTool(),
        "scanned_pdf": RecordingTool(),
    }

    state = run(tools, ScriptedNodes(sources=("policy", "sql")))

    assert len(state.evidence.items) == 1
    assert state.failures == (f"{SOURCE_STAGE_PREFIX}policy: index is missing",)


def test_a_source_with_no_tool_behind_it_is_reported_not_crashed() -> None:
    """A misconfigured registry is a failure of this run, named on the stage, rather
    than an exception out of the middle of a fan-out."""
    tools = {"sql": RecordingTool([sql_evidence()])}

    state = run(tools, ScriptedNodes(sources=("policy", "sql")))

    assert len(state.evidence.items) == 1
    assert any("policy" in failure for failure in state.failures)


def test_a_source_that_returns_nothing_is_not_a_failure() -> None:
    tools = {name: RecordingTool([]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy",)))

    assert state.evidence.items == ()
    assert state.failures == ()
    assert state.stages[-1].detail["evidence"] == 0


# ------------------------------------------------------------------ not fanning out


def test_a_question_the_router_turned_away_calls_nothing() -> None:
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(out_of_scope=True))

    assert not any(tool.called for tool in tools.values())
    assert state.evidence.items == ()
    assert state.routing is not None and state.routing.out_of_scope


def test_a_question_awaiting_clarification_calls_nothing() -> None:
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(needs_clarification=True))

    assert not any(tool.called for tool in tools.values())
    assert state.routing is not None and state.routing.needs_clarification


def test_a_question_the_planner_declined_calls_nothing() -> None:
    """The answerability gate is upstream of every tool call, so declining costs the
    planning call and nothing else."""
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy",), answerable=False))

    assert not any(tool.called for tool in tools.values())
    assert state.plans["answerable"] is False


def test_a_plan_that_failed_outright_calls_nothing() -> None:
    """No plan means no sub-goal, and a tool called without one would be handed the raw
    question -- exactly what the plan node exists to prevent."""
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}
    nodes = ScriptedNodes(sources=("policy",))
    nodes.plan = lambda state, **_: state  # type: ignore[assignment]

    state = run(tools, nodes)

    assert not any(tool.called for tool in tools.values())
    assert state.plans == {}


# ------------------------------------------------------------------ the run


def test_the_run_returns_a_validated_state_not_a_bag_of_keys() -> None:
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy",)))

    assert isinstance(state, GraphState)
    assert state.question == QUESTION
    assert state.understanding["query_type"] == "lookup"


def test_the_stages_are_recorded_once_each() -> None:
    """A node that ran once and was recorded twice makes the cost figure wrong and the
    trace unreadable."""
    tools = {name: RecordingTool([policy_evidence()]) for name in CAPABILITIES}

    state = run(tools, ScriptedNodes(sources=("policy", "sql")))

    names = [stage.name for stage in state.stages]
    assert len(names) == len(set(names))


def test_a_blank_question_never_reaches_a_model() -> None:
    with pytest.raises(ValueError, match="question"):
        run_question(build_graph(tools={}, capabilities=CAPABILITIES), "   ")


def test_the_tools_are_called_on_the_worker_threads_the_graph_provides() -> None:
    """Nothing here is async. The tools are ordinary blocking calls -- a database
    round trip, an embedding, an OCR read -- and the fan-out has to run them without
    the caller arranging anything."""
    seen: list[int] = []

    def tool(goal: str) -> list[Evidence]:
        seen.append(threading.get_ident())
        return [policy_evidence()]

    run(
        {"policy": tool, "sql": tool, "spreadsheet": tool, "scanned_pdf": tool},
        ScriptedNodes(sources=("policy", "sql")),
    )

    assert len(seen) == 2
