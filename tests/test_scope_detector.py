"""Scope detector tests — use fakes for embedder and SOW store.

Covers:
- CLAUDE.md §9 required test: "scope flag requires SOW evidence"
- CLAUDE.md §6 Step 4c regression: "New task matching a SOW deliverable
  phrased differently must NOT flag" + "genuinely new task must flag"
- Threshold boundary, empty SOW handling, sorted matches contract
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.monitor.models import Task
from src.scope.models import SowChunk, SowMatch
from src.scope.scope_detector import ScopeDetector


class FakeEmbedder:
    """Embeds by stuffing the text + a synthetic id into a dim-2 vector.

    Tests don't actually need real semantic similarity — they need to control
    what similarity FakeSowStore reports. The vector is opaque to FakeSowStore.
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []  # (mode, text)

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(("query", text))
        return [1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for t in texts:
            self.calls.append(("doc", t))
        return [[1.0, 0.0] for _ in texts]


class FakeSowStore:
    """In-memory store that returns canned matches per (project_id, query)."""

    def __init__(self, matches_by_project: dict[str, list[SowMatch]]):
        self.matches_by_project = matches_by_project
        self.upserts: list[tuple[str, list[SowChunk]]] = []

    def upsert(self, project_id, chunks, vectors):
        self.upserts.append((project_id, list(chunks)))

    def query(self, project_id, vector, top_k):
        return self.matches_by_project.get(project_id, [])[:top_k]

    def delete_project(self, project_id):
        self.matches_by_project.pop(project_id, None)


def _task(name: str, project_id: str = "P-acme", task_id: str = "T-1") -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=task_id,
        name=name,
        status="to do",
        project_id=project_id,
        project_name="Acme",
        created_at=now,
        updated_at=now,
    )


def _match(section: str, sim: float, text: str = "deliverables...") -> SowMatch:
    return SowMatch(chunk_id=f"c-{section}", section=section, page=3, text=text, similarity=sim)


# ─── CLAUDE.md §9 mandatory: scope flag requires SOW evidence ─────────────


def test_scope_flag_requires_sow_evidence():
    """Every flagged task must carry a best_match with section + similarity."""
    store = FakeSowStore({"P-acme": [_match("Deliverables", 0.40)]})
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)

    evidence = detector.detect(_task("Add native mobile app"))

    assert evidence.is_scope_creep is True
    assert evidence.best_match is not None
    assert evidence.best_match.section == "Deliverables"
    assert evidence.best_match.similarity == pytest.approx(0.40)
    assert evidence.threshold == pytest.approx(0.72)
    assert "section 'Deliverables'" in evidence.evidence_summary
    assert "0.40" in evidence.evidence_summary


# ─── CLAUDE.md §6 Step 4c regression scenarios ────────────────────────────


def test_task_matching_sow_phrased_differently_is_not_flagged():
    """Edge case: paraphrased SOW deliverable should NOT trigger scope flag."""
    # "Migrate WordPress content into Webflow CMS" paraphrases the SOW line
    # "Migration of existing blog posts into Webflow CMS" — high similarity.
    store = FakeSowStore({"P-acme": [_match("Deliverables", 0.91)]})
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)

    evidence = detector.detect(_task("Migrate WordPress content into Webflow CMS"))

    assert evidence.is_scope_creep is False
    assert evidence.best_match.similarity == pytest.approx(0.91)


def test_genuinely_new_task_with_no_sow_basis_is_flagged_with_evidence():
    """Edge case: real scope creep — task has no parallel anywhere in SOW."""
    store = FakeSowStore(
        {
            "P-acme": [
                _match("Deliverables", 0.31),
                _match("Out Of Scope", 0.45),
                _match("Timeline", 0.22),
            ]
        }
    )
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)

    evidence = detector.detect(_task("Build a custom iOS native app for offline mode"))

    assert evidence.is_scope_creep is True
    # Best match is the highest, even if still below threshold.
    assert evidence.best_match.section == "Out Of Scope"
    assert evidence.best_match.similarity == pytest.approx(0.45)
    # all_matches sorted descending so PMs see ranked alternatives.
    assert [m.similarity for m in evidence.all_matches] == sorted(
        [0.31, 0.45, 0.22], reverse=True
    )


# ─── Threshold + edge behavior ────────────────────────────────────────────


def test_at_threshold_is_not_flagged():
    """The rule is `< threshold`; exactly at threshold is on-scope."""
    store = FakeSowStore({"P-acme": [_match("Deliverables", 0.72)]})
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)
    assert detector.detect(_task("borderline task")).is_scope_creep is False


def test_just_below_threshold_is_flagged():
    store = FakeSowStore({"P-acme": [_match("Deliverables", 0.7199)]})
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)
    assert detector.detect(_task("borderline task")).is_scope_creep is True


def test_no_sow_indexed_does_not_flag():
    """If we have no SOW for the project, we cannot judge — never flag blind."""
    store = FakeSowStore({})  # no project indexed
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)
    evidence = detector.detect(_task("Anything"))
    assert evidence.is_scope_creep is False
    assert evidence.best_match is None
    assert "cannot evaluate" in evidence.evidence_summary


def test_invalid_threshold_raises():
    with pytest.raises(ValueError):
        ScopeDetector(FakeEmbedder(), FakeSowStore({}), threshold=1.5)
    with pytest.raises(ValueError):
        ScopeDetector(FakeEmbedder(), FakeSowStore({}), threshold=-0.1)


def test_invalid_top_k_raises():
    with pytest.raises(ValueError):
        ScopeDetector(FakeEmbedder(), FakeSowStore({}), top_k=0)


def test_detect_many_returns_all_results():
    store = FakeSowStore({"P-acme": [_match("Deliverables", 0.40)]})
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)
    tasks = [_task("X", task_id="T-1"), _task("Y", task_id="T-2")]
    results = detector.detect_many(tasks)
    assert [e.task_id for e in results] == ["T-1", "T-2"]


def test_flagged_only_filters_to_creep():
    store = FakeSowStore(
        {
            "P-acme": [_match("Deliverables", 0.95)],
            "P-other": [_match("Deliverables", 0.20)],
        }
    )
    detector = ScopeDetector(FakeEmbedder(), store, threshold=0.72)
    on_scope = _task("aligned task", project_id="P-acme", task_id="T-1")
    creep = _task("new feature", project_id="P-other", task_id="T-2")
    flagged = detector.flagged_only([on_scope, creep])
    assert [e.task_id for e in flagged] == ["T-2"]
