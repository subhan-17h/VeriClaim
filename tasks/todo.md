# VeriClaim Roadmap

Task ids are `C-<phase>.<task>`. One task per commit, committed only once its acceptance
criteria are demonstrated. Each phase closes with a commit updating this file.

Full plan rationale, reuse map, and per-phase acceptance criteria: see the approved
implementation plan and [docs/superpowers/specs/2026-08-10-vericlaim-design.md](../docs/superpowers/specs/2026-08-10-vericlaim-design.md).

**Reuse legend:** `V` = copied near-verbatim, `A` = adapted significantly, `N` = new code.
**Reference repos are READ-ONLY:** `unibot-endgame` (NL2SQL), `CSRS` (RAG).

---

## Phase C-1 — Foundation: repo, config, gateway, tracing

- [x] **C-1.1** Repo scaffold: git init, `.gitignore`, pinned `pyproject.toml`, `uv sync`, CLAUDE.md
      rewritten for VeriClaim, `tasks/`, design doc. — `N`
- [x] **C-1.2** `config.py` — pydantic-settings `Settings` (`VC_` prefix) + `config.yaml` per-task
      model routing. — `V` CSRS `config.py:20-107`, `A` unibot `config.yaml` routing idea
- [x] **C-1.3** `gateway/providers.py` — OpenAI + Gemini adapters behind one protocol, module-cached
      clients, explicit timeouts. — `A` unibot `llm.py` signature only
- [x] **C-1.4** `gateway/__init__.py` — `complete_json` / `complete` / `complete_vision`; reads
      `response.usage` → tokens, cost, latency per call. — `N`
- [x] **C-1.5** `gateway/fallback.py` — transient retry → alternate model → alternate provider →
      structured degradation; fallback events recorded. — `N`
- [x] **C-1.6** `tracing.py` — `@traced` over LangSmith `@traceable`, no-op when unconfigured. — `N`

**Acceptance:** `uv run pytest tests/gateway -v` green; mocked call proves per-tier model choice, a
recorded cost figure, and cross-provider fallback on hard failure.

## Phase C-2 — The Evidence spine

- [ ] **C-2.1** `evidence.py` — frozen `Evidence` + typed locator union (policy / sql / spreadsheet /
      scanned) + `Provenance`. — `N`
- [ ] **C-2.2** `EvidenceSet` — stable `E1..En` ids, dedup, per-source grouping, serialization. — `N`
- [ ] **C-2.3** Citation contract — `resolve_citations`; an unresolvable marker is a hard failure. — `N`

**Acceptance:** mixed-source set serializes and every marker resolves; `[E9]` against a 4-item set
fails loudly.

## Phase C-3 — Policy RAG (source 1)

- [ ] **C-3.1** `policy/loaders/` — parser Protocol + registry, text parser, pdfplumber fallback with
      running header/footer removal. — `V` CSRS
- [ ] **C-3.2** `docling_parser.py` — page-break-placeholder export; converter cache keyed on the
      **full option set**, not just artifacts path. — `A` CSRS
- [ ] **C-3.3** `policy/chunking.py` — `split_text` verbatim; NIST/CSF heading regexes **replaced**
      with policy-form patterns. — `A` CSRS
- [ ] **C-3.4** `policy/embeddings.py` — injectable `Embedder`, nomic prefixes, no module-level
      client. — `A` CSRS
- [ ] **C-3.5** `policy/store.py` — Chroma + SHA-256 manifest, atomic writes; **relative-path doc
      identity** (not bare filename); `search()` gains metadata `filters`. — `A` CSRS
- [ ] **C-3.6** `policy/retrieval.py` — BM25 signature validation, `rrf_fuse`, hybrid search,
      **reranking enabled**. — `V` CSRS
- [ ] **C-3.7** `policy/tool.py` — `search_policy() -> list[Evidence]`; **loud failure on zero chunks
      from a non-zero-page document**. — `N`

**Acceptance:** policy query returns evidence citing correct document + page + clause; re-index
reports all-skipped; deleting a file removes its chunks.

## Phase C-4 — Scanned PDFs / OCR (source 4)

- [ ] **C-4.1** `scanned/classifier.py` — per-page `digital | scanned | mixed`. — `N`
- [ ] **C-4.2** `scanned/docling_ocr.py` — `do_ocr=True` with **explicit** `RapidOcrOptions(lang=["english"])`
      (the default is `["chinese"]`). — `N`
- [ ] **C-4.3** Confidence capture — keep `ConversionResult`, thread `ocr_confidence` through every
      layer to the UI. — `N`
- [ ] **C-4.4** `scanned/chunking.py` — separate path; OCR text has no reliable headings. — `N`
- [ ] **C-4.5** Vision escalation below the confidence floor, recorded as a gateway fallback. — `N`
- [ ] **C-4.6** `scanned/tool.py` — `search_scanned()`; low-confidence evidence flagged. — `N`
- [ ] **C-4.7** `scripts/warm_models.py` — add OCR weights. — `A` CSRS

**Acceptance:** an image-only PDF (zero extractable text) indexes to non-zero chunks with per-page
confidence and cites by page; CSRS's silent zero-chunk path proven closed by test.

## Phase C-5 — SQL transactions (source 2)

- [ ] **C-5.1** `docker-compose.yml` (postgres:16, port 5435) + `init.sql` read-only role +
      `smoke.py` asserting `InsufficientPrivilege`. — `V` unibot
- [ ] **C-5.2** `sql/db.py` — psycopg3 with **connection pooling** + statement timeout. — `A` unibot
- [ ] **C-5.3** `sql/validator.py` — the AST safety layer, **near-verbatim**; deliberately relax the
      `exp.Select` gate to admit `UNION`/`INTERSECT`/`EXCEPT`. — `V` unibot
- [ ] **C-5.4** `sql/contexts/*.yaml` + loader + profiler, authored for insurance. — `V` pattern
- [ ] **C-5.5** `sql/resolver.py` + `values_catalog.py` — insurer noise tokens, claim-number fast
      path, **generation-keyed cache** replacing the un-invalidated global. — `A` unibot
- [ ] **C-5.6** `sql/planner.py` + `generator.py` — structure kept, **all academic prompts
      rewritten**. — `A` structure, `N` prompts
- [ ] **C-5.7** `sql/observer.py` — deterministic verdicts + insurance shape checks. — `A` unibot
- [ ] **C-5.8** `sql/refiner.py` + `sql/pipeline.py` — bounded repair loop + **per-step wall-clock
      budget**. — `A` unibot
- [ ] **C-5.9** Candidate clustering + unit-test arbitration; `DOMAIN_CONVENTIONS` rewritten; the
      silent bare `except` removed. — `A` unibot
- [ ] **C-5.10** `sql/tool.py` — `query_claims_db() -> list[Evidence]` carrying the executed SQL. — `N`

**Acceptance:** `tests/sql` green; `smoke.py` proves DDL rejected at the DB level; every unsafe-SQL
test rejects **before** execution.

## Phase C-6 — Spreadsheets (source 3)

- [ ] **C-6.1** `sheets/profiler.py` — merged ranges, stacked headers, TOTAL footers, multi-table
      sheets, sentinels, currency/percent formats. — `N`
- [ ] **C-6.2** `sheets/ingest.py` — `sheets.*` schema with `_workbook`/`_sheet`/`_row`/`_a1_range`
      lineage; non-destructive generation-tagged ingest. — `A` unibot header detection, rest `N`
- [ ] **C-6.3** Type coercion — currency, percent, separators, NULL sentinels, mixed columns. — `N`
- [ ] **C-6.4** `sheets/contexts/*.yaml`. — `V` pattern
- [ ] **C-6.5** `sheets/tool.py` — shared validator/executor, `SpreadsheetLocator` from lineage. — `N`

**Acceptance:** a spreadsheet answer cites workbook › sheet › row › A1, and that range genuinely
contains the value in the source `.xlsx`.

## Phase C-7 — Orchestrator

- [ ] **C-7.1** `orchestrator/state.py` — validated typed state. — `A` unibot shape
- [ ] **C-7.2** `nodes/understand.py`. — `V` unibot
- [ ] **C-7.3** `nodes/route.py` — **source** router; no question-string matching. — `A` pattern
- [ ] **C-7.4** `nodes/plan.py` — per-source sub-goals + answerability gate. — `A` unibot
- [ ] **C-7.5** `graph.py` — LangGraph conditional fan-out, concurrent independent sources. — `N`
- [ ] **C-7.6** `nodes/collect.py` — normalize into one `EvidenceSet`. — `N`
- [ ] **C-7.7** `nodes/sufficiency.py` — deterministic first, then LLM gap check; `MAX_REPLANS=2`. — `A`
- [ ] **C-7.8** `nodes/synthesize.py` — evidence-only input, `[En]` markers, no overclaiming. — `N`
- [ ] **C-7.9** `nodes/verify.py` — deterministic citation resolution + faithfulness guard. — `N`
- [ ] **C-7.10** LangSmith instrumentation; guard against duplicated run trees. — `N`

**Acceptance:** all-four question returns one cited answer with every marker resolving; policy-only
invokes exactly one tool; out-of-scope refuses with zero tool calls.

## Phase C-8 — Synthetic corpus

- [ ] **C-8.1** `ops` schema + ~12k claims Jan–Jun 2026 with real FKs and indexes. — `N`
- [ ] **C-8.2** 10–14 policy PDFs incl. the sudden-vs-gradual water clause. — `N`
- [ ] **C-8.3** 6–8 deliberately messy `.xlsx`. — `N`
- [ ] **C-8.4** 60–80 image-only scanned PDFs keyed to real `claim_id`s, ~20% degraded. — `N`
- [ ] **C-8.5** Cross-source consistency validator. — `N`

**Acceptance:** `generate_corpus.py --seed 42` is reproducible; consistency validator passes.

## Phase C-9 — API + streaming

- [ ] **C-9.1** `api/protocol.py` — NDJSON event schema. — `V` both repos
- [ ] **C-9.2** `api/app.py` — `/api/ask` + `/api/ask/stream`, keepalive, SPA mount. — `A` both
- [ ] **C-9.3** **Expose the trace and the executed SQL** (neither reference repo does). — `N`
- [ ] **C-9.4** Source-browser endpoints incl. `#page=N` anchoring. — `A` CSRS
- [ ] **C-9.5** Client cancellation. — `N`

## Phase C-10 — Frontend

- [ ] **C-10.1** Vite + React 18 + TS scaffold, NDJSON client, typed event unions. — `V` CSRS
- [ ] **C-10.2** Chat shell, streaming answer, history. — `V` CSRS
- [ ] **C-10.3** Live agent trace rail. — `V` CSRS + unibot reducers
- [ ] **C-10.4** Evidence cards, one renderer per source type. — `A` CSRS + `N`
- [ ] **C-10.5** Source browser with page deep-linking. — `A` CSRS
- [ ] **C-10.6** Query metadata panel (models, tokens, cost, latency, fallbacks). — `N`
- [ ] **C-10.7** Evaluation view. — `N`

## Phase C-11 — Evaluation suite

- [ ] **C-11.1** `eval/dataset.py` — golden schema, **no fixed-count assertions**. — `A` CSRS
- [ ] **C-11.2** Deterministic scorers (routing, SQL fact coverage, evidence hit rate, locator match,
      cross-source completeness, citation precision/recall, refusal). — `A` unibot
- [ ] **C-11.3** LLM judge — supplementary only, cached, strict schema. — `A` CSRS
- [ ] **C-11.4** `eval/goldens.jsonl` — 40–60 cases across 13 categories. — `N`
- [ ] **C-11.5** `eval/run.py` — resumable, per-stage latency/error capture. — `A` both
- [ ] **C-11.6** `eval/report.py` — per-category scorecard + cost/latency aggregates. — `A` CSRS
- [ ] **C-11.7** LangSmith datasets + experiments. — `N`

## Phase C-12 — Integration, hardening, documentation

- [ ] **C-12.1** End-to-end tests for all eight representative query shapes.
- [ ] **C-12.2** Failure-path tests (provider outage, DB down, missing weights, malformed output).
- [ ] **C-12.3** Full regression run.
- [ ] **C-12.4** Root-cause eval failures; general fixes only.
- [ ] **C-12.5** `README.md` — architecture, setup, measured results.
- [ ] **C-12.6** Final verification pass; close out this file with a review section.

---

## Review

_Populated as phases close._
