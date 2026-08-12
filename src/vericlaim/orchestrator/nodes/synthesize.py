"""Write the answer, from the evidence and from nothing else.

Everything before this node exists to put a specific body of evidence in front of it, and
everything after exists to check that the answer stayed inside that body. Three
commitments make this node different from a fluent one.

**It sees only the rendered evidence.** Not the tools' returns, not the SQL, not the
retrieval scores -- the same tagged blocks a citation resolves against, so every material
statement has an id it can be traced to. Raw tool output reaching a synthesizer is the
failure this boundary exists to prevent, and it is enforced here by construction rather
than by instruction.

**It does not write the refusals.** A question the router turned away, one awaiting
clarification, one the planner declined, and one that gathered no evidence are all
answered from what was already recorded, without a model call. Two reasons: the reason
was written by the node that actually made the decision, and a model asked to phrase a
refusal writes something adjacent to an answer -- hedged, plausible, and sourced from
what it already believes. A refusal here also costs nothing, which matters on a free
tier.

**It is told what is missing.** Sources that could not be reached and gaps sufficiency
named are part of the payload, because an answer built while one source was down is
incomplete in a way only the answer can tell the reader about.

The prompt carries the obligations that follow from all of that: distinguish what the
evidence states from what it suggests, qualify anything read with low confidence rather
than asserting it, and never say a claim is approved. This system supports a decision; it
does not make one.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vericlaim.gateway.types import (
    BudgetExceededError,
    GatewayError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

SYNTHESIZE_TASK = "synthesize"
STAGE = "synthesize"

TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

_CITATION = re.compile(r"\[E(\d+)\]")

SYNTHESIZE_SYSTEM_PROMPT = """\
You write one answer to one question, using only the evidence supplied with it.

The evidence is given as blocks, each headed by an id in square brackets such as [E1] and
by where it came from. Those blocks are everything you know. You have no other knowledge
of this business, these records or these documents, and anything you believe you know
about them from elsewhere is not evidence and must not appear in the answer.

CITE EVERY MATERIAL STATEMENT.
After each statement of fact, put the id of the evidence it rests on, as [E1], or [E1][E3]
where it rests on several. A number, a date, a name, a quoted term, a comparison: each
needs its id. Never write an id that is not in the supplied blocks, and never cite a block
for something it does not say. If the evidence will not support a statement, do not write
the statement.

SEPARATE WHAT THE EVIDENCE STATES FROM WHAT IT SUGGESTS.
State a fact plainly and cite it. Where you draw a conclusion the evidence supports but
does not state, mark it as an inference in the sentence itself -- "this suggests", "this
is consistent with" -- and cite what it rests on. A reader has to be able to tell, without
checking anything, which sentences are records and which are your reading of them.

QUALIFY WHAT WAS READ WITH LOW CONFIDENCE.
A block marked as low confidence was read off a page the recognizer was unsure of. Say so
where you use it, name the uncertainty in the sentence, and never let such a block carry a
statement on its own where a confident block contradicts it.

SAY WHAT IS MISSING.
Where `unreachable_sources` is not empty, say plainly that those parts could not be
consulted and that the answer is incomplete for that reason. Where `known_gaps` is not
empty, say what could not be established. An answer that quietly omits what it could not
find reads exactly like one that found everything.

THIS IS DECISION SUPPORT, NOT A DECISION.
Never state or imply that a claim is approved, accepted, declined, settled or payable, and
never recommend that outcome. Evidence may be described as consistent with, or not
consistent with, the terms quoted -- the determination itself rests with the team handling
the case, and the answer should say so where the question invites one.

If `correction` is not empty, your previous answer failed verification and it describes
exactly what was wrong. Fix precisely those problems and change nothing else: rewriting
the parts that were accepted risks breaking them.

Answer in plain prose, as briefly as the question allows, in the shape
`expected_answer_shape` describes. No preamble about what you were asked or how you
searched.
"""


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def synthesize(
    state: GraphState, *, gateway: Any | None = None, correction: str = ""
) -> GraphState:
    """Write the answer, or state plainly why there is not one."""
    if state.routing is None:
        raise ValueError("Synthesis needs a routing decision to answer against")

    refusal = _refusal(state)
    if refusal:
        kind, answer = refusal
        return state.with_(answer=answer).with_stage(
            StageRecord(name=STAGE, detail={"refused": kind, "cited": []})
        )

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()

    from vericlaim.config import get_settings

    payload = {
        "question": state.question,
        "expected_answer_shape": state.plans.get("expected_answer_shape", ""),
        # The only view of the evidence anything downstream of the tools ever gets.
        "evidence": state.evidence.render_for_synthesis(
            low_confidence_floor=get_settings().ocr_confidence_floor
        ),
        "unreachable_sources": list(state.collection.get("failed_sources") or ()),
        "known_gaps": list(state.sufficiency.get("gaps") or ()),
        # Set only on a second attempt, by verification, naming what was wrong with the
        # first one. Empty on the first pass.
        "correction": correction,
    }
    messages = [
        {"role": "system", "content": SYNTHESIZE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    try:
        completion = gateway.complete(SYNTHESIZE_TASK, messages)
        answer = str(getattr(completion, "text", "") or "").strip()
        if not answer:
            raise ValueError("Synthesis returned an empty answer")
    except TERMINAL_ERRORS:
        raise
    except (GatewayError, ValueError) as exc:
        # No answer is a better outcome than one nobody wrote.
        return state.with_stage(StageRecord(name=STAGE, error=str(exc)))

    return state.with_(answer=answer).with_stage(
        StageRecord(
            name=STAGE,
            # Recorded, not corrected: verification decides what to do about an answer
            # that cites nothing, but it must never be invisible.
            detail={"cited": _cited(answer)},
            model=getattr(completion, "model", ""),
            cost_usd=getattr(completion, "cost_usd", 0.0),
            latency_ms=getattr(completion, "latency_ms", 0.0),
        )
    )


def _refusal(state: GraphState) -> tuple[str, str] | None:
    """The answer for a question that never reached the evidence, and why.

    Composed from what the deciding node recorded, so the words the reader sees are the
    words that were actually written down at the moment of the decision.
    """
    routing = state.routing
    assert routing is not None

    if routing.out_of_scope:
        return "out_of_scope", routing.reason
    if routing.needs_clarification:
        return "needs_clarification", routing.clarification_question or routing.reason
    if state.plans and not state.plans.get("answerable"):
        return "unanswerable", str(state.plans.get("unanswerable_reason") or "")
    if not state.evidence.items:
        return "no_evidence", _nothing_found(state)
    return None


def _nothing_found(state: GraphState) -> str:
    """Say that nothing was found, and be exact about what that means.

    A source that was consulted and held nothing, and one that could not be consulted at
    all, support different conclusions: the first is negative information, the second is
    an absence of information, and an answer that blurs them misleads.
    """
    silent = list(state.collection.get("silent_sources") or ())
    failed = list(state.collection.get("failed_sources") or ())
    asked = list(state.plans.get("sub_goals") or ()) or list(state.routing.sources)  # type: ignore[union-attr]

    parts = ["No evidence was found for this question."]
    consulted = [source for source in asked if source not in failed]
    if consulted:
        parts.append(
            "The following were consulted and returned nothing: "
            f"{', '.join(consulted)}."
        )
    if failed:
        parts.append(
            f"The following could not be reached, so this answer is incomplete rather "
            f"than negative: {', '.join(failed)}."
        )
    if not consulted and not failed and silent:
        parts.append(f"Consulted: {', '.join(silent)}.")
    parts.append(
        "Nothing here should be read as a statement about what the records contain."
    )
    return " ".join(parts)


def _cited(answer: str) -> list[str]:
    """The ids the answer refers to, in the order it first refers to them."""
    seen: list[str] = []
    for match in _CITATION.finditer(answer):
        marker = f"E{match.group(1)}"
        if marker not in seen:
            seen.append(marker)
    return seen
