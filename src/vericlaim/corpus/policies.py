"""Generate the hand-authored NorthStar property policy corpus."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from vericlaim.config import get_settings
from vericlaim.corpus.catalog import COVERAGE_PRODUCTS, CoverageProduct
from vericlaim.corpus.pdf import render_policy_pdf


@dataclass(frozen=True, slots=True)
class PolicyForm:
    title: str
    form_number: str
    pages: list[list[str]]
    document_type: str = "Policy Wording"


def _money(amount: Decimal) -> str:
    return f"{amount:,.0f}"


def _sub_limit(product: CoverageProduct, basis_points: int) -> str:
    amount = product.coverage_limit_pkr * Decimal(basis_points) / Decimal(10_000)
    return _money(amount)


def _opening_page(product: CoverageProduct, definitions: list[str]) -> list[str]:
    return [
        product.product_name.upper(),
        "This wording forms part of the contract between the insured named in the schedule "
        "and NorthStar Insurance Limited. Cover is subject to the schedule, these terms and "
        "any endorsement shown as operative. All amounts are expressed in Pakistani Rupees "
        "(PKR).",
        "POLICY SCHEDULE VALUES",
        f"Base deductible: PKR {_money(product.base_deductible_pkr)}.",
        f"Coverage limit: PKR {_money(product.coverage_limit_pkr)}.",
        "The coverage limit is the most the insurer will pay in total for insured loss or "
        "damage arising during the period of insurance. The base deductible is borne by the "
        "insured for each claim unless an operative endorsement expressly changes it.",
        "SECTION 1 — DEFINITIONS",
        *definitions,
    ]


def _homesecure_water_page(
    product: CoverageProduct,
    access_basis_points: int,
) -> list[str]:
    return [
        "SECTION 4 — WATER DAMAGE",
        "4.1 This section applies to water escaping within the insured dwelling from pipes, "
        "tanks, valves, water heaters and fixtures that form part of the fixed plumbing system.",
        "4.2 Sudden and accidental escape of water from a fixed plumbing system is covered "
        "under this policy. Cover extends to the resulting damage to the dwelling and insured "
        f"contents, and to the reasonable cost of locating the source of the escape, subject to "
        f"a sub-limit of PKR {_sub_limit(product, access_basis_points)}. The base deductible of "
        f"PKR {_money(product.base_deductible_pkr)} applies to each claim under this clause.",
        "4.3 Loss or damage caused by gradual leakage, seepage or damage developing over weeks "
        "or months is excluded, whether or not the insured knew that moisture was accumulating.",
        "4.4 The cost of repairing the failed pipe, seal, tank or fitting is not covered, but "
        "reasonable work needed to reach the failed part and reinstate the opened surface is "
        "covered when the resulting water damage is otherwise insured.",
        "4.5 The insured must stop the supply when safe, protect property from further damage "
        "and retain the failed component for inspection where reasonably practicable.",
    ]


def _homesecure_basic(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Dwelling means the private residence at the insured address, including "
                "domestic garages and permanently fixed services within its boundary.",
                "1.2 Fixed plumbing system means permanently installed water supply and waste "
                "pipes, tanks, valves, water heaters and sanitary fixtures at the dwelling.",
                "1.3 Sudden and accidental means unforeseen, unintended and occurring at an "
                "identifiable point in time rather than developing progressively.",
            ],
        ),
        [
            "SECTION 2 — COVERAGE",
            "COVERAGE A — DWELLING",
            "2.1 The insurer will indemnify direct physical loss of or damage to the dwelling "
            "caused by an insured peril during the period of insurance.",
            "COVERAGE B — DOMESTIC CONTENTS",
            "2.2 Cover applies to household goods owned by the insured or a permanent member of "
            "the household while those goods are at the insured address.",
            "SECTION 3 — INSURED PERILS",
            "3.1 Fire, lightning, explosion, storm, impact by a vehicle, theft following forcible "
            "entry, and escape of water subject to Section 4 are insured perils.",
            "3.2 Accidental breakage, loss away from the dwelling and loss of rent are not insured "
            "unless added by endorsement.",
        ],
        _homesecure_water_page(product, 100),
        [
            "SECTION 5 — EXCLUSIONS",
            "5.1 Wear and tear, corrosion, rot, defective workmanship and gradual deterioration "
            "are excluded, as is the cost of routine maintenance.",
            "5.2 Loss while the dwelling is unoccupied for more than sixty consecutive days is "
            "excluded unless the insurer agreed otherwise in writing.",
            "5.3 Theft without evidence of forcible and violent entry is excluded.",
            "SECTION 6 — CLAIMS CONDITIONS",
            "6.1 The insured must notify the insurer as soon as reasonably practicable, provide "
            "evidence of ownership and loss, and permit inspection before permanent repairs.",
            "6.2 The insured must take reasonable emergency measures to prevent further damage. "
            "Those measures do not amount to admission of coverage by the insurer.",
            "6.3 Fraudulent statements or documents may result in refusal of the claim and the "
            "remedies permitted by law.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


def _homesecure_plus(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Dwelling means the private residence, domestic outbuildings, boundary walls "
                "and permanently installed services at the insured address.",
                "1.2 Contents means household goods, personal possessions and domestic appliances "
                "owned by the insured or a permanent member of the household.",
                "1.3 Fixed plumbing system means permanently installed supply and waste pipes, "
                "tanks, valves, water heaters and fixtures serving the dwelling.",
                "1.4 Sudden and accidental means unforeseen, unintended and occurring at an "
                "identifiable point in time rather than developing progressively.",
            ],
        ),
        [
            "SECTION 2 — COVERAGE",
            "COVERAGE A — BUILDINGS",
            "2.1 The insurer will indemnify direct physical loss of or damage to the dwelling and "
            "domestic outbuildings caused by an insured peril during the period of insurance.",
            "COVERAGE B — CONTENTS",
            "2.2 Household contents are covered at the insured address. Temporary removal for "
            "repair or cleaning is included when the insurer has first agreed to it.",
            "COVERAGE C — ALTERNATIVE ACCOMMODATION",
            "2.3 Reasonable additional accommodation expense is covered while an insured peril "
            "makes the dwelling uninhabitable and reinstatement is actively proceeding, subject "
            f"to a sub-limit of PKR {_sub_limit(product, 1_000)}.",
            "SECTION 3 — INSURED PERILS",
            "3.1 Fire, lightning, explosion, storm, flood, impact, theft following forcible entry "
            "and escape of water subject to Section 4 are insured perils.",
        ],
        _homesecure_water_page(product, 150),
        [
            "SECTION 5 — EXCLUSIONS",
            "5.1 Wear and tear, corrosion, rot, vermin, defective workmanship and gradual "
            "deterioration are excluded.",
            "5.2 Damage arising while the dwelling is unoccupied for more than sixty consecutive "
            "days is excluded unless the schedule records the insurer's agreement.",
            "5.3 Mechanical or electrical breakdown is excluded unless it results in a separately "
            "insured peril that damages other property.",
            "SECTION 6 — CLAIMS CONDITIONS",
            "6.1 Notice must be given as soon as reasonably practicable. The insured must provide "
            "invoices, photographs and reports reasonably requested to establish the loss.",
            "6.2 Damaged property must not be discarded before inspection unless retaining it "
            "would create a health or safety hazard.",
            "6.3 The insurer may repair, replace or pay the reasonable cost of reinstatement, "
            "subject always to the schedule and this wording.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


def _landlord_protect(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Let property means the residential building described in the schedule and "
                "occupied under a written tenancy agreement at the date of loss.",
                "1.2 Tenant means a person entitled to occupy the let property under that "
                "agreement.",
                "1.3 Rent means the lawful periodic payment stated in the tenancy agreement, "
                "excluding deposits, service charges and utilities.",
            ],
        ),
        [
            "SECTION 2 — LANDLORD COVER",
            "COVERAGE A — BUILDINGS",
            "2.1 The insurer will indemnify direct physical loss of or damage to the let property "
            "caused by fire, storm, impact, malicious damage or another insured peril.",
            "COVERAGE B — LANDLORD FIXTURES",
            "2.2 Permanently installed kitchens, sanitary fittings and floor coverings owned by "
            "the landlord are included. A tenant's possessions are not insured.",
            "COVERAGE C — LOSS OF RENT",
            "2.3 Rent genuinely lost while insured damage makes the premises uninhabitable is "
            "covered for the reasonable reinstatement period, subject to a sub-limit of PKR "
            f"{_sub_limit(product, 400)}.",
        ],
        [
            "SECTION 3 — WATER AT LET PROPERTY",
            "3.1 Resulting building damage from a pipe or tank that bursts without warning is "
            "covered when the landlord can identify when the discharge occurred. The base "
            f"deductible of PKR {_money(product.base_deductible_pkr)} applies to each claim.",
            "3.2 Repeated dampness, staining between inspections, failed sealant and leakage "
            "caused "
            "by deferred landlord maintenance are excluded as gradual causes.",
            "3.3 The landlord must keep inspection records and arrange prompt repair after a "
            "tenant "
            "reports a plumbing defect. Failure to act may reduce the amount payable to the extent "
            "that the delay increased the damage.",
            "3.4 A tenant's water charges and the cost of replacing the defective plumbing part "
            "are "
            "not insured property damage.",
        ],
        [
            "SECTION 4 — EXCLUSIONS",
            "4.1 Wear and tear, corrosion, infestation and damage caused by an unlawful use of the "
            "premises are excluded.",
            "4.2 Malicious damage by a tenant is excluded unless the schedule shows the relevant "
            "endorsement as operative.",
            "SECTION 5 — CLAIMS CONDITIONS",
            "5.1 The landlord must produce the tenancy agreement, rent ledger and inspection "
            "record when reasonably requested.",
            "5.2 The insurer must be told promptly of criminal damage, proceedings concerning the "
            "tenancy, or circumstances likely to delay reinstatement.",
            "5.3 Settlement for lost rent ends when the premises are reasonably fit for "
            "occupation.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


def _sme_property_shield(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Premises means the business location described in the schedule.",
                "1.2 Business contents means machinery, office equipment, furniture and stock "
                "owned "
                "by the insured or held in trust in the ordinary course of business.",
                "1.3 Business interruption means loss of gross profit caused solely by insured "
                "physical damage that interrupts operations at the premises.",
            ],
        ),
        [
            "SECTION 2 — PROPERTY DAMAGE",
            "COVERAGE A — BUILDINGS AND CONTENTS",
            "2.1 The insurer will indemnify physical loss of or damage to insured property caused "
            "by fire, explosion, storm, impact, theft following forcible entry or accidental "
            "escape "
            "from an automatic sprinkler installation. The base deductible of PKR "
            f"{_money(product.base_deductible_pkr)} applies to each claim.",
            "COVERAGE B — BUSINESS INTERRUPTION",
            "2.2 Cover applies during the reasonable period required to restore operations after "
            "insured property damage, provided accounting records support the claimed loss. "
            f"Professional fees are subject to a sub-limit of PKR {_sub_limit(product, 200)}.",
            "2.3 Utility failure away from the premises does not constitute physical damage at the "
            "premises unless an endorsement expressly provides otherwise.",
        ],
        [
            "SECTION 3 — WATER AND SPRINKLER DISCHARGE",
            "3.1 Accidental discharge from a maintained automatic sprinkler installation is "
            "covered "
            "where a valve, head or supply pipe fails during the period of insurance. Reasonable "
            "removal of damaged stock is subject to a sub-limit of PKR "
            f"{_sub_limit(product, 400)}.",
            "3.2 Stock must be stored on pallets or shelving where the risk survey requires it. "
            "Damage that would have been avoided by the specified clearance is not covered.",
            "3.3 Rain entering through an open door, unfinished roof or known defective flashing "
            "is "
            "excluded. Water used by the fire brigade to control an insured fire is covered as "
            "resulting damage under the fire peril.",
            "3.4 Condensation, humidity, repeated seepage and deterioration of chilled or "
            "perishable "
            "stock are excluded unless caused directly by another insured peril.",
        ],
        [
            "SECTION 4 — RISK CONDITIONS",
            "4.1 Fire alarms, extinguishers and sprinkler controls must be maintained under a "
            "written "
            "service programme and defects must be remedied without unreasonable delay.",
            "4.2 Stock records, purchase invoices and annual inventory counts must be retained for "
            "claim substantiation.",
            "SECTION 5 — EXCLUSIONS",
            "5.1 Unexplained shortage, inventory error, faulty processing and gradual "
            "deterioration "
            "are excluded.",
            "SECTION 6 — CLAIMS CONDITIONS",
            "6.1 The insured must preserve damaged stock for survey unless disposal is required "
            "by a "
            "public authority or necessary to prevent danger.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


def _fire_protection(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Fire means actual ignition accompanied by flame or glowing combustion outside "
                "a place intended to contain it.",
                "1.2 Explosion means a sudden violent release of energy from internal pressure, "
                "excluding deliberate pressure testing.",
                "1.3 Insured property means the buildings and contents described in the schedule.",
            ],
        ),
        [
            "SECTION 2 — FIRE COVER",
            "COVERAGE A — MATERIAL DAMAGE",
            "2.1 The insurer will indemnify direct physical loss of or damage to insured property "
            "caused by fire, lightning or explosion during the period of insurance. The base "
            f"deductible of PKR {_money(product.base_deductible_pkr)} applies to each claim.",
            "2.2 Smoke damage is covered when it results directly from an insured fire. Scorching "
            "without ignition is excluded unless an endorsement says otherwise.",
            "2.3 Reasonable demolition and debris removal required for reinstatement is covered, "
            f"subject to a sub-limit of PKR {_sub_limit(product, 250)}.",
            "SECTION 3 — FIRE SAFETY CONDITIONS",
            "3.1 Fire protection equipment must remain accessible, charged and serviced in "
            "accordance with the manufacturer's instructions.",
            "3.2 Hot work requires a permit, removal of combustible material and a fire watch "
            "after "
            "the work ends.",
        ],
        [
            "SECTION 4 — WATER FOLLOWING FIRE",
            "4.1 Water or suppressant used by the fire brigade, an automatic sprinkler or a person "
            "acting reasonably to control an insured fire is covered as resulting fire damage.",
            "4.2 Escape from domestic or process plumbing where no insured fire has occurred is "
            "not "
            "an insured peril under this wording and requires separate property cover.",
            "4.3 Corrosion, mould or deterioration that continues after the fire has been "
            "controlled "
            "is excluded to the extent reasonable drying and protection would have prevented it.",
            "4.4 The insured must permit safe removal of standing water and soot as part of "
            "emergency "
            "mitigation, without prejudicing investigation of the fire's origin.",
        ],
        [
            "SECTION 5 — EXCLUSIONS",
            "5.1 Electrical arcing confined to the item in which it originates is excluded, but a "
            "resulting fire that damages other insured property remains covered.",
            "5.2 Deliberate burning by or at the direction of the insured is excluded.",
            "5.3 War, nuclear contamination and seizure by public authority are excluded.",
            "SECTION 6 — CLAIMS CONDITIONS",
            "6.1 The insured must notify emergency services where appropriate, preserve the scene "
            "and cooperate with the cause-and-origin investigation.",
            "6.2 Repairs beyond emergency protection require the insurer's agreement unless delay "
            "would create an immediate danger.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


def _flood_protection(product: CoverageProduct) -> PolicyForm:
    pages = [
        _opening_page(
            product,
            [
                "1.1 Flood means the covering of normally dry land by water escaping or released "
                "from the normal confines of a river, canal, reservoir or drainage channel.",
                "1.2 Surface water means rainwater that accumulates at ground level and enters the "
                "insured premises despite maintained drainage and reasonable flood protection.",
                "1.3 Flood event means one continuous occurrence or series of occurrences arising "
                "from the same weather system.",
            ],
        ),
        [
            "SECTION 2 — FLOOD COVER",
            "COVERAGE A — BUILDINGS AND CONTENTS",
            "2.1 The insurer will indemnify direct physical loss of or damage to insured buildings "
            "and contents caused by flood or surface water during the period of insurance. The "
            f"base deductible of PKR {_money(product.base_deductible_pkr)} applies to each claim.",
            "2.2 Reasonable removal of silt and debris from the insured premises is included when "
            "it is necessary to reinstate insured property, subject to a sub-limit of PKR "
            f"{_sub_limit(product, 200)}.",
            "SECTION 3 — FLOOD PRECAUTIONS",
            "3.1 Where the risk survey specifies barriers, pumps or raised storage, the insured "
            "must "
            "keep those protections serviceable and deploy them when a warning permits.",
            "3.2 The insured must not knowingly obstruct drains serving the premises.",
        ],
        [
            "SECTION 4 — WATER NOT TREATED AS FLOOD",
            "4.1 Water escaping from a pipe, tank, appliance or sprinkler entirely within the "
            "premises is not flood, even when it accumulates across a floor.",
            "4.2 Repeated entry through cracked walls, failed waterproofing or an opening left "
            "unsealed is excluded as seepage or lack of maintenance rather than a flood event.",
            "4.3 Backflow from a public drain is covered only when it occurs at the same time as "
            "an "
            "insured flood event affecting the vicinity of the premises.",
            "4.4 Tidal movement and coastal inundation are covered only when the schedule "
            "expressly "
            "includes coastal flood.",
        ],
        [
            "SECTION 5 — EXCLUSIONS",
            "5.1 Loss of market, delay, contamination not caused by flood water, and erosion of "
            "bare "
            "land are excluded.",
            "5.2 Property in a basement is excluded where a risk requirement specified raised "
            "storage and that requirement was not followed.",
            "SECTION 6 — CLAIMS CONDITIONS",
            "6.1 The insured must record the highest visible water line where safe, photograph "
            "damaged areas and separate recoverable property from waste.",
            "6.2 Pumps and emergency drying may be used immediately. Permanent reinstatement must "
            "await inspection unless the insurer agrees otherwise.",
        ],
    ]
    return PolicyForm(product.product_name, f"NS-{product.product_code}-2026", pages)


_PRODUCT_BUILDERS: dict[str, Callable[[CoverageProduct], PolicyForm]] = {
    "HSB": _homesecure_basic,
    "HSP": _homesecure_plus,
    "LLP": _landlord_protect,
    "SPS": _sme_property_shield,
    "FIR": _fire_protection,
    "FLD": _flood_protection,
}


_COMPANION_FORMS: dict[str, PolicyForm] = {
    "General_Conditions_2026.pdf": PolicyForm(
        "General Conditions",
        "NS-GC-2026",
        [
            [
                "GENERAL CONDITIONS",
                "These conditions apply to every NorthStar property policy unless an individual "
                "wording or endorsement expressly replaces a condition.",
                "SECTION 1 — THE CONTRACT",
                "1.1 The policy comprises the schedule, the applicable product wording, these "
                "general conditions and every endorsement shown as operative.",
                "1.2 Changes are effective only when recorded by the insurer in writing.",
                "1.3 Headings aid navigation and do not alter the meaning of a clause.",
            ],
            [
                "SECTION 2 — DUTY OF CARE",
                "2.1 The insured must take reasonable care to maintain insured property and comply "
                "with statutory safety requirements.",
                "2.2 A defect or danger discovered during the period of insurance must be remedied "
                "within a reasonable time. Cover is not a maintenance contract.",
                "2.3 Material changes in occupation, construction, use or vacancy must be "
                "disclosed "
                "as soon as reasonably practicable.",
            ],
            [
                "SECTION 3 — CLAIM COOPERATION",
                "3.1 The insured must provide truthful information, preserve relevant evidence and "
                "allow reasonable access for inspection.",
                "3.2 The insurer may take over the defence or settlement of a third-party demand "
                "and "
                "may pursue recovery in the insured's name after paying a claim.",
                "3.3 No admission, offer or disposal of salvage may be made without consent where "
                "doing so would prejudice investigation or recovery.",
            ],
            [
                "SECTION 4 — CANCELLATION AND DISPUTES",
                "4.1 Cancellation takes effect in accordance with the notice stated in the "
                "schedule "
                "and does not affect a valid claim arising before cancellation.",
                "4.2 A complaint should first be sent to the insurer's complaints function with "
                "the "
                "policy and claim references.",
                "4.3 Governing law and jurisdiction are those stated in the schedule.",
            ],
        ],
        document_type="Conditions Form",
    ),
    "Claims_Procedure_2026.pdf": PolicyForm(
        "Property Claims Procedure",
        "NS-CP-2026",
        [
            [
                "PROPERTY CLAIMS PROCEDURE",
                "This procedure explains the evidence and steps normally required after property "
                "loss. It does not extend or restrict cover under an applicable wording.",
                "SECTION 1 — FIRST RESPONSE",
                "1.1 Protect life first and contact emergency services where necessary.",
                "1.2 Take reasonable steps to stop further damage without putting any person at "
                "risk.",
                "1.3 Notify NorthStar promptly with the policy number, location, date, apparent "
                "cause "
                "and a safe contact method.",
            ],
            [
                "SECTION 2 — DOCUMENTING THE LOSS",
                "2.1 Photograph the affected area before cleanup where safe and keep an inventory "
                "of "
                "damaged property.",
                "2.2 Retain invoices, ownership records, repair estimates and professional reports "
                "that explain the cause and extent of damage.",
                "2.3 Do not discard a failed component or damaged item before inspection unless it "
                "presents a health hazard or a public authority requires disposal.",
            ],
            [
                "SECTION 3 — ASSESSMENT",
                "3.1 The claims handler will confirm the applicable wording and may appoint a loss "
                "adjuster, engineer, plumber or other specialist.",
                "3.2 An inspection considers cause, timing, reasonable mitigation, repair scope "
                "and "
                "whether exclusions or endorsements apply.",
                "3.3 Requests for information will identify what is needed and why. The insured "
                "must "
                "have a reasonable opportunity to respond.",
            ],
            [
                "SECTION 4 — OUTCOME AND REVIEW",
                "4.1 The insurer will explain the coverage position by reference to the policy "
                "wording and the evidence available.",
                "4.2 Any payment method, repair authority or reservation of rights will be "
                "confirmed "
                "in writing.",
                "4.3 The insured may provide further evidence or use the complaints process if the "
                "coverage position is disputed.",
            ],
        ],
        document_type="Claims Procedure",
    ),
    "Property_Endorsements_2026.pdf": PolicyForm(
        "Property Endorsements",
        "NS-PE-2026",
        [
            [
                "PROPERTY ENDORSEMENTS",
                "An endorsement applies only when its number appears in the policy schedule. It "
                "changes the wording only to the extent stated below.",
                "ENDORSEMENT 1 — UNOCCUPANCY PRECAUTIONS",
                "After thirty consecutive days without overnight occupation, the insured must shut "
                "off the mains water supply, drain exposed pipework where practicable and arrange "
                "a "
                "recorded internal inspection at least every seven days.",
            ],
            [
                "ENDORSEMENT 2 — MALICIOUS DAMAGE BY TENANT",
                "Malicious physical damage by a tenant is included where a written tenancy "
                "agreement "
                "and pre-tenancy condition inventory exist and the matter is reported to police.",
                "2.1 Theft by a tenant, unpaid rent, cleaning, redecoration and ordinary wear "
                "remain "
                "excluded.",
                "2.2 The landlord must take reasonable steps to recover possession and prevent "
                "continuing damage after discovery.",
            ],
            [
                "ENDORSEMENT 3 — SPRINKLER MAINTENANCE",
                "Automatic sprinkler valves must be secured in their operating position and the "
                "system must be tested by a competent contractor at the prescribed intervals.",
                "3.1 Any impairment lasting beyond the period stated in the schedule must be "
                "notified "
                "to the insurer and accompanied by temporary fire precautions.",
                "3.2 Accidental discharge during an authorised test remains subject to the "
                "applicable "
                "product wording.",
            ],
            [
                "ENDORSEMENT 4 — FLOOD RESILIENCE",
                "Flood barriers, non-return valves and pumps identified in the risk survey must be "
                "maintained and deployed when a warning gives reasonable time to do so.",
                "4.1 Failure to deploy a measure does not affect a claim where deployment would "
                "have "
                "placed a person at risk or no effective warning was available.",
                "4.2 All other terms, conditions and exclusions remain unchanged.",
            ],
        ],
        document_type="Endorsement Schedule",
    ),
    "Exclusions_Schedule_2026.pdf": PolicyForm(
        "General Exclusions Schedule",
        "NS-GES-2026",
        [
            [
                "GENERAL EXCLUSIONS SCHEDULE",
                "This schedule applies in addition to exclusions in the product wording. The "
                "product "
                "wording prevails where it expressly grants cover that this schedule would remove.",
                "SECTION 1 — WAR AND NUCLEAR RISKS",
                "1.1 War, invasion, civil war, rebellion, insurrection and military or usurped "
                "power "
                "are excluded.",
                "1.2 Ionising radiation and contamination by nuclear fuel or nuclear waste are "
                "excluded.",
            ],
            [
                "SECTION 2 — GRADUAL AND INHERENT CAUSES",
                "2.1 Wear and tear, corrosion, rot, shrinkage, inherent vice and gradual "
                "deterioration "
                "are excluded.",
                "2.2 Damp, condensation, fungus, mould, infestation, seepage and repeated leakage "
                "are "
                "excluded unless the product wording expressly provides otherwise.",
                "2.3 The cost of correcting defective design, materials or workmanship is "
                "excluded, "
                "though separately insured resulting damage is considered under its own cause.",
            ],
            [
                "SECTION 3 — CONDUCT AND AUTHORITY",
                "3.1 A deliberate or wilful act by the insured intended to cause loss is excluded.",
                "3.2 Confiscation, requisition or destruction by order of a public authority is "
                "excluded, except reasonable destruction undertaken to prevent spread of an "
                "insured fire.",
                "3.3 Fraudulent claims are subject to the remedies stated in the general "
                "conditions "
                "and applicable law.",
            ],
            [
                "SECTION 4 — FINANCIAL AND DIGITAL LOSS",
                "4.1 Loss of market, loss of use and other indirect loss are excluded unless an "
                "applicable coverage section expressly includes them.",
                "4.2 Loss, alteration or unavailability of electronic data is excluded unless it "
                "is "
                "the direct result of insured physical damage to insured equipment.",
                "4.3 Fines, penalties and punitive awards are excluded.",
            ],
        ],
        document_type="Exclusions Schedule",
    ),
}


def policy_documents() -> dict[str, PolicyForm]:
    """Return all corpus forms, deriving every product filename from the catalogue."""
    product_codes = {product.product_code for product in COVERAGE_PRODUCTS}
    if product_codes != _PRODUCT_BUILDERS.keys():
        missing = sorted(product_codes - _PRODUCT_BUILDERS.keys())
        unexpected = sorted(_PRODUCT_BUILDERS.keys() - product_codes)
        raise ValueError(
            "Policy builders do not match catalogue: "
            f"missing={missing}, unexpected={unexpected}"
        )

    documents = {
        product.policy_document: _PRODUCT_BUILDERS[product.product_code](product)
        for product in COVERAGE_PRODUCTS
    }
    documents.update(_COMPANION_FORMS)
    return documents


def generate_policy_corpus(output_dir: Path | None = None) -> list[Path]:
    """Write the deterministic policy corpus and return its paths in filename order."""
    destination = output_dir or get_settings().policy_dir
    destination.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, form in sorted(policy_documents().items()):
        path = destination / filename
        render_policy_pdf(
            path,
            form.title,
            form.form_number,
            form.pages,
            document_type=form.document_type,
        )
        written.append(path)
    return written


def main() -> None:
    for path in generate_policy_corpus():
        print(path.name)


if __name__ == "__main__":
    main()
