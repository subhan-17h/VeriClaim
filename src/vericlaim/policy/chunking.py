"""Structure-aware chunking for insurance policy wordings.

Two things happen here, and only the first is generic. ``split_text`` is a pure
recursive splitter with real overlapping text. Everything above it exists to keep a
chunk attached to the clause it came from, because in an insurance wording the clause
number *is* the citation: an answer that says water damage is covered is worth little,
and one that says §4.2 covers it is checkable.

The heading grammar is therefore policy-specific by design. Wordings are written to a
recognisable house style -- SECTION, COVERAGE, EXCLUSIONS, ENDORSEMENT, and decimal
clause numbers -- and that style is what the breadcrumb is built from.

Headings are matched after stripping Markdown list markers, because Docling exports a
numbered clause as a list item (``- 4.2 Sudden and accidental...``) while a text
extractor leaves it bare. The same wording must chunk the same way under either parser.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from vericlaim.policy.models import Chunk, Document, RetrievalSourceType, content_hash

__all__ = ("CHARS_PER_TOKEN", "chunk_document", "split_text")

CHARS_PER_TOKEN = 4
SEPARATORS = ["\n\n", "\n", ". ", " "]

# (depth, label, clause_id). Lower depth is more significant, mirroring Markdown.
Heading = tuple[int, str, str | None]
HeadingParser = Callable[[re.Match[str]], Heading]

_SECTION_DEPTH = 2
_NAMED_PART_DEPTH = 3
_CLAUSE_DEPTH = 4
_UNRECOGNISED_DEPTH = 6

_LABEL_LIMIT = 80

_MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
# Docling exports numbered clauses as list items; a text extractor does not.
_LIST_MARKER_PATTERN = re.compile(r"^[-*o•]\s+")
# A dot-leader line is a table of contents entry, never a real heading.
_DOT_LEADER = "...."

_SECTION_PATTERN = re.compile(
    r"^SECTION\s+(\d+|[IVXLC]+)\b\s*(?:[-–—:.]\s*(.*))?$",
    re.IGNORECASE,
)
_COVERAGE_PATTERN = re.compile(
    r"^COVERAGE\s+([A-Z]|\d+)\b\s*(?:[-–—:.]\s*(.*))?$",
    re.IGNORECASE,
)
_ENDORSEMENT_PATTERN = re.compile(
    r"^ENDORSEMENT\s+(\d+|[A-Z])\b\s*(?:[-–—:.]\s*(.*))?$",
    re.IGNORECASE,
)
# A decimal clause number followed by its text: "4.2 Sudden and accidental ...".
# At least one dot is required so that a bare figure at the start of a sentence --
# "5 percent of the sum insured" -- is not mistaken for a clause.
_CLAUSE_PATTERN = re.compile(r"^(\d+(?:\.\d+)+)\s+(\S.*)$")
# An all-capitals line standing alone: EXCLUSIONS, GENERAL CONDITIONS, DEFINITIONS.
# Requires two words or one long one, so an acronym on its own line is not a heading.
_ALLCAPS_PATTERN = re.compile(r"^([A-Z][A-Z0-9 ,&\-()/']{5,})$")

# A clause number that a layout model has merged into the middle of a line.
#
# Docling's reading-order model groups a lead-in and the clauses beneath it into one
# list item, so page 4 of a wording arrives as a single line reading
# "- The insurer will not indemnify ... in respect of: 5.1 Loss or damage ... 5.2 ...".
# Line-based heading matching cannot see those clauses, and the whole exclusions list
# would then be cited at section level -- accurate, but a clause number is what makes
# a coverage answer checkable.
#
# The boundary is deliberately narrow: a clause number is split out only when it
# follows sentence-ending punctuation or a colon and is followed by a capitalised
# word. That leaves "up to PKR 150,000. A deductible" and "see clause 4.2 above"
# untouched, since neither matches both conditions.
_INLINE_CLAUSE_PATTERN = re.compile(r"(?<=[.:])\s+(?=\d+(?:\.\d+)+\s+[A-Z])")


def _titled(keyword: str, match: re.Match[str], depth: int) -> Heading:
    """Return a heading for a ``KEYWORD n — TITLE`` form, keeping its identifier."""
    identifier = f"{keyword} {match.group(1).upper()}"
    title = (match.group(2) or "").strip()
    label = f"{identifier} {title}".strip() if title else identifier
    return depth, label, identifier


def _section_heading(match: re.Match[str]) -> Heading:
    return _titled("SECTION", match, _SECTION_DEPTH)


def _coverage_heading(match: re.Match[str]) -> Heading:
    return _titled("COVERAGE", match, _NAMED_PART_DEPTH)


def _endorsement_heading(match: re.Match[str]) -> Heading:
    return _titled("ENDORSEMENT", match, _NAMED_PART_DEPTH)


def _clause_heading(match: re.Match[str]) -> Heading:
    """Return a heading for a decimal clause, whose number is its identifier."""
    clause_id = match.group(1)
    return _CLAUSE_DEPTH, f"{clause_id} {match.group(2).strip()}", clause_id


def _allcaps_heading(match: re.Match[str]) -> Heading:
    """Return a heading for a standalone capitalised part title, which has no id."""
    return _NAMED_PART_DEPTH, match.group(1).strip(), None


_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], HeadingParser], ...] = (
    (_SECTION_PATTERN, _section_heading),
    (_COVERAGE_PATTERN, _coverage_heading),
    (_ENDORSEMENT_PATTERN, _endorsement_heading),
    (_CLAUSE_PATTERN, _clause_heading),
    (_ALLCAPS_PATTERN, _allcaps_heading),
)


@dataclass(frozen=True, slots=True)
class _Block:
    """A page-local text block paired with its structural context."""

    text: str
    headings: tuple[Heading, ...]
    clause_id: str | None
    page: int | None


def _match_policy_form(text: str) -> Heading | None:
    """Return the heading for a policy-form line, if it is one."""
    for pattern, parser in _HEADING_PATTERNS:
        if match := pattern.fullmatch(text):
            depth, label, clause_id = parser(match)
            return depth, label[:_LABEL_LIMIT], clause_id
    return None


def _match_heading(line: str) -> Heading | None:
    """Return structural metadata for a heading line, if recognised.

    A Markdown heading keeps its own depth -- the exporter already decided the
    hierarchy -- but its label is still parsed for a clause identifier, so
    ``## SECTION 4 - WATER DAMAGE`` yields the id ``SECTION 4`` rather than nothing.
    """
    if _DOT_LEADER in line:
        return None

    if markdown_match := _MARKDOWN_HEADING_PATTERN.fullmatch(line):
        depth = len(markdown_match.group(1))
        label = markdown_match.group(2).strip()
        parsed = _match_policy_form(label)
        clause_id = parsed[2] if parsed is not None else None
        return depth, label[:_LABEL_LIMIT], clause_id

    stripped = _LIST_MARKER_PATTERN.sub("", line).strip()
    if not stripped:
        return None
    return _match_policy_form(stripped)


def _unmerge_inline_clauses(page_text: str) -> str:
    """Put a merged run of clauses back onto separate lines.

    Applied to page text before heading detection so that the two PDF parsers, whose
    line breaking differs, chunk the same wording into the same clauses.
    """
    return _INLINE_CLAUSE_PATTERN.sub("\n", page_text)


def _nearest_clause(headings: list[Heading]) -> str | None:
    """Return the nearest clause identifier on a heading stack."""
    return next((clause_id for _, _, clause_id in reversed(headings) if clause_id), None)


def _document_blocks(document: Document) -> list[_Block]:
    """Split a document into page-local blocks while tracking its heading stack.

    The stack carries across pages deliberately: a clause that continues onto the next
    page still belongs to its section, and losing that would break its breadcrumb
    exactly where a long exclusions list needs it most.
    """
    pages = document.pages if document.pages is not None else [document.text]
    page_aware = document.pages is not None
    headings: list[Heading] = []
    blocks: list[_Block] = []

    for page_number, page_text in enumerate(pages, start=1):
        lines: list[str] = []
        snapshot = tuple(headings)
        clause_id = _nearest_clause(headings)
        page = page_number if page_aware else None

        for line in _unmerge_inline_clauses(page_text).splitlines(keepends=True):
            heading = _match_heading(line.strip())
            if heading is None:
                lines.append(line)
                continue

            block_text = "".join(lines)
            if block_text.strip():
                blocks.append(_Block(block_text, snapshot, clause_id, page))

            depth, _, _ = heading
            while headings and headings[-1][0] >= depth:
                headings.pop()
            headings.append(heading)
            # The heading line opens the next block, with its Markdown list marker
            # removed. Evidence.content is quoted verbatim into a cited answer, so a
            # stray "- " in front of a policy clause is exporter syntax leaking
            # through the data layer into something a reader sees.
            lines = [_LIST_MARKER_PATTERN.sub("", line)]
            snapshot = tuple(headings)
            clause_id = _nearest_clause(headings)

        block_text = "".join(lines)
        if block_text.strip():
            blocks.append(_Block(block_text, snapshot, clause_id, page))

    return blocks


def _split_on_separator(text: str, separator: str) -> list[str]:
    """Split text without discarding the separator characters."""
    parts = text.split(separator)
    return [part + separator for part in parts[:-1]] + [parts[-1]]


def _recursive_split(text: str, limit: int, separators: list[str]) -> list[str]:
    """Return separator-aware pieces no longer than ``limit`` characters."""
    if len(text) <= limit:
        return [text]
    if not separators:
        return [text[start : start + limit] for start in range(0, len(text), limit)]

    separator, *remaining = separators
    if separator not in text:
        return _recursive_split(text, limit, remaining)

    pieces: list[str] = []
    for piece in _split_on_separator(text, separator):
        if len(piece) <= limit:
            pieces.append(piece)
        else:
            pieces.extend(_recursive_split(piece, limit, remaining))
    return pieces


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text using approximate token sizes and real overlapping text.

    ``chunk_size`` and ``overlap`` are token counts. Tokens are approximated using
    ``CHARS_PER_TOKEN`` so this pure function stays independent of model tokenizers.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must not be negative")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    if not text or not text.strip():
        return []

    limit = chunk_size * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN
    pieces = _recursive_split(text, limit, SEPARATORS)

    boundaries: list[int] = []
    position = 0
    for piece in pieces:
        position += len(piece)
        boundaries.append(position)

    chunks: list[str] = []
    start = 0
    while start < len(text):
        maximum_end = min(start + limit, len(text))
        boundary_index = bisect_right(boundaries, maximum_end) - 1
        end = boundaries[boundary_index] if boundary_index >= 0 else maximum_end

        # An overlap can put the next start inside a recursively split piece. If its
        # next boundary would not advance beyond that overlap, use a bounded hard cut.
        if end <= start + overlap_chars and maximum_end < len(text):
            end = maximum_end

        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = end - overlap_chars

    return chunks


def page_confidence_lookup(document: Document) -> Callable[[int | None], float | None]:
    """Return a lookup from 1-based page number to that page's OCR confidence.

    Bounds-checked rather than indexed directly. The confidence list is aligned
    positionally with the page list, and an off-by-one here would attach one page's
    confidence to another page's text -- reporting a clean page as unreadable, or
    worse, an unreadable one as clean.
    """
    confidences = document.page_confidences

    def lookup(page: int | None) -> float | None:
        if confidences is None or page is None:
            return None
        index = page - 1
        return confidences[index] if 0 <= index < len(confidences) else None

    return lookup


def chunk_document(
    document: Document,
    *,
    doc_id: str,
    chunk_size: int,
    chunk_overlap: int,
    source_type: RetrievalSourceType = "policy",
    ocr_engine: str | None = None,
) -> list[Chunk]:
    """Split a document at structural boundaries and populate Chunk metadata.

    ``doc_id`` is the corpus-relative path and is supplied by the indexer, which is
    the only component that knows the corpus root. It prefixes every chunk id, so two
    documents sharing a basename in different directories never collide.

    When the document carries per-page OCR confidence, every chunk inherits its
    page's score. Confidence has to travel at chunk granularity because that is the
    granularity evidence is cited at: a document-level average would let a clean page
    vouch for an unreadable one in the same file.
    """
    character_limit = chunk_size * CHARS_PER_TOKEN
    confidence_for = page_confidence_lookup(document)
    chunks: list[Chunk] = []

    for block in _document_blocks(document):
        texts = (
            [block.text]
            if len(block.text) <= character_limit
            else split_text(block.text, chunk_size, chunk_overlap)
        )
        section = (
            " > ".join([document.name, *(label for _, label, _ in block.headings)])
            if block.headings
            else None
        )
        confidence = confidence_for(block.page)
        for text in texts:
            chunks.append(
                Chunk(
                    id=f"{doc_id}:{len(chunks)}",
                    text=text,
                    doc_id=doc_id,
                    doc_name=document.name,
                    source_type=source_type,
                    section=section,
                    clause_id=block.clause_id,
                    page=block.page,
                    content_hash=content_hash(text),
                    ocr_confidence=confidence,
                    ocr_engine=ocr_engine if confidence is not None else None,
                )
            )

    return chunks
