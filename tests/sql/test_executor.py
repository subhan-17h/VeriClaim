"""Running validated SQL against the read-only role.

The executor is deliberately thin, and its only real decision is which failures are the
query's fault. A syntax error or a statement timeout is a fact about the SQL and belongs
in the result, where the repair loop can act on it. A database that is not running is not,
and refining SQL against it would burn every attempt in the loop before failing anyway.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vericlaim.config import Settings
from vericlaim.sql.db import Database, DatabaseUnavailableError
from vericlaim.sql.executor import execute

PROBE = "executor_probe"

live = pytest.mark.postgres


@pytest.fixture
def probe(settings: Settings) -> Iterator[Database]:
    admin = Database(settings.dsn(readonly=False), statement_timeout_ms=10_000)
    with admin.connection() as conn:
        conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        conn.execute(f"CREATE TABLE ops.{PROBE} (claim_id bigint, peril text)")
        conn.execute(
            f"INSERT INTO ops.{PROBE} VALUES (1, 'water_damage'), (2, 'fire')"
        )
    try:
        yield admin
    finally:
        with admin.connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        admin.close()


@live
def test_rows_come_back_with_their_column_names(probe: Database) -> None:
    result = execute(probe, f"SELECT claim_id, peril FROM ops.{PROBE} ORDER BY claim_id")

    assert result.columns == ("claim_id", "peril")
    assert result.rows == ((1, "water_damage"), (2, "fire"))
    assert result.error == ""


@live
def test_the_executed_sql_travels_with_its_result(probe: Database) -> None:
    """The citation in SqlLocator is the executed statement, not the generated one."""
    sql = f"SELECT count(*) FROM ops.{PROBE}"

    assert execute(probe, sql).sql == sql


@live
def test_a_query_that_matched_nothing_is_not_an_error(probe: Database) -> None:
    result = execute(probe, f"SELECT claim_id FROM ops.{PROBE} WHERE peril = 'storm'")

    assert result.rows == ()
    assert result.error == ""


@live
def test_a_broken_query_reports_its_error_rather_than_raising(probe: Database) -> None:
    """The repair loop needs the message; an exception here would end the run instead."""
    result = execute(probe, f"SELECT nonexistent FROM ops.{PROBE}")

    assert "nonexistent" in result.error
    assert result.rows == ()


@live
def test_a_query_past_the_statement_timeout_comes_back_as_an_error(
    probe: Database,
) -> None:
    """A timeout is a fact about the query -- too expensive as written -- so the loop gets
    the chance to write a cheaper one."""
    impatient = Database(probe._dsn, statement_timeout_ms=50)  # noqa: SLF001
    try:
        result = execute(impatient, "SELECT pg_sleep(2)")
    finally:
        impatient.close()

    assert result.error
    assert "timeout" in result.error.lower() or "cancel" in result.error.lower()


def test_a_database_that_is_not_running_is_not_the_query_s_fault() -> None:
    """Refining SQL against an absent database would spend every attempt in the loop and
    fail anyway, with the reason buried."""
    absent = Database(
        "host=127.0.0.1 port=5999 dbname=nope user=nobody",
        statement_timeout_ms=1000,
        connect_timeout_s=1.0,
    )

    with pytest.raises(DatabaseUnavailableError):
        execute(absent, "SELECT 1")
