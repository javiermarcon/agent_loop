"""Shared fixtures for control-plane orchestrator integration tests."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.application.execution_orchestrator import ExecutionRunOptions
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_routing import RuntimeReadinessPolicy
from agent_loop.application.recovery_policy import RecoveryLimits
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
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
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
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from agent_loop.ports.agent_runtime import DiscoveryContext

READ = "filesystem.read"
EXPECTED_RETRY_ATTEMPTS = 2
READINESS_AXES = frozenset(
    {
        CapabilityAxis.SUPPORTED,
        CapabilityAxis.AVAILABLE,
        CapabilityAxis.HEALTHY,
        CapabilityAxis.AUTHORIZED,
    },
)


def now() -> datetime:
    """Return a timezone-aware timestamp for fixtures."""
    return datetime.now(tz=UTC)


def snapshot(
    capability_id: str,
    *,
    available: CapabilityState = CapabilityState.YES,
) -> CapabilitySnapshot:
    """Build one fully-observed fixture capability."""
    return CapabilitySnapshot(
        capability_id=CapabilityId(capability_id),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=available,
        connected=CapabilityState.YES,
        healthy=CapabilityState.YES,
        authorized=CapabilityState.YES,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            CapabilityEvidence(
                source=EvidenceSource.SERVER_API,
                observation=f"{capability_id} fixture",
                observed_at=now(),
                confidence=EvidenceConfidence.HIGH,
                freshness=EvidenceFreshness.FRESH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
    )


def profile(profile_id: str, model_id: str) -> ModelProfile:
    """Build one logical model profile."""
    return ModelProfile(profile_id=profile_id, provider_id="provider", model_id=model_id)


def binding(model_profile: ModelProfile, runtime_id: str) -> RuntimeModelProfile:
    """Bind one profile to a runtime with its native model reference."""
    return RuntimeModelProfile(
        runtime_id=runtime_id,
        profile=model_profile,
        native_model_ref=f"provider/{model_profile.model_id}",
    )


def inventory(runtime_id: str, model_id: str = "model-a") -> RuntimeInventory:
    """Build one minimal operational runtime inventory."""
    return RuntimeInventory(
        runtime_identity=runtime_id,
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=now(),
        capabilities=(snapshot(READ), snapshot(f"runtime.{runtime_id}")),
        models=(InventoryModelRef(model_id=f"provider/{model_id}"),),
    )


def assessment() -> TaskAssessment:
    """Build a low-risk task requiring filesystem read."""
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
        required_capabilities=(
            CapabilityRequirement(
                capability_id=CapabilityId(READ),
                mandatory_axes=frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE}),
            ),
        ),
        required_context=(),
        autonomy_required=AutonomyRequired.LOW,
        reasons=(),
    )


def routing(
    *entries: tuple[str, ModelProfile],
    strategy: ExecutionStrategy = ExecutionStrategy.BUILD,
) -> RoutingInputs:
    """Build routing inputs with one inventory per runtime."""
    models_by_runtime: dict[str, set[str]] = {}
    runtime_ids: list[str] = []
    for runtime_id, model_profile in entries:
        if runtime_id not in models_by_runtime:
            models_by_runtime[runtime_id] = set()
            runtime_ids.append(runtime_id)
        models_by_runtime[runtime_id].add(model_profile.model_id)
    return RoutingInputs(
        inventories=tuple(
            RuntimeInventory(
                runtime_identity=runtime_id,
                runtime_version="1.0.0",
                adapter_version="1",
                discovered_at=now(),
                capabilities=(snapshot(READ), snapshot(f"runtime.{runtime_id}")),
                models=tuple(
                    InventoryModelRef(model_id=f"provider/{model_id}")
                    for model_id in sorted(models_by_runtime[runtime_id])
                ),
            )
            for runtime_id in runtime_ids
        ),
        profiles=ModelProfileCatalog.from_profiles(tuple(model_profile for _, model_profile in entries)),
        bindings=RuntimeModelProfileCatalog.from_bindings(
            tuple(binding(model_profile, runtime_id) for runtime_id, model_profile in entries),
        ),
        readiness=tuple(
            RuntimeReadinessPolicy(
                runtime_id=runtime_id,
                capability_id=CapabilityId(f"runtime.{runtime_id}"),
                mandatory_axes=READINESS_AXES,
            )
            for runtime_id in runtime_ids
        ),
        strategy=strategy,
    )


def success_result(
    runtime_id: str,
    *,
    text: str = "ok",
    evidence: tuple[str, ...] = ("evidence://ok",),
) -> AgentExecutionResult:
    """Build a successful normalized runtime result."""
    return AgentExecutionResult(
        status=AgentExecutionStatus.SUCCEEDED,
        runtime_identity=RuntimeIdentity(runtime_id),
        structured_output=text,
        evidence_refs=evidence,
    )


def failure_result(
    runtime_id: str,
    category: FailureCategory,
    state: ExecutionState,
    *,
    evidence: tuple[str, ...] = ("evidence://failure",),
) -> AgentExecutionResult:
    """Build a failed normalized runtime result with neutral detail."""
    return AgentExecutionResult(
        status=AgentExecutionStatus.FAILED,
        runtime_identity=RuntimeIdentity(runtime_id),
        evidence_refs=evidence,
        observed_failures=(f"{category.value}: fixture",),
        failure=ExecutionFailure(category=category, execution_state=state, evidence=evidence),
    )


class ScriptedRuntime:
    """Deterministic AgentRuntime double driven by an explicit result list."""

    def __init__(self, runtime_id: str, results: tuple[AgentExecutionResult, ...]) -> None:
        self._identity = RuntimeIdentity(runtime_id)
        self._results = list(results)
        self.requests: list[AgentExecutionRequest] = []

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        del context
        return inventory(self._identity.runtime_id)

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return self._results.pop(0)


def work_item(*, status: WorkItemStatus = WorkItemStatus.OPEN) -> WorkItem:
    """Build one schedulable fixture WorkItem."""
    return WorkItem(
        work_item_id="wi-1",
        objective="Implement the fixture task",
        status=status,
        context_refs=("context://fixture",),
    )


def options(
    *,
    limits: RecoveryLimits | None = None,
    mutation: bool = False,
) -> ExecutionRunOptions:
    """Build run options with explicit policies and limits."""
    return ExecutionRunOptions(
        working_directory="workspace-fixture",
        mutation_policy=MutationPolicy(allow_workspace_mutation=mutation),
        permission_policy=PermissionPolicy(allow_network=False),
        limits=limits or RecoveryLimits(),
    )
