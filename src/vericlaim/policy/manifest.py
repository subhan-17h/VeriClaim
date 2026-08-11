"""The record of what has been indexed, and from which bytes.

Incremental indexing is not an optimisation here. A full corpus rebuild re-runs
Docling layout analysis over every document, which for the scanned source in C-4 means
OCR over dozens of pages; without a manifest, every restart pays that again.

Documents are keyed by their corpus-relative POSIX path. Keying on the basename --
what the reference implementation does -- forces a uniqueness constraint the per-claim
corpus cannot satisfy, since claims/CLM-1001/estimate.pdf and
claims/CLM-1002/estimate.pdf are different documents with the same name.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypedDict

__all__ = (
    "ManifestRecord",
    "file_content_hash",
    "load_manifest",
    "save_manifest",
)

_HASH_BLOCK_SIZE = 1024 * 1024


class ManifestRecord(TypedDict):
    """Persisted source identity and exact indexed-document statistics."""

    hash: str
    page_count: int | None
    chunk_count: int


def file_content_hash(path: Path) -> str:
    """Return a SHA-256 digest of a file's bytes without loading it all into memory.

    Content, not mtime: a checkout or a copy rewrites mtimes on files that have not
    changed, and re-indexing an unchanged corpus is the expensive mistake.
    """
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(_HASH_BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()


def _is_valid_record(record: object) -> bool:
    """Return whether one manifest entry has exactly the expected shape."""
    if not isinstance(record, dict) or set(record) != {"hash", "page_count", "chunk_count"}:
        return False
    page_count = record["page_count"]
    return (
        isinstance(record["hash"], str)
        and (page_count is None or (type(page_count) is int and page_count >= 0))
        and type(record["chunk_count"]) is int
        and record["chunk_count"] >= 0
    )


def load_manifest(path: Path) -> dict[str, ManifestRecord]:
    """Load a manifest, treating unreadable or invalid data as empty.

    Returning ``{}`` rather than raising is deliberate: an empty manifest means
    "nothing is known to be indexed", which drives a full, correct rebuild. A corrupt
    manifest file must therefore be recoverable by re-running indexing, not by hand.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if any(
        not isinstance(key, str) or not _is_valid_record(record)
        for key, record in data.items()
    ):
        return {}
    return data


def save_manifest(path: Path, manifest: dict[str, ManifestRecord]) -> None:
    """Atomically replace the manifest with deterministic JSON.

    Written to a temporary file in the same directory and renamed, so an interrupted
    write leaves the previous manifest intact rather than a truncated one. A truncated
    manifest silently drops documents from the index's memory of itself.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(manifest, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
