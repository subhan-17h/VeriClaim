# C-10.2 + C-10.4 — The chat shell and evidence cards

Design for VeriClaim's real interface, built on the CSRS design system. Binding authority
for the implementation plan that follows.

## Why this card exists

C-10.1 shipped the pipe and a deliberately bare probe page. It works and it looks like a
test harness, which is what it was. This card replaces it with the interface the project
is actually demonstrated through.

Two roadmap cards are done together, on purpose. C-10.2 is the chat shell; C-10.4 is the
per-source evidence renderers. An answer that shows `[E1]` markers with nothing to click
through to is not a finished screen, and citations are this product's entire thesis —
splitting them would ship a screen that misrepresents the system.

## What is copied, and what is adapted

`CSRS/frontend` is a read-only reference. Copy out; never edit in place.

**Copied verbatim**

- The seven `woff2` faces: DM Sans 400/500/600/700, Geist Mono 400/500, Instrument Serif
  400. About 104 KB total, all SIL Open Font License. Attribution belongs in the README
  when C-12.5 writes it; recorded here so it is not forgotten.
- The `:root` and `[data-theme="light"]` token blocks: palette, shadow scale, radius
  scale, layout constants, easing curves.
- The CSS for the app shell, sidebar, board, thread, message, composer, hero, and the
  `.hbar-*` horizontal bar.

**Skipped**

- The `.dv-*` Data Viewer section, roughly 226 lines. It styles CSRS's corpus explorer,
  which VeriClaim has no equivalent of. Copying dead CSS to delete later is how a
  stylesheet becomes unmaintainable.

**Adapted**

- `SourcesCard` becomes `EvidenceCard`: grouped by source, four renderers rather than one.
- `ProgressSteps` is driven by real `stage` events instead of a simulated trace.
- `Sidebar` keeps history, theme toggle and the rail collapse, and drops CSRS's index
  management and model-picker panels, which have no VeriClaim counterpart.

## The one structural difference

CSRS streams tokens, so its waiting state is a typing indicator. VeriClaim's stream is
**stage-level**: `understand`, `route`, `plan`, one `source.*` per routed source,
`collect`, `sufficiency`, `synthesize`, `verify`. There is no token stream, and the
gateway has no streaming path at all.

So the waiting state is not a typing dot. It renders the pipeline as it happens, each
stage with a live elapsed time, and the answer appears when `final` arrives. This is
strictly more informative than a dot, and it is C-10.3's trace rail arriving early because
the data is already on the wire. C-10.3 is then reduced to promoting this into a
persistent side rail, which the plan for that card will decide.

A stage carrying a non-empty `error` renders in an error state rather than being hidden. A
source that failed is something the reader must see.

## Components

```
src/
  App.tsx                 shell, run lifecycle, history wiring
  components/
    Sidebar.tsx           rail <-> expanded, history, theme toggle
    Composer.tsx          autogrowing textarea, Enter to send
    Message.tsx           user and assistant turns, markdown answer
    Stages.tsx            live pipeline; ProgressSummary once finished
    EvidenceCard.tsx      grouped, collapsible; dispatches per source type
    evidence/
      PolicyEvidence.tsx
      SqlEvidence.tsx
      SpreadsheetEvidence.tsx
      ScannedEvidence.tsx
    Citation.tsx          renders [En] as a chip that targets its card
    EmptyState.tsx        hero and suggested questions
    icons.tsx             the subset actually used
  lib/
    history.ts            localStorage conversations
    citations.ts          split answer text into prose and markers
```

### Evidence renderers

One per source type, because a locator means something different in each. Every card shows
its `[En]` id, the human `citation` string the backend already computes, and the content.
Beyond that:

- **Policy** — document, page, section, chunk id.
- **SQL** — the executed SQL in Geist Mono, wrapped in a horizontal scroller; the tables
  touched; the row count. The executed SQL is exposed deliberately (C-9.3) and is one of
  the things this project demonstrates that neither reference repo does.
- **Spreadsheet** — workbook › sheet › row › A1 range, the cell-level provenance the
  invariants require.
- **Scanned** — document, page, and OCR confidence on the `.hbar-*` bar, plus a note when
  `escalated` is true that the page was re-read by the vision tier.

  The card does **not** label anything "low confidence". `is_low_confidence` compares the
  measured value against `settings.ocr_confidence_floor`, which lives in Python and is not
  on the wire. Mirroring it as a TypeScript constant would be a second copy of a setting
  nobody re-reads, drifting the first time it is tuned — the same duplication the
  no-corpus-in-prompts rule exists to prevent. So the card shows the measured confidence
  and lets the reader judge, and the qualification a low-confidence reading needs is
  already carried by the answer text, which the synthesis prompt requires.

  Putting the floor on the final payload would let the card label it properly. That is a
  protocol change, C-9.6 has just settled this contract, and it is recorded here as a
  candidate for C-10.6 rather than smuggled into a UI card.

Cards are grouped by source with a count in the toggle, collapsed by default, matching
`SourcesCard`'s behaviour.

### Citations

`[E1]` in the answer becomes a chip. Clicking it expands the evidence group if collapsed,
scrolls its card into view, and highlights it briefly. An `[En]` with no matching evidence
renders as plain text in an error style rather than a chip — the answer is displayed as
written, and a marker pointing at nothing must look wrong rather than silently vanish.

Answers are rendered with `react-markdown` and `remark-gfm`, as in CSRS. Citation chips
are substituted after markdown rendering, over text nodes only, so a marker inside a code
block stays literal.

## State and history

One conversation is a list of turns; a turn is a question, its stage list, the final
payload, and any error. Conversations persist to `localStorage` under
`vericlaim.history.v1`, capped at 20, adapted from CSRS's `history.ts`.

No state library. React state in `App.tsx`, passed down. The tree is shallow enough that
anything else would be ceremony.

## Error handling

- A run that throws surfaces the message in the assistant turn, in `.api-error`, with the
  stages already received left visible so the reader can see how far it got.
- A stream ending without `final` throws from `askStream` and is displayed the same way.
- A `degraded` or unverified answer is labelled as such. The system's whole posture is that
  an unverified answer must never look like a verified one.
- `failures` on the final payload are listed under the answer.

## Testing

Vitest, over logic rather than appearance:

- `citations.ts` splits prose and markers, leaves an unmatched marker as plain text, and
  ignores markers inside code fences.
- `history.ts` round-trips, caps at 20, and survives malformed stored JSON.
- The evidence dispatcher picks the right renderer per `source_type`.

Rendering tests are out of scope; there is no jsdom in this project and adding one for
snapshot tests of a demo UI is not worth the dependency.

Python-side tests are unaffected. The suite must still pass with and without
`frontend/dist`, on a machine with no Node.

## Out of scope

Source browser and page deep-linking (C-10.5, needs C-9.4). Metadata panel (C-10.6).
Cancellation and the composer's stop button (C-9.5). The evaluation view (C-10.7).

## Acceptance

`npm run build` and `npm test` pass; `uv run pytest` passes with and without `dist`. In a
browser, against the running stack: the flagship question streams its stages live, renders
a cited answer, shows evidence grouped by all four sources with the correct renderer for
each, and clicking a citation chip reveals and highlights the evidence it names. The light
and dark themes both render correctly, and the sidebar collapses to a rail.
