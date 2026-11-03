"""Declarative profile/skill compatibility against capability inventories."""

from __future__ import annotations

import ast
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.project.profile_skill_compatibility import ProfileSkillAssessment
from agent_loop.project.profile_skill_compatibility import assess_profile_against_inventory
from agent_loop.project.profile_skill_compatibility import diagnose_requirement_against_inventory
from agent_loop.project.profile_skill_compatibility import resolve_skills_for_profile
from agent_loop.project.project_profiles import ProjectProfile
from agent_loop.project.skills import Skill
from agent_loop.project.skills import SkillCatalog


def _requirement(
    capability: str,
    *,
    axes: frozenset[CapabilityAxis] | None = None,
) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=CapabilityId(value=capability),
        mandatory_axes=axes or frozenset({CapabilityAxis.AVAILABLE}),
    )


def _snapshot(
    capability: str,
    *,
    available: CapabilityState = CapabilityState.YES,
    authorized: CapabilityState = CapabilityState.UNKNOWN,
) -> CapabilitySnapshot:
    evidence = CapabilityEvidence(
        source=EvidenceSource.CONFIG,
        observation="synthetic",
        observed_at=datetime.now(tz=UTC),
        confidence=EvidenceConfidence.MEDIUM,
        freshness=EvidenceFreshness.FRESH,
    )
    return CapabilitySnapshot(
        capability_id=CapabilityId(value=capability),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=available,
        connected=CapabilityState.YES,
        healthy=CapabilityState.YES,
        authorized=authorized,
        allowed=CapabilityState.YES,
        evidence=(evidence,),
        confidence=EvidenceConfidence.MEDIUM,
    )


def _inventory(*snapshots: CapabilitySnapshot) -> RuntimeInventory:
    return RuntimeInventory(
        runtime_identity="runtime.test",
        runtime_version="1",
        adapter_version="1",
        discovered_at=datetime.now(tz=UTC),
        capabilities=snapshots,
        models=(),
    )


def _catalog(*skills: Skill) -> SkillCatalog:
    return SkillCatalog.from_skills(skills)


# Skill definitions used in profile-assessment tests. ``bad`` requires a
# capability that the synthetic inventories do not provide.
ok = Skill(skill_id="ok", required_capabilities=())
bad = Skill(skill_id="bad", required_capabilities=(_requirement("cap.missing"),))


def test_resolve_skills_for_profile_returns_declared_skills_in_profile_order() -> None:
    """Resolution materializes skills in profile skill_ids order."""
    quality = Skill(skill_id="quality", required_capabilities=())
    review = Skill(skill_id="review", required_capabilities=())
    catalog = _catalog(review, quality)
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality", "review"))
    resolved = resolve_skills_for_profile(profile, catalog)
    assert [skill.skill_id for skill in resolved] == ["quality", "review"]


def test_resolve_skills_for_profile_fails_when_skill_missing() -> None:
    """Missing required skills fail closed; no silent skip."""
    catalog = _catalog(Skill(skill_id="quality", required_capabilities=()))
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality", "missing"))
    with pytest.raises(KeyError, match="missing"):
        resolve_skills_for_profile(profile, catalog)


def test_compatible_profile_skills_against_sufficient_inventory() -> None:
    """Skills whose requirements are effective are reported compatible."""
    skill = Skill(
        skill_id="quality",
        required_capabilities=(_requirement("filesystem.write"),),
    )
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality",))
    inventory = _inventory(_snapshot("filesystem.write"))
    assessment = assess_profile_against_inventory(profile, catalog, inventory)
    assert assessment.compatible_skill_ids == ("quality",)
    assert not assessment.incompatible_skill_ids
    assert assessment.reasons == ("compatible:quality",)


def test_incompatible_when_capability_missing_or_unknown() -> None:
    """Absent or UNKNOWN axes never count as compatible."""
    skill = Skill(
        skill_id="quality",
        required_capabilities=(
            _requirement(
                "filesystem.write",
                axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
            ),
        ),
    )
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality",))
    missing = assess_profile_against_inventory(profile, catalog, _inventory())
    assert not missing.compatible_skill_ids
    assert missing.incompatible_skill_ids == ("quality",)
    assert any("capability_absent:filesystem.write" in reason for reason in missing.reasons)
    unknown = assess_profile_against_inventory(
        profile,
        catalog,
        _inventory(_snapshot("filesystem.write", authorized=CapabilityState.UNKNOWN)),
    )
    assert not unknown.compatible_skill_ids
    assert unknown.incompatible_skill_ids == ("quality",)
    assert any("axis_unknown:authorized" in reason for reason in unknown.reasons)
    assert not any("capability_absent:" in reason for reason in unknown.reasons)


def test_mixed_profile_reports_compatible_and_incompatible_skills() -> None:
    """Each skill is assessed independently; no winner selection."""
    catalog = _catalog(ok, bad)
    profile = ProjectProfile(profile_id="p1", skill_ids=("ok", "bad"))
    assessment = assess_profile_against_inventory(
        profile,
        catalog,
        _inventory(_snapshot("cap.ok")),
    )
    assert assessment.compatible_skill_ids == ("ok",)
    assert assessment.incompatible_skill_ids == ("bad",)
    assert assessment.reasons == (
        "compatible:ok",
        "incompatible:bad:capability_absent:cap.missing",
    )
    assert "winner" not in " ".join(assessment.reasons)


def test_duplicate_snapshots_preserve_no_and_unknown_on_same_axis() -> None:
    """NO and UNKNOWN on one axis stay visible together, in stable order."""
    req = _requirement("cap.both")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(
            _snapshot("cap.both", available=CapabilityState.NO),
            _snapshot("cap.both", available=CapabilityState.UNKNOWN),
        ),
        req,
    )
    assert diagnosis == "capability_unsatisfied:cap.both:axis_no:available;axis_unknown:available"


def test_duplicate_snapshots_unknown_then_no_matches_no_then_unknown() -> None:
    """Reversing UNKNOWN/NO input order yields exactly the same diagnosis."""
    req = _requirement("cap.both")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(
            _snapshot("cap.both", available=CapabilityState.UNKNOWN),
            _snapshot("cap.both", available=CapabilityState.NO),
        ),
        req,
    )
    assert diagnosis == "capability_unsatisfied:cap.both:axis_no:available;axis_unknown:available"


def test_duplicate_snapshots_preserve_unknown_when_another_snapshot_is_yes() -> None:
    """A fully effective YES snapshot does not hide co-existing uncertainty."""
    req = _requirement("cap.both")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(
            _snapshot("cap.both", available=CapabilityState.UNKNOWN),
            _snapshot("cap.both", available=CapabilityState.YES),
        ),
        req,
    )
    assert diagnosis == "capability_effective:cap.both:axis_unknown:available"


def test_duplicate_snapshots_preserve_no_when_another_snapshot_is_yes() -> None:
    """A fully effective YES snapshot does not erase co-existing negative evidence."""
    req = _requirement("cap.both")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(
            _snapshot("cap.both", available=CapabilityState.NO),
            _snapshot("cap.both", available=CapabilityState.YES),
        ),
        req,
    )
    assert diagnosis == "capability_effective:cap.both:axis_no:available"


def test_single_snapshot_unknown_still_works() -> None:
    """A single UNKNOWN snapshot is reported as uncertain, not negative."""
    req = _requirement("cap.auth")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(_snapshot("cap.auth", available=CapabilityState.UNKNOWN)),
        req,
    )
    assert diagnosis == "capability_unsatisfied:cap.auth:axis_unknown:available"


def test_single_snapshot_no_still_works() -> None:
    """A single NO snapshot is reported as confirmed negative evidence."""
    req = _requirement("cap.auth")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(_snapshot("cap.auth", available=CapabilityState.NO)),
        req,
    )
    assert diagnosis == "capability_unsatisfied:cap.auth:axis_no:available"


def test_single_snapshot_yes_still_works() -> None:
    """A single fully YES snapshot is effective."""
    req = _requirement("cap.auth")
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(_snapshot("cap.auth", available=CapabilityState.YES)),
        req,
    )
    assert diagnosis == "capability_effective:cap.auth"


def test_split_yes_across_snapshots_is_not_a_single_effective_snapshot() -> None:
    """Axes YES on different snapshots of the same capability do not combine."""
    req = _requirement(
        "cap.split",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    inventory = _inventory(
        _snapshot("cap.split", available=CapabilityState.YES, authorized=CapabilityState.NO),
        _snapshot("cap.split", available=CapabilityState.NO, authorized=CapabilityState.YES),
    )
    assert diagnose_requirement_against_inventory(inventory, req) == (
        "capability_unsatisfied:cap.split:no_single_snapshot;axis_no:authorized;axis_no:available"
    )
    skill = Skill(skill_id="split", required_capabilities=(req,))
    assessment = assess_profile_against_inventory(
        ProjectProfile(profile_id="p1", skill_ids=("split",)),
        _catalog(skill),
        inventory,
    )
    assert not assessment.compatible_skill_ids
    assert assessment.incompatible_skill_ids == ("split",)
    assert assessment.reasons == (
        "incompatible:split:capability_unsatisfied:cap.split:no_single_snapshot;axis_no:authorized;axis_no:available",
    )


def test_split_yes_with_unknown_evidence_preserves_unknown_tokens() -> None:
    """Split YES keeps UNKNOWN evidence instead of turning it into NO."""
    req = _requirement(
        "cap.split_unknown",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    inventory = _inventory(
        _snapshot("cap.split_unknown", available=CapabilityState.YES, authorized=CapabilityState.UNKNOWN),
        _snapshot("cap.split_unknown", available=CapabilityState.UNKNOWN, authorized=CapabilityState.YES),
    )
    assert diagnose_requirement_against_inventory(inventory, req) == (
        "capability_unsatisfied:cap.split_unknown:no_single_snapshot;axis_unknown:authorized;axis_unknown:available"
    )


def test_split_yes_diagnosis_is_independent_of_snapshot_order() -> None:
    """Reversing split snapshots keeps the same split token and evidence order."""
    req = _requirement(
        "cap.split_order",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    snapshots = (
        _snapshot("cap.split_order", available=CapabilityState.YES, authorized=CapabilityState.NO),
        _snapshot("cap.split_order", available=CapabilityState.NO, authorized=CapabilityState.YES),
    )
    expected = "capability_unsatisfied:cap.split_order:no_single_snapshot;axis_no:authorized;axis_no:available"
    forward = diagnose_requirement_against_inventory(_inventory(*snapshots), req)
    backward = diagnose_requirement_against_inventory(_inventory(*reversed(snapshots)), req)
    assert forward == expected
    assert backward == expected


def test_one_snapshot_satisfying_every_axis_is_compatible() -> None:
    """A snapshot with all axes YES satisfies the requirement despite others."""
    req = _requirement(
        "cap.full",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    inventory = _inventory(
        _snapshot(
            "cap.full",
            available=CapabilityState.YES,
            authorized=CapabilityState.YES,
        ),
        _snapshot(
            "cap.full",
            available=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
        ),
    )
    skill = Skill(skill_id="full", required_capabilities=(req,))
    assessment = assess_profile_against_inventory(
        ProjectProfile(profile_id="p1", skill_ids=("full",)),
        _catalog(skill),
        inventory,
    )
    assert assessment.compatible_skill_ids == ("full",)
    assert not assessment.incompatible_skill_ids
    diagnosis = diagnose_requirement_against_inventory(inventory, req)
    assert diagnosis.startswith("capability_effective:cap.full")
    assert "axis_unknown:authorized" in diagnosis
    assert "axis_unknown:available" in diagnosis


def test_compatibility_does_not_imply_executability_or_provider_coupling() -> None:
    """Assessment module stays free of execute/adapters/scripts."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "project" / "profile_skill_compatibility.py"
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
    assert "is_effective" in source


def test_explicit_capability_no_is_incompatible() -> None:
    """Abuse/edge: AVAILABLE=NO never counts as declarative compatibility."""
    skill = Skill(
        skill_id="quality",
        required_capabilities=(_requirement("filesystem.write"),),
    )
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality",))
    assessment = assess_profile_against_inventory(
        profile,
        catalog,
        _inventory(_snapshot("filesystem.write", available=CapabilityState.NO)),
    )
    assert not assessment.compatible_skill_ids
    assert assessment.incompatible_skill_ids == ("quality",)
    assert any("axis_no:available" in reason for reason in assessment.reasons)
    assert not any("capability_absent:" in reason for reason in assessment.reasons)


def test_empty_profile_assessment_is_vacuously_compatible() -> None:
    """A profile with no skills yields empty partitions and no winner."""
    catalog = _catalog()
    profile = ProjectProfile(profile_id="empty", skill_ids=())
    assessment = assess_profile_against_inventory(profile, catalog, _inventory())
    assert assessment.profile_id == "empty"
    assert not assessment.compatible_skill_ids
    assert not assessment.incompatible_skill_ids
    assert not assessment.reasons


def test_skill_with_no_requirements_is_compatible_even_on_empty_inventory() -> None:
    """Empty requirement tuples are declaratively open; still not executable."""
    skill = Skill(skill_id="docs-only", required_capabilities=())
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("docs-only",))
    assessment = assess_profile_against_inventory(profile, catalog, _inventory())
    assert assessment.compatible_skill_ids == ("docs-only",)
    assert not assessment.incompatible_skill_ids
    assert assessment.reasons == ("compatible:docs-only",)


def test_wrong_capability_id_on_inventory_does_not_satisfy_skill() -> None:
    """Malicious/ambiguous: similarly named but different capability ids fail closed."""
    skill = Skill(
        skill_id="quality",
        required_capabilities=(_requirement("filesystem.write"),),
    )
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality",))
    assessment = assess_profile_against_inventory(
        profile,
        catalog,
        _inventory(_snapshot("filesystem.write.extra")),
    )
    assert not assessment.compatible_skill_ids
    assert assessment.incompatible_skill_ids == ("quality",)
    assert assessment.reasons == ("incompatible:quality:capability_absent:filesystem.write",)


def test_assess_propagates_missing_skill_key_error() -> None:
    """Assessment does not swallow unresolved skill references."""
    catalog = _catalog(Skill(skill_id="quality", required_capabilities=()))
    profile = ProjectProfile(profile_id="p1", skill_ids=("quality", "ghost"))
    with pytest.raises(KeyError, match="ghost"):
        assess_profile_against_inventory(profile, catalog, _inventory())


def test_incompatibility_reasons_distinguish_absent_no_and_unknown_axes() -> None:
    """Diagnostics separate absent capabilities from axis NO vs UNKNOWN."""
    req = _requirement(
        "cap.auth",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    assert diagnose_requirement_against_inventory(_inventory(), req) == "capability_absent:cap.auth"
    assert (
        diagnose_requirement_against_inventory(
            _inventory(_snapshot("cap.auth", available=CapabilityState.NO)),
            _requirement("cap.auth"),
        )
        == "capability_unsatisfied:cap.auth:axis_no:available"
    )
    assert (
        diagnose_requirement_against_inventory(
            _inventory(_snapshot("cap.auth", authorized=CapabilityState.NO)),
            req,
        )
        == "capability_unsatisfied:cap.auth:axis_no:authorized"
    )
    assert (
        diagnose_requirement_against_inventory(
            _inventory(_snapshot("cap.auth", authorized=CapabilityState.UNKNOWN)),
            req,
        )
        == "capability_unsatisfied:cap.auth:axis_unknown:authorized"
    )


def test_diagnose_reports_effective_when_one_snapshot_satisfies() -> None:
    """Happy path: a fully YES snapshot is diagnosed as effective, not missing."""
    req = _requirement("cap.ok")
    assert (
        diagnose_requirement_against_inventory(_inventory(_snapshot("cap.ok")), req) == "capability_effective:cap.ok"
    )


def test_no_and_unknown_axes_are_listed_together_in_axis_order() -> None:
    """Both failing axes appear; UNKNOWN is not reported as confirmed NO."""
    req = _requirement(
        "cap.auth",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    diagnosis = diagnose_requirement_against_inventory(
        _inventory(_snapshot("cap.auth", available=CapabilityState.NO, authorized=CapabilityState.UNKNOWN)),
        req,
    )
    assert diagnosis == ("capability_unsatisfied:cap.auth:axis_unknown:authorized;axis_no:available")


def test_incompatibility_reason_order_follows_declared_requirements() -> None:
    """Multiple failing requirements keep declaration order in the reason."""
    skill = Skill(
        skill_id="multi",
        required_capabilities=(
            _requirement("cap.a"),
            _requirement("cap.b"),
        ),
    )
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("multi",))
    assessment = assess_profile_against_inventory(profile, catalog, _inventory())
    assert assessment.reasons == ("incompatible:multi:capability_absent:cap.a;capability_absent:cap.b",)


def test_diagnostics_are_independent_of_snapshot_order() -> None:
    """Reversing identical evidence yields the same diagnosis and assessment."""
    req = _requirement(
        "cap.order",
        axes=frozenset({CapabilityAxis.AVAILABLE, CapabilityAxis.AUTHORIZED}),
    )
    snapshots = (
        _snapshot("cap.order", available=CapabilityState.NO, authorized=CapabilityState.UNKNOWN),
        _snapshot("cap.order", available=CapabilityState.UNKNOWN, authorized=CapabilityState.NO),
        _snapshot("cap.order", available=CapabilityState.NO, authorized=CapabilityState.NO),
    )
    skill = Skill(skill_id="order", required_capabilities=(req,))
    catalog = _catalog(skill)
    profile = ProjectProfile(profile_id="p1", skill_ids=("order",))
    forward = assess_profile_against_inventory(profile, catalog, _inventory(*snapshots))
    reversed_inventory = _inventory(*reversed(snapshots))
    backward = assess_profile_against_inventory(profile, catalog, reversed_inventory)
    assert forward == backward
    assert diagnose_requirement_against_inventory(_inventory(*snapshots), req) == (
        diagnose_requirement_against_inventory(reversed_inventory, req)
    )


def test_assessment_collections_are_detached_from_caller_mutables() -> None:
    """Assessment tuples are immutable copies, not aliases of caller lists."""
    skill = Skill(skill_id="quality", required_capabilities=())
    assessment = assess_profile_against_inventory(
        ProjectProfile(profile_id="p1", skill_ids=("quality",)),
        _catalog(skill),
        _inventory(),
    )
    with pytest.raises(AttributeError):
        assessment.compatible_skill_ids = ("mutated",)  # type: ignore[misc]
    with pytest.raises(AttributeError):
        assessment.reasons = ("mutated",)  # type: ignore[misc]


def test_profile_skill_assessment_copies_mutable_constructor_collections() -> None:
    """Direct construction copies lists so later mutation cannot alias the result."""
    compatible: list[str] = ["ok"]
    incompatible: list[str] = ["bad"]
    reasons: list[str] = ["incompatible:bad:capability_absent:x"]
    assessment = ProfileSkillAssessment(
        profile_id="p1",
        compatible_skill_ids=compatible,  # type: ignore[arg-type]
        incompatible_skill_ids=incompatible,  # type: ignore[arg-type]
        reasons=reasons,  # type: ignore[arg-type]
    )
    compatible.clear()
    incompatible.append("extra")
    reasons[:] = ["mutated"]
    assert assessment.compatible_skill_ids == ("ok",)
    assert assessment.incompatible_skill_ids == ("bad",)
    assert assessment.reasons == ("incompatible:bad:capability_absent:x",)
