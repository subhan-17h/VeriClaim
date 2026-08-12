"""The spreadsheet source's tool boundary: a question in, cell-citable ``Evidence`` out.

The spreadsheets run through the same planner, validator, executor and repair loop as the
claims database -- one audited path, not two. What makes them a distinct source is the
citation: a row of an ingested sheet can be pointed at, workbook › sheet › row › A1 range,
and the range genuinely holds the value in the file.

That is why the evidence is per row here rather than per query. A citation that named only
the table would be no better than SQL, and the fourth source would have collapsed into the
second.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from vericlaim.config import Settings
from vericlaim.evidence import SpreadsheetLocator
from vericlaim.sheets.tool import SpreadsheetQuerier
from vericlaim.sql.contexts import load_contexts
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.tool import UnanswerableQuestionError
from vericlaim.sql.values_catalog import StaticCatalog

TABLE = "sheets.regional_inspection_compliance_q1__compliance"

PLAN = {
    "steps": [
        {
            "purpose": "List compliance by region.",
            "table": TABLE,
            "tables": [TABLE],
            "calculations": "Every row, with its lineage.",
        }
    ],
    "expected_answer_shape": "A row per region.",
    "answerable": True,
    "unanswerable_reason": "",
    "data_coverage": "",
}

ROW_SQL = (
    f"SELECT region, compliance_rate, _workbook, _sheet, _row, _a1_range FROM {TABLE}"
)


@pytest.fixture(scope="module")
def contexts():
    return load_contexts(Settings().sheets_context_dir)


@dataclass
class FakeGateway:
    plan: dict[str, Any]
    sql: str
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
    result: ExecutionResult
    executed: list[str] = field(default_factory=list)

    def __call__(self, sql: str) -> ExecutionResult:
        self.executed.append(sql)
        return ExecutionResult(
            sql=sql,
            columns=self.result.columns,
            rows=self.result.rows,
            error=self.result.error,
        )


ROW_RESULT = ExecutionResult(
    sql="",
    columns=("region", "compliance_rate", "_workbook", "_sheet", "_row", "_a1_range"),
    rows=(
        (
            "Lahore",
            Decimal("0.82"),
            "Regional_Inspection_Compliance_Q1.xlsx",
            "Compliance",
            4,
            "A4:E4",
        ),
        (
            "Karachi",
            Decimal("0.61"),
            "Regional_Inspection_Compliance_Q1.xlsx",
            "Compliance",
            5,
            "A5:E5",
        ),
    ),
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


def querier(contexts, gateway, database):
    return SpreadsheetQuerier(
        contexts=contexts,
        catalog=StaticCatalog({}),
        execute=database,
        settings=settings(),
        gateway=gateway,
    )


def ask(contexts, result: ExecutionResult, sql: str = ROW_SQL, plan=None):
    gateway = FakeGateway(plan or PLAN, sql)
    return querier(contexts, gateway, FakeDatabase(result)).query(
        "How is compliance by region?"
    )


# ------------------------------------------------------------------ cell citation


def test_each_row_becomes_its_own_piece_of_evidence(contexts) -> None:
    assert len(ask(contexts, ROW_RESULT)) == 2


def test_a_row_is_cited_down_to_the_cells_it_came_from(contexts) -> None:
    """The reason spreadsheets are a distinct source rather than more SQL."""
    locator = ask(contexts, ROW_RESULT)[0].locator

    assert isinstance(locator, SpreadsheetLocator)
    assert locator.workbook == "Regional_Inspection_Compliance_Q1.xlsx"
    assert locator.sheet == "Compliance"
    assert locator.row == 4
    assert locator.a1_range == "A4:E4"


def test_the_citation_reads_the_way_a_person_would_write_it(contexts) -> None:
    citation = ask(contexts, ROW_RESULT)[0].cite()

    assert citation == (
        "Regional_Inspection_Compliance_Q1.xlsx › Compliance › row 4 (A4:E4)"
    )


def test_evidence_is_tagged_as_coming_from_a_spreadsheet(contexts) -> None:
    """Not "sql", even though the same engine ran the query: the four sources stay four."""
    assert ask(contexts, ROW_RESULT)[0].source_type == "spreadsheet"


def test_the_tool_names_itself_in_the_provenance(contexts) -> None:
    assert ask(contexts, ROW_RESULT)[0].provenance.tool == "query_spreadsheets"


def test_the_values_are_readable_without_the_lineage_getting_in_the_way(contexts) -> None:
    """Synthesis reads this. The lineage columns are the citation, not part of the finding,
    and repeating them in the text invites the model to report a row number as a result."""
    content = ask(contexts, ROW_RESULT)[0].content

    assert "region" in content and "Lahore" in content
    assert "_a1_range" not in content
    assert "A4:E4" not in content


# ------------------------------------------------------------------ aggregates


AGGREGATE_RESULT = ExecutionResult(
    sql="",
    columns=("_workbook", "_sheet", "average"),
    rows=(("Regional_Inspection_Compliance_Q1.xlsx", "Compliance", Decimal("0.715")),),
)

AGGREGATE_PLAN = {
    **PLAN,
    "steps": [
        {
            **PLAN["steps"][0],
            "purpose": "Average compliance.",
            "calculations": "The average of compliance_rate across all rows.",
        }
    ],
}


def test_an_aggregate_cites_the_sheet_rather_than_inventing_a_row(contexts) -> None:
    """No single cell holds an average. Naming one would be a citation that does not check
    out against the file, which is worse than a coarser one that does."""
    evidence = ask(
        contexts,
        AGGREGATE_RESULT,
        sql=f"SELECT _workbook, _sheet, AVG(compliance_rate) FROM {TABLE} "
        "GROUP BY _workbook, _sheet",
        plan=AGGREGATE_PLAN,
    )

    assert len(evidence) == 1
    assert evidence[0].locator.row is None
    assert evidence[0].cite() == (
        "Regional_Inspection_Compliance_Q1.xlsx › Compliance"
    )


def test_an_aggregate_that_dropped_the_lineage_still_cites_its_workbook(contexts) -> None:
    """The context knows which workbook the table came from even when the query forgot to
    project it, so the answer is never left with an uncitable number."""
    evidence = ask(
        contexts,
        ExecutionResult(sql="", columns=("average",), rows=((Decimal("0.715"),),)),
        sql=f"SELECT AVG(compliance_rate) FROM {TABLE}",
        plan=AGGREGATE_PLAN,
    )

    assert evidence[0].locator.workbook == "Regional_Inspection_Compliance_Q1.xlsx"
    assert evidence[0].locator.row is None


# ------------------------------------------------------------------ limits


def test_a_long_result_does_not_become_hundreds_of_citations(contexts) -> None:
    """Every row is citable; not every row belongs in a prompt. Past the limit the rows
    are reported together rather than individually cited."""
    rows = tuple(
        (
            f"Region {index}",
            Decimal("0.5"),
            "Regional_Inspection_Compliance_Q1.xlsx",
            "Compliance",
            index + 4,
            f"A{index + 4}:E{index + 4}",
        )
        for index in range(200)
    )

    evidence = ask(contexts, ExecutionResult(sql="", columns=ROW_RESULT.columns, rows=rows))

    assert len(evidence) == 1
    assert "200" in evidence[0].content
    assert evidence[0].locator.row is None


def test_a_question_the_sheets_cannot_answer_is_still_refused_by_name(contexts) -> None:
    plan = {
        **PLAN,
        "steps": [],
        "answerable": False,
        "unanswerable_reason": "No column records the inspector's name.",
        "data_coverage": "The sheets cover compliance by region.",
    }

    with pytest.raises(UnanswerableQuestionError, match="inspector"):
        ask(contexts, ROW_RESULT, plan=plan)
