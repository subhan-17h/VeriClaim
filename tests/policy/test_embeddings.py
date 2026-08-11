"""Embedder protocol conformance, task prefixes, batching, and dimension validation."""

from __future__ import annotations

from typing import Any

import pytest

from vericlaim.policy.embeddings import (
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    Embedder,
    EmbeddingDimensionError,
    OllamaEmbedder,
)


class RecordingClient:
    """Stands in for ollama.Client, recording calls and returning scripted vectors."""

    def __init__(self, dim: int = 8, *, widths: list[int] | None = None, count: int | None = None):
        self.dim = dim
        self.widths = widths
        self.count = count
        self.calls: list[dict[str, Any]] = []

    def embed(self, *, model: str, input: Any) -> dict[str, Any]:
        texts = [input] if isinstance(input, str) else list(input)
        self.calls.append({"model": model, "input": texts})
        if self.widths is not None:
            return {"embeddings": [[0.1] * width for width in self.widths]}
        length = self.count if self.count is not None else len(texts)
        return {"embeddings": [[float(index)] * self.dim for index in range(length)]}


def _embedder(client: RecordingClient, *, batch_size: int = 32) -> OllamaEmbedder:
    """Build an OllamaEmbedder around a fake client without touching the network."""
    embedder = OllamaEmbedder.__new__(OllamaEmbedder)
    embedder._model = "nomic-embed-text"
    embedder._dim = client.dim
    embedder._batch_size = batch_size
    embedder._client = client
    return embedder


# ------------------------------------------------------------ protocol conformance


def test_ollama_embedder_satisfies_the_protocol() -> None:
    assert isinstance(_embedder(RecordingClient()), Embedder)


def test_the_test_double_satisfies_the_protocol(embedder: Any) -> None:
    """If the fake drifts from the protocol, every test using it proves nothing."""
    assert isinstance(embedder, Embedder)


def test_no_client_is_constructed_at_import() -> None:
    """Importing must never connect; a module-level client breaks offline imports."""
    import vericlaim.policy.embeddings as module

    assert not hasattr(module, "_client")


# ------------------------------------------------------------------ task prefixes


def test_documents_are_embedded_with_the_document_prefix() -> None:
    client = RecordingClient()

    _embedder(client).embed_documents(["escape of water"])

    assert client.calls[0]["input"] == [f"{DOCUMENT_PREFIX}escape of water"]


def test_queries_are_embedded_with_the_query_prefix() -> None:
    client = RecordingClient()

    _embedder(client).embed_query("is gradual leakage covered")

    assert client.calls[0]["input"] == [f"{QUERY_PREFIX}is gradual leakage covered"]


def test_the_two_prefixes_differ() -> None:
    """Nomic Embed is asymmetric; one prefix for both silently costs recall."""
    assert DOCUMENT_PREFIX != QUERY_PREFIX


# ----------------------------------------------------------------------- batching


def test_documents_are_embedded_in_bounded_batches() -> None:
    client = RecordingClient()

    _embedder(client, batch_size=3).embed_documents([f"chunk {index}" for index in range(7)])

    assert [len(call["input"]) for call in client.calls] == [3, 3, 1]


def test_batching_preserves_input_order() -> None:
    """Embeddings are zipped against chunks downstream; reordering mismatches them."""
    client = RecordingClient(dim=2)

    vectors = _embedder(client, batch_size=2).embed_documents(["a", "b", "c", "d", "e"])

    # RecordingClient numbers vectors by position within each batch.
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]
    assert len(vectors) == 5


def test_an_empty_input_makes_no_call() -> None:
    client = RecordingClient()

    assert _embedder(client).embed_documents([]) == []
    assert client.calls == []


def test_a_non_positive_batch_size_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructed for real, so the guard in __init__ is the thing under test."""
    import ollama

    monkeypatch.setattr(ollama, "Client", lambda host: RecordingClient())

    with pytest.raises(ValueError, match="batch_size must be positive"):
        OllamaEmbedder(batch_size=0)


def test_construction_reads_defaults_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import ollama

    from vericlaim.config import get_settings

    monkeypatch.setattr(ollama, "Client", lambda host: RecordingClient())

    embedder = OllamaEmbedder()

    assert embedder.dim == get_settings().embed_dim
    assert embedder.model == get_settings().embed_model


# ------------------------------------------------------------ dimension validation


def test_a_wrong_width_is_rejected() -> None:
    """A changed embedding model must fail loudly, not poison the collection."""
    client = RecordingClient(dim=8, widths=[8, 5])

    with pytest.raises(EmbeddingDimensionError, match="position 1: expected 8, got 5"):
        _embedder(client).embed_documents(["a", "b"])


def test_a_short_response_is_rejected() -> None:
    """A missing vector would attach every later embedding to the wrong chunk."""
    client = RecordingClient(dim=8, count=2)

    with pytest.raises(EmbeddingDimensionError, match="expected 3, got 2"):
        _embedder(client).embed_documents(["a", "b", "c"])


def test_the_error_names_the_model() -> None:
    client = RecordingClient(dim=8, widths=[4])

    with pytest.raises(EmbeddingDimensionError, match="nomic-embed-text"):
        _embedder(client).embed_query("a")


def test_query_validation_expects_exactly_one_vector() -> None:
    client = RecordingClient(dim=8, count=2)

    with pytest.raises(EmbeddingDimensionError, match="expected 1, got 2"):
        _embedder(client).embed_query("a")


def test_vectors_are_returned_as_plain_floats() -> None:
    vectors = _embedder(RecordingClient(dim=3)).embed_documents(["a"])

    assert all(isinstance(value, float) for value in vectors[0])


# ---------------------------------------------------------------- live (needs ollama)


@pytest.mark.ollama
def test_live_ollama_returns_configured_width() -> None:
    from vericlaim.config import get_settings

    embedder = OllamaEmbedder()
    settings = get_settings()

    documents = embedder.embed_documents(
        ["Sudden and accidental escape of water is covered.", "Gradual leakage is excluded."]
    )
    query = embedder.embed_query("is escape of water covered")

    assert len(documents) == 2
    assert all(len(vector) == settings.embed_dim for vector in documents)
    assert len(query) == settings.embed_dim


@pytest.mark.ollama
def test_live_prefixes_produce_different_vectors() -> None:
    """Proof the asymmetry is real, not just a string we prepend."""
    embedder = OllamaEmbedder()
    text = "escape of water from a fixed plumbing system"

    as_document = embedder.embed_documents([text])[0]
    as_query = embedder.embed_query(text)

    assert as_document != as_query
