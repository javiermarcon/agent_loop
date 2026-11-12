"""Contract tests for Skill and SkillCatalog."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.project.skills import Skill
from agent_loop.project.skills import SkillCatalog


def _requirement(
    capability: str = "filesystem.write",
    *,
    axes: frozenset[CapabilityAxis] | None = None,
) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=CapabilityId(value=capability),
        mandatory_axes=axes or frozenset({CapabilityAxis.AVAILABLE}),
    )


def _skill(
    *,
    skill_id: str = "sample.quality.gate",
    required: tuple[CapabilityRequirement, ...] | None = None,
) -> Skill:
    return Skill(
        skill_id=skill_id,
        required_capabilities=required if required is not None else (_requirement(),),
    )


def test_skill_valid_construction() -> None:
    """A skill carries a stable id and declarative capability requirements."""
    skill = _skill()
    assert skill.skill_id == "sample.quality.gate"
    assert skill.required_capabilities[0].capability_id.value == "filesystem.write"


@pytest.mark.parametrize("skill_id", ["", "   "])
def test_skill_rejects_blank_skill_id(skill_id: str) -> None:
    """Blank skill ids fail closed."""
    with pytest.raises(ValueError, match="skill_id"):
        _skill(skill_id=skill_id)


@pytest.mark.parametrize("skill_id", [" padded", "padded "])
def test_skill_rejects_padded_skill_id(skill_id: str) -> None:
    """Padding is rejected; skill ids are never silently trimmed."""
    with pytest.raises(ValueError, match="leading or trailing"):
        _skill(skill_id=skill_id)


def test_skill_rejects_duplicate_capability_ids_in_requirements() -> None:
    """Ambiguous duplicate capability requirements on one skill fail closed."""
    left = _requirement("filesystem.write", axes=frozenset({CapabilityAxis.AVAILABLE}))
    right = _requirement("filesystem.write", axes=frozenset({CapabilityAxis.AUTHORIZED}))
    with pytest.raises(ValueError, match="duplicate capability"):
        _skill(required=(left, right))


def test_skill_allows_empty_required_capabilities() -> None:
    """A skill may declare no capability requirements (always declaratively open)."""
    skill = _skill(required=())
    assert skill.required_capabilities == ()


def test_skill_catalog_get_contains_and_missing() -> None:
    """Catalog lookup is by skill_id; missing ids raise KeyError."""
    quality = _skill()
    review = _skill(skill_id="sample.review.checklist", required=())
    declared = (review, quality)
    catalog = SkillCatalog.from_skills(declared)
    assert catalog.get("sample.quality.gate") == quality
    assert "sample.review.checklist" in catalog
    assert len(catalog) == len(declared)
    with pytest.raises(KeyError, match="not found"):
        catalog.get("missing.skill")


def test_skill_catalog_rejects_duplicate_and_conflict() -> None:
    """Exact duplicates and conflicting same-id definitions are rejected."""
    skill = _skill()
    with pytest.raises(ValueError, match=r"duplicate Skill\.skill_id"):
        SkillCatalog.from_skills((skill, skill))
    conflict = _skill(required=())
    with pytest.raises(ValueError, match="conflicting Skill"):
        SkillCatalog.from_skills((skill, conflict))


def test_skill_catalog_enumeration_is_deterministic() -> None:
    """Catalog order is sorted by skill_id regardless of insertion order."""
    zebra = _skill(skill_id="zebra.skill", required=())
    alpha = _skill(skill_id="alpha.skill", required=())
    catalog = SkillCatalog.from_skills((zebra, alpha))
    assert [item.skill_id for item in catalog.skills] == ["alpha.skill", "zebra.skill"]


def test_skills_module_has_no_provider_or_runtime_coupling() -> None:
    """Skills stay free of adapters, execute, and scripts imports."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "project" / "skills.py"
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


def test_skill_ids_are_case_sensitive_and_not_folded() -> None:
    """Abuse: distinct spellings remain distinct catalog identities."""
    lower = _skill(skill_id="sample.quality", required=())
    upper = _skill(skill_id="Sample.Quality", required=())
    catalog = SkillCatalog.from_skills((lower, upper))
    assert catalog.get("sample.quality") == lower
    assert catalog.get("Sample.Quality") == upper
    assert "SAMPLE.QUALITY" not in catalog


def test_empty_skill_catalog_membership() -> None:
    """Empty catalogs expose no skills and reject lookup."""
    catalog = SkillCatalog.from_skills(())
    assert len(catalog) == 0
    assert "anything" not in catalog
    with pytest.raises(KeyError, match="not found"):
        catalog.get("anything")


def test_skill_required_capabilities_detached_from_mutable_input() -> None:
    """Mutating the constructor list must not alter the Skill instance."""
    req = _requirement()
    mutable: list[CapabilityRequirement] = [req]
    skill = Skill(skill_id="quality", required_capabilities=mutable)  # type: ignore[arg-type]
    mutable.clear()
    assert skill.required_capabilities == (req,)
    with pytest.raises(AttributeError):
        skill.required_capabilities = ()  # type: ignore[misc]


def test_skill_catalog_detached_from_mutable_input() -> None:
    """Catalog materializes a tuple copy of the input collection."""
    skill = _skill(required=())
    mutable: list[Skill] = [skill]
    catalog = SkillCatalog.from_skills(mutable)
    mutable.clear()
    assert len(catalog) == 1
    assert catalog.get("sample.quality.gate") == skill
