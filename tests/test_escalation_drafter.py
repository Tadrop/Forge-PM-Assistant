"""Escalation drafter tests — only fires on transition to RED."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.drafters.escalation import EscalationDrafter
from src.health.models import HealthColor, HealthSignals, ProjectHealth
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


class RecordingLLM:
    def __init__(self, body: str = "Acme is now red: 2 stuck blockers, 1 missed deadline this week."):
        self.body = body
        self.calls: list[dict] = []

    def draft(self, *, health, previous_color, pm, channel):
        self.calls.append(
            {
                "project_id": health.project_id,
                "previous_color": previous_color,
                "pm_id": pm.id,
                "channel": channel,
            }
        )
        return self.body


def _health(color: HealthColor) -> ProjectHealth:
    sig = HealthSignals(
        project_id="P-acme",
        project_name="Acme",
        total_tasks=12,
        overdue_count=2,
        stuck_blocker_count=2,
        untouched_count=0,
        missed_deadline_count=1,
        recent_missed_deadline_count=1,
        scope_creep_count=0,
        worst_examples=["[stuck blocker] CMS migration"],
    )
    return ProjectHealth(
        project_id="P-acme",
        project_name="Acme",
        color=color,
        reasoning="Two stuck blockers and one missed deadline.",
        signals=sig,
    )


def test_drafter_routes_to_leadership_channel():
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    draft = drafter.draft(_health(HealthColor.RED), previous_color=HealthColor.YELLOW)
    assert draft.channel == "#leadership"
    assert draft.previous_color is HealthColor.YELLOW
    assert draft.new_color is HealthColor.RED


def test_drafter_uses_correct_pm():
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    draft = drafter.draft(_health(HealthColor.RED), previous_color=None)
    # P-acme owned by Alex per fixture.
    assert draft.pm_id == "pm-alex"
    assert draft.pm_name == "Alex Chen"


def test_drafter_rejects_non_red_input():
    """The caller (scheduler) must only invoke this on RED; defensive guard."""
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    with pytest.raises(ValueError):
        drafter.draft(_health(HealthColor.YELLOW), previous_color=HealthColor.GREEN)


def test_drafter_passes_previous_color_to_llm():
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    drafter.draft(_health(HealthColor.RED), previous_color=HealthColor.YELLOW)
    assert llm.calls[0]["previous_color"] is HealthColor.YELLOW


def test_drafter_handles_first_seen_red():
    """previous_color=None when project is being scanned for the first time."""
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    draft = drafter.draft(_health(HealthColor.RED), previous_color=None)
    assert draft.previous_color is None
    assert draft.new_color is HealthColor.RED


def test_drafter_requires_channel():
    with pytest.raises(ValueError):
        EscalationDrafter(RecordingLLM(), PMRegistry.from_json(CONFIG), "")


def test_drafter_carries_signals_for_audit():
    llm = RecordingLLM()
    drafter = EscalationDrafter(llm, PMRegistry.from_json(CONFIG), "#leadership")
    draft = drafter.draft(_health(HealthColor.RED), previous_color=HealthColor.YELLOW)
    assert draft.signals.stuck_blocker_count == 2
    assert draft.requires_pm_approval is True
