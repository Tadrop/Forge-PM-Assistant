"""Tests for the ClickUp client — focused on parsing the API's quirks.

Network calls are not exercised here; they live behind the `integration` marker
and require CLICKUP_API_TOKEN at runtime.
"""
from __future__ import annotations

from datetime import timezone

from src.monitor.clickup_client import parse_task


def test_parse_task_normalizes_core_fields(sample_tasks_raw):
    raw = sample_tasks_raw[0]
    t = parse_task(raw, project_id="P-1", project_name="Acme")
    assert t.id == "S-stuck-1"
    assert t.normalized_status == "in progress"
    assert t.has_dependencies is True
    assert t.dependencies == ["S-blocker-source"]
    assert t.assignee_names == ["alex"]
    assert t.created_at.tzinfo == timezone.utc
    assert t.project_name == "Acme"


def test_parse_task_handles_missing_optional_fields():
    raw = {
        "id": "abc",
        "name": "x",
        "status": {"status": "to do"},
        "date_created": "1715000000000",
        "date_updated": "1715000000000",
    }
    t = parse_task(raw, project_id="P", project_name="Q")
    assert t.due_date is None
    assert t.closed_at is None
    assert t.dependencies == []
    assert t.assignee_ids == []
    assert t.url is None


def test_parse_task_filters_reverse_dependencies():
    """ClickUp returns BOTH forward and reverse links in `dependencies`.

    Only forward links (this task waits on X) should count toward
    `has_dependencies`, since the classifier rule means "blocked by upstream".
    """
    raw = {
        "id": "abc",
        "name": "x",
        "status": {"status": "to do"},
        "date_created": "1715000000000",
        "date_updated": "1715000000000",
        "dependencies": [
            {"task_id": "abc", "depends_on": "fwd"},      # forward → keep
            {"task_id": "rev", "depends_on": "abc"},      # reverse → drop
        ],
    }
    t = parse_task(raw, project_id="P", project_name="Q")
    assert t.dependencies == ["fwd"]


def test_parse_task_status_as_plain_string_fallback():
    """Defensive — some endpoints return status as a string, not an object."""
    raw = {
        "id": "abc",
        "name": "x",
        "status": "to do",
        "date_created": "1715000000000",
        "date_updated": "1715000000000",
    }
    t = parse_task(raw, project_id="P", project_name="Q")
    assert t.normalized_status == "to do"


def test_parse_task_closed_date_preserved(sample_tasks_raw):
    """The missed-deadline classifier depends on closed_at being set."""
    missed = next(r for r in sample_tasks_raw if r["id"] == "S-missed-1")
    t = parse_task(missed, project_id="P", project_name="Q")
    assert t.closed_at is not None
    assert t.is_closed is True
