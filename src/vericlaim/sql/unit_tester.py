"""Write assertions that tell two disagreeing queries apart, then grade them.

When several candidate queries return different answers, at most one of them is right and
nothing about the SQL text says which. This asks a model to do the one thing it is good at
here: state, in English, a property the correct query must have -- *the correct SQL should
count every row rather than every distinct value* -- and then judge each candidate against
it. The assertion is about query logic, never about formatting, aliases or row order,
because those are the differences that were already collapsed before anything got here.

The conventions the assertions must respect are not written in this file. They are the
`cautions` of the tables the step reads, hand-authored and reviewed in the context files.
A list baked in here would describe whatever schema it was written against and would
quietly mis-arbitrate everything else -- which is exactly the state the reference
implementation was in, its conventions describing supervisors and roll numbers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from vericlaim.sql.contexts import SchemaContext
from vericlaim.sql.planner import PlanStep
from vericlaim.sql.resolver import EntityResolution, stored_values

UNIT_TESTER_TASK = "sql_unit_tester"

GENERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tests": {"type": "array", "items": {"type": "string"}}},
    "required": ["tests"],
    "additionalProperties": False,
}

EVALUATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {"type": "string", "enum": ["pass", "fail"]},
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

# The conventions that hold whatever the schema is. Everything schema-specific arrives in
# `conventions`, read from the reviewed contexts.
UNIVERSAL_CONVENTIONS = """\
Every predicate in the SQL must trace to an explicit restriction in the question; one that
does not is wrong however sensible it looks. A plain count of rows uses COUNT(*), and a
distinct count is wrong unless the question asked for distinct entities or a join could
duplicate them. Where the question asks which entity has the most, least, highest, lowest,
largest, smallest, best or worst value, correct SQL returns every entity tied at that
extreme -- typically by equality against a MAX or MIN subquery -- and SQL that orders and
takes one row is wrong because it can drop a tie. Where stored values were supplied for a
mention, SQL filtering with the question's own spelling instead is wrong.
"""

GENERATE_SYSTEM_PROMPT = """\
You design natural-language unit tests that tell correct SQL query logic from incorrect
query logic. You are given a question, one plan step, the reviewed conventions of the
tables involved, and several candidate result groups that disagree with each other.

Each test is one assertion beginning "The correct SQL should ". Judge query logic only:
never output formatting, aliases, prose wording, or row ordering, all of which were
already treated as equivalent before you were asked. Prefer an assertion that some of the
shown groups satisfy and others do not -- an assertion every candidate passes decides
nothing. Assert nothing that the question, the plan step, the conventions, or the shown SQL
and rows do not support.
"""

EVALUATE_SYSTEM_PROMPT = """\
Grade one natural-language unit test against every candidate at once.

Judge only whether each candidate's SQL query logic satisfies the test, for the original
question and under the supplied conventions. Ignore output formatting, aliases, prose
wording and row ordering. Return exactly one verdict per candidate, in the order the
candidates are given, each exactly "pass" or "fail".
"""


class UnitTesterError(RuntimeError):
    """Raised when the arbiter's answer cannot be believed."""


def conventions_for(
    contexts: Mapping[str, SchemaContext], tables: Sequence[str]
) -> tuple[str, ...]:
    """Return the reviewed cautions of the tables a step reads.

    These are the domain conventions the assertions must enforce. They live in the context
    files so that the one place a reviewer edits them is the one place they are read.
    """
    return tuple(
        caution
        for table in tables
        if (context := contexts.get(table)) is not None
        for caution in context.cautions
    )


def generate_unit_tests(
    question: str,
    step: PlanStep,
    groups: Sequence[Mapping[str, Any]],
    conventions: Sequence[str],
    count: int,
    *,
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
) -> tuple[str, ...]:
    """Write up to ``count`` assertions that discriminate between the groups."""
    payload = {
        "question": question,
        "plan_step": {
            "purpose": step.purpose,
            "table": step.table,
            "calculations": step.calculations,
        },
        "conventions": [UNIVERSAL_CONVENTIONS, *conventions],
        "candidates": list(groups),
        "test_count": count,
        "resolved_entities": stored_values(resolved),
    }
    parsed = _complete(GENERATE_SYSTEM_PROMPT, payload, GENERATE_SCHEMA, gateway)
    tests = parsed.get("tests")
    if not isinstance(tests, list):
        raise UnitTesterError("The arbiter returned no tests")
    return tuple(str(test) for test in tests)[:count]


def evaluate_unit_test(
    question: str,
    test: str,
    groups: Sequence[Mapping[str, Any]],
    conventions: Sequence[str],
    *,
    resolved: EntityResolution | None = None,
    gateway: Any | None = None,
) -> tuple[str, ...]:
    """Grade one assertion against every group, returning one verdict each."""
    payload = {
        "question": question,
        "test": test,
        "conventions": [UNIVERSAL_CONVENTIONS, *conventions],
        "candidates": list(groups),
        "resolved_entities": stored_values(resolved),
    }
    parsed = _complete(EVALUATE_SYSTEM_PROMPT, payload, EVALUATE_SCHEMA, gateway)
    verdicts = parsed.get("verdicts")
    if not isinstance(verdicts, list) or len(verdicts) != len(groups):
        raise UnitTesterError(
            f"The arbiter graded {len(verdicts or ())} of {len(groups)} candidates"
        )
    if any(verdict not in {"pass", "fail"} for verdict in verdicts):
        raise UnitTesterError(f"The arbiter returned an unexpected verdict: {verdicts}")
    return tuple(str(verdict) for verdict in verdicts)


def _complete(
    system: str, payload: Mapping[str, Any], schema: dict[str, Any], gateway: Any | None
) -> Mapping[str, Any]:
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=True, default=str),
        },
    ]
    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()
    completion = gateway.complete_json(UNIT_TESTER_TASK, messages, schema)

    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise UnitTesterError(f"The arbiter returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise UnitTesterError("The arbiter returned a non-object answer")
    return parsed
