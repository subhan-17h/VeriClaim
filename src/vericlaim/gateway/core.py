"""The gateway: the single door through which every LLM call in VeriClaim passes.

Responsibilities, deliberately few:

* route a **task name** to a model, via the table in ``config.yaml``
* run the call, retrying transient failures against the same model
* price it and time it, recording tokens, USD, and latency on every single call
* parse structured output when a schema was requested

Cost accounting is the reason this exists rather than a bare SDK call. unibot routed
per-task correctly but never read ``response.usage``, so it had no idea what anything
cost. Here every call returns a :class:`Completion` carrying its own price, and an
optional :class:`UsageLedger` accumulates them into the per-question figure the API,
the UI, and the evaluation report all report.

Cross-model and cross-provider fallback lives in ``fallback.py`` and is layered on top
of :meth:`Gateway.call_model`, which handles exactly one model.
"""

from __future__ import annotations

import json
import time
from typing import Any

from vericlaim.config import (
    ModelRouting,
    ModelSpec,
    Settings,
    get_model_routing,
    get_settings,
)
from vericlaim.gateway.providers import get_provider
from vericlaim.gateway.quota import RateLimiter, default_limiter
from vericlaim.gateway.types import (
    BudgetExceededError,
    Completion,
    FallbackEvent,
    ImagePart,
    Message,
    StructuredOutputError,
    TransientProviderError,
    UsageLedger,
)
from vericlaim.tracing import trace_completion


def _coerce_messages(messages: str | list[Message] | list[dict[str, Any]]) -> list[Message]:
    """Accept a bare prompt, Message objects, or OpenAI-style dicts."""
    if isinstance(messages, str):
        return [Message(role="user", content=messages)]
    coerced: list[Message] = []
    for entry in messages:
        if isinstance(entry, Message):
            coerced.append(entry)
        else:
            coerced.append(
                Message(
                    role=entry.get("role", "user"),
                    content=entry.get("content", ""),
                    images=tuple(entry.get("images", ())),
                )
            )
    return coerced


_SESSION_LEDGER: UsageLedger | None = None


def _session_ledger() -> UsageLedger:
    """Return the process-wide ledger the total spend ceiling is measured against."""
    global _SESSION_LEDGER
    if _SESSION_LEDGER is None:
        _SESSION_LEDGER = UsageLedger()
    return _SESSION_LEDGER


def reset_session_spend() -> None:
    """Clear accumulated session spend. For tests and long-lived processes."""
    global _SESSION_LEDGER
    _SESSION_LEDGER = UsageLedger()


def _parse_json(text: str, *, task: str) -> Any:
    """Parse structured output, tolerating a fenced code block around it.

    Providers in JSON mode occasionally wrap the object in ``` fences. Stripping them
    is a one-line accommodation; anything still unparseable is a real failure and is
    raised rather than silently returning None.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1] if "\n" in candidate else candidate
        candidate = candidate.rsplit("```", 1)[0].strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as exc:
        preview = text[:200]
        raise StructuredOutputError(
            f"Task {task!r} did not return valid JSON: {exc}. Got: {preview!r}"
        ) from exc


class Gateway:
    """Routes tasks to models, prices every call, and records the result."""

    def __init__(
        self,
        *,
        routing: ModelRouting | None = None,
        ledger: UsageLedger | None = None,
        settings: Settings | None = None,
        session_ledger: UsageLedger | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._routing = routing or get_model_routing()
        self.settings = settings or get_settings()
        self.limiter = limiter if limiter is not None else default_limiter()
        self.ledger = ledger if ledger is not None else UsageLedger()
        # The session ledger spans many requests, so the total ceiling bounds the
        # project rather than each question in isolation. Defaults to the process-wide
        # one so a caller cannot accidentally opt out of the total cap.
        self.session_ledger = (
            session_ledger if session_ledger is not None else _session_ledger()
        )

    @property
    def routing(self) -> ModelRouting:
        return self._routing

    # ------------------------------------------------------------------ single model

    def call_model(
        self,
        spec: ModelSpec,
        messages: list[Message],
        *,
        task: str,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        """Call exactly one model, retrying only transient failures.

        Permanent failures propagate immediately: retrying a malformed request or a
        bad API key against the same model wastes the retry budget that a genuine
        rate limit needs.
        """
        self._check_budget()
        provider = get_provider(spec.provider)
        attempts = 0
        started = time.perf_counter()
        last_transient: Exception | None = None

        for attempt in range(self._routing.transient_retries + 1):
            attempts = attempt + 1
            try:
                # Throttle per attempt, since every attempt is a real request against
                # the provider's quota -- including retries.
                self.limiter.acquire(spec)
                raw = provider.complete(
                    spec,
                    messages,
                    json_schema=json_schema,
                    temperature=temperature,
                )
                break
            except TransientProviderError as exc:
                last_transient = exc
                if attempt == self._routing.transient_retries:
                    raise
                time.sleep(self._routing.transient_backoff_s * (attempt + 1))
        else:  # pragma: no cover - loop always breaks or raises
            raise last_transient or RuntimeError("retry loop ended unexpectedly")

        latency_ms = (time.perf_counter() - started) * 1000
        return Completion(
            text=raw.text,
            task=task,
            provider=spec.provider,
            model=spec.model,
            usage=raw.usage,
            cost_usd=spec.cost_usd(raw.usage.input_tokens, raw.usage.output_tokens),
            latency_ms=latency_ms,
            attempts=attempts,
        )

    # ------------------------------------------------------------------- public API

    def complete(
        self,
        task: str,
        messages: str | list[Message] | list[dict[str, Any]],
        *,
        temperature: float = 0.0,
    ) -> Completion:
        """Run a free-text completion for ``task``."""
        completion = self._run(
            task, _coerce_messages(messages), temperature=temperature
        )
        return self._finish(completion)

    def complete_json(
        self,
        task: str,
        messages: str | list[Message] | list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        temperature: float = 0.0,
    ) -> Completion:
        """Run a structured completion for ``task`` and parse the result.

        The parsed object is on ``completion.parsed``; the raw text remains on
        ``completion.text`` so a failed parse can be inspected in the trace.
        """
        completion = self._run(
            task,
            _coerce_messages(messages),
            temperature=temperature,
            json_schema=schema,
        )
        parsed = _parse_json(completion.text, task=task)
        return self._finish(self._replace_parsed(completion, parsed))

    def complete_vision(
        self,
        task: str,
        prompt: str,
        images: list[ImagePart],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        """Run a vision completion. Used by the OCR confidence-floor escalation."""
        messages = [Message(role="user", content=prompt, images=tuple(images))]
        completion = self._run(
            task, messages, temperature=temperature, json_schema=schema
        )
        if schema is None:
            return self._finish(completion)
        parsed = _parse_json(completion.text, task=task)
        return self._finish(self._replace_parsed(completion, parsed))

    # --------------------------------------------------------------------- internals

    def _run(
        self,
        task: str,
        messages: list[Message],
        *,
        temperature: float,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        """Execute ``task`` down its fallback ladder.

        Deliberately does not record to the ledger: the public methods record after
        parsing, so the ledger entry and the returned object are the same object.
        """
        from vericlaim.gateway.fallback import walk_ladder

        return walk_ladder(
            self,
            task,
            messages,
            temperature=temperature,
            json_schema=json_schema,
        )

    def _check_budget(self) -> None:
        """Refuse a call that would push spend past a ceiling.

        Checked before the call, not after, so the ceiling bounds actual spend rather
        than merely reporting that it was passed. The per-request cap is what catches
        a pathological retry or replan loop inside a single question; the session cap
        bounds the project as a whole.
        """
        per_request = self.settings.max_cost_usd_per_request
        if per_request > 0 and self.ledger.total_cost_usd >= per_request:
            raise BudgetExceededError(
                "Per-request", self.ledger.total_cost_usd, per_request
            )
        total = self.settings.max_cost_usd_total
        if total > 0 and self.session_ledger.total_cost_usd >= total:
            raise BudgetExceededError(
                "Session", self.session_ledger.total_cost_usd, total
            )

    def _finish(self, completion: Completion) -> Completion:
        """Record a completed call to the ledger and annotate the active trace span.

        One place so that ledger and trace can never disagree about what was billed.
        Tracing failures are swallowed inside trace_completion: annotating a trace
        must never break the call it is describing.
        """
        trace_completion(completion)
        # Recorded to both: the request ledger drives the per-question figure the API
        # reports, the session ledger enforces the project-wide ceiling.
        if self.session_ledger is not self.ledger:
            self.session_ledger.record(completion)
        return self.ledger.record(completion)

    @staticmethod
    def _replace_parsed(completion: Completion, parsed: Any) -> Completion:
        """Return a copy of ``completion`` with ``parsed`` attached (it is frozen)."""
        return Completion(
            text=completion.text,
            task=completion.task,
            provider=completion.provider,
            model=completion.model,
            usage=completion.usage,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
            attempts=completion.attempts,
            fallbacks=completion.fallbacks,
            parsed=parsed,
        )

    def with_fallbacks(
        self, completion: Completion, events: list[FallbackEvent]
    ) -> Completion:
        """Attach fallback events to a completion. Used by the fallback layer."""
        return Completion(
            text=completion.text,
            task=completion.task,
            provider=completion.provider,
            model=completion.model,
            usage=completion.usage,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
            attempts=completion.attempts,
            fallbacks=tuple(events),
            parsed=completion.parsed,
        )
