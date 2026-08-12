"""The spreadsheet source's tool boundary: a question in, cell-citable ``Evidence`` out.

The spreadsheets run through the same planner, validator, executor, observer and repair
loop as the claims database. One audited path, not two -- a second SQL engine for the
sheets would be a second thing to keep safe, and the guarantees would drift apart the
first time only one of them was fixed.

What makes this a distinct source is the citation. A row of an ingested sheet can be
pointed at -- workbook › sheet › row › A1 range -- and the range genuinely holds the value
in the file. That is why evidence here is produced **per row** rather than per query: a
citation naming only the table would be no better than SQL, and the fourth source would
have quietly collapsed into the second.

Three cases, and the honest thing in each:

* **Rows with lineage.** One piece of evidence per row, cited to its cells.
* **An aggregate.** No single cell holds an average, so the citation names the sheet.
  Inventing a row would produce a citation that does not check out against the file,
  which is worse than a coarser one that does.
* **Too many rows.** Every row is citable; not every row belongs in a prompt. Past the
  limit they are reported together, cited to the sheet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from vericlaim.evidence import Evidence, Provenance, SpreadsheetLocator
from vericlaim.sql.contexts import (
    LINEAGE_COLUMN_NAMES,
    ContextError,
    SchemaContext,
    load_contexts,
)
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.pipeline import PipelineOutcome, StepOutcome
from vericlaim.sql.tool import ClaimsQuerier, QueryFailedError
from vericlaim.tracing import traced

__all__ = ("SpreadsheetQuerier", "query_spreadsheets")

TOOL_NAME = "query_spreadsheets"

# How many rows are cited individually. Beyond this the finding is about the shape of the
# result rather than about any particular row, and two hundred citations would crowd every
# other source out of the answer.
MAX_CITED_ROWS = 25
CELL_LIMIT = 120


@dataclass(frozen=True, slots=True)
class SpreadsheetQuerier(ClaimsQuerier):
    """Answers questions from the ingested workbooks, citing the cells it read."""

    tool_name: ClassVar[str] = TOOL_NAME

    @traced(name="query_spreadsheets", run_type="tool")
    def query(
        self,
        question: str,
        *,
        understanding: Mapping[str, Any] | None = None,
        tables: Sequence[str] | None = None,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        # Called explicitly rather than through super(). `slots=True` rebuilds the class
        # after the method is compiled, so the zero-argument form closes over a class that
        # is no longer this one and raises at call time.
        return ClaimsQuerier.query(
            self,
            question,
            understanding=understanding,
            tables=tables,
            trace_id=trace_id,
        )

    def evidence(
        self, outcome: PipelineOutcome, provenance: Provenance
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for step in outcome.steps:
            evidence.extend(self._step_evidence(step, provenance))
        return evidence

    def _step_evidence(
        self, step: StepOutcome, provenance: Provenance
    ) -> list[Evidence]:
        result = step.result or ExecutionResult(sql=step.sql)
        lineage = _lineage_positions(result.columns)
        source = self.contexts.get(step.step.table)

        if not lineage or result.row_count == 0 or result.row_count > MAX_CITED_ROWS:
            return [_summary_evidence(step, result, source, provenance)]

        visible = [
            index
            for index, column in enumerate(result.columns)
            if column not in LINEAGE_COLUMN_NAMES
        ]
        return [
            Evidence(
                source_type="spreadsheet",
                source_id=step.step.table,
                content=_row_content(step, result, row, visible),
                locator=_row_locator(row, lineage, source),
                provenance=provenance,
            )
            for row in result.rows
        ]


def _lineage_positions(columns: Sequence[str]) -> dict[str, int]:
    return {
        column: index
        for index, column in enumerate(columns)
        if column in LINEAGE_COLUMN_NAMES
    }


def _row_locator(
    row: Sequence[Any], lineage: Mapping[str, int], source: SchemaContext | None
) -> SpreadsheetLocator:
    """Build the citation from the row's own lineage, falling back to the context.

    The row is the better source -- it is what the database actually holds -- but a query
    that projected only some of the lineage columns must still produce a citation, so the
    reviewed context fills the gaps rather than leaving a number nobody can point at.
    """

    workbook = _value(row, lineage, "_workbook") or (source.workbook if source else None)
    sheet = _value(row, lineage, "_sheet") or (source.sheet if source else None)
    number = _value(row, lineage, "_row")
    return SpreadsheetLocator(
        workbook=str(workbook or "unknown workbook"),
        sheet=str(sheet or "unknown sheet"),
        row=int(number) if number is not None else None,
        a1_range=_value(row, lineage, "_a1_range"),
    )


def _value(row: Sequence[Any], lineage: Mapping[str, int], name: str) -> Any:
    index = lineage.get(name)
    return row[index] if index is not None and index < len(row) else None


def _row_content(
    step: StepOutcome, result: ExecutionResult, row: Sequence[Any], visible: Sequence[int]
) -> str:
    """Render one row for synthesis, without its lineage.

    The lineage is the citation, not part of the finding. Repeating it in the text invites
    a model to report a row number as though it were a result.
    """
    header = step.step.purpose or "Spreadsheet row"
    fields = ", ".join(
        f"{result.columns[index]}: {_cell(row[index])}" for index in visible
    )
    return f"{header}\n{fields}"


def _summary_evidence(
    step: StepOutcome,
    result: ExecutionResult,
    source: SchemaContext | None,
    provenance: Provenance,
) -> Evidence:
    """One piece of evidence for a result no single cell holds."""
    lineage = _lineage_positions(result.columns)
    first = result.rows[0] if result.rows else ()
    # Deliberately no row and no range: an aggregate belongs to the sheet, not to whichever
    # row happened to come back first, and citing that row would not check out.
    locator = SpreadsheetLocator(
        workbook=_value(first, lineage, "_workbook")
        or (source.workbook if source else None)
        or "unknown workbook",
        sheet=_value(first, lineage, "_sheet")
        or (source.sheet if source else None)
        or "unknown sheet",
    )

    return Evidence(
        source_type="spreadsheet",
        source_id=step.step.table,
        content=_summary_content(step, result),
        locator=locator,
        provenance=provenance,
    )


def _summary_content(step: StepOutcome, result: ExecutionResult) -> str:
    header = step.step.purpose or "Spreadsheet query"
    if result.row_count == 0:
        return f"{header}\nNo rows. {step.failure or 'The query returned no rows.'}"

    visible = [
        index
        for index, column in enumerate(result.columns)
        if column not in LINEAGE_COLUMN_NAMES
    ]
    lines = [header, " | ".join(result.columns[index] for index in visible)]
    lines.extend(
        " | ".join(_cell(row[index]) for index in visible)
        for row in result.rows[:MAX_CITED_ROWS]
    )
    if result.row_count > MAX_CITED_ROWS:
        lines.append(
            f"... {result.row_count} rows in total; the first {MAX_CITED_ROWS} are shown."
        )
    else:
        lines.append(f"({result.row_count} rows)")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"{text[:CELL_LIMIT]}..." if len(text) > CELL_LIMIT else text


def query_spreadsheets(
    question: str,
    *,
    understanding: Mapping[str, Any] | None = None,
    tables: Sequence[str] | None = None,
    trace_id: str | None = None,
) -> list[Evidence]:
    """Answer a question from the ingested workbooks and return cell-citable evidence.

    The entry point the orchestrator calls. It builds its dependencies from settings;
    anything that wants to inject them should construct a :class:`SpreadsheetQuerier`.
    """
    from vericlaim.config import get_settings
    from vericlaim.sql.db import default_database
    from vericlaim.sql.executor import execute as execute_sql
    from vericlaim.sql.values_catalog import database_catalog

    settings = get_settings()
    try:
        contexts = load_contexts(settings.sheets_context_dir)
    except ContextError as exc:
        raise QueryFailedError(f"The spreadsheets are not documented: {exc}") from exc

    database = default_database(readonly=True, settings=settings)

    def execute(sql: str) -> ExecutionResult:
        return execute_sql(database, sql)

    return SpreadsheetQuerier(
        contexts=contexts,
        catalog=database_catalog(database, contexts).select(sorted(contexts)),
        execute=execute,
        settings=settings,
    ).query(
        question, understanding=understanding, tables=tables, trace_id=trace_id
    )
