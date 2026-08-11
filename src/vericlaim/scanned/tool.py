"""The scanned source's tool boundary: a question in, page-cited ``Evidence`` out.

Retrieval is shared with the policy source -- same collection, same hybrid search,
same reranker -- so this module supplies only what makes a scan different from a
wording, and all of that difference is about trust.

A policy clause is quoted exactly as the document reads; its evidence is full
confidence, because there is nothing to qualify. A scanned page is a *reading* of an
image, and how good that reading was is a property of the page. So a scan's evidence
carries its OCR score as its confidence, which is what lets synthesis qualify a claim
drawn from a smeared inspection report instead of asserting it, and lets the citation
tell a reader which pages to go and check.

Two related refusals live here:

* A chunk with no recognition score is treated as unverified, not as trustworthy. Not
  knowing how well a page was read is a reason to qualify a claim, not to assert it.
* A scanned chunk with no page number fails loudly. Every other coordinate a reader
  could use -- clause, section, row -- is absent from an inspection report, so a scan
  cited without a page is evidence nobody can go and check.
"""

from __future__ import annotations

from typing import Any

from vericlaim.config import get_settings
from vericlaim.evidence import Evidence, Provenance, ScannedLocator
from vericlaim.policy.models import RetrievalSourceType, RetrievedChunk
from vericlaim.policy.store import ChunkStore
from vericlaim.policy.tool import ChunkSearcher
from vericlaim.tracing import traced

__all__ = ("ScannedSearcher", "search_scanned")

TOOL_NAME = "search_scanned"

# The confidence given to a scanned chunk carrying no recognition score. Zero rather
# than one: an unrecorded score means nobody measured how well the page was read, and
# the safe reading of "unknown" is the one that makes synthesis qualify the claim.
UNSCORED_CONFIDENCE = 0.0


class ScannedSearcher(ChunkSearcher):
    """Searches OCR'd claim paperwork and cites it by page."""

    source_type: RetrievalSourceType = "scanned_pdf"
    tool_name = TOOL_NAME

    @traced(name=TOOL_NAME, run_type="tool")
    def search(
        self,
        query: str,
        *,
        claim_id: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        """Return scanned evidence answering ``query``, most relevant first.

        ``claim_id`` scopes the search to one matter, which is the usual way this tool
        is called: paperwork from another claim is not merely irrelevant to a question
        about this one, it is misleading. The reference is matched against the one
        recorded from each document's path at index time, never against its
        recognised text.
        """
        scoped = dict(filters or {})
        if claim_id is not None:
            scoped["claim_id"] = claim_id
        return self._search(query, filters=scoped, limit=limit, trace_id=trace_id)

    def _to_evidence(self, result: RetrievedChunk, provenance: Provenance) -> Evidence:
        """Convert one retrieved page of a scan into citable evidence."""
        chunk = result.chunk
        if chunk.page is None:
            raise ValueError(
                f"Scanned chunk {chunk.id} carries no page number and cannot be "
                "cited. A scan has no clause or section to fall back on, so evidence "
                "from it would be unverifiable. This is an indexing fault, not a "
                "retrieval one."
            )

        confidence = (
            chunk.ocr_confidence
            if chunk.ocr_confidence is not None
            else UNSCORED_CONFIDENCE
        )
        return Evidence(
            source_type=chunk.source_type,
            source_id=chunk.doc_id,
            content=chunk.text,
            locator=ScannedLocator(
                document=chunk.doc_name,
                page=chunk.page,
                ocr_confidence=confidence,
                ocr_engine=chunk.ocr_engine,
                escalated=chunk.escalated,
            ),
            provenance=provenance,
            confidence=confidence,
        )


def search_scanned(
    query: str,
    *,
    claim_id: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    trace_id: str | None = None,
) -> list[Evidence]:
    """Search the indexed scanned paperwork and return page-cited evidence.

    The entry point the orchestrator calls. It builds its dependencies from settings;
    anything that wants to inject them should construct a :class:`ScannedSearcher`.

    Deliberately not traced itself -- ``ScannedSearcher.search`` already emits the
    span, and a delegating wrapper would nest a second one for the same tool call.
    """
    settings = get_settings()
    searcher = ScannedSearcher(
        ChunkStore(path=settings.chroma_dir, collection_name=settings.collection_name),
        settings=settings,
    )
    return searcher.search(
        query, claim_id=claim_id, filters=filters, limit=limit, trace_id=trace_id
    )
