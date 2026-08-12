"""Restate one question as structured fields, before anything decides how to answer it.

Understanding is the first node, so it is the one whose mistakes every later node
inherits. It does two things and refuses a third:

* **It restates.** The question's own words become entities, filters, a time range, a
  metric and the shape a correct answer takes. Those words are what the entity resolver
  later matches against values the database actually stores, so they are preserved
  verbatim -- a mention tidied into a canonical form here resolves to a different record
  downstream, or to none at all.
* **It never fills a gap.** An absent time range is an empty string. A model that
  guesses one has invented a filter that then narrows every source the question fans out
  to, and nothing downstream can tell an invented restriction from a stated one.
* **It does not decide where the answer comes from.** That is the router's job one node
  later, made from source capability descriptions. A prompt that named the sources here
  would have this node pre-empt it, and tests assert the prompt names neither the sources
  nor a single table or column of the corpus.

The reference implementation classified questions as retrieval or analytics, which is the
whole vocabulary a single-database system needs. Over four heterogeneous sources it is not
enough: "why did this rise" and "is this consistent with the stated criteria" are neither
a record lookup nor an aggregation, and forcing them into one of those two labels sends a
question that needs reconciliation down a path built for a number.

**Failure is not uniform.** A provider that cannot be reached costs the run its structured
extraction, not its answer -- routing can still work from the raw question -- so the
failure is recorded on the stage and the graph goes on. A spent budget or an exhausted
free-tier quota is terminal for every node that follows, and softening it into a degraded
first stage would just spend the same refusal at each of them and answer from nothing.
Those propagate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vericlaim.gateway.types import (
    BudgetExceededError,
    GatewayError,
    PaidFallbackBlockedError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

UNDERSTAND_TASK = "understand"
STAGE = "understand"

# What kind of work an answer takes. Deliberately about the question's demand, never
# about which source could meet it.
QUERY_TYPES = ("lookup", "aggregate", "explanation", "assessment")

# Terminal for the run rather than for this node: no later node could succeed either,
# and each would pay another refusal to find that out.
TERMINAL_ERRORS = (BudgetExceededError, PaidFallbackBlockedError, QuotaExhaustedError)

UNDERSTAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_type": {"type": "string", "enum": list(QUERY_TYPES)},
        "entities": {"type": "array", "items": {"type": "string"}},
        "filters": {"type": "array", "items": {"type": "string"}},
        "time_range": {"type": "string"},
        "metric": {"type": "string"},
        "output_kind": {"type": "string"},
    },
    "required": [
        "query_type",
        "entities",
        "filters",
        "time_range",
        "metric",
        "output_kind",
    ],
    "additionalProperties": False,
}

UNDERSTAND_SYSTEM_PROMPT = """\
You restate one question as structured fields. You do not answer it, you do not judge
whether it can be answered, and you do not decide where an answer would be found.

USE THE QUESTION'S OWN WORDS.
Copy every named thing, restriction and period of time exactly as the question wrote
them. Do not expand an abbreviation, correct a spelling, translate, re-case, or replace a
phrase with the term you believe was meant. A later step matches these strings against
the values a system actually holds, and a tidied mention matches a different thing or
nothing at all.

RECORD ONLY WHAT WAS ASKED.
Every field must trace to something the question states. When it states nothing for a
field, return an empty string or an empty list. Absence is information; an invented value
becomes a restriction that narrows the answer, and nothing downstream can tell it apart
from one the asker made. Never resolve a relative period such as "last quarter" or
"recently" into dates -- record the phrase as written. You do not know today's date.

THE FIELDS.
- query_type: one of
  - lookup: asks for particular recorded facts or passages, as recorded.
  - aggregate: asks for a count, total, rate, ranking, distribution, trend or a
    comparison computed across many records.
  - explanation: asks why something happened, what accounts for it, or what changed.
  - assessment: asks whether something meets, contradicts or is consistent with stated
    criteria.
  Choose the kind of work the question demands. When two fit, choose the one the answer
  would have to do last: a question asking why a figure moved is an explanation even
  though a figure has to be computed on the way.
- entities: the specific things the question names -- an organisation, a person, a place,
  an identifier, a named document. Not the general nouns describing them.
- filters: the restrictions the question places on what counts, other than time.
- time_range: the period the question restricts the answer to, as written.
- metric: the quantity being asked for, when one is. Empty for a question that asks for
  text or a judgement rather than a number.
- output_kind: one short phrase for the shape a correct answer takes -- a single number,
  a ranked list, a comparison of two figures, a yes or no with reasoning.

An identifier is an entity, not a filter. A question may name none of these things; that
is a valid extraction, not a reason to infer one.
"""


@dataclass(frozen=True, slots=True)
class Understanding:
    """One question, restated in fields that mention no schema and no source."""

    query_type: str = ""
    entities: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    time_range: str = ""
    metric: str = ""
    output_kind: str = ""
    rejected_query_type: str = ""

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> Understanding:
        """Normalize a model's extraction without adding anything to it."""
        declared = str(raw.get("query_type") or "").strip().lower()
        known = declared in QUERY_TYPES
        return cls(
            query_type=declared if known else "",
            entities=_mentions(raw.get("entities")),
            filters=_mentions(raw.get("filters")),
            time_range=_text(raw.get("time_range")),
            metric=_text(raw.get("metric")),
            output_kind=_text(raw.get("output_kind")),
            rejected_query_type="" if known else declared,
        )

    def as_dict(self) -> dict[str, Any]:
        """The shape the state carries and every later node reads."""
        return {
            "query_type": self.query_type,
            "entities": list(self.entities),
            "filters": list(self.filters),
            "time_range": self.time_range,
            "metric": self.metric,
            "output_kind": self.output_kind,
        }


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def understand(state: GraphState, *, gateway: Any | None = None) -> GraphState:
    """Extract the question's structure into the state, and record what it cost."""
    if gateway is None:
        from vericlaim.gateway import default_gateway

        gateway = default_gateway()

    messages = [
        {"role": "system", "content": UNDERSTAND_SYSTEM_PROMPT},
        {"role": "user", "content": state.question},
    ]

    try:
        completion = gateway.complete_json(
            UNDERSTAND_TASK, messages, UNDERSTAND_SCHEMA
        )
        extracted = Understanding.from_payload(_parse(completion))
    except TERMINAL_ERRORS:
        raise
    except (GatewayError, ValueError) as exc:
        # The question still stands on its own, so the run continues without the
        # extraction rather than ending on the first node.
        return state.with_stage(StageRecord(name=STAGE, error=str(exc)))

    detail: dict[str, Any] = {"query_type": extracted.query_type}
    if extracted.rejected_query_type:
        detail["rejected_query_type"] = extracted.rejected_query_type
    if getattr(completion, "used_fallback", False):
        detail["fallbacks"] = len(completion.fallbacks)

    return state.with_(understanding=extracted.as_dict()).with_stage(
        StageRecord(
            name=STAGE,
            detail=detail,
            model=getattr(completion, "model", ""),
            cost_usd=getattr(completion, "cost_usd", 0.0),
            latency_ms=getattr(completion, "latency_ms", 0.0),
        )
    )


def _parse(completion: Any) -> Mapping[str, Any]:
    """Read the structured extraction out of a gateway completion."""
    parsed = getattr(completion, "parsed", None)
    if parsed is None:
        import json

        try:
            parsed = json.loads(completion.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Understanding returned no usable JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("Understanding returned a non-object extraction")
    return parsed


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _mentions(value: Any) -> tuple[str, ...]:
    """Clean a list of mentions without changing the words in it.

    Blanks go, repeats go, order stays. Case is compared but never rewritten: the same
    mention typed twice is one mention, and the first spelling is the one kept, because
    it is a string a later step has to match against a stored value.
    """
    if isinstance(value, str) or not isinstance(value, Sequence):
        value = () if value is None else (value,)
    kept: list[str] = []
    seen: set[str] = set()
    for item in value:
        mention = _text(item)
        if not mention or mention.casefold() in seen:
            continue
        seen.add(mention.casefold())
        kept.append(mention)
    return tuple(kept)
