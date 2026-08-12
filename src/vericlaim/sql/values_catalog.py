"""What values the database actually holds, so a mention can be grounded in one.

A question names things -- a peril, a city, a customer, a claim reference -- and the
generator has to write those names the way the database spells them. `"water damage"` is
not `'water_damage'`, and `WHERE peril = 'water damage'` returns zero rows with no error
and no hint that the spelling was the problem.

This module is the inventory that makes that repairable. It splits every documented text
column into two kinds, because the two need opposite treatment:

* **Vocabulary** columns -- peril, status, city, a customer's name -- hold a bounded set
  of values a person would paraphrase. These are fetched in full and matched fuzzily by
  :mod:`vericlaim.sql.resolver`.
* **Reference** columns -- ``claim_number``, ``policy_number``, ``product_code`` -- hold
  identifiers. ``CLM-1088`` and ``CLM-1089`` are one character apart and are different
  claims, so fuzzy matching them is not a convenience but a fabrication. They are looked
  up exactly, in the database, one key at a time; they never enter the fuzzy catalog.

Values are read from the database rather than from the committed contexts even though the
contexts carry a refreshed ``value_set``: the contexts are the authority on what a column
*means*, the database is the authority on what it *contains*, and a resolver working from
a stale committed file would rewrite a query to a value that no longer exists.

**On the cache.** The implementation this adapts kept its catalog in a module-level dict
with no expiry and no invalidation; its docstring instructed you to restart the process
after re-ingesting data. Here the cache is instance state keyed by a *generation* -- a
cheap fingerprint that advances whenever the database has been written to -- so a
re-ingest is noticed rather than announced in a comment.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from psycopg import sql

from vericlaim.sql.contexts import SchemaContext
from vericlaim.sql.db import Database

# Above this many distinct values a column is an identifier or free text rather than a
# vocabulary. Such a column is dropped whole rather than truncated: a catalog holding the
# first thousand customers resolves some mentions and silently fails the rest, which is
# worse than resolving none of them.
MAX_DISTINCT = 1000
MAX_VALUE_LEN = 120

TEXT_TYPES = {"text", "character varying", "character", "varchar", "citext"}

# Columns whose contents nobody paraphrases in a question: keys, contact details, and
# prose. Matching against prose is what turns one stray word into a confident filter.
_SKIP_RE = re.compile(
    r"(^|_)id($|_)|email|phone|url|link|address|description|comment|remark|note",
    re.IGNORECASE,
)
# Identifier columns: exact-match only, never fuzzy.
_REFERENCE_RE = re.compile(r"(^|_)(number|no|ref|reference|code)$", re.IGNORECASE)

_PUNCTUATION_RE = re.compile(r"[^a-z0-9]+")

# (qualified table, column) -> the column's distinct values, or None if unbounded.
DistinctFetcher = Callable[[str, str], list[str] | None]
# (qualified table, column, reference key) -> the value as stored, or None.
ReferenceLookup = Callable[[str, str, str], str | None]


@dataclass(frozen=True, slots=True)
class CatalogValue:
    """One stored value a mention may be resolved to.

    ``match_kind`` is ``"contains"`` for a value extracted from part of a longer stored
    string, which has to be filtered with ``ILIKE '%part%'`` rather than equality.
    """

    value: str
    match_kind: str = "equals"


@dataclass(frozen=True, slots=True)
class ReferenceMatch:
    """An identifier found, exactly, in the column that holds it."""

    table: str
    column: str
    value: str


class Catalog(Protocol):
    """The view of the catalog one question sees.

    Narrow on purpose: the resolver is deterministic and has no business reaching the
    database for anything other than these two questions.
    """

    def vocabulary(self) -> Mapping[str, Mapping[str, tuple[CatalogValue, ...]]]:
        """Fuzzy-matchable values, keyed by qualified table then column."""

    def lookup_reference(self, key: str) -> tuple[ReferenceMatch, ...]:
        """Every reference column holding exactly ``key``."""


@dataclass(frozen=True, slots=True)
class StaticCatalog:
    """A catalog over values already in hand.

    Used for the fuzzy SQL rewrite, which resolves a literal against one already-loaded
    column, and by tests that have no database.
    """

    values: Mapping[str, Mapping[str, tuple[CatalogValue, ...]]]

    def vocabulary(self) -> Mapping[str, Mapping[str, tuple[CatalogValue, ...]]]:
        return self.values

    def lookup_reference(self, key: str) -> tuple[ReferenceMatch, ...]:
        """Always empty: a static catalog holds vocabulary, and references are exact."""
        return ()


def vocabulary_columns(context: SchemaContext) -> tuple[str, ...]:
    """Return the text columns of ``context`` worth matching a mention against."""
    return tuple(
        column.name
        for column in context.columns
        if column.type.lower() in TEXT_TYPES
        and not _SKIP_RE.search(column.name)
        and not _REFERENCE_RE.search(column.name)
    )


def reference_columns(context: SchemaContext) -> tuple[str, ...]:
    """Return the text columns of ``context`` holding identifiers."""
    return tuple(
        column.name
        for column in context.columns
        if column.type.lower() in TEXT_TYPES and _REFERENCE_RE.search(column.name)
    )


def reference_key(value: str) -> str:
    """Reduce an identifier to the form it is compared in.

    ``CLM-1088``, ``clm 1088`` and ``clm1088`` are the same claim written three ways, and
    all three arrive in questions. Case and punctuation are the only tolerance offered --
    a digit that differs is a different claim.
    """
    return _PUNCTUATION_RE.sub("", value.casefold())


class ValuesCatalog:
    """The database's values, cached until the database changes underneath them.

    One instance is intended to live as long as the process and serve every question;
    :meth:`select` returns the per-question view. The cache is instance state rather than
    module state so a test, or a second database, needs no global reset.
    """

    def __init__(
        self,
        *,
        contexts: Mapping[str, SchemaContext],
        fetch_distinct: DistinctFetcher,
        lookup_reference: ReferenceLookup,
        generation: Callable[[], str],
        max_distinct: int = MAX_DISTINCT,
    ) -> None:
        self._contexts = dict(contexts)
        self._fetch_distinct = fetch_distinct
        self._lookup_reference = lookup_reference
        self.generation = generation
        self._max_distinct = max_distinct
        self._lock = threading.Lock()
        self._generation: str | None = None
        self._values: dict[tuple[str, str], tuple[CatalogValue, ...] | None] = {}
        self._references: dict[tuple[str, str, str], str | None] = {}

    # -- cache -------------------------------------------------------------

    def invalidate(self) -> None:
        """Drop everything cached. Idempotent."""
        with self._lock:
            self._generation = None
            self._values.clear()
            self._references.clear()

    def _check_generation(self) -> None:
        """Clear the cache if the database has been written to since it was filled.

        The generation is read on every entry point rather than on a timer: one cheap
        query is a smaller price than serving a value the corpus no longer contains.
        """
        current = self.generation()
        with self._lock:
            if self._generation == current:
                return
            self._generation = current
            self._values.clear()
            self._references.clear()

    # -- reads -------------------------------------------------------------

    def _values_for(self, table: str, column: str) -> tuple[CatalogValue, ...] | None:
        """Return the catalogued values of one column, or None if it is unbounded.

        Private because it does not check the generation; every public entry point does
        that once, before reading any number of columns.
        """
        key = (table, column)
        if key not in self._values:
            self._values[key] = _build_column_values(
                self._fetch_distinct(table, column), self._max_distinct
            )
        return self._values[key]

    def select(self, selected: Iterable[str]) -> Catalog:
        """Return the catalog view for one question's tables.

        An unknown table raises rather than being skipped: a mention silently left
        ungrounded becomes a filter on a value the database does not hold, and the answer
        is an empty result set that looks like a fact.
        """
        tables = tuple(selected)
        for table in tables:
            if table not in self._contexts:
                known = ", ".join(sorted(self._contexts)) or "none"
                raise KeyError(f"No schema context for {table}. Known: {known}")
        return _SelectedCatalog(self, tables)

    def vocabulary(
        self, tables: Iterable[str]
    ) -> Mapping[str, Mapping[str, tuple[CatalogValue, ...]]]:
        """Return the fuzzy-matchable values of the given tables."""
        self._check_generation()
        catalog: dict[str, dict[str, tuple[CatalogValue, ...]]] = {}
        for table in tables:
            columns: dict[str, tuple[CatalogValue, ...]] = {}
            for name in vocabulary_columns(self._contexts[table]):
                values = self._values_for(table, name)
                if values:
                    columns[name] = values
            catalog[table] = columns
        return catalog

    def lookup_reference(
        self, key: str, tables: Iterable[str]
    ) -> tuple[ReferenceMatch, ...]:
        """Return every reference column of ``tables`` holding exactly ``key``."""
        self._check_generation()
        matches: list[ReferenceMatch] = []
        for table in tables:
            for column in reference_columns(self._contexts[table]):
                cache_key = (table, column, key)
                if cache_key not in self._references:
                    self._references[cache_key] = self._lookup_reference(
                        table, column, key
                    )
                value = self._references[cache_key]
                if value is not None:
                    matches.append(ReferenceMatch(table, column, value))
        return tuple(matches)


@dataclass(frozen=True, slots=True)
class _SelectedCatalog:
    """One question's bound view of a :class:`ValuesCatalog`."""

    catalog: ValuesCatalog
    tables: tuple[str, ...]

    def vocabulary(self) -> Mapping[str, Mapping[str, tuple[CatalogValue, ...]]]:
        return self.catalog.vocabulary(self.tables)

    def lookup_reference(self, key: str) -> tuple[ReferenceMatch, ...]:
        return self.catalog.lookup_reference(key, self.tables)


def _build_column_values(
    values: list[str] | None, max_distinct: int
) -> tuple[CatalogValue, ...] | None:
    if values is None or len(values) > max_distinct:
        return None

    kinds: dict[str, str] = {}
    for raw in values:
        value = str(raw).strip()
        if not value or len(value) > MAX_VALUE_LEN:
            continue
        _record(kinds, value, "equals")
        if "," in value:
            # "Lahore, Punjab" should still be findable by someone who says "Punjab".
            for part in value.split(","):
                part = part.strip()
                if part and len(part) <= MAX_VALUE_LEN:
                    _record(kinds, part, "contains")

    return tuple(CatalogValue(value, kind) for value, kind in kinds.items())


def _record(kinds: dict[str, str], value: str, match_kind: str) -> None:
    """Keep the strongest match kind seen for a value; equality beats containment."""
    current = kinds.get(value)
    if current is None or (current == "contains" and match_kind == "equals"):
        kinds[value] = match_kind


# ------------------------------------------------------------------ database


def database_catalog(
    db: Database,
    contexts: Mapping[str, SchemaContext],
    *,
    max_distinct: int = MAX_DISTINCT,
) -> ValuesCatalog:
    """Wire a :class:`ValuesCatalog` to a live database.

    Every identifier is composed with :mod:`psycopg.sql` rather than interpolated. The
    table and column names come from the committed contexts and so are not attacker
    controlled, but the rule holds everywhere or it holds nowhere.
    """
    def fetch_distinct(table: str, column: str) -> list[str] | None:
        schema, name = table.split(".", 1)
        query = sql.SQL(
            "SELECT DISTINCT {column}::text FROM {table} "
            "WHERE {column} IS NOT NULL LIMIT {limit}"
        ).format(
            column=sql.Identifier(column),
            table=sql.Identifier(schema, name),
            # One over the ceiling, so "too many" is distinguishable from "exactly the
            # ceiling" without counting the whole column first.
            limit=sql.Literal(max_distinct + 1),
        )
        with db.connection() as conn:
            return [row[0] for row in conn.execute(query).fetchall()]

    def lookup_reference(table: str, column: str, key: str) -> str | None:
        schema, name = table.split(".", 1)
        # The stored value is normalized in the database so that the comparison is the
        # same one reference_key() performs. This cannot use an index, but a reference
        # lookup runs once per mention and the result is cached.
        query = sql.SQL(
            "SELECT {column} FROM {table} "
            "WHERE regexp_replace(lower({column}), '[^a-z0-9]', '', 'g') = %s LIMIT 1"
        ).format(column=sql.Identifier(column), table=sql.Identifier(schema, name))
        with db.connection() as conn:
            row = conn.execute(query, (key,)).fetchone()
        return None if row is None else row[0]

    def generation() -> str:
        """A fingerprint that advances whenever anything has been written.

        The snapshot's ``xmax`` is the next transaction id the cluster will assign. Read
        transactions run on virtual ids and do not consume one, so two reads with no
        write between them return the same value, while any committed write moves it on.

        Chosen over ``pg_stat_user_tables`` because the statistics collector reports
        asynchronously: a catalog refreshed immediately after an ingest would have read
        the old counters and cached the old values, which is the exact failure this
        generation key exists to prevent. This errs the other way -- a write to any
        schema invalidates the catalog, costing a re-read we did not strictly need.
        """
        with db.connection() as conn:
            row = conn.execute(
                "SELECT pg_snapshot_xmax(pg_current_snapshot())::text"
            ).fetchone()
        return "0" if row is None else row[0]

    return ValuesCatalog(
        contexts=contexts,
        fetch_distinct=fetch_distinct,
        lookup_reference=lookup_reference,
        generation=generation,
        max_distinct=max_distinct,
    )
