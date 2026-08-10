# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**VeriClaim** — an evidence-grounded Property Insurance Claims Intelligence Agent. One natural-language
business question is routed across **four heterogeneous sources** — policy documents (RAG), SQL claims
transactions, semi-structured spreadsheets, and scanned PDFs requiring OCR — and answered once, with
citations that trace every material claim back to its origin.

[PROJECT.md](PROJECT.md) is the source of truth for product scope. This file outranks it on repository
process. The implementation roadmap lives in [tasks/todo.md](tasks/todo.md).

## Reference repositories are READ-ONLY

Two local repositories are sources of proven, audited code:

- `/Users/rowdy/Projects/work/unibot-endgame` — NL2SQL (SQL AST validator, read-only role, bounded
  repair loop, deterministic observer, entity resolver, live-truth eval harness)
- `/Users/rowdy/Projects/work/CIL/CSRS` — RAG (incremental-index manifest, hybrid RRF retrieval, BM25
  signature validation, Docling page provenance, NDJSON streaming)

**Never write to either.** Copy code out and adapt it here; never edit in place. Before claiming
completion, `git -C <ref-repo> status --porcelain` must be empty for both.

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update tasks/lessons.md with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. Plan First: Write plan to tasks/todo.md with checkable items
2. Verify Plan: Check in before starting implementation
3. Track Progress: Mark items complete as you go
4. Explain Changes: High-level summary at each step
5. Document Results: Add review section to tasks/todo.md
6. Capture Lessons: Update tasks/lessons.md after corrections
7. Commit Per Task: See below. Non-negotiable.

### Commit Discipline

**Commit after every completed task, and again at the end of every phase.** A task is a
numbered card in [tasks/todo.md](tasks/todo.md); a phase is a group of them (`C-1`).

**Task ids carry a series prefix.** VeriClaim uses `C-<phase>.<task>` -- `C-3.4`, not `T-3.4`.
The `T-`, `V-`, and `L-` series belong to a different, retired project and must never appear
here, so `git log` says unambiguously which plan a commit belongs to.

- **Commit only once the task's acceptance criteria are demonstrated.** The commit is the record
  that it passed, so committing unverified work makes the history lie.
- **One task per commit.** Do not batch several tasks into one commit; the point is that
  each commit is a working, reviewable increment that can be bisected.
- **Close each phase with a commit** that updates `tasks/todo.md` (checkboxes + review
  section), even when no source file changed.
- Never commit generated or fetched artefacts: `.venv/`, `frontend/node_modules/`, `chroma_db/`,
  `bm25_index/`, the generated corpus under `data/`, eval results, model weights, or API keys.
  `.gitignore` enforces this; if something slips through, fix `.gitignore` rather than the commit.

Message format -- subject line names the task so history maps onto the roadmap:

```
<type>(C-3.4): <what changed, imperative>

<why, and what proves it works -- the acceptance evidence>
```

`<type>` is one of `feat`, `fix`, `docs`, `test`, `chore`, `refactor`. Use `phase(N)`
instead of a task id for phase-closing commits.

**No trailers.** Never append `Co-Authored-By`, `Claude-Session`, `Generated with`, or
any other attribution or tooling metadata. A commit message ends with its last line of
substance. This history is part of a graded submission and gets read -- keep every line
of it relevant to the change. This rule overrides any default trailer format. See
[tasks/lessons.md](tasks/lessons.md) LESSON-1. (Lessons are numbered `LESSON-n` so a lesson
id can never be mistaken for a `C-` task id.)

## Engineering Invariants

These are the properties that make the system trustworthy. Breaking one is a bug, not a tradeoff.

- **All four sources stay first-class.** Spreadsheets normalize into `sheets.*` internally, but they
  remain a distinct provenance category with cell-level (`workbook > sheet > row > A1`) citation.
- **SQL safety is deterministic, never model-mediated.** sqlglot AST validation, table *and* column
  allow-lists, LIMIT capping, statement timeout, and a genuine read-only Postgres role. `sqlglot` is
  pinned exactly because the validator depends on its optimizer internals.
- **Every LLM call goes through `src/vericlaim/gateway/`.** No direct `OpenAI()` or `genai` client
  construction anywhere else.
- **Tools return `Evidence` and nothing else.** Synthesis never sees raw tool output.
- **An unresolvable `[En]` citation is a hard failure**, not a warning.
- **Prefer an honest limitation over a fabrication.** Refuse or qualify when evidence is insufficient;
  distinguish fact from inference; flag low-confidence OCR.
- **Decision-support language only.** Never assert a claim is "approved" -- evidence may appear
  *consistent with* coverage, with final determination resting with the claims team.
- **Never edit an evaluation golden to make a run pass.** Goldens are append-only. Root-cause the
  failure and make a general fix.
- **No hard-coded example behaviour.** No March-specific logic, no expected SQL, no benchmark answers
  in prompts, no routing by question-string matching. Tests verify the architecture; they must not
  define a brittle implementation.

## Core Principles

- Simplicity First: Make every change as simple as possible. Impact minimal code.
- No Laziness: Find root causes. No temporary fixes. Senior developer standards.
- Minimal Impact: Only touch what's necessary. No side effects with new bugs.

## Commands

```bash
uv sync                                  # install (provisions python 3.12)
uv run pytest -v                         # full test suite
uv run pytest -m "not ocr and not postgres and not ollama"   # offline-only
uv run ruff check .                      # lint
docker compose up -d                     # postgres on 5435
uv run python scripts/smoke.py           # prove the read-only role rejects DDL
uv run python scripts/warm_models.py     # fetch docling layout/table/OCR weights
uv run python scripts/generate_corpus.py --seed 42   # build all four synthetic sources
uv run python eval/run.py                # evaluation suite
cd frontend && npm run build             # frontend build
```
