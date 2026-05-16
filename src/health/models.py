"""Health-scorer schemas.

The split between HealthSignals (counts, deterministic) and ProjectHealth
(color + reasoning) makes the color rule auditable in Python and lets the
LLM contribute only the narrative explanation. Both pieces are stored, so
a PM can always see exactly which numbers drove a red flag.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class HealthColor(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class HealthSignals(BaseModel):
    """Per-project counts produced deterministically by signals.py."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    total_tasks: int
    overdue_count: int
    stuck_blocker_count: int
    untouched_count: int
    missed_deadline_count: int
    recent_missed_deadline_count: int  # within last 7 days
    scope_creep_count: int
    worst_examples: list[str] = Field(default_factory=list)  # top 5 task names


class ProjectHealth(BaseModel):
    """Full health record for one project."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    color: HealthColor
    reasoning: str
    signals: HealthSignals

    @property
    def is_red(self) -> bool:
        return self.color is HealthColor.RED

    @property
    def is_yellow(self) -> bool:
        return self.color is HealthColor.YELLOW
