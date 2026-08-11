"""Generate the image-only scanned PDFs used by the OCR tests.

Run: ``uv run python tests/fixtures/generate_scanned.py``

The generated PDFs are committed, for the same reason the policy fixtures are: the
tests assert against their exact wording and page count.

These are genuinely scanned documents, not text PDFs pretending to be. Each is
rendered to a raster image and re-wrapped, so ``pypdf`` extracts zero characters from
them -- which is the whole point. A fixture that still carries a text layer would let
every OCR test pass without OCR ever running.

Three documents, each keyed to a claim id and each covering a case the pipeline must
handle differently:

* ``CLM-1001`` clean, legible -- OCR should read it with high confidence.
* ``CLM-1002`` degraded (skewed, noisy, downsampled, JPEG artefacts) -- readable, but
  with confidence low enough to exercise the escalation path. Its content is the
  deliberate counter-evidence: gradual seepage over months, which the policy excludes.
* ``CLM-1003`` largely illegible -- the case where the honest output is a refusal, not
  a confident transcription.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).parent / "scanned"

# Fixed so a regenerated fixture is byte-comparable and a test failure means a code
# change, never a different random draw.
SEED = 42

_LEFT = 20 * mm
_TOP = 268 * mm
_LINE = 6.0 * mm
_WRAP = 72


def _wrap(text: str, width: int = _WRAP) -> list[str]:
    words, lines, current = text.split(), [], ""
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


def _text_pdf(blocks: list[str]) -> bytes:
    """Render the source document as an ordinary text PDF, before rasterisation."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = _TOP
    for block in blocks:
        bold = block.isupper()
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 12 if bold else 11)
        for line in _wrap(block):
            pdf.drawString(_LEFT, y, line)
            y -= _LINE
        y -= _LINE * 0.5
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _rasterise(pdf_bytes: bytes, dpi: int) -> list[Image.Image]:
    """Render each page to a bitmap, discarding the text layer entirely."""
    document = pdfium.PdfDocument(pdf_bytes)
    scale = dpi / 72.0
    return [page.render(scale=scale).to_pil().convert("L") for page in document]


def _degrade(
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


def _obscure(image: Image.Image, rng: random.Random) -> Image.Image:
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


def _image_only_pdf(images: list[Image.Image], path: Path) -> None:
    """Write bitmaps as a PDF containing images and no text objects."""
    first, *rest = [image.convert("RGB") for image in images]
    first.save(path, format="PDF", save_all=bool(rest), append_images=rest, resolution=110.0)


CLM_1001 = [
    "NORTHSTAR INSURANCE LIMITED",
    "PLUMBING INSPECTION REPORT",
    "Claim Reference: CLM-1001",
    "Date of Inspection: 14 March 2026",
    "Property: 42 Gulberg Avenue, Lahore",
    "Inspector: M. Farooq, Licensed Plumber",
    "FINDINGS",
    "A copper supply pipe running beneath the kitchen floor has failed at a "
    "soldered joint. The failure is a clean circumferential rupture. There is no "
    "corrosion, scaling or staining around the joint.",
    "In my opinion the pipe ruptured suddenly. Water escaped at mains pressure and "
    "flooded the kitchen and hallway within a short period. The occupier reports "
    "that the floor was dry the previous evening.",
    "There is no evidence of long standing damp. Skirting boards away from the "
    "immediate area remain sound and dry.",
    "CONCLUSION",
    "The escape of water was sudden and accidental. Estimated cost of repair and "
    "making good is PKR 184,000.",
]

CLM_1002 = [
    "NORTHSTAR INSURANCE LIMITED",
    "PLUMBING INSPECTION REPORT",
    "Claim Reference: CLM-1002",
    "Date of Inspection: 19 March 2026",
    "Property: 8 Model Town Link Road, Lahore",
    "Inspector: S. Iqbal, Licensed Plumber",
    "FINDINGS",
    "The waste pipe serving the first floor bathroom has been weeping at a "
    "compression fitting. There is heavy limescale and green corrosion staining "
    "extending approximately 40 centimetres below the joint.",
    "The ceiling below shows established water staining with defined tide marks and "
    "blown plaster. Timber joists are darkened and soft to probe.",
    "In my opinion this leak has been present for several months. The deterioration "
    "is consistent with gradual seepage rather than any sudden failure.",
    "CONCLUSION",
    "The damage results from a long standing leak and poor maintenance. Estimated "
    "cost of repair is PKR 96,000.",
]

CLM_1003 = [
    "NORTHSTAR INSURANCE LIMITED",
    "PLUMBING INSPECTION REPORT",
    "Claim Reference: CLM-1003",
    "Date of Inspection: 22 March 2026",
    "FINDINGS",
    "The supply pipe beneath the utility room has failed. Water has tracked beneath "
    "the floor covering and into the adjoining store room causing damage to stored "
    "contents and to the floor structure throughout that area of the property.",
    "CONCLUSION",
    "Further investigation is required before the mechanism of failure can be "
    "established with confidence by this inspector.",
]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    # Clean scan: rasterised at a good resolution with only mild scanner artefacts.
    clean = [
        _degrade(
            image,
            rotation=0.3,
            noise=1,
            blur=0.3,
            jpeg_quality=92,
            downscale=1.0,
            rng=rng,
        )
        for image in _rasterise(_text_pdf(CLM_1001), dpi=200)
    ]
    _image_only_pdf(clean, OUTPUT_DIR / "CLM-1001_INSPECTION.pdf")

    # Degraded scan: readable, but poorly enough to pull confidence down.
    degraded = [
        _degrade(
            image,
            rotation=1.4,
            noise=14,
            blur=1.1,
            jpeg_quality=32,
            downscale=0.55,
            rng=rng,
        )
        for image in _rasterise(_text_pdf(CLM_1002), dpi=150)
    ]
    _image_only_pdf(degraded, OUTPUT_DIR / "CLM-1002_INSPECTION.pdf")

    # Ruined scan: the honest output is that we could not read it.
    illegible = [_obscure(image, rng) for image in _rasterise(_text_pdf(CLM_1003), dpi=150)]
    _image_only_pdf(illegible, OUTPUT_DIR / "CLM-1003_INSPECTION.pdf")

    for path in sorted(OUTPUT_DIR.glob("*.pdf")):
        relative = path.relative_to(Path(__file__).parents[2])
        print(f"wrote {relative} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
