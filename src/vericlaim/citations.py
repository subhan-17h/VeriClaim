"""Citation resolution: turning `[En]` markers into a verifiable property.

The synthesizer is instructed to tag every material claim with an evidence id. This
module checks, deterministically and without a model, that each tag actually resolves
against the evidence the answer was built from.

That check is the difference between a system that cites and a system whose citations
mean something. A model asked to cite will happily emit `[E7]` when six pieces of
evidence exist; nothing about the prose reveals it. Resolving the markers is what
converts "does it cite correctly?" from a reviewer's judgement into a computed metric,
and it is also what the C-11 citation precision/recall scorers are built on.

Two entry points, deliberately:

* :func:`resolve_citations` returns a report. The verification node needs to weigh a
  failure against a bounded regeneration, so it needs the detail rather than an
  exception.
* :func:`require_resolvable_citations` raises. Per the project's engineering
  invariants an unresolvable `[En]` is a hard failure, not a warning, and callers
  that cannot act on a report should not be able to ignore one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from vericlaim.evidence import EvidenceSet

#: Matches a well-formed citation marker: [E1], [E12]. Deliberately strict -- a
#: malformed marker such as [E] or [EX] is a defect worth surfacing, not a near-miss
#: worth silently accepting.
CITATION_PATTERN = re.compile(r"\[E(\d+)\]")

#: Matches anything bracket-and-E shaped, so malformed attempts can be reported
#: rather than passing unnoticed as ordinary prose.
_LOOSE_PATTERN = re.compile(r"\[\s*E[^\]]*\]", re.IGNORECASE)


class UnresolvableCitationError(ValueError):
    """An answer cited evidence that does not exist.

    Raised rather than warned because a citation pointing at nothing is
    indistinguishable, to a reader, from a citation pointing at something. Letting it
    through would make every other citation in the answer less trustworthy.
    """

    def __init__(self, unresolved: tuple[str, ...], available: tuple[str, ...]) -> None:
        known = ", ".join(available) if available else "none"
        super().__init__(
            f"Answer cites evidence that does not exist: {', '.join(unresolved)}. "
            f"Available evidence: {known}."
        )
        self.unresolved = unresolved
        self.available = available


@dataclass(frozen=True, slots=True)
class CitationReport:
    """The outcome of checking one answer's citations."""

    #: Ids cited and found, in order of first appearance.
    resolved: tuple[str, ...]
    #: Ids cited but absent from the evidence set.
    unresolved: tuple[str, ...]
    #: Markers that look like citations but are not well formed, e.g. ``[E]``.
    malformed: tuple[str, ...]
    #: Evidence that was collected but never cited. Feeds cross-source completeness
    #: scoring: retrieving from a source and then ignoring it is a real failure mode.
    uncited: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether every citation in the answer points at real evidence."""
        return not self.unresolved and not self.malformed

    @property
    def cited_count(self) -> int:
        return len(self.resolved)

    @property
    def precision(self) -> float:
        """Fraction of citations that resolve. 1.0 when the answer cites nothing."""
        total = len(self.resolved) + len(self.unresolved)
        return 1.0 if total == 0 else len(self.resolved) / total

    @property
    def coverage(self) -> float:
        """Fraction of available evidence the answer actually used."""
        total = len(self.resolved) + len(self.uncited)
        return 1.0 if total == 0 else len(self.resolved) / total


def extract_citations(answer: str) -> tuple[str, ...]:
    """Return every well-formed citation id in ``answer``, first appearance first."""
    seen: list[str] = []
    for match in CITATION_PATTERN.finditer(answer):
        evidence_id = f"E{int(match.group(1))}"
        if evidence_id not in seen:
            seen.append(evidence_id)
    return tuple(seen)


def _malformed_markers(answer: str) -> tuple[str, ...]:
    """Return bracket-E markers that are not valid citations."""
    bad: list[str] = []
    for match in _LOOSE_PATTERN.finditer(answer):
        text = match.group(0)
        if not CITATION_PATTERN.fullmatch(text) and text not in bad:
            bad.append(text)
    return tuple(bad)


def resolve_citations(answer: str, evidence: EvidenceSet) -> CitationReport:
    """Check every citation in ``answer`` against ``evidence``.

    Never raises. Callers that need a hard failure use
    :func:`require_resolvable_citations`.
    """
    cited = extract_citations(answer)
    resolved = tuple(item for item in cited if item in evidence)
    unresolved = tuple(item for item in cited if item not in evidence)
    uncited = tuple(item for item in evidence.ids if item not in resolved)
    return CitationReport(
        resolved=resolved,
        unresolved=unresolved,
        malformed=_malformed_markers(answer),
        uncited=uncited,
    )


def require_resolvable_citations(answer: str, evidence: EvidenceSet) -> CitationReport:
    """Return the report, or raise if any citation fails to resolve."""
    report = resolve_citations(answer, evidence)
    if report.unresolved:
        raise UnresolvableCitationError(report.unresolved, evidence.ids)
    if report.malformed:
        raise UnresolvableCitationError(report.malformed, evidence.ids)
    return report


def render_citation_footer(answer: str, evidence: EvidenceSet) -> str:
    """Render the source list for the citations an answer actually used.

    Only cited evidence appears. Listing everything retrieved would imply the answer
    rests on material it never used, which is the kind of quiet overstatement this
    system exists to avoid.
    """
    report = resolve_citations(answer, evidence)
    if not report.resolved:
        return ""
    lines = ["Sources"]
    for evidence_id in report.resolved:
        item = evidence.get(evidence_id)
        if item is not None:
            lines.append(f"  [{item.id}] {item.label} — {item.cite()}")
    return "\n".join(lines)


__all__ = [
    "CITATION_PATTERN",
    "CitationReport",
    "UnresolvableCitationError",
    "extract_citations",
    "render_citation_footer",
    "require_resolvable_citations",
    "resolve_citations",
]
