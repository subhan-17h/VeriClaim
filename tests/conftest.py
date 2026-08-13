"""Shared gateway test fixtures.

A scripted fake provider lets us assert on retry counts, cost arithmetic, and the
fallback ladder deterministically, with no network and no API keys.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from vericlaim.config import ModelRouting, ModelSpec, get_settings
from vericlaim.gateway import providers as providers_mod
from vericlaim.gateway.types import Message, RawCompletion, Usage
from vericlaim.policy.embeddings import DOCUMENT_PREFIX, QUERY_PREFIX


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


@pytest.fixture(autouse=True)
def no_live_tracing(monkeypatch):
    """Keep the suite off LangSmith.

    ``vericlaim.config`` loads ``.env`` into the process environment at import, so that
    the provider adapters and the tracing wrapper can read credentials from
    ``os.environ`` instead of from a settings object that gets logged. The side effect
    is that a developer with LangSmith configured runs the entire offline suite with
    tracing live: every graph test posts a real run tree, and the free plan's 5,000
    traces a month -- the budget the whole evaluation suite has to fit inside -- goes on
    ``pytest`` instead.

    Same hazard as :func:`isolated_quota_state` below, same remedy: the suite gets an
    empty view of the credentials. Tests that are *about* tracing set these themselves,
    and monkeypatch restores the empty state after each one.
    """
    for name in (
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "VC_LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_quota_state(tmp_path, monkeypatch):
    """Keep every test's quota counters and spend ledger in its own temp file.

    Without this, running the suite would consume the real free-tier daily allowance
    recorded in .vericlaim_cache/quota.json and could refuse live calls afterwards.

    Redirecting the environment variables is necessary but not sufficient.
    ``get_settings`` is ``lru_cache``d, so whichever code resolves it first fixes the
    state paths for the entire session, and these variables are read only at that first
    resolution. pytest builds higher-scoped fixtures before function-scoped ones, so a
    single module-scoped fixture reading settings anywhere in the suite pins the real
    paths before this fixture runs -- after which ``reset_default_spend`` faithfully
    reloads the project's own ledger and every scripted call in the suite is billed
    against the real prepaid budget at the fictional prices the tests declare.

    Dropping the cache is what makes the redirection actually take effect, and makes
    isolation a property of the fixture rather than of collection order.
    """
    from vericlaim.gateway import quota, spend

    monkeypatch.setenv("VC_QUOTA_STATE_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setenv("VC_SPEND_STATE_PATH", str(tmp_path / "spend.json"))
    get_settings.cache_clear()
    quota.reset_default_limiter()
    spend.reset_default_spend()
    yield
    get_settings.cache_clear()
    quota.reset_default_limiter()
    spend.reset_default_spend()


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


# --------------------------------------------------------------- retrieval fixtures
#
# Shared by tests/policy and tests/scanned: both exercise the same store, chunker,
# and retrieval layer, so the fake embedder lives once at the root rather than being
# duplicated per package.

class FakeEmbedder:
    """A deterministic bag-of-words embedder with no network dependency.

    Vectors are built by hashing tokens into buckets, so two texts sharing vocabulary
    land near each other under cosine similarity. That is enough for a retrieval test
    to be meaningful without being a claim about semantic quality.
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in text.lower().split():
            cleaned = token.strip(".,;:()[]§—-")
            if not cleaned:
                continue
            digest = hashlib.sha256(cleaned.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # An all-zero vector has undefined cosine distance; anchor it instead.
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(f"{DOCUMENT_PREFIX}{text}") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self._vector(f"{QUERY_PREFIX}{text}")


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()
