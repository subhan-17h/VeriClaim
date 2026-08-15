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
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.responses import StreamingResponse

from vericlaim.api.protocol import Error, Event, Final
from vericlaim.api.sources import SourceCatalog, build_router
from vericlaim.config import PROJECT_ROOT, get_settings
from vericlaim.orchestrator.tools import open_tools

#: A run must be closeable, not merely iterable: closing it is how cancellation
#: reaches the graph and releases the tools a run holds.
type Runner = Callable[..., Generator[Event, None, None]]

PING: dict[str, Any] = {"event": "ping"}
PING_INTERVAL_S = 10.0

#: Where ``npm run build`` writes. Absent until the frontend has been built, which is
#: the normal state of a fresh checkout and of any machine without Node.
SPA_DIST = PROJECT_ROOT / "frontend" / "dist"

# Sentinel closing the queue the worker thread feeds.
_DONE = object()


class AskRequest(BaseModel):
    """One question. Rejected here if blank, before anything is built for it."""

    question: str
    #: Minted by the client so it can cancel this run before the first event names a
    #: trace. Optional: a client that cannot cancel must still be served.
    run_id: str | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A run needs a question to answer")
        return value.strip()


class RunRegistry:
    """The runs a client can still stop, and how to stop them.

    A disconnect cannot carry this. Starlette drives a sync stream through
    ``iterate_in_threadpool``, which never closes the iterator it wraps, so a hung-up
    connection releases a run only when the cyclic collector reaches it -- measured at
    over 15 seconds at a realistic event pace. A stop that leaves paid model calls
    running for an answer nobody will read is not a stop, so the client says so
    explicitly and this is where that reaches the run.
    """

    def __init__(self) -> None:
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def open(self, run_id: str | None) -> threading.Event:
        """Hand back this run's stop flag, registering it when it can be named."""
        stop = threading.Event()
        if run_id is None:
            return stop
        with self._lock:
            self._stops[run_id] = stop
        return stop

    def cancel(self, run_id: str) -> bool:
        """Trip a live run's stop flag. False when no such run is running."""
        with self._lock:
            stop = self._stops.get(run_id)
        if stop is None:
            return False
        stop.set()
        return True

    def close(self, run_id: str | None) -> None:
        """Forget a finished run, so the registry does not grow for the process's life."""
        if run_id is None:
            return
        with self._lock:
            self._stops.pop(run_id, None)


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


def _events(
    run: Runner,
    question: str,
    stop: threading.Event | None = None,
    on_end: Callable[[], None] | None = None,
) -> Iterator[str]:
    """Serialize a run's events, emitting a keepalive whenever it goes quiet.

    The run is driven on a worker thread because a run can go quiet for long enough that
    silence is indistinguishable from a finished stream. A dropped connection and a
    crashed run look identical to a client, so a failure is reported as an error event
    and a clean end rather than as a truncated response.

    ``stop`` is what a cancelling client trips. The worker reads it between events, so
    the node in flight finishes and nothing after it starts. Closing this generator
    trips the same flag, which is the backstop for a client that vanished without
    cancelling -- unreliable in timing, which is why it is not the mechanism.

    ``on_end`` fires when the run stops, on the worker's thread. It hangs off the run
    rather than off this generator because a client can hang up without this generator
    ever being finalized, and a run that has ended must stop being cancellable then.
    """
    channel: queue.Queue[Any] = queue.Queue()
    stop = threading.Event() if stop is None else stop

    def drive() -> None:
        events = run(question)
        try:
            for event in events:
                if stop.is_set():
                    break
                channel.put(event)
        except Exception as exc:  # noqa: BLE001 - reported to the client verbatim
            channel.put(Error(message=str(exc)))
        finally:
            # Closing a generator suspended at its yield raises GeneratorExit inside
            # it, which unwinds the run's `with open_tools(...)` and releases the
            # database pool. This is why a run must be a generator, not any iterator.
            events.close()
            channel.put(_DONE)
            if on_end is not None:
                on_end()

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()

    try:
        while True:
            try:
                item = channel.get(timeout=PING_INTERVAL_S)
            except queue.Empty:
                yield _ndjson(PING)
                continue
            if item is _DONE:
                return
            yield _ndjson(item.to_json())
    finally:
        stop.set()


def _stream(
    run: Runner, question: str, run_id: str | None, registry: RunRegistry
) -> Iterator[str]:
    """Serialize one run, cancellable by name for exactly as long as it is running."""
    stop = registry.open(run_id)
    yield from _events(run, question, stop, lambda: registry.close(run_id))


def create_app(
    run: Runner | None = None,
    dist: Path | None = None,
    catalog: SourceCatalog | None = None,
) -> FastAPI:
    """Build the application. ``run`` is injectable so the transport is testable alone.

    ``dist`` is injectable so both sides of the SPA mount can be tested without a Node
    build ever having happened.
    """
    execute = run if run is not None else _default_run
    application = FastAPI(title="VeriClaim")
    # Per application rather than per module: two apps in one process (the test suite
    # builds many) must not be able to cancel each other's runs.
    registry = RunRegistry()

    @application.post("/api/ask/stream")
    def ask_stream(request: Annotated[AskRequest, Body()]) -> StreamingResponse:
        return StreamingResponse(
            _stream(execute, request.question, request.run_id, registry),
            media_type="application/x-ndjson",
        )

    @application.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, bool]:
        if not registry.cancel(run_id):
            raise HTTPException(
                status_code=404, detail=f"No run '{run_id}' is running to cancel"
            )
        return {"cancelled": True}

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

    # The source routes are registered with the rest of /api, and before the SPA mount
    # for the same reason: a catch-all at "/" would otherwise swallow them.
    sources = (
        catalog if catalog is not None else SourceCatalog.from_settings(get_settings())
    )
    application.include_router(build_router(sources))

    # Mounted last, and only when built. StaticFiles raises on a missing directory, so
    # an unconditional mount would stop the API importing in any checkout that has not
    # run `npm run build` -- including every machine without Node. Registering after
    # the routes is what stops a catch-all at "/" swallowing /api.
    spa = SPA_DIST if dist is None else dist
    if (spa / "index.html").is_file():
        application.mount("/", StaticFiles(directory=spa, html=True), name="spa")

    return application


app = create_app()

__all__ = ["AskRequest", "PING", "RunRegistry", "app", "create_app"]
