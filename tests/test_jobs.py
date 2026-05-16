"""End-to-end job tests with fakes for every external dependency.

This is the integration layer: jobs glue together monitor → scope →
health → drafters → sinks. We use REAL classes for the inner modules
(already well-tested individually) and FAKE the boundaries (ClickUp HTTP,
LLM calls, embeddings, vector stores, sinks).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.drafters.agenda import AgendaDrafter
from src.drafters.escalation import EscalationDrafter
from src.drafters.honesty import SoftPedalError
from src.drafters.nudge import NudgeDrafter
from src.drafters.status import StatusWriter
from src.health.models import HealthColor
from src.health.scorer import HealthScorer
from src.health.state import HealthStateStore
from src.scheduler import jobs
from src.scope.models import SowMatch
from src.scope.scope_detector import ScopeDetector
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"
TODAY = date(2026, 5, 15)


# ─── Fakes ────────────────────────────────────────────────────────────────


def _raw_task(
    task_id: str,
    *,
    status: str = "to do",
    updated_ms: int | None = None,
    deps: list[str] | None = None,
    due_ms: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    if updated_ms is None:
        # 7 days ago in ms
        updated_ms = int(
            (datetime(2026, 5, 15, tzinfo=timezone.utc) - timedelta(days=7)).timestamp() * 1000
        )
    return {
        "id": task_id,
        "name": name or task_id,
        "status": {"status": status},
        "date_created": str(updated_ms),
        "date_updated": str(updated_ms),
        "date_closed": None,
        "due_date": str(due_ms) if due_ms else None,
        "dependencies": [{"task_id": task_id, "depends_on": d} for d in (deps or [])],
        "assignees": [{"id": 1, "username": "taylor"}],
        "list": {"id": "L-fake"},
    }


class FakeClickUpClient:
    """Quacks like ClickUpClient for the monitor.fetch_all_tasks walker."""

    def __init__(self, spaces: list[dict], tasks_by_list: dict[str, list[dict]]):
        self.spaces = spaces
        self.tasks_by_list = tasks_by_list

    def list_spaces(self): return self.spaces
    def list_folders(self, space_id): return []  # all tasks in folderless lists for tests
    def list_folderless_lists(self, space_id):
        return [{"id": f"L-{space_id}"}]
    def list_folder_lists(self, folder_id): return []
    def get_tasks(self, list_id):
        return list(self.tasks_by_list.get(list_id, []))


class FakeEmbedder:
    def embed_query(self, text): return [1.0, 0.0]
    def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]


class FakeSowStore:
    def __init__(self, similarity: float = 0.95):
        self.similarity = similarity

    def upsert(self, *a, **kw): pass
    def query(self, project_id, vector, top_k):
        return [SowMatch(chunk_id="c", section="Deliverables", page=1, text="...", similarity=self.similarity)]
    def delete_project(self, project_id): pass


class FakeToneStore:
    def __init__(self):
        self.by_namespace: dict[str, list] = {}

    def upsert_examples(self, *a, **kw): pass
    def retrieve(self, pm_namespace, vector, top_k):
        return self.by_namespace.get(pm_namespace, [])


class FakeReasoner:
    def write_reasoning(self, signals, color): return f"{color.value} because {signals.stuck_blocker_count} stuck"


class FakeNudgeLLM:
    def __init__(self):
        self.calls = []

    def draft(self, *, task, flag, pm, tone_examples):
        self.calls.append((task.id, flag, pm.id))
        return f"<{pm.name}> nudge for {task.name}"


class FakeStatusLLM:
    """Returns clean (no soft-pedal) bodies, varied by color."""

    def __init__(self):
        self.calls = []

    def draft(self, *, health, pm, extra_constraints=""):
        self.calls.append(health.project_id)
        if health.color is HealthColor.RED:
            body = "Two stuck blockers; we are unblocking this week."
        elif health.color is HealthColor.YELLOW:
            body = "Some risks tracked; details below."
        else:
            body = "Steady progress this week; on plan."
        return {"subject": f"{health.project_name} Weekly", "body": body}


class FakeEscalationLLM:
    def draft(self, *, health, previous_color, pm, channel):
        return f"{health.project_name} just turned red; signals: stuck={health.signals.stuck_blocker_count}"


class FakeAgendaLLM:
    def draft(self, *, health, scope_evidence, pm, meeting_date):
        return f"Agenda for {health.project_name} on {meeting_date.isoformat()}"


class RecordingSink:
    """Captures every emitted artifact for assertions."""

    def __init__(self):
        self.nudges = []
        self.statuses = []
        self.escalations = []
        self.agendas = []
        self.briefings = []

    def emit_nudge(self, draft): self.nudges.append(draft)
    def emit_status(self, draft): self.statuses.append(draft)
    def emit_escalation(self, draft): self.escalations.append(draft)
    def emit_agenda(self, draft): self.agendas.append(draft)
    def emit_briefing(self, briefing): self.briefings.append(briefing)


# ─── Fixture builder ──────────────────────────────────────────────────────


def _build_pipeline(
    *,
    sow_similarity: float = 0.95,
    status_llm=None,
):
    """One ClickUp space P-acme with: 1 stuck blocker + 1 untouched + 1 fine."""
    spaces = [{"id": "P-acme", "name": "Acme"}]
    tasks_by_list = {
        "L-P-acme": [
            _raw_task("stuck-1", status="in progress", deps=["dep-1"], name="CMS migration"),
            _raw_task("untouched-1", status="to do", deps=[], name="Set up Figma"),
            _raw_task(
                "fresh-1",
                status="in progress",
                deps=[],
                updated_ms=int(
                    (datetime(2026, 5, 15, tzinfo=timezone.utc) - timedelta(days=1)).timestamp()
                    * 1000
                ),
                name="Reviewing copy",
            ),
        ]
    }
    clickup = FakeClickUpClient(spaces, tasks_by_list)

    embedder = FakeEmbedder()
    sow_store = FakeSowStore(similarity=sow_similarity)
    scope_detector = ScopeDetector(embedder, sow_store, threshold=0.72)

    scorer = HealthScorer(FakeReasoner())

    pm_registry = PMRegistry.from_json(CONFIG)
    # PM fixture has tone-pm-alex and Alex owns P-acme.

    nudge = NudgeDrafter(
        llm=FakeNudgeLLM(),
        embedder=embedder,
        tone_store=FakeToneStore(),
        pm_registry=pm_registry,
    )
    status = StatusWriter(status_llm or FakeStatusLLM(), pm_registry)
    escalation = EscalationDrafter(FakeEscalationLLM(), pm_registry, "#leadership")
    agenda = AgendaDrafter(FakeAgendaLLM(), pm_registry)

    return {
        "clickup": clickup,
        "scope": scope_detector,
        "scorer": scorer,
        "nudge": nudge,
        "status": status,
        "escalation": escalation,
        "agenda": agenda,
        "pm_registry": pm_registry,
    }


# ─── Daily scan + briefing job ────────────────────────────────────────────


def test_run_daily_scan_and_briefing_emits_one_per_pm():
    p = _build_pipeline()
    sink = RecordingSink()
    out = jobs.run_daily_scan_and_briefing(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        pm_registry=p["pm_registry"],
        sink=sink,
        as_of=TODAY,
    )
    # Fixture has 2 PMs.
    assert len(out) == 2
    assert len(sink.briefings) == 2


# ─── Morning nudges job ───────────────────────────────────────────────────


def test_morning_nudges_filters_to_stuck_and_untouched():
    p = _build_pipeline()
    sink = RecordingSink()
    drafts = jobs.run_morning_nudges(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        nudge_drafter=p["nudge"],
        sink=sink,
        as_of=TODAY,
    )
    # Two flagged tasks → two drafts. The 'fresh-1' task is not flagged.
    assert len(drafts) == 2
    assert {d.task_id for d in drafts} == {"stuck-1", "untouched-1"}
    assert len(sink.nudges) == 2


# ─── Weekly status job ────────────────────────────────────────────────────


def test_weekly_status_one_draft_per_project():
    p = _build_pipeline()
    sink = RecordingSink()
    drafts = jobs.run_weekly_status_drafts(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        status_writer=p["status"],
        sink=sink,
        as_of=TODAY,
    )
    # 1 project (P-acme) → 1 status draft
    assert len(drafts) == 1
    assert drafts[0].project_id == "P-acme"


class SoftPedalLLM:
    """Always returns offending text; never recovers — to trigger SoftPedalError."""

    def draft(self, *, health, pm, extra_constraints=""):
        return {"subject": "x", "body": "Just a slight delay this week."}


def test_weekly_status_swallows_softpedal_error_and_continues():
    """A red project that fails the validator must not crash the whole job."""
    p = _build_pipeline(
        sow_similarity=0.20,  # everything looks like scope creep → many reds
        status_llm=SoftPedalLLM(),
    )
    sink = RecordingSink()
    drafts = jobs.run_weekly_status_drafts(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        status_writer=p["status"],
        sink=sink,
        as_of=TODAY,
    )
    # The drafter failed the red one, so no drafts emitted.
    assert drafts == []
    assert sink.statuses == []
    # Important: the job returned cleanly. The failure was logged, not raised.


# ─── Hourly red-transition job ────────────────────────────────────────────


def test_hourly_red_check_emits_on_transition(tmp_path):
    """First scan of a red project → escalation fires."""
    p = _build_pipeline(sow_similarity=0.20)  # forces scope_creep → RED
    state = HealthStateStore(tmp_path / "s.json")
    sink = RecordingSink()
    drafts = jobs.run_hourly_red_check(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        state_store=state,
        escalation_drafter=p["escalation"],
        sink=sink,
        as_of=TODAY,
    )
    assert len(drafts) == 1
    assert drafts[0].project_id == "P-acme"
    assert sink.escalations == drafts


def test_hourly_red_check_does_not_re_emit_on_steady_red(tmp_path):
    """Second consecutive red scan must NOT re-fire the escalation."""
    p = _build_pipeline(sow_similarity=0.20)
    state = HealthStateStore(tmp_path / "s.json")
    sink = RecordingSink()

    jobs.run_hourly_red_check(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        state_store=state,
        escalation_drafter=p["escalation"],
        sink=sink,
        as_of=TODAY,
    )
    sink.escalations.clear()

    drafts = jobs.run_hourly_red_check(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        state_store=state,
        escalation_drafter=p["escalation"],
        sink=sink,
        as_of=TODAY,
    )
    assert drafts == []
    assert sink.escalations == []


# ─── On-demand agenda job ─────────────────────────────────────────────────


def test_meeting_agenda_returns_draft_for_known_project():
    p = _build_pipeline()
    sink = RecordingSink()
    out = jobs.run_meeting_agenda(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        agenda_drafter=p["agenda"],
        sink=sink,
        project_id="P-acme",
        meeting_date=date(2026, 5, 20),
        as_of=TODAY,
    )
    assert out is not None
    assert out.project_id == "P-acme"
    assert len(sink.agendas) == 1


def test_meeting_agenda_returns_none_for_unknown_project():
    p = _build_pipeline()
    sink = RecordingSink()
    out = jobs.run_meeting_agenda(
        clickup_client=p["clickup"],
        scope_detector=p["scope"],
        health_scorer=p["scorer"],
        agenda_drafter=p["agenda"],
        sink=sink,
        project_id="DOES-NOT-EXIST",
        meeting_date=date(2026, 5, 20),
        as_of=TODAY,
    )
    assert out is None
    assert sink.agendas == []


# ─── daily_scan correctness ───────────────────────────────────────────────


def test_daily_scan_returns_classified_scope_and_health():
    p = _build_pipeline()
    classified, scope, healths = jobs.daily_scan(
        p["clickup"], p["scope"], p["scorer"], as_of=TODAY
    )
    assert len(classified) == 3
    assert "P-acme" in scope
    assert len(healths) == 1
    assert healths[0].project_id == "P-acme"
