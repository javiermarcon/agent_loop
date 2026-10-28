"""OpenCode AgentRuntime adapter: CLI-only read-only discovery (S9)."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode.cli import SubprocessCliRunner
from agent_loop.adapters.opencode.parse import ParseOutcome
from agent_loop.adapters.opencode.parse import parse_agents_json
from agent_loop.adapters.opencode.parse import parse_mcp_list
from agent_loop.adapters.opencode.parse import parse_models
from agent_loop.adapters.opencode.parse import parse_version
from agent_loop.adapters.opencode.parse import redact_sensitive
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from agent_loop.adapters.opencode.cli import CliCommandResult
    from agent_loop.adapters.opencode.cli import CliRunner
    from agent_loop.adapters.opencode.parse import AgentObservation
    from agent_loop.adapters.opencode.parse import McpServerObservation
    from agent_loop.adapters.opencode.parse import ModelsParseResult
    from agent_loop.ports.agent_runtime import AgentExecutionRequest
    from agent_loop.ports.agent_runtime import DiscoveryContext

RUNTIME_ID = "opencode"
ADAPTER_VERSION = "0.1.0"
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 45.0
_UNKNOWN_VERSION = "unknown"


class OpenCodeRuntime:
    """CLI discovery adapter for OpenCode (execute deferred beyond S9)."""

    def __init__(
        self,
        *,
        cli_runner: CliRunner | None = None,
        binary: str = "opencode",
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
        """Probe OpenCode via read-only CLI and build a RuntimeInventory."""
        observed_at = self._now or datetime.now(tz=UTC)
        version_result = self._run(("--version",), cwd=context.working_directory)
        if version_result.missing_binary:
            return self._missing_binary_inventory(
                context=context,
                observed_at=observed_at,
                version_result=version_result,
            )

        runtime_version = parse_version(version_result.stdout) or _UNKNOWN_VERSION
        mcp_result = self._run(("mcp", "list"), cwd=context.working_directory)
        models_result = self._run(
            ("models", "--standalone"),
            cwd=context.working_directory,
        )
        agents_result = self._run(("debug", "agents"), cwd=context.working_directory)

        models_parsed = (
            parse_models(models_result.stdout) if models_result.ok and not models_result.timed_out else None
        )
        capabilities: list[CapabilitySnapshot] = [
            self._runtime_snapshot(
                runtime_version=runtime_version,
                observed_at=observed_at,
                version_result=version_result,
            ),
            self._models_axis_snapshot(
                models_result=models_result,
                models_parsed=models_parsed,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
        ]
        capabilities.extend(
            self._mcp_discovery_snapshots(
                mcp_result=mcp_result,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
        )
        capabilities.extend(
            self._agents_discovery_snapshots(
                agents_result=agents_result,
                runtime_version=runtime_version,
                observed_at=observed_at,
            ),
        )

        models: tuple[InventoryModelRef, ...] = ()
        if models_parsed is not None and models_parsed.outcome is ParseOutcome.VALID:
            models = tuple(InventoryModelRef(model_id=model_id) for model_id in models_parsed.models)

        return RuntimeInventory(
            runtime_identity=RUNTIME_ID,
            runtime_version=runtime_version,
            adapter_version=ADAPTER_VERSION,
            discovered_at=observed_at,
            capabilities=tuple(capabilities),
            models=models,
            project_context=context.project_context,
        )

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        """Refuse execution in the discovery-only S9 adapter."""
        del request
        return AgentExecutionResult(
            status=AgentExecutionStatus.FAILED,
            runtime_identity=self._identity,
            observed_failures=("OpenCodeRuntime S9 is discovery-only; execute is not implemented",),
            harness_metadata=(("adapter", "opencode"), ("slice", "s9-discovery")),
        )

    def _run(self, args: tuple[str, ...], *, cwd: str) -> _ProbeResult:
        result = self._cli.run(
            (self._binary, *args),
            cwd=cwd,
            timeout_seconds=self._timeout_seconds,
        )
        return _ProbeResult(raw=result)

    def _missing_binary_inventory(
        self,
        *,
        context: DiscoveryContext,
        observed_at: datetime,
        version_result: _ProbeResult,
    ) -> RuntimeInventory:
        evidence = self._evidence(
            observation=f"binary missing: {redact_sensitive(version_result.stderr)}",
            observed_at=observed_at,
            runtime_version=None,
            confidence=EvidenceConfidence.HIGH,
        )
        runtime_cap = CapabilitySnapshot(
            capability_id=CapabilityId("runtime.opencode"),
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
        version_result: _ProbeResult,
    ) -> CapabilitySnapshot:
        # ``--version`` only proves the binary responds with a known OpenCode
        # identity. It does not demonstrate configuration, enablement,
        # connectivity, or execution health.
        version_ok = version_result.ok and runtime_version != _UNKNOWN_VERSION
        available = CapabilityState.YES if version_ok else CapabilityState.UNKNOWN
        supported = CapabilityState.YES if version_ok else CapabilityState.UNKNOWN
        observation = redact_sensitive(
            f"opencode --version rc={version_result.returncode} "
            f"version={runtime_version} stderr={version_result.stderr.strip()}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId("runtime.opencode"),
            supported=supported,
            configured=CapabilityState.UNKNOWN,
            enabled=CapabilityState.UNKNOWN,
            available=available,
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

    def _models_axis_snapshot(
        self,
        *,
        models_result: _ProbeResult,
        models_parsed: ModelsParseResult | None,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        parse_ok = models_parsed is not None and models_parsed.outcome is ParseOutcome.VALID
        if models_result.timed_out or not models_result.ok or not parse_ok:
            available = CapabilityState.UNKNOWN
            confidence = EvidenceConfidence.LOW
        else:
            available = CapabilityState.YES
            confidence = EvidenceConfidence.HIGH
        observation = redact_sensitive(
            f"opencode models --standalone rc={models_result.returncode} "
            f"timed_out={models_result.timed_out} "
            f"stderr={models_result.stderr.strip()}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId("runtime.models"),
            supported=CapabilityState.YES,
            configured=CapabilityState.UNKNOWN,
            enabled=CapabilityState.UNKNOWN,
            available=available,
            connected=CapabilityState.UNKNOWN,
            healthy=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=confidence,
                ),
            ),
            confidence=confidence,
            native_binding="models",
        )

    def _mcp_discovery_snapshots(
        self,
        *,
        mcp_result: _ProbeResult,
        runtime_version: str,
        observed_at: datetime,
    ) -> tuple[CapabilitySnapshot, ...]:
        if not mcp_result.ok or mcp_result.timed_out:
            return (
                self._channel_unknown_snapshot(
                    capability_id="runtime.mcp",
                    observation=(
                        f"opencode mcp list rc={mcp_result.returncode} "
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
                self._channel_unknown_snapshot(
                    capability_id="runtime.mcp",
                    observation="opencode mcp list stdout unparseable for known status rows",
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

    def _agents_discovery_snapshots(
        self,
        *,
        agents_result: _ProbeResult,
        runtime_version: str,
        observed_at: datetime,
    ) -> tuple[CapabilitySnapshot, ...]:
        if not agents_result.ok or agents_result.timed_out:
            return (
                self._channel_unknown_snapshot(
                    capability_id="runtime.agents",
                    observation=(
                        f"opencode debug agents rc={agents_result.returncode} "
                        f"timed_out={agents_result.timed_out} "
                        f"stderr={agents_result.stderr.strip()}"
                    ),
                    runtime_version=runtime_version,
                    observed_at=observed_at,
                    native_binding="debug agents",
                ),
            )
        parsed = parse_agents_json(agents_result.stdout)
        if parsed.outcome is ParseOutcome.INVALID:
            return (
                self._channel_unknown_snapshot(
                    capability_id="runtime.agents",
                    observation="opencode debug agents stdout is not a usable JSON array",
                    runtime_version=runtime_version,
                    observed_at=observed_at,
                    native_binding="debug agents",
                ),
            )
        return tuple(
            self._agent_snapshot(
                agent=agent,
                runtime_version=runtime_version,
                observed_at=observed_at,
            )
            for agent in parsed.agents
        )

    def _channel_unknown_snapshot(
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

    def _mcp_snapshot(
        self,
        *,
        entry: McpServerObservation,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        """Map explicit CLI MCP status text without over-claiming health/auth."""
        connected = CapabilityState.NO
        healthy = CapabilityState.UNKNOWN
        enabled = CapabilityState.YES
        available = CapabilityState.NO
        authorized = CapabilityState.UNKNOWN
        if entry.status == "connected":
            # CLI reports connection only; tool health and auth validity stay unknown.
            connected = CapabilityState.YES
            available = CapabilityState.YES
            healthy = CapabilityState.UNKNOWN
            authorized = CapabilityState.UNKNOWN
        elif entry.status == "disabled":
            enabled = CapabilityState.NO
            available = CapabilityState.NO
            connected = CapabilityState.NO
            healthy = CapabilityState.UNKNOWN
        elif entry.status == "needs_auth":
            authorized = CapabilityState.NO
            available = CapabilityState.NO
            connected = CapabilityState.NO
            healthy = CapabilityState.UNKNOWN
        elif entry.status == "failed":
            connected = CapabilityState.NO
            available = CapabilityState.NO
            healthy = CapabilityState.NO
        elif entry.status == "needs_client_registration":
            authorized = CapabilityState.NO
            available = CapabilityState.NO
            connected = CapabilityState.NO
            healthy = CapabilityState.UNKNOWN
        elif entry.status == "not_initialized":
            available = CapabilityState.UNKNOWN
            connected = CapabilityState.UNKNOWN
            healthy = CapabilityState.UNKNOWN
        oauth_note = " oauth_hint=true" if entry.oauth_hint else ""
        observation = redact_sensitive(
            f"mcp {entry.name} status={entry.raw_status or entry.status}{oauth_note}",
        )
        return CapabilitySnapshot(
            capability_id=CapabilityId(f"mcp.{entry.name}"),
            supported=CapabilityState.YES,
            configured=CapabilityState.YES,
            enabled=enabled,
            available=available,
            connected=connected,
            healthy=healthy,
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
            native_binding=entry.name,
        )

    def _agent_snapshot(
        self,
        *,
        agent: AgentObservation,
        runtime_version: str,
        observed_at: datetime,
    ) -> CapabilitySnapshot:
        observation = redact_sensitive(f"debug agents id={agent.agent_id} name={agent.name}")
        return CapabilitySnapshot(
            capability_id=CapabilityId(f"agent.{agent.agent_id}"),
            supported=CapabilityState.YES,
            configured=CapabilityState.YES,
            enabled=CapabilityState.UNKNOWN,
            available=CapabilityState.UNKNOWN,
            connected=CapabilityState.UNKNOWN,
            healthy=CapabilityState.UNKNOWN,
            authorized=CapabilityState.UNKNOWN,
            allowed=CapabilityState.UNKNOWN,
            evidence=(
                self._evidence(
                    observation=observation,
                    observed_at=observed_at,
                    runtime_version=runtime_version,
                    confidence=EvidenceConfidence.MEDIUM,
                ),
            ),
            confidence=EvidenceConfidence.MEDIUM,
            native_binding=agent.agent_id,
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


class _ProbeResult:
    """Thin view over CliCommandResult with convenience predicates."""

    __slots__ = ("raw",)

    def __init__(self, raw: CliCommandResult) -> None:
        self.raw = raw

    @property
    def ok(self) -> bool:
        return not self.raw.missing_binary and not self.raw.timed_out and self.raw.returncode == 0

    @property
    def timed_out(self) -> bool:
        return self.raw.timed_out

    @property
    def missing_binary(self) -> bool:
        return self.raw.missing_binary

    @property
    def returncode(self) -> int:
        return self.raw.returncode

    @property
    def stdout(self) -> str:
        return self.raw.stdout

    @property
    def stderr(self) -> str:
        return self.raw.stderr
