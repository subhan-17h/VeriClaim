# VeriClaim — Design

**Date:** 2026-08-10
**Status:** Approved

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
