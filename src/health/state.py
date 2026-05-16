"""Persisted health-state tracker.

Used by the escalation drafter to detect non-red → red color transitions
(CLAUDE.md §5 RED-PROJECT ESCALATION, §6 Step 4c: "Project flips red
mid-day → escalation draft fires within the hour").

Backing store is a plain JSON file. One-writer assumption — the scheduler
serializes the hourly check job, so we don't need locking.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import HealthColor, ProjectHealth

logger = logging.getLogger(__name__)


class HealthStateStore:
    """Maps project_id → last-known HealthColor, persisted to disk."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {k: str(v) for k, v in raw.items() if isinstance(v, str)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("health_state load_failed path=%s err=%s", self.path, exc)
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def previous_color(self, project_id: str) -> HealthColor | None:
        v = self._data.get(project_id)
        if v is None:
            return None
        try:
            return HealthColor(v)
        except ValueError:
            return None

    def update(self, project_id: str, color: HealthColor) -> None:
        self._data[project_id] = color.value
        self._save()


def detect_red_transitions(
    healths: list[ProjectHealth],
    state: HealthStateStore,
) -> list[ProjectHealth]:
    """Return health records that just flipped non-red → red. Also updates state.

    First-seen projects: if a project has no prior state AND is currently red,
    it counts as a transition (better to over-alert than miss the first scan
    of a newly-onboarded project).
    """
    transitions: list[ProjectHealth] = []
    for h in healths:
        prev = state.previous_color(h.project_id)
        if h.color is HealthColor.RED and prev is not HealthColor.RED:
            transitions.append(h)
        state.update(h.project_id, h.color)
    return transitions
