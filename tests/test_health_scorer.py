"""Tests for the health scorer.

The color rule is a hard rule and gets pinned with exhaustive cases.
The Claude reasoner is mocked via a FakeReasoner so tests run without an API key.
"""
from __future__ import annotations

import pytest

from src.health.models import HealthColor, HealthSignals
from src.health.scorer import HealthScorer, compute_color


def _signals(**overrides) -> HealthSignals:
    base = dict(
        project_id="P-1",
        project_name="Acme",
        total_tasks=10,
        overdue_count=0,
        stuck_blocker_count=0,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=0,
        worst_examples=[],
    )
    base.update(overrides)
    return HealthSignals(**base)


class FakeReasoner:
    def __init__(self, *, response: str = "Reasoning text"):
        self.calls: list[tuple[HealthSignals, HealthColor]] = []
        self.response = response

    def write_reasoning(self, signals, color):
        self.calls.append((signals, color))
        return self.response


# ─── Color rules (deterministic) ──────────────────────────────────────────


def test_all_zero_signals_is_green():
    assert compute_color(_signals()) is HealthColor.GREEN


def test_two_stuck_blockers_is_red():
    assert compute_color(_signals(stuck_blocker_count=2)) is HealthColor.RED


def test_one_stuck_blocker_is_yellow():
    assert compute_color(_signals(stuck_blocker_count=1)) is HealthColor.YELLOW


def test_any_scope_creep_is_red():
    assert compute_color(_signals(scope_creep_count=1)) is HealthColor.RED


def test_recent_missed_deadline_is_red():
    assert (
        compute_color(_signals(recent_missed_deadline_count=1, missed_deadline_count=1))
        is HealthColor.RED
    )


def test_old_missed_deadline_only_is_yellow():
    """Historical miss (>7d ago) is yellow, not red — past sins, not active risk."""
    assert (
        compute_color(_signals(missed_deadline_count=1, recent_missed_deadline_count=0))
        is HealthColor.YELLOW
    )


def test_high_overdue_count_is_red():
    assert compute_color(_signals(overdue_count=6)) is HealthColor.RED


def test_moderate_overdue_is_yellow():
    assert compute_color(_signals(overdue_count=3)) is HealthColor.YELLOW


def test_low_overdue_is_green():
    assert compute_color(_signals(overdue_count=1)) is HealthColor.GREEN


def test_any_untouched_is_yellow():
    assert compute_color(_signals(untouched_count=1)) is HealthColor.YELLOW


# ─── Scorer orchestrator ──────────────────────────────────────────────────


def test_score_calls_reasoner_for_non_empty_projects():
    reasoner = FakeReasoner(response="Things are fine.")
    scorer = HealthScorer(reasoner)
    health = scorer.score(_signals(total_tasks=4))
    assert reasoner.calls
    assert health.reasoning == "Things are fine."


def test_score_skips_reasoner_for_empty_projects():
    """No tasks → no LLM call (saves tokens, deterministic message)."""
    reasoner = FakeReasoner()
    scorer = HealthScorer(reasoner)
    health = scorer.score(_signals(total_tasks=0))
    assert reasoner.calls == []
    assert "no active tasks" in health.reasoning


def test_score_passes_correct_color_to_reasoner():
    reasoner = FakeReasoner()
    scorer = HealthScorer(reasoner)
    scorer.score(_signals(total_tasks=5, stuck_blocker_count=2))
    assert reasoner.calls[0][1] is HealthColor.RED


def test_score_many_processes_in_order():
    reasoner = FakeReasoner()
    scorer = HealthScorer(reasoner)
    results = scorer.score_many([_signals(project_id="A", total_tasks=1), _signals(project_id="B", total_tasks=1)])
    assert [h.project_id for h in results] == ["A", "B"]


def test_health_helpers_match_color():
    reasoner = FakeReasoner()
    scorer = HealthScorer(reasoner)
    red = scorer.score(_signals(total_tasks=5, scope_creep_count=1))
    assert red.is_red is True
    assert red.is_yellow is False
