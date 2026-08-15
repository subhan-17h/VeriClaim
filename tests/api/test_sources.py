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
