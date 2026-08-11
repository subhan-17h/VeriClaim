#!/usr/bin/env python3
"""Prove the read-only Postgres role is genuinely read-only.

    docker compose up -d
    uv run python scripts/smoke.py

The AST validator rejects unsafe SQL before it is sent. This role is what stands behind
that if the validator is ever wrong, so it has to be verified against a real database
rather than assumed from the fact that init.sql ran without error.

Two things are checked that a grant listing alone cannot tell you:

* The role can read a table created **after** it existed. Nearly every table this project
  reads is created later by the corpus generator, so the first-boot grant covers none of
  them -- `ALTER DEFAULT PRIVILEGES` does, and only a live probe distinguishes the two.
* Refused writes are refused **by privilege**. A statement that fails because the table is
  missing looks like success to a naive smoke test while proving nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from vericlaim.config import Settings, get_settings

Status = Literal["PASS", "FAIL"]

# Created by the admin role during the run and dropped afterwards.
PROBE_SUFFIX = "smoke_probe"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""


def expect_blocked(conn: Any, statement: str, name: str) -> CheckResult:
    """Run a statement that must be refused for lack of privilege.

    Anything else is a failure, including success and including refusal by some other
    error: only `InsufficientPrivilege` says the privilege system did the refusing.
    """
    try:
        conn.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        return CheckResult(name, "PASS", "refused: InsufficientPrivilege")
    except Exception as exc:  # noqa: BLE001 - any other refusal proves nothing
        return CheckResult(
            name, "FAIL", f"refused, but not by InsufficientPrivilege: {exc!r}"
        )
    else:
        return CheckResult(name, "FAIL", "the write unexpectedly succeeded")
    finally:
        # Postgres aborts the transaction on error, and a successful write here must not
        # be allowed to commit. Either way the session has to be reusable for the next
        # check.
        conn.rollback()


def expect_readable(conn: Any, query: str, name: str, expected: Any) -> CheckResult:
    """Run a query that must succeed and return ``expected`` as its first value."""
    try:
        value = conn.execute(query).fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return CheckResult(name, "FAIL", repr(exc))
    else:
        if value != expected:
            return CheckResult(name, "FAIL", f"expected {expected!r}, got {value!r}")
        return CheckResult(name, "PASS")
    finally:
        conn.rollback()


def run_checks(
    settings: Settings | None = None,
    *,
    connect: Callable[..., Any] = psycopg.connect,
) -> list[CheckResult]:
    """Exercise the read-only role against a live database, then clean up after itself."""
    settings = settings or get_settings()
    probe = f"{settings.ops_schema}.{PROBE_SUFFIX}"

    with connect(settings.dsn(readonly=False), autocommit=True) as admin:
        admin.execute(f"DROP TABLE IF EXISTS {probe}")
        admin.execute(f"CREATE TABLE {probe} (id integer PRIMARY KEY)")
        admin.execute(f"INSERT INTO {probe} (id) VALUES (1)")

    try:
        with connect(settings.dsn(readonly=True)) as conn:
            return [
                expect_readable(conn, "SELECT 1", "connects as the read-only role", 1),
                expect_readable(
                    conn,
                    f"SELECT count(*) FROM {probe}",
                    "reads a table created after the role existed",
                    1,
                ),
                expect_blocked(
                    conn,
                    f"CREATE TABLE {settings.ops_schema}.smoke_forbidden (id integer)",
                    "CREATE TABLE is blocked",
                ),
                expect_blocked(
                    conn, f"INSERT INTO {probe} (id) VALUES (2)", "INSERT is blocked"
                ),
                expect_blocked(conn, f"UPDATE {probe} SET id = 2", "UPDATE is blocked"),
                expect_blocked(conn, f"DELETE FROM {probe}", "DELETE is blocked"),
                expect_blocked(conn, f"DROP TABLE {probe}", "DROP TABLE is blocked"),
            ]
    finally:
        with connect(settings.dsn(readonly=False), autocommit=True) as admin:
            admin.execute(f"DROP TABLE IF EXISTS {probe}")


def exit_code(results: Sequence[CheckResult]) -> int:
    """Return 1 if any check failed."""
    return 1 if any(result.status == "FAIL" for result in results) else 0


def main() -> int:
    settings = get_settings()
    target = (
        f"{settings.pg_host}:{settings.pg_port}/{settings.pg_database} "
        f"as {settings.pg_readonly_user}"
    )
    print("VeriClaim read-only role smoke test")
    print(f"  {target}\n")

    try:
        results = run_checks(settings)
    except psycopg.OperationalError as exc:
        print(f"  FAIL  cannot reach Postgres: {exc}")
        print("\nStart it with `docker compose up -d`, then run this again.")
        return 2

    for result in results:
        suffix = f" -- {result.detail}" if result.detail else ""
        print(f"  {result.status}  {result.name}{suffix}")

    code = exit_code(results)
    if code:
        print("\nThe role is NOT read-only. Do not point the agent at this database.")
    else:
        print("\nThe role reads the corpus and cannot write to it.")
    return code


if __name__ == "__main__":
    sys.exit(main())
