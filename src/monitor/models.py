"""Pydantic models for ClickUp tasks and classification flags."""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class TaskFlag(str, Enum):
    """Mutually-non-exclusive flags. A task can be both overdue and stuck_blocker."""

    STUCK_BLOCKER = "stuck_blocker"
    UNTOUCHED = "untouched"
    OVERDUE = "overdue"
    MISSED_DEADLINE = "missed_deadline"


CLOSED_STATUSES = frozenset({"complete", "completed", "closed", "done"})
IN_PROGRESS_STATUS = "in progress"
TO_DO_STATUS = "to do"


class Task(BaseModel):
    """Normalized ClickUp task — shape independent of raw API response.

    Created/updated/closed timestamps are timezone-aware (UTC). Callers that
    pass naive datetimes will get a validation error rather than silent drift.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    status: str
    project_id: str
    project_name: str
    list_id: str | None = None
    assignee_ids: list[str] = Field(default_factory=list)
    assignee_names: list[str] = Field(default_factory=list)
    due_date: date | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    closed_at: AwareDatetime | None = None
    dependencies: list[str] = Field(default_factory=list)
    url: str | None = None

    @property
    def normalized_status(self) -> str:
        return self.status.strip().lower()

    @property
    def has_dependencies(self) -> bool:
        return len(self.dependencies) > 0

    @property
    def is_closed(self) -> bool:
        return self.normalized_status in CLOSED_STATUSES

    def no_activity_days(self, as_of: date) -> int:
        last = self.updated_at.astimezone(timezone.utc).date()
        return (as_of - last).days


class ClassifiedTask(BaseModel):
    """A Task plus the set of flags determined by the classifier."""

    model_config = ConfigDict(frozen=True)

    task: Task
    flags: frozenset[TaskFlag] = Field(default_factory=frozenset)

    @property
    def has_any_flag(self) -> bool:
        return len(self.flags) > 0
