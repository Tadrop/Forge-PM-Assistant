"""Health scorer — color determined by rules, reasoning written by Claude.

Why this split:
- Color rules must be deterministic so the red-project escalation
  (CLAUDE.md §5) cannot accidentally fire or be silenced by LLM drift.
- The PM-facing reasoning IS the AI value-add — it names which signals
  drove the color in natural language a PM can paste into a doc.

CLAUDE.md §3 honesty constraint is enforced via the system prompt to
the reasoner; the status writer (Phase 5) adds a post-hoc validator.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .models import HealthColor, HealthSignals, ProjectHealth

logger = logging.getLogger(__name__)


# ─── Color rules (single source of truth) ─────────────────────────────────


def compute_color(signals: HealthSignals) -> HealthColor:
    """Hard rules for project color. No LLM involved.

    RED if any of:
      - 2+ stuck blockers (genuine blockages, not just slow tasks)
      - 1+ scope creep flag (PM needs to renegotiate or push back)
      - 1+ recent missed deadline (last 7 days)
      - >5 overdue tasks (volume signal)
    YELLOW if any of:
      - 1 stuck blocker
      - >2 overdue
      - >0 untouched (something is being neglected)
      - any historical missed deadline (older than 7 days)
    Else GREEN.
    """
    if (
        signals.stuck_blocker_count >= 2
        or signals.scope_creep_count >= 1
        or signals.recent_missed_deadline_count >= 1
        or signals.overdue_count > 5
    ):
        return HealthColor.RED
    if (
        signals.stuck_blocker_count >= 1
        or signals.overdue_count > 2
        or signals.untouched_count > 0
        or signals.missed_deadline_count >= 1
    ):
        return HealthColor.YELLOW
    return HealthColor.GREEN


# ─── Reasoning LLM ────────────────────────────────────────────────────────


class ReasoningLLM(Protocol):
    """Generates the 2-3 sentence narrative explanation. Injectable for tests."""

    def write_reasoning(
        self,
        signals: HealthSignals,
        color: HealthColor,
    ) -> str: ...


SYSTEM_PROMPT = """You are a project-health analyst for Forge Creative Agency.

You receive deterministic signal counts from one project and a pre-computed
color (green / yellow / red). Your job: write 2–3 sentences of reasoning
that explicitly name which signals drove the color.

HARD RULES:
- Cite numbers from the signals. Do not invent counts.
- Be honest. If the color is red, say what is red. If yellow, say what is at risk.
- FORBIDDEN softening words: "minor", "slight", "smooth", "small", "tiny",
  "just", "merely", "only a few". Status is honest — yellow is yellow, red is red.
- No suggestions, no advice, no calls to action — just the read.
- Plain prose. No bullets, no headers, no emoji."""


def _build_user_prompt(signals: HealthSignals, color: HealthColor) -> str:
    examples_block = (
        "\n".join(f"  - {e}" for e in signals.worst_examples)
        if signals.worst_examples
        else "  (none)"
    )
    return f"""Project: {signals.project_name} (id: {signals.project_id})
Pre-computed color: {color.value.upper()}

Signal counts:
  total tasks: {signals.total_tasks}
  overdue: {signals.overdue_count}
  stuck blockers: {signals.stuck_blocker_count}
  untouched: {signals.untouched_count}
  missed deadlines (all-time): {signals.missed_deadline_count}
  missed deadlines (last 7 days): {signals.recent_missed_deadline_count}
  scope-creep flags: {signals.scope_creep_count}

Worst examples (up to 5):
{examples_block}

Write the 2–3 sentence reasoning now."""


class ClaudeReasoner:
    """Production implementation backed by the Anthropic SDK."""

    def __init__(self, api_key: str, model: str = "claude-opus-4-7", *, max_tokens: int = 400):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        from anthropic import Anthropic  # type: ignore

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def write_reasoning(self, signals: HealthSignals, color: HealthColor) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(signals, color)}],
        )
        # Anthropic SDK returns a list of content blocks; first is text.
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


# ─── Scorer orchestrator ──────────────────────────────────────────────────


class HealthScorer:
    def __init__(self, reasoner: ReasoningLLM):
        self.reasoner = reasoner

    def score(self, signals: HealthSignals) -> ProjectHealth:
        color = compute_color(signals)
        if signals.total_tasks == 0:
            reasoning = (
                f"{signals.project_name} has no active tasks in the scan window. "
                "Nothing to flag, nothing to celebrate."
            )
        else:
            reasoning = self.reasoner.write_reasoning(signals, color)
        logger.info(
            "health_score project_id=%s color=%s overdue=%d stuck=%d scope=%d",
            signals.project_id,
            color.value,
            signals.overdue_count,
            signals.stuck_blocker_count,
            signals.scope_creep_count,
        )
        return ProjectHealth(
            project_id=signals.project_id,
            project_name=signals.project_name,
            color=color,
            reasoning=reasoning,
            signals=signals,
        )

    def score_many(self, all_signals: list[HealthSignals]) -> list[ProjectHealth]:
        return [self.score(s) for s in all_signals]
