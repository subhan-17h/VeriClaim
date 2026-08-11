"""OCR confidence must survive every hop from the page it was measured on.

Document -> Chunk -> Chroma metadata -> Chunk -> Evidence. Each hop is a place the
score can be dropped or misaligned, and a misaligned score is worse than none: it
reports one page's legibility for another page's text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.policy.chunking import chunk_document, page_confidence_lookup
from vericlaim.policy.models import Document
from vericlaim.policy.store import ChunkStore


def _scan(pages: list[str], confidences: list[float] | None) -> Document:
    return Document(
        name="CLM-1001_INSPECTION.pdf",
        path=Path("scanned/CLM-1001_INSPECTION.pdf"),
        text="\n\n".join(pages),
        pages=pages,
        page_count=len(pages),
        page_confidences=confidences,
    )


def _chunks(document: Document, **kwargs):
    return chunk_document(
        document,
        doc_id="scanned/CLM-1001_INSPECTION.pdf",
        chunk_size=400,
        chunk_overlap=60,
        source_type="scanned_pdf",
        **kwargs,
    )


# ------------------------------------------------------------------ page lookup


def test_lookup_maps_one_based_pages() -> None:
    lookup = page_confidence_lookup(_scan(["a", "b", "c"], [0.9, 0.5, 0.1]))

    assert lookup(1) == pytest.approx(0.9)
    assert lookup(2) == pytest.approx(0.5)
    assert lookup(3) == pytest.approx(0.1)


def test_lookup_is_bounds_checked() -> None:
    """An off-by-one would vouch for an unreadable page with a clean page's score."""
    lookup = page_confidence_lookup(_scan(["a", "b"], [0.9, 0.5]))

    assert lookup(0) is None
    assert lookup(3) is None
    assert lookup(None) is None


def test_lookup_returns_none_without_confidences() -> None:
    lookup = page_confidence_lookup(_scan(["a"], None))

    assert lookup(1) is None


# ----------------------------------------------------- document -> chunk


def test_each_chunk_inherits_its_own_pages_confidence() -> None:
    """Chunk granularity, not document: a clean page must not vouch for a bad one."""
    document = _scan(
        [
            "The pipe failed at a soldered joint with no corrosion present.",
            "Extensive smearing has rendered this page largely unreadable here.",
        ],
        [0.97, 0.12],
    )

    by_page = {chunk.page: chunk.ocr_confidence for chunk in _chunks(document)}

    assert by_page[1] == pytest.approx(0.97)
    assert by_page[2] == pytest.approx(0.12)


def test_the_engine_is_recorded_alongside_the_score() -> None:
    document = _scan(["Inspection findings for the property."], [0.9])

    chunks = _chunks(document, ocr_engine="rapidocr")

    assert all(chunk.ocr_engine == "rapidocr" for chunk in chunks)


def test_no_engine_is_recorded_without_a_score() -> None:
    """Naming an engine for text it never read would misattribute the extraction."""
    document = _scan(["Body text of a digitally parsed page."], None)

    chunks = _chunks(document, ocr_engine="rapidocr")

    assert all(chunk.ocr_engine is None for chunk in chunks)
    assert all(chunk.ocr_confidence is None for chunk in chunks)


def test_policy_chunks_carry_no_ocr_provenance() -> None:
    """The default path must stay untouched by the scanned path's additions."""
    document = Document(
        name="HomeSecure_Plus_2026.pdf",
        path=Path("policies/HomeSecure_Plus_2026.pdf"),
        text="SECTION 4 — WATER DAMAGE\n4.2 Sudden and accidental escape of water.\n",
        pages=["SECTION 4 — WATER DAMAGE\n4.2 Sudden and accidental escape of water.\n"],
        page_count=1,
    )

    chunks = chunk_document(
        document,
        doc_id="policies/HomeSecure_Plus_2026.pdf",
        chunk_size=400,
        chunk_overlap=60,
    )

    assert all(chunk.ocr_confidence is None for chunk in chunks)
    assert all(chunk.ocr_engine is None for chunk in chunks)


def test_a_split_block_keeps_its_page_confidence_on_every_piece() -> None:
    long_page = "Water tracked beneath the floor covering into the store room. " * 60
    document = _scan([long_page], [0.42])

    chunks = _chunks(document)

    assert len(chunks) > 1
    assert all(chunk.ocr_confidence == pytest.approx(0.42) for chunk in chunks)


def test_a_zero_confidence_page_is_carried_not_dropped() -> None:
    """0.0 is falsy; a truthiness check anywhere on this path would discard it."""
    document = _scan(["Whatever text was recovered from a ruined page."], [0.0])

    chunks = _chunks(document, ocr_engine="rapidocr")

    assert chunks
    assert all(chunk.ocr_confidence == 0.0 for chunk in chunks)
    assert all(chunk.ocr_engine == "rapidocr" for chunk in chunks)


# --------------------------------------------------------- chunk -> store -> chunk


def test_confidence_survives_the_store_round_trip(tmp_path: Path, embedder) -> None:
    document = _scan(
        [
            "The pipe failed at a soldered joint without corrosion.",
            "This page is smeared and largely unreadable throughout.",
        ],
        [0.97, 0.08],
    )
    chunks = _chunks(document, ocr_engine="rapidocr")
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))

    restored = {chunk.page: chunk for chunk in store.all_chunks()}

    assert restored[1].ocr_confidence == pytest.approx(0.97)
    assert restored[2].ocr_confidence == pytest.approx(0.08)
    assert restored[1].ocr_engine == "rapidocr"


def test_zero_confidence_survives_the_store_round_trip(
    tmp_path: Path, embedder
) -> None:
    """Chroma drops None metadata; 0.0 must not be dropped with it."""
    document = _scan(["Text recovered from a ruined page."], [0.0])
    chunks = _chunks(document, ocr_engine="rapidocr")
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))

    restored = store.all_chunks()[0]

    assert restored.ocr_confidence == 0.0
    assert restored.ocr_engine == "rapidocr"
