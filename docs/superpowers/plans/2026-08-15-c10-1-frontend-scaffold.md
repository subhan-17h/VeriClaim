# C-10.1 Frontend Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the frontend project, the typed NDJSON client, and the conditional SPA mount, so phase C-10 has a consumer for the API that has streamed since C-9.2.

**Architecture:** A Vite/React/TypeScript project under `frontend/`, adapted from the proven `CSRS/frontend`. A chunk-safe NDJSON reader (`lib/ndjson.ts`) feeds a typed client (`lib/api.ts`) that drops `ping` keepalives before frames reach typed code. FastAPI mounts the built SPA only when it exists, registered after every `/api` route. No real UI ships here; C-10.2 builds the shell.

**Tech Stack:** Vite 5, React 18, TypeScript 5, Vitest 2, plain CSS. Python side: FastAPI `StaticFiles`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-15-vericlaim-c10-1-frontend-scaffold-design.md`

## Global Constraints

- Vite 5, React 18, TypeScript 5, Vitest 2. No state library, no CSS framework, no UI kit.
- `npm run build` is exactly `tsc --noEmit && vite build`. CLAUDE.md documents this command.
- **No new Python dependencies.** FastAPI's `StaticFiles` is already available.
- `uv run pytest -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"` must pass **both with and without** `frontend/dist` present, on a machine with no Node installed.
- `ping` must never reach typed application code. `EVENT_NAMES` and `types.ts` both exclude it.
- `EvidenceEvent.items` stays an array (C-9.6 decision), even though the server emits exactly one item per event.
- A client must never sum `stages[*].cost_usd` for a run total; `cost_usd` on the final payload is the gateway ledger's figure (C-9.6).
- ASCII only in all source. No invented data.
- Never commit `frontend/node_modules/` or `frontend/dist/` — `.gitignore` already excludes both.
- Reference repos `/Users/rowdy/Projects/work/unibot-endgame` and `/Users/rowdy/Projects/work/CIL/CSRS` are READ-ONLY. Copy out; never edit in place.
- Commit messages carry no trailers of any kind. Task ids use the `C-` series.

## File Structure

| File | Responsibility |
|---|---|
| `frontend/package.json` | Dependencies and the four scripts |
| `frontend/vite.config.ts` | Dev server on 5173, `/api` proxy to 8000, Vitest config |
| `frontend/tsconfig.json` | Strict TypeScript |
| `frontend/index.html` | Single mount point |
| `frontend/src/main.tsx` | React root |
| `frontend/src/App.tsx` | Probe page — proves the pipe, is not a UI |
| `frontend/src/styles.css` | Minimal readable styling |
| `frontend/src/types.ts` | The wire contract: five events plus payload shapes |
| `frontend/src/lib/ndjson.ts` | Chunk-safe line reader over a Response body |
| `frontend/src/lib/api.ts` | `ask()` / `askStream()`, ping filtering, terminator guard |
| `frontend/src/lib/__tests__/ndjson.test.ts` | Parser behaviour |
| `frontend/src/lib/__tests__/api.test.ts` | Client behaviour |
| `tests/api/test_type_parity.py` | Python-side drift guard |
| `src/vericlaim/api/app.py` | Conditional SPA mount, registered last |
| `tests/api/test_app.py` | Mount tests appended to the existing module |

---

### Task 1: Scaffold the project

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/vite-env.d.ts`
- Test: `frontend/src/lib/__tests__/smoke.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `npm run build`, `npm test`, `npm run typecheck`. Later tasks add files under `frontend/src/`.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "vericlaim-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.6.3",
    "vite": "^5.4.11",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: Create `frontend/vite.config.ts`**

The proxy target is port 8000, uvicorn's default. The build writes to `dist/`, which the Python side mounts only if present.

```ts
/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000"
    }
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"]
  }
});
```

- [ ] **Step 3: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vite/client", "vitest/globals"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 4: Create `frontend/tsconfig.node.json`**

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "noEmit": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>VeriClaim</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create `frontend/src/vite-env.d.ts`**

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 7: Create `frontend/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing its #root mount point");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 8: Create a placeholder `frontend/src/App.tsx`**

Task 6 replaces this with the probe page. It exists now only so the scaffold builds.

```tsx
export default function App() {
  return <main className="shell">VeriClaim</main>;
}
```

- [ ] **Step 9: Create `frontend/src/styles.css`**

```css
:root {
  color-scheme: dark;
  --bg: #14161a;
  --fg: #e8e8e8;
  --muted: #9aa0a6;
  --line: #2a2e35;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
}

.shell { max-width: 60rem; margin: 0 auto; padding: 2rem 1.5rem; }
.row { display: flex; gap: 0.5rem; }
.row input { flex: 1; }

input, button {
  padding: 0.5rem 0.75rem;
  background: #1c1f25;
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: 6px;
  font: inherit;
}

button:disabled { opacity: 0.5; }
.status { color: var(--muted); margin: 0.75rem 0; }
.frames { border: 1px solid var(--line); border-radius: 6px; padding: 0.75rem; }
.frame { border-bottom: 1px solid var(--line); padding: 0.35rem 0; }
.frame:last-child { border-bottom: 0; }
.name { color: #7fd4a0; }
```

- [ ] **Step 10: Write the smoke test at `frontend/src/lib/__tests__/smoke.test.ts`**

Proves the test runner is wired before any real logic depends on it.

```ts
import { describe, expect, it } from "vitest";

describe("the test runner", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 11: Install and verify**

```bash
cd frontend && npm install
npm test
npm run build
```

Expected: `npm test` reports 1 passed. `npm run build` succeeds and creates `frontend/dist/index.html`.

- [ ] **Step 12: Confirm the build artifacts are ignored**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim && git status --porcelain
```

Expected: no `frontend/node_modules` or `frontend/dist` entries. If either appears, fix `.gitignore` rather than the commit.

- [ ] **Step 13: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/tsconfig.json frontend/tsconfig.node.json frontend/index.html frontend/src
git commit -F - <<'MSG'
feat(C-10.1): scaffold the frontend project

Vite, React 18 and TypeScript, adapted from the CSRS frontend, which is
proven and deliberately small: plain CSS, no state library, no UI kit.

Vitest is the one addition. CSRS ships no frontend tests, but the NDJSON
reader this project needs has real logic -- chunk boundaries, a trailing
line with no newline, keepalive frames -- and that is where silent bugs
live.

npm run build is tsc --noEmit && vite build, the command CLAUDE.md
already documents.
MSG
```

---

### Task 2: The wire contract and its drift guard

**Files:**
- Create: `frontend/src/types.ts`, `tests/api/test_type_parity.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond the project existing.
- Produces: `SourceType`, `Locator`, `EvidenceItem`, `StageRecord`, `CitationReport`, `RoutingDecision`, `RunStartedEvent`, `StageEvent`, `EvidenceEvent`, `FinalEvent`, `ErrorEvent`, `Event`. Tasks 4 and 6 import `Event` and `FinalEvent`.

- [ ] **Step 1: Write the failing Python parity test at `tests/api/test_type_parity.py`**

```python
"""The TypeScript union and the Python protocol must name the same events.

C-10's client is written against ``api/protocol.py``. Two hand-written definitions
drift, and the first symptom of drift is a shape error in a browser rather than a
failing test. This guard lives in the Python suite so it runs on every pytest, with
no Node required.

It checks event names only. Field-level parity would be a schema generator wearing a
test's clothes, and would need maintaining for five small shapes.
"""

from __future__ import annotations

import re

import pytest

from vericlaim.api.protocol import EVENT_NAMES
from vericlaim.config import PROJECT_ROOT

TYPES_TS = PROJECT_ROOT / "frontend" / "src" / "types.ts"

_EVENT_LITERAL = re.compile(r'event:\s*"([a-z_]+)"')


def test_the_typescript_union_names_exactly_the_protocol_events() -> None:
    if not TYPES_TS.is_file():
        pytest.fail(f"{TYPES_TS} is missing; the client contract must exist")

    declared = set(_EVENT_LITERAL.findall(TYPES_TS.read_text(encoding="utf-8")))

    assert declared == set(EVENT_NAMES)


def test_the_client_contract_never_names_the_keepalive() -> None:
    """``ping`` is a property of the connection, not of the run. A client that typed it
    would be inviting callers to read the network as if it were run information."""
    declared = set(_EVENT_LITERAL.findall(TYPES_TS.read_text(encoding="utf-8")))

    assert "ping" not in declared
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_type_parity.py -v`
Expected: FAIL — `types.ts is missing; the client contract must exist`.

- [ ] **Step 3: Create `frontend/src/types.ts`**

```ts
// The wire contract, mirroring src/vericlaim/api/protocol.py.
//
// `ping` is deliberately absent. A keepalive is a property of the connection, not of
// the run; lib/api.ts drops it before a frame reaches any of these types.

export type SourceType = "policy" | "sql" | "spreadsheet" | "scanned_pdf";

export type PolicyLocator = {
  document: string;
  page: number | null;
  section: string | null;
  chunk_id: string;
};

export type ScannedLocator = {
  document: string;
  page: number | null;
  ocr_confidence: number | null;
  ocr_engine: string;
  escalated: boolean;
};

export type SpreadsheetLocator = {
  workbook: string;
  sheet: string;
  row: number | null;
  a1_range: string;
};

export type SqlLocator = {
  tables: string[];
  executed_sql: string;
  row_count: number;
};

// A union rather than an optional-everything object, so a renderer switching on
// source_type cannot read a field the locator does not have. C-10.4 builds one
// renderer per source type against exactly this.
export type Locator =
  | PolicyLocator
  | ScannedLocator
  | SpreadsheetLocator
  | SqlLocator;

export type Provenance = {
  tool: string;
  retrieved_at: string;
  trace_id: string | null;
  query: string | null;
};

export type EvidenceItem = {
  id: string;
  source_type: SourceType;
  source_label: string;
  source_id: string;
  content: string;
  citation: string;
  locator: Locator;
  provenance: Provenance;
  confidence: number;
};

// cost_usd is this stage's own model call and is accurate. It must never be summed
// into a run total: a source tool's model calls reach no stage. See C-9.6.
export type StageRecord = {
  name: string;
  detail: Record<string, unknown>;
  model: string;
  cost_usd: number;
  latency_ms: number;
  error: string;
};

export type CitationReport = {
  ok: boolean;
  resolved: string[];
  unresolved: string[];
  malformed: string[];
  uncited: string[];
  precision: number;
  coverage: number;
  verified: boolean;
  regenerated: boolean;
  degraded: boolean;
  problems: string[];
};

export type RoutingDecision = {
  sources: SourceType[];
  confidence: number;
  reason: string;
  out_of_scope: boolean;
  needs_clarification: boolean;
  clarification_question: string;
};

export type RunStartedEvent = {
  event: "run_started";
  trace_id: string;
  question: string;
};

export type StageEvent = {
  event: "stage";
  name: string;
  model: string;
  cost_usd: number;
  latency_ms: number;
  error: string;
  detail: Record<string, unknown>;
};

// items is an array though the server emits exactly one per event, so batching per
// source stays a non-breaking change. See C-9.6.
export type EvidenceEvent = {
  event: "evidence";
  source: SourceType;
  items: EvidenceItem[];
};

export type FinalEvent = {
  event: "final";
  question: string;
  answer: string;
  trace_id: string;
  evidence: EvidenceItem[];
  sources_used: SourceType[];
  routing: RoutingDecision | null;
  citations: CitationReport;
  stages: StageRecord[];
  failures: string[];
  replans: number;
  latency_ms: number;
  // The gateway ledger's figure, not a sum of stages.
  cost_usd: number;
  understanding: Record<string, unknown>;
  plans: Record<string, unknown>;
  collection: Record<string, unknown>;
  sufficiency: Record<string, unknown>;
};

export type ErrorEvent = {
  event: "error";
  message: string;
};

export type Event =
  | RunStartedEvent
  | StageEvent
  | EvidenceEvent
  | FinalEvent
  | ErrorEvent;

export const EVENT_NAMES = [
  "run_started",
  "stage",
  "evidence",
  "final",
  "error"
] as const;
```

- [ ] **Step 4: Run the parity test to verify it passes**

Run: `uv run pytest tests/api/test_type_parity.py -v`
Expected: 2 passed.

- [ ] **Step 5: Verify the types compile**

Run: `cd frontend && npm run typecheck`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/types.ts tests/api/test_type_parity.py
git commit -F - <<'MSG'
feat(C-10.1): define the client's wire contract and guard it against drift

types.ts mirrors api/protocol.py as a discriminated union on event. The
shapes are taken from the real serializers rather than inferred, so a
Locator is a union and a renderer switching on source_type cannot read a
field the locator does not have.

Only shapes with a contract are typed. citations and routing get real
types because CitationReport is a frozen dataclass and RoutingDecision a
pydantic model; understanding, plans, collection and sufficiency have no
schema and stay Record<string, unknown> rather than inventing one that
drifts.

The guard is a Python test, so it runs on every pytest with no Node
installed: it extracts the event literals from types.ts and asserts
set-equality with EVENT_NAMES, and separately asserts that ping appears
in neither.
MSG
```

---

### Task 3: The chunk-safe NDJSON reader

**Files:**
- Create: `frontend/src/lib/ndjson.ts`, `frontend/src/lib/__tests__/ndjson.test.ts`
- Delete: `frontend/src/lib/__tests__/smoke.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `readNdjson(response: Response, onFrame: (frame: unknown) => void, signal?: AbortSignal): Promise<void>`, `StreamUnavailableError`, `StreamReadError`. Task 4 imports all three.

- [ ] **Step 1: Write the failing tests at `frontend/src/lib/__tests__/ndjson.test.ts`**

`streamOf` builds a Response whose body delivers exactly the chunks given, so a test can place a split anywhere — including mid-JSON.

```ts
import { describe, expect, it } from "vitest";

import { readNdjson, StreamUnavailableError } from "../ndjson";

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    }
  });
  return new Response(body);
}

describe("readNdjson", () => {
  it("emits one frame per line", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n{"event":"b"}\n']), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a" }, { event: "b" }]);
  });

  it("reassembles a frame split across chunk boundaries", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"eve', 'nt":"a","n":1}', "\n"]), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a", n: 1 }]);
  });

  it("emits a final line that has no trailing newline", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n{"event":"b"}']), (f) =>
      frames.push(f)
    );
    expect(frames).toEqual([{ event: "a" }, { event: "b" }]);
  });

  it("ignores blank lines", async () => {
    const frames: unknown[] = [];
    await readNdjson(streamOf(['{"event":"a"}\n\n\n']), (f) => frames.push(f));
    expect(frames).toEqual([{ event: "a" }]);
  });

  it("rejects a response that carries no body", async () => {
    const bodyless = new Response(null, { status: 204 });
    await expect(readNdjson(bodyless, () => {})).rejects.toBeInstanceOf(
      StreamUnavailableError
    );
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `../ndjson`.

- [ ] **Step 3: Create `frontend/src/lib/ndjson.ts`**

Adapted from `CSRS/frontend/src/lib/api.ts`'s `readNdjson`, whose buffering and abort handling are correct and are not being rewritten. The change here is that it yields raw frames rather than typed events, leaving `lib/api.ts` to decide what a frame means.

```ts
// A line reader over an NDJSON response body.
//
// Adapted from the CSRS frontend. Chunk boundaries fall wherever the network puts
// them, including mid-JSON, so lines are buffered and only complete ones parsed. The
// buffer is flushed after the stream closes, because a server is not obliged to end
// its last line with a newline.

export class StreamUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamUnavailableError";
  }
}

export class StreamReadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamReadError";
  }
}

export async function readNdjson(
  response: Response,
  onFrame: (frame: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  if (!response.body) {
    throw new StreamUnavailableError(
      "The streaming response did not include a body."
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const processLine = (line: string) => {
    if (!line.trim()) return;
    onFrame(JSON.parse(line));
  };

  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        // An aborted read is the caller's own doing and must stay distinguishable
        // from a transport failure, which is a TypeError from fetch.
        if (error instanceof Error && error.name === "AbortError") throw error;
        if (signal?.aborted) {
          throw new DOMException("The operation was aborted.", "AbortError");
        }
        if (error instanceof TypeError) throw new StreamReadError(error.message);
        throw error;
      }

      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(processLine);
    }

    buffer += decoder.decode();
    processLine(buffer);
  } finally {
    reader.releaseLock();
  }
}
```

- [ ] **Step 4: Delete the smoke test**

```bash
rm frontend/src/lib/__tests__/smoke.test.ts
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npm test`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/lib/ndjson.ts frontend/src/lib/__tests__
git rm --cached frontend/src/lib/__tests__/smoke.test.ts 2>/dev/null || true
git commit -F - <<'MSG'
feat(C-10.1): read NDJSON safely across chunk boundaries

Adapted from the CSRS frontend, whose buffering and abort handling are
correct and are not rewritten here. It yields raw frames rather than
typed events, leaving the client above it to decide what a frame means.

Chunk boundaries fall wherever the network puts them, including mid-JSON,
so lines are buffered and only complete ones parsed, and the buffer is
flushed once the stream closes because a server is not obliged to end its
last line with a newline. Tests place a split mid-token and omit the
trailing newline, which is where a reader that looks correct usually is
not. An aborted read stays distinguishable from a transport failure.
MSG
```

---

### Task 4: The typed client

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/__tests__/api.test.ts`

**Interfaces:**
- Consumes: `readNdjson`, `StreamUnavailableError` from Task 3; `Event`, `FinalEvent` from Task 2.
- Produces: `askStream(question, onEvent, signal?): Promise<FinalEvent>`, `ask(question, signal?): Promise<FinalEvent>`, `ApiError`. Task 6 imports `askStream` and `ApiError`.

- [ ] **Step 1: Write the failing tests at `frontend/src/lib/__tests__/api.test.ts`**

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { ask, askStream } from "../api";
import type { Event } from "../../types";

function ndjsonResponse(lines: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(line + "\n"));
      controller.close();
    }
  });
  return new Response(body, { status: 200 });
}

const FINAL = '{"event":"final","question":"q","answer":"a","trace_id":"t"}';

function stubFetch(response: Response) {
  const fetchStub = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("askStream", () => {
  it("returns the final event", async () => {
    stubFetch(ndjsonResponse(['{"event":"run_started","trace_id":"t","question":"q"}', FINAL]));
    const seen: Event[] = [];

    const final = await askStream("q", (event) => seen.push(event));

    expect(final.event).toBe("final");
    expect(final.answer).toBe("a");
    expect(seen.map((e) => e.event)).toEqual(["run_started", "final"]);
  });

  it("never hands a ping to the caller", async () => {
    stubFetch(ndjsonResponse(['{"event":"ping"}', '{"event":"ping"}', FINAL]));
    const seen: Event[] = [];

    await askStream("q", (event) => seen.push(event));

    expect(seen.map((e) => e.event)).toEqual(["final"]);
  });

  it("throws the message carried by an error event", async () => {
    stubFetch(ndjsonResponse(['{"event":"error","message":"the run failed"}']));

    await expect(askStream("q", () => {})).rejects.toThrow("the run failed");
  });

  it("throws when the stream ends with neither final nor error", async () => {
    stubFetch(ndjsonResponse(['{"event":"run_started","trace_id":"t","question":"q"}']));

    await expect(askStream("q", () => {})).rejects.toThrow(
      "ended without a final event"
    );
  });

  it("ignores an event name it does not know", async () => {
    stubFetch(ndjsonResponse(['{"event":"invented_later"}', FINAL]));
    const seen: Event[] = [];

    await askStream("q", (event) => seen.push(event));

    expect(seen.map((e) => e.event)).toEqual(["final"]);
  });

  it("posts the question to the streaming endpoint", async () => {
    const fetchStub = stubFetch(ndjsonResponse([FINAL]));

    await askStream("are burst pipes covered?", () => {});

    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/ask/stream");
    expect(JSON.parse(String(init.body))).toEqual({
      question: "are burst pipes covered?"
    });
  });
});

describe("ask", () => {
  it("returns the awaited payload", async () => {
    stubFetch(
      new Response(JSON.stringify({ question: "q", answer: "a", trace_id: "t" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    const final = await ask("q");

    expect(final.answer).toBe("a");
  });

  it("raises with the server's detail when the request fails", async () => {
    stubFetch(
      new Response(JSON.stringify({ detail: "A run needs a question to answer" }), {
        status: 422,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(ask("")).rejects.toThrow("A run needs a question to answer");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `../api`.

- [ ] **Step 3: Create `frontend/src/lib/api.ts`**

The mutable `outcome` object is deliberate: assigning to a closed-over `let` from inside a callback defeats TypeScript's narrowing, and the object keeps the types honest without a cast.

```ts
import { readNdjson, StreamUnavailableError } from "./ndjson";
import { EVENT_NAMES } from "../types";
import type { Event, FinalEvent } from "../types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const KNOWN_EVENTS = new Set<string>(EVENT_NAMES);

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // Proxy failures answer in HTML, so the status stays the useful fallback.
  }
  return `Request failed with HTTP ${response.status}`;
}

function post(path: string, question: string, signal?: AbortSignal): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal
  });
}

/** Run one question, reporting each event as it arrives. */
export async function askStream(
  question: string,
  onEvent: (event: Event) => void,
  signal?: AbortSignal
): Promise<FinalEvent> {
  const response = await post("/api/ask/stream", question, signal);
  if (!response.ok) {
    throw new StreamUnavailableError(await errorDetail(response));
  }

  const outcome: { final: FinalEvent | null; failure: string | null } = {
    final: null,
    failure: null
  };

  await readNdjson(
    response,
    (frame) => {
      const name = (frame as { event?: unknown }).event;
      if (typeof name !== "string") return;
      // A keepalive is a property of the connection, not of the run. Dropping it
      // here is what keeps `Event` free of it.
      if (name === "ping") return;
      // An event a newer server added must not break an older client, or every
      // protocol addition becomes a breaking change.
      if (!KNOWN_EVENTS.has(name)) return;

      const event = frame as Event;
      onEvent(event);
      if (event.event === "final") outcome.final = event;
      if (event.event === "error") outcome.failure = event.message;
    },
    signal
  );

  if (outcome.failure !== null) throw new Error(outcome.failure);
  if (outcome.final === null) {
    // The awaited route enforces this server-side; the streaming route does not.
    throw new Error("The stream ended without a final event.");
  }
  return outcome.final;
}

/** Run one question and wait for the whole answer. */
export async function ask(
  question: string,
  signal?: AbortSignal
): Promise<FinalEvent> {
  const response = await post("/api/ask", question, signal);
  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  return (await response.json()) as FinalEvent;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test`
Expected: 13 passed (5 from Task 3, 8 here).

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/api.test.ts
git commit -F - <<'MSG'
feat(C-10.1): ask one question over the typed client

askStream resolves with the final payload, throws the message an error
event carries, and throws when a stream ends with neither -- the "never
neither" guard the awaited route enforces server-side and the streaming
route does not, recorded as a parked minor in C-9's review and closed
here on the client side.

Two filtering rules sit above the reader. A ping is dropped before the
frame reaches typed code, which is what keeps Event free of it and keeps
the server's choice true on the client: a keepalive describes the
connection, not the run. An unrecognised event name is ignored rather
than thrown on, so adding an event to the protocol is not a breaking
change for an older client.

ask() posts to the awaited route and returns the same type, so both
routes are proven to agree on shape.
MSG
```

---

### Task 5: Serve the built SPA, but only if it exists

**Files:**
- Modify: `src/vericlaim/api/app.py`
- Test: `tests/api/test_app.py` (append)

**Interfaces:**
- Consumes: `create_app` from the existing module.
- Produces: `create_app(run=None, dist=None)` — `dist` is injectable so tests need no build. `SPA_DIST` is the default location.

- [ ] **Step 1: Write the failing tests, appended to `tests/api/test_app.py`**

```python
class TestTheSpaMount:
    """The API must run from a checkout that has never been built.

    StaticFiles raises when its directory is absent, so mounting unconditionally
    would make an unbuilt clone fail to import -- the same "declared thing that
    fails" C-8.13 deleted two entry points for.
    """

    def test_the_api_works_when_the_frontend_was_never_built(self, tmp_path) -> None:
        app = create_app(run=StubRun([_final()]), dist=tmp_path / "absent")

        response = TestClient(app).post("/api/ask", json={"question": "is it covered?"})

        assert response.status_code == 200
        assert response.json()["answer"] == "an answer"

    def test_the_built_spa_is_served_at_the_root(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>VeriClaim</title>")

        app = create_app(run=StubRun([_final()]), dist=dist)
        response = TestClient(app).get("/")

        assert response.status_code == 200
        assert "VeriClaim" in response.text

    def test_the_mount_never_shadows_the_api(self, tmp_path) -> None:
        """Registered last, so a catch-all at / cannot swallow /api routes."""
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>VeriClaim</title>")

        app = create_app(run=StubRun([_final()]), dist=dist)
        response = TestClient(app).post("/api/ask", json={"question": "is it covered?"})

        assert response.status_code == 200
        assert response.json()["answer"] == "an answer"
```

These build the app directly rather than through the module's `_client` helper, which
calls `create_app(run=stub)` itself and takes no `dist`. `StubRun` yields the events it
is given, so `_final()` is required — a `StubRun()` with no events makes `/api/ask`
return 500 by design. `TestClient`, `StubRun`, `_final` and `create_app` are all already
imported in that module.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_app.py -k SpaMount -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'dist'`.

- [ ] **Step 3: Modify `src/vericlaim/api/app.py`**

Add the import and the default location near the top:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from vericlaim.config import PROJECT_ROOT

#: Where `npm run build` writes. Absent until the frontend has been built, which is
#: the normal state of a fresh checkout.
SPA_DIST = PROJECT_ROOT / "frontend" / "dist"
```

Change the signature and add the mount as the **last** thing before the return:

```python
def create_app(
    run: Callable[..., Iterator[Event]] | None = None,
    dist: Path | None = None,
) -> FastAPI:
    """Build the application. ``run`` is injectable so the transport is testable alone.

    ``dist`` is injectable so the SPA mount can be tested without a Node build.
    """
    execute = run if run is not None else _default_run
    application = FastAPI(title="VeriClaim")

    # ... existing @application.post routes, unchanged ...

    # Mounted last and only when built. StaticFiles raises on a missing directory, so
    # an unconditional mount would break the API in any checkout that has not run
    # `npm run build` -- including every CI machine without Node. Registering after
    # the routes is what stops a catch-all at "/" swallowing /api.
    spa = SPA_DIST if dist is None else dist
    if (spa / "index.html").is_file():
        application.mount("/", StaticFiles(directory=spa, html=True), name="spa")

    return application
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_app.py -v`
Expected: all pass, including the three new ones.

- [ ] **Step 5: Run the whole offline suite**

Run: `uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"`
Expected: all pass. Note the count; it must exceed the pre-task baseline by exactly the number of tests added.

- [ ] **Step 6: Prove it works with a real build present**

```bash
cd frontend && npm run build && cd ..
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
```

Expected: identical result. The suite must pass whether or not `frontend/dist` exists.

- [ ] **Step 7: Lint and commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
uv run ruff check .
git add src/vericlaim/api/app.py tests/api/test_app.py
git commit -F - <<'MSG'
feat(C-10.1): serve the built SPA, and only when it exists

StaticFiles raises when its directory is absent, so mounting it
unconditionally would stop the API importing in any checkout that has
not run npm run build -- including a machine with no Node at all. That
is the same "declared thing that fails" C-8.13 deleted two entry points
for, so the mount is conditional on frontend/dist/index.html being
there.

It is also registered last, after every /api route, because a catch-all
at "/" would otherwise swallow them. A test asserts exactly that, rather
than trusting registration order to stay correct.

dist is injectable so both directions are tested without a Node build,
and the offline suite passes with and without the frontend built.
MSG
```

---

### Task 6: The probe page, and end-to-end proof

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `askStream`, `ApiError` from Task 4; `Event` from Task 2.
- Produces: nothing later tasks depend on. C-10.2 replaces this file.

- [ ] **Step 1: Replace `frontend/src/App.tsx`**

Deliberately not a UI. It sends one question and prints the event stream, which is the smallest thing that proves the pipe end to end. C-10.2 builds the real shell.

```tsx
import { useState } from "react";

import { askStream } from "./lib/api";
import type { Event } from "./types";

const DEFAULT_QUESTION = "Are burst pipes covered under HomeSecure?";

function describe(event: Event): string {
  switch (event.event) {
    case "run_started":
      return event.trace_id;
    case "stage":
      return `${event.name}${event.error ? ` -- ${event.error}` : ""}`;
    case "evidence":
      return `${event.source}: ${event.items.length}`;
    case "final":
      return `${event.citations.resolved.length} citations, verified=${event.citations.verified}`;
    case "error":
      return event.message;
  }
}

export default function App() {
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [events, setEvents] = useState<Event[]>([]);
  const [status, setStatus] = useState("idle");

  const run = async () => {
    setEvents([]);
    setStatus("running");
    try {
      const final = await askStream(question, (event) =>
        setEvents((seen) => [...seen, event])
      );
      setStatus(`done -- ${final.evidence.length} evidence items`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <main className="shell">
      <h1>VeriClaim</h1>
      <div className="row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          aria-label="Question"
        />
        <button onClick={run} disabled={status === "running"}>
          Ask
        </button>
      </div>
      <p className="status">{status}</p>
      <div className="frames">
        {events.map((event, index) => (
          <div className="frame" key={index}>
            <span className="name">{event.event}</span> {describe(event)}
          </div>
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Typecheck and build**

Run: `cd frontend && npm run build`
Expected: exit 0, `frontend/dist/index.html` written.

- [ ] **Step 3: Verify live against the running stack**

Start the API in one shell:

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
docker compose up -d
uv run uvicorn vericlaim.api.app:app --port 8000
```

In another, confirm the SPA is served and the stream still works:

```bash
curl -s localhost:8000/ | head -5
curl -sN -X POST localhost:8000/api/ask/stream \
  -H 'content-type: application/json' \
  -d '{"question":"Are burst pipes covered under HomeSecure?"}' \
  | cut -c1-60
```

Expected: the first returns the built `index.html`. The second returns NDJSON beginning with `run_started` and ending with exactly one `final`.

- [ ] **Step 4: Verify in the browser**

Open `http://localhost:8000/`, click **Ask**, and confirm: frames appear as the run progresses, the status line ends with the evidence count, and **no frame is named `ping`** even if the run goes quiet long enough for keepalives to fire.

- [ ] **Step 5: Record the evidence and commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/App.tsx
git commit -F - <<'MSG'
feat(C-10.1): prove the stream end to end from the browser

A probe page, not a UI: it sends one question and prints each event as
it arrives. C-10.2 builds the real shell. What this establishes is that
the whole path works -- built SPA served by FastAPI, NDJSON read across
chunk boundaries, frames typed, keepalives invisible to application
code, and exactly one final event ending the run.
MSG
```

- [ ] **Step 6: Update `tasks/todo.md`**

Tick C-10.1 and add a review section covering: what shipped, the ping-filtering decision, the conditional mount and why, the parity guard, and anything the live run revealed. Then commit with `docs(C-10.1): ...`.

---

## Verification

After every task:

```bash
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
cd frontend && npm test && npm run typecheck
```

The Python suite must pass with `frontend/dist` both present and absent. Confirm the reference repos are untouched before claiming completion:

```bash
git -C /Users/rowdy/Projects/work/unibot-endgame status --porcelain | wc -l   # expect 2
git -C /Users/rowdy/Projects/work/CIL/CSRS status --porcelain | wc -l         # expect 4
```
