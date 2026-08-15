"""A source is looked up by name in a set built from what exists.

A name that arrives from a client is never joined to a path. That is what makes
traversal a 404 rather than a sanitising problem: `../../etc/passwd` is not a name
the catalog holds, and no filesystem call is ever built from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from vericlaim.api.app import create_app
from vericlaim.api.sources import SourceCatalog, read_sheet
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


def _sheet_client(tmp_path: Path) -> TestClient:
    """A catalog whose one workbook is declared by a reviewed context."""
    workbooks = tmp_path / "spreadsheets"
    workbooks.mkdir()
    _workbook(workbooks / "Compliance.xlsx")
    settings = Settings(spreadsheet_dir=workbooks)
    catalog = SourceCatalog.from_settings(settings)
    declared = {("Compliance.xlsx", "Compliance"): workbooks / "Compliance.xlsx"}
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
