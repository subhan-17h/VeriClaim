"""Document parser registry and directory traversal.

The registry exists so that adding a format is a registration, not an edit to every
caller. Extension dispatch is data; the indexer never branches on file type.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from vericlaim.policy.loaders.base import DocumentParser
from vericlaim.policy.loaders.pdf import PdfParser
from vericlaim.policy.loaders.text import TextParser
from vericlaim.policy.models import Document

__all__ = (
    "DocumentParser",
    "PdfParser",
    "TextParser",
    "get_parser",
    "iter_document_paths",
    "iter_documents",
    "supported_extensions",
)

_PARSERS: tuple[DocumentParser, ...] = (TextParser(), PdfParser())
_PARSERS_BY_EXTENSION: dict[str, DocumentParser] = {
    extension.lower(): parser for parser in _PARSERS for extension in parser.extensions
}


def supported_extensions() -> frozenset[str]:
    """Return every extension the registry can parse."""
    return frozenset(_PARSERS_BY_EXTENSION)


def get_parser(path: Path) -> DocumentParser | None:
    """Return the registered parser for a path, or None if unsupported."""
    return _PARSERS_BY_EXTENSION.get(path.suffix.lower())


def iter_document_paths(docs_dir: Path) -> Iterator[Path]:
    """Yield supported file paths recursively in deterministic order.

    Sorted so that indexing two runs over an unchanged corpus produces identical
    chunk ids -- an evaluation that compares citations across commits depends on it.
    """
    for path in sorted(docs_dir.rglob("*")):
        if path.is_file() and get_parser(path) is not None:
            yield path


def iter_documents(docs_dir: Path) -> Iterator[Document]:
    """Yield supported documents recursively in deterministic path order."""
    for path in iter_document_paths(docs_dir):
        parser = get_parser(path)
        if parser is not None:
            yield parser.parse(path)
