"""The graph: understand, route, plan, then fan out to only the sources that were routed.

A graph earns its place here for exactly one reason -- the fan-out. The four sources are
independent of each other and wildly different in latency: a database round trip finishes
in milliseconds, an embedding search in tenths of a second, a search over pages read by
character recognition in seconds. Run in sequence, the asker waits for their sum. Run as
branches of one superstep, they cost the slowest of them.

Everything else here is wiring, and deliberately thin:

* **The nodes are ordinary functions of state.** Each takes a :class:`GraphState` and
  returns a new one, and is tested that way without the framework anywhere in sight. This
  module translates each into the update the framework wants -- the fields that actually
  changed, and the stages and evidence that were *added* rather than the totals -- so the
  reducers do the merging and no node has to know it is running beside a sibling.
* **The tools are injected.** Each source is a callable taking one frozen, per-call
  request with its sub-goal, the run's understanding and its trace id, and returning
  evidence. The graph never learns that one talks to Postgres and another to an OCR
  index, and tests drive real fan-out behaviour with fakes.
* **Nothing fans out that was not decided.** The conditional edge is built from the
  routing decision and the plan's sub-goals, so a source reaches its tool only if the
  router chose it and the planner gave it something to ask. A question turned away, one
  awaiting clarification, and one the planner declined all cost nothing beyond the calls
  already made.

A tool that raises is a gap in the evidence, not the end of the question: the failure is
recorded on that source's stage and the other branches finish normally. An answer built
while one source was unreachable is not wrong, but it is incomplete in a way the reader
has to be told about, and :attr:`GraphState.failures` is what tells them.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from vericlaim.api.protocol import Event, EvidenceEvent, Final, RunStarted, Stage
from vericlaim.evidence import Evidence
from vericlaim.orchestrator.nodes.collect import collect as collect_node
from vericlaim.orchestrator.nodes.plan import plan as plan_node
from vericlaim.orchestrator.nodes.route import route as route_node
from vericlaim.orchestrator.nodes.sufficiency import sufficiency as sufficiency_node
from vericlaim.orchestrator.nodes.synthesize import synthesize as synthesize_node
from vericlaim.orchestrator.nodes.understand import understand as understand_node
from vericlaim.orchestrator.nodes.verify import verify as verify_node
from vericlaim.orchestrator.sources import SourceCapability, load_capabilities
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import (
    add_run_metadata,
    is_framework_tracing_enabled,
    traced,
    without_own_span,
)

# Source branches share the state's stage list with the orchestration nodes, so their
# records carry a prefix. Without it a source named like a node would be indistinguishable
# from it in the trace and in the failure list. The separator is a dot rather than the
# more obvious colon because the graph reserves ':' inside node names.
SOURCE_STAGE_PREFIX = "source."


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """Everything one source needs for one branch call.

    The four sources fan out concurrently as branches of one LangGraph superstep. A
    mutable per-run channel between the graph and its tools would therefore be a race;
    a frozen value constructed at call time gives every branch its own complete request
    instead.
    """

    goal: str
    understanding: Mapping[str, Any] | None = None
    trace_id: str | None = None


# Evidence is still the only thing that crosses back over the tool boundary. The graph
# never admits raw source output into synthesis.
SourceTool = Callable[[SourceRequest], Sequence[Evidence]]

Node = Callable[..., GraphState]


def build_graph(
    *,
    tools: Mapping[str, SourceTool],
    capabilities: Mapping[str, SourceCapability] | None = None,
    understand: Node | None = None,
    route: Node | None = None,
    plan: Node | None = None,
    collect: Node | None = None,
    sufficiency: Node | None = None,
    synthesize: Node | None = None,
    verify: Node | None = None,
    gateway: Any | None = None,
) -> Any:
    """Compile the graph for one question, over the given source tools.

    Every collaborator is injectable because every one of them is expensive or external.
    The defaults are the real nodes bound to the real capabilities.
    """
    if capabilities is None:
        capabilities = load_capabilities()

    understand = understand or functools.partial(understand_node, gateway=gateway)
    route = route or functools.partial(
        route_node, capabilities=capabilities, gateway=gateway
    )
    plan = plan or functools.partial(plan_node, capabilities=capabilities, gateway=gateway)
    collect = collect or collect_node
    sufficiency = sufficiency or functools.partial(sufficiency_node, gateway=gateway)
    synthesize = synthesize or functools.partial(synthesize_node, gateway=gateway)
    verify = verify or functools.partial(verify_node, gateway=gateway)

    graph = StateGraph(GraphState)
    graph.add_node("understand", _as_graph_node(understand))
    graph.add_node("route", _as_graph_node(route))
    graph.add_node("plan", _plan_graph_node(plan))
    graph.add_node("collect", _as_graph_node(collect))
    graph.add_node("sufficiency", _as_graph_node(sufficiency))
    graph.add_node("synthesize", _as_graph_node(synthesize))
    # Verification's one regeneration happens inside the node rather than as a second
    # edge into synthesis. It is a correction of one answer, not another pass over the
    # question, and modelling it as a cycle would let it interleave with the replan loop.
    graph.add_node("verify", _as_graph_node(verify))
    for source in capabilities:
        graph.add_node(_node_name(source), _source_node(source, tools))

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "route")
    graph.add_edge("route", "plan")
    graph.add_conditional_edges(
        "plan",
        functools.partial(_fan_out, capabilities=capabilities),
        {
            **{source: _node_name(source) for source in capabilities},
            "synthesize": "synthesize",
        },
    )
    # Every branch converges on collect, which is where the separate returns become one
    # ordered body of evidence and one account of what is missing from it.
    for source in capabilities:
        graph.add_edge(_node_name(source), "collect")
    graph.add_edge("collect", "sufficiency")
    # The loop-back edge, and the only cycle in the graph. Sufficiency has already
    # applied the bound, so this edge only asks what it decided.
    graph.add_conditional_edges(
        "sufficiency", _replan, {"plan": "plan", "synthesize": "synthesize"}
    )
    graph.add_edge("synthesize", "verify")
    graph.add_edge("verify", END)

    return graph.compile()


@traced("vericlaim.question", run_type="chain", tags=["orchestrator"])
def run_question(graph: Any, question: str, **config: Any) -> GraphState:
    """Run one question through a compiled graph and return the finished state.

    One question is one trace. The root span is opened here rather than around the graph
    invocation inside it, so the run tree covers the whole question -- including the
    validation that rejects a blank one before any model is reached.
    """
    # Constructed rather than passed as a dict so a blank question fails here, before a
    # model is called, with the same error every other entry point gives.
    start = GraphState(question=question, trace_id=uuid.uuid4().hex)
    state = GraphState(**graph.invoke(start, **config))
    _trace_run(state)
    return state


@traced("vericlaim.question.stream", run_type="chain", tags=["orchestrator"])
def stream_question(
    graph: Any, question: str, *, gateway: Any, **config: Any
) -> Iterator[Event]:
    """Run one question and report what it did as it did it.

    A sibling to ``run_question`` rather than a second entry point built elsewhere: the
    root span, the trace id and the run summary are defined once, so a streamed run and
    an awaited one cannot disagree about what a run is.

    ``gateway`` is required because the final event carries the ledger's cost. Making it
    optional would let a caller silently emit the state's own total, which counts no
    model call a source tool made.

    The graph is asked for accumulated values rather than per-node updates, so the final
    state is the last thing yielded and no reducer has to be reimplemented here to
    rebuild it.
    """
    start = GraphState(question=question, trace_id=uuid.uuid4().hex)
    yield RunStarted(trace_id=start.trace_id, question=start.question)

    reported_stages = 0
    reported_evidence = 0
    last: dict[str, Any] | None = None

    for value in graph.stream(start, stream_mode="values", **config):
        last = value

        stages = tuple(value.get("stages") or ())
        for record in stages[reported_stages:]:
            yield Stage.from_record(record)
        reported_stages = len(stages)

        evidence = value.get("evidence")
        items = list(evidence.items) if evidence is not None else []
        for item in items[reported_evidence:]:
            yield EvidenceEvent(source=item.source_type, items=[item.to_dict()])
        reported_evidence = len(items)

    if last is None:
        raise RuntimeError("The graph produced no state, so there is nothing to report")

    state = GraphState(**last)
    _trace_run(state)
    yield Final.from_state(state, cost_usd=gateway.ledger.total_cost_usd)


def _trace_run(state: GraphState) -> None:
    """Summarize the finished run on the root span.

    What a reader of the trace wants first: which sources were consulted, what it cost,
    whether the answer was checked, and whether the graph went round again.
    """
    add_run_metadata(
        vc_trace_id=state.trace_id,
        vc_sources_routed=list(state.routing.sources) if state.routing else [],
        vc_sources_used=list(state.sources_used),
        vc_evidence=len(state.evidence.items),
        vc_replans=state.replans,
        vc_verified=bool(state.citations.get("verified")),
        vc_degraded=bool(state.citations.get("degraded")),
        vc_failures=list(state.failures),
        vc_cost_usd=round(state.total_cost_usd, 6),
        vc_latency_ms=round(state.total_latency_ms, 2),
    )


def _replan(state: GraphState) -> str:
    """Go round again only if the sufficiency node said so.

    The bound lives with the verdict rather than here: this edge must not be able to
    disagree with what was recorded on the state and shown in the trace.
    """
    return "plan" if state.sufficiency.get("replan") else "synthesize"


def _plan_graph_node(plan: Node) -> Callable[[GraphState], dict[str, Any]]:
    """Adapt the plan node, carrying in what an earlier pass failed to find.

    The hint is read off the state rather than bound at build time, because on the
    second pass it is a thing the first pass discovered.
    """

    def run(state: GraphState) -> dict[str, Any]:
        hint = str(state.sufficiency.get("retry_hint", ""))
        after = _spanned_once(plan)(state, retry_hint=hint)
        _trace_stage(state, after)
        return _update(state, after)

    return run


def _node_name(source: str) -> str:
    return f"{SOURCE_STAGE_PREFIX}{source}"


def _fan_out(
    state: GraphState, *, capabilities: Mapping[str, SourceCapability]
) -> list[str]:
    """Return the sources to run, or synthesis when the question stops here.

    Two conditions have to hold for a source to be reached: the router chose it, and the
    planner wrote it a sub-goal. Either one missing means the tool would be called with
    nothing decided about what to ask it.

    A question that stops here still goes to synthesis, which writes the refusal from
    what the deciding node recorded. Ending the run instead would leave the asker with
    no answer at all where there is a perfectly good thing to tell them.
    """
    if state.routing is None or not state.routing.sources:
        return ["synthesize"]
    if not state.plans.get("answerable"):
        return ["synthesize"]

    sub_goals = state.plans.get("sub_goals") or {}
    routed = [
        source
        for source in state.routing.sources
        if source in capabilities and sub_goals.get(source, {}).get("goal")
    ]
    return routed or ["synthesize"]


def _as_graph_node(node: Node) -> Callable[[GraphState], dict[str, Any]]:
    """Adapt a plain state-to-state node into the update the framework applies."""

    def run(state: GraphState) -> dict[str, Any]:
        after = _spanned_once(node)(state)
        _trace_stage(state, after)
        return _update(state, after)

    return run


def _spanned_once(node: Node) -> Node:
    """The node, traced exactly once.

    Every node carries its own ``@traced`` so it is traced when called directly -- from
    the API, from a test, from the plain-Python subsystem. Inside the graph, LangGraph
    already opens a span for the node it is running, and leaving ours on would put two
    identically named spans in the run tree, one inside the other, for every node of
    every question. On a plan whose trace allowance the whole evaluation suite has to fit
    inside, that is a budget question as much as a legibility one.
    """
    return without_own_span(node) if is_framework_tracing_enabled() else node


def _trace_stage(before: GraphState, after: GraphState) -> None:
    """Attach what the node just did to whichever span is covering it."""
    added = after.stages[len(before.stages) :]
    if added:
        _trace_stage_record(added[-1])


def _trace_stage_record(stage: StageRecord) -> None:
    add_run_metadata(
        vc_stage=stage.name,
        vc_stage_model=stage.model,
        vc_stage_cost_usd=round(stage.cost_usd, 6),
        vc_stage_latency_ms=round(stage.latency_ms, 2),
        vc_stage_error=stage.error,
        **{f"vc_{key}": value for key, value in stage.detail.items()},
    )


def _source_node(
    source: str, tools: Mapping[str, SourceTool]
) -> Callable[[GraphState], dict[str, Any]]:
    """Build the branch that asks one source its sub-goal."""
    stage_name = _node_name(source)

    def run(state: GraphState) -> dict[str, Any]:
        # A branch builds its channel update directly rather than returning a state, so
        # it cannot go through _trace_stage. It belongs on the trace all the same: which
        # sub-goal a source was handed, how much evidence came back, and why none did
        # are the first things a reader of the trace looks for.
        update = ask(state)
        for stage in update.get("stages", ()):
            _trace_stage_record(stage)
        return update

    def ask(state: GraphState) -> dict[str, Any]:
        goal = str((state.plans.get("sub_goals") or {}).get(source, {}).get("goal", ""))
        tool = tools.get(source)
        if tool is None:
            return _failed(
                stage_name,
                f"No tool is registered for source {source!r}, so it was routed and "
                "planned for but could not be called",
            )

        request = SourceRequest(
            goal=goal,
            understanding=state.understanding or None,
            trace_id=state.trace_id or None,
        )
        try:
            returned = tool(request)
        except Exception as exc:  # noqa: BLE001 - one source down is a gap, not the end
            return _failed(stage_name, f"{exc}")

        evidence = list(returned or ())
        wrong = [item for item in evidence if not isinstance(item, Evidence)]
        if wrong:
            # The one thing a tool may return. Letting other shapes through would put
            # raw tool output in front of synthesis, which is the invariant this
            # boundary exists to hold.
            return _failed(
                stage_name,
                f"Source {source!r} returned {type(wrong[0]).__name__} rather than "
                "Evidence",
            )

        return {
            "evidence": evidence,
            "stages": (
                StageRecord(
                    name=stage_name,
                    detail={"goal": goal, "evidence": len(evidence)},
                ),
            ),
        }

    return run


def _failed(stage_name: str, error: str) -> dict[str, Any]:
    return {"stages": (StageRecord(name=stage_name, error=error),)}


def _update(before: GraphState, after: GraphState) -> dict[str, Any]:
    """The difference a node made, expressed as channel updates.

    Accumulating fields report only what they *added*: their reducers append, so
    returning the totals would duplicate everything the state already held. The rest
    report their new value, and only when it changed -- an unchanged field written back
    is a write two concurrent branches could disagree about for no reason.
    """
    update: dict[str, Any] = {}

    added_stages = after.stages[len(before.stages) :]
    if added_stages:
        update["stages"] = added_stages

    added_evidence = after.evidence.items[len(before.evidence.items) :]
    if added_evidence:
        update["evidence"] = list(added_evidence)

    for field in GraphState.model_fields:
        if field in {"stages", "evidence"}:
            continue
        value = getattr(after, field)
        if value != getattr(before, field):
            update[field] = value
    return update
