"""Incremental indexing: parse, chunk, embed, store, and record what was done.

The loop is driven by the manifest. A document whose bytes are unchanged is skipped
without being parsed, which is what makes re-indexing a corpus cheap enough to do on
every start.

Two guards exist because the manifest and the collection are separate pieces of state
that can disagree -- a crash between writing chunks and saving the manifest leaves
them inconsistent, and an inconsistent index answers questions with a corpus nobody
can describe. Both are checked before any work, and either triggers a rebuild.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vericlaim.policy.chunking import chunk_document
from vericlaim.policy.embeddings import Embedder
from vericlaim.policy.loaders import get_parser, iter_document_paths
from vericlaim.policy.manifest import (
    ManifestRecord,
    file_content_hash,
    load_manifest,
    save_manifest,
)
from vericlaim.policy.models import RetrievalSourceType
from vericlaim.policy.store import ChunkStore

__all__ = ("IndexResult", "ZeroChunkError", "index_corpus")

ProgressCallback = Callable[[str], None]


class ZeroChunkError(RuntimeError):
    """Raised when a document with pages produces no chunks.

    This is the reference implementation's silent failure made loud. It records
    ``chunk_count: 0``, treats the document as successfully indexed, and never
    revisits it -- so a corpus can report itself fully indexed while a document in it
    is unsearchable. For a scanned claim file that is the difference between "no
    evidence exists" and "we failed to read it", and those must never look alike.
    """


@dataclass(frozen=True, slots=True)
class IndexResult:
    """What one indexing run did."""

    documents_indexed: int
    chunks_created: int
    added: int
    updated: int
    skipped: int
    removed: int

    @property
    def changed(self) -> bool:
        """Whether the collection's contents differ from before the run."""
        return bool(self.added or self.updated or self.removed)


def index_corpus(
    docs_dir: Path,
    store: ChunkStore,
    embedder: Embedder,
    *,
    manifest_path: Path,
    chunk_size: int,
    chunk_overlap: int,
    source_type: RetrievalSourceType = "policy",
    pdf_parser: str | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> IndexResult:
    """Index every supported document under ``docs_dir`` whose bytes changed."""
    source_dir = docs_dir.resolve()
    report = on_progress if on_progress is not None else lambda _message: None

    paths_by_id = {
        path.relative_to(source_dir).as_posix(): path
        for path in iter_document_paths(source_dir)
    }

    manifest = load_manifest(manifest_path)
    if force or _is_inconsistent(manifest, store):
        report("Rebuilding the index from scratch")
        store.reset()
        manifest = {}

    added = updated = skipped = 0

    for doc_id, path in paths_by_id.items():
        source_hash = file_content_hash(path)
        previous = manifest.get(doc_id)
        if previous is not None and previous["hash"] == source_hash:
            skipped += 1
            report(f"Skipped unchanged document: {doc_id}")
            continue

        parser = get_parser(path, pdf_parser=pdf_parser)
        if parser is None:  # pragma: no cover - iter_document_paths already filtered
            continue

        report(f"Parsing document: {doc_id}")
        document = parser.parse(path)
        chunks = chunk_document(
            document,
            doc_id=doc_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            source_type=source_type,
        )
        if not chunks and document.page_count:
            raise ZeroChunkError(
                f"{doc_id} has {document.page_count} pages but produced no chunks. "
                "The document is in the corpus and would be unsearchable. This is "
                "usually an image-only PDF reaching a parser that does not run OCR."
            )

        report(f"Embedding {len(chunks)} chunk(s) from {doc_id}")
        embeddings = embedder.embed_documents([chunk.embed_text for chunk in chunks])

        # Delete before add so a re-indexed document does not accumulate stale chunks
        # from a previous, longer version of itself.
        store.delete_document(doc_id)
        store.add_chunks(chunks, embeddings)
        manifest[doc_id] = ManifestRecord(
            hash=source_hash,
            page_count=document.page_count,
            chunk_count=len(chunks),
        )
        if previous is None:
            added += 1
        else:
            updated += 1

    removed = 0
    for doc_id in sorted(set(manifest) - set(paths_by_id)):
        store.delete_document(doc_id)
        del manifest[doc_id]
        removed += 1
        report(f"Removed document: {doc_id}")

    save_manifest(manifest_path, manifest)

    return IndexResult(
        documents_indexed=len(manifest),
        chunks_created=store.count(),
        added=added,
        updated=updated,
        skipped=skipped,
        removed=removed,
    )


def _is_inconsistent(manifest: dict[str, ManifestRecord], store: ChunkStore) -> bool:
    """Return whether the manifest and the collection disagree about what is stored.

    Compared on exact per-document chunk counts rather than totals, because two
    documents whose errors cancel out would otherwise pass. Documents recorded with
    zero chunks are excluded: they contribute no rows, so the collection cannot be
    expected to show them.
    """
    expected = {
        doc_id: record["chunk_count"]
        for doc_id, record in manifest.items()
        if record["chunk_count"] > 0
    }
    return expected != store.document_chunk_counts()
