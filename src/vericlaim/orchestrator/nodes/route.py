"""Decide which sources a question needs, and turn away the ones nothing covers.

This is the node that makes "calls only the tools it needs" true. Everything downstream
is shaped by it: an omitted source is evidence the answer will never have, and a
gratuitous one is a tool call, a body of evidence to reconcile, and latency the asker
waits through for nothing.

It routes to **sources**, not to tables. The reference implementation's selector chose
among tables of one database, and its notion of scope was "no table covers this". Here a
question can be covered by written rules, by recorded transactions, by prepared reports,
by received paperwork, or by several at once, and each of those is reached a different
way. The unit of the decision is the source.

**Nothing is decided by recognising the question.** The capability descriptions arrive as
data from a reviewed file, the prompt names no source, and the node never inspects a word
of the question itself. A test routes identical text against two different capability
sets and asserts the decisions differ, which is what makes "no question-string routing" a
property rather than a promise.

**The reply is checked, not trusted.** Three kinds of trouble, handled differently:

* *Unusable* -- a source nobody described, the same source twice, a refusal with no
  reason, a request for clarification with no question, or a routing set that is empty
  while claiming to be neither. There is nothing to call and nothing honest to say, so
  the stage records the failure and leaves the decision unmade.
* *Contradictory* -- refusing the question and naming sources for it in one reply. The
  refusal wins, deterministically, and the discarded sources are named on the stage. The
  alternative is spending four tool calls gathering evidence for an answer the router has
  already said it will not give.
* *Out of range* -- a confidence outside 0..1, brought back into it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
from vericlaim.orchestrator.state import GraphState, RoutingDecision, StageRecord
from vericlaim.tracing import traced

ROUTE_TASK = "route"
STAGE = "route"

TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "out_of_scope": {"type": "boolean"},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string"},
    },
    "required": [
        "sources",
        "confidence",
        "reason",
        "out_of_scope",
        "needs_clarification",
        "clarification_question",
    ],
    "additionalProperties": False,
}

ROUTE_SYSTEM_PROMPT = """\
You decide which of the supplied sources one question needs. You do not answer it and you
do not plan how it would be answered.

Everything you may choose from is supplied to you. Each entry in `sources` describes what
that source holds, what it can answer, what it `cannot_answer`, and what a citation from
it looks like. Nothing outside that list exists. Return names exactly as supplied.

THE MINIMUM SUFFICIENT SET.
Choose every source the question genuinely needs and no others. A question with two parts
that different sources cover needs both; a question one source answers completely needs
that one alone. Never add a source because it might contain something related, because it
would corroborate another, or because the question is broad. Each extra source is work the
asker waits through and material that has to be reconciled against everything else.

Two ways to get this wrong, and both matter. Omitting a source the question needs produces
an answer that is confidently partial, because nothing downstream can miss what was never
gathered. Adding one that cannot help produces material that has to be argued away.

THE STATED LIMITS ARE BINDING.
`cannot_answer` is a statement about that source, not a hint. Never route a question to a
source whose `cannot_answer` covers what is being asked, however close its subject sounds.
Where one source states a rule and another records what happened, a question about both
needs both; a question about only one of them needs only that one.

WHAT THE QUESTION IS ABOUT.
Route on the subject the question asks about, not on the words it uses. A source whose
description covers the subject is a candidate even where the question mentions a detail,
grouping or period the description does not spell out -- descriptions are summaries, and
what a source holds at that level of detail is decided later, not by you.

OUT OF SCOPE.
Set out_of_scope to true only when no supplied source covers the question's subject at
all -- when it asks about material this system simply does not hold. Then return no
sources and write `reason` as one sentence, addressed to the asker, naming what is
missing and saying briefly what is available instead. Never set out_of_scope because a
detail, breakdown or period is unavailable within a source that does cover the subject.

CLARIFICATION.
Set needs_clarification to true only when the question could be asking for materially
different things and choosing wrongly would produce a confident answer to a question
nobody asked. Then write clarification_question as the one thing you need to know.
Spelling, existence and identity of named things are settled later against stored values,
so never ask about those. When you do not need to ask, leave clarification_question empty.

Set `confidence` between 0 and 1 for how sure you are of the set you chose, and write
`reason` as one sentence explaining the choice in terms of what each chosen source holds.
"""


class RouteError(ValueError):
    """Raised for a routing decision that cannot be acted on.

    Never repaired in place: a decision corrected here is no longer the decision anyone
    can be shown, and a routing set with one name quietly removed looks deliberate.
    """


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def route(
    state: GraphState,
    *,
    capabilities: Mapping[str, SourceCapability] | None = None,
    gateway: Any | None = None,
) -> GraphState:
    """Choose the sources this question needs, and record the choice on the state."""
    if capabilities is None:
        capabilities = load_capabilities()
    if not capabilities:
        raise ValueError("Routing needs at least one described source capability")

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()

    payload = {
        "question": state.question,
        "understanding": dict(state.understanding),
        "sources": [capability_detail(capability) for capability in capabilities.values()],
    }
    messages = [
        {"role": "system", "content": ROUTE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    try:
        completion = gateway.complete_json(ROUTE_TASK, messages, ROUTE_SCHEMA)
        decision, dropped = _decision(_parse(completion), capabilities)
    except TERMINAL_ERRORS:
        raise
    except (GatewayError, RouteError, ValueError) as exc:
        return state.with_stage(StageRecord(name=STAGE, error=str(exc)))

    detail: dict[str, Any] = {
        "sources": list(decision.sources),
        "out_of_scope": decision.out_of_scope,
        "needs_clarification": decision.needs_clarification,
    }
    if dropped:
        detail["dropped_sources"] = list(dropped)

    return state.with_(routing=decision).with_stage(
        StageRecord(
            name=STAGE,
            detail=detail,
            model=getattr(completion, "model", ""),
            cost_usd=getattr(completion, "cost_usd", 0.0),
            latency_ms=getattr(completion, "latency_ms", 0.0),
        )
    )


def _parse(completion: Any) -> Mapping[str, Any]:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RouteError(f"Router returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise RouteError("Router returned a non-object decision")
    return parsed


def _decision(
    raw: Mapping[str, Any], capabilities: Mapping[str, SourceCapability]
) -> tuple[RoutingDecision, tuple[str, ...]]:
    """Check a reply and turn it into a decision, with whatever it discarded."""
    named = [str(name).strip() for name in raw.get("sources") or ()]
    unknown = sorted({name for name in named if name not in capabilities})
    if unknown:
        raise RouteError(
            f"Router named source(s) nobody described: {', '.join(unknown)}. "
            f"Described sources are: {', '.join(capabilities)}."
        )
    if len(named) != len(set(named)):
        raise RouteError("Router named the same source more than once")

    # Canonical order, taken from the capability file rather than the reply, so two runs
    # that chose the same sources produce the same decision.
    sources = tuple(name for name in capabilities if name in set(named))

    out_of_scope = bool(raw.get("out_of_scope"))
    needs_clarification = bool(raw.get("needs_clarification"))
    reason = str(raw.get("reason") or "").strip()
    question = str(raw.get("clarification_question") or "").strip()

    if out_of_scope and not reason:
        raise RouteError(
            "Router declined the question without naming what is missing, so there is "
            "nothing to tell the asker"
        )
    if needs_clarification and not question:
        raise RouteError(
            "Router asked for clarification without a question to put to the asker"
        )
    if not sources and not out_of_scope and not needs_clarification:
        raise RouteError(
            "Router chose no source and gave no reason to stop, so there is nothing to "
            "call and nothing to say"
        )

    dropped: tuple[str, ...] = ()
    if sources and (out_of_scope or needs_clarification):
        dropped, sources = sources, ()

    return (
        RoutingDecision(
            sources=sources,
            confidence=_confidence(raw.get("confidence")),
            reason=reason,
            out_of_scope=out_of_scope,
            needs_clarification=needs_clarification,
            clarification_question=question,
        ),
        dropped,
    )


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise RouteError(f"Router returned a non-numeric confidence: {value!r}") from exc
    return min(1.0, max(0.0, confidence))
