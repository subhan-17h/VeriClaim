"""Reviewed, committed descriptions of what the database means.

A schema context is what stands between a question in English and SQL that is not merely
valid but *right*. Column names alone are already in the database; what the planner needs
is that `report_date` is when a claim was filed while `date_of_loss` is when the damage
occurred, and that answering "claims in March" with the wrong one returns a confident,
plausible, wrong number.

Two halves with different provenance, deliberately kept in one file per table:

* **Semantics** -- purpose, meanings, joins, cautions -- are hand-authored and reviewed.
  No profiler can infer them from data, and a model asked to guess them invents them.
* **Statistics** -- sample values, counts, value sets -- are machine-refreshed from the
  live database by :mod:`vericlaim.sql.profiler`, because hand-maintained numbers go
  stale silently.

Contexts are also the *only* route by which a table becomes queryable: :func:`allow_list`
converts them into the validator's allow-list, so a table nobody has documented cannot be
read by generated SQL. That coupling is intentional.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vericlaim.sql.validator import AllowedTable

# Enough distinct values to be a useful hint to the generator, few enough to stay
# readable in a prompt.
SUMMARY_VALUE_LIMIT = 19
SUMMARY_VALUE_LENGTH = 32

# The provenance columns every spreadsheet-backed table carries, and what the planner is
# told they mean. Injected on load rather than declared in each context file: they are
# identical everywhere, so writing them out by hand in every file is one chance per file
# to get one wrong, and they must be in the allow-list or the spreadsheet tool cannot
# select the very columns its citation is built from. Their names are prefixed so they
# cannot collide with a column an analyst named.
LINEAGE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (
        "_workbook",
        "text",
        "The workbook file this row was read from. Lineage, not data: never group or "
        "filter on it unless the question is about a particular file.",
    ),
    (
        "_sheet",
        "text",
        "The sheet within the workbook this row was read from. Lineage, not data.",
    ),
    (
        "_row",
        "integer",
        "The row number in the sheet this row was read from, counting from 1 as the "
        "spreadsheet does. Lineage, not data.",
    ),
    (
        "_a1_range",
        "text",
        "The A1 range of the cells this row was read from, for example B14:F14. This is "
        "what the answer cites, so select it whenever rows are being reported.",
    ),
    (
        "_generation",
        "text",
        "A hash of the workbook contents this row was loaded from. Lineage, not data.",
    ),
)
LINEAGE_COLUMN_NAMES = tuple(name for name, _, _ in LINEAGE_COLUMNS)


class ContextError(ValueError):
    """Raised for a context that cannot be trusted to describe the database.

    Always fatal rather than skipped: a context silently dropped for being malformed
    would remove its table from the allow-list, and the resulting answer would be
    confidently incomplete rather than obviously broken.
    """


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """Machine-refreshed shape of one column. Never hand-edited."""

    total_count: int
    distinct_count: int
    null_count: int
    minimum: str | None = None
    maximum: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnContext:
    name: str
    type: str
    meaning: str
    # Declared for money and other dimensioned quantities. The corpus is PKR throughout;
    # an amount whose unit is inferred at formatting time is a wrong answer waiting.
    unit: str | None = None
    sample_values: tuple[str, ...] = ()
    value_set: tuple[str, ...] | None = None
    stats: ColumnStats | None = None


@dataclass(frozen=True, slots=True)
class Synonym:
    """A word a claims handler would use, and the column it actually means."""

    term: str
    maps_to: str


@dataclass(frozen=True, slots=True)
class Join:
    """A foreign key, described so the planner can cross tables.

    The reference implementation this pattern comes from had one table per question and
    no joins at all. This corpus has real foreign keys, and a question about regional
    water-damage exposure spans three tables.
    """

    column: str
    references: str  # schema.table.column
    meaning: str

    @property
    def target_table(self) -> str:
        schema, table, _ = self.references.split(".", 2)
        return f"{schema}.{table}"


@dataclass(frozen=True, slots=True)
class SumInvariant:
    """A total that is the sum of its parts."""

    total: str
    parts: tuple[str, ...]
    meaning: str
    kind: str = "sum"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.total, *self.parts)


@dataclass(frozen=True, slots=True)
class NonNegativeInvariant:
    """A quantity that cannot go below zero."""

    column: str
    meaning: str
    kind: str = "non_negative"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,)


@dataclass(frozen=True, slots=True)
class OrderedInvariant:
    """Two comparable columns whose order is fixed -- dates or amounts alike."""

    lower: str
    upper: str
    meaning: str
    kind: str = "ordered"

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.lower, self.upper)


Invariant = SumInvariant | NonNegativeInvariant | OrderedInvariant


@dataclass(frozen=True, slots=True)
class SchemaContext:
    schema: str
    table: str
    purpose: str
    columns: tuple[ColumnContext, ...]
    # Set only for a table ingested from a spreadsheet. Its presence is what makes the
    # table a distinct, cell-citable source rather than more SQL: the workbook and sheet
    # are half of every citation this table's rows can carry.
    workbook: str | None = None
    sheet: str | None = None
    useful_for: tuple[str, ...] = ()
    synonyms: tuple[Synonym, ...] = ()
    joins: tuple[Join, ...] = ()
    # The distinctions that make a query wrong in a way that still returns a number:
    # incurred vs paid, deductible vs limit, date of loss vs report date.
    cautions: tuple[str, ...] = ()
    # The subset of those cautions that can be checked against a result rather than
    # merely read. Declared here, beside the prose that states them, so the observer
    # stays free of any knowledge of this particular schema.
    invariants: tuple[Invariant, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def is_spreadsheet(self) -> bool:
        return self.workbook is not None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def allowed_table(self) -> AllowedTable:
        return AllowedTable(
            schema=self.schema, table=self.table, columns=self.column_names
        )


def load_contexts(context_dir: Path | str) -> dict[str, SchemaContext]:
    """Load and validate every context in a directory, keyed by qualified name.

    Cross-file validation happens here rather than per file because a join is only
    meaningful once the table it points at is known to be documented too.
    """
    directory = Path(context_dir)
    contexts: dict[str, SchemaContext] = {}

    for path in sorted(directory.glob("*.yaml")):
        context = _parse(path)
        if context.qualified in contexts:
            raise ContextError(
                f"Duplicate schema context for {context.qualified} in {path.name}"
            )
        contexts[context.qualified] = context

    if not contexts:
        raise ContextError(f"No schema contexts found in {directory}")

    for context in contexts.values():
        for join in context.joins:
            if join.column not in context.column_names:
                raise ContextError(
                    f"{context.qualified}: join declared on unknown column "
                    f"{join.column!r}"
                )
            if join.target_table not in contexts:
                raise ContextError(
                    f"{context.qualified}: join references {join.target_table}, "
                    "which has no schema context"
                )
    return contexts


def _parse(path: Path) -> SchemaContext:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContextError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ContextError(f"{path.name} does not contain a mapping")

    schema = _require_str(raw, "schema", path)
    table = _require_str(raw, "table", path)
    purpose = _require_str(raw, "purpose", path)
    where = f"{path.name} ({schema}.{table})"

    workbook = raw.get("workbook")
    sheet = raw.get("sheet")
    if workbook is not None and not isinstance(sheet, str):
        raise ContextError(f"{where}: a workbook-backed table must also name its 'sheet'")

    raw_columns = raw.get("columns")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise ContextError(f"{where}: columns must be a non-empty list")
    columns = tuple(_parse_column(entry, where) for entry in raw_columns)
    if workbook is not None:
        declared = {column.name for column in columns}
        overlap = sorted(declared & set(LINEAGE_COLUMN_NAMES))
        if overlap:
            raise ContextError(
                f"{where}: lineage columns are added automatically; remove {', '.join(overlap)}"
            )
        columns += tuple(
            ColumnContext(name=name, type=type_, meaning=meaning)
            for name, type_, meaning in LINEAGE_COLUMNS
        )

    names = {column.name for column in columns}
    synonyms = tuple(_parse_synonym(entry, where, names) for entry in raw.get("synonyms") or ())
    joins = tuple(_parse_join(entry, where) for entry in raw.get("joins") or ())

    return SchemaContext(
        schema=schema,
        table=table,
        purpose=purpose,
        columns=columns,
        workbook=workbook,
        sheet=sheet,
        useful_for=tuple(str(item) for item in raw.get("useful_for") or ()),
        synonyms=synonyms,
        joins=joins,
        cautions=tuple(str(item) for item in raw.get("cautions") or ()),
        invariants=tuple(
            _parse_invariant(entry, where, names) for entry in raw.get("invariants") or ()
        ),
    )


def _parse_invariant(entry: Any, where: str, columns: set[str]) -> Invariant:
    if not isinstance(entry, Mapping):
        raise ContextError(f"{where}: each invariant must be a mapping")
    kind = entry.get("kind")
    meaning = entry.get("meaning")
    if not isinstance(meaning, str) or not meaning.strip():
        # An unexplained violation is a number a reader cannot act on, so the reason is
        # required rather than optional.
        raise ContextError(f"{where}: invariant {kind!r} needs a 'meaning'")

    if kind == "sum":
        parts = entry.get("parts")
        if not isinstance(parts, list) or len(parts) < 2:
            raise ContextError(
                f"{where}: a sum invariant needs at least two 'parts'; one part is an "
                "equality between two columns, which 'ordered' states better"
            )
        invariant: Invariant = SumInvariant(
            total=str(entry.get("total")),
            parts=tuple(str(part) for part in parts),
            meaning=meaning.strip(),
        )
    elif kind == "non_negative":
        invariant = NonNegativeInvariant(
            column=str(entry.get("column")), meaning=meaning.strip()
        )
    elif kind == "ordered":
        invariant = OrderedInvariant(
            lower=str(entry.get("lower")),
            upper=str(entry.get("upper")),
            meaning=meaning.strip(),
        )
    else:
        raise ContextError(
            f"{where}: unknown invariant kind {kind!r}. "
            "Known: sum, non_negative, ordered"
        )

    unknown = sorted(set(invariant.columns) - columns)
    if unknown:
        raise ContextError(
            f"{where}: invariant {kind!r} names undocumented column(s): "
            f"{', '.join(unknown)}"
        )
    return invariant


def _require_str(raw: Mapping[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextError(f"{path.name}: {key!r} must be a non-empty string")
    return value.strip()


def _parse_column(entry: Any, where: str) -> ColumnContext:
    if not isinstance(entry, Mapping):
        raise ContextError(f"{where}: each column must be a mapping")
    for key in ("name", "type", "meaning"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ContextError(
                f"{where}: column {entry.get('name', '?')!r} needs a non-empty {key!r}"
            )
    stats = entry.get("stats")
    return ColumnContext(
        name=entry["name"].strip(),
        type=entry["type"].strip(),
        meaning=entry["meaning"].strip(),
        unit=entry.get("unit"),
        sample_values=tuple(str(value) for value in entry.get("sample_values") or ()),
        value_set=(
            tuple(str(value) for value in entry["value_set"])
            if isinstance(entry.get("value_set"), list)
            else None
        ),
        stats=_parse_stats(stats, where) if isinstance(stats, Mapping) else None,
    )


def _parse_stats(raw: Mapping[str, Any], where: str) -> ColumnStats:
    try:
        return ColumnStats(
            total_count=int(raw["total_count"]),
            distinct_count=int(raw["distinct_count"]),
            null_count=int(raw["null_count"]),
            minimum=None if raw.get("minimum") is None else str(raw["minimum"]),
            maximum=None if raw.get("maximum") is None else str(raw["maximum"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextError(f"{where}: malformed stats block: {exc}") from exc


def _parse_synonym(entry: Any, where: str, columns: set[str]) -> Synonym:
    if not isinstance(entry, Mapping):
        raise ContextError(f"{where}: each synonym must be a mapping")
    term = entry.get("term")
    maps_to = entry.get("maps_to")
    if not isinstance(term, str) or not isinstance(maps_to, str):
        raise ContextError(f"{where}: a synonym needs 'term' and 'maps_to'")
    if maps_to not in columns:
        raise ContextError(
            f"{where}: synonym {term!r} maps to unknown column {maps_to!r}"
        )
    return Synonym(term=term, maps_to=maps_to)


def _parse_join(entry: Any, where: str) -> Join:
    if not isinstance(entry, Mapping):
        raise ContextError(f"{where}: each join must be a mapping")
    column = entry.get("column")
    references = entry.get("references")
    meaning = entry.get("meaning")
    if not all(isinstance(value, str) for value in (column, references, meaning)):
        raise ContextError(f"{where}: a join needs 'column', 'references', 'meaning'")
    if references.count(".") != 2:
        raise ContextError(
            f"{where}: join reference {references!r} must be schema.table.column"
        )
    return Join(column=column, references=references, meaning=meaning)


def allow_list(
    contexts: Mapping[str, SchemaContext], selected: Iterable[str]
) -> tuple[AllowedTable, ...]:
    """Convert the selected contexts into the validator's allow-list.

    An unknown selection raises rather than being dropped: quietly narrowing the
    allow-list would produce SQL that omits the table the planner meant to read, and the
    answer would be incomplete rather than refused.
    """
    entries = []
    for qualified in selected:
        context = contexts.get(qualified)
        if context is None:
            known = ", ".join(sorted(contexts)) or "none"
            raise ContextError(f"No schema context for {qualified}. Known: {known}")
        entries.append(context.allowed_table())
    return tuple(entries)


def context_summary(context: SchemaContext) -> dict[str, Any]:
    """Return the routing view of a context: meaning without bulk.

    Every context is shown to the router on every question, so sample values are dropped
    -- they can dominate a prompt without changing which source gets chosen. Cautions
    stay, because they are what the summary exists to convey.
    """
    return {
        "table": context.qualified,
        "purpose": context.purpose,
        "columns": [_summary_column(column) for column in context.columns],
        "useful_for": list(context.useful_for),
        "synonyms": [
            {"term": synonym.term, "maps_to": synonym.maps_to}
            for synonym in context.synonyms
        ],
        "joins": [
            {"column": join.column, "references": join.references}
            for join in context.joins
        ],
        "cautions": list(context.cautions),
    }


def context_detail(context: SchemaContext) -> dict[str, Any]:
    """Return the planning view of a context: everything reviewed about one table.

    Where :func:`context_summary` trades detail for prompt space -- the router is shown
    every context on every question -- the planner and generator are shown only the
    tables the router chose, so nothing is dropped here. The parts the summary omits are
    exactly the ones that decide whether generated SQL is *right* rather than merely
    valid: a column's unit, its observed range, what a join actually means, and the
    cautions that distinguish two columns a question could plausibly mean.
    """
    return {
        "table": context.qualified,
        "purpose": context.purpose,
        "columns": [_detail_column(column) for column in context.columns],
        "useful_for": list(context.useful_for),
        "synonyms": [
            {"term": synonym.term, "maps_to": synonym.maps_to}
            for synonym in context.synonyms
        ],
        "joins": [
            {
                "column": join.column,
                "references": join.references,
                "meaning": join.meaning,
            }
            for join in context.joins
        ],
        "cautions": list(context.cautions),
    }


def _detail_column(column: ColumnContext) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "name": column.name,
        "type": column.type,
        "meaning": column.meaning,
    }
    if column.unit:
        detail["unit"] = column.unit
    if column.sample_values:
        detail["sample_values"] = list(column.sample_values)
    if column.value_set is not None:
        detail["value_set"] = list(column.value_set)
    if column.stats is not None:
        detail["stats"] = {
            "total_count": column.stats.total_count,
            "distinct_count": column.stats.distinct_count,
            "null_count": column.stats.null_count,
            "minimum": column.stats.minimum,
            "maximum": column.stats.maximum,
        }
    return detail


def _summary_column(column: ColumnContext) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": column.name,
        "type": column.type,
        "meaning": column.meaning,
    }
    if column.unit:
        summary["unit"] = column.unit
    if column.stats is not None:
        summary["distinct"] = column.stats.distinct_count
        summary["null"] = column.stats.null_count
    if column.value_set is not None:
        # A low-cardinality column's values are the exception worth their space: they let
        # the generator write `peril = 'water_damage'` instead of guessing a spelling.
        summary["value_set"] = [
            _compact(value) for value in column.value_set[:SUMMARY_VALUE_LIMIT]
        ]
    return summary


def _compact(value: str) -> str:
    text = str(value)
    if len(text) <= SUMMARY_VALUE_LENGTH:
        return text
    return f"{text[: SUMMARY_VALUE_LENGTH - 3]}..."
