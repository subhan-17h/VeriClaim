"""Read scanned PDFs with Docling's OCR pipeline, keeping per-page confidence.

Two things here are load-bearing and neither is obvious.

**The engine is named explicitly.** ``RapidOcrOptions.lang`` defaults to
``["chinese"]``, and Docling's ``OcrAutoOptions`` probes the runtime and resolves to a
different engine on different machines. Either default makes extracted text -- and so
every citation drawn from it -- depend on where the code ran.

**The ConversionResult is kept, not immediately dereferenced to ``.document``.**
Per-page OCR confidence lives on the result and is unreachable once it is discarded,
which is why the reference implementation, which reads ``.document`` inline, has no
route to confidence at all.

A measured warning about that confidence, from this project's fixtures:

===================================  ===========  ===================
Fixture                              ocr_score    Reality
===================================  ===========  ===================
Clean scan                           0.991        Read correctly
Degraded scan                        0.989        Real transcription errors
Largely illegible scan               ``NaN``      Nothing read at all
===================================  ===========  ===================

Two consequences. A confidence floor alone cannot separate the clean scan from the
degraded one -- ``ocr_score`` reports confidence in what OCR *found*, not coverage of
what was on the page. And the worst case does not arrive as a low number, it arrives
as ``NaN``: since ``nan < floor`` is ``False``, a naive threshold check would let the
single most degraded page through as though it had passed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from vericlaim.config import Settings, get_settings
from vericlaim.policy.loaders.docling_parser import (
    ConverterOptions,
    DoclingSetupError,
    _split_pages,
    build_converter,
)
from vericlaim.policy.models import Document
from vericlaim.scanned.classifier import DocumentProfile, profile_pdf

__all__ = ("OcrResult", "ScannedPdfParser", "page_confidence")

# A page whose OCR produced no cells at all. Docling reports NaN; we report the number
# that makes every downstream comparison behave -- a floor check, an average, a sort.
NO_OCR_CONFIDENCE = 0.0

# A page read from a real text layer rather than by OCR. Its text is exact.
DIGITAL_CONFIDENCE = 1.0


@dataclass(frozen=True, slots=True)
class OcrResult:
    """A parsed scanned document together with how well each page was read."""

    document: Document
    profile: DocumentProfile
    engine: str

    @property
    def page_confidences(self) -> tuple[float, ...]:
        return tuple(self.document.page_confidences or ())

    def low_confidence_pages(self, floor: float) -> tuple[int, ...]:
        """Return the 1-based pages whose OCR fell below ``floor``."""
        return tuple(
            number
            for number, confidence in enumerate(self.page_confidences, start=1)
            if confidence < floor
        )

    def unreadable_pages(self) -> tuple[int, ...]:
        """Return the 1-based pages from which nothing legible was recovered.

        Separate from low confidence on purpose. A page read poorly still yields text
        that can be qualified; a page yielding nothing supports no claim at all, and
        the honest output for it is that we could not read it.
        """
        pages = self.document.pages or []
        return tuple(
            number
            for number, text in enumerate(pages, start=1)
            if not text.strip()
        )


def page_confidence(raw_score: float | None, *, is_digital: bool) -> float:
    """Return a usable confidence for one page.

    ``NaN`` and ``None`` both collapse to zero rather than propagating. Docling
    reports a non-finite ``ocr_score`` precisely when OCR produced no cells -- the
    total-failure case -- and ``nan < floor`` is ``False``, so leaving it non-finite
    would make the worst page the one page that never trips a threshold.

    A page classified digital did not come from OCR at all; its text is exact, and
    scoring it zero would escalate a perfectly readable page.
    """
    if is_digital:
        return DIGITAL_CONFIDENCE
    if raw_score is None or not math.isfinite(raw_score):
        return NO_OCR_CONFIDENCE
    return max(0.0, min(1.0, float(raw_score)))


class ScannedPdfParser:
    """Parse image-only and mixed PDFs, recording how well each page was read."""

    extensions = (".pdf",)

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        options: ConverterOptions | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._options = options if options is not None else ConverterOptions(
            artifacts_path=self._settings.docling_artifacts_path,
            do_ocr=True,
            ocr_engine=self._settings.ocr_engine,
            ocr_lang=self._settings.ocr_lang,
        )
        if not self._options.do_ocr:
            raise DoclingSetupError(
                "ScannedPdfParser requires do_ocr=True; a converter without OCR "
                "extracts nothing from an image-only page and reports no error."
            )

    @property
    def engine(self) -> str:
        return self._options.ocr_engine

    def parse(self, path: Path) -> Document:
        """Return the parsed document, discarding the confidence report.

        Present so this satisfies the DocumentParser protocol and can be registered
        like any other parser. Prefer :meth:`parse_with_confidence`, which is the only
        way to obtain the per-page scores.
        """
        return self.parse_with_confidence(path).document

    def parse_with_confidence(self, path: Path) -> OcrResult:
        """Parse a PDF, returning its text and per-page OCR confidence."""
        profile = profile_pdf(
            path, threshold=self._settings.scanned_char_density_threshold
        )
        result = build_converter(self._options).convert(path)
        doc = result.document

        placeholder = f"<!-- VERICLAIM_PAGE_BREAK_{uuid4().hex} -->"
        markdown = doc.export_to_markdown(
            page_break_placeholder=placeholder,
            escape_html=False,
            image_placeholder="",
        )
        pages = _split_pages(markdown, placeholder, len(doc.pages))
        confidences = self._page_confidences(result, profile, len(pages))

        document = Document(
            name=path.name,
            path=path,
            text="\n\n".join(pages),
            pages=pages,
            page_count=len(pages),
            page_confidences=list(confidences),
        )
        return OcrResult(document=document, profile=profile, engine=self.engine)

    def _page_confidences(
        self,
        result: Any,
        profile: DocumentProfile,
        page_count: int,
    ) -> list[float]:
        """Extract one confidence per page, aligned with the exported page list.

        Docling keys its confidence report by 1-based page number. Pages absent from
        it are scored as unread rather than skipped, so the returned list always
        matches the page list it will be zipped against.
        """
        report = getattr(result, "confidence", None)
        by_page = dict(getattr(report, "pages", {}) or {})
        digital_pages = {
            page.page for page in profile.pages if page.kind == "digital"
        }

        confidences = []
        for number in range(1, page_count + 1):
            scores = by_page.get(number)
            raw = getattr(scores, "ocr_score", None) if scores is not None else None
            confidences.append(
                page_confidence(raw, is_digital=number in digital_pages)
            )
        return confidences
