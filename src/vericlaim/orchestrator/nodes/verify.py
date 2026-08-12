"""Check the answer before anyone reads it, and say so plainly when it cannot be trusted.

Two checks, in an order that is itself a decision.

**Citations first, deterministically.** Every ``[En]`` in the answer must point at
evidence that exists, and an answer written over evidence must cite something. This is
arithmetic, it cannot be argued with, and it runs before any model is spent -- an answer
already known to be unpublishable is not worth a guard call. A citation pointing at
nothing is indistinguishable, to a reader, from one pointing at something, which is why
an unresolvable marker is a hard failure here rather than a warning: one bad id makes
every other citation in the answer worth less.

**Then the judgement arithmetic cannot make.** Does each statement actually rest on what
it cites, and does the answer stay on the right side of decision support? A well-cited
answer can still assert what its evidence merely suggests, or decide a case this system
has no business deciding.

**One regeneration, then the truth.** A failed check is sent back once, with the problem
named, and the new answer is re-checked from the start -- a rewrite that satisfies the
guard while breaking its citations is not an improvement. A second regeneration would be
the system arguing with itself about an answer it has twice failed to justify, so the
outcome instead is a degradation that says what could not be established. That is the
honest end of this path, and it is not the same thing as an answer.

A refusal is not held to a citation it could not have: a question that gathered no
evidence is answered by saying so, and demanding an id from it would send a truthful
refusal back to be rewritten.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vericlaim.citations import CitationReport, resolve_citations
from vericlaim.gateway.types import (
    BudgetExceededError,
    GatewayError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

VERIFY_TASK = "verify"
STAGE = "verify"

# One. See the module docstring: the second would be arguing with itself.
MAX_REGENERATIONS = 1

TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

Regenerator = Callable[[GraphState, str], GraphState]

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean"},
        "overclaims": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["faithful", "overclaims", "problems", "reason"],
    "additionalProperties": False,
}

VERIFY_SYSTEM_PROMPT = """\
You check one answer against the evidence it was written from. You are not judging whether
the answer is true, whether the evidence is right, or whether the question was worth
asking. You judge only whether the answer stays inside its evidence.

You are given the answer and the evidence blocks it cites, each headed by an id.

FAITHFULNESS.
Set faithful to false when any material statement in the answer is not supported by the
evidence cited for it. That includes: a number, date, name or quoted term that does not
appear in the cited block; a statement citing a block that says something different; a
comparison where only one side is evidenced; a generalisation from one instance stated as
though it held generally; and anything asserted with no citation at all where a citation
is needed. A statement the answer marks as an inference is faithful when the evidence
cited genuinely supports that reading -- it is unfaithful when the evidence does not, or
when a reading is presented as a record.

Do not fault an answer for saying less than the evidence allows, for omitting evidence, or
for stating plainly that something could not be established.

OVERCLAIMING.
Set overclaims to true when the answer states or implies a determination rather than
supporting one: that a case is approved, accepted, declined, settled, payable or covered
as a matter of decision rather than of quoted wording, or when it recommends such an
outcome. Describing evidence as consistent with, or not consistent with, quoted terms is
correct and is not overclaiming.

NAME THE PROBLEM OR DO NOT REPORT ONE.
Every problem you report must quote or name the exact statement at fault and say what is
wrong with it, so it can be corrected without guessing. A problem that says only that the
answer is unsupported cannot be acted on. If faithful is true and overclaims is false,
problems must be empty. Write `reason` as one sentence.
"""


class VerificationError(ValueError):
    """Raised for a guard verdict that cannot be acted on."""


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def verify(
    state: GraphState,
    *,
    gateway: Any | None = None,
    regenerate: Regenerator | None = None,
) -> GraphState:
    """Check the answer's citations and its faithfulness, regenerating at most once."""
    if not state.answer.strip():
        # Synthesis failed or refused to write. There is nothing to check, and nothing
        # this node could add that the synthesis stage has not already recorded.
        return state.with_stage(StageRecord(name=STAGE, detail={"skipped": "no answer"}))

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()
    if regenerate is None:
        regenerate = _default_regenerator(gateway)

    regenerated = False
    completion: Any | None = None

    for attempt in range(MAX_REGENERATIONS + 1):
        report = resolve_citations(state.answer, state.evidence)
        problems = _citation_problems(report, state)

        if not problems and state.evidence.items:
            try:
                completion = gateway.complete_json(
                    VERIFY_TASK, _guard_messages(state, report), VERIFY_SCHEMA
                )
                problems = _guard_problems(_parse(completion))
            except TERMINAL_ERRORS:
                raise
            except (GatewayError, VerificationError, ValueError) as exc:
                # The citations resolved, so the answer is not known to be wrong. It is
                # known to be unchecked, and that is what gets recorded.
                return _recorded(
                    state,
                    report,
                    verified=False,
                    regenerated=regenerated,
                    degraded=False,
                    problems=[],
                    completion=completion,
                    error=str(exc),
                )

        if not problems:
            return _recorded(
                state,
                report,
                verified=True,
                regenerated=regenerated,
                degraded=False,
                problems=[],
                completion=completion,
            )

        if attempt >= MAX_REGENERATIONS:
            return _degraded(state, report, problems, regenerated, completion)

        state = regenerate(state, _correction(problems))
        regenerated = True

    raise AssertionError("unreachable")  # pragma: no cover


def _citation_problems(report: CitationReport, state: GraphState) -> list[str]:
    """What the deterministic check found wrong, phrased so it can be corrected."""
    problems: list[str] = []
    if report.unresolved:
        problems.append(
            f"The answer cites {', '.join(report.unresolved)}, which do not exist. "
            f"The evidence available is {', '.join(state.evidence.ids) or 'none'}."
        )
    if report.malformed:
        problems.append(
            f"The answer contains malformed citation marker(s): "
            f"{', '.join(report.malformed)}."
        )
    if state.evidence.items and not report.resolved:
        problems.append(
            "The answer cites no evidence at all. Every material statement needs the "
            "id of the evidence it rests on."
        )
    return problems


def _guard_messages(state: GraphState, report: CitationReport) -> list[dict[str, str]]:
    """What the guard sees: the answer, and the evidence it actually cited."""
    from vericlaim.config import get_settings

    cited = [state.evidence.get(evidence_id) for evidence_id in report.resolved]
    floor = get_settings().ocr_confidence_floor
    blocks = "\n\n".join(
        f"[{item.id}] {item.label} — {item.cite()}"
        + ("  ⚠ LOW CONFIDENCE" if item.is_low_confidence(floor) else "")
        + f"\n{item.content.strip()}"
        for item in cited
        if item is not None
    )
    payload = {
        "question": state.question,
        "answer": state.answer,
        "evidence": blocks or "No evidence was cited.",
    }
    return [
        {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]


def _parse(completion: Any) -> Mapping[str, Any]:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise VerificationError(f"Verification returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise VerificationError("Verification returned a non-object verdict")
    return parsed


def _guard_problems(raw: Mapping[str, Any]) -> list[str]:
    faithful = bool(raw.get("faithful"))
    overclaims = bool(raw.get("overclaims"))
    problems = [
        str(problem).strip() for problem in raw.get("problems") or () if str(problem).strip()
    ]

    if (not faithful or overclaims) and not problems:
        raise VerificationError(
            "Verification faulted the answer without naming what is wrong with it, so "
            "there is nothing a rewrite could correct"
        )
    return problems if (not faithful or overclaims) else []


def _correction(problems: Sequence[str]) -> str:
    return (
        "The previous answer failed verification. Correct exactly these problems, "
        "changing nothing else: " + " ".join(problems)
    )


def _degraded(
    state: GraphState,
    report: CitationReport,
    problems: Sequence[str],
    regenerated: bool,
    completion: Any | None,
) -> GraphState:
    """Replace an answer that cannot be justified with a statement of that fact.

    Deliberately not a hedged version of the answer. Publishing the text with a caveat
    attached would leave the unjustified statements in front of the reader, which is the
    outcome every check above exists to prevent.
    """
    text = (
        "This question could not be answered in a form that can be verified against the "
        "evidence gathered. The following problems remained after a corrected attempt: "
        + " ".join(problems)
        + " The evidence that was collected is shown alongside this response; no "
        "conclusion should be drawn from the unverified text."
    )
    return _recorded(
        state.with_(answer=text),
        report,
        verified=False,
        regenerated=regenerated,
        degraded=True,
        problems=list(problems),
        completion=completion,
    )


def _recorded(
    state: GraphState,
    report: CitationReport,
    *,
    verified: bool,
    regenerated: bool,
    degraded: bool,
    problems: Sequence[str],
    completion: Any | None,
    error: str = "",
) -> GraphState:
    citations: dict[str, Any] = {
        "ok": report.ok,
        "resolved": list(report.resolved),
        "unresolved": list(report.unresolved),
        "malformed": list(report.malformed),
        # Evidence gathered and never used. Retrieving from a source and then ignoring
        # it is a real failure mode, and the completeness score is computed from this.
        "uncited": list(report.uncited),
        "precision": report.precision,
        "coverage": report.coverage,
        "verified": verified,
        "regenerated": regenerated,
        "degraded": degraded,
        "problems": list(problems),
    }
    return state.with_(citations=citations).with_stage(
        StageRecord(
            name=STAGE,
            detail={
                "resolved": len(report.resolved),
                "unresolved": len(report.unresolved),
                "uncited": len(report.uncited),
                "verified": verified,
                "regenerated": regenerated,
                "degraded": degraded,
            },
            model=getattr(completion, "model", "") if completion else "",
            cost_usd=getattr(completion, "cost_usd", 0.0) if completion else 0.0,
            latency_ms=getattr(completion, "latency_ms", 0.0) if completion else 0.0,
            error=error,
        )
    )


def _default_regenerator(gateway: Any) -> Regenerator:
    """Ask synthesis again, told exactly what was wrong with its last attempt."""

    def regenerate(state: GraphState, correction: str) -> GraphState:
        from vericlaim.orchestrator.nodes.synthesize import synthesize

        return synthesize(state, gateway=gateway, correction=correction)

    return regenerate
