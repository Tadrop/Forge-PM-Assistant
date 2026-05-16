"""Embedding interface + Voyage AI implementation.

The Embedder Protocol lets the scope detector swap in a fake for tests
(see tests/test_scope_detector.py::FakeEmbedder) without touching the
production code path.
"""
from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """Anything that turns text into a fixed-dim vector."""

    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class VoyageEmbedder:
    """Anthropic's recommended embedding provider.

    `voyage-3-lite` is 512-dim and cheap; bump to `voyage-3` for higher
    accuracy on long-form SOW text.
    """

    def __init__(self, api_key: str, model: str = "voyage-3-lite"):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required")
        # Imported lazily so unit tests don't require the SDK installed.
        import voyageai  # type: ignore

        self._client = voyageai.Client(api_key=api_key)
        self.model = model

    def embed_query(self, text: str) -> list[float]:
        result = self._client.embed([text], model=self.model, input_type="query")
        return result.embeddings[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Voyage batches up to 128 inputs per call.
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            batch = texts[i : i + 128]
            result = self._client.embed(batch, model=self.model, input_type="document")
            out.extend(result.embeddings)
        return out
