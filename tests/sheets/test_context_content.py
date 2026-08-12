"""The committed schema contexts for the spreadsheet tables.

These files are the contract C-8.3's workbook generator has to satisfy, and the only thing
the planner will know about the spreadsheets. Like the claims-database contexts, the tests
are about meaning rather than shape: that a fraction is documented as a fraction, that a
label typed into a sheet is not passed off as a foreign key, and that a target is never
confused with an outcome.

Each of those, got wrong, produces a confident number that is out by a hundredfold or that
reports a plan as a result.
"""

from __future__ import annotations

import pytest

from vericlaim.config import get_settings
from vericlaim.sheets.ingest import table_name
from vericlaim.sql.contexts import LINEAGE_COLUMN_NAMES, allow_list, load_contexts
from vericlaim.sql.validator import validate_sql

# The workbooks C-8.3 generates. Named here so a context deleted by accident fails a test
# rather than quietly shrinking what the agent can answer.
EXPECTED_TABLES = {
    "sheets.regional_inspection_compliance_q1__compliance",
    "sheets.renewals_q1__renewals",
    "sheets.claims_targets_q1__targets",
    "sheets.adjuster_performance__performance",
    "sheets.loss_ratio_report__loss_ratio",
    "sheets.risk_categories__categories",
}


@pytest.fixture(scope="module")
def contexts():
    return load_contexts(get_settings().sheets_context_dir)


def test_every_planned_workbook_is_documented(contexts) -> None:
    assert set(contexts) == EXPECTED_TABLES


def test_every_table_names_the_workbook_and_sheet_it_comes_from(contexts) -> None:
    """Half of every citation this source emits. A context without them describes a table
    whose rows can be read but not pointed at."""
    for context in contexts.values():
        assert context.workbook and context.sheet


def test_the_documented_name_is_the_name_the_ingest_will_create(contexts) -> None:
    """Two files, one contract. A context describing a table the ingest never builds is a
    table the planner will write SQL against and the executor will not find."""
    for context in contexts.values():
        assert context.table == table_name(context.workbook, context.sheet, 0)


def test_every_table_carries_the_lineage_columns(contexts) -> None:
    for context in contexts.values():
        assert set(LINEAGE_COLUMN_NAMES) <= set(context.column_names)


def test_a_rate_is_documented_as_a_fraction_not_a_percentage(contexts) -> None:
    """0.61 reported as "0.61%" is wrong by two orders of magnitude and reads as a
    catastrophe rather than a near miss."""
    compliance = contexts["sheets.regional_inspection_compliance_q1__compliance"]
    rate = next(
        column for column in compliance.columns if column.name == "compliance_rate"
    )

    assert "fraction" in rate.meaning.lower()


def test_a_target_is_documented_as_an_intention(contexts) -> None:
    """The numbers are real; reporting a target as the number of claims filed is wrong
    even so, and nothing about the value itself says which it is."""
    targets = contexts["sheets.claims_targets_q1__targets"]

    assert any("target" in caution.lower() for caution in targets.cautions)


def test_a_typed_region_label_is_not_passed_off_as_a_key(contexts) -> None:
    """It matches ops.regions by spelling. A name in one source and not the other is a
    finding to report, not a row to drop."""
    compliance = contexts["sheets.regional_inspection_compliance_q1__compliance"]
    region = next(column for column in compliance.columns if column.name == "region")

    assert "key" in region.meaning.lower()


def test_a_ratio_column_warns_against_being_averaged(contexts) -> None:
    """The ratio of two sums is not the mean of the ratios, and the gap grows with how
    unequal the denominators are."""
    loss = contexts["sheets.loss_ratio_report__loss_ratio"]

    assert any("averag" in caution.lower() for caution in loss.cautions)


def test_the_counts_that_bound_each_other_are_declared(contexts) -> None:
    """More inspections cannot be completed than scheduled, and more policies cannot be
    renewed than were due. Both are checkable against a result."""
    compliance = contexts["sheets.regional_inspection_compliance_q1__compliance"]
    renewals = contexts["sheets.renewals_q1__renewals"]

    orderings = {
        (invariant.lower, invariant.upper)
        for context in (compliance, renewals)
        for invariant in context.invariants
        if invariant.kind == "ordered"
    }

    assert ("completed_inspections", "scheduled_inspections") in orderings
    assert ("renewed", "due_for_renewal") in orderings


def test_the_validator_accepts_a_query_over_a_documented_sheet(contexts) -> None:
    """The point of the whole arrangement: spreadsheets go through the same audited
    validator as the claims database, with no second code path to keep safe."""
    allowed = allow_list(contexts, ["sheets.regional_inspection_compliance_q1__compliance"])

    verdict = validate_sql(
        "SELECT region, compliance_rate, _a1_range "
        "FROM sheets.regional_inspection_compliance_q1__compliance",
        allowed,
        500,
    )

    assert verdict.ok, verdict.reason


def test_the_validator_rejects_a_column_no_context_documents(contexts) -> None:
    allowed = allow_list(contexts, ["sheets.renewals_q1__renewals"])

    verdict = validate_sql(
        "SELECT churn FROM sheets.renewals_q1__renewals", allowed, 500
    )

    assert not verdict.ok


def test_the_two_context_directories_do_not_overlap() -> None:
    """A table documented twice would take whichever definition loaded last, silently."""
    settings = get_settings()
    ops = load_contexts(settings.sql_context_dir)
    sheets = load_contexts(settings.sheets_context_dir)

    assert not set(ops) & set(sheets)
