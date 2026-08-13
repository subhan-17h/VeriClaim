"""Reviewed reference data shared by every synthetic corpus source.

The transactional generators, policy-document generator, scanned-document generator,
and spreadsheet generator all import these values. Keeping the small, meaningful part of
the corpus here prevents a product name, region label, or policy filename from acquiring
four independently maintained spellings.

This module deliberately contains no generation machinery. Its values are immutable and
importing it performs no I/O, database access, or random work.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Region:
    region_id: int
    region_name: str
    city: str
    province: str


@dataclass(frozen=True, slots=True)
class CoverageProduct:
    product_id: int
    product_code: str
    product_name: str
    policy_document: str
    base_deductible_pkr: Decimal
    coverage_limit_pkr: Decimal


@dataclass(frozen=True, slots=True)
class Adjuster:
    adjuster_id: int
    adjuster_name: str
    region_id: int
    seniority: str
    is_active: bool


REGIONS: tuple[Region, ...] = (
    Region(1, "Lahore Central", "Lahore", "Punjab"),
    Region(2, "Lahore North", "Lahore", "Punjab"),
    Region(3, "Lahore South", "Lahore", "Punjab"),
    Region(4, "Karachi Central", "Karachi", "Sindh"),
    Region(5, "Karachi East", "Karachi", "Sindh"),
    Region(6, "Karachi West", "Karachi", "Sindh"),
    Region(7, "Islamabad Central", "Islamabad", "Islamabad Capital Territory"),
    Region(8, "Islamabad East", "Islamabad", "Islamabad Capital Territory"),
    Region(9, "Islamabad West", "Islamabad", "Islamabad Capital Territory"),
)


COVERAGE_PRODUCTS: tuple[CoverageProduct, ...] = (
    CoverageProduct(
        1,
        "HSB",
        "HomeSecure Basic",
        "HomeSecure_Basic_2026.pdf",
        Decimal("50000"),
        Decimal("5000000"),
    ),
    CoverageProduct(
        2,
        "HSP",
        "HomeSecure Plus",
        "HomeSecure_Plus_2026.pdf",
        Decimal("25000"),
        Decimal("10000000"),
    ),
    CoverageProduct(
        3,
        "LLP",
        "Landlord Protect",
        "Landlord_Protect_2026.pdf",
        Decimal("75000"),
        Decimal("15000000"),
    ),
    CoverageProduct(
        4,
        "SPS",
        "SME Property Shield",
        "SME_Property_Shield_2026.pdf",
        Decimal("60000"),
        Decimal("25000000"),
    ),
    CoverageProduct(
        5,
        "FIR",
        "Fire Protection",
        "Fire_Protection_2026.pdf",
        Decimal("50000"),
        Decimal("20000000"),
    ),
    CoverageProduct(
        6,
        "FLD",
        "Flood Protection",
        "Flood_Protection_2026.pdf",
        Decimal("60000"),
        Decimal("15000000"),
    ),
)


ADJUSTERS: tuple[Adjuster, ...] = (
    Adjuster(1, "Ahsan Malik", 1, "lead", True),
    Adjuster(2, "Sana Iqbal", 1, "senior", True),
    Adjuster(3, "Hamza Rauf", 1, "junior", True),
    Adjuster(4, "Mariam Aslam", 2, "senior", True),
    Adjuster(5, "Bilal Qureshi", 2, "junior", True),
    Adjuster(6, "Nida Farooq", 2, "junior", False),
    Adjuster(7, "Usman Tariq", 3, "lead", True),
    Adjuster(8, "Hira Javed", 3, "senior", True),
    Adjuster(9, "Fahad Siddiqui", 4, "lead", True),
    Adjuster(10, "Ayesha Memon", 4, "senior", True),
    Adjuster(11, "Danish Ahmed", 4, "junior", True),
    Adjuster(12, "Mehwish Khan", 5, "senior", True),
    Adjuster(13, "Saad Mirza", 5, "junior", True),
    Adjuster(14, "Rabia Shaikh", 5, "junior", False),
    Adjuster(15, "Omer Baloch", 6, "lead", True),
    Adjuster(16, "Zara Hussain", 6, "senior", True),
    Adjuster(17, "Ali Raza", 7, "lead", True),
    Adjuster(18, "Komal Shah", 7, "senior", True),
    Adjuster(19, "Waleed Akhtar", 7, "junior", True),
    Adjuster(20, "Sadia Mahmood", 8, "senior", True),
    Adjuster(21, "Taimur Abbasi", 8, "junior", True),
    Adjuster(22, "Noor Fatima", 8, "junior", False),
    Adjuster(23, "Imran Saleem", 9, "lead", True),
    Adjuster(24, "Farah Nadeem", 9, "senior", False),
)


PERILS: tuple[str, ...] = (
    "water_damage",
    "fire",
    "theft",
    "storm",
    "impact",
    "liability",
)
CLAIM_STATUSES: tuple[str, ...] = (
    "open",
    "closed",
    "reopened",
    "denied",
    "withdrawn",
)
POLICY_STATUSES: tuple[str, ...] = ("active", "lapsed", "cancelled")
PAYMENT_TYPES: tuple[str, ...] = ("indemnity", "expense", "recovery")
CUSTOMER_TYPES: tuple[str, ...] = ("individual", "business")
SENIORITY_LEVELS: tuple[str, ...] = ("junior", "senior", "lead")
