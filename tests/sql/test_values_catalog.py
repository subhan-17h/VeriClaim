"""The catalog of values the database actually holds.

Entity grounding is only as good as its inventory of stored values. This file tests what
goes into that inventory -- which columns contribute, how their values are shaped, and
when the cache holding them is allowed to be trusted.

The reference implementation cached the catalog in a module-level dict with no
invalidation at all; its own docstring told you to restart the process after re-ingesting.
The generation key tested here is what replaces that.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vericlaim.config import Settings
from vericlaim.sql.contexts import ColumnContext, SchemaContext
from vericlaim.sql.db import Database
from vericlaim.sql.values_catalog import (
    MAX_VALUE_LEN,
    CatalogValue,
    ReferenceMatch,
    StaticCatalog,
    ValuesCatalog,
    database_catalog,
    reference_columns,
    reference_key,
    vocabulary_columns,
)

PROBE = "probe_catalog"


def column(name: str, type_: str = "text") -> ColumnContext:
    return ColumnContext(name=name, type=type_, meaning=f"The {name}.")


CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(
        column("claim_id", "bigint"),
        column("claim_number"),
        column("peril"),
        column("status"),
        column("cause_description"),
        column("report_date", "date"),
        column("incurred_amount_pkr", "numeric"),
    ),
)

CUSTOMERS = SchemaContext(
    schema="ops",
    table="customers",
    purpose="One row per customer.",
    columns=(
        column("customer_id", "bigint"),
        column("customer_name"),
        column("email"),
        column("city"),
    ),
)

CONTEXTS = {CLAIMS.qualified: CLAIMS, CUSTOMERS.qualified: CUSTOMERS}


# ------------------------------------------------------------------ column choice


def test_only_text_columns_that_carry_a_vocabulary_are_matchable() -> None:
    assert vocabulary_columns(CLAIMS) == ("peril", "status")


def test_free_text_is_not_a_vocabulary() -> None:
    """A cause description is prose, not a value someone would name in a question."""
    assert "cause_description" not in vocabulary_columns(CLAIMS)


def test_contact_details_are_not_a_vocabulary() -> None:
    assert vocabulary_columns(CUSTOMERS) == ("customer_name", "city")


def test_a_reference_column_is_never_fuzzy_matched() -> None:
    assert "claim_number" not in vocabulary_columns(CLAIMS)
    assert reference_columns(CLAIMS) == ("claim_number",)


def test_a_table_with_no_reference_column_has_none() -> None:
    assert reference_columns(CUSTOMERS) == ()


def test_a_reference_key_ignores_punctuation_and_case() -> None:
    assert reference_key("CLM-1088") == reference_key("clm 1088") == "clm1088"


# ------------------------------------------------------------------ the values


def catalog(
    values: dict[tuple[str, str], list[str] | None] | None = None,
    *,
    generation: object = None,
    references: dict[tuple[str, str, str], str] | None = None,
    calls: list[tuple[str, str]] | None = None,
) -> ValuesCatalog:
    stored = values or {}
    looked_up = references or {}
    generations = iter(generation) if isinstance(generation, list) else None

    def fetch_distinct(table: str, column_name: str) -> list[str] | None:
        if calls is not None:
            calls.append((table, column_name))
        return stored.get((table, column_name), [])

    def lookup(table: str, column_name: str, key: str) -> str | None:
        return looked_up.get((table, column_name, key))

    def next_generation() -> str:
        return next(generations) if generations is not None else "g1"

    return ValuesCatalog(
        contexts=CONTEXTS,
        fetch_distinct=fetch_distinct,
        lookup_reference=lookup,
        generation=next_generation,
    )


def test_the_catalog_carries_the_stored_values_of_the_selected_tables() -> None:
    subject = catalog({("ops.claims", "peril"): ["water_damage", "fire"]})

    vocabulary = subject.select(["ops.claims"]).vocabulary()

    assert vocabulary["ops.claims"]["peril"] == (
        CatalogValue("water_damage"),
        CatalogValue("fire"),
    )


def test_an_unselected_table_contributes_nothing() -> None:
    subject = catalog({("ops.customers", "city"): ["Lahore"]})

    assert "ops.customers" not in subject.select(["ops.claims"]).vocabulary()


def test_selecting_an_undocumented_table_is_an_error() -> None:
    with pytest.raises(KeyError, match="ops.regions"):
        catalog().select(["ops.regions"])


def test_a_comma_separated_value_also_offers_its_parts() -> None:
    """"Lahore, Punjab" should be findable by someone who only says "Punjab"."""
    subject = catalog({("ops.customers", "city"): ["Lahore, Punjab"]})

    values = subject.select(["ops.customers"]).vocabulary()["ops.customers"]["city"]

    assert CatalogValue("Lahore, Punjab") in values
    assert CatalogValue("Punjab", "contains") in values


def test_an_unbounded_column_is_dropped_rather_than_truncated() -> None:
    """A truncated catalog resolves some mentions and silently fails others."""
    subject = catalog({("ops.customers", "customer_name"): None})

    assert "customer_name" not in subject.select(["ops.customers"]).vocabulary()["ops.customers"]


def test_an_oversized_value_is_not_a_candidate() -> None:
    subject = catalog({("ops.customers", "city"): ["x" * (MAX_VALUE_LEN + 1), "Karachi"]})

    values = subject.select(["ops.customers"]).vocabulary()["ops.customers"]["city"]

    assert values == (CatalogValue("Karachi"),)


# ------------------------------------------------------------------ the cache


def test_values_are_read_once_while_the_database_is_unchanged() -> None:
    calls: list[tuple[str, str]] = []
    subject = catalog({("ops.claims", "peril"): ["fire"]}, calls=calls)

    subject.select(["ops.claims"]).vocabulary()
    subject.select(["ops.claims"]).vocabulary()

    assert calls.count(("ops.claims", "peril")) == 1


def test_a_changed_database_is_read_again() -> None:
    """The bug this replaces: a re-ingest that the cache never noticed."""
    calls: list[tuple[str, str]] = []
    subject = catalog(
        {("ops.claims", "peril"): ["fire"]},
        generation=["g1", "g2"],
        calls=calls,
    )

    subject.select(["ops.claims"]).vocabulary()
    subject.select(["ops.claims"]).vocabulary()

    assert calls.count(("ops.claims", "peril")) == 2


def test_invalidating_forces_the_next_read_to_hit_the_database() -> None:
    calls: list[tuple[str, str]] = []
    subject = catalog({("ops.claims", "peril"): ["fire"]}, calls=calls)

    subject.select(["ops.claims"]).vocabulary()
    subject.invalidate()
    subject.select(["ops.claims"]).vocabulary()

    assert calls.count(("ops.claims", "peril")) == 2


# ------------------------------------------------------------------ references


def test_a_reference_is_matched_exactly_against_its_column() -> None:
    subject = catalog(references={("ops.claims", "claim_number", "clm1088"): "CLM-1088"})

    matches = subject.select(["ops.claims"]).lookup_reference("clm1088")

    assert matches == (ReferenceMatch("ops.claims", "claim_number", "CLM-1088"),)


def test_a_reference_the_database_does_not_hold_matches_nothing() -> None:
    subject = catalog(references={("ops.claims", "claim_number", "clm1088"): "CLM-1088"})

    assert subject.select(["ops.claims"]).lookup_reference("clm9999") == ()


def test_a_static_catalog_has_no_references_to_look_up() -> None:
    static = StaticCatalog({"ops.claims": {"peril": (CatalogValue("fire"),)}})

    assert static.lookup_reference("clm1088") == ()
    assert static.vocabulary()["ops.claims"]["peril"] == (CatalogValue("fire"),)


# ------------------------------------------------------------------ live


@pytest.fixture
def probe_table(settings: Settings) -> Iterator[Database]:
    """A real table in ops, created and dropped by the test.

    The corpus arrives in C-8.1; until then this proves the catalog's SQL against real
    Postgres rather than against a mock of it.
    """
    admin = Database(settings.dsn(readonly=False), statement_timeout_ms=10_000)
    with admin.connection() as conn:
        conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        conn.execute(
            f"""
            CREATE TABLE ops.{PROBE} (
                claim_id bigint PRIMARY KEY,
                claim_number text,
                peril text
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO ops.{PROBE} VALUES
                (1, 'CLM-1088', 'water_damage'),
                (2, 'CLM-1089', 'fire'),
                (3, 'CLM-1090', 'water_damage')
            """
        )
    try:
        yield admin
    finally:
        with admin.connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        admin.close()


def probe_context() -> SchemaContext:
    return SchemaContext(
        schema="ops",
        table=PROBE,
        purpose="Probe table.",
        columns=(column("claim_id", "bigint"), column("claim_number"), column("peril")),
    )


@pytest.mark.postgres
def test_the_catalog_reads_distinct_values_from_the_real_database(probe_table) -> None:
    context = probe_context()
    subject = database_catalog(probe_table, {context.qualified: context})

    values = subject.select([context.qualified]).vocabulary()[context.qualified]["peril"]

    assert {candidate.value for candidate in values} == {"water_damage", "fire"}


@pytest.mark.postgres
def test_a_claim_number_is_found_by_its_punctuation_free_key(probe_table) -> None:
    context = probe_context()
    subject = database_catalog(probe_table, {context.qualified: context})

    matches = subject.select([context.qualified]).lookup_reference(reference_key("clm 1088"))

    assert matches == (ReferenceMatch(context.qualified, "claim_number", "CLM-1088"),)


@pytest.mark.postgres
def test_a_neighbouring_claim_number_is_not_a_match(probe_table) -> None:
    """CLM-1088 and CLM-1089 are one character apart and are different claims."""
    context = probe_context()
    subject = database_catalog(probe_table, {context.qualified: context})

    assert subject.select([context.qualified]).lookup_reference("clm1091") == ()


@pytest.mark.postgres
def test_the_generation_changes_when_rows_are_written(probe_table) -> None:
    context = probe_context()
    subject = database_catalog(probe_table, {context.qualified: context})
    before = subject.generation()

    with probe_table.connection() as conn:
        conn.execute(f"INSERT INTO ops.{PROBE} VALUES (4, 'CLM-1091', 'storm')")

    assert subject.generation() != before
