"""Weekly client status email writer — honest, with soft-pedal validator.

CLAUDE.md §3: "Status updates must be honest — flag problems clearly, never
sugarcoat. Yellow/red is yellow/red."

CLAUDE.md §9: "If health == red, status update prompt forbids softening
adjectives. A validator scans for soft-pedalling words (e.g. 'minor',
'slight', 'smooth') in red sections and rejects."

Flow on red projects:
    1. LLM drafts with soft-pedal-forbidden system prompt.
    2. Validator scans output. If clean → done.
    3. If offenders found → retry once with explicit list of offending words.
    4. If still offending → raise SoftPedalError. We do NOT silently downgrade.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from ..health.models import HealthColor, ProjectHealth
from ..tone.models import PMProfile
from ..tone.registry import PMRegistry
from .honesty import SoftPedalError, find_soft_pedal_offenders
from .models import StatusDraft

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 1


class StatusLLM(Protocol):
    def draft(
        self,
        *,
        health: ProjectHealth,
        pm: PMProfile,
        extra_constraints: str = "",
    ) -> dict: ...  # {"subject": str, "body": str}


SYSTEM_PROMPT_BASE = """You are drafting the WEEKLY STATUS UPDATE EMAIL for one Forge Creative Agency client.
The project manager will REVIEW your draft before sending. Never imply it has already been sent.

You will receive:
- Project name + the PM's name (for sign-off)
- The pre-computed health color (green / yellow / red) — this is FACT, do not soften it
- Signal counts (overdue, stuck blockers, scope-creep flags, missed deadlines, untouched tasks)
- The 1-paragraph health reasoning

Write a 4–6 paragraph email:
  1. One-line subject (return separately) + warm but professional greeting
  2. What got DONE this week — only items the data supports
  3. What's AT RISK — explicit, signal-cited
  4. What's NEXT or what we need from the client (decisions, assets, sign-offs)
  5. Sign-off using the PM's first name

ABSOLUTE RULES:
- Never invent commitments, timelines, deliverables, or causes the data didn't show.
- Never blame the client for issues caused by Forge.
- Match the color: green is genuinely positive, yellow is concerned-but-factual,
  red is direct and urgent — no euphemisms.
- Plain prose only. No markdown, no bullets, no headers.

Output STRICT JSON ONLY, with this exact shape and no fences:
{"subject": "<one-line subject>", "body": "<full email body>"}"""


RED_HONESTY_RULES = """
RED-COLOR HONESTY RULES (NON-NEGOTIABLE):
- This project is RED. The email must convey URGENCY, not reassurance.
- FORBIDDEN words (do not use, in any form): minor, slight, slightly, smooth,
  smoothly, tiny, modest, trivial, negligible, minimal.
- FORBIDDEN phrases: "nothing major", "no big deal", "no worries", "all good",
  "kind of stuck", "sort of stuck".
- Name the specific problem signals (e.g. "3 stuck blockers, 1 missed deadline").
- Propose a follow-up call this week, by name if possible."""


def _build_user_prompt(health: ProjectHealth, pm: PMProfile, extra_constraints: str) -> str:
    sig = health.signals
    return f"""Project: {health.project_name} (id: {health.project_id})
PM (sign as): {pm.name}
Health color: {health.color.value.upper()}

Health reasoning (use as ground truth):
{health.reasoning}

Signal counts:
  total tasks: {sig.total_tasks}
  overdue: {sig.overdue_count}
  stuck blockers: {sig.stuck_blocker_count}
  untouched: {sig.untouched_count}
  missed deadlines (last 7d / all-time): {sig.recent_missed_deadline_count} / {sig.missed_deadline_count}
  scope-creep flags: {sig.scope_creep_count}

Worst examples (the items that drove the color):
{chr(10).join('  - ' + e for e in sig.worst_examples) or '  (none)'}
{extra_constraints}

Output STRICT JSON: {{"subject": "...", "body": "..."}}"""


class ClaudeStatusLLM:
    """Production status writer using the Anthropic SDK with strict-JSON output."""

    def __init__(self, api_key: str, model: str = "claude-opus-4-7", *, max_tokens: int = 1500):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        from anthropic import Anthropic  # type: ignore

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def draft(self, *, health: ProjectHealth, pm: PMProfile, extra_constraints: str = "") -> dict:
        system = SYSTEM_PROMPT_BASE
        if health.color is HealthColor.RED:
            system = SYSTEM_PROMPT_BASE + "\n" + RED_HONESTY_RULES
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.3,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(health, pm, extra_constraints),
                }
            ],
        )
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text.strip()
                break
        # Tolerate code fences if the model adds them.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)


class StatusWriter:
    def __init__(
        self,
        llm: StatusLLM,
        pm_registry: PMRegistry,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.llm = llm
        self.pm_registry = pm_registry
        self.max_retries = max_retries

    def draft(self, health: ProjectHealth) -> StatusDraft:
        pm = self.pm_registry.for_project(health.project_id)

        attempt = 0
        last_offenders: list[str] = []
        extra = ""

        while True:
            result = self.llm.draft(
                health=health, pm=pm, extra_constraints=extra
            )
            subject = str(result.get("subject", "")).strip()
            body = str(result.get("body", "")).strip()

            offenders = (
                find_soft_pedal_offenders(body)
                if health.color is HealthColor.RED
                else []
            )

            if not offenders:
                return StatusDraft(
                    project_id=health.project_id,
                    project_name=health.project_name,
                    pm_id=pm.id,
                    pm_name=pm.name,
                    color=health.color,
                    subject=subject,
                    body=body,
                    signals=health.signals,
                    honesty_offenders=[],
                    retry_count=attempt,
                )

            last_offenders = offenders
            attempt += 1
            logger.warning(
                "status_writer soft_pedal project_id=%s attempt=%d offenders=%s",
                health.project_id,
                attempt,
                offenders,
            )
            if attempt > self.max_retries:
                raise SoftPedalError(offenders=last_offenders, text=body)

            extra = (
                "\n\nYOUR PREVIOUS ATTEMPT used these forbidden softening words: "
                f"{', '.join(offenders)}. "
                "Rewrite without them. Be direct about what is red and why."
            )
