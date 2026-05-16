"""Classifier tests.

Covers:
- CLAUDE.md §9 required test: "stuck blocker vs untouched correctly classified"
- CLAUDE.md §6 Step 4c edge cases (the four regression scenarios verbatim)
- Threshold boundaries, status normalization, overdue + missed_deadline branches
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src.monitor.classifier import classify, classify_all
from src.monitor.models import TaskFlag

AS_OF = date(2026, 5, 15)


def days_ago(n: int) -> datetime:
    return datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc) - timedelta(days=n)


# ─── CLAUDE.md §6 Step 4c regression scenarios ────────────────────────────


def test_stuck_blocker_in_progress_5_days_with_deps(task_factory):
    """In progress 5 days, has deps → stuck_blocker, NOT untouched."""
    t = task_factory(status="in progress", updated_at=days_ago(5), dependencies=["dep"])
    flags = classify(t, AS_OF).flags
    assert TaskFlag.STUCK_BLOCKER in flags
    assert TaskFlag.UNTOUCHED not in flags


def test_untouched_to_do_5_days_no_deps(task_factory):
    """To do 5 days, no deps → untouched, NOT stuck_blocker."""
    t = task_factory(status="to do", updated_at=days_ago(5), dependencies=[])
    flags = classify(t, AS_OF).flags
    assert TaskFlag.UNTOUCHED in flags
    assert TaskFlag.STUCK_BLOCKER not in flags


# ─── CLAUDE.md §9 mandatory test ──────────────────────────────────────────


def test_stuck_vs_untouched_correctly_classified(task_factory):
    """Same age, same workspace — the *only* differentiators are status + deps."""
    age = days_ago(7)
    stuck = task_factory(id="A", status="in progress", updated_at=age, dependencies=["x"])
    untouched = task_factory(id="B", status="to do", updated_at=age, dependencies=[])

    assert classify(stuck, AS_OF).flags == frozenset({TaskFlag.STUCK_BLOCKER})
    assert classify(untouched, AS_OF).flags == frozenset({TaskFlag.UNTOUCHED})


# ─── Negative space — neither category should fire ────────────────────────


def test_in_progress_without_deps_is_not_stuck_blocker(task_factory):
    """No deps → cannot be a blocker per CLAUDE.md rule. Just slow progress."""
    t = task_factory(status="in progress", updated_at=days_ago(7), dependencies=[])
    flags = classify(t, AS_OF).flags
    assert TaskFlag.STUCK_BLOCKER not in flags
    assert TaskFlag.UNTOUCHED not in flags


def test_to_do_with_deps_is_not_untouched(task_factory):
    """Has deps → not 'untouched', it's waiting on upstream work."""
    t = task_factory(status="to do", updated_at=days_ago(7), dependencies=["x"])
    flags = classify(t, AS_OF).flags
    assert TaskFlag.UNTOUCHED not in flags
    assert TaskFlag.STUCK_BLOCKER not in flags


def test_recent_activity_does_not_classify(task_factory):
    """4 days < 5-day threshold → neither flag fires."""
    t_stuck_candidate = task_factory(
        status="in progress", updated_at=days_ago(4), dependencies=["x"]
    )
    t_untouched_candidate = task_factory(
        status="to do", updated_at=days_ago(4), dependencies=[]
    )
    assert TaskFlag.STUCK_BLOCKER not in classify(t_stuck_candidate, AS_OF).flags
    assert TaskFlag.UNTOUCHED not in classify(t_untouched_candidate, AS_OF).flags


# ─── Overdue + missed_deadline branches ───────────────────────────────────


def test_overdue_when_open_and_past_due(task_factory):
    t = task_factory(
        status="in progress",
        updated_at=days_ago(1),
        due_date=date(2026, 5, 10),
    )
    assert TaskFlag.OVERDUE in classify(t, AS_OF).flags


def test_overdue_does_not_fire_when_closed(task_factory):
    """A closed task can't be 'overdue' anymore — it's either on-time or missed."""
    t = task_factory(
        status="complete",
        updated_at=days_ago(1),
        closed_at=days_ago(1),
        due_date=date(2026, 5, 10),
    )
    assert TaskFlag.OVERDUE not in classify(t, AS_OF).flags


def test_missed_deadline_when_closed_after_due(task_factory):
    t = task_factory(
        status="complete",
        updated_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        due_date=date(2026, 5, 10),
    )
    assert TaskFlag.MISSED_DEADLINE in classify(t, AS_OF).flags


def test_missed_deadline_does_not_fire_when_closed_on_time(task_factory):
    t = task_factory(
        status="complete",
        updated_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
        due_date=date(2026, 5, 10),
    )
    assert TaskFlag.MISSED_DEADLINE not in classify(t, AS_OF).flags


# ─── Robustness ───────────────────────────────────────────────────────────


def test_status_normalization_case_and_whitespace(task_factory):
    """Real ClickUp statuses include 'In Progress' and ' To Do '."""
    a = task_factory(status="In Progress", updated_at=days_ago(6), dependencies=["x"])
    b = task_factory(status=" To Do ", updated_at=days_ago(6), dependencies=[])
    assert TaskFlag.STUCK_BLOCKER in classify(a, AS_OF).flags
    assert TaskFlag.UNTOUCHED in classify(b, AS_OF).flags


def test_configurable_threshold(task_factory):
    """Override stuck_blocker_days lets the rule trigger earlier when configured."""
    t = task_factory(status="in progress", updated_at=days_ago(3), dependencies=["x"])
    assert TaskFlag.STUCK_BLOCKER not in classify(t, AS_OF).flags
    assert TaskFlag.STUCK_BLOCKER in classify(t, AS_OF, stuck_blocker_days=3).flags


def test_stuck_blocker_can_also_be_overdue(task_factory):
    """Flags are independent — a single task can carry multiple labels."""
    t = task_factory(
        status="in progress",
        updated_at=days_ago(7),
        dependencies=["x"],
        due_date=date(2026, 5, 1),
    )
    flags = classify(t, AS_OF).flags
    assert TaskFlag.STUCK_BLOCKER in flags
    assert TaskFlag.OVERDUE in flags


def test_classify_all_preserves_order(task_factory):
    a = task_factory(id="A", status="to do", updated_at=days_ago(6))
    b = task_factory(id="B", status="in progress", updated_at=days_ago(6), dependencies=["x"])
    out = classify_all([a, b], AS_OF)
    assert [c.task.id for c in out] == ["A", "B"]
