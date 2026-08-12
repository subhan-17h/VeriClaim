"""The bounded repair loop, where every safeguard in the SQL subsystem meets.

One plan step becomes one answered query by going round this loop: generate, validate,
ground the values against the catalog, execute, observe, and -- only if something is
wrong -- refine and go round again.

**The bounds are the feature.** A repair loop that can run forever is not a safety
mechanism; it is a way to spend a day's free-tier quota on one question. Three independent
bounds stop it, and in any real failure only one of them will be the one that fires:

* the attempt count, from ``sql_max_refine_attempts``;
* a **per-step wall-clock budget**, which the reference implementation had no equivalent
  of -- its only real timeout was Postgres's, and that does nothing about a loop whose
  time goes on model calls rather than queries;
* the refusal to continue when a repair returns the query it was asked to fix, because
  every further attempt then costs a call to learn the same thing.

**An empty result is not a fault.** Zero rows is frequently the correct answer, and a loop
that rewrites until something comes back manufactures a confident answer to a question
nobody asked. So emptiness gets one repair, then it is reported as the truth. Before even
that, the backstop runs: if a filter names a value the database does not hold, no rewrite
can conjure rows for it, so the run says which value it was instead of spending the budget
discovering that it cannot.

That backstop replaces the one it was adapted from, which could not fire. In the original,
the grounding rewrite ran before execution and the empty-result backstop then re-ran the
same rewrite over the already-rewritten statement -- which by construction returns nothing
to change. Naming the unresolvable value is what that step was reaching for.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot.errors import ParseError

from vericlaim.config import Settings, get_settings
from vericlaim.sql.contexts import SchemaContext, allow_list
from vericlaim.sql.generator import generate_sql
from vericlaim.sql.observer import ExecutionResult, Observation, observe
from vericlaim.sql.planner import PlanStep, QueryPlan
from vericlaim.sql.refiner import execution_feedback, refine_sql
from vericlaim.sql.resolver import (
    EntityResolution,
    fuzzy_rewrite_sql,
    unresolvable_filters,
)
from vericlaim.sql.validator import validate_sql
from vericlaim.sql.values_catalog import Catalog

# One repair for an empty result, then it is believed. More than one is the loop arguing
# with the data.
MAX_EMPTY_REFINES = 1
# How many rows of a completed step are offered to the next one. Enough to carry an
# aggregate or a short list; never enough to reconstruct a row set, which the prompts
# forbid because an earlier result may have been truncated by the row limit.
CARRIED_ROW_LIMIT = 20

Executor = Callable[[str], ExecutionResult]


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What became of one plan step.

    ``ok_empty`` is deliberately not ``failed``: the query was right and there are no such
    rows. Collapsing the two would let a truthful "none" be reported as a breakdown.
    """

    step: PlanStep
    status: str
    sql: str
    result: ExecutionResult | None = None
    observation: Observation | None = None
    attempts: int = 0
    failure: str = ""

    @property
    def answered(self) -> bool:
        return self.status in {"completed", "ok_empty"}


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """Every step of one plan, and whether the whole thing stands up."""

    plan: QueryPlan
    steps: tuple[StepOutcome, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.plan.answerable
            and bool(self.steps)
            and all(step.answered for step in self.steps)
        )


def run_pipeline(
    question: str,
    plan: QueryPlan,
    contexts: Mapping[str, SchemaContext],
    *,
    catalog: Catalog,
    execute: Executor,
    settings: Settings | None = None,
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
    now: Callable[[], float] | None = None,
) -> PipelineOutcome:
    """Run every step of a plan, in order, stopping at the first that fails.

    Steps are sequential because a later one may read an earlier one's result; running it
    on nothing would produce a number with no basis.
    """
    if not plan.answerable:
        return PipelineOutcome(plan=plan)

    resolved_settings = settings or get_settings()
    outcomes: list[StepOutcome] = []
    completed: list[dict[str, Any]] = []

    for step in plan.steps:
        outcome = run_step(
            question,
            step,
            contexts,
            catalog=catalog,
            execute=execute,
            settings=resolved_settings,
            completed=completed,
            resolved=resolved,
            gateway=gateway,
            now=now,
        )
        outcomes.append(outcome)
        if not outcome.answered:
            break
        completed.append(_carried(outcome))

    return PipelineOutcome(plan=plan, steps=tuple(outcomes))


def run_step(
    question: str,
    step: PlanStep,
    contexts: Mapping[str, SchemaContext],
    *,
    catalog: Catalog,
    execute: Executor,
    settings: Settings | None = None,
    completed: Sequence[Mapping[str, Any]] = (),
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
    now: Callable[[], float] | None = None,
) -> StepOutcome:
    """Answer one plan step, repairing it up to the configured bounds."""
    resolved_settings = settings or get_settings()
    clock = now or time.monotonic
    deadline = clock() + resolved_settings.sql_step_budget_s
    allowed = allow_list(contexts, step.tables)

    sql = generate_sql(
        question,
        step,
        contexts,
        completed=completed,
        resolved=resolved,
        gateway=gateway,
    ).sql

    attempts = 0
    empty_refines = 0
    result: ExecutionResult | None = None
    observation: Observation | None = None
    executable = ""

    while True:
        verdict = validate_sql(sql, allowed, resolved_settings.sql_row_limit)
        if verdict.ok:
            executable = _grounded(verdict.sql, catalog, allowed, resolved_settings)
            result = execute(executable)
            observation = observe(result, step, contexts)

            if observation.verdict == "ok":
                return StepOutcome(
                    step, "completed", executable, result, observation, attempts
                )

            if observation.verdict == "empty_result":
                unknown = unresolvable_filters(executable, catalog)
                if unknown:
                    return StepOutcome(
                        step,
                        "ok_empty",
                        executable,
                        result,
                        observation,
                        attempts,
                        "No rows: the database holds no such value for "
                        f"{', '.join(repr(value) for value in unknown)}.",
                    )
                if empty_refines >= MAX_EMPTY_REFINES:
                    return StepOutcome(
                        step, "ok_empty", executable, result, observation, attempts
                    )
                empty_refines += 1

            failure = f"{observation.verdict}: {observation.reason}"
        else:
            failure = f"validation_failed: {verdict.reason}"

        if attempts >= resolved_settings.sql_max_refine_attempts:
            return StepOutcome(
                step,
                "failed",
                executable or sql,
                result,
                observation,
                attempts,
                failure,
            )
        if clock() >= deadline:
            return StepOutcome(
                step,
                "failed",
                executable or sql,
                result,
                observation,
                attempts,
                f"budget_exhausted: the step exceeded its "
                f"{resolved_settings.sql_step_budget_s:g}s wall-clock budget after "
                f"{attempts} repair(s). Last problem: {failure}",
            )

        repaired = refine_sql(
            question,
            step,
            executable or sql,
            failure,
            contexts,
            completed=completed,
            resolved=resolved,
            feedback=execution_feedback(result),
            gateway=gateway,
        )
        attempts += 1

        if _same_sql(repaired, sql) or (executable and _same_sql(repaired, executable)):
            # The repair returned what it was asked to fix. If the complaint was
            # emptiness, that is the model agreeing the query is right.
            status = "ok_empty" if failure.startswith("empty_result") else "failed"
            return StepOutcome(
                step,
                status,
                executable or sql,
                result,
                observation,
                attempts,
                "" if status == "ok_empty" else f"no_progress: {failure}",
            )
        sql = repaired


def _grounded(
    sql: str, catalog: Catalog, allowed: Any, settings: Settings
) -> str:
    """Rewrite filter values to the spellings the database stores, if any resolve.

    Re-validated afterwards: the rewrite edits the statement, and anything the validator
    has not seen in its final form has not been checked. A rewrite that fails to validate
    is discarded rather than executed.
    """
    rewritten = fuzzy_rewrite_sql(sql, catalog)
    if rewritten is None or _same_sql(rewritten, sql):
        return sql
    verdict = validate_sql(rewritten, allowed, settings.sql_row_limit)
    return verdict.sql if verdict.ok else sql


def _carried(outcome: StepOutcome) -> dict[str, Any]:
    """Summarize a completed step for the next one to read."""
    result = outcome.result
    return {
        "purpose": outcome.step.purpose,
        "sql": outcome.sql,
        "columns": list(result.columns) if result else [],
        "rows": [list(row) for row in result.rows[:CARRIED_ROW_LIMIT]] if result else [],
        "row_count": result.row_count if result else 0,
    }


def _same_sql(left: str, right: str) -> bool:
    """Compare two statements ignoring formatting, so a reprint is not mistaken for work."""
    return _canonical(left) == _canonical(right)


def _canonical(sql: str) -> str:
    try:
        return sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres")
    except (ParseError, ValueError, AttributeError):
        return " ".join(sql.split()).casefold()
