"""The HTTP surface over one question.

Transport only. The events come from ``stream_question``; this module serializes them
and adds the one thing a connection needs that a run does not -- a keepalive.

The endpoints are declared ``def`` rather than ``async def`` on purpose. Everything
beneath them blocks: psycopg, Chroma, Ollama and the provider SDKs. Declaring them
async would hold the event loop for the length of a run.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from starlette.responses import StreamingResponse

from vericlaim.api.protocol import Error, Event, Final
from vericlaim.orchestrator.tools import open_tools

PING: dict[str, Any] = {"event": "ping"}
PING_INTERVAL_S = 10.0

# Sentinel closing the queue the worker thread feeds.
_DONE = object()


class AskRequest(BaseModel):
    """One question. Rejected here if blank, before anything is built for it."""

    question: str

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A run needs a question to answer")
        return value.strip()


def _ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str) + "\n"


def _default_run(question: str) -> Iterator[Event]:
    """Run a real question, with a gateway and tools scoped to this request.

    The gateway is per-request because its ledger is what the reported cost is read
    from, and a shared one would bill every question for every other. The pool is
    injected explicitly: ``build_tools`` treats an absent database as one it owns, and
    would close the process-wide pool when this request's tools were released.
    """
    from vericlaim.config import get_settings
    from vericlaim.gateway.core import Gateway
    from vericlaim.orchestrator.graph import build_graph, stream_question
    from vericlaim.orchestrator.sources import load_capabilities
    from vericlaim.sql.db import default_database

    settings = get_settings()
    gateway = Gateway(settings=settings)
    capabilities = load_capabilities()

    with open_tools(
        settings=settings,
        gateway=gateway,
        database=default_database(readonly=True, settings=settings),
    ) as tools:
        graph = build_graph(
            tools=tools.registry(), capabilities=capabilities, gateway=gateway
        )
        yield from stream_question(
            graph, question, gateway=gateway, config={"recursion_limit": 40}
        )


def _events(run: Callable[..., Iterator[Event]], question: str) -> Iterator[str]:
    """Serialize a run's events, emitting a keepalive whenever it goes quiet.

    The run is driven on a worker thread because a run can go quiet for long enough that
    silence is indistinguishable from a finished stream. A dropped connection and a
    crashed run look identical to a client, so a failure is reported as an error event
    and a clean end rather than as a truncated response.
    """
    channel: queue.Queue[Any] = queue.Queue()

    def drive() -> None:
        try:
            for event in run(question):
                channel.put(event)
        except Exception as exc:  # noqa: BLE001 - reported to the client verbatim
            channel.put(Error(message=str(exc)))
        finally:
            channel.put(_DONE)

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()

    while True:
        try:
            item = channel.get(timeout=PING_INTERVAL_S)
        except queue.Empty:
            yield _ndjson(PING)
            continue
        if item is _DONE:
            return
        yield _ndjson(item.to_json())


def create_app(run: Callable[..., Iterator[Event]] | None = None) -> FastAPI:
    """Build the application. ``run`` is injectable so the transport is testable alone."""
    execute = run if run is not None else _default_run
    application = FastAPI(title="VeriClaim")

    @application.post("/api/ask/stream")
    def ask_stream(request: Annotated[AskRequest, Body()]) -> StreamingResponse:
        return StreamingResponse(
            _events(execute, request.question), media_type="application/x-ndjson"
        )

    @application.post("/api/ask")
    def ask(request: Annotated[AskRequest, Body()]) -> dict[str, Any]:
        final: Final | None = None
        try:
            for event in execute(request.question):
                if isinstance(event, Final):
                    final = event
        except Exception as exc:  # noqa: BLE001 - reported to the caller verbatim
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if final is None:
            raise HTTPException(
                status_code=500, detail="The run produced no answer to return"
            )
        return final.payload

    return application


app = create_app()

__all__ = ["AskRequest", "PING", "app", "create_app"]
