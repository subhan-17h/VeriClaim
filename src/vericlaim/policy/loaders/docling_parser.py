"""Page-preserving PDF parsing with Docling's structural layout model.

Docling reads a PDF's layout with a document model rather than its text stream, so it
recovers headings, reading order, and table structure that a text extractor flattens.
For an insurance wording -- where the clause number *is* the citation -- that
structure is what makes a chunk locatable.

Page provenance is recovered by exporting the whole document once with a unique
placeholder at each page break and splitting on it. The alternative, exporting page by
page, re-runs layout analysis per page and loses cross-page reading order.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from vericlaim.config import get_settings
from vericlaim.policy.models import Document

__all__ = ("ConverterOptions", "DoclingParser", "DoclingSetupError")


class DoclingSetupError(RuntimeError):
    """Report an actionable remedy when Docling is not ready to run offline."""


@dataclass(frozen=True, slots=True)
class ConverterOptions:
    """The complete set of options that determine a converter's behaviour.

    This is the converter cache key, and it must stay complete. Keying the cache on
    the artifacts path alone -- as the reference implementation does -- is correct
    only while there is exactly one option set in the process. The moment C-4 asks for
    an OCR-enabled converter, a path-keyed cache hands back the digital-only converter
    built earlier and every scanned page silently extracts as empty.
    """

    artifacts_path: Path
    do_ocr: bool = False
    do_table_structure: bool = True
    do_cell_matching: bool = True
    ocr_engine: str = "rapidocr"
    # RapidOcrOptions.lang defaults to ["chinese"]. Never leave it implicit.
    ocr_lang: tuple[str, ...] = ("english",)


def _split_pages(markdown: str, placeholder: str, page_count: int) -> list[str]:
    """Split one Markdown export into exactly the number of converted PDF pages.

    Docling emits the placeholder *between* pages, so an N-page document yields N
    segments. Fewer means a page produced no output, which is padded. More means the
    placeholder leaked into content and every page number after it would be wrong --
    that is a hard failure, because a citation to the wrong page is worse than none.
    """
    pages = markdown.split(placeholder)
    if len(pages) < page_count:
        pages.extend("" for _ in range(page_count - len(pages)))
    elif len(pages) > page_count:
        raise ValueError(
            "Docling Markdown export produced "
            f"{len(pages)} page segments for a {page_count}-page PDF"
        )
    return pages


@cache
def build_converter(options: ConverterOptions) -> Any:
    """Build and cache one Docling converter per distinct option set.

    Cached because constructing a converter loads the layout and table models, which
    costs seconds and hundreds of megabytes.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise DoclingSetupError(
            "Docling is not installed. Run `uv sync`, or set VC_PDF_PARSER=pypdf "
            "to use the fallback parser."
        ) from exc

    artifacts_path = options.artifacts_path
    try:
        artifacts_available = artifacts_path.is_dir() and any(artifacts_path.iterdir())
    except OSError as exc:
        raise DoclingSetupError(
            f"Docling model artifacts are not readable at {artifacts_path}. Run "
            "`uv run python scripts/warm_models.py`, or set VC_PDF_PARSER=pypdf "
            "to use the fallback parser."
        ) from exc
    if not artifacts_available:
        raise DoclingSetupError(
            f"Docling model artifacts are missing or empty at {artifacts_path}. Run "
            "`uv run python scripts/warm_models.py`, or set VC_PDF_PARSER=pypdf "
            "to use the fallback parser."
        )

    pipeline_options = PdfPipelineOptions(
        artifacts_path=artifacts_path,
        do_ocr=options.do_ocr,
    )
    pipeline_options.do_table_structure = options.do_table_structure
    pipeline_options.table_structure_options.do_cell_matching = options.do_cell_matching
    if options.do_ocr:
        pipeline_options.ocr_options = _ocr_options(options)

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def _ocr_options(options: ConverterOptions) -> Any:
    """Return an explicitly configured OCR engine.

    Never ``OcrAutoOptions``: it probes the runtime and therefore resolves to a
    different engine on different machines, which makes extracted text -- and so every
    citation drawn from it -- environment-dependent.
    """
    if options.ocr_engine == "rapidocr":
        from docling.datamodel.pipeline_options import RapidOcrOptions

        return RapidOcrOptions(lang=list(options.ocr_lang))
    if options.ocr_engine == "easyocr":
        from docling.datamodel.pipeline_options import EasyOcrOptions

        return EasyOcrOptions(lang=list(options.ocr_lang))
    if options.ocr_engine == "tesseract":
        from docling.datamodel.pipeline_options import TesseractOcrOptions

        return TesseractOcrOptions(lang=list(options.ocr_lang))
    raise DoclingSetupError(f"Unsupported OCR engine: {options.ocr_engine!r}")


class DoclingParser:
    """Parse digital-native PDFs into structural Markdown with page provenance."""

    extensions = (".pdf",)

    def __init__(self, options: ConverterOptions | None = None) -> None:
        if options is None:
            settings = get_settings()
            options = ConverterOptions(
                artifacts_path=settings.docling_artifacts_path,
                ocr_engine=settings.ocr_engine,
                ocr_lang=settings.ocr_lang,
            )
        self._options = options

    def parse(self, path: Path) -> Document:
        """Return a page-preserving document produced by one Markdown export."""
        result = build_converter(self._options).convert(path)
        return self._to_document(path, result)

    def _to_document(self, path: Path, result: Any) -> Document:
        """Build a Document from a ConversionResult.

        The whole result is threaded through rather than ``result.document`` alone,
        because per-page OCR confidence lives on the result and is unreachable once it
        is discarded. C-4 reads it here.
        """
        doc = result.document
        placeholder = f"<!-- VERICLAIM_PAGE_BREAK_{uuid4().hex} -->"
        markdown = doc.export_to_markdown(
            page_break_placeholder=placeholder,
            escape_html=False,
            image_placeholder="",
        )
        pages = _split_pages(markdown, placeholder, len(doc.pages))
        return Document(
            name=path.name,
            path=path,
            text="\n\n".join(pages),
            pages=pages,
            page_count=len(pages),
        )
