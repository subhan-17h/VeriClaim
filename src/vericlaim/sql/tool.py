"""The SQL source's tool boundary: a question in, ``Evidence`` out.

Where the NL2SQL subsystem stops and the rest of the system begins. Nothing downstream
ever sees a plan step, a candidate, a validation verdict or a raw row -- only Evidence,
which is what makes "every material claim traces back to its origin" enforceable rather
than aspirational.

The citation is the load-bearing part. A number about claims data is auditable only if the
reader can see the query that produced it, so the executed SQL travels inside the locator
rather than in a log nobody keeps.

Three things this boundary refuses to blur:

* **Zero rows is a finding, not an absence of evidence.** "There were no such claims" is
  an answer, and it is citable. Returning nothing here would let synthesis read the
  silence as "we did not look".
* **A question the data cannot answer is refused by name.** The planner's answerability
  gate produces a reason and a description of what the tables do cover, and both reach the
  caller instead of being flattened into an empty list.
* **A step the repair loop could not fix fails loudly.** Emitting the steps that did work
  would present a partial answer as a whole one, which looks complete and is not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from vericlaim.config import Settings, get_settings
from vericlaim.evidence import Evidence, Provenance, SqlLocator
from vericlaim.sql.contexts import ContextError, SchemaContext, load_contexts
from vericlaim.sql.observer import ExecutionResult
from vericlaim.sql.pipeline import (
    Executor,
    PipelineOutcome,
    StepOutcome,
    run_pipeline,
)
from vericlaim.sql.planner import PlanError, plan_query
from vericlaim.sql.resolver import EntityResolution, resolve_entities
from vericlaim.sql.values_catalog import Catalog
from vericlaim.tracing import traced

__all__ = (
    "ClaimsQuerier",
    "ClaimsQueryError",
    "QueryFailedError",
    "UnanswerableQuestionError",
    "query_claims_db",
)

TOOL_NAME = "query_claims_db"

# How many rows of a result are written into the evidence a synthesizer reads. Enough to
# support a claim about the data; never the whole result set, which would put the row limit
# itself into the prompt.
EVIDENCE_ROW_LIMIT = 50
EVIDENCE_CELL_LIMIT = 120


class ClaimsQueryError(RuntimeError):
    """Base for every way the claims database can decline to answer."""


class UnanswerableQuestionError(ClaimsQueryError):
    """Raised when the question cannot be answered from the documented tables.

    Carries what is missing and what the data does cover, because "we cannot answer that"
    and "here is what we could answer instead" lead to different next questions, and the
    difference is lost if this degrades to an empty result.
    """

    def __init__(self, reason: str, coverage: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.coverage = coverage


class QueryFailedError(ClaimsQueryError):
    """Raised when a query could not be produced or repaired into a working one."""


@dataclass(frozen=True, slots=True)
class ClaimsQuerier:
    """Answers questions from the claims database, returning citable evidence.

    Constructed with its dependencies rather than reaching for globals, so a test can
    supply a fake executor and an in-memory catalog. :func:`query_claims_db` is the
    settings-driven entry point the orchestrator calls.
    """

    contexts: Mapping[str, SchemaContext]
    catalog: Catalog
    execute: Executor
    settings: Settings
    gateway: Any | None = None

    # Named on the class so a source that shares this machinery -- the spreadsheets do,
    # through the same validator and executor -- is not mislabelled in its own provenance.
    tool_name: ClassVar[str] = TOOL_NAME

    @traced(name="query_claims_db", run_type="tool")
    def query(
        self,
        question: str,
        *,
        understanding: Mapping[str, Any] | None = None,
        tables: Sequence[str] | None = None,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        """Answer one question and return the evidence it produced."""
        outcome = self.run(question, understanding=understanding, tables=tables)
        provenance = Provenance(
            tool=self.tool_name, trace_id=trace_id, query=question
        )
        return self.evidence(outcome, provenance)

    def run(
        self,
        question: str,
        *,
        understanding: Mapping[str, Any] | None = None,
        tables: Sequence[str] | None = None,
    ) -> PipelineOutcome:
        """Plan and run the question, raising rather than returning a partial answer."""
        selected = self._selected(tables)
        resolved = self._grounded(question, understanding)

        try:
            plan = plan_query(
                question,
                understanding or {},
                selected,
                resolved=resolved,
                gateway=self.gateway,
            )
        except PlanError as exc:
            raise QueryFailedError(f"The claims database could not be planned: {exc}") from exc

        if not plan.answerable:
            raise UnanswerableQuestionError(plan.unanswerable_reason, plan.data_coverage)

        outcome = run_pipeline(
            question,
            plan,
            selected,
            catalog=self.catalog,
            execute=self.execute,
            settings=self.settings,
            resolved=resolved,
            gateway=self.gateway,
        )
        if not outcome.ok:
            raise QueryFailedError(_failure_reason(outcome.steps))
        return outcome

    def evidence(
        self, outcome: PipelineOutcome, provenance: Provenance
    ) -> list[Evidence]:
        """One piece of evidence per plan step, citing the query that produced it.

        Overridden by the spreadsheet source, whose citation is a cell range rather than a
        table, and which therefore emits one piece of evidence per row.
        """
        return [_evidence(step, provenance) for step in outcome.steps]

    # -- setup -------------------------------------------------------------

    def _selected(self, tables: Sequence[str] | None) -> dict[str, SchemaContext]:
        """Narrow the documented tables to the ones the router chose.

        An unknown selection is an error rather than a silent drop: quietly narrowing
        would produce SQL that omits the table the router meant, and the answer would be
        incomplete rather than refused.
        """
        if tables is None:
            selected = dict(self.contexts)
        else:
            unknown = sorted(set(tables) - set(self.contexts))
            if unknown:
                raise QueryFailedError(
                    f"No schema context for {', '.join(unknown)}. "
                    f"Known: {', '.join(sorted(self.contexts)) or 'none'}"
                )
            selected = {name: self.contexts[name] for name in tables}

        if not selected:
            raise QueryFailedError(
                "No table in the claims database is documented, so nothing can be queried."
            )
        return selected

    def _grounded(
        self, question: str, understanding: Mapping[str, Any] | None
    ) -> EntityResolution | None:
        """Resolve every run-level mention, but scope refusal to this sub-goal.

        Only an ambiguous mention named by ``question`` stops this source. An out-of-scope
        ambiguity cannot become a filter through grounding because ``stored_values``
        excludes every non-resolved mention from the planner and generator. If such a
        filter is written anyway, the pipeline's ``unresolvable_filters`` check catches
        the empty result and reports that the database holds no such value. An in-scope
        ambiguity still keeps the refusal contract: choosing between matching entities
        remains the user's decision.
        """
        if not understanding:
            return None
        resolved = resolve_entities(understanding, self.catalog, scope=question)
        if resolved.needs_clarification:
            raise UnanswerableQuestionError(resolved.clarification_question)
        return resolved


def _failure_reason(steps: Sequence[StepOutcome]) -> str:
    failed = next((step for step in steps if not step.answered), None)
    if failed is None:
        return "The claims database produced no usable query."
    return (
        f"No working query could be written for: {failed.step.purpose} "
        f"({failed.failure or 'no reason recorded'})"
    )


def _evidence(step: StepOutcome, provenance: Provenance) -> Evidence:
    result = step.result or ExecutionResult(sql=step.sql)
    return Evidence(
        source_type="sql",
        source_id=step.step.table,
        content=_render(step, result),
        locator=SqlLocator(
            tables=step.step.tables,
            executed_sql=step.sql,
            row_count=result.row_count,
        ),
        provenance=provenance,
    )


def _render(step: StepOutcome, result: ExecutionResult) -> str:
    """Write the result as the text a synthesizer reads.

    Deliberately plain: the purpose of the step, then the rows. The executed SQL is not
    repeated here -- it is in the locator, and duplicating it would spend prompt space on
    something the synthesizer must not reason from.
    """
    header = step.step.purpose or "Claims database query"
    if result.row_count == 0:
        note = step.failure or "The query returned no rows."
        return f"{header}\nNo rows. {note}"

    lines = [header, " | ".join(result.columns)]
    lines.extend(
        " | ".join(_cell(value) for value in row)
        for row in result.rows[:EVIDENCE_ROW_LIMIT]
    )
    if result.row_count > EVIDENCE_ROW_LIMIT:
        lines.append(
            f"... {result.row_count} rows in total; "
            f"the first {EVIDENCE_ROW_LIMIT} are shown."
        )
    else:
        lines.append(f"({result.row_count} rows)")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) > EVIDENCE_CELL_LIMIT:
        return f"{text[:EVIDENCE_CELL_LIMIT]}..."
    return text


def query_claims_db(
    question: str,
    *,
    understanding: Mapping[str, Any] | None = None,
    tables: Sequence[str] | None = None,
    trace_id: str | None = None,
) -> list[Evidence]:
    """Answer a question from the claims database and return citable evidence.

    The entry point the orchestrator calls. It builds its dependencies from settings;
    anything that wants to inject them should construct a :class:`ClaimsQuerier`.

    Not traced itself: ``ClaimsQuerier.query`` already emits the span, and wrapping a
    delegating function would put two nested spans in the trace for one tool call.
    """
    settings = get_settings()
    from vericlaim.sql.db import default_database
    from vericlaim.sql.executor import execute as execute_sql
    from vericlaim.sql.values_catalog import database_catalog

    try:
        contexts = load_contexts(settings.sql_context_dir)
    except ContextError as exc:
        raise QueryFailedError(f"The claims database is not documented: {exc}") from exc

    database = default_database(readonly=True, settings=settings)

    def execute(sql: str) -> ExecutionResult:
        return execute_sql(database, sql)

    return ClaimsQuerier(
        contexts=contexts,
        catalog=database_catalog(database, contexts).select(sorted(contexts)),
        execute=execute,
        settings=settings,
    ).query(
        question, understanding=understanding, tables=tables, trace_id=trace_id
    )
