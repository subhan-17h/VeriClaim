"""Schema contexts: what the planner is told the database means.

A context is the reviewed, committed description of one table -- its purpose, its columns
and what they mean, its join keys, and the distinctions that make a query wrong in a way
that still returns a plausible number. Statistics are machine-refreshed; the semantics are
hand-authored, because no profiler can infer that `incurred_amount_pkr` is an estimate of
ultimate cost while `paid_amount_pkr` is cash already out the door.

This file tests the loader and the invariants. The committed content is checked in
test_context_content.py.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vericlaim.sql.contexts import (
    LINEAGE_COLUMN_NAMES,
    ContextError,
    allow_list,
    context_detail,
    context_summary,
    load_contexts,
)
from vericlaim.sql.profiler import dump_context

CLAIMS_YAML = """
schema: ops
table: claims
purpose: One row per reported claim.
columns:
  - name: claim_id
    type: bigint
    meaning: Surrogate key.
  - name: claim_number
    type: text
    meaning: The reference printed on scanned paperwork.
  - name: policy_id
    type: bigint
    meaning: The policy this claim was made against.
  - name: incurred_amount_pkr
    type: numeric
    meaning: Estimated ultimate cost.
    unit: PKR
useful_for:
  - How many claims were reported in a period.
synonyms:
  - term: claim reference
    maps_to: claim_number
joins:
  - column: policy_id
    references: ops.policies.policy_id
    meaning: Each claim belongs to exactly one policy.
cautions:
  - Incurred is not paid.
"""

POLICIES_YAML = """
schema: ops
table: policies
purpose: One row per policy.
columns:
  - name: policy_id
    type: bigint
    meaning: Surrogate key.
  - name: deductible_pkr
    type: numeric
    meaning: Borne by the insured per claim.
    unit: PKR
useful_for:
  - Which product a claim was covered under.
"""


@pytest.fixture
def context_dir(tmp_path: Path) -> Path:
    (tmp_path / "ops.claims.yaml").write_text(textwrap.dedent(CLAIMS_YAML), encoding="utf-8")
    (tmp_path / "ops.policies.yaml").write_text(textwrap.dedent(POLICIES_YAML), encoding="utf-8")
    return tmp_path


def write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ------------------------------------------------------------------ loading


def test_contexts_are_keyed_by_qualified_name(context_dir) -> None:
    """Bare table names collide across schemas; ops.regions and sheets.regions are two
    different tables."""
    contexts = load_contexts(context_dir)

    assert set(contexts) == {"ops.claims", "ops.policies"}


def test_a_context_carries_its_columns_in_order(context_dir) -> None:
    claims = load_contexts(context_dir)["ops.claims"]

    assert [column.name for column in claims.columns][:2] == ["claim_id", "claim_number"]
    assert claims.columns[1].meaning.startswith("The reference")


def test_a_money_column_declares_its_unit(context_dir) -> None:
    """PKR is the corpus currency; an amount whose unit is a guess is a wrong answer
    waiting to be formatted."""
    claims = load_contexts(context_dir)["ops.claims"]
    incurred = next(column for column in claims.columns if column.name == "incurred_amount_pkr")

    assert incurred.unit == "PKR"


def test_an_empty_directory_is_an_error(tmp_path) -> None:
    """Failing closed: an empty allow-list would otherwise silently answer nothing."""
    with pytest.raises(ContextError):
        load_contexts(tmp_path)


def test_a_duplicate_table_is_an_error(context_dir) -> None:
    write(context_dir, "duplicate.yaml", CLAIMS_YAML)

    with pytest.raises(ContextError, match="ops.claims"):
        load_contexts(context_dir)


def test_a_context_without_columns_is_an_error(context_dir) -> None:
    write(context_dir, "ops.empty.yaml", "schema: ops\ntable: empty\npurpose: x\ncolumns: []\n")

    with pytest.raises(ContextError, match="columns"):
        load_contexts(context_dir)


def test_a_column_without_a_meaning_is_an_error(context_dir) -> None:
    """The meaning is the only reason a context exists; a name alone is already in the
    database."""
    write(
        context_dir,
        "ops.bare.yaml",
        """
        schema: ops
        table: bare
        purpose: x
        columns:
          - name: id
            type: bigint
        """,
    )

    with pytest.raises(ContextError, match="meaning"):
        load_contexts(context_dir)


def test_a_synonym_for_an_unknown_column_is_an_error(context_dir) -> None:
    """A synonym pointing at nothing sends the generator after a column that does not
    exist, and the failure would surface as invalid SQL instead."""
    write(
        context_dir,
        "ops.bad_synonym.yaml",
        """
        schema: ops
        table: bad_synonym
        purpose: x
        columns:
          - name: id
            type: bigint
            meaning: Surrogate key.
        synonyms:
          - term: reference
            maps_to: no_such_column
        """,
    )

    with pytest.raises(ContextError, match="no_such_column"):
        load_contexts(context_dir)


def test_a_join_to_an_undocumented_table_is_an_error(context_dir) -> None:
    """Checked across files: a join is only usable if the target is also described."""
    write(
        context_dir,
        "ops.orphan.yaml",
        """
        schema: ops
        table: orphan
        purpose: x
        columns:
          - name: ghost_id
            type: bigint
            meaning: Points nowhere.
        joins:
          - column: ghost_id
            references: ops.ghosts.ghost_id
            meaning: Dangling.
        """,
    )

    with pytest.raises(ContextError, match="ops.ghosts"):
        load_contexts(context_dir)


def test_a_join_from_an_unknown_local_column_is_an_error(context_dir) -> None:
    write(
        context_dir,
        "ops.bad_join.yaml",
        """
        schema: ops
        table: bad_join
        purpose: x
        columns:
          - name: id
            type: bigint
            meaning: Surrogate key.
        joins:
          - column: missing_id
            references: ops.policies.policy_id
            meaning: Wrong local column.
        """,
    )

    with pytest.raises(ContextError, match="missing_id"):
        load_contexts(context_dir)


def test_malformed_yaml_names_the_file(context_dir) -> None:
    write(context_dir, "broken.yaml", "schema: ops\ntable: [unclosed\n")

    with pytest.raises(ContextError, match="broken.yaml"):
        load_contexts(context_dir)


# ------------------------------------------------- the seam into the validator


def test_the_allow_list_carries_schema_table_and_columns(context_dir) -> None:
    """This is the only path by which a table becomes queryable."""
    contexts = load_contexts(context_dir)

    allowed = allow_list(contexts, ["ops.claims"])

    assert [entry.qualified for entry in allowed] == ["ops.claims"]
    assert "claim_number" in allowed[0].columns


def test_selecting_an_unknown_table_is_an_error(context_dir) -> None:
    """Silently dropping it would produce an allow-list that quietly excludes the table
    the planner intended to read."""
    contexts = load_contexts(context_dir)

    with pytest.raises(ContextError, match="ops.nope"):
        allow_list(contexts, ["ops.nope"])


def test_selecting_nothing_yields_nothing(context_dir) -> None:
    contexts = load_contexts(context_dir)

    assert allow_list(contexts, []) == ()


def test_the_allow_list_feeds_the_validator(context_dir) -> None:
    """The two halves are only useful joined: contexts decide what SQL may touch."""
    from vericlaim.sql.validator import validate_sql

    contexts = load_contexts(context_dir)
    allowed = allow_list(contexts, ["ops.claims"])

    accepted = validate_sql("SELECT claim_number FROM ops.claims", allowed, 50)
    refused = validate_sql("SELECT policy_id FROM ops.policies", allowed, 50)

    assert accepted.ok, accepted.reason
    assert not refused.ok
    assert "policies" in refused.reason


# ------------------------------------------------------------------- routing


def test_the_summary_keeps_what_routing_needs(context_dir) -> None:
    summary = context_summary(load_contexts(context_dir)["ops.claims"])

    assert summary["table"] == "ops.claims"
    assert summary["purpose"]
    assert summary["useful_for"]
    assert [column["name"] for column in summary["columns"]][0] == "claim_id"


def test_the_summary_drops_bulky_sample_values(context_dir) -> None:
    """Every context is shown to the router on every question; samples would dominate
    the prompt without changing which source is chosen."""
    summary = context_summary(load_contexts(context_dir)["ops.claims"])

    assert all("sample_values" not in column for column in summary["columns"])


def test_the_summary_keeps_the_cautions(context_dir) -> None:
    """The distinctions that make a query wrong are the point, not decoration."""
    summary = context_summary(load_contexts(context_dir)["ops.claims"])

    assert summary["cautions"] == ["Incurred is not paid."]



# ------------------------------------------------------------------ planning view


def test_the_planning_view_keeps_what_the_summary_drops(context_dir) -> None:
    """The router sees every context on every question and gets the summary. The planner
    sees only the chosen tables, and needs the units the summary drops."""
    claims = load_contexts(context_dir)["ops.claims"]

    detail = context_detail(claims)
    incurred = next(
        column for column in detail["columns"] if column["name"] == "incurred_amount_pkr"
    )

    assert incurred["unit"] == "PKR"
    assert incurred["meaning"] == "Estimated ultimate cost."


def test_a_join_carries_its_meaning_to_the_planner(context_dir) -> None:
    """The summary gives the router the key; the planner needs to know what it means."""
    claims = load_contexts(context_dir)["ops.claims"]

    joins = context_detail(claims)["joins"]

    assert joins[0]["meaning"] == "Each claim belongs to exactly one policy."


def test_the_cautions_reach_the_planner_intact(context_dir) -> None:
    claims = load_contexts(context_dir)["ops.claims"]

    assert context_detail(claims)["cautions"] == ["Incurred is not paid."]


# ------------------------------------------------------------------ invariants


INVARIANT_YAML = """
schema: ops
table: money
purpose: One row per settlement.
columns:
  - name: total_pkr
    type: numeric
    meaning: The whole.
  - name: part_a_pkr
    type: numeric
    meaning: One part.
  - name: part_b_pkr
    type: numeric
    meaning: The other part.
  - name: opened_on
    type: date
    meaning: When it began.
  - name: closed_on
    type: date
    meaning: When it ended.
invariants:
  - kind: sum
    total: total_pkr
    parts: [part_a_pkr, part_b_pkr]
    meaning: The whole is the sum of its parts.
  - kind: non_negative
    column: part_a_pkr
    meaning: A part cannot be negative.
  - kind: ordered
    lower: opened_on
    upper: closed_on
    meaning: Nothing closes before it opens.
"""


def loaded(tmp_path: Path, body: str = INVARIANT_YAML):
    write(tmp_path, "ops.money.yaml", body)
    return load_contexts(tmp_path)["ops.money"]


def test_a_declared_sum_names_its_total_and_its_parts(tmp_path: Path) -> None:
    """These are the facts the reviewed cautions state in prose, in a form the observer
    can check a result against."""
    invariant = loaded(tmp_path).invariants[0]

    assert invariant.kind == "sum"
    assert invariant.total == "total_pkr"
    assert invariant.parts == ("part_a_pkr", "part_b_pkr")


def test_a_declared_floor_names_its_column(tmp_path: Path) -> None:
    invariant = loaded(tmp_path).invariants[1]

    assert invariant.kind == "non_negative"
    assert invariant.column == "part_a_pkr"


def test_a_declared_ordering_names_both_sides(tmp_path: Path) -> None:
    invariant = loaded(tmp_path).invariants[2]

    assert invariant.kind == "ordered"
    assert (invariant.lower, invariant.upper) == ("opened_on", "closed_on")


def test_every_invariant_carries_the_reason_it_holds(tmp_path: Path) -> None:
    """The meaning is what an observation reports; a violation with no explanation is a
    number a reader cannot act on."""
    assert all(invariant.meaning for invariant in loaded(tmp_path).invariants)


def test_an_invariant_over_an_undocumented_column_is_an_error(tmp_path: Path) -> None:
    body = INVARIANT_YAML.replace("column: part_a_pkr", "column: profit_pkr")

    with pytest.raises(ContextError, match="profit_pkr"):
        loaded(tmp_path, body)


def test_an_invariant_of_an_unknown_kind_is_an_error(tmp_path: Path) -> None:
    body = INVARIANT_YAML.replace("kind: non_negative", "kind: vibes")

    with pytest.raises(ContextError, match="vibes"):
        loaded(tmp_path, body)


def test_a_sum_needs_at_least_two_parts(tmp_path: Path) -> None:
    """A one-part sum is an equality between two columns, which `ordered` already says
    better and which no reviewer would have meant."""
    body = INVARIANT_YAML.replace("parts: [part_a_pkr, part_b_pkr]", "parts: [part_a_pkr]")

    with pytest.raises(ContextError, match="parts"):
        loaded(tmp_path, body)


def test_a_context_declaring_nothing_has_no_invariants(context_dir) -> None:
    assert load_contexts(context_dir)["ops.claims"].invariants == ()


def test_invariants_stay_out_of_the_prompt_views(tmp_path: Path) -> None:
    """The cautions already state them in prose, which instructs a model better than a
    machine form does; carrying both would spend prompt space saying it twice."""
    context = loaded(tmp_path)

    assert "invariants" not in context_summary(context)
    assert "invariants" not in context_detail(context)


# ------------------------------------------------------------------ spreadsheets


SHEET_YAML = """
schema: sheets
table: ric_q1__northern
workbook: RIC_Q1.xlsx
sheet: Northern
purpose: Inspection compliance by region for Q1.
columns:
  - name: region
    type: text
    meaning: The region inspected.
  - name: compliance
    type: numeric
    meaning: Proportion of scheduled inspections completed.
"""


def test_a_spreadsheet_backed_table_knows_which_file_it_came_from(tmp_path: Path) -> None:
    write(tmp_path, "sheets.ric.yaml", SHEET_YAML)

    context = load_contexts(tmp_path)["sheets.ric_q1__northern"]

    assert context.workbook == "RIC_Q1.xlsx"
    assert context.sheet == "Northern"
    assert context.is_spreadsheet is True


def test_an_ops_table_is_not_a_spreadsheet(context_dir) -> None:
    assert load_contexts(context_dir)["ops.claims"].is_spreadsheet is False


def test_the_lineage_columns_are_added_to_every_spreadsheet_context(tmp_path: Path) -> None:
    """They have to be in the allow-list or the tool cannot select the very columns the
    citation is built from -- and they are identical everywhere, so declaring them in each
    file by hand is six chances to get one wrong."""
    write(tmp_path, "sheets.ric.yaml", SHEET_YAML)

    names = load_contexts(tmp_path)["sheets.ric_q1__northern"].column_names

    assert names[:2] == ("region", "compliance")
    assert set(LINEAGE_COLUMN_NAMES) <= set(names)


def test_the_lineage_columns_explain_themselves_to_the_planner(tmp_path: Path) -> None:
    write(tmp_path, "sheets.ric.yaml", SHEET_YAML)
    context = load_contexts(tmp_path)["sheets.ric_q1__northern"]

    lineage = next(column for column in context.columns if column.name == "_a1_range")

    assert lineage.meaning


def test_a_refresh_does_not_write_the_lineage_columns_back(tmp_path: Path) -> None:
    """Injected on load, so writing them out would make the next load inject duplicates."""
    write(tmp_path, "sheets.ric.yaml", SHEET_YAML)
    context = load_contexts(tmp_path)["sheets.ric_q1__northern"]

    body = dump_context(context)

    assert "_a1_range" not in body
    assert "workbook: RIC_Q1.xlsx" in body
