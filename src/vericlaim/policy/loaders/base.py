"""Structural interface shared by document parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from vericlaim.policy.models import Document


@runtime_checkable
class DocumentParser(Protocol):
    """Allow new document formats to slot into ingestion without changing callers."""

    extensions: tuple[str, ...]

    def parse(self, path: Path) -> Document: ...
