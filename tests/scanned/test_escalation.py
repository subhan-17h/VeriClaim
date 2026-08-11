"""Vision escalation: recovers degraded pages, refuses to invent unreadable ones."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest

from vericlaim.config import get_settings
from vericlaim.gateway.types import Usage
from vericlaim.policy.models import Document
from vericlaim.scanned.classifier import DocumentProfile, PageProfile
from vericlaim.scanned.docling_ocr import OcrResult
from vericlaim.scanned.escalation import (
    VISION_SCHEMA,
    escalate_low_confidence_pages,
    render_page_png,
)

SCANS = Path(__file__).parents[1] / "fixtures" / "scanned"


@dataclass
class FakeCompletion:
    parsed: Any
    text: str = ""
    provider: str = "google"
    model: str = "gemini-2.5-flash"
    cost_usd: float = 0.0
    usage: Usage = None  # type: ignore[assignment]


class FakeVisionGateway:
    """Records vision calls and returns scripted structured responses."""

    def __init__(self, *responses: Any, error: Exception | None = None) -> None:
        self._responses = list(responses)
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def complete_vision(self, task, prompt, images, *, schema=None, temperature=0.0):
        self.calls.append(
            {"task": task, "prompt": prompt, "images": images, "schema": schema}
        )
        if self._error is not None:
            raise self._error
        response = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return FakeCompletion(parsed=response)


def _result(pages: list[str], confidences: list[float], path: Path | None = None) -> OcrResult:
    source = path if path is not None else SCANS / "CLM-1002_INSPECTION.pdf"
    document = Document(
        name=source.name,
        path=source,
        text="\n\n".join(pages),
        pages=pages,
        page_count=len(pages),
        page_confidences=confidences,
    )
    profile = DocumentProfile(
        path=source,
        pages=tuple(
            PageProfile(page=index, char_count=len(text), area=500000.0, kind="scanned")
            for index, text in enumerate(pages, start=1)
        ),
    )
    return OcrResult(document=document, profile=profile, engine="rapidocr")


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stub page rendering, returning the pages asked for.

    The fixtures on disk are one page each; a multi-page scenario is about
    escalation's control flow, not about rasterising a real file.
    """
    from vericlaim.scanned import escalation as module

    requested: list[int] = []

    def render(path, page, *, dpi=200):
        requested.append(page)
        return b"\x89PNG\r\n\x1a\n" + b"stub"

    monkeypatch.setattr(module, "render_page_png", render)
    return requested


# ------------------------------------------------------------------- when it runs


def test_nothing_below_the_floor_makes_no_call() -> None:
    gateway = FakeVisionGateway()

    outcome = escalate_low_confidence_pages(_result(["good"], [0.95]), gateway=gateway)

    assert gateway.calls == []
    assert outcome.escalations == ()


def test_escalation_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from vericlaim.config import get_settings as real_settings

    settings = real_settings().model_copy(update={"ocr_vision_escalation": False})
    gateway = FakeVisionGateway()

    outcome = escalate_low_confidence_pages(
        _result([""], [0.0]), settings=settings, gateway=gateway
    )

    assert gateway.calls == []
    assert outcome.result.page_confidences == (0.0,)


def test_only_pages_below_the_floor_are_sent(fake_render) -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "recovered", "confidence": 0.8, "notes": ""}
    )

    escalate_low_confidence_pages(
        _result(["clean", "poor", "clean"], [0.95, 0.10, 0.91]), gateway=gateway
    )

    assert len(gateway.calls) == 1
    assert fake_render == [2]


def test_the_free_vision_tier_is_the_routed_task() -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "recovered", "confidence": 0.8, "notes": ""}
    )

    escalate_low_confidence_pages(_result(["poor"], [0.1]), gateway=gateway)

    assert gateway.calls[0]["task"] == "ocr_vision"
    assert gateway.calls[0]["schema"] == VISION_SCHEMA


# --------------------------------------------------------------------- recovery


def test_a_recovered_page_replaces_its_text() -> None:
    gateway = FakeVisionGateway(
        {
            "legible": True,
            "text": "The pipe ruptured suddenly at a soldered joint.",
            "confidence": 0.82,
            "notes": "",
        }
    )

    outcome = escalate_low_confidence_pages(_result(["frag ments"], [0.2]), gateway=gateway)

    assert outcome.result.document.pages == [
        "The pipe ruptured suddenly at a soldered joint."
    ]
    assert outcome.escalated_pages == (1,)


def test_document_text_is_rebuilt_from_the_replaced_pages(fake_render) -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "page two recovered", "confidence": 0.8, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(
        _result(["page one", "garbled"], [0.9, 0.1]), gateway=gateway
    )

    assert outcome.result.document.text == "page one\n\npage two recovered"


def test_an_escalated_confidence_is_capped_below_pristine() -> None:
    """A page that needed a second reading must not end up ranked with a clean one."""
    gateway = FakeVisionGateway(
        {"legible": True, "text": "recovered text", "confidence": 1.0, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    cap = get_settings().ocr_escalated_confidence_cap
    assert outcome.result.page_confidences[0] == pytest.approx(cap)
    assert cap < 1.0


def test_a_modest_self_report_is_not_inflated_to_the_cap() -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "partly recovered", "confidence": 0.4, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    assert outcome.result.page_confidences[0] == pytest.approx(0.4)


def test_the_escalation_is_recorded_for_the_trace() -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "recovered", "confidence": 0.8, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    record = outcome.escalations[0]
    assert record.outcome == "replaced"
    assert record.before_confidence == pytest.approx(0.1)
    assert record.provider == "google"
    assert record.model == "gemini-2.5-flash"


# ------------------------------------------------------------- refusing to invent


def test_an_illegible_verdict_keeps_the_refusal() -> None:
    """A model that just said it cannot read the page has no basis for a transcription."""
    gateway = FakeVisionGateway(
        {
            "legible": False,
            "text": "NORTHSTAR INSURANCE LIMITED PLUMBING INSPECTION REPORT",
            "confidence": 0.9,
            "notes": "page is smeared",
        }
    )

    outcome = escalate_low_confidence_pages(_result(["<!-- image -->"], [0.0]), gateway=gateway)

    assert outcome.result.document.pages == ["<!-- image -->"]
    assert outcome.escalated_pages == ()
    assert outcome.escalations[0].outcome == "illegible"


def test_an_empty_transcription_is_not_treated_as_recovery() -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "   ", "confidence": 0.9, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(_result(["original"], [0.1]), gateway=gateway)

    assert outcome.result.document.pages == ["original"]
    assert outcome.escalations[0].outcome == "illegible"


def test_the_schema_makes_illegibility_a_first_class_answer() -> None:
    """"I cannot read this" must be a valid response, not a deviation from the format."""
    assert VISION_SCHEMA["properties"]["legible"]["type"] == "boolean"
    assert "legible" in VISION_SCHEMA["required"]


def test_the_prompt_forbids_inference() -> None:
    from vericlaim.scanned.escalation import _PROMPT

    lowered = _PROMPT.lower()
    assert "verbatim" in lowered
    assert "do not complete partial words" in lowered
    assert "do not supply content you expect" in lowered


# ------------------------------------------------------------ degrading honestly


def test_an_unavailable_gateway_leaves_the_original_text() -> None:
    """An exhausted quota is a worse answer, not a failed index."""
    from vericlaim.gateway.types import QuotaExhaustedError

    gateway = FakeVisionGateway(
        error=QuotaExhaustedError(
            "daily limit reached", provider="google", model="gemini-2.5-flash"
        )
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    assert outcome.result.document.pages == ["garbled"]
    assert outcome.result.page_confidences == (0.1,)
    assert outcome.escalations[0].outcome == "unavailable"


def test_a_blocked_paid_rung_does_not_fail_the_index() -> None:
    from vericlaim.gateway.types import PaidFallbackBlockedError

    gateway = FakeVisionGateway(
        error=PaidFallbackBlockedError("ocr_vision", ["openai/gpt-4o-mini"])
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    assert outcome.escalations[0].outcome == "unavailable"
    assert "VC_ALLOW_PAID_FALLBACK" in outcome.escalations[0].detail


def test_a_render_failure_degrades_rather_than_raising() -> None:
    gateway = FakeVisionGateway()
    missing = _result(["garbled"], [0.1], path=Path("/nonexistent/never.pdf"))

    outcome = escalate_low_confidence_pages(missing, gateway=gateway)

    assert outcome.escalations[0].outcome == "render_failed"
    assert gateway.calls == []


def test_a_malformed_response_is_not_trusted() -> None:
    gateway = FakeVisionGateway("not a dict at all")

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    assert outcome.result.document.pages == ["garbled"]
    assert outcome.escalations[0].outcome == "illegible"


@pytest.mark.parametrize("value", [None, "high", float("nan"), [0.9]])
def test_an_unusable_confidence_defaults_low(value: Any) -> None:
    gateway = FakeVisionGateway(
        {"legible": True, "text": "recovered", "confidence": value, "notes": ""}
    )

    outcome = escalate_low_confidence_pages(_result(["garbled"], [0.1]), gateway=gateway)

    assert outcome.result.page_confidences[0] == 0.0


# ------------------------------------------------------------------- page render


def test_a_page_renders_to_png_bytes() -> None:
    data = render_page_png(SCANS / "CLM-1001_INSPECTION.pdf", 1, dpi=72)

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 1000


def test_rendering_an_absent_page_raises() -> None:
    """Whatever pypdfium raises, escalation catches it and degrades honestly."""
    with pytest.raises(pdfium.PdfiumError):
        render_page_png(SCANS / "CLM-1001_INSPECTION.pdf", 99)
