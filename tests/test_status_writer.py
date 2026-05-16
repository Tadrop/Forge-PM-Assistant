"""Status writer tests.

Covers:
- CLAUDE.md §9 mandatory: 'red status update cannot contain soft-pedal words'
- Retry loop: soft-pedal in first attempt → retry succeeds
- Hard failure: persistent soft-pedal → SoftPedalError raised, no fallback
- Yellow/green paths do NOT trigger the validator (only red, per §9 spec)
- Output is always a draft requiring PM approval
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.drafters.honesty import SoftPedalError
from src.drafters.status import StatusWriter
from src.health.models import HealthColor, HealthSignals, ProjectHealth
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


class ScriptedLLM:
    """Returns a queue of canned responses, recording every call."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def draft(self, *, health, pm, extra_constraints=""):
        self.calls.append(
            {
                "color": health.color,
                "extra": extra_constraints,
                "pm_id": pm.id,
            }
        )
        if not self.responses:
            raise RuntimeError("ScriptedLLM exhausted")
        return self.responses.pop(0)


def _signals(project_id="P-acme", project_name="Acme") -> HealthSignals:
    return HealthSignals(
        project_id=project_id,
        project_name=project_name,
        total_tasks=10,
        overdue_count=4,
        stuck_blocker_count=2,
        untouched_count=0,
        missed_deadline_count=1,
        recent_missed_deadline_count=1,
        scope_creep_count=1,
        worst_examples=["[stuck blocker] CMS migration"],
    )


def _health(color: HealthColor) -> ProjectHealth:
    return ProjectHealth(
        project_id="P-acme",
        project_name="Acme",
        color=color,
        reasoning="Two stuck blockers and one missed deadline drove the color.",
        signals=_signals(),
    )


# ─── CLAUDE.md §9 mandatory test ──────────────────────────────────────────


def test_red_status_update_cannot_contain_soft_pedal_words():
    """First attempt has 'minor'; LLM is given the offender list and retries clean."""
    llm = ScriptedLLM(
        [
            {
                "subject": "Acme Weekly Update",
                "body": "We had a minor delay; nothing major to worry about.",
            },
            {
                "subject": "Acme Weekly Update — Action Needed",
                "body": (
                    "Two stuck blockers and one missed deadline this week. "
                    "We are scheduling a call Thursday to unblock."
                ),
            },
        ]
    )
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG), max_retries=1)

    draft = writer.draft(_health(HealthColor.RED))

    # Final body is clean.
    assert "minor" not in draft.body.lower()
    assert "nothing major" not in draft.body.lower()
    # Validator ran once + retried once → 2 LLM calls.
    assert len(llm.calls) == 2
    # Second call must have carried the offender list back to the LLM.
    assert "minor" in llm.calls[1]["extra"]
    assert "nothing major" in llm.calls[1]["extra"]
    assert draft.retry_count == 1
    assert draft.color is HealthColor.RED


def test_red_status_raises_when_soft_pedal_persists():
    """If retries are exhausted and the model keeps soft-pedalling, raise loudly."""
    llm = ScriptedLLM(
        [
            {"subject": "x", "body": "Just a slight blip this week."},
            {"subject": "x", "body": "Just a slight blip this week."},
        ]
    )
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG), max_retries=1)

    with pytest.raises(SoftPedalError) as exc_info:
        writer.draft(_health(HealthColor.RED))
    assert "slight" in exc_info.value.offenders


def test_red_clean_first_attempt_no_retry():
    llm = ScriptedLLM(
        [
            {
                "subject": "Acme Weekly",
                "body": "Two stuck blockers; one missed deadline; call Thursday.",
            }
        ]
    )
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG), max_retries=1)
    draft = writer.draft(_health(HealthColor.RED))
    assert len(llm.calls) == 1
    assert draft.retry_count == 0


# ─── Yellow / green do NOT trigger the validator (§9 spec is red-only) ────


def test_yellow_status_can_contain_soft_words():
    """Per CLAUDE.md §9, the validator is red-only.

    Yellow updates still get the honesty system prompt, but no hard rejection.
    (System-prompt-level honesty is not unit-testable; behavior is by design.)
    """
    llm = ScriptedLLM(
        [{"subject": "Yellow", "body": "One minor risk; tracking through next sprint."}]
    )
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG))
    draft = writer.draft(_health(HealthColor.YELLOW))
    assert draft.color is HealthColor.YELLOW
    assert len(llm.calls) == 1  # No retry triggered.


def test_green_status_can_contain_soft_words():
    llm = ScriptedLLM(
        [{"subject": "Green", "body": "Smooth week, things are progressing well."}]
    )
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG))
    draft = writer.draft(_health(HealthColor.GREEN))
    assert draft.color is HealthColor.GREEN
    assert len(llm.calls) == 1


# ─── Safety constraints ───────────────────────────────────────────────────


def test_every_draft_requires_pm_approval():
    """CLAUDE.md §3: client-facing comms are always drafts to the PM."""
    llm = ScriptedLLM([{"subject": "x", "body": "Two stuck blockers; one miss."}])
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG))
    draft = writer.draft(_health(HealthColor.RED))
    assert draft.requires_pm_approval is True


def test_draft_carries_signals_for_audit():
    llm = ScriptedLLM([{"subject": "x", "body": "Two stuck blockers; one miss."}])
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG))
    draft = writer.draft(_health(HealthColor.RED))
    assert draft.signals.stuck_blocker_count == 2
    assert draft.signals.scope_creep_count == 1


def test_routes_to_correct_pm_via_registry():
    llm = ScriptedLLM([{"subject": "x", "body": "All quiet."}])
    writer = StatusWriter(llm, PMRegistry.from_json(CONFIG))
    draft = writer.draft(_health(HealthColor.GREEN))
    assert draft.pm_id == "pm-alex"  # P-acme owned by Alex per fixture
    assert draft.pm_name == "Alex Chen"


def test_max_retries_must_be_nonneg():
    with pytest.raises(ValueError):
        StatusWriter(ScriptedLLM([]), PMRegistry.from_json(CONFIG), max_retries=-1)
