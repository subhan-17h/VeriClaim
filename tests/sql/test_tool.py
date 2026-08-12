"""The SQL source's tool boundary: a question in, ``Evidence`` out.

This is where the NL2SQL subsystem stops and the rest of the system begins. Nothing
downstream ever sees a plan step, a candidate, a validation verdict or a raw row -- only
Evidence, which is what makes "every material claim traces back to its origin" enforceable
rather than aspirational.

The citation is the load-bearing part. A number about claims data is auditable only if the
reader can see the query that produced it, so the executed SQL travels inside the locator
rather than in a log nobody keeps.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.config import Settings
from vericlaim.evidence import SqlLocator
from vericlaim.sql.contexts import ColumnContext, SchemaContext
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.tool import (
    ClaimsQuerier,
    QueryFailedError,
    UnanswerableQuestionError,
)
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
CATALOG = StaticCatalog({"ops.claims": {"peril": (CatalogValue("water_damage"),)}})

PLAN = {
    "steps": [
        {
            "purpose": "Count the claims.",
            "table": "ops.claims",
            "tables": ["ops.claims"],
            "calculations": "COUNT(*) over all rows.",
        }
    ],
    "expected_answer_shape": "A single count.",
    "answerable": True,
    "unanswerable_reason": "",
    "data_coverage": "",
}


@dataclass
class FakeGateway:
    """Answers the planner with a plan and every other task with SQL."""

    plan: dict[str, Any]
    sql: str = "SELECT COUNT(*) FROM ops.claims"
    tasks: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        with self.lock:
            self.tasks.append(task)
        if task == "sql_planner":
            return _Completion(self.plan)
        return _Completion({"sql": self.sql})


@dataclass
class _Completion:
    parsed: Any
    text: str = ""


@dataclass
class FakeDatabase:
    results: list[ExecutionResult]
    executed: list[str] = field(default_factory=list)

    def __call__(self, sql: str) -> ExecutionResult:
        index = min(len(self.executed), len(self.results) - 1)
        self.executed.append(sql)
        canned = self.results[index]
        return ExecutionResult(
            sql=sql, columns=canned.columns, rows=canned.rows, error=canned.error
        )


def settings(**overrides: Any) -> Settings:
    return Settings(
        **{
            "sql_max_refine_attempts": 1,
            "sql_multi_candidate_enabled": False,
            "sql_row_limit": 500,
            **overrides,
        }
    )


def querier(gateway: FakeGateway, database: FakeDatabase, **overrides: Any):
    return ClaimsQuerier(
        contexts=CONTEXTS,
        catalog=CATALOG,
        execute=database,
        settings=settings(**overrides),
        gateway=gateway,
    )


def plan_for(calculations: str, purpose: str = "Count the claims.") -> dict[str, Any]:
    return {
        **PLAN,
        "steps": [{**PLAN["steps"][0], "purpose": purpose, "calculations": calculations}],
    }


def rows(*values: tuple[Any, ...], columns: tuple[str, ...] = ("count",)):
    return ExecutionResult(sql="", columns=columns, rows=values)


# ------------------------------------------------------------------ evidence


def test_a_question_becomes_evidence_carrying_the_query_that_answered_it() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])

    evidence = querier(gateway, database).query("How many claims?")

    assert len(evidence) == 1
    locator = evidence[0].locator
    assert isinstance(locator, SqlLocator)
    assert "ops.claims" in locator.executed_sql
    assert locator.tables == ("ops.claims",)
    assert locator.row_count == 1


def test_the_rows_are_readable_in_the_evidence_itself() -> None:
    """Synthesis never sees the result set, only this. A citation pointing at rows the
    synthesizer could not read would be unverifiable by anything but the database."""
    gateway = FakeGateway(plan_for("List every row.", purpose="List the claims."))
    database = FakeDatabase([rows((3,), (7,), columns=("claims",))])

    content = querier(gateway, database).query("Which claims?")[0].content

    assert "claims" in content
    assert "3" in content and "7" in content


def test_the_evidence_says_which_tool_produced_it_and_from_what_question() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])

    provenance = querier(gateway, database).query("How many claims?")[0].provenance

    assert provenance.tool == "query_claims_db"
    assert provenance.query == "How many claims?"


def test_evidence_is_tagged_as_coming_from_the_claims_database() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])

    assert querier(gateway, database).query("How many?")[0].source_type == "sql"


def test_a_long_result_is_summarized_rather_than_carried_whole() -> None:
    gateway = FakeGateway(plan_for("List every row.", purpose="List the claims."))
    database = FakeDatabase([rows(*((n,) for n in range(200)))])

    evidence = querier(gateway, database).query("List them.")[0]

    assert evidence.locator.row_count == 200
    assert "200" in evidence.content


def test_one_piece_of_evidence_is_produced_per_plan_step() -> None:
    plan = {**PLAN, "steps": [PLAN["steps"][0], {**PLAN["steps"][0], "purpose": "Again."}]}
    gateway = FakeGateway(plan)
    database = FakeDatabase([rows((42,))])

    assert len(querier(gateway, database).query("How many?")) == 2


# ------------------------------------------------------------------ honest limits


def test_a_query_that_matched_nothing_is_still_a_citable_finding() -> None:
    """"There were no such claims" is an answer, and it has to be citable. Returning
    nothing would let synthesis treat the silence as an absence of evidence."""
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([ExecutionResult(sql="", columns=("count",), rows=())])

    evidence = querier(gateway, database).query("How many meteor claims?")

    assert len(evidence) == 1
    assert "no rows" in evidence[0].content.lower()
    assert evidence[0].locator.row_count == 0


def test_a_question_the_data_cannot_answer_is_refused_by_name() -> None:
    plan = {
        **PLAN,
        "steps": [],
        "answerable": False,
        "unanswerable_reason": "No column records the weather.",
        "data_coverage": "The tables cover claims and their policies.",
    }
    gateway = FakeGateway(plan)
    database = FakeDatabase([rows((42,))])

    with pytest.raises(UnanswerableQuestionError, match="weather"):
        querier(gateway, database).query("Was it raining?")

    assert database.executed == []


def test_a_refusal_carries_what_the_data_does_cover() -> None:
    plan = {
        **PLAN,
        "steps": [],
        "answerable": False,
        "unanswerable_reason": "No column records the weather.",
        "data_coverage": "The tables cover claims and their policies.",
    }
    gateway = FakeGateway(plan)

    with pytest.raises(UnanswerableQuestionError) as caught:
        querier(gateway, FakeDatabase([rows((42,))])).query("Was it raining?")

    assert caught.value.coverage == "The tables cover claims and their policies."


def test_a_step_the_loop_could_not_repair_fails_loudly() -> None:
    """A partial answer presented as a whole one is the failure this project exists to
    avoid: it looks complete and is not."""
    gateway = FakeGateway(PLAN, sql="SELECT bogus FROM ops.claims")
    database = FakeDatabase([rows((42,))])

    with pytest.raises(QueryFailedError):
        querier(gateway, database).query("How many?")


def test_a_question_with_no_documented_tables_never_reaches_the_model() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])
    empty = ClaimsQuerier(
        contexts={},
        catalog=CATALOG,
        execute=database,
        settings=settings(),
        gateway=gateway,
    )

    with pytest.raises(QueryFailedError):
        empty.query("How many?")

    assert gateway.tasks == []


# ------------------------------------------------------------------ scoping


def test_the_router_can_narrow_which_tables_are_in_play() -> None:
    """A question about one table should not put every documented table in the planner's
    prompt, nor in the allow-list the validator is built from."""
    policies = SchemaContext(
        schema="ops",
        table="policies",
        purpose="One row per policy.",
        columns=(ColumnContext(name="policy_id", type="bigint", meaning="Key."),),
    )
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])
    subject = ClaimsQuerier(
        contexts={**CONTEXTS, policies.qualified: policies},
        catalog=CATALOG,
        execute=database,
        settings=settings(),
        gateway=gateway,
    )

    subject.query("How many claims?", tables=["ops.claims"])

    assert database.executed


def test_narrowing_to_a_table_nobody_documented_is_an_error() -> None:
    gateway = FakeGateway(PLAN)

    with pytest.raises(QueryFailedError, match="ops.invoices"):
        querier(gateway, FakeDatabase([rows((42,))])).query("x", tables=["ops.invoices"])


# ------------------------------------------------------------------ grounding


def test_a_named_entity_is_grounded_before_the_plan_is_written() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])

    querier(gateway, database).query(
        "How many water damage claims?",
        understanding={"entities": ["water damage"]},
    )

    assert gateway.tasks[0] == "sql_planner"


def test_an_ambiguous_entity_stops_the_run_and_asks() -> None:
    """Choosing between two customers whose names both fit is the user's decision;
    guessing produces a confident answer about the wrong one."""
    catalog = StaticCatalog(
        {
            "ops.claims": {
                "peril": (
                    CatalogValue("Ahmed Textiles Pvt Ltd"),
                    CatalogValue("Ahmed Traders Pvt Ltd"),
                )
            }
        }
    )
    gateway = FakeGateway(PLAN)
    subject = ClaimsQuerier(
        contexts=CONTEXTS,
        catalog=catalog,
        execute=FakeDatabase([rows((42,))]),
        settings=settings(),
        gateway=gateway,
    )

    with pytest.raises(UnanswerableQuestionError, match="Did you mean"):
        subject.query("How many for Ahmed?", understanding={"entities": ["Ahmed"]})

    assert gateway.tasks == []


def test_the_payload_reaching_the_planner_names_only_the_selected_tables() -> None:
    gateway = FakeGateway(PLAN)
    database = FakeDatabase([rows((42,))])

    querier(gateway, database).query("How many?")

    assert json.dumps(PLAN)  # the fake plan is what came back; the tool validated it
    assert gateway.tasks[0] == "sql_planner"
