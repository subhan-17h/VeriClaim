"""OCR extraction: explicit engine options, and NaN-safe per-page confidence."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from vericlaim.policy.loaders.docling_parser import ConverterOptions, DoclingSetupError
from vericlaim.policy.models import Document
from vericlaim.scanned.classifier import DocumentProfile, PageProfile
from vericlaim.scanned.docling_ocr import (
    DIGITAL_CONFIDENCE,
    NO_OCR_CONFIDENCE,
    OcrResult,
    ScannedPdfParser,
    page_confidence,
)

SCANS = Path(__file__).parents[1] / "fixtures" / "scanned"


# ------------------------------------------------------------- confidence mapping


def test_a_finite_score_passes_through() -> None:
    assert page_confidence(0.87, is_digital=False) == pytest.approx(0.87)


def test_nan_collapses_to_zero() -> None:
    """The load-bearing case: nan < floor is False, so NaN must never survive.

    Docling reports a non-finite ocr_score exactly when OCR produced no cells -- the
    total-failure case. Left as NaN, the worst page in a corpus is the one page that
    never trips a threshold.
    """
    assert page_confidence(math.nan, is_digital=False) == NO_OCR_CONFIDENCE
    assert page_confidence(math.nan, is_digital=False) < 0.6


def test_infinity_collapses_to_zero() -> None:
    assert page_confidence(math.inf, is_digital=False) == NO_OCR_CONFIDENCE
    assert page_confidence(-math.inf, is_digital=False) == NO_OCR_CONFIDENCE


def test_a_missing_score_collapses_to_zero() -> None:
    assert page_confidence(None, is_digital=False) == NO_OCR_CONFIDENCE


def test_a_digital_page_is_full_confidence() -> None:
    """Its text came from a text layer; scoring it zero would escalate a clean page."""
    assert page_confidence(math.nan, is_digital=True) == DIGITAL_CONFIDENCE
    assert page_confidence(None, is_digital=True) == DIGITAL_CONFIDENCE


def test_scores_are_clamped_into_range() -> None:
    """Evidence rejects a confidence outside [0, 1] at construction."""
    assert page_confidence(1.4, is_digital=False) == 1.0
    assert page_confidence(-0.2, is_digital=False) == 0.0


# ----------------------------------------------------------------- parser options


def test_the_parser_enables_ocr_by_default() -> None:
    assert ScannedPdfParser()._options.do_ocr is True


def test_the_language_is_english_never_the_library_default() -> None:
    """RapidOcrOptions.lang defaults to ["chinese"]; leaving it implicit is a live bug."""
    assert ScannedPdfParser()._options.ocr_lang == ("english",)


def test_a_converter_without_ocr_is_rejected() -> None:
    """Such a converter extracts nothing from an image-only page and reports no error."""
    options = ConverterOptions(artifacts_path=Path("/models"), do_ocr=False)

    with pytest.raises(DoclingSetupError, match="requires do_ocr=True"):
        ScannedPdfParser(options=options)


def test_the_ocr_converter_is_a_different_cache_entry_than_the_digital_one() -> None:
    """The C-3.2 cache-key fix, from the side that depends on it."""
    digital = ConverterOptions(artifacts_path=Path("/models"))
    ocr = replace(digital, do_ocr=True)

    assert ocr != digital
    assert hash(ocr) != hash(digital)


def test_the_engine_is_reported() -> None:
    assert ScannedPdfParser().engine == "rapidocr"


# ------------------------------------------------------------- result inspection


def _result(pages: list[str], confidences: list[float]) -> OcrResult:
    document = Document(
        name="CLM-1.pdf",
        path=Path("scanned/CLM-1.pdf"),
        text="\n\n".join(pages),
        pages=pages,
        page_count=len(pages),
        page_confidences=confidences,
    )
    profile = DocumentProfile(
        path=document.path,
        pages=tuple(
            PageProfile(page=index, char_count=len(text), area=500000.0, kind="scanned")
            for index, text in enumerate(pages, start=1)
        ),
    )
    return OcrResult(document=document, profile=profile, engine="rapidocr")


def test_pages_below_the_floor_are_identified() -> None:
    result = _result(["good page", "poor page", "fine page"], [0.95, 0.31, 0.88])

    assert result.low_confidence_pages(0.6) == (2,)


def test_the_floor_boundary_excludes_an_exact_match() -> None:
    result = _result(["a", "b"], [0.6, 0.599])

    assert result.low_confidence_pages(0.6) == (2,)


def test_unreadable_pages_are_tracked_separately_from_low_confidence() -> None:
    """A page read poorly can be qualified; a page yielding nothing supports no claim."""
    result = _result(["some text", "   ", ""], [0.4, 0.0, 0.0])

    assert result.low_confidence_pages(0.6) == (1, 2, 3)
    assert result.unreadable_pages() == (2, 3)


def test_a_fully_readable_document_flags_nothing() -> None:
    result = _result(["page one", "page two"], [0.97, 0.93])

    assert result.low_confidence_pages(0.6) == ()
    assert result.unreadable_pages() == ()


# --------------------------------------------------------- live OCR (needs weights)


@pytest.mark.ocr
def test_a_clean_scan_is_read_with_high_confidence() -> None:
    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1001_INSPECTION.pdf")

    assert "CLM-1001" in result.document.text
    assert "sudden" in result.document.text.lower()
    assert result.page_confidences[0] > 0.9
    assert result.low_confidence_pages(0.6) == ()


@pytest.mark.ocr
def test_an_image_only_pdf_yields_text_that_extraction_cannot() -> None:
    """The capability the reference implementation has at exactly zero percent."""
    from pypdf import PdfReader

    path = SCANS / "CLM-1002_INSPECTION.pdf"
    extracted = "".join((page.extract_text() or "") for page in PdfReader(path).pages)

    result = ScannedPdfParser().parse_with_confidence(path)

    assert extracted.strip() == ""
    assert len(result.document.text.strip()) > 300


@pytest.mark.ocr
def test_the_degraded_scan_carries_the_counter_evidence() -> None:
    """Contradictory evidence has to be a real retrievable case, not an aspiration."""
    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1002_INSPECTION.pdf")

    assert "gradual" in result.document.text.lower()
    assert "CLM-1002" in result.document.text


@pytest.mark.ocr
def test_an_illegible_page_reports_zero_not_nan() -> None:
    """End to end, on the page that produced NaN before the collapse was added."""
    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1003_INSPECTION.pdf")

    confidence = result.page_confidences[0]
    assert math.isfinite(confidence)
    assert confidence == NO_OCR_CONFIDENCE
    assert result.low_confidence_pages(0.6) == (1,)
    assert result.unreadable_pages() == (1,)


@pytest.mark.ocr
def test_confidences_align_with_pages() -> None:
    """They are zipped positionally downstream; a length mismatch misattributes them."""
    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1001_INSPECTION.pdf")

    assert result.document.pages is not None
    assert len(result.page_confidences) == len(result.document.pages)
    assert len(result.page_confidences) == result.document.page_count
