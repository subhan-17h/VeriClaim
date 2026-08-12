"""Repair one failed query, without letting it wander off the question.

The refiner is the only component that gets a second chance, which makes it the one most
able to do damage. Told that a query returned nothing, a model will happily relax the
filters until something comes back -- and the result is a confident, well-formed answer to
a question nobody asked. Told that a query failed to validate, it will reach for a table
that was never selected. So the repair is scoped to the plan step it was handed, and most
of the prompt is about what may *not* change.

The execution feedback is what makes the repair informed rather than a re-roll: the error
text, or the columns and the first few rows that came back. It is truncated on both axes,
because the point is the shape of what returned, not carrying a result set through the
context window once per attempt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from vericlaim.sql.contexts import SchemaContext, context_detail
from vericlaim.sql.generator import GENERATOR_SCHEMA, GeneratorError
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.resolver import EntityResolution, stored_values

REFINER_TASK = "sql_refiner"
FEEDBACK_ROW_LIMIT = 5
FEEDBACK_CELL_LIMIT = 200

REFINER_SYSTEM_PROMPT = """\
Repair the failed PostgreSQL query. Return only the structured sql field.

Produce exactly one read-only SELECT, over only the tables the plan step lists and only
the columns their contexts describe. Correct the reported problem and change nothing else.
If the query you were given is already correct for the plan step, return it unchanged.

WHAT MAY NOT CHANGE.
The question's restrictions are fixed. Preserve every value and condition the question
asked for, exactly, and do not add a filter it did not ask for -- not a null or blank-value
predicate, not a data-cleaning predicate, not a narrowing you think would help. Do not
reach for a table the plan step does not list. Where a supplied stored value was used,
keep it: it is the spelling the database holds.

AN EMPTY RESULT IS OFTEN CORRECT.
When the failure is that nothing came back, do not widen the query until something does.
Re-read every predicate against the question instead and remove any that the question does
not support -- a phrase naming a table or an attribute is not a value restriction. If every
predicate traces to the question, the query is right and the answer is that there are no
such rows: return it unchanged.

USE THE FEEDBACK.
`execution_feedback` carries the database's error, or the columns and first rows that came
back. Locate the precise failure in it before rewriting, rather than rewriting the whole
query.

THE CAUTIONS ARE BINDING.
Each supplied context carries a `cautions` list: reviewed distinctions that make a query
wrong in a way that still returns a plausible number. If the failure is that the result
contradicts one of them, the fix is almost always that several columns were aggregated over
different sets of rows because a join multiplied one of them -- repair the row set, not the
arithmetic.

SHAPES THAT MUST SURVIVE A REPAIR.
Where the question asks which entity has the most, least, highest, lowest, largest,
smallest, best or worst value, the repaired query must still return every entity tied at
that extreme, by equality against a MAX or MIN subquery over the computed value, excluding
rows whose grouping value is null. Several tied rows are a correct result, not a fault to
repair, and an ordering with a limit of one is never the fix. Where a join or expansion can
duplicate the thing being counted, count distinct values rather than expanded rows.

Prior results in `completed_steps` may be copied into predicates exactly where the step
requires it. They never authorize reading a table the step does not list, a write, a system
catalog, an invented identifier or value, or a second statement.
"""


def refine_sql(
    question: str,
    step: PlanStep,
    failed_sql: str,
    failure: str,
    contexts: Mapping[str, SchemaContext],
    *,
    completed: Sequence[Mapping[str, Any]] = (),
    resolved: EntityResolution | None = None,
    feedback: Mapping[str, Any] | None = None,
    gateway: Any | None = None,
) -> str:
    """Return a repaired query for one failed attempt at ``step``."""
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
        "failed_sql": failed_sql,
        "failure": failure,
        "schema_contexts": [
            context_detail(contexts[name]) for name in sorted(contexts)
        ],
        "completed_steps": [dict(entry) for entry in completed],
        "resolved_entities": stored_values(resolved),
        "execution_feedback": dict(feedback) if feedback is not None else None,
    }
    messages = [
        {"role": "system", "content": REFINER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=True, default=str),
        },
    ]

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()
    completion = gateway.complete_json(REFINER_TASK, messages, GENERATOR_SCHEMA)
    return _parse_sql(completion)


def execution_feedback(result: ExecutionResult | None) -> dict[str, Any] | None:
    """Summarize what the database did, small enough to send on every attempt."""
    if result is None:
        return None
    if result.error:
        return {"error": result.error}
    return {
        "columns": list(result.columns),
        "row_count": result.row_count,
        "rows": [
            [_truncate(value) for value in row]
            for row in result.rows[:FEEDBACK_ROW_LIMIT]
        ],
    }


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > FEEDBACK_CELL_LIMIT:
        return f"{value[:FEEDBACK_CELL_LIMIT]}..."
    return value


def _parse_sql(completion: Any) -> str:
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GeneratorError(f"Refiner returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("sql"), str):
        raise GeneratorError("Refiner returned no sql field")
    return parsed["sql"]
