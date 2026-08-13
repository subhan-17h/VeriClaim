#!/usr/bin/env python3
"""Load the generated corpus into Chroma and the sheets schema.

    docker compose up -d
    uv run python scripts/generate_corpus.py --seed 42
    uv run python scripts/load_corpus.py

Run it twice: the second run should report every document skipped, because the index
manifests key on file content and nothing changed. A second run that re-indexes is a
bug in the manifest, not a slow day.

Scanned pages that OCR below the confidence floor are re-read through the vision tier,
which is the only part of this that spends provider quota. That is deliberate: a page
recovered honestly is worth more than a page cheaply left unreadable, and the pages
that stay below the floor after escalation are flagged rather than asserted.
"""

from __future__ import annotations

import argparse
import sys

from vericlaim.config import get_settings
from vericlaim.corpus.index import load_corpus
from vericlaim.policy.indexer import ZeroChunkError
from vericlaim.sql.db import DatabaseUnavailableError, close_databases


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the corpus into every index.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-index every document even if its content is unchanged",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Loading the corpus into {settings.chroma_dir}\n")

    try:
        report = load_corpus(
            settings=settings,
            force=args.force,
            on_progress=lambda message: print(f"  {message}"),
        )
    except DatabaseUnavailableError as exc:
        print(f"  FAILED: {exc}")
        print("  Is Postgres up? docker compose up -d")
        return 2
    except (ZeroChunkError, ValueError) as exc:
        print(f"  FAILED: {exc}")
        return 1
    finally:
        close_databases()

    for name, result in (("policy", report.policy), ("scanned", report.scanned)):
        print(
            f"\n{name}: {result.documents_indexed} documents, "
            f"{result.chunks_created} chunks "
            f"({result.added} added, {result.updated} updated, "
            f"{result.skipped} skipped, {result.removed} removed)"
        )
    print(f"\nsheets: {report.workbooks} workbooks -> {len(report.tables)} tables")
    for table in report.tables:
        print(f"  {table.qualified}: {table.row_count} rows")

    if not report.changed:
        print("\nNothing changed. Every document was already indexed at this content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
