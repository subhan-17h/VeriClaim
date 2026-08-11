"""Per-page digital/scanned/mixed classification, the gate on the expensive path."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from vericlaim.config import get_settings
from vericlaim.scanned.classifier import (
    DEFAULT_DENSITY_THRESHOLD,
    DocumentProfile,
    PageProfile,
    classify_document,
    classify_page,
    documents_needing_ocr,
    profile_pdf,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
POLICIES = FIXTURES / "policies"
SCANS = FIXTURES / "scanned"

A4_AREA = 595.0 * 842.0


# ------------------------------------------------------------------ pure classification


def test_a_full_text_page_is_digital() -> None:
    # ~1000 characters on A4, the density measured across the real wordings.
    assert classify_page(1000, A4_AREA) == "digital"


def test_a_page_with_no_text_is_scanned() -> None:
    assert classify_page(0, A4_AREA) == "scanned"


def test_a_page_with_a_trace_of_text_is_mixed() -> None:
    """A stamped header over a scan: needs OCR, but is not a bare image."""
    assert classify_page(20, A4_AREA) == "mixed"


def test_the_threshold_boundary_is_inclusive_on_digital() -> None:
    exactly = int(DEFAULT_DENSITY_THRESHOLD * A4_AREA) + 1

    assert classify_page(exactly, A4_AREA) == "digital"
    assert classify_page(exactly // 4, A4_AREA) != "digital"


def test_a_page_without_geometry_is_treated_as_scanned() -> None:
    """Unjudgeable on density; costing OCR time beats losing the document."""
    assert classify_page(0, 0.0) == "scanned"
    assert classify_page(5000, 0.0) == "scanned"


def test_density_normalises_across_page_sizes() -> None:
    """The same text on a larger sheet must not be reclassified."""
    letter_area = 612.0 * 792.0

    assert classify_page(900, A4_AREA) == classify_page(
        int(900 * letter_area / A4_AREA), letter_area
    )


def test_a_custom_threshold_is_honoured() -> None:
    assert classify_page(1000, A4_AREA, threshold=0.5) == "mixed"


# ------------------------------------------------------------------- page profiles


def test_profile_reports_density() -> None:
    profile = PageProfile(page=1, char_count=1000, area=A4_AREA, kind="digital")

    assert profile.density == pytest.approx(1000 / A4_AREA)


def test_a_zero_area_profile_reports_zero_density() -> None:
    assert PageProfile(page=1, char_count=10, area=0.0, kind="scanned").density == 0.0


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("digital", False), ("scanned", True), ("mixed", True)],
)
def test_only_digital_pages_skip_ocr(kind: str, expected: bool) -> None:
    profile = PageProfile(page=1, char_count=0, area=A4_AREA, kind=kind)

    assert profile.needs_ocr is expected


# --------------------------------------------------------------- real digital corpus


@pytest.mark.parametrize(
    "name",
    ["HomeSecure_Plus_2026.pdf", "Landlord_Protect_2026.pdf", "Exclusions_Schedule_2026.pdf"],
)
def test_the_policy_corpus_is_classified_digital(name: str) -> None:
    """If this regresses, every indexing run pays full OCR over the wordings."""
    profile = profile_pdf(POLICIES / name)

    assert profile.kind == "digital"
    assert profile.needs_ocr is False
    assert profile.scanned_pages == ()


def test_the_configured_threshold_separates_the_two_populations() -> None:
    """The shipped default must sit between the measured populations, not inside one."""
    threshold = get_settings().scanned_char_density_threshold

    digital = [
        page.density
        for name in ("HomeSecure_Plus_2026.pdf", "Landlord_Protect_2026.pdf")
        for page in profile_pdf(POLICIES / name).pages
    ]
    scanned = [
        page.density for page in profile_pdf(SCANS / "CLM-1001_INSPECTION.pdf").pages
    ]

    assert min(digital) > threshold
    assert max(scanned) < threshold


# ---------------------------------------------------------------- real scanned corpus


@pytest.mark.parametrize(
    "name",
    ["CLM-1001_INSPECTION.pdf", "CLM-1002_INSPECTION.pdf", "CLM-1003_INSPECTION.pdf"],
)
def test_the_scanned_corpus_is_classified_scanned(name: str) -> None:
    profile = profile_pdf(SCANS / name)

    assert profile.kind == "scanned"
    assert profile.needs_ocr is True
    assert profile.scanned_pages == (1,)


def test_scanned_fixtures_carry_no_text_layer() -> None:
    """Guards the fixtures themselves: a text layer would let OCR tests pass without OCR."""
    for path in sorted(SCANS.glob("*.pdf")):
        pages = PdfReader(path).pages
        assert all(not (page.extract_text() or "").strip() for page in pages), path.name


# ------------------------------------------------------------------------ mixed docs


@pytest.fixture
def mixed_pdf(tmp_path: Path) -> Path:
    """A digital wording page followed by a scanned inspection page."""
    writer = PdfWriter()
    writer.add_page(PdfReader(POLICIES / "HomeSecure_Plus_2026.pdf").pages[2])
    writer.add_page(PdfReader(SCANS / "CLM-1001_INSPECTION.pdf").pages[0])
    path = tmp_path / "claim_file.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_a_document_with_both_page_kinds_is_mixed(mixed_pdf: Path) -> None:
    """A digital form with a scan stapled on is a real and common shape."""
    profile = profile_pdf(mixed_pdf)

    assert profile.kind == "mixed"
    assert [page.kind for page in profile.pages] == ["digital", "scanned"]


def test_a_mixed_document_needs_ocr_for_its_scanned_page_only(mixed_pdf: Path) -> None:
    profile = profile_pdf(mixed_pdf)

    assert profile.needs_ocr is True
    assert profile.scanned_pages == (2,)


def test_classify_document_summarises(mixed_pdf: Path) -> None:
    assert classify_document(POLICIES / "HomeSecure_Plus_2026.pdf") == "digital"
    assert classify_document(SCANS / "CLM-1001_INSPECTION.pdf") == "scanned"
    assert classify_document(mixed_pdf) == "mixed"


def test_an_empty_document_profile_is_digital() -> None:
    """Nothing to read is not the same as unreadable; it must not trigger OCR."""
    profile = DocumentProfile(path=Path("empty.pdf"), pages=())

    assert profile.kind == "digital"
    assert profile.needs_ocr is False


# ------------------------------------------------------------------------ filtering


def test_only_documents_needing_ocr_are_returned() -> None:
    paths = [
        POLICIES / "HomeSecure_Plus_2026.pdf",
        SCANS / "CLM-1001_INSPECTION.pdf",
        POLICIES / "Landlord_Protect_2026.pdf",
        SCANS / "CLM-1002_INSPECTION.pdf",
    ]

    selected = documents_needing_ocr(paths)

    assert [profile.path.name for profile in selected] == [
        "CLM-1001_INSPECTION.pdf",
        "CLM-1002_INSPECTION.pdf",
    ]
