# VeriClaim C-9.4 + C-10.5 — the source browser

**Status:** approved design, not yet implemented
**Cards:** C-9.4 (source-browser endpoints, incl. `#page=N` anchoring), C-10.5 (source browser with
page deep-linking)
**Depends on:** C-9.1 (event schema), C-10.4 (evidence cards, one renderer per source type)

## What this is for

The system's claim is that every material statement traces back to its origin. Today that trace stops
one step short: an evidence card names `HomeSecure_Plus_2026.pdf › p.3` and the reader has to take it
on trust. This pair of cards closes the last step — from the citation, to the evidence, to the source
itself, opened at the place the evidence came from.

Scope is deliberately **evidence-anchored**: there is no corpus to browse without a question. Every
route exists to answer "show me where this evidence came from", and nothing exists to answer "show me
everything you have". A standalone explorer would be a second navigation surface in an interface that
currently has exactly one, and it would not strengthen the trace.

## What a source is, per type

The four sources mean four different things by "open the source", and a locator is what says which:

| Source | Locator names | Opening it shows |
|---|---|---|
| `policy` | `document`, `page`, `section`, `chunk_id` | The PDF, rendered inline, at `#page=N` |
| `scanned_pdf` | `document`, `page`, `ocr_confidence`, … | The PDF, rendered inline, at `#page=N` |
| `spreadsheet` | `workbook`, `sheet`, `row`, `a1_range` | The sheet as a grid, the cited row highlighted |
| `sql` | `tables`, `executed_sql`, `row_count` | Each named table's reviewed context |

A SQL claim has no file behind it. What it traces back to is the hand-authored context that says what
the table is and what each column means — reviewed data this repository owns, in `contexts/sql/` and
`contexts/sheets/`. Re-querying the table on a click was considered and rejected: it would run SQL
outside the validated plan path, and the rows it returned need not be the rows the answer used.

## C-9.4 — the endpoints

### Shape

One route per source type, rather than one polymorphic route:

```
GET /api/sources/policy/{document}                  -> application/pdf, inline
GET /api/sources/scanned/{document}                 -> application/pdf, inline
GET /api/sources/spreadsheet/{workbook}/{sheet}     -> application/json (a grid)
GET /api/sources/sql/{table}                        -> application/json (a context)
```

Each returns what its type actually means. A single `GET /api/source?type=…&id=…` returning a
discriminated union was considered: PDF bytes would still need their own route, so it is really two
routes either way, and the union would re-implement in a response body the dispatch a path already
expresses. A `GET /api/evidence/{id}/source` was also considered and rejected — the server would have
to remember a finished run's evidence, and runs here are streamed and forgotten. Adding a session
store to save the client a four-way switch is the wrong trade, particularly when C-10.4 already
established that switch as the honest shape.

### Resolution is by whitelist, never by path join

A name that arrives from a client is **looked up, never joined**. Each route builds the set of names
it will serve and rejects anything outside it with 404:

- `policy` — the `.pdf` files in `settings.policy_dir`
- `scanned` — the `.pdf` files in `settings.scanned_dir`
- `spreadsheet` — the `(workbook, sheet)` pairs declared by the reviewed contexts in
  `settings.sheets_context_dir`, and the workbook must exist in `settings.spreadsheet_dir`
- `sql` — the qualified table names in `settings.sql_context_dir` and `settings.sheets_context_dir`

`../` therefore never reaches a filesystem call: it is a name that is not in the set. This is stronger
than sanitising the input, because it cannot be defeated by an encoding the sanitiser did not
anticipate — the set is built from what exists, not from what was asked for.

The spreadsheet whitelist comes from the reviewed contexts rather than from a directory listing on
purpose. It is the same source of truth the sheets tool resolves against, so the browser cannot open a
sheet the system would never cite.

### Payloads

**Files** are served with `FileResponse`, `media_type="application/pdf"`, and
`content_disposition_type="inline"` so a browser renders rather than downloads. Page anchoring is the
client's `#page=N` fragment on the iframe URL, which browsers' built-in PDF viewers honour and which
never reaches the server.

**A spreadsheet grid:**

```json
{
  "workbook": "Regional_Inspection_Compliance_Q1.xlsx",
  "sheet": "Compliance",
  "columns": ["A", "B", "C"],
  "header": ["region", "inspections_due", "inspections_done"],
  "rows": [["North", "120", "98"], ...],
  "first_row": 2,
  "total_rows": 41,
  "truncated": false
}
```

`header` is the sheet's first row; `rows` are the rows after it, so `first_row` is 2 for an untruncated
sheet. Cells are stringified server-side, so the client renders what the sheet holds rather than
reformatting a number into something the sheet does not say. `first_row` is the spreadsheet row number
of `rows[0]`, which is what makes a locator's `row` addressable in the rendered grid. `columns` carries
the A1 column letters, which is what makes a locator's `a1_range` addressable.

`total_rows` and `truncated` are both present because a capped grid must say so — a truncated sheet
that looked complete would be a quiet lie about the source. The cap is 500 rows, an order of magnitude
above the largest sheet in this corpus, so it bounds a pathological workbook without ever truncating a
real one silently.

**A SQL context** is `context_detail(context)` from `vericlaim.sql.contexts`, already the planning view
of a reviewed table: purpose, columns with meanings and units, joins, cautions. Reused rather than
reshaped, so the browser and the planner cannot disagree about what a table is.

### What these routes are not

No authentication, no rate limiting: the same single-operator local scope C-9.2 recorded. They are
read-only, they run no model call, and they touch no database.

## C-10.5 — the drawer

### Placement

A right-hand drawer over the thread. The thread narrows rather than being replaced, so the answer and
its citations stay visible beside the source — which is the point of opening it. A full-screen overlay
hides the claim while you check it; a new browser tab works only for the two sources that are PDFs.

### Dispatch

`SourceDrawer` takes an `EvidenceItem` and switches on `source_type` through the same kind of table
`EvidenceCard` uses, so a fifth source type breaks in exactly one place:

```
policy      -> PdfSource        <iframe src="/api/sources/policy/…#page=N">
scanned_pdf -> PdfSource        <iframe src="/api/sources/scanned/…#page=N">
spreadsheet -> SheetSource      the grid, cited row highlighted and scrolled to
                                (and the a1_range's cells within it, when the locator has one)
sql         -> TableSource      the context of each named table, beside the executed SQL
```

A locator with a null `page` opens the document at its start rather than fabricating page 1.

### How it opens

An "Open source" control on each evidence card. Citation chips stay as they are: `[E1]` scroll-links
to its evidence card (C-10.2), and from there one more click reaches the source. A chip that jumped
straight into a document would skip the evidence — the thing that says what was actually extracted and
whether the answer used it.

Escape and a close button dismiss the drawer. Opening a different source replaces the content rather
than stacking.

### State

The drawer is one piece of `App` state — the `EvidenceItem` currently open, or null. Nothing is
persisted: which source was last open is not part of a conversation.

## Testing

**Python.** Each route's happy path against the real corpus. A traversal attempt (`../../etc/passwd`,
its percent-encoded form, an absolute path) is a 404 and reads no file. An unknown document, an
unknown sheet of a known workbook, and an undocumented table are each 404. A grid longer than the cap
reports `truncated: true` and the true `total_rows`. The SQL route serves no table that has no
reviewed context.

**TypeScript.** The drawer has a renderer for all four source types, mirroring the existing
`rendererName` test. The URL built from a locator carries the right page anchor, and a null page
produces no anchor.

**Browser.** All four sources opened from a real run against the running stack, which is how C-10.2's
defects were found.

## Out of scope

The trace rail (C-10.3), the metadata panel (C-10.6), the evaluation view (C-10.7). Highlighting the
cited passage *within* a PDF page: the built-in viewer offers no such control and rendering pages
ourselves would mean shipping a PDF renderer, which this pair of cards does not need.
