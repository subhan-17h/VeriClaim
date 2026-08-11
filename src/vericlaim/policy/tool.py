"""The policy source's tool boundary: a question in, ``Evidence`` out.

This is where the retrieval subsystem stops and the rest of the system begins.
Nothing downstream sees a Chunk, a score, or a Chroma result -- synthesis receives
Evidence and only Evidence, which is what makes "every material claim traces back to
its origin" enforceable rather than aspirational.

The sparse index is loaded lazily and validated against the collection's current
signature on every use. A BM25 index that has fallen behind returns ids that no longer
exist; fusing those degrades hybrid retrieval to dense-only with nothing in the logs
to say so.

Both retrieval sources search the same collection through :class:`ChunkSearcher`, and
each subclass fixes the three things that must not be left to a caller: which source
it may see, what its span is called in a trace, and which locator its evidence
carries. Those three travel together -- a searcher scoped to scanned pages that built
policy locators would cite somebody's paperwork as a policy clause -- so they are
settled by construction rather than by argument.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vericlaim.config import Settings, get_settings
from vericlaim.evidence import Evidence, PolicyLocator, Provenance
from vericlaim.policy.embeddings import Embedder, get_embedder
from vericlaim.policy.models import RetrievalSourceType, RetrievedChunk
from vericlaim.policy.retrieval import (
    BM25Index,
    BM25IndexCorruptError,
    BM25IndexNotFoundError,
    compute_chunk_signature,
    retrieve,
)
from vericlaim.policy.store import ChunkStore
from vericlaim.tracing import traced

__all__ = ("ChunkSearcher", "EmptyIndexError", "PolicySearcher", "search_policy")

TOOL_NAME = "search_policy"


class EmptyIndexError(RuntimeError):
    """Raised when a search runs against an index containing nothing.

    Distinguished from "no results" on purpose. An empty index means indexing never
    ran or was wiped, and the honest answer is that we do not know -- not that the
    policy is silent on the question. Those two lead to opposite decisions.
    """


class ChunkSearcher:
    """Searches the document collection and returns citable evidence.

    Constructed with its dependencies rather than reaching for globals, so a test can
    supply an in-memory store and a fake embedder.

    Abstract in one respect only: a subclass declares its source and names its tool,
    and turns a retrieved chunk into evidence carrying the locator that source can be
    cited by.
    """

    #: The only source this searcher may return. Never overridable by a caller.
    source_type: RetrievalSourceType
    #: The tool name recorded in provenance and used as the trace span name.
    tool_name: str

    def __init__(
        self,
        store: ChunkStore,
        embedder: Embedder | None = None,
        *,
        settings: Settings | None = None,
        bm25_path: Path | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._store = store
        self._embedder = embedder if embedder is not None else get_embedder()
        self._bm25_path = bm25_path if bm25_path is not None else self._settings.bm25_dir
        self._sparse: BM25Index | None = None

    # ------------------------------------------------------------- sparse index

    def sparse_index(self) -> BM25Index:
        """Return a sparse index guaranteed to match the collection's contents.

        Rebuilt in memory when the persisted one is missing, corrupt, or stale. This
        self-healing is why the indexer does not have to know BM25 exists: it changes
        the collection, and the signature check here notices.
        """
        chunks = self._store.all_chunks()
        signature = compute_chunk_signature(chunks)
        if self._sparse is not None and self._sparse.signature == signature:
            return self._sparse

        try:
            loaded = BM25Index.load(self._bm25_path)
        except (BM25IndexNotFoundError, BM25IndexCorruptError):
            loaded = None

        if loaded is not None and loaded.signature == signature:
            self._sparse = loaded
            return loaded

        rebuilt = BM25Index.build(chunks)
        rebuilt.save(self._bm25_path)
        self._sparse = rebuilt
        return rebuilt

    # -------------------------------------------------------------------- search

    def _search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        """Return evidence answering ``query``, most relevant first.

        ``filters`` scopes the search by chunk metadata -- a document, a set of
        documents, one claim. The source type is always applied last and cannot be
        overridden, since a policy search returning a scanned page would cite it as a
        policy clause.

        Untraced, so that each subclass's public ``search`` emits exactly one span
        under its own tool name. Wrapping both would put two spans in the trace for
        one logical tool call, and against a 5,000-trace monthly budget that
        duplication is a cost rather than only noise.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if self._store.count() == 0:
            raise EmptyIndexError(
                "The document index is empty, so this question cannot be answered "
                "from policy documents. Run indexing before searching; an empty "
                "index is not evidence that the policies are silent."
            )

        settings = self._settings
        scoped: dict[str, Any] = {**(filters or {}), "source_type": self.source_type}
        mode = settings.retrieval_mode

        results = retrieve(
            query,
            self._embedder.embed_query(query),
            self._store,
            self.sparse_index() if mode == "hybrid" else None,
            limit=limit if limit is not None else settings.rerank_top_n,
            mode=mode,
            rerank_enabled=settings.rerank_enabled,
            top_k_dense=settings.top_k_dense,
            top_k_bm25=settings.top_k_bm25,
            rrf_k=settings.rrf_k,
            rerank_candidates=settings.rerank_candidates,
            flashrank_model=settings.flashrank_model,
            flashrank_cache_dir=settings.flashrank_cache_dir,
            filters=scoped,
        )

        provenance = Provenance(tool=self.tool_name, trace_id=trace_id, query=query)
        return [self._to_evidence(result, provenance) for result in results]

    def _to_evidence(
        self, result: RetrievedChunk, provenance: Provenance
    ) -> Evidence:  # pragma: no cover - every subclass overrides this
        """Convert one retrieved chunk into evidence its source can be cited by."""
        raise NotImplementedError


class PolicySearcher(ChunkSearcher):
    """Searches indexed policy wordings."""

    source_type: RetrievalSourceType = "policy"
    tool_name = TOOL_NAME

    @traced(name=TOOL_NAME, run_type="tool")
    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        """Return policy evidence answering ``query``, most relevant first."""
        return self._search(query, filters=filters, limit=limit, trace_id=trace_id)

    def _to_evidence(self, result: RetrievedChunk, provenance: Provenance) -> Evidence:
        """Convert one retrieved chunk into citable evidence.

        Confidence is 1.0 for a digitally-parsed policy: the text is exactly what the
        document says, and there is nothing to qualify. Retrieval score measures
        relevance, not trustworthiness, and putting it here would mark a correctly
        extracted clause as low-confidence merely for ranking fourth -- causing
        synthesis to hedge about a wording it can read verbatim. Scanned evidence in
        C-4 carries a real OCR confidence, which is what the field is for.
        """
        chunk = result.chunk
        return Evidence(
            source_type=chunk.source_type,
            source_id=chunk.doc_id,
            content=chunk.text,
            locator=PolicyLocator(
                document=chunk.doc_name,
                page=chunk.page,
                section=chunk.clause_id or chunk.section,
                chunk_id=chunk.id,
            ),
            provenance=provenance,
            confidence=1.0,
        )


def search_policy(
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    trace_id: str | None = None,
) -> list[Evidence]:
    """Search the indexed policy wordings and return citable evidence.

    The entry point the orchestrator calls. It builds its dependencies from settings;
    anything that wants to inject them should construct a :class:`PolicySearcher`.

    Deliberately not traced itself: ``PolicySearcher.search`` already emits the span,
    and wrapping a delegating function would put two nested spans in the trace for one
    logical tool call. Against a 5,000-trace monthly budget that duplication is a cost,
    not just noise.
    """
    settings = get_settings()
    searcher = PolicySearcher(
        ChunkStore(path=settings.chroma_dir, collection_name=settings.collection_name),
        settings=settings,
    )
    return searcher.search(query, filters=filters, limit=limit, trace_id=trace_id)
