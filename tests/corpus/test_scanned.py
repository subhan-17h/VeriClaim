"""Deterministic, claim-keyed, image-only scanned corpus generation."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfReader

from vericlaim.config import get_settings
from vericlaim.corpus.catalog import REGIONS
from vericlaim.corpus.scanned import (
    QUALITY_SCHEDULE,
    generate_scanned_corpus,
    scanned_documents,
)
from vericlaim.corpus.transactions import WATER_DAMAGE_CAUSES, generate_transactions

SEED = 42


@pytest.fixture(scope="module")
def generated_corpora(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Generate once per destination so all offline proofs inspect the same bytes."""
    root = tmp_path_factory.mktemp("scanned-corpus")
    first = root / "first"
    second = root / "second"
    generate_scanned_corpus(first, seed=SEED)
    generate_scanned_corpus(second, seed=SEED)
    return first, second


def test_generation_is_byte_identical_per_filename(
    generated_corpora: tuple[Path, Path],
) -> None:
    first, second = generated_corpora
    first_names = {path.name for path in first.glob("*.pdf")}
    second_names = {path.name for path in second.glob("*.pdf")}

    assert first_names == second_names
    assert first_names
    for filename in first_names:
        assert (first / filename).read_bytes() == (second / filename).read_bytes(), filename


def test_document_count_is_within_the_card_range() -> None:
    documents = scanned_documents(SEED)

    assert 60 <= len(documents) <= 80


def test_every_filename_joins_to_a_generated_claim() -> None:
    settings = get_settings()
    pattern = re.compile(settings.claim_id_pattern)
    claim_numbers = {
        claim.claim_number for claim in generate_transactions(SEED).claims
    }

    for document in scanned_documents(SEED):
        match = pattern.search(document.filename)
        assert match is not None, document.filename
        assert match.group(0) == document.claim.claim_number
        assert match.group(0) in claim_numbers


def test_every_document_body_uses_its_claim_facts() -> None:
    regions = {region.region_id: region for region in REGIONS}

    for document in scanned_documents(SEED):
        claim = document.claim
        region = regions[claim.region_id]
        body = "\n".join(document.blocks).lower()
        assert claim.claim_number.lower() in body
        assert claim.peril.replace("_", " ") in body
        assert f"{claim.date_of_loss:%d %B %Y}".lower() in body
        assert f"{claim.report_date:%d %B %Y}".lower() in body
        assert claim.cause_description.lower() in body
        assert region.region_name.lower() in body
        assert region.city.lower() in body
        assert region.province.lower() in body


def test_every_pdf_has_zero_extractable_text(
    generated_corpora: tuple[Path, Path],
) -> None:
    first, _second = generated_corpora

    for path in first.glob("*.pdf"):
        pages = PdfReader(path).pages
        assert pages
        assert all(not (page.extract_text() or "").strip() for page in pages), path.name


def test_quality_mix_matches_the_reviewed_schedule() -> None:
    documents = scanned_documents(SEED)
    qualities = Counter(document.quality for document in documents)

    assert qualities == Counter(QUALITY_SCHEDULE)
    assert qualities == {"clean": 55, "degraded": 14, "illegible": 3}
    assert qualities["degraded"] / len(documents) == pytest.approx(0.20, abs=0.03)
    assert qualities["illegible"] in {2, 3}
    assert sum(qualities.values()) == len(documents)


def test_all_three_document_types_have_distinct_registers() -> None:
    documents = scanned_documents(SEED)
    by_type = Counter(document.document_type for document in documents)

    assert set(by_type) == {"inspection_report", "contractor_invoice", "claim_form"}
    assert all(count > 0 for count in by_type.values())
    assert any("SITE INSPECTION FINDINGS" in document.blocks for document in documents)
    assert any("SCOPE OF WORKS" in document.blocks for document in documents)
    assert any("SECTION B - CLAIMANT STATEMENT" in document.blocks for document in documents)


def test_gradual_seepage_is_counter_evidence_for_water_damage_claims() -> None:
    documents = scanned_documents(SEED)
    water_documents = [
        document for document in documents if document.claim.peril == "water_damage"
    ]
    contradictions = [document for document in water_documents if document.counter_evidence]
    claims_by_number = {
        claim.claim_number: claim for claim in generate_transactions(SEED).claims
    }
    sudden_causes = set(WATER_DAMAGE_CAUSES["sudden"])

    assert contradictions
    assert len(contradictions) < len(water_documents)
    for document in contradictions:
        claim = claims_by_number[document.claim.claim_number]
        body = " ".join(document.blocks).lower()
        assert claim.peril == "water_damage"
        assert claim.cause_description in sudden_causes
        assert "gradual seepage" in body
        assert "long-term leakage" in body
        assert "not consistent with a sudden and accidental escape" in body


@pytest.mark.ocr
def test_a_clean_document_ocrs_its_claim_and_known_phrase(
    generated_corpora: tuple[Path, Path],
) -> None:
    from vericlaim.scanned.docling_ocr import ScannedPdfParser

    first, _second = generated_corpora
    document = next(
        item
        for item in scanned_documents(SEED)
        if item.quality == "clean" and item.document_type == "inspection_report"
    )
    result = ScannedPdfParser().parse_with_confidence(first / document.filename)
    text = " ".join(result.document.text.lower().split())

    assert document.claim.claim_number.lower() in text
    assert document.known_phrase in text


@pytest.mark.ocr
def test_an_illegible_document_does_not_yield_confident_text(
    generated_corpora: tuple[Path, Path],
) -> None:
    from vericlaim.scanned.docling_ocr import ScannedPdfParser

    first, _second = generated_corpora
    document = next(item for item in scanned_documents(SEED) if item.quality == "illegible")
    result = ScannedPdfParser().parse_with_confidence(first / document.filename)
    floor = get_settings().ocr_confidence_floor

    assert result.unreadable_pages() or result.low_confidence_pages(floor)
