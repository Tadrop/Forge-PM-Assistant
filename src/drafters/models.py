"""Shared draft schemas — every drafter returns a frozen audit-trail record.

CLAUDE.md §3: "Never send anything externally without PM approval."
Each draft carries `requires_pm_approval=True` and the inputs used so a PM
can review the source signals as well as the generated text.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ..health.models import HealthColor, HealthSignals
from ..monitor.models import TaskFlag


class NudgeDraft(BaseModel):
    """A Slack nudge draft routed to a PM's review queue. Never auto-sent."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_name: str
    project_id: str
    project_name: str

    pm_id: str
    pm_name: str

    assignee_names: list[str] = Field(default_factory=list)
    assignee_slack_user_ids: list[str] = Field(default_factory=list)

    flag: TaskFlag
    body: str

    tone_example_ids: list[str] = Field(default_factory=list)
    tone_example_previews: list[str] = Field(default_factory=list)

    requires_pm_approval: bool = True
    drafted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusDraft(BaseModel):
    """A weekly client status email. Always a draft to the PM, never auto-sent."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    pm_id: str
    pm_name: str
    color: HealthColor
    subject: str
    body: str
    signals: HealthSignals

    honesty_offenders: list[str] = Field(default_factory=list)
    retry_count: int = 0

    requires_pm_approval: bool = True
    drafted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EscalationDraft(BaseModel):
    """Internal escalation to leadership when a project flips to red."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    pm_id: str
    pm_name: str
    channel: str
    body: str
    previous_color: HealthColor | None
    new_color: HealthColor
    signals: HealthSignals

    requires_pm_approval: bool = True
    drafted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgendaDraft(BaseModel):
    """Weekly client check-in agenda. Always a draft to the PM."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    pm_id: str
    pm_name: str
    meeting_date: date
    body: str
    signals: HealthSignals
    open_scope_items: list[str] = Field(default_factory=list)

    requires_pm_approval: bool = True
    drafted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
