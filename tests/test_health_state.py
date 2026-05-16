"""HealthStateStore + red-transition detection tests."""
from __future__ import annotations

from src.health.models import HealthColor, HealthSignals, ProjectHealth
from src.health.state import HealthStateStore, detect_red_transitions


def _signals(project_id: str, project_name: str = "Acme") -> HealthSignals:
    return HealthSignals(
        project_id=project_id,
        project_name=project_name,
        total_tasks=1,
        overdue_count=0,
        stuck_blocker_count=0,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=0,
        worst_examples=[],
    )


def _health(project_id: str, color: HealthColor) -> ProjectHealth:
    return ProjectHealth(
        project_id=project_id,
        project_name="Acme",
        color=color,
        reasoning="r",
        signals=_signals(project_id),
    )


def test_state_returns_none_for_unknown_project(tmp_path):
    store = HealthStateStore(tmp_path / "state.json")
    assert store.previous_color("P-new") is None


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    a = HealthStateStore(path)
    a.update("P-1", HealthColor.RED)

    b = HealthStateStore(path)
    assert b.previous_color("P-1") is HealthColor.RED


def test_state_recovers_from_corrupt_file(tmp_path):
    """Garbage on disk shouldn't crash the scheduler — best effort."""
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    store = HealthStateStore(path)
    assert store.previous_color("anything") is None


def test_yellow_to_red_is_a_transition(tmp_path):
    state = HealthStateStore(tmp_path / "state.json")
    state.update("P-1", HealthColor.YELLOW)

    transitions = detect_red_transitions([_health("P-1", HealthColor.RED)], state)
    assert [h.project_id for h in transitions] == ["P-1"]
    # State updated.
    assert state.previous_color("P-1") is HealthColor.RED


def test_green_to_red_is_a_transition(tmp_path):
    state = HealthStateStore(tmp_path / "state.json")
    state.update("P-1", HealthColor.GREEN)
    transitions = detect_red_transitions([_health("P-1", HealthColor.RED)], state)
    assert [h.project_id for h in transitions] == ["P-1"]


def test_red_to_red_is_not_a_transition(tmp_path):
    state = HealthStateStore(tmp_path / "state.json")
    state.update("P-1", HealthColor.RED)
    transitions = detect_red_transitions([_health("P-1", HealthColor.RED)], state)
    assert transitions == []  # already red; do not re-alert leadership


def test_first_seen_red_counts_as_transition(tmp_path):
    """First-ever scan with red color → alert. Better to over-alert than miss."""
    state = HealthStateStore(tmp_path / "state.json")
    transitions = detect_red_transitions([_health("P-fresh", HealthColor.RED)], state)
    assert [h.project_id for h in transitions] == ["P-fresh"]


def test_first_seen_yellow_is_not_a_transition(tmp_path):
    state = HealthStateStore(tmp_path / "state.json")
    transitions = detect_red_transitions([_health("P-fresh", HealthColor.YELLOW)], state)
    assert transitions == []


def test_red_to_yellow_resets_state(tmp_path):
    """After a project recovers, the next red flip should re-alert."""
    state = HealthStateStore(tmp_path / "state.json")
    state.update("P-1", HealthColor.RED)
    detect_red_transitions([_health("P-1", HealthColor.YELLOW)], state)
    assert state.previous_color("P-1") is HealthColor.YELLOW

    transitions = detect_red_transitions([_health("P-1", HealthColor.RED)], state)
    assert [h.project_id for h in transitions] == ["P-1"]
