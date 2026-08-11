"""The scanned tool boundary: cite by page, carry the OCR score, refuse to overstate."""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.config import get_settings
from vericlaim.evidence import Evidence, EvidenceSet, ScannedLocator
from vericlaim.policy.models import Chunk, content_hash
from vericlaim.policy.store import ChunkStore
from vericlaim.policy.tool import EmptyIndexError
from vericlaim.scanned.chunking import UNREADABLE_PAGE_TEXT
from vericlaim.scanned.indexer import index_scanned_corpus
from vericlaim.scanned.tool import ScannedSearcher

SCANS = Path(__file__).parents[1] / "fixtures" / "scanned"


def _chunk(
    chunk_id: str,
    text: str,
    *,
    page: int | None = 1,
    claim_id: str | None = None,
    confidence: float | None = 0.9,
    engine: str | None = "rapidocr",
    escalated: bool = False,
    source_type: str = "scanned_pdf",
) -> Chunk:
    doc_id = chunk_id.rpartition(":")[0]
    return Chunk(
        id=chunk_id,
        text=text,
        doc_id=doc_id,
        doc_name=Path(doc_id).name,
        source_type=source_type,
        section=f"{Path(doc_id).name} > p.{page}",
        page=page,
        content_hash=content_hash(text),
        claim_id=claim_id,
        ocr_confidence=confidence,
        ocr_engine=engine,
        escalated=escalated,
    )


@pytest.fixture
def store(tmp_path: Path) -> ChunkStore:
    return ChunkStore(path=tmp_path / "chroma", collection_name="test")


@pytest.fixture
def searcher(tmp_path: Path, store: ChunkStore, embedder) -> ScannedSearcher:
    return ScannedSearcher(store, embedder, bm25_path=tmp_path / "bm25")


def _populate(store: ChunkStore, embedder, chunks: list[Chunk]) -> None:
    store.add_chunks(chunks, embedder.embed_documents([c.embed_text for c in chunks]))


SCANNED_CHUNKS = [
    _chunk(
        "scanned/CLM-1001_INSPECTION.pdf:0",
        "The copper supply pipe failed at a soldered joint. The failure is a clean "
        "circumferential rupture with no corrosion present.",
        page=1,
        claim_id="CLM-1001",
        confidence=0.94,
    ),
    _chunk(
        "scanned/CLM-1002_INSPECTION.pdf:0",
        "The waste pipe has been weeping at a compression fitting for several months, "
        "consistent with gradual seepage rather than sudden failure.",
        page=1,
        claim_id="CLM-1002",
        confidence=0.38,
    ),
]


# ------------------------------------------------------------- the evidence contract


def test_search_returns_only_evidence(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    results = searcher.search("ruptured pipe")

    assert results
    assert all(isinstance(item, Evidence) for item in results)


def test_evidence_carries_a_scanned_locator(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    evidence = searcher.search("copper supply pipe soldered joint")[0]

    assert isinstance(evidence.locator, ScannedLocator)
    assert evidence.locator.document == "CLM-1001_INSPECTION.pdf"
    assert evidence.locator.page == 1
    assert evidence.locator.ocr_confidence == pytest.approx(0.94)
    assert evidence.locator.ocr_engine == "rapidocr"


def test_the_citation_names_the_page_and_the_ocr_score(searcher, store, embedder) -> None:
    """A reader must be able to find the page and know how well it was read."""
    _populate(store, embedder, SCANNED_CHUNKS)

    citation = searcher.search("copper supply pipe soldered joint")[0].cite()

    assert citation == "CLM-1001_INSPECTION.pdf › p.1 (OCR 0.94)"


def test_a_vision_assisted_page_says_so_in_its_citation(searcher, store, embedder) -> None:
    _populate(
        store,
        embedder,
        [
            _chunk(
                "scanned/CLM-1002_INSPECTION.pdf:0",
                "Recovered on a second reading: the fitting had been weeping.",
                confidence=0.85,
                escalated=True,
                claim_id="CLM-1002",
            )
        ],
    )

    evidence = searcher.search("weeping fitting")[0]

    assert evidence.locator.escalated is True
    assert "vision-assisted" in evidence.cite()


def test_provenance_records_the_tool_and_the_query(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    evidence = searcher.search("gradual seepage", trace_id="trace-9")[0]

    assert evidence.provenance.tool == "search_scanned"
    assert evidence.provenance.query == "gradual seepage"
    assert evidence.provenance.trace_id == "trace-9"


def test_source_id_is_the_document_path(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    evidence = searcher.search("copper supply pipe soldered joint")[0]

    assert evidence.source_id == "scanned/CLM-1001_INSPECTION.pdf"


# --------------------------------------------------------- confidence, not relevance


def test_evidence_confidence_is_the_pages_ocr_score(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    by_document = {
        item.locator.document: item for item in searcher.search("pipe", limit=10)
    }

    assert by_document["CLM-1001_INSPECTION.pdf"].confidence == pytest.approx(0.94)
    assert by_document["CLM-1002_INSPECTION.pdf"].confidence == pytest.approx(0.38)


def test_a_badly_read_page_is_flagged_low_confidence(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)
    floor = get_settings().ocr_confidence_floor

    results = searcher.search("weeping compression fitting seepage", limit=10)
    degraded = next(
        item for item in results if item.locator.document == "CLM-1002_INSPECTION.pdf"
    )

    assert degraded.is_low_confidence(floor)


def test_synthesis_is_told_to_qualify_a_low_confidence_page(
    searcher, store, embedder
) -> None:
    """The flag has to survive into the only view synthesis is ever given."""
    _populate(store, embedder, SCANNED_CHUNKS)
    floor = get_settings().ocr_confidence_floor

    rendered = EvidenceSet(searcher.search("pipe", limit=10)).render_for_synthesis(
        low_confidence_floor=floor
    )

    assert "LOW CONFIDENCE" in rendered
    assert rendered.count("LOW CONFIDENCE") == 1


def test_a_chunk_without_a_score_is_treated_as_unverified(
    searcher, store, embedder
) -> None:
    """Not knowing how well a page was read is a reason to qualify, not to assert."""
    _populate(
        store,
        embedder,
        [
            _chunk(
                "scanned/CLM-1004_INSPECTION.pdf:0",
                "Findings on a page whose recognition score was never recorded.",
                confidence=None,
                engine=None,
            )
        ],
    )

    evidence = searcher.search("findings")[0]

    assert evidence.confidence == 0.0
    assert evidence.is_low_confidence(get_settings().ocr_confidence_floor)


def test_an_unreadable_page_is_returned_as_refusal_grade_evidence(
    searcher, store, embedder
) -> None:
    """It must stay retrievable: "we could not read it" is itself an answer."""
    _populate(
        store,
        embedder,
        [
            _chunk(
                "scanned/CLM-1003_INSPECTION.pdf:0",
                UNREADABLE_PAGE_TEXT,
                confidence=0.0,
                claim_id="CLM-1003",
            )
        ],
    )

    evidence = searcher.search("inspection findings", claim_id="CLM-1003")[0]

    assert evidence.content == UNREADABLE_PAGE_TEXT
    assert evidence.confidence == 0.0


# ------------------------------------------------------------------------- scoping


def test_search_scopes_to_one_claim(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    results = searcher.search("pipe", claim_id="CLM-1002", limit=10)

    assert results
    assert all(item.locator.document == "CLM-1002_INSPECTION.pdf" for item in results)


def test_policy_chunks_never_surface_in_a_scanned_search(
    searcher, store, embedder
) -> None:
    _populate(
        store,
        embedder,
        [
            *SCANNED_CHUNKS,
            _chunk(
                "policies/HomeSecure_Plus_2026.pdf:15",
                "Sudden and accidental escape of water from a fixed plumbing system "
                "is covered under this policy.",
                page=3,
                source_type="policy",
                confidence=None,
                engine=None,
            ),
        ],
    )

    results = searcher.search("sudden escape of water plumbing", limit=10)

    assert results
    assert all(item.source_type == "scanned_pdf" for item in results)


def test_a_caller_cannot_override_the_source_filter(searcher, store, embedder) -> None:
    """Otherwise a policy clause would be cited as a page of somebody's paperwork."""
    _populate(
        store,
        embedder,
        [
            *SCANNED_CHUNKS,
            _chunk(
                "policies/HomeSecure_Plus_2026.pdf:15",
                "Sudden and accidental escape of water is covered under this policy.",
                page=3,
                source_type="policy",
                confidence=None,
                engine=None,
            ),
        ],
    )

    results = searcher.search(
        "escape of water", filters={"source_type": "policy"}, limit=10
    )

    assert all(item.source_type == "scanned_pdf" for item in results)


# ------------------------------------------------------------------ honest failure


def test_searching_an_empty_index_raises(searcher) -> None:
    with pytest.raises(EmptyIndexError, match="index is empty"):
        searcher.search("ruptured pipe")


def test_an_empty_query_is_rejected(searcher, store, embedder) -> None:
    _populate(store, embedder, SCANNED_CHUNKS)

    with pytest.raises(ValueError, match="query must not be empty"):
        searcher.search("  ")


def test_a_scanned_chunk_with_no_page_fails_loudly(searcher, store, embedder) -> None:
    """A scan cited without a page is evidence nobody can go and check."""
    _populate(
        store,
        embedder,
        [_chunk("scanned/CLM-1005_INSPECTION.pdf:0", "Findings text.", page=None)],
    )

    with pytest.raises(ValueError, match="CLM-1005_INSPECTION.pdf:0"):
        searcher.search("findings")


# -------------------------------------------------- end to end over the real scans


@pytest.fixture
def indexed(tmp_path: Path, embedder) -> ScannedSearcher:
    """OCR the three committed image-only fixtures and return a searcher over them."""
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")
    index_scanned_corpus(
        SCANS,
        store,
        embedder,
        manifest_path=tmp_path / "manifest.json",
        settings=get_settings().model_copy(update={"ocr_vision_escalation": False}),
    )
    return ScannedSearcher(store, embedder, bm25_path=tmp_path / "bm25")


@pytest.mark.ocr
def test_an_image_only_pdf_becomes_evidence_cited_by_page(indexed) -> None:
    """The C-4 acceptance: zero extractable text in, a checkable citation out."""
    results = indexed.search("sudden rupture of a copper pipe", claim_id="CLM-1001")

    assert results
    assert all(item.locator.page >= 1 for item in results)
    assert all(item.locator.ocr_engine == "rapidocr" for item in results)
    assert any("rupture" in item.content.lower() for item in results)


@pytest.mark.ocr
def test_the_counter_evidence_scan_is_retrievable_and_qualified(indexed) -> None:
    """Gradual seepage is the deliberate counter-evidence; it must not be smoothed away."""
    results = indexed.search("gradual seepage over several months", claim_id="CLM-1002")

    assert results
    assert any("gradual" in item.content.lower() for item in results)


@pytest.mark.ocr
def test_the_ruined_scan_offers_a_refusal_rather_than_a_transcription(indexed) -> None:
    results = indexed.search("inspection findings", claim_id="CLM-1003")

    assert results
    assert all(item.content == UNREADABLE_PAGE_TEXT for item in results)
    assert all(item.confidence == 0.0 for item in results)
