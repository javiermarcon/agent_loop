"""Deterministic resolution of compatible model/runtime candidates.

Compatibility is declarative: task capability requirements are checked against
``RuntimeInventory`` snapshots via ``is_effective``. Catalog membership and
inventory model refs gate which declared bindings may appear. This module does
not execute tasks, discover runtimes, authenticate, authorize, or infer
capabilities from names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.domain.capabilities import is_effective

if TYPE_CHECKING:
    from agent_loop.domain.capabilities import CapabilityRequirement
    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
    from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
    from agent_loop.domain.model_profiles import ModelProfile
    from agent_loop.domain.model_profiles import RuntimeModelProfile
    from agent_loop.domain.task_assessment import TaskAssessment


@dataclass(frozen=True, slots=True)
class CompatibleModelCandidate:
    """One declarative binding that satisfies S14 compatibility checks."""

    runtime_id: str
    profile: ModelProfile
    native_model_ref: str

    def identity_key(self) -> tuple[str, str]:
        """Return ``(runtime_id, profile_id)`` for stable ordering."""
        return (self.runtime_id, self.profile.profile_id)


@dataclass(frozen=True, slots=True)
class CompatibleCandidateResolution:
    """Immutable outcome of candidate resolution.

    ``candidates`` is sorted by ``(runtime_id, profile_id)``. ``reasons`` lists
    stable, ordered explanations of acceptances and rejections. An empty
    candidate tuple is a valid outcome (no fallback winner).
    """

    candidates: tuple[CompatibleModelCandidate, ...]
    reasons: tuple[str, ...]


def _inventory_satisfies_requirements(
    inventory: RuntimeInventory,
    requirements: tuple[CapabilityRequirement, ...],
) -> bool:
    for requirement in requirements:
        if not any(is_effective(snapshot, requirement) for snapshot in inventory.capabilities):
            return False
    return True


def _missing_requirement_ids(
    inventory: RuntimeInventory,
    requirements: tuple[CapabilityRequirement, ...],
) -> tuple[str, ...]:
    return tuple(
        requirement.capability_id.value
        for requirement in requirements
        if not any(is_effective(snapshot, requirement) for snapshot in inventory.capabilities)
    )


def _index_inventories(
    inventories: tuple[RuntimeInventory, ...],
) -> dict[str, RuntimeInventory]:
    indexed: dict[str, RuntimeInventory] = {}
    for inventory in inventories:
        runtime_id = inventory.runtime_identity
        if runtime_id in indexed:
            message = f"duplicate RuntimeInventory.runtime_identity: {runtime_id}"
            raise ValueError(message)
        indexed[runtime_id] = inventory
    return indexed


def _inventory_model_ids(inventory: RuntimeInventory) -> frozenset[str]:
    return frozenset(ref.model_id for ref in inventory.models)


def resolve_compatible_candidates(
    assessment: TaskAssessment,
    *,
    inventories: tuple[RuntimeInventory, ...],
    profiles: ModelProfileCatalog,
    bindings: RuntimeModelProfileCatalog,
) -> CompatibleCandidateResolution:
    """Resolve declarative compatible candidates for ``assessment``.

    A binding becomes a candidate only when:

    1. Its profile exists in ``profiles`` and equals the catalogued definition.
    2. An inventory exists whose ``runtime_identity`` equals ``binding.runtime_id``.
    3. That inventory satisfies every ``assessment.required_capabilities`` entry
       via ``is_effective`` (empty requirements pass vacuously).
    4. ``binding.native_model_ref`` is present and equals an
       ``InventoryModelRef.model_id`` in that same inventory.

    Catalog conflicts remain the responsibility of catalog construction.
    Resolution never invents a default candidate.
    """
    inventory_by_runtime = _index_inventories(inventories)
    requirements = assessment.required_capabilities
    accepted: list[CompatibleModelCandidate] = []
    reasons: list[str] = []

    for binding in bindings.bindings:
        outcome = _evaluate_binding(
            binding,
            profiles=profiles,
            inventory_by_runtime=inventory_by_runtime,
            requirements=requirements,
        )
        if isinstance(outcome, tuple):
            reasons.extend(outcome)
            continue
        accepted.append(
            CompatibleModelCandidate(
                runtime_id=binding.runtime_id,
                profile=binding.profile,
                native_model_ref=outcome,
            ),
        )
        reasons.append(
            f"accepted={binding.runtime_id}/{binding.profile.profile_id}@{outcome}",
        )

    accepted.sort(key=lambda item: item.identity_key())
    reasons.sort()
    return CompatibleCandidateResolution(
        candidates=tuple(accepted),
        reasons=tuple(reasons),
    )


def _evaluate_binding(
    binding: RuntimeModelProfile,
    *,
    profiles: ModelProfileCatalog,
    inventory_by_runtime: dict[str, RuntimeInventory],
    requirements: tuple[CapabilityRequirement, ...],
) -> str | tuple[str, ...]:
    """Return accepted ``native_model_ref``, or a rejection-reason tuple."""
    key = f"{binding.runtime_id}/{binding.profile.profile_id}"
    profile_id = binding.profile.profile_id
    rejection: str | None = None

    try:
        catalogued = profiles.get(profile_id)
    except KeyError:
        rejection = f"rejected={key}:profile_not_in_catalog"
        catalogued = None

    if rejection is None and catalogued is not None and catalogued != binding.profile:
        rejection = f"rejected={key}:profile_conflicts_with_catalog"

    inventory = inventory_by_runtime.get(binding.runtime_id)
    if rejection is None and inventory is None:
        rejection = f"rejected={key}:inventory_missing"

    if (
        rejection is None
        and inventory is not None
        and not _inventory_satisfies_requirements(
            inventory,
            requirements,
        )
    ):
        missing_text = ",".join(_missing_requirement_ids(inventory, requirements))
        rejection = f"rejected={key}:capabilities_unsatisfied:{missing_text}"

    native_ref = binding.native_model_ref
    if rejection is None and native_ref is None:
        rejection = f"rejected={key}:native_model_ref_missing"

    if (
        rejection is None
        and inventory is not None
        and native_ref is not None
        and native_ref not in _inventory_model_ids(inventory)
    ):
        rejection = f"rejected={key}:native_model_ref_absent_from_inventory"

    if rejection is not None:
        return (rejection,)
    if native_ref is None:
        return (f"rejected={key}:native_model_ref_missing",)
    return native_ref
