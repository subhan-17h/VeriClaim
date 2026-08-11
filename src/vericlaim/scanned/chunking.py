"""Chunk OCR output, which is structurally unlike a policy wording.

The plan for this phase predicted that OCR text would carry no headings at all. It
does: Docling's layout model runs over the recognised page and still emits Markdown
headings, so an inspection report arrives with ``## FINDINGS`` and ``## CONCLUSION``
intact. Those are worth keeping -- they are the only structure such a document has.

A separate path is still needed, for a sharper reason than the one predicted: the
**policy clause grammar is actively dangerous on OCR text**. It reads a line opening
``<digits>.<digits> <Capitalised>`` as a numbered clause, and a scanned invoice or
estimate is full of lines that match -- ``184.000 Estimated cost of repair`` becomes
clause "184.000". A fabricated clause identifier on a scanned document is worse than
no identifier, because a citation naming a clause asserts that the clause exists.

So this path keeps the headings, drops the clause grammar, and anchors every
breadcrumb with a page number. The page anchor matters more here than for a wording:
an inspection report has no clause numbers, so page is the only structural coordinate
a reader can use to find the text again.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from vericlaim.policy.chunking import CHARS_PER_TOKEN, page_confidence_lookup, split_text
from vericlaim.policy.models import Chunk, Document, content_hash

__all__ = ("UNREADABLE_PAGE_TEXT", "chunk_scanned_document")

# Emitted for a page OCR attempted and could not read.
#
# Such a page would otherwise produce no chunks, which has two bad consequences: the
# indexer's zero-chunk guard would abort the whole corpus because one page is smeared,
# and the page would vanish -- indistinguishable from a page that was never in the
# document. Recording it explicitly, at zero confidence, keeps "we could not read
# this" available as evidence in its own right, which is the honest answer and the
# opposite of inventing what the page might have said.
#
# The wording is deliberately not prose an inspector could have written, so it cannot
# be mistaken for recovered content.
UNREADABLE_PAGE_TEXT = (
    "[UNREADABLE PAGE] Optical character recognition recovered no legible text "
    "from this page. Its contents are unknown and no claim can be based on it."
)

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_MARKER = re.compile(r"^[-*o•]\s+")
_IMAGE_PLACEHOLDER = re.compile(r"^<!--\s*image\s*-->$", re.IGNORECASE)

_HEADING_LIMIT = 80


@dataclass(frozen=True, slots=True)
class _Block:
    """A page-local run of text under at most one recovered heading."""

    text: str
    heading: str | None
    page: int | None


def _clean(line: str) -> str:
    """Strip exporter syntax from a line destined for chunk text.

    Evidence.content is quoted verbatim into a cited answer, so a stray ``##`` or
    ``-`` in front of an inspector's finding is Markdown leaking into something a
    reader sees.
    """
    stripped = line.strip()
    if heading := _MARKDOWN_HEADING.fullmatch(stripped):
        return heading.group(2).strip()
    return _LIST_MARKER.sub("", stripped)


def _is_noise(line: str) -> bool:
    """Return whether a line carries no recoverable content.

    Docling emits an image placeholder for a region it could not read. Indexing that
    would create a chunk whose entire content is a comment -- retrievable, citable,
    and meaningless.
    """
    return not line.strip() or bool(_IMAGE_PLACEHOLDER.fullmatch(line.strip()))


def _page_blocks(page_text: str, page: int | None) -> list[_Block]:
    """Split one page into blocks at its recovered headings."""
    blocks: list[_Block] = []
    heading: str | None = None
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            blocks.append(_Block(text=body, heading=heading, page=page))

    for raw_line in page_text.splitlines():
        if _is_noise(raw_line):
            continue
        if match := _MARKDOWN_HEADING.fullmatch(raw_line.strip()):
            flush()
            heading = match.group(2).strip()[:_HEADING_LIMIT]
            lines = [_clean(raw_line)]
            continue
        lines.append(_clean(raw_line))

    flush()
    return blocks


def _section(doc_name: str, block: _Block) -> str:
    """Build the breadcrumb, always anchored by page.

    An inspection report has no clause numbers, so the page is the only coordinate a
    reader can use to find this text in the original document.
    """
    parts = [doc_name]
    if block.page is not None:
        parts.append(f"p.{block.page}")
    if block.heading:
        parts.append(block.heading)
    return " > ".join(parts)


def chunk_scanned_document(
    document: Document,
    *,
    doc_id: str,
    chunk_size: int,
    chunk_overlap: int,
    ocr_engine: str | None = None,
    claim_id: str | None = None,
    escalated_pages: Collection[int] = (),
) -> list[Chunk]:
    """Split an OCR-parsed document into chunks carrying page and OCR provenance.

    ``clause_id`` is always ``None``. A scanned inspection report has no clauses, and
    inferring one from OCR text would invent a coordinate that does not exist in the
    source document.

    ``escalated_pages`` are the 1-based pages re-read through the vision tier. The
    flag travels to the citation, so it is recorded per page rather than per document:
    a document with one escalated page is not a vision-assisted document.
    """
    character_limit = chunk_size * CHARS_PER_TOKEN
    confidence_for = page_confidence_lookup(document)
    pages = document.pages if document.pages is not None else [document.text]
    page_aware = document.pages is not None
    escalated = frozenset(escalated_pages)

    chunks: list[Chunk] = []
    for page_number, page_text in enumerate(pages, start=1):
        page = page_number if page_aware else None
        confidence = confidence_for(page)
        was_escalated = page_number in escalated

        blocks = _page_blocks(page_text, page)
        if not blocks:
            chunks.append(
                _chunk(
                    chunks,
                    doc_id=doc_id,
                    doc_name=document.name,
                    text=UNREADABLE_PAGE_TEXT,
                    section=_section(document.name, _Block("", None, page)),
                    page=page,
                    confidence=0.0,
                    ocr_engine=ocr_engine,
                    claim_id=claim_id,
                    escalated=was_escalated,
                )
            )
            continue

        for block in blocks:
            texts = (
                [block.text]
                if len(block.text) <= character_limit
                else split_text(block.text, chunk_size, chunk_overlap)
            )
            section = _section(document.name, block)
            for text in texts:
                chunks.append(
                    _chunk(
                        chunks,
                        doc_id=doc_id,
                        doc_name=document.name,
                        text=text,
                        section=section,
                        page=page,
                        confidence=confidence,
                        ocr_engine=ocr_engine,
                        claim_id=claim_id,
                        escalated=was_escalated,
                    )
                )

    return chunks


def _chunk(
    existing: list[Chunk],
    *,
    doc_id: str,
    doc_name: str,
    text: str,
    section: str,
    page: int | None,
    confidence: float | None,
    ocr_engine: str | None,
    claim_id: str | None,
    escalated: bool,
) -> Chunk:
    """Build one scanned chunk, numbered by its position in the document."""
    return Chunk(
        id=f"{doc_id}:{len(existing)}",
        text=text,
        doc_id=doc_id,
        doc_name=doc_name,
        source_type="scanned_pdf",
        section=section,
        clause_id=None,
        page=page,
        content_hash=content_hash(text),
        claim_id=claim_id,
        ocr_confidence=confidence,
        ocr_engine=ocr_engine if confidence is not None else None,
        escalated=escalated,
    )
