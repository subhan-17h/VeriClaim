"""The reviewed SQL contexts and the corpus DDL describe one schema.

These checks deliberately parse the committed DDL as text. Requiring PostgreSQL here
would move the contract out of the offline suite, where a missing database could hide
schema drift rather than expose it.
"""

from __future__ import annotations

import re
from dataclasses import fields
from decimal import Decimal

import pytest

from vericlaim.config import PROJECT_ROOT, get_settings
from vericlaim.corpus.catalog import (
    ADJUSTERS,
    CLAIM_STATUSES,
    COVERAGE_PRODUCTS,
    CUSTOMER_TYPES,
    PAYMENT_TYPES,
    PERILS,
    POLICY_STATUSES,
    REGIONS,
    SENIORITY_LEVELS,
    Adjuster,
    CoverageProduct,
    Region,
)
from vericlaim.sql.contexts import load_contexts

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?P<table>ops\.\w+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
FOREIGN_KEY_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*(?P<column>\w+)\s*\)\s*"
    r"REFERENCES\s+(?P<table>ops\.\w+)\s*\(\s*(?P<target>\w+)\s*\)",
    re.IGNORECASE,
)
COLUMN_RE = re.compile(
    r"^\s*(?P<name>\w+)\s+"
    r"(?P<type>timestamp\s+without\s+time\s+zone|integer|bigint|text|date|numeric|boolean)\b",
    re.IGNORECASE,
)
VOCABULARY_CHECK_RE = re.compile(
    r"CHECK\s*\(\s*(?P<column>\w+)\s+IN\s*\("
    r"(?P<values>\s*'[^']*'(?:\s*,\s*'[^']*')*\s*)"
    r"\)\s*\)",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def ddl() -> str:
    return (PROJECT_ROOT / "scripts" / "schema.sql").read_text(encoding="ascii")


@pytest.fixture(scope="module")
def contexts():
    return load_contexts(get_settings().sql_context_dir)


@pytest.fixture(scope="module")
def ddl_tables(ddl: str) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for match in CREATE_TABLE_RE.finditer(ddl):
        columns: dict[str, str] = {}
        for line in match.group("body").splitlines():
            column = COLUMN_RE.match(line)
            if column:
                columns[column.group("name").lower()] = " ".join(
                    column.group("type").lower().split()
                )
        tables[match.group("table").lower()] = columns
    return tables


def test_context_and_ddl_tables_match_in_both_directions(contexts, ddl_tables) -> None:
    assert set(ddl_tables) == set(contexts)


def test_context_and_ddl_columns_match_in_both_directions(contexts, ddl_tables) -> None:
    for qualified, context in contexts.items():
        assert set(ddl_tables[qualified]) == set(context.column_names)


def test_context_and_ddl_column_types_match(contexts, ddl_tables) -> None:
    for qualified, context in contexts.items():
        documented = {column.name: column.type for column in context.columns}
        assert ddl_tables[qualified] == documented


def test_context_and_ddl_joins_match_in_both_directions(contexts, ddl: str) -> None:
    foreign_keys = set()
    for table_match in CREATE_TABLE_RE.finditer(ddl):
        source_table = table_match.group("table").lower()
        foreign_keys.update(
            (
                source_table,
                match.group("column").lower(),
                match.group("table").lower(),
                match.group("target").lower(),
            )
            for match in FOREIGN_KEY_RE.finditer(table_match.group("body"))
        )

    documented_joins = set()
    for context in contexts.values():
        for join in context.joins:
            target_table, target_column = join.references.rsplit(".", 1)
            documented_joins.add(
                (context.qualified, join.column, target_table, target_column)
            )

    assert foreign_keys == documented_joins


def test_catalog_records_match_their_table_columns(contexts) -> None:
    records = {
        "ops.regions": Region,
        "ops.adjusters": Adjuster,
        "ops.coverage_products": CoverageProduct,
    }
    for qualified, record_type in records.items():
        record_fields = {field.name for field in fields(record_type)}
        assert record_fields == set(contexts[qualified].column_names)


def test_region_ids_are_unique_and_contiguous() -> None:
    region_ids = [region.region_id for region in REGIONS]

    assert len(set(region_ids)) == len(region_ids)
    assert sorted(region_ids) == list(range(1, len(REGIONS) + 1))


def test_every_adjuster_has_a_real_base_region() -> None:
    region_ids = {region.region_id for region in REGIONS}

    assert all(adjuster.region_id in region_ids for adjuster in ADJUSTERS)


def test_every_policy_document_is_a_pdf_filename() -> None:
    policy_documents = [product.policy_document for product in COVERAGE_PRODUCTS]

    assert all(document.endswith(".pdf") for document in policy_documents)
    assert len(set(policy_documents)) == len(policy_documents)


def test_product_money_is_decimal() -> None:
    assert all(
        isinstance(amount, Decimal)
        for product in COVERAGE_PRODUCTS
        for amount in (product.base_deductible_pkr, product.coverage_limit_pkr)
    )


def test_catalogue_and_ddl_vocabularies_match_in_both_directions(ddl: str) -> None:
    catalogue_vocabularies = {
        ("ops.adjusters", "seniority"): SENIORITY_LEVELS,
        ("ops.claim_payments", "payment_type"): PAYMENT_TYPES,
        ("ops.claims", "peril"): PERILS,
        ("ops.claims", "status"): CLAIM_STATUSES,
        ("ops.customers", "customer_type"): CUSTOMER_TYPES,
        ("ops.policies", "status"): POLICY_STATUSES,
    }
    ddl_vocabularies = {}
    for table_match in CREATE_TABLE_RE.finditer(ddl):
        table = table_match.group("table").lower()
        for check_match in VOCABULARY_CHECK_RE.finditer(table_match.group("body")):
            values = tuple(re.findall(r"'([^']*)'", check_match.group("values")))
            ddl_vocabularies[(table, check_match.group("column").lower())] = values

    assert ddl_vocabularies == catalogue_vocabularies
