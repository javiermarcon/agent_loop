"""Contract tests for Claude Code CLI-only discovery AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.adapters.claude.cli import CliCommandResult
from agent_loop.adapters.claude.runtime import ADAPTER_VERSION
from agent_loop.adapters.claude.runtime import RUNTIME_ID
from agent_loop.adapters.claude.runtime import ClaudeCodeRuntime
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import AgentRuntime
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIXTURES = Path(__file__).parent / "fixtures"
_FIXED_NOW = datetime.now(tz=UTC) - timedelta(seconds=1)
_WORKSPACE = "workspace-fixture"


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _ScriptedCli:
    """Deterministic CLI double keyed by argv tuple."""

    responses: Mapping[tuple[str, ...], CliCommandResult]
    missing_binary: bool = False
    calls: list[tuple[str, ...]] | None = None

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        timeout_seconds: float,
    ) -> CliCommandResult:
        del cwd, timeout_seconds
        if self.calls is not None:
            self.calls.append(argv)
        if self.missing_binary:
            return CliCommandResult(
                argv=argv,
                returncode=127,
                stdout="",
                stderr="claude: not found",
                timed_out=False,
                missing_binary=True,
            )
        if argv not in self.responses:
            message = f"unexpected argv: {argv!r}"
            raise AssertionError(message)
        return self.responses[argv]


def _ok(argv: tuple[str, ...], stdout: str) -> CliCommandResult:
    return CliCommandResult(argv=argv, returncode=0, stdout=stdout, stderr="")


def _fail(
    argv: tuple[str, ...],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 1,
    timed_out: bool = False,
) -> CliCommandResult:
    return CliCommandResult(
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def _observed_discovery_cli(
    *,
    auth_stdout: str | None = None,
    mcp_stdout: str | None = None,
    plugin_stdout: str | None = None,
) -> _ScriptedCli:
    """CLI double using observed fixtures by default."""
    return _ScriptedCli(
        responses={
            ("claude", "--version"): _ok(
                ("claude", "--version"),
                _read("observed_v2_1_283_version.txt"),
            ),
            ("claude", "auth", "status", "--json"): _ok(
                ("claude", "auth", "status", "--json"),
                auth_stdout or _read("observed_v2_1_283_auth_status_not_logged_in.json"),
            ),
            ("claude", "mcp", "list"): _ok(
                ("claude", "mcp", "list"),
                mcp_stdout or _read("observed_v2_1_283_mcp_list_empty.txt"),
            ),
            ("claude", "plugin", "list"): _ok(
                ("claude", "plugin", "list"),
                plugin_stdout or _read("observed_v2_1_283_plugin_list_empty.txt"),
            ),
        },
        calls=[],
    )


def _runtime(cli: _ScriptedCli) -> ClaudeCodeRuntime:
    return ClaudeCodeRuntime(cli_runner=cli, now=_FIXED_NOW)


def test_claude_runtime_satisfies_agent_runtime_protocol() -> None:
    """ClaudeCodeRuntime structurally satisfies AgentRuntime."""
    runtime: AgentRuntime = _runtime(_observed_discovery_cli())
    assert runtime.identity.runtime_id == RUNTIME_ID
    inventory = runtime.discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.runtime_identity == RUNTIME_ID


def test_discover_builds_inventory_from_observed_cli_outputs() -> None:
    """Observed version/auth/mcp-empty/plugins-empty become RuntimeInventory."""
    inventory = _runtime(_observed_discovery_cli()).discover(
        DiscoveryContext(working_directory=_WORKSPACE, project_context="sample"),
    )
    assert inventory.runtime_version == "2.1.283"
    assert inventory.adapter_version == ADAPTER_VERSION
    assert inventory.discovered_at == _FIXED_NOW
    assert inventory.project_context == "sample"
    assert inventory.models == ()
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.claude"].available is CapabilityState.YES
    assert by_id["runtime.claude"].supported is CapabilityState.YES
    assert by_id["runtime.auth"].authorized is CapabilityState.NO
    assert by_id["runtime.auth"].available is CapabilityState.NO
    assert by_id["runtime.auth"].configured is CapabilityState.UNKNOWN
    assert by_id["runtime.plugins"].configured is CapabilityState.NO
    assert by_id["runtime.models"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.lsp"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.skills"].available is CapabilityState.UNKNOWN
    assert "runtime.mcp" not in by_id
    assert all(snap.evidence[0].source is EvidenceSource.CLI for snap in inventory.capabilities)


def test_discover_pending_mcp_is_configured_not_connected() -> None:
    """Observed pending-approval MCP is configured without connectivity."""
    cli = _observed_discovery_cli(mcp_stdout=_read("observed_v2_1_283_mcp_list_pending.txt"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    snap = by_id["mcp.demo-http"]
    assert snap.configured is CapabilityState.YES
    assert snap.connected is CapabilityState.NO
    assert snap.available is CapabilityState.NO
    assert snap.allowed is CapabilityState.NO
    assert snap.healthy is CapabilityState.UNKNOWN


def test_discover_synthetic_mcp_status_axes() -> None:
    """Synthetic Connected/Disabled/needs-auth/Failed/Rejected map conservatively."""
    cli = _observed_discovery_cli(mcp_stdout=_read("synthetic_mcp_mixed_statuses.txt"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["mcp.docs"].connected is CapabilityState.YES
    assert by_id["mcp.docs"].healthy is CapabilityState.UNKNOWN
    assert by_id["mcp.cache"].enabled is CapabilityState.NO
    assert by_id["mcp.private"].authorized is CapabilityState.NO
    assert by_id["mcp.broken"].healthy is CapabilityState.NO
    assert by_id["mcp.denied"].allowed is CapabilityState.NO
    assert by_id["mcp.tools-bad"].connected is CapabilityState.YES
    assert by_id["mcp.tools-bad"].healthy is CapabilityState.NO


def test_discover_ws_invisible_mcp_list_is_channel_unknown() -> None:
    """Health-header-only mcp list becomes runtime.mcp UNKNOWN, not empty YES."""
    cli = _observed_discovery_cli(mcp_stdout=_read("observed_v2_1_283_mcp_list_ws_invisible.txt"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert "WebSocket" in by_id["runtime.mcp"].evidence[0].observation


def test_discover_logged_in_auth_without_leaking_email() -> None:
    """Synthetic logged-in auth sets authorized=YES and redacts email material."""
    cli = _observed_discovery_cli(auth_stdout=_read("synthetic_auth_logged_in.json"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    auth = next(snap for snap in inventory.capabilities if snap.capability_id.value == "runtime.auth")
    assert auth.authorized is CapabilityState.YES
    assert auth.available is CapabilityState.YES
    joined = " ".join(item.observation for item in auth.evidence)
    assert "user@example.com" not in joined
    assert "org-secret" not in joined


def test_discover_invalid_auth_is_unknown() -> None:
    """Invalid auth JSON yields runtime.auth UNKNOWN axes."""
    cli = _observed_discovery_cli(auth_stdout=_read("synthetic_auth_invalid.json"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    auth = next(snap for snap in inventory.capabilities if snap.capability_id.value == "runtime.auth")
    assert auth.authorized is CapabilityState.UNKNOWN
    assert auth.confidence.value == "low"


def test_discover_auth_nonzero_exit_with_logged_in_json_is_unknown() -> None:
    """Non-zero auth exit must not trust JSON claiming loggedIn=true."""
    observed = _observed_discovery_cli()
    responses = dict(observed.responses)
    auth_argv = ("claude", "auth", "status", "--json")
    responses[auth_argv] = _fail(
        auth_argv,
        stdout=_read("synthetic_auth_logged_in.json"),
        stderr="auth probe failed",
        returncode=1,
    )
    inventory = _runtime(_ScriptedCli(responses=responses, calls=[])).discover(
        DiscoveryContext(working_directory=_WORKSPACE),
    )
    auth = next(snap for snap in inventory.capabilities if snap.capability_id.value == "runtime.auth")
    assert auth.authorized is CapabilityState.UNKNOWN
    assert auth.available is CapabilityState.UNKNOWN
    assert auth.configured is CapabilityState.UNKNOWN
    assert auth.confidence.value == "low"
    joined = " ".join(item.observation for item in auth.evidence)
    assert "user@example.com" not in joined
    assert "org-secret" not in joined


def test_discover_plugin_unexpected_format_is_unknown() -> None:
    """Unobserved non-empty plugin list format is channel UNKNOWN."""
    cli = _observed_discovery_cli(plugin_stdout=_read("synthetic_plugin_list_unexpected.txt"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    plugins = next(snap for snap in inventory.capabilities if snap.capability_id.value == "runtime.plugins")
    assert plugins.available is CapabilityState.UNKNOWN


def test_discover_timeouts_and_errors_are_channel_unknown() -> None:
    """Timeouts and non-zero MCP exits become UNKNOWN, never fabricated rows."""
    base = _observed_discovery_cli()
    responses = dict(base.responses)
    responses[("claude", "mcp", "list")] = _fail(
        ("claude", "mcp", "list"),
        stderr="Authorization: Bearer leak-token",
        timed_out=True,
        returncode=124,
    )
    responses[("claude", "auth", "status", "--json")] = _fail(
        ("claude", "auth", "status", "--json"),
        stderr="boom",
        returncode=1,
    )
    inventory = _runtime(_ScriptedCli(responses=responses, calls=[])).discover(
        DiscoveryContext(working_directory=_WORKSPACE),
    )
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert "leak-token" not in by_id["runtime.mcp"].evidence[0].observation
    # Non-zero exit with empty/invalid stdout → auth UNKNOWN
    assert by_id["runtime.auth"].authorized is CapabilityState.UNKNOWN


def test_discover_missing_binary() -> None:
    """Missing claude binary yields runtime.claude available=NO."""
    inventory = _runtime(_ScriptedCli(responses={}, missing_binary=True)).discover(
        DiscoveryContext(working_directory=_WORKSPACE),
    )
    assert inventory.runtime_version == "unknown"
    assert len(inventory.capabilities) == 1
    assert inventory.capabilities[0].available is CapabilityState.NO


def test_discover_duplicate_mcp_names_channel_unknown() -> None:
    """Duplicate MCP ids invalidate the inventory into runtime.mcp UNKNOWN."""
    cli = _observed_discovery_cli(mcp_stdout=_read("synthetic_mcp_duplicates.txt"))
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert not any(cap.capability_id.value.startswith("mcp.") for cap in inventory.capabilities)


def test_execute_is_fail_closed_and_does_not_run_cli() -> None:
    """execute fails closed without invoking task CLI commands."""
    calls: list[tuple[str, ...]] = []
    observed = _observed_discovery_cli()
    cli = _ScriptedCli(responses=dict(observed.responses), calls=calls)
    runtime = _runtime(cli)
    result = runtime.execute(
        AgentExecutionRequest(
            task="do something",
            strategy=ExecutionStrategy.BUILD,
            model=None,
            required_capabilities=(),
            working_directory=_WORKSPACE,
            mutation_policy=MutationPolicy(allow_workspace_mutation=False),
            permission_policy=PermissionPolicy(allow_network=False),
        ),
    )
    assert result.status is AgentExecutionStatus.FAILED
    assert result.runtime_identity.runtime_id == RUNTIME_ID
    assert calls == []
    assert "discovery-only" in result.observed_failures[0]


def test_discover_uses_only_read_only_cli_commands() -> None:
    """Discovery argv set is limited to verified read-only Claude probes."""
    calls: list[tuple[str, ...]] = []
    observed = _observed_discovery_cli()
    cli = _ScriptedCli(responses=dict(observed.responses), calls=calls)
    _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert calls == [
        ("claude", "--version"),
        ("claude", "auth", "status", "--json"),
        ("claude", "mcp", "list"),
        ("claude", "plugin", "list"),
    ]
