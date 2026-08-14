"""Deterministic SQL safety, decided on the parsed AST.

Nothing a model writes reaches Postgres without passing through here, and every decision
is made by inspecting the parsed statement -- never by asking a model whether its own SQL
looks safe. Adapted near-verbatim from an audited implementation, because a rewrite of a
safety layer buys nothing and risks a great deal.

The checks, in order, each one cheap enough to run before the expensive ones:

1. parse as PostgreSQL, exactly one statement;
2. the statement is a query -- ``SELECT`` or a set operation over queries;
3. no DDL/DML node anywhere in the tree, and no row locking;
4. every physical source resolves to a table on the allow-list, in an allowed schema,
   with no catalog component;
5. every column resolves against that table's declared columns
   (``qualify(validate_qualify_columns=True)``);
6. no uncorrelated ``EXISTS``;
7. ``LIMIT`` is an integer literal, injected when absent and capped when too large.

This layer and the read-only Postgres role are independent, and both are necessary. The
role cannot tell a legitimate aggregate from a scan of everything; this cannot survive
its own bugs.

Two deliberate departures from the source implementation:

* **Set operations are admitted.** It accepts only a bare ``SELECT``, which suits one
  table per question. Here, "water-damage claims plus the policies covering them" is
  naturally a ``UNION``, and rejecting it would make the repair loop rewrite correct SQL.
  Allow-list enforcement is unchanged: it walks scopes, so every leg is checked.
* **Schemas are plural.** It hard-codes ``public``. Here the allow-list carries the
  schema for each table, so ``ops`` and ``sheets`` are distinguished, and an unqualified
  table is rewritten to its qualified form when that is unambiguous -- otherwise the
  citation would depend on the session's ``search_path``.

``sqlglot`` is pinned exactly (see pyproject.toml): this depends on optimizer internals
that an unpinned bump could change without failing a single test.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

# Node types that must not appear anywhere in the tree, including inside a CTE. Resolved
# by name so a sqlglot version lacking one of them degrades to checking the rest rather
# than failing at import.
_PROHIBITED_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Copy",
    "Grant",
    "Revoke",
    "Into",
)

_PROHIBITED = tuple(
    node_type
    for name in _PROHIBITED_NAMES
    if isinstance((node_type := getattr(exp, name, None)), type)
)

# A query is a SELECT or a set operation over queries. Both are read-only shapes; the
# statement gate admits them and nothing else.
_QUERY_TYPES = (exp.Select, exp.SetOperation)


@dataclass(frozen=True, slots=True)
class AllowedTable:
    """One table the current question is permitted to read, and its columns.

    Built from the schema contexts. Columns are part of the allow-list rather than
    decoration: a hallucinated column name is one of the commonest ways generated SQL
    fails, and catching it here turns a runtime error into a repairable rejection.
    """

    schema: str
    table: str
    columns: tuple[str, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.schema.lower()}.{self.table.lower()}"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The verdict, plus what may be executed and what it reads.

    ``sql`` is empty on rejection -- a caller holding normalized SQL beside a rejection
    would eventually execute it. ``tables`` is the qualified physical sources, computed
    here because the citation in ``SqlLocator`` needs exactly this and recovering it
    later by pattern-matching the text would be strictly worse.
    """

    ok: bool
    reason: str
    sql: str = ""
    tables: tuple[str, ...] = field(default=())


def _reject(reason: str) -> ValidationResult:
    return ValidationResult(ok=False, reason=reason)


def _allow_map(allowed: Iterable[AllowedTable]) -> dict[str, AllowedTable]:
    """Index the allow-list by qualified name, lower-cased."""
    return {entry.qualified: entry for entry in allowed}


def _resolve_unqualified(
    statement: exp.Expression, allow_map: dict[str, AllowedTable]
) -> str:
    """Rewrite bare table names to their qualified form, in place.

    A model that omits the schema is repaired rather than rejected, but only when the
    name is unambiguous: two tables of the same name in different schemas is precisely
    the case where guessing would answer from the wrong source. Returns a rejection
    reason, or an empty string.
    """
    by_name: dict[str, list[AllowedTable]] = {}
    for entry in allow_map.values():
        by_name.setdefault(entry.table.lower(), []).append(entry)

    for table in statement.find_all(exp.Table):
        if table.db or not table.name:
            continue
        candidates = by_name.get(table.name.lower())
        if not candidates:
            # Left as-is: it may be a CTE or a derived table, which the scope walk
            # distinguishes from a physical source far more reliably than a name lookup.
            continue
        if len(candidates) > 1:
            names = ", ".join(sorted(entry.qualified for entry in candidates))
            return (
                f"Table reference {table.name!r} is ambiguous: it exists as {names}. "
                "Qualify it with its schema."
            )
        table.set("db", exp.to_identifier(candidates[0].schema))
    return ""


def _source_reason(
    scopes: list, allow_map: dict[str, AllowedTable]
) -> tuple[str, tuple[str, ...]]:
    """Check every physical source against the allow-list.

    Returns a rejection reason and, when there is none, the qualified tables read.
    """
    allowed_schemas = {entry.schema.lower() for entry in allow_map.values()}
    tables: list[str] = []

    for scope in scopes:
        if isinstance(scope.expression, exp.Select):
            has_source = bool(scope.selected_sources)
            for projection in scope.expression.expressions:
                is_star = isinstance(projection, exp.Star) or (
                    isinstance(projection, exp.Column)
                    and isinstance(projection.this, exp.Star)
                )
                if is_star and not has_source:
                    return "SELECT * requires an allowed source table", ()
        for _, source in scope.selected_sources.values():
            if not isinstance(source, exp.Table):
                continue
            if source.catalog:
                return f"Catalog is not allowed: {source.catalog}", ()
            schema = source.db.lower()
            if schema and schema not in allowed_schemas:
                return f"Schema is not allowed: {source.db}", ()
            qualified = f"{schema}.{source.name.lower()}"
            if qualified not in allow_map:
                return f"Table is not allowed: {source.name}", ()
            if qualified not in tables:
                tables.append(qualified)

    if not tables:
        return "The query must reference an allowed table", ()
    return "", tuple(tables)


def _uncorrelated_exists_reason(statement: exp.Expression) -> str:
    """Reject an ``EXISTS`` whose subquery does not reference the outer row.

    Row-independent by construction: it is either always true or always false, so it
    cannot express the restriction the question asked for. It is a common way a generated
    query silently answers a different question than the one posed.
    """
    scope_by_expression = {
        id(scope.expression): scope
        for scope in traverse_scope(statement)
        if isinstance(scope.expression, exp.Select)
    }
    for exists in statement.find_all(exp.Exists):
        subquery = exists.this
        if not isinstance(subquery, exp.Select):
            subquery = exists.find(exp.Select)
        scope = scope_by_expression.get(id(subquery))
        if scope is not None and not scope.external_columns:
            return (
                "Uncorrelated EXISTS subquery is not allowed: it is row-independent "
                "and cannot express the intended restriction"
            )
    return ""


def _limit_reason(statement: exp.Expression, row_limit: int) -> str:
    """Inject the row limit when absent, cap it when too large, reject anything odd."""
    limit = statement.args.get("limit")
    if limit is None:
        statement.limit(row_limit, copy=False)
        return ""

    value = limit.expression
    # `LIMIT -1` parses as a unary minus over a literal rather than a negative literal,
    # so the sign has to be unwrapped here. The source implementation's `parsed < 0`
    # branch is unreachable without this, which reports the wrong reason for a case the
    # repair loop then cannot fix.
    negated = isinstance(value, exp.Neg)
    if negated:
        value = value.this
    if not isinstance(value, exp.Literal) or not value.is_int:
        return "LIMIT must be an integer literal"
    parsed = int(value.this)
    if negated or parsed < 0:
        return "LIMIT must not be negative"
    if parsed > row_limit:
        limit.set("expression", exp.Literal.number(row_limit))
    return ""


def validate_sql(
    sql: str, allowed: Iterable[AllowedTable], row_limit: int
) -> ValidationResult:
    """Validate and normalize one read-only PostgreSQL query.

    Returns the statement rewritten with qualified tables and a bounded ``LIMIT``, or a
    rejection whose reason is specific enough for the repair loop to act on.
    """
    allow_map = _allow_map(allowed)
    if not allow_map:
        return _reject("No tables are allowed")
    if not isinstance(row_limit, int) or isinstance(row_limit, bool) or row_limit < 1:
        return _reject("Row limit must be a positive integer")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    # SqlglotError rather than ParseError: an unterminated quote raises TokenError,
    # which is ParseError's sibling, not its subclass. A model that emits prose in place
    # of SQL produces exactly that, and catching only ParseError let it escape past the
    # repair loop and abort the whole source instead of being rejected and retried.
    except (SqlglotError, ValueError) as exc:
        return _reject(f"SQL parse error: {exc}")
    if len(statements) != 1:
        return _reject("Exactly one SQL statement is required")

    statement = statements[0]
    if statement is None or not isinstance(statement, _QUERY_TYPES):
        return _reject("Only SELECT statements are allowed")
    if _PROHIBITED and any(statement.find_all(_PROHIBITED)):
        return _reject("DDL and DML operations are not allowed")
    if statement.args.get("locks"):
        return _reject("Row locking is not allowed in a read-only query")

    ambiguity = _resolve_unqualified(statement, allow_map)
    if ambiguity:
        return _reject(ambiguity)

    try:
        scopes = list(traverse_scope(statement))
    except OptimizeError as exc:
        return _reject(f"Unable to resolve SQL sources: {exc}")

    reason, tables = _source_reason(scopes, allow_map)
    if reason:
        return _reject(reason)

    # Nested schema so identically-named tables in ops and sheets stay distinct. Types
    # are irrelevant here -- only the set of column names is being validated.
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for entry in allow_map.values():
        schema.setdefault(entry.schema.lower(), {})[entry.table.lower()] = {
            column.lower(): "UNKNOWN" for column in entry.columns
        }

    try:
        statement = qualify(
            statement,
            dialect="postgres",
            schema=schema,
            validate_qualify_columns=True,
            identify=False,
        )
    except OptimizeError as exc:
        return _reject(f"Column validation failed: {exc}")

    exists_reason = _uncorrelated_exists_reason(statement)
    if exists_reason:
        return _reject(exists_reason)

    limit_reason = _limit_reason(statement, row_limit)
    if limit_reason:
        return _reject(limit_reason)

    return ValidationResult(
        ok=True,
        reason="SQL passed validation",
        sql=statement.sql(dialect="postgres"),
        tables=tables,
    )
