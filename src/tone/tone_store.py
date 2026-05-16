"""Vector store for PM tone examples.

Mirrors src/scope/sow_store.py: same Pinecone index pattern, but each
namespace holds one PM's past writing instead of one project's SOW.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .models import ToneExample

logger = logging.getLogger(__name__)


class ToneStore(Protocol):
    def upsert_examples(
        self,
        pm_namespace: str,
        examples: list[ToneExample],
        vectors: list[list[float]],
    ) -> None: ...

    def retrieve(
        self,
        pm_namespace: str,
        vector: list[float],
        top_k: int,
    ) -> list[ToneExample]: ...


class PineconeToneStore:
    def __init__(
        self,
        api_key: str,
        index_name: str,
        *,
        dimension: int = 512,
        cloud: str = "aws",
        region: str = "us-east-1",
    ):
        if not api_key:
            raise ValueError("PINECONE_API_KEY is required")
        from pinecone import Pinecone, ServerlessSpec  # type: ignore

        self._pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        existing = {idx["name"] for idx in self._pc.list_indexes()}
        if index_name not in existing:
            logger.info(
                "Creating Pinecone tone index name=%s dim=%d", index_name, dimension
            )
            self._pc.create_index(
                name=index_name,
                dimension=dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud=cloud, region=region),
            )
        self._index = self._pc.Index(index_name)

    def upsert_examples(
        self,
        pm_namespace: str,
        examples: list[ToneExample],
        vectors: list[list[float]],
    ) -> None:
        if len(examples) != len(vectors):
            raise ValueError("examples and vectors must be the same length")
        payload = [
            {
                "id": e.example_id,
                "values": v,
                "metadata": {"text": e.text, "context": e.context, "pm_id": e.pm_id},
            }
            for e, v in zip(examples, vectors)
        ]
        for i in range(0, len(payload), 100):
            self._index.upsert(vectors=payload[i : i + 100], namespace=pm_namespace)

    def retrieve(
        self,
        pm_namespace: str,
        vector: list[float],
        top_k: int,
    ) -> list[ToneExample]:
        result = self._index.query(
            vector=vector,
            top_k=top_k,
            namespace=pm_namespace,
            include_metadata=True,
        )
        out: list[ToneExample] = []
        for m in getattr(result, "matches", []) or result.get("matches", []):
            md = m.get("metadata", {}) if isinstance(m, dict) else (m.metadata or {})
            score = m.get("score") if isinstance(m, dict) else m.score
            example_id = m.get("id") if isinstance(m, dict) else m.id
            out.append(
                ToneExample(
                    example_id=str(example_id),
                    pm_id=md.get("pm_id", ""),
                    text=md.get("text", ""),
                    context=md.get("context", "nudge"),
                    similarity=float(score) if score is not None else None,
                )
            )
        return out
