"""Contract tests for operational routing (S13)."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest

from agent_loop.application.execution_routing import RoutingConstraints
from agent_loop.application.execution_routing import RuntimeReadinessPolicy
from agent_loop.application.execution_routing import select_routing
from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile
from agent_loop.domain.task_assessment import AutonomyRequired
from agent_loop.domain.task_assessment import ImpactPresence
from agent_loop.domain.task_assessment import TaskAssessment
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.ports.agent_runtime import ExecutionStrategy

_RUNTIME = "runtime.opencode.server"
_READ = "filesystem.read"
_BASE = frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE})
_READINESS_AXES = frozenset(
    {
        CapabilityAxis.SUPPORTED,
        CapabilityAxis.AVAILABLE,
        CapabilityAxis.HEALTHY,
        CapabilityAxis.AUTHORIZED,
    },
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _snapshot(  # noqa: PLR0913 - explicit independent capability axes fixture
    capability_id: str,
    *,
    supported: CapabilityState = CapabilityState.YES,
    configured: CapabilityState = CapabilityState.YES,
    enabled: CapabilityState = CapabilityState.YES,
    available: CapabilityState = CapabilityState.YES,
    connected: CapabilityState = CapabilityState.YES,
    healthy: CapabilityState = CapabilityState.YES,
    authorized: CapabilityState = CapabilityState.YES,
    allowed: CapabilityState = CapabilityState.UNKNOWN,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        capability_id=CapabilityId(capability_id),
        supported=supported,
        configured=configured,
        enabled=enabled,
        available=available,
        connected=connected,
        healthy=healthy,
        authorized=authorized,
        allowed=allowed,
        evidence=(
            CapabilityEvidence(
                source=EvidenceSource.SERVER_API,
                observation=f"{capability_id} fixture",
                observed_at=_now(),
                confidence=EvidenceConfidence.HIGH,
                freshness=EvidenceFreshness.FRESH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
    )


def _requirement(capability_id: str, axes: frozenset[CapabilityAxis] = _BASE) -> CapabilityRequirement:
    return CapabilityRequirement(capability_id=CapabilityId(capability_id), mandatory_axes=axes)


def _assessment(*capabilities: CapabilityRequirement) -> TaskAssessment:
    return TaskAssessment(
        complexity=TaskComplexity.LOCAL,
        risk=TaskRisk.LOW,
        uncertainty=TaskUncertainty.LOW,
        impacted_domains=(),
        estimated_files=None,
        architecture_impact=ImpactPresence.NO,
        schema_impact=ImpactPresence.NO,
        security_impact=ImpactPresence.NO,
        authentication_impact=ImpactPresence.NO,
        payments_impact=ImpactPresence.NO,
        concurrency_impact=ImpactPresence.NO,
        external_integrations=(),
        frontend_backend_boundary=ImpactPresence.NO,
        required_capabilities=capabilities or (_requirement(_READ),),
        required_context=(),
        autonomy_required=AutonomyRequired.LOW,
        reasons=(),
    )


def _profile(profile_id: str = "server-default", model_id: str = "model-a") -> ModelProfile:
    return ModelProfile(profile_id=profile_id, provider_id="opencode-go", model_id=model_id)


def _inventory(
    *,
    runtime_id: str = "opencode-server",
    runtime_snapshot: CapabilitySnapshot | None = None,
    models: tuple[str, ...] = ("opencode-go/model-a",),
) -> RuntimeInventory:
    snapshot = runtime_snapshot or _snapshot(_RUNTIME)
    return RuntimeInventory(
        runtime_identity=runtime_id,
        runtime_version="2.2.6",
        adapter_version="1",
        discovered_at=_now(),
        capabilities=(_snapshot(_READ), snapshot),
        models=tuple(InventoryModelRef(model_id=model_id) for model_id in models),
    )


def _policy(runtime_id: str = "opencode-server") -> RuntimeReadinessPolicy:
    return RuntimeReadinessPolicy(
        runtime_id=runtime_id,
        capability_id=CapabilityId(_RUNTIME),
        mandatory_axes=_READINESS_AXES,
    )


def _route(
    *,
    inventories: tuple[RuntimeInventory, ...],
    bindings: tuple[RuntimeModelProfile, ...],
    profiles: tuple[ModelProfile, ...] | None = None,
    constraints: RoutingConstraints | None = None,
    assessment: TaskAssessment | None = None,
):
    profile_list = profiles or tuple(binding.profile for binding in bindings)
    return select_routing(
        assessment or _assessment(),
        inventories=inventories,
        profiles=ModelProfileCatalog.from_profiles(profile_list),
        bindings=RuntimeModelProfileCatalog.from_bindings(bindings),
        constraints=constraints or RoutingConstraints(strategy=ExecutionStrategy.BUILD, readiness=(_policy(),)),
    )


def _binding(profile: ModelProfile, runtime_id: str = "opencode-server") -> RuntimeModelProfile:
    return RuntimeModelProfile(
        runtime_id=runtime_id,
        profile=profile,
        native_model_ref="opencode-go/model-a",
    )


def test_selects_operation_ready_candidate_with_strategy_and_evidence() -> None:
    """Compatibility plus readiness yields one deterministic selection."""
    profile = _profile()
    decision = _route(inventories=(_inventory(),), bindings=(_binding(profile),))
    assert decision.selected is not None
    assert decision.selected.runtime_id == "opencode-server"
    assert decision.selected.profile.profile_id == "server-default"
    assert decision.strategy is ExecutionStrategy.BUILD
    assert decision.evidence
    assert any(reason.startswith("routing_selected=opencode-server/") for reason in decision.reasons)


def test_configured_but_unavailable_runtime_is_rejected() -> None:
    """CONFIGURED=YES does not make a runtime selectable."""
    profile = _profile()
    inventory = _inventory(
        runtime_snapshot=_snapshot(
            _RUNTIME,
            configured=CapabilityState.YES,
            available=CapabilityState.NO,
        ),
    )
    decision = _route(inventories=(inventory,), bindings=(_binding(profile),))
    assert decision.selected is None
    assert any("available=no" in reason for reason in decision.reasons)


def test_unauthorized_runtime_is_rejected() -> None:
    """A healthy runtime without authorization is not selected."""
    profile = _profile()
    inventory = _inventory(
        runtime_snapshot=_snapshot(_RUNTIME, authorized=CapabilityState.NO),
    )
    decision = _route(inventories=(inventory,), bindings=(_binding(profile),))
    assert decision.selected is None
    assert any("authorized=no" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    "axis",
    [CapabilityAxis.AVAILABLE, CapabilityAxis.HEALTHY, CapabilityAxis.AUTHORIZED, CapabilityAxis.ALLOWED],
)
def test_unknown_operational_axis_fails_closed(axis: CapabilityAxis) -> None:
    """UNKNOWN never becomes availability or authorization."""
    profile = _profile()
    unknown = _snapshot(_RUNTIME)
    snapshot = CapabilitySnapshot(
        capability_id=unknown.capability_id,
        supported=unknown.supported,
        configured=unknown.configured,
        enabled=unknown.enabled,
        available=CapabilityState.UNKNOWN if axis is CapabilityAxis.AVAILABLE else unknown.available,
        connected=unknown.connected,
        healthy=CapabilityState.UNKNOWN if axis is CapabilityAxis.HEALTHY else unknown.healthy,
        authorized=CapabilityState.UNKNOWN if axis is CapabilityAxis.AUTHORIZED else unknown.authorized,
        allowed=unknown.allowed,
        evidence=unknown.evidence,
        confidence=unknown.confidence,
    )
    decision = _route(
        inventories=(_inventory(runtime_snapshot=snapshot),),
        bindings=(_binding(profile),),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(
                RuntimeReadinessPolicy(
                    runtime_id="opencode-server",
                    capability_id=CapabilityId(_RUNTIME),
                    mandatory_axes=_READINESS_AXES | {axis},
                ),
            ),
        ),
    )
    assert decision.selected is None
    assert any(f"{axis.value}=unknown" in reason for reason in decision.reasons)


def test_missing_readiness_policy_fails_closed() -> None:
    """No explicit readiness contract means no operational selection."""
    profile = _profile()
    decision = _route(
        inventories=(_inventory(),),
        bindings=(_binding(profile),),
        constraints=RoutingConstraints(strategy=ExecutionStrategy.BUILD, readiness=()),
    )
    assert decision.selected is None
    assert any("readiness_policy_missing" in reason for reason in decision.reasons)


def test_missing_readiness_capability_snapshot_fails_closed() -> None:
    """A policy referencing an absent inventory snapshot is rejected."""
    profile = _profile()
    inventory = RuntimeInventory(
        runtime_identity="opencode-server",
        runtime_version="2.2.6",
        adapter_version="1",
        discovered_at=_now(),
        capabilities=(_snapshot(_READ),),
        models=(InventoryModelRef(model_id="opencode-go/model-a"),),
    )
    decision = _route(inventories=(inventory,), bindings=(_binding(profile),))
    assert decision.selected is None
    assert any("readiness_capability_absent" in reason for reason in decision.reasons)


def test_excluded_combination_is_never_reselected() -> None:
    """S14 escalation exclusions are honored by the router."""
    profile = _profile()
    decision = _route(
        inventories=(_inventory(),),
        bindings=(_binding(profile),),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(_policy(),),
            excluded_combinations=frozenset({("opencode-server", "server-default")}),
        ),
    )
    assert decision.selected is None
    assert any("excluded_combination" in reason for reason in decision.reasons)


def test_required_runtime_restriction_rejects_other_runtimes() -> None:
    """Model escalation can constrain selection to the current runtime."""
    profile = _profile()
    decision = _route(
        inventories=(_inventory(),),
        bindings=(_binding(profile),),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(_policy(),),
            required_runtime_id="other-runtime",
        ),
    )
    assert decision.selected is None
    assert any("runtime_not_required" in reason for reason in decision.reasons)


def test_forbidden_runtime_restriction_enables_runtime_escalation() -> None:
    """Runtime escalation excludes the runtime that just failed."""
    profile = _profile()
    second = _profile(profile_id="second", model_id="model-b")
    second_inventory = RuntimeInventory(
        runtime_identity="opencode",
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=_now(),
        capabilities=(_snapshot(_READ), _snapshot("runtime.opencode")),
        models=(InventoryModelRef(model_id="opencode-go/model-b"),),
    )
    decision = _route(
        inventories=(_inventory(), second_inventory),
        bindings=(
            _binding(profile),
            RuntimeModelProfile(
                runtime_id="opencode",
                profile=second,
                native_model_ref="opencode-go/model-b",
            ),
        ),
        profiles=(profile, second),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(
                _policy(),
                RuntimeReadinessPolicy(
                    runtime_id="opencode",
                    capability_id=CapabilityId("runtime.opencode"),
                    mandatory_axes=_BASE,
                ),
            ),
            forbidden_runtime_ids=frozenset({"opencode-server"}),
        ),
    )
    assert decision.selected is not None
    assert decision.selected.runtime_id == "opencode"


def test_no_compatible_candidate_is_a_terminal_routing_outcome() -> None:
    """Empty compatibility never invents a winner."""
    profile = _profile()
    inventory = _inventory(models=("other/model",))
    decision = _route(inventories=(inventory,), bindings=(_binding(profile),))
    assert decision.selected is None
    assert decision.eligible == ()
    assert "routing_terminal=no_operationally_eligible_candidate" in decision.reasons


def test_selection_is_deterministic_across_input_order() -> None:
    """Shuffled inventories/bindings yield the same selection."""
    alpha = _profile(profile_id="alpha", model_id="model-a")
    beta = _profile(profile_id="beta", model_id="model-b")
    inventories_a = (
        _inventory(runtime_id="z-runtime", models=("opencode-go/model-b",)),
        _inventory(runtime_id="a-runtime", models=("opencode-go/model-a", "opencode-go/model-b")),
    )
    inventories_b = tuple(reversed(inventories_a))
    bindings_a = (
        RuntimeModelProfile("z-runtime", beta, "opencode-go/model-b"),
        RuntimeModelProfile("a-runtime", beta, "opencode-go/model-b"),
        RuntimeModelProfile("a-runtime", alpha, "opencode-go/model-a"),
    )
    bindings_b = tuple(reversed(bindings_a))
    readiness = (
        _policy("z-runtime"),
        _policy("a-runtime"),
    )
    first = _route(
        inventories=inventories_a,
        bindings=bindings_a,
        profiles=(alpha, beta),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=readiness,
        ),
    )
    second = _route(
        inventories=inventories_b,
        bindings=bindings_b,
        profiles=(beta, alpha),
        constraints=RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=tuple(reversed(readiness)),
        ),
    )
    assert first.selected == second.selected
    assert first.reasons == second.reasons


def test_duplicate_readiness_policy_is_rejected() -> None:
    """Ambiguous per-runtime readiness policies fail closed."""
    with pytest.raises(ValueError, match="duplicate RuntimeReadinessPolicy"):
        RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(_policy(), _policy()),
        )


def test_required_and_forbidden_runtime_are_mutually_exclusive() -> None:
    """Contradictory routing constraints are rejected."""
    with pytest.raises(ValueError, match="required_runtime_id"):
        RoutingConstraints(
            strategy=ExecutionStrategy.BUILD,
            readiness=(_policy(),),
            required_runtime_id="opencode-server",
            forbidden_runtime_ids=frozenset({"opencode-server"}),
        )


def test_readiness_policy_validation() -> None:
    """Readiness policies require a runtime id and at least one axis."""
    with pytest.raises(ValueError, match="runtime_id"):
        RuntimeReadinessPolicy(
            runtime_id=" ",
            capability_id=CapabilityId(_RUNTIME),
            mandatory_axes=_READINESS_AXES,
        )
    with pytest.raises(ValueError, match="mandatory_axes"):
        RuntimeReadinessPolicy(
            runtime_id="opencode-server",
            capability_id=CapabilityId(_RUNTIME),
            mandatory_axes=frozenset(),
        )
