"""Schemas for PM voice profiles."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PMProfile(BaseModel):
    """One project manager — owns a set of projects, has a tone namespace."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    slack_user_id: str | None = None
    email: str | None = None
    tone_namespace: str
    projects: list[str] = Field(default_factory=list)


class ToneExample(BaseModel):
    """One example of a PM's past writing — basis for voice imitation."""

    model_config = ConfigDict(frozen=True)

    example_id: str
    pm_id: str
    text: str
    # Free-form tag: "nudge", "status_update", "escalation", "agenda".
    context: str = "nudge"
    similarity: float | None = None
