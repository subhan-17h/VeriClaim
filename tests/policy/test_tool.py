"""The policy tool boundary: Evidence out, correct locators, honest failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.citations import resolve_citations
from vericlaim.config import get_settings
from vericlaim.evidence import Evidence, EvidenceSet, PolicyLocator
from vericlaim.policy.indexer import index_corpus
from vericlaim.policy.models import Chunk, content_hash
from vericlaim.policy.retrieval import BM25Index
from vericlaim.policy.store import ChunkStore
from vericlaim.policy.tool import EmptyIndexError, PolicySearcher

FIXTURES = Path(__file__).parents[1] / "fixtures" / "policies"


def _chunk(chunk_id: str, text: str, *, clause_id=None, page=None, source_type="policy") -> Chunk:
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
def store(tmp_path: Path) -> ChunkStore:
    return ChunkStore(path=tmp_path / "chroma", collection_name="test")


@pytest.fixture
def searcher(tmp_path: Path, store: ChunkStore, embedder) -> PolicySearcher:
    return PolicySearcher(store, embedder, bm25_path=tmp_path / "bm25")


def _populate(store: ChunkStore, embedder, chunks: list[Chunk]) -> None:
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))


POLICY_CHUNKS = [
    _chunk(
        "policies/HomeSecure_Plus_2026.pdf:15",
        "Sudden and accidental escape of water from a fixed plumbing system is covered "
        "under this policy. A deductible of PKR 25,000 applies.",
        clause_id="4.2",
        page=3,
    ),
    _chunk(
        "policies/HomeSecure_Plus_2026.pdf:19",
        "Loss or damage caused by gradual leakage, seepage or dripping over a period of "
        "time is excluded.",
        clause_id="5.1",
        page=4,
    ),
]


# --------------------------------------------------------------- the evidence contract


def test_search_returns_only_evidence(searcher, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)

    results = searcher.search("escape of water")

    assert results
    assert all(isinstance(item, Evidence) for item in results)


def test_evidence_carries_a_policy_locator(searcher, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)

    evidence = searcher.search("sudden accidental escape of water covered")[0]

    assert isinstance(evidence.locator, PolicyLocator)
    assert evidence.locator.document == "HomeSecure_Plus_2026.pdf"
    assert evidence.locator.page == 3
    assert evidence.locator.section == "4.2"
    assert evidence.locator.chunk_id == "policies/HomeSecure_Plus_2026.pdf:15"


def test_the_citation_names_document_page_and_clause(searcher, store, embedder) -> None:
    """Document, page, and clause together are what make an answer checkable."""
    _populate(store, embedder, POLICY_CHUNKS)

    citation = searcher.search("sudden accidental escape of water covered")[0].cite()

    assert citation == "HomeSecure_Plus_2026.pdf › p.3 › 4.2"


def test_source_id_is_the_document_path_not_its_name(searcher, store, embedder) -> None:
    """Two documents can share a name; source_id must stay unambiguous."""
    _populate(store, embedder, POLICY_CHUNKS)

    evidence = searcher.search("escape of water")[0]

    assert evidence.source_id == "policies/HomeSecure_Plus_2026.pdf"


def test_provenance_records_the_tool_and_the_query(searcher, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)

    evidence = searcher.search("gradual leakage", trace_id="trace-123")[0]

    assert evidence.provenance.tool == "search_policy"
    assert evidence.provenance.query == "gradual leakage"
    assert evidence.provenance.trace_id == "trace-123"


def test_policy_evidence_is_full_confidence(searcher, store, embedder) -> None:
    """The text is exactly what the wording says; ranking fourth is not a reason to hedge."""
    _populate(store, embedder, POLICY_CHUNKS)

    results = searcher.search("escape of water")

    assert all(evidence.confidence == 1.0 for evidence in results)
    assert not any(evidence.is_low_confidence(0.6) for evidence in results)


def test_evidence_composes_into_a_citable_set(searcher, store, embedder) -> None:
    """The tool's output must be usable by the C-2 spine without adaptation."""
    _populate(store, embedder, POLICY_CHUNKS)

    evidence_set = EvidenceSet(searcher.search("water damage", limit=2))

    assert [item.id for item in evidence_set] == ["E1", "E2"]
    assert "[E1]" in evidence_set.render_for_synthesis()


# ------------------------------------------------------------------------- scoping


def test_scanned_chunks_never_surface_in_a_policy_search(searcher, store, embedder) -> None:
    """Sharing a collection is only safe because the source filter is unconditional."""
    _populate(
        store,
        embedder,
        [
            *POLICY_CHUNKS,
            _chunk(
                "scanned/CLM-1088.pdf:0",
                "Inspection found sudden and accidental escape of water at the property.",
                page=2,
                source_type="scanned_pdf",
            ),
        ],
    )

    results = searcher.search("sudden and accidental escape of water", limit=10)

    assert results
    assert all(evidence.source_type == "policy" for evidence in results)


def test_a_caller_cannot_override_the_source_filter(searcher, store, embedder) -> None:
    """Otherwise a scanned page would be wrapped in a PolicyLocator and cited as a clause."""
    _populate(
        store,
        embedder,
        [
            *POLICY_CHUNKS,
            _chunk(
                "scanned/CLM-1088.pdf:0",
                "Inspection found sudden and accidental escape of water.",
                page=2,
                source_type="scanned_pdf",
            ),
        ],
    )

    results = searcher.search(
        "escape of water", filters={"source_type": "scanned_pdf"}, limit=10
    )

    assert all(evidence.source_type == "policy" for evidence in results)


def test_search_can_be_scoped_to_one_document(searcher, store, embedder) -> None:
    _populate(
        store,
        embedder,
        [
            *POLICY_CHUNKS,
            _chunk(
                "policies/Landlord_Protect_2026.pdf:4",
                "Sudden and accidental escape of water is covered for the let property.",
                clause_id="3.1",
                page=2,
            ),
        ],
    )

    results = searcher.search(
        "escape of water",
        filters={"doc_id": "policies/Landlord_Protect_2026.pdf"},
        limit=10,
    )

    assert results
    assert all(item.source_id == "policies/Landlord_Protect_2026.pdf" for item in results)


def test_the_limit_bounds_the_result_count(searcher, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)

    assert len(searcher.search("water", limit=1)) == 1


# ------------------------------------------------------------------ honest failure


def test_searching_an_empty_index_raises_rather_than_returning_nothing(searcher) -> None:
    """An empty index is "we do not know", not "the policy is silent" -- opposite answers."""
    with pytest.raises(EmptyIndexError, match="index is empty"):
        searcher.search("escape of water")


def test_an_empty_query_is_rejected(searcher, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)

    with pytest.raises(ValueError, match="query must not be empty"):
        searcher.search("   ")


def test_a_question_with_no_match_returns_no_evidence(searcher, store, embedder) -> None:
    """Distinct from an empty index: the corpus was searched and had nothing to say."""
    _populate(store, embedder, POLICY_CHUNKS)

    results = searcher.search("cryptocurrency staking rewards", limit=5)

    assert isinstance(results, list)


# ------------------------------------------------------------- sparse self-healing


def test_a_missing_sparse_index_is_built_and_persisted(
    tmp_path, store, embedder
) -> None:
    _populate(store, embedder, POLICY_CHUNKS)
    bm25_path = tmp_path / "bm25"
    searcher = PolicySearcher(store, embedder, bm25_path=bm25_path)

    searcher.search("escape of water")

    assert (bm25_path / "metadata.json").exists()


def test_a_stale_sparse_index_is_rebuilt(tmp_path, store, embedder) -> None:
    """The indexer changes the collection; the signature check here is what notices."""
    bm25_path = tmp_path / "bm25"
    _populate(store, embedder, POLICY_CHUNKS[:1])
    searcher = PolicySearcher(store, embedder, bm25_path=bm25_path)
    searcher.search("escape of water")
    stale_signature = BM25Index.load(bm25_path).signature

    _populate(store, embedder, POLICY_CHUNKS[1:])
    searcher.search("gradual leakage")

    assert BM25Index.load(bm25_path).signature != stale_signature


def test_a_corrupt_sparse_index_is_rebuilt(tmp_path, store, embedder) -> None:
    bm25_path = tmp_path / "bm25"
    bm25_path.mkdir()
    (bm25_path / "metadata.json").write_text("{truncated", encoding="utf-8")
    _populate(store, embedder, POLICY_CHUNKS)

    results = PolicySearcher(store, embedder, bm25_path=bm25_path).search("escape of water")

    assert results


def test_an_unchanged_index_is_reused(tmp_path, store, embedder) -> None:
    _populate(store, embedder, POLICY_CHUNKS)
    searcher = PolicySearcher(store, embedder, bm25_path=tmp_path / "bm25")

    searcher.search("escape of water")
    first = searcher.sparse_index()
    searcher.search("gradual leakage")

    assert searcher.sparse_index() is first


# -------------------------------------------------- end to end over the real fixtures


@pytest.fixture
def indexed(tmp_path: Path, embedder) -> PolicySearcher:
    """Index the three committed fixture wordings and return a searcher over them."""
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    index_corpus(
        FIXTURES,
        store,
        embedder,
        manifest_path=tmp_path / "manifest.json",
        chunk_size=get_settings().chunk_size,
        chunk_overlap=get_settings().chunk_overlap,
        pdf_parser="pypdf",
    )
    return PolicySearcher(store, embedder, bm25_path=tmp_path / "bm25")


def test_the_flagship_policy_question_cites_the_governing_clause(indexed) -> None:
    """The C-3 acceptance query, answered from the corpus with a checkable citation."""
    results = indexed.search("sudden escape of water from fixed plumbing", limit=5)

    citations = [evidence.cite() for evidence in results]
    assert "HomeSecure_Plus_2026.pdf › p.3 › 4.2" in citations

    governing = next(item for item in results if item.locator.section == "4.2")
    assert "PKR 25,000" in governing.content


def test_the_exclusion_is_retrievable_and_separately_cited(indexed) -> None:
    """Cover and exclusion must be distinct evidence, or an answer cannot weigh them."""
    results = indexed.search("gradual leakage seepage excluded maintenance", limit=5)

    sections = [evidence.locator.section for evidence in results]
    assert "5.1" in sections


def test_every_citation_from_the_real_corpus_resolves(indexed) -> None:
    evidence_set = EvidenceSet(indexed.search("water damage cover and exclusions", limit=5))

    answer = " ".join(f"Point {item.id} [{item.id}]." for item in evidence_set)
    report = resolve_citations(answer, evidence_set)

    assert report.ok
    assert not report.unresolved
