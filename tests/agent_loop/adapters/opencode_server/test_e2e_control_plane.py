"""End-to-end control-plane flow through the OpenCode Server adapter (S22)."""

from __future__ import annotations

import threading
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode_server.discovery import managed_not_started_inventory
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import DiffEntry
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.application.execution_orchestrator import ExecutionRunOptions
from agent_loop.application.execution_orchestrator import ExecutionRunStatus
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_orchestrator import execute_work_item_with_recovery
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
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Mapping
    from collections.abc import Sequence
    from pathlib import Path

    from agent_loop.adapters.opencode_server.client import PermissionRule

_READINESS_AXES = frozenset(
    {
        CapabilityAxis.SUPPORTED,
        CapabilityAxis.AVAILABLE,
        CapabilityAxis.HEALTHY,
        CapabilityAxis.AUTHORIZED,
    },
)


class _Stream:
    def __init__(self, client: _ServerClient) -> None:
        self._client = client

    def open(self) -> None:
        return None

    def iter_events(self) -> Iterator[Mapping[str, object]]:
        self._client.prompt_started.wait(timeout=5)
        yield from self._client.events
        while not self._client.closed.wait(timeout=0.01):
            continue

    def shutdown(self) -> None:
        self._client.closed.set()

    def close(self) -> None:
        self._client.closed.set()


class _ServerClient:
    """Controlled server implementing only the documented adapter surface."""

    credentials_configured = True
    base_url = "http://127.0.0.1:1"

    def __init__(
        self,
        *,
        outcome: str | None = "succeeded",
        create_error: OpenCodeServerError | None = None,
    ) -> None:
        self.outcome = outcome
        self.create_error = create_error
        self.sessions: dict[str, SessionInfo] = {}
        self.prompt_started = threading.Event()
        self.closed = threading.Event()
        self.prompts: list[str] = []
        self.interrupts: list[str] = []

    def info(self) -> ServerInfo:
        return ServerInfo(version="2.2.6", pid=1, urls=(self.base_url,))

    def models(self) -> tuple[ModelRef, ...]:
        return (ModelRef("opencode-go", "model-a"),)

    def agents(self) -> tuple[str, ...]:
        return ("build", "plan")

    def create_session(
        self,
        *,
        directory: str,
        title: str,
        model: ModelRef | None,
        agent: str,
        permissions: Sequence[PermissionRule] = (),
    ) -> SessionInfo:
        del title, permissions
        if self.create_error is not None:
            raise self.create_error
        session = SessionInfo(
            session_id=f"ses_{len(self.sessions) + 1}",
            directory=directory,
            model=model,
            outcome=None,
            tokens=TokenUsage(),
            title="agent-loop execution",
        )
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionInfo:
        session = self.sessions[session_id]
        return SessionInfo(
            session_id=session.session_id,
            directory=session.directory,
            model=session.model,
            outcome=self.outcome,
            tokens=TokenUsage(input_tokens=3, output_tokens=2),
            title=session.title,
        )

    def prompt(self, session_id: str, text: str) -> None:
        del session_id
        self.prompts.append(text)
        self.prompt_started.set()

    def interrupt(self, session_id: str) -> bool:
        self.interrupts.append(session_id)
        self.outcome = "interrupted"
        return True

    def messages(self, session_id: str) -> tuple[AssistantMessage, ...]:
        del session_id
        return (AssistantMessage("msg_1", "done"),)

    def diffs(self, session_id: str) -> tuple[DiffEntry, ...]:
        del session_id
        return (DiffEntry("calc.py", 2, 0, "modified"),)

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        del session_id, request_id, decision

    def open_events(self, types: tuple[str, ...]) -> _Stream:
        del types
        return _Stream(self)

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        if self.outcome != "succeeded":
            return ()
        return ({"type": "session.execution.succeeded", "data": {"sessionID": "ses_1"}},)


class _BackupRuntime:
    def __init__(self, runtime_id: str = "runtime-backup") -> None:
        self._identity = RuntimeIdentity(runtime_id)
        self.requests: list[AgentExecutionRequest] = []

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        del context
        return _backup_inventory()

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.SUCCEEDED,
            runtime_identity=self._identity,
            structured_output="backup executed",
            evidence_refs=("evidence://backup",),
        )


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _backup_inventory() -> RuntimeInventory:
    def snapshot(capability_id: str) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            capability_id=CapabilityId(capability_id),
            supported=CapabilityState.YES,
            configured=CapabilityState.YES,
            enabled=CapabilityState.YES,
            available=CapabilityState.YES,
            connected=CapabilityState.YES,
            healthy=CapabilityState.YES,
            authorized=CapabilityState.YES,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                CapabilityEvidence(
                    source=EvidenceSource.PROBE,
                    observation=f"{capability_id} e2e fixture",
                    observed_at=_now(),
                    confidence=EvidenceConfidence.HIGH,
                    freshness=EvidenceFreshness.FRESH,
                ),
            ),
            confidence=EvidenceConfidence.HIGH,
        )

    return RuntimeInventory(
        runtime_identity="runtime-backup",
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=_now(),
        capabilities=(snapshot("filesystem.read"), snapshot("runtime.runtime-backup")),
        models=(InventoryModelRef(model_id="provider/model-backup"),),
    )


def _assessment() -> TaskAssessment:
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
                capability_id=CapabilityId("filesystem.read"),
                mandatory_axes=frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE}),
            ),
        ),
        required_context=(),
        autonomy_required=AutonomyRequired.LOW,
        reasons=(),
    )


def _routing(
    inventories: tuple[RuntimeInventory, ...],
    entries: tuple[tuple[str, ModelProfile], ...],
) -> RoutingInputs:
    return RoutingInputs(
        inventories=inventories,
        profiles=ModelProfileCatalog.from_profiles(tuple(profile for _, profile in entries)),
        bindings=RuntimeModelProfileCatalog.from_bindings(
            tuple(
                RuntimeModelProfile(
                    runtime_id=runtime_id,
                    profile=profile,
                    native_model_ref=(
                        f"opencode-go/{profile.model_id}"
                        if runtime_id == "opencode-server"
                        else f"provider/{profile.model_id}"
                    ),
                )
                for runtime_id, profile in entries
            ),
        ),
        readiness=tuple(
            RuntimeReadinessPolicy(
                runtime_id=runtime_id,
                capability_id=CapabilityId(
                    "runtime.opencode.server" if runtime_id == "opencode-server" else f"runtime.{runtime_id}",
                ),
                mandatory_axes=_READINESS_AXES,
            )
            for runtime_id, _ in entries
        ),
        strategy=ExecutionStrategy.BUILD,
    )


def _options(
    workdir: Path,
    *,
    mutation: bool,
    limits: RecoveryLimits | None = None,
) -> ExecutionRunOptions:
    return ExecutionRunOptions(
        working_directory=str(workdir),
        mutation_policy=MutationPolicy(allow_workspace_mutation=mutation),
        permission_policy=PermissionPolicy(allow_network=False),
        limits=limits or RecoveryLimits(),
    )


def _work_item() -> WorkItem:
    return WorkItem(work_item_id="wi-e2e", objective="Implement the fixture change")


def _server_runtime(client: _ServerClient, *, timeout_seconds: float = 5.0) -> OpenCodeServerRuntime:
    return OpenCodeServerRuntime(
        client=client,
        options=RuntimeOptions(execution_timeout_seconds=timeout_seconds),
    )


def test_opencode_server_is_selected_and_executes_through_the_full_flow(tmp_path: Path) -> None:
    """TaskAssessment → S13 → S16 → S22 → result is demonstrated end to end."""
    client = _ServerClient()
    runtime = _server_runtime(client)
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    profile = ModelProfile(profile_id="server-default", provider_id="opencode-go", model_id="model-a")
    outcome = execute_work_item_with_recovery(
        _work_item(),
        _assessment(),
        options=_options(tmp_path, mutation=False),
        routing=_routing((inventory,), (("opencode-server", profile),)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].routing.selected is not None
    assert outcome.attempts[0].routing.selected.runtime_id == "opencode-server"
    assert outcome.attempts[0].request is not None
    assert outcome.attempts[0].request.model == "opencode-go/model-a"
    assert outcome.final_result is not None
    assert outcome.final_result.session_ref == "ses_1"
    assert outcome.final_result.evidence_refs
    assert client.prompts == ["Implement the fixture change"]


def test_another_runtime_is_selected_when_opencode_server_is_not_available(tmp_path: Path) -> None:
    """Configured but stopped OpenCode Server never blocks a valid alternative."""
    unavailable = managed_not_started_inventory(observed_at=_now(), project_context=None)
    backup = _BackupRuntime()
    profile = ModelProfile(profile_id="backup", provider_id="provider", model_id="model-backup")
    outcome = execute_work_item_with_recovery(
        _work_item(),
        _assessment(),
        options=_options(tmp_path, mutation=False),
        routing=_routing(
            (unavailable, _backup_inventory()),
            (("opencode-server", ModelProfile("server", "opencode-go", "model-a")), ("runtime-backup", profile)),
        ),
        runtimes=(backup,),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].routing.selected is not None
    assert outcome.attempts[0].routing.selected.runtime_id == "runtime-backup"
    assert len(backup.requests) == 1


def test_started_timeout_is_not_retried_after_possible_effects(tmp_path: Path) -> None:
    """A real S22 timeout is mapped to S14 and blocks automatic re-execution."""
    client = _ServerClient(outcome=None)
    runtime = _server_runtime(client, timeout_seconds=0.1)
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    profile = ModelProfile(profile_id="server-default", provider_id="opencode-go", model_id="model-a")
    outcome = execute_work_item_with_recovery(
        _work_item(),
        _assessment(),
        options=_options(
            tmp_path,
            mutation=True,
            limits=RecoveryLimits(max_attempts_per_combination=3, max_total_attempts=5),
        ),
        routing=_routing((inventory,), (("opencode-server", profile),)),
        runtimes=(runtime,),
    )
    assert outcome.status is ExecutionRunStatus.HUMAN_INTERVENTION
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].classification.category.value == "timeout"
    assert outcome.attempts[0].classification.effects_possible is True
    assert len(client.prompts) == 1
    assert client.interrupts


def test_pre_execution_server_failure_escalates_to_another_runtime(tmp_path: Path) -> None:
    """A failure before any session escalates through S13 to a valid runtime."""
    client = _ServerClient(create_error=OpenCodeServerError(ServerFailureCode.SERVER_UNAVAILABLE, "down"))
    runtime = _server_runtime(client)
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    backup = _BackupRuntime()
    server_profile = ModelProfile("server-default", "opencode-go", "model-a")
    backup_profile = ModelProfile("backup", "provider", "model-backup")
    outcome = execute_work_item_with_recovery(
        _work_item(),
        _assessment(),
        options=_options(
            tmp_path,
            mutation=False,
            limits=RecoveryLimits(max_attempts_per_combination=1, max_total_attempts=3),
        ),
        routing=_routing(
            (inventory, _backup_inventory()),
            (("opencode-server", server_profile), ("runtime-backup", backup_profile)),
        ),
        runtimes=(runtime, backup),
    )
    assert outcome.status is ExecutionRunStatus.SUCCEEDED
    assert outcome.attempts[0].classification is not None
    assert outcome.attempts[0].recovery is not None
    assert outcome.attempts[0].recovery.action.value == "escalate_runtime"
    assert outcome.attempts[1].routing.selected is not None
    assert outcome.attempts[1].routing.selected.runtime_id == "runtime-backup"
    assert client.prompts == []
    assert len(backup.requests) == 1
