# Source browser (C-9.4 + C-10.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last step of the citation trace — from an evidence card to the source itself, opened at the page, row or table the evidence came from.

**Architecture:** Four read-only endpoints under `/api/sources/`, one per source type, resolving every client-supplied name against a whitelist built from what exists rather than joining it to a path. A right-hand drawer in the SPA dispatches on `source_type` through the same table `EvidenceCard` already uses, rendering PDFs in an iframe anchored with `#page=N`, spreadsheets as the sheet exactly as written, and SQL as the table's reviewed context.

**Tech Stack:** FastAPI + Starlette `FileResponse`, openpyxl (already a dependency), pytest; React 18 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-08-15-vericlaim-c9-4-c10-5-source-browser-design.md`

## Global Constraints

- Task ids are the `C-` series. Commit subjects name the card: `feat(C-9.4): …`, `feat(C-10.5): …`. **No trailers** — no `Co-Authored-By`, no `Generated with`, no session links. See `CLAUDE.md` and `tasks/lessons.md` LESSON-1.
- One task per commit, committed only once its acceptance is demonstrated.
- Reference repos `/Users/rowdy/Projects/work/unibot-endgame` and `/Users/rowdy/Projects/work/CIL/CSRS` are **read-only**. Copy out, never edit in place.
- No prompt names the corpus. These endpoints route nothing and reach no model, so they sit outside that rule entirely — but nothing added here may be imported into a prompt module.
- Every LLM call goes through `src/vericlaim/gateway/`. This plan adds no LLM call.
- Sheet row cap: **500** (`MAX_SHEET_ROWS`).
- Offline test command (used throughout): `uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"`
- Lint: `uv run ruff check .` must pass before every commit.
- Frontend: `npm test`, `npm run typecheck`, `npm run build` from `frontend/`.

---

## File Structure

**Created**
- `src/vericlaim/api/sources.py` — the catalog, the sheet reader, and the four routes. One responsibility: serving a named source. Kept out of `app.py`, which is about one run's transport.
- `tests/api/test_sources.py`
- `frontend/src/lib/sources.ts` — URL building and the two JSON fetches.
- `frontend/src/lib/__tests__/sources.test.ts`
- `frontend/src/components/SourceDrawer.tsx` — the drawer shell and the four-way dispatch.
- `frontend/src/components/sources/PdfSource.tsx`
- `frontend/src/components/sources/SheetSource.tsx`
- `frontend/src/components/sources/TableSource.tsx`
- `frontend/src/components/__tests__/sources.test.ts`

**Modified**
- `src/vericlaim/api/app.py` — include the router, before the SPA mount.
- `frontend/src/types.ts` — `SheetGrid`, `TableContext`, `TableColumn`.
- `frontend/src/components/EvidenceCard.tsx` — an "Open source" control per card.
- `frontend/src/components/Message.tsx` — thread the callback through.
- `frontend/src/App.tsx` — hold which source is open.
- `frontend/src/styles.css` — drawer and grid styles.
- `tasks/todo.md` — checkboxes and the phase review section (Task 7).

---

### Task 1: The catalog and the two PDF routes

**Files:**
- Create: `src/vericlaim/api/sources.py`
- Create: `tests/api/test_sources.py`
- Modify: `src/vericlaim/api/app.py`

**Interfaces:**
- Consumes: `vericlaim.config.Settings`, `vericlaim.sql.contexts.load_contexts`, `SchemaContext`.
- Produces:
  - `MAX_SHEET_ROWS: int = 500`
  - `SourceCatalog` — frozen dataclass with `policy: Mapping[str, Path]`, `scanned: Mapping[str, Path]`, `sheets: Mapping[tuple[str, str], Path]`, `tables: Mapping[str, SchemaContext]`, and `SourceCatalog.from_settings(settings: Settings) -> SourceCatalog`
  - `build_router(catalog: SourceCatalog) -> APIRouter`
  - `create_app(run=None, dist=None, catalog: SourceCatalog | None = None) -> FastAPI`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_sources.py`:

```python
"""A source is looked up by name in a set built from what exists.

A name that arrives from a client is never joined to a path. That is what makes
traversal a 404 rather than a sanitising problem: `../../etc/passwd` is not a name
the catalog holds, and no filesystem call is ever built from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vericlaim.api.app import create_app
from vericlaim.api.sources import SourceCatalog
from vericlaim.config import Settings

TRAVERSALS = [
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "....//....//etc/passwd",
    "%2Fetc%2Fpasswd",
]


def _corpus(tmp_path: Path) -> Settings:
    """A settings object pointing at a corpus this test built."""
    policies = tmp_path / "policies"
    scanned = tmp_path / "scanned"
    policies.mkdir()
    scanned.mkdir()
    (policies / "HomeSecure_Plus_2026.pdf").write_bytes(b"%PDF-1.4 policy")
    (policies / "notes.txt").write_text("not a document")
    (scanned / "CLM-1001_CLAIM_FORM.pdf").write_bytes(b"%PDF-1.4 scanned")
    return Settings(policy_dir=policies, scanned_dir=scanned)


def _client(tmp_path: Path) -> TestClient:
    catalog = SourceCatalog.from_settings(_corpus(tmp_path))
    return TestClient(create_app(catalog=catalog))


def test_a_policy_document_is_served_for_the_browser_to_render(tmp_path) -> None:
    response = _client(tmp_path).get("/api/sources/policy/HomeSecure_Plus_2026.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    # inline, or the browser downloads the file instead of showing the page.
    assert "inline" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.4 policy"


def test_a_scanned_document_is_served_from_its_own_directory(tmp_path) -> None:
    response = _client(tmp_path).get("/api/sources/scanned/CLM-1001_CLAIM_FORM.pdf")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 scanned"


def test_a_policy_route_does_not_serve_a_scanned_document(tmp_path) -> None:
    """Each route serves the corpus its locator type names, and no other."""
    response = _client(tmp_path).get("/api/sources/policy/CLM-1001_CLAIM_FORM.pdf")

    assert response.status_code == 404


def test_a_document_nobody_indexed_is_not_found(tmp_path) -> None:
    response = _client(tmp_path).get("/api/sources/policy/Invented_2026.pdf")

    assert response.status_code == 404


def test_only_pdfs_are_documents(tmp_path) -> None:
    response = _client(tmp_path).get("/api/sources/policy/notes.txt")

    assert response.status_code == 404


@pytest.mark.parametrize("attempt", TRAVERSALS)
def test_a_traversal_is_a_name_that_does_not_exist(tmp_path, attempt: str) -> None:
    response = _client(tmp_path).get(f"/api/sources/policy/{attempt}")

    assert response.status_code == 404
    assert b"root:" not in response.content


def test_a_corpus_that_was_never_generated_serves_nothing_and_crashes_nothing(
    tmp_path,
) -> None:
    """A fresh checkout has no data/. The API must still import and answer."""
    settings = Settings(
        policy_dir=tmp_path / "absent", scanned_dir=tmp_path / "absent-too"
    )
    client = TestClient(create_app(catalog=SourceCatalog.from_settings(settings)))

    assert client.get("/api/sources/policy/anything.pdf").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_sources.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vericlaim.api.sources'`

- [ ] **Step 3: Write the module**

Create `src/vericlaim/api/sources.py`:

```python
"""Serving the source a piece of evidence came from.

Read-only, and deliberately narrow: every route answers "show me where this evidence
came from" and none answers "show me everything you have". There is no corpus to
browse without a question.

A name that arrives from a client is looked up, never joined. The catalog is built
from what exists, so `../../etc/passwd` is simply a name it does not hold -- which is
stronger than sanitising the input, because it cannot be defeated by an encoding the
sanitiser did not anticipate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from vericlaim.config import Settings
from vericlaim.sql.contexts import SchemaContext, load_contexts

#: The largest sheet this will render. An order of magnitude above the largest sheet in
#: this corpus, so it bounds a pathological workbook without silently truncating a real
#: one -- and a truncated grid says so either way.
MAX_SHEET_ROWS = 500


def _pdfs(directory: Path) -> dict[str, Path]:
    """The PDFs in a directory, by name. Absent directory means an empty corpus."""
    if not directory.is_dir():
        return {}
    return {path.name: path for path in sorted(directory.glob("*.pdf"))}


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    """Every source this API will serve, and nothing else.

    The spreadsheet entries come from the reviewed contexts rather than from a
    directory listing, so the browser cannot open a sheet the system would never cite.
    """

    policy: Mapping[str, Path]
    scanned: Mapping[str, Path]
    sheets: Mapping[tuple[str, str], Path]
    tables: Mapping[str, SchemaContext]

    @classmethod
    def from_settings(cls, settings: Settings) -> SourceCatalog:
        sql_contexts = load_contexts(settings.sql_context_dir)
        sheet_contexts = load_contexts(settings.sheets_context_dir)

        sheets: dict[tuple[str, str], Path] = {}
        for context in sheet_contexts.values():
            if context.workbook is None or context.sheet is None:
                continue
            path = settings.spreadsheet_dir / context.workbook
            if path.is_file():
                sheets[(context.workbook, context.sheet)] = path

        return cls(
            policy=_pdfs(settings.policy_dir),
            scanned=_pdfs(settings.scanned_dir),
            sheets=sheets,
            tables={**sql_contexts, **sheet_contexts},
        )


def _document(corpus: Mapping[str, Path], name: str, kind: str) -> FileResponse:
    path = corpus.get(name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"No {kind} document named '{name}'")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        # Inline, or the browser hands the file to a download instead of showing it.
        content_disposition_type="inline",
    )


def build_router(catalog: SourceCatalog) -> APIRouter:
    router = APIRouter(prefix="/api/sources")

    @router.get("/policy/{document}")
    def policy_document(document: str) -> FileResponse:
        return _document(catalog.policy, document, "policy")

    @router.get("/scanned/{document}")
    def scanned_document(document: str) -> FileResponse:
        return _document(catalog.scanned, document, "scanned")

    return router
```

- [ ] **Step 4: Register the router before the SPA mount**

In `src/vericlaim/api/app.py`, add the import at the top:

```python
from vericlaim.api.sources import SourceCatalog, build_router
```

Change the signature and body of `create_app`:

```python
def create_app(
    run: Runner | None = None,
    dist: Path | None = None,
    catalog: SourceCatalog | None = None,
) -> FastAPI:
```

and immediately before the comment block that begins `# Mounted last, and only when built.`, add:

```python
    # The source routes are registered with the rest of /api, and before the SPA mount
    # for the same reason: a catch-all at "/" would otherwise swallow them.
    sources = (
        catalog if catalog is not None else SourceCatalog.from_settings(get_settings())
    )
    application.include_router(build_router(sources))
```

`get_settings` is imported lazily inside `_default_run` today; add a module-level import beside the other `vericlaim.config` import:

```python
from vericlaim.config import PROJECT_ROOT, get_settings
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS — the new file's tests plus the 29 existing ones.

- [ ] **Step 6: Lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/vericlaim/api/sources.py src/vericlaim/api/app.py tests/api/test_sources.py
git commit -F - <<'EOF'
feat(C-9.4): serve a policy or scanned document by name

A client's name is looked up in a catalog built from what exists, never joined
to a path, so a traversal attempt is simply a name nobody has -- which cannot be
defeated by an encoding a sanitiser did not anticipate. Four traversal forms,
a .txt in the policy directory and a scanned document asked of the policy route
are each a 404. A checkout with no data/ still imports and answers.
EOF
```

---

### Task 2: The spreadsheet grid route

**Files:**
- Modify: `src/vericlaim/api/sources.py`
- Modify: `tests/api/test_sources.py`

**Interfaces:**
- Consumes: `SourceCatalog.sheets`, `MAX_SHEET_ROWS` from Task 1.
- Produces: `read_sheet(path: Path, sheet: str, *, limit: int = MAX_SHEET_ROWS) -> dict[str, object]` returning keys `workbook`, `sheet`, `columns`, `first_row`, `rows`, `total_rows`, `truncated`; route `GET /api/sources/spreadsheet/{workbook}/{sheet}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_sources.py`:

```python
from openpyxl import Workbook

from vericlaim.api.sources import read_sheet


def _workbook(path: Path, rows: int = 4) -> Path:
    """A workbook shaped like this corpus: a title banner, a blank row, then a header."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Compliance"
    sheet.append(["Regional Inspection Compliance - Q1"])
    sheet.append([])
    sheet.append(["region", "scheduled", "completed"])
    for index in range(rows):
        sheet.append([f"Region {index}", 110, 66 + index])
    book.save(path)
    return path


def test_a_sheet_is_returned_as_written_not_as_a_table(tmp_path) -> None:
    """The banner and the blank row are part of the source. Tidying them away would
    show the reader the system's reading of the sheet, not the sheet."""
    grid = read_sheet(_workbook(tmp_path / "Book.xlsx"), "Compliance")

    assert grid["first_row"] == 1
    assert grid["rows"][0][0] == "Regional Inspection Compliance - Q1"
    assert grid["rows"][1] == ["", "", ""]
    assert grid["rows"][2] == ["region", "scheduled", "completed"]


def test_a_row_number_addresses_the_row_the_locator_names(tmp_path) -> None:
    grid = read_sheet(_workbook(tmp_path / "Book.xlsx"), "Compliance")

    # A locator naming row 4 must reach the first data row.
    assert grid["rows"][4 - grid["first_row"]][0] == "Region 0"


def test_columns_are_a1_letters_so_a_range_can_be_pointed_at(tmp_path) -> None:
    grid = read_sheet(_workbook(tmp_path / "Book.xlsx"), "Compliance")

    assert grid["columns"] == ["A", "B", "C"]


def test_cells_are_what_the_sheet_holds(tmp_path) -> None:
    grid = read_sheet(_workbook(tmp_path / "Book.xlsx"), "Compliance")

    assert grid["rows"][3] == ["Region 0", "110", "66"]


def test_a_capped_sheet_says_so_rather_than_looking_complete(tmp_path) -> None:
    grid = read_sheet(_workbook(tmp_path / "Big.xlsx", rows=40), "Compliance", limit=10)

    assert grid["truncated"] is True
    assert grid["total_rows"] == 43
    assert len(grid["rows"]) == 10


def test_an_untruncated_sheet_says_that_too(tmp_path) -> None:
    grid = read_sheet(_workbook(tmp_path / "Book.xlsx"), "Compliance")

    assert grid["truncated"] is False
    assert grid["total_rows"] == len(grid["rows"]) == 7
```

Then a route-level test. Add this helper and tests:

```python
def _sheet_client(tmp_path: Path) -> TestClient:
    """A catalog whose one workbook is declared by a reviewed context."""
    workbooks = tmp_path / "spreadsheets"
    workbooks.mkdir()
    _workbook(workbooks / "Compliance.xlsx")
    settings = Settings(spreadsheet_dir=workbooks)
    catalog = SourceCatalog.from_settings(settings)
    declared = {
        key: path
        for key, path in {("Compliance.xlsx", "Compliance"): workbooks / "Compliance.xlsx"}.items()
    }
    catalog = SourceCatalog(
        policy={}, scanned={}, sheets=declared, tables=catalog.tables
    )
    return TestClient(create_app(catalog=catalog))


def test_a_declared_sheet_is_served_as_a_grid(tmp_path) -> None:
    response = _sheet_client(tmp_path).get(
        "/api/sources/spreadsheet/Compliance.xlsx/Compliance"
    )

    assert response.status_code == 200
    assert response.json()["sheet"] == "Compliance"


def test_a_sheet_no_context_declares_is_not_found(tmp_path) -> None:
    response = _sheet_client(tmp_path).get(
        "/api/sources/spreadsheet/Compliance.xlsx/Undeclared"
    )

    assert response.status_code == 404


def test_a_workbook_nobody_reviewed_is_not_found(tmp_path) -> None:
    response = _sheet_client(tmp_path).get(
        "/api/sources/spreadsheet/Invented.xlsx/Compliance"
    )

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_sources.py -q`
Expected: FAIL — `ImportError: cannot import name 'read_sheet'`

- [ ] **Step 3: Implement the reader and the route**

Add to `src/vericlaim/api/sources.py`, after `_pdfs`:

```python
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def _cell(value: object) -> str:
    """Render a cell as the sheet holds it.

    Stringified here rather than in the client so nothing downstream reformats a
    number into something the sheet does not say. An empty cell is an empty string,
    not a null: the grid is a rectangle.
    """
    if value is None:
        return ""
    return str(value)


def read_sheet(
    path: Path, sheet: str, *, limit: int = MAX_SHEET_ROWS
) -> dict[str, object]:
    """Read one sheet as written: banner, blank rows, header and all."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in book.sheetnames:
            raise KeyError(sheet)
        worksheet = book[sheet]
        width = worksheet.max_column or 0
        rows: list[list[str]] = []
        total = 0
        for values in worksheet.iter_rows(values_only=True):
            total += 1
            if len(rows) < limit:
                padded = list(values) + [None] * (width - len(values))
                rows.append([_cell(value) for value in padded[:width]])
        return {
            "workbook": path.name,
            "sheet": sheet,
            "columns": [get_column_letter(index + 1) for index in range(width)],
            "first_row": 1,
            "rows": rows,
            "total_rows": total,
            "truncated": total > len(rows),
        }
    finally:
        book.close()
```

Add the route inside `build_router`, before the `return router`:

```python
    @router.get("/spreadsheet/{workbook}/{sheet}")
    def spreadsheet(workbook: str, sheet: str) -> dict[str, object]:
        path = catalog.sheets.get((workbook, sheet))
        if path is None:
            raise HTTPException(
                status_code=404,
                detail=f"No reviewed sheet '{sheet}' in workbook '{workbook}'",
            )
        return read_sheet(path, sheet)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/vericlaim/api/sources.py tests/api/test_sources.py
git commit -F - <<'EOF'
feat(C-9.4): serve a spreadsheet as the sheet was written

The corpus workbooks open with a title banner and a blank row, with the real
header at row 3. sheets/profiler.py untangles that for ingestion; reproducing
its judgement here would show the reader the system's reading of the sheet
while claiming to show the source, so the grid carries every row as it stands
and the row numbers stay the ones a locator cites.

A grid capped at 500 rows reports its true total and says it was truncated.
Only a (workbook, sheet) pair some reviewed context declares can be opened.
EOF
```

---

### Task 3: The SQL context route

**Files:**
- Modify: `src/vericlaim/api/sources.py`
- Modify: `tests/api/test_sources.py`

**Interfaces:**
- Consumes: `SourceCatalog.tables` from Task 1, `vericlaim.sql.contexts.context_detail`.
- Produces: `GET /api/sources/sql/{table}` returning `context_detail(context)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_sources.py`:

```python
def _table_client() -> TestClient:
    """The real reviewed contexts: they are committed, so this needs no fixture."""
    catalog = SourceCatalog.from_settings(Settings())
    return TestClient(create_app(catalog=catalog))


def test_a_documented_table_traces_to_its_reviewed_context() -> None:
    response = _table_client().get("/api/sources/sql/ops.claims")
    body = response.json()

    assert response.status_code == 200
    assert body["table"] == "ops.claims"
    assert body["purpose"]
    assert all(column["meaning"] for column in body["columns"])


def test_a_spreadsheet_backed_table_resolves_too() -> None:
    """A SQL locator can name a sheets.* table, and it has a reviewed context as well."""
    response = _table_client().get(
        "/api/sources/sql/sheets.adjuster_performance__performance"
    )

    assert response.status_code == 200


def test_an_undocumented_table_is_not_found() -> None:
    response = _table_client().get("/api/sources/sql/ops.invented")

    assert response.status_code == 404


def test_opening_a_table_runs_no_query() -> None:
    """This test is marked neither postgres nor ollama, and passes with both absent.

    Opening a source must not re-query: the rows a fresh query returned need not be
    the rows the answer used, and the query would sit outside the validated path.
    """
    assert _table_client().get("/api/sources/sql/ops.claims").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_sources.py -q -k table`
Expected: FAIL — 404 for `ops.claims`, because the route does not exist.

- [ ] **Step 3: Implement the route**

In `src/vericlaim/api/sources.py`, extend the contexts import:

```python
from vericlaim.sql.contexts import SchemaContext, context_detail, load_contexts
```

and add inside `build_router`, before `return router`:

```python
    @router.get("/sql/{table}")
    def sql_table(table: str) -> dict[str, object]:
        """What a SQL claim traces back to: the reviewed description of its table.

        No query runs. The rows a fresh query returned need not be the rows the
        answer used, and it would sit outside the validated plan path.
        """
        context = catalog.tables.get(table)
        if context is None:
            raise HTTPException(
                status_code=404, detail=f"No reviewed context for table '{table}'"
            )
        return context_detail(context)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/api/ -q`
Expected: PASS

- [ ] **Step 5: Run the whole offline suite and lint**

Run: `uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"` then `uv run ruff check .`
Expected: all pass; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/vericlaim/api/sources.py tests/api/test_sources.py
git commit -F - <<'EOF'
feat(C-9.4): trace a SQL claim to the reviewed context of its table

A SQL claim has no file behind it. What it traces back to is the hand-authored
context that says what the table is and what each column means, which is reused
verbatim from context_detail so the browser and the planner cannot disagree
about what a table is. Opening a source runs no query: fresh rows need not be
the rows the answer used, and the query would sit outside the validated path.
EOF
```

---

### Task 4: The frontend source client

**Files:**
- Create: `frontend/src/lib/sources.ts`
- Create: `frontend/src/lib/__tests__/sources.test.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: `EvidenceItem`, `Locator` from `types.ts`; `ApiError` from `lib/api.ts`.
- Produces:
  - `types.ts`: `SheetGrid`, `TableColumn`, `TableContext`
  - `sources.ts`: `documentUrl(item: EvidenceItem): string | null`, `fetchSheet(workbook: string, sheet: string, signal?: AbortSignal): Promise<SheetGrid>`, `fetchTable(table: string, signal?: AbortSignal): Promise<TableContext>`

- [ ] **Step 1: Add the wire types**

Append to `frontend/src/types.ts`:

```ts
// What GET /api/sources/spreadsheet/{workbook}/{sheet} returns. The sheet as written:
// rows[i] is spreadsheet row first_row + i, banner and blank rows included.
export type SheetGrid = {
  workbook: string;
  sheet: string;
  columns: string[];
  first_row: number;
  rows: string[][];
  total_rows: number;
  truncated: boolean;
};

export type TableColumn = {
  name: string;
  type: string;
  meaning: string;
  unit?: string;
};

// What GET /api/sources/sql/{table} returns: context_detail's planning view.
export type TableContext = {
  table: string;
  purpose: string;
  columns: TableColumn[];
  useful_for: string[];
  joins: { column: string; references: string; meaning: string }[];
  cautions: string[];
};
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/lib/__tests__/sources.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { documentUrl, fetchSheet, fetchTable } from "../sources";
import type { EvidenceItem } from "../../types";

function evidence(partial: Partial<EvidenceItem>): EvidenceItem {
  return {
    id: "E1",
    source_type: "policy",
    source_label: "Policy document",
    source_id: "doc",
    content: "text",
    citation: "cite",
    locator: { document: "A.pdf", page: 3, section: null, chunk_id: "c1" },
    provenance: { tool: "t", retrieved_at: "now", trace_id: null, query: null },
    confidence: 1,
    ...partial
  } as EvidenceItem;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("documentUrl", () => {
  it("anchors a policy document at the page the evidence came from", () => {
    expect(documentUrl(evidence({}))).toBe(
      "/api/sources/policy/A.pdf#page=3"
    );
  });

  it("sends a scanned document to its own route", () => {
    const item = evidence({
      source_type: "scanned_pdf",
      locator: {
        document: "CLM-1001_CLAIM_FORM.pdf",
        page: 2,
        ocr_confidence: 0.8,
        ocr_engine: "e",
        escalated: false
      }
    });

    expect(documentUrl(item)).toBe(
      "/api/sources/scanned/CLM-1001_CLAIM_FORM.pdf#page=2"
    );
  });

  it("opens a document at its start rather than inventing page one", () => {
    const item = evidence({
      locator: { document: "A.pdf", page: null, section: null, chunk_id: "c1" }
    });

    expect(documentUrl(item)).toBe("/api/sources/policy/A.pdf");
  });

  it("escapes a name so a space or a hash cannot break the URL", () => {
    const item = evidence({
      locator: { document: "A B#1.pdf", page: 1, section: null, chunk_id: "c" }
    });

    expect(documentUrl(item)).toBe("/api/sources/policy/A%20B%231.pdf#page=1");
  });

  it("has no document to open for a source that is not a file", () => {
    const item = evidence({
      source_type: "sql",
      locator: { tables: ["ops.claims"], executed_sql: "SELECT 1", row_count: 1 }
    });

    expect(documentUrl(item)).toBeNull();
  });
});

describe("fetching a source", () => {
  it("asks for the sheet the locator names", async () => {
    const stub = vi.fn(async () =>
      new Response(JSON.stringify({ sheet: "Compliance" }), { status: 200 })
    );
    vi.stubGlobal("fetch", stub);

    const grid = await fetchSheet("Loss Ratio.xlsx", "Loss Ratio");

    expect(stub.mock.calls[0][0]).toBe(
      "/api/sources/spreadsheet/Loss%20Ratio.xlsx/Loss%20Ratio"
    );
    expect(grid.sheet).toBe("Compliance");
  });

  it("reports a source it could not open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "No reviewed context" }), {
          status: 404
        })
      )
    );

    await expect(fetchTable("ops.invented")).rejects.toThrow(
      "No reviewed context"
    );
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/lib/__tests__/sources.test.ts`
Expected: FAIL — cannot resolve `../sources`

- [ ] **Step 4: Implement the client**

Create `frontend/src/lib/sources.ts`:

```ts
// Turning a locator into the source it names.
//
// Only two of the four sources are files. A spreadsheet is fetched as a grid and a
// SQL claim as the reviewed context of its table, because that is what each of those
// actually traces back to.

import { ApiError } from "./api";
import type {
  EvidenceItem,
  PolicyLocator,
  ScannedLocator,
  SheetGrid,
  TableContext
} from "../types";

const DOCUMENT_ROUTE = {
  policy: "policy",
  scanned_pdf: "scanned"
} as const;

/** The URL of the document this evidence came from, anchored at its page. */
export function documentUrl(item: EvidenceItem): string | null {
  const route = DOCUMENT_ROUTE[item.source_type as keyof typeof DOCUMENT_ROUTE];
  if (route === undefined) return null;

  const locator = item.locator as PolicyLocator | ScannedLocator;
  const url = `/api/sources/${route}/${encodeURIComponent(locator.document)}`;
  // A locator with no page opens the document at its start. Defaulting to page one
  // would claim a page the evidence never named.
  return locator.page === null ? url : `${url}#page=${locator.page}`;
}

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    let detail = `Request failed with HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) detail = body.detail;
    } catch {
      // A proxy failure answers in HTML; the status stays the useful fallback.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/** One sheet of one workbook, as written. */
export function fetchSheet(
  workbook: string,
  sheet: string,
  signal?: AbortSignal
): Promise<SheetGrid> {
  const path = `/api/sources/spreadsheet/${encodeURIComponent(
    workbook
  )}/${encodeURIComponent(sheet)}`;
  return read<SheetGrid>(path, signal);
}

/** The reviewed context of one table. */
export function fetchTable(
  table: string,
  signal?: AbortSignal
): Promise<TableContext> {
  return read<TableContext>(
    `/api/sources/sql/${encodeURIComponent(table)}`,
    signal
  );
}
```

- [ ] **Step 5: Run the tests and the typecheck**

Run (from `frontend/`): `npx vitest run` then `npm run typecheck`
Expected: PASS; typecheck exits 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/sources.ts frontend/src/lib/__tests__/sources.test.ts frontend/src/types.ts
git commit -F - <<'EOF'
feat(C-10.5): turn a locator into the source it names

Two of the four sources are files and two are not, so the client builds a URL
for a document and fetches a payload for a sheet or a table. A locator with no
page opens the document at its start: defaulting to page one would claim a page
the evidence never named.
EOF
```

---

### Task 5: The drawer and its four renderers

**Files:**
- Create: `frontend/src/components/SourceDrawer.tsx`
- Create: `frontend/src/components/sources/PdfSource.tsx`
- Create: `frontend/src/components/sources/SheetSource.tsx`
- Create: `frontend/src/components/sources/TableSource.tsx`
- Create: `frontend/src/components/__tests__/sources.test.ts`

**Interfaces:**
- Consumes: `documentUrl`, `fetchSheet`, `fetchTable` from Task 4; `EvidenceItem`, `SheetGrid`, `TableContext`.
- Produces: `SourceDrawer({ item, onClose }: { item: EvidenceItem | null; onClose: () => void })`, and `sourceRendererName(source: SourceType): string` exported for test.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/__tests__/sources.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { citedRowIndex, sourceRendererName } from "../SourceDrawer";
import type { SheetGrid } from "../../types";

const GRID: SheetGrid = {
  workbook: "Book.xlsx",
  sheet: "Compliance",
  columns: ["A", "B"],
  first_row: 1,
  rows: [["banner", ""], ["", ""], ["region", "done"], ["North", "98"]],
  total_rows: 4,
  truncated: false
};

describe("the source dispatcher", () => {
  it("opens every source type", () => {
    expect(sourceRendererName("policy")).toBe("PdfSource");
    expect(sourceRendererName("scanned_pdf")).toBe("PdfSource");
    expect(sourceRendererName("spreadsheet")).toBe("SheetSource");
    expect(sourceRendererName("sql")).toBe("TableSource");
  });
});

describe("finding the cited row", () => {
  it("maps a spreadsheet row number onto the grid", () => {
    expect(citedRowIndex(GRID, 4)).toBe(3);
  });

  it("has nothing to highlight when the locator names no row", () => {
    expect(citedRowIndex(GRID, null)).toBeNull();
  });

  it("has nothing to highlight when the row is outside a truncated grid", () => {
    expect(citedRowIndex(GRID, 99)).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/__tests__/sources.test.ts`
Expected: FAIL — cannot resolve `../SourceDrawer`

- [ ] **Step 3: Write the three renderers**

Create `frontend/src/components/sources/PdfSource.tsx`:

```tsx
import { documentUrl } from "../../lib/sources";
import type { EvidenceItem, PolicyLocator, ScannedLocator } from "../../types";

/** A document, rendered by the browser's own viewer at the page the evidence cites. */
export function PdfSource({ item }: { item: EvidenceItem }) {
  const url = documentUrl(item);
  const locator = item.locator as PolicyLocator | ScannedLocator;
  if (url === null) return null;

  return (
    <>
      <div className="source-meta">
        {locator.document}
        {locator.page === null ? "" : ` · p.${locator.page}`}
      </div>
      <iframe className="source-frame" src={url} title={locator.document} />
    </>
  );
}
```

Create `frontend/src/components/sources/SheetSource.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

import { fetchSheet } from "../../lib/sources";
import type { EvidenceItem, SheetGrid, SpreadsheetLocator } from "../../types";

// Every renderer takes the whole item and reads the locator it knows, so one dispatch
// table can serve all four -- the same shape EvidenceCard uses.
export function SheetSource({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SpreadsheetLocator;
  const [grid, setGrid] = useState<SheetGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const citedRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setGrid(null);
    setError(null);
    fetchSheet(locator.workbook, locator.sheet, controller.signal)
      .then(setGrid)
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [locator.workbook, locator.sheet]);

  useEffect(() => {
    citedRef.current?.scrollIntoView({ block: "center" });
  }, [grid]);

  if (error) return <div className="source-state error">{error}</div>;
  if (!grid) return <div className="source-state">Opening the workbook...</div>;

  const cited = citedRowIndexOf(grid, locator.row);

  return (
    <>
      <div className="source-meta">
        {grid.workbook} › {grid.sheet}
        {locator.row === null ? "" : ` › row ${locator.row}`}
        {locator.a1_range ? ` › ${locator.a1_range}` : ""}
      </div>
      {grid.truncated && (
        <div className="source-state">
          Showing the first {grid.rows.length} of {grid.total_rows} rows.
        </div>
      )}
      <div className="source-grid-wrap">
        <table className="source-grid">
          <thead>
            <tr>
              <th />
              {grid.columns.map((letter) => (
                <th key={letter}>{letter}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, index) => (
              <tr
                key={grid.first_row + index}
                className={index === cited ? "cited" : ""}
                ref={index === cited ? citedRef : null}
              >
                <th scope="row">{grid.first_row + index}</th>
                {row.map((cell, column) => (
                  <td key={grid.columns[column] ?? column}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Re-exported through SourceDrawer, which is where the tested surface lives.
function citedRowIndexOf(grid: SheetGrid, row: number | null): number | null {
  if (row === null) return null;
  const index = row - grid.first_row;
  return index >= 0 && index < grid.rows.length ? index : null;
}
```

Create `frontend/src/components/sources/TableSource.tsx`:

```tsx
import { useEffect, useState } from "react";

import { fetchTable } from "../../lib/sources";
import type { EvidenceItem, SqlLocator, TableContext } from "../../types";

/** What a SQL claim traces back to: what the table is, and what was asked of it. */
export function TableSource({ item }: { item: EvidenceItem }) {
  const locator = item.locator as SqlLocator;
  const [contexts, setContexts] = useState<TableContext[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setContexts(null);
    setError(null);
    Promise.all(
      locator.tables.map((table) => fetchTable(table, controller.signal))
    )
      .then(setContexts)
      .catch((reason: unknown) => {
        if (reason instanceof Error && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [locator.tables]);

  return (
    <>
      <div className="source-meta">{locator.tables.join(", ")}</div>
      <pre className="source-sql">{locator.executed_sql}</pre>
      {error && <div className="source-state error">{error}</div>}
      {!contexts && !error && (
        <div className="source-state">Reading the reviewed context...</div>
      )}
      {contexts?.map((context) => (
        <section className="source-table" key={context.table}>
          <h3>{context.table}</h3>
          <p>{context.purpose}</p>
          <table className="source-columns">
            <tbody>
              {context.columns.map((column) => (
                <tr key={column.name}>
                  <th scope="row">{column.name}</th>
                  <td className="source-type">
                    {column.type}
                    {column.unit ? ` · ${column.unit}` : ""}
                  </td>
                  <td>{column.meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {context.cautions.length > 0 && (
            <ul className="source-cautions">
              {context.cautions.map((caution) => (
                <li key={caution}>{caution}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}
```

- [ ] **Step 4: Write the drawer**

Create `frontend/src/components/SourceDrawer.tsx`:

```tsx
import { useEffect } from "react";

import { PdfSource } from "./sources/PdfSource";
import { SheetSource } from "./sources/SheetSource";
import { TableSource } from "./sources/TableSource";
import type { EvidenceItem, SheetGrid, SourceType } from "../types";

// A locator means something different in each source, so opening one does too. This is
// the table the drawer actually renders through, not a second list kept for the test:
// a table only the test reads would let the body drift away from what it asserts.
const RENDERER: Record<
  SourceType,
  (props: { item: EvidenceItem }) => JSX.Element | null
> = {
  policy: PdfSource,
  scanned_pdf: PdfSource,
  spreadsheet: SheetSource,
  sql: TableSource
};

/** Exposed for test: every source type must be openable. */
export function sourceRendererName(source: SourceType): string {
  return RENDERER[source].name;
}

/** Exposed for test: which rendered row a locator's row number addresses. */
export function citedRowIndex(grid: SheetGrid, row: number | null): number | null {
  if (row === null) return null;
  const index = row - grid.first_row;
  return index >= 0 && index < grid.rows.length ? index : null;
}

function renderSource(item: EvidenceItem) {
  const Renderer = RENDERER[item.source_type];
  return <Renderer item={item} />;
}

export function SourceDrawer({
  item,
  onClose
}: {
  item: EvidenceItem | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!item) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  if (!item) return null;

  return (
    <aside className="source-drawer" aria-label="Source">
      <div className="source-head">
        <span className="source-id">[{item.id}]</span>
        <span className="source-label">{item.source_label}</span>
        <button type="button" className="source-close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="source-body">{renderSource(item)}</div>
    </aside>
  );
}
```

Then delete the private `citedRowIndexOf` from `SheetSource.tsx` and import the exported one instead:

```tsx
import { citedRowIndex } from "../SourceDrawer";
```

replacing its one call site (`const cited = citedRowIndex(grid, locator.row);`).

Note the import direction: `SourceDrawer` imports the three renderers and they import
`citedRowIndex` back from it. If TypeScript or Vite complains about the cycle, move
`citedRowIndex` into `lib/sources.ts` and import it from there in both places — it is a
pure function over a `SheetGrid` and has no reason to live in a component file. Update
the test's import to match if you move it.

- [ ] **Step 5: Run the tests and the typecheck**

Run (from `frontend/`): `npx vitest run` then `npm run typecheck`
Expected: PASS; typecheck exits 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SourceDrawer.tsx frontend/src/components/sources frontend/src/components/__tests__/sources.test.ts
git commit -F - <<'EOF'
feat(C-10.5): render each source the way its locator means

One renderer per kind of locator: a document at the page it cites, a sheet with
the cited row found by its own row number, and a table beside the SQL that was
run against it. A row outside a truncated grid highlights nothing rather than
pointing at the wrong row.
EOF
```

---

### Task 6: Wire the drawer to the evidence, and see it work

**Files:**
- Modify: `frontend/src/components/EvidenceCard.tsx`
- Modify: `frontend/src/components/Message.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `SourceDrawer` from Task 5.
- Produces: `EvidenceCards({ evidence, citations, onOpenSource })`, `Message({ turn, onOpenSource })`.

- [ ] **Step 1: Add the control to each evidence card**

In `frontend/src/components/EvidenceCard.tsx`, change the component signature:

```tsx
export function EvidenceCards({
  evidence,
  citations,
  onOpenSource
}: {
  evidence: EvidenceItem[];
  citations: CitationReport;
  onOpenSource: (item: EvidenceItem) => void;
}) {
```

and inside the `<div className="ev-head">`, after the `not cited` span, add:

```tsx
                    <button
                      type="button"
                      className="ev-open"
                      onClick={() => onOpenSource(item)}
                    >
                      Open source
                    </button>
```

- [ ] **Step 2: Thread the callback through the message**

In `frontend/src/components/Message.tsx`, change the signature:

```tsx
export function Message({
  turn,
  onOpenSource
}: {
  turn: Turn;
  onOpenSource: (item: EvidenceItem) => void;
}) {
```

add `EvidenceItem` to the type import from `../types`, and pass it on:

```tsx
              <EvidenceCards
                evidence={evidence}
                citations={final.citations}
                onOpenSource={onOpenSource}
              />
```

- [ ] **Step 3: Hold the open source in App**

In `frontend/src/App.tsx`, add the import:

```tsx
import { SourceDrawer } from "./components/SourceDrawer";
import type { EvidenceItem } from "./types";
```

add the state beside the others:

```tsx
  const [source, setSource] = useState<EvidenceItem | null>(null);
```

pass the callback to each message:

```tsx
              {turns.map((turn) => (
                <Message key={turn.id} turn={turn} onOpenSource={setSource} />
              ))}
```

add the class so the layout can narrow the thread, replacing the existing `className` expression on the root div:

```tsx
      className={
        "app" +
        (collapsed ? " collapsed" : "") +
        (empty ? " is-empty" : "") +
        (source ? " source-open" : "")
      }
```

and render the drawer as the last child of `<main className="board">`, after `<Composer …/>`:

```tsx
        <SourceDrawer item={source} onClose={() => setSource(null)} />
```

- [ ] **Step 4: Style the drawer**

Append to `frontend/src/styles.css`:

```css
/* ============================================================
   Source drawer: the last step of the trace, beside the answer
   ============================================================ */
.ev-open {
  margin-left: auto; font: inherit; font-size: 11.5px; cursor: pointer;
  background: var(--soft); color: var(--text-2);
  border: 1px solid var(--border-muted); border-radius: var(--r-xs);
  padding: 3px 9px; transition: color .18s var(--ease), border-color .18s var(--ease);
}
.ev-open:hover { color: var(--text); border-color: var(--border); }

.source-drawer {
  position: absolute; top: 0; right: 0; bottom: 0; width: min(46%, 620px);
  display: flex; flex-direction: column;
  background: var(--surface); border-left: 1px solid var(--border);
  box-shadow: var(--shadow-lg); z-index: 20;
}
.source-head {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px; border-bottom: 1px solid var(--border-muted);
}
.source-head .source-id {
  font-family: 'Geist Mono', monospace; font-size: 11px; color: var(--cyan);
}
.source-head .source-label { font-size: 12.5px; color: var(--text-2); }
.source-close {
  margin-left: auto; font: inherit; font-size: 12px; cursor: pointer;
  background: none; border: 1px solid var(--border-muted); border-radius: var(--r-xs);
  color: var(--text-2); padding: 4px 10px;
}
.source-close:hover { color: var(--text); border-color: var(--border); }
.source-body { flex: 1; overflow: auto; padding: 14px 16px; }
.source-meta {
  font-family: 'Geist Mono', monospace; font-size: 11px;
  color: var(--text-muted); margin-bottom: 10px; word-break: break-word;
}
.source-frame {
  width: 100%; height: 100%; min-height: 60vh; border: 1px solid var(--border-muted);
  border-radius: var(--r-sm); background: var(--elevated);
}
.source-state { font-size: 12.5px; color: var(--text-muted); padding: 8px 0; }
.source-state.error { color: var(--text-2); }

.source-grid-wrap { overflow: auto; border: 1px solid var(--border-muted); border-radius: var(--r-sm); }
.source-grid { border-collapse: collapse; font-size: 12px; width: 100%; }
.source-grid th, .source-grid td {
  border: 1px solid var(--border-muted); padding: 4px 8px;
  text-align: left; white-space: nowrap; color: var(--text-2);
}
.source-grid thead th, .source-grid tbody th {
  background: var(--soft); color: var(--text-muted);
  font-family: 'Geist Mono', monospace; font-size: 10.5px; font-weight: 500;
}
.source-grid tr.cited td, .source-grid tr.cited th {
  background: var(--cyan-soft); color: var(--text);
}

.source-sql {
  font-family: 'Geist Mono', monospace; font-size: 11.5px; line-height: 1.6;
  background: var(--elevated); border: 1px solid var(--border-muted);
  border-radius: var(--r-sm); padding: 10px 12px; overflow-x: auto;
  color: var(--text-2); white-space: pre-wrap; word-break: break-word;
}
.source-table { margin-top: 16px; }
.source-table h3 {
  font-family: 'Geist Mono', monospace; font-size: 12px; color: var(--text);
  margin: 0 0 6px;
}
.source-table p { font-size: 12.5px; color: var(--text-2); margin: 0 0 10px; }
.source-columns { border-collapse: collapse; width: 100%; font-size: 12px; }
.source-columns th, .source-columns td {
  border-top: 1px solid var(--border-muted); padding: 6px 8px;
  text-align: left; vertical-align: top; color: var(--text-2);
}
.source-columns th { font-family: 'Geist Mono', monospace; font-size: 11px; color: var(--text); font-weight: 500; }
.source-columns .source-type { color: var(--text-muted); white-space: nowrap; }
.source-cautions { margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--text-muted); }
.source-cautions li { margin-bottom: 4px; }

/* The thread narrows rather than being replaced, so the claim stays beside its source.
   .thread-scroll is `position: absolute; inset: 0`, which ignores padding on .stage --
   its `right` is what has to move. */
.app.source-open .thread-scroll { right: min(46%, 620px); }
.app.source-open .composer-dock { padding-right: calc(28px + min(46%, 620px)); }
```

`.board` is already `position: relative` (`styles.css:397-402`), which is what the drawer positions against — no change needed there.

- [ ] **Step 5: Run the frontend checks**

Run (from `frontend/`): `npx vitest run`, `npm run typecheck`, `npm run build`
Expected: all pass, `dist/` written.

- [ ] **Step 6: Drive it in a real browser**

```bash
docker compose up -d
uv run uvicorn vericlaim.api.app:app --port 8000
```

Open `http://localhost:8000/`, ask the four-source question, and after it finishes open one source of each type from the evidence cards. Confirm, and record what you saw:

1. A policy card opens the PDF at the page its locator names.
2. A scanned card opens its PDF at its page.
3. A spreadsheet card opens the sheet with the cited row highlighted and scrolled into view, banner row and all.
4. A SQL card shows the executed SQL and each named table's reviewed context.
5. Escape closes the drawer; the thread narrows rather than being covered.
6. `list_console_messages` reports no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -F - <<'EOF'
feat(C-10.5): open a source from the evidence that cites it

Every evidence card gains one control, and the drawer opens beside the answer
rather than over it, so a claim and the source it rests on are readable at the
same time. Citation chips are left as they are: a chip that jumped straight
into a document would skip the evidence, which is what says what was extracted
and whether the answer used it.

Driven in a real browser against the running stack: all four source types
opened from a four-source run, with the cited spreadsheet row highlighted and
the PDF viewer landing on the cited page.
EOF
```

---

### Task 7: Close phase C-9

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Tick the cards**

In `tasks/todo.md`, change:

```
- [ ] **C-9.4** Source-browser endpoints incl. `#page=N` anchoring. — `A` CSRS
- [ ] **C-9.5** Client cancellation. — `N`
```

to `- [x]` for both, and tick `- [x] **C-10.5**` in the phase C-10 list.

- [ ] **Step 2: Write the phase review section**

Append to the review sections at the end of `tasks/todo.md`, following the house style of the existing ones (what was built, what was found, what was deferred and why). It must record at minimum:

- C-9.5's finding: client disconnect cannot carry cancellation in this stack, because Starlette's `iterate_in_threadpool` never closes the iterator it wraps — measured at over 15s of continued running at a realistic event pace, and only ever ended by a forced collection. The explicit `POST /api/runs/{run_id}/cancel` is the mechanism; the disconnect path remains a backstop.
- C-9.4's resolution rule: names are looked up in a catalog built from what exists, never joined to a path.
- Why opening a SQL source runs no query.
- Why the spreadsheet grid is the sheet as written rather than the profiler's reading of it.
- What is still open in phase C-10: C-10.3, C-10.6, C-10.7.

- [ ] **Step 3: Verify the whole thing before claiming the phase**

Run each and confirm the output:

```bash
uv run pytest -q -m "not ocr and not postgres and not ollama and not docling and not flashrank and not live_llm"
uv run ruff check .
uv run python scripts/smoke.py
cd frontend && npm test && npm run typecheck && npm run build
git -C /Users/rowdy/Projects/work/unibot-endgame status --porcelain | wc -l
git -C /Users/rowdy/Projects/work/CIL/CSRS status --porcelain | wc -l
```

The two reference repos must show their baseline counts (2 and 4 respectively) — anything higher means something wrote to a read-only repository.

Also confirm the API works in a checkout that was never built:

```bash
mv frontend/dist /tmp/hold && uv run pytest -q tests/api/ && mv /tmp/hold frontend/dist
```

- [ ] **Step 4: Commit**

```bash
git add tasks/todo.md
git commit -F - <<'EOF'
phase(9): close the API and streaming phase

C-9.4 and C-9.5 land the two cards deferred until C-10 had a consumer for them.
Records what the cancellation work found about disconnect detection in this
stack, and why the source routes resolve a name by lookup rather than by
joining it to a path.
EOF
```

---

## Self-Review

**Spec coverage.** Endpoint shape → Task 1–3. Whitelist resolution → Task 1 (PDFs), Task 2 (sheets), Task 3 (tables). Grid payload including truncation → Task 2. SQL context payload → Task 3. Drawer placement and dispatch → Tasks 5–6. Citation chips left alone → Task 6 commit message and the spec. `#page=N` anchoring including the null-page case → Task 4. Testing section → Tasks 1–6. Out-of-scope items are named in the spec and touched by no task.

**Placeholders.** None: every code step carries its actual content, every test step its actual assertions, every run step its actual command and expected output.

**Type consistency.** `SourceCatalog` fields (`policy`, `scanned`, `sheets`, `tables`) are used identically in Tasks 1–3. `read_sheet`'s returned keys match `SheetGrid` in Task 4 and its consumers in Task 5. `documentUrl`, `fetchSheet`, `fetchTable` are defined once in Task 4 and imported with those names in Task 5. `citedRowIndex` is defined in `SourceDrawer.tsx` and imported into `SheetSource.tsx`, which is why Task 5 Step 4 removes the private copy that Step 3 wrote.
