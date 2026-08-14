# C-10.2 + C-10.4 Chat Shell and Evidence Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace C-10.1's probe page with the interface VeriClaim is demonstrated through: the CSRS design system, a streaming chat shell, and per-source evidence cards wired to citations.

**Architecture:** CSRS's fonts, tokens and layout CSS copied out and adapted. React state in `App.tsx`, no state library. The waiting state renders real `stage` events as a live pipeline rather than a typing dot, because this stream is stage-level. Evidence cards dispatch on `source_type` to four renderers, and `[En]` markers in the answer become chips that reveal the card they name.

**Tech Stack:** Vite 5, React 18, TypeScript 5, Vitest 2, react-markdown 10, remark-gfm 4, plain CSS.

**Spec:** `docs/superpowers/specs/2026-08-15-vericlaim-c10-2-ui-design.md`

## Global Constraints

- `CSRS` = `/Users/rowdy/Projects/work/CIL/CSRS` is **READ-ONLY**. Copy out; never edit in place. `git -C /Users/rowdy/Projects/work/CIL/CSRS status --porcelain | wc -l` must stay at **4**.
- Copy `styles.css` lines **1-1363** only. Lines 1364-1723 are the Data Viewer and its mode transition, which VeriClaim has no equivalent of.
- The seven fonts are SIL Open Font License. Attribution is owed in the README at C-12.5.
- No state library, no CSS framework, no UI kit. Only `react-markdown` and `remark-gfm` are added.
- `npm run build` stays `tsc --noEmit && vite build`.
- `uv run pytest -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"` must pass **with and without** `frontend/dist`, on a machine with no Node.
- `ping` must never reach typed code. Never sum `stages[*].cost_usd` for a run total.
- Never commit `frontend/node_modules/` or `frontend/dist/`.
- ASCII only in source. No invented data — every figure rendered comes from the payload.
- Commit messages carry no trailers. Task ids use the `C-` series.

## File Structure

| File | Responsibility |
|---|---|
| `frontend/public/fonts/*.woff2` | Seven faces, copied from CSRS |
| `frontend/src/styles.css` | The design system, adapted |
| `frontend/src/components/icons.tsx` | Only the icons used |
| `frontend/src/components/Sidebar.tsx` | Rail, history list, theme toggle |
| `frontend/src/components/Composer.tsx` | Autogrowing textarea |
| `frontend/src/components/Message.tsx` | One turn |
| `frontend/src/components/Stages.tsx` | Live pipeline and finished summary |
| `frontend/src/components/EvidenceCard.tsx` | Group, toggle, dispatch |
| `frontend/src/components/evidence/*.tsx` | One renderer per source type |
| `frontend/src/components/EmptyState.tsx` | Hero and suggestions |
| `frontend/src/lib/citations.tsx` | Split text into prose and markers; the `[En]` chip |
| `frontend/src/lib/history.ts` | localStorage conversations |
| `frontend/src/App.tsx` | Shell, run lifecycle, wiring |

---

### Task 1: The design system

**Files:**
- Create: `frontend/public/fonts/` (7 files), `frontend/public/favicon.svg`
- Modify: `frontend/src/styles.css`, `frontend/index.html`

**Interfaces:**
- Produces: every CSS custom property and class later tasks use. No TypeScript.

- [ ] **Step 1: Copy the fonts**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
mkdir -p frontend/public/fonts
cp /Users/rowdy/Projects/work/CIL/CSRS/frontend/public/fonts/*.woff2 frontend/public/fonts/
ls -1 frontend/public/fonts/ | wc -l    # expect 7
```

- [ ] **Step 2: Copy the design system CSS**

Lines 1-1363 only. The tail is CSRS's Data Viewer.

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
sed -n '1,1363p' /Users/rowdy/Projects/work/CIL/CSRS/frontend/src/styles.css > frontend/src/styles.css
wc -l frontend/src/styles.css          # expect 1363
grep -c "dv-" frontend/src/styles.css  # expect 0
```

- [ ] **Step 3: Confirm the reference repo is untouched**

```bash
git -C /Users/rowdy/Projects/work/CIL/CSRS status --porcelain | wc -l   # expect 4
```

- [ ] **Step 4: Create `frontend/public/favicon.svg`**

VeriClaim's own mark, not CSRS's. A shield outline in the cyan accent.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#0D1117"/>
  <path d="M16 6 L25 10 V16 C25 21 21 25 16 27 C11 25 7 21 7 16 V10 Z"
        fill="none" stroke="#38BDF8" stroke-width="2" stroke-linejoin="round"/>
  <path d="M11.5 16.5 L14.5 19.5 L20.5 12.5" fill="none" stroke="#38BDF8"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```

- [ ] **Step 5: Point `index.html` at the favicon**

Replace the contents of `frontend/index.html` with:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <title>VeriClaim</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Build and confirm the fonts ship**

```bash
cd frontend && npm run build
ls dist/assets/*.woff2 2>/dev/null | wc -l   # 0 is fine: public/ is copied verbatim
ls dist/fonts/*.woff2 | wc -l                 # expect 7
```

- [ ] **Step 7: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/public frontend/src/styles.css frontend/index.html
git commit -F - <<'MSG'
feat(C-10.2): adopt the CSRS design system

The seven OFL faces -- DM Sans, Geist Mono, Instrument Serif -- and the
first 1363 lines of the stylesheet: both theme token blocks, the shell,
sidebar, thread, message and composer. The tail is CSRS's Data Viewer
and its mode transition, which this project has no equivalent of, so it
is not copied and will not need deleting later.

Self-hosted rather than fetched, so the interface renders identically
offline. Attribution for the fonts is owed in the README at C-12.5.
MSG
```

---

### Task 2: Icons and the app shell

**Files:**
- Create: `frontend/src/components/icons.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/components/EmptyState.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `Ico` (named SVG components), `Sidebar`, `EmptyState`. Tasks 3-8 import `Ico`.

- [ ] **Step 1: Create `frontend/src/components/icons.tsx`**

```tsx
type P = { className?: string };

const S = (d: string) => (p: P) => (
  <svg
    className={p.className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d={d} />
  </svg>
);

export const Ico = {
  Plus: S("M12 5v14M5 12h14"),
  Send: S("M4 12l16-8-6 8 6 8z"),
  Sun: S("M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M15 12a3 3 0 11-6 0 3 3 0 016 0z"),
  Moon: S("M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"),
  Panel: S("M4 5h16v14H4zM10 5v14"),
  Trash: S("M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"),
  Check: S("M5 13l4 4 10-10"),
  Doc: S("M7 3h7l5 5v13H7zM14 3v5h5"),
  Db: S("M5 7c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 7v10c0 1.7 3.1 3 7 3s7-1.3 7-3V7"),
  Grid: S("M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"),
  Scan: S("M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M7 12h10"),
  Spark: S("M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z")
};

/** The icon that stands for a source type, used wherever evidence is labelled. */
export const SOURCE_ICON = {
  policy: Ico.Doc,
  sql: Ico.Db,
  spreadsheet: Ico.Grid,
  scanned_pdf: Ico.Scan
} as const;
```

- [ ] **Step 2: Create `frontend/src/components/EmptyState.tsx`**

The suggestions exercise different routing paths, so a reviewer can see the router work without inventing questions.

```tsx
const SUGGESTIONS = [
  "Are burst pipes covered under HomeSecure?",
  "How many water-damage claims were filed in March 2026?",
  "Which regions missed their Q1 inspection-compliance targets?",
  "Why did water-damage claims increase in March 2026, which regions were responsible, did those regions miss their Q1 inspection-compliance targets, and are burst pipes covered under our current HomeSecure policy?"
];

export function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="hero">
      <div className="hero-desc">Property Insurance Claims Intelligence</div>
      <div className="hero-word">
        <span>Ask across policies, claims, spreadsheets and scans.</span>
      </div>
      <div className="hero-chips">
        {SUGGESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            className="suggest-chip"
            onClick={() => onPick(question)}
          >
            {question.length > 74 ? `${question.slice(0, 74)}...` : question}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/Sidebar.tsx`**

```tsx
import { Ico } from "./icons";
import type { Conversation } from "../lib/history";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
  theme: "dark" | "light";
  onThemeToggle: () => void;
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
};

export function Sidebar({
  collapsed,
  onToggle,
  theme,
  onThemeToggle,
  conversations,
  activeId,
  onSelect,
  onDelete,
  onNew
}: SidebarProps) {
  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="side-top">
        <div className="brand">
          <span className="brand-mark">
            <Ico.Spark />
          </span>
          {!collapsed && <span className="brand-word">VeriClaim</span>}
        </div>
        <button
          type="button"
          className="rail-toggle"
          onClick={onToggle}
          title={collapsed ? "Expand" : "Collapse"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <Ico.Panel />
        </button>
      </div>

      <button type="button" className="new-chat" onClick={onNew}>
        <Ico.Plus />
        {!collapsed && <span>New question</span>}
      </button>

      <div className="side-scroll">
        {!collapsed && conversations.length > 0 && (
          <div className="conversation-history">
            <div className="side-label">History</div>
            <div className="conversation-list">
              {conversations.map((conversation) => (
                <div
                  key={conversation.id}
                  className={
                    "conversation-item" +
                    (conversation.id === activeId ? " active" : "")
                  }
                >
                  <button
                    type="button"
                    className="conversation-select"
                    onClick={() => onSelect(conversation.id)}
                    title={conversation.title}
                  >
                    {conversation.title}
                  </button>
                  <button
                    type="button"
                    className="conversation-delete"
                    onClick={() => onDelete(conversation.id)}
                    aria-label={`Delete ${conversation.title}`}
                  >
                    <Ico.Trash />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="side-foot">
        <button
          type="button"
          className="theme-toggle"
          onClick={onThemeToggle}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <Ico.Sun /> : <Ico.Moon />}
          {!collapsed && <span>{theme === "dark" ? "Light" : "Dark"}</span>}
        </button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Typecheck**

This fails until Task 7 creates `lib/history.ts`. That is expected and is why Task 7 exists; to typecheck now, create the module stub:

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim/frontend
cat > src/lib/history.ts <<'TS'
export type Conversation = {
  id: string;
  title: string;
  createdAt: number;
};
TS
npm run typecheck
```

Expected: exit 0. Task 7 fills this module in.

- [ ] **Step 5: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/components frontend/src/lib/history.ts
git commit -F - <<'MSG'
feat(C-10.2): add the icon set, sidebar and empty state

The sidebar keeps what CSRS's does and this project needs -- rail
collapse, history, theme toggle -- and drops its index management and
model picker, which have no counterpart here.

The suggested questions are chosen to exercise different routing paths:
policy only, claims database only, spreadsheet only, and the four-clause
flagship that reaches all four sources.
MSG
```

---

### Task 3: The composer

`Message` is deliberately **not** here: it consumes `Stages`, `EvidenceCards` and
`withCitations`, so it lands in Task 7 once those exist. Building it earlier would leave a
task that cannot typecheck.

**Files:**
- Create: `frontend/src/components/Composer.tsx`
- Modify: `frontend/package.json` (add react-markdown, remark-gfm)

**Interfaces:**
- Consumes: `Ico` from Task 2.
- Produces: `Composer`. The markdown dependencies Task 7 needs are installed here.

- [ ] **Step 1: Add the markdown dependencies**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim/frontend
npm install react-markdown@^10.1.0 remark-gfm@^4.0.1
```

- [ ] **Step 2: Create `frontend/src/components/Composer.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";

import { Ico } from "./icons";

type ComposerProps = {
  onSend: (text: string) => void;
  busy: boolean;
};

export function Composer({ onSend, busy }: ComposerProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 140)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    onSend(trimmed);
    setValue("");
  };

  const ready = value.trim().length > 0 && !busy;

  return (
    <div className="composer-dock">
      <div className="composer-inner">
        <div className={"composer" + (focused ? " focused" : "")}>
          <textarea
            ref={textareaRef}
            value={value}
            placeholder="Ask about policies, claims, spreadsheets or scanned documents..."
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            rows={1}
          />
          <button
            className={"send-btn" + (ready ? " ready" : "")}
            onClick={submit}
            disabled={!ready}
            title="Send"
            type="button"
          >
            <Ico.Send />
          </button>
        </div>
        <div className="composer-hint">
          <kbd>Enter</kbd> to send <span className="sep" /> <kbd>Shift</kbd>+
          <kbd>Enter</kbd> new line
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/components/Composer.tsx frontend/package.json frontend/package-lock.json
git commit -F - <<'MSG'
feat(C-10.2): add the composer

CSRS's composer, minus the disabled-reason path this project has no
source for: an autogrowing textarea capped at 140px, Enter to send and
Shift+Enter for a newline. react-markdown and remark-gfm are installed
here because the message turn in Task 7 renders answers with them.
MSG
```

---

### Task 4: The live pipeline

**Files:**
- Create: `frontend/src/components/Stages.tsx`

**Interfaces:**
- Consumes: `StageEvent` from `types.ts`, `Ico` from Task 2.
- Produces: `Stages`.

- [ ] **Step 1: Create `frontend/src/components/Stages.tsx`**

```tsx
import { useEffect, useState } from "react";

import { Ico } from "./icons";
import type { StageEvent } from "../types";

/** What a stage is called in the interface. Anything unlisted shows its own name. */
const LABEL: Record<string, string> = {
  understand: "Reading the question",
  route: "Choosing sources",
  plan: "Planning the work",
  collect: "Collecting evidence",
  sufficiency: "Checking for gaps",
  synthesize: "Writing the answer",
  verify: "Verifying citations"
};

function label(name: string): string {
  if (name.startsWith("source.")) return `Querying ${name.slice("source.".length)}`;
  return LABEL[name] ?? name;
}

export function Stages({
  stages,
  running
}: {
  stages: StageEvent[];
  running: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 120);
    return () => window.clearInterval(timer);
  }, [running]);

  if (stages.length === 0 && !running) return null;

  const total = stages.reduce((sum, stage) => sum + stage.latency_ms, 0);

  // While running the list is the interesting thing; once finished it collapses to a
  // single line, because a finished pipeline is history and the answer is the point.
  if (!running && !expanded) {
    return (
      <button
        type="button"
        className="stage-summary"
        onClick={() => setExpanded(true)}
      >
        <Ico.Check />
        <span>
          {stages.length} steps in {(total / 1000).toFixed(1)}s
        </span>
      </button>
    );
  }

  return (
    <div className="stage-list" data-tick={tick}>
      {stages.map((stage, index) => (
        <div
          className={"stage-row" + (stage.error ? " failed" : "")}
          key={`${stage.name}-${index}`}
        >
          <span className="stage-dot" aria-hidden="true" />
          <span className="stage-name">{label(stage.name)}</span>
          {stage.error ? (
            <span className="stage-error" title={stage.error}>
              {stage.error}
            </span>
          ) : (
            <span className="stage-time">{(stage.latency_ms / 1000).toFixed(1)}s</span>
          )}
        </div>
      ))}
      {running && (
        <div className="stage-row active">
          <span className="stage-dot pulse" aria-hidden="true" />
          <span className="stage-name">Working...</span>
        </div>
      )}
      {!running && expanded && (
        <button
          type="button"
          className="stage-collapse"
          onClick={() => setExpanded(false)}
        >
          Hide steps
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Append the stage styles to `frontend/src/styles.css`**

```css
/* ============================================================
   Stage pipeline: this stream is stage-level, not token-level,
   so waiting shows the real work rather than a typing dot.
   ============================================================ */
.stage-list {
  border: 1px solid var(--border-muted);
  background: var(--surface);
  border-radius: var(--r-md);
  padding: 10px 12px;
  margin-bottom: 10px;
  font-size: 12.5px;
}
.stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 0;
  color: var(--text-2);
}
.stage-row.failed .stage-name { color: #FCA5A5; }
.stage-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--steel);
  flex: none;
}
.stage-row.failed .stage-dot { background: #F87171; }
.stage-dot.pulse {
  background: var(--cyan);
  animation: stagePulse 1.1s var(--ease) infinite;
}
@keyframes stagePulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.7); }
}
.stage-name { flex: 1; }
.stage-time { color: var(--text-muted); font-family: "Geist Mono", ui-monospace, monospace; font-size: 11.5px; }
.stage-error { color: #FCA5A5; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stage-summary, .stage-collapse {
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--surface); color: var(--text-2);
  border: 1px solid var(--border-muted); border-radius: var(--r-sm);
  padding: 6px 11px; font: inherit; font-size: 12.5px; cursor: pointer;
  margin-bottom: 10px;
}
.stage-summary:hover, .stage-collapse:hover { color: var(--text); border-color: var(--border); }
.stage-summary svg { width: 14px; height: 14px; color: var(--success); }
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/components/Stages.tsx frontend/src/styles.css
git commit -F - <<'MSG'
feat(C-10.2): render the pipeline as it runs

CSRS waits behind a typing dot because it streams tokens. This stream is
stage-level, so waiting shows the actual work: which sources are being
queried, how long each step took, and which of them failed. A stage
carrying an error renders as failed rather than being hidden, because a
source that did not answer is something the reader must see.

Once the run finishes the list collapses to one line. A finished
pipeline is history; the answer is the point.
MSG
```

---

### Task 5: Evidence cards

**Files:**
- Create: `frontend/src/components/EvidenceCard.tsx`, `frontend/src/components/evidence/PolicyEvidence.tsx`, `SqlEvidence.tsx`, `SpreadsheetEvidence.tsx`, `ScannedEvidence.tsx`
- Test: `frontend/src/components/__tests__/evidence.test.ts`

**Interfaces:**
- Consumes: `EvidenceItem`, `CitationReport` from `types.ts`.
- Produces: `EvidenceCards`, and `bodyFor(item)` which Task 5's test asserts on.

- [ ] **Step 1: Write the failing dispatcher test at `frontend/src/components/__tests__/evidence.test.ts`**

```ts
import { describe, expect, it } from "vitest";

import { rendererName } from "../EvidenceCard";

describe("the evidence dispatcher", () => {
  it("picks a renderer for every source type", () => {
    expect(rendererName("policy")).toBe("PolicyEvidence");
    expect(rendererName("sql")).toBe("SqlEvidence");
    expect(rendererName("spreadsheet")).toBe("SpreadsheetEvidence");
    expect(rendererName("scanned_pdf")).toBe("ScannedEvidence");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL, cannot resolve `../EvidenceCard`.

- [ ] **Step 3: Create the four renderers**

`frontend/src/components/evidence/PolicyEvidence.tsx`:

```tsx
import type { EvidenceItem, PolicyLocator } from "../../types";

export function PolicyEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as PolicyLocator;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.document}</span>
        {locator.page !== null && <span>Page {locator.page}</span>}
        {locator.section && <span className="ev-strong">{locator.section}</span>}
      </div>
      <p className="ev-text">{item.content}</p>
    </>
  );
}
```

`frontend/src/components/evidence/SqlEvidence.tsx`:

```tsx
import type { EvidenceItem, SqlLocator } from "../../types";

export function SqlEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SqlLocator;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.tables.join(", ")}</span>
        <span>
          {locator.row_count} {locator.row_count === 1 ? "row" : "rows"}
        </span>
      </div>
      <p className="ev-text">{item.content}</p>
      {/* The executed SQL is exposed on purpose (C-9.3): the reader can check the
          number against the query that produced it. Neither reference repo does this. */}
      <pre className="ev-sql">
        <code>{locator.executed_sql}</code>
      </pre>
    </>
  );
}
```

`frontend/src/components/evidence/SpreadsheetEvidence.tsx`:

```tsx
import type { EvidenceItem, SpreadsheetLocator } from "../../types";

export function SpreadsheetEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SpreadsheetLocator;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.workbook}</span>
        <span>{locator.sheet}</span>
        {locator.row !== null && <span>Row {locator.row}</span>}
        <span className="ev-strong">{locator.a1_range}</span>
      </div>
      <p className="ev-text">{item.content}</p>
    </>
  );
}
```

`frontend/src/components/evidence/ScannedEvidence.tsx`:

```tsx
import type { EvidenceItem, ScannedLocator } from "../../types";

export function ScannedEvidence({ item }: { item: EvidenceItem }) {
  const locator = item.locator as ScannedLocator;
  const confidence = locator.ocr_confidence;
  return (
    <>
      <div className="ev-meta">
        <span>{locator.document}</span>
        {locator.page !== null && <span>Page {locator.page}</span>}
        {locator.escalated && (
          <span className="ev-strong">re-read by the vision tier</span>
        )}
      </div>
      {/* The measured confidence, not a verdict on it: the floor that decides "low"
          is a Python setting and is not on the wire, and a second copy of it here
          would drift the first time it is tuned. The answer text carries the
          qualification. */}
      {confidence !== null && (
        <div className="ev-score">
          <span>OCR confidence</span>
          <div className="hbar-track">
            <div
              className="hbar-fill"
              style={{ width: `${Math.round(confidence * 100)}%` }}
            />
          </div>
          <span className="ev-score-value">{confidence.toFixed(2)}</span>
        </div>
      )}
      <p className="ev-text">{item.content}</p>
    </>
  );
}
```

- [ ] **Step 4: Create `frontend/src/components/EvidenceCard.tsx`**

```tsx
import { useState } from "react";

import { SOURCE_ICON } from "./icons";
import { PolicyEvidence } from "./evidence/PolicyEvidence";
import { ScannedEvidence } from "./evidence/ScannedEvidence";
import { SpreadsheetEvidence } from "./evidence/SpreadsheetEvidence";
import { SqlEvidence } from "./evidence/SqlEvidence";
import type { CitationReport, EvidenceItem, SourceType } from "../types";

const RENDERER = {
  policy: PolicyEvidence,
  sql: SqlEvidence,
  spreadsheet: SpreadsheetEvidence,
  scanned_pdf: ScannedEvidence
} as const;

/** Exposed for test: every source type must have a renderer. */
export function rendererName(source: SourceType): string {
  return RENDERER[source].name;
}

const GROUP_LABEL: Record<SourceType, string> = {
  policy: "Policy documents",
  sql: "Claims database",
  spreadsheet: "Operational spreadsheets",
  scanned_pdf: "Scanned documents"
};

const ORDER: SourceType[] = ["policy", "sql", "spreadsheet", "scanned_pdf"];

export function EvidenceCards({
  evidence,
  citations
}: {
  evidence: EvidenceItem[];
  citations: CitationReport;
}) {
  const [open, setOpen] = useState<SourceType | null>(null);
  if (evidence.length === 0) return null;

  const cited = new Set(citations.resolved);
  const groups = ORDER.map((source) => ({
    source,
    items: evidence.filter((item) => item.source_type === source)
  })).filter((group) => group.items.length > 0);

  return (
    <section className="evidence" aria-label="Evidence">
      <div className="evidence-tabs">
        {groups.map(({ source, items }) => {
          const Icon = SOURCE_ICON[source];
          const used = items.filter((item) => cited.has(item.id)).length;
          return (
            <button
              key={source}
              type="button"
              className={"evidence-tab" + (open === source ? " active" : "")}
              onClick={() => setOpen(open === source ? null : source)}
              aria-expanded={open === source}
            >
              <Icon />
              <span>{GROUP_LABEL[source]}</span>
              <span className="evidence-count">
                {used}/{items.length}
              </span>
            </button>
          );
        })}
      </div>

      {groups
        .filter((group) => group.source === open)
        .map(({ source, items }) => {
          const Renderer = RENDERER[source];
          return (
            <div className="evidence-panel" key={source}>
              {items.map((item) => (
                <article
                  className={"ev-card" + (cited.has(item.id) ? " cited" : "")}
                  id={`evidence-${item.id}`}
                  key={item.id}
                >
                  <div className="ev-head">
                    <span className="ev-id">[{item.id}]</span>
                    <span className="ev-citation">{item.citation}</span>
                    {!cited.has(item.id) && (
                      <span className="ev-uncited">not cited</span>
                    )}
                  </div>
                  <Renderer item={item} />
                </article>
              ))}
            </div>
          );
        })}
    </section>
  );
}
```

- [ ] **Step 5: Append the evidence styles to `frontend/src/styles.css`**

```css
/* ============================================================
   Evidence: grouped by source, one renderer per source type
   ============================================================ */
.evidence { margin-top: 10px; }
.evidence-tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.evidence-tab {
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--surface); color: var(--text-2);
  border: 1px solid var(--border-muted); border-radius: 999px;
  padding: 6px 12px; font: inherit; font-size: 12.5px; cursor: pointer;
  transition: border-color .18s var(--ease), color .18s var(--ease);
}
.evidence-tab:hover { color: var(--text); border-color: var(--border); }
.evidence-tab.active { color: var(--text); border-color: var(--cyan-ring); background: var(--cyan-soft); }
.evidence-tab svg { width: 14px; height: 14px; }
.evidence-count { color: var(--text-muted); font-family: "Geist Mono", ui-monospace, monospace; font-size: 11px; }
.evidence-panel { display: grid; gap: 8px; margin-top: 10px; }
.ev-card {
  border: 1px solid var(--border-muted); background: var(--surface);
  border-radius: var(--r-md); padding: 12px 14px;
  transition: border-color .3s var(--ease), box-shadow .3s var(--ease);
}
.ev-card.cited { border-color: var(--border); }
.ev-card.flash { border-color: var(--cyan); box-shadow: 0 0 0 3px var(--cyan-soft); }
.ev-head { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; margin-bottom: 7px; }
.ev-id { font-family: "Geist Mono", ui-monospace, monospace; font-size: 12px; color: var(--cyan); }
.ev-citation { color: var(--text-2); font-size: 12.5px; }
.ev-uncited { color: var(--text-muted); font-size: 11px; border: 1px solid var(--border-muted); border-radius: 999px; padding: 1px 7px; }
.ev-meta { display: flex; flex-wrap: wrap; gap: 10px; color: var(--text-muted); font-size: 11.5px; margin-bottom: 6px; }
.ev-strong { color: var(--text-2); }
.ev-text { color: var(--text); font-size: 13px; line-height: 1.65; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.ev-sql {
  margin: 8px 0 0; padding: 10px 12px; overflow-x: auto;
  background: var(--bg); border: 1px solid var(--border-muted); border-radius: var(--r-sm);
  font-family: "Geist Mono", ui-monospace, monospace; font-size: 11.5px; line-height: 1.6;
  color: var(--text-2);
}
.ev-score { display: flex; align-items: center; gap: 9px; margin-bottom: 7px; font-size: 11.5px; color: var(--text-muted); }
.ev-score .hbar-track { flex: 1; max-width: 160px; }
.ev-score-value { font-family: "Geist Mono", ui-monospace, monospace; color: var(--text-2); }
```

- [ ] **Step 6: Run the test**

Run: `cd frontend && npm test`
Expected: 14 passed (13 from C-10.1, 1 here).

- [ ] **Step 7: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/components frontend/src/styles.css
git commit -F - <<'MSG'
feat(C-10.4): render evidence with one card per source type

A locator means something different in each source, so one renderer
cannot serve all four honestly: policy shows document, page and clause;
the claims database shows the executed SQL beside the number it
produced; a spreadsheet shows workbook, sheet, row and A1 range; a scan
shows its OCR confidence and whether the vision tier re-read it.

Each group's tab reports how many of its items the answer actually
cited, so evidence gathered and then ignored is visible rather than
implied. The scanned card shows the measured confidence and does not
label it low: the floor is a Python setting, not on the wire, and a
second copy of it here would drift the first time it is tuned.
MSG
```

---

### Task 6: Citations

**Files:**
- Create: `frontend/src/lib/citations.tsx`, `frontend/src/lib/__tests__/citations.test.ts`

**Interfaces:**
- Consumes: `EvidenceItem` from `types.ts`.
- Produces: `withCitations(children, evidence)`, used by Task 3's `Message`.

- [ ] **Step 1: Write the failing test at `frontend/src/lib/__tests__/citations.test.ts`**

```ts
import { describe, expect, it } from "vitest";

import { splitCitations } from "../citations";

describe("splitCitations", () => {
  it("splits prose from markers", () => {
    expect(splitCitations("Covered [E1] and paid [E2].")).toEqual([
      { text: "Covered " },
      { id: "E1" },
      { text: " and paid " },
      { id: "E2" },
      { text: "." }
    ]);
  });

  it("returns a single chunk when there are no markers", () => {
    expect(splitCitations("No citations here.")).toEqual([
      { text: "No citations here." }
    ]);
  });

  it("normalises a leading zero so [E01] and [E1] are one id", () => {
    expect(splitCitations("[E01]")).toEqual([{ id: "E1" }]);
  });

  it("leaves a malformed marker as prose", () => {
    expect(splitCitations("As shown [E] and [EX].")).toEqual([
      { text: "As shown [E] and [EX]." }
    ]);
  });

  it("handles two markers with nothing between them", () => {
    expect(splitCitations("[E1][E2]")).toEqual([{ id: "E1" }, { id: "E2" }]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL, cannot resolve `../citations`.

- [ ] **Step 3: Create `frontend/src/lib/citations.tsx`**

Note the `.tsx` extension: it returns React nodes.

```tsx
import { Children, isValidElement } from "react";
import type { ReactNode } from "react";

import type { EvidenceItem } from "../types";

export type Chunk = { text: string } | { id: string };

// Mirrors CITATION_PATTERN in src/vericlaim/citations.py. Anything not matching this
// stays prose, exactly as the resolver treats it.
const MARKER = /\[E(\d+)\]/g;

export function splitCitations(text: string): Chunk[] {
  const chunks: Chunk[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const start = match.index ?? 0;
    if (start > last) chunks.push({ text: text.slice(last, start) });
    chunks.push({ id: `E${Number(match[1])}` });
    last = start + match[0].length;
  }
  if (last < text.length) chunks.push({ text: text.slice(last) });
  return chunks.length > 0 ? chunks : [{ text }];
}

function reveal(id: string) {
  const card = document.getElementById(`evidence-${id}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  window.setTimeout(() => card.classList.remove("flash"), 1400);
}

/** Replace every [En] in a markdown text node with a chip that reveals its card. */
export function withCitations(children: ReactNode, evidence: EvidenceItem[]): ReactNode {
  const known = new Set(evidence.map((item) => item.id));

  return Children.map(children, (child) => {
    // Only text is rewritten. A marker inside inline code or any other element is
    // left exactly as written.
    if (typeof child !== "string") return isValidElement(child) ? child : child;

    return splitCitations(child).map((chunk, index) => {
      if ("text" in chunk) return chunk.text;
      if (!known.has(chunk.id)) {
        // A marker naming evidence that does not exist must look wrong, not vanish.
        return (
          <span className="cite missing" key={`${chunk.id}-${index}`}>
            [{chunk.id}]
          </span>
        );
      }
      return (
        <button
          type="button"
          className="cite"
          key={`${chunk.id}-${index}`}
          onClick={() => reveal(chunk.id)}
          title={`Show ${chunk.id}`}
        >
          {chunk.id}
        </button>
      );
    });
  });
}
```

- [ ] **Step 4: Append the citation styles to `frontend/src/styles.css`**

```css
/* ============================================================
   Citation chips: the answer's link to its evidence
   ============================================================ */
.cite {
  display: inline-block; vertical-align: baseline;
  font-family: "Geist Mono", ui-monospace, monospace; font-size: 11px;
  line-height: 1.5; padding: 0 6px; margin: 0 2px;
  color: var(--cyan); background: var(--cyan-soft);
  border: 1px solid var(--cyan-ring); border-radius: 999px;
  cursor: pointer; transition: background .15s var(--ease);
}
.cite:hover { background: var(--cyan-ring); }
.cite.missing {
  color: #FCA5A5; background: rgba(248,113,113,.10);
  border-color: rgba(248,113,113,.30); cursor: default;
}
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npm test`
Expected: 19 passed (13 + 1 + 5).

- [ ] **Step 6: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/lib frontend/src/styles.css
git commit -F - <<'MSG'
feat(C-10.2): make every citation a link to the evidence it names

An [En] marker becomes a chip that scrolls its evidence card into view
and flashes it. That is the product's thesis made operable: a reader can
check any claim against its source in one click rather than trusting the
prose.

The pattern mirrors CITATION_PATTERN in citations.py, and anything not
matching stays prose exactly as the resolver treats it. A marker naming
evidence that does not exist renders in an error style rather than
disappearing -- the answer is shown as written, and a citation pointing
at nothing has to look wrong.

Only text nodes are rewritten, so a marker inside inline code or a fence
is left alone.
MSG
```

---

### Task 7: The message turn and conversation history

**Files:**
- Create: `frontend/src/components/Message.tsx`
- Modify: `frontend/src/lib/history.ts`, `frontend/src/styles.css`
- Test: `frontend/src/lib/__tests__/history.test.ts`

**Interfaces:**
- Consumes: `Stages` (Task 4), `EvidenceCards` (Task 5), `withCitations` (Task 6), `Ico` (Task 2).
- Produces: `Turn`, `turnFromEvent`, `Message`, `Conversation`, `load`, `save`, `titleFromQuestion`. Task 8 wires all of them into `App`.

- [ ] **Step 0: Create `frontend/src/components/Message.tsx`**

`Turn` is the unit of conversation and is defined here because `Message` is what renders it.

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Ico } from "./icons";
import { EvidenceCards } from "./EvidenceCard";
import { Stages } from "./Stages";
import { withCitations } from "../lib/citations";
import type { Event, FinalEvent, StageEvent } from "../types";

export type Turn = {
  id: string;
  question: string;
  stages: StageEvent[];
  final: FinalEvent | null;
  error: string | null;
  running: boolean;
};

export function turnFromEvent(turn: Turn, event: Event): Turn {
  if (event.event === "stage") return { ...turn, stages: [...turn.stages, event] };
  if (event.event === "final") return { ...turn, final: event, running: false };
  if (event.event === "error") return { ...turn, error: event.message, running: false };
  return turn;
}

function Verdict({ final }: { final: FinalEvent }) {
  const { verified, degraded } = final.citations;
  if (degraded) {
    return (
      <div className="verdict degraded">
        Not verified against the evidence. Shown for inspection only.
      </div>
    );
  }
  if (!verified) {
    return <div className="verdict unchecked">Answer could not be checked.</div>;
  }
  return null;
}

export function Message({ turn }: { turn: Turn }) {
  const final = turn.final;
  const evidence = final?.evidence ?? [];

  return (
    <>
      <div className="msg user">
        <div className="msg-avatar">Q</div>
        <div className="msg-col">
          <div className="bubble">{turn.question}</div>
        </div>
      </div>

      <div className="msg assistant">
        <div className="msg-avatar">
          <Ico.Spark className="spark" />
        </div>
        <div className="msg-col">
          <Stages stages={turn.stages} running={turn.running} />

          {final && (
            <>
              <Verdict final={final} />
              <div className="bubble">
                <div className="markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      p: ({ children }) => <p>{withCitations(children, evidence)}</p>,
                      li: ({ children }) => <li>{withCitations(children, evidence)}</li>,
                      table: ({ children }) => (
                        <div className="markdown-table-wrap">
                          <table>{children}</table>
                        </div>
                      )
                    }}
                  >
                    {final.answer}
                  </ReactMarkdown>
                </div>
              </div>

              {final.failures.length > 0 && (
                <div className="api-error">
                  {final.failures.map((failure) => (
                    <div key={failure}>{failure}</div>
                  ))}
                </div>
              )}

              <EvidenceCards evidence={evidence} citations={final.citations} />
            </>
          )}

          {turn.error && <div className="api-error">{turn.error}</div>}
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 0b: Add the verdict styles to `frontend/src/styles.css`**

Append:

```css
/* ============================================================
   Verdict: an unverified answer must never look verified
   ============================================================ */
.verdict {
  font-size: 12.5px;
  padding: 8px 12px;
  border-radius: var(--r-sm);
  margin-bottom: 8px;
  border: 1px solid var(--border);
}
.verdict.degraded {
  color: #FCA5A5;
  background: rgba(248, 113, 113, 0.10);
  border-color: rgba(248, 113, 113, 0.28);
}
.verdict.unchecked {
  color: var(--text-2);
  background: var(--soft);
}
```


- [ ] **Step 1: Write the failing test at `frontend/src/lib/__tests__/history.test.ts`**

```ts
import { describe, expect, it } from "vitest";

import { load, save, titleFromQuestion, MAX_CONVERSATIONS } from "../history";
import type { Conversation } from "../history";

function memoryStorage(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value)
  };
}

function conversation(id: string): Conversation {
  return { id, title: `Question ${id}`, createdAt: 1, turns: [] };
}

describe("history", () => {
  it("round-trips conversations", () => {
    const storage = memoryStorage();
    save([conversation("a")], storage);
    expect(load(storage).map((c) => c.id)).toEqual(["a"]);
  });

  it("keeps at most MAX_CONVERSATIONS, newest first", () => {
    const storage = memoryStorage();
    const many = Array.from({ length: MAX_CONVERSATIONS + 5 }, (_, i) =>
      conversation(String(i))
    );
    save(many, storage);
    expect(load(storage)).toHaveLength(MAX_CONVERSATIONS);
    expect(load(storage)[0].id).toBe("0");
  });

  it("returns nothing rather than throwing on malformed stored JSON", () => {
    expect(load(memoryStorage({ "vericlaim.history.v1": "{not json" }))).toEqual([]);
  });

  it("returns nothing when the key is absent", () => {
    expect(load(memoryStorage())).toEqual([]);
  });

  it("titles a conversation from its question", () => {
    expect(titleFromQuestion("  Are burst pipes covered?  ")).toBe(
      "Are burst pipes covered?"
    );
  });

  it("truncates a long title", () => {
    const title = titleFromQuestion("x".repeat(120));
    expect(title.length).toBeLessThanOrEqual(60);
    expect(title.endsWith("...")).toBe(true);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — `load` is not exported.

- [ ] **Step 3: Replace `frontend/src/lib/history.ts`**

```ts
import type { Turn } from "../components/Message";

export const HISTORY_STORAGE_KEY = "vericlaim.history.v1";
export const MAX_CONVERSATIONS = 20;
const MAX_TITLE = 60;

export type Conversation = {
  id: string;
  title: string;
  createdAt: number;
  turns: Turn[];
};

export type HistoryStorage = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): HistoryStorage | null {
  try {
    return window.localStorage;
  } catch {
    // Storage can be unavailable in a private window; history is a convenience and
    // its absence must not stop a question being asked.
    return null;
  }
}

export function titleFromQuestion(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  if (trimmed.length <= MAX_TITLE) return trimmed;
  return `${trimmed.slice(0, MAX_TITLE - 3)}...`;
}

export function load(storage: HistoryStorage | null = defaultStorage()): Conversation[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is Conversation =>
        typeof item === "object" && item !== null && "id" in item && "turns" in item
    );
  } catch {
    // Stored history is not worth failing a page load over.
    return [];
  }
}

export function save(
  conversations: Conversation[],
  storage: HistoryStorage | null = defaultStorage()
): void {
  if (!storage) return;
  try {
    storage.setItem(
      HISTORY_STORAGE_KEY,
      JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS))
    );
  } catch {
    // A full quota must not break the interface.
  }
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test`
Expected: 25 passed (19 + 6).

- [ ] **Step 5: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src/lib
git commit -F - <<'MSG'
feat(C-10.2): keep conversations across reloads

Adapted from CSRS's history module, capped at twenty. Every failure path
degrades rather than throwing: storage unavailable in a private window,
malformed stored JSON, a full quota. History is a convenience, and none
of those is a reason a question cannot be asked.
MSG
```

---

### Task 8: Wire it together and verify in a browser

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: everything from Tasks 2-7.

- [ ] **Step 1: Replace `frontend/src/App.tsx`**

```tsx
import { useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { Message, turnFromEvent } from "./components/Message";
import type { Turn } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { askStream } from "./lib/api";
import { load, save, titleFromQuestion } from "./lib/history";
import type { Conversation } from "./lib/history";

type Theme = "dark" | "light";

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>(() => load());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<Theme>("dark");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [turns]);

  const persist = (id: string, question: string, next: Turn[]) => {
    setConversations((current) => {
      const rest = current.filter((conversation) => conversation.id !== id);
      const updated: Conversation = {
        id,
        title: titleFromQuestion(question),
        createdAt: Date.now(),
        turns: next
      };
      const merged = [updated, ...rest];
      save(merged);
      return merged;
    });
  };

  const send = async (question: string) => {
    const conversationId = activeId ?? newId();
    if (!activeId) setActiveId(conversationId);

    const turn: Turn = {
      id: newId(),
      question,
      stages: [],
      final: null,
      error: null,
      running: true
    };
    let current = turn;
    setTurns((seen) => [...seen, turn]);
    setBusy(true);

    const update = (next: Turn) => {
      current = next;
      setTurns((seen) => seen.map((t) => (t.id === next.id ? next : t)));
    };

    try {
      await askStream(question, (event) => update(turnFromEvent(current, event)));
    } catch (error) {
      update({
        ...current,
        running: false,
        error: error instanceof Error ? error.message : String(error)
      });
    } finally {
      setBusy(false);
      setTurns((seen) => {
        const settled = seen.map((t) =>
          t.id === current.id ? { ...current, running: false } : t
        );
        persist(conversationId, question, settled);
        return settled;
      });
    }
  };

  const openConversation = (id: string) => {
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    setActiveId(id);
    setTurns(conversation.turns);
  };

  const deleteConversation = (id: string) => {
    setConversations((current) => {
      const next = current.filter((conversation) => conversation.id !== id);
      save(next);
      return next;
    });
    if (id === activeId) {
      setActiveId(null);
      setTurns([]);
    }
  };

  const startNew = () => {
    setActiveId(null);
    setTurns([]);
  };

  const body = useMemo(
    () =>
      turns.length === 0 ? (
        <EmptyState onPick={send} />
      ) : (
        turns.map((turn) => <Message key={turn.id} turn={turn} />)
      ),
    [turns]
  );

  return (
    <div className={"app" + (collapsed ? " rail" : "")}>
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((value) => !value)}
        theme={theme}
        onThemeToggle={() => setTheme(theme === "dark" ? "light" : "dark")}
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onDelete={deleteConversation}
        onNew={startNew}
      />
      <main className="board">
        <div className="stage">
          <div className="thread-scroll" ref={scrollRef}>
            <div className="thread">{body}</div>
          </div>
        </div>
        <Composer onSend={send} busy={busy} />
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck and build**

```bash
cd frontend && npm run typecheck && npm run build
```

Expected: exit 0. Fix any error before continuing; do not proceed on a red typecheck.

- [ ] **Step 3: Prune CSS that nothing references**

Now that every component exists, unused CSRS selectors can be removed on evidence rather than guesswork.

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim/frontend
for cls in $(grep -oE '^\.[a-z0-9-]+' src/styles.css | tr -d '.' | sort -u); do
  grep -rqF "$cls" src --include=*.tsx || echo "UNUSED: $cls"
done
```

Remove the blocks for clearly CSRS-only families reported above — `settings-*`, `index-*`, `rebuild-*`, `document-*`, `chart-*`, `vbar-*`, `ctx-*`, `runtime-*`, `clarify-*`, `dv-*`. Keep anything a later C-10 card will need (`hbar-*` is used by the scanned card; `markdown` and `pill` stay). Re-run `npm run build` after pruning.

- [ ] **Step 4: Run every check**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
cd frontend && npm test && npm run build
```

Expected: 1514 Python passed, ruff clean, 25 frontend passed, build succeeds.

- [ ] **Step 5: Verify the never-built case still holds**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
mv frontend/dist /tmp/vc-dist-holdout
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
mv /tmp/vc-dist-holdout frontend/dist
```

Expected: identical pass count.

- [ ] **Step 6: Verify live in a browser**

```bash
docker compose up -d
uv run uvicorn vericlaim.api.app:app --port 8000
```

Open `http://localhost:8000/` and confirm:

1. The hero renders in DM Sans with the suggestion chips.
2. Asking the flagship question streams stage rows live, each with an elapsed time.
3. The answer renders with citation chips; clicking one scrolls to and flashes its card.
4. Evidence tabs appear for every source reached, each showing `cited/total`.
5. The SQL card shows the executed SQL in Geist Mono; the scanned card shows an OCR bar.
6. The theme toggle switches light and dark, and the sidebar collapses to a rail.
7. Reloading restores the conversation from history.
8. No frame named `ping` ever appears.

- [ ] **Step 7: Commit**

```bash
cd /Users/rowdy/Projects/CSAI/VeriClaim
git add frontend/src
git commit -F - <<'MSG'
feat(C-10.2): wire the shell, the stream and the evidence together

One conversation is a list of turns, and a turn owns everything the run
reported: its question, the stages it produced, the final payload and
any error. That is why a conversation restored from history renders
identically to a live one -- there is no second shape for replay.

CSS that nothing references was pruned with grep over the components
rather than by guesswork, once every component existed to be measured.
MSG
```

- [ ] **Step 8: Update `tasks/todo.md`**

Tick C-10.2 and C-10.4, and add a review section covering: what was copied and what was skipped, the stage-level waiting state and why it differs from CSRS, the four renderers, the citation link, and what the live browser run showed. Commit with `docs(C-10.2): ...`.

## Verification

After every task:

```bash
cd frontend && npm test && npm run typecheck
cd .. && uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm" && uv run ruff check .
git -C /Users/rowdy/Projects/work/CIL/CSRS status --porcelain | wc -l    # must stay 4
git -C /Users/rowdy/Projects/work/unibot-endgame status --porcelain | wc -l  # must stay 2
```
