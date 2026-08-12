"""Decide what to compute, from which documented tables, and whether it can be answered.

The planner is the only step allowed to say *no*. Everything downstream -- generation,
validation, execution, repair -- assumes the question can be answered from the tables in
front of it, and each of those steps will do its job faithfully on a question that cannot.
A model asked for a plan always produces one; the answerability gate is what forces it to
map every requested attribute onto a real column first and to refuse when one is missing,
instead of reaching for the nearest column that happens to exist and returning a plausible
number for a different question.

The plan the model returns is then checked here, deterministically, before any SQL exists.
Three checks earn their place:

* **Only documented tables.** A step over a table nobody wrote a context for would be
  rejected by the validator later, but as an opaque failure rather than a repairable one.
* **A step reads its own primary table.** Otherwise the generator's notion of the FROM
  clause and the plan's disagree, silently.
* **A step's tables are connected by declared joins.** This is the check the reference
  implementation could not have: it had one table per context and no joins at all. Two
  tables no chain of declared foreign keys connects do not belong in one step, because
  the only SQL that can combine them is a cross product -- which returns rows, and a
  number, and is wrong.

The prompt is domain-free by design. Every insurance-specific rule lives in the reviewed
context files as a `cautions` entry, and the prompt's job is to make those binding. The
alternative -- restating them here -- gives the same knowledge two homes, and the prompt
is the copy nobody reviews. A test asserts that no table or column of the corpus appears
in the prompt text.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vericlaim.sql.contexts import SchemaContext, context_detail
from vericlaim.sql.resolver import EntityResolution, stored_values

PLANNER_TASK = "sql_planner"

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string"},
                    "table": {"type": "string"},
                    "tables": {
                        "anyOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "null"},
                        ]
                    },
                    "calculations": {"type": "string"},
                },
                "required": ["purpose", "table", "tables", "calculations"],
                "additionalProperties": False,
            },
        },
        "expected_answer_shape": {"type": "string"},
        "answerable": {"type": "boolean"},
        "unanswerable_reason": {"type": "string"},
        "data_coverage": {"type": "string"},
    },
    "required": [
        "steps",
        "expected_answer_shape",
        "answerable",
        "unanswerable_reason",
        "data_coverage",
    ],
    "additionalProperties": False,
}

PLANNER_SYSTEM_PROMPT = """\
You produce a read-only analysis plan for one question over a relational database. You do
not write SQL; a later step writes it from your plan.

Everything you may use is supplied to you. `schema_contexts` describes, column by column,
every table you are permitted to touch. Nothing outside it exists.

ANSWERABILITY GATE.
Before planning, map every attribute the question asks you to filter on, group by, compare
across, or report onto a concrete column in the supplied contexts. If any of them has no
column in any supplied context, set answerable to false, name in unanswerable_reason
exactly which attribute is missing, and describe in one plain sentence in data_coverage
what the supplied tables do cover. Never substitute a different column as a proxy for a
missing attribute, and never plan a constant to stand in for one. An honest refusal is a
correct answer; a plausible number computed from the wrong column is not. When every
requested attribute maps to a real column, set answerable to true, leave
unanswerable_reason and data_coverage empty, and plan.

THE CAUTIONS ARE BINDING.
Each supplied context carries a `cautions` list. These are reviewed distinctions that make
a query wrong in a way that still returns a plausible number: which of two similar columns
answers which phrasing, which quantities must never be added to each other, which join
drops rows. Treat every caution on a table you touch as an instruction. Say in the step's
`calculations` which side of a distinction you took and what in the question put you there.
Where the question does not settle a distinction a caution describes, take the reading the
caution names for that phrasing rather than choosing one silently.

STEPS.
Each step names one supplied table in `table` as its primary source, and lists in `tables`
every supplied table the step reads, including the primary one. Prefer a single step: a
question one query can answer is one step. Add another step only when its calculation
needs the result of an earlier one. Never pass a set of rows from one step to the next --
a result may be truncated by the row limit, so a later step reading it would silently
compute over part of the data. Only an aggregate or a small final result may pass between
steps. Matching, intersecting or excluding whole sets of rows therefore happens inside a
single step, as a join, an IN subquery, or EXISTS.

JOINS.
Only the relationships declared in the contexts' `joins` blocks exist. Every table listed
in one step's `tables` must be reachable from the others by following those declarations
one hop at a time, through tables the same step also lists. Two tables no declared chain
connects do not belong in the same step. Where a declared key is nullable, an inner join
silently drops the unmatched rows: state in `calculations` whether an outer join is
required so those rows survive.

WHAT MAY BE FILTERED.
Every filter must trace to an explicit restriction in the question. Sample values, value
sets, observed ranges, null counts, apparent data quality and convenience never authorize
one. Do not plan null, not-null, trimming or blank-value predicates unless the question
asks about missing or present values. The only exception is a guard the superlative rule
below requires.

COUNTING AND RATIOS.
Plan COUNT(*) for a plain count of rows. Use a distinct count only where the question asks
for distinct or unique entities, or where a join or expansion in the same step can
duplicate the thing being counted. A sequential-looking column, or an identifier-shaped
name, is not evidence of uniqueness on its own. For any proportion, rate or percentage,
plan the numerator and the denominator as projected values beside the ratio, so the answer
can state the counts it rests on.

UNITS.
A column that declares a `unit` may be added to, or compared with, another only when the
unit is identical. Never plan arithmetic mixing a rate with an amount, or two amounts
declared in different units.

SUPERLATIVES.
When the question asks which entity has the most, least, highest, lowest, largest,
smallest, best or worst value, the plan must return every entity tied at that extreme:
compute the per-entity value, then keep the rows whose value equals the maximum or minimum
of it. Do not plan an ordering with a limit of one. Exclude rows whose grouping value is
null before computing the extreme; a null group is not a valid answer to a which-entity
question, and this guard is required rather than an unrequested filter. An ordering with a
limit of N is correct only where the question asks for a top or bottom N.

RESOLVED VALUES.
`resolved_entities` maps what the question said onto the values the database actually
stores. It corrects spelling only. It never chooses which column to filter on and never
widens the scope of the question, and one mention listed under several columns is an
inventory of where that value occurs, not permission to filter on all of them. A step
filtering on a resolved mention must use the stored values exactly, and must filter a
column listed for that mention.

Set expected_answer_shape to one short phrase describing what a correct answer looks like.
If `retry_hint` is not empty it describes what was wrong with your previous plan: correct
exactly that, without widening the plan beyond the question or the supplied contexts.
"""


class PlanError(ValueError):
    """Raised when a returned plan cannot be trusted to be executed.

    Always fatal for the attempt rather than repaired in place: a plan silently corrected
    here would no longer be the plan the model can be told to fix, and the repair loop in
    C-5.8 needs the reason as a retry hint.
    """


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One query's worth of work, named in terms of documented tables."""

    purpose: str
    table: str
    tables: tuple[str, ...]
    calculations: str


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """What to compute, or why it cannot be computed from these tables."""

    answerable: bool
    steps: tuple[PlanStep, ...] = ()
    expected_answer_shape: str = ""
    unanswerable_reason: str = ""
    data_coverage: str = ""


def plan_query(
    question: str,
    understanding: Mapping[str, Any],
    contexts: Mapping[str, SchemaContext],
    *,
    resolved: EntityResolution | None = None,
    retry_hint: str = "",
    gateway: Any | None = None,
) -> QueryPlan:
    """Plan the SQL work for one question over the given documented tables."""
    if not contexts:
        raise PlanError("Planning needs at least one documented table")

    payload = {
        "question": question,
        "understanding": dict(understanding),
        "schema_contexts": [
            context_detail(contexts[name]) for name in sorted(contexts)
        ],
        "resolved_entities": stored_values(resolved),
        "retry_hint": retry_hint,
    }
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]

    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()
    completion = gateway.complete_json(PLANNER_TASK, messages, PLANNER_SCHEMA)
    return _validated(_parse(completion), contexts)


def _parse(completion: Any) -> dict[str, Any]:
    """Read the structured plan out of a gateway completion."""
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PlanError(f"Planner returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise PlanError("Planner returned a non-object plan")
    return dict(parsed)


def _validated(
    raw: Mapping[str, Any], contexts: Mapping[str, SchemaContext]
) -> QueryPlan:
    answerable = bool(raw.get("answerable"))
    reason = str(raw.get("unanswerable_reason") or "").strip()

    if not answerable:
        if not reason:
            raise PlanError(
                "Planner declined the question without a reason naming what is missing"
            )
        return QueryPlan(
            answerable=False,
            unanswerable_reason=reason,
            data_coverage=str(raw.get("data_coverage") or "").strip(),
            expected_answer_shape=str(raw.get("expected_answer_shape") or "").strip(),
        )

    raw_steps = raw.get("steps") or []
    if not raw_steps:
        raise PlanError("Planner returned no steps for a question it called answerable")

    adjacency = _join_graph(contexts)
    steps = tuple(_step(entry, contexts, adjacency) for entry in raw_steps)
    return QueryPlan(
        answerable=True,
        steps=steps,
        expected_answer_shape=str(raw.get("expected_answer_shape") or "").strip(),
    )


def _step(
    entry: Any,
    contexts: Mapping[str, SchemaContext],
    adjacency: Mapping[str, set[str]],
) -> PlanStep:
    if not isinstance(entry, Mapping):
        raise PlanError("Planner returned a step that is not an object")

    primary = str(entry.get("table") or "").strip()
    if primary not in contexts:
        raise PlanError(f"Planner used an undocumented table: {primary or '(unnamed)'}")

    declared = entry.get("tables")
    # A step that names no tables reads only its primary one. Defaulting the other way --
    # every selected table available -- would let one under-specified step quietly widen
    # the allow-list the validator is built from.
    tables = tuple(sorted({primary, *(str(name) for name in declared or ())}))

    undocumented = sorted(set(tables) - set(contexts))
    if undocumented:
        raise PlanError(
            f"Planner step reads undocumented table(s): {', '.join(undocumented)}"
        )
    if declared and primary not in {str(name) for name in declared}:
        raise PlanError(
            f"Planner step omitted its primary table {primary} from the tables it reads"
        )

    unreachable = _unreachable(primary, tables, adjacency)
    if unreachable:
        raise PlanError(
            f"Planner step joins {', '.join(unreachable)} to {primary}, which no "
            "declared join connects. Split the step or route it through the table that "
            "does connect them."
        )

    return PlanStep(
        purpose=str(entry.get("purpose") or "").strip(),
        table=primary,
        tables=tables,
        calculations=str(entry.get("calculations") or "").strip(),
    )


def _join_graph(contexts: Mapping[str, SchemaContext]) -> dict[str, set[str]]:
    """Build the undirected graph of declared foreign keys between documented tables."""
    adjacency: dict[str, set[str]] = {name: set() for name in contexts}
    for name, context in contexts.items():
        for join in context.joins:
            target = join.target_table
            if target in adjacency:
                adjacency[name].add(target)
                adjacency[target].add(name)
    return adjacency


def _unreachable(
    primary: str, tables: tuple[str, ...], adjacency: Mapping[str, set[str]]
) -> tuple[str, ...]:
    """Return the step's tables not reachable from its primary one within the step.

    Reachability is confined to the tables the step itself lists: a path through a table
    the step does not read is not a path the step's SQL can take.
    """
    within = set(tables)
    seen = {primary}
    queue = deque([primary])
    while queue:
        for neighbour in adjacency.get(queue.popleft(), ()):
            if neighbour in within and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return tuple(sorted(within - seen))
