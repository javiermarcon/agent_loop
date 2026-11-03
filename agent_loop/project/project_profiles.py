"""Declarative project profiles that reference skills by id.

Project profiles carry project-bound configuration identity and the ordered
set of skill ids they include. They do not embed provider APIs, probe
runtimes, or execute agents. Shipii-specific command strings belong in
profile/skill *data*, not in core domain logic (ADR-039 / ADR-052).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def _require_nonempty_unpadded(field_name: str, value: str) -> None:
    if not value or not value.strip():
        message = f"{field_name} must be a non-empty string"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not include leading or trailing whitespace"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    """Project profile referencing declared skills by id.

    ``skill_ids`` preserves declaration order and must not contain duplicates.
    Presence of a skill id does not mean the skill is available or executable.
    """

    profile_id: str
    skill_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("ProjectProfile.profile_id", self.profile_id)
        skill_ids = tuple(self.skill_ids)
        object.__setattr__(self, "skill_ids", skill_ids)
        seen: set[str] = set()
        for skill_id in skill_ids:
            _require_nonempty_unpadded("ProjectProfile.skill_ids", skill_id)
            if skill_id in seen:
                message = f"ProjectProfile.skill_ids duplicate skill_id: {skill_id}"
                raise ValueError(message)
            seen.add(skill_id)


def _profile_sort_key(profile: ProjectProfile) -> str:
    return profile.profile_id


@dataclass(frozen=True, slots=True)
class ProjectProfileCatalog:
    """Immutable catalog of declared ``ProjectProfile`` definitions.

    ``profile_id`` is unique. Exact duplicates and conflicting definitions that
    share an id are rejected. Order is deterministic by ``profile_id``.
    """

    profiles: tuple[ProjectProfile, ...]

    def __post_init__(self) -> None:
        materialised = tuple(self.profiles)
        by_id: dict[str, ProjectProfile] = {}
        for profile in materialised:
            existing = by_id.get(profile.profile_id)
            if existing is None:
                by_id[profile.profile_id] = profile
                continue
            if existing == profile:
                message = f"duplicate ProjectProfile.profile_id: {profile.profile_id}"
                raise ValueError(message)
            message = f"conflicting ProjectProfile definitions for profile_id: {profile.profile_id}"
            raise ValueError(message)
        ordered = tuple(sorted(by_id.values(), key=_profile_sort_key))
        object.__setattr__(self, "profiles", ordered)

    @classmethod
    def from_profiles(cls, profiles: Iterable[ProjectProfile]) -> ProjectProfileCatalog:
        """Build a catalog from an explicit collection of profiles."""
        return cls(profiles=tuple(profiles))

    def get(self, profile_id: str) -> ProjectProfile:
        """Return the profile for ``profile_id`` or raise ``KeyError``."""
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        message = f"ProjectProfile not found: {profile_id}"
        raise KeyError(message)

    def __contains__(self, profile_id: str) -> bool:
        return any(profile.profile_id == profile_id for profile in self.profiles)

    def __len__(self) -> int:
        return len(self.profiles)
