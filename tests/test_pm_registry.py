"""PMRegistry tests — loading, project lookup, error handling."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.tone.models import PMProfile
from src.tone.registry import PMRegistry, PMRegistryError

FIXTURE = Path(__file__).parent / "fixtures" / "config" / "sample_pms.json"


def test_from_json_loads_sample_config():
    reg = PMRegistry.from_json(FIXTURE)
    assert len(reg.all()) == 2
    assert reg.by_id("pm-alex").name == "Alex Chen"


def test_for_project_returns_assigned_pm():
    reg = PMRegistry.from_json(FIXTURE)
    assert reg.for_project("P-acme").id == "pm-alex"
    assert reg.for_project("P-initech").id == "pm-jordan"


def test_for_project_falls_back_to_default_when_unassigned():
    reg = PMRegistry.from_json(FIXTURE)
    pm = reg.for_project("UNKNOWN-PROJECT")
    assert pm.id == "pm-alex"  # default per fixture


def test_for_project_raises_when_no_default(tmp_path):
    config = tmp_path / "pms.json"
    config.write_text(
        '{"pms": [{"id": "p1", "name": "A", "tone_namespace": "tn", "projects": ["P-1"]}]}'
    )
    reg = PMRegistry.from_json(config)
    with pytest.raises(PMRegistryError):
        reg.for_project("UNKNOWN")


def test_duplicate_pm_ids_rejected():
    profiles = [
        PMProfile(id="pm-1", name="A", tone_namespace="t1"),
        PMProfile(id="pm-1", name="B", tone_namespace="t2"),
    ]
    with pytest.raises(PMRegistryError):
        PMRegistry(profiles)


def test_project_assigned_to_multiple_pms_rejected():
    profiles = [
        PMProfile(id="pm-1", name="A", tone_namespace="t1", projects=["P-1"]),
        PMProfile(id="pm-2", name="B", tone_namespace="t2", projects=["P-1"]),
    ]
    with pytest.raises(PMRegistryError, match="multiple PMs"):
        PMRegistry(profiles)


def test_empty_registry_rejected():
    with pytest.raises(PMRegistryError):
        PMRegistry([])


def test_missing_default_pm_id_rejected():
    profiles = [PMProfile(id="pm-1", name="A", tone_namespace="t1")]
    with pytest.raises(PMRegistryError):
        PMRegistry(profiles, default_pm_id="pm-doesnt-exist")


def test_missing_config_file_raises():
    with pytest.raises(PMRegistryError, match="not found"):
        PMRegistry.from_json(Path("does-not-exist.json"))
