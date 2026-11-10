"""Operational runtime/model routing on top of declarative compatibility (S13).

``resolve_compatible_candidates`` answers *what could be compatible*. This
module answers *what may actually be selected now* by requiring explicit
operational readiness evidence for the candidate runtime. It never treats
``CONFIGURED`` as ``AVAILABLE`` and never invents a fallback winner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.compatible_candidates import resolve_compatible_candidates

if TYPE_CHECKING:
    from agent_loop.domain.capabilities import CapabilityAxis
    from agent_loop.domain.capabilities import CapabilityEvidence
    from agent_loop.domain.capabilities import CapabilityId
    from agent_loop.domain.capabilities import CapabilitySnapshot
    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.domain.compatible_candidates import CompatibleModelCandidate
    from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
    from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
    from agent_loop.domain.task_assessment import TaskAssessment
    from agent_loop.ports.agent_runtime import ExecutionStrategy


@dataclass(frozen=True, slots=True)
class RuntimeReadinessPolicy:
    """Explicit operational axes one runtime must prove before selection.

    The capability id must exist in that runtime's ``RuntimeInventory``. Missing
    snapshots or non-``YES`` axes fail closed; the policy is never inferred from
    adapter names.
    """

    runtime_id: str
    capability_id: CapabilityId
    mandatory_axes: frozenset[CapabilityAxis]

    def __post_init__(self) -> None:
        if not self.runtime_id or not self.runtime_id.strip():
            message = "RuntimeReadinessPolicy.runtime_id must be non-empty"
            raise ValueError(message)
        if not self.mandatory_axes:
            message = "RuntimeReadinessPolicy.mandatory_axes must not be empty"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RoutingConstraints:
    """Control-plane inputs that constrain one routing decision."""

    strategy: ExecutionStrategy
    readiness: tuple[RuntimeReadinessPolicy, ...]
    excluded_combinations: frozenset[tuple[str, str]] = frozenset()
    required_runtime_id: str | None = None
    forbidden_runtime_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.required_runtime_id is not None and self.required_runtime_id in self.forbidden_runtime_ids:
            message = "required_runtime_id must not also be forbidden"
            raise ValueError(message)
        _readiness_by_runtime(self.readiness)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Deterministic S13 outcome consumable by S16 and S14.

    ``selected`` is ``None`` when no operationally eligible candidate exists;
    that is a valid terminal routing outcome, never a silent fallback.
    """

    strategy: ExecutionStrategy
    selected: CompatibleModelCandidate | None
    eligible: tuple[CompatibleModelCandidate, ...]
    evidence: tuple[CapabilityEvidence, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _OperationalOutcome:
    """Internal result of one operational readiness check."""

    reason: str
    evidence: tuple[CapabilityEvidence, ...] | None = None


def select_routing(
    assessment: TaskAssessment,
    *,
    inventories: tuple[RuntimeInventory, ...],
    profiles: ModelProfileCatalog,
    bindings: RuntimeModelProfileCatalog,
    constraints: RoutingConstraints,
) -> RoutingDecision:
    """Select the first operationally eligible candidate in stable order."""
    resolution = resolve_compatible_candidates(
        assessment,
        inventories=inventories,
        profiles=profiles,
        bindings=bindings,
    )
    inventory_by_runtime = {inventory.runtime_identity: inventory for inventory in inventories}
    readiness_by_runtime = _readiness_by_runtime(constraints.readiness)
    eligible: list[CompatibleModelCandidate] = []
    reasons: list[str] = list(resolution.reasons)
    evidence: tuple[CapabilityEvidence, ...] = ()

    for candidate in resolution.candidates:
        outcome = _evaluate_operational_candidate(
            candidate,
            inventory=inventory_by_runtime[candidate.runtime_id],
            readiness=readiness_by_runtime,
            constraints=constraints,
        )
        reasons.append(outcome.reason)
        if outcome.evidence is None:
            continue
        eligible.append(candidate)
        evidence = outcome.evidence

    selected = eligible[0] if eligible else None
    reasons.append(_selection_reason(selected))
    return RoutingDecision(
        strategy=constraints.strategy,
        selected=selected,
        eligible=tuple(eligible),
        evidence=evidence,
        reasons=tuple(sorted(reasons)),
    )


def _readiness_by_runtime(
    readiness: tuple[RuntimeReadinessPolicy, ...],
) -> dict[str, RuntimeReadinessPolicy]:
    indexed: dict[str, RuntimeReadinessPolicy] = {}
    for policy in readiness:
        if policy.runtime_id in indexed:
            message = f"duplicate RuntimeReadinessPolicy.runtime_id: {policy.runtime_id}"
            raise ValueError(message)
        indexed[policy.runtime_id] = policy
    return indexed


def _evaluate_operational_candidate(
    candidate: CompatibleModelCandidate,
    *,
    inventory: RuntimeInventory,
    readiness: dict[str, RuntimeReadinessPolicy],
    constraints: RoutingConstraints,
) -> _OperationalOutcome:
    key = f"{candidate.runtime_id}/{candidate.profile.profile_id}"
    rejection = _constraint_rejection(candidate, constraints)
    if rejection is not None:
        return _OperationalOutcome(f"routing_rejected={key}:{rejection}")
    policy = readiness.get(candidate.runtime_id)
    if policy is None:
        return _OperationalOutcome(f"routing_rejected={key}:readiness_policy_missing")
    snapshot = _snapshot_for(inventory, policy.capability_id.value)
    if snapshot is None:
        return _OperationalOutcome(
            f"routing_rejected={key}:readiness_capability_absent:{policy.capability_id.value}",
        )
    failures = _axis_failures(snapshot, policy.mandatory_axes)
    if failures:
        return _OperationalOutcome(f"routing_rejected={key}:runtime_not_operational:{failures}")
    reason = (
        f"routing_eligible={key}:capability={policy.capability_id.value}:"
        f"axes={','.join(sorted(axis.value for axis in policy.mandatory_axes))}"
    )
    return _OperationalOutcome(reason, snapshot.evidence)


def _constraint_rejection(
    candidate: CompatibleModelCandidate,
    constraints: RoutingConstraints,
) -> str | None:
    if candidate.identity_key() in constraints.excluded_combinations:
        return "excluded_combination"
    if constraints.required_runtime_id is not None and candidate.runtime_id != constraints.required_runtime_id:
        return "runtime_not_required"
    if candidate.runtime_id in constraints.forbidden_runtime_ids:
        return "runtime_forbidden"
    return None


def _snapshot_for(inventory: RuntimeInventory, capability_id: str) -> CapabilitySnapshot | None:
    for snapshot in inventory.capabilities:
        if snapshot.capability_id.value == capability_id:
            return snapshot
    return None


def _axis_failures(snapshot: CapabilitySnapshot, mandatory_axes: frozenset[CapabilityAxis]) -> str:
    states: list[str] = []
    for axis in sorted(mandatory_axes, key=lambda item: item.value):
        state = snapshot.axis_state(axis)
        if state is not CapabilityState.YES:
            states.append(f"{axis.value}={state.value}")
    return ",".join(states)


def _selection_reason(selected: CompatibleModelCandidate | None) -> str:
    if selected is None:
        return "routing_terminal=no_operationally_eligible_candidate"
    return f"routing_selected={selected.runtime_id}/{selected.profile.profile_id}@{selected.native_model_ref}"
