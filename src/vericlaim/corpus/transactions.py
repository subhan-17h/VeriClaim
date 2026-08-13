"""Deterministic in-memory generation of the operational transaction corpus.

The public entry point in this module performs no I/O. It creates one local
``random.Random`` instance from the supplied seed and returns frozen records whose
field names match the reviewed ``ops`` DDL exactly.

Policy references use ``POL-2026-000001``. Claim references use ``CLM-1000`` and
therefore satisfy the configured ``CLM-\\d{3,}`` corpus convention.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from vericlaim.corpus.catalog import (
    ADJUSTERS,
    CLAIM_STATUSES,
    COVERAGE_PRODUCTS,
    CUSTOMER_TYPES,
    PAYMENT_TYPES,
    PERILS,
    POLICY_STATUSES,
    REGIONS,
)

CUSTOMER_COUNT = 4_000
POLICY_COUNT = 6_000
CLAIM_COUNT = 12_000
PAYMENT_COUNT = 9_000

REPORT_START = date(2026, 1, 1)
REPORT_END = date(2026, 6, 30)
CENT = Decimal("0.01")

# Products span a five-fold range of coverage limits while claim severity is peril-led.
# These reviewed ranges keep expected annual premium in the same order of magnitude
# across products without abandoning the basis-point relationship to sum insured.
_PREMIUM_BASIS_POINT_RANGES: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "HSB": (850, 1_286),
        "HSP": (468, 709),
        "LLP": (226, 345),
        "SPS": (162, 246),
        "FIR": (212, 323),
        "FLD": (269, 409),
    }
)


@dataclass(frozen=True, slots=True)
class CustomerRow:
    customer_id: int
    customer_name: str
    customer_type: str
    city: str
    region_id: int
    email: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyRow:
    policy_id: int
    policy_number: str
    customer_id: int
    product_id: int
    region_id: int
    inception_date: date
    expiry_date: date
    status: str
    sum_insured_pkr: Decimal
    deductible_pkr: Decimal
    annual_premium_pkr: Decimal


@dataclass(frozen=True, slots=True)
class ClaimRow:
    claim_id: int
    claim_number: str
    policy_id: int
    adjuster_id: int | None
    region_id: int
    peril: str
    date_of_loss: date
    report_date: date
    closed_date: date | None
    status: str
    cause_description: str
    incurred_amount_pkr: Decimal
    paid_amount_pkr: Decimal
    reserve_amount_pkr: Decimal
    deductible_applied_pkr: Decimal


@dataclass(frozen=True, slots=True)
class ClaimPaymentRow:
    payment_id: int
    claim_id: int
    payment_date: date
    payment_type: str
    amount_pkr: Decimal


@dataclass(frozen=True, slots=True)
class TransactionRows:
    customers: tuple[CustomerRow, ...]
    policies: tuple[PolicyRow, ...]
    claims: tuple[ClaimRow, ...]
    claim_payments: tuple[ClaimPaymentRow, ...]


@dataclass(frozen=True, slots=True)
class ClaimRate:
    """Frequency and gross-loss severity for one month, region, and peril."""

    frequency_weight: int
    severity_min_pkr: Decimal
    severity_max_pkr: Decimal


# These baseline rows are expanded across every month and region. The resulting public
# CLAIM_RATE_TABLE is keyed only by the complete (month, region_id, peril) key.
_BASE_PERIL_RATES: Mapping[str, ClaimRate] = MappingProxyType(
    {
        "water_damage": ClaimRate(25, Decimal("130000.00"), Decimal("160000.00")),
        "fire": ClaimRate(8, Decimal("230000.00"), Decimal("270000.00")),
        "theft": ClaimRate(15, Decimal("120000.00"), Decimal("150000.00")),
        "storm": ClaimRate(10, Decimal("150000.00"), Decimal("180000.00")),
        "impact": ClaimRate(12, Decimal("90000.00"), Decimal("110000.00")),
        "liability": ClaimRate(6, Decimal("170000.00"), Decimal("200000.00")),
    }
)

# Planted trends are reviewed data rows, not date branches in the generator. Adding or
# replacing a row here changes both the selection weight and severity for that cell.
PLANTED_RATE_ROWS: Mapping[tuple[int, int, str], ClaimRate] = MappingProxyType(
    {
        (3, 1, "water_damage"): ClaimRate(
            75, Decimal("180000.00"), Decimal("220000.00")
        ),
        (3, 2, "water_damage"): ClaimRate(
            75, Decimal("180000.00"), Decimal("220000.00")
        ),
        (3, 4, "water_damage"): ClaimRate(
            75, Decimal("180000.00"), Decimal("220000.00")
        ),
        (3, 5, "water_damage"): ClaimRate(
            75, Decimal("180000.00"), Decimal("220000.00")
        ),
        (4, 3, "theft"): ClaimRate(55, Decimal("170000.00"), Decimal("210000.00")),
        (4, 6, "theft"): ClaimRate(55, Decimal("170000.00"), Decimal("210000.00")),
        (4, 7, "theft"): ClaimRate(55, Decimal("170000.00"), Decimal("210000.00")),
        (4, 9, "theft"): ClaimRate(55, Decimal("170000.00"), Decimal("210000.00")),
    }
)


def _build_claim_rate_table() -> Mapping[tuple[int, int, str], ClaimRate]:
    rows = {
        (month, region.region_id, peril): _BASE_PERIL_RATES[peril]
        for month in range(1, 7)
        for region in REGIONS
        for peril in PERILS
    }
    rows.update(PLANTED_RATE_ROWS)
    return MappingProxyType(rows)


CLAIM_RATE_TABLE = _build_claim_rate_table()


# Committed locale pools replace Faker so a dependency update cannot rewrite the corpus.
PAKISTANI_GIVEN_NAMES: tuple[str, ...] = (
    "Aamir",
    "Aamna",
    "Adeel",
    "Adnan",
    "Afaq",
    "Ahsan",
    "Ali",
    "Alina",
    "Amir",
    "Amna",
    "Anam",
    "Aqsa",
    "Arham",
    "Arsalan",
    "Asad",
    "Ayesha",
    "Azhar",
    "Bilal",
    "Danish",
    "Eman",
    "Faheem",
    "Fahad",
    "Farah",
    "Farhan",
    "Fatima",
    "Fiza",
    "Hafsa",
    "Hamza",
    "Hania",
    "Hassan",
    "Hina",
    "Hira",
    "Humaira",
    "Imran",
    "Iqra",
    "Javeria",
    "Kashif",
    "Komal",
    "Laiba",
    "Mahnoor",
    "Maham",
    "Mariam",
    "Mehwish",
    "Mohsin",
    "Nabeel",
    "Nadia",
    "Nida",
    "Noor",
    "Omer",
    "Rabia",
    "Rafay",
    "Raza",
    "Rehan",
    "Rida",
    "Saad",
    "Saba",
    "Sadia",
    "Salman",
    "Sana",
    "Shahbaz",
    "Shazia",
    "Sohail",
    "Taimur",
    "Talha",
    "Umar",
    "Usman",
    "Waleed",
    "Yasir",
    "Zain",
    "Zara",
    "Zoya",
)

PAKISTANI_FAMILY_NAMES: tuple[str, ...] = (
    "Abbasi",
    "Abid",
    "Ahmed",
    "Akhtar",
    "Ali",
    "Ansari",
    "Aslam",
    "Awan",
    "Aziz",
    "Bajwa",
    "Baloch",
    "Baig",
    "Bhatti",
    "Butt",
    "Chaudhry",
    "Dar",
    "Farooq",
    "Fatima",
    "Gill",
    "Haider",
    "Hashmi",
    "Hussain",
    "Iqbal",
    "Javed",
    "Khan",
    "Khokhar",
    "Lodhi",
    "Mahmood",
    "Malik",
    "Memon",
    "Minhas",
    "Mir",
    "Mirza",
    "Mughal",
    "Nadeem",
    "Naqvi",
    "Nawaz",
    "Paracha",
    "Qazi",
    "Qureshi",
    "Rana",
    "Rauf",
    "Raza",
    "Rehman",
    "Riaz",
    "Rizvi",
    "Saleem",
    "Shah",
    "Shaikh",
    "Sheikh",
    "Siddiqui",
    "Soomro",
    "Syed",
    "Tariq",
    "Warraich",
    "Yousaf",
    "Zafar",
    "Zia",
)

PAKISTANI_BUSINESS_NAMES: tuple[str, ...] = (
    "Aabpara Office Supplies Pvt Ltd",
    "Abdali Cold Storage Pvt Ltd",
    "Amanah Home Textiles Pvt Ltd",
    "Anarkali Books and Stationery",
    "Arif Brothers Hardware",
    "Badshahi Catering Services Pvt Ltd",
    "Baghban Nursery and Farms",
    "Bahria Auto Parts Pvt Ltd",
    "Blue Area Business Services Pvt Ltd",
    "Canal View Furnishings Pvt Ltd",
    "Capital Electrical Works",
    "Capital Packaging Industries Pvt Ltd",
    "Chenab Cotton Traders",
    "Clifton Marine Supplies Pvt Ltd",
    "Data Darbar Foods Pvt Ltd",
    "Defence Homeware Traders",
    "Empress Market Spices",
    "Faisal Town Medical Supplies",
    "Ferozepur Engineering Works Pvt Ltd",
    "Garden East Printing House",
    "Gulberg Business Machines Pvt Ltd",
    "Gulshan Furniture Works",
    "Gulshan Paper Products Pvt Ltd",
    "Iqbal Town Pharmacy",
    "Islamabad Fresh Foods Pvt Ltd",
    "Jinnah Avenue Optics",
    "Johar Town Diagnostics Pvt Ltd",
    "Kahna Agricultural Supplies",
    "Karachi Coast Logistics Pvt Ltd",
    "Kashmir Road Garments Pvt Ltd",
    "Korangi Engineering Services Pvt Ltd",
    "Lakshmi Metal Works",
    "Landhi Safety Equipment Pvt Ltd",
    "Liberty Leather Goods Pvt Ltd",
    "Mall Road Office Systems",
    "Margalla Building Materials Pvt Ltd",
    "Mazang Motor Works",
    "Model Town Bakers Pvt Ltd",
    "Mohatta Arts and Crafts",
    "Murree Road Electronics Pvt Ltd",
    "Nazimabad Textile Traders",
    "North Nazimabad Medical Centre",
    "Orangi Packaging Works",
    "Pakistan Chowk Book Depot",
    "Pindi Wholesale Foods Pvt Ltd",
    "Port Qasim Industrial Supplies",
    "Raiwind Dairy Products Pvt Ltd",
    "Rawal Lake Hospitality Pvt Ltd",
    "Saddar Camera and Optics",
    "Samnabad Auto Services",
    "Shah Alam Household Goods",
    "Shahrah e Faisal Travel Services",
    "Shalimar Garden Services Pvt Ltd",
    "Sindh Industrial Tools Pvt Ltd",
    "Sundar Packaging Works Pvt Ltd",
    "Tariq Road Apparel Pvt Ltd",
    "Thokar Transport Services Pvt Ltd",
    "Township Plastic Products",
    "Urdu Bazaar Publishers",
    "Walled City Handicrafts",
    "Zamzama Interior Works Pvt Ltd",
)

WATER_DAMAGE_CAUSES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "sudden": (
            "Sudden escape of water from a burst pipe under the kitchen sink",
            "Sudden discharge from a ruptured bathroom supply hose",
            "Burst overhead water tank pipe flooded two rooms without warning",
            "Washing machine inlet hose split suddenly during a normal cycle",
            "Sudden overflow followed a blocked internal drain during heavy use",
            "Water heater connection failed suddenly and soaked the adjoining wall",
        ),
        "gradual": (
            "Gradual seepage through the roof after months of unresolved dampness",
            "Long-term concealed pipe leak caused progressive wall staining",
            "Recurring bathroom seepage damaged plaster over an extended period",
            "Slow foundation moisture ingress produced gradual floor damage",
            "Long-standing roof crack allowed repeated rainwater penetration",
            "Gradual leakage behind the wash basin caused timber decay",
        ),
    }
)

CAUSE_DESCRIPTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "fire": (
            "Electrical short circuit ignited wiring in the distribution board",
            "Cooking oil fire spread from the kitchen to fitted cabinets",
            "Warehouse stock caught fire after machinery overheated",
            "Generator wiring fault caused a fire in the service area",
            "Flames from an adjacent unit damaged the insured premises",
            "Small electrical fire caused smoke and heat damage throughout the room",
        ),
        "theft": (
            "Forced entry through a rear door and theft of office equipment",
            "Shop shutters were cut overnight and insured stock was removed",
            "Burglars broke a ground-floor window and stole household electronics",
            "Store inventory was taken after locks on the loading entrance were forced",
            "Intruders entered through the roof and removed tools and machinery",
            "Office computers were stolen following visible forced entry",
        ),
        "storm": (
            "Windstorm lifted roof sheets and rain damaged the interior",
            "Severe rainfall overwhelmed roof drainage and damaged ceilings",
            "Falling tree branches damaged the boundary wall during a storm",
            "High winds broke windows and drove rain into the premises",
            "Storm debris struck rooftop equipment and damaged its housing",
            "Heavy rain and wind displaced tiles from the insured roof",
        ),
        "impact": (
            "Vehicle struck the boundary wall and damaged the entrance gate",
            "Falling rooftop equipment damaged the covered structure",
            "Delivery truck reversed into the insured shop frontage",
            "Construction debris from a neighbouring site struck the roof",
            "Tree limb fell onto the garage and damaged roof supports",
            "Forklift collision damaged a warehouse partition and stored goods",
        ),
        "liability": (
            "Visitor reported injury after slipping on a wet tiled entrance",
            "Neighbour alleged property damage from work at the insured premises",
            "Customer reported injury from a loose fitting inside the shop",
            "Tenant alleged damage to belongings from building maintenance work",
            "Delivery contractor reported injury in the insured loading area",
            "Third party alleged wall damage caused by the insured property",
        ),
    }
)

_STATUS_COUNTS: Mapping[str, int] = MappingProxyType(
    {
        "closed": 4_800,
        "open": 4_200,
        "reopened": 600,
        "denied": 1_440,
        "withdrawn": 960,
    }
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _to_cents(value: Decimal) -> int:
    cents = value / CENT
    if cents != cents.to_integral_value():
        raise ValueError(f"money value must have at most two decimal places: {value}")
    return int(cents)


def _from_cents(value: int) -> Decimal:
    return (Decimal(value) * CENT).quantize(CENT)


def _random_money(rng: random.Random, minimum: Decimal, maximum: Decimal) -> Decimal:
    return _from_cents(rng.randint(_to_cents(minimum), _to_cents(maximum)))


def _validate_rate_table(
    rate_table: Mapping[tuple[int, int, str], ClaimRate],
) -> tuple[tuple[tuple[int, int, str], ClaimRate], ...]:
    expected = set(CLAIM_RATE_TABLE)
    actual = set(rate_table)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"rate table keys differ; missing={missing}, extra={extra}")

    rows = tuple(sorted(rate_table.items()))
    for key, rate in rows:
        if rate.frequency_weight <= 0:
            raise ValueError(f"frequency weight must be positive for {key}")
        if rate.severity_min_pkr < 0 or rate.severity_max_pkr < rate.severity_min_pkr:
            raise ValueError(f"invalid severity range for {key}")
        _to_cents(rate.severity_min_pkr)
        _to_cents(rate.severity_max_pkr)
    return rows


def _email_local_part(name: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "." for character in name)
    return normalized.strip(".")


def _generate_customers(rng: random.Random) -> tuple[CustomerRow, ...]:
    rows: list[CustomerRow] = []
    created_epoch = datetime(2020, 1, 1)
    for offset in range(CUSTOMER_COUNT):
        customer_id = 100_001 + offset
        region = rng.choice(REGIONS)
        customer_type = rng.choices(CUSTOMER_TYPES, weights=(4, 1), k=1)[0]
        if customer_type == "individual":
            name = f"{rng.choice(PAKISTANI_GIVEN_NAMES)} {rng.choice(PAKISTANI_FAMILY_NAMES)}"
        else:
            name = rng.choice(PAKISTANI_BUSINESS_NAMES)

        email = None
        if rng.randrange(100) >= 8:
            email = f"{_email_local_part(name)}.{customer_id}@example.pk"
        created_at = created_epoch + timedelta(
            days=rng.randrange(2_100), minutes=rng.randrange(24 * 60)
        )
        rows.append(
            CustomerRow(
                customer_id=customer_id,
                customer_name=name,
                customer_type=customer_type,
                city=region.city,
                region_id=region.region_id,
                email=email,
                created_at=created_at,
            )
        )
    return tuple(rows)


def _generate_policies(
    rng: random.Random, customers: tuple[CustomerRow, ...]
) -> tuple[PolicyRow, ...]:
    rows: list[PolicyRow] = []
    inception_epoch = date(2025, 1, 1)
    customer_order = list(customers)
    rng.shuffle(customer_order)

    for offset in range(POLICY_COUNT):
        policy_id = 200_001 + offset
        customer = customer_order[offset] if offset < len(customers) else rng.choice(customers)
        product = rng.choice(COVERAGE_PRODUCTS)
        region_id = (
            customer.region_id if rng.randrange(100) < 82 else rng.choice(REGIONS).region_id
        )
        inception_date = inception_epoch + timedelta(days=rng.randrange(181))
        expiry_date = inception_date + timedelta(days=729)
        status = rng.choices(POLICY_STATUSES, weights=(15, 3, 2), k=1)[0]

        insured_basis_points = rng.randrange(5_000, 10_001)
        sum_insured = _money(
            product.coverage_limit_pkr * Decimal(insured_basis_points) / Decimal(10_000)
        )
        deductible_factor = Decimal(rng.choice((100, 125, 150))) / Decimal(100)
        deductible = _money(product.base_deductible_pkr * deductible_factor)
        premium_basis_points = rng.randrange(
            *_PREMIUM_BASIS_POINT_RANGES[product.product_code]
        )
        annual_premium = _money(
            sum_insured * Decimal(premium_basis_points) / Decimal(10_000)
        )

        rows.append(
            PolicyRow(
                policy_id=policy_id,
                policy_number=f"POL-2026-{offset + 1:06d}",
                customer_id=customer.customer_id,
                product_id=product.product_id,
                region_id=region_id,
                inception_date=inception_date,
                expiry_date=expiry_date,
                status=status,
                sum_insured_pkr=sum_insured,
                deductible_pkr=deductible,
                annual_premium_pkr=annual_premium,
            )
        )
    return tuple(rows)


def _status_sequence(rng: random.Random) -> list[str]:
    statuses = [status for status, count in _STATUS_COUNTS.items() for _ in range(count)]
    if len(statuses) != CLAIM_COUNT or set(statuses) != set(CLAIM_STATUSES):
        raise AssertionError("claim status allocation does not match the catalogue")
    rng.shuffle(statuses)
    return statuses


def _paying_claim_offsets(rng: random.Random, statuses: list[str]) -> set[int]:
    by_status = {
        status: [offset for offset, candidate in enumerate(statuses) if candidate == status]
        for status in CLAIM_STATUSES
    }
    paying = set(by_status["closed"])
    paying.update(rng.sample(by_status["open"], 2_100))
    paying.update(rng.sample(by_status["reopened"], 300))
    return paying


def _cause_description(rng: random.Random, peril: str) -> str:
    if peril == "water_damage":
        classification = "sudden" if rng.randrange(100) < 68 else "gradual"
        return rng.choice(WATER_DAMAGE_CAUSES[classification])
    return rng.choice(CAUSE_DESCRIPTIONS[peril])


def _generate_claims(
    rng: random.Random,
    policies: tuple[PolicyRow, ...],
    rate_rows: tuple[tuple[tuple[int, int, str], ClaimRate], ...],
) -> tuple[ClaimRow, ...]:
    policies_by_region = {
        region.region_id: tuple(
            policy for policy in policies if policy.region_id == region.region_id
        )
        for region in REGIONS
    }
    adjusters_by_region = {
        region.region_id: tuple(
            adjuster for adjuster in ADJUSTERS if adjuster.region_id == region.region_id
        )
        for region in REGIONS
    }
    population = [key for key, _rate in rate_rows]
    weights = [rate.frequency_weight for _key, rate in rate_rows]
    sampled_keys = rng.choices(population, weights=weights, k=CLAIM_COUNT)
    rates = dict(rate_rows)
    statuses = _status_sequence(rng)
    paying_offsets = _paying_claim_offsets(rng, statuses)
    openish_offsets = [
        offset for offset, status in enumerate(statuses) if status in {"open", "reopened"}
    ]
    unassigned_offsets = set(rng.sample(openish_offsets, CLAIM_COUNT // 25))

    rows: list[ClaimRow] = []
    for offset, (month, loss_region_id, peril) in enumerate(sampled_keys):
        policy_candidates = policies_by_region[loss_region_id]
        policy = (
            rng.choice(policy_candidates) if rng.randrange(100) < 82 else rng.choice(policies)
        )
        report_month_start = date(2026, month, 1)
        next_month = date(2026, month + 1, 1) if month < 6 else date(2026, 7, 1)
        report_date = report_month_start + timedelta(
            days=rng.randrange((next_month - report_month_start).days)
        )
        date_of_loss = report_date - timedelta(days=rng.randrange(22))

        status = statuses[offset]
        closed_date = None
        if status not in {"open", "reopened"}:
            closed_date = report_date + timedelta(days=rng.randrange(1, 46))

        adjuster_id = None
        if offset not in unassigned_offsets:
            eligible_adjusters = (
                tuple(adjuster for adjuster in ADJUSTERS if adjuster.is_active)
                if status in {"open", "reopened"}
                else ADJUSTERS
            )
            regional_adjusters = tuple(
                adjuster
                for adjuster in adjusters_by_region[loss_region_id]
                if adjuster in eligible_adjusters
            )
            adjuster = (
                rng.choice(regional_adjusters)
                if rng.randrange(100) < 82
                else rng.choice(eligible_adjusters)
            )
            adjuster_id = adjuster.adjuster_id

        rate = rates[(month, loss_region_id, peril)]
        gross_loss = _random_money(rng, rate.severity_min_pkr, rate.severity_max_pkr)
        gross_loss = max(gross_loss, policy.deductible_pkr + Decimal("10000.00"))
        net_loss = _money(
            min(gross_loss - policy.deductible_pkr, policy.sum_insured_pkr)
        )

        if status in {"denied", "withdrawn"}:
            incurred = Decimal("0.00")
            paid = Decimal("0.00")
            reserve = Decimal("0.00")
            deductible_applied = Decimal("0.00")
        elif status == "closed":
            paid = net_loss
            reserve = Decimal("0.00")
            incurred = paid + reserve
            deductible_applied = policy.deductible_pkr
        else:
            incurred = net_loss
            if offset in paying_offsets:
                paid_percentage = rng.randrange(15, 71)
                paid = _money(incurred * Decimal(paid_percentage) / Decimal(100))
            else:
                paid = Decimal("0.00")
            reserve = incurred - paid
            deductible_applied = policy.deductible_pkr

        rows.append(
            ClaimRow(
                claim_id=300_001 + offset,
                claim_number=f"CLM-{1_000 + offset}",
                policy_id=policy.policy_id,
                adjuster_id=adjuster_id,
                region_id=loss_region_id,
                peril=peril,
                date_of_loss=date_of_loss,
                report_date=report_date,
                closed_date=closed_date,
                status=status,
                cause_description=_cause_description(rng, peril),
                incurred_amount_pkr=incurred,
                paid_amount_pkr=paid,
                reserve_amount_pkr=reserve,
                deductible_applied_pkr=deductible_applied,
            )
        )
    return tuple(rows)


def _split_payment(rng: random.Random, amount: Decimal) -> tuple[Decimal, Decimal]:
    total_cents = _to_cents(amount)
    first_cents = total_cents * rng.randrange(35, 66) // 100
    first_cents = min(max(first_cents, 1), total_cents - 1)
    return _from_cents(first_cents), _from_cents(total_cents - first_cents)


def _generate_payments(
    rng: random.Random, claims: tuple[ClaimRow, ...]
) -> tuple[ClaimPaymentRow, ...]:
    paid_claims = [claim for claim in claims if claim.paid_amount_pkr > 0]
    extra_count = PAYMENT_COUNT - len(paid_claims)
    if extra_count < 0 or extra_count > len(paid_claims):
        raise AssertionError("payment allocation cannot meet the requested volume")
    two_payment_ids = {
        claim.claim_id for claim in rng.sample(paid_claims, extra_count)
    }

    rows: list[ClaimPaymentRow] = []
    for claim in paid_claims:
        if claim.claim_id in two_payment_ids:
            if rng.randrange(100) < 18:
                expense = _money(
                    claim.paid_amount_pkr * Decimal(rng.randrange(3, 11)) / Decimal(100)
                )
                payment_types = ("indemnity", "expense")
                amounts = (claim.paid_amount_pkr - expense, expense)
            else:
                payment_types = ("indemnity", "indemnity")
                amounts = _split_payment(rng, claim.paid_amount_pkr)
        else:
            payment_types = ("indemnity",)
            amounts = (claim.paid_amount_pkr,)

        latest_payment_date = claim.closed_date or claim.report_date + timedelta(days=60)
        day_span = (latest_payment_date - claim.report_date).days
        for payment_type, amount in zip(payment_types, amounts, strict=True):
            rows.append(
                ClaimPaymentRow(
                    payment_id=400_001 + len(rows),
                    claim_id=claim.claim_id,
                    payment_date=claim.report_date
                    + timedelta(days=rng.randrange(1, day_span + 1)),
                    payment_type=payment_type,
                    amount_pkr=amount,
                )
            )
    if len(rows) != PAYMENT_COUNT or not set(row.payment_type for row in rows) <= set(
        PAYMENT_TYPES
    ):
        raise AssertionError("payment allocation does not match the corpus contract")
    return tuple(rows)


def generate_transactions(
    seed: int,
    *,
    rate_table: Mapping[tuple[int, int, str], ClaimRate] | None = None,
) -> TransactionRows:
    """Return all four deterministic operational row sets without performing I/O.

    ``rate_table`` is an explicit input seam for reviewers and tests. Omitting it uses
    the immutable reviewed ``CLAIM_RATE_TABLE``. A supplied table must contain exactly
    the same complete set of month, region, and peril keys.
    """

    rng = random.Random(seed)
    rate_rows = _validate_rate_table(CLAIM_RATE_TABLE if rate_table is None else rate_table)
    customers = _generate_customers(rng)
    policies = _generate_policies(rng, customers)
    claims = _generate_claims(rng, policies, rate_rows)
    claim_payments = _generate_payments(rng, claims)
    return TransactionRows(
        customers=customers,
        policies=policies,
        claims=claims,
        claim_payments=claim_payments,
    )


__all__ = [
    "CLAIM_RATE_TABLE",
    "PLANTED_RATE_ROWS",
    "ClaimPaymentRow",
    "ClaimRate",
    "ClaimRow",
    "CustomerRow",
    "PolicyRow",
    "TransactionRows",
    "generate_transactions",
]
