"""Contract tests for ProjectProfile and ProjectProfileCatalog."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_loop.project.project_profiles import ProjectProfile
from agent_loop.project.project_profiles import ProjectProfileCatalog


def _profile(
    *,
    profile_id: str = "shipii.default",
    skill_ids: tuple[str, ...] = ("shipii.quality.gate",),
) -> ProjectProfile:
    return ProjectProfile(profile_id=profile_id, skill_ids=skill_ids)


def test_project_profile_valid_construction() -> None:
    """A project profile declares identity and ordered skill references."""
    profile = _profile()
    assert profile.profile_id == "shipii.default"
    assert profile.skill_ids == ("shipii.quality.gate",)


@pytest.mark.parametrize("profile_id", ["", "   "])
def test_project_profile_rejects_blank_profile_id(profile_id: str) -> None:
    """Blank profile ids fail closed."""
    with pytest.raises(ValueError, match="profile_id"):
        _profile(profile_id=profile_id)


@pytest.mark.parametrize("profile_id", [" padded", "padded "])
def test_project_profile_rejects_padded_profile_id(profile_id: str) -> None:
    """Padding is rejected; profile ids are never silently trimmed."""
    with pytest.raises(ValueError, match="leading or trailing"):
        _profile(profile_id=profile_id)


def test_project_profile_rejects_blank_or_padded_skill_ids() -> None:
    """Referenced skill ids must be non-empty and unpadded."""
    with pytest.raises(ValueError, match="skill_ids"):
        _profile(skill_ids=("",))
    with pytest.raises(ValueError, match="leading or trailing"):
        _profile(skill_ids=(" padded",))


def test_project_profile_rejects_duplicate_skill_ids() -> None:
    """Ambiguous duplicate skill references on one profile fail closed."""
    with pytest.raises(ValueError, match="duplicate skill_id"):
        _profile(skill_ids=("a.skill", "b.skill", "a.skill"))


def test_project_profile_allows_empty_skill_ids() -> None:
    """An empty skill set is a valid declarative profile."""
    profile = _profile(skill_ids=())
    assert profile.skill_ids == ()


def test_project_profile_catalog_get_contains_missing_and_order() -> None:
    """Catalog lookup, membership, missing KeyError, and deterministic order."""
    zebra = _profile(profile_id="zebra.profile", skill_ids=())
    alpha = _profile(profile_id="alpha.profile", skill_ids=("s1",))
    catalog = ProjectProfileCatalog.from_profiles((zebra, alpha))
    assert catalog.get("alpha.profile") == alpha
    assert "zebra.profile" in catalog
    assert [item.profile_id for item in catalog.profiles] == [
        "alpha.profile",
        "zebra.profile",
    ]
    with pytest.raises(KeyError, match="not found"):
        catalog.get("missing.profile")


def test_project_profile_catalog_rejects_duplicate_and_conflict() -> None:
    """Exact duplicates and conflicting same-id definitions are rejected."""
    profile = _profile()
    with pytest.raises(ValueError, match="duplicate ProjectProfile.profile_id"):
        ProjectProfileCatalog.from_profiles((profile, profile))
    conflict = _profile(skill_ids=())
    with pytest.raises(ValueError, match="conflicting ProjectProfile"):
        ProjectProfileCatalog.from_profiles((profile, conflict))


def test_project_profiles_module_has_no_provider_coupling() -> None:
    """Project profiles stay free of adapters, execute, and scripts imports."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "project" / "project_profiles.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith("agent_loop.adapters") for name in imported)
    assert not any(name.startswith("agent_loop.application") for name in imported)
    assert "scripts" not in imported
    assert "AgentRuntime" not in source
    assert ".execute(" not in source


def test_project_profile_ids_are_case_sensitive() -> None:
    """Abuse: profile_id case variants are distinct, never silently merged."""
    lower = _profile(profile_id="shipii.default", skill_ids=())
    mixed = _profile(profile_id="Shipii.Default", skill_ids=())
    catalog = ProjectProfileCatalog.from_profiles((lower, mixed))
    assert "shipii.default" in catalog
    assert "Shipii.Default" in catalog
    assert "SHIPII.DEFAULT" not in catalog


def test_project_profile_is_not_a_model_profile() -> None:
    """ProjectProfile must not be confused with domain ModelProfile fields."""
    profile = _profile()
    assert not hasattr(profile, "provider_id")
    assert not hasattr(profile, "model_id")
    assert hasattr(profile, "skill_ids")


def test_project_profile_skill_ids_detached_from_mutable_input() -> None:
    """Mutating the constructor list must not alter the ProjectProfile."""
    mutable: list[str] = ["skill.a", "skill.b"]
    profile = ProjectProfile(profile_id="p1", skill_ids=mutable)  # type: ignore[arg-type]
    mutable.append("skill.c")
    mutable.clear()
    assert profile.skill_ids == ("skill.a", "skill.b")
    with pytest.raises(AttributeError):
        profile.skill_ids = ()  # type: ignore[misc]


def test_project_profile_catalog_detached_from_mutable_input() -> None:
    """Catalog materializes a tuple copy of the input collection."""
    profile = _profile(skill_ids=())
    mutable: list[ProjectProfile] = [profile]
    catalog = ProjectProfileCatalog.from_profiles(mutable)
    mutable.clear()
    assert len(catalog) == 1
    assert catalog.get("shipii.default") == profile
