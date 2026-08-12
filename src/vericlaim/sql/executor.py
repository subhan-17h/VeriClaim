"""Run validated SQL against the read-only role.

Thin on purpose. Everything that makes running generated SQL safe has already happened by
the time control reaches here: the AST validator has rejected anything outside the
allow-list, the role has no privilege beyond SELECT, and the session is opened read-only
with a statement timeout. This module's only real decision is which failures belong to the
query.

A syntax error, a missing column, or a statement timeout is a fact about the SQL: it goes
into the result, where the repair loop can read it and write something better. A database
that is not running is not, and refining SQL against it would spend every attempt in the
loop before failing anyway, with the actual reason buried under five rewrites.
"""

from __future__ import annotations

import psycopg

from vericlaim.sql.db import Database
from vericlaim.sql.observer import ExecutionResult


def execute(db: Database, sql: str) -> ExecutionResult:
    """Execute already-validated SQL and return its rows, or the error it produced."""
    try:
        with db.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)  # type: ignore[arg-type]
            columns = tuple(
                description.name for description in cursor.description or ()
            )
            rows = tuple(tuple(row) for row in cursor.fetchall())
    except psycopg.Error as exc:
        # Only errors Postgres raised about this statement land here. A pool that cannot
        # be opened raises DatabaseUnavailableError, which is deliberately not caught.
        return ExecutionResult(sql=sql, error=_message(exc))
    return ExecutionResult(sql=sql, columns=columns, rows=rows)


def _message(exc: psycopg.Error) -> str:
    """Render a database error as one line the refiner can act on."""
    text = str(exc).strip()
    return " ".join(text.split()) or exc.__class__.__name__
