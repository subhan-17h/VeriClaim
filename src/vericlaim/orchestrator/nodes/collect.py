"""Make one body of evidence out of what several sources returned, and say what is missing.

After the fan-out the state holds whatever each branch contributed, in whatever order the
branches were scheduled. This node makes that into something the rest of the run can rely
on. No model is involved; every judgement here is arithmetic over what came back.

**One canonical ordering, ids assigned once.** Evidence is re-laid in routing order, and
within a source in the order that source returned it -- rank order within a source is the
retrieval's own judgement and carries information. Ids are assigned from that ordering, so
``[E1]`` means the same passage on every run of the same question rather than depending on
which branch a thread scheduler reached first. Reassigning ids is safe here and nowhere
after: nothing has cited them yet, and the next node that could is synthesis.

**An explicit account of what is not there.** This is the part that earns the node. Once
the evidence is in one pile, a source that was asked and answered nothing looks exactly
like a source that was never asked; only the plan knows the difference, and only here is
it still known. The account distinguishes:

* *silent* -- asked, reached, returned nothing. Real negative information.
* *failed* -- never reached. Not negative information at all: an answer built without it
  is incomplete, and the reader has to be told which source is missing.
* *low confidence* -- returned, but read off a page the recognizer was unsure of.
  Synthesis must qualify these rather than assert them.

Everything downstream reads that account instead of recomputing it: sufficiency decides
whether to replan from it, synthesis is told what to qualify, and the answer says what it
could not reach.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from vericlaim.evidence import Evidence, EvidenceSet
from vericlaim.orchestrator.state import GraphState, StageRecord
from vericlaim.tracing import traced

STAGE = "collect"

# Kept here rather than imported from the graph, which imports the nodes: the prefix is
# part of the stage vocabulary the state carries, not of the wiring.
SOURCE_STAGE_PREFIX = "source."


@traced(STAGE, run_type="chain", tags=["orchestrator"])
def collect(
    state: GraphState, *, low_confidence_floor: float | None = None
) -> GraphState:
    """Canonicalize the gathered evidence and record what each source did or did not give."""
    if state.routing is None:
        raise ValueError("Collecting needs a routing decision to order the evidence by")

    if low_confidence_floor is None:
        from vericlaim.config import get_settings

        low_confidence_floor = get_settings().ocr_confidence_floor

    ordered = EvidenceSet(_in_routing_order(state.evidence.items, state.routing.sources))

    returned = _returned_per_source(state)
    by_source = {
        source: len(items) for source, items in ordered.by_source().items()
    }
    failed = _failed_sources(state)
    asked = tuple(state.plans.get("sub_goals") or ())
    silent = [
        source
        for source in asked
        if source not in failed and not by_source.get(source)
    ]

    collection: dict[str, Any] = {
        "by_source": by_source,
        "sources_used": list(ordered.source_types()),
        "silent_sources": silent,
        "failed_sources": failed,
        "low_confidence": [
            item.id for item in ordered.low_confidence(low_confidence_floor)
        ],
        # What the tools handed over, minus what survived: a count that shrinks
        # silently is a retrieval problem nobody can see.
        "duplicates_removed": max(0, returned - len(ordered.items)),
    }

    return state.with_(evidence=ordered, collection=collection).with_stage(
        StageRecord(
            name=STAGE,
            detail={
                "evidence": len(ordered.items),
                "by_source": by_source,
                "silent_sources": silent,
                "failed_sources": failed,
            },
        )
    )


def _in_routing_order(
    items: Sequence[Evidence], sources: Sequence[str]
) -> list[Evidence]:
    """Lay the evidence out by source, keeping each source's own order within it.

    Evidence from a source the router did not choose sorts last rather than being
    dropped: it was gathered, and discarding it here would let an answer cite something
    the set no longer holds.
    """
    order = {source: index for index, source in enumerate(sources)}
    unrouted = len(order)
    return sorted(items, key=lambda item: order.get(item.source_type, unrouted))


def _returned_per_source(state: GraphState) -> int:
    """How many pieces of evidence the source branches actually handed over."""
    return sum(
        int(stage.detail.get("evidence", 0))
        for stage in state.stages
        if stage.name.startswith(SOURCE_STAGE_PREFIX) and not stage.failed
    )


def _failed_sources(state: GraphState) -> list[str]:
    return [
        stage.name[len(SOURCE_STAGE_PREFIX) :]
        for stage in state.stages
        if stage.name.startswith(SOURCE_STAGE_PREFIX) and stage.failed
    ]
