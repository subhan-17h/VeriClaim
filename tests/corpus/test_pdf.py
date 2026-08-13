"""The shared rendering primitives, and the determinism both corpora rest on.

Every generated document reaches disk through this module, and `.gitignore` promises
the whole corpus is reproducible from a seed. Both PDF writers embed wall-clock time
unless told not to -- reportlab stamps `/CreationDate`, `/ModDate` and a time-derived
`/ID`, and Pillow stamps `creationDate`/`modDate` from `time.gmtime()`. A generator
that forgets either flag produces a corpus that differs between runs while looking
correct, so the flags are asserted here rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from vericlaim.corpus.pdf import (
    rasterise,
    render_image_only_pdf,
    render_policy_pdf,
    render_text_pdf,
    wrap_text,
)

BLOCKS = (
    "NORTHSTAR INSURANCE LIMITED",
    "PROPERTY INSPECTION REPORT",
    "Claim Reference: CLM-1001",
    "The inspection recorded moisture behind the utility room wall.",
)


def test_render_text_pdf_is_byte_identical_across_calls() -> None:
    assert render_text_pdf(BLOCKS) == render_text_pdf(BLOCKS)


def test_render_policy_pdf_is_byte_identical_across_calls(tmp_path: Path) -> None:
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    for path in (first, second):
        render_policy_pdf(path, "HomeSecure Plus", "NS-HS-2026", [list(BLOCKS)])

    assert first.read_bytes() == second.read_bytes()


def test_render_image_only_pdf_pins_its_timestamps(tmp_path: Path) -> None:
    """The defect that made the committed fixtures irreproducible until C-8.4.

    Pillow falls back to ``time.gmtime()`` when ``encoderinfo`` names no dates, so an
    unpinned writer stamps the hour it ran into a corpus promised to be reproducible.
    """
    path = tmp_path / "scan.pdf"
    render_image_only_pdf(rasterise(render_text_pdf(BLOCKS), dpi=100), path)
    raw = path.read_bytes()

    assert b"/CreationDate (D:20260101000000Z)" in raw
    assert b"/ModDate (D:20260101000000Z)" in raw


def test_render_image_only_pdf_is_byte_identical_across_calls(tmp_path: Path) -> None:
    """Identical images under an identical name give identical bytes.

    The name is part of that: Pillow writes the file's own stem into ``/Title``, so a
    document is reproducible at the path it is filed under rather than in the abstract.
    Harmless for a corpus whose filenames are derived from claim numbers, and worth
    stating because it is the one input to these bytes that is not an image.
    """
    pages = rasterise(render_text_pdf(BLOCKS), dpi=100)
    first, second = tmp_path / "first" / "scan.pdf", tmp_path / "second" / "scan.pdf"
    for path in (first, second):
        path.parent.mkdir()
        render_image_only_pdf(pages, path)

    assert first.read_bytes() == second.read_bytes()
    assert first.name == second.name


def test_render_image_only_pdf_carries_no_text_layer(tmp_path: Path) -> None:
    """A fixture that kept its text would let every OCR test pass without OCR."""
    path = tmp_path / "scan.pdf"
    render_image_only_pdf(rasterise(render_text_pdf(BLOCKS), dpi=100), path)
    pages = PdfReader(path).pages

    assert pages
    assert all(not (page.extract_text() or "").strip() for page in pages)


def test_rasterise_returns_one_greyscale_bitmap_per_page() -> None:
    pages = rasterise(render_text_pdf(BLOCKS), dpi=100)

    assert len(pages) == 1
    assert all(isinstance(page, Image.Image) and page.mode == "L" for page in pages)


def test_wrap_text_keeps_every_word_within_the_column() -> None:
    text = " ".join(f"word{index}" for index in range(40))
    lines = wrap_text(text, width=20)

    assert " ".join(lines) == text
    assert all(len(line) <= 20 for line in lines)


def test_wrap_text_preserves_a_deliberate_blank_line() -> None:
    assert wrap_text("") == [""]
