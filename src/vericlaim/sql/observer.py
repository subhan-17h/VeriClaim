"""Judge an execution result. Deterministically, with no model involved.

Between "the query ran" and "the query answered the question" there is a gap that no
error message fills. A query can succeed, return a tidy number, and have computed
something else entirely. The observer is what notices, and it is deterministic on
purpose: a model asked whether its own result looks right will agree with itself, and the
repair loop it drives would then be steered by the same judgement that produced the error.

Two families of check.

**Shape.** Does the result match what the plan said it would be? A scalar aggregate that
came back with four hundred rows did not compute what the plan described, and an average
that is null in every row averaged nothing.

**Values.** Do the numbers contradict a fact the reviewed contexts declare? This is the
family the reference implementation had no equivalent of, and it catches the failure that
is otherwise invisible: several columns aggregated over different sets of rows because a
join fanned one of them out. Every column is individually plausible; only the arithmetic
between them gives it away.

Nothing here knows anything about insurance. The facts come from the contexts'
`invariants` blocks, declared beside the prose cautions that state them, so a renamed
column changes one YAML file and no code. Each invariant is checked only where it is
sound to: an ordering between two columns means nothing between two separate extremes of
them, so it is applied to raw values and not to aggregates, while a sum is linear and
survives SUM() -- which is exactly where the fan-out bug shows up.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from vericlaim.sql.contexts import (
    Invariant,
    NonNegativeInvariant,
    OrderedInvariant,
    SchemaContext,
    SumInvariant,
)
from vericlaim.sql.planner import PlanStep

# Money is stored to the paisa. A discrepancy smaller than this is the arithmetic of
# rounding, not a query reading the wrong rows.
SUM_TOLERANCE = Decimal("0.01")

AGGREGATE_TERMS = ("count", "average", "avg", "sum", "minimum", "maximum", "min", "max")
# "group" as a stem, not the phrase "group by": the planner writes this field in prose
# and conjugates it freely -- "grouped by region", "grouping by region" -- and matching
# only the SQL keyword missed the forms a model actually writes. The failure was not a
# missed warning but a rejected correct answer: a real GROUP BY was called a scalar that
# returned too many rows, and the repair loop then spent its whole budget rewriting a
# query that was already right. A stray "age group" merely skips the arity check, which
# is the safe direction to be wrong in.
GROUPED_TERMS = ("group", "per ", "each ", "distribution", "breakdown", "rank", "top ")

# Phrases that mention grouping in order to rule it out. Removed before the search
# above, the same way thresholds are removed before the superlative search: a plan
# saying "with no grouping" is the clearest possible statement that one row is expected,
# and a bare stem match would read it as the opposite.
UNGROUPED_TERMS = (
    "no grouping",
    "no group",
    "without grouping",
    "without group",
    "not grouped",
    "ungrouped",
)
SUPERLATIVE_TERMS = (
    "most",
    "least",
    "fewest",
    "highest",
    "lowest",
    "largest",
    "smallest",
    "best",
    "worst",
    "tie",
    "tied",
    "ties",
    "max(",
    "min(",
    "maximum value",
    "minimum value",
)

# Which projections each kind of invariant may be judged through. A bare column is always
# eligible. Beyond that: a sum is linear, so it survives SUM over the same row set and its
# failure there is the fan-out bug. A floor survives any aggregate that returns one of the
# values or their mean. An ordering survives neither -- MIN of one column and MIN of
# another come from different rows, and their order proves nothing about any single one.
SUM_FUNCTIONS = frozenset({"", "sum"})
NON_NEGATIVE_FUNCTIONS = frozenset({"", "sum", "min", "max", "avg"})
ORDERED_FUNCTIONS = frozenset({""})


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """One executed query and what came back.

    Produced by the executor in C-5.8; defined here because the observer is what gives it
    meaning. The SQL travels with the rows because both the value checks and the citation
    in :class:`~vericlaim.evidence.SqlLocator` need to know what produced them.
    """

    sql: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    error: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class Observation:
    """The verdict on one execution, and what is wrong if anything is."""

    verdict: str
    reason: str
    violations: tuple[str, ...] = ()


def observe(
    result: ExecutionResult,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
) -> Observation:
    """Classify an execution result against its plan step and the reviewed contexts."""
    if result.error:
        return Observation("sql_error", result.error)
    if result.row_count == 0:
        return Observation("empty_result", "The query returned zero rows.")

    shape = _shape_verdict(result, step)
    if shape is not None:
        return shape

    violations = _invariant_violations(result, step, contexts)
    if violations:
        return Observation(
            "implausible_values",
            "The result contradicts a documented fact about the data, which usually "
            "means the query read a different set of rows for each column.",
            violations,
        )
    return Observation("ok", "The result is the shape the plan described.")


# ------------------------------------------------------------------ shape


def _shape_verdict(result: ExecutionResult, step: PlanStep) -> Observation | None:
    if not result.columns:
        return Observation(
            "suspicious_shape", "The query returned rows without any columns."
        )
    if any(len(row) != len(result.columns) for row in result.rows):
        return Observation(
            "suspicious_shape",
            "The result row width does not match the column count.",
        )

    # Both free-text fields, because the planner splits its prose between them as it
    # sees fit: a step whose purpose reads "the rate for each region" may leave only
    # "compute the gap against target" in calculations, and reading one field alone
    # then calls a per-region result a scalar that returned too many rows.
    calculations = f"{step.purpose} {step.calculations}".lower()

    average_indexes = [
        index
        for index, column in enumerate(result.columns)
        if "average" in column.lower() or "avg" in column.lower()
    ]
    expects_average = "average" in calculations or "avg" in calculations
    if (
        expects_average
        and average_indexes
        and all(row[index] is None for row in result.rows for index in average_indexes)
    ):
        return Observation(
            "suspicious_shape", "The requested average is NULL in every result row."
        )

    if _expects_scalar(calculations) and result.row_count != 1:
        return Observation(
            "suspicious_shape",
            "The plan describes a scalar aggregate but the query returned "
            f"{result.row_count} rows.",
        )
    return None


def _expects_scalar(calculations: str) -> bool:
    """Whether the plan described one number rather than one number per group."""
    # "at least"/"at most" are thresholds, not extremes; leaving them in would read every
    # bounded count as a superlative and disable the arity check.
    threshold_free = calculations.replace("at least", "").replace("at most", "")
    superlative = any(
        re.search(rf"\b{re.escape(term)}", threshold_free) for term in SUPERLATIVE_TERMS
    )
    grouped_free = calculations
    for phrase in UNGROUPED_TERMS:
        grouped_free = grouped_free.replace(phrase, "")
    return (
        any(term in calculations for term in AGGREGATE_TERMS)
        and not any(term in grouped_free for term in GROUPED_TERMS)
        and not superlative
    )


# ------------------------------------------------------------------ values


def _invariant_violations(
    result: ExecutionResult,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
) -> tuple[str, ...]:
    """Check every declared invariant the result carries enough columns to judge."""
    sources = _projection_sources(result.sql, len(result.columns))
    if sources is None:
        return ()

    violations: list[str] = []
    for table in step.tables:
        context = contexts.get(table)
        if context is None:
            continue
        for invariant in context.invariants:
            violated = check_invariant(invariant, sources, result.rows)
            if violated is not None:
                violations.append(violated)
    return tuple(violations)


def check_invariant(
    invariant: Invariant,
    sources: Mapping[tuple[str, str], int],
    rows: Sequence[Sequence[Any]],
) -> str | None:
    """Return a description of the first row that violates ``invariant``, or None.

    Public because the corpus validator judges the *generated* rows by exactly this
    rule before they are ever loaded. Two implementations of "incurred is paid plus
    reserve" would eventually disagree, and the way that surfaces is a corpus the
    validator passes and the observer then flags on every query over it.

    ``sources`` maps ``(function, column)`` -- in that order, and with the column
    lowercased -- to the position that column occupies in ``rows``. A caller holding
    plain stored rows rather than a query result uses ``""`` for the function, which
    every invariant kind accepts as a bare column.
    """
    if isinstance(invariant, SumInvariant):
        return _check_sum(invariant, sources, rows)
    if isinstance(invariant, NonNegativeInvariant):
        return _check_non_negative(invariant, sources, rows)
    return _check_ordered(invariant, sources, rows)


def _check_sum(
    invariant: SumInvariant,
    sources: Mapping[tuple[str, str], int],
    rows: Sequence[Sequence[Any]],
) -> str | None:
    for function in sorted(SUM_FUNCTIONS):
        indexes = _indexes(invariant.columns, function, sources)
        if indexes is None:
            continue
        total_index, *part_indexes = indexes
        for row in rows:
            total = _number(row[total_index])
            parts = [_number(row[index]) for index in part_indexes]
            if total is None or any(part is None for part in parts):
                continue
            difference = total - sum(parts)  # type: ignore[arg-type]
            if abs(difference) > SUM_TOLERANCE:
                return (
                    f"{invariant.total} is {total} but "
                    f"{' + '.join(invariant.parts)} is {sum(parts)}"  # type: ignore[arg-type]
                    f" (off by {difference}). {invariant.meaning}"
                )
    return None


def _check_non_negative(
    invariant: NonNegativeInvariant,
    sources: Mapping[tuple[str, str], int],
    rows: Sequence[Sequence[Any]],
) -> str | None:
    for function in sorted(NON_NEGATIVE_FUNCTIONS):
        indexes = _indexes(invariant.columns, function, sources)
        if indexes is None:
            continue
        for row in rows:
            value = _number(row[indexes[0]])
            if value is not None and value < 0:
                return f"{invariant.column} is {value}. {invariant.meaning}"
    return None


def _check_ordered(
    invariant: OrderedInvariant,
    sources: Mapping[tuple[str, str], int],
    rows: Sequence[Sequence[Any]],
) -> str | None:
    for function in sorted(ORDERED_FUNCTIONS):
        indexes = _indexes(invariant.columns, function, sources)
        if indexes is None:
            continue
        lower_index, upper_index = indexes
        for row in rows:
            lower, upper = row[lower_index], row[upper_index]
            if lower is None or upper is None:
                continue
            try:
                out_of_order = upper < lower
            except TypeError:
                # Two columns of incomparable types cannot be judged; that is a schema
                # question, not a result to reject.
                return None
            if out_of_order:
                return (
                    f"{invariant.upper} is {upper}, before {invariant.lower} at "
                    f"{lower}. {invariant.meaning}"
                )
    return None


def _indexes(
    columns: Sequence[str], function: str, sources: Mapping[tuple[str, str], int]
) -> list[int] | None:
    """Return the output positions of ``columns``, all read through ``function``.

    All or nothing: an invariant judged with two of its three columns aggregated and the
    third raw would compare quantities that are not comparable.
    """
    indexes = [sources.get((function, name.lower())) for name in columns]
    if any(index is None for index in indexes):
        return None
    return indexes  # type: ignore[return-value]


def _projection_sources(
    sql: str, column_count: int
) -> dict[tuple[str, str], int] | None:
    """Map each output column back to the single stored column it reads, if it reads one.

    Returns None when the statement cannot be traced -- unparseable, not a plain SELECT,
    or a projection list that does not line up with the columns that came back, which is
    what ``SELECT *`` produces. An untraceable result is left unjudged rather than judged
    on a guess.

    A projection counts only when it references exactly one column: bare, or wrapped in a
    single aggregate. `incurred - paid AS reserve` is legitimately anything, and checking
    it against the floor declared for a reserve would reject correct queries.
    """
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except (ParseError, ValueError):
        return None
    if not isinstance(statement, exp.Select):
        return None

    projections = statement.expressions
    if len(projections) != column_count:
        return None

    sources: dict[tuple[str, str], int] = {}
    for index, projection in enumerate(projections):
        traced = _traced_column(projection)
        # First position wins: two projections of the same column read the same value,
        # and the earlier one is the one a reader would name.
        if traced is not None and traced not in sources:
            sources[traced] = index
    return sources


def _traced_column(projection: exp.Expression) -> tuple[str, str] | None:
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    function = ""
    if isinstance(expression, exp.AggFunc):
        function = expression.sql_name().lower()
        expression = expression.this
    if isinstance(expression, exp.Distinct):
        return None
    if not isinstance(expression, exp.Column):
        return None
    return function, expression.name.lower()


def _number(value: Any) -> Decimal | None:
    """Coerce a stored value to a Decimal, or None if it is not a number.

    Decimal throughout: rows come back from Postgres as Decimal, and mixing them with
    floats would either raise or reintroduce the rounding error the tolerance exists to
    absorb.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None
