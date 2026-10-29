"""Deterministic catalogs of model profiles and runtime associations.

These collections own declared definitions only. They do not discover runtimes,
probe availability, authenticate, authorize, select models, or infer
capabilities from identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agent_loop.domain.model_profiles import ModelProfile
    from agent_loop.domain.model_profiles import RuntimeModelProfile


def _profile_sort_key(profile: ModelProfile) -> str:
    return profile.profile_id


def _binding_sort_key(binding: RuntimeModelProfile) -> tuple[str, str]:
    return binding.identity_key()


@dataclass(frozen=True, slots=True)
class ModelProfileCatalog:
    """Immutable catalog of logical ``ModelProfile`` definitions.

    ``profile_id`` is unique within the catalog. Structural equality of
    ``ModelProfile`` remains field-wise (S12); the catalog uses ``profile_id``
    as the lookup key and rejects both exact duplicates and conflicting
    definitions that share an id.
    """

    profiles: tuple[ModelProfile, ...]

    def __post_init__(self) -> None:
        by_id: dict[str, ModelProfile] = {}
        for profile in self.profiles:
            existing = by_id.get(profile.profile_id)
            if existing is None:
                by_id[profile.profile_id] = profile
                continue
            if existing == profile:
                message = f"duplicate ModelProfile.profile_id: {profile.profile_id}"
                raise ValueError(message)
            message = f"conflicting ModelProfile definitions for profile_id: {profile.profile_id}"
            raise ValueError(message)
        ordered = tuple(sorted(by_id.values(), key=_profile_sort_key))
        if ordered != self.profiles:
            object.__setattr__(self, "profiles", ordered)

    @classmethod
    def from_profiles(cls, profiles: Iterable[ModelProfile]) -> ModelProfileCatalog:
        """Build a catalog from an explicit collection of profiles."""
        return cls(profiles=tuple(profiles))

    def get(self, profile_id: str) -> ModelProfile:
        """Return the profile for ``profile_id`` or raise ``KeyError``."""
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        message = f"ModelProfile not found: {profile_id}"
        raise KeyError(message)

    def __contains__(self, profile_id: str) -> bool:
        return any(profile.profile_id == profile_id for profile in self.profiles)

    def __len__(self) -> int:
        return len(self.profiles)


@dataclass(frozen=True, slots=True)
class RuntimeModelProfileCatalog:
    """Immutable catalog of declared ``RuntimeModelProfile`` associations.

    Association identity is ``(runtime_id, profile_id)`` via
    ``RuntimeModelProfile.identity_key()``. The same logical profile may bind
    to multiple runtimes. A binding does not imply availability, health, auth,
    or authorization.
    """

    bindings: tuple[RuntimeModelProfile, ...]

    def __post_init__(self) -> None:
        by_key: dict[tuple[str, str], RuntimeModelProfile] = {}
        for binding in self.bindings:
            key = binding.identity_key()
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = binding
                continue
            if existing == binding:
                message = f"duplicate RuntimeModelProfile identity_key: ({key[0]}, {key[1]})"
                raise ValueError(message)
            message = f"conflicting RuntimeModelProfile definitions for identity_key: ({key[0]}, {key[1]})"
            raise ValueError(message)
        ordered = tuple(sorted(by_key.values(), key=_binding_sort_key))
        if ordered != self.bindings:
            object.__setattr__(self, "bindings", ordered)

    @classmethod
    def from_bindings(
        cls,
        bindings: Iterable[RuntimeModelProfile],
        *,
        profiles: ModelProfileCatalog | None = None,
    ) -> RuntimeModelProfileCatalog:
        """Build a catalog; optionally require bindings to match ``profiles``."""
        materialised = tuple(bindings)
        if profiles is not None:
            for binding in materialised:
                try:
                    catalogued = profiles.get(binding.profile.profile_id)
                except KeyError:
                    message = f"RuntimeModelProfile references unknown profile_id: {binding.profile.profile_id}"
                    raise ValueError(message) from None
                if catalogued != binding.profile:
                    message = (
                        "RuntimeModelProfile profile conflicts with catalog for "
                        f"profile_id: {binding.profile.profile_id}"
                    )
                    raise ValueError(message)
        return cls(bindings=materialised)

    def get(self, runtime_id: str, profile_id: str) -> RuntimeModelProfile:
        """Return the binding for ``(runtime_id, profile_id)`` or raise ``KeyError``."""
        key = (runtime_id, profile_id)
        for binding in self.bindings:
            if binding.identity_key() == key:
                return binding
        message = f"RuntimeModelProfile not found: ({runtime_id}, {profile_id})"
        raise KeyError(message)

    def for_runtime(self, runtime_id: str) -> tuple[RuntimeModelProfile, ...]:
        """Return bindings for ``runtime_id`` in catalog order."""
        return tuple(binding for binding in self.bindings if binding.runtime_id == runtime_id)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return any(binding.identity_key() == key for binding in self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)
