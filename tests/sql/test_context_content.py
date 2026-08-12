"""The committed schema contexts for the claims database.

These files are the contract the corpus generator (C-8.1) has to satisfy, and the only
thing the planner will know about the database. The tests are therefore about meaning,
not shape: that the two dates are distinguished, that incurred is not confused with paid,
that a deductible is not a limit, and that money declares its currency.

Getting any of those wrong produces a confident, plausible, wrong number -- which is the
failure mode this whole project exists to avoid.
"""

from __future__ import annotations

import pytest

from vericlaim.config import get_settings
from vericlaim.sql.contexts import allow_list, load_contexts
from vericlaim.sql.validator import validate_sql

# The tables the corpus generator builds. Named here so a context deleted by accident
# fails a test rather than quietly shrinking what the agent can answer.
EXPECTED_TABLES = {
    "ops.regions",
    "ops.customers",
    "ops.coverage_products",
    "ops.policies",
    "ops.adjusters",
    "ops.claims",
    "ops.claim_payments",
}


@pytest.fixture(scope="module")
def contexts():
    return load_contexts(get_settings().sql_context_dir)


@pytest.fixture(scope="module")
def claims(contexts):
    return contexts["ops.claims"]


def text_of(context) -> str:
    """Everything a planner would read, lower-cased, for meaning assertions."""
    parts = [context.purpose, *context.useful_for, *context.cautions]
    parts += [column.meaning for column in context.columns]
    parts += [join.meaning for join in context.joins]
    return " ".join(parts).lower()


# ------------------------------------------------------------------ coverage


def test_every_corpus_table_is_documented(contexts) -> None:
    assert set(contexts) == EXPECTED_TABLES


def test_every_context_says_what_it_answers(contexts) -> None:
    for qualified, context in contexts.items():
        assert context.useful_for, qualified


# ------------------------------------------------------- the two dates trap


def test_claims_carries_both_dates(claims) -> None:
    assert {"date_of_loss", "report_date"} <= set(claims.column_names)


def test_the_two_dates_are_explicitly_distinguished(claims) -> None:
    """"Claims filed in March" and "losses occurring in March" are different questions
    over the same table, and the planted March spike makes the difference material."""
    cautions = " ".join(claims.cautions).lower()

    assert "date_of_loss" in cautions
    assert "report_date" in cautions


# ------------------------------------------------- the money-meaning traps


def test_claims_distinguishes_incurred_from_paid(claims) -> None:
    """Incurred is the estimate of ultimate cost; paid is cash already out."""
    assert {"incurred_amount_pkr", "paid_amount_pkr"} <= set(claims.column_names)

    cautions = " ".join(claims.cautions).lower()
    assert "incurred" in cautions and "paid" in cautions


def test_a_deductible_is_not_a_limit(contexts) -> None:
    """One is what the insured bears per claim, the other is the ceiling on the payout.
    Reading either as the other misstates the answer by orders of magnitude."""
    policies = contexts["ops.policies"]
    cautions = " ".join(policies.cautions).lower()

    assert "deductible" in cautions
    assert "limit" in cautions or "sum insured" in cautions


def test_every_money_column_declares_pkr(contexts) -> None:
    """The corpus is Pakistani rupees throughout; a unit inferred at formatting time is a
    wrong answer waiting to be printed."""
    for qualified, context in contexts.items():
        for column in context.columns:
            if column.name.endswith("_pkr"):
                assert column.unit == "PKR", f"{qualified}.{column.name}"


def test_no_column_declares_money_without_saying_so_in_its_name(contexts) -> None:
    """The converse, so the naming convention stays a reliable signal."""
    for qualified, context in contexts.items():
        for column in context.columns:
            if column.unit == "PKR":
                assert column.name.endswith("_pkr"), f"{qualified}.{column.name}"


# --------------------------------------------------------- decision support


def test_claim_status_is_not_presented_as_a_coverage_determination(claims) -> None:
    """A denied or closed claim is a workflow state in this data. The agent must never
    report it as the insurer's coverage decision."""
    assert "status" in claims.column_names
    assert any("determination" in caution.lower() for caution in claims.cautions)


# ------------------------------------------------------------ cross-source


def test_claims_documents_the_reference_scanned_paperwork_cites(claims) -> None:
    """`claim_number` is the join between this source and the scanned documents; without
    it, an OCR'd inspection report cannot be tied to the claim it concerns."""
    assert "claim_number" in claims.column_names

    assert "scan" in text_of(claims)


# ------------------------------------------------------------------- joins


@pytest.mark.parametrize(
    "table, target",
    [
        ("ops.claims", "ops.policies"),
        ("ops.claims", "ops.adjusters"),
        ("ops.claim_payments", "ops.claims"),
        ("ops.policies", "ops.customers"),
        ("ops.policies", "ops.coverage_products"),
        ("ops.customers", "ops.regions"),
    ],
)
def test_the_join_graph_connects_the_corpus(contexts, table, target) -> None:
    """A question about regional water-damage exposure spans three tables; undocumented
    join keys make that question unanswerable no matter how good the generator is."""
    targets = {join.target_table for join in contexts[table].joins}

    assert target in targets


# ------------------------------------------- contexts and validator agree


def test_a_realistic_cross_table_query_validates(contexts) -> None:
    """The end-to-end point of this task: documented column names are the real ones, so
    SQL written from the contexts survives the validator."""
    allowed = allow_list(contexts, ["ops.claims", "ops.policies", "ops.regions"])
    sql = """
        SELECT r.region_name, COUNT(*) AS claim_count, SUM(c.incurred_amount_pkr) AS incurred
        FROM ops.claims AS c
        JOIN ops.policies AS p ON p.policy_id = c.policy_id
        JOIN ops.regions AS r ON r.region_id = c.region_id
        WHERE c.peril = 'water_damage' AND c.report_date >= '2026-03-01'
        GROUP BY r.region_name
        ORDER BY claim_count DESC
    """

    result = validate_sql(sql, allowed, 50)

    assert result.ok, result.reason
    assert result.tables == ("ops.claims", "ops.policies", "ops.regions")


def test_a_column_nobody_documented_is_rejected(contexts) -> None:
    allowed = allow_list(contexts, ["ops.claims"])

    result = validate_sql("SELECT settlement_verdict FROM ops.claims", allowed, 50)

    assert not result.ok
    assert "Column validation failed" in result.reason


def test_an_undocumented_table_cannot_be_queried(contexts) -> None:
    """Documentation is the gate: a table nobody described is unreadable by design."""
    allowed = allow_list(contexts, list(contexts))

    result = validate_sql("SELECT * FROM ops.reserves_internal", allowed, 50)

    assert not result.ok
    assert "Table is not allowed" in result.reason


# ------------------------------------------------------------------ invariants


def test_the_money_columns_of_a_claim_reconcile_by_declaration(contexts) -> None:
    """The prose caution says incurred is paid plus reserve. This is the same fact in the
    form the observer can hold a result against."""
    sums = [
        invariant
        for invariant in contexts["ops.claims"].invariants
        if invariant.kind == "sum"
    ]

    assert [(sums[0].total, sums[0].parts)] == [
        ("incurred_amount_pkr", ("paid_amount_pkr", "reserve_amount_pkr"))
    ]


def test_a_reserve_is_declared_to_have_a_floor(contexts) -> None:
    floors = {
        invariant.column
        for invariant in contexts["ops.claims"].invariants
        if invariant.kind == "non_negative"
    }

    assert "reserve_amount_pkr" in floors


def test_the_two_dates_are_declared_in_order(contexts) -> None:
    """A loss reported before it happened means the query swapped the two columns the
    contexts spend most of their words distinguishing."""
    orderings = {
        (invariant.lower, invariant.upper)
        for invariant in contexts["ops.claims"].invariants
        if invariant.kind == "ordered"
    }

    assert ("date_of_loss", "report_date") in orderings


def test_a_negative_payment_is_never_declared_impossible(contexts) -> None:
    """Recoveries are stored as negative amounts, so a floor here would reject the
    corpus. The declarations have to describe the data, not tidy it."""
    floors = {
        invariant.column
        for invariant in contexts["ops.claim_payments"].invariants
        if invariant.kind == "non_negative"
    }

    assert "amount_pkr" not in floors
