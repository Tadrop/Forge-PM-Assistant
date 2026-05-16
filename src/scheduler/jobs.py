"""Job functions — orchestrate the pipeline. Called by APScheduler or the CLI.

Each job:
    1. Does a fresh ClickUp scan (cheap to repeat, simplifies state)
    2. Runs classifier → scope detector → health scorer
    3. Calls the relevant drafter(s)
    4. Writes results to the configured sink (filesystem + optional Slack/Gmail)

Every dependency is injected so the same functions can run in tests
against fakes and in production against real clients.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Protocol

from ..briefing.briefing import DailyBriefing, build_briefings_for_all_pms
from ..drafters.agenda import AgendaDrafter
from ..drafters.escalation import EscalationDrafter
from ..drafters.honesty import SoftPedalError
from ..drafters.models import (
    AgendaDraft,
    EscalationDraft,
    NudgeDraft,
    StatusDraft,
)
from ..drafters.nudge import NudgeDrafter
from ..drafters.status import StatusWriter
from ..health.models import HealthColor, HealthSignals, ProjectHealth
from ..health.scorer import HealthScorer
from ..health.signals import compute_signals, group_by_project
from ..health.state import HealthStateStore, detect_red_transitions
from ..monitor.classifier import classify_all
from ..monitor.clickup_client import ClickUpClient
from ..monitor.models import ClassifiedTask, TaskFlag
from ..monitor.monitor import fetch_all_tasks
from ..scope.models import ScopeEvidence
from ..scope.scope_detector import ScopeDetector
from ..tone.registry import PMRegistry

logger = logging.getLogger(__name__)


class HasEmitNudge(Protocol):
    def emit_nudge(self, draft: NudgeDraft) -> None: ...


class HasEmitStatus(Protocol):
    def emit_status(self, draft: StatusDraft) -> None: ...


class HasEmitEscalation(Protocol):
    def emit_escalation(self, draft: EscalationDraft) -> None: ...


class HasEmitAgenda(Protocol):
    def emit_agenda(self, draft: AgendaDraft) -> None: ...


class HasEmitBriefing(Protocol):
    def emit_briefing(self, briefing: DailyBriefing) -> None: ...


# ─── Shared scan pipeline ─────────────────────────────────────────────────


def daily_scan(
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    *,
    stuck_blocker_days: int = 5,
    untouched_days: int = 5,
    as_of: date | None = None,
) -> tuple[list[ClassifiedTask], dict[str, list[ScopeEvidence]], list[ProjectHealth]]:
    """Shared spine: ClickUp → classify → scope → health. Pure function over deps."""
    as_of = as_of or date.today()
    tasks = fetch_all_tasks(clickup_client)
    classified = classify_all(
        tasks,
        as_of,
        stuck_blocker_days=stuck_blocker_days,
        untouched_days=untouched_days,
    )

    by_project = group_by_project(classified)
    scope_by_project: dict[str, list[ScopeEvidence]] = {}
    healths: list[ProjectHealth] = []
    for project_id, cts in by_project.items():
        project_name = cts[0].task.project_name
        scope_evidence = scope_detector.detect_many([ct.task for ct in cts])
        scope_by_project[project_id] = scope_evidence
        sig: HealthSignals = compute_signals(
            project_id, project_name, cts, scope_evidence, as_of=as_of
        )
        healths.append(health_scorer.score(sig))

    logger.info(
        "daily_scan_complete tasks=%d projects=%d red=%d yellow=%d green=%d",
        len(classified),
        len(by_project),
        sum(1 for h in healths if h.color is HealthColor.RED),
        sum(1 for h in healths if h.color is HealthColor.YELLOW),
        sum(1 for h in healths if h.color is HealthColor.GREEN),
    )
    return classified, scope_by_project, healths


# ─── 7am: daily scan + PM briefing ────────────────────────────────────────


def run_daily_scan_and_briefing(
    *,
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    pm_registry: PMRegistry,
    sink: HasEmitBriefing,
    as_of: date | None = None,
) -> list[DailyBriefing]:
    _, _, healths = daily_scan(clickup_client, scope_detector, health_scorer, as_of=as_of)
    briefings = build_briefings_for_all_pms(pm_registry, healths, as_of=as_of)
    for b in briefings:
        sink.emit_briefing(b)
    return briefings


# ─── 9am: nudge drafts for stuck + untouched tasks ────────────────────────


def _nudge_candidates(classified: Iterable[ClassifiedTask]) -> list[tuple[ClassifiedTask, TaskFlag]]:
    """Yield (task, flag) pairs the nudge drafter can handle. Stable order."""
    out: list[tuple[ClassifiedTask, TaskFlag]] = []
    for ct in classified:
        if TaskFlag.STUCK_BLOCKER in ct.flags:
            out.append((ct, TaskFlag.STUCK_BLOCKER))
        elif TaskFlag.UNTOUCHED in ct.flags:
            out.append((ct, TaskFlag.UNTOUCHED))
    return out


def run_morning_nudges(
    *,
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    nudge_drafter: NudgeDrafter,
    sink: HasEmitNudge,
    as_of: date | None = None,
) -> list[NudgeDraft]:
    classified, _, _ = daily_scan(
        clickup_client, scope_detector, health_scorer, as_of=as_of
    )
    drafts: list[NudgeDraft] = []
    for ct, flag in _nudge_candidates(classified):
        try:
            draft = nudge_drafter.draft(ct.task, flag)
        except Exception:
            logger.exception(
                "nudge_draft_failed task_id=%s flag=%s", ct.task.id, flag.value
            )
            continue
        sink.emit_nudge(draft)
        drafts.append(draft)
    logger.info("morning_nudges_complete drafts=%d", len(drafts))
    return drafts


# ─── Friday 3pm: weekly status drafts per project ─────────────────────────


def run_weekly_status_drafts(
    *,
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    status_writer: StatusWriter,
    sink: HasEmitStatus,
    as_of: date | None = None,
) -> list[StatusDraft]:
    _, _, healths = daily_scan(
        clickup_client, scope_detector, health_scorer, as_of=as_of
    )
    drafts: list[StatusDraft] = []
    for h in healths:
        try:
            draft = status_writer.draft(h)
        except SoftPedalError as exc:
            # Critical: log loudly. PM still needs to know the project is red.
            logger.error(
                "status_draft_softpedal project_id=%s offenders=%s",
                h.project_id,
                exc.offenders,
            )
            continue
        except Exception:
            logger.exception("status_draft_failed project_id=%s", h.project_id)
            continue
        sink.emit_status(draft)
        drafts.append(draft)
    logger.info("weekly_status_complete drafts=%d", len(drafts))
    return drafts


# ─── Hourly: red-transition detection + escalation drafts ────────────────


def run_hourly_red_check(
    *,
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    state_store: HealthStateStore,
    escalation_drafter: EscalationDrafter,
    sink: HasEmitEscalation,
    as_of: date | None = None,
) -> list[EscalationDraft]:
    _, _, healths = daily_scan(
        clickup_client, scope_detector, health_scorer, as_of=as_of
    )
    # Capture previous colors BEFORE detect_red_transitions mutates state.
    previous_colors = {h.project_id: state_store.previous_color(h.project_id) for h in healths}
    transitions = detect_red_transitions(healths, state_store)

    drafts: list[EscalationDraft] = []
    for h in transitions:
        try:
            draft = escalation_drafter.draft(
                h, previous_color=previous_colors.get(h.project_id)
            )
        except Exception:
            logger.exception("escalation_draft_failed project_id=%s", h.project_id)
            continue
        sink.emit_escalation(draft)
        drafts.append(draft)
    logger.info(
        "hourly_red_check_complete transitions=%d drafts=%d",
        len(transitions),
        len(drafts),
    )
    return drafts


# ─── On-demand: meeting agenda for a single project ───────────────────────


def run_meeting_agenda(
    *,
    clickup_client: ClickUpClient,
    scope_detector: ScopeDetector,
    health_scorer: HealthScorer,
    agenda_drafter: AgendaDrafter,
    sink: HasEmitAgenda,
    project_id: str,
    meeting_date: date,
    as_of: date | None = None,
) -> AgendaDraft | None:
    _, scope_by_project, healths = daily_scan(
        clickup_client, scope_detector, health_scorer, as_of=as_of
    )
    target = next((h for h in healths if h.project_id == project_id), None)
    if target is None:
        logger.warning("agenda_no_project project_id=%s", project_id)
        return None
    scope_evidence = scope_by_project.get(project_id, [])
    draft = agenda_drafter.draft(target, meeting_date, scope_evidence=scope_evidence)
    sink.emit_agenda(draft)
    return draft
