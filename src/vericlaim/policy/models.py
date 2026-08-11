"""Data contract for the document-retrieval sources.

Values move ``Document -> Chunk -> RetrievedChunk`` and are converted to
:class:`~vericlaim.evidence.Evidence` at the tool boundary. Nothing downstream of a
tool ever sees these types; they exist so parsing, chunking, storage, and retrieval
can agree on a shape without agreeing on a source.

Two sources share this contract. Policy PDFs (C-3) and scanned PDFs (C-4) differ in
how their text is *obtained*, not in what a retrievable passage is, so they share one
Chroma collection and one BM25 index, separated by :attr:`Chunk.source_type` at query
time. The alternative -- a second parallel storage stack -- would duplicate the
manifest, the index lifecycle, and the reset story for no retrieval benefit.

The OCR fields are declared here, now, and carry ``None`` for every policy chunk until
C-4 populates them. Adding a field to a schema whose rows are already persisted in a
vector store means a migration; carrying an unused ``None`` for a phase costs nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

__all__ = (
    "Chunk",
    "Document",
    "RetrievedChunk",
    "RetrievalSourceType",
    "content_hash",
)

# The subset of vericlaim.evidence.SourceType that is served from the vector store.
# SQL and spreadsheets are queried, not retrieved, so they never produce a Chunk.
#
# These are deliberately the same string values the Evidence contract uses, so a
# chunk's source_type passes straight through to its Evidence with no translation
# table in between -- one fewer place for the two to drift out of agreement.
RetrievalSourceType = Literal["policy", "scanned_pdf"]


def content_hash(text: str) -> str:
    """Return the stable digest used to detect unchanged chunk content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Document(BaseModel):
    """A parsed source document, before it is split into chunks.

    Parsers are deliberately unaware of the corpus root, so they set ``name`` (the
    basename) and leave document *identity* to the indexer, which alone knows what the
    path is relative to. See :class:`Chunk.doc_id`.
    """

    name: str
    path: Path
    text: str
    page_count: int | None = None
    # Index 0 is page 1. Plain text leaves this None; every PDF parser populates it,
    # because page provenance is what makes a policy citation checkable.
    pages: list[str] | None = None
    # Per-page OCR confidence, aligned with ``pages``. None for digitally-parsed
    # documents; C-4 populates it from Docling's ConversionResult.
    page_confidences: list[float] | None = None


class Chunk(BaseModel):
    """A persisted slice of a Document, ready for indexing and retrieval."""

    id: str
    text: str
    # Document identity is the corpus-relative POSIX path, NOT the basename.
    #
    # A per-claim corpus contains claims/CLM-1001/estimate.pdf and
    # claims/CLM-1002/estimate.pdf. Keyed on the basename these collide: their chunk
    # ids overlap and deleting one document deletes the other's chunks. The reference
    # implementation resolved this by refusing to index duplicate filenames at all,
    # which is not an option here.
    doc_id: str
    # The basename, carried for display and citation only. Never an identity key.
    doc_name: str
    source_type: RetrievalSourceType
    # The full structural breadcrumb, e.g.
    # "HomeSecure_Plus_2026.pdf > SECTION 4 WATER DAMAGE > 4.2 Sudden and accidental".
    section: str | None = None
    # The innermost structural identifier alone -- "4.2", "SECTION 4", "COVERAGE A".
    # Carried separately from the breadcrumb because "cite the clause" is then an
    # equality check rather than a substring search, which is what lets C-11 score
    # clause-level citation accuracy deterministically.
    clause_id: str | None = None
    page: int | None = None
    content_hash: str
    # Which matter this document belongs to, for scoping a search to one claim.
    #
    # Read from the document's path, never from its recognised text: a scanned
    # inspection report states its own claim reference, but an OCR error in that line
    # would file the document under a claim it has nothing to do with. The path is
    # assigned by whoever filed the document and is not a recognition result.
    # None for policy wordings, which belong to no single claim.
    claim_id: str | None = None

    # --- OCR provenance: None for every policy chunk, populated by C-4 -----
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ocr_engine: str | None = None
    # True when a low-confidence page was re-extracted through the vision tier.
    escalated: bool = False

    @property
    def embed_text(self) -> str:
        """Return text enriched with its section breadcrumb for embedding.

        The breadcrumb carries the clause number, which is frequently the only place
        a policy chunk states which section it belongs to.
        """
        return f"{self.section}\n\n{self.text}" if self.section is not None else self.text


class RetrievedChunk(BaseModel):
    """A Chunk paired with its retrieval scores and final rank."""

    chunk: Chunk
    # Chroma reports cosine distance; the store converts it with 1.0 - distance.
    score: float
    rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
