"""Contract tests for OpenCode Server runtime identity and per-package invariants."""

from __future__ import annotations

import ast
from pathlib import Path

from agent_loop.adapters.opencode_server import ADAPTER_VERSION
from agent_loop.adapters.opencode_server import RUNTIME_ID
from agent_loop.adapters.opencode_server import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "agent_loop"
_DOMAIN_ROOT = _PACKAGE_ROOT / "domain"
_PORTS_ROOT = _PACKAGE_ROOT / "ports"
_SERVER_PACKAGE = _PACKAGE_ROOT / "adapters" / "opencode_server"


def test_runtime_identity_is_distinct_from_cli_adapter() -> None:
    """Server identity must not collide with the S09 ``opencode`` runtime."""
    runtime = OpenCodeServerRuntime(endpoint=AttachEndpoint("http://127.0.0.1:9"))
    assert runtime.identity.runtime_id == RUNTIME_ID == "opencode-server"
    assert runtime.adapter_version == ADAPTER_VERSION


def test_unconfigured_runtime_reports_not_configured_inventory(tmp_path: Path) -> None:
    """No endpoint is an explicit configured=NO state, not an empty success."""
    runtime = OpenCodeServerRuntime()
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    assert inventory.runtime_identity == RUNTIME_ID
    capability = next(item for item in inventory.capabilities if item.capability_id.value == "runtime.opencode.server")
    assert capability.configured is CapabilityState.NO
    assert capability.available is CapabilityState.NO
    assert capability.supported is CapabilityState.UNKNOWN


def test_unconfigured_execute_fails_closed_with_neutral_code(tmp_path: Path) -> None:
    """Execution without configuration returns a failure, never a fake success."""
    runtime = OpenCodeServerRuntime()
    request = AgentExecutionRequest(
        task="task",
        strategy=ExecutionStrategy.BUILD,
        model=None,
        required_capabilities=(),
        working_directory=str(tmp_path),
        mutation_policy=MutationPolicy(allow_workspace_mutation=True),
        permission_policy=PermissionPolicy(allow_network=False),
    )
    result = runtime.execute(request)
    assert result.status.value == "failed"
    assert any("server_not_configured" in failure for failure in result.observed_failures)


def test_domain_and_ports_do_not_import_opencode_server_types() -> None:
    """The neutral layer must not depend on adapter-specific OpenCode modules."""
    offenders: list[str] = []
    for root in (_DOMAIN_ROOT, _PORTS_ROOT):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                offenders.extend(
                    f"{path}:{module}"
                    for module in modules
                    if module.startswith("agent_loop.adapters.opencode_server")
                )
    assert offenders == []


def test_adapter_does_not_import_scripts_apps_or_django() -> None:
    """Adapter infrastructure stays independent of host-only packages."""
    forbidden = ("scripts", "apps", "django")
    offenders: list[str] = []
    for path in _SERVER_PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            offenders.extend(
                f"{path}:{module}"
                for module in modules
                if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
            )
    assert offenders == []


def test_error_code_enum_covers_required_neutral_categories() -> None:
    """The required failure taxonomy stays explicit and stable."""
    values = {code.value for code in ServerFailureCode}
    assert {
        "connection_refused",
        "server_unavailable",
        "unsupported_version",
        "authentication_failed",
        "authorization_failed",
        "invalid_request",
        "session_not_found",
        "execution_timeout",
        "cancelled",
        "stream_interrupted",
        "runtime_execution_failure",
        "invalid_response",
        "incomplete_result",
    } <= values


def test_error_carries_code_and_neutral_message() -> None:
    """Errors expose a stable code and a bounded message."""
    error = OpenCodeServerError(ServerFailureCode.INVALID_RESPONSE, "bad payload")
    assert error.code is ServerFailureCode.INVALID_RESPONSE
    assert error.message == "bad payload"
