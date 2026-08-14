"""What a run reports while it runs, as a closed set of events.

One event per line of NDJSON. C-10's typed event unions are written against this
module, so it is the single place either side learns the shape of a run.

``ping`` is deliberately absent. A keepalive is a property of the connection, not of
the run, and a client that treated one as run information would be reading the network
rather than the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vericlaim.orchestrator.state import GraphState, StageRecord

EVENT_NAMES = frozenset({"run_started", "stage", "evidence", "final", "error"})


@dataclass(frozen=True, slots=True)
class RunStarted:
    """First event of every stream: which run this is, and what it was asked."""

    trace_id: str
    question: str

    def to_json(self) -> dict[str, Any]:
        return {
            "event": "run_started",
            "trace_id": self.trace_id,
            "question": self.question,
        }


@dataclass(frozen=True, slots=True)
class Stage:
    """One graph node, once it has finished."""

    name: str
    model: str
    cost_usd: float
    latency_ms: float
    error: str
    detail: dict[str, Any]

    @classmethod
    def from_record(cls, record: StageRecord) -> Stage:
        return cls(
            name=record.name,
            model=record.model,
            cost_usd=record.cost_usd,
            latency_ms=record.latency_ms,
            error=record.error,
            detail=dict(record.detail),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "event": "stage",
            "name": self.name,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """Evidence a source returned, as soon as it returned it."""

    source: str
    items: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {"event": "evidence", "source": self.source, "items": self.items}


@dataclass(frozen=True, slots=True)
class Final:
    """The finished run. Last event of a stream that succeeded."""

    payload: dict[str, Any]

    @classmethod
    def from_state(cls, state: GraphState, *, cost_usd: float) -> Final:
        """Build the final payload, with the cost supplied rather than derived.

        ``GraphState.total_cost_usd`` sums stage records, and the model calls a source
        tool makes are recorded on no stage. Deriving the cost here would under-report
        every multi-source question, so the caller passes the gateway ledger's figure.
        """
        payload = state.to_dict()
        payload["cost_usd"] = cost_usd
        return cls(payload=payload)

    def to_json(self) -> dict[str, Any]:
        return {"event": "final", **self.payload}


@dataclass(frozen=True, slots=True)
class Error:
    """A run that could not finish. Last event of a stream that failed."""

    message: str

    def to_json(self) -> dict[str, Any]:
        return {"event": "error", "message": self.message}


Event = RunStarted | Stage | EvidenceEvent | Final | Error

__all__ = [
    "EVENT_NAMES",
    "Error",
    "Event",
    "EvidenceEvent",
    "Final",
    "RunStarted",
    "Stage",
]
