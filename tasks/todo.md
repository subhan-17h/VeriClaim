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
recorded cost figure, and cross-provider fallback on hard failure. — **MET**, see review below.

### Cost-control addendum (2026-08-11)

Gemini's free tier was cut 50–80% in Dec 2025 (`2.5-flash` 10 RPM / **250 RPD**, `2.5-flash-lite`
15 RPM / **1000 RPD**). Free-tier exhaustion arrives as HTTP 429 — a genuinely transient error —
so the C-1 ladder would have retried, fallen through to OpenAI, and **started billing silently**.

- [x] **C-1.7** Spend guard + paid-provider policy: `config.yaml` rebalanced Gemini-first;
      `paid` on `ModelSpec` (**fails closed**); `VC_ALLOW_PAID_FALLBACK` (default `false`) makes
      the ladder refuse paid rungs; `VC_MAX_COST_USD_TOTAL=5.00` / `_PER_REQUEST=0.25` checked
      **before** each call. — `N`
- [x] **C-1.8** Free-tier rate limiter: `gateway/quota.py` token bucket over `rpm`/`rpd`; RPM
      overrun waits, RPD overrun raises `QuotaExhaustedError`; daily counters persist keyed by
      model + **US/Pacific** date so a restart cannot reset them. Self-throttling is what stops
      us generating the 429s that would walk the ladder toward a paid provider. — `N`

**Acceptance:** free tier exhausted with the flag off → paid rung refused, **$0.00** spent, error
names `VC_ALLOW_PAID_FALLBACK`; flag on → hop taken and priced; loop past the ceiling →
`BudgetExceededError`.

## Phase C-2 — The Evidence spine

- [x] **C-2.1** `evidence.py` — frozen `Evidence` + typed locator union (policy / sql / spreadsheet /
      scanned) + `Provenance`. — `N`
- [x] **C-2.2** `EvidenceSet` — stable `E1..En` ids, dedup, per-source grouping, serialization. — `N`
- [x] **C-2.3** Citation contract — `resolve_citations`; an unresolvable marker is a hard failure. — `N`

**Acceptance:** mixed-source set serializes and every marker resolves; `[E9]` against a 4-item set
fails loudly. — **MET**, see review below.

## Phase C-3 — Policy RAG (source 1)

- [x] **C-3.1** `policy/loaders/` — parser Protocol + registry, text parser, pdfplumber fallback with
      running header/footer removal; `policy/models.py` carries the Chunk contract. — `V` CSRS
- [x] **C-3.2** `docling_parser.py` — page-break-placeholder export; converter cache keyed on the
      **full option set**, not just artifacts path. — `A` CSRS
- [x] **C-3.3** `policy/chunking.py` — `split_text` verbatim; NIST/CSF heading regexes **replaced**
      with policy-form patterns; merged clause runs recovered. — `A` CSRS
- [x] **C-3.4** `policy/embeddings.py` — injectable `Embedder`, nomic prefixes, no module-level
      client. — `A` CSRS
- [x] **C-3.5** `policy/{manifest,store,indexer}.py` — Chroma + SHA-256 manifest, atomic writes;
      **relative-path doc identity** (not bare filename); `search()` gains metadata `filters`;
      `ZeroChunkError` on a paged document that yields nothing. — `A` CSRS
- [x] **C-3.6** `policy/retrieval.py` — BM25 signature validation **and metadata filtering**,
      `rrf_fuse`, hybrid search, **reranking enabled**. — `V` CSRS
- [x] **C-3.7** `policy/tool.py` — `search_policy() -> list[Evidence]`; unconditional source
      scoping; `EmptyIndexError` distinct from no results. — `N`

**Acceptance:** policy query returns evidence citing correct document + page + clause; re-index
reports all-skipped; deleting a file removes its chunks.

## Phase C-4 — Scanned PDFs / OCR (source 4)

- [x] **C-4.1** `scanned/classifier.py` — per-page `digital | scanned | mixed`. — `N`
- [x] **C-4.2** `scanned/docling_ocr.py` — `do_ocr=True` with **explicit** `RapidOcrOptions(lang=["english"])`
      (the default is `["chinese"]`). — `N`
- [x] **C-4.3** Confidence capture — keep `ConversionResult`, thread `ocr_confidence` through every
      layer to the UI. — `N`
- [x] **C-4.4** `scanned/chunking.py` — separate path; the policy clause grammar fabricates clause
      ids from OCR text. — `N`
- [x] **C-4.5** Vision escalation below the confidence floor, recorded as a gateway fallback. — `N`
- [x] **C-4.6** `scanned/tool.py` — `search_scanned()`; low-confidence evidence flagged. Absorbed
      the scanned indexing path (`scanned/indexer.py` + the processor seam), which no task owned
      and without which the tool had no corpus to search. — `N`
- [x] **C-4.7** `scripts/warm_models.py` — add OCR weights. — `A` CSRS

**Acceptance:** an image-only PDF (zero extractable text) indexes to non-zero chunks with per-page
confidence and cites by page; CSRS's silent zero-chunk path proven closed by test.

## Phase C-5 — SQL transactions (source 2)

- [x] **C-5.1** `docker-compose.yml` (postgres:16, port 5435) + `init.sql` read-only role +
      `smoke.py` asserting `InsufficientPrivilege`. — `V` unibot
- [x] **C-5.2** `sql/db.py` — psycopg3 with **connection pooling** + statement timeout. — `A` unibot
- [x] **C-5.3** `sql/validator.py` — the AST safety layer, **near-verbatim**; deliberately relax the
      `exp.Select` gate to admit `UNION`/`INTERSECT`/`EXCEPT`. — `V` unibot
- [x] **C-5.4** `sql/contexts/*.yaml` + loader + profiler, authored for insurance. — `V` pattern
      (YAML lives at `contexts/sql/`; a module and a package cannot share `sql/contexts`.)
- [x] **C-5.5** `sql/resolver.py` + `values_catalog.py` — insurer noise tokens, claim-number fast
      path, **generation-keyed cache** replacing the un-invalidated global. — `A` unibot
      (References are looked up **exactly, in the database**, and never fall through to fuzzy
      matching; the embedding fallback is dropped — grounding here is deterministic.)
- [x] **C-5.6** `sql/planner.py` + `generator.py` — structure kept, **all academic prompts
      rewritten**. — `A` structure, `N` prompts
      (The new prompts are **domain-free**: the insurance rules stay in the reviewed
      contexts' `cautions`, which the prompts make binding, so the knowledge has one home.
      A step's tables must be connected by declared joins — a check the reference could not
      have had, with one table per context and no joins at all.)
- [x] **C-5.7** `sql/observer.py` — deterministic verdicts + insurance shape checks. — `A` unibot
      (Four inherited verdicts plus a fifth, `implausible_values`: a result contradicting a
      documented fact is a different problem from a malformed one. The facts are declared in
      the contexts as `invariants` — sum, non_negative, ordered — so the observer knows
      nothing about insurance, and each is checked only where it is sound.)
- [x] **C-5.8** `sql/refiner.py` + `sql/pipeline.py` — bounded repair loop + **per-step wall-clock
      budget**. — `A` unibot
      (Also `sql/executor.py`. Three independent bounds stop the loop: attempts, wall clock,
      and a repair that returns what it was asked to fix. The inherited empty-result backstop
      could never fire — it re-ran the grounding rewrite over already-rewritten SQL — so it is
      replaced by naming the filter value the database does not hold.)
- [x] **C-5.9** Candidate clustering + unit-test arbitration; `DOMAIN_CONVENTIONS` rewritten; the
      silent bare `except` removed. — `A` unibot
      (`DOMAIN_CONVENTIONS` is not rewritten but **replaced by the contexts' `cautions`**, so the
      arbiter enforces what a reviewer wrote. A candidate the observer already rejected never
      gets a vote. Arbitration failure degrades to the deterministic pick, logged and named in
      the selection — the reference swallowed it without a word.)
- [x] **C-5.10** `sql/tool.py` — `query_claims_db() -> list[Evidence]` carrying the executed SQL. — `N`
      (Zero rows is citable evidence, not an absent answer; an unanswerable question and a
      question whose entity is ambiguous both refuse **by name** rather than returning `[]`.)

**Acceptance:** `tests/sql` green; `smoke.py` proves DDL rejected at the DB level; every unsafe-SQL
test rejects **before** execution.

## Phase C-6 — Spreadsheets (source 3)

- [x] **C-6.1** `sheets/profiler.py` — merged ranges, stacked headers, TOTAL footers, multi-table
      sheets, sentinels, currency/percent formats. — `N`
- [x] **C-6.2** `sheets/ingest.py` — `sheets.*` schema with `_workbook`/`_sheet`/`_row`/`_a1_range`
      lineage; non-destructive generation-tagged ingest. — `A` unibot header detection, rest `N`
      (Non-destructive means **atomic**: the new table is built beside the old one and swapped
      in, so a load that falls over half way leaves the previous data untouched.)
- [x] **C-6.3** Type coercion — currency, percent, separators, NULL sentinels, mixed columns. — `N`
      (Built before C-6.2, which uses it: a commit that does not work on its own is not a
      reviewable increment.)
- [x] **C-6.4** `contexts/sheets/*.yaml` + spreadsheet-aware `SchemaContext`. — `V` pattern
      (A context declaring a `workbook` gets the five lineage columns injected on load, so the
      allow-list permits the very columns a citation is built from and six files cannot drift.)
- [x] **C-6.5** `sheets/tool.py` — shared validator/executor, `SpreadsheetLocator` from lineage. — `N`
      (Evidence is **per row**, not per query: a citation naming only the table would be no
      better than SQL. An aggregate cites the sheet rather than inventing a row.)

**Acceptance:** a spreadsheet answer cites workbook › sheet › row › A1, and that range genuinely
contains the value in the source `.xlsx`.

## Phase C-7 — Orchestrator

- [x] **C-7.1** `orchestrator/state.py` — validated typed state. — `A` unibot shape
- [x] **C-7.2** `nodes/understand.py`. — `V` unibot
- [x] **C-7.3** `nodes/route.py` — **source** router; no question-string matching. — `A` pattern
- [x] **C-7.4** `nodes/plan.py` — per-source sub-goals + answerability gate. — `A` unibot
- [x] **C-7.5** `graph.py` — LangGraph conditional fan-out, concurrent independent sources. — `N`
- [x] **C-7.6** `nodes/collect.py` — normalize into one `EvidenceSet`. — `N`
- [x] **C-7.7** `nodes/sufficiency.py` — deterministic first, then LLM gap check; `MAX_REPLANS=2`. — `A`
- [x] **C-7.8** `nodes/synthesize.py` — evidence-only input, `[En]` markers, no overclaiming. — `N`
- [x] **C-7.9** `nodes/verify.py` — deterministic citation resolution + faithfulness guard. — `N`
- [x] **C-7.10** LangSmith instrumentation; guard against duplicated run trees. — `N`

**Acceptance:** all-four question returns one cited answer with every marker resolving; policy-only
invokes exactly one tool; out-of-scope refuses with zero tool calls.

### Ratification addendum (2026-08-13)

Two decisions were taken in practice across C-5, C-6 and C-7 and never written where they bind —
they lived only in closed review sections. Ratified now, before C-8 generates a corpus against
them: prompts name no part of the corpus, and the corpus is PKR while the code is not.

- [x] **C-7.11** Record both as engineering invariants — `CLAUDE.md`, design non-negotiables 9 and
      10 with their rationale, and the two carve-outs the rule must name honestly: the vision
      transcription prompt and `Evidence.label`. — `N`
- [x] **C-7.12** Guard the three prompts the rule covers but no test did: `escalation.py`'s
      transcription prompt and `unit_tester.py`'s two. — `N`
- [x] **C-7.13** Prove the currency-agnosticism claim the invariant makes. — `N`

**Acceptance:** every prompt constant in `src/` has a test asserting it names no corpus table or
column; the coercion reads the same amount whatever marks its currency; offline suite green.

## Phase C-8 — Synthetic corpus

- [x] **C-8.1** `ops` schema + ~12k claims Jan–Jun 2026 with real FKs and indexes. — `N`
- [x] **C-8.2** 10–14 policy PDFs incl. the sudden-vs-gradual water clause. — `N`
- [x] **C-8.3** 6 deliberately messy `.xlsx` — exactly the six `contexts/sheets/` declares, since
      every workbook needs a reviewed context and a test asserts the names agree. — `N`
- [x] **C-8.4** 60–80 image-only scanned PDFs keyed to real `claim_id`s, ~20% degraded. — `N`
- [x] **C-8.5** Cross-source consistency validator. — `N`

**Acceptance:** `generate_corpus.py --seed 42` is reproducible; consistency validator passes.

### Scope addendum (2026-08-13)

The board's five cards stop at generated files. Verified while planning: that leaves the system
still unable to answer a question. `build_graph` takes its tools injected and only fakes have ever
been passed, and the three ingest functions are called only from tests. Two cards are appended so
the phase ends with the corpus loaded and the flagship question answered live — which is also the
acceptance C-5 and C-7 have both been carrying as "not yet demonstrable". A third records a
blocking defect found while verifying C-8.1. Existing numbers are unchanged, per the C-1.7 and
C-7.11 precedent.

- [x] **C-8.6** The loader — walk `data/` through the existing `index_corpus`,
      `index_scanned_corpus` and `ingest_workbook`. No new indexing logic, and two distinct
      manifest paths so the second pass cannot delete the first's chunks. — `V` CSRS
- [x] **C-8.7** `orchestrator/tools.py` — the registry handing the four real tools to
      `build_graph`, sharing one embedder, `ChunkStore` and `Database` across them. No
      module-level globals. Plus `scripts/ask.py`, the repo's first CLI entry point. — `N`
- [x] **C-8.8** Stop the offline suite billing the real spend ledger. Taken out of order because
      it blocked verification of every other card. — `N`

**Acceptance (appended):** the all-four question returns one cited answer with every `[En]`
resolving, a policy-only question invokes exactly one tool, and an out-of-scope question refuses
with zero tool calls.

### Reliability addendum (2026-08-13)

C-8 closed carrying six unresolved items: four technical deferrals recorded in its review, and
two open product questions it declined to settle alone. C-9 puts HTTP over `run_question` and
C-10 puts a UI over that, so both build on the contracts these items are about — the tool
signature, the trace id, and the answer path's reliability. A contract fixed after the API is
written is fixed in two places. Four cards settle them first.

Two deferrals stay deferred, with their consequences recorded. **Tool-internal model spend is
invisible to the state** — `_source_node` leaves `StageRecord.cost_usd` at 0.0, so every call
the SQL and spreadsheet tools make appears in no stage and `GraphState.total_cost_usd`
under-reports a four-source question by most of its cost. C-9.3 and C-10.6 must therefore read
`gateway.ledger.total_cost_usd`, never `state.total_cost_usd`. And **the BM25 rebuild is
unscoped**: both searchers sign over `store.all_chunks()` unfiltered, so they build the
identical index and cannot corrupt each other's file. Latency only; revisit if C-11 makes it
visible.

- [x] **C-8.9** `scripts/replay.py` — put one question through the graph N times and record
      what varied: routing, per-source evidence counts, the verifier's verdict and its
      objections, which model wrote each stage, fallbacks walked, and cost. — `N`
- [x] **C-8.10** Widen `SourceTool` to `Callable[[SourceRequest], Sequence[Evidence]]`, carrying
      the sub-goal, `understanding` and `trace_id` in a frozen per-call request. Unstrands
      C-5.5's entity resolver and writes `Provenance.trace_id`. — `N`
- [x] **C-8.11** Make a failed ladder walk record what it tried — the attempts and hops now
      travel on the raised error — and reproduce the live failure offline at no quota cost. — `N`
- [x] **C-8.12** Give the final callable rung a larger transient retry budget without
      spending retries on daily quota exhaustion. — `N`
      - [x] Pin ordinary retries on non-final rungs and extended transient retries on
            the final callable rung, including terminal daily quota and recovery cases.
      - [x] Add and thread a configured last-rung transient retry budget without
            changing the ladder's models, order, or paid-fallback policy.
      - [x] Run the offline suite and Ruff, then record the evidence below.
- [ ] **C-8.13** The four-clause flagship question, run live with no `contexts/` edits; and
      delete the two `pyproject.toml` entry points that name modules which do not exist. — `N`
      - [x] Delete the two entry points naming modules that do not exist.
      - [ ] Run the four-clause question live and resolve citations from all four sources.
            Blocked on C-8.14: the first live run answered two sources of four.
- [x] **C-8.14** Scope the entity resolver's ambiguity refusal to the sub-goal the source was
      actually asked, so a mention belonging to another clause cannot abort a source. — `N`
- [x] **C-8.15** Make a counted gap say what the source was asked and what it declares it
      cannot answer, so the replan loop can correct a mis-assigned sub-goal instead of
      rephrasing it. — `N`

**Acceptance:** ten runs recorded and their differences named; a source tool receives the
run's understanding; the flagship question resolves citations from all four sources.

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

### Phase C-1 — closed

**Delivered.** Repo scaffold with exactly pinned dependencies; typed settings plus a
task-to-model routing table; OpenAI and Gemini adapters behind one protocol; a gateway that
routes, retries, prices, and parses; a cross-provider fallback ladder; and LangSmith tracing
that is a genuine no-op when unconfigured.

**Evidence.** `uv run pytest tests -q` → **117 passed**; `uv run ruff check .` → clean. The
acceptance script demonstrated all four required properties against faked providers:

| Property | Result |
|---|---|
| Per-tier routing | `route`→gemini/flash, `sql_generator`→openai/4o-mini, `synthesize`→openai/4o |
| Cost accounting | 3 calls, 4500 tokens, **$0.006600**, with a per-task breakdown |
| Cross-provider fallback | primary outage → answered by `gemini/gemini-2.5-pro`, hop recorded, **charged at the model that answered** |
| Exhaustion | `AllProvidersFailedError` naming all 3 rungs; **0 calls billed** |
| Tracing off | `@traced` returns normally; langsmith never imported |

**Decisions that departed from the reference repos, and why.**

- *Unrouted task raises rather than defaults.* A typo must not silently bill the strong tier
  or downgrade a task that needs it.
- *Routing validated on load.* unibot would have surfaced a bad tier reference only the first
  time that path ran.
- *Config cached once per process.* unibot re-read `config.yaml` and `.env` from disk on every
  LLM call and every DB connection.
- *Failed calls are never recorded.* A ledger counting attempts nobody received would overstate
  spend; cost is charged at the model that actually answered.
- *Dead config not carried over.* CSRS declares `refusal_threshold` and never reads it.

**Carried forward.** `Gateway.with_fallbacks` and `UsageLedger` are the seam C-9.3 will use to
expose cost and fallback events over the API — neither reference repo exposed its trace at all.

**No lessons recorded.** No user corrections during this phase.

### Phase C-1 addendum — closed (C-1.7, C-1.8)

**Why.** Verified free-tier facts invalidated part of what C-1 shipped. Gemini's quotas were
cut 50–80% in Dec 2025, and exhaustion arrives as HTTP 429 — a genuinely transient error — so
the shipped ladder would have retried, fallen through to OpenAI, and **started billing with no
signal**. See [lessons.md](lessons.md) LESSON-4.

**Evidence.** `pytest -q` → **155 passed**; ruff clean. Acceptance runs showed:

| Property | Result |
|---|---|
| Free tier exhausted, flag off | paid provider contacted **0 times**, **$0.00** spent, error names `VC_ALLOW_PAID_FALLBACK` |
| Same run, flag on | answered by `openai/gpt-4o-mini`, 2 hops recorded, priced |
| Spend ceiling | halted at `$0.62` against a `$0.50` cap, bounded *before* the breaching call |
| RPM overrun | 11th call in a minute waited 60 s — **no 429 generated** |
| RPD overrun | refused without sleeping; counters survived a restart; rolled over on the **US/Pacific** boundary |

`ModelSpec.paid` defaults to `True` so a config entry that forgets the flag fails closed.

### Phase C-2 — closed

**Delivered.** `Evidence` + four typed locators, `EvidenceSet` with stable ids, and
deterministic `[En]` citation resolution. Zero API cost — pure data structures and regex.

**Evidence.** `pytest -q` → **270 passed**; ruff clean. The acceptance run built a five-item
set spanning all four sources and showed: locator/`source_type` mismatch refused at
construction; the synthesis view tagging every block and marking a 0.38-confidence OCR page
*"⚠ LOW CONFIDENCE — qualify, do not assert"*; a well-cited answer at **precision 1.00 /
coverage 1.00**; and a fabricated `[E9]` raising `UnresolvableCitationError` naming the
available ids.

**Decisions worth defending.**

- *Locator type checked at construction.* A tool cannot emit evidence nothing can cite,
  because such an object cannot be built.
- *Executed SQL withheld from the synthesis view.* It belongs in the citation and the UI;
  in the prompt it would spend tokens and invite reasoning about the query, not the result.
- *Empty set renders "No evidence was retrieved."* The synthesizer must distinguish that
  from a missing evidence block — opposite correct responses.
- *Under-citing measured, not fatal.* Existing citations still resolve, so it is a
  completeness concern for sufficiency, feeding cross-source scoring in C-11.

**Carried forward.** `render_for_synthesis` is the boundary C-7.8 must call and nothing else;
`resolve_citations` is what C-7.9 checks and what C-11's precision/recall scorers reuse.

### Phase C-3 — closed

**Delivered.** The policy source end to end: parse (two interchangeable PDF parsers), chunk at
clause boundaries, embed through an injectable protocol, store in Chroma under path-keyed
identity, retrieve hybrid with reranking, and return `Evidence` carrying document, page, and
clause.

**Evidence.** `pytest -m "not docling and not ollama"` → **521 passed** (203 new); `-m docling`
→ 5 passed; ruff clean. The live acceptance run, against real Ollama embeddings and Docling:

| Property | Result |
|---|---|
| Index the fixture corpus | 3 documents, **63 chunks** |
| Re-index unchanged | **all 3 skipped**, zero embedding calls |
| `search_policy("sudden escape of water from fixed plumbing")` | `HomeSecure_Plus_2026.pdf › p.3 › 4.2` **first**, PKR 25,000 in its content |
| `search_policy("is gradual leakage covered")` | exclusion clauses 2.1, 4.1, 5.1 across all three documents |
| Delete a file | its chunks and manifest entry removed; 3 → 2 documents |
| Reranker (FlashRank, live weights) | ranks clause 5.1 first for "is gradual leakage excluded" |

**Decisions worth defending.**

- *One collection, `source_type` filter, OCR fields declared now.* Policy and scanned differ in
  how text is obtained, not in what a retrievable passage is. A second storage stack would
  duplicate the manifest, index lifecycle, and reset story for no retrieval benefit. The OCR
  fields carry `None` until C-4 because adding a field to a schema whose rows are already
  persisted in a vector store means a migration.
- *Document identity is the corpus-relative path, everywhere.* The reference implementation
  keys on the basename and enforces global filename uniqueness to survive it — impossible for
  `claims/CLM-1001/estimate.pdf` beside `claims/CLM-1002/estimate.pdf`. The coupling was deeper
  than the manifest: chunk ids, deletion, counts, and pagination all keyed on the name.
- *The sparse index filters its own results.* Dense search filters at the store, so an
  unfiltered BM25 leg would fuse scanned pages into a policy question and the tool would cite
  one as a policy clause.
- *`clause_id` separate from the breadcrumb.* "Cite the clause" becomes an equality check
  rather than a substring search, which is what makes C-11's clause-level citation accuracy
  deterministic.
- *Merged clause runs are recovered.* Docling's reading-order model collapsed the lead-in and
  clauses 5.1–5.4 into one list item, hiding those clause numbers from line-based matching. The
  split boundary is deliberately narrow — a false split would fabricate a clause number.
- *Policy evidence is confidence 1.0.* Retrieval score is relevance, not trustworthiness.
  Putting it in that field would make synthesis hedge about a clause it can read verbatim.
- *Empty index raises; empty result does not.* "We do not know" and "the wording is silent"
  lead to opposite decisions.
- *The converter cache key is the full option set.* Keyed on the artifacts path alone, C-4's
  request for an OCR converter would receive the digital-only one and every scanned page would
  extract as empty, with no error.

**Carried forward.** `PolicySearcher` takes `source_type` and `tool_name`, so C-4 reuses it for
the scanned source rather than reimplementing search. `ZeroChunkError` is the guard that makes
an image-only PDF reaching a non-OCR parser fail loudly — the exact failure C-4 exists to fix.

**No lessons recorded.** No user corrections during this phase.

### Phase C-4 — closed

**Delivered.** The scanned source end to end: classify pages by text density, OCR the image-only
ones with an explicitly named engine, keep per-page confidence, re-read the worst pages through a
vision model that is allowed to refuse, chunk without inventing clause numbers, index through the
shared loop, and return `Evidence` citing document, page, and OCR score.

**Evidence.** `pytest -m "not ocr and not docling and not ollama"` → **657 passed** (136 new);
`-m "docling or ocr"` → 15 passed; ruff clean. The live acceptance run, against real RapidOCR,
real Ollama embeddings, and a real Gemini vision call:

| Property | Result |
|---|---|
| Index three image-only PDFs (zero extractable text) | 3 documents, **7 chunks**, every one page-anchored |
| Per-page confidence populated | CLM-1001 0.99, CLM-1002 0.99, CLM-1003 **0.00** |
| `search_scanned("sudden rupture of a copper supply pipe", claim_id="CLM-1001")` | `CLM-1001_INSPECTION.pdf › p.1 (OCR 0.99)`, findings and conclusion both cited |
| `search_scanned("gradual seepage over several months", claim_id="CLM-1002")` | the counter-evidence scan, retrieved and not smoothed away |
| Ruined scan escalated to `gemini-3.5-flash` | model returned `legible=false`; **the refusal held** and the page stayed refusal-grade evidence |
| Spend | **$0.000000** (free tier) |
| `scripts/warm_models.py` | Docling (layout, table, OCR), FlashRank, Ollama all ready |

**Measurement that overturned the plan.** Four findings, each from running the code rather than
reading it:

- *The density threshold was above every real page.* `0.005` as planned; digital pages measure
  0.00142–0.00230 and image-only scans exactly 0.00000. It would have routed the entire policy
  corpus through OCR. Corrected to `0.0002`, ~7× below the sparsest real text page.
- *`ocr_score` does not measure quality.* Clean scan 0.991, degraded-with-real-errors 0.989. It
  reports confidence in what OCR *found*, not coverage of what was on the page.
- *The worst case arrives as `NaN`, not as a low number.* A page yielding no cells scores `NaN`,
  and `nan < floor` is `False` — the plan's trigger would have passed the single most degraded
  page through untouched. Non-finite now collapses to 0.0.
- *OCR output does have headings.* The plan justified a separate chunker on their absence.
  Docling's layout model emits them. The real justification is sharper: the policy clause grammar
  reads `184.000 Estimated cost of repair` as clause "184.000", and a fabricated clause id is
  worse than none, because citing a clause asserts the clause exists.

**Decisions worth defending.**

- *An unreadable page emits one refusal-grade chunk, not nothing.* Otherwise the zero-chunk guard
  aborts a whole corpus over one smeared page, and the page becomes indistinguishable from one
  that was never in the document. "We could not read this" is evidence in its own right.
- *Escalation is structured to make refusal easy.* The schema's first field is a legibility
  verdict, the prompt forbids inference and completion, and an illegible verdict discards the
  model's text entirely. Asked to transcribe a page it cannot read, a model writes a fluent
  inspection report — the fabrication this project forbids, arriving with no visible defect.
- *An escalated page is capped below 1.0.* A page that needed a second reading is not a page to
  then call pristine.
- *`escalated` means the text came from the vision tier*, not that one was called. A refusal
  assisted nothing, and the flag renders in the citation as "vision-assisted".
- *The claim reference comes from the path, never the recognised text.* An inspection report
  states its own reference, but a misread character there would attach its evidence to the wrong
  matter.
- *One indexing loop, one seam.* Change detection, delete-before-add, the zero-chunk guard, and
  the manifest consistency check are the rules that keep an index honest; only "path → chunks"
  differs between the sources, so only that is injected.
- *`PolicySearcher`'s `source_type`/`tool_name` parameters were not enough.* The trace span name
  and the locator differ too, so retrieval moved to a `ChunkSearcher` base and each source fixes
  all three by construction — a scanned-scoped searcher building policy locators would cite
  somebody's paperwork as a policy clause.
- *Required OCR weights are resolved through Docling, not listed by hand.* A checkpoint version
  bump would otherwise leave the warm script verifying files the parser no longer uses.

**Two holes closed that C-4.5 had left open.** An unbuildable gateway (a missing key) raised
straight out of escalation and would have aborted a corpus, contradicting that module's own
promise; it now degrades exactly as an exhausted quota does. And BM25's format version was bumped,
because an index persisted before `claim_id` became filterable would still look fresh to the
content signature and quietly cost a claim-scoped search its sparse leg.

**Carried forward — a real limitation, not a defect.** `ocr_confidence` catches pages that were
*not read*, not pages that were *misread*. The degraded fixture scores 0.99 and is therefore not
flagged low-confidence, despite genuine transcription errors in its text (`firstfloorbathroom`,
`PKR 96.000` for 96,000), and escalation never fires on it. C-11 must score OCR field-value match
directly rather than trusting the confidence score, and C-8.4's degraded documents should not be
assumed to self-identify.

**No lessons recorded.** No user corrections during this phase.

### Phase C-5 — closed

**Delivered.** A natural-language question becomes safe, validated, read-only SQL over a
documented relational schema, and comes back as evidence carrying the exact query that
produced it. Postgres behind a genuinely read-only role; a pooled, time-bounded connection;
the sqlglot AST validator; seven reviewed schema contexts; deterministic entity grounding;
an answerability gate with a join-graph check; a generator; a deterministic observer;
a bounded repair loop; candidate arbitration; and the tool boundary.

**Evidence.** `uv run pytest tests/sql -q` → **349 passed** (46 of them the unsafe-SQL
rejection suite, every one rejecting *before* execution). `uv run python scripts/smoke.py` →
**7 PASS, exit 0**: the read-only role reads the corpus and is refused `CREATE`, `INSERT`,
`UPDATE`, `DELETE` and `DROP` at the database level. Whole suite **1006 passed**, ruff clean.

**The through-line: the domain lives in the contexts, not in the code.** Three components
needed insurance knowledge — the planner and generator prompts, the observer's shape checks,
and the arbiter's conventions. All three read it from the reviewed context files instead:
`cautions` for the prose the models must obey, `invariants` for the facts a result can be
checked against. A test asserts that no table or column of the corpus appears in any prompt.
The alternative — the reference implementation's approach — leaves the same knowledge in
several places, and the prompt is the copy nobody reviews.

**Departures from the reference, and why.**

- *References are matched exactly, in the database, never fuzzily.* `CLM-1089` scores above
  0.9 against `CLM-1088` under any useful metric; a near miss there is an invented claim.
  The original went the other way and short-circuited every numeric mention to `not_found`.
- *The catalog cache is keyed by a generation* — the snapshot's `xmax`, which advances on any
  committed write — replacing a module global whose docstring told you to restart the process.
  `pg_stat_user_tables` was tried first and rejected: its counters are collected
  asynchronously, so a refresh right after an ingest would cache exactly the stale values the
  key exists to prevent.
- *The embedding fallback is dropped.* It built an OpenAI client inline, and grounding that
  varies between runs cannot be scored.
- *A fifth observer verdict, `implausible_values`.* A result contradicting a documented fact is
  a different problem from a malformed one; folding them together would have the refiner
  rewrite SQL that is correct over data that is merely odd.
- *A step's tables must be connected by declared joins.* A check the reference could not have
  had — one table per context, no joins at all.
- *`DOMAIN_CONVENTIONS` deleted, not rewritten.* The arbiter enforces the contexts' cautions.
- *The inherited empty-result backstop could not fire.* It re-ran the grounding rewrite over
  SQL that rewrite had already produced. Replaced by naming the filter value the database does
  not hold, which turns a wasted repair budget into an honest answer.
- *Arbitration failure is logged and named in the selection.* The original swallowed it, so a
  system whose arbitration had stopped working looked exactly like one that never disagreed.

**Not yet demonstrable, and deliberately so.** The phase's flagship acceptance —
`query_claims_db("How many water-damage claims were filed in March 2026?")` returning evidence
whose locator holds the executed SQL — needs the corpus from **C-8.1** and a live model. Every
component is proven against fakes and against real Postgres; the end-to-end run belongs with
C-8. `scripts/refresh_contexts.py` correctly fails with `Unknown table: ops.adjusters` until
then, and the seven committed contexts are the contract C-8.1 must satisfy.

**Carried forward.** `ClaimsQuerier` takes its dependencies by construction, so C-7's graph node
injects rather than patches. `StepOutcome.selection` carries how a candidate was chosen,
including a note when arbitration could not be reached, which C-9.3 exposes on the trace.

**No lessons recorded.** No user corrections during this phase.

### Phase C-6 — closed

**Delivered.** Messy workbooks become queryable with cell-level citation. A structural
profiler that reads banners, stacked headers, TOTAL footers, spacer columns and sentinels;
coercion that turns a displayed cell into a value without losing its sign or its magnitude;
an atomic ingest into `sheets.*` with lineage on every row; six reviewed schema contexts;
and a tool that answers through the same audited engine as the claims database while citing
workbook › sheet › row › A1 range.

**Evidence.** `uv run pytest tests/sheets -q` → **92 passed**, twelve of them against real
Postgres. Whole suite **1103 passed**, ruff clean.

**The invariant this phase exists to protect.** Spreadsheets normalize into `sheets.*` and
run through one audited SQL path — a second engine would be a second thing to keep safe, and
the guarantees would drift the first time only one was fixed. What keeps them a *distinct*
source is the citation, so evidence is emitted per row rather than per query. An aggregate
cites the sheet rather than inventing a row: a coarser citation that checks out against the
file beats a precise one that does not.

**Departures from the reference, and why.**

- *Re-ingest is atomic, not destructive.* The new table is built beside the old one and
  swapped in inside one transaction. The reference dropped first and loaded second, so a
  failure left no data at all. A test drives that failure and asserts the old rows survive.
- *A merged span's value is repeated across the span.* openpyxl reports it only on the
  top-left cell; taking that literally turns a merged header into one named column and one
  nameless one, and a merged data cell into a NULL that was never in the file.
- *A column's kind is decided by the majority of its values*, with the stragglers recorded.
  The reference degraded a whole column to text on one stray word.
- *Sentinels are set aside before anything is counted.* `N/A` is an absent number, not a
  string; letting it vote makes every column with a gap a text column.
- *Lineage columns are injected when a spreadsheet context loads*, not written into six
  files by hand, and `dump_context` drops them again so a refresh cannot duplicate them.

**Carried forward.** The six contexts are the contract C-8.3's generator must satisfy, and a
test asserts each documented table name is the name the ingest will actually create. The
profiler splits tables on blank *rows* and drops blank columns as spacers; two tables placed
side by side on one sheet would be read as one, which the corpus does not do and the
docstring records.

**No lessons recorded.** No user corrections during this phase.

### Phase C-8 — closed

**Delivered.** The system has been fed. Four generated sources, loaded into three indexes,
answering questions from one CLI with citations that resolve. `generate_corpus.py --seed 42`
builds 12,000 claims across 9 regions, 10 policy wordings, 6 deliberately messy workbooks and
72 image-only scans, validates them against each other, and writes a manifest that is
byte-identical between runs. `load_corpus.py` walks them into Chroma and `sheets.*`.
`ask.py` puts one question to all of it.

**Evidence.** Two `--seed 42` runs, identical manifest. Offline suite **1440 passed**,
`-m postgres` 55, `-m "ocr or docling"` 18, ruff clean. The read-only role still refuses DDL.
Live, against the loaded corpus: the flagship question exits 0, verified and not degraded,
with 3 citations resolved and 0 unresolved or malformed; a policy-only question invokes
exactly one tool; an out-of-scope question refuses after two model calls and zero tool calls;
a compliance question returns cell-level citations from a workbook.

**Said plainly, because the acceptance deserves it.** The flagship answer cites three sources,
not four. It asks for no target or compliance figure, so the router does not reach for the
workbooks — and editing source descriptions until a fourth lit up would be tuning the system
to its own benchmark. All four are demonstrated separately. The answer also states that the
evidence does not establish *why* claims rose, while six scanned documents sat retrieved and
uncited. That is a cross-source completeness gap, not a citation failure, and it is what
C-11's scorers exist to measure.

**What running it for real found.** Every defect below was invisible to a suite of 1348 tests
because nothing had ever fed the system.

- *Two manifests were necessary and not sufficient.* The known hazard was a shared manifest.
  The real one was in the indexer: `_is_inconsistent` compared a manifest against the whole
  collection, but policy and scanned share that collection, so each pass read the other's
  documents as corruption and called `store.reset()`. Both passes reported success while the
  collection only ever held whichever ran last.
- *The observer rejected correct answers twice over.* `GROUPED_TERMS` matched the SQL keyword
  "group by" and missed "grouped by", the form a model actually writes; and the arity check
  read only `calculations` when the planner freely puts the grouping in `purpose`. Either way
  a correct nine-region `GROUP BY` was called a scalar that returned too many rows, and the
  repair loop spent its whole budget rewriting a query that was already right.
- *The validator's first draft passed a corpus that was not there.* Every rule held vacuously
  over an absent `data/scanned`, and "no findings" is exactly what a correct corpus looks like.
- *A per-minute limit killed questions outright.* `max_rate_limit_wait_s` was 20s against a
  tier allowing 10 calls a minute, for questions measured at 24 calls.
- *The committed OCR fixtures had never been reproducible*, despite a plan that identified the
  cause; the primitives were extracted and the fix they were extracted for was not applied.

**Operational note.** `pytest -m postgres` drops every table in `sheets`, so it destroys the
loaded spreadsheet corpus. Re-run `load_corpus.py` before a demo that follows a test run.

**Decisions recorded, so they are not re-litigated.**

- *The LLM context drafter stays unbuilt, deliberately.* The `contexts/` files are the contract,
  and their hand-reviewed half — purpose, `cautions`, `invariants`, the join graph — is what
  makes them trustworthy. An LLM-drafted `cautions` block nobody re-reads is the failure mode
  the "no prompt names the corpus" rule exists to prevent. The half that benefits from
  automation is already automated: `refresh_contexts.py` fills `value_set`, `sample_values` and
  `stats` deterministically from the database.
- *Eval goldens target 40*, the low end of C-11.4's range. A four-source question was measured
  at 24 model calls, most of them on the tiers with a 250/day allowance; single-source questions
  cost a fraction of that. 40 mixed cases fit in one to two days, which is what C-11.5's
  resumability is for.
- *`mid` moved to flash-lite*, decided from the trace rather than guessed. `mid` and `strong`
  both pointed at flash, so one question's calls contended for a single 10/min pool while
  flash-lite sat nearly idle — and flash-lite was already `mid`'s declared fallback.

**Carried forward.** `SourceTool` passes only the sub-goal, so `understanding` never reaches the
SQL path and entity grounding is skipped: a mention the database spells differently becomes a
filter on a value it does not hold. It cannot be recovered from the sub-goal, and it must not be
smuggled through mutable per-run state because the sources fan out concurrently. Widening the
signature is a change to the graph's contract and belongs in its own card. Separately,
`Provenance.trace_id` is `None` on all real evidence and `GraphState.trace_id` is written by
nobody, so a trace cannot yet be reconciled against a finished answer.

**Lessons.** LESSON-11 and LESSON-12 recorded — the shape of a defect that only appears when a
component meets its real inputs, and writing the failing test first when the obvious version of
it passes.

### Phase C-7 — closed

**Delivered.** One question goes in and the system decides which of the four sources it
needs, asks only those, makes one body of evidence out of what they returned, judges
whether that is enough, writes an answer from the evidence and from nothing else, and
checks the answer before returning it. Ten tasks: validated state, understanding,
source routing, per-source planning, the LangGraph fan-out, collection, the bounded
sufficiency loop, synthesis, verification, and the tracing that makes all of it
inspectable afterwards.

**Evidence.** `uv run pytest tests/orchestrator -q` → **226 passed**. Whole offline suite
`-m "not ocr and not docling and not ollama"` → **1332 passed, 18 deselected**, repeated
under randomized ordering. ruff clean. Reference repos match the LESSON-3 baseline.

**The invariant this phase exists to protect.** Synthesis sees `render_for_synthesis()`
and nothing else, so no raw tool output can reach the answer. Around that sit the two
guards that make a wrong answer loud rather than plausible: an unresolvable `[En]` fails
the run, and a source that could not be reached is recorded as a gap the answer must
own rather than a silence it can paper over.

**Departures from the reference, and why.**

- *Every prompt is domain-free and source-free.* No corpus table, column, or source name
  appears in any node's prompt — the knowledge lives in the reviewed context files, and
  each node's tests assert the prompt stays clean. A prompt that names `ops.claims` is a
  routing table maintained in two places, and the second copy is the one nobody updates.
  This continued the C-5/C-6 decision, unratified when this section was written; ratified
  2026-08-13 as design non-negotiables 9 and 10. See the ratification addendum above.
- *The query-type vocabulary is `lookup | aggregate | explanation | assessment`.* The
  reference offers retrieval and analytics, which is the right split for a system with
  one source and the wrong one for a question that needs a policy clause read against a
  count.
- *Failure is not uniform.* A provider failure degrades — the stage records it and the run
  continues from the raw question. `BudgetExceededError`, `PaidFallbackBlockedError` and
  `QuotaExhaustedError` propagate as terminal, because retrying past a spend ceiling is
  the one failure that gets more expensive the harder you try.
- *A contradictory router reply resolves to the refusal.* Asked to both refuse and route,
  the router is taken at its most conservative word and the dropped sources are named on
  the stage. An unusable reply fails the stage and leaves routing unmade rather than
  guessing a source set.
- *Declining is first-class and requires a reason.* A routed source left unasked is an
  error, and so is a sub-goal for a source that was never routed.
- *Nodes stay plain state-to-state functions.* `_update()` translates each into channel
  updates, so a node is callable from a test, from the API, or from the graph with no
  framework in the signature. Graph node names carry a `source.` prefix — `:` is reserved
  by LangGraph.
- *Counted gaps short-circuit the sufficiency model call entirely.* If a planned sub-goal
  returned nothing, that is arithmetic, and paying a model to agree with arithmetic is
  waste. The replan bound is applied where the verdict is written, not where the edge is
  followed, so the loop cannot be re-entered by a second writer.
- *Four refusal paths are answered deterministically with no model call.* Out of scope,
  needing clarification, unanswerable, and no evidence are decisions already made by the
  time synthesis runs.
- *A failed verification regenerates once and is re-checked from the start*, and if that
  fails the answer is replaced by an honest degradation rather than hedged in place. A
  hedge leaves the unsupported sentence in the answer with a qualifier in front of it.
- *One question is one trace.* Our span steps aside when LangGraph is already providing
  one, so the run tree holds one span per node instead of two.

**Lesson recorded.** LESSON-9 — `.env` loaded into `os.environ` at import means the test
suite inherits live credentials. Found by proving C-7.10: the offline suite had been
tracing against the real LangSmith API, which answered `monthly unique traces usage limit
exceeded`. The month's trace allowance was spent by `pytest`.

**Not yet demonstrable, and deliberately so.** The phase's flagship acceptance — an
all-four question returning one cited answer whose every marker resolves — needs the
corpus from **C-8** and a live model. The graph is proven end to end against fakes;
`build_graph` takes every node and every tool as an injectable, so wiring the real four
is construction, not surgery. No `orchestrator/tools.py` registry exists yet; building it
belongs with C-8 when there is a corpus for the tools to read.

**Carried forward.** `run_question` is the single entry point C-9.2 serves over HTTP, and
the stage records it accumulates are already the shape C-9.1's `stage_start/update/end`
events need. `_trace_stage` attaches per-stage model, cost and latency, which is what
C-10.6's metadata panel reads.

### Ratification addendum — closed (C-7.11, C-7.12, C-7.13)

**Why.** Both decisions had been made and enforced by tests for three phases, and were
written down only in review sections describing what a closed phase had done. A rule
recorded where it is described rather than where it binds is a rule the next phase can
contradict without anyone noticing — and C-8, which generates a corpus against both, is
the phase that would have.

**Delivered.** Design non-negotiables 9 and 10, dated against the 2026-08-10 approval,
with the rationale for each; two `CLAUDE.md` invariants; and guards on the three prompts
that had none.

**The rule is stated with its exceptions, which is the only way it is true.**
`Evidence.label` puts "Claims database" in front of the model in every evidence block, and
must: evidence that cannot say where it came from cannot be cited. The vision prompt names
the document kind because a transcriber reads better for knowing it, and it decides
nothing. Stating the rule without those two would have made it a claim the code already
broke — and the comment on `SOURCE_LABELS` said in as many words that those labels were
"used in prompts", which would have contradicted the new invariant three files away.

**Found while ratifying.** The rule covers twelve prompt constants, not the ten that were
guarded. `sql/unit_tester.py` holds two — the assertions that discriminate between
disagreeing SQL candidates, and the grading of candidates against them — with no test file
at all. Both already complied; neither was proven to. And the generic guard fails on the
vision prompt over two corpus identifiers that are also ordinary English words: the table
`claims` and the column `notes`, the latter being the schema field that prompt exists to
fill. The test declares those two and holds the prompt to every other identifier, plus a
second test on schema-qualified names with no allow-list. Rewording the prompt would have
been the wrong way round.

**Departures from what was planned.**

- *Geography stayed out of `CLAUDE.md`.* Regions being sub-city districts is corpus shape,
  not a property that makes the system trustworthy. Breaking it is a corpus bug, and C-8
  could legitimately revisit it with the user. It is recorded in the design doc instead.
- *No new dated decision record under `docs/superpowers/specs/`.* A second file would make
  "where is this written" a two-place question, inside the ratification of a rule about
  knowledge having one home. The approved non-negotiable 8 was left exactly as it was —
  "no hard-coded scenario logic" is a different constraint, and rewriting it would erase
  what was approved on 2026-08-10.
- *Nothing in `tasks/lessons.md`.* Every LESSON there is a defect pattern and the rule that
  prevents it. Nothing was corrected here: the decisions were right, consistently applied,
  and enforced by twenty tests. They were simply unwritten.

**Evidence.** Offline suite **1348 passed, 18 deselected** — 1332 before, plus two guards
on the transcription prompt, four on the arbiter's two prompts, and ten currency spellings.
ruff clean. No executable line changed in `src/`; the only source edits are a docstring and
a comment.

**Carried forward.** `sheets/profiler.py`'s `CURRENCY_FORMAT_RE` omits `inr`, which
`coercion.py`'s `CURRENCY_RE` includes, so a cell formatted `#,##0 "INR"` is stripped by one
and not classified as currency by the other. Harmless on a PKR corpus and left alone rather
than folded into a no-behaviour-change task, but it is a second definition of the same list
and belongs in one place.

### C-8.9 — the variance, measured

**What was asked.** The same question had returned a verified answer on one run and a
degraded one on another, and nothing was known about why. Four causes could produce that
symptom: (a) a source returning nothing on one run, (b) a SQL filter on a value the database
does not hold, (c) a fallback putting a different model on the answer, or (d) ordinary
sampling variance. They are distinguished by different fields of the same record, so the card
measures before anything is fixed.

**Evidence.** Ten runs, written to NDJSON — five of the flagship question, five of the
policy-only control. Offline suite **1440 passed**, ruff clean. Every run cost $0.000000.

| | flagship (n=5) | policy-only (n=5) |
|---|---|---|
| verified | **5 / 5** | **5 / 5** |
| degraded | 0 | 0 |
| model calls per run | 21–30 | 6–8 |
| evidence retrieved | 15–19 | 5 |
| evidence **uncited** | 10–16 | 1–3 |
| varied across runs | evidence counts only | nothing |

**The degraded outcome did not reproduce.** Said plainly because the card's acceptance was
written expecting it to: ten for ten verified. What the runs found instead is a defect that
would produce exactly that symptom, and a control that isolates it.

**Hypothesis (c) is confirmed, and the mechanism is narrower than "a fallback changed the
model".** All ten runs executed with `gemini-3.5-flash` already at **250/250** for the day, so
every `strong` task — plan, synthesize, verify — ran on its fallback rather than its primary.
The fallback ladder is **inverted between two tiers**:

- `strong` is flash (250/day) falling back to flash-lite (1000/day). It degrades *onto the
  larger pool*, which is why all ten runs still verified with flash entirely spent.
- `mid` is flash-lite falling back to **flash** — the *smaller* pool, and the one that runs out
  first. Once flash's daily allowance is gone, a `mid` task walking its ladder finds only the
  paid rung, which fails closed exactly as C-1.7 designed it to.

Flagship run 5 is that failure, recorded verbatim: *"Task 'sql_generator' exhausted its free
models and the remaining fallbacks are paid."* `sql_generator`, `sql_refiner` and
`sql_unit_tester` are all `mid`, and `config.yaml`'s own comment measures `sql_generator` at
nine calls in a single question — so `mid` is the highest-volume tier and the one with nowhere
to go. Enough of those failing leaves the sources with little to return, which leaves synthesis
with little to cite, which is the reported symptom.

The comment at `config.yaml:146-147` is true of a *per-minute* limit and false of a *per-day*
one: flash has somewhere free to go, and flash does not.

**A second finding, unlooked-for and consistent.** The flagship question leaves **10–16 of its
15–19 evidence items uncited**, on every run. The policy-only control leaves 1–3 of 5. This is
the cross-source completeness gap the C-8 review named, now measured rather than observed once,
and it is the strongest available argument for C-11.2's completeness scorer.

**Recorded as a limitation of the measurement, not hidden.** Ten runs on one day cannot
establish a rate for an intermittent outcome, and every one of them ran against an already-
exhausted flash allowance — an unrepresentative condition that happens to be the one that
exposed the defect. The honest statement is that a real defect was found and the historical
degradation was not reproduced.

### C-8.11 — the failed walk, made legible

**A correction to the finding above, from reading `data/replay/flagship.ndjson` run 5 rather
than re-reading the write-up.** The finding named the inverted ladder as the *confirmed* cause.
The record shows something narrower, and the difference matters:

- All eight recorded fallback events are `flash -> flash-lite` on a daily-quota exhaustion, and
  every one **succeeded**. The `strong` ladder worked exactly as designed.
- What failed is `sql_generator`, a `mid` task, whose effective ladder is flash-lite (primary)
  -> flash (fallback) -> paid. So **flash-lite failed first**, and only then was flash found
  day-exhausted.
- **Why flash-lite failed is unrecoverable.** `walk_ladder` built its `events` and `failures`
  lists and discarded both on every path that raised; they survived only via
  `gateway.with_fallbacks`, which runs on success. `Gateway._finish` records to the ledger on
  success too. The one ladder walk most worth diagnosing was the only one leaving no trace.

So the inverted ladder is a real fragility but was **not proven** to be run 5's proximate cause,
and no amount of re-reading recovers it. That is the defect this card fixes, and it is why the
ladder itself is left alone until C-8.12.

**What changed.** The attempts and the hops now travel on the raised error —
`PaidFallbackBlockedError.attempts` / `.events` and `AllProvidersFailedError.events` — as
structured data rather than only as prose, so C-9.3 can expose them. Two situations that shared
one sentence now read differently: a ladder with no free rung configured (nothing attempted) and
a ladder whose every free rung was tried and failed. Because `_source_node` stringifies the
exception, the richer message reaches `GraphState.failures` and the replay record for free.

**Evidence.** Four new tests, all of which fail against the previous behaviour — verified by
reverting the two source files and re-running them, not assumed. They reproduce run 5's exact
shape offline: a transient on the free primary, a daily-quota exhaustion on the free backup, the
paid rung blocked; and they assert the error can tell those two reasons apart, which is
precisely what the live record could not do. **1448 passed, 77 deselected**; ruff clean; no
quota spent.

**A limitation, stated plainly.** This makes the *next* such failure diagnosable. It does not
recover the 2026-08-13 one, whose proximate trigger is gone for good.

### C-8.12 - protect the last callable rung

**What changed.** `limits.last_rung_transient_retries` sets a five-retry budget for the final
rung left after the paid-fallback filter. `walk_ladder` supplies that override only at the final
index; every earlier rung still uses `transient_retries`. The models, their order, and the paid
guard are unchanged.

**Why daily quota stays terminal.** `QuotaExhaustedError` remains a non-transient
`ProviderError`, so it never enters `Gateway.call_model`'s transient retry handler. The larger
budget therefore helps timeouts and other recoverable failures without waiting on an allowance
that resets at midnight Pacific.

**Evidence.** Four new offline fallback tests pin the ordinary non-final budget, the extended
final budget, one-attempt daily exhaustion, and successful recovery without duplicate ledger
entries. **1452 passed, 77 deselected**; ruff clean; no live quota spent.

**Which of those four actually pin the new behaviour, stated precisely.** Reverting the four
source files and re-running showed **one** of them failing — the extended final-rung budget,
where the old ladder gave the last rung three attempts against the six now configured. The
other three pass in both directions *by design*: they guard the halves that must NOT change —
that a non-final rung still gets the ordinary budget, that a daily exhaustion is never retried
into the larger one, and that a recovery is recorded once. A guard that passed before the change
is doing its job; it is only worth saying so that nobody later mistakes four green tests for four
proofs of the fix.

### C-8.14 - scope the resolver's refusal to the sub-goal

**Found by running C-8.13, not by reading code.** The four-clause flagship question returned an
answer from two sources of four. Both SQL-shaped sources died on the same refusal:
*"Did you mean ... for ..."* -- an ambiguity in a product name that only the **policy** clause
had named. The three clauses that never mentioned it died with it.

**Root cause.** `ClaimsQuerier._grounded` resolved mentions from the **whole run's**
`understanding`, while the source had only been asked a **sub-goal**. Every mention in the
question therefore gated every source, so an ambiguity belonging to one clause aborted sources
that had nothing to do with it. `SpreadsheetQuerier` inherits `run`, which is why both failed
identically.

**C-8.10 is the trigger, and the defect is C-8.10's.** Before it, `understanding` was never
supplied, `_grounded` returned `None`, and the resolver never ran in production. C-8.10's card
predicted this risk for the spreadsheet source; it landed on the claims source too, which the
card did not anticipate. The entity resolver was correct and dead; wiring it in made it live and
over-broad in the same commit.

**What changed -- the refusal narrowed, the resolution did not.** `resolve_entities` gained a
keyword-only `scope`. Every mention is still resolved exactly as before, so `stored_values`
loses no spelling help; only the ambiguity gate is scoped, to mentions the sub-goal actually
names. `scope=None` reproduces the previous behaviour, so no other caller changes.

**Why this is safe, and not a weakening of C-5.5.** An out-of-scope ambiguous mention cannot
reach the planner or the generator, because `stored_values` already excludes every non-resolved
mention from what a prompt is shown. If a filter on an ungrounded value is written regardless,
`unresolvable_filters` still catches the empty result and reports that the database holds no
such value -- the "empty result set that looks like a fact" the resolver exists to prevent has a
second line of defence downstream. When the ambiguous mention *is* the sub-goal's own, the
refusal is unchanged: choosing between two entities that both fit stays the user's decision.

**Evidence.** Five tests, and the honest breakdown of which prove what. Reverting the two source
files and re-running showed **three failing**: the out-of-scope stored-values case, the
adapter's threading of the sub-goal as scope, and -- the behavioural proof -- an out-of-scope
ambiguity raising `UnanswerableQuestionError` on the old source, reproducing the live failure's
exact shape offline at zero quota. **Two pass in both directions by design**: that `scope=None`
still gates every mention, and that an in-scope ambiguity still refuses with the same question.
Those two guard the halves that must NOT change. **1456 passed, 77 deselected**; ruff clean.

### C-8.15 - let a counted gap explain itself

**Found by the second flagship run.** With C-8.14 in, three sources of four answered. The
workbook source did not, and the reason had changed: it was handed a sub-goal about per-event
transaction counts -- something its own `cannot_answer` in `contexts/sources.yaml` covers -- and
correctly refused.

**What it is not.** Not the contexts: the workbook entry states plainly that it answers what
target or threshold was set for a period, and that it cannot answer the underlying events one
row per occurrence. Not the model tier: `plan` already runs on `strong`. Not the prompt's
content: it says in as many words never to ask a source for something its `cannot_answer`
covers. The planner was given the right description and disregarded it.

**Root cause.** The plan is model-authored and only checked for arity. `_plan` verifies that
each routed source gets exactly one sub-goal and that none is left unasked, but nothing checks
that a sub-goal suits the source it was handed to. A mis-assignment therefore passes straight
through, the source refuses, and the result is an evidence gap indistinguishable from a source
that genuinely held nothing.

**Why three replans did not correct it.** `_counted_gaps` emitted only "<source> was asked for
its part of the question and returned nothing", and that string is the whole of `retry_hint`.
The planner learned that a source came back empty and never what it had been asked or what that
source covers, so it rephrased the same wrong request three times. The three near-identical
recorded failures are that loop, verbatim.

**What changed.** A counted gap now carries the sub-goal the source was actually given, the
`cannot_answer` it declares, and -- for an unreachable source -- the recorded failure reason.
Each part is omitted cleanly when absent. No model call was added: this path is deliberately
model-free and stays so. No prompt changed; capabilities already reach the planner as data in
the user message, and the hint is data on the same footing.

**Evidence.** Eight tests. Reverting the source file fails **four**: that a silent gap names its
sub-goal and its declared limits, and that a failed gap names its sub-goal, its declared limits
and its recorded reason. The remaining four pass in both directions by design -- they guard the
halves that must not change: the two missing-detail cases degrading to one readable sentence, a
source that returned evidence producing no gap, and the hint still being the gaps joined with
"; ". **1464 passed, 77 deselected**; ruff clean; no quota spent.

**One thing left plain rather than tidied.** The two branches build their shared details with
duplicated lines. A helper would collapse them; it was not worth another round to save ten lines
in a function that reads clearly as it stands.
