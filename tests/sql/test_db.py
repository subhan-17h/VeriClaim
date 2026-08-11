"""The pooled, time-bounded connection layer.

Every query the agent runs arrives here, so this module is where three guarantees are
made concrete: a query cannot run forever, a read-only session cannot write even if a
grant is wrong, and a database that is down produces a clear error rather than a hang.

The live half needs the container (``docker compose up -d``); deselect with
``-m 'not postgres'``.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import psycopg_pool
import pytest

from vericlaim.sql.db import Database, DatabaseUnavailableError, close_databases, default_database

# A port nothing can be listening on, so the connection is refused immediately.
UNREACHABLE_DSN = "host=127.0.0.1 port=1 dbname=nowhere user=nobody password=none"


# ------------------------------------------------------------------ offline


def test_constructing_a_database_does_not_connect() -> None:
    """Import-time or construction-time connections make every test need a database."""
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=1000)

    assert db.is_open is False


def test_an_unreachable_database_says_so_and_says_what_to_do() -> None:
    """Neither a hang nor a bare PoolTimeout: the failure has an obvious remedy."""
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=1000, connect_timeout_s=0.5)

    with pytest.raises(DatabaseUnavailableError) as excinfo:
        with db.connection():
            pass

    assert "docker compose up -d" in str(excinfo.value)


def test_a_read_only_session_is_declared_up_front() -> None:
    """Independent of the role's grants: two mechanisms, either one sufficient."""
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=1000, read_only_session=True)

    assert "SET default_transaction_read_only = on" in db.session_options()


def test_a_writable_session_does_not_declare_itself_read_only() -> None:
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=1000, read_only_session=False)

    assert not any("read_only" in option for option in db.session_options())


def test_the_statement_timeout_is_a_session_setting() -> None:
    """Set per session from Settings, not left to the role default init.sql installs."""
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=4321)

    assert "SET statement_timeout = 4321" in db.session_options()


def test_the_session_is_labelled_for_pg_stat_activity() -> None:
    db = Database(UNREACHABLE_DSN, statement_timeout_ms=1000, application_name="vericlaim-eval")

    assert any("vericlaim-eval" in option for option in db.session_options())


def test_the_process_wide_database_is_built_once_per_role() -> None:
    """A pool rebuilt per call is not a pool."""
    try:
        assert default_database(readonly=True) is default_database(readonly=True)
        assert default_database(readonly=True) is not default_database(readonly=False)
    finally:
        close_databases()


def test_the_read_only_database_is_the_default() -> None:
    try:
        assert default_database().read_only_session is True
    finally:
        close_databases()


def test_closing_releases_the_cached_databases() -> None:
    first = default_database(readonly=True)
    close_databases()

    assert default_database(readonly=True) is not first
    close_databases()


# --------------------------------------------------------------------- live

live = pytest.mark.postgres


@pytest.fixture
def readonly_db(settings) -> Iterator[Database]:
    db = Database(
        settings.dsn(readonly=True),
        statement_timeout_ms=settings.sql_statement_timeout_ms,
        read_only_session=True,
    )
    try:
        yield db
    finally:
        db.close()


@live
def test_a_query_runs_through_the_pool(readonly_db) -> None:
    with readonly_db.connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


@live
def test_the_connection_is_reused_rather_than_reopened(readonly_db) -> None:
    """Proved by the backend PID, which is per connection, not per query."""
    with readonly_db.connection() as conn:
        first = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
    with readonly_db.connection() as conn:
        second = conn.execute("SELECT pg_backend_pid()").fetchone()[0]

    assert first == second


@live
def test_a_runaway_query_is_cancelled_by_the_server(settings) -> None:
    """The bound that holds when a generated query scans more than anyone expected."""
    db = Database(
        settings.dsn(readonly=True), statement_timeout_ms=200, read_only_session=True
    )
    try:
        with pytest.raises(psycopg.errors.QueryCanceled):
            with db.connection() as conn:
                conn.execute("SELECT pg_sleep(5)")
    finally:
        db.close()


@live
def test_a_read_only_session_refuses_a_write_the_role_could_make(settings) -> None:
    """Uses the *admin* DSN deliberately.

    The role's grants are proved in test_readonly_role.py. This proves the second,
    independent guard: a session opened read-only refuses a write even when the role
    connecting is fully privileged.
    """
    db = Database(
        settings.dsn(readonly=False), statement_timeout_ms=5000, read_only_session=True
    )
    try:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with db.connection() as conn:
                conn.execute("CREATE TABLE ops.session_guard_probe (id integer)")
    finally:
        db.close()


@live
def test_the_pool_is_bounded_and_waiting_for_it_is_not(settings) -> None:
    """An exhausted pool fails quickly instead of stalling the request forever."""
    db = Database(
        settings.dsn(readonly=True),
        statement_timeout_ms=1000,
        max_size=1,
        acquire_timeout_s=0.5,
    )
    try:
        with db.connection():
            with pytest.raises(psycopg_pool.PoolTimeout):
                with db.connection():
                    pass
    finally:
        db.close()


@live
def test_a_failed_query_does_not_poison_the_pooled_connection(readonly_db) -> None:
    """Postgres aborts the transaction; the next borrower must get a usable session."""
    with pytest.raises(psycopg.errors.UndefinedTable):
        with readonly_db.connection() as conn:
            conn.execute("SELECT * FROM ops.no_such_table")

    with readonly_db.connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


@live
def test_closing_twice_is_harmless(readonly_db) -> None:
    readonly_db.close()
    readonly_db.close()

    assert readonly_db.is_open is False
