"""Load the deterministic operational corpus into PostgreSQL."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import astuple, fields
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from vericlaim.config import PROJECT_ROOT, Settings, get_settings
from vericlaim.corpus.catalog import ADJUSTERS, COVERAGE_PRODUCTS, REGIONS
from vericlaim.corpus.transactions import TransactionRows, generate_transactions

SCHEMA_PATH = PROJECT_ROOT / "scripts" / "schema.sql"


def ops_tables(transactions: TransactionRows) -> tuple[tuple[str, Sequence[Any]], ...]:
    """Every ``ops`` table and its rows, in the order foreign keys require.

    Shared with the corpus validator, which checks the declared invariants against
    these rows before they are loaded. The load order is also the dependency order:
    a table is listed after everything it references.
    """
    return (
        ("regions", REGIONS),
        ("coverage_products", COVERAGE_PRODUCTS),
        ("adjusters", ADJUSTERS),
        ("customers", transactions.customers),
        ("policies", transactions.policies),
        ("claims", transactions.claims),
        ("claim_payments", transactions.claim_payments),
    )


def _copy_rows(cursor: psycopg.Cursor, table: str, rows: Sequence[Any]) -> None:
    if not rows:
        return
    columns = [sql.Identifier(field.name) for field in fields(rows[0])]
    statement = sql.SQL("COPY {table} ({columns}) FROM STDIN").format(
        table=sql.Identifier("ops", table),
        columns=sql.SQL(", ").join(columns),
    )
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(astuple(row))


def load_ops_corpus(
    seed: int = 42,
    *,
    settings: Settings | None = None,
    schema_path: Path | str = SCHEMA_PATH,
) -> dict[str, int]:
    """Recreate ``ops`` and load every corpus table through the admin role."""
    settings = settings or get_settings()
    tables = ops_tables(generate_transactions(seed))

    ddl = Path(schema_path).read_text(encoding="utf-8")
    with psycopg.connect(settings.dsn(readonly=False)) as conn:
        conn.execute(ddl)
        with conn.cursor() as cursor:
            for table, rows in tables:
                _copy_rows(cursor, table, rows)

    return {table: len(rows) for table, rows in tables}


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the generated ops corpus.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    counts = load_ops_corpus(args.seed)
    for table, count in counts.items():
        print(f"ops.{table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
