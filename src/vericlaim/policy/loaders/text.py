"""Plain-text document parser."""

from __future__ import annotations

from pathlib import Path

from vericlaim.policy.models import Document


class TextParser:
    """Load UTF-8 text files into the shared Document contract."""

    extensions = (".txt", ".md")

    def parse(self, path: Path) -> Document:
        # A stray invalid byte should not abort ingestion of the whole corpus.
        text = path.read_text(encoding="utf-8", errors="replace")
        return Document(name=path.name, path=path, text=text)
