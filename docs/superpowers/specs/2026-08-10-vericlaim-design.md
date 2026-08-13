# VeriClaim — Design

**Date:** 2026-08-10
**Status:** Approved 2026-08-10. Non-negotiables 9–10 ratified 2026-08-13.

## Problem

Claims staff at a property insurer answer a single business question by searching four disconnected
systems: policy PDFs, an operational claims database, Excel reports, and scanned paperwork. This
causes slow investigations, inconsistent answers, missed evidence, and no audit trail.

## What we are building

One natural-language question → the system determines which of four heterogeneous sources it needs,
executes only those, reconciles the returned evidence, and produces **one cited, grounded, traceable
answer**.

The distinguishing requirement is not retrieval-augmented generation. It is that four *structurally
different, messy* sources collapse into one answer in which every material claim traces back to its
origin, and in which the system prefers an honest limitation over a fabrication.

## Non-negotiables

1. Four first-class sources: policy documents (RAG), SQL transactions, spreadsheets, scanned PDFs (real OCR)
2. Every material claim traceable to an originating source and locator
3. LangSmith tracing end-to-end — not merely wrapping the final LLM call
4. An evaluation suite we designed and ran, with measured numbers
5. Model gateway: per-task tiering, cost/latency accounting, provider fallback
6. Genuine cross-source questions (two-, three-, and four-source)
7. Decision-support language — never "claim approved"; refuse or qualify when evidence is thin
8. Generalizes — no hard-coded scenario logic, no benchmark answers in prompts
9. Domain-free prompts — no corpus table, column, or source name in any prompt that routes, plans, generates SQL, or synthesizes; that knowledge lives only in reviewed context files
10. One locale — PKR throughout, declared as context metadata, never branched on in code

## Architecture

```
                          React 18 + TypeScript + Vite  (single SPA)
        chat · live agent trace · evidence cards · SQL viewer · sheet/page citations
                                        │  NDJSON stream (POST)
                                        ▼
                        FastAPI   POST /api/ask/stream
                                        │
   ┌────────────────────────────────────▼─────────────────────────────────────┐
   │            ORCHESTRATOR  —  LangGraph StateGraph (typed state)           │
   │                                                                          │
   │   understand ──▶ route ──▶ plan ──┐                                      │
   │       ▲                            │ conditional fan-out: only the       │
   │       │                            │ sources the router actually chose   │
   │       │      ┌──────────┬──────────┴──┬──────────────┬───────────────┐   │
   │       │      ▼          ▼             ▼              ▼               │   │
   │       │  policy_rag   nl2sql     spreadsheet     scanned_ocr         │   │
   │       │      │          │             │              │               │   │
   │       │      └──────────┴──────┬──────┴──────────────┘               │   │
   │       │                        ▼                                     │   │
   │       │              normalize → EvidenceSet                         │   │
   │       │                        ▼                                     │   │
   │       │                sufficiency check  (deterministic + LLM)      │   │
   │       └──── insufficient ──────┤  bounded: MAX_REPLANS = 2           │   │
   │                          sufficient                                  │   │
   │                                ▼                                     │   │
   │                     synthesize  (cited, [En] markers)                │   │
   │                                ▼                                     │   │
   │           verify: citation resolution (deterministic) +              │   │
   │                   faithfulness / overclaim guard (LLM)               │   │
   └────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
                   Answer + EvidenceSet + full trace + cost/latency
                     └──────── every node @traced → LangSmith
```

### Tool internals

```
policy_rag      Docling(digital) → heading-stack chunks → nomic embed
                → Chroma + BM25 → RRF(k=60) → FlashRank → PolicyEvidence
                                        locator: {document, page, section/clause}

nl2sql          understand → table-select → resolve entities (deterministic)
                → plan → generate(N candidates) → VALIDATE (sqlglot AST)
                → execute (read-only role, statement_timeout) → observe
                → refine ≤5  ──────────────────────────────▶ SqlEvidence
                                        locator: {schema.table, executed SQL, row refs}

spreadsheet     openpyxl profile (merged ranges, headers, totals, multi-table sheets)
                → normalize into  sheets.*  WITH lineage columns
                   (_workbook, _sheet, _row, _a1_range) → same validator + executor
                                        ────────▶ SpreadsheetEvidence
                                        locator: {workbook, sheet, row, A1 range}

scanned_ocr     scanned-page classifier → Docling do_ocr=True + RapidOCR
                → per-page confidence → OCR-specific chunking → Chroma
                   (source_kind=scanned)  ──────▶ ScannedEvidence
                                        locator: {document, page, ocr_confidence}
```

## The Evidence spine

Every tool returns `list[Evidence]` and nothing else. Synthesis never sees raw tool output. This is
what makes heterogeneous results reconcilable and citations measurable rather than aspirational.

```python
Evidence(
  id="E3",
  source_type="scanned_pdf",              # policy | sql | spreadsheet | scanned_pdf
  source_id="CLM-1088_INSPECTION.pdf",
  content="Inspection identified a burst kitchen supply pipe...",
  locator=ScannedLocator(document=..., page=2, ocr_confidence=0.91),
  provenance=Provenance(tool="scanned_ocr", retrieved_at=..., trace_id=...),
  confidence=0.91,
)
```

Citations are `[En]` markers the synthesizer must emit. A deterministic post-pass resolves every
marker against the `EvidenceSet`; **an unresolvable marker is a hard failure, not a warning.** This
converts citation precision and recall from a judgement call into a computed metric.

## Key decisions and rationale

### Orchestration — LangGraph on top, plain Python inside

LangGraph governs the top level, where a graph genuinely earns its keep: conditional fan-out across
four sources and a real sufficiency loop-back edge. The NL2SQL subsystem remains the adapted
plain-Python bounded pipeline, invoked as a single node.

Rewriting a proven generate→validate→execute→observe→refine loop into graph nodes buys nothing and
risks the most safety-critical code in the system. LangSmith requires neither — `@traceable` traces
plain functions perfectly well — so this choice is about graph legibility, not tracing.

### Spreadsheets — distinct loader, shared query substrate

A dedicated openpyxl profiler handles what a naive pandas ingest cannot: merged ranges, stacked
headers, TOTAL footers, multiple tables per sheet, `N/A` sentinels, currency and percent formats.
Each workbook lands in a separate `sheets.*` schema carrying lineage columns `_workbook`, `_sheet`,
`_row`, `_a1_range` on every row.

Queries then run through the same audited validator and read-only executor as the claims database,
but results are tagged `source_type="spreadsheet"` and cite to the cell:
`Regional_Inspection_Compliance_Q1.xlsx › Northern › row 14 (B14:F14)`.

This keeps spreadsheets a semantically distinct, cell-citable source while making SQL+spreadsheet
cross-source questions a natural join rather than a fragile reconciliation. The alternative —
querying workbooks directly — would require a second query engine, either LLM-generated pandas
(unsafe, no AST guard) or a bespoke op-DSL.

### OCR — RapidOCR primary, vision escalation on low confidence

Docling with an **explicit** `RapidOcrOptions(lang=["english"])`. RapidOCR is ONNX/CPU, offline,
free, and yields per-page confidence. Pages below a confidence floor escalate to a vision model.

That escalation is not decoration: it makes the required gateway fallback observable on a genuine
failure mode rather than a simulated one, and it is the honest answer for handwritten claim forms.
Where OCR quality remains inadequate, the evidence is flagged low-confidence so synthesis qualifies
rather than asserts.

### Model gateway — small, coherent, cost-aware

One module. Per-task model tiering keyed by task name (`router` → cheap, `sql_generator` → mid,
`synthesizer` → strong). Two providers (OpenAI, Gemini) so fallback is real rather than theatre.
Every call reads `response.usage` and records tokens, cost, and latency, which surface in the API,
the UI, and the evaluation report.

Deterministic — no LLM involved — are: SQL validation, entity resolution, result-shape observation,
citation resolution, spreadsheet cell lookup, evidence assembly, and routing *verification*.

### Domain knowledge lives in the reviewed context files, not in the prompts

*Ratified 2026-08-13, having governed the code since C-5.*

Twelve prompts route, plan, write SQL, arbitrate between candidates, judge sufficiency, synthesize,
and verify. None of them names a table, a column, or a source. Every one of those names lives in
`contexts/sql/*.yaml`, `contexts/sheets/*.yaml`, and `contexts/sources.yaml`, and reaches the model
as JSON in the user message.

The reason is not tidiness. A prompt naming `ops.claims` is a schema maintained in two places, and
the copy inside a prompt is the one nobody re-reads when the schema changes. In a reviewed file the
claim is diffable, a fifth source is a file rather than a code change, and a new column is covered
by the guard tests the moment it is written.

Each prompt carries a test that collects every table and column from the real context files and
asserts none appears, so a new table is covered without anyone remembering to extend a list. The
orchestrator's prompts carry a second test asserting no source name appears — choosing sources is
the router's job, made from `contexts/sources.yaml` and nothing else.

**Two things are outside this rule, by name.**

- The vision *transcription* prompt in `src/vericlaim/scanned/escalation.py` says it is reading a
  scanned insurance document. That is deliberate: a transcriber that knows what kind of page it is
  looking at resolves ambiguous characters better. It routes nothing, plans nothing, writes no
  query, and has no schema to leak. It is still held to the table-and-column half of the rule by
  its own test.
- `Evidence.label` — "Claims database", "Policy document" — reaches the model in every evidence
  block. That is provenance, not instruction. Evidence that does not say where it came from cannot
  be cited, and being citable is the whole value of the answer.

### Locale — one currency, regions below the city

*Ratified 2026-08-13.*

The corpus is Pakistani rupees throughout, and the policy wordings are written in them. The code is
not: coercion strips any currency mark, and the unit is declared once per column as `unit: PKR`
beside a `*_pkr` name, with tests asserting the two never disagree. Nothing in the logic asks which
currency a figure is in, so a second currency would be context-file work rather than a code change.

Geography is three levels and one join. A region is a district below a city — `region_name` is
"Lahore Central" — with `city` in {Lahore, Karachi, Islamabad} and a `province` above it. Claims
carry `region_id` into `ops.regions` and no city of their own; the spreadsheets carry `region_name`
as a bare label and join on the string. This is the model `contexts/sql/ops.regions.yaml` already
commits to, and it is what C-8's generator must produce.

PROJECT.md uses PKR and those cities only as example flavour, in a document written throughout in
advisory language. Neither was mandated. Both are chosen here so that the corpus, the contexts and
the worked examples in the brief agree.

## Safety and grounding

- **SQL:** read-only Postgres role, SELECT-only via sqlglot AST validation, table *and* column
  allow-lists enforced by an optimizer pass, LIMIT injection and capping, statement timeout, bounded
  retries. `sqlglot` is pinned exactly because the validator depends on its optimizer internals.
- **Retrieval / OCR / spreadsheets:** cite or do not assert; never answer from model memory; flag
  low-confidence OCR; never fabricate a spreadsheet row or cell.
- **Cross-source synthesis:** distinguish fact from inference; preserve provenance through every
  transformation; never claim a claim is formally approved merely because evidence appears consistent
  with policy — evidence may appear *consistent with* coverage, with final determination resting with
  the claims team.

## Evaluation

Deterministic wherever the evidence permits; the LLM judge is supplementary and never the sole
correctness signal. SQL goldens are stored as SQL and re-executed live at eval time, so they never go
stale as the corpus changes.

Categories: policy-only, SQL-only, spreadsheet-only, OCR-only, each two-source pair, three-source,
all-four, unanswerable, contradictory evidence, ambiguous entity, missing evidence, unsafe-SQL
attempts, OCR degradation.

Metrics: routing accuracy, SQL fact coverage, retrieval evidence hit rate / recall@k, spreadsheet
locator+value match, OCR field match, cross-source completeness, citation precision/recall, refusal
correctness, faithfulness (judge), plus latency, token cost, and loop counts.

**Goldens are append-only and never edited to make a run pass.**

## Synthetic corpus

Four internally consistent sources with a cross-source consistency validator that fails the build if
they drift apart. The planted scenario — normal January/February water-damage rates, a March spike
concentrated in two regions, those regions' inspection-compliance metric collapsing in the
spreadsheet, their scanned inspection reports citing sudden pipe rupture, and policy wording covering
exactly that while excluding gradual leakage — is one of several, and nothing in the implementation
keys off it. Deliberate counter-evidence (a minority of reports describing gradual seepage) makes
"contradictory evidence" and "fact vs inference" real evaluation cases.

## Explicitly out of scope

- **Semantic caching.** Returning previously-computed numbers rather than re-executing SQL is a
  compliance hazard for claims data. If revisited: cache the plan/SQL and re-execute.
- Authentication, multi-tenancy, queues, microservices, distributed anything.
- Editing claim records from the UI — a governance problem, not a feature.
