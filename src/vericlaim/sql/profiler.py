"""Read the shape of the live database into the schema contexts.

A context has two halves. The semantics are hand-authored, because no amount of profiling
reveals that an incurred amount is an estimate of ultimate cost. The statistics -- row
counts, null counts, distinct counts, sample values, ranges, and the value set of a
low-cardinality column -- are read from the database, because hand-maintained numbers are
wrong the moment the corpus is regenerated and nothing announces it.

The refresh doubles as the contract check between the contexts and the corpus generator:
a column present in one and absent from the other is a failure in both directions. A
column the database has and nobody documented is invisible to the planner and therefore
unusable; a column documented but absent produces SQL that fails at execution.

All identifiers are composed with :mod:`psycopg.sql`, never interpolated -- this module
runs as the admin role, which is the one place in the system where that matters most.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from psycopg import sql

from vericlaim.sql.contexts import (
    LINEAGE_COLUMN_NAMES,
    ColumnStats,
    ContextError,
    Invariant,
    NonNegativeInvariant,
    SchemaContext,
    SumInvariant,
    load_contexts,
)
from vericlaim.sql.db import Database

# Above this many distinct values a column is a key or free text rather than a
# vocabulary, and listing it would cost prompt space without helping the generator.
VALUE_SET_LIMIT = 20
SAMPLE_LIMIT = 8

TEXT_TYPES = {"text", "character varying", "character", "varchar"}
RANGED_TYPES = {
    "smallint",
    "integer",
    "bigint",
    "real",
    "double precision",
    "numeric",
    "date",
    "timestamp without time zone",
    "timestamp with time zone",
}

# A function of (schema, table) returning the observed columns. Injected so a context
# file can be refreshed against anything that can answer the question, and so the
# round-trip is testable without a database.
Profiler = Callable[[str, str], Mapping[str, "ColumnProfile"]]


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """What the database says about one column. Distinct from ``ColumnContext``, which
    also carries the meaning a human wrote."""

    name: str
    type: str
    sample_values: tuple[str, ...]
    stats: ColumnStats
    value_set: tuple[str, ...] | None = None


def profile_table(db: Database, schema: str, table: str) -> dict[str, ColumnProfile]:
    """Return a profile of every column in ``schema.table``, in ordinal order."""
    qualified = sql.Identifier(schema, table)

    with db.connection() as conn:
        columns = conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        ).fetchall()
        if not columns:
            raise ContextError(f"Unknown table: {schema}.{table}")

        profiles: dict[str, ColumnProfile] = {}
        for name, data_type in columns:
            column = sql.Identifier(name)
            total, distinct, nulls = conn.execute(
                sql.SQL(
                    "SELECT count(*), count(DISTINCT {column}), "
                    "count(*) FILTER (WHERE {column} IS NULL) FROM {table}"
                ).format(column=column, table=qualified)
            ).fetchone()

            samples = [
                str(row[0])
                for row in conn.execute(
                    sql.SQL(
                        "SELECT DISTINCT {column}::text FROM {table} "
                        "WHERE {column} IS NOT NULL ORDER BY {column}::text LIMIT {limit}"
                    ).format(
                        column=column, table=qualified, limit=sql.Literal(SAMPLE_LIMIT)
                    )
                ).fetchall()
            ]

            minimum = maximum = None
            if data_type in RANGED_TYPES:
                minimum, maximum = conn.execute(
                    sql.SQL("SELECT min({column})::text, max({column})::text FROM {table}").format(
                        column=column, table=qualified
                    )
                ).fetchone()

            value_set = None
            if _is_vocabulary(data_type, distinct):
                value_set = tuple(
                    str(row[0])
                    for row in conn.execute(
                        sql.SQL(
                            "SELECT DISTINCT {column}::text FROM {table} "
                            "WHERE {column} IS NOT NULL ORDER BY {column}::text"
                        ).format(column=column, table=qualified)
                    ).fetchall()
                )

            profiles[name] = ColumnProfile(
                name=name,
                type=data_type,
                sample_values=tuple(samples),
                stats=ColumnStats(
                    total_count=total,
                    distinct_count=distinct,
                    null_count=nulls,
                    minimum=minimum,
                    maximum=maximum,
                ),
                value_set=value_set,
            )
    return profiles


def _is_vocabulary(data_type: str, distinct_count: int) -> bool:
    """Whether a column's distinct values are worth listing to the generator."""
    return data_type.lower() in TEXT_TYPES and distinct_count < VALUE_SET_LIMIT


def refresh_context(
    context: SchemaContext, profiles: Mapping[str, ColumnProfile]
) -> SchemaContext:
    """Return ``context`` with live statistics merged into its documented columns.

    Hand-authored fields are copied through untouched. Drift in either direction is an
    error rather than a silent partial refresh.
    """
    documented = set(context.column_names)
    observed = set(profiles)

    undocumented = sorted(observed - documented)
    if undocumented:
        raise ContextError(
            f"{context.qualified}: the database has columns no context documents: "
            f"{', '.join(undocumented)}. Document them or drop them from the corpus."
        )
    missing = sorted(documented - observed)
    if missing:
        raise ContextError(
            f"{context.qualified}: documented columns are absent from the database: "
            f"{', '.join(missing)}. SQL written from this context would fail."
        )

    columns = tuple(
        replace(
            column,
            type=profiles[column.name].type,
            sample_values=profiles[column.name].sample_values,
            value_set=profiles[column.name].value_set,
            stats=profiles[column.name].stats,
        )
        for column in context.columns
    )
    return replace(context, columns=columns)


def dump_context(context: SchemaContext) -> str:
    """Serialize a context back to YAML the loader accepts.

    Key order is fixed and empty optional sections are omitted, so a refresh that changes
    no statistics produces no diff.
    """
    body: dict[str, Any] = {
        "schema": context.schema,
        "table": context.table,
        "purpose": context.purpose,
        # The lineage columns are injected on load. Writing them out would make the next
        # load reject the file for declaring them twice.
        "columns": [
            _dump_column(column)
            for column in context.columns
            if column.name not in LINEAGE_COLUMN_NAMES
        ],
    }
    if context.workbook is not None:
        body["workbook"] = context.workbook
        body["sheet"] = context.sheet
    if context.useful_for:
        body["useful_for"] = list(context.useful_for)
    if context.synonyms:
        body["synonyms"] = [
            {"term": synonym.term, "maps_to": synonym.maps_to}
            for synonym in context.synonyms
        ]
    if context.joins:
        body["joins"] = [
            {"column": join.column, "references": join.references, "meaning": join.meaning}
            for join in context.joins
        ]
    if context.cautions:
        body["cautions"] = list(context.cautions)
    if context.invariants:
        # Carried across explicitly. A refresh that dropped them would disarm the
        # observer on the day the corpus was regenerated -- exactly when a result is
        # most likely to be wrong -- and nothing would say so.
        body["invariants"] = [_dump_invariant(entry) for entry in context.invariants]
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=96)


def _dump_invariant(invariant: Invariant) -> dict[str, Any]:
    entry: dict[str, Any] = {"kind": invariant.kind}
    if isinstance(invariant, SumInvariant):
        entry["total"] = invariant.total
        entry["parts"] = list(invariant.parts)
    elif isinstance(invariant, NonNegativeInvariant):
        entry["column"] = invariant.column
    else:
        entry["lower"] = invariant.lower
        entry["upper"] = invariant.upper
    entry["meaning"] = invariant.meaning
    return entry


def _dump_column(column) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": column.name,
        "type": column.type,
        "meaning": column.meaning,
    }
    if column.unit:
        entry["unit"] = column.unit
    if column.sample_values:
        entry["sample_values"] = list(column.sample_values)
    if column.value_set is not None:
        entry["value_set"] = list(column.value_set)
    if column.stats is not None:
        stats: dict[str, Any] = {
            "total_count": column.stats.total_count,
            "distinct_count": column.stats.distinct_count,
            "null_count": column.stats.null_count,
        }
        if column.stats.minimum is not None:
            stats["minimum"] = column.stats.minimum
        if column.stats.maximum is not None:
            stats["maximum"] = column.stats.maximum
        entry["stats"] = stats
    return entry


def refresh_context_file(path: Path | str, profiler: Profiler) -> SchemaContext:
    """Refresh one context file in place, preserving its leading comment header."""
    path = Path(path)
    body, refreshed = _refreshed_body(path, profiler)
    path.write_text(body, encoding="utf-8")
    return refreshed


def _refreshed_body(path: Path, profiler: Profiler) -> tuple[str, SchemaContext]:
    """Return the new file contents without writing them.

    The leading comment header is carried across: it states that the file is
    hand-reviewed, and rewriting it away would invite the next reader to treat the whole
    file as generated and edit it accordingly.
    """
    original = path.read_text(encoding="utf-8")
    # Loaded through load_contexts so the file is validated -- including its joins
    # against its neighbours -- before anything is written back over it.
    context = _context_from_file(path, load_contexts(path.parent))
    refreshed = refresh_context(context, profiler(context.schema, context.table))
    return _leading_comments(original) + dump_context(refreshed), refreshed


def _context_from_file(path: Path, contexts: Mapping[str, SchemaContext]) -> SchemaContext:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    qualified = f"{raw.get('schema')}.{raw.get('table')}"
    context = contexts.get(qualified)
    if context is None:
        raise ContextError(f"{path.name} does not describe a loadable context")
    return context


def _leading_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "".join(f"{line}\n" for line in lines)


def refresh_contexts(context_dir: Path | str, profiler: Profiler) -> list[Path]:
    """Refresh every context in a directory, or none of them.

    Profiling completes for every file before the first byte is written. A refresh that
    stopped halfway would leave the committed contexts describing two different
    databases, which is a worse state than describing one stale one.
    """
    directory = Path(context_dir)
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise ContextError(f"No schema contexts found in {directory}")

    planned = [(path, _refreshed_body(path, profiler)[0]) for path in paths]
    for path, body in planned:
        path.write_text(body, encoding="utf-8")
    return paths


def database_profiler(db: Database) -> Profiler:
    """Return a profiler backed by a live database connection pool."""

    def profiler(schema: str, table: str) -> Mapping[str, ColumnProfile]:
        return profile_table(db, schema, table)

    return profiler
