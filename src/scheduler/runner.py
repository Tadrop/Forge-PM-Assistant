"""APScheduler wiring + dependency-injection factory.

Schedule (CLAUDE.md §5):
    Daily 7am   — full scan + PM briefings
    Daily 9am   — nudge drafts (stuck + untouched)
    Friday 3pm  — weekly client status drafts
    Hourly      — red-project transition check / escalation drafts

Run as `python -m src.scheduler.runner` (or via `python -m src.cli scheduler`).
"""
from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore

from ..config import settings
from ..drafters.agenda import AgendaDrafter, ClaudeAgendaLLM
from ..drafters.escalation import ClaudeEscalationLLM, EscalationDrafter
from ..drafters.nudge import ClaudeNudgeLLM, NudgeDrafter
from ..drafters.status import ClaudeStatusLLM, StatusWriter
from ..health.scorer import ClaudeReasoner, HealthScorer
from ..health.state import HealthStateStore
from ..monitor.clickup_client import ClickUpClient
from ..scope.embeddings import VoyageEmbedder
from ..scope.scope_detector import ScopeDetector
from ..scope.sow_store import PineconeSowStore
from ..tone.registry import PMRegistry
from ..tone.tone_store import PineconeToneStore
from . import jobs
from .sinks import CompositeSink, DraftSink, FileSystemDraftSink, GmailDraftSink, SlackDraftSink

logger = logging.getLogger(__name__)

DEFAULT_PM_CONFIG = Path("./config/pms.json")
DEFAULT_DRAFTS_ROOT = Path("./drafts")
DEFAULT_STATE_PATH = Path("./state/health_state.json")


@dataclass
class Wiring:
    """Bundle of every dependency a job needs. Constructed once at startup."""

    clickup_client: ClickUpClient
    scope_detector: ScopeDetector
    health_scorer: HealthScorer
    pm_registry: PMRegistry
    nudge_drafter: NudgeDrafter
    status_writer: StatusWriter
    agenda_drafter: AgendaDrafter
    escalation_drafter: EscalationDrafter
    state_store: HealthStateStore
    sink: DraftSink


def build_sink() -> DraftSink:
    """Filesystem sink always on. Slack + Gmail layered in when keys present."""
    sinks: list[DraftSink] = [FileSystemDraftSink(DEFAULT_DRAFTS_ROOT)]
    if not settings.dry_run:
        if settings.slack_bot_token:
            sinks.append(
                SlackDraftSink(
                    bot_token=settings.slack_bot_token,
                    pm_review_channel=settings.slack_pm_review_channel,
                    leadership_channel=settings.slack_leadership_channel,
                )
            )
        else:
            logger.warning("DRY_RUN=false but SLACK_BOT_TOKEN missing — Slack sink disabled")
        gmail_creds = Path(settings.gmail_credentials_path)
        if gmail_creds.exists():
            sinks.append(
                GmailDraftSink(
                    credentials_path=settings.gmail_credentials_path,
                    token_path=settings.gmail_token_path,
                )
            )
        else:
            logger.warning("Gmail credentials not found at %s — Gmail sink disabled", gmail_creds)
    return CompositeSink(sinks) if len(sinks) > 1 else sinks[0]


def build_wiring(*, pm_config: Path = DEFAULT_PM_CONFIG) -> Wiring:
    """Wire every dependency from settings + config file. Called at startup."""
    pm_registry = PMRegistry.from_json(pm_config)
    clickup_client = ClickUpClient(settings.clickup_api_token, settings.clickup_team_id)

    embedder = VoyageEmbedder(settings.voyage_api_key, settings.voyage_model)
    sow_store = PineconeSowStore(
        api_key=settings.pinecone_api_key,
        index_name=settings.pinecone_index,
        cloud=settings.pinecone_cloud,
        region=settings.pinecone_region,
    )
    scope_detector = ScopeDetector(
        embedder=embedder,
        sow_store=sow_store,
        threshold=settings.scope_similarity_threshold,
        top_k=settings.scope_rag_top_k,
    )

    health_scorer = HealthScorer(
        reasoner=ClaudeReasoner(settings.anthropic_api_key, settings.anthropic_model)
    )

    tone_store = PineconeToneStore(
        api_key=settings.pinecone_api_key,
        index_name=f"{settings.pinecone_index}-tone",
    )
    nudge_drafter = NudgeDrafter(
        llm=ClaudeNudgeLLM(settings.anthropic_api_key, settings.anthropic_model),
        embedder=embedder,
        tone_store=tone_store,
        pm_registry=pm_registry,
    )
    status_writer = StatusWriter(
        llm=ClaudeStatusLLM(settings.anthropic_api_key, settings.anthropic_model),
        pm_registry=pm_registry,
    )
    agenda_drafter = AgendaDrafter(
        llm=ClaudeAgendaLLM(settings.anthropic_api_key, settings.anthropic_model),
        pm_registry=pm_registry,
    )
    escalation_drafter = EscalationDrafter(
        llm=ClaudeEscalationLLM(settings.anthropic_api_key, settings.anthropic_model),
        pm_registry=pm_registry,
        leadership_channel=settings.slack_leadership_channel,
    )

    return Wiring(
        clickup_client=clickup_client,
        scope_detector=scope_detector,
        health_scorer=health_scorer,
        pm_registry=pm_registry,
        nudge_drafter=nudge_drafter,
        status_writer=status_writer,
        agenda_drafter=agenda_drafter,
        escalation_drafter=escalation_drafter,
        state_store=HealthStateStore(DEFAULT_STATE_PATH),
        sink=build_sink(),
    )


# ─── Job thunks (the scheduler calls these zero-arg wrappers) ─────────────


def _morning_scan_and_briefing(w: Wiring) -> None:
    jobs.run_daily_scan_and_briefing(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        pm_registry=w.pm_registry,
        sink=w.sink,
    )


def _morning_nudges(w: Wiring) -> None:
    jobs.run_morning_nudges(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        nudge_drafter=w.nudge_drafter,
        sink=w.sink,
    )


def _friday_status(w: Wiring) -> None:
    jobs.run_weekly_status_drafts(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        status_writer=w.status_writer,
        sink=w.sink,
    )


def _hourly_red_check(w: Wiring) -> None:
    jobs.run_hourly_red_check(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        state_store=w.state_store,
        escalation_drafter=w.escalation_drafter,
        sink=w.sink,
    )


def start(*, pm_config: Path = DEFAULT_PM_CONFIG) -> None:
    """Configure APScheduler with all four jobs and start the blocking loop."""
    w = build_wiring(pm_config=pm_config)
    scheduler = BlockingScheduler(timezone=settings.tz)

    scheduler.add_job(
        _morning_scan_and_briefing,
        CronTrigger(hour=7, minute=0, timezone=settings.tz),
        args=[w],
        name="daily_7am_scan_and_briefing",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _morning_nudges,
        CronTrigger(hour=9, minute=0, timezone=settings.tz),
        args=[w],
        name="daily_9am_nudges",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _friday_status,
        CronTrigger(day_of_week="fri", hour=15, minute=0, timezone=settings.tz),
        args=[w],
        name="friday_3pm_status",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _hourly_red_check,
        IntervalTrigger(hours=1, timezone=settings.tz),
        args=[w],
        name="hourly_red_check",
        misfire_grace_time=600,
    )

    def _shutdown(signum, frame):  # noqa: ARG001
        logger.info("scheduler shutdown signal=%s", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Scheduler starting — DRY_RUN=%s tz=%s", settings.dry_run, settings.tz)
    scheduler.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    start()
