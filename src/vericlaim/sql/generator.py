"""Write one PostgreSQL SELECT for one plan step.

This is the only component in the system that produces SQL, and nothing downstream trusts
it: :mod:`vericlaim.sql.validator` rejects anything unsafe on the AST before it can reach
Postgres, and the read-only role rejects it again if the validator were ever wrong. What
the generator is responsible for is narrower, and two-fold.

**The plan's intent has to survive into the SQL.** A model asked to count rows by group
routinely returns the count and drops the grouping column, leaving a number attached to
nothing. :func:`_preserve_planned_projections` reads the columns the plan explicitly
projected, checks them against the primary table's documented columns, and adds back any
the generated SQL lost -- cheaper and more reliable than another round trip, and incapable
of adding a column the context does not list.

**Several genuinely different attempts, at once.** C-5.9 arbitrates between candidates by
clustering them on the results they produce, which only discriminates if the candidates
disagree. Sampling one prompt N times produces N near-identical queries; two prompts that
ask for different reasoning produce candidates worth comparing. They run concurrently
because they have no order, and four sequential model calls is four round trips of latency
for nothing.

As in the planner, the prompt carries no insurance vocabulary. The domain rules live in
the reviewed contexts as `cautions`, and the prompt's job is to make them binding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from vericlaim.sql.contexts import SchemaContext, context_detail
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.resolver import EntityResolution, stored_values

GENERATOR_TASK = "sql_generator"
MAX_CANDIDATE_WORKERS = 4

GENERATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}

GENERATOR_SYSTEM_PROMPT = """\
Write exactly one read-only PostgreSQL query for the supplied plan step. Return only the
structured sql field.

The result must be a single statement whose root is SELECT, or WITH ending in SELECT. No
data definition, no data modification, no system catalogs, no invented identifiers. Use
only the tables and columns described in `schema_contexts`, and make the table named by
the plan step the primary source of the query.

THE CAUTIONS ARE BINDING.
Each supplied context carries a `cautions` list: reviewed distinctions that make a query
wrong in a way that still returns a plausible number. Every caution on a table you read is
an instruction. Where the plan and a caution disagree, follow the caution.

JOINS.
Join only along relationships the contexts declare in their `joins` blocks, and only
tables the plan step lists. Give every physical source a distinct alias. Where a declared
key is nullable, an inner join drops the unmatched rows: use an outer join where the step
says those rows must survive.

PROJECTION.
Name columns explicitly rather than selecting everything; COUNT(*) is the exception and is
the right way to count rows. Project what the question asks for and nothing more, except
values another rule here requires. Group by every projected column that is not aggregated.
Never project a constant as though it were a stored value, and never alias a literal or an
unrelated column to stand in for an attribute the tables do not hold: every projected
value must come from a column of the supplied tables.

FILTERS.
Every predicate must trace to an explicit restriction in the question. Sample values, value
sets, observed ranges, null counts, apparent data quality and convenience never authorize
one, and neither does a filter in the plan that the question does not support -- drop it.
Do not add null, not-null, trimming or blank-value predicates unless the question asks
about missing or present values. The only exception is a guard the superlative rule
requires.

COUNTING AND RATIOS.
Do not introduce a distinct count unless the question asks for distinct or unique entities,
or a join or expansion in this same query can duplicate the thing being counted. A
sequential-looking column, or an identifier-shaped name, is not evidence of uniqueness. For
any proportion, rate or percentage, project the numerator and the denominator beside the
ratio so the answer can state the counts it rests on.

UNITS.
A column declaring a `unit` may be added to, or compared with, another only when the unit
is identical. Never mix a rate with an amount, or two amounts declared in different units.

SUPERLATIVE TIES.
When the question asks which entity has the most, least, highest, lowest, largest,
smallest, best or worst value, return every entity tied at that extreme, never one
arbitrary row, so an ordering with a limit of one is forbidden. Compute the per-entity
value in a CTE where grouping is needed and filter with `value = (SELECT MAX(value) FROM
cte)`; for a directly stored metric filter with `metric = (SELECT MAX(metric) FROM t)`.
Exclude rows whose grouping value is null before computing the extreme; a null group is not
a valid answer to a which-entity question, and this guard is required rather than an
unrequested filter. An ordering with a limit of N is correct only where the question asks
for a top or bottom N, and there the ordering column's nulls must be excluded or sorted
last.

RESOLVED VALUES.
`resolved_entities` gives the values the database actually stores for what the question
said. It corrects spelling only: it never chooses which column to filter on and never
widens the question, and one mention listed under several columns is an inventory of where
that value occurs, not permission to filter on all of them. Filter the chosen column with
exactly those values -- `column = 'value'` for one, `column IN (...)` for several. Where
the match kind is "contains", use `column ILIKE '%value%'` patterns ORed across the values.
Never filter with the wording from the question when a stored value was supplied.

COMPLETED STEPS.
`completed_steps` holds results already computed. They are grounded evidence for an
aggregate or a small final result only. Never rebuild a set of rows from them, and never
use them in place of reading a table: an earlier result may have been truncated.

Before returning, re-read your query once and remove every predicate you cannot trace to
an explicit restriction in the question or to a supplied stored value, even if the plan
contained it.
"""

EXECUTION_PLAN_STYLE = """\

Reason as a query planner before writing the SQL: which source is opened first, which rows
survive filtering, what is grouped, what is aggregated, and what is finally projected.
Return only the sql field.
"""

STYLES: tuple[str, ...] = ("direct", "execution_plan")


class GeneratorError(ValueError):
    """Raised when a plan step cannot be turned into SQL at all."""


@dataclass(frozen=True, slots=True)
class SqlCandidate:
    """One generated query, and which prompt produced it.

    The style travels with the SQL so that C-5.9's arbitration, and the trace, can say
    whether the candidates that agreed were reasoned about the same way -- two candidates
    from one prompt agreeing is much weaker evidence than two from different ones.
    """

    sql: str
    style: str


def generate_sql(
    question: str,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
    *,
    completed: Sequence[Mapping[str, Any]] = (),
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
) -> SqlCandidate:
    """Write the single query for one plan step, deterministically."""
    return _generate(
        question,
        step,
        contexts,
        completed=completed,
        resolved=resolved,
        gateway=gateway,
        style="direct",
        temperature=0.0,
    )


def generate_sql_candidates(
    question: str,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
    *,
    candidate_count: int,
    temperature: float,
    completed: Sequence[Mapping[str, Any]] = (),
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
) -> tuple[SqlCandidate, ...]:
    """Write several candidate queries for one plan step, concurrently.

    The styles alternate rather than filling the first half with one: an interrupted or
    partially failed run then still holds candidates of both kinds.
    """
    if candidate_count <= 0:
        return ()

    styles = [STYLES[index % len(STYLES)] for index in range(candidate_count)]

    def generate(style: str) -> SqlCandidate:
        return _generate(
            question,
            step,
            contexts,
            completed=completed,
            resolved=resolved,
            gateway=gateway,
            style=style,
            temperature=temperature,
        )

    if candidate_count == 1:
        return (generate(styles[0]),)
    with ThreadPoolExecutor(
        max_workers=min(candidate_count, MAX_CANDIDATE_WORKERS)
    ) as pool:
        return tuple(pool.map(generate, styles))


def _generate(
    question: str,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
    *,
    completed: Sequence[Mapping[str, Any]],
    resolved: EntityResolution | None,
    gateway: Any | None,
    style: str,
    temperature: float,
) -> SqlCandidate:
    if step.table not in contexts:
        raise GeneratorError(
            f"Plan step names a table with no schema context: {step.table}"
        )

    payload = {
        "question": question,
        "plan_step": {
            "purpose": step.purpose,
            "table": step.table,
            "tables": list(step.tables),
            "calculations": step.calculations,
        },
        "schema_contexts": [
            context_detail(contexts[name]) for name in sorted(contexts)
        ],
        "completed_steps": [dict(entry) for entry in completed],
        "resolved_entities": stored_values(resolved),
    }
    system = GENERATOR_SYSTEM_PROMPT
    if style == "execution_plan":
        system += EXECUTION_PLAN_STYLE
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()
    completion = gateway.complete_json(
        GENERATOR_TASK, messages, GENERATOR_SCHEMA, temperature=temperature
    )
    sql = _parse_sql(completion)
    return SqlCandidate(
        sql=_preserve_planned_projections(sql, step, contexts), style=style
    )


def _parse_sql(completion: Any) -> str:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GeneratorError(f"Generator returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("sql"), str):
        raise GeneratorError("Generator returned no sql field")
    return parsed["sql"]


# ------------------------------------------------------------------ projections


def _preserve_planned_projections(
    sql: str, step: PlanStep, contexts: Mapping[str, SchemaContext]
) -> str:
    """Add back columns the plan projected and the generated SQL dropped.

    Bounded on both sides. A column is only restored if it appears as a direct projection
    in the plan's own calculations *and* is a documented column of the step's primary
    table, so the plan's prose can never introduce a column the context does not list.
    Anything that fails to parse is returned untouched, for the validator to reject.
    """
    context = contexts.get(step.table)
    if context is None:
        return sql

    available = {name.lower() for name in context.column_names}
    planned = _direct_projection_columns(step.calculations, available)
    if not planned:
        return sql

    missing = sorted(planned - _direct_projection_columns(sql, available))
    if not missing:
        return sql

    statement = _parse_select(sql)
    if statement is None:
        return sql

    alias = _primary_table_alias(statement, step.table)
    for name in missing:
        statement.select(exp.column(name, table=alias), copy=False)
    return statement.sql(dialect="postgres")


def _direct_projection_columns(sql: str, available: set[str]) -> set[str]:
    """Return the bare documented columns a statement projects directly.

    Only bare columns count. An aggregate or an expression is the query's own working, not
    a value the plan asked to see beside the result.
    """
    statement = _parse_select(sql)
    if statement is None:
        return set()

    columns: set[str] = set()
    for projection in statement.expressions:
        expression = (
            projection.this if isinstance(projection, exp.Alias) else projection
        )
        if isinstance(expression, exp.Column) and expression.name.lower() in available:
            columns.add(expression.name.lower())
    return columns


def _parse_select(sql: str) -> exp.Select | None:
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except (ParseError, ValueError):
        return None
    return statement if isinstance(statement, exp.Select) else None


def _primary_table_alias(statement: exp.Select, table: str) -> str:
    """Return the alias the primary table goes by, matching qualified or bare names."""
    bare = table.rsplit(".", 1)[-1].lower()
    for source in statement.find_all(exp.Table):
        if source.name.lower() == bare:
            return source.alias_or_name
    return bare
