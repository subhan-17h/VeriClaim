"""The read-only role, proved against a real Postgres.

The AST validator in C-5.3 is the first line of SQL defence; this role is the last. It
has to hold on its own, because the whole point of a database-level guarantee is that it
survives a bug in the layer above it. Everything here therefore asserts on what Postgres
does, never on what the validator would have allowed.

Needs the container: ``docker compose up -d``. Deselect with ``-m 'not postgres'``.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

pytestmark = pytest.mark.postgres

PROBE_TABLE = "ops.readonly_role_probe"


@pytest.fixture(scope="module")
def probe(settings) -> Iterator[str]:
    """A table created by the admin role *after* the read-only role existed.

    Created here rather than assumed, because that is precisely the case ``ALTER DEFAULT
    PRIVILEGES`` covers and the one first-boot grants cannot.
    """
    with psycopg.connect(settings.dsn(readonly=False), autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {PROBE_TABLE}")
        conn.execute(f"CREATE TABLE {PROBE_TABLE} (id integer PRIMARY KEY)")
        conn.execute(f"INSERT INTO {PROBE_TABLE} (id) VALUES (1)")
    try:
        yield PROBE_TABLE
    finally:
        with psycopg.connect(settings.dsn(readonly=False), autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {PROBE_TABLE}")


@pytest.fixture
def readonly(settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.dsn(readonly=True)) as conn:
        yield conn


def test_the_readonly_role_can_connect(readonly) -> None:
    assert readonly.execute("SELECT 1").fetchone()[0] == 1


def test_it_reads_a_table_created_after_it_existed(readonly, probe) -> None:
    """Proves ALTER DEFAULT PRIVILEGES, not the no-op first-boot grant."""
    assert readonly.execute(f"SELECT count(*) FROM {probe}").fetchone()[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE smoke_forbidden (id integer)",
        "CREATE TABLE ops.smoke_forbidden (id integer)",
        "CREATE TABLE sheets.smoke_forbidden (id integer)",
        "CREATE SCHEMA smoke_forbidden",
    ],
    ids=["public", "ops", "sheets", "schema"],
)
def test_it_cannot_create(readonly, statement) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        readonly.execute(statement)


@pytest.mark.parametrize(
    "template",
    [
        "INSERT INTO {table} (id) VALUES (2)",
        "UPDATE {table} SET id = 2",
        "DELETE FROM {table}",
        "TRUNCATE {table}",
        "DROP TABLE {table}",
        "ALTER TABLE {table} ADD COLUMN injected text",
    ],
    ids=["insert", "update", "delete", "truncate", "drop", "alter"],
)
def test_it_cannot_write_to_a_table_it_can_read(readonly, probe, template) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        readonly.execute(template.format(table=probe))


def test_the_smoke_script_passes_every_check(smoke, settings) -> None:
    """``scripts/smoke.py`` is the operator-facing form of this file; keep them agreed."""
    results = smoke.run_checks(settings)

    assert results
    assert [result for result in results if result.status == "FAIL"] == []
