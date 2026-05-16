"""Deterministic signal aggregation per project.

Pure function — no I/O, no LLM. Inputs: classified tasks + scope evidence.
Output: HealthSignals tuple used by the scorer + drafters + briefing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..monitor.models import ClassifiedTask, TaskFlag
from ..scope.models import ScopeEvidence
from .models import HealthSignals

MAX_WORST_EXAMPLES = 5
RECENT_MISS_WINDOW_DAYS = 7


def group_by_project(
    classified: list[ClassifiedTask],
) -> dict[str, list[ClassifiedTask]]:
    """Bucket classified tasks by their project_id."""
    out: dict[str, list[ClassifiedTask]] = defaultdict(list)
    for ct in classified:
        out[ct.task.project_id].append(ct)
    return dict(out)


def _worst_examples(
    classified: list[ClassifiedTask],
    scope_evidence: list[ScopeEvidence],
) -> list[str]:
    """Up to 5 task names representing the worst signals, in severity order.

    Severity (highest first): missed_deadline, scope_creep, stuck_blocker,
    overdue, untouched. Within a severity, keeps natural input order.
    """
    examples: list[str] = []

    def add(label: str, name: str) -> None:
        line = f"[{label}] {name}"
        if line not in examples:
            examples.append(line)

    for ct in classified:
        if TaskFlag.MISSED_DEADLINE in ct.flags:
            add("missed deadline", ct.task.name)
    for ev in scope_evidence:
        if ev.is_scope_creep:
            add("scope creep", ev.task_name)
    for ct in classified:
        if TaskFlag.STUCK_BLOCKER in ct.flags:
            add("stuck blocker", ct.task.name)
    for ct in classified:
        if TaskFlag.OVERDUE in ct.flags:
            add("overdue", ct.task.name)
    for ct in classified:
        if TaskFlag.UNTOUCHED in ct.flags:
            add("untouched", ct.task.name)

    return examples[:MAX_WORST_EXAMPLES]


def compute_signals(
    project_id: str,
    project_name: str,
    classified: list[ClassifiedTask],
    scope_evidence: list[ScopeEvidence],
    *,
    as_of: date,
) -> HealthSignals:
    """Reduce a project's tasks + scope flags to a HealthSignals tuple."""
    overdue = 0
    stuck = 0
    untouched = 0
    missed = 0
    recent_missed = 0
    cutoff = as_of - timedelta(days=RECENT_MISS_WINDOW_DAYS)

    for ct in classified:
        if TaskFlag.OVERDUE in ct.flags:
            overdue += 1
        if TaskFlag.STUCK_BLOCKER in ct.flags:
            stuck += 1
        if TaskFlag.UNTOUCHED in ct.flags:
            untouched += 1
        if TaskFlag.MISSED_DEADLINE in ct.flags:
            missed += 1
            if ct.task.closed_at and ct.task.closed_at.date() >= cutoff:
                recent_missed += 1

    scope_creep = sum(1 for e in scope_evidence if e.is_scope_creep)

    return HealthSignals(
        project_id=project_id,
        project_name=project_name,
        total_tasks=len(classified),
        overdue_count=overdue,
        stuck_blocker_count=stuck,
        untouched_count=untouched,
        missed_deadline_count=missed,
        recent_missed_deadline_count=recent_missed,
        scope_creep_count=scope_creep,
        worst_examples=_worst_examples(classified, scope_evidence),
    )
