"""Declarative project skills (not harness runtime skill inventories).

Skills name project-bound reusable rule packs and the capability requirements
they declare. They do not discover providers, probe availability, authorize,
or execute agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agent_loop.domain.capabilities import CapabilityRequirement


def _require_nonempty_unpadded(field_name: str, value: str) -> None:
    if not value or not value.strip():
        message = f"{field_name} must be a non-empty string"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not include leading or trailing whitespace"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Skill:
    """Project skill with declarative capability requirements.

    ``skill_id`` is the stable identity. ``required_capabilities`` lists
    ``CapabilityRequirement`` values that must be effective for declarative
    compatibility; effectiveness is assessed elsewhere and never implies
    executability.
    """

    skill_id: str
    required_capabilities: tuple[CapabilityRequirement, ...]

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("Skill.skill_id", self.skill_id)
        capabilities = tuple(self.required_capabilities)
        object.__setattr__(self, "required_capabilities", capabilities)
        seen: set[str] = set()
        for requirement in capabilities:
            capability = requirement.capability_id.value
            if capability in seen:
                message = f"Skill.required_capabilities duplicate capability: {capability}"
                raise ValueError(message)
            seen.add(capability)


def _skill_sort_key(skill: Skill) -> str:
    return skill.skill_id


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    """Immutable catalog of declared ``Skill`` definitions.

    ``skill_id`` is unique. Exact duplicates and conflicting definitions that
    share an id are rejected. Order is deterministic by ``skill_id``.
    """

    skills: tuple[Skill, ...]

    def __post_init__(self) -> None:
        materialised = tuple(self.skills)
        by_id: dict[str, Skill] = {}
        for skill in materialised:
            existing = by_id.get(skill.skill_id)
            if existing is None:
                by_id[skill.skill_id] = skill
                continue
            if existing == skill:
                message = f"duplicate Skill.skill_id: {skill.skill_id}"
                raise ValueError(message)
            message = f"conflicting Skill definitions for skill_id: {skill.skill_id}"
            raise ValueError(message)
        ordered = tuple(sorted(by_id.values(), key=_skill_sort_key))
        object.__setattr__(self, "skills", ordered)

    @classmethod
    def from_skills(cls, skills: Iterable[Skill]) -> SkillCatalog:
        """Build a catalog from an explicit collection of skills."""
        return cls(skills=tuple(skills))

    def get(self, skill_id: str) -> Skill:
        """Return the skill for ``skill_id`` or raise ``KeyError``."""
        for skill in self.skills:
            if skill.skill_id == skill_id:
                return skill
        message = f"Skill not found: {skill_id}"
        raise KeyError(message)

    def __contains__(self, skill_id: str) -> bool:
        return any(skill.skill_id == skill_id for skill in self.skills)

    def __len__(self) -> int:
        return len(self.skills)
