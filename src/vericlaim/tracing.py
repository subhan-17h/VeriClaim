"""LangSmith tracing, applied as a thin decorator over ordinary functions.

Tracing is a required property of this system: the routing decision, the plan, every
tool call, the evidence returned, retries, verification, and the final citations must
all be inspectable after the fact. Instrumenting only the final LLM call would satisfy
the letter of that and none of its purpose.

Two design commitments:

* **Off by default, and genuinely off.** Without ``LANGSMITH_TRACING=true`` and a key,
  ``@traced`` calls the wrapped function directly. Not a stub that swallows errors, not
  a no-op span object -- the original function, so traced and untraced runs cannot
  diverge in behaviour. Tests and offline development need no credentials.
* **Decided per call, not at import.** The env is read at call time so enabling tracing
  does not depend on import order, and tests can toggle it with monkeypatch.

The decorator is deliberately usable on plain functions. Nothing here requires the
callable to be a LangGraph node, so the NL2SQL subsystem -- which is plain Python by
design -- traces exactly like the graph does.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Run types LangSmith renders distinctly. "tool" and "llm" get special treatment in
# the UI, which is worth using: a reader should see at a glance which spans hit an
# external system and which were pure orchestration.
RunType = str


def is_tracing_enabled() -> bool:
    """Return whether tracing should be active for the current call.

    Requires both the flag and a key. A flag without a key would make every span
    attempt a network call that cannot succeed.
    """
    flag = os.environ.get(
        "LANGSMITH_TRACING", os.environ.get("VC_LANGSMITH_TRACING", "")
    )
    if flag.strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return bool(os.environ.get("LANGSMITH_API_KEY", "").strip())


def traced(
    name: str | None = None,
    *,
    run_type: RunType = "chain",
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Callable[[F], F]:
    """Trace a function as one LangSmith span when tracing is enabled.

    Usage::

        @traced("route", run_type="chain", tags=["orchestrator"])
        def route(state): ...

    When tracing is off the wrapped function is invoked directly, so this decorator
    is safe to apply everywhere without conditioning on the environment.
    """

    def decorator(func: F) -> F:
        span_name = name or func.__name__
        # Built on first traced call, then reused: constructing the wrapper is not free
        # and most runs never trace at all.
        #
        # Keyed on the module that built it, not merely "built once". The wrapper closes
        # over the ``traceable`` that made it, so a single-slot cache goes on serving a
        # span from a langsmith that is no longer the imported one -- and whichever
        # module happened to be there for the first traced call in the process wins for
        # the rest of it. That is invisible in production, where the module never
        # changes, and it silently defeats every attempt to substitute one.
        cache: dict[Any, Callable[..., Any]] = {}

        def _traceable() -> Callable[..., Any]:
            import langsmith

            if langsmith not in cache:
                cache[langsmith] = langsmith.traceable(
                    run_type=run_type,
                    name=span_name,
                    metadata=metadata or {},
                    tags=tags or [],
                )(func)
            return cache[langsmith]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_tracing_enabled():
                return func(*args, **kwargs)
            return _traceable()(*args, **kwargs)

        wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def add_run_metadata(**fields: Any) -> bool:
    """Attach metadata to the currently active span.

    Returns whether anything was attached, so callers can assert on it in tests.
    Silently does nothing when tracing is off or no span is active -- annotating a
    trace must never be able to break the operation being traced.
    """
    if not is_tracing_enabled():
        return False
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return False
        run.metadata.update(fields)
        return True
    except Exception:  # noqa: BLE001 - tracing must never break the traced call
        return False


def trace_completion(completion: Any) -> bool:
    """Record a gateway call's model, usage, cost, latency, and fallbacks.

    This is what makes the model-gateway decisions visible in the trace rather than
    only in the API response: which model actually served a task, what it cost, and
    whether it was the first choice or a fallback.
    """
    fields: dict[str, Any] = {
        "vc_task": completion.task,
        "vc_provider": completion.provider,
        "vc_model": completion.model,
        "vc_input_tokens": completion.usage.input_tokens,
        "vc_output_tokens": completion.usage.output_tokens,
        "vc_cost_usd": round(completion.cost_usd, 6),
        "vc_latency_ms": round(completion.latency_ms, 2),
        "vc_attempts": completion.attempts,
        "vc_used_fallback": completion.used_fallback,
    }
    if completion.fallbacks:
        fields["vc_fallbacks"] = [
            {
                "from": f"{event.from_provider}/{event.from_model}",
                "to": f"{event.to_provider}/{event.to_model}",
                "reason": event.reason,
            }
            for event in completion.fallbacks
        ]
    return add_run_metadata(**fields)


def is_framework_tracing_enabled() -> bool:
    """Whether LangChain/LangGraph will emit its own run for every node it executes.

    The graph is instrumented twice over by default: LangGraph traces each node it runs,
    and our own ``@traced`` decorator wraps the same functions so they are traced when
    called directly -- from the API, from a test, from the plain-Python NL2SQL path. With
    both active the run tree carries two spans per node, one nested inside the other with
    the same name, and a question that should cost one trace costs ten.

    That matters beyond tidiness: the free LangSmith plan allows 5,000 traces a month,
    and the whole evaluation suite has to fit inside it.

    So the two are told apart here. LangChain reads its own environment variables; when
    those are set it will provide the span, and :func:`without_own_span` steps aside.
    When only ``VC_LANGSMITH_TRACING`` is set the framework stays quiet and our span is
    the only one there is.
    """
    flag = os.environ.get(
        "LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2", "")
    )
    if flag.strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return bool(os.environ.get("LANGSMITH_API_KEY", "").strip())


def without_own_span(fn: Any) -> Any:
    """Return ``fn`` with our tracing layer removed, if it has one.

    Used where something else is already spanning the call. Partials are rebuilt around
    the unwrapped function so injected dependencies survive; anything undecorated is
    returned unchanged.
    """
    if isinstance(fn, functools.partial):
        inner = without_own_span(fn.func)
        if inner is fn.func:
            return fn
        return functools.partial(inner, *fn.args, **fn.keywords)
    return getattr(fn, "__wrapped__", fn)


def configure_tracing(*, project: str | None = None) -> bool:
    """Point tracing at a project. Returns whether tracing is active afterwards."""
    if project:
        os.environ["LANGSMITH_PROJECT"] = project
    return is_tracing_enabled()


__all__ = [
    "add_run_metadata",
    "configure_tracing",
    "is_framework_tracing_enabled",
    "is_tracing_enabled",
    "trace_completion",
    "traced",
    "without_own_span",
]
