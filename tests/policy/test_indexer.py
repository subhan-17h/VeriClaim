"""Incremental indexing: change detection, removal, self-healing, and loud failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vericlaim.policy.indexer import ZeroChunkError, index_corpus
from vericlaim.policy.manifest import file_content_hash, load_manifest, save_manifest
from vericlaim.policy.store import ChunkStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "policies"

WORDING = """SECTION 4 — WATER DAMAGE
4.1 This section applies to water escaping within the insured premises.
4.2 Sudden and accidental escape of water from a fixed plumbing system is covered.
SECTION 5 — EXCLUSIONS
5.1 Loss caused by gradual leakage over a period of time is excluded.
"""


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "wording.txt").write_text(WORDING, encoding="utf-8")
    return docs


@pytest.fixture
def store(tmp_path: Path) -> ChunkStore:
    return ChunkStore(path=tmp_path / "chroma", collection_name="test")


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "manifest.json"


def _index(corpus: Path, store: ChunkStore, embedder, manifest_path: Path, **kwargs):
    return index_corpus(
        corpus,
        store,
        embedder,
        manifest_path=manifest_path,
        chunk_size=400,
        chunk_overlap=60,
        **kwargs,
    )


# ------------------------------------------------------------------- manifest I/O


def test_a_missing_manifest_reads_as_empty(tmp_path: Path) -> None:
    assert load_manifest(tmp_path / "absent.json") == {}


def test_corrupt_json_reads_as_empty(tmp_path: Path) -> None:
    """An empty manifest drives a full rebuild, so corruption is self-correcting."""
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_manifest(path) == {}


@pytest.mark.parametrize(
    "payload",
    [
        '["a", "b"]',
        '{"doc.pdf": {"hash": "abc"}}',
        '{"doc.pdf": {"hash": 1, "page_count": 2, "chunk_count": 3}}',
        '{"doc.pdf": {"hash": "abc", "page_count": -1, "chunk_count": 3}}',
        '{"doc.pdf": {"hash": "abc", "page_count": 2, "chunk_count": "many"}}',
        '{"doc.pdf": {"hash": "abc", "page_count": 2, "chunk_count": 3, "extra": 1}}',
    ],
    ids=[
        "not-a-map",
        "missing-key",
        "wrong-hash-type",
        "negative-pages",
        "text-count",
        "extra-key",
    ],
)
def test_a_malformed_record_invalidates_the_whole_manifest(
    tmp_path: Path, payload: str
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(payload, encoding="utf-8")

    assert load_manifest(path) == {}


def test_a_valid_manifest_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = {"policies/a.pdf": {"hash": "abc", "page_count": 5, "chunk_count": 29}}

    save_manifest(path, manifest)

    assert load_manifest(path) == manifest


def test_a_null_page_count_is_valid(tmp_path: Path) -> None:
    """Plain-text documents have no pages; that is not corruption."""
    path = tmp_path / "manifest.json"
    manifest = {"notes.txt": {"hash": "abc", "page_count": None, "chunk_count": 2}}

    save_manifest(path, manifest)

    assert load_manifest(path) == manifest


def test_saving_leaves_no_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"

    save_manifest(path, {"a.pdf": {"hash": "x", "page_count": 1, "chunk_count": 1}})

    assert [entry.name for entry in tmp_path.iterdir()] == ["manifest.json"]


def test_saving_preserves_the_previous_file_on_failure(tmp_path: Path) -> None:
    """An interrupted write must not truncate the manifest in place."""
    path = tmp_path / "manifest.json"
    save_manifest(path, {"a.pdf": {"hash": "x", "page_count": 1, "chunk_count": 1}})
    original = path.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        save_manifest(path, {"b.pdf": object()})  # type: ignore[dict-item]

    assert path.read_text(encoding="utf-8") == original


def test_content_hash_tracks_bytes_not_mtime(tmp_path: Path) -> None:
    """A checkout rewrites mtimes; re-indexing an unchanged corpus is the mistake."""
    path = tmp_path / "doc.txt"
    path.write_text("body", encoding="utf-8")
    before = file_content_hash(path)

    path.touch()
    assert file_content_hash(path) == before

    path.write_text("changed", encoding="utf-8")
    assert file_content_hash(path) != before


# ------------------------------------------------------------- indexing lifecycle


def test_a_first_run_adds_every_document(corpus, store, embedder, manifest_path) -> None:
    result = _index(corpus, store, embedder, manifest_path)

    assert result.added == 1
    assert result.skipped == 0
    assert result.chunks_created > 0
    assert result.changed is True


def test_an_unchanged_rerun_skips_everything(corpus, store, embedder, manifest_path) -> None:
    _index(corpus, store, embedder, manifest_path)
    embedder.document_calls.clear()

    result = _index(corpus, store, embedder, manifest_path)

    assert (result.added, result.updated, result.skipped) == (0, 0, 1)
    assert result.changed is False
    # The expensive half of indexing must not run for an unchanged document.
    assert embedder.document_calls == []


def test_a_changed_document_is_reindexed(corpus, store, embedder, manifest_path) -> None:
    _index(corpus, store, embedder, manifest_path)
    (corpus / "wording.txt").write_text(WORDING + "\n5.2 Wear and tear is excluded.\n", "utf-8")

    result = _index(corpus, store, embedder, manifest_path)

    assert (result.added, result.updated, result.skipped) == (0, 1, 0)


def test_reindexing_replaces_rather_than_accumulates(
    corpus, store, embedder, manifest_path
) -> None:
    """Without a delete before add, a shortened document keeps its removed clauses."""
    _index(corpus, store, embedder, manifest_path)
    (corpus / "wording.txt").write_text("SECTION 4 — WATER DAMAGE\n4.1 Only this.\n", "utf-8")

    _index(corpus, store, embedder, manifest_path)

    texts = " ".join(chunk.text for chunk in store.all_chunks())
    assert "gradual leakage" not in texts


def test_a_deleted_file_removes_its_chunks(corpus, store, embedder, manifest_path) -> None:
    (corpus / "second.txt").write_text("SECTION 9 — GENERAL\n9.1 Body.\n", encoding="utf-8")
    _index(corpus, store, embedder, manifest_path)

    (corpus / "second.txt").unlink()
    result = _index(corpus, store, embedder, manifest_path)

    assert result.removed == 1
    assert store.document_ids() == ["wording.txt"]
    assert "second.txt" not in load_manifest(manifest_path)


def test_a_new_document_is_added_beside_existing_ones(
    corpus, store, embedder, manifest_path
) -> None:
    _index(corpus, store, embedder, manifest_path)
    (corpus / "second.txt").write_text("SECTION 9 — GENERAL\n9.1 Body.\n", encoding="utf-8")

    result = _index(corpus, store, embedder, manifest_path)

    assert (result.added, result.skipped) == (1, 1)
    assert result.documents_indexed == 2


def test_force_reindexes_unchanged_documents(corpus, store, embedder, manifest_path) -> None:
    _index(corpus, store, embedder, manifest_path)

    result = _index(corpus, store, embedder, manifest_path, force=True)

    assert (result.added, result.skipped) == (1, 0)


def test_nested_directories_are_indexed_by_relative_path(
    tmp_path, store, embedder, manifest_path
) -> None:
    """The identity that makes a per-claim corpus indexable, end to end."""
    docs = tmp_path / "docs"
    for claim in ("CLM-1001", "CLM-1002"):
        (docs / "claims" / claim).mkdir(parents=True)
        (docs / "claims" / claim / "estimate.txt").write_text(
            f"SECTION 1 — ESTIMATE\n1.1 Estimate for claim {claim}.\n", encoding="utf-8"
        )

    result = _index(docs, store, embedder, manifest_path)

    assert result.added == 2
    assert store.document_ids() == [
        "claims/CLM-1001/estimate.txt",
        "claims/CLM-1002/estimate.txt",
    ]


def test_progress_is_reported_per_document(corpus, store, embedder, manifest_path) -> None:
    messages: list[str] = []

    _index(corpus, store, embedder, manifest_path, on_progress=messages.append)

    assert any("Parsing document" in message for message in messages)
    assert any("Embedding" in message for message in messages)


# ---------------------------------------------------------------- self-healing


def test_a_manifest_describing_absent_chunks_triggers_a_rebuild(
    corpus, store, embedder, manifest_path
) -> None:
    """Manifest and collection are separate state; a crash between them desynchronises."""
    _index(corpus, store, embedder, manifest_path)
    store.reset()  # collection emptied behind the manifest's back

    result = _index(corpus, store, embedder, manifest_path)

    assert result.added == 1
    assert store.count() > 0


def test_a_wrong_chunk_count_triggers_a_rebuild(
    corpus, store, embedder, manifest_path
) -> None:
    _index(corpus, store, embedder, manifest_path)
    manifest = load_manifest(manifest_path)
    manifest["wording.txt"]["chunk_count"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _index(corpus, store, embedder, manifest_path)

    assert result.added == 1
    assert result.skipped == 0


def test_a_consistent_index_is_not_rebuilt(corpus, store, embedder, manifest_path) -> None:
    _index(corpus, store, embedder, manifest_path)

    messages: list[str] = []
    _index(corpus, store, embedder, manifest_path, on_progress=messages.append)

    assert not any("Rebuilding" in message for message in messages)


# ------------------------------------------------------------------ loud failure


def test_a_paged_document_producing_no_chunks_raises(
    tmp_path, store, embedder, manifest_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reference implementation records chunk_count 0 and never revisits it.

    An image-only PDF reaching a parser that does not run OCR extracts nothing. That
    must not be indistinguishable from a document that genuinely says nothing.
    """
    from vericlaim.policy import indexer as indexer_module
    from vericlaim.policy.models import Document

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "scanned.pdf").write_bytes((FIXTURES / "HomeSecure_Plus_2026.pdf").read_bytes())

    class TextlessParser:
        extensions = (".pdf",)

        def parse(self, path: Path) -> Document:
            return Document(name=path.name, path=path, text="", pages=["", ""], page_count=2)

    monkeypatch.setattr(
        indexer_module, "get_parser", lambda path, pdf_parser=None: TextlessParser()
    )

    with pytest.raises(ZeroChunkError, match="2 pages but produced no chunks"):
        _index(docs, store, embedder, manifest_path)


def test_an_empty_text_file_does_not_raise(tmp_path, store, embedder, manifest_path) -> None:
    """A text file has no pages, so an empty one is genuinely empty, not a failure."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "empty.txt").write_text("", encoding="utf-8")

    result = _index(docs, store, embedder, manifest_path)

    assert result.added == 1
    assert result.chunks_created == 0


# ------------------------------------------------------------- against the fixtures


def test_the_committed_policy_corpus_indexes(tmp_path, embedder) -> None:
    store = ChunkStore(path=tmp_path / "chroma", collection_name="test")

    result = index_corpus(
        FIXTURES,
        store,
        embedder,
        manifest_path=tmp_path / "manifest.json",
        chunk_size=400,
        chunk_overlap=60,
        pdf_parser="pypdf",
    )

    assert result.added == 3
    assert result.chunks_created > 30
    assert set(store.document_ids()) == {
        "Exclusions_Schedule_2026.pdf",
        "HomeSecure_Plus_2026.pdf",
        "Landlord_Protect_2026.pdf",
    }


# ------------------------------------------------------- the processor seam (C-4.6)


def test_a_supplied_processor_replaces_parsing_and_chunking(
    corpus, store, embedder, manifest_path
) -> None:
    """The scanned source reuses this loop; only how text is obtained differs."""
    from vericlaim.policy.indexer import ProcessedDocument
    from vericlaim.policy.models import Chunk, content_hash

    seen: list[tuple[Path, str]] = []

    def processor(path, doc_id, *, chunk_size, chunk_overlap):
        seen.append((path, doc_id))
        text = "Recovered by a different pipeline entirely."
        return ProcessedDocument(
            page_count=1,
            chunks=[
                Chunk(
                    id=f"{doc_id}:0",
                    text=text,
                    doc_id=doc_id,
                    doc_name=path.name,
                    source_type="scanned_pdf",
                    page=1,
                    content_hash=content_hash(text),
                )
            ],
        )

    result = _index(corpus, store, embedder, manifest_path, processor=processor)

    assert seen == [(corpus / "wording.txt", "wording.txt")]
    assert result.chunks_created == 1
    assert store.all_chunks()[0].source_type == "scanned_pdf"


def test_the_zero_chunk_guard_still_covers_a_supplied_processor(
    corpus, store, embedder, manifest_path
) -> None:
    """The guard is the loop's, not the policy chunker's; a silent OCR failure must trip it."""
    from vericlaim.policy.indexer import ProcessedDocument

    def processor(path, doc_id, *, chunk_size, chunk_overlap):
        return ProcessedDocument(page_count=4, chunks=[])

    with pytest.raises(ZeroChunkError, match="4 pages"):
        _index(corpus, store, embedder, manifest_path, processor=processor)


# ----------------------------------------------------- two sources, one collection


class TestASharedCollection:
    """Policy wordings and scanned paperwork live in one collection, separated by
    ``source_type`` metadata and each tracked by its own manifest.

    Until C-8.6 the consistency check compared a manifest against the *whole*
    collection, so each pass saw the other source's documents as corruption. The
    consequence was not a slow re-index: the rebuild wiped the collection, so the two
    passes deleted each other's chunks on every run while reporting success.
    """

    @pytest.fixture
    def second_corpus(self, tmp_path: Path) -> Path:
        docs = tmp_path / "other-docs"
        docs.mkdir()
        (docs / "report.txt").write_text("SECTION 1\nAn unrelated document.\n", encoding="utf-8")
        return docs

    def _both(self, corpus, second_corpus, store, embedder, tmp_path, **kwargs):
        first = _index(corpus, store, embedder, tmp_path / "one.json", **kwargs)
        second = _index(
            second_corpus, store, embedder, tmp_path / "two.json",
            source_type="scanned_pdf", **kwargs,
        )
        return first, second

    def test_the_second_pass_keeps_the_first_pass_chunks(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        self._both(corpus, second_corpus, store, embedder, tmp_path)
        stored = set(store.document_ids())

        assert "wording.txt" in stored
        assert "report.txt" in stored

    def test_a_re_run_skips_every_document_in_both_sources(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        self._both(corpus, second_corpus, store, embedder, tmp_path)
        first, second = self._both(corpus, second_corpus, store, embedder, tmp_path)

        assert (first.added, first.updated, first.skipped) == (0, 0, 1)
        assert (second.added, second.updated, second.skipped) == (0, 0, 1)
        assert not first.changed and not second.changed

    def test_each_pass_counts_only_its_own_chunks(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        """store.count() is the whole shared collection, which is not this pass's work."""
        first, second = self._both(corpus, second_corpus, store, embedder, tmp_path)

        assert first.chunks_created + second.chunks_created == store.count()
        assert first.chunks_created > 0
        assert second.chunks_created > 0

    def test_forcing_one_source_leaves_the_other_intact(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        self._both(corpus, second_corpus, store, embedder, tmp_path)
        _index(corpus, store, embedder, tmp_path / "one.json", force=True)

        assert "report.txt" in set(store.document_ids())

    def test_deleting_a_file_removes_only_its_chunks(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        self._both(corpus, second_corpus, store, embedder, tmp_path)
        (second_corpus / "report.txt").unlink()

        result = _index(
            second_corpus, store, embedder, tmp_path / "two.json", source_type="scanned_pdf"
        )

        assert result.removed == 1
        assert set(store.document_ids()) == {"wording.txt"}

    def test_a_genuinely_missing_document_still_triggers_a_rebuild(
        self, corpus, second_corpus, store, embedder, tmp_path
    ) -> None:
        """Scoping the check must not blind it to the case it exists for."""
        self._both(corpus, second_corpus, store, embedder, tmp_path)
        store.delete_document("wording.txt")

        result = _index(corpus, store, embedder, tmp_path / "one.json")

        assert result.added == 1
        assert "report.txt" in set(store.document_ids())
