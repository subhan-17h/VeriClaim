"""The read-only role's provisioning contract, checked without a database.

Two things are asserted here. First, that ``docker-compose.yml`` and ``scripts/init.sql``
still describe the database ``Settings`` expects to connect to -- a role rename on either
side would otherwise surface as an authentication failure days later. Second, that
``scripts/smoke.py`` classifies outcomes correctly, because a smoke test that reports PASS
when a write succeeds is worse than no smoke test at all.

The live proof lives in ``test_readonly_role.py`` and needs the container.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest
import yaml

ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "docker-compose.yml"
INIT_SQL = ROOT / "scripts" / "init.sql"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def postgres_service(compose) -> dict:
    return compose["services"]["postgres"]


@pytest.fixture(scope="module")
def init_sql() -> str:
    return INIT_SQL.read_text(encoding="utf-8")


# ------------------------------------------------------- the container contract


def test_the_database_is_postgres_16(postgres_service) -> None:
    assert postgres_service["image"] == "postgres:16"


def test_the_published_port_is_the_one_settings_dial(postgres_service, settings) -> None:
    """5435 avoids both the default 5432 and the Supabase stack on 54321-54324."""
    published = [str(mapping) for mapping in postgres_service["ports"]]

    assert any(f"{settings.pg_port}:5432" in mapping for mapping in published)


def test_the_port_is_published_to_loopback_only(postgres_service) -> None:
    """A claims database reachable from the LAN is a different kind of project."""
    for mapping in postgres_service["ports"]:
        assert str(mapping).startswith("127.0.0.1:")


def test_the_database_and_admin_role_match_settings(postgres_service, settings) -> None:
    environment = postgres_service["environment"]

    assert environment["POSTGRES_DB"] == settings.pg_database
    assert environment["POSTGRES_USER"] == settings.pg_admin_user


def test_an_unset_password_stops_the_container(postgres_service) -> None:
    """``:?`` refuses to start rather than inventing a password for a claims database."""
    environment = postgres_service["environment"]

    for name in ("POSTGRES_PASSWORD", "READONLY_PASSWORD"):
        assert re.match(rf"^\$\{{{name}:\?", str(environment[name])), name


def test_the_init_script_runs_at_first_boot(postgres_service) -> None:
    mounts = [str(volume) for volume in postgres_service["volumes"]]

    assert any(
        "./scripts/init.sql:/docker-entrypoint-initdb.d/" in mount for mount in mounts
    )


# ------------------------------------------------------------ the grant contract


def test_the_readonly_role_is_the_one_settings_connect_as(init_sql, settings) -> None:
    assert re.search(rf"CREATE ROLE {settings.pg_readonly_user}\b", init_sql)


def test_both_schemas_exist_before_any_ingest_runs(init_sql, settings) -> None:
    for schema in (settings.ops_schema, settings.sheets_schema):
        assert re.search(rf"CREATE SCHEMA IF NOT EXISTS {schema}\b", init_sql), schema


def test_the_role_is_granted_select_and_nothing_else(init_sql, settings) -> None:
    """The privilege list is the mechanism; everything above it is a convention."""
    granted = re.findall(
        rf"GRANT\s+(.+?)\s+ON\b[^;]*?TO {settings.pg_readonly_user}",
        init_sql,
        flags=re.IGNORECASE | re.DOTALL,
    )

    assert granted
    assert set(granted) <= {"SELECT", "CONNECT", "USAGE"}


def test_tables_created_later_are_readable_by_default(init_sql, settings) -> None:
    """The load-bearing clause.

    ``GRANT SELECT ON ALL TABLES`` is a no-op at first boot -- no table exists yet. Every
    table this project reads is created afterwards by the corpus generator, so without
    default privileges in both schemas the role would authenticate and then see nothing.
    """
    for schema in (settings.ops_schema, settings.sheets_schema):
        assert re.search(
            rf"ALTER DEFAULT PRIVILEGES\s+FOR ROLE {settings.pg_admin_user}\s+"
            rf"IN SCHEMA {schema}\s+GRANT SELECT ON TABLES\s+TO {settings.pg_readonly_user}",
            init_sql,
            flags=re.IGNORECASE,
        ), schema


def test_the_password_is_read_from_the_environment(init_sql) -> None:
    """A password committed to a file in the repository is a password published."""
    assert "\\getenv readonly_password READONLY_PASSWORD" in init_sql
    assert ":'readonly_password'" in init_sql


# --------------------------------------------------- how the smoke test classifies


class FakeConnection:
    """A connection whose every statement produces a scripted outcome."""

    def __init__(self, outcome=None, rows=None):
        self.outcome = outcome
        self.rows = rows
        self.executed: list[str] = []
        self.rollbacks = 0

    def execute(self, statement: str, *_args):
        self.executed.append(statement)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return FakeCursor(self.rows)

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows


def _insufficient_privilege() -> psycopg.errors.InsufficientPrivilege:
    return psycopg.errors.InsufficientPrivilege("permission denied for schema ops")


def test_a_write_refused_for_lack_of_privilege_passes(smoke) -> None:
    conn = FakeConnection(outcome=_insufficient_privilege())

    result = smoke.expect_blocked(conn, "CREATE TABLE probe (id int)", "CREATE TABLE")

    assert result.status == "PASS"


def test_a_write_that_succeeds_fails_the_smoke_test(smoke) -> None:
    """The regression this whole script exists to catch."""
    conn = FakeConnection()

    result = smoke.expect_blocked(conn, "CREATE TABLE probe (id int)", "CREATE TABLE")

    assert result.status == "FAIL"
    assert "succeeded" in result.detail


def test_a_write_blocked_by_the_wrong_error_fails(smoke) -> None:
    """A write that fails because the table is missing proves nothing about privileges."""
    conn = FakeConnection(outcome=psycopg.errors.UndefinedTable("no such table"))

    result = smoke.expect_blocked(conn, "INSERT INTO ops.probe VALUES (1)", "INSERT")

    assert result.status == "FAIL"
    assert "InsufficientPrivilege" in result.detail


def test_a_refused_write_leaves_the_session_usable(smoke) -> None:
    """Postgres aborts the transaction; without a rollback every later check errors."""
    conn = FakeConnection(outcome=_insufficient_privilege())

    smoke.expect_blocked(conn, "CREATE TABLE probe (id int)", "CREATE TABLE")

    assert conn.rollbacks == 1


def test_a_readable_probe_row_passes(smoke) -> None:
    conn = FakeConnection(rows=(1,))

    result = smoke.expect_readable(conn, "SELECT count(*) FROM ops.probe", "SELECT", 1)

    assert result.status == "PASS"


def test_a_probe_the_role_cannot_read_fails(smoke) -> None:
    """Default privileges missing looks exactly like this."""
    conn = FakeConnection(outcome=_insufficient_privilege())

    result = smoke.expect_readable(conn, "SELECT count(*) FROM ops.probe", "SELECT", 1)

    assert result.status == "FAIL"


def test_any_failed_check_fails_the_run(smoke) -> None:
    results = [
        smoke.CheckResult("readable", "PASS"),
        smoke.CheckResult("blocked", "FAIL", "the write unexpectedly succeeded"),
    ]

    assert smoke.exit_code(results) == 1


def test_a_clean_run_exits_zero(smoke) -> None:
    results = [smoke.CheckResult("readable", "PASS"), smoke.CheckResult("blocked", "PASS")]

    assert smoke.exit_code(results) == 0
