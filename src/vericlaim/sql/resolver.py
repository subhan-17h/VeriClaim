"""Ground what a question names in what the database stores. No model involved.

A question says "water damage"; the column holds ``water_damage``. It says "Al-Falah
Insurance Pvt Ltd"; the customer is stored as "Al-Falah Insurance". SQL written from the
question rather than from the data returns zero rows -- no error, no warning, and zero
rows reads as a fact. Grounding the mention first is what turns that silent wrong answer
into a correct one.

This is deliberately deterministic: string normalization, character-trigram and token
similarity, and a union-find clustering step that decides whether two near-matches are
the same entity spelled twice or two different entities. The implementation it adapts
also had an embedding fallback that constructed an OpenAI client inline; that is dropped
here, because entity grounding that varies between runs cannot be evaluated, and because
every model call in this system goes through the gateway.

**Two paths, opposite policies.**

*Vocabulary* values -- perils, statuses, cities, customer names -- are fuzzy-matched:
people paraphrase them, and a near miss is a paraphrase.

*References* -- ``CLM-1088``, ``POL-2026-0001`` -- are matched exactly, in the database.
``CLM-1089`` scores above 0.9 against ``CLM-1088`` under any string metric worth using,
and resolving one to the other would not be a near miss but an invented claim. A
reference that is not found stays not found; it never falls through to fuzzy matching.
The implementation this adapts went further the other way and short-circuited *every*
numeric mention to `not_found`, so a claim named by its digits could not be grounded at
all.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from vericlaim.sql.values_catalog import (
    Catalog,
    CatalogValue,
    ReferenceMatch,
    StaticCatalog,
    reference_key,
)

# A candidate must beat this to be considered a match at all.
RESOLVE_THRESHOLD = 0.88
# Candidates within this fraction of the best score are considered together, so a value
# stored twice under slightly different spellings resolves to both rather than to one.
VARIANT_BAND = 0.90
# Two candidate values this similar to each other are the same entity, not a choice.
SAME_ENTITY = 0.80
MIN_MENTION_LEN = 3
MULTI_TOKEN_FLOOR = 0.75
MULTI_TOKEN_MEAN = 0.80
# A near-perfect match this far clear of every rival resolves rather than asking.
DOMINANT_SCORE = 0.98
DOMINANT_MARGIN = 0.05

Status = Literal["resolved", "ambiguous", "not_found"]
Kind = Literal["value", "reference"]

# Words that are part of a company's registration, not of its name. A question says
# "Al-Falah Insurance" for a customer stored as "Al-Falah Insurance Pvt Ltd", and just as
# often the other way round; stripping them from both ends makes the two the same string.
_NOISE_TOKENS = frozenset(
    {
        "pvt",
        "private",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "plc",
        "co",
        "company",
        "corp",
        "corporation",
        "and",
        "the",
        # "M/s" normalizes to two tokens, so both letters have to be listed.
        "m",
        "s",
    }
)
_QUOTED_RE = re.compile(r"(['\"])(.*?)\1")
# An identifier: an optional short alphabetic prefix followed by at least three digits.
# Matched against the punctuation-free form, so CLM-1088, clm 1088 and clm1088 are one.
_REFERENCE_SHAPE_RE = re.compile(r"(?:[a-z]{2,6})?\d{3,}")


@dataclass(frozen=True, slots=True)
class Match:
    """One column, and the stored values a mention was grounded to in it."""

    table: str
    column: str
    values: tuple[str, ...]
    match_kind: str
    score: float


@dataclass(frozen=True, slots=True)
class Resolution:
    """What became of one mention.

    ``ambiguous`` is a first-class outcome rather than a low-confidence ``resolved``:
    picking between two customers whose names both fit is the user's decision, and
    guessing produces a confident answer about the wrong one.
    """

    mention: str
    status: Status
    kind: Kind = "value"
    matches: tuple[Match, ...] = ()
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityResolution:
    """Every mention in one question, and whether the user has to be asked."""

    mentions: tuple[Resolution, ...] = ()
    needs_clarification: bool = False
    clarification_question: str = ""


# ------------------------------------------------------------------ normalization


def normalize(text: str) -> str:
    """Fold a string to the form scoring happens in; originals survive in the results."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", without_marks.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def strip_noise(text: str) -> str:
    """Shave registration boilerplate from both ends of a normalized name.

    Only from the ends: "Ltd Horizons Group" is a name that happens to start with a noise
    word, and removing it from the middle of a name would corrupt it. A string that is
    *entirely* noise is returned unchanged rather than emptied, since an empty mention
    matches everything equally badly.
    """
    tokens = text.split()
    while tokens and tokens[0] in _NOISE_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1] in _NOISE_TOKENS:
        tokens.pop()
    return " ".join(tokens) if tokens else text


def collapse_runs(text: str) -> str:
    """Collapse repeated characters, so "Hussain" and "Husain" score as one spelling."""
    return re.sub(r"(.)\1+", r"\1", text)


def score_strings(mention_norm: str, candidate_norm: str) -> float:
    """Score two normalized strings for entity-value similarity."""
    base = _symmetric_pair_score(mention_norm, candidate_norm)
    containment = _token_containment_score(mention_norm, candidate_norm)
    return max(base, containment)


# ------------------------------------------------------------------ resolution


def resolve_mention(mention: str, catalog: Catalog) -> Resolution:
    """Resolve one mention against the values the database holds."""
    # Keyed with reference_key rather than normalize() so the mention is folded exactly
    # the way the database folds the stored value it will be compared against.
    key = reference_key(mention)
    if _REFERENCE_SHAPE_RE.fullmatch(key):
        return _resolve_reference(mention, key, catalog)

    stripped = strip_noise(normalize(mention))
    if len(stripped.replace(" ", "")) < MIN_MENTION_LEN:
        return Resolution(mention=mention, status="not_found")

    scored = _score_catalog(stripped, catalog.vocabulary())
    if not scored:
        return Resolution(mention=mention, status="not_found")

    top = max(candidate["score"] for candidate in scored)
    if top < RESOLVE_THRESHOLD:
        return Resolution(mention=mention, status="not_found")
    return _resolution_from_scored(mention, scored, top)


def _resolve_reference(mention: str, key: str, catalog: Catalog) -> Resolution:
    """Resolve an identifier by exact lookup, or not at all.

    There is no fallback to fuzzy matching on purpose. A reference the database does not
    hold is a reference the user got wrong or a claim that does not exist, and both of
    those deserve to be said rather than approximated.
    """
    matches = catalog.lookup_reference(key)
    if not matches:
        return Resolution(mention=mention, status="not_found", kind="reference")

    values = sorted({match.value for match in matches})
    if len(values) > 1:
        return Resolution(
            mention=mention,
            status="ambiguous",
            kind="reference",
            candidates=tuple(values),
        )
    return Resolution(
        mention=mention,
        status="resolved",
        kind="reference",
        matches=_reference_matches(matches),
    )


def _reference_matches(matches: Sequence[ReferenceMatch]) -> tuple[Match, ...]:
    by_slot: dict[tuple[str, str], list[str]] = {}
    for match in matches:
        by_slot.setdefault((match.table, match.column), []).append(match.value)
    return tuple(
        Match(
            table=table,
            column=column,
            values=tuple(sorted(set(values))),
            match_kind="equals",
            score=1.0,
        )
        for (table, column), values in sorted(by_slot.items())
    )


def resolve_entities(
    understanding: Mapping[str, Any], catalog: Catalog
) -> EntityResolution:
    """Resolve every entity and quoted filter value the question named."""
    mentions = _extract_mentions(understanding)
    if not mentions:
        return EntityResolution()

    resolved = tuple(resolve_mention(mention, catalog) for mention in mentions)
    ambiguous = [result for result in resolved if result.status == "ambiguous"]
    return EntityResolution(
        mentions=resolved,
        needs_clarification=bool(ambiguous),
        clarification_question=(
            _clarification_question(ambiguous[0]) if ambiguous else ""
        ),
    )


def _extract_mentions(understanding: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect entity names and the quoted literals inside filter expressions."""
    mentions: list[str] = []
    for entity in understanding.get("entities") or ():
        _append_mention(mentions, entity)
    for filter_text in understanding.get("filters") or ():
        if isinstance(filter_text, str):
            for match in _QUOTED_RE.finditer(filter_text):
                _append_mention(mentions, match.group(2))

    seen: set[str] = set()
    deduped: list[str] = []
    for mention in mentions:
        if mention not in seen:
            seen.add(mention)
            deduped.append(mention)
    return tuple(deduped)


def _append_mention(mentions: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        mentions.append(value.strip())


def stored_values(resolved: EntityResolution | None) -> list[dict[str, Any]]:
    """Flatten grounded mentions into the spelling inventory a prompt is shown.

    Only resolved mentions travel. An ambiguous or not-found mention offered to the
    planner or the generator would read as a value the database holds -- the question of
    which one was meant would disappear, and the filter written from it would match
    nothing while looking deliberate.
    """
    if resolved is None:
        return []
    return [
        {
            "mention": resolution.mention,
            "table": match.table,
            "column": match.column,
            "values": list(match.values),
            "match_kind": match.match_kind,
        }
        for resolution in resolved.mentions
        if resolution.status == "resolved"
        for match in resolution.matches
    ]


def _clarification_question(result: Resolution) -> str:
    options = " or ".join(f'"{candidate}"' for candidate in result.candidates)
    return f'Did you mean {options} for "{result.mention}"?'


# ------------------------------------------------------------------ scoring


def _score_catalog(
    mention_norm: str,
    vocabulary: Mapping[str, Mapping[str, tuple[CatalogValue, ...]]],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for table, columns in vocabulary.items():
        for column, candidates in columns.items():
            for candidate in candidates:
                candidate_norm = strip_noise(normalize(candidate.value))
                if not candidate_norm:
                    continue
                scored.append(
                    {
                        "table": table,
                        "column": column,
                        "value": candidate.value,
                        "match_kind": candidate.match_kind,
                        "norm": candidate_norm,
                        "score": score_strings(mention_norm, candidate_norm),
                    }
                )
    return scored


def _resolution_from_scored(
    mention: str, scored: list[dict[str, Any]], top: float
) -> Resolution:
    """Decide between one entity spelled several ways and several entities."""
    group = _variant_group(scored, top)
    clusters = _clusters(group)

    if len(clusters) == 1:
        return Resolution(
            mention=mention,
            status="resolved",
            matches=_matches_for_cluster(group, clusters[0]),
        )

    dominant = _dominant_cluster(group, clusters)
    if dominant is not None:
        return Resolution(
            mention=mention,
            status="resolved",
            matches=_matches_for_cluster(group, dominant),
        )
    return Resolution(
        mention=mention,
        status="ambiguous",
        candidates=_cluster_candidates(group, clusters)[:4],
    )


def _variant_group(scored: list[dict[str, Any]], top: float) -> list[dict[str, Any]]:
    """Every candidate close enough to the best to be considered alongside it."""
    band_cutoff = VARIANT_BAND * top
    group = [candidate for candidate in scored if candidate["score"] >= band_cutoff]
    for candidate in scored:
        if candidate in group:
            continue
        if any(_near_variant(candidate["norm"], member["norm"]) for member in group):
            group.append(candidate)
    return group


def _near_variant(left: str, right: str) -> bool:
    token_similarity = SequenceMatcher(
        None, " ".join(sorted(left.split())), " ".join(sorted(right.split()))
    ).ratio()
    return token_similarity >= VARIANT_BAND


def _clusters(group: list[dict[str, Any]]) -> list[set[str]]:
    """Union-find over the candidates, joining spellings of the same entity."""
    norms = sorted({candidate["norm"] for candidate in group})
    parent = {norm: norm for norm in norms}

    def find(norm: str) -> str:
        while parent[norm] != norm:
            parent[norm] = parent[parent[norm]]
            norm = parent[norm]
        return norm

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, left in enumerate(norms):
        for right in norms[index + 1 :]:
            if _symmetric_pair_score(left, right) >= SAME_ENTITY or _near_variant(
                left, right
            ):
                union(left, right)

    clustered: dict[str, set[str]] = {}
    for norm in norms:
        clustered.setdefault(find(norm), set()).add(norm)
    return sorted(clustered.values(), key=lambda cluster: sorted(cluster)[0])


def _dominant_cluster(
    group: list[dict[str, Any]], clusters: list[set[str]]
) -> set[str] | None:
    """Return the one cluster that is both near-perfect and clear of the rest."""
    cluster_scores = [
        (
            max(
                candidate["score"]
                for candidate in group
                if candidate["norm"] in cluster
            ),
            cluster,
        )
        for cluster in clusters
    ]
    best_score, best_cluster = max(
        cluster_scores, key=lambda item: (item[0], sorted(item[1])[0])
    )
    if best_score < DOMINANT_SCORE:
        return None
    if all(
        score <= best_score - DOMINANT_MARGIN
        for score, cluster in cluster_scores
        if cluster is not best_cluster
    ):
        return best_cluster
    return None


def _matches_for_cluster(
    group: list[dict[str, Any]], cluster: set[str]
) -> tuple[Match, ...]:
    by_slot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in group:
        if candidate["norm"] in cluster:
            by_slot.setdefault((candidate["table"], candidate["column"]), []).append(
                candidate
            )

    return tuple(
        Match(
            table=table,
            column=column,
            values=tuple(sorted({candidate["value"] for candidate in candidates})),
            match_kind=(
                "contains"
                if any(
                    candidate["match_kind"] == "contains" for candidate in candidates
                )
                else "equals"
            ),
            score=max(candidate["score"] for candidate in candidates),
        )
        for (table, column), candidates in sorted(by_slot.items())
    )


def _cluster_candidates(
    group: list[dict[str, Any]], clusters: list[set[str]]
) -> tuple[str, ...]:
    """Pick one readable value per cluster to offer the user, best-scoring first."""
    candidates: list[tuple[float, str]] = []
    for cluster in clusters:
        members = [
            candidate for candidate in group if candidate["norm"] in cluster
        ]
        best = sorted(
            members,
            key=lambda candidate: (
                # A comma means the value is a compound ("Lahore, Punjab"); the plain
                # form reads better as a question to a person.
                "," in candidate["value"],
                -candidate["score"],
                candidate["value"],
            ),
        )[0]
        candidates.append((best["score"], best["value"]))

    if any("," not in value for _, value in candidates):
        candidates = [
            candidate for candidate in candidates if "," not in candidate[1]
        ]
    return tuple(
        value for _, value in sorted(candidates, key=lambda item: (-item[0], item[1]))
    )


def _char3_grams(value: str) -> set[str]:
    padded = f"  {value}  "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def _char3_jaccard(left: str, right: str) -> float:
    left_grams, right_grams = _char3_grams(left), _char3_grams(right)
    if not left_grams and not right_grams:
        return 1.0
    union = left_grams | right_grams
    if not union:
        return 0.0
    return len(left_grams & right_grams) / len(union)


def _symmetric_pair_score(left: str, right: str) -> float:
    """Blend sequence, token-order-free and trigram similarity, typo-tolerantly."""
    score = 0.0
    for pair_left, pair_right in (
        (left, right),
        (collapse_runs(left), collapse_runs(right)),
    ):
        seq = SequenceMatcher(None, pair_left, pair_right).ratio()
        tok = SequenceMatcher(
            None,
            " ".join(sorted(pair_left.split())),
            " ".join(sorted(pair_right.split())),
        ).ratio()
        jac = _char3_jaccard(pair_left, pair_right)
        score = max(score, 0.55 * max(seq, tok) + 0.45 * jac)
    return score


def _token_containment_score(mention_norm: str, candidate_norm: str) -> float:
    """Score a mention that is a subset of a longer stored value.

    "Ahmed" against "Ahmed Textiles" is a real match; whole-string similarity would miss
    it because most of the candidate is absent from the mention.
    """
    mention_tokens = [
        token for token in mention_norm.split() if len(token) >= MIN_MENTION_LEN
    ]
    candidate_tokens = candidate_norm.split()
    if not mention_tokens or not candidate_tokens:
        return 0.0

    for mention_token in mention_tokens:
        collapsed = collapse_runs(mention_token)
        if not any(
            SequenceMatcher(None, collapsed, collapse_runs(candidate_token)).ratio()
            >= 0.85
            for candidate_token in candidate_tokens
        ):
            return _moderate_multi_token_score(mention_tokens, candidate_tokens)

    return min(1.0, 0.85 + 0.15 * (len(mention_tokens) / len(candidate_tokens)))


def _moderate_multi_token_score(
    mention_tokens: list[str], candidate_tokens: list[str]
) -> float:
    """Allow a multi-token mention where every token is close but none is exact."""
    if len(mention_tokens) < 2:
        return 0.0

    token_scores = [
        max(
            _token_similarity(mention_token, candidate_token)
            for candidate_token in candidate_tokens
        )
        for mention_token in mention_tokens
    ]
    if min(token_scores) < MULTI_TOKEN_FLOOR:
        return 0.0

    mean_score = sum(token_scores) / len(token_scores)
    if mean_score < MULTI_TOKEN_MEAN:
        return 0.0

    count_ratio = len(mention_tokens) / len(candidate_tokens)
    return min(1.0, 0.85 + 0.15 * mean_score * count_ratio)


def _token_similarity(left: str, right: str) -> float:
    return max(
        SequenceMatcher(None, left, right).ratio(),
        SequenceMatcher(None, collapse_runs(left), collapse_runs(right)).ratio(),
        _char3_jaccard(left, right),
    )


# ------------------------------------------------------------------ SQL rewriting


def fuzzy_rewrite_sql(sql: str, catalog: Catalog) -> str | None:
    """Rewrite resolvable string filters to the values the database stores.

    Returns ``None`` when nothing changed, so the caller can tell "already correct" from
    "repaired" and does not re-execute an identical query.

    Only vocabulary columns are eligible. Reference columns never appear in the
    vocabulary, so a claim number written into a filter is left exactly as the generator
    wrote it -- to be answered with no rows if it is wrong, which is the truthful answer.
    """
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except (ParseError, ValueError):
        return None
    if tree is None:
        return None

    vocabulary = catalog.vocabulary()
    alias_map = _alias_map(tree, vocabulary)

    changed = False
    for target in _rewrite_targets(tree):
        slot = _target_slot(target, alias_map, vocabulary)
        if slot is None:
            continue
        table, column = slot
        replacement = _replacement_for_target(
            target, table, column, vocabulary[table][column]
        )
        if replacement is None:
            continue
        target["node"].replace(replacement)
        changed = True

    return tree.sql(dialect="postgres") if changed else None


def unresolvable_filters(sql: str, catalog: Catalog) -> tuple[str, ...]:
    """Return the filter literals no catalogued value matches.

    The backstop for an empty result. Zero rows because the query filtered on a value the
    database does not hold is a different answer from zero rows because nothing matched
    the combination of conditions, and no amount of rewriting SQL will fix the first one.
    Naming the value lets the run stop and say so, instead of spending the repair budget
    discovering it cannot.

    Only vocabulary columns are judged. A reference column is not catalogued, and an
    unknown claim number means there is no such claim -- which the answer must say
    plainly, not attribute to a spelling mistake.
    """
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except (ParseError, ValueError):
        return ()
    if tree is None:
        return ()

    vocabulary = catalog.vocabulary()
    alias_map = _alias_map(tree, vocabulary)

    unknown: list[str] = []
    for target in _rewrite_targets(tree):
        slot = _target_slot(target, alias_map, vocabulary)
        if slot is None:
            continue
        table, column = slot
        slice_catalog = StaticCatalog({table: {column: vocabulary[table][column]}})
        for literal in target["literals"]:
            if resolve_mention(literal, slice_catalog).status == "not_found":
                unknown.append(literal)
    return tuple(unknown)


def _alias_map(
    tree: exp.Expression,
    vocabulary: Mapping[str, Mapping[str, tuple[CatalogValue, ...]]],
) -> dict[str, str]:
    """Map every name a column can be qualified by to its catalogued table.

    Unqualified table names are matched by their bare name when exactly one catalogued
    table has it. The validator qualifies tables before this runs, so that path is a
    safety net rather than the norm -- but a rewrite that silently did nothing because a
    schema was missing would be indistinguishable from a query that needed no repair.
    """
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        qualified = f"{table.db}.{table.name}".lower() if table.db else None
        if qualified is None or qualified not in vocabulary:
            candidates = [
                name
                for name in vocabulary
                if name.rsplit(".", 1)[-1] == table.name.lower()
            ]
            if len(candidates) != 1:
                continue
            qualified = candidates[0]
        aliases[table.name.lower()] = qualified
        aliases[table.alias_or_name.lower()] = qualified
    return aliases


def _rewrite_targets(tree: exp.Expression) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for node in tree.find_all(exp.EQ, exp.In, exp.ILike, exp.Like):
        if isinstance(node, exp.EQ):
            target = _eq_target(node)
        elif isinstance(node, exp.In):
            target = _in_target(node)
        else:
            target = _like_target(node)
        if target is not None:
            targets.append(target)
    return targets


def _eq_target(node: exp.EQ) -> dict[str, Any] | None:
    left, right = node.left, node.right
    if isinstance(left, exp.Column) and _is_string_literal(right):
        return {"node": node, "kind": "equality", "column": left, "literals": [right.this]}
    if isinstance(right, exp.Column) and _is_string_literal(left):
        return {"node": node, "kind": "equality", "column": right, "literals": [left.this]}
    return None


def _in_target(node: exp.In) -> dict[str, Any] | None:
    expressions = list(node.expressions)
    if (
        not isinstance(node.this, exp.Column)
        or not expressions
        or not all(_is_string_literal(expression) for expression in expressions)
    ):
        return None
    return {
        "node": node,
        "kind": "equality",
        "column": node.this,
        "literals": [expression.this for expression in expressions],
    }


def _like_target(node: exp.ILike | exp.Like) -> dict[str, Any] | None:
    """Only a plain ``%value%`` pattern is rewritable; anything else is deliberate."""
    pattern = node.expression
    if not isinstance(node.this, exp.Column) or not _is_string_literal(pattern):
        return None
    pattern_text = pattern.this
    if (
        len(pattern_text) < 2
        or not pattern_text.startswith("%")
        or not pattern_text.endswith("%")
    ):
        return None
    inner = pattern_text[1:-1]
    if not inner or "%" in inner or "_" in inner:
        return None
    return {"node": node, "kind": "contains", "column": node.this, "literals": [inner]}


def _is_string_literal(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and node.is_string


def _target_slot(
    target: dict[str, Any],
    alias_map: dict[str, str],
    vocabulary: Mapping[str, Mapping[str, tuple[CatalogValue, ...]]],
) -> tuple[str, str] | None:
    column = target["column"]
    qualifier = column.table
    if qualifier:
        table = alias_map.get(qualifier.lower())
    else:
        tables = sorted(set(alias_map.values()))
        table = tables[0] if len(tables) == 1 else None
    if table is None:
        return None

    columns = vocabulary.get(table, {})
    name = next(
        (
            candidate
            for candidate in columns
            if candidate.lower() == column.name.lower()
        ),
        None,
    )
    return None if name is None else (table, name)


def _replacement_for_target(
    target: dict[str, Any],
    table: str,
    column: str,
    values: tuple[CatalogValue, ...],
) -> exp.Expression | None:
    """Resolve every literal in one filter, or leave the filter alone.

    All-or-nothing on purpose: rewriting two of three values in an ``IN`` list would
    narrow the result set to something no one asked for.
    """
    slice_catalog = StaticCatalog({table: {column: values}})
    resolved: set[str] = set()
    match_kind = "equals"

    for literal in target["literals"]:
        resolution = resolve_mention(literal, slice_catalog)
        if resolution.status != "resolved" or not resolution.matches:
            return None
        for match in resolution.matches:
            resolved.update(match.values)
            if match.match_kind == "contains":
                match_kind = "contains"

    if resolved == set(target["literals"]):
        return None

    ordered = sorted(resolved)
    if target["kind"] == "contains" or match_kind == "contains":
        return _contains_replacement(target["column"], ordered)
    return _equality_replacement(target["column"], ordered)


def _equality_replacement(column: exp.Column, values: list[str]) -> exp.Expression:
    if len(values) == 1:
        return exp.EQ(this=column.copy(), expression=exp.Literal.string(values[0]))
    return exp.In(
        this=column.copy(),
        expressions=[exp.Literal.string(value) for value in values],
    )


def _contains_replacement(column: exp.Column, values: list[str]) -> exp.Expression:
    expressions: list[exp.Expression] = [
        exp.ILike(
            this=column.copy(),
            expression=exp.Literal.string(f"%{_escape_like_value(value)}%"),
        )
        for value in values
    ]
    expression = expressions[0]
    for next_expression in expressions[1:]:
        expression = exp.or_(expression, next_expression)
    return exp.Paren(this=expression) if len(expressions) > 1 else expression


def _escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
