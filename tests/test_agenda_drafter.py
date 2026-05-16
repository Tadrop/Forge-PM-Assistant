"""Agenda drafter tests."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from src.drafters.agenda import AgendaDrafter
from src.health.models import HealthColor, HealthSignals, ProjectHealth
from src.scope.models import ScopeEvidence, SowMatch
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


class RecordingLLM:
    def __init__(self, body: str = "Recap of last week\n- ...\n"):
        self.body = body
        self.calls: list[dict] = []

    def draft(self, *, health, scope_evidence, pm, meeting_date):
        self.calls.append(
            {
                "project_id": health.project_id,
                "pm_id": pm.id,
                "meeting_date": meeting_date,
                "scope_count": len(scope_evidence),
            }
        )
        return self.body


def _health(color: HealthColor = HealthColor.YELLOW) -> ProjectHealth:
    sig = HealthSignals(
        project_id="P-acme",
        project_name="Acme",
        total_tasks=8,
        overdue_count=1,
        stuck_blocker_count=1,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=1,
        worst_examples=["[stuck blocker] CMS migration"],
    )
    return ProjectHealth(
        project_id="P-acme",
        project_name="Acme",
        color=color,
        reasoning="One stuck blocker; one scope-creep item to discuss.",
        signals=sig,
    )


def _scope_evidence(name: str, is_creep: bool = True) -> ScopeEvidence:
    return ScopeEvidence(
        task_id=name,
        task_name=name,
        project_id="P-acme",
        is_scope_creep=is_creep,
        threshold=0.72,
        best_match=SowMatch(
            chunk_id="c",
            section="Deliverables",
            page=3,
            text="Migration of existing blog posts...",
            similarity=0.40 if is_creep else 0.90,
        ),
    )


def test_drafter_routes_to_correct_pm():
    llm = RecordingLLM()
    drafter = AgendaDrafter(llm, PMRegistry.from_json(CONFIG))
    draft = drafter.draft(_health(), date(2026, 5, 20))
    assert draft.pm_id == "pm-alex"
    assert draft.meeting_date == date(2026, 5, 20)


def test_drafter_surfaces_open_scope_items():
    llm = RecordingLLM()
    drafter = AgendaDrafter(llm, PMRegistry.from_json(CONFIG))
    scope = [
        _scope_evidence("Add native iOS app"),
        _scope_evidence("Migrate WordPress posts", is_creep=False),
    ]
    draft = drafter.draft(_health(), date(2026, 5, 20), scope_evidence=scope)
    assert draft.open_scope_items == ["Add native iOS app"]
    assert llm.calls[0]["scope_count"] == 2  # both passed to LLM as context


def test_drafter_carries_signals_for_audit():
    llm = RecordingLLM()
    drafter = AgendaDrafter(llm, PMRegistry.from_json(CONFIG))
    draft = drafter.draft(_health(), date(2026, 5, 20))
    assert draft.signals.scope_creep_count == 1
    assert draft.requires_pm_approval is True


def test_drafter_no_scope_evidence_defaults_to_empty():
    llm = RecordingLLM()
    drafter = AgendaDrafter(llm, PMRegistry.from_json(CONFIG))
    draft = drafter.draft(_health(), date(2026, 5, 20))
    assert draft.open_scope_items == []


def test_drafter_passes_meeting_date_to_llm():
    llm = RecordingLLM()
    drafter = AgendaDrafter(llm, PMRegistry.from_json(CONFIG))
    drafter.draft(_health(), date(2026, 5, 20))
    assert llm.calls[0]["meeting_date"] == date(2026, 5, 20)
