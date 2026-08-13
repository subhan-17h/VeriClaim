"""Shared deterministic PDF rendering, for both the digital and the scanned corpus."""

from __future__ import annotations

import io
import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

_BODY_FONT = ("Helvetica", 10)
_HEADING_FONT = ("Helvetica-Bold", 11)
_LEFT = 20 * mm
_TOP = 272 * mm
_LINE = 5.2 * mm
_WIDTH = 78

TextPdfLayout = Literal["report", "invoice", "form"]

# Pillow's PdfImagePlugin reads creationDate/modDate from ``im.encoderinfo`` and only
# falls back to ``time.gmtime()``, so a fixed struct_time wins. It is the default here
# rather than a parameter each caller must remember, because a corpus the .gitignore
# promises is reproducible from a seed cannot carry wall-clock metadata -- and every
# generator that forgot the flag is exactly how that promise gets quietly broken.
FIXED_PDF_TIMESTAMP = time.struct_time((2026, 1, 1, 0, 0, 0, 3, 1, 0))


def render_text_pdf(
    blocks: Sequence[str],
    *,
    left: float = 20 * mm,
    top: float = 268 * mm,
    line_height: float = 6 * mm,
    wrap_width: int = 72,
    body_size: int = 11,
    heading_size: int = 12,
    layout: TextPdfLayout = "report",
) -> bytes:
    """Render one deterministic text page for later rasterisation.

    This is the scanned-corpus counterpart to :func:`render_policy_pdf`. ReportLab's
    invariant mode pins its document metadata; the caller then discards the text layer
    by rasterising this intermediate page.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    y = top
    for index, block in enumerate(blocks):
        bold = block.isupper()
        pdf.setFont(
            "Helvetica-Bold" if bold else "Helvetica",
            heading_size if bold else body_size,
        )
        lines = wrap_text(block, width=wrap_width)
        if layout == "form" and block.startswith("SECTION "):
            pdf.rect(
                left - 2 * mm,
                y - line_height * 0.35,
                A4[0] - 2 * left + 4 * mm,
                line_height * 1.2,
                stroke=1,
                fill=0,
            )
        for line in lines:
            if layout == "invoice" and index < 2:
                pdf.drawCentredString(A4[0] / 2, y, line)
            else:
                pdf.drawString(left, y, line)
            y -= line_height
        if layout == "invoice" and index == 1:
            pdf.line(left, y + line_height * 0.25, A4[0] - left, y + line_height * 0.25)
        y -= line_height * 0.5
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def wrap_text(text: str, width: int = _WIDTH) -> list[str]:
    """Wrap a paragraph to the text column, preserving deliberate blank lines."""
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def rasterise(pdf_bytes: bytes, dpi: int) -> list[Image.Image]:
    """Render every page to a greyscale bitmap, discarding the text layer entirely."""
    document = pdfium.PdfDocument(pdf_bytes)
    scale = dpi / 72.0
    return [page.render(scale=scale).to_pil().convert("L") for page in document]


def degrade(
    image: Image.Image,
    *,
    rotation: float,
    noise: int,
    blur: float,
    jpeg_quality: int,
    downscale: float,
    rng: random.Random,
) -> Image.Image:
    """Apply the artefacts a real desk scanner introduces.

    Skew, sensor noise, soft focus, JPEG ringing, and a lower effective resolution --
    together these are what pull OCR confidence down, which is what the escalation
    path exists to respond to.
    """
    if rotation:
        image = image.rotate(rotation, resample=Image.BICUBIC, fillcolor=255, expand=False)
    if downscale != 1.0:
        reduced = (int(image.width * downscale), int(image.height * downscale))
        image = image.resize(reduced, Image.BILINEAR).resize(image.size, Image.BILINEAR)
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        pixels = image.load()
        for _ in range(noise * image.width * image.height // 1000):
            x = rng.randrange(image.width)
            y = rng.randrange(image.height)
            pixels[x, y] = rng.choice((0, 255))
    if jpeg_quality < 100:
        buffer = io.BytesIO()
        image.convert("L").save(buffer, format="JPEG", quality=jpeg_quality)
        image = Image.open(io.BytesIO(buffer.getvalue())).convert("L")
    return image


def obscure(image: Image.Image, rng: random.Random) -> Image.Image:
    """Smear most of a page past the point of legibility.

    Models a page ruined in handling -- the case where a confident transcription would
    be a fabrication and the only honest output is that we could not read it.
    """
    image = image.filter(ImageFilter.GaussianBlur(4.2))
    draw = ImageDraw.Draw(image)
    for _ in range(160):
        x0 = rng.randrange(image.width)
        y0 = rng.randrange(int(image.height * 0.15), int(image.height * 0.9))
        draw.line(
            [(x0, y0), (x0 + rng.randrange(-260, 260), y0 + rng.randrange(-40, 40))],
            fill=rng.randrange(70, 190),
            width=rng.randrange(5, 16),
        )
    return image


def render_image_only_pdf(
    images: Sequence[Image.Image],
    path: Path,
    *,
    timestamp: time.struct_time = FIXED_PDF_TIMESTAMP,
) -> None:
    """Write bitmaps as a PDF containing images and no text objects."""
    first, *rest = [image.convert("RGB") for image in images]
    first.save(
        path,
        format="PDF",
        save_all=bool(rest),
        append_images=rest,
        resolution=110.0,
        creationDate=timestamp,
        modDate=timestamp,
    )


def render_policy_pdf(
    path: Path,
    title: str,
    form_number: str,
    pages: Sequence[Sequence[str]],
    *,
    document_type: str = "Policy Wording",
) -> None:
    """Render a digital policy PDF with stable metadata and running furniture."""
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    total = len(pages)

    for page_number, blocks in enumerate(pages, start=1):
        pdf.setFont("Helvetica-Oblique", 8)
        pdf.drawString(_LEFT, 285 * mm, f"{title} — {document_type} {form_number}")
        pdf.drawString(_LEFT, 12 * mm, f"Page {page_number} of {total}")
        pdf.drawRightString(190 * mm, 12 * mm, "NorthStar Insurance Limited")

        y = _TOP
        for block in blocks:
            is_heading = block.isupper() or block[:1].isdigit() or block.startswith("(")
            pdf.setFont(*(_HEADING_FONT if is_heading else _BODY_FONT))
            for line in wrap_text(block):
                pdf.drawString(_LEFT, y, line)
                y -= _LINE
            y -= _LINE * 0.6

        pdf.showPage()

    pdf.save()
