"""SOW vector store interface + Pinecone implementation.

Each project_id is one Pinecone namespace, so retrieval is scoped to the
right SOW without per-task filtering.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .models import SowChunk, SowMatch

logger = logging.getLogger(__name__)


class SowStore(Protocol):
    """Vector DB abstraction for SOW chunks."""

    def upsert(
        self,
        project_id: str,
        chunks: list[SowChunk],
        vectors: list[list[float]],
    ) -> None: ...

    def query(
        self,
        project_id: str,
        vector: list[float],
        top_k: int,
    ) -> list[SowMatch]: ...

    def delete_project(self, project_id: str) -> None: ...


class PineconeSowStore:
    """Pinecone-backed store. One namespace per project_id."""

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
            logger.info("Creating Pinecone index name=%s dim=%d", index_name, dimension)
            self._pc.create_index(
                name=index_name,
                dimension=dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud=cloud, region=region),
            )
        self._index = self._pc.Index(index_name)

    def upsert(
        self,
        project_id: str,
        chunks: list[SowChunk],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must be the same length")
        payload = [
            {
                "id": c.chunk_id,
                "values": v,
                "metadata": {
                    "section": c.section,
                    "page": c.page if c.page is not None else -1,
                    "text": c.text,
                    "project_id": c.project_id,
                },
            }
            for c, v in zip(chunks, vectors)
        ]
        # Pinecone recommends batches of 100.
        for i in range(0, len(payload), 100):
            self._index.upsert(vectors=payload[i : i + 100], namespace=project_id)

    def query(
        self,
        project_id: str,
        vector: list[float],
        top_k: int,
    ) -> list[SowMatch]:
        result = self._index.query(
            vector=vector,
            top_k=top_k,
            namespace=project_id,
            include_metadata=True,
        )
        matches = []
        for m in getattr(result, "matches", []) or result.get("matches", []):
            md = m.get("metadata", {}) if isinstance(m, dict) else (m.metadata or {})
            score = m.get("score") if isinstance(m, dict) else m.score
            chunk_id = m.get("id") if isinstance(m, dict) else m.id
            page_val = md.get("page", -1)
            matches.append(
                SowMatch(
                    chunk_id=str(chunk_id),
                    section=md.get("section", ""),
                    page=None if page_val in (None, -1) else int(page_val),
                    text=md.get("text", ""),
                    similarity=float(score),
                )
            )
        return matches

    def delete_project(self, project_id: str) -> None:
        self._index.delete(delete_all=True, namespace=project_id)
