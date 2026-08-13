"""The generated operational corpus, proved after crossing into PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from vericlaim.config import Settings, get_settings
from vericlaim.corpus.load import load_ops_corpus

pytestmark = pytest.mark.postgres

TABLE_KEYS = {
    "regions": "region_id",
    "coverage_products": "product_id",
    "adjusters": "adjuster_id",
    "customers": "customer_id",
    "policies": "policy_id",
    "claims": "claim_id",
    "claim_payments": "payment_id",
}
EXPECTED_COUNTS = {
    "regions": 9,
    "coverage_products": 6,
    "adjusters": 24,
    "customers": 4_000,
    "policies": 6_000,
    "claims": 12_000,
    "claim_payments": 9_000,
}


@pytest.fixture(scope="module")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="module")
def loaded(settings: Settings) -> Iterator[psycopg.Connection]:
    load_ops_corpus(seed=42, settings=settings)
    with psycopg.connect(settings.dsn(readonly=False), autocommit=True) as conn:
        yield conn
    load_ops_corpus(seed=42, settings=settings)


def _row_counts(conn: psycopg.Connection) -> dict[str, int]:
    return {
        table: conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("ops", table))
        ).fetchone()[0]
        for table in TABLE_KEYS
    }


def _row_hashes(conn: psycopg.Connection) -> dict[str, str]:
    hashes = {}
    for table, key in TABLE_KEYS.items():
        statement = sql.SQL(
            "SELECT md5(string_agg(row_to_json(source_row)::text, E'\\n' "
            "ORDER BY {key})) FROM {table} AS source_row"
        ).format(key=sql.Identifier(key), table=sql.Identifier("ops", table))
        hashes[table] = conn.execute(statement).fetchone()[0]
    return hashes


def test_load_lands_every_planned_row(loaded: psycopg.Connection) -> None:
    assert _row_counts(loaded) == EXPECTED_COUNTS


def test_claim_invariants_hold_when_queried_in_sql(loaded: psycopg.Connection) -> None:
    violations = loaded.execute(
        """
        SELECT count(*)
        FROM ops.claims
        WHERE incurred_amount_pkr <> paid_amount_pkr + reserve_amount_pkr
           OR paid_amount_pkr > incurred_amount_pkr
           OR incurred_amount_pkr < 0
           OR paid_amount_pkr < 0
           OR reserve_amount_pkr < 0
           OR deductible_applied_pkr < 0
           OR date_of_loss > report_date
           OR report_date > closed_date
        """
    ).fetchone()[0]

    assert violations == 0


def test_seed_42_is_deterministic_through_the_database(
    loaded: psycopg.Connection, settings: Settings
) -> None:
    load_ops_corpus(seed=42, settings=settings)
    first = _row_hashes(loaded)
    load_ops_corpus(seed=42, settings=settings)
    second = _row_hashes(loaded)

    assert first == second


def test_reload_is_idempotent(
    loaded: psycopg.Connection, settings: Settings
) -> None:
    load_ops_corpus(seed=42, settings=settings)
    first = _row_counts(loaded)
    load_ops_corpus(seed=42, settings=settings)
    second = _row_counts(loaded)

    assert first == EXPECTED_COUNTS
    assert second == first


def test_referential_integrity_holds_when_queried_in_sql(
    loaded: psycopg.Connection,
) -> None:
    orphan_counts = loaded.execute(
        """
        SELECT
            (SELECT count(*)
             FROM ops.policies AS policy
             WHERE NOT EXISTS (
                       SELECT FROM ops.customers
                       WHERE customer_id = policy.customer_id)
                OR NOT EXISTS (
                       SELECT FROM ops.coverage_products
                       WHERE product_id = policy.product_id)
                OR NOT EXISTS (
                       SELECT FROM ops.regions
                       WHERE region_id = policy.region_id)),
            (SELECT count(*)
             FROM ops.claims AS claim
             WHERE NOT EXISTS (
                       SELECT FROM ops.policies
                       WHERE policy_id = claim.policy_id)
                OR NOT EXISTS (
                       SELECT FROM ops.regions
                       WHERE region_id = claim.region_id)
                OR (claim.adjuster_id IS NOT NULL AND NOT EXISTS (
                       SELECT FROM ops.adjusters
                       WHERE adjuster_id = claim.adjuster_id))),
            (SELECT count(*)
             FROM ops.claim_payments AS payment
             WHERE NOT EXISTS (
                       SELECT FROM ops.claims
                       WHERE claim_id = payment.claim_id))
        """
    ).fetchone()

    assert orphan_counts == (0, 0, 0)
