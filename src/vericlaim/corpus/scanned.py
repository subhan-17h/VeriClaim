"""Generate the deterministic image-only scanned claim-document corpus.

The source records come from :func:`generate_transactions`; no database is queried.
Each text page is rendered deterministically, converted to a greyscale bitmap, given
scanner artefacts, and saved as a PDF with fixed Pillow timestamps. The resulting PDF
contains no text objects even though its photographed page remains readable by OCR.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image
from reportlab.lib.units import mm

from vericlaim.config import get_settings
from vericlaim.corpus.catalog import REGIONS
from vericlaim.corpus.pdf import (
    degrade,
    obscure,
    rasterise,
    render_image_only_pdf,
    render_text_pdf,
)
from vericlaim.corpus.transactions import WATER_DAMAGE_CAUSES, ClaimRow, generate_transactions

DocumentType = Literal["inspection_report", "contractor_invoice", "claim_form"]
ScanQuality = Literal["clean", "degraded", "illegible"]

DOCUMENT_COUNT = 72
COUNTER_EVIDENCE_COUNT = 8

# An explicit schedule, rather than a random quality draw. Entry N applies to the Nth
# selected claim in the reviewed selection groups below. This makes the exact 55/14/3
# mix inspectable without running the generator.
QUALITY_SCHEDULE: tuple[ScanQuality, ...] = (
    "clean", "clean", "clean", "clean", "degraded", "clean",
    "clean", "clean", "clean", "degraded", "clean", "clean",
    "clean", "clean", "degraded", "clean", "clean", "clean",
    "clean", "degraded", "clean", "clean", "clean", "illegible",
    "degraded", "clean", "clean", "clean", "clean", "degraded",
    "clean", "clean", "clean", "clean", "degraded", "clean",
    "clean", "clean", "clean", "degraded", "clean", "clean",
    "clean", "clean", "degraded", "clean", "clean", "illegible",
    "clean", "degraded", "clean", "clean", "clean", "clean",
    "degraded", "clean", "clean", "clean", "clean", "degraded",
    "clean", "clean", "clean", "clean", "degraded", "clean",
    "clean", "clean", "clean", "degraded", "clean", "illegible",
)


@dataclass(frozen=True, slots=True)
class SelectionGroup:
    """A reviewed concentration of documents around one peril and report month."""

    peril: str
    month: int
    count: int


SELECTION_GROUPS: tuple[SelectionGroup, ...] = (
    SelectionGroup("water_damage", 3, 36),
    SelectionGroup("fire", 1, 8),
    SelectionGroup("theft", 4, 10),
    SelectionGroup("storm", 5, 6),
    SelectionGroup("impact", 2, 6),
    SelectionGroup("liability", 6, 6),
)


@dataclass(frozen=True, slots=True)
class ScannedDocument:
    """One resolved corpus document and the declaration used to render it."""

    claim: ClaimRow
    document_type: DocumentType
    quality: ScanQuality
    counter_evidence: bool
    filename: str
    blocks: tuple[str, ...]
    known_phrase: str


_REGION_BY_ID = {region.region_id: region for region in REGIONS}

_TYPE_LABELS: dict[DocumentType, str] = {
    "inspection_report": "INSPECTION_REPORT",
    "contractor_invoice": "CONTRACTOR_INVOICE",
    "claim_form": "CLAIM_FORM",
}

_PERIL_FINDINGS = {
    "water_damage": "Moisture readings and the affected plumbing route were examined on site.",
    "fire": "The seat of fire, heat pattern and damaged electrical fittings were examined.",
    "theft": "Entry points, damaged locks and the reported areas of missing property were checked.",
    "storm": "Roof edges, rain entry points and displaced external materials were inspected.",
    "impact": "The point of impact and the resulting structural cracking were measured.",
    "liability": (
        "The reported incident area, walking surface and available warning signs were reviewed."
    ),
}


def _text_pdf(blocks: list[str], *, document_type: DocumentType = "inspection_report") -> bytes:
    """Render a deterministic text page before its text layer is discarded."""
    layouts = {
        "inspection_report": (20 * mm, 268 * mm, 6.0 * mm, 72, 11, 12, "report"),
        "contractor_invoice": (25 * mm, 274 * mm, 5.4 * mm, 66, 10, 14, "invoice"),
        "claim_form": (18 * mm, 272 * mm, 5.8 * mm, 76, 10, 12, "form"),
    }
    left, top, line_height, width, body_size, heading_size, layout = layouts[document_type]
    return render_text_pdf(
        blocks,
        left=left,
        top=top,
        line_height=line_height,
        wrap_width=width,
        body_size=body_size,
        heading_size=heading_size,
        layout=layout,
    )


def _document_types() -> tuple[DocumentType, ...]:
    """Assign a register to each selected claim, in selection order.

    The water-damage group leads with inspection reports because only that register
    carries a surveyor's conclusion, and the counter-evidence documents are exactly a
    conclusion that contradicts the recorded cause. ``scanned_documents`` enforces
    that alignment rather than leaving it to this arithmetic.
    """
    water: list[DocumentType] = (
        ["inspection_report"] * 12 + ["contractor_invoice"] * 12 + ["claim_form"] * 12
    )
    other = [kind for _ in range(12) for kind in _TYPE_LABELS]
    return tuple(water + other)


def _selected_claims(seed: int) -> tuple[tuple[ClaimRow, bool], ...]:
    claims = generate_transactions(seed).claims
    selected: list[tuple[ClaimRow, bool]] = []
    used: set[int] = set()

    for group in SELECTION_GROUPS:
        candidates = sorted(
            (
                claim
                for claim in claims
                if claim.peril == group.peril and claim.report_date.month == group.month
            ),
            key=lambda claim: claim.claim_id,
        )
        if group.peril == "water_damage":
            sudden_causes = set(WATER_DAMAGE_CAUSES["sudden"])
            contradictions = [
                claim for claim in candidates if claim.cause_description in sudden_causes
            ][:COUNTER_EVIDENCE_COUNT]
            chosen = contradictions + [
                claim
                for claim in candidates
                if claim.claim_id not in {item.claim_id for item in contradictions}
            ][: group.count - len(contradictions)]
            counter_ids = {claim.claim_id for claim in contradictions}
        else:
            chosen = candidates[: group.count]
            counter_ids = set()

        if len(chosen) != group.count:
            raise ValueError(
                f"Not enough {group.peril} claims in month {group.month}: "
                f"needed {group.count}, found {len(chosen)}"
            )
        for claim in chosen:
            if claim.claim_id in used:
                raise AssertionError(f"claim selected twice: {claim.claim_number}")
            used.add(claim.claim_id)
            selected.append((claim, claim.claim_id in counter_ids))

    if len(selected) != DOCUMENT_COUNT:
        raise AssertionError(f"selection plan produced {len(selected)} documents")
    return tuple(selected)


def _inspection_blocks(claim: ClaimRow, *, counter_evidence: bool) -> tuple[str, ...]:
    region = _REGION_BY_ID[claim.region_id]
    if counter_evidence:
        conclusion = (
            "COUNTER-EVIDENCE CONCLUSION: Although the claim record describes "
            f"'{claim.cause_description}', the physical staining, corrosion and softened "
            "material show gradual seepage from a long-term leakage over several months. "
            "The observed damage is not consistent with a sudden and accidental escape."
        )
    else:
        conclusion = (
            "CONCLUSION: The observed condition is consistent with the reported cause. "
            "No different mechanism was identified during this inspection."
        )
    return (
        "NORTHSTAR INSURANCE LIMITED",
        "PROPERTY INSPECTION REPORT",
        f"Claim Reference: {claim.claim_number}",
        f"Date of Loss: {claim.date_of_loss:%d %B %Y}",
        f"Date Reported: {claim.report_date:%d %B %Y}",
        f"Inspection Region: {region.region_name}, {region.city}, {region.province}",
        f"Recorded Peril: {claim.peril.replace('_', ' ').title()}",
        "SITE INSPECTION FINDINGS",
        _PERIL_FINDINGS[claim.peril],
        f"Cause reported on the claim record: {claim.cause_description}.",
        conclusion,
        "Prepared by NorthStar Field Inspection Services.",
    )


def _invoice_blocks(claim: ClaimRow) -> tuple[str, ...]:
    region = _REGION_BY_ID[claim.region_id]
    amount = f"{claim.incurred_amount_pkr:,.2f}"
    return (
        f"{region.city.upper()} PROPERTY REPAIRS",
        "CONTRACTOR INVOICE AND WORK ESTIMATE",
        f"Invoice: NS-{claim.claim_id}",
        f"Claim Reference: {claim.claim_number}",
        f"Service Area: {region.region_name}, {region.province}",
        f"Peril: {claim.peril.replace('_', ' ').title()}",
        f"Loss Date: {claim.date_of_loss:%d %B %Y}",
        f"Instructions Received: {claim.report_date:%d %B %Y}",
        "SCOPE OF WORKS",
        _PERIL_FINDINGS[claim.peril],
        f"Reported cause used for this estimate: {claim.cause_description}.",
        "Labour, materials, removal of damaged finishes and reinstatement are included.",
        f"ESTIMATED WORKS TOTAL: PKR {amount}",
        "This estimate records repair scope only and is not a coverage decision.",
    )


def _claim_form_blocks(claim: ClaimRow) -> tuple[str, ...]:
    region = _REGION_BY_ID[claim.region_id]
    return (
        "NORTHSTAR INSURANCE LIMITED",
        "PROPERTY CLAIM NOTIFICATION FORM",
        "SECTION A - CLAIM DETAILS",
        f"Claim Number: {claim.claim_number}",
        f"Date of Loss: {claim.date_of_loss:%d %B %Y}",
        f"Date Notified: {claim.report_date:%d %B %Y}",
        f"Loss Location: {region.city}, {region.province}",
        f"Handling Region: {region.region_name}",
        f"Peril Declared: {claim.peril.replace('_', ' ').title()}",
        "SECTION B - CLAIMANT STATEMENT",
        claim.cause_description,
        "I confirm that the details above describe the event as reported to NorthStar.",
        "SECTION C - INTERNAL USE",
        f"Current claim status: {claim.status.title()}",
        "Receipt of this form does not constitute acceptance of coverage or liability.",
    )


def scanned_documents(seed: int) -> tuple[ScannedDocument, ...]:
    """Resolve the reviewed selection and return its complete document manifest."""
    selected = _selected_claims(seed)
    document_types = _document_types()
    if len(QUALITY_SCHEDULE) != len(selected) or len(document_types) != len(selected):
        raise AssertionError("document, type and quality plans must have equal length")

    documents = []
    for (claim, counter_evidence), document_type, quality in zip(
        selected, document_types, QUALITY_SCHEDULE, strict=True
    ):
        if counter_evidence and document_type != "inspection_report":
            # Only _inspection_blocks writes the gradual-seepage conclusion, so a
            # counter-evidence claim landing on any other register would drop the
            # contradiction silently -- and the contradiction is the whole reason
            # these documents exist.
            raise AssertionError(
                f"counter-evidence claim {claim.claim_number} was assigned "
                f"{document_type!r}, which cannot carry a surveyor's conclusion"
            )
        if document_type == "inspection_report":
            blocks = _inspection_blocks(claim, counter_evidence=counter_evidence)
            known_phrase = "site inspection findings"
        elif document_type == "contractor_invoice":
            blocks = _invoice_blocks(claim)
            known_phrase = "scope of works"
        else:
            blocks = _claim_form_blocks(claim)
            known_phrase = "claimant statement"
        documents.append(
            ScannedDocument(
                claim=claim,
                document_type=document_type,
                quality=quality,
                counter_evidence=counter_evidence,
                filename=f"{claim.claim_number}_{_TYPE_LABELS[document_type]}.pdf",
                blocks=blocks,
                known_phrase=known_phrase,
            )
        )
    return tuple(documents)


def _scan_images(document: ScannedDocument, rng: random.Random) -> list[Image.Image]:
    source = rasterise(
        _text_pdf(list(document.blocks), document_type=document.document_type),
        dpi=200 if document.quality == "clean" else 150,
    )
    if document.quality == "clean":
        return [
            degrade(
                image,
                rotation=0.3,
                noise=1,
                blur=0.3,
                jpeg_quality=92,
                downscale=1.0,
                rng=rng,
            )
            for image in source
        ]
    if document.quality == "degraded":
        return [
            degrade(
                image,
                rotation=1.4,
                noise=14,
                blur=1.1,
                jpeg_quality=32,
                downscale=0.55,
                rng=rng,
            )
            for image in source
        ]
    return [obscure(image, rng) for image in source]


def generate_scanned_corpus(output_dir: Path | None = None, *, seed: int) -> list[Path]:
    """Write 72 deterministic image-only PDFs and return paths by filename."""
    destination = output_dir or get_settings().scanned_dir
    destination.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    written = []
    for document in sorted(scanned_documents(seed), key=lambda item: item.filename):
        path = destination / document.filename
        render_image_only_pdf(_scan_images(document, rng), path)
        written.append(path)
    return written


def main() -> None:
    for path in generate_scanned_corpus(seed=42):
        print(path.name)


if __name__ == "__main__":
    main()


__all__ = [
    "COUNTER_EVIDENCE_COUNT",
    "DOCUMENT_COUNT",
    "DocumentType",
    "QUALITY_SCHEDULE",
    "ScanQuality",
    "SELECTION_GROUPS",
    "ScannedDocument",
    "SelectionGroup",
    "generate_scanned_corpus",
    "scanned_documents",
]
