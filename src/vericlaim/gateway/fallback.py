"""The fallback ladder.

When a model fails in a way that retrying it will not fix, the gateway walks to the
next model in that tier's ladder. Because ``config.yaml`` puts a *different provider*
on the next rung, a provider outage degrades the answer's model quality instead of
failing the request. That is the difference between real fallback and a same-provider
retry wearing a different hat.

Every hop is recorded as a :class:`FallbackEvent` and travels back on the completion,
so the trace, the UI, and the evaluation report can all show that a fallback happened
and why. A fallback that nobody can see is indistinguishable from a slow success.

The ladder is provider-agnostic by construction: it branches only on the
transient/permanent taxonomy that ``providers.py`` maps every SDK exception onto.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vericlaim.config import ModelSpec, Settings, get_settings
from vericlaim.gateway.types import (
    AllProvidersFailedError,
    Completion,
    FallbackEvent,
    Message,
    PaidFallbackBlockedError,
    ProviderError,
)

if TYPE_CHECKING:  # pragma: no cover
    from vericlaim.gateway.core import Gateway


def build_ladder(gateway: Gateway, task: str) -> tuple[ModelSpec, ...]:
    """Return the ordered models to try for ``task``: primary first, then fallbacks."""
    routing = gateway.routing
    return (routing.resolve(task), *routing.fallback_chain(task))


def walk_ladder(
    gateway: Gateway,
    task: str,
    messages: list[Message],
    *,
    temperature: float = 0.0,
    json_schema: dict[str, Any] | None = None,
) -> Completion:
    """Try each model in ``task``'s ladder until one succeeds.

    Transient failures are already retried inside :meth:`Gateway.call_model`; by the
    time an exception reaches here that model is considered exhausted, so we move on.

    Raises :class:`AllProvidersFailedError` when the ladder is exhausted. It carries
    every per-model failure rather than only the last, because "gemini timed out and
    then openai rejected the key" is a diagnosable message and "invalid api key" alone
    is not.
    """
    settings: Settings = getattr(gateway, "settings", None) or get_settings()
    full_ladder = build_ladder(gateway, task)

    # Drop paid rungs up front unless billed use is explicitly permitted, so a free
    # tier running dry degrades or refuses rather than quietly starting to spend.
    if settings.allow_paid_fallback:
        ladder = full_ladder
        blocked: list[str] = []
    else:
        ladder = tuple(spec for spec in full_ladder if not spec.paid)
        blocked = [spec.label for spec in full_ladder if spec.paid]

    events: list[FallbackEvent] = []
    failures: list[tuple[str, str, Exception]] = []

    if not ladder:
        raise PaidFallbackBlockedError(
            task, blocked, attempts=failures, events=events
        )

    for index, spec in enumerate(ladder):
        try:
            retry_override = {}
            if index == len(ladder) - 1:
                retry_override["transient_retries"] = (
                    gateway.routing.last_rung_transient_retries
                )
            completion = gateway.call_model(
                spec,
                messages,
                task=task,
                json_schema=json_schema,
                temperature=temperature,
                **retry_override,
            )
        except ProviderError as exc:
            failures.append((spec.provider, spec.model, exc))
            next_spec = ladder[index + 1] if index + 1 < len(ladder) else None
            if next_spec is not None:
                events.append(
                    FallbackEvent(
                        from_provider=spec.provider,
                        from_model=spec.model,
                        to_provider=next_spec.provider,
                        to_model=next_spec.model,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
            continue

        return gateway.with_fallbacks(completion, events) if events else completion

    # Every free rung failed. If paid rungs existed and were withheld, say so --
    # "all providers failed" would hide the fact that a working option was declined
    # on policy, which is a different problem with a different fix.
    if blocked:
        raise PaidFallbackBlockedError(
            task, blocked, attempts=failures, events=events
        )
    raise AllProvidersFailedError(task, failures, events=events)
