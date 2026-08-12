"""Choosing between several queries that all ran.

Generating one query and repairing it until it stops erroring finds queries that *work*.
It does not find queries that are *right*: a query that counts the wrong rows returns a
number, cleanly, every time. Writing the step several ways and comparing what came back is
the only cheap signal we have about which one understood the question.

Candidates that returned the same rows are one answer written twice, so they are grouped
before anything is compared. If every candidate agrees there is nothing to arbitrate and
no model is asked. Only genuine disagreement is worth a call -- and when that call cannot
be made, the deterministic choice stands and says so, rather than the run failing or
pretending it arbitrated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from vericlaim.sql.candidates import Candidate, select
from vericlaim.sql.contexts import ColumnContext, SchemaContext
from vericlaim.sql.observer import ExecutionResult, Observation
from vericlaim.sql.planner import PlanStep

CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(
        ColumnContext(name="claim_id", type="bigint", meaning="Surrogate key."),
        ColumnContext(name="peril", type="text", meaning="Cause of loss."),
    ),
    cautions=("Incurred is not paid.",),
)
CONTEXTS = {CLAIMS.qualified: CLAIMS}

STEP = PlanStep(
    purpose="Count the rows.",
    table="ops.claims",
    tables=("ops.claims",),
    calculations="COUNT(*) over all rows.",
)


def candidate(
    sql: str,
    rows: tuple[tuple[Any, ...], ...] = ((42,),),
    verdict: str = "ok",
    style: str = "direct",
) -> Candidate:
    return Candidate(
        sql=sql,
        style=style,
        result=ExecutionResult(sql=sql, columns=("count",), rows=rows),
        observation=Observation(verdict, "reason"),
    )


@dataclass
class FakeGateway:
    """Answers the two arbitration prompts: tests first, then verdicts."""

    tests: list[str]
    verdicts: list[list[str]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: Exception | None = None

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        if self.fail is not None:
            raise self.fail
        payload = json.loads(messages[-1]["content"])
        self.calls.append(
            {"task": task, "payload": payload, "system": messages[0]["content"]}
        )
        if "test" in payload:
            graded = sum(1 for call in self.calls if "test" in call["payload"])
            index = min(len(self.verdicts) - 1, graded - 1)
            return _Completion({"verdicts": self.verdicts[index]})
        return _Completion({"tests": self.tests})


@dataclass
class _Completion:
    parsed: Any
    text: str = ""


def choose(candidates, gateway=None, unit_test_count: int = 2):
    return select(
        "How many?",
        STEP,
        candidates,
        CONTEXTS,
        unit_test_count=unit_test_count,
        gateway=gateway,
    )


# ------------------------------------------------------------------ grouping


def test_candidates_that_agree_need_no_arbitration() -> None:
    gateway = FakeGateway(tests=["The correct SQL should count rows."])

    selection = choose([candidate("SELECT COUNT(*) FROM a"), candidate("SELECT count(*) FROM a")])

    assert selection.reason == "single_cluster"
    assert selection.clusters == 1
    assert gateway.calls == []


def test_row_order_is_formatting_not_disagreement() -> None:
    left = candidate("SELECT peril FROM a", (("fire",), ("storm",)))
    right = candidate("SELECT peril FROM a ORDER BY peril DESC", (("storm",), ("fire",)))

    assert choose([left, right]).clusters == 1


def test_the_same_amount_written_two_ways_is_one_answer() -> None:
    """Postgres returns 70000 and 70000.00 for the same money depending on the
    expression; treating them as disagreement would arbitrate over nothing."""
    left = candidate("SELECT a FROM t", ((Decimal("70000"),),))
    right = candidate("SELECT a FROM t", ((Decimal("70000.00"),),))

    assert choose([left, right]).clusters == 1


def test_values_that_do_not_serialize_are_still_comparable() -> None:
    left = candidate("SELECT d FROM t", ((date(2026, 3, 1),),))
    right = candidate("SELECT d FROM t", ((date(2026, 3, 1),),))

    assert choose([left, right]).clusters == 1


def test_the_shortest_query_of_an_agreeing_group_is_the_one_kept() -> None:
    selection = choose(
        [
            candidate("SELECT COUNT(*) FROM a WHERE 1 = 1 AND 2 = 2"),
            candidate("SELECT COUNT(*) FROM a"),
        ]
    )

    assert selection.sql == "SELECT COUNT(*) FROM a"


# ------------------------------------------------------------------ viability


def test_a_candidate_the_database_rejected_is_not_a_contender() -> None:
    broken = Candidate(
        sql="SELECT bogus FROM a",
        style="direct",
        result=ExecutionResult(sql="SELECT bogus FROM a", error="boom"),
        observation=Observation("sql_error", "boom"),
    )

    selection = choose([broken, candidate("SELECT COUNT(*) FROM a")])

    assert selection.sql == "SELECT COUNT(*) FROM a"
    assert selection.candidates == 1


def test_a_candidate_whose_numbers_contradict_the_data_is_not_a_contender() -> None:
    """The observer already judged it. Letting it into a cluster vote would give a wrong
    query a say in which query is right."""
    implausible = candidate("SELECT a, b, c FROM t", verdict="implausible_values")

    assert choose([implausible, candidate("SELECT COUNT(*) FROM a")]).candidates == 1


def test_when_nothing_survives_there_is_nothing_to_choose() -> None:
    broken = Candidate(
        sql="SELECT bogus FROM a",
        style="direct",
        result=ExecutionResult(sql="x", error="boom"),
        observation=Observation("sql_error", "boom"),
    )

    selection = choose([broken])

    assert selection.reason == "no_candidates"
    assert selection.sql == ""


# ------------------------------------------------------------------ arbitration


def test_disagreeing_candidates_are_arbitrated_by_written_assertions() -> None:
    gateway = FakeGateway(
        tests=["The correct SQL should count every row."],
        verdicts=[["fail", "pass"]],
    )

    selection = choose(
        [candidate("SELECT COUNT(DISTINCT peril) FROM a", ((3,),)),
         candidate("SELECT COUNT(*) FROM a", ((42,),))],
        gateway=gateway,
    )

    assert selection.reason == "unit_test_winner"
    assert selection.sql == "SELECT COUNT(*) FROM a"
    assert selection.scores == (0, 1)


def test_the_arbiter_is_told_the_reviewed_cautions_of_the_tables_in_play() -> None:
    """The conventions it enforces are the ones a reviewer wrote down, not a list baked
    into this file that would describe some other schema."""
    gateway = FakeGateway(
        tests=["The correct SQL should count every row."], verdicts=[["pass", "fail"]]
    )

    choose(
        [
            candidate("SELECT COUNT(*) FROM a", ((1,),)),
            candidate("SELECT COUNT(*) FROM b", ((2,),)),
        ],
        gateway=gateway,
    )

    assert "Incurred is not paid." in gateway.calls[0]["payload"]["conventions"]


def test_arbitration_is_skipped_when_it_is_switched_off() -> None:
    gateway = FakeGateway(tests=["x"], verdicts=[["pass", "fail"]])

    selection = choose(
        [
            candidate("SELECT COUNT(*) FROM a", ((1,),)),
            candidate("SELECT COUNT(*) FROM b", ((2,),)),
        ],
        gateway=gateway,
        unit_test_count=0,
    )

    assert selection.reason == "largest_cluster"
    assert gateway.calls == []


def test_an_arbiter_that_cannot_be_reached_leaves_the_deterministic_choice_standing() -> None:
    """The reference swallowed this failure without a word. Degrading is right; degrading
    silently means nobody ever learns the arbitration stopped working."""
    gateway = FakeGateway(tests=[], fail=RuntimeError("quota exhausted"))

    selection = choose(
        [
            candidate("SELECT COUNT(*) FROM a", ((1,),)),
            candidate("SELECT COUNT(*) FROM b", ((2,),)),
        ],
        gateway=gateway,
    )

    assert selection.reason == "arbitration_unavailable"
    assert "quota exhausted" in selection.detail
    assert selection.sql


def test_an_arbiter_that_grades_the_wrong_number_of_candidates_is_not_believed() -> None:
    gateway = FakeGateway(tests=["The correct SQL should count every row."], verdicts=[["pass"]])

    selection = choose(
        [
            candidate("SELECT COUNT(*) FROM a", ((1,),)),
            candidate("SELECT COUNT(*) FROM b", ((2,),)),
        ],
        gateway=gateway,
    )

    assert selection.reason == "arbitration_unavailable"


def test_a_tie_on_assertions_falls_back_to_the_larger_group() -> None:
    """Two queries agreeing is weak evidence, but it is evidence, and it is the only
    tiebreak that does not amount to picking at random."""
    gateway = FakeGateway(
        tests=["The correct SQL should count every row."], verdicts=[["pass", "pass"]]
    )
    agreeing = [candidate("SELECT COUNT(*) FROM a", ((1,),)),
                candidate("SELECT count(*) FROM a", ((1,),))]

    selection = choose([*agreeing, candidate("SELECT COUNT(*) FROM b", ((2,),))], gateway=gateway)

    assert selection.sql in {"SELECT COUNT(*) FROM a", "SELECT count(*) FROM a"}


def test_no_more_tests_are_written_than_were_asked_for() -> None:
    gateway = FakeGateway(
        tests=["one", "two", "three", "four"], verdicts=[["pass", "fail"]] * 4
    )

    selection = choose(
        [
            candidate("SELECT COUNT(*) FROM a", ((1,),)),
            candidate("SELECT COUNT(*) FROM b", ((2,),)),
        ],
        gateway=gateway,
        unit_test_count=2,
    )

    assert len(selection.tests) == 2
