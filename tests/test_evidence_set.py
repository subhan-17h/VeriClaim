"""EvidenceSet: stable ids, dedup, grouping, and the synthesis boundary.

Id stability is the property that matters most. `[E3]` has to mean the same passage
when synthesis writes it and when verification checks it -- a citation that shifts
meaning between those two points is a correctness bug, not a display quirk.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vericlaim.evidence import (
    Evidence,
    EvidenceSet,
    PolicyLocator,
    Provenance,
    ScannedLocator,
    SpreadsheetLocator,
    SqlLocator,
)

PROV = Provenance(tool="t", retrieved_at=datetime(2026, 8, 11, tzinfo=UTC))


def policy(content="Sudden escape of water is covered.", page=7) -> Evidence:
    return Evidence(
        source_type="policy",
        source_id="HomeSecure_Plus_2026.pdf",
        content=content,
        locator=PolicyLocator(
            document="HomeSecure_Plus_2026.pdf", page=page, section="§4.2"
        ),
        provenance=PROV,
    )


def sql(content="398 water-damage claims in March 2026") -> Evidence:
    return Evidence(
        source_type="sql",
        source_id="ops",
        content=content,
        locator=SqlLocator(
            tables=("ops.claims",),
            executed_sql="SELECT count(*) FROM ops.claims",
            row_count=1,
        ),
        provenance=PROV,
    )


def sheet(content="Northern compliance fell to 72%") -> Evidence:
    return Evidence(
        source_type="spreadsheet",
        source_id="RIC_Q1.xlsx",
        content=content,
        locator=SpreadsheetLocator(
            workbook="RIC_Q1.xlsx", sheet="Northern", row=14, a1_range="B14:F14"
        ),
        provenance=PROV,
    )


def scanned(content="Burst kitchen supply pipe.", confidence=0.91) -> Evidence:
    return Evidence(
        source_type="scanned_pdf",
        source_id="CLM-1088.pdf",
        content=content,
        locator=ScannedLocator(
            document="CLM-1088.pdf", page=2, ocr_confidence=confidence
        ),
        provenance=PROV,
        confidence=confidence,
    )


def mixed_set() -> EvidenceSet:
    return EvidenceSet([policy(), sql(), sheet(), scanned()])


class TestIdAssignment:
    def test_ids_are_assigned_in_insertion_order(self):
        assert mixed_set().ids == ("E1", "E2", "E3", "E4")

    def test_add_returns_the_tagged_copy(self):
        evidence_set = EvidenceSet()
        tagged = evidence_set.add(policy())
        assert tagged is not None
        assert tagged.id == "E1"

    def test_lookup_by_id(self):
        evidence_set = mixed_set()
        assert evidence_set.get("E2").source_type == "sql"
        assert evidence_set.get("E99") is None

    def test_membership_is_by_id(self):
        evidence_set = mixed_set()
        assert "E1" in evidence_set
        assert "E9" not in evidence_set

    def test_grouping_does_not_disturb_ids(self):
        # The UI groups by source for its four card types; that view must not
        # renumber anything the answer already cited.
        evidence_set = mixed_set()
        before = evidence_set.ids
        grouped = evidence_set.by_source()
        assert grouped["scanned_pdf"][0].id == "E4"
        assert evidence_set.ids == before

    def test_ids_survive_repeated_reads(self):
        evidence_set = mixed_set()
        first = [item.id for item in evidence_set]
        second = [item.id for item in evidence_set]
        assert first == second == ["E1", "E2", "E3", "E4"]

    def test_later_additions_do_not_renumber_earlier_ones(self):
        evidence_set = EvidenceSet([policy()])
        assert evidence_set.get("E1").source_type == "policy"
        evidence_set.add(sql())
        # E1 still means what it meant before the set grew.
        assert evidence_set.get("E1").source_type == "policy"
        assert evidence_set.get("E2").source_type == "sql"


class TestDedup:
    def test_identical_evidence_is_added_once(self):
        evidence_set = EvidenceSet([policy(), policy()])
        assert len(evidence_set) == 1

    def test_duplicate_add_returns_none(self):
        evidence_set = EvidenceSet([policy()])
        assert evidence_set.add(policy()) is None

    def test_same_text_on_a_different_page_is_kept(self):
        # Separately citable, so collapsing them would lose a real distinction.
        evidence_set = EvidenceSet([policy(page=7), policy(page=9)])
        assert len(evidence_set) == 2

    def test_different_text_at_the_same_locator_is_kept(self):
        evidence_set = EvidenceSet([policy(content="first"), policy(content="second")])
        assert len(evidence_set) == 2

    def test_dedup_does_not_leave_gaps_in_ids(self):
        evidence_set = EvidenceSet([policy(), policy(), sql()])
        assert evidence_set.ids == ("E1", "E2")

    def test_extend_returns_only_what_was_accepted(self):
        evidence_set = EvidenceSet([policy()])
        accepted = evidence_set.extend([policy(), sql()])
        assert [item.id for item in accepted] == ["E2"]


class TestGrouping:
    def test_groups_by_source_type(self):
        grouped = mixed_set().by_source()
        assert set(grouped) == {"policy", "sql", "spreadsheet", "scanned_pdf"}
        assert all(len(items) == 1 for items in grouped.values())

    def test_source_types_reports_contributors_in_first_appearance_order(self):
        evidence_set = EvidenceSet([sql(), policy(), sql(content="another row")])
        assert evidence_set.source_types() == ("sql", "policy")

    def test_empty_set_has_no_sources(self):
        assert EvidenceSet().source_types() == ()
        assert not EvidenceSet()


class TestLowConfidence:
    def test_reports_evidence_below_the_floor(self):
        evidence_set = EvidenceSet([policy(), scanned(confidence=0.42)])
        flagged = evidence_set.low_confidence(0.6)
        assert [item.id for item in flagged] == ["E2"]

    def test_nothing_flagged_when_all_are_confident(self):
        assert mixed_set().low_confidence(0.5) == ()


class TestRenderForSynthesis:
    def test_every_block_is_tagged_with_its_id(self):
        rendered = mixed_set().render_for_synthesis()
        for evidence_id in ("E1", "E2", "E3", "E4"):
            assert f"[{evidence_id}]" in rendered

    def test_blocks_carry_their_citation(self):
        rendered = mixed_set().render_for_synthesis()
        assert "HomeSecure_Plus_2026.pdf › p.7 › §4.2" in rendered
        assert "RIC_Q1.xlsx › Northern › row 14 (B14:F14)" in rendered

    def test_blocks_carry_their_content(self):
        rendered = mixed_set().render_for_synthesis()
        assert "Sudden escape of water is covered." in rendered
        assert "Burst kitchen supply pipe." in rendered

    def test_source_category_is_named(self):
        rendered = mixed_set().render_for_synthesis()
        assert "Policy document" in rendered
        assert "Claims database" in rendered
        assert "Scanned document" in rendered

    def test_low_confidence_blocks_are_marked_inline(self):
        # Marked in the rendering itself so the prompt does not have to carry the
        # judgement separately, and so it cannot be forgotten.
        evidence_set = EvidenceSet([scanned(confidence=0.3)])
        rendered = evidence_set.render_for_synthesis(low_confidence_floor=0.6)
        assert "LOW CONFIDENCE" in rendered

    def test_confident_blocks_are_not_marked(self):
        rendered = mixed_set().render_for_synthesis(low_confidence_floor=0.6)
        assert "LOW CONFIDENCE" not in rendered

    def test_no_marking_without_a_floor(self):
        evidence_set = EvidenceSet([scanned(confidence=0.1)])
        assert "LOW CONFIDENCE" not in evidence_set.render_for_synthesis()

    def test_empty_set_renders_an_explicit_statement(self):
        # Must not render as an empty string: the synthesizer has to be able to tell
        # "nothing was found" apart from "the evidence block is missing".
        assert EvidenceSet().render_for_synthesis() == "No evidence was retrieved."

    def test_executed_sql_is_not_dumped_into_the_prompt(self):
        # The query belongs in the citation and the UI, not in the synthesis context
        # where it would waste tokens and invite the model to reason about SQL.
        rendered = EvidenceSet([sql()]).render_for_synthesis()
        assert "SELECT count(*)" not in rendered
        assert "ops.claims (SQL · 1 row)" in rendered


class TestSerialization:
    def test_serializes_every_item_with_its_id(self):
        payload = mixed_set().serialize()
        assert [row["id"] for row in payload] == ["E1", "E2", "E3", "E4"]

    def test_each_row_carries_a_citation_and_locator(self):
        for row in mixed_set().serialize():
            assert row["citation"]
            assert row["locator"]
            assert row["source_type"]

    def test_sql_row_exposes_the_query_for_the_ui(self):
        row = next(r for r in mixed_set().serialize() if r["source_type"] == "sql")
        assert row["locator"]["executed_sql"].startswith("SELECT")

    def test_empty_set_serializes_to_an_empty_list(self):
        assert EvidenceSet().serialize() == []


class TestConstruction:
    def test_built_from_a_list(self):
        assert len(EvidenceSet([policy(), sql()])) == 2

    def test_built_empty(self):
        assert len(EvidenceSet()) == 0

    @pytest.mark.parametrize("factory", [policy, sql, sheet, scanned])
    def test_accepts_every_source_type(self, factory):
        evidence_set = EvidenceSet([factory()])
        assert len(evidence_set) == 1
