"""Machine-refreshed statistics for the schema contexts.

The semantics in a context are hand-authored; the numbers are not. Counts, sample values
and low-cardinality value sets are read from the live database, because a hand-maintained
distinct count is wrong the moment the corpus is regenerated and nothing announces it.

The refresh is also the contract check between the contexts and the database: a column in
one and not the other is a failure, in both directions.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from vericlaim.sql.contexts import ColumnStats, ContextError, load_contexts
from vericlaim.sql.db import Database
from vericlaim.sql.profiler import (
    ColumnProfile,
    dump_context,
    profile_table,
    refresh_context,
    refresh_context_file,
    refresh_contexts,
)

PROBE = "profiler_probe"

CONTEXT_YAML = """
# Hand-authored and reviewed.
# Second header line.
schema: ops
table: profiler_probe
purpose: A probe table.
columns:
  - name: claim_id
    type: bigint
    meaning: Surrogate key.
  - name: peril
    type: text
    meaning: Cause of loss as classified at notification.
  - name: incurred_amount_pkr
    type: numeric
    meaning: Estimated ultimate cost.
    unit: PKR
useful_for:
  - Counting probes.
cautions:
  - Incurred is not paid.
"""


def profile(name: str, **overrides) -> ColumnProfile:
    defaults = {
        "name": name,
        "type": "text",
        "sample_values": ("a", "b"),
        "stats": ColumnStats(total_count=10, distinct_count=2, null_count=1),
        "value_set": None,
    }
    return ColumnProfile(**{**defaults, **overrides})


@pytest.fixture
def context(tmp_path: Path):
    (tmp_path / "ops.profiler_probe.yaml").write_text(
        textwrap.dedent(CONTEXT_YAML).lstrip(), encoding="utf-8"
    )
    return load_contexts(tmp_path)["ops.profiler_probe"]


@pytest.fixture
def observed() -> dict[str, ColumnProfile]:
    return {
        "claim_id": profile("claim_id", type="bigint"),
        "peril": profile("peril", value_set=("fire", "water_damage")),
        "incurred_amount_pkr": profile("incurred_amount_pkr", type="numeric"),
    }


# ------------------------------------------------------------------- merging


def test_statistics_are_merged_into_the_documented_column(context, observed) -> None:
    refreshed = refresh_context(context, observed)
    peril = next(column for column in refreshed.columns if column.name == "peril")

    assert peril.stats == ColumnStats(total_count=10, distinct_count=2, null_count=1)
    assert peril.value_set == ("fire", "water_damage")


def test_the_hand_authored_semantics_survive_a_refresh(context, observed) -> None:
    """The whole file is rewritten, so anything a human wrote has to come through."""
    refreshed = refresh_context(context, observed)
    incurred = next(
        column for column in refreshed.columns if column.name == "incurred_amount_pkr"
    )

    assert incurred.meaning == "Estimated ultimate cost."
    assert incurred.unit == "PKR"
    assert refreshed.cautions == ("Incurred is not paid.",)
    assert refreshed.purpose == "A probe table."


def test_a_column_the_database_has_and_nobody_documented_is_an_error(context, observed) -> None:
    """The contract check against the corpus. An undocumented column is invisible to the
    planner, so it is unusable and the drift has to be noticed here."""
    observed["settlement_verdict"] = profile("settlement_verdict")

    with pytest.raises(ContextError, match="settlement_verdict"):
        refresh_context(context, observed)


def test_a_documented_column_the_database_lacks_is_an_error(context, observed) -> None:
    """The other direction: SQL written from this context would fail at execution."""
    del observed["peril"]

    with pytest.raises(ContextError, match="peril"):
        refresh_context(context, observed)


# --------------------------------------------------------------- writing back


def test_a_refreshed_context_round_trips_through_the_loader(context, observed, tmp_path) -> None:
    """Serialization has to produce something the loader accepts, or the refresh
    silently breaks every context it touches."""
    written = tmp_path / "out" / "ops.profiler_probe.yaml"
    written.parent.mkdir()
    written.write_text(dump_context(refresh_context(context, observed)), encoding="utf-8")

    reloaded = load_contexts(written.parent)["ops.profiler_probe"]

    assert reloaded.cautions == context.cautions
    assert [column.meaning for column in reloaded.columns] == [
        column.meaning for column in context.columns
    ]
    assert reloaded.columns[1].value_set == ("fire", "water_damage")


def test_refreshing_a_file_preserves_its_header_comment(tmp_path, observed) -> None:
    """The header says the file is hand-reviewed; a rewrite that drops it invites the
    next reader to treat the whole file as generated."""
    path = tmp_path / "ops.profiler_probe.yaml"
    path.write_text(textwrap.dedent(CONTEXT_YAML).lstrip(), encoding="utf-8")

    refresh_context_file(path, lambda schema, table: observed)

    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Hand-authored and reviewed.\n# Second header line.\n")


def test_refreshing_a_file_leaves_it_loadable(tmp_path, observed) -> None:
    path = tmp_path / "ops.profiler_probe.yaml"
    path.write_text(textwrap.dedent(CONTEXT_YAML).lstrip(), encoding="utf-8")

    refresh_context_file(path, lambda schema, table: observed)

    assert "ops.profiler_probe" in load_contexts(tmp_path)


def test_a_failure_on_one_table_leaves_every_file_untouched(tmp_path, observed) -> None:
    """A refresh that stops halfway would leave the committed contexts describing two
    different databases, which is worse than describing a stale one."""
    first = tmp_path / "ops.profiler_probe.yaml"
    first.write_text(textwrap.dedent(CONTEXT_YAML).lstrip(), encoding="utf-8")
    second = tmp_path / "ops.second.yaml"
    second.write_text(
        textwrap.dedent(
            """
            schema: ops
            table: second
            purpose: Another probe.
            columns:
              - name: id
                type: bigint
                meaning: Surrogate key.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    before = {path: path.read_text(encoding="utf-8") for path in (first, second)}

    def profiler(schema: str, table: str):
        if table == "second":
            raise ContextError("ops.second is not in the database")
        return observed

    with pytest.raises(ContextError, match="ops.second"):
        refresh_contexts(tmp_path, profiler)

    assert {path: path.read_text(encoding="utf-8") for path in (first, second)} == before


# ------------------------------------------------------------------ live


@pytest.fixture
def probe_table(settings) -> Iterator[Database]:
    """A real table in ops, created and dropped by the test.

    The corpus arrives in C-8.1; until then this proves the profiler against real
    Postgres types rather than against a mock of them.
    """
    admin = Database(settings.dsn(readonly=False), statement_timeout_ms=10_000)
    with admin.connection() as conn:
        conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        conn.execute(
            f"""
            CREATE TABLE ops.{PROBE} (
                claim_id bigint PRIMARY KEY,
                peril text,
                incurred_amount_pkr numeric,
                report_date date
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO ops.{PROBE} VALUES
                (1, 'water_damage', 25000, DATE '2026-03-01'),
                (2, 'fire', 90000, DATE '2026-03-15'),
                (3, 'water_damage', 12500, DATE '2026-01-20'),
                (4, NULL, NULL, DATE '2026-02-02')
            """
        )
    try:
        yield admin
    finally:
        with admin.connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS ops.{PROBE}")
        admin.close()


@pytest.mark.postgres
def test_the_profile_counts_rows_distincts_and_nulls(probe_table) -> None:
    profiles = profile_table(probe_table, "ops", PROBE)

    peril = profiles["peril"]
    assert peril.stats.total_count == 4
    assert peril.stats.distinct_count == 2
    assert peril.stats.null_count == 1


@pytest.mark.postgres
def test_a_low_cardinality_text_column_gets_its_value_set(probe_table) -> None:
    """These are what let the generator write `peril = 'water_damage'` rather than
    guessing a spelling the corpus does not use."""
    profiles = profile_table(probe_table, "ops", PROBE)

    assert profiles["peril"].value_set == ("fire", "water_damage")


@pytest.mark.postgres
def test_a_key_column_does_not_get_a_value_set(probe_table) -> None:
    """One value per row is not a vocabulary, and dumping it into a prompt is expensive
    and useless."""
    profiles = profile_table(probe_table, "ops", PROBE)

    assert profiles["claim_id"].value_set is None


@pytest.mark.postgres
def test_numeric_and_date_columns_report_their_range(probe_table) -> None:
    """The range is what tells the planner the corpus covers Jan-Jun 2026, so a question
    about last year can be refused rather than answered with an empty result."""
    profiles = profile_table(probe_table, "ops", PROBE)

    assert profiles["incurred_amount_pkr"].stats.minimum == "12500"
    assert profiles["incurred_amount_pkr"].stats.maximum == "90000"
    assert profiles["report_date"].stats.minimum == "2026-01-20"


@pytest.mark.postgres
def test_the_column_types_come_from_the_database(probe_table) -> None:
    profiles = profile_table(probe_table, "ops", PROBE)

    assert profiles["claim_id"].type == "bigint"
    assert profiles["incurred_amount_pkr"].type == "numeric"


@pytest.mark.postgres
def test_profiling_an_unknown_table_is_an_error(probe_table) -> None:
    with pytest.raises(ContextError, match="ops.no_such_table"):
        profile_table(probe_table, "ops", "no_such_table")


@pytest.mark.postgres
def test_a_context_file_refreshes_against_the_real_database(probe_table, tmp_path) -> None:
    """End to end: hand-authored semantics in, live statistics merged, file still valid."""
    path = tmp_path / "ops.profiler_probe.yaml"
    body = textwrap.dedent(CONTEXT_YAML).lstrip().replace(
        "  - name: incurred_amount_pkr",
        "  - name: report_date\n    type: date\n    meaning: When it was notified.\n"
        "  - name: incurred_amount_pkr",
    )
    path.write_text(body, encoding="utf-8")

    refresh_context_file(path, lambda schema, table: profile_table(probe_table, schema, table))

    reloaded = load_contexts(tmp_path)["ops.profiler_probe"]
    peril = next(column for column in reloaded.columns if column.name == "peril")
    assert peril.meaning.startswith("Cause of loss")
    assert peril.value_set == ("fire", "water_damage")
    assert peril.stats.null_count == 1
