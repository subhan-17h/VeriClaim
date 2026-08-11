"""Fixtures shared by the policy-retrieval tests.

The fake embedder is deterministic and hash-based rather than random, so a test can
assert that a query retrieves a specific chunk without a model server. It is not a
semantic model: it exists to prove the plumbing -- ordering, batching, dimension
validation, storage round-trips, and rank fusion -- which is the part that breaks.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import pytest

from vericlaim.policy.embeddings import DOCUMENT_PREFIX, QUERY_PREFIX


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
