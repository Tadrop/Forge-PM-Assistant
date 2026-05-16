"""Weekly client check-in agenda drafter.

CLAUDE.md §5 MEETING AGENDA GENERATOR:
    "Day before weekly client check-in: pull recent activity + open items
     + decisions needed → draft agenda → PM review"
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

from ..health.models import ProjectHealth
from ..scope.models import ScopeEvidence
from ..tone.models import PMProfile
from ..tone.registry import PMRegistry
from .models import AgendaDraft

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are drafting a MEETING AGENDA for an upcoming weekly client check-in at Forge Creative Agency.
The project manager will REVIEW your draft before sharing. Never imply it has been shared.

You receive:
- Project name + meeting date
- Health color and signal counts (overdue, stuck blockers, scope-creep, etc.)
- Worst-example task names (these are real data — cite them, don't paraphrase out)
- Open scope-creep items (these MUST be discussed at the meeting)

Output a structured agenda in PLAIN TEXT (no markdown). Use these sections:
  Recap of last week
  Decisions needed from client
  Risks and blockers
  Action items proposed
  Time check

HARD RULES:
- 6–10 line items total across all sections
- Every line cites specific tasks or signals from the data
- No filler ("quick sync", "touch base", "circle back")
- Match the color: red agendas focus on unblock items, yellow on risks, green on next-phase planning
- No promises or commitments on behalf of the team"""


class AgendaLLM(Protocol):
    def draft(
        self,
        *,
        health: ProjectHealth,
        scope_evidence: list[ScopeEvidence],
        pm: PMProfile,
        meeting_date: date,
    ) -> str: ...


def _format_scope_items(scope_evidence: list[ScopeEvidence]) -> str:
    creep = [e for e in scope_evidence if e.is_scope_creep]
    if not creep:
        return "  (none)"
    return "\n".join(f"  - {e.task_name}: {e.evidence_summary}" for e in creep)


def _build_user_prompt(
    health: ProjectHealth,
    scope_evidence: list[ScopeEvidence],
    pm: PMProfile,
    meeting_date: date,
) -> str:
    sig = health.signals
    return f"""Project: {health.project_name} (id: {health.project_id})
Meeting date: {meeting_date.isoformat()}
PM (sign as): {pm.name}
Health color: {health.color.value.upper()}

Health reasoning (ground truth):
{health.reasoning}

Signal counts:
  overdue: {sig.overdue_count}
  stuck blockers: {sig.stuck_blocker_count}
  untouched: {sig.untouched_count}
  missed deadlines (last 7d / all-time): {sig.recent_missed_deadline_count} / {sig.missed_deadline_count}
  scope-creep flags: {sig.scope_creep_count}

Worst examples:
{chr(10).join('  - ' + e for e in sig.worst_examples) or '  (none)'}

Open scope-creep items to surface:
{_format_scope_items(scope_evidence)}

Draft the agenda now."""


class ClaudeAgendaLLM:
    def __init__(self, api_key: str, model: str = "claude-opus-4-7", *, max_tokens: int = 800):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        from anthropic import Anthropic  # type: ignore

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def draft(self, *, health, scope_evidence, pm, meeting_date) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.3,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(health, scope_evidence, pm, meeting_date),
                }
            ],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


class AgendaDrafter:
    def __init__(self, llm: AgendaLLM, pm_registry: PMRegistry):
        self.llm = llm
        self.pm_registry = pm_registry

    def draft(
        self,
        health: ProjectHealth,
        meeting_date: date,
        *,
        scope_evidence: list[ScopeEvidence] | None = None,
    ) -> AgendaDraft:
        scope_evidence = scope_evidence or []
        pm = self.pm_registry.for_project(health.project_id)
        body = self.llm.draft(
            health=health, scope_evidence=scope_evidence, pm=pm, meeting_date=meeting_date
        )
        open_items = [
            e.task_name for e in scope_evidence if e.is_scope_creep
        ]
        logger.info(
            "agenda_drafted project_id=%s pm=%s meeting=%s scope_items=%d",
            health.project_id,
            pm.id,
            meeting_date.isoformat(),
            len(open_items),
        )
        return AgendaDraft(
            project_id=health.project_id,
            project_name=health.project_name,
            pm_id=pm.id,
            pm_name=pm.name,
            meeting_date=meeting_date,
            body=body,
            signals=health.signals,
            open_scope_items=open_items,
        )
