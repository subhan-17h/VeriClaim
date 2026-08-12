"""Turning a question into a plan the generator can write SQL from.

The planner never writes SQL. It decides two things a model is allowed to decide -- what
to compute and from which documented tables -- and one thing it must be forced to admit:
that the question cannot be answered from the data at all. Everything the planner returns
is then checked here, deterministically, before a single character of SQL exists.

The checks matter more than the plan. A model asked for a plan will always produce one;
these tests are what stop it producing one over a table nobody documented, or one that
joins two tables no declared foreign key connects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pytest

from vericlaim.sql.contexts import (
    ColumnContext,
    Join,
    SchemaContext,
    load_contexts,
)
from vericlaim.sql.planner import (
    PLANNER_SYSTEM_PROMPT,
    PlanError,
    plan_query,
)
from vericlaim.sql.resolver import EntityResolution, Match, Resolution

CONTEXT_DIR = "contexts/sql"


def column(name: str, type_: str = "text") -> ColumnContext:
    return ColumnContext(name=name, type=type_, meaning=f"The {name}.")


CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(column("claim_id", "bigint"), column("policy_id", "bigint"), column("peril")),
    joins=(
        Join(
            column="policy_id",
            references="ops.policies.policy_id",
            meaning="The policy claimed against.",
        ),
    ),
)
POLICIES = SchemaContext(
    schema="ops",
    table="policies",
    purpose="One row per policy.",
    columns=(column("policy_id", "bigint"), column("customer_id", "bigint")),
    joins=(
        Join(
            column="customer_id",
            references="ops.customers.customer_id",
            meaning="The policyholder.",
        ),
    ),
)
CUSTOMERS = SchemaContext(
    schema="ops",
    table="customers",
    purpose="One row per customer.",
    columns=(column("customer_id", "bigint"), column("customer_name")),
)
ADJUSTERS = SchemaContext(
    schema="ops",
    table="adjusters",
    purpose="One row per adjuster.",
    columns=(column("adjuster_id", "integer"), column("adjuster_name")),
)

CONTEXTS = {
    context.qualified: context
    for context in (CLAIMS, POLICIES, CUSTOMERS, ADJUSTERS)
}


@dataclass
class FakeGateway:
    """A gateway that returns a canned plan and remembers what it was asked."""

    payload: dict[str, Any]
    calls: list[tuple[str, Any, dict[str, Any]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((task, messages, schema))
        return _Completion(json.dumps(self.payload), self.payload)


@dataclass
class _Completion:
    text: str
    parsed: Any


def step(
    table: str = "ops.claims",
    tables: list[str] | None = None,
    purpose: str = "Count the rows.",
    calculations: str = "COUNT(*) over all rows.",
) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "table": table,
        "tables": tables,
        "calculations": calculations,
    }


def plan_payload(
    steps: list[dict[str, Any]] | None = None, **overrides: Any
) -> dict[str, Any]:
    payload = {
        "steps": [step()] if steps is None else steps,
        "expected_answer_shape": "A single count.",
        "answerable": True,
        "unanswerable_reason": "",
        "data_coverage": "",
    }
    payload.update(overrides)
    return payload


def sent_payload(gateway: FakeGateway) -> dict[str, Any]:
    return json.loads(gateway.calls[0][1][-1]["content"])


# ------------------------------------------------------------------ the happy path


def test_a_plan_comes_back_as_typed_steps() -> None:
    gateway = FakeGateway(plan_payload())

    plan = plan_query("How many?", {}, CONTEXTS, gateway=gateway)

    assert plan.answerable is True
    assert len(plan.steps) == 1
    assert plan.steps[0].table == "ops.claims"
    assert plan.steps[0].calculations == "COUNT(*) over all rows."


def test_a_step_that_names_no_tables_reads_only_its_primary_one() -> None:
    """`tables` is optional; omitting it must not mean "every table is fair game"."""
    gateway = FakeGateway(plan_payload([step(tables=None)]))

    plan = plan_query("How many?", {}, CONTEXTS, gateway=gateway)

    assert plan.steps[0].tables == ("ops.claims",)


def test_the_planner_is_billed_to_its_own_task() -> None:
    gateway = FakeGateway(plan_payload())

    plan_query("How many?", {}, CONTEXTS, gateway=gateway)

    assert gateway.calls[0][0] == "sql_planner"


# ------------------------------------------------------------------ answerability


def test_an_unanswerable_question_needs_no_steps() -> None:
    """The gate is the point: a question about an attribute nobody stores must be
    refused, not answered from the nearest column that happens to exist."""
    gateway = FakeGateway(
        plan_payload(
            steps=[],
            answerable=False,
            unanswerable_reason="No column records the weather.",
            data_coverage="The tables cover claims and the policies they belong to.",
        )
    )

    plan = plan_query("Was it raining?", {}, CONTEXTS, gateway=gateway)

    assert plan.answerable is False
    assert plan.unanswerable_reason == "No column records the weather."
    assert plan.steps == ()


def test_an_answerable_plan_with_no_steps_is_a_failure() -> None:
    gateway = FakeGateway(plan_payload(steps=[]))

    with pytest.raises(PlanError, match="no steps"):
        plan_query("How many?", {}, CONTEXTS, gateway=gateway)


def test_an_unanswerable_plan_must_say_why() -> None:
    gateway = FakeGateway(plan_payload(steps=[], answerable=False))

    with pytest.raises(PlanError, match="reason"):
        plan_query("Was it raining?", {}, CONTEXTS, gateway=gateway)


# ------------------------------------------------------------------ post-validation


def test_a_step_over_an_undocumented_table_is_rejected() -> None:
    gateway = FakeGateway(plan_payload([step(table="ops.invoices")]))

    with pytest.raises(PlanError, match="ops.invoices"):
        plan_query("How many?", {}, CONTEXTS, gateway=gateway)


def test_a_step_that_reads_an_undocumented_table_is_rejected() -> None:
    gateway = FakeGateway(
        plan_payload([step(tables=["ops.claims", "ops.invoices"])])
    )

    with pytest.raises(PlanError, match="ops.invoices"):
        plan_query("How many?", {}, CONTEXTS, gateway=gateway)


def test_a_step_must_read_its_own_primary_table() -> None:
    gateway = FakeGateway(plan_payload([step(tables=["ops.policies"])]))

    with pytest.raises(PlanError, match="primary"):
        plan_query("How many?", {}, CONTEXTS, gateway=gateway)


def test_a_step_joining_two_unrelated_tables_is_rejected() -> None:
    """No declared foreign key connects claims to adjusters here, so joining them in one
    step is a cross product wearing a join's clothes."""
    gateway = FakeGateway(
        plan_payload([step(tables=["ops.claims", "ops.adjusters"])])
    )

    with pytest.raises(PlanError, match="ops.adjusters"):
        plan_query("How many?", {}, CONTEXTS, gateway=gateway)


def test_a_step_joining_through_an_intermediate_table_is_accepted() -> None:
    """claims -> policies -> customers is a real path, declared one hop at a time."""
    gateway = FakeGateway(
        plan_payload(
            [step(tables=["ops.claims", "ops.policies", "ops.customers"])]
        )
    )

    plan = plan_query("Which customers?", {}, CONTEXTS, gateway=gateway)

    assert plan.steps[0].tables == ("ops.claims", "ops.customers", "ops.policies")


def test_planning_with_no_documented_tables_never_calls_the_model() -> None:
    gateway = FakeGateway(plan_payload())

    with pytest.raises(PlanError):
        plan_query("How many?", {}, {}, gateway=gateway)

    assert gateway.calls == []


# ------------------------------------------------------------------ the payload


def test_the_planner_is_shown_the_full_context_of_each_table() -> None:
    gateway = FakeGateway(plan_payload())

    plan_query("How many?", {}, CONTEXTS, gateway=gateway)

    tables = [context["table"] for context in sent_payload(gateway)["schema_contexts"]]
    assert tables == ["ops.adjusters", "ops.claims", "ops.customers", "ops.policies"]


def test_resolved_values_reach_the_planner_as_stored_spellings() -> None:
    resolved = EntityResolution(
        mentions=(
            Resolution(
                mention="water damage",
                status="resolved",
                matches=(
                    Match("ops.claims", "peril", ("water_damage",), "equals", 1.0),
                ),
            ),
        )
    )
    gateway = FakeGateway(plan_payload())

    plan_query("How many?", {}, CONTEXTS, resolved=resolved, gateway=gateway)

    entities = sent_payload(gateway)["resolved_entities"]
    assert entities == [
        {
            "mention": "water damage",
            "table": "ops.claims",
            "column": "peril",
            "values": ["water_damage"],
            "match_kind": "equals",
        }
    ]


def test_an_unresolved_mention_is_not_offered_as_a_value() -> None:
    """Passing a not-found mention through would invite a filter on a value the database
    does not hold."""
    resolved = EntityResolution(
        mentions=(Resolution(mention="Zephyr", status="not_found"),)
    )
    gateway = FakeGateway(plan_payload())

    plan_query("How many?", {}, CONTEXTS, resolved=resolved, gateway=gateway)

    assert sent_payload(gateway)["resolved_entities"] == []


def test_a_retry_hint_is_carried_into_the_next_attempt() -> None:
    gateway = FakeGateway(plan_payload())

    plan_query("How many?", {}, CONTEXTS, retry_hint="Use the loss date.", gateway=gateway)

    assert sent_payload(gateway)["retry_hint"] == "Use the loss date."


# ------------------------------------------------------------------ the prompt


def test_the_prompt_names_no_table_or_column_of_the_corpus() -> None:
    """The schema reaches the model as data, never baked into the prompt.

    A prompt that named the corpus would answer the corpus and nothing else, and would
    have to be edited every time a column is. This is the test that keeps the SQL
    subsystem's instructions domain-free while its knowledge stays in the reviewed
    contexts.
    """
    contexts = load_contexts(CONTEXT_DIR)
    identifiers = {context.table for context in contexts.values()} | {
        name for context in contexts.values() for name in context.column_names
    }

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(rf"\b{re.escape(identifier)}\b", PLANNER_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_defers_the_domain_rules_to_the_reviewed_cautions() -> None:
    """The cautions live in one place, the context files. Restating them here would let
    the two drift, and the prompt is the copy nobody reviews."""
    assert "cautions" in PLANNER_SYSTEM_PROMPT.lower()
