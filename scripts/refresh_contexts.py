#!/usr/bin/env python3
"""Refresh the statistics in the committed schema contexts from the live database.

    docker compose up -d
    uv run python scripts/refresh_contexts.py

Only the machine-owned half of each context is rewritten -- sample values, counts, ranges,
and the value sets of low-cardinality columns. Purpose, meanings, joins and cautions are
hand-authored and come through untouched; review the diff before committing.

Run this after regenerating the corpus. It is also the contract check between the contexts
and the generator: a column in one and not the other stops the refresh rather than
producing a context that describes a database nobody has.
"""

from __future__ import annotations

import argparse
import sys

from vericlaim.config import get_settings
from vericlaim.sql.contexts import ContextError
from vericlaim.sql.db import Database, DatabaseUnavailableError
from vericlaim.sql.profiler import database_profiler, refresh_contexts


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh schema context statistics.")
    parser.add_argument(
        "--context-dir",
        default=None,
        help="directory of context YAML files (default: contexts/sql)",
    )
    args = parser.parse_args()

    settings = get_settings()
    context_dir = args.context_dir or settings.sql_context_dir

    # The admin role: information_schema and the tables themselves are read here, and
    # the read-only role is scoped to the corpus rather than to the catalogue.
    db = Database(
        settings.dsn(readonly=False),
        statement_timeout_ms=settings.sql_statement_timeout_ms,
    )
    print(f"Refreshing schema contexts in {context_dir}\n")
    try:
        paths = refresh_contexts(context_dir, database_profiler(db))
    except DatabaseUnavailableError as exc:
        print(f"  FAILED: {exc}")
        return 2
    except ContextError as exc:
        print(f"  FAILED: {exc}")
        return 1
    finally:
        db.close()

    for path in paths:
        print(f"  refreshed {path.name}")
    print(f"\n{len(paths)} contexts refreshed. Review the diff before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
