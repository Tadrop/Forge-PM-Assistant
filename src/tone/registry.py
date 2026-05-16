"""PM registry — loads project↔PM mapping from a JSON config file.

Why a config file (not env vars): the PM↔project mapping is structured
and changes when projects start/end. Keeping it as JSON makes it easy
for Mei to edit without touching code.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import PMProfile

logger = logging.getLogger(__name__)


class PMRegistryError(Exception):
    """Raised when the PM registry can't resolve a project."""


class PMRegistry:
    def __init__(self, profiles: list[PMProfile], *, default_pm_id: str | None = None):
        if not profiles:
            raise PMRegistryError("PM registry is empty — at least one PM required")
        self._by_id: dict[str, PMProfile] = {p.id: p for p in profiles}
        if len(self._by_id) != len(profiles):
            raise PMRegistryError("Duplicate PM ids in registry")
        self._by_project: dict[str, PMProfile] = {}
        for p in profiles:
            for project_id in p.projects:
                if project_id in self._by_project:
                    raise PMRegistryError(
                        f"Project {project_id} is assigned to multiple PMs"
                    )
                self._by_project[project_id] = p
        if default_pm_id and default_pm_id not in self._by_id:
            raise PMRegistryError(f"default_pm_id {default_pm_id} not in registry")
        self.default_pm_id = default_pm_id

    @classmethod
    def from_json(cls, path: Path) -> "PMRegistry":
        if not path.exists():
            raise PMRegistryError(f"PM config not found at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles = [PMProfile(**p) for p in data.get("pms", [])]
        return cls(profiles, default_pm_id=data.get("default_pm_id"))

    def all(self) -> list[PMProfile]:
        return list(self._by_id.values())

    def by_id(self, pm_id: str) -> PMProfile:
        if pm_id not in self._by_id:
            raise PMRegistryError(f"Unknown PM id: {pm_id}")
        return self._by_id[pm_id]

    def for_project(self, project_id: str) -> PMProfile:
        """Returns the PM owning the project, or the default PM if configured."""
        pm = self._by_project.get(project_id)
        if pm is not None:
            return pm
        if self.default_pm_id:
            logger.info(
                "pm_registry no_owner project_id=%s falling_back_to=%s",
                project_id,
                self.default_pm_id,
            )
            return self._by_id[self.default_pm_id]
        raise PMRegistryError(
            f"No PM assigned to project {project_id} and no default configured"
        )
