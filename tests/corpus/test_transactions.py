"""Offline contract tests for the deterministic operational corpus."""

from __future__ import annotations

import pickle
import re
from collections import Counter, defaultdict
from dataclasses import fields, replace
from decimal import Decimal

import pytest

from vericlaim.config import get_settings
from vericlaim.corpus.catalog import (
    ADJUSTERS,
    CLAIM_STATUSES,
    COVERAGE_PRODUCTS,
    CUSTOMER_TYPES,
    PAYMENT_TYPES,
    PERILS,
    POLICY_STATUSES,
    REGIONS,
)
from vericlaim.corpus.transactions import (
    CLAIM_RATE_TABLE,
    PLANTED_RATE_ROWS,
    ClaimPaymentRow,
    ClaimRow,
    CustomerRow,
    PolicyRow,
    TransactionRows,
    generate_transactions,
)

CENT = Decimal("0.01")
OPENISH_STATUSES = {"open", "reopened"}


@pytest.fixture(scope="module")
def corpus() -> TransactionRows:
    return generate_transactions(42)


def test_same_seed_is_byte_identical_and_different_seed_differs() -> None:
    first = generate_transactions(73)
    second = generate_transactions(73)
    different = generate_transactions(74)

    assert first == second
    assert pickle.dumps(first) == pickle.dumps(second)
    assert first != different


def test_row_counts_match_the_planned_volumes(corpus: TransactionRows) -> None:
    assert len(corpus.customers) == 4_000
    assert len(corpus.policies) == 6_000
    assert len(corpus.claims) == 12_000
    assert len(corpus.claim_payments) == 9_000


def test_record_fields_are_the_ddl_column_names() -> None:
    expected = {
        CustomerRow: {
            "customer_id",
            "customer_name",
            "customer_type",
            "city",
            "region_id",
            "email",
            "created_at",
        },
        PolicyRow: {
            "policy_id",
            "policy_number",
            "customer_id",
            "product_id",
            "region_id",
            "inception_date",
            "expiry_date",
            "status",
            "sum_insured_pkr",
            "deductible_pkr",
            "annual_premium_pkr",
        },
        ClaimRow: {
            "claim_id",
            "claim_number",
            "policy_id",
            "adjuster_id",
            "region_id",
            "peril",
            "date_of_loss",
            "report_date",
            "closed_date",
            "status",
            "cause_description",
            "incurred_amount_pkr",
            "paid_amount_pkr",
            "reserve_amount_pkr",
            "deductible_applied_pkr",
        },
        ClaimPaymentRow: {
            "payment_id",
            "claim_id",
            "payment_date",
            "payment_type",
            "amount_pkr",
        },
    }

    for record_type, column_names in expected.items():
        assert {field.name for field in fields(record_type)} == column_names


def test_all_ddl_checks_and_vocabularies_hold(corpus: TransactionRows) -> None:
    for customer in corpus.customers:
        assert customer.customer_type in CUSTOMER_TYPES

    for policy in corpus.policies:
        assert policy.status in POLICY_STATUSES
        assert policy.inception_date <= policy.expiry_date
        assert policy.sum_insured_pkr >= 0
        assert policy.deductible_pkr >= 0
        assert policy.annual_premium_pkr >= 0

    for claim in corpus.claims:
        assert claim.peril in PERILS
        assert claim.status in CLAIM_STATUSES
        assert claim.incurred_amount_pkr == (
            claim.paid_amount_pkr + claim.reserve_amount_pkr
        )
        assert claim.paid_amount_pkr <= claim.incurred_amount_pkr
        assert claim.incurred_amount_pkr >= 0
        assert claim.paid_amount_pkr >= 0
        assert claim.reserve_amount_pkr >= 0
        assert claim.deductible_applied_pkr >= 0
        assert claim.date_of_loss <= claim.report_date
        assert claim.closed_date is None or claim.report_date <= claim.closed_date

    for payment in corpus.claim_payments:
        assert payment.payment_type in PAYMENT_TYPES
        assert payment.amount_pkr >= 0


def test_every_money_value_is_a_two_place_decimal(corpus: TransactionRows) -> None:
    money_values = (
        [
            amount
            for policy in corpus.policies
            for amount in (
                policy.sum_insured_pkr,
                policy.deductible_pkr,
                policy.annual_premium_pkr,
            )
        ]
        + [
            amount
            for claim in corpus.claims
            for amount in (
                claim.incurred_amount_pkr,
                claim.paid_amount_pkr,
                claim.reserve_amount_pkr,
                claim.deductible_applied_pkr,
            )
        ]
        + [payment.amount_pkr for payment in corpus.claim_payments]
    )

    assert all(isinstance(value, Decimal) for value in money_values)
    assert all(not isinstance(value, float) for value in money_values)
    assert all(value == value.quantize(CENT) for value in money_values)


def test_external_identifiers_are_unique_and_match_their_formats(
    corpus: TransactionRows,
) -> None:
    claim_numbers = [claim.claim_number for claim in corpus.claims]
    policy_numbers = [policy.policy_number for policy in corpus.policies]
    claim_pattern = re.compile(get_settings().claim_id_pattern)

    assert len(claim_numbers) == len(set(claim_numbers))
    assert all(claim_pattern.fullmatch(number) for number in claim_numbers)
    assert len(policy_numbers) == len(set(policy_numbers))
    assert all(re.fullmatch(r"POL-2026-\d{6}", number) for number in policy_numbers)


def test_referential_integrity_is_constructed_across_all_rows(
    corpus: TransactionRows,
) -> None:
    region_ids = {region.region_id for region in REGIONS}
    product_ids = {product.product_id for product in COVERAGE_PRODUCTS}
    adjuster_ids = {adjuster.adjuster_id for adjuster in ADJUSTERS}
    customer_ids = {customer.customer_id for customer in corpus.customers}
    policy_ids = {policy.policy_id for policy in corpus.policies}
    claim_ids = {claim.claim_id for claim in corpus.claims}

    assert all(customer.region_id in region_ids for customer in corpus.customers)
    assert all(policy.customer_id in customer_ids for policy in corpus.policies)
    assert all(policy.product_id in product_ids for policy in corpus.policies)
    assert all(policy.region_id in region_ids for policy in corpus.policies)
    assert all(claim.policy_id in policy_ids for claim in corpus.claims)
    assert all(claim.region_id in region_ids for claim in corpus.claims)
    assert all(
        claim.adjuster_id is None or claim.adjuster_id in adjuster_ids
        for claim in corpus.claims
    )
    assert any(claim.adjuster_id is None for claim in corpus.claims)
    assert all(payment.claim_id in claim_ids for payment in corpus.claim_payments)


def test_loss_policy_and_adjuster_regions_have_documented_semantics(
    corpus: TransactionRows,
) -> None:
    policy_by_id = {policy.policy_id: policy for policy in corpus.policies}
    adjuster_by_id = {adjuster.adjuster_id: adjuster for adjuster in ADJUSTERS}

    assert all(
        policy_by_id[claim.policy_id].inception_date
        <= claim.date_of_loss
        <= policy_by_id[claim.policy_id].expiry_date
        for claim in corpus.claims
    )
    assert any(
        claim.region_id != policy_by_id[claim.policy_id].region_id
        for claim in corpus.claims
    )
    assert any(
        claim.adjuster_id is not None
        and claim.region_id != adjuster_by_id[claim.adjuster_id].region_id
        for claim in corpus.claims
    )


def test_claim_dates_and_statuses_are_coherent(corpus: TransactionRows) -> None:
    assert min(claim.report_date for claim in corpus.claims).isoformat() == "2026-01-01"
    assert max(claim.report_date for claim in corpus.claims).isoformat() == "2026-06-30"

    for claim in corpus.claims:
        assert (claim.closed_date is None) == (claim.status in OPENISH_STATUSES)
        if claim.status in OPENISH_STATUSES:
            assert claim.reserve_amount_pkr > 0
        if claim.status == "closed":
            assert claim.reserve_amount_pkr == Decimal("0.00")
        if claim.status in {"denied", "withdrawn"}:
            assert claim.incurred_amount_pkr == Decimal("0.00")


def test_deductibles_come_from_policies_and_limits_are_respected(
    corpus: TransactionRows,
) -> None:
    policy_by_id = {policy.policy_id: policy for policy in corpus.policies}
    for claim in corpus.claims:
        policy = policy_by_id[claim.policy_id]
        assert claim.deductible_applied_pkr in {Decimal("0.00"), policy.deductible_pkr}
        assert claim.incurred_amount_pkr <= policy.sum_insured_pkr
        if claim.status not in {"denied", "withdrawn"}:
            assert claim.incurred_amount_pkr > Decimal("0.00")


def test_six_month_loss_ratios_are_plausible_in_every_region_product_cell(
    corpus: TransactionRows,
) -> None:
    policy_by_id = {policy.policy_id: policy for policy in corpus.policies}
    annual_premium_by_cell: defaultdict[tuple[int, int], Decimal] = defaultdict(Decimal)
    incurred_by_cell: defaultdict[tuple[int, int], Decimal] = defaultdict(Decimal)

    for policy in corpus.policies:
        annual_premium_by_cell[(policy.region_id, policy.product_id)] += (
            policy.annual_premium_pkr
        )
    for claim in corpus.claims:
        product_id = policy_by_id[claim.policy_id].product_id
        incurred_by_cell[(claim.region_id, product_id)] += claim.incurred_amount_pkr

    expected_cells = {
        (region.region_id, product.product_id)
        for region in REGIONS
        for product in COVERAGE_PRODUCTS
    }
    assert set(annual_premium_by_cell) == expected_cells
    assert set(incurred_by_cell) == expected_cells

    earned_fraction = Decimal("0.5")
    cell_ratios = {
        cell: incurred_by_cell[cell] / (annual_premium * earned_fraction)
        for cell, annual_premium in annual_premium_by_cell.items()
    }
    overall_ratio = sum(incurred_by_cell.values(), Decimal("0.00")) / (
        sum(annual_premium_by_cell.values(), Decimal("0.00")) * earned_fraction
    )

    assert Decimal("0.5") <= overall_ratio <= Decimal("1.2")
    assert all(
        Decimal("0.5") <= ratio <= Decimal("1.2")
        for ratio in cell_ratios.values()
    )


def test_payments_reconcile_exactly_to_claim_paid_amounts(
    corpus: TransactionRows,
) -> None:
    payments_by_claim: defaultdict[int, list[ClaimPaymentRow]] = defaultdict(list)
    for payment in corpus.claim_payments:
        payments_by_claim[payment.claim_id].append(payment)

    for claim in corpus.claims:
        payments = payments_by_claim[claim.claim_id]
        assert sum((payment.amount_pkr for payment in payments), Decimal("0.00")) == (
            claim.paid_amount_pkr
        )
        assert sum(
            (
                payment.amount_pkr
                for payment in payments
                if payment.payment_type == "indemnity"
            ),
            Decimal("0.00"),
        ) <= claim.paid_amount_pkr
        assert all(payment.payment_date > claim.report_date for payment in payments)
        if claim.closed_date is not None:
            assert all(payment.payment_date <= claim.closed_date for payment in payments)


def test_water_cause_pool_contains_sudden_and_gradual_notifications(
    corpus: TransactionRows,
) -> None:
    water_causes = {
        claim.cause_description
        for claim in corpus.claims
        if claim.peril == "water_damage"
    }

    assert any("sudden" in cause.lower() or "burst" in cause.lower() for cause in water_causes)
    assert any(
        "gradual" in cause.lower()
        or "long-term" in cause.lower()
        or "long-standing" in cause.lower()
        for cause in water_causes
    )


def test_planted_trends_are_reviewable_rate_rows_and_present_in_output(
    corpus: TransactionRows,
) -> None:
    assert len(CLAIM_RATE_TABLE) == 6 * len(REGIONS) * len(PERILS)
    assert all(CLAIM_RATE_TABLE[key] == rate for key, rate in PLANTED_RATE_ROWS.items())
    for (month, region_id, peril), planted_rate in PLANTED_RATE_ROWS.items():
        neighbour_month = month - 1
        neighbour = CLAIM_RATE_TABLE[(neighbour_month, region_id, peril)]
        assert planted_rate.frequency_weight > neighbour.frequency_weight
        assert planted_rate.severity_max_pkr > neighbour.severity_max_pkr
    assert all(
        CLAIM_RATE_TABLE[(month, region.region_id, "fire")].severity_min_pkr
        > CLAIM_RATE_TABLE[(month, region.region_id, "impact")].severity_max_pkr
        for month in range(1, 7)
        for region in REGIONS
    )

    frequencies = Counter((claim.report_date.month, claim.peril) for claim in corpus.claims)
    assert frequencies[(3, "water_damage")] > frequencies[(2, "water_damage")]
    assert frequencies[(3, "water_damage")] > frequencies[(4, "water_damage")]
    assert frequencies[(4, "theft")] > frequencies[(3, "theft")]
    assert frequencies[(4, "theft")] > frequencies[(5, "theft")]

    water_regions = {1, 2, 4, 5}
    theft_regions = {3, 6, 7, 9}
    march_water = Counter(
        claim.region_id
        for claim in corpus.claims
        if claim.report_date.month == 3 and claim.peril == "water_damage"
    )
    april_theft = Counter(
        claim.region_id
        for claim in corpus.claims
        if claim.report_date.month == 4 and claim.peril == "theft"
    )
    assert sum(march_water[region_id] for region_id in water_regions) > sum(
        march_water[region.region_id]
        for region in REGIONS
        if region.region_id not in water_regions
    )
    assert sum(april_theft[region_id] for region_id in theft_regions) > sum(
        april_theft[region.region_id]
        for region in REGIONS
        if region.region_id not in theft_regions
    )


def test_changing_one_rate_row_changes_generated_frequency() -> None:
    key = (3, 1, "water_damage")
    changed_table = dict(CLAIM_RATE_TABLE)
    changed_table[key] = replace(
        changed_table[key], frequency_weight=changed_table[key].frequency_weight * 50
    )

    baseline = generate_transactions(91)
    changed = generate_transactions(91, rate_table=changed_table)
    baseline_count = sum(
        claim.report_date.month == key[0]
        and claim.region_id == key[1]
        and claim.peril == key[2]
        for claim in baseline.claims
    )
    changed_count = sum(
        claim.report_date.month == key[0]
        and claim.region_id == key[1]
        and claim.peril == key[2]
        for claim in changed.claims
    )

    assert changed != baseline
    assert changed_count > baseline_count


def test_generated_text_is_ascii(corpus: TransactionRows) -> None:
    all_rows = (
        *corpus.customers,
        *corpus.policies,
        *corpus.claims,
        *corpus.claim_payments,
    )
    assert all(
        value.isascii()
        for row in all_rows
        for field in fields(row)
        if isinstance((value := getattr(row, field.name)), str)
    )
