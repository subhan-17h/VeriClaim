"""Judging an execution result without asking a model.

The observer is what stands between "the query ran" and "the query answered the
question". It is deterministic on purpose: a model asked whether a result looks right
will agree with itself, and the repair loop it drives would then be steered by the same
judgement that produced the error.

Two families of check. The *shape* checks ask whether the result matches what the plan
said it would be -- a scalar aggregate that came back with four hundred rows did not
compute what the plan described. The *value* checks ask whether the numbers contradict a
fact the reviewed contexts declare, which is almost always a query that aggregated
several columns over different sets of rows: a join fanned one of them out, every column
is individually plausible, and only the arithmetic between them gives it away.

Nothing here knows anything about insurance. The facts come from the contexts'
`invariants` blocks, beside the prose cautions that state them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from vericlaim.sql.contexts import (
    ColumnContext,
    NonNegativeInvariant,
    OrderedInvariant,
    SchemaContext,
    SumInvariant,
)
from vericlaim.sql.observer import ExecutionResult, observe
from vericlaim.sql.planner import PlanStep


def column(name: str, type_: str = "numeric") -> ColumnContext:
    return ColumnContext(name=name, type=type_, meaning=f"The {name}.")


CLAIMS = SchemaContext(
    schema="ops",
    table="claims",
    purpose="One row per reported claim.",
    columns=(
        column("claim_id", "bigint"),
        column("region_id", "integer"),
        column("incurred_amount_pkr"),
        column("paid_amount_pkr"),
        column("reserve_amount_pkr"),
        column("date_of_loss", "date"),
        column("report_date", "date"),
    ),
    invariants=(
        SumInvariant(
            total="incurred_amount_pkr",
            parts=("paid_amount_pkr", "reserve_amount_pkr"),
            meaning="Incurred is paid plus reserve.",
        ),
        NonNegativeInvariant(
            column="reserve_amount_pkr",
            meaning="A reserve is money still set aside; it cannot be negative.",
        ),
        OrderedInvariant(
            lower="date_of_loss",
            upper="report_date",
            meaning="A loss cannot be reported before it happened.",
        ),
    ),
)
CONTEXTS = {CLAIMS.qualified: CLAIMS}


def step(calculations: str = "COUNT(*) over all rows.") -> PlanStep:
    return PlanStep(
        purpose="Look at the rows.",
        table="ops.claims",
        tables=("ops.claims",),
        calculations=calculations,
    )


def result(
    sql: str,
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    error: str = "",
) -> ExecutionResult:
    return ExecutionResult(sql=sql, columns=columns, rows=rows, error=error)


def money(amount: str) -> Decimal:
    return Decimal(amount)


# ------------------------------------------------------------------ shape


def test_a_failed_execution_reports_the_database_error() -> None:
    observation = observe(
        result("SELECT 1", (), (), error='column "peril" does not exist'),
        step(),
        CONTEXTS,
    )

    assert observation.verdict == "sql_error"
    assert "peril" in observation.reason


def test_a_query_that_matched_nothing_is_its_own_verdict() -> None:
    """Zero rows is a fact about the data, not a broken query, and the loop above has to
    tell them apart before deciding whether to rewrite anything."""
    observation = observe(
        result("SELECT claim_id FROM ops.claims", ("claim_id",), ()), step(), CONTEXTS
    )

    assert observation.verdict == "empty_result"


def test_rows_without_columns_are_suspicious() -> None:
    observation = observe(result("SELECT 1", (), ((1,),)), step(), CONTEXTS)

    assert observation.verdict == "suspicious_shape"


def test_a_row_wider_than_its_header_is_suspicious() -> None:
    observation = observe(
        result("SELECT claim_id FROM ops.claims", ("claim_id",), ((1, 2),)),
        step(),
        CONTEXTS,
    )

    assert observation.verdict == "suspicious_shape"


def test_an_average_that_is_null_everywhere_did_not_average_anything() -> None:
    observation = observe(
        result(
            "SELECT AVG(incurred_amount_pkr) AS avg_cost FROM ops.claims",
            ("avg_cost",),
            ((None,),),
        ),
        step("Average of incurred_amount_pkr across all rows."),
        CONTEXTS,
    )

    assert observation.verdict == "suspicious_shape"
    assert "average" in observation.reason.lower()


def test_a_scalar_aggregate_that_came_back_grouped_is_suspicious() -> None:
    observation = observe(
        result(
            "SELECT COUNT(*) FROM ops.claims GROUP BY region_id",
            ("count",),
            ((3,), (4,), (5,)),
        ),
        step("COUNT(*) over all rows, with no grouping."),
        CONTEXTS,
    )

    assert observation.verdict == "suspicious_shape"
    assert "3 rows" in observation.reason


def test_a_grouped_aggregate_is_expected_to_have_many_rows() -> None:
    observation = observe(
        result(
            "SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id",
            ("region_id", "count"),
            ((1, 3), (2, 4)),
        ),
        step("COUNT(*) per region_id, grouped by region."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_superlative_may_return_every_tied_row() -> None:
    observation = observe(
        result(
            "SELECT region_id, COUNT(*) FROM ops.claims GROUP BY region_id",
            ("region_id", "count"),
            ((1, 9), (2, 9)),
        ),
        step("The region with the highest count, returning all tied regions."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


# ------------------------------------------------------------------ values


def test_a_negative_reserve_contradicts_the_declared_floor() -> None:
    observation = observe(
        result(
            "SELECT claim_id, reserve_amount_pkr FROM ops.claims",
            ("claim_id", "reserve_amount_pkr"),
            ((1, money("2500")), (2, money("-400"))),
        ),
        step("Every row's reserve."),
        CONTEXTS,
    )

    assert observation.verdict == "implausible_values"
    assert "reserve_amount_pkr" in observation.violations[0]


def test_a_violation_quotes_the_reviewed_reason() -> None:
    """A number that "looks wrong" is not actionable; the declared meaning is what tells
    a reader which of the query and the data to doubt."""
    observation = observe(
        result(
            "SELECT reserve_amount_pkr FROM ops.claims",
            ("reserve_amount_pkr",),
            ((money("-1"),),),
        ),
        step("Every row's reserve."),
        CONTEXTS,
    )

    assert "cannot be negative" in observation.violations[0]


def test_parts_that_do_not_add_up_to_their_total_are_implausible() -> None:
    observation = observe(
        result(
            "SELECT incurred_amount_pkr, paid_amount_pkr, reserve_amount_pkr FROM ops.claims",
            ("incurred_amount_pkr", "paid_amount_pkr", "reserve_amount_pkr"),
            ((money("100000"), money("40000"), money("30000")),),
        ),
        step("The three amounts, per row."),
        CONTEXTS,
    )

    assert observation.verdict == "implausible_values"
    assert "incurred_amount_pkr" in observation.violations[0]


def test_parts_that_do_add_up_pass() -> None:
    observation = observe(
        result(
            "SELECT incurred_amount_pkr, paid_amount_pkr, reserve_amount_pkr FROM ops.claims",
            ("incurred_amount_pkr", "paid_amount_pkr", "reserve_amount_pkr"),
            ((money("70000"), money("40000"), money("30000")),),
        ),
        step("The three amounts, per row."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_rounding_difference_is_not_a_violation() -> None:
    observation = observe(
        result(
            "SELECT incurred_amount_pkr, paid_amount_pkr, reserve_amount_pkr FROM ops.claims",
            ("incurred_amount_pkr", "paid_amount_pkr", "reserve_amount_pkr"),
            ((money("70000.005"), money("40000"), money("30000")),),
        ),
        step("The three amounts, per row."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_sum_that_does_not_reconcile_across_aggregates_is_the_fan_out_bug() -> None:
    """Each total is individually plausible. Only the arithmetic between them shows that
    a join multiplied one of the three."""
    observation = observe(
        result(
            "SELECT SUM(incurred_amount_pkr), SUM(paid_amount_pkr), "
            "SUM(reserve_amount_pkr) FROM ops.claims",
            ("sum", "sum_1", "sum_2"),
            ((money("900000"), money("400000"), money("300000")),),
        ),
        step("Total incurred, paid and reserve across all rows."),
        CONTEXTS,
    )

    assert observation.verdict == "implausible_values"


def test_a_loss_reported_before_it_happened_is_implausible() -> None:
    observation = observe(
        result(
            "SELECT date_of_loss, report_date FROM ops.claims",
            ("date_of_loss", "report_date"),
            ((date(2026, 3, 20), date(2026, 3, 2)),),
        ),
        step("The two dates, per row."),
        CONTEXTS,
    )

    assert observation.verdict == "implausible_values"
    assert "report_date" in observation.violations[0]


def test_an_ordering_is_not_judged_across_separate_extremes() -> None:
    """MIN of one column and MIN of another come from different rows, so their order
    proves nothing about any claim."""
    observation = observe(
        result(
            "SELECT MIN(date_of_loss), MIN(report_date) FROM ops.claims",
            ("min", "min_1"),
            ((date(2026, 3, 20), date(2026, 3, 2)),),
        ),
        step("The earliest of each date."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_null_operand_is_skipped_rather_than_reported() -> None:
    """An open claim has no closing figure; that is absence, not contradiction."""
    observation = observe(
        result(
            "SELECT incurred_amount_pkr, paid_amount_pkr, reserve_amount_pkr FROM ops.claims",
            ("incurred_amount_pkr", "paid_amount_pkr", "reserve_amount_pkr"),
            ((money("70000"), None, money("30000")),),
        ),
        step("The three amounts, per row."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_derived_column_is_not_traced_back_to_an_invariant() -> None:
    """`incurred - paid` is legitimately anything; checking it against the floor for a
    reserve would reject correct queries."""
    observation = observe(
        result(
            "SELECT incurred_amount_pkr - paid_amount_pkr AS reserve_amount_pkr "
            "FROM ops.claims",
            ("reserve_amount_pkr",),
            ((money("-5000"),),),
        ),
        step("The gap between the two amounts."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_a_projection_the_observer_cannot_map_is_left_unjudged() -> None:
    observation = observe(
        result(
            "SELECT * FROM ops.claims",
            ("reserve_amount_pkr",),
            ((money("-5000"),),),
        ),
        step("Everything."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_only_the_tables_the_step_reads_contribute_invariants() -> None:
    observation = observe(
        result(
            "SELECT reserve_amount_pkr FROM ops.claims",
            ("reserve_amount_pkr",),
            ((money("-5000"),),),
        ),
        PlanStep(
            purpose="Look at the rows.",
            table="ops.policies",
            tables=("ops.policies",),
            calculations="Every row.",
        ),
        CONTEXTS,
    )

    assert observation.verdict == "ok"


def test_every_violated_invariant_is_reported_not_just_the_first() -> None:
    observation = observe(
        result(
            "SELECT incurred_amount_pkr, paid_amount_pkr, reserve_amount_pkr FROM ops.claims",
            ("incurred_amount_pkr", "paid_amount_pkr", "reserve_amount_pkr"),
            ((money("100000"), money("40000"), money("-30000")),),
        ),
        step("The three amounts, per row."),
        CONTEXTS,
    )

    assert len(observation.violations) == 2


def test_a_plausible_result_says_so() -> None:
    observation = observe(
        result(
            "SELECT claim_id FROM ops.claims",
            ("claim_id",),
            ((1,), (2,)),
        ),
        step("Every claim's identifier."),
        CONTEXTS,
    )

    assert observation.verdict == "ok"
    assert observation.violations == ()
