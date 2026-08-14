"""One question, one trace, one span per node.

The graph is instrumented twice over by default, and that is the problem this file is
about. Every node carries its own ``@traced`` so it is traced when called directly -- from
the API, from a test, from the plain-Python subsystem that is not a graph at all. Inside
the graph, LangGraph opens a span for the node it is running. Leave both on and the run
tree carries two identically named spans per node, one nested in the other, for every node
of every question.

That is a budget question, not only a tidiness one: the free plan allows 5,000 traces a
month and the whole evaluation suite has to fit inside it.

The rule these tests hold: whoever is going to provide the span, exactly one of them does.
"""

from __future__ import annotations

import functools
import sys
import types
from typing import Any

import pytest

from vericlaim.orchestrator.graph import (
    SourceRequest,
    _spanned_once,
    build_graph,
    run_question,
)
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import (
    is_framework_tracing_enabled,
    is_tracing_enabled,
    traced,
    without_own_span,
)

from .test_graph import CAPABILITIES, RecordingTool, ScriptedNodes, policy_evidence


@pytest.fixture
def tracing_off(monkeypatch):
    for name in (
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "VC_LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def spans(monkeypatch, tracing_off):
    """Records every span our decorator opens, with no network anywhere.

    The stub is the real langsmith module with `traceable` replaced, rather than a bare
    object: LangChain imports other names from it, and a stub missing them turns a
    tracing test into an import error three layers down.
    """
    import langsmith

    opened: list[str] = []

    def traceable(**kwargs):
        def decorate(fn):
            @functools.wraps(fn)
            def inner(*args, **kw):
                opened.append(str(kwargs.get("name")))
                return fn(*args, **kw)

            return inner

        return decorate

    shim = types.ModuleType("langsmith")
    shim.__dict__.update(langsmith.__dict__)
    shim.traceable = traceable  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", shim)
    return opened


# ------------------------------------------------------------------ telling them apart


def test_the_framework_traces_when_it_is_told_to(monkeypatch, tracing_off) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")

    assert is_framework_tracing_enabled() is True
    assert is_tracing_enabled() is True


def test_our_own_flag_does_not_turn_the_framework_on(monkeypatch, tracing_off) -> None:
    """Tracing this project without tracing LangChain's world is a real configuration,
    and in it our span is the only one there is."""
    monkeypatch.setenv("VC_LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")

    assert is_tracing_enabled() is True
    assert is_framework_tracing_enabled() is False


def test_the_framework_flag_without_a_key_traces_nothing(monkeypatch, tracing_off) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert is_framework_tracing_enabled() is False


# ------------------------------------------------------------------ stepping aside


def test_a_traced_function_can_be_called_without_its_span() -> None:
    @traced("understand")
    def node(state: str) -> str:
        return state.upper()

    assert without_own_span(node)("x") == "X"
    assert without_own_span(node) is node.__wrapped__


def test_stepping_aside_keeps_the_dependencies_a_partial_carries() -> None:
    """The nodes reach the graph as partials with their gateway bound. Unwrapping must
    not drop what was bound into them."""

    @traced("route")
    def node(state: str, *, gateway: str = "") -> str:
        return f"{state}:{gateway}"

    bound = functools.partial(node, gateway="fake")

    assert without_own_span(bound)("q") == "q:fake"


def test_something_that_was_never_traced_is_returned_untouched() -> None:
    def node(state: str) -> str:
        return state

    assert without_own_span(node) is node


# ------------------------------------------------------------------ in the graph


def run_graph(nodes: ScriptedNodes, tools: dict[str, Any] | None = None) -> GraphState:
    tools = tools or {
        name: RecordingTool([policy_evidence(f"From {name}.")]) for name in CAPABILITIES
    }
    graph = build_graph(
        tools=tools,
        capabilities=CAPABILITIES,
        understand=nodes.understand,
        route=nodes.route,
        plan=nodes.plan,
        sufficiency=nodes.sufficiency,
        synthesize=nodes.synthesize,
        verify=nodes.verify,
    )
    return run_question(graph, "Is a burst pipe covered?")


class TracedNodes(ScriptedNodes):
    """Scripted nodes wearing the decorator the real ones wear."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.understand = traced("understand")(self.understand)  # type: ignore[method-assign]
        self.route = traced("route")(self.route)  # type: ignore[method-assign]
        self.plan = traced("plan")(self.plan)  # type: ignore[method-assign]


def test_a_node_steps_aside_when_the_framework_is_spanning_it(
    monkeypatch, tracing_off
) -> None:
    """The guard itself. Asserted here rather than through a live graph run, because a
    run with the framework's tracer armed would try to ship the spans somewhere."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")

    @traced("understand")
    def node(state: str) -> str:
        return state

    assert _spanned_once(node) is node.__wrapped__


def test_the_node_keeps_its_span_when_the_framework_is_quiet(
    monkeypatch, tracing_off
) -> None:
    monkeypatch.setenv("VC_LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")

    @traced("understand")
    def node(state: str) -> str:
        return state

    assert _spanned_once(node) is node


def test_every_node_of_a_run_is_spanned_exactly_once(monkeypatch, spans) -> None:
    """End to end, with the framework quiet: one span per node, one root, no repeats."""
    monkeypatch.setenv("VC_LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")

    run_graph(TracedNodes(sources=("policy", "sql")))

    assert spans.count("vericlaim.question") == 1
    assert spans.count("understand") == 1
    assert spans.count("route") == 1
    assert spans.count("plan") == 1


def test_tracing_off_means_no_span_is_opened_at_all(spans) -> None:
    """The property everything else rests on: a traced run and an untraced one are the
    same run."""
    state = run_graph(TracedNodes(sources=("policy",)))

    assert spans == []
    assert state.answer


# ------------------------------------------------------------------ what the trace says


def test_each_node_puts_what_it_did_on_the_trace(monkeypatch) -> None:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.add_run_metadata",
        lambda **fields: recorded.append(fields) or True,
    )

    run_graph(ScriptedNodes(sources=("policy",)))

    stages = [entry["vc_stage"] for entry in recorded if "vc_stage" in entry]
    assert stages[:3] == ["understand", "route", "plan"]
    assert "verify" in stages


def test_the_run_is_summarized_on_the_root_span(monkeypatch) -> None:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.add_run_metadata",
        lambda **fields: recorded.append(fields) or True,
    )

    state = run_graph(ScriptedNodes(sources=("policy", "sql")))

    summary = recorded[-1]
    assert summary["vc_sources_routed"] == ["policy", "sql"]
    assert summary["vc_evidence"] == 2
    assert summary["vc_replans"] == 0
    assert summary["vc_verified"] is True
    assert summary["vc_trace_id"] == state.trace_id


def test_each_source_says_what_it_was_asked_and_what_it_returned(monkeypatch) -> None:
    """A source branch is a node like any other, and the trace is where a reader finds
    out which sub-goal it was handed and how much evidence came back."""
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.add_run_metadata",
        lambda **fields: recorded.append(fields) or True,
    )

    run_graph(ScriptedNodes(sources=("policy", "sql")))

    branches = {
        entry["vc_stage"]: entry
        for entry in recorded
        if str(entry.get("vc_stage", "")).startswith("source.")
    }
    assert set(branches) == {"source.policy", "source.sql"}
    assert branches["source.policy"]["vc_evidence"] == 1
    assert branches["source.sql"]["vc_goal"]


def test_a_source_that_could_not_be_reached_says_so_on_the_trace(monkeypatch) -> None:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.add_run_metadata",
        lambda **fields: recorded.append(fields) or True,
    )

    def broken(_request: SourceRequest) -> list[Any]:
        raise RuntimeError("policy index is missing")

    run_graph(ScriptedNodes(sources=("policy",)), tools={"policy": broken})

    errors = [entry.get("vc_stage_error") for entry in recorded]
    assert "policy index is missing" in errors


def test_a_stage_that_failed_says_so_on_the_trace(monkeypatch) -> None:
    """A source that could not be reached has to be visible in the trace, not only in
    the answer."""
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.add_run_metadata",
        lambda **fields: recorded.append(fields) or True,
    )

    nodes = ScriptedNodes(sources=("policy",))
    nodes.verify = lambda state, **_: state.with_stage(  # type: ignore[assignment]
        StageRecord(name="verify", error="guard unreachable")
    )

    run_graph(nodes)

    errors = [entry.get("vc_stage_error") for entry in recorded if "vc_stage" in entry]
    assert "guard unreachable" in errors
