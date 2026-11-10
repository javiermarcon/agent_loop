"""Contract tests for the minimal AgentRuntime port."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import get_type_hints

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
from agent_loop.domain.faults import ExecutionFailure
from agent_loop.domain.faults import ExecutionState
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import AgentRuntime
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import ExecutionUsage
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy
from agent_loop.ports.agent_runtime import RuntimeIdentity

_FORBIDDEN_RUNTIME_METHODS = frozenset(
    {
        "read_file",
        "write_file",
        "bash",
        "lsp_definition",
        "mcp_call",
        "notion_update",
        "compact_context",
        "spawn_subagent",
    },
)
_FIXTURE_WORKSPACE = "workspace-fixture"
_FIXTURE_TOKEN_COUNT = 1


def _observed_at() -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=1)


def _requirement() -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=CapabilityId("filesystem.read"),
        mandatory_axes=frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.ALLOWED}),
    )


def _snapshot() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        capability_id=CapabilityId("filesystem.read"),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=CapabilityState.YES,
        connected=CapabilityState.YES,
        healthy=CapabilityState.YES,
        authorized=CapabilityState.YES,
        allowed=CapabilityState.YES,
        evidence=(
            CapabilityEvidence(
                source=EvidenceSource.CONFIG,
                observation="fixture",
                observed_at=_observed_at(),
                confidence=EvidenceConfidence.HIGH,
                freshness=EvidenceFreshness.FRESH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
    )


def _inventory(runtime_id: str = "runtime-fixture") -> RuntimeInventory:
    return RuntimeInventory(
        runtime_identity=runtime_id,
        runtime_version="0.1.0",
        adapter_version="0.1.0",
        discovered_at=_observed_at(),
        capabilities=(_snapshot(),),
        models=(),
    )


def _request(
    *,
    session_ref: str | None = None,
    required_capabilities: tuple[CapabilityRequirement, ...] | None = None,
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        task="bounded fixture task",
        strategy=ExecutionStrategy.BUILD,
        model="model-fixture",
        required_capabilities=required_capabilities or (_requirement(),),
        working_directory=_FIXTURE_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
        session_ref=session_ref,
        structured_output_schema=None,
        context_refs=("context://fixture",),
    )


class _RecordingRuntime:
    """Minimal AgentRuntime stand-in for structural contract checks."""

    def __init__(self) -> None:
        self._identity = RuntimeIdentity(runtime_id="runtime-fixture")
        self.last_request: AgentExecutionRequest | None = None

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        del context
        return _inventory(self._identity.runtime_id)

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.last_request = request
        return AgentExecutionResult(
            status=AgentExecutionStatus.SUCCEEDED,
            runtime_identity=self._identity,
            model_id=request.model,
            session_ref=request.session_ref or "session-created",
            structured_output='{"ok": true}',
            evidence_refs=("evidence://stdout",),
            usage=ExecutionUsage(
                input_tokens=_FIXTURE_TOKEN_COUNT,
                output_tokens=_FIXTURE_TOKEN_COUNT,
            ),
            observed_failures=(),
            harness_metadata=(("adapter", "fixture"),),
        )


def test_agent_runtime_protocol_exposes_only_discover_and_execute() -> None:
    """The port surface is identity, discover, and execute only."""
    public_names = {
        name
        for name, value in vars(AgentRuntime).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }
    assert public_names == {"identity", "discover", "execute"}
    assert _FORBIDDEN_RUNTIME_METHODS.isdisjoint(public_names)


def test_agent_runtime_protocol_has_no_tool_level_methods() -> None:
    """Harness-internal tool methods must not appear on AgentRuntime."""
    hints = get_type_hints(AgentRuntime)
    for forbidden in _FORBIDDEN_RUNTIME_METHODS:
        assert forbidden not in hints
        assert not hasattr(AgentRuntime, forbidden)


def test_execution_request_carries_required_capabilities() -> None:
    """Required capabilities travel with the execution request."""
    requirement = _requirement()
    request = _request(required_capabilities=(requirement,))
    assert request.required_capabilities == (requirement,)
    assert request.strategy is ExecutionStrategy.BUILD
    assert request.mutation_policy.allow_workspace_mutation is False
    assert request.permission_policy.allow_network is False


def test_session_ref_is_optional_on_request_and_result() -> None:
    """session_ref may be omitted on request and still returned on result."""
    runtime: AgentRuntime = _RecordingRuntime()
    result = runtime.execute(_request(session_ref=None))
    assert result.session_ref == "session-created"
    resumed = runtime.execute(_request(session_ref="session-existing"))
    assert resumed.session_ref == "session-existing"


def test_execution_result_is_normalized() -> None:
    """Results expose status, identity, evidence, usage, and failures."""
    runtime = _RecordingRuntime()
    result = runtime.execute(_request())
    assert result.status is AgentExecutionStatus.SUCCEEDED
    assert result.runtime_identity.runtime_id == "runtime-fixture"
    assert result.model_id == "model-fixture"
    assert result.structured_output == '{"ok": true}'
    assert result.evidence_refs == ("evidence://stdout",)
    assert result.usage is not None
    assert result.usage.input_tokens == _FIXTURE_TOKEN_COUNT
    assert result.observed_failures == ()
    assert result.harness_metadata == (("adapter", "fixture"),)
    assert result.failure is None


def test_execution_result_can_carry_neutral_failure_detail() -> None:
    """A failed result exposes adapter-neutral failure state for S14."""
    failure = ExecutionFailure(
        category=FailureCategory.TIMEOUT,
        execution_state=ExecutionState.STARTED,
        evidence=("evidence://timeout",),
        detail="timed out",
    )
    result = AgentExecutionResult(
        status=AgentExecutionStatus.FAILED,
        runtime_identity=RuntimeIdentity("runtime-fixture"),
        failure=failure,
    )
    assert result.failure == failure
    assert result.failure.execution_state is ExecutionState.STARTED


def test_recording_runtime_satisfies_agent_runtime_protocol() -> None:
    """A discover/execute adapter structurally satisfies AgentRuntime."""
    runtime: AgentRuntime = _RecordingRuntime()
    inventory = runtime.discover(
        DiscoveryContext(working_directory=_FIXTURE_WORKSPACE),
    )
    assert inventory.runtime_identity == runtime.identity.runtime_id
    assert isinstance(runtime.identity, RuntimeIdentity)


def test_request_rejects_empty_task() -> None:
    """Execution requests fail closed on blank tasks."""
    with pytest.raises(ValueError, match="task"):
        AgentExecutionRequest(
            task="   ",
            strategy=ExecutionStrategy.PLAN,
            model=None,
            required_capabilities=(_requirement(),),
            working_directory=_FIXTURE_WORKSPACE,
            mutation_policy=MutationPolicy(allow_workspace_mutation=False),
            permission_policy=PermissionPolicy(allow_network=False),
        )


def test_request_rejects_empty_working_directory() -> None:
    """Execution requests fail closed on blank working directories."""
    with pytest.raises(ValueError, match="working_directory"):
        AgentExecutionRequest(
            task="bounded fixture task",
            strategy=ExecutionStrategy.PLAN,
            model=None,
            required_capabilities=(_requirement(),),
            working_directory="   ",
            mutation_policy=MutationPolicy(allow_workspace_mutation=False),
            permission_policy=PermissionPolicy(allow_network=False),
        )


def test_runtime_identity_rejects_blank_values() -> None:
    """RuntimeIdentity fails closed on empty or whitespace-only ids."""
    with pytest.raises(ValueError, match="runtime_id"):
        RuntimeIdentity("")
    with pytest.raises(ValueError, match="runtime_id"):
        RuntimeIdentity("   ")


def test_discovery_context_rejects_blank_working_directory() -> None:
    """DiscoveryContext fails closed on blank working directories."""
    with pytest.raises(ValueError, match="working_directory"):
        DiscoveryContext(working_directory="")
    with pytest.raises(ValueError, match="working_directory"):
        DiscoveryContext(working_directory="   ")
