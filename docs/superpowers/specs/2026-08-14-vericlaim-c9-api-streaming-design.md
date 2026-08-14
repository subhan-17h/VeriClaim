# C-9 — HTTP API and NDJSON streaming

Design for phase C-9. Approved 2026-08-14.

## What this phase is for

`run_question` answers one question and returns a finished `GraphState`. C-9 puts an HTTP
surface over that, and a stream that reports what the run is doing while it does it. C-10's
frontend consumes both, so the event protocol defined here is the contract that phase builds
against. A contract fixed after the UI exists is fixed in two places.

## Scope

**Built in this phase:** C-9.1 (protocol), C-9.2 (endpoints), C-9.3 (trace and executed SQL).

**Deferred, deliberately:** C-9.4 (source-browser endpoints with `#page=N` anchoring) and C-9.5
(client cancellation). Both are built when C-10 has a consumer for them. An endpoint shipped
before anything calls it is an endpoint designed against a guess.

**Not built, and not deferred either — simply out of scope:** authentication, CORS beyond local
development, rate limiting, and multi-user session state. This is a single-operator demo
system; none of those have a requirement behind them.

**The SPA mount named in the C-9.2 card is not built here.** No `frontend/` directory exists
yet, and a mount pointing at a missing `dist/` is exactly the "declared thing that fails" that
C-8.13 deleted two entry points for. It belongs to C-10.1, where the build output it serves
first exists.

## Architecture

Three new files under `src/vericlaim/api/`:

| File | Responsibility |
| --- | --- |
| `protocol.py` | The event types and their serialization. No HTTP, no orchestrator imports beyond types. |
| `app.py` | FastAPI application: the two endpoints, request/response models, keepalive, error mapping. |
| `__init__.py` | Public exports. |

One addition to `src/vericlaim/orchestrator/graph.py`: `stream_question()`, a sibling to
`run_question`.

### Where streaming lives, and why

`stream_question(graph, question, **config)` wraps LangGraph's compiled-graph `.stream()`,
opens the same root trace span `run_question` opens, and yields typed protocol events. `app.py`
serializes those to NDJSON and does nothing else with them.

The alternative considered was having `app.py` call `graph.stream()` directly. It was rejected:
the API would re-implement the root-span and run-summary logic that `run_question` owns, giving
two entry points that can disagree about what a run is, and making streaming impossible to test
without standing up HTTP. The orchestrator owns its own contract; the API is transport.

Injecting callbacks or an event bus into the nodes was rejected outright. It would touch all
eight nodes to obtain what `.stream()` already provides.

## The event protocol (C-9.1)

A closed set of five events. Each is a frozen dataclass with a `to_json()` returning a flat
JSON object carrying an `event` discriminator. One event per line, `application/x-ndjson`.

| `event` | When | Payload |
| --- | --- | --- |
| `run_started` | Once, first | `trace_id`, `question` |
| `stage` | Each graph node completes | `name`, `model`, `cost_usd`, `latency_ms`, `error`, `detail` |
| `evidence` | A source returns | `source`, serialized evidence items |
| `final` | Once, last, on success | answer, evidence, citations, sources consulted, failures, cost |
| `error` | Once, last, on terminal failure | `message` |

A stream ends with exactly one `final` or one `error`, never both and never neither. The
frontend's typed event unions in C-10.1 are generated against this table, so it is the single
source of truth for both sides.

`ping` is **not** a protocol event. It is a transport keepalive emitted by `app.py` only, and
carries no run information. Clients ignore it.

`GraphState.to_dict()` already supplies most of the `final` payload and is reused rather than
duplicated.

## The endpoints (C-9.2)

`POST /api/ask` — runs the question, returns the `final` payload as one JSON object.

`POST /api/ask/stream` — returns `StreamingResponse(..., media_type="application/x-ndjson")`.

Both are declared `def`, not `async def`, so Starlette runs them in a threadpool. Everything
beneath them blocks: psycopg, Chroma, Ollama, and the provider SDKs. Declaring them `async`
would block the event loop for the length of a run. This follows the pattern proven in the
CSRS reference implementation.

### Keepalive

A four-source run is roughly a minute, and the source fan-out is a single silence of about
forty seconds. Without keepalive a proxy or browser may drop the connection mid-run.

`app.py` runs the `stream_question` iterator on a thread feeding a `queue.Queue`. The response
generator calls `get(timeout=...)` and emits `{"event": "ping"}` when the timeout expires.
`stream_question` itself stays free of pings: keepalive is a property of the transport, not of
the run, and putting it in the orchestrator would make the run's event history depend on how
fast the network was.

### Gateway and tool lifecycle

`Gateway` already carries a per-instance `ledger` and a shared process-wide `session_ledger`.
That is exactly the shape a server needs: **one `Gateway` per request** gives correct
per-request cost and per-request budget enforcement, while the session ledger continues to
enforce the global ceiling across every request.

The gateway is bound into the source tools when they are constructed, so a per-request gateway
means a per-request `build_tools`. That is affordable — `build_tools` opens nothing, the values
catalogue profiles lazily, and the Postgres pool is process-wide.

**One defect must be handled at the call site.** `build_tools` sets `owns_database = database is
None`, and when no database is injected it takes the process-wide pool from
`default_database()`. `SourceTools.close()` then closes that shared pool. A CLI never notices,
because the process exits immediately afterwards. A server calling `open_tools()` per request
would have the first request's teardown close the pool for every request after it.

C-9.2 therefore injects the pool explicitly:

```python
build_tools(settings=settings, gateway=gateway, database=default_database(readonly=True))
```

which sets `owns_database = False` and leaves teardown correct. The fix is made at the call site
rather than by changing `build_tools`' ownership semantics, because the CLI depends on the
current behaviour and this phase has no mandate to change it. The defect is recorded in
`tasks/todo.md` so a later phase can decide whether the semantics themselves are wrong.

### Error mapping

A terminal failure inside a run becomes one `error` event and a normal end of stream, not a
truncated connection: a client cannot distinguish a dropped socket from a crashed run, and the
difference matters. Gateway budget, paid-fallback and quota errors carry their own messages,
which are already written for a reader and are passed through unchanged.

For `POST /api/ask`, the same failures map to a non-2xx response with the same message.

A blank question is rejected by `GraphState`'s own validator before any model is reached, and
surfaces as a 422.

## Trace and executed SQL (C-9.3)

Mostly exposure rather than new plumbing:

- `trace_id` is already on `GraphState` and has been written since C-8.10. It appears on
  `run_started` and on `final`.
- The executed SQL already rides on each SQL evidence item's locator, and reaches the client
  through the serialized evidence in `evidence` and `final`.

**One binding constraint, carried from the C-8 review.** Cost reported by the API is read from
`gateway.ledger.total_cost_usd`, never from `state.total_cost_usd`. Source tools make their own
model calls — the SQL planner, generator and refiner among them — and those are recorded on no
graph stage, so the state under-reports a four-source question by most of what it spent.
`scripts/ask.py` and `scripts/replay.py` already read the ledger; the API must agree with them.

## Testing

FastAPI's `TestClient`, against a fake graph and a fake gateway. No models, no database, no
quota, and therefore markable offline alongside the rest of the suite.

The tests that must exist:

1. Every emitted event validates against the protocol, and the stream ends with exactly one
   `final` or one `error`.
2. `final` reports the gateway ledger's cost, not the state's total. Asserted with a fake whose
   two values deliberately differ, so an implementation reading the wrong one fails.
3. A terminal error mid-run produces one `error` event and a clean end of stream.
4. The keepalive fires on a stream that goes silent, and `ping` never appears in the protocol's
   own event set.
5. `POST /api/ask` and `POST /api/ask/stream` agree: the same question yields the same answer
   and the same citations by both routes.
6. A blank question is rejected before any model call.

## Acceptance

- Both endpoints answer the flagship question against the live stack.
- The stream reports stages as they happen and ends with exactly one `final`.
- Reported cost matches `scripts/ask.py` for the same question.
- The offline suite and ruff are green.
- Per-request gateway isolation holds: two sequential requests report their own costs, and
  neither closes the shared pool.

## Conventions

`CLAUDE.md` in full. In particular: one task per commit taken only once acceptance is
demonstrated; `C-` task ids; no commit trailers of any kind; no prompt naming the corpus; no
hard-coded example behaviour; decision-support language only; reference repos stay read-only.
