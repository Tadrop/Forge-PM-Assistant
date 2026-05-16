"""Internal escalation drafter — fires when a project transitions to RED.

CLAUDE.md §5 RED-PROJECT ESCALATION:
    "Project flips to red → draft internal escalation message to
     leadership Slack channel (PM approves)"

CLAUDE.md §6 4c: "Project flips red mid-day (escalation draft fires
within the hour)" — handled by the hourly scheduler + HealthStateStore.

Only escalates on a TRANSITION; a project that has already been red for
days does not re-fire — leadership doesn't need the same alarm hourly.
"""
from __future__ import annotations

import logging
from typing import Protocol

from ..health.models import HealthColor, ProjectHealth
from ..tone.models import PMProfile
from ..tone.registry import PMRegistry
from .models import EscalationDraft

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are drafting an INTERNAL Slack escalation to Forge Creative Agency leadership.
The project manager will REVIEW the draft before posting. Never imply it has already been posted.

This message is INTERNAL — leadership eyes only. Be direct, factual, brief.

You receive:
- Project name + the previous and new health colors (only fires when new=red)
- Signal counts that drove the red
- The PM's name (sign as them)
- The leadership channel (already chosen — do not add your own routing)

Write a 3–5 sentence Slack message:
- Open with the project name and "now red"
- State the specific signals that drove it (cite numbers)
- State briefly what the PM is doing (1 short sentence)
- State what the PM is asking from leadership, if anything

HARD RULES:
- Under 80 words
- No softening — this is a red transition, treat it as one
- No bullet lists, no headers
- Plain Slack text, no markdown
- Never blame an individual; describe the situation"""


class EscalationLLM(Protocol):
    def draft(
        self,
        *,
        health: ProjectHealth,
        previous_color: HealthColor | None,
        pm: PMProfile,
        channel: str,
    ) -> str: ...


def _build_user_prompt(
    health: ProjectHealth,
    previous_color: HealthColor | None,
    pm: PMProfile,
    channel: str,
) -> str:
    sig = health.signals
    prev = previous_color.value.upper() if previous_color else "UNKNOWN (first scan)"
    return f"""Project: {health.project_name} (id: {health.project_id})
Previous color: {prev}
New color: RED
PM (sign as): {pm.name}
Channel: {channel}

Signal counts that drove the transition:
  overdue: {sig.overdue_count}
  stuck blockers: {sig.stuck_blocker_count}
  missed deadlines (last 7d): {sig.recent_missed_deadline_count}
  scope-creep flags: {sig.scope_creep_count}

Worst examples:
{chr(10).join('  - ' + e for e in sig.worst_examples) or '  (none)'}

Health reasoning (use as ground truth):
{health.reasoning}

Draft the escalation message now."""


class ClaudeEscalationLLM:
    def __init__(self, api_key: str, model: str = "claude-opus-4-7", *, max_tokens: int = 400):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        from anthropic import Anthropic  # type: ignore

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def draft(self, *, health, previous_color, pm, channel) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.2,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(health, previous_color, pm, channel),
                }
            ],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


class EscalationDrafter:
    def __init__(self, llm: EscalationLLM, pm_registry: PMRegistry, leadership_channel: str):
        if not leadership_channel:
            raise ValueError("leadership_channel is required")
        self.llm = llm
        self.pm_registry = pm_registry
        self.leadership_channel = leadership_channel

    def draft(
        self,
        health: ProjectHealth,
        *,
        previous_color: HealthColor | None,
    ) -> EscalationDraft:
        if health.color is not HealthColor.RED:
            raise ValueError(
                "Escalation should only be drafted for RED projects "
                f"(got {health.color.value})"
            )
        pm = self.pm_registry.for_project(health.project_id)
        body = self.llm.draft(
            health=health, previous_color=previous_color, pm=pm, channel=self.leadership_channel
        )
        logger.info(
            "escalation_drafted project_id=%s pm=%s previous=%s",
            health.project_id,
            pm.id,
            previous_color.value if previous_color else None,
        )
        return EscalationDraft(
            project_id=health.project_id,
            project_name=health.project_name,
            pm_id=pm.id,
            pm_name=pm.name,
            channel=self.leadership_channel,
            body=body,
            previous_color=previous_color,
            new_color=health.color,
            signals=health.signals,
        )
