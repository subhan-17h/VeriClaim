"""Re-read poorly scanned pages with a vision model, without inviting invention.

RapidOCR is fast, free, and offline, which is why it runs first. It is also brittle:
on a skewed, speckled, low-resolution page it returns fragments or nothing. A vision
model reads such pages far better, so pages below the confidence floor are re-read
through the gateway.

The danger is specific and worth naming. Asked to transcribe a page it cannot read, a
language model will produce a fluent, plausible inspection report -- the exact
fabrication this project forbids, and more dangerous than the blank it replaces
because it arrives with no visible defect. Three things guard against that:

1. The model answers a **structured schema** whose first field is a legibility
   verdict, so "I cannot read this" is a first-class answer rather than a deviation
   from the requested format.
2. The prompt requires verbatim transcription and forbids inference, completion of
   partial words, and reconstruction of expected content.
3. A verdict of illegible **keeps the refusal**. Nothing the model wrote is used.

Escalation never aborts indexing. A missing key, an exhausted quota, or a blocked
paid rung leaves the original OCR text in place, flagged low-confidence, which is a
worse answer than escalating but a truthful one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vericlaim.config import Settings, get_settings
from vericlaim.gateway import ImagePart
from vericlaim.policy.models import Document
from vericlaim.scanned.docling_ocr import OcrResult
from vericlaim.tracing import traced

__all__ = (
    "ESCALATION_TASK",
    "EscalationOutcome",
    "PageEscalation",
    "VISION_SCHEMA",
    "escalate_low_confidence_pages",
    "render_page_png",
)

logger = logging.getLogger(__name__)

# Routed in config.yaml to the free Gemini vision tier.
ESCALATION_TASK = "ocr_vision"

# Rendering resolution for the page handed to the model. High enough that small print
# survives, low enough that the encoded image stays a sane request size.
RENDER_DPI = 200

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["legible", "text", "confidence", "notes"],
    "properties": {
        "legible": {
            "type": "boolean",
            "description": (
                "True only if you can actually read the text on this page. False if "
                "it is blurred, smeared, cut off, or otherwise unreadable."
            ),
        },
        "text": {
            "type": "string",
            "description": (
                "The text you can read, transcribed verbatim. Empty string if not "
                "legible. Never guess at or complete text you cannot clearly see."
            ),
        },
        "confidence": {
            "type": "number",
            "description": "Your confidence in the transcription, from 0.0 to 1.0.",
        },
        "notes": {
            "type": "string",
            "description": "Brief note on legibility problems, or an empty string.",
        },
    },
}

_PROMPT = """You are transcribing one page of a scanned insurance document whose \
automatic text recognition produced a poor result.

Transcribe only what you can actually see. This transcription becomes evidence in a \
claims decision, so an invented line is far worse than a missing one.

Rules:
- Transcribe verbatim. Do not correct, summarise, paraphrase, or reorder.
- Do not complete partial words or infer text hidden by damage or blur.
- Do not supply content you expect a document of this kind to contain.
- If the page is largely unreadable, set legible to false and text to an empty \
string. That is a correct and useful answer.
- If only part of the page is readable, transcribe that part and describe the gap in \
notes."""


@dataclass(frozen=True, slots=True)
class PageEscalation:
    """What escalation did to one page."""

    page: int
    outcome: str
    before_confidence: float
    after_confidence: float
    provider: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    detail: str = ""

    @property
    def replaced(self) -> bool:
        return self.outcome == "replaced"


@dataclass(frozen=True, slots=True)
class EscalationOutcome:
    """The document after escalation, plus a record of what happened per page."""

    result: OcrResult
    escalations: tuple[PageEscalation, ...] = ()

    @property
    def escalated_pages(self) -> tuple[int, ...]:
        """1-based pages whose text was replaced by the vision transcription."""
        return tuple(item.page for item in self.escalations if item.replaced)

    @property
    def cost_usd(self) -> float:
        return sum(item.cost_usd for item in self.escalations)


def render_page_png(path: Path, page: int, *, dpi: int = RENDER_DPI) -> bytes:
    """Render one 1-based PDF page to PNG bytes."""
    import io

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    try:
        image = document[page - 1].render(scale=dpi / 72.0).to_pil()
    finally:
        document.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@traced(name="ocr_vision_escalation", run_type="tool")
def escalate_low_confidence_pages(
    result: OcrResult,
    *,
    settings: Settings | None = None,
    gateway: Any = None,
) -> EscalationOutcome:
    """Re-read every page below the confidence floor with the vision tier.

    Returns the document unchanged, plus an empty record, when escalation is disabled
    or nothing falls below the floor.
    """
    settings = settings if settings is not None else get_settings()
    if not settings.ocr_vision_escalation:
        return EscalationOutcome(result=result)

    floor = settings.ocr_confidence_floor
    candidates = result.low_confidence_pages(floor)
    if not candidates:
        return EscalationOutcome(result=result)

    if gateway is None:
        gateway = _default_gateway_or_none()
        if gateway is None:
            return EscalationOutcome(
                result=result,
                escalations=tuple(
                    PageEscalation(
                        page=page,
                        outcome="unavailable",
                        before_confidence=result.page_confidences[page - 1],
                        after_confidence=result.page_confidences[page - 1],
                        detail="No usable model gateway.",
                    )
                    for page in candidates
                ),
            )

    pages = list(result.document.pages or [])
    confidences = list(result.page_confidences)
    escalations: list[PageEscalation] = []

    for page in candidates:
        escalation = _escalate_page(result, page, floor, gateway, settings)
        escalations.append(escalation)
        if escalation.replaced:
            index = page - 1
            pages[index] = escalation.detail
            confidences[index] = escalation.after_confidence

    document = replace_document_text(result.document, pages, confidences)
    return EscalationOutcome(
        result=OcrResult(document=document, profile=result.profile, engine=result.engine),
        escalations=tuple(escalations),
    )


def replace_document_text(
    document: Document,
    pages: list[str],
    confidences: list[float],
) -> Document:
    """Return a copy of ``document`` with new page text and confidences."""
    return document.model_copy(
        update={
            "pages": pages,
            "text": "\n\n".join(pages),
            "page_confidences": confidences,
        }
    )


def _default_gateway_or_none() -> Any | None:
    """Return the shared gateway, or None if it cannot be built.

    Construction is a failure mode of its own: an absent API key raises here rather
    than at call time. Escalation is an improvement, not a requirement, so an
    unbuildable gateway must degrade exactly as an exhausted quota does -- leaving the
    original OCR text in place, flagged -- instead of aborting the whole index.
    """
    try:
        from vericlaim.gateway import default_gateway

        return default_gateway()
    except Exception as exc:  # noqa: BLE001 - a missing key must not abort indexing
        logger.warning("No model gateway available for vision escalation: %s", exc)
        return None


def _escalate_page(
    result: OcrResult,
    page: int,
    floor: float,
    gateway: Any,
    settings: Settings,
) -> PageEscalation:
    """Attempt one page. Any failure degrades to keeping the original OCR text."""
    before = result.page_confidences[page - 1]

    try:
        image = render_page_png(result.document.path, page)
    except Exception as exc:  # noqa: BLE001 - a render failure must not abort indexing
        logger.warning("Could not render %s page %d: %s", result.document.path, page, exc)
        return PageEscalation(
            page=page,
            outcome="render_failed",
            before_confidence=before,
            after_confidence=before,
            detail=str(exc),
        )

    try:
        completion = gateway.complete_vision(
            ESCALATION_TASK,
            _PROMPT,
            [ImagePart(data=image, mime_type="image/png")],
            schema=VISION_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 - quota, budget, and outages all land here
        # Deliberately broad. Escalation is an improvement, not a requirement: an
        # exhausted free-tier quota or a refused paid rung must leave a worse but
        # truthful answer, not fail the whole index.
        logger.warning("Vision escalation unavailable for page %d: %s", page, exc)
        return PageEscalation(
            page=page,
            outcome="unavailable",
            before_confidence=before,
            after_confidence=before,
            detail=f"{type(exc).__name__}: {exc}",
        )

    parsed = completion.parsed if isinstance(completion.parsed, dict) else {}
    text = str(parsed.get("text") or "").strip()
    legible = bool(parsed.get("legible"))
    reported = _clamp(parsed.get("confidence"))

    if not legible or not text:
        # The refusal stands. Nothing the model wrote is used, because a model that
        # has just reported it cannot read the page has no basis for any transcription.
        return PageEscalation(
            page=page,
            outcome="illegible",
            before_confidence=before,
            after_confidence=before,
            provider=completion.provider,
            model=completion.model,
            cost_usd=completion.cost_usd,
            detail=str(parsed.get("notes") or ""),
        )

    return PageEscalation(
        page=page,
        outcome="replaced",
        before_confidence=before,
        # Capped at the floor's own confidence ceiling rather than trusting the
        # model's self-report outright: a page that needed escalation is not a page
        # to then call pristine, and the cap keeps its evidence qualified.
        after_confidence=min(reported, settings.ocr_escalated_confidence_cap),
        provider=completion.provider,
        model=completion.model,
        cost_usd=completion.cost_usd,
        detail=text,
    )


def _clamp(value: Any) -> float:
    """Coerce a model-reported confidence into [0, 1], defaulting low."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))
