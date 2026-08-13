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

import random
from pathlib import Path

from vericlaim.corpus.pdf import degrade, obscure, rasterise, render_image_only_pdf, render_text_pdf

OUTPUT_DIR = Path(__file__).parent / "scanned"

# Fixed so a regenerated fixture is byte-comparable and a test failure means a code
# change, never a different random draw. The PDF timestamp is pinned by
# render_image_only_pdf's default, so regenerating changes bytes only when the
# rendering does.
SEED = 42

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
        degrade(
            image,
            rotation=0.3,
            noise=1,
            blur=0.3,
            jpeg_quality=92,
            downscale=1.0,
            rng=rng,
        )
        for image in rasterise(render_text_pdf(CLM_1001), dpi=200)
    ]
    render_image_only_pdf(clean, OUTPUT_DIR / "CLM-1001_INSPECTION.pdf")

    # Degraded scan: readable, but poorly enough to pull confidence down.
    degraded = [
        degrade(
            image,
            rotation=1.4,
            noise=14,
            blur=1.1,
            jpeg_quality=32,
            downscale=0.55,
            rng=rng,
        )
        for image in rasterise(render_text_pdf(CLM_1002), dpi=150)
    ]
    render_image_only_pdf(degraded, OUTPUT_DIR / "CLM-1002_INSPECTION.pdf")

    # Ruined scan: the honest output is that we could not read it.
    illegible = [obscure(image, rng) for image in rasterise(render_text_pdf(CLM_1003), dpi=150)]
    render_image_only_pdf(illegible, OUTPUT_DIR / "CLM-1003_INSPECTION.pdf")

    for path in sorted(OUTPUT_DIR.glob("*.pdf")):
        relative = path.relative_to(Path(__file__).parents[2])
        print(f"wrote {relative} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
