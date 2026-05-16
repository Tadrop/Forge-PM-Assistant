"""Thin wrapper around the ClickUp v2 REST API.

All network I/O lives here so classifier.py can stay pure and unit-testable.
Auth: personal API token passed via the Authorization header (no Bearer prefix).
Docs: https://clickup.com/api
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests.exceptions import HTTPError, RequestException

from .models import Task

logger = logging.getLogger(__name__)

BASE_URL = "https://api.clickup.com/api/v2"
PAGE_SIZE_CAP = 100


class ClickUpError(Exception):
    """Raised when ClickUp returns an unrecoverable error."""


class ClickUpClient:
    """Minimal client — only the read endpoints needed by the monitor."""

    def __init__(self, token: str, team_id: str, *, timeout: int = 20):
        if not token:
            raise ValueError("CLICKUP_API_TOKEN is required")
        if not team_id:
            raise ValueError("CLICKUP_TEAM_ID is required")
        self.token = token
        self.team_id = team_id
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Authorization": token})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except HTTPError as exc:
            body = exc.response.text if exc.response is not None else "<no body>"
            logger.error(
                "ClickUp HTTP error endpoint=%s params=%s response=%s",
                path,
                params,
                body,
            )
            raise ClickUpError(f"ClickUp {path} failed: {exc}") from exc
        except RequestException as exc:
            logger.error("ClickUp network error endpoint=%s params=%s", path, params)
            raise ClickUpError(f"ClickUp {path} network error: {exc}") from exc

    def list_spaces(self) -> list[dict[str, Any]]:
        return self._get(
            f"/team/{self.team_id}/space", params={"archived": "false"}
        ).get("spaces", [])

    def list_folders(self, space_id: str) -> list[dict[str, Any]]:
        return self._get(
            f"/space/{space_id}/folder", params={"archived": "false"}
        ).get("folders", [])

    def list_folderless_lists(self, space_id: str) -> list[dict[str, Any]]:
        return self._get(
            f"/space/{space_id}/list", params={"archived": "false"}
        ).get("lists", [])

    def list_folder_lists(self, folder_id: str) -> list[dict[str, Any]]:
        return self._get(
            f"/folder/{folder_id}/list", params={"archived": "false"}
        ).get("lists", [])

    def get_tasks(self, list_id: str) -> list[dict[str, Any]]:
        """Paginated fetch of all tasks (including closed) for a list."""
        tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            data = self._get(
                f"/list/{list_id}/task",
                params={
                    "page": page,
                    "subtasks": "true",
                    "include_closed": "true",
                    "order_by": "updated",
                    "reverse": "true",
                },
            )
            batch = data.get("tasks", [])
            if not batch:
                break
            tasks.extend(batch)
            if len(batch) < PAGE_SIZE_CAP:
                break
            page += 1
            time.sleep(0.2)  # courteous rate-limit
        return tasks


def _parse_ms(value: Any) -> datetime | None:
    """ClickUp returns timestamps as millisecond-epoch strings or ints."""
    if value in (None, "", "null"):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def parse_task(raw: dict[str, Any], project_id: str, project_name: str) -> Task:
    """Normalize a raw ClickUp task dict into our Task model.

    Dependency normalization is critical: ClickUp returns BOTH forward and
    reverse links in `dependencies`. We keep only forward (this task waits on X)
    because the classifier rule "has dependencies" means "blocked by something".
    """
    status_obj = raw.get("status") or {}
    if isinstance(status_obj, dict):
        status_name = status_obj.get("status") or ""
    else:
        status_name = str(status_obj)

    deps_raw = raw.get("dependencies") or []
    task_id = str(raw.get("id"))
    dependencies = [
        str(d.get("depends_on"))
        for d in deps_raw
        if isinstance(d, dict)
        and str(d.get("task_id")) == task_id
        and d.get("depends_on")
    ]

    assignees = raw.get("assignees") or []
    created = _parse_ms(raw.get("date_created")) or datetime.now(timezone.utc)
    updated = _parse_ms(raw.get("date_updated")) or created
    closed = _parse_ms(raw.get("date_closed"))
    due_dt = _parse_ms(raw.get("due_date"))

    list_obj = raw.get("list")
    list_id = str(list_obj["id"]) if isinstance(list_obj, dict) and list_obj.get("id") else None

    return Task(
        id=task_id,
        name=raw.get("name", ""),
        status=status_name,
        project_id=project_id,
        project_name=project_name,
        list_id=list_id,
        assignee_ids=[str(a.get("id")) for a in assignees if a.get("id") is not None],
        assignee_names=[a.get("username", "") for a in assignees if a.get("username")],
        due_date=due_dt.date() if due_dt else None,
        created_at=created,
        updated_at=updated,
        closed_at=closed,
        dependencies=dependencies,
        url=raw.get("url"),
    )
