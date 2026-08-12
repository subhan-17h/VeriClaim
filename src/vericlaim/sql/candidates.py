"""Choose between several queries that all ran.

Generating one query and repairing it until it stops erroring finds queries that *work*.
It does not find queries that are *right*: a query that counts the wrong rows returns a
number, cleanly, every time, and no error message is ever produced. Writing the step
several ways and comparing what came back is the only cheap signal available about which
one understood the question.

Three stages, in increasing order of cost:

1. **Viability**, free. A candidate the validator rejected, the database refused, or the
   observer found to contradict the data is not a contender. Letting it into a vote would
   give a query already known to be wrong a say in which query is right.
2. **Grouping**, free. Candidates that returned the same rows are one answer written
   twice. Row order and numeric spelling are collapsed, because they were never
   disagreement. If one group survives, there is nothing to arbitrate and no model is
   asked anything.
3. **Arbitration**, one call per assertion. Only genuine disagreement earns it.

When arbitration cannot be reached -- an exhausted quota, a refused paid rung, a garbled
answer -- the deterministic choice stands and the selection *says so*. The implementation
this adapts swallowed that failure without a word, so a system whose arbitration had
stopped working looked exactly like one where it had never disagreed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from vericlaim.sql.contexts import SchemaContext
from vericlaim.sql.observer import ExecutionResult, Observation
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.resolver import EntityResolution
from vericlaim.sql.unit_tester import (
    conventions_for,
    evaluate_unit_test,
    generate_unit_tests,
)

logger = logging.getLogger(__name__)

# Verdicts a candidate may hold and still be worth considering. An empty result is kept:
# it may well be the answer, and the assertions are the mechanism for deciding whether it
# is or whether a filter was too narrow.
VIABLE_VERDICTS = frozenset({"ok", "empty_result"})
# How many rows of a group the arbiter is shown. Enough to see the shape of the answer,
# few enough that four groups do not fill the context window.
GROUP_ROW_LIMIT = 5


@dataclass(frozen=True, slots=True)
class Candidate:
    """One generated query, what it returned, and what the observer made of it."""

    sql: str
    style: str
    result: ExecutionResult
    observation: Observation


@dataclass(frozen=True, slots=True)
class Selection:
    """The query chosen, and an account of how -- for the trace and the answer."""

    sql: str
    reason: str
    candidates: int = 0
    clusters: int = 0
    tests: tuple[str, ...] = ()
    scores: tuple[int, ...] = ()
    detail: str = ""


def select(
    question: str,
    step: PlanStep,
    candidates: Sequence[Candidate],
    contexts: Mapping[str, SchemaContext],
    *,
    unit_test_count: int,
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
) -> Selection:
    """Pick the candidate most likely to have answered the question."""
    viable = [
        candidate
        for candidate in candidates
        if not candidate.result.error
        and candidate.observation.verdict in VIABLE_VERDICTS
    ]
    if not viable:
        return Selection(sql="", reason="no_candidates", candidates=0)

    clusters = _clusters(viable)
    shortest = [min(cluster, key=lambda item: len(item.sql)) for cluster in clusters]

    if len(clusters) == 1:
        return Selection(
            sql=shortest[0].sql,
            reason="single_cluster",
            candidates=len(viable),
            clusters=1,
        )

    deterministic = Selection(
        sql=_largest(clusters).sql,
        reason="largest_cluster",
        candidates=len(viable),
        clusters=len(clusters),
    )
    if unit_test_count < 1:
        return deterministic

    groups = [_summary(cluster) for cluster in clusters]
    conventions = conventions_for(contexts, step.tables)
    try:
        tests = generate_unit_tests(
            question,
            step,
            groups,
            conventions,
            unit_test_count,
            resolved=resolved,
            gateway=gateway,
        )
        scores = [0] * len(clusters)
        for test in tests:
            verdicts = evaluate_unit_test(
                question,
                test,
                groups,
                conventions,
                resolved=resolved,
                gateway=gateway,
            )
            for index, verdict in enumerate(verdicts):
                if verdict == "pass":
                    scores[index] += 1
    except Exception as exc:  # noqa: BLE001 - quota, budget, outages and garbled answers
        # Deliberately broad, and deliberately loud. Arbitration is an improvement on the
        # deterministic choice, never a requirement, so no failure of it may end a run --
        # but every failure of it has to be visible, in the log and in the selection.
        logger.warning("Candidate arbitration unavailable: %s", exc)
        return Selection(
            sql=deterministic.sql,
            reason="arbitration_unavailable",
            candidates=len(viable),
            clusters=len(clusters),
            detail=str(exc),
        )

    if not tests:
        return deterministic

    winner = min(
        range(len(clusters)),
        key=lambda index: (
            -scores[index],
            # A tie on assertions falls back to the larger group. Two queries agreeing is
            # weak evidence, but it is evidence, and it beats picking at random.
            -len(clusters[index]),
            len(shortest[index].sql),
        ),
    )
    return Selection(
        sql=shortest[winner].sql,
        reason="unit_test_winner",
        candidates=len(viable),
        clusters=len(clusters),
        tests=tests,
        scores=tuple(scores),
    )


def _clusters(candidates: Sequence[Candidate]) -> list[list[Candidate]]:
    """Group candidates by the answer they produced, in a stable order."""
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_answer_key(candidate.result), []).append(candidate)
    return [grouped[key] for key in sorted(grouped)]


def _largest(clusters: Sequence[Sequence[Candidate]]) -> Candidate:
    ranked = sorted(
        clusters,
        key=lambda cluster: (
            -len(cluster),
            min(len(candidate.sql) for candidate in cluster),
        ),
    )
    return min(ranked[0], key=lambda candidate: len(candidate.sql))


def _answer_key(result: ExecutionResult) -> str:
    """Canonicalize a result into the answer it represents.

    Row order is dropped and numbers are normalized, because neither is disagreement:
    Postgres returns 70000 and 70000.00 for the same money depending on the expression,
    and two queries differing only in an ORDER BY gave the same answer.
    """
    rows = sorted(
        json.dumps([_canonical(value) for value in row], default=str)
        for row in result.rows
    )
    return json.dumps(rows)


def _canonical(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, Decimal):
        try:
            return str(value.normalize())
        except InvalidOperation:
            return str(value)
    return value


def _summary(cluster: Sequence[Candidate]) -> dict[str, Any]:
    """Describe one group to the arbiter: its shortest query and a few of its rows."""
    representative = min(cluster, key=lambda candidate: len(candidate.sql))
    result = representative.result
    return {
        "sql": representative.sql,
        "columns": list(result.columns),
        "row_count": result.row_count,
        "rows": [list(row) for row in result.rows[:GROUP_ROW_LIMIT]],
        "agreeing_candidates": len(cluster),
    }
