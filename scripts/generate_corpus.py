#!/usr/bin/env python3
"""Build all four synthetic sources from one seed, then check they agree.

    docker compose up -d
    uv run python scripts/generate_corpus.py --seed 42

Order matters: the operational rows come first and the other three sources are derived
from them, so spreadsheet figures reconcile with the database and scanned reports are
written against claims that exist. Consistency is a property of that construction; the
validator at the end proves it rather than repairing it.

The manifest records the seed and a hash of every file produced. Two runs at the same
seed must write byte-identical manifests -- that is what makes the corpus reproducible
from a seed rather than merely regenerable, and it is why the generators pin every
timestamp their writers would otherwise take from the clock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vericlaim.config import Settings, get_settings
from vericlaim.corpus.load import load_ops_corpus
from vericlaim.corpus.policies import generate_policy_corpus
from vericlaim.corpus.scanned import generate_scanned_corpus
from vericlaim.corpus.spreadsheets import generate_spreadsheet_corpus
from vericlaim.corpus.validate import validate_corpus
from vericlaim.policy.manifest import file_content_hash
from vericlaim.sql.contexts import ContextError
from vericlaim.sql.db import DatabaseUnavailableError

MANIFEST_VERSION = 1


def write_manifest(
    path: Path, *, seed: int, sources: dict[str, list[Path]], row_counts: dict[str, int]
) -> None:
    """Record what this run produced, hashed, so two runs can be compared exactly.

    Paths are stored relative to the project root: an absolute path would make the
    manifest differ between checkouts and destroy the comparison it exists for.
    """
    root = path.parent.parent
    manifest = {
        "version": MANIFEST_VERSION,
        "seed": seed,
        "row_counts": row_counts,
        "documents": {
            source: {
                str(item.relative_to(root)): file_content_hash(item)
                for item in sorted(paths)
            }
            for source, paths in sorted(sources.items())
        },
    }
    # Same atomic temp-and-replace as the index manifest, so an interrupted run cannot
    # leave a half-written file that a later run would read as truth.
    temp = path.with_suffix(f"{path.suffix}.tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _generate(settings: Settings, seed: int, *, skip_load: bool) -> tuple[dict, dict]:
    row_counts: dict[str, int] = {}
    if skip_load:
        print("  ops.*: skipped (--skip-load)")
    else:
        row_counts = load_ops_corpus(seed, settings=settings)
        for table, count in row_counts.items():
            print(f"  ops.{table}: {count} rows")

    sources = {
        "policies": generate_policy_corpus(settings.policy_dir),
        "spreadsheets": generate_spreadsheet_corpus(settings.spreadsheet_dir, seed=seed),
        "scanned": generate_scanned_corpus(settings.scanned_dir, seed=seed),
    }
    for source, paths in sources.items():
        print(f"  {source}: {len(paths)} files")
    return sources, row_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the four synthetic sources.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="write the files without loading ops.* into Postgres",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Generating the corpus at seed {args.seed}\n")

    try:
        sources, row_counts = _generate(settings, args.seed, skip_load=args.skip_load)
    except DatabaseUnavailableError as exc:
        print(f"  FAILED: {exc}")
        print("  Is Postgres up? docker compose up -d")
        return 2
    except (ContextError, ValueError) as exc:
        print(f"  FAILED: {exc}")
        return 1

    print("\nValidating across sources")
    findings = validate_corpus(args.seed, settings=settings)
    for finding in findings:
        print(f"  {finding}")
    if findings:
        print(f"\n{len(findings)} inconsistencies. The corpus was not written to the manifest.")
        return 1
    print("  every cross-source rule holds")

    manifest_path = settings.data_dir / "corpus_manifest.json"
    write_manifest(manifest_path, seed=args.seed, sources=sources, row_counts=row_counts)
    total = sum(len(paths) for paths in sources.values())
    print(f"\n{total} documents written. Manifest at {manifest_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
