"""Shared pytest fixtures.

The task_factory fixture lets every test build a Task with explicit overrides
for just the fields that matter to that test — avoids brittle inline literals.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from src.monitor.models import Task

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_task(
    *,
    id: str = "T-1",
    name: str = "Sample task",
    status: str = "to do",
    project_id: str = "P-1",
    project_name: str = "Acme Website",
    list_id: str | None = None,
    assignee_ids: list[str] | None = None,
    assignee_names: list[str] | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
    due_date: date | None = None,
    dependencies: list[str] | None = None,
    url: str | None = None,
) -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=id,
        name=name,
        status=status,
        project_id=project_id,
        project_name=project_name,
        list_id=list_id,
        assignee_ids=assignee_ids or [],
        assignee_names=assignee_names or [],
        updated_at=updated_at or now,
        created_at=created_at or now,
        closed_at=closed_at,
        due_date=due_date,
        dependencies=dependencies or [],
        url=url,
    )


@pytest.fixture
def task_factory() -> Callable[..., Task]:
    return _make_task


@pytest.fixture
def sample_tasks_raw() -> list[dict[str, Any]]:
    """Raw ClickUp-shaped task dicts for parse_task tests."""
    with (FIXTURES_DIR / "tasks" / "sample_tasks.json").open() as f:
        return json.load(f)
