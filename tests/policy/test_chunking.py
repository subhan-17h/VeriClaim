"""Policy-form heading grammar, clause identity, and the pure text splitter."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.policy.chunking import (
    CHARS_PER_TOKEN,
    _match_heading,
    _unmerge_inline_clauses,
    chunk_document,
    split_text,
)
from vericlaim.policy.loaders import DoclingParser, PdfParser
from vericlaim.policy.models import Document

FIXTURES = Path(__file__).parents[1] / "fixtures" / "policies"
HOMESECURE = FIXTURES / "HomeSecure_Plus_2026.pdf"


def _document(*pages: str, name: str = "wording.pdf") -> Document:
    return Document(
        name=name,
        path=Path(f"/corpus/{name}"),
        text="\n\n".join(pages),
        pages=list(pages),
        page_count=len(pages),
    )


def _chunks(document: Document, *, doc_id: str = "policies/wording.pdf"):
    return chunk_document(document, doc_id=doc_id, chunk_size=400, chunk_overlap=60)


# --------------------------------------------------------------- heading grammar


@pytest.mark.parametrize(
    ("line", "expected_id"),
    [
        ("SECTION 4 — WATER DAMAGE", "SECTION 4"),
        ("SECTION 4 - WATER DAMAGE", "SECTION 4"),
        ("SECTION 4: WATER DAMAGE", "SECTION 4"),
        ("SECTION 4", "SECTION 4"),
        ("SECTION IV — WATER DAMAGE", "SECTION IV"),
        ("COVERAGE A — DWELLING", "COVERAGE A"),
        ("COVERAGE B — CONTENTS", "COVERAGE B"),
        ("ENDORSEMENT 2 — UNOCCUPANCY CONDITION", "ENDORSEMENT 2"),
        ("4.2 Sudden and accidental escape of water", "4.2"),
        ("5.1 Loss or damage caused by gradual leakage", "5.1"),
        ("4.2.1 A sub-clause of the water damage section", "4.2.1"),
    ],
)
def test_policy_headings_yield_their_clause_identifier(line: str, expected_id: str) -> None:
    heading = _match_heading(line)

    assert heading is not None
    assert heading[2] == expected_id


def test_all_caps_part_titles_are_headings_without_an_identifier() -> None:
    heading = _match_heading("GENERAL EXCLUSIONS SCHEDULE")

    assert heading is not None
    assert heading[2] is None


def test_markdown_headings_keep_their_depth_and_gain_a_clause_id() -> None:
    """Docling decides the hierarchy; the label is still parsed for an identifier."""
    heading = _match_heading("## SECTION 4 - WATER DAMAGE")

    assert heading is not None
    depth, label, clause_id = heading
    assert depth == 2
    assert clause_id == "SECTION 4"
    assert "WATER DAMAGE" in label


def test_list_markers_are_stripped_before_matching() -> None:
    """Docling exports a numbered clause as a list item; a text extractor does not."""
    assert _match_heading("- 4.2 Sudden and accidental escape") == _match_heading(
        "4.2 Sudden and accidental escape"
    )


@pytest.mark.parametrize(
    "line",
    [
        "The insurer will not indemnify the insured in respect of:",
        "5 percent of the sum insured is retained by the insured",
        "Section 4 ................................................. 12",
        "up to PKR 150,000 for each and every claim",
        "",
    ],
    ids=["prose", "bare-figure", "toc-dot-leader", "amount", "empty"],
)
def test_body_text_is_not_mistaken_for_a_heading(line: str) -> None:
    assert _match_heading(line) is None


def test_a_deeper_heading_does_not_pop_its_parent() -> None:
    document = _document("SECTION 4 — WATER DAMAGE\n4.2 Sudden and accidental escape.\n")

    chunk = _chunks(document)[-1]

    assert chunk.clause_id == "4.2"
    assert "SECTION 4" in (chunk.section or "")


def test_a_sibling_heading_replaces_its_predecessor() -> None:
    document = _document(
        "SECTION 4 — WATER DAMAGE\n4.1 First clause.\nSECTION 5 — EXCLUSIONS\n5.1 Excluded.\n"
    )

    last = _chunks(document)[-1]

    assert last.clause_id == "5.1"
    assert "SECTION 5" in (last.section or "")
    assert "SECTION 4" not in (last.section or "")


# --------------------------------------------------------- merged-clause recovery


def test_inline_clauses_are_split_back_onto_their_own_lines() -> None:
    merged = "in respect of: 5.1 Loss by leakage. 5.2 Loss by wear and tear."

    assert _unmerge_inline_clauses(merged).splitlines() == [
        "in respect of:",
        "5.1 Loss by leakage.",
        "5.2 Loss by wear and tear.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "up to PKR 150,000. A deductible of PKR 25,000 applies.",
        "see clause 4.2 above for the treatment of sudden escape",
        "the limit is 4.2 million rupees in aggregate",
    ],
    ids=["amount-then-capital", "cross-reference", "lowercase-follows"],
)
def test_inline_splitting_leaves_ordinary_prose_alone(text: str) -> None:
    """The boundary must be narrow: a false split would fabricate a clause number."""
    assert _unmerge_inline_clauses(text) == text


def test_merged_exclusion_run_recovers_every_clause() -> None:
    page = (
        "## SECTION 5 - EXCLUSIONS\n\n"
        "- The insurer will not indemnify the insured in respect of: "
        "5.1 Loss caused by gradual leakage over a period of time. "
        "5.2 Loss arising from wear and tear. "
        "5.3 The cost of repairing the pipe itself.\n"
    )

    clause_ids = {chunk.clause_id for chunk in _chunks(_document(page))}

    assert {"5.1", "5.2", "5.3"} <= clause_ids


# ----------------------------------------------------------------- chunk metadata


def test_chunk_ids_are_prefixed_with_document_identity() -> None:
    """Two documents sharing a basename must not produce colliding chunk ids."""
    document = _document("SECTION 1 — DEFINITIONS\n1.1 A definition.\n")

    first = _chunks(document, doc_id="claims/CLM-1001/estimate.pdf")
    second = _chunks(document, doc_id="claims/CLM-1002/estimate.pdf")

    assert {chunk.id for chunk in first}.isdisjoint({chunk.id for chunk in second})
    assert first[0].id.startswith("claims/CLM-1001/estimate.pdf:")


def test_chunks_carry_their_page_number() -> None:
    document = _document(
        "SECTION 4 — WATER DAMAGE\n4.1 On page one.\n",
        "SECTION 5 — EXCLUSIONS\n5.1 On page two.\n",
    )

    pages = {chunk.clause_id: chunk.page for chunk in _chunks(document)}

    assert pages["4.1"] == 1
    assert pages["5.1"] == 2


def test_page_free_documents_leave_the_page_unset() -> None:
    document = Document(name="notes.txt", path=Path("/corpus/notes.txt"), text="4.1 Body.")

    assert all(chunk.page is None for chunk in _chunks(document))


def test_chunks_default_to_the_policy_source_type() -> None:
    document = _document("SECTION 1 — DEFINITIONS\n1.1 A definition.\n")

    assert all(chunk.source_type == "policy" for chunk in _chunks(document))
    assert all(chunk.ocr_confidence is None for chunk in _chunks(document))


def test_source_type_is_selectable_for_the_scanned_path() -> None:
    document = _document("Inspection report body text.")

    chunks = chunk_document(
        document, doc_id="scanned/a.pdf", chunk_size=400, chunk_overlap=60,
        source_type="scanned_pdf",
    )

    assert all(chunk.source_type == "scanned_pdf" for chunk in chunks)


def test_embed_text_prepends_the_breadcrumb() -> None:
    document = _document("SECTION 4 — WATER DAMAGE\n4.2 Sudden and accidental escape.\n")

    chunk = _chunks(document)[-1]

    assert chunk.embed_text.startswith(chunk.section or "")
    assert chunk.text in chunk.embed_text


def test_content_hash_tracks_the_text() -> None:
    document = _document("SECTION 1 — DEFINITIONS\n1.1 A definition.\n")

    for chunk in _chunks(document):
        assert chunk.content_hash != ""
    assert len({chunk.content_hash for chunk in _chunks(document)}) == len(_chunks(document))


def test_a_long_block_splits_while_keeping_its_clause() -> None:
    body = "Sudden and accidental escape of water. " * 200
    document = _document(f"SECTION 4 — WATER DAMAGE\n4.2 {body}\n")

    chunks = _chunks(document)

    assert len(chunks) > 1
    assert all(chunk.clause_id == "4.2" for chunk in chunks if chunk.clause_id == "4.2")


def test_empty_document_yields_no_chunks() -> None:
    assert _chunks(_document("", "   ")) == []


# ------------------------------------------------------------------- split_text


def test_split_text_returns_one_chunk_when_under_the_limit() -> None:
    assert split_text("short body", 100, 10) == ["short body"]


def test_split_text_respects_the_character_limit() -> None:
    text = "word " * 500

    for chunk in split_text(text, 50, 10):
        assert len(chunk) <= 50 * CHARS_PER_TOKEN


def test_split_text_overlaps_with_real_text() -> None:
    text = "".join(f"sentence {index}. " for index in range(200))

    chunks = split_text(text, 40, 10)

    assert len(chunks) > 1
    # The tail of one chunk reappears at the head of the next.
    assert chunks[1][:10] in chunks[0]


def test_split_text_covers_the_whole_input() -> None:
    text = "".join(f"clause {index} body text. " for index in range(100))

    reassembled = "".join(split_text(text, 30, 0))

    assert reassembled == text


def test_split_text_terminates_without_a_separator() -> None:
    """A single unbroken token must hard-cut rather than loop forever."""
    chunks = split_text("x" * 1000, 20, 5)

    assert len(chunks) > 1
    assert "".join(chunks) != ""


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
    ids=["zero-size", "negative-size", "negative-overlap", "equal", "larger"],
)
def test_split_text_rejects_invalid_bounds(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        split_text("body", chunk_size, overlap)


def test_split_text_ignores_blank_input() -> None:
    assert split_text("", 100, 10) == []
    assert split_text("   \n\n  ", 100, 10) == []


# --------------------------------------------------------- against the real fixture


def test_fixture_chunks_at_clause_level() -> None:
    document = PdfParser().parse(HOMESECURE)

    chunks = chunk_document(
        document,
        doc_id="policies/HomeSecure_Plus_2026.pdf",
        chunk_size=400,
        chunk_overlap=60,
    )
    by_clause = {chunk.clause_id: chunk for chunk in chunks if chunk.clause_id}

    assert by_clause["4.2"].page == 3
    assert "PKR 25,000" in by_clause["4.2"].text
    assert by_clause["5.1"].page == 4
    assert "gradual leakage" in by_clause["5.1"].text


@pytest.mark.docling
def test_both_pdf_parsers_chunk_the_same_wording_alike() -> None:
    """Citations must not depend on which parser was configured when indexing ran."""
    kwargs = {
        "doc_id": "policies/HomeSecure_Plus_2026.pdf",
        "chunk_size": 400,
        "chunk_overlap": 60,
    }
    docling = chunk_document(DoclingParser().parse(HOMESECURE), **kwargs)
    pypdf = chunk_document(PdfParser().parse(HOMESECURE), **kwargs)

    def clause_pages(chunks):
        return {chunk.clause_id: chunk.page for chunk in chunks if chunk.clause_id}

    assert clause_pages(docling) == clause_pages(pypdf)


def test_list_markers_do_not_leak_into_chunk_text() -> None:
    """Evidence.content is quoted verbatim into a cited answer; exporter syntax is not."""
    document = _document("## SECTION 4 - WATER DAMAGE\n\n- 4.2 Sudden and accidental escape.\n")

    chunk = next(c for c in _chunks(document) if c.clause_id == "4.2")

    assert chunk.text.lstrip().startswith("4.2 Sudden")
