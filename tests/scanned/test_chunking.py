"""Chunking OCR output: page anchoring, no fabricated clauses, honest unreadables."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.policy.chunking import chunk_document
from vericlaim.policy.models import Document
from vericlaim.scanned.chunking import UNREADABLE_PAGE_TEXT, chunk_scanned_document

SCANS = Path(__file__).parents[1] / "fixtures" / "scanned"


def _scan(*pages: str, confidences: list[float] | None = None) -> Document:
    return Document(
        name="CLM-1001_INSPECTION.pdf",
        path=Path("scanned/CLM-1001_INSPECTION.pdf"),
        text="\n\n".join(pages),
        pages=list(pages),
        page_count=len(pages),
        page_confidences=confidences,
    )


def _chunks(document: Document, **kwargs):
    return chunk_scanned_document(
        document,
        doc_id="scanned/CLM-1001_INSPECTION.pdf",
        chunk_size=400,
        chunk_overlap=60,
        **kwargs,
    )


# ------------------------------------------------------- no fabricated coordinates


def test_the_scanned_path_never_assigns_a_clause_id() -> None:
    """A scanned report has no clauses; naming one asserts something untrue."""
    document = _scan("## FINDINGS\n\n4.2 The pipe failed at a soldered joint.\n")

    assert all(chunk.clause_id is None for chunk in _chunks(document))


def test_an_estimate_line_does_not_become_a_clause() -> None:
    """The concrete reason this path exists, contrasted against the policy chunker."""
    page = "184.000 Estimated cost of repair and making good the damage\n"
    document = _scan(page)

    from_policy = chunk_document(
        document,
        doc_id="scanned/CLM-1001_INSPECTION.pdf",
        chunk_size=400,
        chunk_overlap=60,
        source_type="scanned_pdf",
    )
    from_scanned = _chunks(document)

    assert from_policy[0].clause_id == "184.000"  # what we must not ship
    assert from_scanned[0].clause_id is None


# ------------------------------------------------------------------- page anchoring


def test_every_breadcrumb_carries_its_page() -> None:
    """Without clause numbers, the page is the only coordinate a reader can use."""
    document = _scan("## FINDINGS\n\nPage one body.\n", "## CONCLUSION\n\nPage two body.\n")

    sections = [chunk.section for chunk in _chunks(document)]

    assert sections == [
        "CLM-1001_INSPECTION.pdf > p.1 > FINDINGS",
        "CLM-1001_INSPECTION.pdf > p.2 > CONCLUSION",
    ]


def test_recovered_headings_are_kept() -> None:
    """Docling emits headings even from OCR; they are the only structure available."""
    document = _scan("## FINDINGS\n\nThe pipe failed.\n")

    assert "FINDINGS" in (_chunks(document)[0].section or "")


def test_a_page_without_headings_still_gets_a_breadcrumb() -> None:
    document = _scan("Loose body text with no heading at all.\n")

    assert _chunks(document)[0].section == "CLM-1001_INSPECTION.pdf > p.1"


def test_a_page_free_document_omits_the_page_anchor() -> None:
    document = Document(
        name="notes.txt", path=Path("scanned/notes.txt"), text="Body text here."
    )

    chunk = _chunks(document)[0]

    assert chunk.page is None
    assert chunk.section == "notes.txt"


# ------------------------------------------------------------- exporter syntax


def test_heading_markers_do_not_leak_into_chunk_text() -> None:
    """Evidence.content is quoted verbatim into a cited answer."""
    document = _scan("## FINDINGS\n\nThe pipe failed at a soldered joint.\n")

    text = _chunks(document)[0].text

    assert text.startswith("FINDINGS")
    assert "##" not in text


def test_list_markers_do_not_leak_into_chunk_text() -> None:
    document = _scan("## FINDINGS\n\n- The pipe failed at a joint.\n- No corrosion.\n")

    text = _chunks(document)[0].text

    assert "- The pipe" not in text
    assert "The pipe failed at a joint." in text


def test_image_placeholders_are_not_indexed() -> None:
    """A chunk whose whole content is an exporter comment is retrievable and meaningless."""
    document = _scan("## FINDINGS\n\n<!-- image -->\n\nThe pipe failed.\n")

    texts = " ".join(chunk.text for chunk in _chunks(document))

    assert "<!-- image -->" not in texts
    assert "The pipe failed." in texts


# ------------------------------------------------------------ unreadable pages


def test_a_page_with_no_recoverable_text_becomes_refusal_grade_evidence() -> None:
    """Not silence. "We could not read this" is itself a finding a reader needs."""
    document = _scan("<!-- image -->\n", confidences=[0.0])

    chunks = _chunks(document, ocr_engine="rapidocr")

    assert len(chunks) == 1
    assert chunks[0].text == UNREADABLE_PAGE_TEXT
    assert chunks[0].ocr_confidence == 0.0


def test_the_unreadable_marker_cannot_pass_as_recovered_content() -> None:
    """It must not read like something an inspector could have written."""
    assert "UNREADABLE PAGE" in UNREADABLE_PAGE_TEXT
    assert "no claim can be based on it" in UNREADABLE_PAGE_TEXT


def test_an_unreadable_page_keeps_its_place_among_readable_ones() -> None:
    document = _scan(
        "## FINDINGS\n\nThe pipe failed at a joint.\n",
        "<!-- image -->\n",
        "## CONCLUSION\n\nSudden and accidental escape.\n",
        confidences=[0.95, 0.0, 0.91],
    )

    chunks = _chunks(document)

    assert [chunk.page for chunk in chunks] == [1, 2, 3]
    assert chunks[1].text == UNREADABLE_PAGE_TEXT
    assert chunks[1].ocr_confidence == 0.0


def test_a_document_of_only_unreadable_pages_still_produces_chunks() -> None:
    """Otherwise the indexer's zero-chunk guard aborts a corpus over one smeared page."""
    document = _scan("<!-- image -->\n", "   \n", confidences=[0.0, 0.0])

    chunks = _chunks(document)

    assert len(chunks) == 2
    assert all(chunk.text == UNREADABLE_PAGE_TEXT for chunk in chunks)


# ---------------------------------------------------------------- ocr provenance


def test_chunks_carry_page_confidence_and_engine() -> None:
    document = _scan(
        "## FINDINGS\n\nClean page.\n",
        "## CONCLUSION\n\nPoor page.\n",
        confidences=[0.97, 0.31],
    )

    by_page = {c.page: c for c in _chunks(document, ocr_engine="rapidocr")}

    assert by_page[1].ocr_confidence == pytest.approx(0.97)
    assert by_page[2].ocr_confidence == pytest.approx(0.31)
    assert by_page[1].ocr_engine == "rapidocr"


def test_every_chunk_is_typed_as_scanned() -> None:
    document = _scan("## FINDINGS\n\nBody.\n")

    assert all(chunk.source_type == "scanned_pdf" for chunk in _chunks(document))


def test_chunk_ids_are_unique_and_document_prefixed() -> None:
    document = _scan("## A\n\nOne.\n", "## B\n\nTwo.\n")

    ids = [chunk.id for chunk in _chunks(document)]

    assert len(set(ids)) == len(ids)
    assert all(chunk_id.startswith("scanned/CLM-1001_INSPECTION.pdf:") for chunk_id in ids)


def test_a_long_block_splits_and_keeps_its_provenance() -> None:
    body = "Water tracked beneath the floor covering into the store room. " * 60
    document = _scan(f"## FINDINGS\n\n{body}\n", confidences=[0.42])

    chunks = _chunks(document, ocr_engine="rapidocr")

    assert len(chunks) > 1
    assert all(chunk.ocr_confidence == pytest.approx(0.42) for chunk in chunks)
    assert all(chunk.page == 1 for chunk in chunks)


# ------------------------------------------------------------ live OCR end to end


@pytest.mark.ocr
def test_the_clean_scan_chunks_with_its_findings() -> None:
    from vericlaim.scanned.docling_ocr import ScannedPdfParser

    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1001_INSPECTION.pdf")
    chunks = chunk_scanned_document(
        result.document,
        doc_id="scanned/CLM-1001_INSPECTION.pdf",
        chunk_size=400,
        chunk_overlap=60,
        ocr_engine=result.engine,
    )

    assert chunks
    assert all(chunk.clause_id is None for chunk in chunks)
    assert all("p.1" in (chunk.section or "") for chunk in chunks)
    assert any("sudden" in chunk.text.lower() for chunk in chunks)


@pytest.mark.ocr
def test_the_illegible_scan_yields_one_refusal_grade_chunk() -> None:
    from vericlaim.scanned.docling_ocr import ScannedPdfParser

    result = ScannedPdfParser().parse_with_confidence(SCANS / "CLM-1003_INSPECTION.pdf")
    chunks = chunk_scanned_document(
        result.document,
        doc_id="scanned/CLM-1003_INSPECTION.pdf",
        chunk_size=400,
        chunk_overlap=60,
        ocr_engine=result.engine,
    )

    assert len(chunks) == 1
    assert chunks[0].text == UNREADABLE_PAGE_TEXT
    assert chunks[0].ocr_confidence == 0.0
