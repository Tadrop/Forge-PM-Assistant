"""CLI — run individual jobs on demand. Useful for testing + manual triggers.

Examples (from project root):

    python -m src.cli briefing
    python -m src.cli nudges
    python -m src.cli status
    python -m src.cli hourly
    python -m src.cli agenda --project P-acme --date 2026-05-22
    python -m src.cli ingest-sow --project P-acme --path ./sows/acme.pdf
    python -m src.cli scheduler          # start the long-running scheduler

CLAUDE.md §8 requires "single command for each job" — this is it.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger("forge_pm.cli")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _cmd_briefing(_: argparse.Namespace) -> int:
    from .scheduler.runner import build_wiring
    from .scheduler import jobs

    w = build_wiring()
    out = jobs.run_daily_scan_and_briefing(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        pm_registry=w.pm_registry,
        sink=w.sink,
    )
    logger.info("Wrote %d briefings", len(out))
    return 0


def _cmd_nudges(_: argparse.Namespace) -> int:
    from .scheduler.runner import build_wiring
    from .scheduler import jobs

    w = build_wiring()
    out = jobs.run_morning_nudges(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        nudge_drafter=w.nudge_drafter,
        sink=w.sink,
    )
    logger.info("Wrote %d nudge drafts", len(out))
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    from .scheduler.runner import build_wiring
    from .scheduler import jobs

    w = build_wiring()
    out = jobs.run_weekly_status_drafts(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        status_writer=w.status_writer,
        sink=w.sink,
    )
    logger.info("Wrote %d status drafts", len(out))
    return 0


def _cmd_hourly(_: argparse.Namespace) -> int:
    from .scheduler.runner import build_wiring
    from .scheduler import jobs

    w = build_wiring()
    out = jobs.run_hourly_red_check(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        state_store=w.state_store,
        escalation_drafter=w.escalation_drafter,
        sink=w.sink,
    )
    logger.info("Wrote %d escalation drafts", len(out))
    return 0


def _cmd_agenda(ns: argparse.Namespace) -> int:
    from .scheduler.runner import build_wiring
    from .scheduler import jobs

    w = build_wiring()
    meeting_date = date.fromisoformat(ns.date)
    out = jobs.run_meeting_agenda(
        clickup_client=w.clickup_client,
        scope_detector=w.scope_detector,
        health_scorer=w.health_scorer,
        agenda_drafter=w.agenda_drafter,
        sink=w.sink,
        project_id=ns.project,
        meeting_date=meeting_date,
    )
    if out is None:
        logger.error("No project found for id=%s", ns.project)
        return 1
    logger.info("Wrote agenda for %s on %s", out.project_id, out.meeting_date)
    return 0


def _cmd_ingest_sow(ns: argparse.Namespace) -> int:
    from .config import settings
    from .scope.embeddings import VoyageEmbedder
    from .scope.sow_ingest import ingest_sow
    from .scope.sow_store import PineconeSowStore

    embedder = VoyageEmbedder(settings.voyage_api_key, settings.voyage_model)
    store = PineconeSowStore(
        api_key=settings.pinecone_api_key,
        index_name=settings.pinecone_index,
        cloud=settings.pinecone_cloud,
        region=settings.pinecone_region,
    )
    count = ingest_sow(Path(ns.path), ns.project, embedder=embedder, store=store)
    logger.info("Ingested %d chunks from %s into project %s", count, ns.path, ns.project)
    return 0


def _cmd_scheduler(_: argparse.Namespace) -> int:
    from .scheduler.runner import start

    start()
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser("forge-pm")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("briefing", help="Run the morning briefing job (7am job)").set_defaults(
        func=_cmd_briefing
    )
    sub.add_parser("nudges", help="Draft Slack nudges (9am job)").set_defaults(
        func=_cmd_nudges
    )
    sub.add_parser("status", help="Draft weekly client status (Fri 3pm job)").set_defaults(
        func=_cmd_status
    )
    sub.add_parser("hourly", help="Hourly red-transition check").set_defaults(func=_cmd_hourly)

    agenda = sub.add_parser("agenda", help="Draft a meeting agenda for one project")
    agenda.add_argument("--project", required=True, help="ClickUp space / project id")
    agenda.add_argument("--date", required=True, help="Meeting date (YYYY-MM-DD)")
    agenda.set_defaults(func=_cmd_agenda)

    ingest = sub.add_parser("ingest-sow", help="Index a SOW file into Pinecone")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--path", required=True)
    ingest.set_defaults(func=_cmd_ingest_sow)

    sub.add_parser("scheduler", help="Start the long-running scheduler").set_defaults(
        func=_cmd_scheduler
    )

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
