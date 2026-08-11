"""PDF parser with page-preserving extraction and running-furniture removal.

The fallback path, used when Docling is unavailable or explicitly disabled. It is
markedly worse at tables and layout than Docling, but it has no model weights and no
cold start, which makes it the parser that always works.

Header/footer stripping matters more for policy documents than it looks. Insurance
wordings repeat the product name and a page stamp on every page; left in, that text
is chunked, embedded, and indexed dozens of times, and BM25 in particular then ranks
the furniture above the clause that actually answers the question.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from vericlaim.policy.models import Document

_BOUNDARY_LINE_COUNT = 5
_DIGIT_RUN = re.compile(r"\d+")
_MIN_PAGE_STAMP_PAGES = 3


def _normalise_text(text: str) -> str:
    """Return stable retrieval text while preserving paragraph boundaries."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _line_signature(line: str) -> str:
    """Return a stable key for matching running lines across pages.

    Digit runs collapse to ``#`` so that ``Page 3 of 12`` and ``Page 4 of 12`` share
    one signature -- a page stamp is furniture precisely because its only variation
    is the number.
    """
    return _DIGIT_RUN.sub("#", " ".join(line.split()))


def _render_table(rows: Sequence[Sequence[str | None]]) -> str:
    """Render a useful extracted table as compact pipe-delimited rows."""
    width = max((len(row) for row in rows), default=0)
    cleaned = [
        [(row[index] or "").strip() if index < len(row) else "" for index in range(width)]
        for row in rows
    ]
    kept_columns = [index for index in range(width) if any(row[index] for row in cleaned)]
    non_empty_rows = [row for row in cleaned if any(row[index] for index in kept_columns)]
    if len(non_empty_rows) < 2:
        return ""
    return "\n".join(
        " | ".join(row[index] for index in kept_columns) for row in non_empty_rows
    )


def _find_repeated_lines(pages: Sequence[str]) -> set[str]:
    """Return boundary signatures detected as running furniture.

    Two independent detectors, because they catch different things. The first finds
    lines appearing near a page boundary on at least half the pages -- a fixed header.
    The second finds a signature that occupies one consistent slot across several
    pages while its *rendered form differs every time* -- that is a page stamp, whose
    number changes even though its shape does not.
    """
    if len(pages) < 3:
        return set()

    counts: dict[str, int] = {}
    stamp_pages: dict[str, set[int]] = {}
    stamp_slots: dict[str, set[int]] = {}
    stamp_forms: dict[str, set[str]] = {}
    for page_index, page in enumerate(pages):
        lines = [" ".join(line.split()) for line in page.splitlines() if line.strip()]
        signatures = [_line_signature(line) for line in lines]
        boundary = signatures[:_BOUNDARY_LINE_COUNT] + signatures[-_BOUNDARY_LINE_COUNT:]
        for signature in set(boundary):
            counts[signature] = counts.get(signature, 0) + 1

        leading = enumerate(lines[:_BOUNDARY_LINE_COUNT])
        trailing = enumerate(
            lines[-_BOUNDARY_LINE_COUNT:], start=-min(_BOUNDARY_LINE_COUNT, len(lines))
        )
        for slot, line in (*leading, *trailing):
            signature = _line_signature(line)
            stamp_pages.setdefault(signature, set()).add(page_index)
            stamp_slots.setdefault(signature, set()).add(slot)
            stamp_forms.setdefault(signature, set()).add(line)

    repeated = {signature for signature, count in counts.items() if count * 2 >= len(pages)}
    page_stamps = {
        signature
        for signature, signature_pages in stamp_pages.items()
        if len(signature_pages) >= _MIN_PAGE_STAMP_PAGES
        and len(stamp_slots[signature]) == 1
        and len(stamp_forms[signature]) == len(signature_pages)
    }
    return repeated | page_stamps


def _strip_repeated_lines(pages: Sequence[str]) -> list[str]:
    """Remove detected running headers and footers from page text."""
    repeated = _find_repeated_lines(pages)
    return [
        "\n".join(line for line in page.splitlines() if _line_signature(line) not in repeated)
        for page in pages
    ]


class PdfParser:
    """Load PDF text and tables while preserving page boundaries."""

    extensions = (".pdf",)

    def parse(self, path: Path) -> Document:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]

        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages):
                rendered_tables = [
                    rendered
                    for table in page.find_tables()
                    if (rendered := _render_table(table.extract()))
                ]
                if rendered_tables:
                    pages[index] = "\n\n".join(
                        part
                        for part in (pages[index].rstrip(), "\n".join(rendered_tables))
                        if part
                    )

        pages = [_normalise_text(page) for page in _strip_repeated_lines(pages)]
        return Document(
            name=path.name,
            path=path,
            text="\n\n".join(pages),
            pages=pages,
            page_count=len(pages),
        )
