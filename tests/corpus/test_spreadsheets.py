"""Offline contracts for the generated semi-structured spreadsheet corpus."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from openpyxl.utils import column_index_from_string

from vericlaim.config import get_settings
from vericlaim.corpus.catalog import ADJUSTERS, COVERAGE_PRODUCTS, REGIONS
from vericlaim.corpus.spreadsheets import generate_spreadsheet_corpus
from vericlaim.corpus.transactions import generate_transactions
from vericlaim.sheets.coercion import coerce, postgres_type
from vericlaim.sheets.profiler import profile_workbook
from vericlaim.sql.contexts import LINEAGE_COLUMN_NAMES, load_contexts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table(path: Path):
    profiles = profile_workbook(path)
    assert len(profiles) == 1
    assert len(profiles[0].tables) == 1
    return profiles[0], profiles[0].tables[0]


def _records(path: Path) -> list[dict[str, object]]:
    _profile, table = _table(path)
    return [
        {
            column.name: coerce(
                table.value_at(row, column_index_from_string(column.letter)),
                column.kind,
            ).value
            for column in table.columns
        }
        for row in range(table.first_data_row, table.last_data_row + 1)
    ]


@pytest.fixture
def generated(tmp_path: Path) -> dict[str, Path]:
    return {
        path.name: path
        for path in generate_spreadsheet_corpus(tmp_path / "spreadsheets", seed=42)
    }


def test_generation_is_byte_identical_across_directories(tmp_path: Path) -> None:
    first = generate_spreadsheet_corpus(tmp_path / "first", seed=42)
    second = generate_spreadsheet_corpus(tmp_path / "second", seed=42)

    first_hashes = {path.name: _sha256(path) for path in first}
    second_hashes = {path.name: _sha256(path) for path in second}

    assert first_hashes == second_hashes


def test_generated_workbooks_match_the_reviewed_contexts_in_both_directions(
    generated: dict[str, Path],
) -> None:
    contexts = load_contexts(get_settings().sheets_context_dir)
    declared = {
        (context.workbook, context.sheet) for context in contexts.values()
    }
    actual = {
        (path.name, profile_workbook(path)[0].sheet) for path in generated.values()
    }

    assert len(generated) == 6
    assert set(generated) == {path.name for path in generated.values()}
    assert set(next(iter(generated.values())).parent.glob("*.xlsx")) == set(
        generated.values()
    )
    assert actual == declared


def test_every_workbook_ingests_to_its_declared_columns_and_types(
    generated: dict[str, Path],
) -> None:
    contexts = load_contexts(get_settings().sheets_context_dir)
    by_workbook = {context.workbook: context for context in contexts.values()}

    for name, path in generated.items():
        context = by_workbook[name]
        _profile, table = _table(path)
        declared = [
            column
            for column in context.columns
            if column.name not in LINEAGE_COLUMN_NAMES
        ]

        assert [column.name for column in table.columns] == [
            column.name for column in declared
        ]
        assert [postgres_type(column.kind).lower() for column in table.columns] == [
            column.type for column in declared
        ]


def test_declared_orderings_and_non_negative_figures_hold(
    generated: dict[str, Path],
) -> None:
    compliance = _records(generated["Regional_Inspection_Compliance_Q1.xlsx"])
    renewals = _records(generated["Renewals_Q1.xlsx"])
    loss_ratios = _records(generated["Loss_Ratio_Report.xlsx"])
    risk_categories = _records(generated["Risk_Categories.xlsx"])

    assert all(
        row["completed_inspections"] <= row["scheduled_inspections"]
        for row in compliance
    )
    assert all(row["renewed"] <= row["due_for_renewal"] for row in renewals)
    assert all(row["earned_premium_pkr"] >= 0 for row in loss_ratios)
    assert all(row["loss_ratio"] >= 0 for row in loss_ratios)
    assert all(row["premium_loading"] >= 0 for row in risk_categories)


def test_region_and_product_labels_are_exact_catalogue_spellings(
    generated: dict[str, Path],
) -> None:
    region_names = {region.region_name for region in REGIONS}
    product_names = {product.product_name for product in COVERAGE_PRODUCTS}

    for path in generated.values():
        records = _records(path)
        if records and "region" in records[0]:
            assert {row["region"] for row in records} == region_names
        if records and "product" in records[0]:
            assert {row["product"] for row in records} == product_names


def test_operational_reports_reconcile_to_the_generated_transactions(
    generated: dict[str, Path],
) -> None:
    transactions = generate_transactions(42)
    policy_by_id = {policy.policy_id: policy for policy in transactions.policies}
    region_by_id = {region.region_id: region.region_name for region in REGIONS}
    product_by_id = {
        product.product_id: product.product_name for product in COVERAGE_PRODUCTS
    }

    expected_premium: defaultdict[tuple[str, str], Decimal] = defaultdict(
        lambda: Decimal("0.00")
    )
    expected_incurred: defaultdict[tuple[str, str], Decimal] = defaultdict(
        lambda: Decimal("0.00")
    )
    for policy in transactions.policies:
        key = (region_by_id[policy.region_id], product_by_id[policy.product_id])
        expected_premium[key] += policy.annual_premium_pkr
    for claim in transactions.claims:
        key = (
            region_by_id[claim.region_id],
            product_by_id[policy_by_id[claim.policy_id].product_id],
        )
        expected_incurred[key] += claim.incurred_amount_pkr

    for row in _records(generated["Loss_Ratio_Report.xlsx"]):
        key = (row["region"], row["product"])
        assert row["earned_premium_pkr"] == expected_premium[key]
        assert row["incurred_claims_pkr"] == expected_incurred[key]
        assert row["loss_ratio"] == pytest.approx(
            expected_incurred[key] / expected_premium[key]
        )

    expected_handled: defaultdict[str, int] = defaultdict(int)
    expected_close_days: defaultdict[str, list[int]] = defaultdict(list)
    adjuster_by_id = {
        adjuster.adjuster_id: adjuster.adjuster_name for adjuster in ADJUSTERS
    }
    for claim in transactions.claims:
        if claim.adjuster_id is not None:
            adjuster = adjuster_by_id[claim.adjuster_id]
            expected_handled[adjuster] += 1
            if claim.status == "closed" and claim.closed_date is not None:
                expected_close_days[adjuster].append(
                    (claim.closed_date - claim.report_date).days
                )
    performance = _records(generated["Adjuster_Performance.xlsx"])
    assert {row["adjuster"]: row["claims_handled"] for row in performance} == (
        expected_handled
    )
    for row in performance:
        if row["average_days_to_close"] is None:
            continue
        days = expected_close_days[row["adjuster"]]
        expected_average = (Decimal(sum(days)) / Decimal(len(days))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        assert row["average_days_to_close"] == expected_average

    expected_due: defaultdict[tuple[str, str], int] = defaultdict(int)
    expected_renewed: defaultdict[tuple[str, str], int] = defaultdict(int)
    for policy in transactions.policies:
        if policy.inception_date.month <= 3:
            key = (region_by_id[policy.region_id], product_by_id[policy.product_id])
            expected_due[key] += 1
            if policy.status == "active":
                expected_renewed[key] += 1
    for row in _records(generated["Renewals_Q1.xlsx"]):
        key = (row["region"], row["product"])
        assert row["due_for_renewal"] == expected_due[key]
        assert row["renewed"] == expected_renewed[key]
        assert row["retention_rate"] == pytest.approx(
            Decimal(expected_renewed[key]) / Decimal(expected_due[key])
        )


def test_targets_are_in_the_same_order_of_magnitude_as_q1_actuals(
    generated: dict[str, Path],
) -> None:
    transactions = generate_transactions(42)
    region_by_id = {region.region_id: region.region_name for region in REGIONS}
    actual_counts: defaultdict[str, int] = defaultdict(int)
    actual_incurred: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for claim in transactions.claims:
        if claim.report_date.month <= 3:
            region = region_by_id[claim.region_id]
            actual_counts[region] += 1
            actual_incurred[region] += claim.incurred_amount_pkr

    for row in _records(generated["Claims_Targets_Q1.xlsx"]):
        region = row["region"]
        assert Decimal("0.75") <= row["target_claims"] / actual_counts[region] <= Decimal(
            "1.25"
        )
        assert (
            Decimal("0.75")
            <= row["target_incurred_pkr"] / actual_incurred[region]
            <= Decimal("1.25")
        )


def test_the_deliberate_mess_is_present_and_still_profiled_as_data(
    generated: dict[str, Path],
) -> None:
    tables = [_table(path) for path in generated.values()]
    values = [
        value
        for _profile, table in tables
        for value in (table.values or {}).values()
    ]

    assert any(profile.merged_ranges for profile, _table_profile in tables)
    assert any(len(table.header_rows) == 2 for _profile, table in tables)
    assert any(table.footer_rows for _profile, table in tables)
    assert any(table.spacer_columns for _profile, table in tables)
    assert "N/A" in values
    assert "-" in values
    assert any(isinstance(value, str) and value.endswith("%") for value in values)
    assert all(
        row.get("region") != "TOTAL"
        for path in generated.values()
        for row in _records(path)
    )
