# C-9 API and NDJSON Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put an HTTP surface and an NDJSON event stream over `run_question`, so C-10's frontend has a stable contract to build against.

**Architecture:** A closed five-event protocol in `api/protocol.py`. A generator `stream_question` in `orchestrator/graph.py` drives LangGraph's `.stream(stream_mode="values")` and yields those events, so the orchestrator owns its own contract and the API cannot disagree with it about what a run is. `api/app.py` is transport only: it serializes events to NDJSON and adds a keepalive ping that never enters the protocol.

**Tech Stack:** Python 3.12, FastAPI, Starlette `StreamingResponse`, LangGraph, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-vericlaim-c9-api-streaming-design.md`

## Global Constraints

- Task ids are `C-<phase>.<task>`. The `T-`, `V-`, `L-` series must never appear.
- One task per commit, committed only once its acceptance is demonstrated.
- **No commit trailers of any kind.** No `Co-Authored-By`, no `Generated with`, no session links.
- Every `git add` is path-scoped. **Never `git add -A`** — `CLAUDE.md` carries an uncommitted user edit that must never be staged.
- ASCII only in source and tests.
- Cost reported anywhere in the API is read from `gateway.ledger.total_cost_usd`, **never** `state.total_cost_usd`. Tool-internal model spend reaches no `StageRecord`, so the state under-reports a four-source question by most of its cost.
- No prompt names the corpus. This phase adds no prompts.
- No hard-coded example behaviour: no question-string matching, no expected answers, no fixed evidence counts in assertions.
- Tests in this phase are offline: no models, no database, no quota. They take no pytest marker.
- Reference repos `/Users/rowdy/Projects/work/unibot-endgame` and `/Users/rowdy/Projects/work/CIL/CSRS` are **read-only**.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/vericlaim/api/__init__.py` | Public exports. |
| `src/vericlaim/api/protocol.py` | The five event types and their JSON serialization. No HTTP. |
| `src/vericlaim/api/app.py` | FastAPI app, two endpoints, keepalive, error mapping, per-request gateway and tools. |
| `src/vericlaim/orchestrator/graph.py` | Gains `stream_question`. Nothing else changes. |
| `tests/api/test_protocol.py` | Event shape and serialization. |
| `tests/orchestrator/test_stream.py` | `stream_question` against a fake graph. |
| `tests/api/test_app.py` | Endpoints via `TestClient` against a fake graph and fake gateway. |

---

### Task 1: The event protocol (C-9.1)

**Files:**
- Create: `src/vericlaim/api/__init__.py`
- Create: `src/vericlaim/api/protocol.py`
- Test: `tests/api/test_protocol.py`

**Interfaces:**
- Consumes: `GraphState` from `vericlaim.orchestrator.state`, `StageRecord` from the same module, `Evidence` from `vericlaim.evidence`.
- Produces: `RunStarted`, `Stage`, `EvidenceEvent`, `Final`, `Error`, each with `.to_json() -> dict[str, Any]`; the union alias `Event`; and `EVENT_NAMES: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/__init__.py` as an empty file, then `tests/api/test_protocol.py`:

```python
"""The event protocol is the contract C-10 builds against, so its shape is pinned here."""

from __future__ import annotations

import pytest

from vericlaim.api.protocol import (
    EVENT_NAMES,
    Error,
    EvidenceEvent,
    Final,
    RunStarted,
    Stage,
)
from vericlaim.evidence import Evidence, PolicyLocator, Provenance
from vericlaim.orchestrator.state import GraphState, StageRecord


def test_run_started_carries_the_trace_and_the_question() -> None:
    payload = RunStarted(trace_id="abc123", question="a question").to_json()

    assert payload == {
        "event": "run_started",
        "trace_id": "abc123",
        "question": "a question",
    }


def test_a_stage_carries_what_the_node_cost_and_whether_it_failed() -> None:
    record = StageRecord(
        name="understand",
        model="a-model",
        cost_usd=0.5,
        latency_ms=12.5,
        error="",
        detail={"key": "value"},
    )

    payload = Stage.from_record(record).to_json()

    assert payload == {
        "event": "stage",
        "name": "understand",
        "model": "a-model",
        "cost_usd": 0.5,
        "latency_ms": 12.5,
        "error": "",
        "detail": {"key": "value"},
    }


def test_an_error_event_carries_its_message() -> None:
    assert Error(message="it broke").to_json() == {
        "event": "error",
        "message": "it broke",
    }


def test_every_event_name_is_declared_and_ping_is_not_one_of_them() -> None:
    assert EVENT_NAMES == frozenset(
        {"run_started", "stage", "evidence", "final", "error"}
    )
    assert "ping" not in EVENT_NAMES


def test_a_final_event_reports_the_ledger_cost_not_the_states_own() -> None:
    # The state's cost comes from stage records only; tool-internal spend reaches none of
    # them. A final event that reported the state's total would under-report every
    # multi-source question, so the cost is supplied rather than derived.
    state = GraphState(
        question="a question",
        answer="an answer",
        trace_id="abc123",
        stages=(StageRecord(name="understand", cost_usd=1.0),),
    )

    payload = Final.from_state(state, cost_usd=99.0).to_json()

    assert payload["event"] == "final"
    assert payload["cost_usd"] == 99.0
    assert payload["answer"] == "an answer"
    assert payload["trace_id"] == "abc123"


def test_an_evidence_event_names_its_source_and_serializes_its_item() -> None:
    # id is assigned by EvidenceSet on insertion, never by the producing tool, so it is
    # left at its default here.
    item = Evidence(
        source_type="policy",
        source_id="a-document.pdf",
        content="some text",
        locator=PolicyLocator(document="a-document.pdf", page=1),
        provenance=Provenance(tool="search_policy"),
    )

    payload = EvidenceEvent(source="policy", items=[item.to_dict()]).to_json()

    assert payload["event"] == "evidence"
    assert payload["source"] == "policy"
    assert payload["items"] == [item.to_dict()]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vericlaim.api'`.

If `Evidence(...)` or `Provenance(...)` raises a validation error, read `src/vericlaim/evidence.py` and correct the constructor call in the test to the real required fields. Do not change `evidence.py`.

- [ ] **Step 3: Write the implementation**

Create `src/vericlaim/api/__init__.py`:

```python
"""The HTTP surface over one question, and the events a run reports while it runs."""

from vericlaim.api.protocol import (
    EVENT_NAMES,
    Error,
    Event,
    EvidenceEvent,
    Final,
    RunStarted,
    Stage,
)

__all__ = [
    "EVENT_NAMES",
    "Error",
    "Event",
    "EvidenceEvent",
    "Final",
    "RunStarted",
    "Stage",
]
```

Create `src/vericlaim/api/protocol.py`:

```python
"""What a run reports while it runs, as a closed set of events.

One event per line of NDJSON. C-10's typed event unions are written against this
module, so it is the single place either side learns the shape of a run.

``ping`` is deliberately absent. A keepalive is a property of the connection, not of
the run, and a client that treated one as run information would be reading the network
rather than the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vericlaim.orchestrator.state import GraphState, StageRecord

EVENT_NAMES = frozenset({"run_started", "stage", "evidence", "final", "error"})


@dataclass(frozen=True, slots=True)
class RunStarted:
    """First event of every stream: which run this is, and what it was asked."""

    trace_id: str
    question: str

    def to_json(self) -> dict[str, Any]:
        return {
            "event": "run_started",
            "trace_id": self.trace_id,
            "question": self.question,
        }


@dataclass(frozen=True, slots=True)
class Stage:
    """One graph node, once it has finished."""

    name: str
    model: str
    cost_usd: float
    latency_ms: float
    error: str
    detail: dict[str, Any]

    @classmethod
    def from_record(cls, record: StageRecord) -> Stage:
        return cls(
            name=record.name,
            model=record.model,
            cost_usd=record.cost_usd,
            latency_ms=record.latency_ms,
            error=record.error,
            detail=dict(record.detail),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "event": "stage",
            "name": self.name,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """Evidence a source returned, as soon as it returned it."""

    source: str
    items: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {"event": "evidence", "source": self.source, "items": self.items}


@dataclass(frozen=True, slots=True)
class Final:
    """The finished run. Last event of a stream that succeeded."""

    payload: dict[str, Any]

    @classmethod
    def from_state(cls, state: GraphState, *, cost_usd: float) -> Final:
        """Build the final payload, with the cost supplied rather than derived.

        ``GraphState.total_cost_usd`` sums stage records, and the model calls a source
        tool makes are recorded on no stage. Deriving the cost here would under-report
        every multi-source question, so the caller passes the gateway ledger's figure.
        """
        payload = state.to_dict()
        payload["cost_usd"] = cost_usd
        return cls(payload=payload)

    def to_json(self) -> dict[str, Any]:
        return {"event": "final", **self.payload}


@dataclass(frozen=True, slots=True)
class Error:
    """A run that could not finish. Last event of a stream that failed."""

    message: str

    def to_json(self) -> dict[str, Any]:
        return {"event": "error", "message": self.message}


Event = RunStarted | Stage | EvidenceEvent | Final | Error

__all__ = [
    "EVENT_NAMES",
    "Error",
    "Event",
    "EvidenceEvent",
    "Final",
    "RunStarted",
    "Stage",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_protocol.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the full offline suite and the linter**

Run:
```bash
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
```
Expected: the previous total plus 6, zero failures; `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/vericlaim/api/__init__.py src/vericlaim/api/protocol.py tests/api/__init__.py tests/api/test_protocol.py
git commit -m "feat(C-9.1): define the NDJSON event protocol

Five events, closed set, one per line. C-10's typed unions are written
against this module so both sides learn a run's shape in one place.

A final event takes its cost rather than deriving it: GraphState sums
stage records, and a source tool's own model calls reach no stage, so a
derived figure would under-report every multi-source question.

ping is deliberately not a protocol event. A keepalive belongs to the
connection, not the run."
```

---

### Task 2: `stream_question` (C-9.2, part one)

**Files:**
- Modify: `src/vericlaim/orchestrator/graph.py` (add `stream_question` after `run_question`, around line 174)
- Test: `tests/orchestrator/test_stream.py`

**Interfaces:**
- Consumes: `RunStarted`, `Stage`, `EvidenceEvent`, `Final` from Task 1.
- Produces: `stream_question(graph, question, *, gateway, **config) -> Iterator[Event]`. `gateway` is a **required** keyword argument, so a caller cannot build a final event without the ledger that holds the true cost.

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_stream.py`:

```python
"""stream_question drives the graph and reports what it did, event by event."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.api.protocol import EvidenceEvent, Final, RunStarted, Stage
from vericlaim.evidence import Evidence, EvidenceSet, PolicyLocator, Provenance
from vericlaim.orchestrator.graph import stream_question
from vericlaim.orchestrator.state import GraphState, StageRecord


@dataclass
class FakeLedger:
    total_cost_usd: float = 0.0


@dataclass
class FakeGateway:
    ledger: FakeLedger = field(default_factory=FakeLedger)


@dataclass
class FakeGraph:
    """Yields the accumulated state after each node, as stream_mode='values' does."""

    values: list[dict[str, Any]]

    def stream(self, start: GraphState, **config: Any) -> Any:
        self.seen_start = start
        self.seen_config = config
        return iter(self.values)


def _evidence(document: str) -> Evidence:
    # EvidenceSet assigns the citation id on insertion, so none is passed here.
    return Evidence(
        source_type="policy",
        source_id=document,
        content="some text",
        locator=PolicyLocator(document=document, page=1),
        provenance=Provenance(tool="search_policy"),
    )


def _state(**fields: Any) -> dict[str, Any]:
    return GraphState(question="a question", **fields).model_dump()


def test_the_first_event_names_the_run_and_the_question() -> None:
    graph = FakeGraph([_state()])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))

    assert isinstance(events[0], RunStarted)
    assert events[0].question == "a question"
    assert events[0].trace_id


def test_each_new_stage_is_reported_once() -> None:
    first = StageRecord(name="understand")
    second = StageRecord(name="route")
    graph = FakeGraph([_state(stages=(first,)), _state(stages=(first, second))])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))
    stages = [event for event in events if isinstance(event, Stage)]

    assert [stage.name for stage in stages] == ["understand", "route"]


def test_new_evidence_is_reported_as_it_arrives() -> None:
    # Two distinct documents, because EvidenceSet deduplicates identical items.
    one = EvidenceSet([_evidence("one.pdf")])
    two = EvidenceSet([_evidence("one.pdf"), _evidence("two.pdf")])
    graph = FakeGraph([_state(evidence=one), _state(evidence=two)])

    events = list(stream_question(graph, "a question", gateway=FakeGateway()))
    evidence_events = [event for event in events if isinstance(event, EvidenceEvent)]

    assert sum(len(event.items) for event in evidence_events) == 2


def test_the_last_event_is_final_and_reports_the_ledger_cost() -> None:
    gateway = FakeGateway(FakeLedger(total_cost_usd=42.0))
    graph = FakeGraph(
        [_state(answer="an answer", stages=(StageRecord(name="x", cost_usd=1.0),))]
    )

    events = list(stream_question(graph, "a question", gateway=gateway))

    assert isinstance(events[-1], Final)
    assert events[-1].to_json()["cost_usd"] == 42.0


def test_a_blank_question_is_refused_before_the_graph_is_touched() -> None:
    graph = FakeGraph([])

    with pytest.raises(ValueError):
        list(stream_question(graph, "   ", gateway=FakeGateway()))


def test_the_graph_is_asked_for_accumulated_values() -> None:
    graph = FakeGraph([_state()])

    list(stream_question(graph, "a question", gateway=FakeGateway()))

    assert graph.seen_config["stream_mode"] == "values"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/orchestrator/test_stream.py -v`
Expected: FAIL with `ImportError: cannot import name 'stream_question'`.

- [ ] **Step 3: Write the implementation**

In `src/vericlaim/orchestrator/graph.py`, add these imports beside the existing ones:

```python
from collections.abc import Iterator

from vericlaim.api.protocol import Event, EvidenceEvent, Final, RunStarted, Stage
```

Then add `stream_question` immediately after `run_question` (which ends around line 173):

```python
@traced("vericlaim.question.stream", run_type="chain", tags=["orchestrator"])
def stream_question(
    graph: Any, question: str, *, gateway: Any, **config: Any
) -> Iterator[Event]:
    """Run one question and report what it did as it did it.

    A sibling to ``run_question`` rather than a second entry point built elsewhere: the
    root span, the trace id and the run summary are defined once, so a streamed run and
    an awaited one cannot disagree about what a run is.

    ``gateway`` is required because the final event carries the ledger's cost. Making it
    optional would let a caller silently emit the state's own total, which counts no
    model call a source tool made.

    The graph is asked for accumulated values rather than per-node updates, so the final
    state is the last thing yielded and no reducer has to be reimplemented here to
    rebuild it.
    """
    start = GraphState(question=question, trace_id=uuid.uuid4().hex)
    yield RunStarted(trace_id=start.trace_id, question=start.question)

    reported_stages = 0
    reported_evidence = 0
    last: dict[str, Any] | None = None

    for value in graph.stream(start, stream_mode="values", **config):
        last = value

        stages = tuple(value.get("stages") or ())
        for record in stages[reported_stages:]:
            yield Stage.from_record(record)
        reported_stages = len(stages)

        evidence = value.get("evidence")
        items = list(evidence.items) if evidence is not None else []
        for item in items[reported_evidence:]:
            yield EvidenceEvent(source=item.source_type, items=[item.to_dict()])
        reported_evidence = len(items)

    if last is None:
        raise RuntimeError("The graph produced no state, so there is nothing to report")

    state = GraphState(**last)
    _trace_run(state)
    yield Final.from_state(state, cost_usd=gateway.ledger.total_cost_usd)
```

Add `stream_question` to the module's `__all__` if the module declares one.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/orchestrator/test_stream.py -v`
Expected: PASS, 6 tests.

If `GraphState(**last)` raises because `model_dump()` turned `EvidenceSet` into a plain value, change `_state()` in the test to return the state object's `dict()` of live field values instead — for example `dict(GraphState(question="a question", **fields))` — and keep the implementation reading `value.get(...)`. The implementation must keep working against what LangGraph actually yields, which is a mapping of live channel values, not a JSON dump.

- [ ] **Step 5: Run the full offline suite and the linter**

Run:
```bash
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
```
Expected: previous total plus 6, zero failures; `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/vericlaim/orchestrator/graph.py tests/orchestrator/test_stream.py
git commit -m "feat(C-9.2): stream one question as it runs

stream_question sits beside run_question rather than in the API, so the
root span, the trace id and the run summary are defined once and a
streamed run cannot disagree with an awaited one about what a run is.

The gateway is a required argument because the final event carries the
ledger's cost. Optional would let a caller emit the state's own total,
which counts no model call a source tool made.

The graph is asked for accumulated values, so the final state is the last
thing yielded and no reducer is reimplemented to rebuild it."
```

---

### Task 3: The endpoints and the keepalive (C-9.2, part two)

**Files:**
- Create: `src/vericlaim/api/app.py`
- Test: `tests/api/test_app.py`

**Interfaces:**
- Consumes: `stream_question` from Task 2; `RunStarted`, `Final`, `Error` from Task 1.
- Produces: `create_app() -> FastAPI`; `PING = {"event": "ping"}`; `AskRequest` with one field `question: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_app.py`:

```python
"""The endpoints are transport. They must not invent, drop or reorder a run's events."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vericlaim.api.app import create_app
from vericlaim.api.protocol import EVENT_NAMES, Error, Final, RunStarted


class StubRun:
    """Stands in for stream_question: yields fixed events, or raises."""

    def __init__(self, events: list[Any] | None = None, error: Exception | None = None):
        self.events = events or []
        self.error = error

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


def _client(stub: StubRun) -> TestClient:
    return TestClient(create_app(run=stub))


def _lines(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _final(cost: float = 1.0) -> Final:
    return Final(payload={"answer": "an answer", "cost_usd": cost, "trace_id": "t1"})


def test_a_stream_ends_with_exactly_one_final() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final()])

    response = _client(stub).post("/api/ask/stream", json={"question": "q"})
    events = _lines(response)

    assert response.status_code == 200
    assert [event["event"] for event in events] == ["run_started", "final"]


def test_a_failure_mid_run_becomes_one_error_event_not_a_dropped_stream() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q")], error=RuntimeError("boom"))

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    assert [event["event"] for event in events] == ["run_started", "error"]
    assert events[-1]["message"] == "boom"


def test_every_streamed_event_name_is_in_the_protocol_or_is_the_keepalive() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final()])

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    for event in events:
        assert event["event"] in EVENT_NAMES | {"ping"}


def test_ask_returns_the_final_payload_as_one_object() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final(cost=42.0)])

    response = _client(stub).post("/api/ask", json={"question": "q"})

    assert response.status_code == 200
    assert response.json()["cost_usd"] == 42.0
    assert response.json()["answer"] == "an answer"


def test_ask_reports_a_failed_run_rather_than_returning_an_answer() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q")], error=RuntimeError("boom"))

    response = _client(stub).post("/api/ask", json={"question": "q"})

    assert response.status_code == 500
    assert "boom" in response.json()["detail"]


def test_a_blank_question_is_refused() -> None:
    stub = StubRun([_final()])

    response = _client(stub).post("/api/ask", json={"question": "   "})

    assert response.status_code == 422


def test_both_routes_agree_on_the_same_run() -> None:
    events = [RunStarted(trace_id="t1", question="q"), _final(cost=7.0)]

    streamed = _lines(_client(StubRun(events)).post("/api/ask/stream", json={"question": "q"}))
    awaited = _client(StubRun(events)).post("/api/ask", json={"question": "q"}).json()

    final = next(event for event in streamed if event["event"] == "final")
    assert final["answer"] == awaited["answer"]
    assert final["cost_usd"] == awaited["cost_usd"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vericlaim.api.app'`.

- [ ] **Step 3: Write the implementation**

Create `src/vericlaim/api/app.py`:

```python
"""The HTTP surface over one question.

Transport only. The events come from ``stream_question``; this module serializes them
and adds the one thing a connection needs that a run does not -- a keepalive.

The endpoints are declared ``def`` rather than ``async def`` on purpose. Everything
beneath them blocks: psycopg, Chroma, Ollama and the provider SDKs. Declaring them
async would hold the event loop for the length of a run.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from starlette.responses import StreamingResponse

from vericlaim.api.protocol import Error, Event, Final

PING: dict[str, Any] = {"event": "ping"}
PING_INTERVAL_S = 10.0

# Sentinel closing the queue the worker thread feeds.
_DONE = object()


class AskRequest(BaseModel):
    """One question. Rejected here if blank, before anything is built for it."""

    question: str

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A run needs a question to answer")
        return value.strip()


def _ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True) + "\n"


def _default_run(question: str) -> Iterator[Event]:
    """Run a real question, with a gateway and tools scoped to this request.

    The gateway is per-request because its ledger is what the reported cost is read
    from, and a shared one would bill every question for every other. The pool is
    injected explicitly: ``build_tools`` treats an absent database as one it owns, and
    would close the process-wide pool when this request's tools were released.
    """
    from vericlaim.config import get_settings
    from vericlaim.gateway.core import Gateway
    from vericlaim.orchestrator.graph import build_graph, stream_question
    from vericlaim.orchestrator.sources import load_capabilities
    from vericlaim.orchestrator.tools import open_tools
    from vericlaim.sql.db import default_database

    settings = get_settings()
    gateway = Gateway(settings=settings)
    capabilities = load_capabilities()

    with open_tools(
        settings=settings,
        gateway=gateway,
        database=default_database(readonly=True, settings=settings),
    ) as tools:
        graph = build_graph(
            tools=tools.registry(), capabilities=capabilities, gateway=gateway
        )
        yield from stream_question(graph, question, gateway=gateway)


def _events(run: Callable[..., Iterator[Event]], question: str) -> Iterator[str]:
    """Serialize a run's events, emitting a keepalive whenever it goes quiet.

    The run is driven on a worker thread so a silence -- the source fan-out is one of
    about forty seconds -- can be distinguished from a finished stream. A dropped
    connection and a crashed run look identical to a client, so a failure is reported as
    an error event and a clean end rather than as a truncated response.
    """
    channel: queue.Queue[Any] = queue.Queue()

    def drive() -> None:
        try:
            for event in run(question):
                channel.put(event)
        except Exception as exc:  # noqa: BLE001 - reported to the client verbatim
            channel.put(Error(message=str(exc)))
        finally:
            channel.put(_DONE)

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()

    while True:
        try:
            item = channel.get(timeout=PING_INTERVAL_S)
        except queue.Empty:
            yield _ndjson(PING)
            continue
        if item is _DONE:
            return
        yield _ndjson(item.to_json())


def create_app(run: Callable[..., Iterator[Event]] | None = None) -> FastAPI:
    """Build the application. ``run`` is injectable so the transport is testable alone."""
    execute = run if run is not None else _default_run
    application = FastAPI(title="VeriClaim")

    @application.post("/api/ask/stream")
    def ask_stream(request: AskRequest = Body(...)) -> StreamingResponse:
        return StreamingResponse(
            _events(execute, request.question), media_type="application/x-ndjson"
        )

    @application.post("/api/ask")
    def ask(request: AskRequest = Body(...)) -> dict[str, Any]:
        final: Final | None = None
        for event in execute(request.question):
            if isinstance(event, Final):
                final = event
        if final is None:
            raise HTTPException(
                status_code=500, detail="The run produced no answer to return"
            )
        return final.payload

    return application


app = create_app()

__all__ = ["AskRequest", "PING", "app", "create_app"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: PASS, 7 tests.

`test_ask_reports_a_failed_run_rather_than_returning_an_answer` expects a 500 carrying the message. `TestClient` re-raises exceptions from the app by default; if the test errors instead of returning 500, wrap the loop in `ask` so the raised exception becomes `HTTPException(status_code=500, detail=str(exc))`:

```python
        try:
            for event in execute(request.question):
                if isinstance(event, Final):
                    final = event
        except Exception as exc:  # noqa: BLE001 - reported to the caller verbatim
            raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 5: Prove the keepalive fires**

Add to `tests/api/test_app.py`:

```python
def test_the_keepalive_fires_while_a_run_is_quiet(monkeypatch) -> None:
    import vericlaim.api.app as module

    monkeypatch.setattr(module, "PING_INTERVAL_S", 0.01)
    ready = threading.Event()

    class SlowRun(StubRun):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            yield RunStarted(trace_id="t1", question="q")
            ready.wait(0.2)
            yield _final()

    events = _lines(_client(SlowRun()).post("/api/ask/stream", json={"question": "q"}))

    assert any(event["event"] == "ping" for event in events)
    assert [event["event"] for event in events if event["event"] != "ping"] == [
        "run_started",
        "final",
    ]
```

Add `import threading` to the test module's imports.

Run: `uv run pytest tests/api/test_app.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Run the full offline suite and the linter**

Run:
```bash
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
```
Expected: previous total plus 8, zero failures; `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add src/vericlaim/api/app.py tests/api/test_app.py
git commit -m "feat(C-9.2): serve one question over HTTP, streamed and awaited

Two endpoints over the same run. Both are declared def, not async: every
layer beneath them blocks, and async would hold the event loop for the
length of a question.

A failure becomes one error event and a clean end of stream. A client
cannot tell a dropped socket from a crashed run, and the difference is
the whole reason to report it.

The keepalive lives here rather than in the orchestrator, so a run's
event history does not depend on how fast the network was. The gateway is
per-request because its ledger is the reported cost, and the pool is
injected because build_tools would otherwise close the process-wide one
when this request released its tools."
```

---

### Task 4: Trace and executed SQL, proven end to end (C-9.3)

**Files:**
- Test: `tests/api/test_app.py` (append)
- Modify: `tasks/todo.md` (tick C-9.1 to C-9.3, add the review section, record the `build_tools` defect)

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces: no new code. This card proves the exposure the spec claims and records what was left undone.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_app.py`:

```python
def test_the_trace_id_and_the_executed_sql_reach_the_client() -> None:
    # The trace id is how a run in the UI is matched to a run in the tracing backend, and
    # the executed SQL is the claim's audit trail. Neither is derivable downstream, so
    # both are asserted at the boundary rather than assumed.
    payload = {
        "answer": "an answer",
        "cost_usd": 1.0,
        "trace_id": "trace-abc",
        "evidence": [
            {
                "id": "E1",
                "source_type": "sql",
                "locator": {"query": "SELECT 1"},
            }
        ],
    }
    stub = StubRun([RunStarted(trace_id="trace-abc", question="q"), Final(payload=payload)])

    response = _client(stub).post("/api/ask", json={"question": "q"})
    body = response.json()

    assert body["trace_id"] == "trace-abc"
    assert body["evidence"][0]["locator"]["query"] == "SELECT 1"


def test_the_first_streamed_event_carries_the_same_trace_as_the_final() -> None:
    payload = {"answer": "an answer", "cost_usd": 1.0, "trace_id": "trace-abc"}
    stub = StubRun([RunStarted(trace_id="trace-abc", question="q"), Final(payload=payload)])

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    assert events[0]["trace_id"] == events[-1]["trace_id"] == "trace-abc"
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: PASS, 10 tests. These assert the boundary rather than new behaviour, so they pass immediately — that is the point of the card, and it must be stated plainly in the review rather than presented as proof of a fix.

- [ ] **Step 3: Verify against the live stack**

The stack must be up: `docker compose up -d`, then confirm `uv run python scripts/smoke.py` passes.

Run in one terminal:
```bash
uv run uvicorn vericlaim.api.app:app --port 8099
```

In another:
```bash
curl -s -X POST localhost:8099/api/ask \
  -H 'content-type: application/json' \
  -d '{"question":"Are burst pipes covered under HomeSecure?"}' | python3 -m json.tool | head -40
```

Expected: a JSON object with a non-empty `answer`, a non-empty `trace_id`, and a `cost_usd`. Then run the same question through `uv run python scripts/ask.py "Are burst pipes covered under HomeSecure?"` and confirm the two report the **same** cost figure. A disagreement means the API is reading the state's total instead of the ledger's — the one thing this card exists to prevent.

Then confirm the stream:
```bash
curl -sN -X POST localhost:8099/api/ask/stream \
  -H 'content-type: application/json' \
  -d '{"question":"Are burst pipes covered under HomeSecure?"}' | head -20
```

Expected: one JSON object per line, beginning with `run_started` and ending with exactly one `final`.

Record the observed cost figures and event sequence — they are this card's acceptance evidence.

- [ ] **Step 4: Record the phase in `tasks/todo.md`**

Tick C-9.1, C-9.2 and C-9.3. Add a review section covering: what the three cards delivered; the live cost agreement between the API and `scripts/ask.py`; and these two items recorded rather than fixed:

1. **`build_tools` ownership.** `owns_database = database is None`, and an absent database is filled from the process-wide `default_database()`, so `SourceTools.close()` closes a pool it does not own. C-9.2 injects the pool at the call site to avoid it. A later phase should decide whether the semantics themselves are wrong; until then every server-side caller must inject.
2. **C-9.4 and C-9.5 remain open**, deferred until C-10 has a consumer for source-browser endpoints and cancellation.

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_app.py tasks/todo.md
git commit -m "test(C-9.3): prove the trace and the executed SQL reach the client

The trace id matches a run in the UI to a run in the tracing backend, and
the executed SQL is the audit trail behind a claim. Neither is derivable
downstream, so both are asserted at the boundary.

Both tests pass against the code as written: this card proves an exposure
rather than fixing a defect, and saying so is the point. The evidence
that matters is live -- the API and scripts/ask.py report the same cost
for the same question, which is what shows the ledger is being read and
not the state's own total.

Records the build_tools ownership defect that C-9.2 works around at the
call site, and leaves C-9.4 and C-9.5 open until C-10 needs them."
```

---

## Self-Review

**Spec coverage.** C-9.1 protocol → Task 1. C-9.2 endpoints, keepalive, per-request gateway and the pool-ownership workaround → Tasks 2 and 3. C-9.3 trace and executed SQL, and the ledger-cost constraint → Task 4, with the constraint additionally pinned in Tasks 1 and 2. Deferred items (C-9.4, C-9.5, SPA mount) are recorded in Task 4 Step 4. Error mapping → Task 3. All six spec test requirements appear: protocol validation and single terminator (Task 3), ledger cost (Tasks 1, 2, 4), terminal error (Task 3), keepalive plus `ping` absent from `EVENT_NAMES` (Tasks 1, 3), both routes agreeing (Task 3), blank question (Tasks 2, 3).

**Type consistency.** `Final.from_state(state, *, cost_usd)` in Task 1 is called that way in Task 2. `Final(payload=...)` is constructed directly in Tasks 3 and 4, matching the dataclass field. `Stage.from_record` takes a `StageRecord` in both. `stream_question(graph, question, *, gateway, **config)` in Task 2 is what `_default_run` calls in Task 3. `create_app(run=...)` in Task 3 is what the tests inject in Tasks 3 and 4. `PING_INTERVAL_S` is defined in Task 3 and monkeypatched in Task 3 Step 5.

**One dependency worth naming.** Task 2 imports `vericlaim.api.protocol` from `vericlaim.orchestrator.graph`, so the orchestrator depends on the api package. This is deliberate: the protocol module imports only `state` and holds no HTTP, so the dependency runs from transport-shaped names toward the domain and not the reverse. If it ever causes a circular import, move `protocol.py` to `src/vericlaim/events.py` and re-export it from `api/` — the event classes themselves do not change.

**Assumptions checked before the plan was handed over, not left for the implementer.** `tools.registry()` is confirmed against `scripts/ask.py:150` and `tools.py:73` — it returns the `dict[str, SourceTool]` keyed by evidence source type that `build_graph(tools=...)` takes. `Evidence` was corrected after checking `evidence.py:230`: it requires `source_id` and a typed `locator` matching its `source_type`, and its `id` is assigned by `EvidenceSet` on insertion rather than passed by the producer. The fixtures above reflect the real constructor.

**One thing the implementer must still confirm at Task 2 Step 4.** What LangGraph yields under `stream_mode="values"` is a mapping of live channel values, and the fake in `tests/orchestrator/test_stream.py` uses `model_dump()` to imitate it. If `GraphState(**last)` rejects a dumped `EvidenceSet`, the fake is wrong and the implementation is right — fix the fake, and never loosen `stream_question` to accept a shape the real graph does not produce.
