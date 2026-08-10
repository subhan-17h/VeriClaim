# VeriClaim

**Evidence-Grounded Insurance Claims Intelligence**

A multi-source agentic system for property insurance. One natural-language business question is
routed across **four heterogeneous enterprise sources** and answered once, with citations that trace
every material claim back to its origin.

| Source | Kind | Cited as |
|---|---|---|
| Policy documents | Unstructured text (RAG) | document › page › clause |
| Claims transactions | Structured SQL | schema.table + the executed query |
| Operational spreadsheets | Semi-structured workbooks | workbook › sheet › row › A1 range |
| Scanned paperwork | Images of text (OCR) | document › page + OCR confidence |

The system determines which sources a question actually requires, executes only those, reconciles the
returned evidence rather than concatenating it, and refuses or qualifies when the evidence is
insufficient.

> Status: in development. See [tasks/todo.md](tasks/todo.md) for the roadmap and
> [docs/superpowers/specs/2026-08-10-vericlaim-design.md](docs/superpowers/specs/2026-08-10-vericlaim-design.md)
> for the design.

## Architecture

```
                       React + TypeScript SPA
      chat · live agent trace · evidence cards · source browser
                              │  NDJSON stream
                              ▼
                    FastAPI  /api/ask/stream
                              │
   ┌──────────────────────────▼───────────────────────────────┐
   │        ORCHESTRATOR — LangGraph StateGraph               │
   │                                                          │
   │  understand ─▶ route ─▶ plan ─┐                          │
   │      ▲                         │ only the routed sources │
   │      │   ┌──────────┬──────────┴─┬────────────┬───────┐  │
   │      │   ▼          ▼            ▼            ▼       │  │
   │      │ policy    nl2sql     spreadsheet   scanned     │  │
   │      │  RAG                                  OCR      │  │
   │      │   └──────────┴──────┬─────┴────────────┘       │  │
   │      │                     ▼                          │  │
   │      │           normalize → EvidenceSet              │  │
   │      │                     ▼                          │  │
   │      │             sufficiency check                  │  │
   │      └──── insufficient ───┤  bounded: MAX_REPLANS=2   │  │
   │                       sufficient                       │  │
   │                            ▼                           │  │
   │                  synthesize (cited [En])               │  │
   │                            ▼                           │  │
   │        verify: citation resolution + overclaim guard   │  │
   └────────────────────────────┬─────────────────────────────┘
                                ▼
              Answer + EvidenceSet + trace + cost/latency
                └─────── every node traced → LangSmith
```

Every tool returns `Evidence` and nothing else; synthesis never sees raw tool output. Citations are
`[En]` markers resolved deterministically against the evidence set — **an unresolvable marker is a
hard failure, not a warning**.

## Setup

Requires Python 3.12 (provisioned by `uv`), Docker, Node 20+, and a local Ollama with
`nomic-embed-text` pulled.

```bash
uv sync                                # install; provisions python 3.12
cp .env.example .env                   # then add your API keys
docker compose up -d                   # postgres on 5435
uv run python scripts/warm_models.py   # docling layout/table/OCR weights
uv run python scripts/generate_corpus.py --seed 42   # build all four synthetic sources
```

## Commands

```bash
uv run pytest -v                                             # full test suite
uv run pytest -m "not ocr and not postgres and not ollama"   # offline-only
uv run ruff check .                                          # lint
uv run python scripts/smoke.py                               # prove the read-only role rejects DDL
uv run python eval/run.py                                    # evaluation suite
cd frontend && npm run build                                 # frontend build
```

## Safety properties

- **SQL safety is deterministic, never model-mediated** — sqlglot AST validation, table *and* column
  allow-lists enforced by an optimizer pass, LIMIT capping, statement timeout, and a genuine
  read-only Postgres role. `sqlglot` is pinned exactly because the validator depends on its
  optimizer internals.
- **Decision-support language only** — evidence may appear *consistent with* coverage; final
  determination rests with the claims team. The system never asserts a claim is approved.
- **Honest limitation over fabrication** — refuses or qualifies when evidence is insufficient,
  distinguishes fact from inference, and flags low-confidence OCR.

## Licence

Academic project. Synthetic data throughout — no real customer data.
