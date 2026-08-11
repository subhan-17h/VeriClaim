"""Document parser registry and directory traversal.

The registry exists so that adding a format is a registration, not an edit to every
caller. Extension dispatch is data; the indexer never branches on file type.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from vericlaim.config import get_settings
from vericlaim.policy.loaders.base import DocumentParser
from vericlaim.policy.loaders.docling_parser import (
    ConverterOptions,
    DoclingParser,
    DoclingSetupError,
)
from vericlaim.policy.loaders.pdf import PdfParser
from vericlaim.policy.loaders.text import TextParser
from vericlaim.policy.models import Document

__all__ = (
    "ConverterOptions",
    "DoclingParser",
    "DoclingSetupError",
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
_PDF_EXTENSION = ".pdf"
_KNOWN_EXTENSIONS = frozenset(_PARSERS_BY_EXTENSION) | {_PDF_EXTENSION}


def supported_extensions() -> frozenset[str]:
    """Return every extension the registry can parse."""
    return _KNOWN_EXTENSIONS


def get_parser(path: Path, *, pdf_parser: str | None = None) -> DocumentParser | None:
    """Return the registered parser for a path, or None if unsupported.

    PDFs are the one format with two implementations. Which one serves them is
    configuration (``VC_PDF_PARSER``), not a property of the extension, so the choice
    is resolved here rather than baked into the extension table. ``pdf_parser``
    overrides that setting, which is what lets a caller compare the two.
    """
    if path.suffix.lower() == _PDF_EXTENSION:
        choice = pdf_parser if pdf_parser is not None else get_settings().pdf_parser
        return DoclingParser() if choice == "docling" else PdfParser()
    return _PARSERS_BY_EXTENSION.get(path.suffix.lower())


def iter_document_paths(docs_dir: Path) -> Iterator[Path]:
    """Yield supported file paths recursively in deterministic order.

    Sorted so that indexing two runs over an unchanged corpus produces identical
    chunk ids -- an evaluation that compares citations across commits depends on it.
    Extension membership alone decides inclusion, so this never builds a parser and
    therefore never touches Docling's model weights.
    """
    for path in sorted(docs_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in _KNOWN_EXTENSIONS:
            yield path


def iter_documents(docs_dir: Path, *, pdf_parser: str | None = None) -> Iterator[Document]:
    """Yield supported documents recursively in deterministic path order."""
    for path in iter_document_paths(docs_dir):
        parser = get_parser(path, pdf_parser=pdf_parser)
        if parser is not None:
            yield parser.parse(path)
