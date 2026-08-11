"""Rank fusion, BM25 persistence and staleness detection, hybrid search, reranking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vericlaim.policy.models import Chunk, content_hash
from vericlaim.policy.retrieval import (
    BM25Index,
    BM25IndexCorruptError,
    BM25IndexNotFoundError,
    compute_chunk_signature,
    hybrid_search,
    retrieve,
    rrf_fuse,
)
from vericlaim.policy.store import ChunkStore

CLAUSES = [
    ("policies/HomeSecure_Plus_2026.pdf:0", "4.2", 3,
     "Sudden and accidental escape of water from a fixed plumbing system is covered "
     "under this policy, subject to a deductible of PKR 25,000."),
    ("policies/HomeSecure_Plus_2026.pdf:1", "5.1", 4,
     "Loss or damage caused by gradual leakage, seepage or dripping over a period of "
     "time is excluded."),
    ("policies/HomeSecure_Plus_2026.pdf:2", "3.4", 2,
     "Theft following forcible and violent entry to the dwelling is an insured peril."),
    ("policies/Landlord_Protect_2026.pdf:0", "COVERAGE C", 2,
     "Where the let property is rendered uninhabitable, the insurer will indemnify "
     "the landlord for rent genuinely lost."),
]


def _chunk(
    chunk_id: str,
    text: str,
    *,
    clause_id: str | None = None,
    page: int | None = None,
    source_type: str = "policy",
) -> Chunk:
    doc_id = chunk_id.rpartition(":")[0]
    return Chunk(
        id=chunk_id,
        text=text,
        doc_id=doc_id,
        doc_name=Path(doc_id).name,
        source_type=source_type,
        section=f"{Path(doc_id).name} > {clause_id}" if clause_id else None,
        clause_id=clause_id,
        page=page,
        content_hash=content_hash(text),
    )


@pytest.fixture
def chunks() -> list[Chunk]:
    return [
        _chunk(chunk_id, text, clause_id=clause_id, page=page)
        for chunk_id, clause_id, page, text in CLAUSES
    ]


@pytest.fixture
def populated(tmp_path: Path, embedder, chunks: list[Chunk]) -> ChunkStore:
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))
    return store


# ------------------------------------------------------------------------ rrf_fuse


def test_fusion_starts_ranks_at_one() -> None:
    """Rank 0 would divide by k alone and overweight the first result."""
    assert rrf_fuse([["a"]], k=60) == [("a", 1.0 / 61)]


def test_fusion_rewards_agreement_between_rankings() -> None:
    fused = dict(rrf_fuse([["a", "b"], ["b", "a"]], k=60))

    assert fused["a"] == pytest.approx(fused["b"])
    assert fused["a"] == pytest.approx(1.0 / 61 + 1.0 / 62)


def test_a_result_ranked_first_by_both_wins() -> None:
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]], k=60)

    assert fused[0][0] == "a"


def test_fusion_is_order_independent_across_rankings() -> None:
    left = rrf_fuse([["a", "b"], ["c", "a"]], k=60)
    right = rrf_fuse([["c", "a"], ["a", "b"]], k=60)

    assert left == right


def test_ties_break_deterministically_on_id() -> None:
    """Two runs of the same query must return the same order."""
    assert rrf_fuse([["b", "a"], ["a", "b"]], k=60) == rrf_fuse([["b", "a"], ["a", "b"]], k=60)
    assert [chunk_id for chunk_id, _ in rrf_fuse([["b"], ["a"]], k=60)] == ["a", "b"]


def test_a_duplicate_id_within_one_ranking_is_rejected() -> None:
    """It would double-count that chunk and silently inflate its fused score."""
    with pytest.raises(ValueError, match="duplicate IDs"):
        rrf_fuse([["a", "a"]], k=60)


def test_fusion_rejects_a_non_positive_k() -> None:
    with pytest.raises(ValueError, match="k must be greater than zero"):
        rrf_fuse([["a"]], k=0)


def test_fusing_nothing_returns_nothing() -> None:
    assert rrf_fuse([], k=60) == []
    assert rrf_fuse([[], []], k=60) == []


# ---------------------------------------------------------------------- BM25 index


def test_bm25_finds_a_literal_clause_number(chunks: list[Chunk]) -> None:
    """The case dense retrieval loses: an exact token an embedding blurs away."""
    results = BM25Index.build(chunks).search("PKR 25,000 deductible", k=5)

    assert results
    assert results[0][0] == "policies/HomeSecure_Plus_2026.pdf:0"


def test_bm25_returns_nothing_for_unmatched_vocabulary(chunks: list[Chunk]) -> None:
    assert BM25Index.build(chunks).search("cryptocurrency staking rewards", k=5) == []


def test_bm25_respects_the_result_limit(chunks: list[Chunk]) -> None:
    assert len(BM25Index.build(chunks).search("water damage escape leakage", k=1)) <= 1


def test_bm25_rejects_a_non_positive_k(chunks: list[Chunk]) -> None:
    with pytest.raises(ValueError, match="k must be greater than zero"):
        BM25Index.build(chunks).search("water", k=0)


def test_an_empty_corpus_builds_and_searches(tmp_path: Path) -> None:
    index = BM25Index.build([])

    assert index.search("anything", k=5) == []
    assert len(index) == 0


# --------------------------------------------------------------- BM25 filtering


def test_sparse_results_are_scoped_by_source_type() -> None:
    """Unfiltered, a scanned page fuses into a policy answer and is cited as a clause."""
    chunks = [
        _chunk("policies/a.pdf:0", "Escape of water from a fixed plumbing system."),
        _chunk(
            "scanned/CLM-1.pdf:0",
            "Escape of water from a fixed plumbing system observed on site.",
            source_type="scanned_pdf",
        ),
    ]

    results = BM25Index.build(chunks).search(
        "escape of water", k=10, filters={"source_type": "policy"}
    )

    assert [chunk_id for chunk_id, _ in results] == ["policies/a.pdf:0"]


def test_sparse_results_are_scoped_by_document(chunks: list[Chunk]) -> None:
    results = BM25Index.build(chunks).search(
        "insured", k=10, filters={"doc_id": "policies/Landlord_Protect_2026.pdf"}
    )

    assert all(
        chunk_id.startswith("policies/Landlord_Protect_2026.pdf") for chunk_id, _ in results
    )


def test_a_list_filter_matches_any_value(chunks: list[Chunk]) -> None:
    results = BM25Index.build(chunks).search(
        "insured water", k=10, filters={"source_type": ["policy", "scanned_pdf"]}
    )

    assert results


def test_filtering_on_an_unindexed_field_is_rejected(chunks: list[Chunk]) -> None:
    """Failing loudly beats silently ignoring a filter the caller believes applied."""
    with pytest.raises(ValueError, match="cannot filter on 'page'"):
        BM25Index.build(chunks).search("water", k=5, filters={"page": 3})


# --------------------------------------------------------------- BM25 persistence


def test_an_index_round_trips(tmp_path: Path, chunks: list[Chunk]) -> None:
    path = tmp_path / "bm25"
    BM25Index.build(chunks).save(path)

    loaded = BM25Index.load(path)

    assert loaded.signature == compute_chunk_signature(chunks)
    assert loaded.search("gradual leakage", k=1)[0][0] == "policies/HomeSecure_Plus_2026.pdf:1"


def test_filters_survive_persistence(tmp_path: Path) -> None:
    path = tmp_path / "bm25"
    BM25Index.build(
        [
            _chunk("policies/a.pdf:0", "Escape of water is covered."),
            _chunk("scanned/b.pdf:0", "Escape of water was observed.", source_type="scanned_pdf"),
        ]
    ).save(path)

    results = BM25Index.load(path).search(
        "escape of water", k=10, filters={"source_type": "scanned_pdf"}
    )

    assert [chunk_id for chunk_id, _ in results] == ["scanned/b.pdf:0"]


def test_an_empty_index_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "bm25"
    BM25Index.build([]).save(path)

    assert BM25Index.load(path).search("anything", k=5) == []


def test_loading_an_absent_index_is_distinguishable(tmp_path: Path) -> None:
    with pytest.raises(BM25IndexNotFoundError):
        BM25Index.load(tmp_path / "never-built")


def test_loading_a_file_instead_of_a_directory_raises(tmp_path: Path) -> None:
    path = tmp_path / "bm25"
    path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(BM25IndexCorruptError, match="not a directory"):
        BM25Index.load(path)


def test_unreadable_metadata_raises(tmp_path: Path, chunks: list[Chunk]) -> None:
    path = tmp_path / "bm25"
    BM25Index.build(chunks).save(path)
    (path / "metadata.json").write_text("{truncated", encoding="utf-8")

    with pytest.raises(BM25IndexCorruptError, match="metadata is invalid"):
        BM25Index.load(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.pop("signature"),
        lambda m: m.update(count=99),
        lambda m: m.update(signature="0" * 64),
        lambda m: m.update(format_version=99),
        lambda m: m["chunk_ids"].append("extra:0"),
        lambda m: m["filters"].pop(),
        lambda m: m.update(has_index="yes"),
    ],
    ids=["no-signature", "wrong-count", "wrong-signature", "old-format",
         "extra-id", "short-filters", "wrong-type"],
)
def test_inconsistent_metadata_is_rejected(tmp_path: Path, chunks, mutate) -> None:
    """These arrays are zipped positionally against score vectors at query time."""
    path = tmp_path / "bm25"
    BM25Index.build(chunks).save(path)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    mutate(metadata)
    (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(BM25IndexCorruptError):
        BM25Index.load(path)


def test_the_signature_detects_a_stale_index(chunks: list[Chunk]) -> None:
    """A changed corpus must not be answered from an index built before the change."""
    index = BM25Index.build(chunks)
    revised = [*chunks, _chunk("policies/new.pdf:0", "A newly added clause.")]

    assert index.signature != compute_chunk_signature(revised)


def test_the_signature_tracks_content_not_only_ids(chunks: list[Chunk]) -> None:
    edited = [*chunks[:-1], _chunk(chunks[-1].id, "Entirely different wording.")]

    assert compute_chunk_signature(chunks) != compute_chunk_signature(edited)


def test_the_signature_is_stable_for_unchanged_chunks(chunks: list[Chunk]) -> None:
    assert compute_chunk_signature(chunks) == compute_chunk_signature(list(chunks))


# -------------------------------------------------------------------- hybrid search


def _hybrid(question, embedder, store, chunks, **kwargs):
    return hybrid_search(
        question,
        embedder.embed_query(question),
        store,
        BM25Index.build(chunks),
        limit=kwargs.pop("limit", 10),
        top_k_dense=kwargs.pop("top_k_dense", 10),
        top_k_bm25=kwargs.pop("top_k_bm25", 10),
        rrf_k=60,
        **kwargs,
    )


def test_hybrid_search_returns_fused_ranked_results(populated, embedder, chunks) -> None:
    results = _hybrid("escape of water covered", embedder, populated, chunks)

    assert results
    assert [result.rank for result in results] == list(range(len(results)))
    assert all(result.rrf_score is not None for result in results)


def test_hybrid_search_respects_the_limit(populated, embedder, chunks) -> None:
    assert len(_hybrid("water", embedder, populated, chunks, limit=2)) <= 2


def test_a_sparse_only_hit_gets_a_real_similarity_score(populated, embedder, chunks) -> None:
    """Left at zero, a strong keyword match would display as semantically irrelevant."""
    results = _hybrid("PKR 25,000 deductible", embedder, populated, chunks, top_k_dense=1)

    assert all(result.score != 0.0 for result in results)


def test_hybrid_search_filters_both_legs(tmp_path, embedder) -> None:
    chunks = [
        _chunk("policies/a.pdf:0", "Escape of water from a fixed plumbing system is covered."),
        _chunk(
            "scanned/CLM-1.pdf:0",
            "Escape of water from a fixed plumbing system was observed on site.",
            source_type="scanned_pdf",
        ),
    ]
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))

    results = _hybrid(
        "escape of water", embedder, store, chunks, filters={"source_type": "policy"}
    )

    assert [result.chunk.source_type for result in results] == ["policy"]


def test_hybrid_search_on_an_empty_corpus_returns_nothing(tmp_path, embedder) -> None:
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")

    assert _hybrid("anything", embedder, store, []) == []


def test_a_stale_sparse_id_is_skipped_not_fatal(populated, embedder, chunks) -> None:
    """Belt and braces: the signature check should prevent this, but a hit must not crash."""
    stale = BM25Index.build([*chunks, _chunk("policies/ghost.pdf:0", "A ghost clause here.")])

    results = hybrid_search(
        "ghost clause",
        embedder.embed_query("ghost clause"),
        populated,
        stale,
        limit=10,
        top_k_dense=10,
        top_k_bm25=10,
        rrf_k=60,
    )

    assert all(result.chunk.id != "policies/ghost.pdf:0" for result in results)


# ------------------------------------------------------------------- retrieve modes


def _retrieve(question, embedder, store, sparse, **kwargs):
    return retrieve(
        question,
        embedder.embed_query(question),
        store,
        sparse,
        limit=kwargs.pop("limit", 3),
        mode=kwargs.pop("mode", "hybrid"),
        rerank_enabled=kwargs.pop("rerank_enabled", False),
        top_k_dense=10,
        top_k_bm25=10,
        rrf_k=60,
        rerank_candidates=10,
        flashrank_model="ms-marco-MiniLM-L-12-v2",
        flashrank_cache_dir=Path.home() / ".cache" / "flashrank",
        **kwargs,
    )


def test_dense_mode_needs_no_sparse_index(populated, embedder) -> None:
    assert _retrieve("escape of water", embedder, populated, None, mode="dense")


def test_hybrid_mode_without_a_sparse_index_is_rejected(populated, embedder) -> None:
    with pytest.raises(ValueError, match="requires a sparse index"):
        _retrieve("escape of water", embedder, populated, None, mode="hybrid")


def test_an_unknown_mode_is_rejected(populated, embedder, chunks) -> None:
    with pytest.raises(ValueError, match="unsupported retrieval mode"):
        _retrieve("q", embedder, populated, BM25Index.build(chunks), mode="magic")


def test_retrieve_honours_the_limit(populated, embedder, chunks) -> None:
    results = _retrieve("water", embedder, populated, BM25Index.build(chunks), limit=2)

    assert len(results) <= 2


@pytest.mark.flashrank
def test_reranking_reorders_and_scores(populated, embedder, chunks) -> None:
    """A cross-encoder reads question and passage together; a bi-encoder cannot."""
    results = _retrieve(
        "is gradual leakage excluded",
        embedder,
        populated,
        BM25Index.build(chunks),
        limit=3,
        rerank_enabled=True,
    )

    assert results
    assert all(result.rerank_score is not None for result in results)
    assert [result.rank for result in results] == list(range(len(results)))
    assert results[0].chunk.clause_id == "5.1"


@pytest.mark.flashrank
def test_reranking_an_empty_candidate_set_returns_nothing(tmp_path, embedder) -> None:
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")

    assert _retrieve("anything", embedder, store, BM25Index.build([]), rerank_enabled=True) == []
