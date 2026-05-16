"""Scope-creep detector — compares current tasks against the project SOW.

CLAUDE.md §3: "Scope creep flags must show evidence."
CLAUDE.md §9: "Every flagged task must cite the SOW section retrieved +
              similarity score below threshold."

Decision rule:
    For each task, embed its name+description, retrieve top-K SOW chunks
    via cosine similarity. If the BEST match falls below the threshold,
    the task is flagged as scope creep with the closest-match chunk as
    evidence (so the PM can see exactly what the agent compared against).
"""
from __future__ import annotations

import logging

from ..monitor.models import Task
from .embeddings import Embedder
from .models import ScopeEvidence
from .sow_store import SowStore

logger = logging.getLogger(__name__)


class ScopeDetector:
    def __init__(
        self,
        embedder: Embedder,
        sow_store: SowStore,
        *,
        threshold: float = 0.72,
        top_k: int = 3,
    ):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        self.embedder = embedder
        self.sow_store = sow_store
        self.threshold = threshold
        self.top_k = top_k

    def _query_text(self, task: Task) -> str:
        # Description isn't on our Task model today; name carries most signal.
        return task.name.strip()

    def detect(self, task: Task) -> ScopeEvidence:
        query = self._query_text(task)
        if not query:
            return ScopeEvidence(
                task_id=task.id,
                task_name=task.name,
                project_id=task.project_id,
                is_scope_creep=False,
                threshold=self.threshold,
            )

        vector = self.embedder.embed_query(query)
        matches = self.sow_store.query(task.project_id, vector, self.top_k)

        if not matches:
            # No SOW indexed for this project → can't make a call. Don't flag.
            logger.warning(
                "scope_detect no_sow project_id=%s task_id=%s",
                task.project_id,
                task.id,
            )
            return ScopeEvidence(
                task_id=task.id,
                task_name=task.name,
                project_id=task.project_id,
                is_scope_creep=False,
                threshold=self.threshold,
            )

        best = max(matches, key=lambda m: m.similarity)
        is_creep = best.similarity < self.threshold

        if is_creep:
            logger.info(
                "scope_creep_flag project_id=%s task=%s best_section=%s "
                "best_similarity=%.3f threshold=%.3f",
                task.project_id,
                task.id,
                best.section,
                best.similarity,
                self.threshold,
            )

        return ScopeEvidence(
            task_id=task.id,
            task_name=task.name,
            project_id=task.project_id,
            is_scope_creep=is_creep,
            threshold=self.threshold,
            best_match=best,
            all_matches=sorted(matches, key=lambda m: -m.similarity),
        )

    def detect_many(self, tasks: list[Task]) -> list[ScopeEvidence]:
        return [self.detect(t) for t in tasks]

    def flagged_only(self, tasks: list[Task]) -> list[ScopeEvidence]:
        return [e for e in self.detect_many(tasks) if e.is_scope_creep]

    @staticmethod
    def best_evidence_line(evidence: ScopeEvidence) -> str:
        """Convenience for use by the briefing / status drafters."""
        return evidence.evidence_summary
