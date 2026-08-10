"""Shared gateway vocabulary: messages, results, and the error taxonomy.

Kept in its own module so providers and the gateway core can both import it without
a cycle, and so the error taxonomy has one definition rather than one per provider.

The taxonomy is the important part. Each provider translates its own SDK exceptions
into :class:`TransientProviderError` or :class:`PermanentProviderError`, which is what
lets the fallback ladder stay provider-agnostic: it retries transients against the same
model and walks to the next model on permanents, without knowing whose SDK raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ImagePart:
    """One inline image attached to a message, for the vision path."""

    data: bytes
    mime_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of a conversation, optionally carrying images."""

    role: Role
    content: str
    images: tuple[ImagePart, ...] = ()


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for a single provider call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class RawCompletion:
    """What a provider returns, before the gateway prices or traces it."""

    text: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class FallbackEvent:
    """One hop down the fallback ladder, recorded for the trace.

    Kept as data rather than a log line because the UI shows fallbacks and the
    evaluation harness counts them.
    """

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str
    reason: str


@dataclass(frozen=True, slots=True)
class Completion:
    """A completed gateway call, with everything needed to trace and bill it."""

    text: str
    task: str
    provider: str
    model: str
    usage: Usage
    cost_usd: float
    latency_ms: float
    attempts: int = 1
    fallbacks: tuple[FallbackEvent, ...] = ()
    parsed: Any = None

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallbacks)


@dataclass
class UsageLedger:
    """Running total of spend across many calls.

    A request-scoped ledger is what turns per-call accounting into the per-question
    cost figure the API and the evaluation report both need.
    """

    calls: list[Completion] = field(default_factory=list)

    def record(self, completion: Completion) -> Completion:
        self.calls.append(completion)
        return completion

    @property
    def total_cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(call.usage.input_tokens for call in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(call.usage.output_tokens for call in self.calls)

    @property
    def total_latency_ms(self) -> float:
        return sum(call.latency_ms for call in self.calls)

    @property
    def fallback_events(self) -> list[FallbackEvent]:
        return [event for call in self.calls for event in call.fallbacks]

    def by_task(self) -> dict[str, dict[str, float | int]]:
        """Return per-task totals, for the UI's cost panel and the eval report."""
        summary: dict[str, dict[str, float | int]] = {}
        for call in self.calls:
            entry = summary.setdefault(
                call.task,
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            )
            entry["calls"] = int(entry["calls"]) + 1
            entry["input_tokens"] = int(entry["input_tokens"]) + call.usage.input_tokens
            entry["output_tokens"] = (
                int(entry["output_tokens"]) + call.usage.output_tokens
            )
            entry["cost_usd"] = float(entry["cost_usd"]) + call.cost_usd
        return summary


class GatewayError(RuntimeError):
    """Base class for every gateway failure."""


class ProviderError(GatewayError):
    """A provider call failed."""

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class TransientProviderError(ProviderError):
    """A failure worth retrying against the same model.

    Connection resets, timeouts, rate limits, and 5xx responses.
    """


class PermanentProviderError(ProviderError):
    """A failure that retrying the same model will not fix.

    Authentication, malformed requests, unsupported capabilities, content filtering.
    The ladder walks to the next model instead of burning retries.
    """


class ProviderUnavailableError(PermanentProviderError):
    """The provider is not configured, typically a missing API key."""


class AllProvidersFailedError(GatewayError):
    """Every model in the ladder failed.

    Carries the per-model failures so the trace explains what was actually tried,
    rather than reporting only the last error.
    """

    def __init__(self, task: str, failures: list[tuple[str, str, Exception]]) -> None:
        detail = "; ".join(
            f"{provider}/{model}: {error}" for provider, model, error in failures
        )
        super().__init__(f"All models failed for task {task!r}: {detail}")
        self.task = task
        self.failures = failures


class StructuredOutputError(GatewayError):
    """The model returned content that is not valid JSON for the requested schema."""


class PaidFallbackBlockedError(GatewayError):
    """Every remaining rung of the ladder is a paid provider, and paid use is off.

    Raised instead of silently spending. Gemini's free tier reports exhaustion as
    HTTP 429, which is a genuinely transient error, so without this guard a routine
    free-quota overrun would retry, fall through to a billed provider, and start
    costing money with no signal that anything had changed.
    """

    def __init__(self, task: str, blocked: list[str]) -> None:
        super().__init__(
            f"Task {task!r} exhausted its free models and the remaining fallbacks are "
            f"paid ({', '.join(blocked)}). Set VC_ALLOW_PAID_FALLBACK=true to permit "
            "billed providers."
        )
        self.task = task
        self.blocked = blocked


class BudgetExceededError(GatewayError):
    """A call would push spend past a configured ceiling, so it was not made.

    Checked before the call rather than after, so the ceiling is a bound on actual
    spend rather than a report of having passed it.
    """

    def __init__(self, scope: str, spent: float, ceiling: float) -> None:
        super().__init__(
            f"{scope} budget exhausted: ${spent:.4f} spent against a ${ceiling:.2f} "
            "ceiling. Raise VC_MAX_COST_USD_TOTAL / VC_MAX_COST_USD_PER_REQUEST to continue."
        )
        self.scope = scope
        self.spent = spent
        self.ceiling = ceiling


class QuotaExhaustedError(ProviderError):
    """A model's free-tier daily allowance is spent.

    A ProviderError subclass so the ladder treats it as "this model is done" and moves
    on. Deliberately not transient: the quota resets at midnight Pacific, and no
    retry budget can outlast that.
    """

    def __init__(self, message: str, *, provider: str, model: str, resets_at: str = "") -> None:
        super().__init__(message, provider=provider, model=model)
        self.resets_at = resets_at
