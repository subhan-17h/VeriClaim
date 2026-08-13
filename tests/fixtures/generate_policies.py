"""Generate the hand-written policy PDFs used by the retrieval tests.

Run: ``uv run python tests/fixtures/generate_policies.py``

The generated PDFs are committed. They are test *inputs*, not build artefacts: the
tests assert against their exact wording, page numbers, and clause structure, so
regenerating them on the fly would make a failure ambiguous between a code bug and a
fixture change.

These stand in for the C-8 corpus, which does not exist yet, and deliberately carry
the load-bearing distinction the whole project turns on -- sudden and accidental
escape of water is covered, gradual leakage is excluded -- so that retrieval quality
is measurable before the generated corpus lands.

Each document is multi-page with a running header and a page stamp, which is what
exercises the furniture-stripping path in ``loaders/pdf.py``.
"""

from __future__ import annotations

from pathlib import Path

from vericlaim.corpus.pdf import render_policy_pdf

OUTPUT_DIR = Path(__file__).parent / "policies"


HOMESECURE_PLUS = [
    [
        "HOMESECURE PLUS",
        "This policy wording sets out the cover provided to the insured named in the "
        "schedule, subject to the terms, conditions, limits and exclusions below. All "
        "amounts are expressed in Pakistani Rupees (PKR).",
        "SECTION 1 — DEFINITIONS",
        "1.1 Sudden and accidental means an event that is unforeseen, unintended, and "
        "which occurs at an identifiable point in time. An event that develops "
        "progressively over days, weeks or months is not sudden and accidental.",
        "1.2 Fixed plumbing system means the permanently installed pipes, tanks, "
        "valves, water heaters and fixtures forming part of the building's water "
        "supply or waste system. It does not include hoses, portable appliances, or "
        "connections not permanently fixed to the building.",
        "1.3 Deductible means the amount shown in the schedule that the insured bears "
        "for each and every claim before the insurer's liability attaches.",
    ],
    [
        "SECTION 2 — COVERAGE",
        "COVERAGE A — DWELLING",
        "The insurer will indemnify the insured for direct physical loss or damage to "
        "the dwelling described in the schedule, up to the sum insured, arising from "
        "an insured peril occurring during the period of insurance.",
        "COVERAGE B — CONTENTS",
        "The insurer will indemnify the insured for direct physical loss or damage to "
        "household contents at the insured address, up to the sum insured stated for "
        "Coverage B in the schedule.",
        "SECTION 3 — INSURED PERILS",
        "3.1 Fire, lightning and explosion.",
        "3.2 Storm, tempest and flood, subject to Section 5.",
        "3.3 Escape of water, subject to Section 4.",
        "3.4 Theft following forcible and violent entry to the dwelling.",
    ],
    [
        "SECTION 4 — WATER DAMAGE",
        "4.1 This section applies to all loss or damage caused by water escaping "
        "within the insured premises, whether from the fixed plumbing system or from "
        "any appliance permanently connected to it.",
        "4.2 Sudden and accidental escape of water from a fixed plumbing system is "
        "covered under this policy. Cover extends to the resulting damage to the "
        "dwelling and to contents, and to the reasonable cost of locating the source "
        "of the escape, up to PKR 150,000. A deductible of PKR 25,000 applies to each "
        "and every claim made under this clause.",
        "4.3 Where an escape of water results from the failure of a pipe that has "
        "ruptured without prior warning, the insurer will treat the event as sudden "
        "and accidental for the purposes of clause 4.2, provided the insured reports "
        "the loss within thirty days of discovery.",
        "4.4 The insurer may require an inspection report from a qualified plumber or "
        "loss adjuster establishing the mechanism and timing of the escape before "
        "settling a claim under this section.",
    ],
    [
        "SECTION 5 — EXCLUSIONS",
        "The insurer will not indemnify the insured in respect of:",
        "5.1 Loss or damage caused by gradual leakage, seepage, weeping or dripping "
        "from any pipe, tank, fitting or appliance, where that leakage has occurred "
        "over a period of time. Such loss is excluded whether or not the insured was "
        "aware of it.",
        "5.2 Loss or damage arising from wear and tear, gradual deterioration, "
        "corrosion, rust, or the failure of the insured to maintain the fixed "
        "plumbing system in sound condition.",
        "5.3 The cost of repairing or replacing the pipe, fitting or appliance from "
        "which the water escaped, as distinct from the damage that escape caused.",
        "5.4 Loss or damage occurring while the dwelling has been left unoccupied for "
        "more than sixty consecutive days, unless the water supply has been shut off "
        "and the system drained.",
        "5.5 Loss or damage caused by subsidence, landslip or heave.",
    ],
    [
        "SECTION 6 — CLAIMS CONDITIONS",
        "6.1 The insured must notify the insurer of any event likely to give rise to a "
        "claim as soon as reasonably practicable, and in any event within thirty days "
        "of the date of loss.",
        "6.2 The insured must take reasonable steps to prevent further damage and must "
        "not dispose of damaged property before the insurer has had an opportunity to "
        "inspect it.",
        "6.3 The insurer will appoint a loss adjuster for any claim where the "
        "estimated indemnity exceeds PKR 100,000.",
        "6.4 Where a claim is found to be fraudulent in whole or in part, the insurer "
        "may decline the claim in its entirety and void the policy from inception.",
    ],
]

LANDLORD_PROTECT = [
    [
        "LANDLORD PROTECT",
        "This policy wording covers residential property let to tenants under a "
        "written tenancy agreement. Cover is provided in Pakistani Rupees (PKR) and "
        "is subject to the schedule, the terms below, and any endorsement attached.",
        "SECTION 1 — DEFINITIONS",
        "1.1 Let property means the residential building described in the schedule "
        "and occupied under a written tenancy agreement at the date of loss.",
        "1.2 Tenant means the person named in that tenancy agreement.",
        "1.3 Sudden and accidental carries the same meaning as in the HomeSecure "
        "range of wordings: unforeseen, unintended, and occurring at an identifiable "
        "point in time.",
    ],
    [
        "SECTION 2 — COVERAGE",
        "COVERAGE A — BUILDINGS",
        "The insurer will indemnify the landlord for direct physical loss or damage to "
        "the let property up to the sum insured, arising from an insured peril.",
        "COVERAGE C — LOSS OF RENT",
        "Where the let property is rendered uninhabitable by an insured peril, the "
        "insurer will indemnify the landlord for rent genuinely lost, for the period "
        "reasonably required to reinstate the property, up to a maximum of twelve "
        "months and not exceeding PKR 600,000.",
        "SECTION 3 — WATER DAMAGE",
        "3.1 Sudden and accidental escape of water from the fixed plumbing system of "
        "the let property is covered. A deductible of PKR 40,000 applies to each and "
        "every claim, which is higher than the equivalent deductible under the "
        "HomeSecure range.",
    ],
    [
        "SECTION 4 — EXCLUSIONS",
        "4.1 Gradual leakage, seepage and damage arising from lack of maintenance are "
        "excluded, on the same basis as under the HomeSecure wordings.",
        "4.2 Malicious damage caused by the tenant or by any person lawfully in the "
        "let property with the tenant's permission is excluded unless the Malicious "
        "Damage by Tenant endorsement is shown as operative in the schedule.",
        "4.3 Loss of rent is excluded where the property was vacant at the date of "
        "loss and no tenancy agreement was in force.",
        "4.4 Wear and tear and gradual deterioration are excluded.",
        "SECTION 5 — CLAIMS CONDITIONS",
        "5.1 The landlord must hold a written tenancy agreement and produce it on "
        "request as a condition precedent to liability under Coverage C.",
        "5.2 Notification must be given within thirty days of the date of loss.",
    ],
]

EXCLUSIONS_SCHEDULE = [
    [
        "GENERAL EXCLUSIONS SCHEDULE",
        "This schedule applies to every NorthStar property wording and operates in "
        "addition to the exclusions stated in the individual policy. Where an "
        "individual wording and this schedule conflict, the individual wording "
        "prevails to the extent of the conflict.",
        "SECTION 1 — GENERAL EXCLUSIONS",
        "1.1 War, invasion, act of foreign enemy, hostilities, civil war, rebellion, "
        "revolution, insurrection or military usurpation of power.",
        "1.2 Ionising radiation or contamination by radioactivity from nuclear fuel or "
        "nuclear waste.",
        "1.3 Any consequence of a cyber act or cyber incident, except to the extent "
        "written back by an operative endorsement.",
        "1.4 Wilful act or wilful neglect of the insured.",
    ],
    [
        "SECTION 2 — MAINTENANCE AND GRADUAL CAUSES",
        "2.1 The insurer does not cover loss or damage that develops gradually, "
        "including but not limited to gradual leakage, seepage, damp, condensation, "
        "rot, fungus, mould, and infestation.",
        "2.2 Where damage results partly from a sudden and accidental event and partly "
        "from a gradual cause, the insurer will indemnify only that portion "
        "attributable to the sudden and accidental event. The burden of demonstrating "
        "the apportionment rests with the insured, and an inspection report "
        "establishing the mechanism of loss will ordinarily be required.",
        "2.3 The cost of remedying a defect in design, materials or workmanship is "
        "excluded, though resulting damage may be covered.",
        "ENDORSEMENT 1 — ESCAPE OF WATER INSPECTION CONDITION",
        "Where the schedule shows this endorsement as operative, it is a condition "
        "precedent to liability for any escape of water claim exceeding PKR 200,000 "
        "that the insured produces an inspection report from a qualified plumber "
        "identifying the point of failure and stating whether the failure was sudden.",
    ],
    [
        "ENDORSEMENT 2 — UNOCCUPANCY CONDITION",
        "Where the schedule shows this endorsement as operative, cover for escape of "
        "water is suspended after the property has been unoccupied for thirty "
        "consecutive days, rather than the sixty days stated in the policy wording, "
        "unless the water supply is shut off at the mains and the system drained.",
        "ENDORSEMENT 3 — REINSTATEMENT BASIS",
        "Settlement is on a reinstatement basis provided reinstatement is carried out "
        "without unreasonable delay. Where reinstatement is not carried out, "
        "settlement is on an indemnity basis reflecting wear and tear.",
        "SECTION 3 — INTERPRETATION",
        "3.1 Headings in this schedule are for convenience and do not affect its "
        "construction.",
        "3.2 Where an amount is stated in this schedule it is inclusive of any "
        "applicable tax unless stated otherwise.",
    ],
]

DOCUMENTS: dict[str, tuple[str, str, list[list[str]]]] = {
    "HomeSecure_Plus_2026.pdf": ("HomeSecure Plus", "NS-HSP-2026", HOMESECURE_PLUS),
    "Landlord_Protect_2026.pdf": ("Landlord Protect", "NS-LLP-2026", LANDLORD_PROTECT),
    "Exclusions_Schedule_2026.pdf": (
        "General Exclusions Schedule",
        "NS-GES-2026",
        EXCLUSIONS_SCHEDULE,
    ),
}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (title, form_number, pages) in DOCUMENTS.items():
        path = OUTPUT_DIR / filename
        render_policy_pdf(path, title, form_number, pages)
        print(f"wrote {path.relative_to(Path(__file__).parents[2])} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
