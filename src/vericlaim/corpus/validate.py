"""Check that the four generated sources describe the same world.

The corpus is built so that consistency is a property of its construction: spreadsheet
figures are computed from generated claims, scanned reports are written against real
claim rows, and coverage products name real policy documents. This module is the proof
of that rather than the repair of it -- every finding here means a generator drifted
from the contract, not that a value needs patching.

Nothing here touches Postgres. The rows are regenerated in memory from the same seed
that produced the files on disk, so the whole check runs in the offline suite and
catches drift before a load can bake it in.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vericlaim.config import Settings, get_settings
from vericlaim.corpus.catalog import COVERAGE_PRODUCTS, REGIONS
from vericlaim.corpus.load import ops_tables
from vericlaim.corpus.policies import policy_documents
from vericlaim.corpus.scanned import scanned_documents
from vericlaim.corpus.spreadsheets import WORKBOOK_GENERATORS
from vericlaim.corpus.transactions import TransactionRows, generate_transactions
from vericlaim.sheets.ingest import rows_for
from vericlaim.sheets.profiler import ColumnProfile, profile_workbook
from vericlaim.sql.contexts import Invariant, SchemaContext, load_contexts
from vericlaim.sql.observer import check_invariant

__all__ = ("Finding", "validate_corpus")


@dataclass(frozen=True, slots=True)
class Finding:
    """One way the corpus contradicts itself, named well enough to act on."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


def validate_corpus(seed: int = 42, *, settings: Settings | None = None) -> tuple[Finding, ...]:
    """Return every cross-source inconsistency in the generated corpus.

    An empty result is the pass. Findings are ordered by rule so two runs over the same
    corpus report the same thing in the same order.
    """
    resolved = settings or get_settings()
    transactions = generate_transactions(seed)

    findings: list[Finding] = []
    findings.extend(_every_source_is_present(resolved, seed))
    findings.extend(_policy_documents_resolve(resolved))
    findings.extend(_scanned_documents_key_to_claims(resolved, transactions))
    findings.extend(_workbooks_match_their_contexts(resolved))
    findings.extend(_workbook_regions_exist(resolved))
    findings.extend(_ops_invariants_hold(resolved, transactions))
    findings.extend(_sheet_invariants_hold(resolved))
    return tuple(findings)


# ---------------------------------------------------------------- cross-source keys


def _every_source_is_present(settings: Settings, seed: int) -> Iterator[Finding]:
    """A source with no files passes every other rule vacuously.

    This is the rule that stops the validator from reporting a clean corpus over an
    absent one -- the most misleading result it could give, because "no findings" is
    exactly what a correct corpus looks like. Counts come from the generators
    themselves, never from a number written here twice.
    """
    expected = (
        (settings.policy_dir, "*.pdf", len(policy_documents()), "policy wordings"),
        (settings.spreadsheet_dir, "*.xlsx", len(WORKBOOK_GENERATORS), "workbooks"),
        (settings.scanned_dir, "*.pdf", len(scanned_documents(seed)), "scanned documents"),
    )
    for directory, glob, count, description in expected:
        present = len(list(directory.glob(glob))) if directory.is_dir() else 0
        if present != count:
            yield Finding(
                "source_incomplete",
                f"{directory} holds {present} {description}, not the {count} generated",
            )


def _policy_documents_resolve(settings: Settings) -> Iterator[Finding]:
    """Every product must name a wording a reader can actually open."""
    for product in COVERAGE_PRODUCTS:
        if not (settings.policy_dir / product.policy_document).is_file():
            yield Finding(
                "policy_document_missing",
                f"{product.product_name} names {product.policy_document}, "
                f"which is not in {settings.policy_dir}",
            )


def _scanned_documents_key_to_claims(
    settings: Settings, transactions: TransactionRows
) -> Iterator[Finding]:
    """A scan filed under a claim that does not exist cites a matter nobody can open."""
    pattern = re.compile(settings.claim_id_pattern)
    claim_numbers = {claim.claim_number for claim in transactions.claims}

    for path in sorted(settings.scanned_dir.glob("*.pdf")):
        match = pattern.search(path.name)
        if match is None:
            yield Finding(
                "scanned_filename_unkeyed",
                f"{path.name} names no claim matching {settings.claim_id_pattern}",
            )
        elif match.group(0) not in claim_numbers:
            yield Finding(
                "scanned_claim_unknown",
                f"{path.name} is filed under {match.group(0)}, which is not in ops.claims",
            )


def _workbooks_match_their_contexts(settings: Settings) -> Iterator[Finding]:
    """The reviewed contexts are the contract; the workbooks on disk must match them."""
    declared = {
        context.workbook
        for context in load_contexts(settings.sheets_context_dir).values()
        if context.workbook is not None
    }
    present = {path.name for path in settings.spreadsheet_dir.glob("*.xlsx")}

    for workbook in sorted(declared - present):
        yield Finding("workbook_missing", f"{workbook} is declared by a context but not generated")
    for workbook in sorted(present - declared):
        yield Finding("workbook_undeclared", f"{workbook} was generated but no context declares it")


def _workbook_regions_exist(settings: Settings) -> Iterator[Finding]:
    """A spreadsheet naming a region the database does not hold cannot be reconciled."""
    known = {region.region_name for region in REGIONS}

    for context, columns, rows in _workbook_rows(settings):
        for position, column in enumerate(columns):
            if column.name != "region":
                continue
            for row in rows:
                value = row[position]
                if isinstance(value, str) and value and value not in known:
                    yield Finding(
                        "workbook_region_unknown",
                        f"{context.workbook} > {context.sheet} names region {value!r}, "
                        "which is not in ops.regions",
                    )


# ------------------------------------------------------------------- invariants


def _ops_invariants_hold(settings: Settings, transactions: TransactionRows) -> Iterator[Finding]:
    """Judge the generated rows by the rule the observer will judge queries by."""
    contexts = load_contexts(settings.sql_context_dir)

    for table, rows in ops_tables(transactions):
        context = contexts.get(f"ops.{table}")
        if context is None or not rows:
            continue
        for invariant in context.invariants:
            violated = _violation(invariant, invariant.columns, _attribute_rows(invariant, rows))
            if violated is not None:
                yield Finding("ops_invariant_violated", f"ops.{table}: {violated}")


def _sheet_invariants_hold(settings: Settings) -> Iterator[Finding]:
    """Judge the coerced workbook rows -- what the ingest will actually insert."""
    for context, columns, rows in _workbook_rows(settings):
        names = tuple(column.name for column in columns)
        for invariant in context.invariants:
            # An invariant whose columns are not on the sheet is reported rather than
            # skipped. Skipping is how a rule stops firing without anyone noticing --
            # a renamed column would otherwise turn a real check into silence, and
            # silence is indistinguishable from a corpus that passes.
            missing = [column for column in invariant.columns if column not in names]
            if missing:
                yield Finding(
                    "sheet_column_missing",
                    f"{context.workbook} > {context.sheet} declares an invariant over "
                    f"{', '.join(missing)}, which the sheet does not have",
                )
                continue
            violated = _violation(invariant, names, rows)
            if violated is not None:
                yield Finding(
                    "sheet_invariant_violated",
                    f"{context.workbook} > {context.sheet}: {violated}",
                )


def _violation(
    invariant: Invariant,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> str | None:
    """Check one invariant over rows laid out in ``columns`` order.

    The empty function name is what the observer calls a bare column, as opposed to one
    read through an aggregate. These rows are the stored values themselves, so that is
    what they are.
    """
    sources: Mapping[tuple[str, str], int] = {
        ("", name.lower()): index for index, name in enumerate(columns)
    }
    if any(("", column.lower()) not in sources for column in invariant.columns):
        return None
    return check_invariant(invariant, sources, rows)


def _attribute_rows(invariant: Invariant, rows: Sequence[Any]) -> list[tuple[Any, ...]]:
    """Project generated dataclass rows down to the columns one invariant names."""
    return [tuple(getattr(row, column) for column in invariant.columns) for row in rows]


# ------------------------------------------------------------------- workbook reading


def _workbook_rows(
    settings: Settings,
) -> Iterator[tuple[SchemaContext, Sequence[ColumnProfile], list[tuple[Any, ...]]]]:
    """Yield each declared workbook table as the ingest will read it.

    Reading through the profiler rather than openpyxl directly is deliberate: the
    workbooks carry merged banners, two-row headers, spacer columns and TOTAL footers,
    and the validator must judge the values the database will hold, not the cells a
    naive reader would find.
    """
    contexts = {
        (context.workbook, context.sheet): context
        for context in load_contexts(settings.sheets_context_dir).values()
        if context.workbook is not None
    }

    for path in sorted(settings.spreadsheet_dir.glob("*.xlsx")):
        for profile in profile_workbook(path):
            context = contexts.get((profile.workbook, profile.sheet))
            if context is None:
                continue
            for table in profile.tables:
                # rows_for appends five lineage columns; trim to the data the context
                # describes so a column position means what the context says it means.
                rows = [row[: len(table.columns)] for row in rows_for(profile, table, "validate")]
                yield context, table.columns, rows
