"""Shared deterministic PDF rendering for policy-form documents."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

_BODY_FONT = ("Helvetica", 10)
_HEADING_FONT = ("Helvetica-Bold", 11)
_LEFT = 20 * mm
_TOP = 272 * mm
_LINE = 5.2 * mm
_WIDTH = 78


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
