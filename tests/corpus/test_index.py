"""The loader: three existing ingest paths, one set of connections, two manifests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from vericlaim.config import Settings
from vericlaim.corpus.index import load_corpus
from vericlaim.corpus.pdf import rasterise, render_text_pdf
from vericlaim.policy.models import Document
from vericlaim.policy.store import ChunkStore
from vericlaim.scanned.classifier import DocumentProfile, PageProfile
from vericlaim.scanned.docling_ocr import OcrResult

WORDING = """SECTION 4 — WATER DAMAGE
4.2 Sudden and accidental escape of water from a fixed plumbing system is covered.
SECTION 5 — EXCLUSIONS
5.1 Loss caused by gradual leakage over a period of time is excluded.
"""

REPORT = """NORTHSTAR INSURANCE LIMITED
PROPERTY INSPECTION REPORT
Claim Reference: CLM-1001
The inspection recorded staining consistent with a burst supply line.
"""


class FakeOcrParser:
    """Returns fixed text at a confidence above the floor, so no page escalates.

    The scanned pipeline insists on a real PDF, so the fixture writes one; what it must
    never do here is read it, because that would put OCR weights and provider quota
    behind a test about which manifest each pass writes.
    """

    engine = "rapidocr"

    def parse_with_confidence(self, path: Path) -> OcrResult:
        return OcrResult(
            document=Document(
                name=path.name,
                path=path,
                text=REPORT,
                pages=[REPORT],
                page_count=1,
                page_confidences=[0.92],
            ),
            profile=DocumentProfile(
                path=path,
                pages=(PageProfile(page=1, char_count=0, area=500000.0, kind="scanned"),),
            ),
            engine=self.engine,
        )


class FakeDatabase:
    """Stands in for the pool. The sheets pass is proved against real Postgres in
    tests/sheets; here it only has to not be reached for the wrong reason."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:  # pragma: no cover - exercised only if the loader closes it
        self.closed = True


@pytest.fixture
def corpus(tmp_path: Path) -> Settings:
    """A settings pointing at a two-document corpus, one per indexed source.

    The OCR parser is faked rather than run: what is under test is which manifest each
    pass reads and writes, not how a page is recognised, and real OCR would put model
    weights and provider quota behind a wiring test. No workbooks, so the sheets pass
    is a no-op and needs no database.
    """
    root = tmp_path / "data"
    (root / "policies").mkdir(parents=True)
    (root / "scanned").mkdir()
    (root / "spreadsheets").mkdir()
    (root / "policies" / "HomeSecure.txt").write_text(WORDING, encoding="utf-8")
    (root / "scanned" / "CLM-1001_INSPECTION.pdf").write_bytes(_scan())
    return Settings().model_copy(
        update={
            "data_dir": root,
            "policy_dir": root / "policies",
            "scanned_dir": root / "scanned",
            "spreadsheet_dir": root / "spreadsheets",
            "chroma_dir": tmp_path / "chroma",
            "policy_manifest_path": tmp_path / "manifest.policy.json",
            "scanned_manifest_path": tmp_path / "manifest.scanned.json",
        }
    )


@pytest.fixture
def store(corpus: Settings) -> ChunkStore:
    return ChunkStore(path=corpus.chroma_dir, collection_name="test")


def _load(corpus: Settings, store: ChunkStore, embedder, **kwargs):
    return load_corpus(
        settings=corpus,
        store=store,
        embedder=embedder,
        database=FakeDatabase(),
        parser=FakeOcrParser(),
        **kwargs,
    )


def _scan() -> bytes:
    """A real image-only PDF: the scanned pipeline refuses anything else."""
    pages = rasterise(render_text_pdf(REPORT.splitlines()), dpi=72)
    buffer = io.BytesIO()
    pages[0].convert("RGB").save(buffer, format="PDF")
    return buffer.getvalue()


def test_both_sources_land_in_the_shared_collection(corpus, store, embedder) -> None:
    report = _load(corpus, store, embedder)

    assert set(store.document_ids()) == {"HomeSecure.txt", "CLM-1001_INSPECTION.pdf"}
    assert report.policy.added == 1
    assert report.scanned.added == 1
    assert report.changed


def test_a_second_run_reports_everything_skipped(corpus, store, embedder) -> None:
    _load(corpus, store, embedder)
    report = _load(corpus, store, embedder)

    assert report.policy.skipped == 1
    assert report.scanned.skipped == 1
    assert (report.policy.added, report.scanned.added) == (0, 0)
    assert not report.changed


def test_deleting_a_document_removes_its_chunks(corpus, store, embedder) -> None:
    _load(corpus, store, embedder)
    (corpus.scanned_dir / "CLM-1001_INSPECTION.pdf").unlink()

    report = _load(corpus, store, embedder)

    assert report.scanned.removed == 1
    assert set(store.document_ids()) == {"HomeSecure.txt"}


def test_each_pass_writes_its_own_manifest(corpus, store, embedder) -> None:
    _load(corpus, store, embedder)

    assert corpus.policy_manifest_path.is_file()
    assert corpus.scanned_manifest_path.is_file()
    assert corpus.policy_manifest_path.read_bytes() != corpus.scanned_manifest_path.read_bytes()


def test_one_manifest_for_both_corpora_is_refused(corpus, store, embedder) -> None:
    """A default that happens to differ is not a guarantee; this is the guarantee.

    The damage a shared manifest does is silent -- the second pass deletes the first's
    chunks and reports a successful run -- so it has to fail before any work starts.
    """
    shared = corpus.model_copy(
        update={"scanned_manifest_path": corpus.policy_manifest_path}
    )

    with pytest.raises(ValueError, match="deletes every policy chunk"):
        _load(shared, store, embedder)

    assert store.count() == 0


def test_the_default_manifest_paths_are_distinct() -> None:
    settings = Settings()

    assert settings.policy_manifest_path != settings.scanned_manifest_path
