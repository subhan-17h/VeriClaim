"""Contract tests for the generated policy wording corpus."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from vericlaim.corpus.catalog import COVERAGE_PRODUCTS
from vericlaim.corpus.policies import generate_policy_corpus


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(path: Path) -> str:
    return " ".join(
        " ".join((page.extract_text() or "").split()) for page in PdfReader(path).pages
    )


def _clause(text: str, clause: str, next_clause: str) -> str:
    return text.split(clause, maxsplit=1)[1].split(next_clause, maxsplit=1)[0].lower()


def _amount(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


@pytest.fixture
def generated(tmp_path: Path) -> dict[str, Path]:
    return {path.name: path for path in generate_policy_corpus(tmp_path / "policies")}


def test_generation_is_byte_identical_across_directories(tmp_path: Path) -> None:
    first = generate_policy_corpus(tmp_path / "first")
    second = generate_policy_corpus(tmp_path / "second")

    first_hashes = {path.name: _sha256(path) for path in first}
    second_hashes = {path.name: _sha256(path) for path in second}

    assert first_hashes == second_hashes


def test_document_count_is_within_policy_corpus_range(generated: dict[str, Path]) -> None:
    assert 10 <= len(generated) <= 14


def test_every_catalogue_policy_document_exists(generated: dict[str, Path]) -> None:
    for product in COVERAGE_PRODUCTS:
        assert product.policy_document in generated


def test_every_pdf_is_digital_and_multi_page(generated: dict[str, Path]) -> None:
    for path in generated.values():
        reader = PdfReader(path)
        assert len(reader.pages) > 1, path.name
        assert all((page.extract_text() or "").strip() for page in reader.pages), path.name


def test_homesecure_wordings_cover_sudden_and_exclude_gradual_water(
    generated: dict[str, Path],
) -> None:
    homesecure_products = [
        product for product in COVERAGE_PRODUCTS if product.product_code in {"HSB", "HSP"}
    ]

    for product in homesecure_products:
        text = _text(generated[product.policy_document])
        assert "SECTION 4 — WATER DAMAGE" in text
        covered_clause = _clause(text, "4.2 ", "4.3 ")
        excluded_clause = _clause(text, "4.3 ", "4.4 ")

        assert all(
            phrase in covered_clause
            for phrase in ("sudden and accidental", "fixed plumbing system", "covered")
        )
        assert all(
            phrase in excluded_clause
            for phrase in ("gradual", "leakage", "seepage", "weeks or months", "excluded")
        )


def test_product_figures_come_from_catalogue(generated: dict[str, Path]) -> None:
    for product in COVERAGE_PRODUCTS:
        text = _text(generated[product.policy_document])
        deductibles = [
            _amount(value)
            for value in re.findall(
                r"base deductible(?: of|:) PKR ([0-9][0-9,]*)",
                text,
                flags=re.IGNORECASE,
            )
        ]
        coverage_limits = [
            _amount(value)
            for value in re.findall(
                r"coverage limit: PKR ([0-9][0-9,]*)",
                text,
                flags=re.IGNORECASE,
            )
        ]
        sub_limits = [
            _amount(value)
            for value in re.findall(
                r"sub-limit of PKR ([0-9][0-9,]*)",
                text,
                flags=re.IGNORECASE,
            )
        ]

        assert deductibles
        assert set(deductibles) == {product.base_deductible_pkr}
        assert coverage_limits == [product.coverage_limit_pkr]
        assert sub_limits
        assert all(Decimal("0") < amount <= product.coverage_limit_pkr for amount in sub_limits)


def test_companion_forms_are_not_labelled_as_policy_wordings(
    generated: dict[str, Path],
) -> None:
    product_documents = {product.policy_document for product in COVERAGE_PRODUCTS}

    for filename, path in generated.items():
        text = _text(path)
        if filename in product_documents:
            assert "Policy Wording" in text
        else:
            assert "Policy Wording" not in text
