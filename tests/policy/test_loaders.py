"""Parser registry, plain-text loading, and PDF page/furniture handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.policy.loaders import (
    DocumentParser,
    PdfParser,
    TextParser,
    get_parser,
    iter_document_paths,
    iter_documents,
    supported_extensions,
)
from vericlaim.policy.loaders.pdf import _find_repeated_lines, _line_signature, _render_table
from vericlaim.policy.models import Document

FIXTURES = Path(__file__).parents[1] / "fixtures" / "policies"
HOMESECURE = FIXTURES / "HomeSecure_Plus_2026.pdf"


# --------------------------------------------------------------------- registry


def test_registry_dispatches_on_extension() -> None:
    assert isinstance(get_parser(Path("a/b/wording.pdf")), PdfParser)
    assert isinstance(get_parser(Path("a/b/notes.txt")), TextParser)
    assert isinstance(get_parser(Path("a/b/notes.md")), TextParser)


def test_registry_is_case_insensitive() -> None:
    assert isinstance(get_parser(Path("WORDING.PDF")), PdfParser)


def test_unsupported_extension_has_no_parser() -> None:
    assert get_parser(Path("claims.xlsx")) is None
    assert get_parser(Path("archive.zip")) is None


def test_registered_parsers_satisfy_the_protocol() -> None:
    for extension in supported_extensions():
        parser = get_parser(Path(f"doc{extension}"))
        assert isinstance(parser, DocumentParser)


# ------------------------------------------------------------------- traversal


def test_iter_document_paths_recurses_and_is_sorted(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    for relative in ("z.txt", "a/second.txt", "b/first.txt"):
        (tmp_path / relative).write_text("body", encoding="utf-8")
    (tmp_path / "ignored.xlsx").write_text("nope", encoding="utf-8")

    found = [path.relative_to(tmp_path).as_posix() for path in iter_document_paths(tmp_path)]

    assert found == ["a/second.txt", "b/first.txt", "z.txt"]


def test_iter_document_paths_is_deterministic_across_calls(tmp_path: Path) -> None:
    for name in ("m.txt", "c.txt", "x.txt", "a.txt"):
        (tmp_path / name).write_text("body", encoding="utf-8")

    first = list(iter_document_paths(tmp_path))
    second = list(iter_document_paths(tmp_path))

    assert first == second


def test_iter_documents_parses_each_supported_path(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("first", encoding="utf-8")
    (tmp_path / "two.txt").write_text("second", encoding="utf-8")

    documents = list(iter_documents(tmp_path))

    assert [document.name for document in documents] == ["one.txt", "two.txt"]
    assert [document.text for document in documents] == ["first", "second"]


# ------------------------------------------------------------------------ text


def test_text_parser_reads_utf8(tmp_path: Path) -> None:
    path = tmp_path / "wording.txt"
    path.write_text("Escape of water — PKR 25,000", encoding="utf-8")

    document = TextParser().parse(path)

    assert document.text == "Escape of water — PKR 25,000"
    assert document.name == "wording.txt"
    assert document.pages is None


def test_text_parser_survives_invalid_bytes(tmp_path: Path) -> None:
    """One bad byte must not abort ingestion of an entire corpus."""
    path = tmp_path / "wording.txt"
    path.write_bytes(b"clause 4.2 \xff\xfe covered")

    document = TextParser().parse(path)

    assert "clause 4.2" in document.text
    assert "covered" in document.text


# ------------------------------------------------------------------------- pdf


@pytest.fixture(scope="module")
def homesecure() -> Document:
    return PdfParser().parse(HOMESECURE)


def test_pdf_parser_preserves_page_boundaries(homesecure: Document) -> None:
    assert homesecure.page_count == 5
    assert homesecure.pages is not None
    assert len(homesecure.pages) == 5


def test_pdf_parser_places_clauses_on_their_own_page(homesecure: Document) -> None:
    """Page provenance is the whole point: a citation to p.3 must be checkable."""
    assert homesecure.pages is not None
    assert "4.2 Sudden and accidental escape of water" in homesecure.pages[2]
    assert "PKR 25,000" in homesecure.pages[2]
    # The exclusion lives on a different page from the grant of cover.
    assert "gradual leakage" in homesecure.pages[3]
    assert "gradual leakage" not in homesecure.pages[2]


def test_pdf_parser_strips_running_header_and_page_stamp(homesecure: Document) -> None:
    """Furniture repeated on every page would otherwise outrank real clauses in BM25."""
    assert "Policy Wording NS-HSP-2026" not in homesecure.text
    assert "NorthStar Insurance Limited" not in homesecure.text
    for page_number in range(1, 6):
        assert f"Page {page_number} of 5" not in homesecure.text


def test_pdf_document_text_is_the_joined_pages(homesecure: Document) -> None:
    assert homesecure.pages is not None
    assert homesecure.text == "\n\n".join(homesecure.pages)


def test_pdf_parser_records_the_source_path(homesecure: Document) -> None:
    assert homesecure.path == HOMESECURE
    assert homesecure.name == "HomeSecure_Plus_2026.pdf"


def test_pdf_parser_leaves_ocr_fields_unset(homesecure: Document) -> None:
    """Digital parsing reports no OCR confidence; C-4 is the only producer."""
    assert homesecure.page_confidences is None


# ------------------------------------------------- furniture detection, unit level


def test_line_signature_collapses_digit_runs() -> None:
    """A page stamp is furniture precisely because only its number varies."""
    assert _line_signature("Page 3 of 12") == _line_signature("Page 11 of 12")
    assert _line_signature("Section 4") != _line_signature("Coverage A")


def test_line_signature_normalises_whitespace() -> None:
    assert _line_signature("  Escape   of  water ") == _line_signature("Escape of water")


def test_repeated_lines_need_three_pages_to_be_detectable() -> None:
    """Two pages cannot distinguish furniture from a coincidence."""
    assert _find_repeated_lines(["HEADER\nbody one", "HEADER\nbody two"]) == set()


def test_repeated_lines_detects_a_fixed_header() -> None:
    pages = [f"NORTHSTAR WORDING\nclause {index}" for index in range(4)]

    assert _line_signature("NORTHSTAR WORDING") in _find_repeated_lines(pages)


def test_repeated_lines_detects_a_varying_page_stamp() -> None:
    pages = [f"clause {index}\nPage {index} of 4" for index in range(1, 5)]

    assert _line_signature("Page 1 of 4") in _find_repeated_lines(pages)


def test_repeated_lines_keeps_body_text() -> None:
    pages = [
        "HEADER\n4.2 sudden and accidental escape of water",
        "HEADER\n5.1 gradual leakage is excluded",
        "HEADER\n6.1 notification within thirty days",
    ]

    repeated = _find_repeated_lines(pages)

    assert _line_signature("4.2 sudden and accidental escape of water") not in repeated
    assert _line_signature("5.1 gradual leakage is excluded") not in repeated


# ------------------------------------------------------ table rendering, unit level


def test_render_table_emits_pipe_delimited_rows() -> None:
    rows = [["Peril", "Deductible"], ["Escape of water", "PKR 25,000"]]

    assert _render_table(rows) == "Peril | Deductible\nEscape of water | PKR 25,000"


def test_render_table_drops_entirely_empty_columns() -> None:
    rows = [["Peril", None, "Deductible"], ["Fire", None, "PKR 10,000"]]

    assert _render_table(rows) == "Peril | Deductible\nFire | PKR 10,000"


def test_render_table_rejects_a_single_row() -> None:
    """A one-row 'table' is nearly always a layout artefact, not data."""
    assert _render_table([["Peril", "Deductible"]]) == ""


def test_render_table_tolerates_ragged_rows() -> None:
    """A short row is padded to the table width rather than skewing the columns."""
    rows = [["Peril", "Limit", "Deductible"], ["Fire", "PKR 1m"]]

    assert _render_table(rows) == "Peril | Limit | Deductible\nFire | PKR 1m | "
