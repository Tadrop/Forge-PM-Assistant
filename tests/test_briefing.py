"""Daily briefing tests — deterministic so we assert structure precisely."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from src.briefing.briefing import build_briefings_for_all_pms, generate_briefing
from src.health.models import HealthColor, HealthSignals, ProjectHealth
from src.tone.models import PMProfile
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


def _signals(project_id: str, name: str, **counts) -> HealthSignals:
    return HealthSignals(
        project_id=project_id,
        project_name=name,
        total_tasks=counts.get("total_tasks", 10),
        overdue_count=counts.get("overdue_count", 0),
        stuck_blocker_count=counts.get("stuck_blocker_count", 0),
        untouched_count=counts.get("untouched_count", 0),
        missed_deadline_count=counts.get("missed_deadline_count", 0),
        recent_missed_deadline_count=counts.get("recent_missed_deadline_count", 0),
        scope_creep_count=counts.get("scope_creep_count", 0),
        worst_examples=counts.get("worst_examples", []),
    )


def _health(project_id, name, color, **counts) -> ProjectHealth:
    return ProjectHealth(
        project_id=project_id,
        project_name=name,
        color=color,
        reasoning="r",
        signals=_signals(project_id, name, **counts),
    )


def test_briefing_filters_to_pm_projects():
    """Alex owns P-acme + P-globex; should not see P-initech (Jordan's)."""
    reg = PMRegistry.from_json(CONFIG)
    alex = reg.by_id("pm-alex")
    healths = [
        _health("P-acme", "Acme", HealthColor.RED, stuck_blocker_count=2),
        _health("P-globex", "Globex", HealthColor.YELLOW, overdue_count=3),
        _health("P-initech", "Initech", HealthColor.GREEN),
    ]
    b = generate_briefing(alex, healths, as_of=date(2026, 5, 15))
    project_names = {s.project_name for s in b.summaries}
    assert "Acme" in project_names
    assert "Globex" in project_names
    assert "Initech" not in project_names


def test_briefing_sorts_red_first():
    """RED projects must appear before YELLOW before GREEN."""
    reg = PMRegistry.from_json(CONFIG)
    alex = reg.by_id("pm-alex")
    healths = [
        _health("P-globex", "Globex", HealthColor.GREEN),
        _health("P-acme", "Acme", HealthColor.RED, stuck_blocker_count=2),
    ]
    b = generate_briefing(alex, healths)
    colors = [s.color for s in b.summaries]
    assert colors == [HealthColor.RED, HealthColor.GREEN]


def test_briefing_counts_by_color():
    reg = PMRegistry.from_json(CONFIG)
    alex = reg.by_id("pm-alex")
    healths = [
        _health("P-acme", "Acme", HealthColor.RED, stuck_blocker_count=2),
        _health("P-globex", "Globex", HealthColor.YELLOW, overdue_count=3),
    ]
    b = generate_briefing(alex, healths)
    assert b.red_count == 1
    assert b.yellow_count == 1
    assert b.green_count == 0


def test_briefing_body_includes_pm_name_and_date():
    pm = PMProfile(id="p1", name="Alex Chen", tone_namespace="t", projects=["P-1"])
    b = generate_briefing(pm, [_health("P-1", "Acme", HealthColor.GREEN)], as_of=date(2026, 5, 15))
    assert "Alex Chen" in b.body
    assert "2026-05-15" in b.body


def test_briefing_body_lists_worst_examples():
    pm = PMProfile(id="p1", name="Alex", tone_namespace="t", projects=["P-1"])
    healths = [
        _health(
            "P-1",
            "Acme",
            HealthColor.RED,
            stuck_blocker_count=2,
            worst_examples=["[stuck blocker] CMS migration"],
        )
    ]
    b = generate_briefing(pm, healths)
    assert "CMS migration" in b.body


def test_briefing_handles_pm_with_no_projects():
    """If a PM has zero matching projects, body must still render cleanly."""
    pm = PMProfile(id="p1", name="Alex", tone_namespace="t", projects=["P-none"])
    b = generate_briefing(pm, [_health("P-other", "Other", HealthColor.GREEN)])
    assert b.summaries == []
    assert "Nothing to flag" in b.body


def test_build_briefings_for_all_pms_returns_one_per_pm():
    reg = PMRegistry.from_json(CONFIG)
    healths = [
        _health("P-acme", "Acme", HealthColor.RED, stuck_blocker_count=2),
        _health("P-initech", "Initech", HealthColor.GREEN),
    ]
    briefings = build_briefings_for_all_pms(reg, healths)
    pm_ids = {b.pm_id for b in briefings}
    assert pm_ids == {"pm-alex", "pm-jordan"}


def test_briefing_headline_includes_signal_counts():
    pm = PMProfile(id="p1", name="Alex", tone_namespace="t", projects=["P-1"])
    h = _health(
        "P-1",
        "Acme",
        HealthColor.YELLOW,
        stuck_blocker_count=1,
        overdue_count=3,
        scope_creep_count=0,
    )
    b = generate_briefing(pm, [h])
    headline = b.summaries[0].headline
    assert "1 stuck" in headline
    assert "3 overdue" in headline
