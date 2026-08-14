"""The endpoints are transport. They must not invent, drop or reorder a run's events."""

from __future__ import annotations

import json
import threading
from typing import Any

from fastapi.testclient import TestClient

from vericlaim.api.app import create_app
from vericlaim.api.protocol import EVENT_NAMES, Final, RunStarted


class StubRun:
    """Stands in for stream_question: yields fixed events, or raises."""

    def __init__(self, events: list[Any] | None = None, error: Exception | None = None):
        self.events = events or []
        self.error = error

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        yield from self.events
        if self.error is not None:
            raise self.error


def _client(stub: StubRun) -> TestClient:
    return TestClient(create_app(run=stub))


def _lines(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _final(cost: float = 1.0) -> Final:
    return Final(payload={"answer": "an answer", "cost_usd": cost, "trace_id": "t1"})


def test_default_run_injects_the_shared_database(monkeypatch) -> None:
    import vericlaim.api.app as module

    settings = object()
    gateway = object()
    database = object()
    graph = object()
    tool_kwargs: dict[str, Any] = {}
    stream_kwargs: dict[str, Any] = {}

    class ToolScope:
        def __enter__(self) -> ToolScope:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def registry(self) -> dict[str, Any]:
            return {}

    def fake_open_tools(**kwargs: Any) -> ToolScope:
        tool_kwargs.update(kwargs)
        return ToolScope()

    def fake_stream_question(*args: Any, **kwargs: Any) -> Any:
        stream_kwargs.update(kwargs)
        return iter(())

    monkeypatch.setattr(module, "open_tools", fake_open_tools)
    monkeypatch.setattr("vericlaim.config.get_settings", lambda: settings)
    monkeypatch.setattr("vericlaim.gateway.core.Gateway", lambda **kwargs: gateway)
    monkeypatch.setattr("vericlaim.orchestrator.graph.build_graph", lambda **kwargs: graph)
    monkeypatch.setattr(
        "vericlaim.orchestrator.graph.stream_question", fake_stream_question
    )
    monkeypatch.setattr("vericlaim.orchestrator.sources.load_capabilities", lambda: {})
    monkeypatch.setattr(
        "vericlaim.sql.db.default_database", lambda **kwargs: database
    )

    list(module._default_run("q"))

    assert tool_kwargs["database"] is database
    assert stream_kwargs["config"] == {"recursion_limit": 40}


def test_a_stream_ends_with_exactly_one_final() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final()])

    response = _client(stub).post("/api/ask/stream", json={"question": "q"})
    events = _lines(response)

    assert response.status_code == 200
    assert [event["event"] for event in events] == ["run_started", "final"]


def test_a_failure_mid_run_becomes_one_error_event_not_a_dropped_stream() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q")], error=RuntimeError("boom"))

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    assert [event["event"] for event in events] == ["run_started", "error"]
    assert events[-1]["message"] == "boom"


def test_every_streamed_event_name_is_in_the_protocol_or_is_the_keepalive() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final()])

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    for event in events:
        assert event["event"] in EVENT_NAMES | {"ping"}


def test_ask_returns_the_final_payload_as_one_object() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q"), _final(cost=42.0)])

    response = _client(stub).post("/api/ask", json={"question": "q"})

    assert response.status_code == 200
    assert response.json()["cost_usd"] == 42.0
    assert response.json()["answer"] == "an answer"


def test_ask_reports_a_failed_run_rather_than_returning_an_answer() -> None:
    stub = StubRun([RunStarted(trace_id="t1", question="q")], error=RuntimeError("boom"))

    response = _client(stub).post("/api/ask", json={"question": "q"})

    assert response.status_code == 500
    assert "boom" in response.json()["detail"]


def test_a_blank_question_is_refused() -> None:
    stub = StubRun([_final()])

    response = _client(stub).post("/api/ask", json={"question": "   "})

    assert response.status_code == 422


def test_both_routes_agree_on_the_same_run() -> None:
    events = [RunStarted(trace_id="t1", question="q"), _final(cost=7.0)]

    streamed = _lines(_client(StubRun(events)).post("/api/ask/stream", json={"question": "q"}))
    awaited = _client(StubRun(events)).post("/api/ask", json={"question": "q"}).json()

    final = next(event for event in streamed if event["event"] == "final")
    assert final["answer"] == awaited["answer"]
    assert final["cost_usd"] == awaited["cost_usd"]


def test_the_keepalive_fires_while_a_run_is_quiet(monkeypatch) -> None:
    import vericlaim.api.app as module

    monkeypatch.setattr(module, "PING_INTERVAL_S", 0.01)
    ready = threading.Event()

    class SlowRun(StubRun):
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            yield RunStarted(trace_id="t1", question="q")
            ready.wait(0.2)
            yield _final()

    events = _lines(_client(SlowRun()).post("/api/ask/stream", json={"question": "q"}))

    assert any(event["event"] == "ping" for event in events)
    assert [event["event"] for event in events if event["event"] != "ping"] == [
        "run_started",
        "final",
    ]


def test_the_trace_id_and_the_executed_sql_reach_the_client() -> None:
    # The trace id is how a run in the UI is matched to a run in the tracing backend, and
    # the executed SQL is the claim's audit trail. Neither is derivable downstream, so
    # both are asserted at the boundary rather than assumed.
    payload = {
        "answer": "an answer",
        "cost_usd": 1.0,
        "trace_id": "trace-abc",
        "evidence": [
            {
                "id": "E1",
                "source_type": "sql",
                "locator": {
                    "tables": ["example_table"],
                    "executed_sql": "SELECT 1",
                    "row_count": 1,
                },
            }
        ],
    }
    stub = StubRun([RunStarted(trace_id="trace-abc", question="q"), Final(payload=payload)])

    response = _client(stub).post("/api/ask", json={"question": "q"})
    body = response.json()

    assert body["trace_id"] == "trace-abc"
    assert body["evidence"][0]["locator"]["executed_sql"] == "SELECT 1"


def test_the_first_streamed_event_carries_the_same_trace_as_the_final() -> None:
    payload = {"answer": "an answer", "cost_usd": 1.0, "trace_id": "trace-abc"}
    stub = StubRun([RunStarted(trace_id="trace-abc", question="q"), Final(payload=payload)])

    events = _lines(_client(stub).post("/api/ask/stream", json={"question": "q"}))

    assert events[0]["trace_id"] == events[-1]["trace_id"] == "trace-abc"


class TestTheSpaMount:
    """The API must run from a checkout that has never been built.

    StaticFiles raises when its directory is absent, so mounting unconditionally would
    make an unbuilt clone fail to import -- the same "declared thing that fails" that
    C-8.13 deleted two entry points for.
    """

    def test_the_api_works_when_the_frontend_was_never_built(self, tmp_path) -> None:
        app = create_app(run=StubRun([_final()]), dist=tmp_path / "absent")

        response = TestClient(app).post("/api/ask", json={"question": "is it covered?"})

        assert response.status_code == 200
        assert response.json()["answer"] == "an answer"

    def test_the_built_spa_is_served_at_the_root(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>VeriClaim</title>")

        app = create_app(run=StubRun([_final()]), dist=dist)
        response = TestClient(app).get("/")

        assert response.status_code == 200
        assert "VeriClaim" in response.text

    def test_the_mount_never_shadows_the_api(self, tmp_path) -> None:
        """Registered last, so a catch-all at / cannot swallow /api routes."""
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>VeriClaim</title>")

        app = create_app(run=StubRun([_final()]), dist=dist)
        response = TestClient(app).post("/api/ask", json={"question": "is it covered?"})

        assert response.status_code == 200
        assert response.json()["answer"] == "an answer"
