"""Behavioral tests for the OpenCode Server AgentRuntime adapter."""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Mapping
    from collections.abc import Sequence

from agent_loop.adapters.opencode_server.client import PermissionRule
from agent_loop.adapters.opencode_server.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.execution import ExecutionPolicy
from agent_loop.adapters.opencode_server.execution import execute_request
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import DiffEntry
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

_RUNTIME_CAPABILITY = "runtime.opencode.server"
_WORKSPACE = "/workspace"
_POLICY_TIMEOUT = 1.5


@dataclass(slots=True)
class _FakeStream:
    client: _ScriptedClient

    def open(self) -> None:
        self.client.stream_opened = True

    def iter_events(self) -> Iterator[Mapping[str, object]]:
        self.client.prompt_started.wait(timeout=5)
        if self.client.stream_error is not None:
            raise self.client.stream_error
        yield from self.client.events
        while not self.client.closed.wait(timeout=0.01):
            continue

    def close(self) -> None:
        self.client.closed.set()

    def shutdown(self) -> None:
        self.client.closed.set()


class _ScriptedClient:
    credentials_configured = True
    base_url = "http://127.0.0.1:1"

    def __init__(  # noqa: PLR0913 - scripted fixture keeps failure modes explicit
        self,
        *,
        version: str | None = "2.2.6",
        outcome: str | None = "succeeded",
        info_error: OpenCodeServerError | None = None,
        create_error: OpenCodeServerError | None = None,
        get_session_error: OpenCodeServerError | None = None,
        events: tuple[dict[str, object], ...] = (),
        stream_error: OpenCodeServerError | None = None,
    ) -> None:
        self.version = version
        self.outcome = outcome
        self.info_error = info_error
        self.create_error = create_error
        self.get_session_error = get_session_error
        self.event_templates = events
        self.stream_error = stream_error
        self.sessions: dict[str, SessionInfo] = {}
        self.prompt_started = threading.Event()
        self.closed = threading.Event()
        self._session_id = "ses_1"
        self.prompt_text: str | None = None
        self.create_calls: list[dict[str, object]] = []
        self.permission_replies: list[tuple[str, str, str]] = []
        self.interrupt_calls: list[str] = []
        self.stream_opened = False

    def info(self) -> ServerInfo:
        if self.info_error is not None:
            raise self.info_error
        return ServerInfo(version=self.version, pid=1, urls=(self.base_url,))

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
        if self.create_error is not None:
            raise self.create_error
        self.create_calls.append(
            {
                "directory": directory,
                "title": title,
                "model": model,
                "agent": agent,
                "permissions": permissions,
            },
        )
        session = SessionInfo(
            session_id=f"ses_{len(self.sessions) + 1}",
            directory=directory,
            model=model,
            outcome=None,
            tokens=TokenUsage(),
            title=title,
        )
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionInfo:
        if self.get_session_error is not None:
            raise self.get_session_error
        session = self.sessions[session_id]
        return SessionInfo(
            session_id=session.session_id,
            directory=session.directory,
            model=session.model,
            outcome=self.outcome,
            tokens=TokenUsage(input_tokens=10, output_tokens=5, cost_usd=0.25),
            title=session.title,
        )

    def prompt(self, session_id: str, text: str) -> None:
        self.prompt_text = text
        self._session_id = session_id
        self.prompt_started.set()

    def interrupt(self, session_id: str) -> bool:
        self.interrupt_calls.append(session_id)
        self.outcome = "interrupted"
        return True

    def messages(self, session_id: str) -> tuple[AssistantMessage, ...]:
        return (AssistantMessage("msg_1", "result text"),)

    def diffs(self, session_id: str) -> tuple[DiffEntry, ...]:
        return (DiffEntry("a.py", 2, 0, "modified"),)

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        self.permission_replies.append((session_id, request_id, decision))

    def open_events(self, types: tuple[str, ...]) -> _FakeStream:
        del types
        return _FakeStream(self)

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        templates = self.event_templates
        if not templates and self.outcome == "succeeded":
            templates = ({"type": "session.execution.succeeded", "data": {}},)
        return tuple(_with_session(event, self._session_id) for event in templates)


def _with_session(event: dict[str, object], session_id: str) -> dict[str, object]:
    copied = dict(event)
    raw_data = event.get("data")
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    data["sessionID"] = session_id
    copied["data"] = data
    return copied


def _request(  # noqa: PLR0913 - test request builder mirrors the port fields
    workspace: Path,
    *,
    task: str = "Implement the task",
    model: str | None = "opencode-go/model-a",
    strategy: ExecutionStrategy = ExecutionStrategy.BUILD,
    mutation: bool = True,
    network: bool = False,
    session_ref: str | None = None,
    schema: str | None = None,
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        task=task,
        strategy=strategy,
        model=model,
        required_capabilities=(),
        working_directory=str(workspace),
        mutation_policy=MutationPolicy(mutation),
        permission_policy=PermissionPolicy(network),
        structured_output_schema=schema,
        session_ref=session_ref,
    )


def _runtime(
    client: _ScriptedClient,
    *,
    execution_timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> OpenCodeServerRuntime:
    return OpenCodeServerRuntime(
        client=client,
        options=RuntimeOptions(execution_timeout_seconds=execution_timeout_seconds),
    )


def _capability(
    inventory: RuntimeInventory,
    capability_id: str = _RUNTIME_CAPABILITY,
) -> CapabilitySnapshot:
    return next(item for item in inventory.capabilities if item.capability_id.value == capability_id)


def test_discover_reports_supported_version_models_and_agents(tmp_path: Path) -> None:
    """Discovery reads only info/model/agent and preserves inventory identity."""
    client = _ScriptedClient()
    inventory = _runtime(client).discover(DiscoveryContext(working_directory=str(tmp_path)))
    capability = _capability(inventory)
    assert capability.supported is CapabilityState.YES
    assert capability.available is CapabilityState.YES
    assert capability.connected is CapabilityState.YES
    assert capability.healthy is CapabilityState.YES
    assert capability.authorized is CapabilityState.YES
    assert [model.model_id for model in inventory.models] == ["opencode-go/model-a"]


def test_discover_auth_failure_is_not_availability(tmp_path: Path) -> None:
    """A reachable 401 sets connected=YES but authorized/available=NO."""
    error = OpenCodeServerError(ServerFailureCode.AUTHENTICATION_FAILED, "bad credentials")
    inventory = _runtime(_ScriptedClient(info_error=error)).discover(
        DiscoveryContext(working_directory=str(tmp_path)),
    )
    capability = _capability(inventory)
    assert capability.connected is CapabilityState.YES
    assert capability.authorized is CapabilityState.NO
    assert capability.available is CapabilityState.NO


def test_discover_connection_refused_is_not_configured_success(tmp_path: Path) -> None:
    """An unreachable configured server is unavailable, not healthy."""
    error = OpenCodeServerError(ServerFailureCode.CONNECTION_REFUSED, "refused")
    inventory = _runtime(_ScriptedClient(info_error=error)).discover(
        DiscoveryContext(working_directory=str(tmp_path)),
    )
    capability = _capability(inventory)
    assert capability.configured is CapabilityState.YES
    assert capability.connected is CapabilityState.NO
    assert capability.available is CapabilityState.NO
    assert capability.healthy is CapabilityState.UNKNOWN


def test_discover_unknown_version_never_becomes_supported(tmp_path: Path) -> None:
    """Unknown versions keep supported and available UNKNOWN."""
    inventory = _runtime(_ScriptedClient(version="not-semver")).discover(
        DiscoveryContext(working_directory=str(tmp_path)),
    )
    capability = _capability(inventory)
    assert capability.supported is CapabilityState.UNKNOWN
    assert capability.available is CapabilityState.UNKNOWN


def test_discover_incompatible_version_is_not_available(tmp_path: Path) -> None:
    """Incompatible versions are reachable but not usable."""
    inventory = _runtime(_ScriptedClient(version="3.0.0")).discover(
        DiscoveryContext(working_directory=str(tmp_path)),
    )
    capability = _capability(inventory)
    assert capability.supported is CapabilityState.NO
    assert capability.available is CapabilityState.NO


def test_discover_does_not_start_managed_server(tmp_path: Path) -> None:
    """Discovery for a managed server never starts a process."""
    managed = ManagedServer(ManagedServerConfig(binary="definitely-missing-opencode"))
    runtime = OpenCodeServerRuntime(managed=managed)
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    capability = _capability(inventory)
    assert capability.configured is CapabilityState.YES
    assert capability.available is CapabilityState.NO
    assert managed.running is False


def test_execute_success_normalizes_result(tmp_path: Path) -> None:
    """A succeeded execution returns neutral content, evidence, usage and session."""
    client = _ScriptedClient()
    runtime = _runtime(client)
    result = runtime.execute(_request(tmp_path))
    assert result.status.value == "succeeded"
    assert result.runtime_identity.runtime_id == "opencode-server"
    assert result.model_id == "opencode-go/model-a"
    assert result.session_ref == "ses_1"
    assert result.structured_output == "result text"
    assert "opencode-server:session:ses_1" in result.evidence_refs
    assert "opencode-server:diff:a.py" in result.evidence_refs
    assert result.usage is not None
    assert result.observed_failures == ()
    assert ("adapter", "opencode-server") in result.harness_metadata
    assert client.prompt_text == "Implement the task"
    call = client.create_calls[0]
    assert call["directory"] == str(tmp_path.resolve())
    assert call["agent"] == "build"
    assert call["model"] == ModelRef("opencode-go", "model-a")


def test_execute_plan_and_read_only_enforces_restrictive_rules(tmp_path: Path) -> None:
    """Read-only execution maps to the plan agent and deny rules."""
    client = _ScriptedClient()
    result = _runtime(client).execute(
        _request(tmp_path, strategy=ExecutionStrategy.PLAN, mutation=False),
    )
    assert result.status.value == "succeeded"
    call = client.create_calls[0]
    assert call["agent"] == "plan"
    assert call["permissions"] == (
        PermissionRule("external_directory", "*", "deny"),
        PermissionRule("webfetch", "*", "deny"),
        PermissionRule("websearch", "*", "deny"),
        PermissionRule("edit", "*", "deny"),
        PermissionRule("shell", "*", "deny"),
    )


def test_execute_rejects_structured_output_schema(tmp_path: Path) -> None:
    """An unsupported contract fails before any session or prompt is created."""
    client = _ScriptedClient()
    result = _runtime(client).execute(_request(tmp_path, schema='{"type":"object"}'))
    assert result.status.value == "failed"
    assert any("unsupported_request" in failure for failure in result.observed_failures)
    assert client.create_calls == []
    assert client.prompt_text is None


@pytest.mark.parametrize("workspace", ["relative/path", "/definitely/missing/workspace"])
def test_execute_rejects_invalid_workspace(workspace: str) -> None:
    """Workspace must be an existing absolute directory."""
    client = _ScriptedClient()
    request = _request(Path(workspace))
    result = _runtime(client).execute(request)
    assert result.status.value == "failed"
    assert any("workspace_invalid" in failure for failure in result.observed_failures)
    assert client.create_calls == []


def test_execute_reuses_explicit_session_ref(tmp_path: Path) -> None:
    """An explicit session is reused only in the same workspace."""
    client = _ScriptedClient()
    existing = SessionInfo("ses_existing", str(tmp_path.resolve()), None, None, TokenUsage(), "t")
    client.sessions["ses_existing"] = existing
    result = _runtime(client).execute(_request(tmp_path, session_ref="ses_existing"))
    assert result.status.value == "succeeded"
    assert result.session_ref == "ses_existing"
    assert client.create_calls == []


def test_execute_rejects_cross_workspace_session_reuse(tmp_path: Path) -> None:
    """A session from another workspace is never silently reused."""
    client = _ScriptedClient()
    client.sessions["ses_other"] = SessionInfo(
        "ses_other",
        str(tmp_path / "other"),
        None,
        None,
        TokenUsage(),
        "t",
    )
    result = _runtime(client).execute(_request(tmp_path, session_ref="ses_other"))
    assert result.status.value == "failed"
    assert any("invalid_request" in failure for failure in result.observed_failures)


def test_execute_missing_session_does_not_create_a_replacement(tmp_path: Path) -> None:
    """A missing session ref fails closed instead of starting a new session."""
    error = OpenCodeServerError(ServerFailureCode.SESSION_NOT_FOUND, "missing")
    client = _ScriptedClient(get_session_error=error)
    result = _runtime(client).execute(_request(tmp_path, session_ref="ses_missing"))
    assert result.status.value == "failed"
    assert any("session_not_found" in failure for failure in result.observed_failures)
    assert client.create_calls == []


def test_execute_failure_event_is_not_success(tmp_path: Path) -> None:
    """A failed terminal event yields FAILED with its detail."""
    client = _ScriptedClient(
        outcome=None,
        events=(
            {
                "type": "session.execution.failed",
                "data": {"error": {"type": "provider", "message": "boom"}},
            },
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert any("boom" in failure for failure in result.observed_failures)


def test_execute_interrupted_event_is_cancelled(tmp_path: Path) -> None:
    """An interrupted terminal event yields CANCELLED, not success."""
    client = _ScriptedClient(
        outcome=None,
        events=({"type": "session.execution.interrupted", "data": {"reason": "user"}},),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "cancelled"


def test_execute_timeout_interrupts_and_fails(tmp_path: Path) -> None:
    """Timeout interrupts the session and never becomes success."""
    client = _ScriptedClient(outcome=None)
    result = _runtime(client, execution_timeout_seconds=0.2).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert any("execution_timeout" in failure for failure in result.observed_failures)
    assert client.interrupt_calls


def test_execute_cancellation_signal_interrupts_and_cancels(tmp_path: Path) -> None:
    """External cancellation propagates to the server session."""
    client = _ScriptedClient(outcome=None)
    cancel = threading.Event()
    cancel.set()
    result = _runtime(client).execute_with_cancellation(_request(tmp_path), cancel)
    assert result.status.value == "cancelled"
    assert client.interrupt_calls


def test_execute_rejects_permission_requests_fail_closed(tmp_path: Path) -> None:
    """Permission asks are rejected and counted, never auto-approved."""
    client = _ScriptedClient(
        events=(
            {
                "type": "permission.asked",
                "data": {"id": "per_1", "action": "read", "resources": ["*.env"]},
            },
            {"type": "session.execution.succeeded", "data": {}},
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "succeeded"
    assert client.permission_replies == [("ses_1", "per_1", "reject")]
    assert ("permission_rejections", "1") in result.harness_metadata


def test_execute_stream_interruption_falls_back_to_session_state(tmp_path: Path) -> None:
    """A lost stream may still recover a terminal outcome from session state."""
    client = _ScriptedClient(
        outcome="succeeded",
        stream_error=OpenCodeServerError(ServerFailureCode.STREAM_INTERRUPTED, "closed"),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "succeeded"


def test_execute_stream_interruption_without_outcome_fails(tmp_path: Path) -> None:
    """A lost stream with no terminal outcome is incomplete, not success."""
    client = _ScriptedClient(
        outcome=None,
        stream_error=OpenCodeServerError(ServerFailureCode.STREAM_INTERRUPTED, "closed"),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert any("stream_interrupted" in failure for failure in result.observed_failures)


def test_execute_unsupported_version_fails_before_session(tmp_path: Path) -> None:
    """The version gate runs before session creation or prompt dispatch."""
    client = _ScriptedClient(version="3.0.0")
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert any("unsupported_version" in failure for failure in result.observed_failures)
    assert client.create_calls == []


def test_execute_authentication_failure_is_neutral(tmp_path: Path) -> None:
    """Authentication failures are reported as neutral codes."""
    client = _ScriptedClient(
        info_error=OpenCodeServerError(ServerFailureCode.AUTHENTICATION_FAILED, "bad password"),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert any("authentication_failed" in failure for failure in result.observed_failures)


def test_execute_error_messages_never_retain_password_patterns(tmp_path: Path) -> None:
    """Failure output redacts secret-shaped text."""
    client = _ScriptedClient(
        info_error=OpenCodeServerError(
            ServerFailureCode.AUTHENTICATION_FAILED,
            "server password super-secret",
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    rendered = " ".join(result.observed_failures)
    assert "super-secret" not in rendered
    assert "<redacted>" in rendered


def test_adapter_satisfies_agent_runtime_surface() -> None:
    """The adapter exposes the existing port surface without widening it."""
    runtime = OpenCodeServerRuntime(endpoint=AttachEndpoint("http://127.0.0.1:9"))
    assert set(inspect.signature(runtime.discover).parameters) == {"context"}
    assert set(inspect.signature(runtime.execute).parameters) == {"request"}
    assert runtime.identity.runtime_id == "opencode-server"
    assert not hasattr(runtime, "read_file")


def test_execution_policy_is_explicit() -> None:
    """ExecutionPolicy keeps timeout/cancellation/observer inputs explicit."""
    policy = ExecutionPolicy(timeout_seconds=_POLICY_TIMEOUT)
    assert policy.timeout_seconds == _POLICY_TIMEOUT
    assert policy.cancel_event is None


def test_execute_request_gate_can_be_used_directly(tmp_path: Path) -> None:
    """The public execution helper keeps the same neutral mapping."""
    client = _ScriptedClient()
    result = execute_request(
        client,
        _request(tmp_path),
        identity=OpenCodeServerRuntime(client=client).identity,
        server_version="2.2.6",
        policy=ExecutionPolicy(timeout_seconds=5),
    )
    assert result.status.value == "succeeded"
