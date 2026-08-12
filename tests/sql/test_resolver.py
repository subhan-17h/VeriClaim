"""Grounding what a question names in what the database stores.

Someone asks about "water damage"; the column holds `water_damage`. Someone names
"Al-Falah Insurance Pvt Ltd"; the customer is stored as "Al-Falah Insurance". A filter
written from the question rather than from the data returns zero rows, silently, and zero
rows reads as a fact.

Two paths, deliberately different. Vocabulary values are matched fuzzily, because people
paraphrase them. Claim and policy references are matched exactly, because CLM-1088 and
CLM-1089 differ by one character and are different claims -- the resolver must never
guess which one was meant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from vericlaim.sql.resolver import (
    EntityResolution,
    Match,
    Resolution,
    fuzzy_rewrite_sql,
    normalize,
    resolve_entities,
    resolve_mention,
    stored_values,
    strip_noise,
)
from vericlaim.sql.values_catalog import CatalogValue, ReferenceMatch, reference_key


@dataclass(frozen=True)
class FakeCatalog:
    """A catalog over values held in the test, matching the real one's contract."""

    values: dict[str, dict[str, tuple[CatalogValue, ...]]] = field(default_factory=dict)
    references: tuple[ReferenceMatch, ...] = ()

    def vocabulary(self) -> dict[str, dict[str, tuple[CatalogValue, ...]]]:
        return self.values

    def lookup_reference(self, key: str) -> tuple[ReferenceMatch, ...]:
        return tuple(
            match for match in self.references if reference_key(match.value) == key
        )


def vocabulary(**columns: tuple[str, ...]) -> FakeCatalog:
    return FakeCatalog(
        values={
            "ops.claims": {
                name: tuple(CatalogValue(value) for value in values)
                for name, values in columns.items()
            }
        }
    )


PERILS = vocabulary(peril=("water_damage", "fire", "storm"))


# ------------------------------------------------------------------ normalization


def test_normalization_ignores_case_accents_and_punctuation() -> None:
    assert normalize("Al-Faláh  INSURANCE") == "al falah insurance"


def test_a_corporate_suffix_is_not_part_of_the_name() -> None:
    assert strip_noise("al falah insurance pvt ltd") == "al falah insurance"


def test_a_corporate_prefix_is_not_part_of_the_name_either() -> None:
    assert strip_noise("m s karachi traders") == "karachi traders"


def test_noise_inside_a_name_is_left_alone() -> None:
    """Only the ends are shaved; a noise word mid-name is part of the name."""
    assert strip_noise("horizons ltd group co") == "horizons ltd group"


def test_a_name_made_only_of_noise_survives_stripping() -> None:
    """Reducing a mention to nothing would make every mention of it identical."""
    assert strip_noise("ltd co") == "ltd co"


# ------------------------------------------------------------------ references


def test_a_claim_reference_resolves_to_the_stored_spelling() -> None:
    catalog = FakeCatalog(
        references=(ReferenceMatch("ops.claims", "claim_number", "CLM-1088"),)
    )

    result = resolve_mention("clm 1088", catalog)

    assert result.status == "resolved"
    assert result.kind == "reference"
    assert result.matches[0].values == ("CLM-1088",)


def test_a_bare_number_is_a_reference_rather_than_a_dead_end() -> None:
    """The implementation this adapts short-circuited every numeric mention to
    not_found, so a question naming a claim by its digits could never be grounded."""
    catalog = FakeCatalog(
        references=(ReferenceMatch("ops.claims", "claim_number", "1088"),)
    )

    assert resolve_mention("1088", catalog).status == "resolved"


def test_an_unknown_reference_is_never_fuzzily_matched_to_a_neighbour() -> None:
    """CLM-1089 scores over 0.9 against CLM-1088. Resolving to it would invent a claim."""
    catalog = FakeCatalog(
        values={"ops.claims": {"claim_number": (CatalogValue("CLM-1088"),)}},
        references=(ReferenceMatch("ops.claims", "claim_number", "CLM-1088"),),
    )

    result = resolve_mention("CLM-1089", catalog)

    assert result.status == "not_found"
    assert result.kind == "reference"


def test_one_reference_held_by_two_tables_is_ambiguous() -> None:
    catalog = FakeCatalog(
        references=(
            ReferenceMatch("ops.claims", "claim_number", "REF-4021"),
            ReferenceMatch("ops.policies", "policy_number", "ref 4021"),
        )
    )

    result = resolve_mention("REF-4021", catalog)

    assert result.status == "ambiguous"
    assert set(result.candidates) == {"REF-4021", "ref 4021"}


# ------------------------------------------------------------------ vocabulary


def test_a_paraphrased_value_resolves_to_the_stored_one() -> None:
    result = resolve_mention("water damage", PERILS)

    assert result.status == "resolved"
    assert len(result.matches) == 1
    assert result.matches[0].table == "ops.claims"
    assert result.matches[0].column == "peril"
    assert result.matches[0].values == ("water_damage",)


def test_a_typo_still_resolves() -> None:
    assert resolve_mention("watr damage", PERILS).status == "resolved"


def test_a_customer_named_with_its_suffix_resolves_without_it() -> None:
    catalog = vocabulary(customer_name=("Al-Falah Insurance",))

    result = resolve_mention("Al Falah Insurance Pvt Ltd", catalog)

    assert result.matches[0].values == ("Al-Falah Insurance",)


def test_a_mention_nothing_resembles_is_not_found() -> None:
    assert resolve_mention("Zephyr Holdings", PERILS).status == "not_found"


def test_a_mention_too_short_to_discriminate_is_not_found() -> None:
    assert resolve_mention("ab", PERILS).status == "not_found"


def test_a_mention_matching_two_different_entities_is_ambiguous() -> None:
    catalog = vocabulary(
        customer_name=("Ahmed Textiles Pvt Ltd", "Ahmed Traders Pvt Ltd")
    )

    result = resolve_mention("Ahmed", catalog)

    assert result.status == "ambiguous"
    assert set(result.candidates) == {"Ahmed Textiles Pvt Ltd", "Ahmed Traders Pvt Ltd"}


def test_a_value_matched_as_part_of_a_longer_one_keeps_its_match_kind() -> None:
    catalog = FakeCatalog(
        values={"ops.claims": {"city": (CatalogValue("Punjab", "contains"),)}}
    )

    assert resolve_mention("punjab", catalog).matches[0].match_kind == "contains"


# ------------------------------------------------------------------ entities


def test_entities_and_quoted_filters_are_both_resolved() -> None:
    understanding = {
        "entities": ["water damage"],
        "filters": ["status = 'closed'"],
    }
    catalog = vocabulary(peril=("water_damage",), status=("closed",))

    result = resolve_entities(understanding, catalog)

    assert [mention.mention for mention in result.mentions] == [
        "water damage",
        "closed",
    ]
    assert result.needs_clarification is False


def test_the_same_mention_is_only_resolved_once() -> None:
    understanding = {"entities": ["water damage", "water damage"]}

    result = resolve_entities(understanding, PERILS)

    assert len(result.mentions) == 1


def test_a_question_naming_nothing_needs_no_resolution() -> None:
    result = resolve_entities({"entities": [], "filters": []}, PERILS)

    assert result.mentions == ()
    assert result.needs_clarification is False


def test_an_ambiguous_mention_asks_which_one_was_meant() -> None:
    understanding = {"entities": ["Ahmed"]}
    catalog = vocabulary(
        customer_name=("Ahmed Textiles Pvt Ltd", "Ahmed Traders Pvt Ltd")
    )

    result = resolve_entities(understanding, catalog)

    assert result.needs_clarification is True
    assert "Ahmed Textiles Pvt Ltd" in result.clarification_question
    assert "Ahmed" in result.clarification_question


# ------------------------------------------------------------------ rewriting


def test_a_filter_written_from_the_question_is_rewritten_to_the_stored_value() -> None:
    rewritten = fuzzy_rewrite_sql(
        "SELECT count(*) FROM ops.claims WHERE peril = 'water damage'", PERILS
    )

    assert "'water_damage'" in rewritten


def test_a_filter_already_written_correctly_is_left_alone() -> None:
    assert (
        fuzzy_rewrite_sql(
            "SELECT count(*) FROM ops.claims WHERE peril = 'water_damage'", PERILS
        )
        is None
    )


def test_an_aliased_column_is_rewritten_too() -> None:
    rewritten = fuzzy_rewrite_sql(
        "SELECT count(*) FROM ops.claims AS c WHERE c.peril = 'water damage'", PERILS
    )

    assert "'water_damage'" in rewritten


def test_an_unqualified_table_is_matched_by_name() -> None:
    rewritten = fuzzy_rewrite_sql(
        "SELECT count(*) FROM claims WHERE peril = 'water damage'", PERILS
    )

    assert "'water_damage'" in rewritten


def test_a_reference_literal_is_never_rewritten() -> None:
    """No vocabulary holds a claim number, so nothing here is fuzzily replaceable."""
    catalog = FakeCatalog(
        values={"ops.claims": {"peril": (CatalogValue("water_damage"),)}},
        references=(ReferenceMatch("ops.claims", "claim_number", "CLM-1088"),),
    )

    assert (
        fuzzy_rewrite_sql(
            "SELECT * FROM ops.claims WHERE claim_number = 'CLM-1089'", catalog
        )
        is None
    )


def test_every_member_of_an_in_list_is_rewritten() -> None:
    rewritten = fuzzy_rewrite_sql(
        "SELECT count(*) FROM ops.claims WHERE peril IN ('water damage', 'FIRE')",
        PERILS,
    )

    assert "'water_damage'" in rewritten
    assert "'fire'" in rewritten


def test_a_partial_value_is_rewritten_as_a_containment_filter() -> None:
    catalog = FakeCatalog(
        values={"ops.claims": {"city": (CatalogValue("Punjab", "contains"),)}}
    )

    rewritten = fuzzy_rewrite_sql(
        "SELECT count(*) FROM ops.claims WHERE city = 'punjab'", catalog
    )

    assert "ILIKE" in rewritten.upper()
    assert "%Punjab%" in rewritten


def test_a_column_no_catalog_covers_is_left_alone() -> None:
    assert (
        fuzzy_rewrite_sql(
            "SELECT count(*) FROM ops.claims WHERE status = 'clsoed'", PERILS
        )
        is None
    )


def test_sql_that_does_not_parse_is_not_rewritten() -> None:
    assert fuzzy_rewrite_sql("this is not sql (((", PERILS) is None


@pytest.mark.parametrize("mention", ["CLM-1088", "clm 1088", "clm1088"])
def test_a_reference_is_recognized_however_it_is_punctuated(mention: str) -> None:
    catalog = FakeCatalog(
        references=(ReferenceMatch("ops.claims", "claim_number", "CLM-1088"),)
    )

    assert resolve_mention(mention, catalog).status == "resolved"


# ------------------------------------------------------------------ prompt payload


def test_only_grounded_mentions_become_stored_values() -> None:
    """An ambiguous mention offered to the planner reads as a value the database holds,
    and the question of which one was meant disappears."""
    resolved = EntityResolution(
        mentions=(
            Resolution(
                mention="water damage",
                status="resolved",
                matches=(Match("ops.claims", "peril", ("water_damage",), "equals", 1.0),),
            ),
            Resolution(
                mention="Ahmed",
                status="ambiguous",
                candidates=("Ahmed Textiles", "Ahmed Traders"),
            ),
            Resolution(mention="Zephyr", status="not_found"),
        )
    )

    assert stored_values(resolved) == [
        {
            "mention": "water damage",
            "table": "ops.claims",
            "column": "peril",
            "values": ["water_damage"],
            "match_kind": "equals",
        }
    ]


def test_a_question_with_nothing_to_ground_offers_nothing() -> None:
    assert stored_values(None) == []
