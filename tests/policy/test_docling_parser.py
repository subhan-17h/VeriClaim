"""Docling parsing: converter cache identity, page splitting, and setup failure.

Only the tests marked ``docling`` load model weights. Everything that can be proven
without them is proven without them, because a 90-second conversion is not a unit test.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from vericlaim.policy.loaders import get_parser
from vericlaim.policy.loaders.docling_parser import (
    ConverterOptions,
    DoclingParser,
    DoclingSetupError,
    _split_pages,
    build_converter,
)
from vericlaim.policy.loaders.pdf import PdfParser

FIXTURES = Path(__file__).parents[1] / "fixtures" / "policies"
HOMESECURE = FIXTURES / "HomeSecure_Plus_2026.pdf"

DIGITAL = ConverterOptions(artifacts_path=Path("/models"))


# ------------------------------------------------- the converter cache key is complete


def test_converter_options_are_hashable() -> None:
    """The options object is the cache key, so it must be usable as one."""
    assert hash(DIGITAL) == hash(ConverterOptions(artifacts_path=Path("/models")))


@pytest.mark.parametrize(
    "changed",
    [
        {"artifacts_path": Path("/other")},
        {"do_ocr": True},
        {"do_table_structure": False},
        {"do_cell_matching": False},
        {"ocr_engine": "tesseract"},
        {"ocr_lang": ("urdu",)},
    ],
    ids=["artifacts", "ocr", "tables", "cells", "engine", "lang"],
)
def test_every_option_participates_in_the_cache_key(changed: dict[str, object]) -> None:
    """Any option the cache key ignores silently returns the wrong converter.

    The reference implementation keys only on the artifacts path. Under that key,
    asking for an OCR-enabled converter after a digital one has been built returns the
    digital converter, and every scanned page extracts as empty with no error.
    """
    variant = replace(DIGITAL, **changed)

    assert variant != DIGITAL
    assert hash(variant) != hash(DIGITAL)


def test_ocr_defaults_to_english_never_chinese() -> None:
    """RapidOcrOptions.lang defaults to ["chinese"]; leaving it implicit is a live bug."""
    assert DIGITAL.ocr_lang == ("english",)


# ------------------------------------------------------------------- page splitting


def test_split_pages_returns_one_segment_per_page() -> None:
    markdown = "page one|BREAK|page two|BREAK|page three"

    assert _split_pages(markdown, "|BREAK|", 3) == ["page one", "page two", "page three"]


def test_split_pages_pads_when_a_page_produced_no_output() -> None:
    """An empty page is normal; it must not shift every later page number."""
    assert _split_pages("only page", "|BREAK|", 3) == ["only page", "", ""]


def test_split_pages_rejects_more_segments_than_pages() -> None:
    """A leaked placeholder would misnumber every page after it -- worse than no page."""
    markdown = "a|BREAK|b|BREAK|c"

    with pytest.raises(ValueError, match="3 page segments for a 2-page PDF"):
        _split_pages(markdown, "|BREAK|", 2)


def test_split_pages_handles_a_single_page() -> None:
    assert _split_pages("sole page", "|BREAK|", 1) == ["sole page"]


# ------------------------------------------------------------------ setup failure


def test_missing_artifacts_raise_an_actionable_error(tmp_path: Path) -> None:
    build_converter.cache_clear()
    empty = tmp_path / "no-models"
    empty.mkdir()

    with pytest.raises(DoclingSetupError) as excinfo:
        build_converter(ConverterOptions(artifacts_path=empty))

    message = str(excinfo.value)
    assert "warm_models.py" in message
    assert "VC_PDF_PARSER=pypdf" in message


def test_absent_artifacts_directory_raises(tmp_path: Path) -> None:
    build_converter.cache_clear()

    with pytest.raises(DoclingSetupError, match="missing or empty"):
        build_converter(ConverterOptions(artifacts_path=tmp_path / "never-created"))


def test_unsupported_ocr_engine_is_rejected(tmp_path: Path) -> None:
    from vericlaim.policy.loaders.docling_parser import _ocr_options

    with pytest.raises(DoclingSetupError, match="Unsupported OCR engine"):
        _ocr_options(ConverterOptions(artifacts_path=tmp_path, ocr_engine="paddle"))


# ------------------------------------------------------------- registry dispatch


def test_pdf_parser_choice_is_configuration_not_extension() -> None:
    assert isinstance(get_parser(Path("wording.pdf"), pdf_parser="docling"), DoclingParser)
    assert isinstance(get_parser(Path("wording.pdf"), pdf_parser="pypdf"), PdfParser)


def test_non_pdf_dispatch_ignores_the_pdf_parser_choice() -> None:
    from vericlaim.policy.loaders import TextParser

    assert isinstance(get_parser(Path("notes.txt"), pdf_parser="docling"), TextParser)


# --------------------------------------------------------- live conversion (weights)


@pytest.mark.docling
def test_docling_parses_the_fixture_with_page_provenance() -> None:
    document = DoclingParser().parse(HOMESECURE)

    assert document.page_count == 5
    assert document.pages is not None
    assert "4.2 Sudden and accidental escape of water" in document.pages[2]
    assert "PKR 25,000" in document.pages[2]
    assert "gradual leakage" in document.pages[3]
    assert "gradual leakage" not in document.pages[2]


@pytest.mark.docling
def test_docling_recovers_structural_headings() -> None:
    """Docling's value over text extraction is that clause headings survive as headings."""
    document = DoclingParser().parse(HOMESECURE)

    assert "## SECTION 4" in document.text
    assert "## SECTION 5" in document.text


@pytest.mark.docling
def test_docling_layout_model_drops_running_furniture() -> None:
    document = DoclingParser().parse(HOMESECURE)

    assert "NorthStar Insurance Limited" not in document.text
    assert "Page 3 of 5" not in document.text


@pytest.mark.docling
def test_converter_is_reused_for_one_option_set_and_not_across_two() -> None:
    """The cache must hit on a repeat and miss on a different option set."""
    from vericlaim.config import get_settings

    build_converter.cache_clear()
    digital = ConverterOptions(artifacts_path=get_settings().docling_artifacts_path)

    first = build_converter(digital)
    second = build_converter(digital)
    assert first is second
    assert build_converter.cache_info().hits == 1

    other = ConverterOptions(
        artifacts_path=get_settings().docling_artifacts_path,
        do_table_structure=False,
    )
    assert build_converter(other) is not first
