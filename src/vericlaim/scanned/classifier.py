"""Decide, per page, whether a PDF needs OCR at all.

This gate is what makes the scanned source affordable. OCR costs seconds per page;
text extraction costs milliseconds. Running OCR over the policy corpus because one
page in it happens to be a scan would make indexing unusable, and skipping OCR on a
genuinely scanned claim file makes it invisible to the system.

Classification is on extracted-character density -- characters per square point of
page area -- which normalises across page sizes, so an A4 wording and a Letter-sized
inspection form are judged on the same scale.

Measured on this project's fixtures:

===========================  ===================
Page                         Characters/pt²
===========================  ===================
Digital policy wordings      0.00142 – 0.00230
Image-only scans             0.00000
===========================  ===================

The threshold sits an order of magnitude below the sparsest real text page and above
zero, so the two populations are separated with room on both sides rather than split
down the middle of either.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

__all__ = (
    "DEFAULT_DENSITY_THRESHOLD",
    "DocumentProfile",
    "PageKind",
    "PageProfile",
    "classify_document",
    "classify_page",
    "profile_pdf",
)

PageKind = Literal["digital", "scanned", "mixed"]

# Characters per square point. See the table above; this is ~7x below the sparsest
# measured real text page. A page whose density is below it carries too little text
# to have come from a text layer describing the whole page.
DEFAULT_DENSITY_THRESHOLD = 0.0002

# Below this, a page has essentially no text layer at all rather than a sparse one.
# Kept separate from the density threshold because "no text" and "little text" call
# for different handling: the first is a scan, the second is a scan with a stamped
# header, or a cover page, and both need OCR but only one is unambiguous.
_EMPTY_DENSITY = 1e-6


@dataclass(frozen=True, slots=True)
class PageProfile:
    """One page's text yield and what it implies."""

    page: int
    char_count: int
    area: float
    kind: PageKind

    @property
    def density(self) -> float:
        """Characters per square point of page area."""
        return self.char_count / self.area if self.area > 0 else 0.0

    @property
    def needs_ocr(self) -> bool:
        """Whether this page's text can only be recovered by reading the image."""
        return self.kind != "digital"


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    """A whole document's per-page classification."""

    path: Path
    pages: tuple[PageProfile, ...]

    @property
    def needs_ocr(self) -> bool:
        """Whether any page requires OCR.

        Any, not all: a claim file whose covering letter is digital and whose
        inspection report is a scan is exactly the document that must not be skipped.
        """
        return any(page.needs_ocr for page in self.pages)

    @property
    def kind(self) -> PageKind:
        """The document-level classification.

        ``mixed`` when its pages disagree, which is a real and common shape -- a
        digitally generated form with a scanned attachment stapled on.
        """
        kinds = {page.kind for page in self.pages}
        if not kinds:
            return "digital"
        if kinds == {"digital"}:
            return "digital"
        if kinds == {"scanned"}:
            return "scanned"
        return "mixed"

    @property
    def scanned_pages(self) -> tuple[int, ...]:
        """The 1-based page numbers that require OCR."""
        return tuple(page.page for page in self.pages if page.needs_ocr)


def classify_page(
    char_count: int,
    area: float,
    *,
    threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> PageKind:
    """Classify one page from its character count and area."""
    if area <= 0:
        # A page with no declared geometry cannot be judged on density. Treating it
        # as scanned costs OCR time; treating it as digital could lose it entirely.
        return "scanned"
    density = char_count / area
    if density >= threshold:
        return "digital"
    if density <= _EMPTY_DENSITY:
        return "scanned"
    return "mixed"


def profile_pdf(
    path: Path,
    *,
    threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> DocumentProfile:
    """Classify every page of a PDF by its extracted-text density."""
    reader = PdfReader(path)
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        area = float(page.mediabox.width) * float(page.mediabox.height)
        pages.append(
            PageProfile(
                page=number,
                char_count=len(text.strip()),
                area=area,
                kind=classify_page(len(text.strip()), area, threshold=threshold),
            )
        )
    return DocumentProfile(path=path, pages=tuple(pages))


def classify_document(
    path: Path,
    *,
    threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> PageKind:
    """Return one document's overall classification."""
    return profile_pdf(path, threshold=threshold).kind


def documents_needing_ocr(
    paths: Sequence[Path],
    *,
    threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> list[DocumentProfile]:
    """Return profiles for those documents that contain at least one scanned page."""
    profiles = [profile_pdf(path, threshold=threshold) for path in paths]
    return [profile for profile in profiles if profile.needs_ocr]
