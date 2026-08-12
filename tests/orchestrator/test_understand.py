"""Turning one question into structured fields the rest of the graph can reason over.

Understanding is the first node, so everything downstream inherits whatever it gets
wrong. Two properties are worth more than the extraction itself:

* **It restates, it never resolves.** The words the question used are the words the
  entity resolver has to match against stored values; an expanded or tidied mention
  resolves to a different record, or to nothing.
* **It never invents.** An absent time range is an empty string, not a plausible one.
  A filter nobody asked for narrows every source that follows it.

The node also has to survive the model being unavailable, because routing can still
proceed from the raw question -- but not a budget that has run out, which is terminal
for the whole run and must not be softened into a degraded first stage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.gateway.providers import strictify_schema
from vericlaim.gateway.types import (
    AllProvidersFailedError,
    BudgetExceededError,
    PaidFallbackBlockedError,
    ProviderUnavailableError,
    QuotaExhaustedError,
)
from vericlaim.orchestrator.nodes.understand import (
    QUERY_TYPES,
    UNDERSTAND_SCHEMA,
    UNDERSTAND_SYSTEM_PROMPT,
    UNDERSTAND_TASK,
    Understanding,
    understand,
)
from vericlaim.orchestrator.state import GraphState
from vericlaim.sql.contexts import load_contexts

QUESTION = "How many water-damage incidents were reported in March 2026 in the north?"


@dataclass
class _Completion:
    text: str
    parsed: Any
    task: str = UNDERSTAND_TASK
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    cost_usd: float = 0.0
    latency_ms: float = 412.0
    fallbacks: tuple[Any, ...] = ()

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallbacks)


@dataclass
class FakeGateway:
    """Returns a canned extraction, or raises, and remembers what it was asked."""

    payload: dict[str, Any] | None = None
    raises: Exception | None = None
    text: str | None = None
    calls: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)

    def complete_json(
        self, task: str, messages: Any, schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append((task, messages, schema))
        if self.raises is not None:
            raise self.raises
        if self.text is not None:
            return _Completion(self.text, None)
        return _Completion(json.dumps(self.payload), self.payload)


def payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "query_type": "aggregate",
        "entities": ["north"],
        "filters": ["water damage"],
        "time_range": "March 2026",
        "metric": "number of incidents",
        "output_kind": "a single count",
    }
    base.update(overrides)
    return base


def sent_question(gateway: FakeGateway) -> str:
    return gateway.calls[0][1][-1]["content"]


# ------------------------------------------------------------------ extraction


def test_the_question_becomes_structured_fields() -> None:
    gateway = FakeGateway(payload())

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {
        "query_type": "aggregate",
        "entities": ["north"],
        "filters": ["water damage"],
        "time_range": "March 2026",
        "metric": "number of incidents",
        "output_kind": "a single count",
    }


def test_the_model_is_asked_the_question_on_the_cheap_tier_task() -> None:
    gateway = FakeGateway(payload())

    understand(GraphState(question=QUESTION), gateway=gateway)

    task, _, schema = gateway.calls[0]
    assert task == UNDERSTAND_TASK
    assert QUESTION in sent_question(gateway)
    assert schema is UNDERSTAND_SCHEMA


def test_the_state_it_was_given_is_left_alone() -> None:
    """Nodes return a new state. One that mutated its input would corrupt the branch a
    concurrent sibling is reading."""
    gateway = FakeGateway(payload())
    before = GraphState(question=QUESTION)

    after = understand(before, gateway=gateway)

    assert before.understanding == {}
    assert after is not before


def test_a_missing_field_is_empty_rather_than_guessed() -> None:
    """A model that omits a field has told us nothing about it. Filling the gap here is
    how a filter nobody asked for reaches four source tools."""
    gateway = FakeGateway({"query_type": "lookup"})

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {
        "query_type": "lookup",
        "entities": [],
        "filters": [],
        "time_range": "",
        "metric": "",
        "output_kind": "",
    }


# ------------------------------------------------------------------ normalization


def test_mentions_keep_the_words_the_question_used() -> None:
    """The resolver matches these against stored values. `  Karachi ` and `Karachi` are
    the same mention; `karachi` typed by the model is not a licence to re-case it."""
    extracted = Understanding.from_payload(
        payload(entities=["  Karachi  ", "Karachi", "", "   ", "Lahore"])
    )

    assert extracted.entities == ("Karachi", "Lahore")


def test_a_mention_repeated_in_another_casing_is_one_mention() -> None:
    extracted = Understanding.from_payload(payload(entities=["Karachi", "KARACHI"]))

    assert extracted.entities == ("Karachi",)


def test_the_order_the_question_named_things_in_survives() -> None:
    extracted = Understanding.from_payload(
        payload(entities=["Lahore", "Karachi", "Islamabad"])
    )

    assert extracted.entities == ("Lahore", "Karachi", "Islamabad")


def test_a_filter_list_is_cleaned_the_same_way_as_the_entities() -> None:
    extracted = Understanding.from_payload(
        payload(filters=["water damage", " water damage ", ""])
    )

    assert extracted.filters == ("water damage",)


def test_a_non_string_in_a_list_does_not_reach_the_next_node() -> None:
    extracted = Understanding.from_payload(payload(entities=["north", None, 7]))

    assert extracted.entities == ("north", "7")


def test_a_query_type_outside_the_vocabulary_is_dropped_not_passed_on() -> None:
    """Downstream branches on this value. An unrecognized one silently takes whichever
    branch is written last, so it is recorded as unknown and named in the trace."""
    gateway = FakeGateway(payload(query_type="freeform"))

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding["query_type"] == ""
    assert state.stages[-1].detail["rejected_query_type"] == "freeform"


def test_a_query_type_is_read_case_insensitively() -> None:
    extracted = Understanding.from_payload(payload(query_type="  Aggregate "))

    assert extracted.query_type == "aggregate"


def test_every_declared_query_type_is_accepted() -> None:
    for query_type in QUERY_TYPES:
        assert Understanding.from_payload(payload(query_type=query_type)).query_type == (
            query_type
        )


# ------------------------------------------------------------------ the stage record


def test_the_stage_records_what_the_call_cost() -> None:
    gateway = FakeGateway(payload())

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    stage = state.stages[-1]
    assert stage.name == "understand"
    assert stage.model == "gemini-3.5-flash-lite"
    assert stage.latency_ms == 412.0
    assert not stage.failed
    assert state.total_latency_ms == 412.0


def test_the_stage_names_the_reading_it_took() -> None:
    gateway = FakeGateway(payload())

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.stages[-1].detail["query_type"] == "aggregate"


# ------------------------------------------------------------------ failure


def test_a_provider_failure_leaves_the_question_to_speak_for_itself() -> None:
    """Routing can work from the raw question. Raising here would lose a run that was
    still answerable, so the failure is recorded and the graph goes on."""
    gateway = FakeGateway(
        raises=AllProvidersFailedError(
            UNDERSTAND_TASK,
            [("gemini", "gemini-3.5-flash-lite", RuntimeError("every rung failed"))],
        )
    )

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {}
    assert state.stages[-1].failed
    assert "every rung failed" in state.stages[-1].error
    assert state.failures[0].startswith("understand: All models failed")


def test_a_provider_being_unavailable_is_recorded_the_same_way() -> None:
    gateway = FakeGateway(
        raises=ProviderUnavailableError(
            "no key configured", provider="gemini", model="gemini-3.5-flash-lite"
        )
    )

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {}
    assert state.stages[-1].failed


def test_a_reply_that_is_not_json_is_recorded_rather_than_guessed_at() -> None:
    gateway = FakeGateway(text="I think you want the March figures.")

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {}
    assert state.stages[-1].failed


def test_a_reply_that_is_not_an_object_is_recorded_too() -> None:
    gateway = FakeGateway(text="[1, 2, 3]")

    state = understand(GraphState(question=QUESTION), gateway=gateway)

    assert state.understanding == {}
    assert state.stages[-1].failed


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceededError("total", 5.0, 5.0),
        PaidFallbackBlockedError("understand", ["openai/gpt-4o-mini"]),
        QuotaExhaustedError(
            "daily free-tier allowance is spent",
            provider="gemini",
            model="gemini-3.5-flash-lite",
        ),
    ],
)
def test_running_out_of_money_or_quota_stops_the_run(error: Exception) -> None:
    """These are terminal for every node that follows, not a gap in this one. Recording
    them as a soft failure would send the graph on to spend the same refusal six more
    times and answer from nothing."""
    gateway = FakeGateway(raises=error)

    with pytest.raises(type(error)):
        understand(GraphState(question=QUESTION), gateway=gateway)


# ------------------------------------------------------------------ the contract


def test_the_schema_survives_strict_structured_output() -> None:
    """OpenAI's strict json_schema mode requires every property required and
    additionalProperties false. A schema that needs rewriting at call time is one the
    fallback leg answers differently from the primary."""
    assert strictify_schema(UNDERSTAND_SCHEMA) == UNDERSTAND_SCHEMA


def test_the_prompt_names_no_table_or_column_of_the_corpus() -> None:
    """Understanding is schema-independent by design: it restates the question, and the
    schema it will be answered from is not its business."""
    identifiers = set()
    for directory in ("contexts/sql", "contexts/sheets"):
        for context in load_contexts(directory).values():
            identifiers.add(context.table)
            identifiers.update(context.column_names)

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(
            rf"\b{re.escape(identifier)}\b", UNDERSTAND_SYSTEM_PROMPT, re.I
        )
    )

    assert named == []


def test_the_prompt_names_none_of_the_four_sources() -> None:
    """Choosing sources is the router's job. A prompt that mentioned them here would
    have understanding pre-empt a decision made one node later from capability
    descriptions."""
    named = sorted(
        word
        for word in ("policy", "spreadsheet", "workbook", "scanned", "OCR", "SQL")
        if re.search(rf"\b{re.escape(word)}\b", UNDERSTAND_SYSTEM_PROMPT, re.I)
    )

    assert named == []


def test_the_prompt_refuses_to_answer_the_question() -> None:
    assert "not answer" in UNDERSTAND_SYSTEM_PROMPT.lower()
