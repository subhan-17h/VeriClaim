"""The bounded repair loop: generate, validate, ground, execute, observe, refine.

Every safeguard in the SQL subsystem meets here, and what this file tests is that the loop
*stops*. A repair loop that can run forever is not a safety feature; it is a way to spend
an entire free-tier quota on one question. There are three independent bounds -- the
attempt count, the wall clock, and the refusal to keep going when a repair changes nothing
-- and each is tested on its own, because in a real failure only one of them will be the
one that fires.

The other thing under test is that an empty result is not treated as a fault. Zero rows is
frequently the correct answer, and a loop that rewrites until something comes back
manufactures a confident answer to a question nobody asked.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from vericlaim.config import Settings
from vericlaim.sql.contexts import ColumnContext, SchemaContext
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.pipeline import run_pipeline, run_step
from vericlaim.sql.planner import PlanStep, QueryPlan
from vericlaim.sql.values_catalog import CatalogValue, StaticCatalog

CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(
        ColumnContext(name="claim_id", type="bigint", meaning="Surrogate key."),
        ColumnContext(name="peril", type="text", meaning="Cause of loss."),
    ),
)
CONTEXTS = {CLAIMS.qualified: CLAIMS}
CATALOG = StaticCatalog(
    {"ops.claims": {"peril": (CatalogValue("water_damage"), CatalogValue("fire"))}}
)

COUNT_SQL = "SELECT COUNT(*) FROM ops.claims"


def step(calculations: str = "COUNT(*) over all rows.") -> PlanStep:
    return PlanStep(
        purpose="Count the rows.",
        table="ops.claims",
        tables=("ops.claims",),
        calculations=calculations,
    )


def settings(**overrides: Any) -> Settings:
    """One generated query by default, so the repair loop is what is under test."""
    return Settings(
        **{
            "sql_max_refine_attempts": 2,
            "sql_step_budget_s": 60.0,
            "sql_row_limit": 500,
            "sql_multi_candidate_enabled": False,
            **overrides,
        }
    )


@dataclass
class FakeGateway:
    """Returns a queue of SQL, one per call, repeating the last entry forever."""

    sql: list[str]
    tasks: list[str] = field(default_factory=list)
    # Candidates are generated concurrently, so the queue has to be handed out safely.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        with self.lock:
            index = min(len(self.tasks), len(self.sql) - 1)
            self.tasks.append(task)
        return _Completion({"sql": self.sql[index]})

    @property
    def refine_calls(self) -> int:
        return self.tasks.count("sql_refiner")


@dataclass
class _Completion:
    parsed: Any
    text: str = ""


@dataclass
class FakeDatabase:
    """Answers each executed statement from a queue of results."""

    results: list[ExecutionResult]
    executed: list[str] = field(default_factory=list)

    def __call__(self, sql: str) -> ExecutionResult:
        index = min(len(self.executed), len(self.results) - 1)
        self.executed.append(sql)
        canned = self.results[index]
        return ExecutionResult(
            sql=sql,
            columns=canned.columns,
            rows=canned.rows,
            error=canned.error,
        )


def rows(*values: tuple[Any, ...]) -> ExecutionResult:
    return ExecutionResult(sql="", columns=("count",), rows=values)


def failure(message: str) -> ExecutionResult:
    return ExecutionResult(sql="", error=message)


def run(
    gateway: FakeGateway,
    database: FakeDatabase,
    *,
    plan_step: PlanStep | None = None,
    now: Any = None,
    config: Settings | None = None,
):
    return run_step(
        "How many?",
        plan_step or step(),
        CONTEXTS,
        catalog=CATALOG,
        execute=database,
        settings=config or settings(),
        gateway=gateway,
        now=now,
    )


# ------------------------------------------------------------------ the happy path


def test_a_query_that_works_first_time_needs_no_repair() -> None:
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database)

    assert outcome.status == "completed"
    assert outcome.attempts == 0
    assert gateway.refine_calls == 0


def test_the_executed_sql_is_the_normalized_one_not_the_generated_one() -> None:
    """The validator qualifies tables and caps the row limit; the citation has to name
    what actually ran."""
    gateway = FakeGateway(["SELECT claim_id FROM claims"])
    database = FakeDatabase([rows((1,))])

    outcome = run(gateway, database)

    assert "ops.claims" in outcome.sql
    assert "LIMIT" in outcome.sql.upper()


def test_a_filter_written_from_the_question_is_grounded_before_execution() -> None:
    """The whole point of the catalog: the question says "water damage", the column holds
    `water_damage`, and an ungrounded filter would return zero rows and read as a fact."""
    gateway = FakeGateway(["SELECT claim_id FROM ops.claims WHERE peril = 'water damage'"])
    database = FakeDatabase([rows((1,))])

    run(gateway, database)

    assert "'water_damage'" in database.executed[0]


# ------------------------------------------------------------------ repair


def test_a_query_the_validator_rejects_is_repaired_without_being_executed() -> None:
    gateway = FakeGateway(["DROP TABLE ops.claims", COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database)

    assert outcome.status == "completed"
    assert outcome.attempts == 1
    assert len(database.executed) == 1


def test_a_hallucinated_column_is_repaired_before_the_database_sees_it() -> None:
    """The column allow-list is why this never becomes a database error: the commonest
    failure of generated SQL is caught where it can be described precisely."""
    gateway = FakeGateway(["SELECT bogus FROM ops.claims", COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database)

    assert outcome.status == "completed"
    assert len(database.executed) == 1
    assert "bogus" not in database.executed[0]


def test_a_query_the_database_rejects_is_repaired() -> None:
    gateway = FakeGateway(["SELECT claim_id FROM ops.claims", COUNT_SQL])
    database = FakeDatabase([failure("statement timeout"), rows((42,))])

    outcome = run(gateway, database)

    assert outcome.status == "completed"
    assert outcome.attempts == 1


def test_the_loop_stops_at_the_attempt_limit() -> None:
    gateway = FakeGateway(["SELECT a FROM ops.claims", "SELECT b FROM ops.claims",
                           "SELECT c FROM ops.claims", "SELECT d FROM ops.claims"])
    database = FakeDatabase([failure("no such column")])

    outcome = run(gateway, database)

    assert outcome.status == "failed"
    assert outcome.attempts == 2
    assert gateway.refine_calls == 2


def test_the_loop_stops_when_a_repair_changes_nothing() -> None:
    """A model returning the query it was asked to fix has nothing more to give, and
    every further attempt costs a call to learn the same thing."""
    gateway = FakeGateway(["SELECT bogus FROM ops.claims"])
    database = FakeDatabase([failure('column "bogus" does not exist')])

    outcome = run(gateway, database)

    assert outcome.status == "failed"
    assert gateway.refine_calls == 1


def test_the_loop_stops_when_the_step_runs_out_of_time() -> None:
    """The reference implementation's only real timeout was Postgres's, which does
    nothing about a loop whose time goes on model calls."""
    # One tick sets the deadline; the next is the budget check on the first iteration.
    clock = iter([0.0, 999.0, 999.0])
    gateway = FakeGateway(["SELECT bogus FROM ops.claims", COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database, now=lambda: next(clock))

    assert outcome.status == "failed"
    assert "budget" in outcome.failure.lower()
    assert gateway.refine_calls == 0


# ------------------------------------------------------------------ empty results


def test_an_empty_result_is_refined_once_and_then_accepted() -> None:
    """Zero rows is usually the answer. One attempt to find a mistake, then it is
    reported as the truth rather than rewritten until something appears."""
    gateway = FakeGateway(
        ["SELECT claim_id FROM ops.claims WHERE peril = 'fire'",
         "SELECT claim_id FROM ops.claims WHERE peril = 'water_damage'"]
    )
    database = FakeDatabase([ExecutionResult(sql="", columns=("claim_id",), rows=())])

    outcome = run(gateway, database)

    assert outcome.status == "ok_empty"
    assert gateway.refine_calls == 1


def test_an_empty_result_from_a_value_the_database_lacks_is_not_refined_at_all() -> None:
    """No rewrite can conjure rows for a value nobody stored, so the run says which value
    it was instead of spending the budget discovering that."""
    gateway = FakeGateway(["SELECT claim_id FROM ops.claims WHERE peril = 'meteor'"])
    database = FakeDatabase([ExecutionResult(sql="", columns=("claim_id",), rows=())])

    outcome = run(gateway, database)

    assert outcome.status == "ok_empty"
    assert "meteor" in outcome.failure
    assert gateway.refine_calls == 0


# ------------------------------------------------------------------ across steps


def test_every_step_of_a_plan_runs_in_order() -> None:
    plan = QueryPlan(answerable=True, steps=(step("First: COUNT(*)."), step("Second: COUNT(*).")))
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run_pipeline(
        "How many?", plan, CONTEXTS, catalog=CATALOG, execute=database,
        settings=settings(), gateway=gateway,
    )

    assert [step.purpose for step in outcome.plan.steps] == ["Count the rows."] * 2
    assert len(outcome.steps) == 2
    assert outcome.ok is True


def test_a_step_that_fails_stops_the_ones_after_it() -> None:
    """A later step reads the earlier one's result; running it on nothing produces a
    number with no basis."""
    plan = QueryPlan(answerable=True, steps=(step(), step()))
    gateway = FakeGateway(["SELECT bogus FROM ops.claims"])
    database = FakeDatabase([failure("no such column")])

    outcome = run_pipeline(
        "How many?", plan, CONTEXTS, catalog=CATALOG, execute=database,
        settings=settings(), gateway=gateway,
    )

    assert len(outcome.steps) == 1
    assert outcome.ok is False


def test_an_unanswerable_plan_executes_nothing() -> None:
    plan = QueryPlan(
        answerable=False, unanswerable_reason="No column records the weather."
    )
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run_pipeline(
        "Was it raining?", plan, CONTEXTS, catalog=CATALOG, execute=database,
        settings=settings(), gateway=gateway,
    )

    assert outcome.steps == ()
    assert outcome.ok is False
    assert database.executed == []


def test_a_completed_step_is_offered_to_the_next_one() -> None:
    plan = QueryPlan(answerable=True, steps=(step(), step()))
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    run_pipeline(
        "How many?", plan, CONTEXTS, catalog=CATALOG, execute=database,
        settings=settings(), gateway=gateway,
    )

    assert gateway.tasks == ["sql_generator", "sql_generator"]


# ------------------------------------------------------------------ candidates


def multi(**overrides: Any) -> Settings:
    return settings(
        sql_multi_candidate_enabled=True,
        sql_candidate_count=3,
        sql_unit_test_count=0,
        **overrides,
    )


def test_several_candidates_are_written_and_the_agreed_one_is_kept() -> None:
    """Three tries at the step, all agreeing, is one answer with corroboration -- and no
    arbitration call, because there is nothing to arbitrate."""
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database, config=multi())

    assert outcome.status == "completed"
    assert gateway.tasks == ["sql_generator"] * 3
    assert outcome.selection.reason == "single_cluster"


def test_the_winning_candidate_is_not_executed_a_second_time() -> None:
    """It already ran. Running it again spends a query to learn what is in hand."""
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    run(gateway, database, config=multi())

    assert len(database.executed) == 3


def test_candidates_that_all_fail_fall_back_to_writing_one_query() -> None:
    gateway = FakeGateway(["DROP TABLE ops.claims"] * 3 + [COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    outcome = run(gateway, database, config=multi())

    assert outcome.status == "completed"
    assert gateway.tasks.count("sql_generator") == 4


def test_one_query_is_written_when_candidates_are_switched_off() -> None:
    gateway = FakeGateway([COUNT_SQL])
    database = FakeDatabase([rows((42,))])

    run(gateway, database, config=settings())

    assert gateway.tasks == ["sql_generator"]
