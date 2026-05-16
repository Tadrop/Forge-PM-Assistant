"""Pure classification logic for ClickUp tasks.

Rules are codified verbatim from CLAUDE.md §9 to keep behavior auditable.
This module performs no I/O — pass it Task objects and a reference date.

Rules (single source of truth):
    stuck_blocker   = status == "in progress" AND no_activity >= N AND has_deps
    untouched       = status == "to do"       AND no_activity >= N AND not has_deps
    overdue         = due_date < today AND NOT closed
    missed_deadline = closed AND closed_at > due_date
"""
from __future__ import annotations

from datetime import date

from .models import (
    IN_PROGRESS_STATUS,
    TO_DO_STATUS,
    ClassifiedTask,
    Task,
    TaskFlag,
)


def classify(
    task: Task,
    as_of: date,
    *,
    stuck_blocker_days: int = 5,
    untouched_days: int = 5,
) -> ClassifiedTask:
    """Apply CLAUDE.md §9 rules to a single task. Returns the set of flags."""
    flags: set[TaskFlag] = set()
    activity_days = task.no_activity_days(as_of)

    if (
        task.normalized_status == IN_PROGRESS_STATUS
        and activity_days >= stuck_blocker_days
        and task.has_dependencies
    ):
        flags.add(TaskFlag.STUCK_BLOCKER)

    if (
        task.normalized_status == TO_DO_STATUS
        and activity_days >= untouched_days
        and not task.has_dependencies
    ):
        flags.add(TaskFlag.UNTOUCHED)

    if (
        task.due_date is not None
        and task.due_date < as_of
        and not task.is_closed
    ):
        flags.add(TaskFlag.OVERDUE)

    if (
        task.is_closed
        and task.closed_at is not None
        and task.due_date is not None
        and task.closed_at.date() > task.due_date
    ):
        flags.add(TaskFlag.MISSED_DEADLINE)

    return ClassifiedTask(task=task, flags=frozenset(flags))


def classify_all(
    tasks: list[Task],
    as_of: date,
    *,
    stuck_blocker_days: int = 5,
    untouched_days: int = 5,
) -> list[ClassifiedTask]:
    return [
        classify(
            t,
            as_of,
            stuck_blocker_days=stuck_blocker_days,
            untouched_days=untouched_days,
        )
        for t in tasks
    ]
