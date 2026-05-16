"""Tests for the deterministic signals aggregator."""
from __future__ import annotations

from datetime import date, datetime, timezone

from src.monitor.models import ClassifiedTask, Task, TaskFlag
from src.scope.models import ScopeEvidence, SowMatch
from src.health.signals import compute_signals, group_by_project


def _task(
    *,
    id: str,
    project_id: str = "P-1",
    name: str = "x",
    status: str = "to do",
    closed_at: datetime | None = None,
) -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=id,
        name=name,
        status=status,
        project_id=project_id,
        project_name="Acme",
        created_at=now,
        updated_at=now,
        closed_at=closed_at,
    )


def _ct(task: Task, *flags: TaskFlag) -> ClassifiedTask:
    return ClassifiedTask(task=task, flags=frozenset(flags))


def _ev(task_name: str, is_creep: bool = True, project_id: str = "P-1") -> ScopeEvidence:
    match = SowMatch(chunk_id="c", section="Deliverables", page=1, text="", similarity=0.3 if is_creep else 0.9)
    return ScopeEvidence(
        task_id=task_name,
        task_name=task_name,
        project_id=project_id,
        is_scope_creep=is_creep,
        threshold=0.72,
        best_match=match,
    )


AS_OF = date(2026, 5, 15)


def test_group_by_project_buckets_correctly():
    a = _ct(_task(id="a", project_id="P-1"))
    b = _ct(_task(id="b", project_id="P-2"))
    c = _ct(_task(id="c", project_id="P-1"))
    out = group_by_project([a, b, c])
    assert set(out.keys()) == {"P-1", "P-2"}
    assert [ct.task.id for ct in out["P-1"]] == ["a", "c"]


def test_compute_signals_counts_each_flag_independently():
    classified = [
        _ct(_task(id="1"), TaskFlag.OVERDUE),
        _ct(_task(id="2"), TaskFlag.STUCK_BLOCKER),
        _ct(_task(id="3"), TaskFlag.STUCK_BLOCKER, TaskFlag.OVERDUE),
        _ct(_task(id="4"), TaskFlag.UNTOUCHED),
    ]
    s = compute_signals("P-1", "Acme", classified, scope_evidence=[], as_of=AS_OF)
    assert s.overdue_count == 2
    assert s.stuck_blocker_count == 2
    assert s.untouched_count == 1
    assert s.scope_creep_count == 0
    assert s.total_tasks == 4


def test_compute_signals_counts_scope_creep_separately():
    s = compute_signals(
        "P-1",
        "Acme",
        classified=[],
        scope_evidence=[_ev("Add native app"), _ev("Aligned task", is_creep=False)],
        as_of=AS_OF,
    )
    assert s.scope_creep_count == 1


def test_recent_missed_deadline_window_is_7_days():
    recent = _task(
        id="r",
        status="complete",
        closed_at=datetime(2026, 5, 14, tzinfo=timezone.utc),  # 1 day ago
    )
    old = _task(
        id="o",
        status="complete",
        closed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),  # >7d ago
    )
    classified = [
        _ct(recent, TaskFlag.MISSED_DEADLINE),
        _ct(old, TaskFlag.MISSED_DEADLINE),
    ]
    s = compute_signals("P-1", "Acme", classified, scope_evidence=[], as_of=AS_OF)
    assert s.missed_deadline_count == 2
    assert s.recent_missed_deadline_count == 1


def test_worst_examples_ordered_by_severity():
    """Severity order: missed → scope_creep → stuck → overdue → untouched."""
    classified = [
        _ct(_task(id="1", name="Untouched item"), TaskFlag.UNTOUCHED),
        _ct(
            _task(
                id="2",
                name="Missed deliverable",
                status="complete",
                closed_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            ),
            TaskFlag.MISSED_DEADLINE,
        ),
        _ct(_task(id="3", name="Stuck on dep"), TaskFlag.STUCK_BLOCKER),
        _ct(_task(id="4", name="Overdue task"), TaskFlag.OVERDUE),
    ]
    scope = [_ev("New scope task")]
    s = compute_signals("P-1", "Acme", classified, scope, as_of=AS_OF)
    labels = [e.split("]")[0].lstrip("[") for e in s.worst_examples]
    assert labels == ["missed deadline", "scope creep", "stuck blocker", "overdue", "untouched"]


def test_worst_examples_capped_at_five():
    classified = [
        _ct(_task(id=str(i), name=f"task-{i}"), TaskFlag.OVERDUE) for i in range(10)
    ]
    s = compute_signals("P-1", "Acme", classified, [], as_of=AS_OF)
    assert len(s.worst_examples) == 5


def test_empty_project_yields_zeros():
    s = compute_signals("P-1", "Empty", [], [], as_of=AS_OF)
    assert s.total_tasks == 0
    assert s.overdue_count == 0
    assert s.worst_examples == []
