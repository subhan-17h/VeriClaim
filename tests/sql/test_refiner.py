"""Repairing one failed query without letting it wander.

The refiner is the only component that gets a second chance, which makes it the one most
able to do damage. A model told "that returned nothing" will happily widen the filters
until something comes back, and the result is a confident answer to a question nobody
asked. So the repair is bounded to the plan step it was given, and the prompt's job is to
say what may not change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.sql.contexts import ColumnContext, SchemaContext, load_contexts
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.refiner import (
    FEEDBACK_ROW_LIMIT,
    REFINER_SYSTEM_PROMPT,
    execution_feedback,
    refine_sql,
)

CONTEXT_DIR = "contexts/sql"

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

STEP = PlanStep(
    purpose="Count the rows.",
    table="ops.claims",
    tables=("ops.claims",),
    calculations="COUNT(*) over all rows.",
)


@dataclass
class FakeGateway:
    sql: str = "SELECT count(*) FROM ops.claims"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append(
            {
                "task": task,
                "system": messages[0]["content"],
                "payload": json.loads(messages[-1]["content"]),
            }
        )
        payload = {"sql": self.sql}
        return _Completion(json.dumps(payload), payload)


@dataclass
class _Completion:
    text: str
    parsed: Any


def test_the_repaired_sql_comes_back() -> None:
    gateway = FakeGateway(sql="SELECT count(*) FROM ops.claims WHERE peril = 'fire'")

    repaired = refine_sql(
        "How many fires?",
        STEP,
        "SELECT count(*) FROM ops.claim",
        "sql_error: relation does not exist",
        CONTEXTS,
        gateway=gateway,
    )

    assert repaired == "SELECT count(*) FROM ops.claims WHERE peril = 'fire'"


def test_repair_is_billed_to_its_own_task() -> None:
    gateway = FakeGateway()

    refine_sql("How many?", STEP, "SELECT 1", "boom", CONTEXTS, gateway=gateway)

    assert gateway.calls[0]["task"] == "sql_refiner"


def test_the_refiner_is_told_what_failed_and_why() -> None:
    gateway = FakeGateway()

    refine_sql(
        "How many?",
        STEP,
        "SELECT bogus FROM ops.claims",
        'sql_error: column "bogus" does not exist',
        CONTEXTS,
        gateway=gateway,
    )

    payload = gateway.calls[0]["payload"]
    assert payload["failed_sql"] == "SELECT bogus FROM ops.claims"
    assert "bogus" in payload["failure"]


def test_a_step_over_an_undocumented_table_never_reaches_the_model() -> None:
    gateway = FakeGateway()
    step = PlanStep(purpose="x", table="ops.invoices", tables=("ops.invoices",), calculations="y")

    with pytest.raises(ValueError, match="ops.invoices"):
        refine_sql("How many?", step, "SELECT 1", "boom", CONTEXTS, gateway=gateway)

    assert gateway.calls == []


# ------------------------------------------------------------------ feedback


def test_an_error_is_the_whole_feedback() -> None:
    feedback = execution_feedback(ExecutionResult(sql="SELECT 1", error="boom"))

    assert feedback == {"error": "boom"}


def test_the_rows_that_came_back_are_shown_but_not_all_of_them() -> None:
    """The refiner needs to see the shape of what returned, not carry the result set
    through the context window at every attempt."""
    rows = tuple((index,) for index in range(FEEDBACK_ROW_LIMIT + 10))
    result = ExecutionResult(sql="SELECT n FROM t", columns=("n",), rows=rows)

    feedback = execution_feedback(result)

    assert feedback["row_count"] == FEEDBACK_ROW_LIMIT + 10
    assert len(feedback["rows"]) == FEEDBACK_ROW_LIMIT


def test_a_long_cell_is_truncated_rather_than_sent_whole() -> None:
    result = ExecutionResult(
        sql="SELECT note FROM t", columns=("note",), rows=(("x" * 5000,),)
    )

    assert len(feedback_cell(execution_feedback(result))) < 5000


def feedback_cell(feedback: dict[str, Any]) -> str:
    return str(feedback["rows"][0][0])


def test_nothing_executed_means_nothing_to_feed_back() -> None:
    assert execution_feedback(None) is None


# ------------------------------------------------------------------ the prompt


def test_the_prompt_names_no_table_or_column_of_the_corpus() -> None:
    contexts = load_contexts(CONTEXT_DIR)
    identifiers = {context.table for context in contexts.values()} | {
        name for context in contexts.values() for name in context.column_names
    }

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(rf"\b{re.escape(identifier)}\b", REFINER_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_forbids_widening_the_question_to_find_rows() -> None:
    """The failure mode a repair prompt has to guard against: told a query returned
    nothing, a model relaxes the filters until something does."""
    assert "empty" in REFINER_SYSTEM_PROMPT.lower()
    assert "unchanged" in REFINER_SYSTEM_PROMPT.lower()
