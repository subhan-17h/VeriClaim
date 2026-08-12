"""Writing one SELECT for one plan step.

The generator is the only component that produces SQL, and it is the one place where a
model's output becomes something that will run against a database. Nothing here trusts it:
the validator rejects what is unsafe, and this file tests the two things the generator
itself is responsible for -- that the plan's intent survives into the SQL, and that
several genuinely different attempts can be made at once so C-5.9 has candidates to
arbitrate between.

`_preserve_planned_projections` is the load-bearing repair. A model asked to count rows by
group frequently returns the count and drops the grouping column, which produces a number
with nothing to attach it to. Adding the planned column back is cheaper and more reliable
than another round trip.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.sql.contexts import ColumnContext, Join, SchemaContext, load_contexts
from vericlaim.sql.generator import (
    GENERATOR_SYSTEM_PROMPT,
    GeneratorError,
    generate_sql,
    generate_sql_candidates,
)
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.resolver import EntityResolution, Match, Resolution

CONTEXT_DIR = "contexts/sql"


def column(name: str, type_: str = "text") -> ColumnContext:
    return ColumnContext(name=name, type=type_, meaning=f"The {name}.")


CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(
        column("claim_id", "bigint"),
        column("policy_id", "bigint"),
        column("region_id", "integer"),
        column("peril"),
    ),
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
    columns=(column("policy_id", "bigint"),),
)
CONTEXTS = {CLAIMS.qualified: CLAIMS, POLICIES.qualified: POLICIES}


def plan_step(
    table: str = "ops.claims",
    calculations: str = "COUNT(*) over all rows.",
) -> PlanStep:
    return PlanStep(
        purpose="Count the rows.",
        table=table,
        tables=(table,),
        calculations=calculations,
    )


@dataclass
class FakeGateway:
    """Returns canned SQL, one entry per call, and remembers every request."""

    sql: list[str]
    calls: list[dict[str, Any]] = field(default_factory=list)
    before_return: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        with self._lock:
            index = len(self.calls)
            self.calls.append(
                {
                    "task": task,
                    "system": messages[0]["content"],
                    "payload": json.loads(messages[-1]["content"]),
                    "kwargs": kwargs,
                }
            )
        if self.before_return is not None:
            self.before_return()
        payload = {"sql": self.sql[min(index, len(self.sql) - 1)]}
        return _Completion(json.dumps(payload), payload)


@dataclass
class _Completion:
    text: str
    parsed: Any


# ------------------------------------------------------------------ one statement


def test_the_generated_sql_comes_back_as_a_candidate() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    candidate = generate_sql("How many?", plan_step(), CONTEXTS, gateway=gateway)

    assert candidate.sql == "SELECT COUNT(*) FROM ops.claims"
    assert candidate.style == "direct"


def test_generation_is_billed_to_its_own_task() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    generate_sql("How many?", plan_step(), CONTEXTS, gateway=gateway)

    assert gateway.calls[0]["task"] == "sql_generator"


def test_a_step_over_an_undocumented_table_never_reaches_the_model() -> None:
    gateway = FakeGateway(["SELECT 1"])

    with pytest.raises(GeneratorError, match="ops.invoices"):
        generate_sql("How many?", plan_step(table="ops.invoices"), CONTEXTS, gateway=gateway)

    assert gateway.calls == []


def test_a_single_statement_is_generated_deterministically() -> None:
    """One shot at one step is a decision, not a sample; candidates are where diversity
    belongs."""
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    generate_sql("How many?", plan_step(), CONTEXTS, gateway=gateway)

    assert gateway.calls[0]["kwargs"].get("temperature", 0.0) == 0.0


# ------------------------------------------------------------------ the payload


def test_the_generator_is_shown_the_step_and_its_tables() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    generate_sql("How many?", plan_step(), CONTEXTS, gateway=gateway)

    payload = gateway.calls[0]["payload"]
    assert payload["plan_step"]["table"] == "ops.claims"
    assert [context["table"] for context in payload["schema_contexts"]] == [
        "ops.claims",
        "ops.policies",
    ]


def test_completed_steps_are_offered_as_evidence() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])
    completed = [{"purpose": "Count the rows.", "rows": [{"count": 42}]}]

    generate_sql("How many?", plan_step(), CONTEXTS, completed=completed, gateway=gateway)

    assert gateway.calls[0]["payload"]["completed_steps"] == completed


def test_only_resolved_values_are_offered_for_filtering() -> None:
    resolved = EntityResolution(
        mentions=(
            Resolution(
                mention="water damage",
                status="resolved",
                matches=(Match("ops.claims", "peril", ("water_damage",), "equals", 1.0),),
            ),
            Resolution(mention="Zephyr", status="not_found"),
        )
    )
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    generate_sql("How many?", plan_step(), CONTEXTS, resolved=resolved, gateway=gateway)

    entities = gateway.calls[0]["payload"]["resolved_entities"]
    assert [entity["mention"] for entity in entities] == ["water damage"]


# ------------------------------------------------------------------ projections


def test_a_grouping_column_the_plan_asked_for_is_restored() -> None:
    """A count with the grouping column dropped is a number attached to nothing."""
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims GROUP BY region_id"])
    step = plan_step(calculations="SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id")

    candidate = generate_sql("How many per region?", step, CONTEXTS, gateway=gateway)

    assert "region_id" in candidate.sql.split("FROM")[0]


def test_a_column_already_projected_is_not_added_twice() -> None:
    gateway = FakeGateway(
        ["SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id"]
    )
    step = plan_step(calculations="SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id")

    candidate = generate_sql("How many per region?", step, CONTEXTS, gateway=gateway)

    assert candidate.sql.upper().count("REGION_ID") == 2


def test_a_column_the_primary_table_does_not_have_is_never_added() -> None:
    """The plan's prose is a hint, not an authority on what columns exist."""
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])
    step = plan_step(calculations="SELECT weather, COUNT(*) FROM ops.claims GROUP BY weather")

    candidate = generate_sql("How many?", step, CONTEXTS, gateway=gateway)

    assert candidate.sql == "SELECT COUNT(*) FROM ops.claims"


def test_calculations_that_are_prose_leave_the_sql_alone() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])
    step = plan_step(calculations="Count every row, with no grouping at all.")

    candidate = generate_sql("How many?", step, CONTEXTS, gateway=gateway)

    assert candidate.sql == "SELECT COUNT(*) FROM ops.claims"


def test_sql_that_does_not_parse_is_passed_through_for_the_validator_to_reject() -> None:
    gateway = FakeGateway(["SELCT COUNT(*) FRM ops.claims ((("])
    step = plan_step(calculations="SELECT region_id, COUNT(*) FROM ops.claims")

    candidate = generate_sql("How many?", step, CONTEXTS, gateway=gateway)

    assert candidate.sql == "SELCT COUNT(*) FRM ops.claims ((("


def test_the_restored_column_is_qualified_with_the_primary_alias() -> None:
    gateway = FakeGateway(
        ["SELECT COUNT(*) FROM ops.claims AS c JOIN ops.policies AS p ON c.policy_id = p.policy_id"]
    )
    step = plan_step(calculations="SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id")

    candidate = generate_sql("How many per region?", step, CONTEXTS, gateway=gateway)

    assert "c.region_id" in candidate.sql.lower()


# ------------------------------------------------------------------ candidates


def test_candidates_are_written_from_two_different_prompts() -> None:
    """Identical prompts at a sampling temperature give near-identical SQL, and C-5.9 can
    only arbitrate between candidates that genuinely disagree."""
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    candidates = generate_sql_candidates(
        "How many?", plan_step(), CONTEXTS, candidate_count=4, temperature=0.6,
        gateway=gateway,
    )

    assert len(candidates) == 4
    assert {candidate.style for candidate in candidates} == {"direct", "execution_plan"}
    assert len({call["system"] for call in gateway.calls}) == 2


def test_candidates_are_sampled_rather_than_repeated() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    generate_sql_candidates(
        "How many?", plan_step(), CONTEXTS, candidate_count=2, temperature=0.6,
        gateway=gateway,
    )

    assert all(call["kwargs"]["temperature"] == 0.6 for call in gateway.calls)


def test_asking_for_no_candidates_calls_nothing() -> None:
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"])

    assert generate_sql_candidates(
        "How many?", plan_step(), CONTEXTS, candidate_count=0, temperature=0.6,
        gateway=gateway,
    ) == ()
    assert gateway.calls == []


def test_candidates_are_generated_concurrently() -> None:
    """Four sequential calls to a model cost four round trips of latency for work that has
    no order. The barrier deadlocks unless all four are in flight together."""
    barrier = threading.Barrier(4, timeout=5)
    gateway = FakeGateway(["SELECT COUNT(*) FROM ops.claims"], before_return=barrier.wait)

    candidates = generate_sql_candidates(
        "How many?", plan_step(), CONTEXTS, candidate_count=4, temperature=0.6,
        gateway=gateway,
    )

    assert len(candidates) == 4


# ------------------------------------------------------------------ the prompt


def test_the_prompt_names_no_table_or_column_of_the_corpus() -> None:
    """The schema arrives as data. A prompt naming the corpus would have to be edited
    every time a column is, and would silently rot when one was renamed."""
    contexts = load_contexts(CONTEXT_DIR)
    identifiers = {context.table for context in contexts.values()} | {
        name for context in contexts.values() for name in context.column_names
    }

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(rf"\b{re.escape(identifier)}\b", GENERATOR_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_defers_the_domain_rules_to_the_reviewed_cautions() -> None:
    assert "cautions" in GENERATOR_SYSTEM_PROMPT.lower()
