# C-10.1 — Frontend scaffold, NDJSON client, typed event unions

Design for the first card of phase C-10. Binding authority for the implementation plan
that follows.

## Why this card exists

The API has streamed NDJSON since C-9.2 and has had no consumer. Two C-9 cards (C-9.4
source-browser endpoints, C-9.5 cancellation) were deliberately deferred until one
existed, because an endpoint shipped without a consumer is a guess at its own interface.
This card creates the consumer, and with it the `frontend/dist` that the SPA mount has
been waiting on since C-9.2.

It deliberately does **not** build a UI. C-10.2 builds the chat shell. What ships here is
the pipe — project, types, client, mount — plus the smallest possible page that proves a
question can be asked and its events received.

## What is already decided

Settled in C-9.6 and not reopened here:

- `EvidenceEvent.items` stays an array, though the server emits exactly one item per
  event, so batching per source stays a non-breaking change.
- Only shapes with a real contract get typed. `understanding`, `plans`, `collection` and
  `sufficiency` are loose dicts no schema governs and are typed as such.
- The state publishes no run cost. `cost_usd` on the final payload comes from the gateway
  ledger; `stages[*].cost_usd` are per-node figures that do not sum to it.

## Stack

Adapted from `CSRS/frontend`, which is proven and deliberately small: Vite 5, React 18,
TypeScript 5, plain CSS, no state library, no CSS framework. Added here: **Vitest**, for
the one piece with real logic.

```
frontend/
  package.json          scripts: dev, build, test, typecheck
  vite.config.ts        dev :5173, proxy /api -> http://localhost:8000
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx             minimal probe page, not a UI
    styles.css
    types.ts            the wire contract
    lib/ndjson.ts       chunk-safe line reader
    lib/api.ts          ask(), askStream()
    lib/__tests__/ndjson.test.ts
    lib/__tests__/api.test.ts
```

`npm run build` is `tsc --noEmit && vite build`, matching the command CLAUDE.md already
documents. Node is never required to run the Python side.

## The wire contract

`types.ts` mirrors `src/vericlaim/api/protocol.py` as a discriminated union on `event`.
The five members are `run_started`, `stage`, `evidence`, `final`, `error`.

Shapes are taken from the real serializers, not inferred:

```ts
export type SourceType = "policy" | "sql" | "spreadsheet" | "scanned_pdf";

export type Locator =
  | { document: string; page: number | null; section: string | null; chunk_id: string }
  | { document: string; page: number | null; ocr_confidence: number | null;
      ocr_engine: string; escalated: boolean }
  | { workbook: string; sheet: string; row: number | null; a1_range: string }
  | { tables: string[]; executed_sql: string; row_count: number };

export type EvidenceItem = {
  id: string; source_type: SourceType; source_label: string; source_id: string;
  content: string; citation: string; locator: Locator;
  provenance: { tool: string; retrieved_at: string; trace_id: string | null;
                query: string | null };
  confidence: number;
};

export type StageRecord = {
  name: string; detail: Record<string, unknown>; model: string;
  cost_usd: number; latency_ms: number; error: string;
};
```

`Locator` is a union rather than an optional-everything object, so a renderer that
switches on `source_type` cannot read a field the locator does not have. C-10.4 builds one
renderer per source type against exactly this.

The `evidence` event wraps items rather than being one: `{event: "evidence", source:
SourceType, items: EvidenceItem[]}`.

The final payload is flattened — `{event: "final", ...state}` — not nested. Sixteen keys,
each accounted for:

```ts
export type CitationReport = {
  ok: boolean; resolved: string[]; unresolved: string[]; malformed: string[];
  uncited: string[]; precision: number; coverage: number;
  verified: boolean; regenerated: boolean; degraded: boolean; problems: string[];
};

export type RoutingDecision = {
  sources: SourceType[]; confidence: number; reason: string;
  out_of_scope: boolean; needs_clarification: boolean; clarification_question: string;
};

export type FinalEvent = {
  event: "final";
  question: string; answer: string; trace_id: string;
  evidence: EvidenceItem[]; sources_used: SourceType[];
  routing: RoutingDecision | null;          // null before routing has run
  citations: CitationReport;
  stages: StageRecord[]; failures: string[];
  replans: number; latency_ms: number;
  cost_usd: number;                         // gateway ledger, not a stage sum
  understanding: Record<string, unknown>;
  plans: Record<string, unknown>;
  collection: Record<string, unknown>;
  sufficiency: Record<string, unknown>;
};
```

`citations` and `routing` get real types because both have a definite shape in Python —
`CitationReport` is a frozen dataclass and `RoutingDecision` a pydantic model. The four
remaining dicts have no schema and stay `Record<string, unknown>`. `routing` is nullable
because a run that fails before the router leaves it unset.

`cost_usd` is present on the payload but is **not** part of `GraphState.to_dict()`; it is
added by `Final.from_state` from the gateway ledger (C-9.6). A client must never derive a
run total by summing `stages[*].cost_usd`, which omits every model call a source tool
makes and reads `$0.00` on runs that genuinely cost money.

### Drift guard

A **Python** test reads `frontend/src/types.ts`, extracts every `event: "<name>"` literal,
and asserts set-equality with `EVENT_NAMES`. It lives in the Python suite so it runs on
every `uv run pytest`, with no Node required. Adding an event on either side alone fails
the suite.

The test asserts event names only. It does not attempt to check field-level parity, which
would be a schema generator wearing a test's clothes and would drift into maintenance.

## The client

`lib/ndjson.ts` adapts CSRS's `readNdjson` with its behaviour intact: a `TextDecoder` over
the reader, a buffer split on `\n` keeping the trailing partial line, a final flush of
whatever remains after the stream closes, and `AbortError` distinguished from a transport
`TypeError`. That code is correct and is not being rewritten.

`lib/api.ts` sits above it and owns the one rule that is VeriClaim's own:

```ts
if ((frame as { event?: string }).event === "ping") return;
```

Pings are dropped before a frame reaches typed code, so `Event` never contains one. This
keeps the server's deliberate choice true on the client: a keepalive is a property of the
connection, not of the run, and a client that surfaced one would be showing the user the
network. `EVENT_NAMES` has no `ping` and neither does `types.ts`.

`askStream(question, onEvent, signal)` resolves with the `final` payload. It throws on an
`error` event, and throws if the stream ends having yielded neither `final` nor `error` —
the "never neither" guard the awaited route has and the streaming route lacks, recorded as
a parked minor in C-9's review and closed here on the client side.

`ask(question, signal)` posts to `/api/ask` and returns the same payload type, so both
routes are proven to agree on shape.

An unknown `event` value is ignored rather than thrown on: a client that hard-fails on an
event a newer server added would make every protocol addition a breaking change.

## The SPA mount

The hazard is exactly what C-8.13 deleted two entry points for — a declared thing that
fails. `StaticFiles(directory=...)` raises when the directory is absent, so an unbuilt
checkout would break the whole API.

Therefore the mount is conditional and last:

- registered only when `frontend/dist/index.html` exists;
- mounted at `/` **after** every `/api` route, so it can never shadow them;
- `html=True`, so `/` serves `index.html`.

With no `dist/`, the API behaves exactly as it does today. `uv run pytest` passes on a
machine with no Node installed. Deep-link fallback for client-side routes is not needed
yet — there are no client routes — and is C-10.5's problem when the source browser gains
them.

## Testing

Vitest, over the parser, because that is where silent bugs live:

- an event split across two chunks arrives as one event;
- a final line with no trailing newline is still emitted;
- `ping` frames never reach `onEvent`;
- an `error` event rejects;
- a stream ending with neither `final` nor `error` rejects;
- an unknown event name is ignored, not thrown.

Python side: the drift guard, and a test that the app still constructs and serves
`/api/ask` when `frontend/dist` does not exist.

## Out of scope

Any real UI (C-10.2), the trace rail (C-10.3), evidence cards (C-10.4), the source browser
(C-10.5), the metadata panel (C-10.6). Cancellation is C-9.5, but `askStream` accepts an
`AbortSignal` so that card has something to connect to. Authentication, CORS beyond local
development, and rate limiting remain out of scope for a single-operator demo.

## Acceptance

`npm run build` produces `frontend/dist`; `npm test` passes; `uv run pytest` passes both
with and without `dist/` present; and with the API running, the probe page sends the
flagship question and receives `run_started`, `stage`, `evidence` and exactly one `final`,
with no `ping` reaching application code.
