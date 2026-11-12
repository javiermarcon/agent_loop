"""Contract tests for OpenCode CLI-only discovery AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode.cli import CliCommandResult
from agent_loop.adapters.opencode.runtime import ADAPTER_VERSION
from agent_loop.adapters.opencode.runtime import RUNTIME_ID
from agent_loop.adapters.opencode.runtime import OpenCodeRuntime
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
                stderr="opencode: not found",
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


def _observed_discovery_cli(*, agents_stdout: str | None = None) -> _ScriptedCli:
    """CLI double using observed fixtures; agents default to observed empty array."""
    agents_payload = _read("observed_v2_1_4_debug_agents.json") if agents_stdout is None else agents_stdout
    return _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("observed_v2_1_4_mcp_list.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("observed_v2_1_4_models_sample.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                agents_payload,
            ),
        },
        calls=[],
    )


def _runtime(cli: _ScriptedCli) -> OpenCodeRuntime:
    return OpenCodeRuntime(cli_runner=cli, now=_FIXED_NOW)


def test_opencode_runtime_satisfies_agent_runtime_protocol() -> None:
    """OpenCodeRuntime structurally satisfies AgentRuntime."""
    runtime: AgentRuntime = _runtime(_observed_discovery_cli())
    assert runtime.identity.runtime_id == RUNTIME_ID
    inventory = runtime.discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.runtime_identity == RUNTIME_ID


def test_discover_builds_inventory_from_observed_cli_outputs() -> None:
    """Observed version/mcp/models/agents=[] become RuntimeInventory (no synthetic agents)."""
    inventory = _runtime(_observed_discovery_cli()).discover(
        DiscoveryContext(working_directory=_WORKSPACE, project_context="sample"),
    )
    assert inventory.runtime_version == "2.1.4"
    assert inventory.adapter_version == ADAPTER_VERSION
    assert inventory.discovered_at == _FIXED_NOW
    assert inventory.project_context == "sample"
    assert [model.model_id for model in inventory.models] == [
        "cursor-acp/auto",
        "cursor-acp/claude-4-sonnet",
        "cursor-acp/claude-4.5-sonnet",
        "opencode/gpt-5",
        "anthropic/claude-sonnet-4",
        "ollama/gemma3:4b",
    ]
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert "runtime.opencode" in by_id
    assert by_id["runtime.opencode"].available is CapabilityState.YES
    assert by_id["runtime.opencode"].supported is CapabilityState.YES
    assert by_id["runtime.opencode"].configured is CapabilityState.UNKNOWN
    assert by_id["runtime.opencode"].enabled is CapabilityState.UNKNOWN
    assert by_id["runtime.opencode"].connected is CapabilityState.UNKNOWN
    assert by_id["runtime.opencode"].healthy is CapabilityState.UNKNOWN
    assert by_id["runtime.models"].available is CapabilityState.YES
    assert by_id["mcp.notion"].connected is CapabilityState.YES
    assert by_id["mcp.notion"].healthy is CapabilityState.UNKNOWN
    assert by_id["mcp.notion"].authorized is CapabilityState.UNKNOWN
    assert "agent.build" not in by_id
    assert "runtime.agents" not in by_id
    assert all(evidence.source is EvidenceSource.CLI for snap in inventory.capabilities for evidence in snap.evidence)


def test_discover_maps_synthetic_non_empty_agents() -> None:
    """Synthetic non-empty agents JSON maps to agent.* capabilities (not observed CLI)."""
    inventory = _runtime(
        _observed_discovery_cli(agents_stdout=_read("synthetic_agents.json")),
    ).discover(DiscoveryContext(working_directory=_WORKSPACE))
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["agent.build"].configured is CapabilityState.YES
    assert by_id["agent.plan"].configured is CapabilityState.YES


def test_discover_version_alone_does_not_claim_runtime_health() -> None:
    """Detectable ``--version`` does not set configured/enabled/connected/healthy YES."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("observed_v2_1_4_mcp_list_empty.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("observed_v2_1_4_models_sample.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("observed_v2_1_4_debug_agents.json"),
            ),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    runtime_cap = by_id["runtime.opencode"]
    assert runtime_cap.available is CapabilityState.YES
    assert runtime_cap.supported is CapabilityState.YES
    assert runtime_cap.configured is CapabilityState.UNKNOWN
    assert runtime_cap.enabled is CapabilityState.UNKNOWN
    assert runtime_cap.connected is CapabilityState.UNKNOWN
    assert runtime_cap.healthy is CapabilityState.UNKNOWN


def test_discover_maps_synthetic_mcp_statuses_without_claiming_health() -> None:
    """Connected/OAuth CLI text must not over-claim health or authorization."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("synthetic_mcp_mixed_statuses.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("observed_v2_1_4_models_sample.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(("opencode", "debug", "agents"), "[]"),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert by_id["mcp.docs"].connected is CapabilityState.YES
    assert by_id["mcp.docs"].healthy is CapabilityState.UNKNOWN
    assert by_id["mcp.docs"].authorized is CapabilityState.UNKNOWN
    assert "oauth_hint=true" in by_id["mcp.docs"].evidence[0].observation
    assert by_id["mcp.private"].authorized is CapabilityState.NO
    assert by_id["mcp.private"].connected is CapabilityState.NO
    assert by_id["mcp.broken"].healthy is CapabilityState.NO
    assert by_id["mcp.cache"].enabled is CapabilityState.NO
    assert by_id["mcp.pending"].available is CapabilityState.UNKNOWN


def test_discover_missing_binary_fails_closed() -> None:
    """Absent opencode binary yields NO runtime availability and no models."""
    inventory = _runtime(_ScriptedCli(responses={}, missing_binary=True)).discover(
        DiscoveryContext(working_directory=_WORKSPACE),
    )
    assert inventory.runtime_version == "unknown"
    assert inventory.models == ()
    runtime_cap = next(snap for snap in inventory.capabilities if snap.capability_id.value == "runtime.opencode")
    assert runtime_cap.available is CapabilityState.NO
    assert runtime_cap.supported is CapabilityState.NO


def test_discover_timeout_and_execution_errors_are_not_success() -> None:
    """Timeouts/failures keep models empty and channel capabilities unknown."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(("opencode", "--version"), "opencode v2.1.4"),
            ("opencode", "mcp", "list"): _fail(
                ("opencode", "mcp", "list"),
                stderr="permission denied",
            ),
            ("opencode", "models", "--standalone"): _fail(
                ("opencode", "models", "--standalone"),
                returncode=124,
                timed_out=True,
                stderr="timed out",
            ),
            ("opencode", "debug", "agents"): _fail(("opencode", "debug", "agents")),
        },
    )
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.models == ()
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.opencode"].available is CapabilityState.YES
    assert "mcp.notion" not in by_id
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.agents"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.models"].available is CapabilityState.UNKNOWN


def test_discover_valid_empty_mcp_is_not_parse_failure() -> None:
    """Successful empty mcp list must not emit runtime.mcp UNKNOWN."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(("opencode", "mcp", "list"), ""),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("observed_v2_1_4_models_sample.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(("opencode", "debug", "agents"), "[]"),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert "runtime.mcp" not in by_id
    assert not any(cap_id.startswith("mcp.") for cap_id in by_id)
    assert "runtime.agents" not in by_id
    assert by_id["runtime.models"].available is CapabilityState.YES


def test_discover_empty_models_stdout_is_runtime_models_unknown() -> None:
    """rc=0 with empty models stdout is INVALID parse → runtime.models UNKNOWN."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("observed_v2_1_4_mcp_list_empty.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("observed_v2_1_4_debug_agents.json"),
            ),
        },
    )
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.models == ()
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.models"].available is CapabilityState.UNKNOWN


def test_discover_malformed_outputs_do_not_invent_resources() -> None:
    """Garbage mcp/agents output stays UNKNOWN at channel level, not empty inventory."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(("opencode", "--version"), "weird"),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("synthetic_mcp_malformed.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("synthetic_models_malformed.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                "{not-json",
            ),
        },
    )
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.runtime_version == "unknown"
    assert inventory.models == ()
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.agents"].available is CapabilityState.UNKNOWN
    assert by_id["runtime.models"].available is CapabilityState.UNKNOWN
    assert not any(cap_id.startswith("mcp.") for cap_id in by_id)
    assert not any(cap_id.startswith("agent.") for cap_id in by_id)


def test_discover_exit_zero_with_invalid_models_keeps_runtime_models_unknown() -> None:
    """rc=0 alone does not make runtime.models YES when stdout is unparseable."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(("opencode", "mcp", "list"), ""),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                _read("synthetic_models_illegible.txt"),
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("observed_v2_1_4_debug_agents.json"),
            ),
        },
    )
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert inventory.models == ()
    by_id = {snap.capability_id.value: snap for snap in inventory.capabilities}
    assert by_id["runtime.models"].available is CapabilityState.UNKNOWN


def test_discover_invalid_agents_array_propagates_runtime_agents_unknown() -> None:
    """Invalid-only agent arrays become runtime.agents UNKNOWN, not empty agents."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(("opencode", "mcp", "list"), ""),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("synthetic_agents_invalid_only.json"),
            ),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert by_id["runtime.agents"].available is CapabilityState.UNKNOWN
    assert not any(cap_id.startswith("agent.") for cap_id in by_id)


def test_discover_unknown_mcp_status_row_propagates_runtime_mcp_unknown() -> None:
    """Unknown MCP resource status invalidates the channel, not a partial list."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("synthetic_mcp_unknown_status_row.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(("opencode", "debug", "agents"), "[]"),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert "mcp.context7" not in by_id
    assert "mcp.github" not in by_id


def test_discover_never_invokes_mutating_or_auth_commands() -> None:
    """Discovery argv must stay read-only (no mcp auth/add/logout)."""
    cli = _observed_discovery_cli()
    _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    assert cli.calls is not None
    joined = [" ".join(argv) for argv in cli.calls]
    assert all("mcp auth" not in command for command in joined)
    assert all("mcp add" not in command for command in joined)
    assert all("mcp logout" not in command for command in joined)
    assert ("opencode", "mcp", "list") in cli.calls
    assert ("opencode", "models", "--standalone") in cli.calls


def test_discover_duplicate_mcp_names_propagate_runtime_mcp_unknown() -> None:
    """Duplicate MCP names become runtime.mcp UNKNOWN, not colliding mcp.* ids."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(
                ("opencode", "mcp", "list"),
                _read("synthetic_mcp_duplicate_names.txt"),
            ),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("observed_v2_1_4_debug_agents.json"),
            ),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert by_id["runtime.mcp"].available is CapabilityState.UNKNOWN
    assert "mcp.docs" not in by_id


def test_discover_duplicate_agent_ids_propagate_runtime_agents_unknown() -> None:
    """Duplicate synthetic agent ids become runtime.agents UNKNOWN."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): _ok(
                ("opencode", "--version"),
                _read("observed_v2_1_4_version.txt"),
            ),
            ("opencode", "mcp", "list"): _ok(("opencode", "mcp", "list"), ""),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(
                ("opencode", "debug", "agents"),
                _read("synthetic_agents_duplicate.json"),
            ),
        },
    )
    by_id = {
        snap.capability_id.value: snap
        for snap in _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE)).capabilities
    }
    assert by_id["runtime.agents"].available is CapabilityState.UNKNOWN
    assert "agent.build" not in by_id


def test_execute_is_discovery_only_and_side_effect_free() -> None:
    """S9 execute fails closed without invoking the CLI."""
    cli = _observed_discovery_cli()
    runtime = _runtime(cli)
    result = runtime.execute(
        AgentExecutionRequest(
            task="must not run",
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
    assert result.observed_failures
    assert cli.calls == []


def test_discover_redacts_secrets_in_capability_evidence() -> None:
    """Stderr with credential-like content is redacted in evidence."""
    cli = _ScriptedCli(
        responses={
            ("opencode", "--version"): CliCommandResult(
                argv=("opencode", "--version"),
                returncode=0,
                stdout="opencode v2.1.4",
                stderr=(
                    "token=super-secret-token Authorization: Bearer sk-live-abcdef password=hunter2 api_key=cfg-secret"
                ),
            ),
            ("opencode", "mcp", "list"): _ok(("opencode", "mcp", "list"), ""),
            ("opencode", "models", "--standalone"): _ok(
                ("opencode", "models", "--standalone"),
                "",
            ),
            ("opencode", "debug", "agents"): _ok(("opencode", "debug", "agents"), "[]"),
        },
    )
    inventory = _runtime(cli).discover(DiscoveryContext(working_directory=_WORKSPACE))
    observations = [evidence.observation for snap in inventory.capabilities for evidence in snap.evidence]
    joined = "\n".join(observations)
    assert "super-secret-token" not in joined
    assert "sk-live-abcdef" not in joined
    assert "hunter2" not in joined
    assert "cfg-secret" not in joined


def test_required_capabilities_empty_tuple_still_accepted_on_execute() -> None:
    """Empty required_capabilities remains valid on the port request object."""
    request = AgentExecutionRequest(
        task="noop",
        strategy=ExecutionStrategy.PLAN,
        model=None,
        required_capabilities=(),
        working_directory=_WORKSPACE,
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
    )
    assert request.required_capabilities == ()
