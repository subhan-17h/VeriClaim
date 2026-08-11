"""Chroma storage: metadata round-trip, filtered search, and path-keyed identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.policy.models import Chunk, content_hash
from vericlaim.policy.store import ChunkStore, build_where


def _chunk(
    chunk_id: str,
    text: str,
    *,
    doc_id: str | None = None,
    doc_name: str | None = None,
    source_type: str = "policy",
    **extra: object,
) -> Chunk:
    """Build a chunk whose identity is consistent with its id by default.

    A chunk id is ``<doc_id>:<n>``, so deriving doc_id from it here keeps the fixture
    from silently describing a document other than the one its id names.
    """
    derived_doc_id = doc_id if doc_id is not None else chunk_id.rpartition(":")[0]
    return Chunk(
        id=chunk_id,
        text=text,
        doc_id=derived_doc_id,
        doc_name=doc_name if doc_name is not None else Path(derived_doc_id).name,
        source_type=source_type,
        content_hash=content_hash(text),
        **extra,
    )


@pytest.fixture
def store(tmp_path: Path) -> ChunkStore:
    return ChunkStore(path=tmp_path / "chroma", collection_name="test")


def _add(store: ChunkStore, embedder, chunks: list[Chunk]) -> None:
    store.add_chunks(chunks, embedder.embed_documents([chunk.embed_text for chunk in chunks]))


# ------------------------------------------------------------------ where clauses


def test_no_filters_produce_no_where_clause() -> None:
    assert build_where(None) is None
    assert build_where({}) is None


def test_a_single_field_needs_no_conjunction() -> None:
    assert build_where({"source_type": "policy"}) == {"source_type": "policy"}


def test_multiple_fields_are_wrapped_in_an_explicit_and() -> None:
    """Chroma rejects a bare two-key mapping rather than implying a conjunction."""
    clause = build_where({"source_type": "policy", "page": 3})

    assert clause == {"$and": [{"source_type": "policy"}, {"page": 3}]}


def test_a_list_value_becomes_a_membership_test() -> None:
    assert build_where({"doc_id": ["a.pdf", "b.pdf"]}) == {
        "doc_id": {"$in": ["a.pdf", "b.pdf"]}
    }


def test_an_empty_list_filter_is_rejected() -> None:
    """It would match nothing, which is nearly always a caller bug, not an intent."""
    with pytest.raises(ValueError, match="matches nothing"):
        build_where({"doc_id": []})


# -------------------------------------------------------------- metadata fidelity


def test_every_chunk_field_survives_a_round_trip(store: ChunkStore, embedder) -> None:
    original = _chunk(
        "policies/a.pdf:0",
        "Sudden and accidental escape of water is covered.",
        section="a.pdf > SECTION 4 > 4.2",
        clause_id="4.2",
        page=3,
    )

    _add(store, embedder, [original])
    stored = store.all_chunks()[0]

    assert stored == original


def test_ocr_fields_round_trip(store: ChunkStore, embedder) -> None:
    """C-4 populates these; the store must carry them before C-4 exists."""
    original = _chunk(
        "scanned/CLM-1088.pdf:0",
        "Plumber reports a ruptured pipe.",
        doc_id="scanned/CLM-1088.pdf",
        doc_name="CLM-1088.pdf",
        source_type="scanned",
        page=2,
        ocr_confidence=0.91,
        ocr_engine="rapidocr",
        escalated=True,
    )

    _add(store, embedder, [original])
    stored = store.all_chunks()[0]

    assert stored.ocr_confidence == pytest.approx(0.91)
    assert stored.ocr_engine == "rapidocr"
    assert stored.escalated is True


def test_unset_optional_fields_come_back_unset(store: ChunkStore, embedder) -> None:
    _add(store, embedder, [_chunk("policies/a.pdf:0", "Body text.")])

    stored = store.all_chunks()[0]

    assert stored.section is None
    assert stored.clause_id is None
    assert stored.page is None
    assert stored.ocr_confidence is None
    assert stored.escalated is False


def test_mismatched_embedding_count_is_rejected(store: ChunkStore) -> None:
    with pytest.raises(ValueError, match="counts differ"):
        store.add_chunks([_chunk("a:0", "one"), _chunk("a:1", "two")], [[0.1]])


def test_adding_nothing_is_a_no_op(store: ChunkStore) -> None:
    store.add_chunks([], [])

    assert store.count() == 0


# -------------------------------------------------------------- document identity


def test_same_basename_in_two_directories_both_index(store: ChunkStore, embedder) -> None:
    """The per-claim corpus this system indexes cannot satisfy a filename constraint."""
    first = _chunk(
        "claims/CLM-1001/estimate.pdf:0",
        "Estimate for claim 1001.",
        doc_id="claims/CLM-1001/estimate.pdf",
        doc_name="estimate.pdf",
    )
    second = _chunk(
        "claims/CLM-1002/estimate.pdf:0",
        "Estimate for claim 1002.",
        doc_id="claims/CLM-1002/estimate.pdf",
        doc_name="estimate.pdf",
    )

    _add(store, embedder, [first, second])

    assert store.count() == 2
    assert store.document_ids() == [
        "claims/CLM-1001/estimate.pdf",
        "claims/CLM-1002/estimate.pdf",
    ]


def test_deleting_one_document_spares_its_namesake(store: ChunkStore, embedder) -> None:
    """Keyed on the basename, this delete would take both documents' chunks."""
    _add(
        store,
        embedder,
        [
            _chunk(
                "claims/CLM-1001/estimate.pdf:0",
                "Estimate for claim 1001.",
                doc_id="claims/CLM-1001/estimate.pdf",
                doc_name="estimate.pdf",
            ),
            _chunk(
                "claims/CLM-1002/estimate.pdf:0",
                "Estimate for claim 1002.",
                doc_id="claims/CLM-1002/estimate.pdf",
                doc_name="estimate.pdf",
            ),
        ],
    )

    store.delete_document("claims/CLM-1001/estimate.pdf")

    assert store.document_ids() == ["claims/CLM-1002/estimate.pdf"]


def test_chunk_counts_are_keyed_by_path(store: ChunkStore, embedder) -> None:
    _add(
        store,
        embedder,
        [
            _chunk("policies/a.pdf:0", "One.", doc_id="policies/a.pdf", doc_name="a.pdf"),
            _chunk("policies/a.pdf:1", "Two.", doc_id="policies/a.pdf", doc_name="a.pdf"),
            _chunk("policies/b.pdf:0", "Three.", doc_id="policies/b.pdf", doc_name="b.pdf"),
        ],
    )

    assert store.document_chunk_counts() == {"policies/a.pdf": 2, "policies/b.pdf": 1}


# ---------------------------------------------------------------------- searching


def test_search_returns_ranked_results(store: ChunkStore, embedder) -> None:
    _add(
        store,
        embedder,
        [
            _chunk("policies/a.pdf:0", "Sudden and accidental escape of water is covered."),
            _chunk("policies/a.pdf:1", "Theft following forcible and violent entry."),
        ],
    )

    results = store.search(embedder.embed_query("escape of water covered"), k=2)

    assert [result.rank for result in results] == [0, 1]
    assert results[0].chunk.text.startswith("Sudden and accidental")


def test_search_scopes_by_source_type(store: ChunkStore, embedder) -> None:
    """Two sources share the collection; a policy query must not see scanned pages."""
    _add(
        store,
        embedder,
        [
            _chunk("policies/a.pdf:0", "Escape of water is covered under this policy."),
            _chunk(
                "scanned/CLM-1.pdf:0",
                "Escape of water observed at the property during inspection.",
                doc_id="scanned/CLM-1.pdf",
                doc_name="CLM-1.pdf",
                source_type="scanned",
            ),
        ],
    )

    results = store.search(
        embedder.embed_query("escape of water"), k=10, filters={"source_type": "policy"}
    )

    assert [result.chunk.source_type for result in results] == ["policy"]


def test_search_combines_filters(store: ChunkStore, embedder) -> None:
    _add(
        store,
        embedder,
        [
            _chunk("policies/a.pdf:0", "Water damage on page three.", page=3),
            _chunk("policies/a.pdf:1", "Water damage on page four.", page=4),
        ],
    )

    results = store.search(
        embedder.embed_query("water damage"), k=10, filters={"source_type": "policy", "page": 4}
    )

    assert [result.chunk.page for result in results] == [4]


def test_search_on_an_empty_collection_returns_nothing(store: ChunkStore, embedder) -> None:
    assert store.search(embedder.embed_query("anything"), k=5) == []


def test_search_rejects_a_non_positive_k(store: ChunkStore, embedder) -> None:
    with pytest.raises(ValueError, match="k must be greater than zero"):
        store.search(embedder.embed_query("anything"), k=0)


def test_scores_are_similarities_not_distances(store: ChunkStore, embedder) -> None:
    text = "Sudden and accidental escape of water is covered."
    _add(store, embedder, [_chunk("policies/a.pdf:0", text)])

    result = store.search(embedder.embed_query(text), k=1)[0]

    assert 0.0 <= result.score <= 1.0


# ----------------------------------------------------------------------- reading


def test_all_chunks_are_ordered_deterministically(store: ChunkStore, embedder) -> None:
    chunks = [_chunk(f"policies/a.pdf:{index}", f"Clause {index}.") for index in range(5)]
    _add(store, embedder, list(reversed(chunks)))

    assert [chunk.id for chunk in store.all_chunks()] == sorted(chunk.id for chunk in chunks)


def test_all_chunks_can_be_filtered(store: ChunkStore, embedder) -> None:
    _add(
        store,
        embedder,
        [
            _chunk("policies/a.pdf:0", "Policy text."),
            _chunk(
                "scanned/b.pdf:0",
                "Scanned text.",
                doc_id="scanned/b.pdf",
                doc_name="b.pdf",
                source_type="scanned",
            ),
        ],
    )

    assert len(store.all_chunks(filters={"source_type": "scanned"})) == 1


def test_chunks_with_embeddings_returns_the_stored_vectors(
    store: ChunkStore, embedder
) -> None:
    chunk = _chunk("policies/a.pdf:0", "Escape of water.")
    vector = embedder.embed_documents([chunk.embed_text])[0]
    store.add_chunks([chunk], [vector])

    stored_chunk, stored_vector = store.chunks_with_embeddings([chunk.id])[chunk.id]

    assert stored_chunk == chunk
    assert stored_vector == pytest.approx(vector)


def test_chunks_with_embeddings_short_circuits_on_no_ids(store: ChunkStore) -> None:
    assert store.chunks_with_embeddings([]) == {}


def test_document_chunks_paginate_in_numeric_order(store: ChunkStore, embedder) -> None:
    """String ordering would place chunk :10 before chunk :2."""
    chunks = [_chunk(f"policies/a.pdf:{index}", f"Clause {index}.") for index in range(12)]
    _add(store, embedder, chunks)

    page, total = store.chunks_for_document("policies/a.pdf", limit=3, offset=9)

    assert total == 12
    assert [chunk.id for chunk in page] == [
        "policies/a.pdf:9",
        "policies/a.pdf:10",
        "policies/a.pdf:11",
    ]


def test_paging_past_the_end_returns_the_total(store: ChunkStore, embedder) -> None:
    _add(store, embedder, [_chunk("policies/a.pdf:0", "Only chunk.")])

    page, total = store.chunks_for_document("policies/a.pdf", limit=5, offset=10)

    assert page == []
    assert total == 1


# ------------------------------------------------------------------------- reset


def test_reset_empties_the_collection(store: ChunkStore, embedder) -> None:
    _add(store, embedder, [_chunk("policies/a.pdf:0", "Body.")])

    store.reset()

    assert store.count() == 0
    assert store.document_ids() == []


def test_the_store_persists_across_instances(tmp_path: Path, embedder) -> None:
    path = tmp_path / "chroma"
    first = ChunkStore(path=path, collection_name="test")
    _add(first, embedder, [_chunk("policies/a.pdf:0", "Escape of water.")])

    second = ChunkStore(path=path, collection_name="test")

    assert second.count() == 1
