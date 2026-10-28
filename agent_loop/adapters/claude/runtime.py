"""Claude Code AgentRuntime adapter: CLI-only read-only discovery (S10)."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.adapters.claude.cli import SubprocessCliRunner
from agent_loop.adapters.claude.mcp_axes import mcp_axes_for_status
from agent_loop.adapters.claude.parse import ParseOutcome
from agent_loop.adapters.claude.parse import parse_auth_status_json
from agent_loop.adapters.claude.parse import parse_mcp_list
from agent_loop.adapters.claude.parse import parse_plugin_list
from agent_loop.adapters.claude.parse import parse_version
from agent_loop.adapters.claude.parse import redact_sensitive
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from agent_loop.adapters.claude.cli import CliCommandResult
    from agent_loop.adapters.claude.cli import CliRunner
    from agent_loop.adapters.claude.parse import AuthObservation
    from agent_loop.adapters.claude.parse import McpServerObservation
    from agent_loop.ports.agent_runtime import AgentExecutionRequest
    from agent_loop.ports.agent_runtime import DiscoveryContext

RUNTIME_ID = "claude"
ADAPTER_VERSION = "0.1.0"
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 45.0
_UNKNOWN_VERSION = "unknown"


class ClaudeCodeRuntime:
    """CLI discovery adapter for Claude Code (execute deferred beyond S10)."""

    def __init__(
        self,
        *,
        cli_runner: CliRunner | None = None,
        binary: str = "claude",
        now: datetime | None = None,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._cli = cli_runner or SubprocessCliRunner()
        self._binary = binary
        self._now = now
        self._timeout_seconds = timeout_seconds
        self._identity = RuntimeIdentity(runtime_id=RUNTIME_ID)

    @property
    def identity(self) -> RuntimeIdentity:
        """Return the stable runtime identity for this adapter."""
        return self._identity

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        """Probe Claude Code via read-only CLI and build a RuntimeInventory."""
        observed_at = self._now or datetime.now(tz=UTC)
        version_result = self._run(("--version",), cwd=context.working_directory)
        if version_result.missing_binary:
            return self._missing_binary_inventory(
                context=context,
                observed_at=observed_at,
                version_result=version_result,
            )

        runtime_version = parse_version(version_result.stdout) or _UNKNOWN_VERSION
        auth_result = self._run(("auth", "status", "--json"), cwd=context.working_directory)
        mcp_result = self._run(("mcp", "list"), cwd=context.working_directory)
        plugin_result = self._run(("plugin", "list"), cwd=context.working_directory)

        capabilities: list[CapabilitySnapshot] = [
            self._runtime_snapshot(
                runtime_version=runtime_version,
                observed_at=observed_at,
                version_result=version_result,
            ),
            self._auth_snapshot(
                auth_result=auth_result,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
            self._unverified_channel(
                capability_id="runtime.models",
                observation=(f"no read-only Claude Code models inventory CLI verified (adapter={ADAPTER_VERSION})"),
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="models",
            ),
            self._unverified_channel(
                capability_id="runtime.lsp",
                observation="no read-only Claude Code LSP inventory CLI verified",
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="lsp",
            ),
            self._unverified_channel(
                capability_id="runtime.skills",
                observation="no read-only Claude Code skills inventory CLI verified",
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="skills",
            ),
        ]
        capabilities.extend(
            self._mcp_discovery_snapshots(
                mcp_result=mcp_result,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
        )
        capabilities.append(
            self._plugins_snapshot(
                plugin_result=plugin_result,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
        )
        return RuntimeInventory(
            runtime_identity=RUNTIME_ID,
            runtime_version=runtime_version,
            adapter_version=ADAPTER_VERSION,
            discovered_at=observed_at,
            capabilities=tuple(capabilities),
            models=(),
            project_context=context.project_context,
        )

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        """Refuse execution in the discovery-only S10 adapter."""
        del request
        return AgentExecutionResult(
            status=AgentExecutionStatus.FAILED,
            runtime_identity=self._identity,
            observed_failures=("ClaudeCodeRuntime S10 is discovery-only; execute is not implemented",),
            harness_metadata=(("adapter", "claude"), ("slice", "s10-discovery")),
        )

    def _run(self, args: tuple[str, ...], *, cwd: str) -> CliCommandResult:
        return self._cli.run(
            (self._binary, *args),
            cwd=cwd,
            timeout_seconds=self._timeout_seconds,
        )

    def _missing_binary_inventory(
        self,
        *,
        context: DiscoveryContext,
        observed_at: datetime,
        version_result: CliCommandResult,
    ) -> RuntimeInventory:
        evidence = self._evidence(
            observation=f"binary missing: {redact_sensitive(version_result.stderr)}",
            observed_at=observed_at,
            runtime_version=None,
            confidence=EvidenceConfidence.HIGH,
        )
        runtime_cap = CapabilitySnapshot(
            capability_id=CapabilityId("runtime.claude"),
            supported=CapabilityState.NO,
            configured=CapabilityState.UNKNOWN,
            enabled=CapabilityState.UNKNOWN,
            available=CapabilityState.NO,
            connected=CapabilityState.NO,
            healthy=CapabilityState.NO,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(evidence,),
            confidence=EvidenceConfidence.HIGH,
            native_binding=self._binary,
        )
        return RuntimeInventory(
            runtime_identity=RUNTIME_ID,
            runtime_version=_UNKNOWN_VERSION,
            adapter_version=ADAPTER_VERSION,
            discovered_at=observed_at,
            capabilities=(runtime_cap,),
            models=(),
            project_context=context.project_context,
        )

    def _runtime_snapshot(
        self,
        *,
        runtime_version: str,
        observed_at: datetime,
        version_result: CliCommandResult,
    ) -> CapabilitySnapshot:
        # ``--version`` only proves the binary responds with a known Claude
        # Code identity. It does not demonstrate auth, MCP, or execution.
        version_ok = version_result.ok and runtime_version != _UNKNOWN_VERSION
        state = CapabilityState.YES if version_ok else CapabilityState.UNKNOWN
        observation = redact_sensitive(
            f"claude --version rc={version_result.returncode} "
            f"version={runtime_version} stderr={version_result.stderr.strip()}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId("runtime.claude"),
            supported=state,
            configured=CapabilityState.UNKNOWN,
            enabled=CapabilityState.UNKNOWN,
            available=state,
            connected=CapabilityState.UNKNOWN,
            healthy=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.HIGH,
                ),
            ),
            confidence=EvidenceConfidence.HIGH,
            native_binding=self._binary,
        )

    def _auth_snapshot(
        self,
        *,
        auth_result: CliCommandResult,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        # Auth axes require a successful CLI exit; non-zero/timeout/missing
        # binary stdout is not trustworthy even when JSON-shaped.
        if not auth_result.ok:
            return self._unverified_channel(
                capability_id="runtime.auth",
                observation=(
                    f"claude auth status --json rc={auth_result.returncode} "
                    f"timed_out={auth_result.timed_out} "
                    f"missing_binary={auth_result.missing_binary} "
                    f"stderr={auth_result.stderr.strip()}"
                ),
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="auth status",
            )
        parsed = parse_auth_status_json(auth_result.stdout)
        if parsed.outcome is ParseOutcome.INVALID or parsed.auth is None:
            return self._unverified_channel(
                capability_id="runtime.auth",
                observation="claude auth status --json stdout unparseable for known schema",
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="auth status",
            )
        return self._auth_from_observation(
            auth=parsed.auth,
            runtime_version=runtime_version,
            observed_at=observed_at,
        )

    def _auth_from_observation(
        self,
        *,
        auth: AuthObservation,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        if auth.logged_in:
            authorized = CapabilityState.YES
            available = CapabilityState.YES
            configured = CapabilityState.YES
        else:
            # loggedIn=false proves no session; local config may still exist.
            authorized = CapabilityState.NO
            available = CapabilityState.NO
            configured = CapabilityState.UNKNOWN
        provider = auth.api_provider or "unknown"
        observation = redact_sensitive(
            f"auth status loggedIn={auth.logged_in} authMethod={auth.auth_method} apiProvider={provider}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId("runtime.auth"),
            supported=CapabilityState.YES,
            configured=configured,
            enabled=CapabilityState.UNKNOWN,
            available=available,
            connected=CapabilityState.UNKNOWN,
            healthy=CapabilityState.UNKNOWN,
            authorized=authorized,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.HIGH,
                ),
            ),
            confidence=EvidenceConfidence.HIGH,
            native_binding="auth status",
        )

    def _plugins_snapshot(
        self,
        *,
        plugin_result: CliCommandResult,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        if not plugin_result.ok or plugin_result.timed_out:
            return self._unverified_channel(
                capability_id="runtime.plugins",
                observation=(
                    f"claude plugin list rc={plugin_result.returncode} "
                    f"timed_out={plugin_result.timed_out} "
                    f"stderr={plugin_result.stderr.strip()}"
                ),
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="plugin list",
            )
        parsed = parse_plugin_list(plugin_result.stdout)
        if parsed.outcome is ParseOutcome.INVALID:
            return self._unverified_channel(
                capability_id="runtime.plugins",
                observation=(
                    "claude plugin list stdout is not the observed empty-inventory "
                    "message; non-empty plugin format is unobserved"
                ),
                runtime_version=runtime_version,
                observed_at=observed_at,
                native_binding="plugin list",
            )
        observation = "claude plugin list: no plugins installed (observed empty message)"
        return CapabilitySnapshot(
            capability_id=CapabilityId("runtime.plugins"),
            supported=CapabilityState.YES,
            configured=CapabilityState.NO,
            enabled=CapabilityState.NO,
            available=CapabilityState.NO,
            connected=CapabilityState.NO,
            healthy=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.HIGH,
                ),
            ),
            confidence=EvidenceConfidence.HIGH,
            native_binding="plugin list",
        )

    def _mcp_discovery_snapshots(
        self,
        *,
        mcp_result: CliCommandResult,
        runtime_version: str,
        observed_at: datetime,
    ) -> tuple[CapabilitySnapshot, ...]:
        if not mcp_result.ok or mcp_result.timed_out:
            return (
                self._unverified_channel(
                    capability_id="runtime.mcp",
                    observation=(
                        f"claude mcp list rc={mcp_result.returncode} "
                        f"timed_out={mcp_result.timed_out} "
                        f"stderr={mcp_result.stderr.strip()}"
                    ),
                    runtime_version=runtime_version,
                    observed_at=observed_at,
                    native_binding="mcp list",
                ),
            )
        parsed = parse_mcp_list(mcp_result.stdout)
        if parsed.outcome is ParseOutcome.INVALID:
            return (
                self._unverified_channel(
                    capability_id="runtime.mcp",
                    observation=(
                        "claude mcp list stdout unparseable for known status rows "
                        "(includes health-header-only / WebSocket-invisible cases)"
                    ),
                    runtime_version=runtime_version,
                    observed_at=observed_at,
                    native_binding="mcp list",
                ),
            )
        return tuple(
            self._mcp_snapshot(
                entry=entry,
                runtime_version=runtime_version,
                observed_at=observed_at,
            )
            for entry in parsed.entries
        )

    def _mcp_snapshot(
        self,
        *,
        entry: McpServerObservation,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        """Map explicit Claude MCP status text without over-claiming health/auth."""
        axes = mcp_axes_for_status(entry.status)
        observation = redact_sensitive(
            f"mcp {entry.name} transport={entry.transport} status={entry.raw_status or entry.status}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId(f"mcp.{entry.name}"),
            supported=CapabilityState.YES,
            configured=axes.configured,
            enabled=axes.enabled,
            available=axes.available,
            connected=axes.connected,
            healthy=axes.healthy,
            authorized=axes.authorized,
            allowed=axes.allowed,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.HIGH,
                ),
            ),
            confidence=EvidenceConfidence.HIGH,
            native_binding=entry.name,
        )

    def _unverified_channel(
        self,
        *,
        capability_id: str,
        observation: str,
        runtime_version: str,
        observed_at: datetime,
        native_binding: str,
    ) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            capability_id=CapabilityId(capability_id),
            supported=CapabilityState.UNKNOWN,
            configured=CapabilityState.UNKNOWN,
            enabled=CapabilityState.UNKNOWN,
            available=CapabilityState.UNKNOWN,
            connected=CapabilityState.UNKNOWN,
            healthy=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=redact_sensitive(observation),
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.LOW,
                ),
            ),
            confidence=EvidenceConfidence.LOW,
            native_binding=native_binding,
        )

    @staticmethod
    def _evidence(
        *,
        observation: str,
        observed_at: datetime,
        runtime_version: str | None,
        confidence: EvidenceConfidence,
    ) -> CapabilityEvidence:
        return CapabilityEvidence(
            source=EvidenceSource.CLI,
            observation=observation,
            observed_at=observed_at,
            confidence=confidence,
            freshness=EvidenceFreshness.FRESH,
            runtime_version=runtime_version,
        )
