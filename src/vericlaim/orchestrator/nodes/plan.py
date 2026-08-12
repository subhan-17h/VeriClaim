"""Say what each routed source is being asked for, or decline the question.

Routing decides *which* sources; this node decides *what to ask each of them*, and it is
the last point at which a question can be turned down before any tool spends anything.

The sub-goal is the text each tool receives. That makes this the node where a question
with two halves is split -- one half put to the source that holds written rules, the
other to the one that holds recorded events -- so no tool is handed the whole question
and left to answer the part it cannot.

Three deterministic checks on the returned plan, each closing a gap that is invisible
later:

* **Every routed source is asked something.** A source the router chose and the plan
  skipped produces an answer missing evidence nobody can notice is missing: the gap is in
  what was never gathered, and no downstream check can see an absence it was never told
  to expect.
* **Nothing outside the routed set is asked anything.** The routing decision is what
  authorises a tool call; a sub-goal beyond it is a call nobody decided to make.
* **One sub-goal per source.** Each tool takes one question and plans its own work from
  there. Two sub-goals for one source would either be silently concatenated or silently
  halved.

Declining is a first-class outcome rather than a failure. A question the routed sources
between them cannot supply is better refused here, for the price of one call, than
answered from whatever the tools happen to return.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vericlaim.gateway.types import (
    BudgetExceededError,
    GatewayError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.sources import (
    SourceCapability,
    capability_detail,
    load_capabilities,
)
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

PLAN_TASK = "plan"
STAGE = "plan"

TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "unanswerable_reason": {"type": "string"},
        "sub_goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "goal": {"type": "string"},
                    "expected_evidence": {"type": "string"},
                },
                "required": ["source", "goal", "expected_evidence"],
                "additionalProperties": False,
            },
        },
        "expected_answer_shape": {"type": "string"},
    },
    "required": [
        "answerable",
        "unanswerable_reason",
        "sub_goals",
        "expected_answer_shape",
    ],
    "additionalProperties": False,
}

PLAN_SYSTEM_PROMPT = """\
You say what each chosen source is being asked for. You do not answer the question, and
you do not choose the sources -- that is already decided and supplied to you.

Each entry in `sources` describes one chosen source: what it holds, what it can answer,
what it `cannot_answer`, and what a citation from it looks like. Write exactly one
sub-goal for each of them, naming the source exactly as supplied. Never write a sub-goal
for anything not in that list.

THE SUB-GOAL IS WHAT THAT SOURCE IS ASKED.
Write it as a self-contained request in plain words, carrying every restriction from the
question that applies to that source -- the period, the named things, the conditions.
Whoever receives it sees only it, never the original question, so a sub-goal that says
"the same period" or "for those" asks for something undefined.

SPLIT THE QUESTION, DO NOT REPEAT IT.
Where a question has parts that different sources cover, give each source only its part.
Handing every source the whole question makes each of them answer the fraction they can
and stay silent about the rest, and the silences are indistinguishable from absence of
evidence. Ask each source only for what its description says it can supply, and never ask
one for something its `cannot_answer` covers.

Set `expected_evidence` to what a useful reply from that source would look like, in the
terms its citation is expressed in. This is what makes it possible to tell later that a
source answered the wrong thing rather than nothing.

THE ANSWERABILITY GATE.
Before writing any sub-goal, check that the chosen sources between them can supply
everything the question asks for. If some part of it is covered by nothing they hold, set
answerable to false, write no sub-goals, and name in unanswerable_reason exactly what is
missing and what the chosen sources do cover, in one sentence addressed to the asker.
Never substitute a nearby fact for a missing one, and never plan to infer what none of
the sources record. An honest refusal is a correct outcome; an answer assembled from
whatever happens to come back is not.

Set expected_answer_shape to one short phrase describing what a correct answer looks
like. If `retry_hint` is not empty it describes what an earlier attempt failed to find:
address exactly that, without widening any sub-goal beyond the question.
"""


class PlanningError(ValueError):
    """Raised for a plan that cannot be executed as returned."""


@dataclass(frozen=True, slots=True)
class SubGoal:
    """What one source is asked for, and what a useful reply from it looks like."""

    source: str
    goal: str
    expected_evidence: str = ""


@dataclass(frozen=True, slots=True)
class QuestionPlan:
    """The work for one question, or the reason there is none."""

    answerable: bool
    sub_goals: tuple[SubGoal, ...] = ()
    expected_answer_shape: str = ""
    unanswerable_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "answerable": self.answerable,
            "unanswerable_reason": self.unanswerable_reason,
            "expected_answer_shape": self.expected_answer_shape,
            "sub_goals": {
                goal.source: {
                    "goal": goal.goal,
                    "expected_evidence": goal.expected_evidence,
                }
                for goal in self.sub_goals
            },
        }


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def plan(
    state: GraphState,
    *,
    capabilities: Mapping[str, SourceCapability] | None = None,
    retry_hint: str = "",
    gateway: Any | None = None,
) -> GraphState:
    """Plan what to ask each routed source, and record the plan on the state."""
    if state.routing is None:
        raise ValueError("Planning needs a routing decision to plan against")

    skipped = _nothing_to_plan(state)
    if skipped:
        # Not a failure. The router already decided this question stops here, and calling
        # the model would spend a request to be told what it has just been told.
        return state.with_stage(StageRecord(name=STAGE, detail={"skipped": skipped}))

    if capabilities is None:
        capabilities = load_capabilities()
    routed = {
        name: capabilities[name]
        for name in state.routing.sources
        if name in capabilities
    }
    missing = sorted(set(state.routing.sources) - set(routed))
    if missing:
        raise ValueError(
            f"Routed to source(s) nobody described: {', '.join(missing)}"
        )

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()

    payload = {
        "question": state.question,
        "understanding": dict(state.understanding),
        "routing_reason": state.routing.reason,
        # Only the routed sources. Describing the others invites a sub-goal for one the
        # router deliberately excluded.
        "sources": [capability_detail(capability) for capability in routed.values()],
        "retry_hint": retry_hint,
    }
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    try:
        completion = gateway.complete_json(PLAN_TASK, messages, PLAN_SCHEMA)
        question_plan, dropped = _plan(_parse(completion), tuple(routed))
    except TERMINAL_ERRORS:
        raise
    except (GatewayError, PlanningError, ValueError) as exc:
        return state.with_stage(StageRecord(name=STAGE, error=str(exc)))

    detail: dict[str, Any] = {
        "answerable": question_plan.answerable,
        "sub_goals": [goal.source for goal in question_plan.sub_goals],
    }
    if dropped:
        detail["dropped_sub_goals"] = list(dropped)

    return state.with_(plans=question_plan.as_dict()).with_stage(
        StageRecord(
            name=STAGE,
            detail=detail,
            model=getattr(completion, "model", ""),
            cost_usd=getattr(completion, "cost_usd", 0.0),
            latency_ms=getattr(completion, "latency_ms", 0.0),
        )
    )


def _nothing_to_plan(state: GraphState) -> str:
    routing = state.routing
    assert routing is not None
    if routing.out_of_scope:
        return "the question is out of scope"
    if routing.needs_clarification:
        return "the question is awaiting clarification"
    if not routing.sources:
        return "no source was routed"
    return ""


def _parse(completion: Any) -> Mapping[str, Any]:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PlanningError(f"Planner returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise PlanningError("Planner returned a non-object plan")
    return parsed


def _plan(
    raw: Mapping[str, Any], routed: tuple[str, ...]
) -> tuple[QuestionPlan, tuple[str, ...]]:
    """Check a returned plan against the routing, with whatever it discarded."""
    entries = [_sub_goal(entry, routed) for entry in raw.get("sub_goals") or ()]
    answerable = bool(raw.get("answerable"))
    reason = str(raw.get("unanswerable_reason") or "").strip()
    shape = str(raw.get("expected_answer_shape") or "").strip()

    if not answerable:
        if not reason:
            raise PlanningError(
                "Planner declined the question without naming what is missing"
            )
        # Declining and planning work at once is a contradiction; the refusal wins, and
        # what it discarded is recorded rather than dropped.
        return (
            QuestionPlan(
                answerable=False, unanswerable_reason=reason, expected_answer_shape=shape
            ),
            tuple(goal.source for goal in entries),
        )

    if not entries:
        raise PlanningError(
            "Planner produced no sub-goals for a question it called answerable"
        )

    named = [goal.source for goal in entries]
    if len(named) != len(set(named)):
        raise PlanningError(
            "Planner gave one source more than one sub-goal; each source is asked once"
        )
    unasked = [name for name in routed if name not in set(named)]
    if unasked:
        raise PlanningError(
            f"Planner left routed source(s) with nothing to do: {', '.join(unasked)}. "
            "Evidence never gathered is a gap nothing downstream can see."
        )

    # Routing order, not reply order, so the same plan always reads the same way.
    order = {name: index for index, name in enumerate(routed)}
    ordered = tuple(sorted(entries, key=lambda goal: order[goal.source]))
    return (
        QuestionPlan(
            answerable=True, sub_goals=ordered, expected_answer_shape=shape
        ),
        (),
    )


def _sub_goal(entry: Any, routed: tuple[str, ...]) -> SubGoal:
    if not isinstance(entry, Mapping):
        raise PlanningError("Planner returned a sub-goal that is not an object")

    source = str(entry.get("source") or "").strip()
    if source not in routed:
        raise PlanningError(
            f"Planner gave work to {source or '(unnamed)'}, which was not routed. "
            f"Routed sources are: {', '.join(routed)}."
        )

    goal = str(entry.get("goal") or "").strip()
    if not goal:
        raise PlanningError(f"Planner gave {source} a sub-goal that asks for nothing")

    return SubGoal(
        source=source,
        goal=goal,
        expected_evidence=str(entry.get("expected_evidence") or "").strip(),
    )
