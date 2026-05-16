"""DraftSink tests — focused on FileSystemDraftSink and CompositeSink.

SlackDraftSink and GmailDraftSink hit live APIs and live behind the
`integration` marker; they are not exercised here.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from src.briefing.briefing import DailyBriefing, ProjectSummary
from src.drafters.models import (
    AgendaDraft,
    EscalationDraft,
    NudgeDraft,
    StatusDraft,
)
from src.health.models import HealthColor, HealthSignals
from src.monitor.models import TaskFlag
from src.scheduler.sinks import CompositeSink, FileSystemDraftSink


def _nudge() -> NudgeDraft:
    return NudgeDraft(
        task_id="T-1",
        task_name="Wire CMS",
        project_id="P-1",
        project_name="Acme",
        pm_id="pm-alex",
        pm_name="Alex",
        flag=TaskFlag.STUCK_BLOCKER,
        body="hey",
    )


def _status() -> StatusDraft:
    sig = HealthSignals(
        project_id="P-1",
        project_name="Acme",
        total_tasks=1,
        overdue_count=0,
        stuck_blocker_count=0,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=0,
    )
    return StatusDraft(
        project_id="P-1",
        project_name="Acme",
        pm_id="pm-alex",
        pm_name="Alex",
        color=HealthColor.GREEN,
        subject="Acme Weekly",
        body="All quiet.",
        signals=sig,
    )


def _escalation() -> EscalationDraft:
    sig = HealthSignals(
        project_id="P-1",
        project_name="Acme",
        total_tasks=1,
        overdue_count=0,
        stuck_blocker_count=2,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=0,
    )
    return EscalationDraft(
        project_id="P-1",
        project_name="Acme",
        pm_id="pm-alex",
        pm_name="Alex",
        channel="#leadership",
        body="Acme red.",
        previous_color=HealthColor.YELLOW,
        new_color=HealthColor.RED,
        signals=sig,
    )


def _agenda() -> AgendaDraft:
    sig = HealthSignals(
        project_id="P-1",
        project_name="Acme",
        total_tasks=1,
        overdue_count=0,
        stuck_blocker_count=0,
        untouched_count=0,
        missed_deadline_count=0,
        recent_missed_deadline_count=0,
        scope_creep_count=0,
    )
    return AgendaDraft(
        project_id="P-1",
        project_name="Acme",
        pm_id="pm-alex",
        pm_name="Alex",
        meeting_date=date(2026, 5, 20),
        body="Recap...",
        signals=sig,
    )


def _briefing() -> DailyBriefing:
    return DailyBriefing(
        pm_id="pm-alex",
        pm_name="Alex",
        as_of=date(2026, 5, 15),
        body="Morning, Alex.",
        summaries=[
            ProjectSummary(
                project_id="P-1",
                project_name="Acme",
                color=HealthColor.GREEN,
                headline="0 stuck, 0 overdue, 0 scope, 0 missed-this-week",
            )
        ],
    )


# ─── FileSystemDraftSink ──────────────────────────────────────────────────


def test_fs_sink_writes_nudge_under_dated_directory(tmp_path):
    sink = FileSystemDraftSink(tmp_path)
    sink.emit_nudge(_nudge())

    today = date.today().isoformat()
    files = list((tmp_path / today / "nudges").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["task_id"] == "T-1"
    assert data["requires_pm_approval"] is True


def test_fs_sink_writes_all_draft_types(tmp_path):
    sink = FileSystemDraftSink(tmp_path)
    sink.emit_nudge(_nudge())
    sink.emit_status(_status())
    sink.emit_escalation(_escalation())
    sink.emit_agenda(_agenda())
    sink.emit_briefing(_briefing())

    today = date.today().isoformat()
    for subdir in ("nudges", "status", "escalations", "agendas", "briefings"):
        files = list((tmp_path / today / subdir).glob("*.json"))
        assert len(files) == 1, f"expected one file in {subdir}, found {len(files)}"


def test_fs_sink_writes_pm_id_in_briefing(tmp_path):
    sink = FileSystemDraftSink(tmp_path)
    sink.emit_briefing(_briefing())
    today = date.today().isoformat()
    data = json.loads((tmp_path / today / "briefings" / "pm-alex.json").read_text())
    assert data["pm_id"] == "pm-alex"


# ─── CompositeSink ────────────────────────────────────────────────────────


class RecordingSink:
    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[str] = []
        self.fail_on = fail_on

    def _record(self, name):
        self.calls.append(name)
        if self.fail_on == name:
            raise RuntimeError(f"sink failure on {name}")

    def emit_nudge(self, _): self._record("nudge")
    def emit_status(self, _): self._record("status")
    def emit_escalation(self, _): self._record("escalation")
    def emit_agenda(self, _): self._record("agenda")
    def emit_briefing(self, _): self._record("briefing")


def test_composite_sink_fans_out_to_all_sinks():
    a, b = RecordingSink(), RecordingSink()
    sink = CompositeSink([a, b])
    sink.emit_nudge(_nudge())
    assert a.calls == ["nudge"]
    assert b.calls == ["nudge"]


def test_composite_sink_failure_in_one_does_not_block_others():
    """One sink raising must not stop the next sink from receiving the draft."""
    failing = RecordingSink(fail_on="nudge")
    working = RecordingSink()
    sink = CompositeSink([failing, working])
    sink.emit_nudge(_nudge())  # should not raise
    assert failing.calls == ["nudge"]
    assert working.calls == ["nudge"]  # still called despite failing sibling
