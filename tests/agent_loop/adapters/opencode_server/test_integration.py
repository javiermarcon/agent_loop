"""Real OpenCode Server integration tests (skipped when the binary is absent)."""

from __future__ import annotations

import os
import shutil
import time
from typing import TYPE_CHECKING

import pytest

from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.mapping import parse_native_model
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_BINARY = shutil.which("opencode")
_DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
_E2E_ENV = "AGENT_LOOP_E2E_OPENCODE_SERVER"
_MODEL_ENV = "AGENT_LOOP_OPENCODE_SERVER_MODEL"
_MAX_EXECUTION_SECONDS = 180

pytestmark = pytest.mark.skipif(_BINARY is None, reason="opencode binary is not installed")


@pytest.fixture
def managed_runtime(tmp_path: Path) -> Iterator[OpenCodeServerRuntime]:
    managed = ManagedServer(
        ManagedServerConfig(
            binary=_BINARY or "opencode",
            working_directory=str(tmp_path),
            startup_timeout_seconds=60,
            readiness_timeout_seconds=30,
        ),
    )
    runtime = OpenCodeServerRuntime(
        managed=managed,
        options=RuntimeOptions(execution_timeout_seconds=180),
    )
    runtime.ensure_started()
    try:
        yield runtime
    finally:
        pid = managed.pid
        runtime.close()
        assert managed.running is False
        assert not _alive(pid)


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _model() -> str:
    return os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)


def _request(workspace: Path, task: str) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        task=task,
        strategy=ExecutionStrategy.BUILD,
        model=_model(),
        required_capabilities=(),
        working_directory=str(workspace.resolve()),
        mutation_policy=MutationPolicy(allow_workspace_mutation=True),
        permission_policy=PermissionPolicy(allow_network=False),
    )


def test_real_discovery_reports_supported_running_server(
    managed_runtime: OpenCodeServerRuntime,
    tmp_path: Path,
) -> None:
    """A real managed server is discoverable through public endpoints only."""
    inventory = managed_runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    capability = next(item for item in inventory.capabilities if item.capability_id.value == "runtime.opencode.server")
    assert inventory.runtime_identity == "opencode-server"
    assert capability.configured is CapabilityState.YES
    assert capability.connected is CapabilityState.YES
    assert capability.healthy is CapabilityState.YES
    assert capability.supported is CapabilityState.YES
    assert capability.available is CapabilityState.YES
    assert capability.authorized is CapabilityState.YES
    assert inventory.models
    assert inventory.runtime_version.startswith("2.")


@pytest.mark.skipif(
    os.environ.get(_E2E_ENV) != "1",
    reason=f"set {_E2E_ENV}=1 to run real model execution",
)
def test_real_execution_returns_terminal_result(
    managed_runtime: OpenCodeServerRuntime,
    tmp_path: Path,
) -> None:
    """One real prompt reaches the server and produces a terminal normalized result."""
    model = parse_native_model(_model())
    assert model is not None
    inventory = managed_runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    if not any(ref.model_id == model.as_text() for ref in inventory.models):
        pytest.skip(f"configured model {model.as_text()} is not present in the server inventory")
    started = time.monotonic()
    result = managed_runtime.execute(
        _request(
            tmp_path,
            "Reply with the single word READY. Do not use tools and do not modify files.",
        ),
    )
    assert result.status.value == "succeeded"
    assert result.session_ref is not None
    assert result.session_ref.startswith("ses_")
    assert result.structured_output is not None
    assert "READY" in result.structured_output.upper()
    assert result.evidence_refs
    assert time.monotonic() - started < _MAX_EXECUTION_SECONDS
