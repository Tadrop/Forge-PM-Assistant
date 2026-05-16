"""Nudge drafter tests.

Covers:
- CLAUDE.md §9 mandatory: "PM tone profile influences nudge phrasing"
- Stuck vs untouched produces different prompts / guidance
- Flags outside {stuck_blocker, untouched} are rejected (drafter scope)
- requires_pm_approval is always True (CLAUDE.md §3 safety constraint)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.drafters.nudge import (
    FLAG_GUIDANCE,
    NudgeDrafter,
    build_user_prompt,
)
from src.monitor.models import Task, TaskFlag
from src.tone.models import PMProfile, ToneExample
from src.tone.registry import PMRegistry

CONFIG = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


class FakeEmbedder:
    def embed_query(self, text):
        return [1.0, 0.0]

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeToneStore:
    """Returns canned tone examples per pm_namespace — lets us prove voice influence."""

    def __init__(self, by_namespace: dict[str, list[ToneExample]]):
        self.by_namespace = by_namespace
        self.retrieve_calls: list[tuple[str, int]] = []

    def upsert_examples(self, pm_namespace, examples, vectors):
        self.by_namespace.setdefault(pm_namespace, []).extend(examples)

    def retrieve(self, pm_namespace, vector, top_k):
        self.retrieve_calls.append((pm_namespace, top_k))
        return self.by_namespace.get(pm_namespace, [])[:top_k]


class RecordingNudgeLLM:
    """A fake LLM that ECHOES the tone examples into its draft.

    This is the key to the 'tone profile influences phrasing' test: with a
    deterministic echo, two PMs with different tone examples produce
    measurably different output.
    """

    def __init__(self):
        self.draft_calls: list[dict] = []

    def draft(self, *, task, flag, pm, tone_examples):
        self.draft_calls.append(
            {"task_id": task.id, "flag": flag, "pm_id": pm.id, "tone": [e.text for e in tone_examples]}
        )
        sample = tone_examples[0].text if tone_examples else ""
        return f"[{pm.name} voice -> {sample}] {flag.value} on {task.name}"


def _task(*, project_id: str, name: str = "Wire up CMS", task_id: str = "T-1") -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=task_id,
        name=name,
        status="in progress",
        project_id=project_id,
        project_name="Project " + project_id,
        created_at=now,
        updated_at=now,
        assignee_names=["taylor"],
    )


# ─── CLAUDE.md §9 mandatory: tone profile influences phrasing ─────────────


def test_pm_tone_profile_influences_nudge_phrasing():
    """Same task + same flag + DIFFERENT PMs => DIFFERENT drafts.

    We prove this by giving each PM a distinct tone example and showing the
    output reflects that example. The drafter routes via PMRegistry.for_project,
    so swapping the project_id swaps the PM and the tone namespace consulted.
    """
    tone_store = FakeToneStore(
        {
            "tone-pm-alex": [
                ToneExample(
                    example_id="alex-1",
                    pm_id="pm-alex",
                    text="hey team — quick check on this one, where are we",
                )
            ],
            "tone-pm-jordan": [
                ToneExample(
                    example_id="jordan-1",
                    pm_id="pm-jordan",
                    text="Hi all, I wanted to follow up on the status here. Please advise.",
                )
            ],
        }
    )
    drafter = NudgeDrafter(
        llm=RecordingNudgeLLM(),
        embedder=FakeEmbedder(),
        tone_store=tone_store,
        pm_registry=PMRegistry.from_json(CONFIG),
    )

    alex_task = _task(project_id="P-acme")  # owned by Alex per fixture
    jordan_task = _task(project_id="P-initech")  # owned by Jordan per fixture

    alex_draft = drafter.draft(alex_task, TaskFlag.STUCK_BLOCKER)
    jordan_draft = drafter.draft(jordan_task, TaskFlag.STUCK_BLOCKER)

    assert alex_draft.body != jordan_draft.body
    assert "Alex Chen" in alex_draft.body
    assert "Jordan Park" in jordan_draft.body
    # Audit trail records which examples informed each draft.
    assert alex_draft.tone_example_ids == ["alex-1"]
    assert jordan_draft.tone_example_ids == ["jordan-1"]


def test_tone_store_consulted_with_correct_namespace():
    """The drafter must query the PM's namespace, not a generic one."""
    tone_store = FakeToneStore({})
    drafter = NudgeDrafter(
        llm=RecordingNudgeLLM(),
        embedder=FakeEmbedder(),
        tone_store=tone_store,
        pm_registry=PMRegistry.from_json(CONFIG),
        top_k=3,
    )
    drafter.draft(_task(project_id="P-acme"), TaskFlag.UNTOUCHED)
    assert tone_store.retrieve_calls == [("tone-pm-alex", 3)]


# ─── Stuck vs untouched handling ──────────────────────────────────────────


def test_stuck_blocker_and_untouched_have_distinct_guidance():
    """Flag-specific prompt guidance must differ (CLAUDE.md §6 4c)."""
    pm = PMProfile(id="pm-1", name="A", tone_namespace="t")
    stuck_prompt = build_user_prompt(
        _task(project_id="x"), TaskFlag.STUCK_BLOCKER, pm, tone_examples=[]
    )
    untouched_prompt = build_user_prompt(
        _task(project_id="x"), TaskFlag.UNTOUCHED, pm, tone_examples=[]
    )
    assert stuck_prompt != untouched_prompt
    assert "BLOCKING DEPENDENCY" in stuck_prompt
    assert "STARTED" in untouched_prompt or "START" in untouched_prompt


def test_drafter_supports_only_stuck_and_untouched():
    """Overdue / missed_deadline are escalation territory, not nudge territory."""
    assert NudgeDrafter.supports(TaskFlag.STUCK_BLOCKER) is True
    assert NudgeDrafter.supports(TaskFlag.UNTOUCHED) is True
    assert NudgeDrafter.supports(TaskFlag.OVERDUE) is False
    assert NudgeDrafter.supports(TaskFlag.MISSED_DEADLINE) is False


def test_drafter_rejects_unsupported_flag():
    drafter = NudgeDrafter(
        llm=RecordingNudgeLLM(),
        embedder=FakeEmbedder(),
        tone_store=FakeToneStore({}),
        pm_registry=PMRegistry.from_json(CONFIG),
    )
    with pytest.raises(ValueError):
        drafter.draft(_task(project_id="P-acme"), TaskFlag.OVERDUE)


# ─── Safety constraint ────────────────────────────────────────────────────


def test_every_draft_requires_pm_approval():
    """CLAUDE.md §3: 'All client-facing communication is a draft.'

    Nudges are internal but still require approval per CLAUDE.md §5
    Slack-review-queue workflow. This must always be True.
    """
    drafter = NudgeDrafter(
        llm=RecordingNudgeLLM(),
        embedder=FakeEmbedder(),
        tone_store=FakeToneStore({}),
        pm_registry=PMRegistry.from_json(CONFIG),
    )
    draft = drafter.draft(_task(project_id="P-acme"), TaskFlag.UNTOUCHED)
    assert draft.requires_pm_approval is True


def test_draft_carries_audit_trail():
    """tone_example_previews captures what the drafter saw — for PM review."""
    tone_store = FakeToneStore(
        {
            "tone-pm-alex": [
                ToneExample(
                    example_id="x", pm_id="pm-alex", text="A very long sample message " * 5
                )
            ]
        }
    )
    drafter = NudgeDrafter(
        llm=RecordingNudgeLLM(),
        embedder=FakeEmbedder(),
        tone_store=tone_store,
        pm_registry=PMRegistry.from_json(CONFIG),
    )
    draft = drafter.draft(_task(project_id="P-acme"), TaskFlag.UNTOUCHED)
    assert draft.tone_example_previews
    assert all(len(p) <= 80 for p in draft.tone_example_previews)


def test_drafter_rejects_invalid_top_k():
    with pytest.raises(ValueError):
        NudgeDrafter(
            llm=RecordingNudgeLLM(),
            embedder=FakeEmbedder(),
            tone_store=FakeToneStore({}),
            pm_registry=PMRegistry.from_json(CONFIG),
            top_k=0,
        )


def test_flag_guidance_table_covers_both_flags():
    """If somebody adds a flag type, FLAG_GUIDANCE must keep up — pinned here."""
    assert set(FLAG_GUIDANCE.keys()) == {TaskFlag.STUCK_BLOCKER, TaskFlag.UNTOUCHED}
