"""Orchestrate a full ClickUp scan + classification across all active projects.

Treats each ClickUp Space as one client project (CLAUDE.md "18 active projects").
If a workspace doesn't map that way, replace fetch_all_tasks accordingly.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from ..config import settings
from .classifier import classify_all
from .clickup_client import ClickUpClient, parse_task
from .models import ClassifiedTask, Task, TaskFlag

logger = logging.getLogger(__name__)


def fetch_all_tasks(client: ClickUpClient) -> list[Task]:
    """Walk Spaces → Folders/Lists → Tasks and normalize every task."""
    tasks: list[Task] = []
    for space in client.list_spaces():
        space_id = str(space["id"])
        project_name = space.get("name", space_id)

        for lst in client.list_folderless_lists(space_id):
            for raw in client.get_tasks(str(lst["id"])):
                tasks.append(parse_task(raw, space_id, project_name))

        for folder in client.list_folders(space_id):
            for lst in client.list_folder_lists(str(folder["id"])):
                for raw in client.get_tasks(str(lst["id"])):
                    tasks.append(parse_task(raw, space_id, project_name))

    return tasks


def _log_counts(classified: list[ClassifiedTask]) -> None:
    """Per-project counts per CLAUDE.md §6 Step 2 logging requirement."""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, **{f.value: 0 for f in TaskFlag}}
    )
    for ct in classified:
        bucket = counts[ct.task.project_id]
        bucket["total"] += 1
        for f in ct.flags:
            bucket[f.value] += 1
    for project_id, c in counts.items():
        logger.info("clickup_scan project_id=%s counts=%s", project_id, dict(c))


def scan(
    as_of: date | None = None,
    *,
    client: ClickUpClient | None = None,
) -> list[ClassifiedTask]:
    """Full daily scan: fetch + classify + log. Returns the classified set."""
    as_of = as_of or date.today()
    client = client or ClickUpClient(settings.clickup_api_token, settings.clickup_team_id)

    tasks = fetch_all_tasks(client)
    classified = classify_all(
        tasks,
        as_of,
        stuck_blocker_days=settings.stuck_blocker_days,
        untouched_days=settings.untouched_days,
    )
    _log_counts(classified)
    return classified
