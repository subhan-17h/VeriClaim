"""Index scanned claim files by running OCR through the shared indexing loop.

The loop itself belongs to :mod:`vericlaim.policy.indexer` and is not repeated here.
Change detection, delete-before-add, the zero-chunk guard, removal of deleted
documents, and the manifest/collection consistency check are the rules that keep an
index honest, and they are the same rules whether the text came from a PDF's text
layer or from reading its pixels. What differs -- and all that is supplied here -- is
how a path becomes chunks:

    OCR the pages, keeping per-page confidence
    → re-read pages below the floor through the vision tier
    → chunk with the scanned grammar, which asserts no clauses
    → tag every chunk with the claim it belongs to

Change detection is what makes this affordable. OCR costs seconds per page, so a
scanned corpus that re-read every unchanged document on every start would be unusable;
the manifest means an unchanged scan is never opened.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from vericlaim.config import Settings, get_settings
from vericlaim.policy.embeddings import Embedder
from vericlaim.policy.indexer import (
    IndexResult,
    ProcessedDocument,
    ProgressCallback,
    index_corpus,
)
from vericlaim.policy.store import ChunkStore
from vericlaim.scanned.chunking import chunk_scanned_document
from vericlaim.scanned.docling_ocr import OcrResult, ScannedPdfParser
from vericlaim.scanned.escalation import escalate_low_confidence_pages

__all__ = ("ScannedProcessor", "extract_claim_id", "index_scanned_corpus")

_PDF_EXTENSION = ".pdf"


class OcrParser(Protocol):
    """The narrow surface the processor needs, so tests need no OCR weights."""

    @property
    def engine(self) -> str: ...

    def parse_with_confidence(self, path: Path) -> OcrResult: ...


def extract_claim_id(doc_id: str, *, pattern: str | None = None) -> str | None:
    """Return the claim reference named by a document's corpus-relative path.

    The path, never the recognised text. A scanned inspection report states its own
    claim reference in its heading, but that heading is an OCR result: a single
    misread character would file the document under a different matter, and evidence
    attached to the wrong claim is worse than evidence nobody found. The path is
    assigned by whoever filed the document.

    Matches a directory (``claims/CLM-1001/estimate.pdf``) or a filename
    (``CLM-1088_INSPECTION.pdf``). A path naming no claim yields ``None``: such a
    document is still indexed and still searchable, it simply cannot be scoped to one
    matter, which is the honest consequence of not knowing.
    """
    expression = pattern if pattern is not None else get_settings().claim_id_pattern
    match = re.search(expression, doc_id)
    return match.group(0) if match else None


class ScannedProcessor:
    """Turns one scanned PDF into chunks: OCR, escalate, chunk, tag.

    Dependencies are injected rather than constructed on use, so a test can drive the
    whole path with a scripted parser and gateway, and so a caller can share one
    Docling converter across a corpus instead of rebuilding it per document.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        parser: OcrParser | None = None,
        gateway: Any = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._parser = parser if parser is not None else ScannedPdfParser(
            settings=self._settings
        )
        self._gateway = gateway

    def __call__(
        self,
        path: Path,
        doc_id: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
    ) -> ProcessedDocument:
        if path.suffix.lower() != _PDF_EXTENSION:
            raise ValueError(
                f"{path.name} is not a PDF and cannot be read by the scanned pipeline. "
                "Skipping it would record a document as indexed that was never read."
            )

        outcome = escalate_low_confidence_pages(
            self._parser.parse_with_confidence(path),
            settings=self._settings,
            gateway=self._gateway,
        )
        document = outcome.result.document
        return ProcessedDocument(
            page_count=document.page_count,
            chunks=chunk_scanned_document(
                document,
                doc_id=doc_id,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                ocr_engine=outcome.result.engine,
                claim_id=extract_claim_id(
                    doc_id, pattern=self._settings.claim_id_pattern
                ),
                escalated_pages=outcome.escalated_pages,
            ),
        )


def index_scanned_corpus(
    docs_dir: Path,
    store: ChunkStore,
    embedder: Embedder,
    *,
    manifest_path: Path,
    settings: Settings | None = None,
    parser: OcrParser | None = None,
    gateway: Any = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IndexResult:
    """Index every scanned PDF under ``docs_dir`` into the shared collection.

    The collection is shared with the policy source and separated by ``source_type``;
    the manifest is not, because the two corpora are indexed independently and one
    manifest describing both would make either run look inconsistent to the other.
    """
    resolved = settings if settings is not None else get_settings()
    return index_corpus(
        docs_dir,
        store,
        embedder,
        manifest_path=manifest_path,
        chunk_size=resolved.chunk_size,
        chunk_overlap=resolved.chunk_overlap,
        processor=ScannedProcessor(
            settings=resolved, parser=parser, gateway=gateway
        ),
        force=force,
        on_progress=on_progress,
    )
