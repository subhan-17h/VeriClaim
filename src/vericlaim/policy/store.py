"""Persistent Chroma storage for chunks and caller-supplied embeddings.

The store holds both retrieval sources. Which one a query sees is decided by the
``source_type`` metadata filter, not by which collection was opened -- see
:mod:`vericlaim.policy.models`.

Everything here keys documents on ``doc_id``, the corpus-relative path. The basename
is stored for display and never used to find, count, or delete anything.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chromadb

from vericlaim.config import get_settings
from vericlaim.policy.models import Chunk, RetrievedChunk

__all__ = ("ChunkStore", "build_where")

_COSINE_METADATA = {"hnsw:space": "cosine"}

# Metadata fields that round-trip a Chunk. Kept in one place because a field added to
# the model but not written here is silently lost on retrieval.
_METADATA_FIELDS = (
    "doc_id",
    "doc_name",
    "source_type",
    "section",
    "clause_id",
    "page",
    "content_hash",
    "ocr_confidence",
    "ocr_engine",
    "escalated",
)


def build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a Chroma ``where`` clause for a flat field/value mapping.

    Chroma requires an explicit ``$and`` once more than one field is constrained;
    passing a two-key mapping directly is an error rather than an implicit conjunction.
    A list value becomes ``$in``, which is what lets a caller scope to several claims.
    """
    if not filters:
        return None

    clauses: list[dict[str, Any]] = []
    for field, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                raise ValueError(f"Filter {field!r} has no values; that matches nothing")
            clauses.append({field: {"$in": values}})
        else:
            clauses.append({field: value})

    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _to_metadata(chunk: Chunk) -> dict[str, Any]:
    """Return Chroma metadata for a chunk, dropping values Chroma rejects.

    Chroma stores no nulls, so an unset field is absent rather than None. Retrieval
    therefore reads optional fields with ``.get`` and required ones by subscript, so a
    genuinely missing required field raises instead of silently becoming None.
    """
    values = {field: getattr(chunk, field) for field in _METADATA_FIELDS}
    return {field: value for field, value in values.items() if value is not None}


def _to_chunk(chunk_id: str, text: str, metadata: dict[str, Any]) -> Chunk:
    """Rebuild a Chunk from stored metadata."""
    return Chunk(
        id=chunk_id,
        text=text,
        doc_id=metadata["doc_id"],
        doc_name=metadata["doc_name"],
        source_type=metadata["source_type"],
        section=metadata.get("section"),
        clause_id=metadata.get("clause_id"),
        page=metadata.get("page"),
        content_hash=metadata["content_hash"],
        ocr_confidence=metadata.get("ocr_confidence"),
        ocr_engine=metadata.get("ocr_engine"),
        escalated=bool(metadata.get("escalated", False)),
    )


class ChunkStore:
    """Persist chunks and perform dense retrieval with zero-based result ranks."""

    def __init__(
        self,
        path: str | Path | None = None,
        collection_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self._collection_name = collection_name or settings.collection_name
        self._client = chromadb.PersistentClient(path=str(path or settings.chroma_dir))
        self._collection = self._create_collection()

    def _create_collection(self) -> Any:
        return self._client.get_or_create_collection(
            name=self._collection_name,
            metadata=_COSINE_METADATA,
        )

    # ------------------------------------------------------------------- writing

    def add_chunks(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Add chunks with explicit vectors.

        Vectors are supplied rather than computed here so the store never depends on
        an embedding backend, which is what keeps it testable offline.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunk and embedding counts differ: {len(chunks)} != {len(embeddings)}"
            )
        if not chunks:
            return

        self._collection.add(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[_to_metadata(chunk) for chunk in chunks],
            embeddings=[list(embedding) for embedding in embeddings],
        )

    def delete_document(self, doc_id: str) -> None:
        """Delete every chunk belonging to one document, identified by path."""
        self._collection.delete(where={"doc_id": doc_id})

    def reset(self) -> None:
        """Delete every stored chunk by replacing the collection with a fresh one."""
        self._client.delete_collection(name=self._collection_name)
        self._collection = self._create_collection()

    # ------------------------------------------------------------------ searching

    def search(
        self,
        query_embedding: Sequence[float],
        k: int,
        *,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the nearest chunks with cosine similarity scores and zero-based ranks.

        ``filters`` scopes the search by metadata -- ``source_type`` to keep a policy
        question away from scanned paperwork, ``doc_id`` or a claim field to scope to
        one matter. Without it, the two sources sharing a collection would leak into
        each other's results.
        """
        if k <= 0:
            raise ValueError("k must be greater than zero")
        if self.count() == 0:
            return []

        result = self._collection.query(
            query_embeddings=[list(query_embedding)],
            n_results=k,
            where=build_where(filters),
            include=["documents", "metadatas", "distances"],
        )
        rows = zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            strict=True,
        )
        return [
            RetrievedChunk(
                chunk=_to_chunk(chunk_id, text, metadata),
                score=1.0 - distance,
                rank=rank,
            )
            for rank, (chunk_id, text, metadata, distance) in enumerate(rows)
        ]

    # ------------------------------------------------------------------- reading

    def all_chunks(self, *, filters: dict[str, Any] | None = None) -> list[Chunk]:
        """Return every stored chunk in deterministic chunk-ID order."""
        result = self._collection.get(
            where=build_where(filters),
            include=["documents", "metadatas"],
        )
        rows = sorted(
            zip(result["ids"], result["documents"] or [], result["metadatas"] or [], strict=True),
            key=lambda row: row[0],
        )
        return [_to_chunk(chunk_id, text, metadata) for chunk_id, text, metadata in rows]

    def chunks_with_embeddings(
        self,
        chunk_ids: Sequence[str],
    ) -> dict[str, tuple[Chunk, list[float]]]:
        """Return requested stored chunks and their embeddings, keyed by chunk ID."""
        if not chunk_ids:
            return {}

        result = self._collection.get(
            ids=list(chunk_ids),
            include=["documents", "metadatas", "embeddings"],
        )
        embeddings = result["embeddings"]
        if embeddings is None:
            embeddings = []

        return {
            chunk_id: (
                _to_chunk(chunk_id, text, metadata),
                [float(value) for value in embedding],
            )
            for chunk_id, text, metadata, embedding in zip(
                result["ids"],
                result["documents"] or [],
                result["metadatas"] or [],
                embeddings,
                strict=True,
            )
        }

    def chunks_for_document(
        self,
        doc_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[Chunk], int]:
        """Return one numerically ordered page of a document's chunks, and its total."""
        ids = self._collection.get(where={"doc_id": doc_id}, include=[])["ids"]

        def chunk_order(chunk_id: str) -> tuple[int, int, str]:
            _, separator, suffix = chunk_id.rpartition(":")
            if separator:
                try:
                    return (0, int(suffix), chunk_id)
                except ValueError:
                    pass
            # Malformed ids stay browseable, after every well-formed chunk.
            return (1, 0, chunk_id)

        ordered_ids = sorted(ids, key=chunk_order)
        page_ids = ordered_ids[offset : offset + limit]
        if not page_ids:
            return [], len(ordered_ids)

        page = self._collection.get(ids=page_ids, include=["documents", "metadatas"])
        rows_by_id = {
            chunk_id: (text, metadata)
            for chunk_id, text, metadata in zip(
                page["ids"], page["documents"] or [], page["metadatas"] or [], strict=True
            )
        }
        chunks = [_to_chunk(chunk_id, *rows_by_id[chunk_id]) for chunk_id in page_ids]
        return chunks, len(ordered_ids)

    # ------------------------------------------------------------------ counting

    def count(self) -> int:
        """Return the number of chunks in the collection."""
        return self._collection.count()

    def document_chunk_counts(self) -> dict[str, int]:
        """Return exact chunk counts per document, keyed by corpus-relative path."""
        metadatas = self._collection.get(include=["metadatas"])["metadatas"] or []
        counts = Counter(metadata["doc_id"] for metadata in metadatas)
        return dict(sorted(counts.items()))

    def document_ids(self) -> list[str]:
        """Return every document currently represented in the collection."""
        return sorted(self.document_chunk_counts())
