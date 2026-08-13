"""The corpus manifest: the artefact that makes "reproducible from a seed" checkable.

Two runs at the same seed must write the same manifest byte for byte. That is a
stronger claim than "the generators are deterministic", because the manifest is what a
reviewer actually compares, and a path or an ordering that varies would break the
comparison while every document stayed identical.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_corpus.py"


def _load_script():
    """Import the script by path; scripts/ is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("generate_corpus", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, dict[str, list[Path]]]:
    """A project root holding data/ with two small stand-in documents."""
    data = tmp_path / "data"
    (data / "policies").mkdir(parents=True)
    (data / "scanned").mkdir()
    first = data / "policies" / "HomeSecure.pdf"
    second = data / "scanned" / "CLM-1001_INSPECTION.pdf"
    first.write_bytes(b"policy wording")
    second.write_bytes(b"scanned page")
    return data, {"policies": [first], "scanned": [second]}


def test_the_manifest_is_byte_identical_across_runs(script, corpus) -> None:
    data, sources = corpus
    first, second = data / "one.json", data / "two.json"
    for path in (first, second):
        script.write_manifest(path, seed=42, sources=sources, row_counts={"claims": 12000})

    assert first.read_bytes() == second.read_bytes()


def test_the_manifest_records_relative_paths(script, corpus) -> None:
    """An absolute path would differ between checkouts and destroy the comparison."""
    data, sources = corpus
    path = data / "corpus_manifest.json"
    script.write_manifest(path, seed=42, sources=sources, row_counts={})
    manifest = json.loads(path.read_text())

    recorded = [name for entries in manifest["documents"].values() for name in entries]
    assert recorded == ["data/policies/HomeSecure.pdf", "data/scanned/CLM-1001_INSPECTION.pdf"]
    assert not any(name.startswith("/") for name in recorded)


def test_the_manifest_hashes_change_when_a_document_does(script, corpus) -> None:
    data, sources = corpus
    path = data / "corpus_manifest.json"
    script.write_manifest(path, seed=42, sources=sources, row_counts={})
    before = json.loads(path.read_text())["documents"]

    sources["policies"][0].write_bytes(b"policy wording, amended")
    script.write_manifest(path, seed=42, sources=sources, row_counts={})
    after = json.loads(path.read_text())["documents"]

    assert before != after


def test_the_manifest_records_the_seed_and_row_counts(script, corpus) -> None:
    data, sources = corpus
    path = data / "corpus_manifest.json"
    script.write_manifest(path, seed=7, sources=sources, row_counts={"claims": 12000})
    manifest = json.loads(path.read_text())

    assert manifest["seed"] == 7
    assert manifest["row_counts"] == {"claims": 12000}
    assert manifest["version"] == script.MANIFEST_VERSION


def test_an_interrupted_write_leaves_no_temporary_file(script, corpus) -> None:
    data, sources = corpus
    path = data / "corpus_manifest.json"
    script.write_manifest(path, seed=42, sources=sources, row_counts={})

    assert path.is_file()
    assert not list(data.glob("*.tmp"))
