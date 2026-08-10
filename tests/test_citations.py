"""Citation resolution tests.

A model asked to cite will happily emit [E7] when six pieces of evidence exist, and
nothing in the prose reveals it. These tests pin the check that catches that, which is
what makes citation correctness a computed metric rather than a reviewer's judgement.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vericlaim.citations import (
    CitationReport,
    UnresolvableCitationError,
    extract_citations,
    render_citation_footer,
    require_resolvable_citations,
    resolve_citations,
)
from vericlaim.evidence import (
    Evidence,
    EvidenceSet,
    PolicyLocator,
    Provenance,
    ScannedLocator,
    SqlLocator,
)

PROV = Provenance(tool="t", retrieved_at=datetime(2026, 8, 11, tzinfo=UTC))


def four_items() -> EvidenceSet:
    return EvidenceSet(
        [
            Evidence(
                source_type="policy",
                source_id="HomeSecure_Plus_2026.pdf",
                content="Sudden escape of water is covered.",
                locator=PolicyLocator(
                    document="HomeSecure_Plus_2026.pdf", page=7, section="§4.2"
                ),
                provenance=PROV,
            ),
            Evidence(
                source_type="sql",
                source_id="ops",
                content="398 water-damage claims in March 2026",
                locator=SqlLocator(
                    tables=("ops.claims",),
                    executed_sql="SELECT count(*) FROM ops.claims",
                    row_count=1,
                ),
                provenance=PROV,
            ),
            Evidence(
                source_type="scanned_pdf",
                source_id="CLM-1088.pdf",
                content="Burst kitchen supply pipe.",
                locator=ScannedLocator(
                    document="CLM-1088.pdf", page=2, ocr_confidence=0.91
                ),
                provenance=PROV,
            ),
            Evidence(
                source_type="policy",
                source_id="HomeSecure_Plus_2026.pdf",
                content="Gradual leakage is excluded.",
                locator=PolicyLocator(
                    document="HomeSecure_Plus_2026.pdf", page=8, section="§4.3"
                ),
                provenance=PROV,
            ),
        ]
    )


class TestExtraction:
    def test_finds_markers_in_order_of_first_appearance(self):
        assert extract_citations("a [E2] b [E1] c") == ("E2", "E1")

    def test_repeated_markers_are_reported_once(self):
        assert extract_citations("[E1] and again [E1]") == ("E1",)

    def test_multi_digit_ids(self):
        assert extract_citations("see [E12]") == ("E12",)

    def test_leading_zeros_normalise(self):
        # [E01] and [E1] must not be treated as different evidence.
        assert extract_citations("[E01]") == ("E1",)

    def test_no_markers_yields_empty(self):
        assert extract_citations("an answer with no citations") == ()

    @pytest.mark.parametrize("text", ["[E]", "[EX]", "[E ]", "[e]"])
    def test_malformed_markers_are_not_extracted(self, text):
        assert extract_citations(f"claim {text}") == ()

    def test_bare_e_words_are_not_citations(self):
        assert extract_citations("Section E1 of the policy") == ()


class TestResolution:
    def test_all_citations_resolving_is_ok(self):
        report = resolve_citations("Water damage rose [E2], and it is covered [E1].", four_items())
        assert report.ok is True
        assert report.resolved == ("E2", "E1")
        assert report.unresolved == ()

    def test_out_of_range_citation_is_unresolved(self):
        # The headline failure: four items exist, the answer cites a ninth.
        report = resolve_citations("As shown [E9].", four_items())
        assert report.ok is False
        assert report.unresolved == ("E9",)

    def test_malformed_marker_fails_the_report(self):
        report = resolve_citations("As shown [E].", four_items())
        assert report.ok is False
        assert report.malformed == ("[E]",)

    def test_uncited_evidence_is_reported(self):
        # Retrieving from a source and then ignoring it is a real failure mode, so
        # cross-source completeness scoring needs this.
        report = resolve_citations("Only this one [E1].", four_items())
        assert set(report.uncited) == {"E2", "E3", "E4"}

    def test_answer_with_no_citations_is_all_uncited(self):
        report = resolve_citations("A confident but unsourced claim.", four_items())
        assert report.resolved == ()
        assert len(report.uncited) == 4

    def test_resolution_against_an_empty_evidence_set(self):
        report = resolve_citations("Citing nothing that exists [E1].", EvidenceSet())
        assert report.unresolved == ("E1",)
        assert report.uncited == ()

    def test_never_raises(self):
        # The verification node weighs failures against a bounded regeneration, so it
        # needs the detail rather than an exception.
        assert resolve_citations("[E9] [E] [E1]", four_items()) is not None

    def test_mixed_valid_and_invalid(self):
        report = resolve_citations("[E1] then [E7]", four_items())
        assert report.resolved == ("E1",)
        assert report.unresolved == ("E7",)


class TestMetrics:
    def test_precision_is_one_when_all_resolve(self):
        assert resolve_citations("[E1] [E2]", four_items()).precision == 1.0

    def test_precision_reflects_bad_citations(self):
        report = resolve_citations("[E1] [E2] [E9] [E8]", four_items())
        assert report.precision == 0.5

    def test_precision_is_one_when_nothing_is_cited(self):
        # Vacuously correct: an uncited answer makes no false citation claims. Its
        # failure shows up in coverage instead.
        assert resolve_citations("no citations", four_items()).precision == 1.0

    def test_coverage_reflects_unused_evidence(self):
        assert resolve_citations("[E1] [E2]", four_items()).coverage == 0.5

    def test_coverage_is_one_when_everything_is_used(self):
        report = resolve_citations("[E1] [E2] [E3] [E4]", four_items())
        assert report.coverage == 1.0

    def test_metrics_on_an_empty_set_do_not_divide_by_zero(self):
        report = resolve_citations("", EvidenceSet())
        assert report.precision == 1.0
        assert report.coverage == 1.0

    def test_cited_count(self):
        assert resolve_citations("[E1] [E3]", four_items()).cited_count == 2


class TestHardFailure:
    def test_unresolvable_citation_raises(self):
        with pytest.raises(UnresolvableCitationError):
            require_resolvable_citations("As shown [E9].", four_items())

    def test_error_names_the_offending_id(self):
        with pytest.raises(UnresolvableCitationError, match="E9"):
            require_resolvable_citations("[E9]", four_items())

    def test_error_lists_what_was_available(self):
        with pytest.raises(UnresolvableCitationError, match="E1, E2, E3, E4"):
            require_resolvable_citations("[E9]", four_items())

    def test_malformed_marker_also_raises(self):
        with pytest.raises(UnresolvableCitationError):
            require_resolvable_citations("[E]", four_items())

    def test_valid_answer_returns_the_report(self):
        report = require_resolvable_citations("[E1] [E2]", four_items())
        assert isinstance(report, CitationReport)
        assert report.ok is True

    def test_uncited_evidence_alone_does_not_raise(self):
        # Under-citing is a completeness concern for the sufficiency check, not a
        # correctness failure -- the citations that exist all point at real evidence.
        report = require_resolvable_citations("[E1]", four_items())
        assert len(report.uncited) == 3

    def test_uncited_answer_does_not_raise(self):
        assert require_resolvable_citations("no citations at all", four_items()).ok


class TestFooter:
    def test_lists_only_cited_sources(self):
        footer = render_citation_footer("Covered [E1], per the scan [E3].", four_items())
        assert "[E1]" in footer
        assert "[E3]" in footer
        # Listing everything retrieved would imply the answer rests on material it
        # never used.
        assert "[E2]" not in footer
        assert "[E4]" not in footer

    def test_renders_the_locator_citation(self):
        footer = render_citation_footer("[E1]", four_items())
        assert "HomeSecure_Plus_2026.pdf › p.7 › §4.2" in footer

    def test_names_the_source_category(self):
        footer = render_citation_footer("[E3]", four_items())
        assert "Scanned document" in footer

    def test_empty_when_nothing_is_cited(self):
        assert render_citation_footer("no citations", four_items()) == ""

    def test_unresolvable_ids_are_not_rendered(self):
        footer = render_citation_footer("[E1] [E9]", four_items())
        assert "E9" not in footer
