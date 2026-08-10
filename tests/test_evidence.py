"""Evidence spine tests.

The construction-time locator check is the load-bearing one: it is what makes it
impossible for a source tool to emit evidence that nothing can cite. Everything
downstream -- citation resolution, the UI's four card types, the evaluation scorers --
assumes that guarantee holds.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vericlaim.evidence import (
    Evidence,
    LocatorMismatchError,
    PolicyLocator,
    Provenance,
    ScannedLocator,
    SpreadsheetLocator,
    SqlLocator,
    content_hash,
)

PROV = Provenance(
    tool="test",
    retrieved_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    query="does the policy cover sudden water escape?",
)


def policy_evidence(**kw) -> Evidence:
    defaults = dict(
        source_type="policy",
        source_id="HomeSecure_Plus_2026.pdf",
        content="Sudden and accidental escape of water from fixed plumbing is covered.",
        locator=PolicyLocator(
            document="HomeSecure_Plus_2026.pdf", page=7, section="§4.2"
        ),
        provenance=PROV,
    )
    return Evidence(**{**defaults, **kw})


class TestLocatorCitations:
    def test_policy_renders_document_page_and_clause(self):
        locator = PolicyLocator(
            document="HomeSecure_Plus_2026.pdf", page=7, section="§4.2"
        )
        assert locator.cite() == "HomeSecure_Plus_2026.pdf › p.7 › §4.2"

    def test_policy_omits_absent_parts(self):
        assert PolicyLocator(document="Policy.pdf").cite() == "Policy.pdf"

    def test_sql_shows_tables_and_row_count(self):
        locator = SqlLocator(
            tables=("ops.claims",),
            executed_sql="SELECT count(*) FROM ops.claims",
            row_count=398,
        )
        assert locator.cite() == "ops.claims (SQL · 398 rows)"

    def test_sql_singular_row(self):
        locator = SqlLocator(tables=("ops.claims",), executed_sql="SELECT 1", row_count=1)
        assert "1 row)" in locator.cite()

    def test_sql_carries_the_executed_query(self):
        # The query is part of the citation, not a debug aid: a numeric claim is only
        # auditable if the reader can see how it was derived.
        sql = "SELECT count(*) FROM ops.claims WHERE cause = 'water'"
        assert SqlLocator(tables=("ops.claims",), executed_sql=sql).executed_sql == sql

    def test_spreadsheet_renders_down_to_the_cell_range(self):
        locator = SpreadsheetLocator(
            workbook="RIC_Q1.xlsx", sheet="Northern", row=14, a1_range="B14:F14"
        )
        assert locator.cite() == "RIC_Q1.xlsx › Northern › row 14 (B14:F14)"

    def test_spreadsheet_without_a_range(self):
        locator = SpreadsheetLocator(workbook="RIC_Q1.xlsx", sheet="Northern")
        assert locator.cite() == "RIC_Q1.xlsx › Northern"

    def test_scanned_includes_ocr_confidence(self):
        locator = ScannedLocator(
            document="CLM-1088_INSPECTION.pdf", page=2, ocr_confidence=0.91
        )
        assert locator.cite() == "CLM-1088_INSPECTION.pdf › p.2 (OCR 0.91)"

    def test_scanned_marks_vision_assisted_pages(self):
        locator = ScannedLocator(
            document="CLM-1088.pdf", page=2, ocr_confidence=0.55, escalated=True
        )
        assert "vision-assisted" in locator.cite()

    def test_scanned_without_confidence_omits_the_suffix(self):
        assert ScannedLocator(document="d.pdf", page=1).cite() == "d.pdf › p.1"


class TestLocatorTypeEnforcement:
    @pytest.mark.parametrize(
        "source_type,locator",
        [
            ("policy", SqlLocator(tables=("t",), executed_sql="SELECT 1")),
            ("sql", PolicyLocator(document="p.pdf")),
            ("spreadsheet", ScannedLocator(document="d.pdf", page=1)),
            ("scanned_pdf", SpreadsheetLocator(workbook="w.xlsx", sheet="s")),
        ],
    )
    def test_mismatched_locator_is_rejected_at_construction(self, source_type, locator):
        # A tool must not be able to emit evidence nothing can cite.
        with pytest.raises(LocatorMismatchError):
            Evidence(
                source_type=source_type,
                source_id="x",
                content="something",
                locator=locator,
                provenance=PROV,
            )

    @pytest.mark.parametrize(
        "source_type,locator",
        [
            ("policy", PolicyLocator(document="p.pdf")),
            ("sql", SqlLocator(tables=("t",), executed_sql="SELECT 1")),
            ("spreadsheet", SpreadsheetLocator(workbook="w.xlsx", sheet="s")),
            ("scanned_pdf", ScannedLocator(document="d.pdf", page=1)),
        ],
    )
    def test_matched_locator_is_accepted(self, source_type, locator):
        evidence = Evidence(
            source_type=source_type,
            source_id="x",
            content="something",
            locator=locator,
            provenance=PROV,
        )
        assert evidence.source_type == source_type

    def test_unknown_source_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown source_type"):
            Evidence(
                source_type="carrier_pigeon",  # type: ignore[arg-type]
                source_id="x",
                content="c",
                locator=PolicyLocator(document="p.pdf"),
                provenance=PROV,
            )

    def test_error_names_both_expected_and_actual(self):
        with pytest.raises(LocatorMismatchError, match="PolicyLocator.*SqlLocator"):
            Evidence(
                source_type="policy",
                source_id="x",
                content="c",
                locator=SqlLocator(tables=("t",), executed_sql="SELECT 1"),
                provenance=PROV,
            )


class TestEvidenceValidation:
    def test_empty_content_is_rejected(self):
        # Empty evidence would be citable but say nothing -- a silent hole in an
        # answer that looks properly sourced.
        with pytest.raises(ValueError, match="must not be empty"):
            policy_evidence(content="   ")

    @pytest.mark.parametrize("bad", [-0.1, 1.5])
    def test_confidence_must_be_a_probability(self, bad):
        with pytest.raises(ValueError, match="confidence"):
            policy_evidence(confidence=bad)

    @pytest.mark.parametrize("good", [0.0, 0.5, 1.0])
    def test_confidence_bounds_are_inclusive(self, good):
        assert policy_evidence(confidence=good).confidence == good


class TestLowConfidence:
    def test_below_the_floor_is_flagged(self):
        assert policy_evidence(confidence=0.4).is_low_confidence(0.6) is True

    def test_at_the_floor_is_not_flagged(self):
        # Boundary is exclusive: the floor is the lowest acceptable value.
        assert policy_evidence(confidence=0.6).is_low_confidence(0.6) is False

    def test_above_the_floor_is_not_flagged(self):
        assert policy_evidence(confidence=0.95).is_low_confidence(0.6) is False


class TestIdentityAndDedup:
    def test_content_hash_ignores_whitespace_differences(self):
        assert content_hash("a  b\nc") == content_hash("a b c")

    def test_identical_evidence_shares_a_dedup_key(self):
        assert policy_evidence().dedup_key() == policy_evidence().dedup_key()

    def test_same_text_on_different_pages_stays_distinct(self):
        # Separately citable, and a reader may need either, so this must not collapse.
        a = policy_evidence(locator=PolicyLocator(document="p.pdf", page=1))
        b = policy_evidence(locator=PolicyLocator(document="p.pdf", page=2))
        assert a.dedup_key() != b.dedup_key()

    def test_different_text_at_the_same_locator_stays_distinct(self):
        a = policy_evidence(content="first passage")
        b = policy_evidence(content="second passage")
        assert a.dedup_key() != b.dedup_key()

    def test_sql_dedup_ignores_query_whitespace(self):
        def ev(sql: str) -> Evidence:
            return Evidence(
                source_type="sql",
                source_id="ops",
                content="398",
                locator=SqlLocator(tables=("ops.claims",), executed_sql=sql),
                provenance=PROV,
            )

        assert ev("SELECT  1").dedup_key() == ev("SELECT 1").dedup_key()


class TestIdAssignment:
    def test_evidence_starts_without_an_id(self):
        # Ids belong to the EvidenceSet, so they are unique across a whole answer
        # rather than per tool.
        assert policy_evidence().id == ""

    def test_with_id_returns_a_copy(self):
        original = policy_evidence()
        tagged = original.with_id("E3")
        assert tagged.id == "E3"
        assert original.id == ""

    def test_with_id_preserves_everything_else(self):
        original = policy_evidence(confidence=0.8)
        tagged = original.with_id("E1")
        assert tagged.content == original.content
        assert tagged.locator == original.locator
        assert tagged.confidence == 0.8


class TestSerialization:
    def test_dict_carries_citation_and_locator(self):
        payload = policy_evidence().with_id("E1").to_dict()
        assert payload["id"] == "E1"
        assert payload["citation"] == "HomeSecure_Plus_2026.pdf › p.7 › §4.2"
        assert payload["locator"]["page"] == 7
        assert payload["source_label"] == "Policy document"

    def test_sql_dict_exposes_the_executed_query(self):
        evidence = Evidence(
            source_type="sql",
            source_id="ops",
            content="398 water-damage claims",
            locator=SqlLocator(
                tables=("ops.claims",),
                executed_sql="SELECT count(*) FROM ops.claims",
                row_count=1,
            ),
            provenance=PROV,
        )
        assert (
            evidence.to_dict()["locator"]["executed_sql"]
            == "SELECT count(*) FROM ops.claims"
        )

    def test_scanned_dict_exposes_confidence(self):
        evidence = Evidence(
            source_type="scanned_pdf",
            source_id="CLM-1088.pdf",
            content="burst kitchen supply pipe",
            locator=ScannedLocator(
                document="CLM-1088.pdf", page=2, ocr_confidence=0.91, ocr_engine="rapidocr"
            ),
            provenance=PROV,
            confidence=0.91,
        )
        payload = evidence.to_dict()
        assert payload["locator"]["ocr_confidence"] == 0.91
        assert payload["locator"]["ocr_engine"] == "rapidocr"

    def test_provenance_round_trips_as_iso(self):
        payload = policy_evidence().to_dict()["provenance"]
        assert payload["tool"] == "test"
        assert payload["retrieved_at"].startswith("2026-08-11T12:00")
        assert payload["query"]
