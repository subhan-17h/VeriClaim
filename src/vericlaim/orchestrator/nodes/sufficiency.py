"""Decide whether what came back is enough, and whether another pass is worth its price.

Two judgements, deliberately ordered.

**Counted gaps first, with no model involved.** A source that was asked and returned
nothing, or that could not be reached at all, is a gap arithmetic can find in the
collection account. Asking a model about it would be paying to be told what is already
known -- and worse than that: a model shown evidence with a hole in it tends to assess
the part it can see, and the missing source never comes up. So a counted gap short-
circuits the assessment entirely.

**Then the judgement arithmetic cannot make.** When every planned sub-goal produced
something, whether that something actually answers the question is a reading question,
and it goes to a model. The model sees the same rendered evidence synthesis will see and
nothing else: a sufficiency check reading raw tool output would be judging something the
answer will never be built from.

**The loop back is bounded at two.** Each pass is a full fan-out, and a third is the
graph arguing with itself at that price. At the bound the verdict stands as insufficient
with its gaps named, because the honest outcome is an answer that says what is missing
rather than another attempt to find it.

A model that cannot be reached leaves the question *unassessed* rather than assessed
either way. Calling it sufficient would hide a gap; calling it insufficient would spend
another fan-out on a judgement nobody made.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from vericlaim.gateway.types import (
    BudgetExceededError,
    GatewayError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.sources import load_capabilities
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

SUFFICIENCY_TASK = "sufficiency"
STAGE = "sufficiency"

TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

SUFFICIENCY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["sufficient", "gaps", "reason"],
    "additionalProperties": False,
}

SUFFICIENCY_SYSTEM_PROMPT = """\
You judge whether the gathered evidence is enough to answer one question. You must
not answer it, and you do not judge whether the evidence is true.

You are given the question, what each part of the retrieval was asked to find, the shape
a correct answer takes, and every piece of evidence that came back, each tagged with an
id. That evidence is all there is. Do not assume anything else was found, and do not
suppose that something obvious must be available somewhere.

WHAT SUFFICIENT MEANS.
The evidence is sufficient when every part of the question can be answered from what is
in front of you, with each material statement resting on a specific tagged piece. It is
not sufficient merely because the evidence is on the right subject, plentiful, or
interesting. It is also not insufficient merely because it is thin: one exact figure or
one exact sentence can be complete.

Judge only against what the question asks. A question with two parts needs both. A
question asking about a period needs evidence about that period, not a neighbouring one.
A question asking for a comparison needs both sides of it.

NAME THE GAP OR DO NOT CLAIM ONE.
If it is not sufficient, list in `gaps` each missing thing as a short, concrete
description of what would have to be found -- the quantity, the period, the term, the
side of the comparison. A gap that says only "more evidence" or "further detail" cannot
be searched for, and the next attempt would repeat the last one exactly. Never list a
gap you would not know how to go and look for.

Write `reason` as one sentence. When the evidence is sufficient, `gaps` must be empty.
"""


class SufficiencyError(ValueError):
    """Raised for a verdict that cannot be acted on."""


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def sufficiency(state: GraphState, *, gateway: Any | None = None) -> GraphState:
    """Record whether the evidence answers the question, and whether to go round again."""
    sub_goals = state.plans.get("sub_goals") or {}
    if not sub_goals:
        raise ValueError("Assessing sufficiency needs a plan to assess against")

    counted = _counted_gaps(state)
    if counted:
        # No model call. The gap is arithmetic, and showing a model evidence with a hole
        # in it invites a verdict about the part it can see.
        return _recorded(state, sufficient=False, gaps=counted, reason=_reason(counted),
                         assessed_by="deterministic")

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()

    from vericlaim.config import get_settings

    payload = {
        "question": state.question,
        "expected_answer_shape": state.plans.get("expected_answer_shape", ""),
        "sub_goals": {
            source: goal.get("goal", "") for source, goal in sub_goals.items()
        },
        # The same boundary synthesis has: rendered evidence, never raw tool output.
        "evidence": state.evidence.render_for_synthesis(
            low_confidence_floor=get_settings().ocr_confidence_floor
        ),
    }
    messages = [
        {"role": "system", "content": SUFFICIENCY_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    try:
        completion = gateway.complete_json(
            SUFFICIENCY_TASK, messages, SUFFICIENCY_SCHEMA
        )
        verdict = _verdict(_parse(completion))
    except TERMINAL_ERRORS:
        raise
    except (GatewayError, SufficiencyError, ValueError) as exc:
        # Unassessed, on purpose. Neither verdict is safe to invent here.
        return state.with_stage(StageRecord(name=STAGE, error=str(exc)))

    return _recorded(
        state,
        sufficient=verdict["sufficient"],
        gaps=verdict["gaps"],
        reason=verdict["reason"],
        assessed_by="model",
        completion=completion,
    )


def _counted_gaps(state: GraphState) -> list[str]:
    """The gaps that can be found without reading anything.

    A source that was planned for and stayed silent, and one that could not be reached,
    are different facts about the answer and are described differently -- but both mean
    a part of the plan produced nothing.
    """
    collection = state.collection
    sub_goals = state.plans.get("sub_goals") or {}
    silent_sources = collection.get("silent_sources") or ()
    failed_sources = collection.get("failed_sources") or ()
    capabilities = load_capabilities() if silent_sources or failed_sources else {}
    gaps = []
    for source in silent_sources:
        details = []
        goal = str((sub_goals.get(source) or {}).get("goal") or "").strip()
        if goal:
            details.append(f"assigned sub-goal: {goal}")
        capability = capabilities.get(source)
        if capability:
            details.append(
                "declared cannot answer: " + ", ".join(capability.cannot_answer)
            )
        detail = f" ({'; '.join(details)})" if details else ""
        gaps.append(
            f"{source} was asked for its part of the question and returned nothing"
            f"{detail}"
        )

    for source in failed_sources:
        details = []
        goal = str((sub_goals.get(source) or {}).get("goal") or "").strip()
        if goal:
            details.append(f"assigned sub-goal: {goal}")
        capability = capabilities.get(source)
        if capability:
            details.append(
                "declared cannot answer: " + ", ".join(capability.cannot_answer)
            )
        prefix = f"source.{source}: "
        failure_reason = next(
            (
                failure[len(prefix) :].strip()
                for failure in reversed(state.failures)
                if failure.startswith(prefix)
            ),
            "",
        )
        if failure_reason:
            details.append(f"failure reason: {failure_reason}")
        detail = f" ({'; '.join(details)})" if details else ""
        gaps.append(
            f"{source} could not be reached, so its part of the question is unevidenced"
            f"{detail}"
        )
    return gaps


def _reason(gaps: Sequence[str]) -> str:
    return (
        f"{len(gaps)} part(s) of the plan produced no evidence."
        if gaps
        else "Every part of the plan produced evidence."
    )


def _recorded(
    state: GraphState,
    *,
    sufficient: bool,
    gaps: Sequence[str],
    reason: str,
    assessed_by: str,
    completion: Any | None = None,
) -> GraphState:
    """Write the verdict, and take the replan if the bound still allows one."""
    replan = not sufficient and state.can_replan
    verdict: dict[str, Any] = {
        "sufficient": sufficient,
        "gaps": list(gaps),
        "reason": reason
        if replan or sufficient
        else f"{reason} No further attempt: the evidence already gathered is what the "
        "answer must be built from.",
        "assessed_by": assessed_by,
        "replan": replan,
        # What the next plan is told to go and find. Empty when there is no next plan.
        "retry_hint": "; ".join(gaps) if replan else "",
    }

    after = state.with_(sufficiency=verdict)
    if replan:
        after = after.replanned()
    return after.with_stage(
        StageRecord(
            name=STAGE,
            detail={
                "sufficient": sufficient,
                "gaps": len(gaps),
                "replan": replan,
                "assessed_by": assessed_by,
            },
            model=getattr(completion, "model", "") if completion else "",
            cost_usd=getattr(completion, "cost_usd", 0.0) if completion else 0.0,
            latency_ms=getattr(completion, "latency_ms", 0.0) if completion else 0.0,
        )
    )


def _parse(completion: Any) -> Mapping[str, Any]:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SufficiencyError(f"Sufficiency returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise SufficiencyError("Sufficiency returned a non-object verdict")
    return parsed


def _verdict(raw: Mapping[str, Any]) -> dict[str, Any]:
    sufficient = bool(raw.get("sufficient"))
    gaps = [str(gap).strip() for gap in raw.get("gaps") or () if str(gap).strip()]
    reason = str(raw.get("reason") or "").strip()

    if not sufficient and not gaps:
        raise SufficiencyError(
            "Sufficiency called the evidence insufficient without naming a gap, so the "
            "next attempt would repeat the last one exactly"
        )
    if sufficient and gaps:
        # Sufficient with gaps listed is a contradiction; the gaps win, because the
        # cheaper mistake is one more pass rather than an answer with a known hole.
        sufficient = False

    return {"sufficient": sufficient, "gaps": gaps, "reason": reason}
