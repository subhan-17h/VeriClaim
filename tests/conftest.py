"""Shared gateway test fixtures.

A scripted fake provider lets us assert on retry counts, cost arithmetic, and the
fallback ladder deterministically, with no network and no API keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.config import ModelRouting, ModelSpec
from vericlaim.gateway import providers as providers_mod
from vericlaim.gateway.types import Message, RawCompletion, Usage


@dataclass
class ScriptedProvider:
    """A provider whose behaviour per model is scripted in advance.

    ``script`` maps a model name to a list of outcomes consumed in order. An outcome
    is either an Exception (raised) or a string (returned as the completion text).
    When a model's script is exhausted the last outcome repeats, which keeps
    "always fails" and "always succeeds" cases short to write.
    """

    name: str
    script: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    default_usage: Usage = field(default_factory=lambda: Usage(1000, 500))
    _cursor: dict[str, int] = field(default_factory=dict)

    def complete(
        self,
        spec: ModelSpec,
        messages: list[Message],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> RawCompletion:
        self.calls.append((self.name, spec.model))
        outcomes = self.script.get(spec.model, ["ok"])
        index = min(self._cursor.get(spec.model, 0), len(outcomes) - 1)
        self._cursor[spec.model] = index + 1
        outcome = outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return RawCompletion(text=outcome, usage=self.default_usage)

    @property
    def models_called(self) -> list[str]:
        return [model for _, model in self.calls]


# paid=False throughout: ModelSpec fails closed, so unmarked specs would be refused
# by the paid-fallback guard. These fixtures model free providers.
PRIMARY = ModelSpec(
    provider="alpha",
    model="alpha-main",
    usd_per_1m_input=2.0,
    usd_per_1m_output=10.0,
    timeout_s=5.0,
    paid=False,
)
SECONDARY = ModelSpec(
    provider="beta",
    model="beta-backup",
    usd_per_1m_input=0.5,
    usd_per_1m_output=1.0,
    timeout_s=5.0,
    paid=False,
)
TERTIARY = ModelSpec(
    provider="alpha",
    model="alpha-small",
    usd_per_1m_input=0.1,
    usd_per_1m_output=0.2,
    timeout_s=5.0,
    paid=False,
)


@pytest.fixture
def routing() -> ModelRouting:
    """A two-provider routing table with a two-hop ladder on the strong tier."""
    return ModelRouting(
        tiers={"strong": PRIMARY, "cheap": TERTIARY},
        tasks={"synthesize": "strong", "route": "cheap"},
        fallbacks={"strong": (SECONDARY, TERTIARY)},
        transient_retries=2,
        transient_backoff_s=0.0,  # no real sleeping in tests
    )


@pytest.fixture
def alpha(monkeypatch) -> ScriptedProvider:
    provider = ScriptedProvider(name="alpha")
    monkeypatch.setitem(providers_mod._REGISTRY, "alpha", provider)
    return provider


@pytest.fixture
def beta(monkeypatch) -> ScriptedProvider:
    provider = ScriptedProvider(name="beta")
    monkeypatch.setitem(providers_mod._REGISTRY, "beta", provider)
    return provider
