"""Hybrid retrieval: dense vectors, BM25, reciprocal rank fusion, and reranking.

Policy questions need both halves. A question about "sudden escape of water" is
answered by dense retrieval understanding the phrasing; a question naming clause 4.2
or a product like HomeSecure Plus is answered by BM25 matching the literal token,
which an embedding blurs away. Reciprocal rank fusion combines them without needing
their scores to be comparable, which they are not.

The sparse index carries the filterable metadata of the chunks it indexed. That is not
an optimisation: dense search filters at the store, so an unfiltered BM25 leg would
fuse scanned pages into a policy question's results, and the tool would then wrap a
scanned chunk in a PolicyLocator and cite it as a policy clause.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import bm25s
import Stemmer
from flashrank import Ranker, RerankRequest

from vericlaim.policy.models import Chunk, RetrievedChunk
from vericlaim.policy.store import ChunkStore

__all__ = (
    "BM25Index",
    "BM25IndexCorruptError",
    "BM25IndexError",
    "BM25IndexNotFoundError",
    "compute_chunk_signature",
    "hybrid_search",
    "rerank",
    "retrieve",
    "rrf_fuse",
)

_FORMAT_VERSION = 1
_METADATA_NAME = "metadata.json"
_TOKEN_PATTERN = r"(?u)\b\w[\w-]*\b"
_STEMMER = Stemmer.Stemmer("english")

# The chunk fields the sparse index can filter on. Deliberately narrow: these are the
# scoping dimensions (which source, which document), not arbitrary metadata.
_FILTERABLE = ("source_type", "doc_id")

_METADATA_KEYS = {
    "chunk_ids",
    "content_hashes",
    "count",
    "filters",
    "format_version",
    "has_index",
    "signature",
}


class BM25IndexError(RuntimeError):
    """Base error raised when a persisted BM25 index cannot be loaded."""


class BM25IndexNotFoundError(BM25IndexError):
    """Raised when no persisted BM25 index exists at the requested path."""


class BM25IndexCorruptError(BM25IndexError):
    """Raised when a persisted BM25 index is incomplete or invalid."""


def _tokenize(texts: str | Sequence[str]) -> list[list[str]]:
    """Tokenize corpus and query text with one shared retrieval configuration.

    Corpus and query must tokenize identically -- a stemmer applied to one and not the
    other silently stops the two from ever matching.
    """
    return bm25s.tokenize(
        texts,
        token_pattern=_TOKEN_PATTERN,
        stopwords="en",
        stemmer=_STEMMER,
        return_ids=False,
        show_progress=False,
    )


def _signature_for_chunks(chunk_keys: Sequence[tuple[str, str]]) -> str:
    payload = json.dumps(
        {"chunks": list(chunk_keys), "count": len(chunk_keys)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_chunk_signature(chunks: Sequence[Chunk]) -> str:
    """Return the persisted-index signature without building a BM25 index.

    Comparing this to a loaded index's signature is what detects an index that has
    fallen behind the collection. Without it, a stale sparse leg silently returns ids
    that no longer exist and hybrid search degrades to dense-only with no signal.
    """
    return _signature_for_chunks([(chunk.id, chunk.content_hash) for chunk in chunks])


def rrf_fuse(rankings: Sequence[Sequence[str]], k: int) -> list[tuple[str, float]]:
    """Fuse ID rankings using reciprocal rank only.

    Rank, not score: BM25 scores are unbounded and cosine similarities are not, so any
    weighted sum of the two would be dominated by whichever happened to be on a larger
    scale. Ties break on the id so results are reproducible across runs.
    """
    if k <= 0:
        raise ValueError("k must be greater than zero")

    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id in seen:
                raise ValueError("a ranking must not contain duplicate IDs")
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda result: (-result[1], result[0]))


def _matches(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    """Return whether one chunk's retained metadata satisfies a filter mapping."""
    if not filters:
        return True
    for field, expected in filters.items():
        if field not in _FILTERABLE:
            raise ValueError(
                f"The sparse index cannot filter on {field!r}. "
                f"Filterable fields: {', '.join(_FILTERABLE)}"
            )
        actual = metadata.get(field)
        if isinstance(expected, (list, tuple, set)):
            if actual not in set(expected):
                return False
        elif actual != expected:
            return False
    return True


class BM25Index:
    """A persisted BM25 index aligned with an ordered sequence of chunk IDs."""

    def __init__(
        self,
        index: bm25s.BM25 | None,
        chunk_ids: Sequence[str],
        content_hashes: Sequence[str],
        filters: Sequence[dict[str, Any]],
        signature: str,
    ) -> None:
        self._index = index
        self._chunk_ids = tuple(chunk_ids)
        self._content_hashes = tuple(content_hashes)
        self._filters = tuple(filters)
        self._signature = signature

    @classmethod
    def build(cls, chunks: Sequence[Chunk]) -> BM25Index:
        """Build an in-memory index over each chunk's embedding text."""
        chunk_list = list(chunks)
        chunk_ids = [chunk.id for chunk in chunk_list]
        content_hashes = [chunk.content_hash for chunk in chunk_list]
        filters = [
            {field: getattr(chunk, field) for field in _FILTERABLE} for chunk in chunk_list
        ]
        signature = compute_chunk_signature(chunk_list)

        tokenized = _tokenize([chunk.embed_text for chunk in chunk_list])
        if not tokenized or not any(tokenized):
            # An empty corpus is legitimate; bm25s cannot index one.
            return cls(None, chunk_ids, content_hashes, filters, signature)

        index = bm25s.BM25()
        index.index(tokenized, show_progress=False)
        return cls(index, chunk_ids, content_hashes, filters, signature)

    def save(self, path: Path) -> None:
        """Persist the BM25 data and its ordered chunk metadata."""
        path.mkdir(parents=True, exist_ok=True)
        if self._index is not None:
            self._index.save(path, show_progress=False)

        metadata = {
            "chunk_ids": list(self._chunk_ids),
            "content_hashes": list(self._content_hashes),
            "count": len(self._chunk_ids),
            "filters": list(self._filters),
            "format_version": _FORMAT_VERSION,
            "has_index": self._index is not None,
            "signature": self._signature,
        }
        (path / _METADATA_NAME).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        """Load a persisted index, or raise a specific missing/corrupt error.

        Validation is exhaustive because the failure it prevents is silent. The stored
        arrays are zipped positionally against BM25 score vectors; a length mismatch
        would attach scores to the wrong chunks and return confidently wrong results.
        """
        if not path.exists():
            raise BM25IndexNotFoundError(f"BM25 index does not exist: {path}")
        if not path.is_dir():
            raise BM25IndexCorruptError(f"BM25 index path is not a directory: {path}")

        try:
            metadata = json.loads((path / _METADATA_NAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BM25IndexCorruptError(f"BM25 index metadata is invalid: {path}") from exc

        if not isinstance(metadata, dict) or set(metadata) != _METADATA_KEYS:
            raise BM25IndexCorruptError(f"BM25 index metadata has an invalid shape: {path}")

        chunk_ids = metadata["chunk_ids"]
        content_hashes = metadata["content_hashes"]
        filters = metadata["filters"]
        count = metadata["count"]
        has_index = metadata["has_index"]
        signature = metadata["signature"]

        if (
            type(metadata["format_version"]) is not int
            or metadata["format_version"] != _FORMAT_VERSION
            or not isinstance(chunk_ids, list)
            or any(not isinstance(chunk_id, str) for chunk_id in chunk_ids)
            or not isinstance(content_hashes, list)
            or any(not isinstance(digest, str) for digest in content_hashes)
            or not isinstance(filters, list)
            or any(not isinstance(entry, dict) for entry in filters)
            or type(count) is not int
            or count != len(chunk_ids)
            or count != len(content_hashes)
            or count != len(filters)
            or type(has_index) is not bool
            or not isinstance(signature, str)
            or signature != _signature_for_chunks(
                list(zip(chunk_ids, content_hashes, strict=True))
            )
        ):
            raise BM25IndexCorruptError(f"BM25 index metadata is inconsistent: {path}")

        if not has_index:
            return cls(None, chunk_ids, content_hashes, filters, signature)

        try:
            index = bm25s.BM25.load(path)
            indexed_count = index.scores["num_docs"]
        except Exception as exc:
            raise BM25IndexCorruptError(f"BM25 index data is invalid: {path}") from exc
        if indexed_count != count:
            raise BM25IndexCorruptError(
                f"BM25 index data contains {indexed_count} documents, expected {count}: {path}"
            )
        return cls(index, chunk_ids, content_hashes, filters, signature)

    def search(
        self,
        query: str,
        k: int,
        *,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Return positive-scoring chunk IDs in deterministic rank order."""
        if k <= 0:
            raise ValueError("k must be greater than zero")
        if self._index is None:
            return []

        query_tokens = _tokenize(query)[0]
        query_token_ids = self._index.get_tokens_ids(query_tokens)
        if not query_token_ids:
            return []

        scores = self._index.get_scores_from_ids(query_token_ids)
        results = [
            (chunk_id, float(score))
            for chunk_id, score, metadata in zip(
                self._chunk_ids, scores, self._filters, strict=True
            )
            if score > 0 and _matches(metadata, filters)
        ]
        results.sort(key=lambda result: (-result[1], result[0]))
        return results[:k]

    @property
    def signature(self) -> str:
        """Return the stable digest of indexed chunk IDs, content hashes, and count."""
        return self._signature

    def __len__(self) -> int:
        return len(self._chunk_ids)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def hybrid_search(
    question: str,
    query_embedding: Sequence[float],
    store: ChunkStore,
    sparse: BM25Index,
    *,
    limit: int,
    top_k_dense: int,
    top_k_bm25: int,
    rrf_k: int,
    filters: dict[str, Any] | None = None,
) -> list[RetrievedChunk]:
    """Fuse dense and sparse rankings while preserving dense cosine scores.

    A chunk found only by BM25 has no dense score, so one is computed against its
    stored embedding rather than left at zero -- a zero would misreport a strong
    keyword match as semantically irrelevant everywhere that score is displayed.
    """
    dense_results = store.search(query_embedding, top_k_dense, filters=filters)
    sparse_results = sparse.search(question, top_k_bm25, filters=filters)
    fused = rrf_fuse(
        [
            [result.chunk.id for result in dense_results],
            [chunk_id for chunk_id, _ in sparse_results],
        ],
        rrf_k,
    )[:limit]
    if not fused:
        return []

    dense_by_id = {result.chunk.id: result for result in dense_results}
    sparse_only_ids = [chunk_id for chunk_id, _ in fused if chunk_id not in dense_by_id]
    stored_by_id = store.chunks_with_embeddings(sparse_only_ids)

    retrieved: list[RetrievedChunk] = []
    for chunk_id, rrf_score in fused:
        dense_result = dense_by_id.get(chunk_id)
        if dense_result is not None:
            chunk, score = dense_result.chunk, dense_result.score
        else:
            stored = stored_by_id.get(chunk_id)
            if stored is None:
                # The sparse index has fallen behind the collection for this id.
                continue
            chunk, embedding = stored
            score = _cosine_similarity(query_embedding, embedding)
        retrieved.append(
            RetrievedChunk(chunk=chunk, score=score, rank=len(retrieved), rrf_score=rrf_score)
        )
    return retrieved


@lru_cache
def _ranker(model: str, cache_dir: Path) -> Ranker:
    """Return one FlashRank model instance per process and cache location."""
    return Ranker(model_name=model, cache_dir=str(cache_dir), log_level="WARNING")


def rerank(
    question: str,
    candidates: Sequence[RetrievedChunk],
    *,
    limit: int,
    model: str,
    cache_dir: Path,
) -> list[RetrievedChunk]:
    """Rerank candidate text while preserving dense scores and chunk identity.

    A cross-encoder reads the question and passage together, which is what lets it
    tell "escape of water is covered" from "escape of water is excluded" -- a
    distinction bi-encoder embeddings of an insurance wording routinely miss, and
    exactly the distinction that decides a coverage answer.
    """
    if not candidates:
        return []

    passages = [
        {"id": index, "text": candidate.chunk.text} for index, candidate in enumerate(candidates)
    ]
    ranked = _ranker(model, cache_dir).rerank(RerankRequest(query=question, passages=passages))
    return [
        candidates[passage["id"]].model_copy(
            update={"rank": rank, "rerank_score": float(passage["score"])}
        )
        for rank, passage in enumerate(ranked[:limit])
    ]


def retrieve(
    question: str,
    query_embedding: Sequence[float],
    store: ChunkStore,
    sparse_index: BM25Index | None,
    *,
    limit: int,
    mode: str,
    rerank_enabled: bool,
    top_k_dense: int,
    top_k_bm25: int,
    rrf_k: int,
    rerank_candidates: int,
    flashrank_model: str,
    flashrank_cache_dir: Path,
    filters: dict[str, Any] | None = None,
) -> list[RetrievedChunk]:
    """Gather dense or hybrid candidates and optionally rerank them."""
    # top_k_dense and rerank_candidates size the candidate pool; limit is what reaches
    # synthesis. Reranking is only worth its latency over a pool wider than the limit.
    candidate_limit = max(rerank_candidates, limit) if rerank_enabled else limit

    if mode == "hybrid":
        if sparse_index is None:
            raise ValueError("hybrid retrieval requires a sparse index")
        candidates = hybrid_search(
            question,
            query_embedding,
            store,
            sparse_index,
            limit=candidate_limit,
            top_k_dense=top_k_dense,
            top_k_bm25=top_k_bm25,
            rrf_k=rrf_k,
            filters=filters,
        )
    elif mode == "dense":
        candidates = store.search(query_embedding, candidate_limit, filters=filters)
    else:
        raise ValueError(f"unsupported retrieval mode: {mode}")

    if not rerank_enabled:
        return candidates[:limit]
    return rerank(
        question,
        candidates,
        limit=limit,
        model=flashrank_model,
        cache_dir=flashrank_cache_dir,
    )
