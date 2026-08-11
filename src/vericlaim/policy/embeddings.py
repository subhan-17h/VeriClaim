"""Embedding boundary for the retrieval sources.

Everything that embeds text depends on the :class:`Embedder` protocol rather than on
Ollama, so indexing and retrieval can be tested without a model server and the engine
can be swapped without touching either.

The client is built when an embedder is constructed, never at import. A module-level
client -- what the reference implementation uses -- connects on import, which makes
importing any module that transitively reaches this one fail when Ollama is down, and
makes the host impossible to override in a test.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol, runtime_checkable

from vericlaim.config import get_settings

__all__ = (
    "DOCUMENT_PREFIX",
    "Embedder",
    "EmbeddingDimensionError",
    "OllamaEmbedder",
    "QUERY_PREFIX",
    "get_embedder",
)

# Nomic Embed is trained with asymmetric task prefixes: a passage and the question
# that should retrieve it are embedded differently on purpose. Dropping them, or
# using one for both, costs recall silently -- nothing errors, results just get worse.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbeddingDimensionError(ValueError):
    """Raised when a model returns vectors of an unexpected width.

    Worth its own type because the usual cause is a changed embedding model, and the
    symptom without this check is a Chroma collection holding two incompatible vector
    families that returns plausible nonsense rather than failing.
    """


@runtime_checkable
class Embedder(Protocol):
    """The embedding capability that indexing and retrieval depend on."""

    @property
    def dim(self) -> int:
        """The width of the vectors this embedder produces."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for indexing, preserving input order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a question for search."""
        ...


class OllamaEmbedder:
    """Embed text with a local Ollama model, offline and free."""

    def __init__(
        self,
        *,
        host: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model if model is not None else settings.embed_model
        self._dim = dim if dim is not None else settings.embed_dim
        self._batch_size = batch_size if batch_size is not None else settings.embed_batch_size
        if self._batch_size <= 0:
            raise ValueError("batch_size must be positive")

        import ollama

        self._client = ollama.Client(host=host if host is not None else settings.ollama_host)

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    def _validate(
        self, vectors: Sequence[Sequence[float]], expected_count: int
    ) -> list[list[float]]:
        """Check arity and width before a vector can reach the store.

        Arity is checked because the batch is zipped against its chunks downstream: a
        short response would silently attach every embedding to the wrong chunk.
        """
        if len(vectors) != expected_count:
            raise EmbeddingDimensionError(
                f"Embedding count mismatch from {self._model}: expected "
                f"{expected_count}, got {len(vectors)}"
            )
        validated = []
        for position, vector in enumerate(vectors):
            if len(vector) != self._dim:
                raise EmbeddingDimensionError(
                    f"Embedding dimension mismatch from {self._model} at batch "
                    f"position {position}: expected {self._dim}, got {len(vector)}"
                )
            validated.append([float(value) for value in vector])
        return validated

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages with the document prefix, preserving input order."""
        if not texts:
            return []

        prefixed = [f"{DOCUMENT_PREFIX}{text}" for text in texts]
        embeddings: list[list[float]] = []
        for start in range(0, len(prefixed), self._batch_size):
            batch = prefixed[start : start + self._batch_size]
            response = self._client.embed(model=self._model, input=batch)
            embeddings.extend(self._validate(response["embeddings"], len(batch)))
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a question with the query-specific task prefix."""
        response = self._client.embed(model=self._model, input=f"{QUERY_PREFIX}{text}")
        return self._validate(response["embeddings"], expected_count=1)[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Return the process-wide default embedder, constructed on first use.

    Callers that can accept an embedder should take one as an argument; this exists
    for entry points, which have to choose a concrete implementation somewhere.
    """
    return OllamaEmbedder()
