"""The Evidence spine: the one data contract every source tool must satisfy.

Four structurally different sources -- policy prose, SQL result sets, spreadsheet
cells, and OCR'd scans -- have to collapse into a single cited answer. They do that by
never reaching synthesis in their native shapes. Every tool returns ``list[Evidence]``
and nothing else, so reconciliation compares like with like and citation correctness
becomes something the system computes rather than something a reader judges.

Two invariants carry most of the weight:

* **A locator's type must match its ``source_type``.** Enforced at construction, so a
  tool cannot quietly emit evidence that nothing can cite.
* **Ids are stable.** ``E3`` means the same passage at synthesis time and at
  verification time. A citation that shifts meaning between the two is a correctness
  bug, not a cosmetic one.

Locators own their own ``cite()`` rendering so the citation string has one definition,
rather than being reinvented in the API, the UI, and the evaluation scorer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

type SourceType = Literal["policy", "sql", "spreadsheet", "scanned_pdf"]

SOURCE_TYPES: tuple[SourceType, ...] = ("policy", "sql", "spreadsheet", "scanned_pdf")

#: Human labels for the four sources, used in prompts and the UI.
SOURCE_LABELS: dict[SourceType, str] = {
    "policy": "Policy document",
    "sql": "Claims database",
    "spreadsheet": "Operational spreadsheet",
    "scanned_pdf": "Scanned document",
}


def content_hash(text: str) -> str:
    """Return a stable SHA-256 of normalised text, used for dedup."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- locators


@dataclass(frozen=True, slots=True)
class PolicyLocator:
    """Where a passage sits in a policy document."""

    source_type: ClassVar[SourceType] = "policy"

    document: str
    page: int | None = None
    section: str | None = None
    chunk_id: str | None = None

    def cite(self) -> str:
        parts = [self.document]
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.section:
            parts.append(self.section)
        return " › ".join(parts)

    def key(self) -> tuple[Any, ...]:
        return (self.document, self.page, self.section)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "page": self.page,
            "section": self.section,
            "chunk_id": self.chunk_id,
        }


@dataclass(frozen=True, slots=True)
class SqlLocator:
    """Which tables were read, and the exact query that read them.

    The executed SQL is part of the citation rather than a debug aid: a numeric claim
    about claims data is only auditable if the reader can see how it was derived.
    """

    source_type: ClassVar[SourceType] = "sql"

    tables: tuple[str, ...]
    executed_sql: str
    row_count: int = 0

    def cite(self) -> str:
        tables = ", ".join(self.tables) if self.tables else "claims database"
        rows = "1 row" if self.row_count == 1 else f"{self.row_count} rows"
        return f"{tables} (SQL · {rows})"

    def key(self) -> tuple[Any, ...]:
        return (self.tables, " ".join(self.executed_sql.split()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": list(self.tables),
            "executed_sql": self.executed_sql,
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class SpreadsheetLocator:
    """A workbook down to the cell range, which is what keeps sheets citable.

    Ingesting spreadsheets into SQL without this would reduce four sources to three:
    the answer could no longer point at the cell it came from.
    """

    source_type: ClassVar[SourceType] = "spreadsheet"

    workbook: str
    sheet: str
    row: int | None = None
    a1_range: str | None = None

    def cite(self) -> str:
        parts = [self.workbook, self.sheet]
        if self.row is not None:
            parts.append(f"row {self.row}")
        rendered = " › ".join(parts)
        return f"{rendered} ({self.a1_range})" if self.a1_range else rendered

    def key(self) -> tuple[Any, ...]:
        return (self.workbook, self.sheet, self.row, self.a1_range)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workbook": self.workbook,
            "sheet": self.sheet,
            "row": self.row,
            "a1_range": self.a1_range,
        }


@dataclass(frozen=True, slots=True)
class ScannedLocator:
    """A page of a scanned document, carrying how much to trust the OCR.

    ``ocr_confidence`` travels with the citation because a claim read off a degraded
    scan must be qualified rather than asserted, and the reader deserves to know which.
    """

    source_type: ClassVar[SourceType] = "scanned_pdf"

    document: str
    page: int
    ocr_confidence: float | None = None
    ocr_engine: str | None = None
    escalated: bool = False

    def cite(self) -> str:
        base = f"{self.document} › p.{self.page}"
        if self.ocr_confidence is None:
            return base
        suffix = f"OCR {self.ocr_confidence:.2f}"
        if self.escalated:
            suffix += ", vision-assisted"
        return f"{base} ({suffix})"

    def key(self) -> tuple[Any, ...]:
        return (self.document, self.page)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "page": self.page,
            "ocr_confidence": self.ocr_confidence,
            "ocr_engine": self.ocr_engine,
            "escalated": self.escalated,
        }


type Locator = PolicyLocator | SqlLocator | SpreadsheetLocator | ScannedLocator

_LOCATOR_BY_SOURCE: dict[SourceType, type] = {
    PolicyLocator.source_type: PolicyLocator,
    SqlLocator.source_type: SqlLocator,
    SpreadsheetLocator.source_type: SpreadsheetLocator,
    ScannedLocator.source_type: ScannedLocator,
}


class LocatorMismatchError(TypeError):
    """A piece of evidence was built with a locator its source type cannot use."""


# ------------------------------------------------------------------------ provenance


@dataclass(frozen=True, slots=True)
class Provenance:
    """Which tool produced this evidence, when, and in response to what.

    ``query`` and ``trace_id`` are what let a LangSmith trace be reconciled against a
    finished answer -- without them, "where did this come from?" is unanswerable once
    the run is over.
    """

    tool: str
    retrieved_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    trace_id: str | None = None
    query: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "retrieved_at": self.retrieved_at.isoformat(),
            "trace_id": self.trace_id,
            "query": self.query,
        }


# -------------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class Evidence:
    """One citable fact from one source.

    ``id`` is assigned by :class:`EvidenceSet` rather than by the producing tool, so
    that ids are unique and stable across a whole answer instead of per tool.
    """

    source_type: SourceType
    source_id: str
    content: str
    locator: Locator
    provenance: Provenance
    confidence: float = 1.0
    id: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in _LOCATOR_BY_SOURCE:
            raise ValueError(
                f"Unknown source_type {self.source_type!r}. "
                f"Expected one of {', '.join(SOURCE_TYPES)}."
            )
        expected = _LOCATOR_BY_SOURCE[self.source_type]
        if not isinstance(self.locator, expected):
            raise LocatorMismatchError(
                f"source_type {self.source_type!r} requires {expected.__name__}, "
                f"got {type(self.locator).__name__}. Evidence that cannot be cited "
                "must not be constructible."
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if not self.content.strip():
            raise ValueError("Evidence content must not be empty")

    # ------------------------------------------------------------------ properties

    @property
    def label(self) -> str:
        """The human name of this evidence's source category."""
        return SOURCE_LABELS[self.source_type]

    @property
    def content_hash(self) -> str:
        return content_hash(self.content)

    def cite(self) -> str:
        """Render the citation string for this evidence."""
        return self.locator.cite()

    def dedup_key(self) -> tuple[Any, ...]:
        """Identity for deduplication.

        Includes the locator, so the same sentence appearing on two different pages
        stays two pieces of evidence -- they are separately citable and a reader
        may need either.
        """
        return (
            self.source_type,
            self.source_id,
            self.locator.key(),
            self.content_hash,
        )

    def is_low_confidence(self, floor: float) -> bool:
        """Whether synthesis must qualify this rather than assert it."""
        return self.confidence < floor

    def with_id(self, new_id: str) -> Evidence:
        """Return a copy carrying ``new_id`` (the dataclass is frozen)."""
        return replace(self, id=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_label": self.label,
            "source_id": self.source_id,
            "content": self.content,
            "citation": self.cite(),
            "locator": self.locator.to_dict(),
            "provenance": self.provenance.to_dict(),
            "confidence": self.confidence,
        }


# ----------------------------------------------------------------------- collection


class EvidenceSet:
    """An ordered, deduplicated collection of evidence with stable citation ids.

    Ids are assigned on insertion and never reassigned. That is the whole point:
    ``[E3]`` must mean the same passage when synthesis writes it and when
    verification checks it. Grouping or re-sorting for display must not disturb them,
    so every view returns a new ordering over the same tagged objects.
    """

    def __init__(self, evidence: list[Evidence] | None = None) -> None:
        self._items: list[Evidence] = []
        self._by_id: dict[str, Evidence] = {}
        self._seen: set[tuple[Any, ...]] = set()
        for item in evidence or []:
            self.add(item)

    # ------------------------------------------------------------------- building

    def add(self, evidence: Evidence) -> Evidence | None:
        """Add one piece of evidence, returning the tagged copy, or None if duplicate."""
        key = evidence.dedup_key()
        if key in self._seen:
            return None
        self._seen.add(key)
        tagged = evidence.with_id(f"E{len(self._items) + 1}")
        self._items.append(tagged)
        self._by_id[tagged.id] = tagged
        return tagged

    def extend(self, evidence: list[Evidence]) -> list[Evidence]:
        """Add many, returning only those actually accepted."""
        return [tagged for item in evidence if (tagged := self.add(item)) is not None]

    # -------------------------------------------------------------------- reading

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._by_id

    @property
    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self._items)

    def get(self, evidence_id: str) -> Evidence | None:
        """Return the evidence carrying ``evidence_id``, or None."""
        return self._by_id.get(evidence_id)

    def by_source(self) -> dict[SourceType, list[Evidence]]:
        """Group by source, preserving insertion order and ids within each group."""
        grouped: dict[SourceType, list[Evidence]] = {}
        for item in self._items:
            grouped.setdefault(item.source_type, []).append(item)
        return grouped

    def source_types(self) -> tuple[SourceType, ...]:
        """Which sources actually contributed, in the order they first appeared."""
        seen: list[SourceType] = []
        for item in self._items:
            if item.source_type not in seen:
                seen.append(item.source_type)
        return tuple(seen)

    def low_confidence(self, floor: float) -> tuple[Evidence, ...]:
        """Evidence that synthesis must qualify rather than assert."""
        return tuple(item for item in self._items if item.is_low_confidence(floor))

    # ------------------------------------------------------------------ rendering

    def render_for_synthesis(self, *, low_confidence_floor: float | None = None) -> str:
        """Render the only view of the evidence that synthesis is ever given.

        Raw tool output never reaches the synthesizer; this string is the boundary.
        Each block is tagged with its id so the model has something concrete to cite,
        and low-confidence blocks are marked inline so the prompt does not have to
        carry that judgement separately.
        """
        if not self._items:
            return "No evidence was retrieved."

        blocks: list[str] = []
        for item in self._items:
            header = f"[{item.id}] {item.label} — {item.cite()}"
            if (
                low_confidence_floor is not None
                and item.is_low_confidence(low_confidence_floor)
            ):
                header += "  ⚠ LOW CONFIDENCE — qualify, do not assert"
            blocks.append(f"{header}\n{item.content.strip()}")
        return "\n\n".join(blocks)

    def serialize(self) -> list[dict[str, Any]]:
        """Serialize for the API and the UI's per-source evidence cards."""
        return [item.to_dict() for item in self._items]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        counts = {source: len(items) for source, items in self.by_source().items()}
        return f"EvidenceSet({len(self._items)} items, {counts})"


__all__ = [
    "SOURCE_LABELS",
    "SOURCE_TYPES",
    "Evidence",
    "EvidenceSet",
    "Locator",
    "LocatorMismatchError",
    "PolicyLocator",
    "Provenance",
    "ScannedLocator",
    "SourceType",
    "SpreadsheetLocator",
    "SqlLocator",
    "content_hash",
]
