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
