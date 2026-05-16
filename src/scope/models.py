"""Models for SOW chunks and scope-creep evidence.

CLAUDE.md §3: "Scope creep flags must show evidence — original SOW vs current
task list, with diffs." Every ScopeEvidence carries the retrieved SOW
section text + similarity score so the PM can verify the flag, not trust it.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SowChunk(BaseModel):
    """One indexable chunk of an SOW, scoped to a project."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    project_id: str
    section: str  # e.g., "Deliverables", "Out of Scope", "Timeline"
    page: int | None = None
    text: str


class SowMatch(BaseModel):
    """A single Pinecone match — normalized away from vendor SDK shape."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    section: str
    page: int | None
    text: str
    similarity: float


class ScopeEvidence(BaseModel):
    """Audit trail for a scope-creep decision on one task.

    Always carries the best-match SOW chunk even when not flagged, so the PM
    can also audit *near-misses* and tune the threshold if needed.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_name: str
    project_id: str
    is_scope_creep: bool
    threshold: float
    best_match: SowMatch | None = None
    all_matches: list[SowMatch] = Field(default_factory=list)

    @property
    def evidence_summary(self) -> str:
        """Human-readable evidence line for PM-facing surfaces."""
        if self.best_match is None:
            return (
                f"Task '{self.task_name}' — no SOW available for project "
                f"{self.project_id}; cannot evaluate scope."
            )
        page_ref = f", p.{self.best_match.page}" if self.best_match.page else ""
        if self.is_scope_creep:
            return (
                f"Task '{self.task_name}' has no parallel in SOW section "
                f"'{self.best_match.section}'{page_ref} "
                f"(closest match similarity={self.best_match.similarity:.2f} "
                f"< threshold {self.threshold:.2f})."
            )
        return (
            f"Task '{self.task_name}' aligns with SOW section "
            f"'{self.best_match.section}'{page_ref} "
            f"(similarity={self.best_match.similarity:.2f})."
        )
