"""Declarative compatibility between project profiles, skills, and inventories.

Assessment uses ``is_effective`` against a ``RuntimeInventory`` snapshot. It
does not select a winner, dispatch work, discover runtimes, authenticate, or
execute agents. UNKNOWN/NO never count as YES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import is_effective

if TYPE_CHECKING:
    from agent_loop.domain.capabilities import CapabilityAxis
    from agent_loop.domain.capabilities import CapabilityRequirement
    from agent_loop.domain.capabilities import CapabilitySnapshot
    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.project.project_profiles import ProjectProfile
    from agent_loop.project.skills import Skill
    from agent_loop.project.skills import SkillCatalog


@dataclass(frozen=True, slots=True)
class ProfileSkillAssessment:
    """Immutable outcome of assessing a profile's skills against an inventory.

    ``compatible_skill_ids`` / ``incompatible_skill_ids`` preserve profile
    ``skill_ids`` order among each partition. ``reasons`` are stable, ordered
    explanations. An empty compatible tuple is valid (no fallback winner).
    """

    profile_id: str
    compatible_skill_ids: tuple[str, ...]
    incompatible_skill_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "compatible_skill_ids", tuple(self.compatible_skill_ids))
        object.__setattr__(
            self,
            "incompatible_skill_ids",
            tuple(self.incompatible_skill_ids),
        )
        object.__setattr__(self, "reasons", tuple(self.reasons))


def resolve_skills_for_profile(
    profile: ProjectProfile,
    skills: SkillCatalog,
) -> tuple[Skill, ...]:
    """Materialize ``profile.skill_ids`` from ``skills`` in declaration order.

    Missing skill ids raise ``KeyError`` (fail closed).
    """
    return tuple(skills.get(skill_id) for skill_id in profile.skill_ids)


def _matching_snapshots(
    inventory: RuntimeInventory,
    requirement: CapabilityRequirement,
) -> tuple[CapabilitySnapshot, ...]:
    return tuple(
        snapshot for snapshot in inventory.capabilities if snapshot.capability_id == requirement.capability_id
    )


def _axis_evidence_tokens(
    axis: CapabilityAxis,
    states: set[CapabilityState],
) -> list[str]:
    """Return deterministic evidence tokens for one axis.

    ``YES`` never erases a co-existing ``NO`` or ``UNKNOWN``: those states stay
    visible because they describe other snapshots of the same capability.
    """
    tokens: list[str] = []
    if CapabilityState.NO in states:
        tokens.append(f"axis_no:{axis.value}")
    if CapabilityState.UNKNOWN in states:
        tokens.append(f"axis_unknown:{axis.value}")
    return tokens


def _sorted_mandatory_axes(requirement: CapabilityRequirement) -> tuple[CapabilityAxis, ...]:
    return tuple(sorted(requirement.mandatory_axes, key=lambda axis: axis.value))


def diagnose_requirement_against_inventory(
    inventory: RuntimeInventory,
    requirement: CapabilityRequirement,
) -> str:
    """Explain why ``requirement`` is or is not effective on ``inventory``.

    Uses ``is_effective`` for the positive case, so a requirement is effective
    only when one snapshot has every mandatory axis at ``YES``. Evidence tokens
    are accumulated per axis across snapshots: UNKNOWN is never reported as
    confirmed NO, and a YES in another snapshot does not hide a co-existing
    NO or UNKNOWN. When YES is split across snapshots so no single one is
    effective, the canonical ``no_single_snapshot`` token is emitted together
    with the same per-axis evidence tokens, preserving relevant NO/UNKNOWN
    observations.
    """
    capability = requirement.capability_id.value
    matching = _matching_snapshots(inventory, requirement)
    if not matching:
        return f"capability_absent:{capability}"

    axes = _sorted_mandatory_axes(requirement)
    states_by_axis = {axis: {snapshot.axis_state(axis) for snapshot in matching} for axis in axes}
    evidence_tokens = [token for axis in axes for token in _axis_evidence_tokens(axis, states_by_axis[axis])]

    if any(is_effective(snapshot, requirement) for snapshot in matching):
        if not evidence_tokens:
            return f"capability_effective:{capability}"
        return f"capability_effective:{capability}:{';'.join(evidence_tokens)}"

    if all(CapabilityState.YES in states_by_axis[axis] for axis in axes):
        split_tokens = ";".join(("no_single_snapshot", *evidence_tokens))
        return f"capability_unsatisfied:{capability}:{split_tokens}"
    return f"capability_unsatisfied:{capability}:{';'.join(evidence_tokens)}"


def _requirement_is_satisfied(
    inventory: RuntimeInventory,
    requirement: CapabilityRequirement,
) -> bool:
    return any(is_effective(snapshot, requirement) for snapshot in _matching_snapshots(inventory, requirement))


def _inventory_satisfies(
    inventory: RuntimeInventory,
    requirements: tuple[CapabilityRequirement, ...],
) -> bool:
    return all(_requirement_is_satisfied(inventory, requirement) for requirement in requirements)


def _skill_incompatibility_reason(
    skill: Skill,
    inventory: RuntimeInventory,
) -> str:
    diagnostics = tuple(
        diagnose_requirement_against_inventory(inventory, requirement) for requirement in skill.required_capabilities
    )
    detail = ";".join(diagnostics) if diagnostics else "no_requirements"
    return f"incompatible:{skill.skill_id}:{detail}"


def assess_profile_against_inventory(
    profile: ProjectProfile,
    skills: SkillCatalog,
    inventory: RuntimeInventory,
) -> ProfileSkillAssessment:
    """Assess each profile skill against ``inventory`` declaratively.

    Skills are resolved fail-closed via ``resolve_skills_for_profile``. A skill
    is compatible only when every declared requirement is effective on some
    inventory snapshot. Compatibility never implies executability.
    """
    resolved = resolve_skills_for_profile(profile, skills)
    compatible: list[str] = []
    incompatible: list[str] = []
    reasons: list[str] = []
    for skill in resolved:
        if _inventory_satisfies(inventory, skill.required_capabilities):
            compatible.append(skill.skill_id)
            reasons.append(f"compatible:{skill.skill_id}")
            continue
        incompatible.append(skill.skill_id)
        reasons.append(_skill_incompatibility_reason(skill, inventory))
    return ProfileSkillAssessment(
        profile_id=profile.profile_id,
        compatible_skill_ids=tuple(compatible),
        incompatible_skill_ids=tuple(incompatible),
        reasons=tuple(reasons),
    )
