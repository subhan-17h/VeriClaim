"""Model gateway - the single door for every LLM call in VeriClaim.

Nothing outside this package may construct an ``OpenAI()`` or ``genai.Client()``.
Routing that through one module is what makes per-task model choice, cost accounting,
and provider fallback properties of the system rather than of each call site.

Typical use::

    from vericlaim.gateway import Gateway

    gateway = Gateway()                                  # per request
    result = gateway.complete_json("route", messages, ROUTE_SCHEMA)
    decision = result.parsed
    print(gateway.ledger.total_cost_usd)

A module-level default is provided for scripts and one-off calls, but request-scoped
work should construct its own :class:`Gateway` so the ledger measures that request.
"""

from __future__ import annotations

from typing import Any

from vericlaim.gateway.core import Gateway
from vericlaim.gateway.providers import (
    GeminiProvider,
    OpenAIProvider,
    Provider,
    available_providers,
    get_provider,
    register_provider,
    strictify_schema,
)
from vericlaim.gateway.types import (
    AllProvidersFailedError,
    Completion,
    FallbackEvent,
    GatewayError,
    ImagePart,
    Message,
    PermanentProviderError,
    ProviderError,
    ProviderUnavailableError,
    StructuredOutputError,
    TransientProviderError,
    Usage,
    UsageLedger,
)

_default: Gateway | None = None


def default_gateway() -> Gateway:
    """Return the process-wide gateway, created on first use."""
    global _default
    if _default is None:
        _default = Gateway()
    return _default


def complete(task: str, messages: Any, *, temperature: float = 0.0) -> Completion:
    """Free-text completion via the default gateway."""
    return default_gateway().complete(task, messages, temperature=temperature)


def complete_json(
    task: str, messages: Any, schema: dict[str, Any], *, temperature: float = 0.0
) -> Completion:
    """Structured completion via the default gateway."""
    return default_gateway().complete_json(
        task, messages, schema, temperature=temperature
    )


def complete_vision(
    task: str,
    prompt: str,
    images: list[ImagePart],
    *,
    schema: dict[str, Any] | None = None,
    temperature: float = 0.0,
) -> Completion:
    """Vision completion via the default gateway."""
    return default_gateway().complete_vision(
        task, prompt, images, schema=schema, temperature=temperature
    )


__all__ = [
    "AllProvidersFailedError",
    "Completion",
    "FallbackEvent",
    "Gateway",
    "GatewayError",
    "GeminiProvider",
    "ImagePart",
    "Message",
    "OpenAIProvider",
    "PermanentProviderError",
    "Provider",
    "ProviderError",
    "ProviderUnavailableError",
    "StructuredOutputError",
    "TransientProviderError",
    "Usage",
    "UsageLedger",
    "available_providers",
    "complete",
    "complete_json",
    "complete_vision",
    "default_gateway",
    "get_provider",
    "register_provider",
    "strictify_schema",
]
