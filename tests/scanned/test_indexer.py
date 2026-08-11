"""Indexing scanned claim files: OCR in, citable chunks out, nothing invented.

The policy loop is reused deliberately, so these tests concentrate on what the
scanned path adds to it -- OCR confidence, the claim reference, escalation marking --
rather than re-testing change detection and removal, which C-3 already covers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vericlaim.config import get_settings
from vericlaim.policy.models import Document
from vericlaim.policy.store import ChunkStore
from vericlaim.scanned.classifier import DocumentProfile, PageProfile
from vericlaim.scanned.docling_ocr import OcrResult
from vericlaim.scanned.indexer import (
    ScannedProcessor,
    extract_claim_id,
    index_scanned_corpus,
)

SCANS = Path(__file__).parents[1] / "fixtures" / "scanned"


class FakeOcrParser:
    """Stands in for Docling so the pipeline can be tested without weights.

    Returns whatever pages and confidences a test asks for, which is what makes the
    escalation and confidence paths reachable in an offline run.
    """

    engine = "rapidocr"

    def __init__(self, pages: list[str], confidences: list[float]) -> None:
        self._pages = pages
        self._confidences = confidences
        self.parsed: list[Path] = []

    def parse_with_confidence(self, path: Path) -> OcrResult:
        self.parsed.append(path)
        document = Document(
            name=path.name,
            path=path,
            text="\n\n".join(self._pages),
            pages=list(self._pages),
            page_count=len(self._pages),
            page_confidences=list(self._confidences),
        )
        profile = DocumentProfile(
            path=path,
            pages=tuple(
                PageProfile(page=number, char_count=0, area=500000.0, kind="scanned")
                for number in range(1, len(self._pages) + 1)
            ),
        )
        return OcrResult(document=document, profile=profile, engine=self.engine)


def _write_pdf(path: Path, pages: int = 2) -> None:
    """Write a real image-only PDF.

    Real rather than a stub because escalation renders the page it is escalating; a
    fake parser can invent the text, but the file underneath still has to be a PDF.
    """
    from PIL import Image

    first, *rest = [Image.new("RGB", (620, 877), "white") for _ in range(pages)]
    first.save(path, format="PDF", save_all=bool(rest), append_images=rest)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    docs = tmp_path / "scanned"
    docs.mkdir()
    _write_pdf(docs / "CLM-1001_INSPECTION.pdf")
    return docs


@pytest.fixture
def store(tmp_path: Path) -> ChunkStore:
    return ChunkStore(path=tmp_path / "chroma", collection_name="test")


def _process(processor: ScannedProcessor, path: Path, doc_id: str):
    return processor(path, doc_id, chunk_size=400, chunk_overlap=60)


# ------------------------------------------------------------- the claim reference


def test_the_claim_reference_is_read_from_a_directory() -> None:
    assert extract_claim_id("claims/CLM-1001/estimate.pdf") == "CLM-1001"


def test_the_claim_reference_is_read_from_a_filename() -> None:
    assert extract_claim_id("scanned/CLM-1088_INSPECTION.pdf") == "CLM-1088"


def test_a_path_naming_no_claim_yields_none() -> None:
    """Guessing would file a document under a matter it has nothing to do with."""
    assert extract_claim_id("scanned/inspection_report.pdf") is None


def test_the_claim_reference_never_comes_from_the_recognised_text(corpus) -> None:
    """An OCR error in that line would re-key the document to the wrong claim."""
    parser = FakeOcrParser(["Claim Reference: CLM-9999\nThe pipe failed."], [0.9])
    processor = ScannedProcessor(parser=parser, settings=_offline_settings())

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    assert {chunk.claim_id for chunk in processed.chunks} == {"CLM-1001"}


# ------------------------------------------------------------------ what a chunk carries


def _offline_settings():
    return get_settings().model_copy(update={"ocr_vision_escalation": False})


def test_chunks_carry_the_page_confidence_and_engine(corpus) -> None:
    parser = FakeOcrParser(
        [
            "The copper supply pipe failed at a soldered joint.",
            "The ceiling below shows established water staining.",
        ],
        [0.94, 0.41],
    )
    processor = ScannedProcessor(parser=parser, settings=_offline_settings())

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    by_page = {chunk.page: chunk for chunk in processed.chunks}
    assert by_page[1].ocr_confidence == pytest.approx(0.94)
    assert by_page[2].ocr_confidence == pytest.approx(0.41)
    assert by_page[1].ocr_engine == "rapidocr"


def test_the_page_count_reaches_the_zero_chunk_guard(corpus) -> None:
    """The guard needs the page count from OCR, not from a chunk that may not exist."""
    parser = FakeOcrParser(["Findings recorded on the page."], [0.9])
    processor = ScannedProcessor(parser=parser, settings=_offline_settings())

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    assert processed.page_count == 1


def test_a_non_pdf_in_the_scanned_corpus_is_rejected(tmp_path: Path) -> None:
    """Silently skipping it would report a document as indexed that never was."""
    path = tmp_path / "notes.txt"
    path.write_text("plain text", encoding="utf-8")
    processor = ScannedProcessor(
        parser=FakeOcrParser(["x"], [0.9]), settings=_offline_settings()
    )

    with pytest.raises(ValueError, match="notes.txt"):
        _process(processor, path, "notes.txt")


# ----------------------------------------------------------------------- escalation


class FakeGateway:
    """A vision tier that reads every page it is shown."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_vision(self, task, prompt, images, *, schema=None, temperature=0.0):
        from vericlaim.gateway.types import Completion, Usage

        self.calls += 1
        return Completion(
            task=task,
            provider="fake",
            model="fake-vision",
            text="{}",
            parsed={
                "legible": True,
                "text": "Recovered on a second reading: the joint had ruptured.",
                "confidence": 0.9,
                "notes": "",
            },
            usage=Usage(10, 10),
            cost_usd=0.0,
            latency_ms=1.0,
        )


def test_only_the_escalated_page_is_flagged_on_its_chunks(corpus) -> None:
    parser = FakeOcrParser(
        ["A clean page needing no second reading at all.", "smeared"],
        [0.95, 0.10],
    )
    gateway = FakeGateway()
    processor = ScannedProcessor(
        parser=parser,
        settings=get_settings().model_copy(
            update={"ocr_vision_escalation": True, "ocr_confidence_floor": 0.6}
        ),
        gateway=gateway,
    )

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    by_page = {chunk.page: chunk for chunk in processed.chunks}
    assert gateway.calls == 1
    assert by_page[1].escalated is False
    assert by_page[2].escalated is True
    assert "second reading" in by_page[2].text


class RefusingGateway:
    """A vision tier that reports it cannot read the page."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_vision(self, task, prompt, images, *, schema=None, temperature=0.0):
        from vericlaim.gateway.types import Completion, Usage

        self.calls += 1
        return Completion(
            task=task,
            provider="fake",
            model="fake-vision",
            text="{}",
            parsed={
                "legible": False,
                "text": "",
                "confidence": 0.0,
                "notes": "Heavily blurred and obscured throughout.",
            },
            usage=Usage(10, 10),
            cost_usd=0.0,
            latency_ms=1.0,
        )


def test_a_page_the_vision_tier_refused_is_not_marked_vision_read(corpus) -> None:
    """The flag reaches the citation as "vision-assisted"; a refusal assisted nothing."""
    parser = FakeOcrParser(["smeared"], [0.10])
    gateway = RefusingGateway()
    processor = ScannedProcessor(
        parser=parser,
        settings=get_settings().model_copy(
            update={"ocr_vision_escalation": True, "ocr_confidence_floor": 0.6}
        ),
        gateway=gateway,
    )

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    assert gateway.calls == 1
    assert processed.chunks[0].escalated is False
    assert processed.chunks[0].ocr_confidence == pytest.approx(0.10)
    assert "smeared" in processed.chunks[0].text


def test_an_escalated_chunk_carries_the_capped_confidence(corpus) -> None:
    """A page that needed a second reading is never promoted to pristine."""
    parser = FakeOcrParser(["smeared"], [0.10])
    settings = get_settings().model_copy(
        update={"ocr_vision_escalation": True, "ocr_confidence_floor": 0.6}
    )
    processor = ScannedProcessor(
        parser=parser, settings=settings, gateway=FakeGateway()
    )

    processed = _process(
        processor, corpus / "CLM-1001_INSPECTION.pdf", "CLM-1001_INSPECTION.pdf"
    )

    assert processed.chunks[0].ocr_confidence == pytest.approx(
        settings.ocr_escalated_confidence_cap
    )


# ------------------------------------------------------------------ the whole loop


def test_the_corpus_indexes_into_the_shared_collection(corpus, store, embedder) -> None:
    parser = FakeOcrParser(["The supply pipe ruptured suddenly at the joint."], [0.88])

    result = index_scanned_corpus(
        corpus,
        store,
        embedder,
        manifest_path=corpus.parent / "manifest.json",
        settings=_offline_settings(),
        parser=parser,
    )

    stored = store.all_chunks()
    assert result.added == 1
    assert stored
    assert all(chunk.source_type == "scanned_pdf" for chunk in stored)
    assert all(chunk.claim_id == "CLM-1001" for chunk in stored)


def test_reindexing_an_unchanged_corpus_reruns_no_ocr(corpus, store, embedder) -> None:
    """OCR is seconds per page; re-reading an unchanged scan is the cost to avoid."""
    parser = FakeOcrParser(["The supply pipe ruptured suddenly at the joint."], [0.88])
    manifest = corpus.parent / "manifest.json"
    index_scanned_corpus(
        corpus,
        store,
        embedder,
        manifest_path=manifest,
        settings=_offline_settings(),
        parser=parser,
    )

    result = index_scanned_corpus(
        corpus,
        store,
        embedder,
        manifest_path=manifest,
        settings=_offline_settings(),
        parser=parser,
    )

    assert result.skipped == 1
    assert len(parser.parsed) == 1
