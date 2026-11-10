"""Read-only inventory construction for the OpenCode Server adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.adapters.opencode_server.constants import ADAPTER_VERSION
from agent_loop.adapters.opencode_server.constants import CAPABILITY_AGENTS
from agent_loop.adapters.opencode_server.constants import CAPABILITY_MODELS
from agent_loop.adapters.opencode_server.constants import CAPABILITY_RUNTIME
from agent_loop.adapters.opencode_server.constants import RUNTIME_ID
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.version import VersionCompatibility
from agent_loop.adapters.opencode_server.version import VersionPolicy
from agent_loop.adapters.opencode_server.version import classify_server_version
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory

if TYPE_CHECKING:
    from datetime import datetime

    from agent_loop.adapters.opencode_server.client import ServerClient

_UNKNOWN_VERSION = "unknown"


def not_configured_inventory(*, observed_at: datetime, project_context: str | None) -> RuntimeInventory:
    """Return the explicit inventory for a server that is not configured."""
    snapshot = CapabilitySnapshot(
        capability_id=CapabilityId(CAPABILITY_RUNTIME),
        supported=CapabilityState.UNKNOWN,
        configured=CapabilityState.NO,
        enabled=CapabilityState.UNKNOWN,
        available=CapabilityState.NO,
        connected=CapabilityState.NO,
        healthy=CapabilityState.UNKNOWN,
        authorized=CapabilityState.UNKNOWN,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            _evidence(
                observation="OpenCode Server is not configured",
                observed_at=observed_at,
                runtime_version=None,
                confidence=EvidenceConfidence.HIGH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
        native_binding="opencode serve",
    )
    return RuntimeInventory(
        runtime_identity=RUNTIME_ID,
        runtime_version=_UNKNOWN_VERSION,
        adapter_version=ADAPTER_VERSION,
        discovered_at=observed_at,
        capabilities=(snapshot, *_filesystem_snapshots(snapshot, observed_at=observed_at, version=None)),
        models=(),
        project_context=project_context,
    )


def managed_not_started_inventory(*, observed_at: datetime, project_context: str | None) -> RuntimeInventory:
    """Return the inventory for a managed server that discovery did not start."""
    snapshot = CapabilitySnapshot(
        capability_id=CapabilityId(CAPABILITY_RUNTIME),
        supported=CapabilityState.UNKNOWN,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=CapabilityState.NO,
        connected=CapabilityState.NO,
        healthy=CapabilityState.UNKNOWN,
        authorized=CapabilityState.UNKNOWN,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            _evidence(
                observation="managed OpenCode Server is not running; discovery does not start processes",
                observed_at=observed_at,
                runtime_version=None,
                confidence=EvidenceConfidence.HIGH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
        native_binding="opencode serve",
    )
    return RuntimeInventory(
        runtime_identity=RUNTIME_ID,
        runtime_version=_UNKNOWN_VERSION,
        adapter_version=ADAPTER_VERSION,
        discovered_at=observed_at,
        capabilities=(snapshot, *_filesystem_snapshots(snapshot, observed_at=observed_at, version=None)),
        models=(),
        project_context=project_context,
    )


def build_inventory(
    client: ServerClient,
    *,
    observed_at: datetime,
    project_context: str | None,
    credentials_configured: bool,
    policy: VersionPolicy,
) -> RuntimeInventory:
    """Probe documented read-only endpoints and build a fail-closed inventory."""
    version: str | None = None
    try:
        info = client.info()
    except OpenCodeServerError as exc:
        return _connectivity_failure_inventory(
            exc,
            observed_at=observed_at,
            project_context=project_context,
            credentials_configured=credentials_configured,
        )
    version = info.version
    compatibility = classify_server_version(version, policy)
    runtime_snapshot = _runtime_snapshot(
        version=version,
        compatibility=compatibility,
        observed_at=observed_at,
        credentials_configured=credentials_configured,
    )
    models, models_snapshot = _models_inventory(client, observed_at=observed_at, version=version)
    agents_snapshot = _agents_inventory(client, observed_at=observed_at, version=version)
    return RuntimeInventory(
        runtime_identity=RUNTIME_ID,
        runtime_version=version or _UNKNOWN_VERSION,
        adapter_version=ADAPTER_VERSION,
        discovered_at=observed_at,
        capabilities=(
            runtime_snapshot,
            models_snapshot,
            agents_snapshot,
            *_filesystem_snapshots(runtime_snapshot, observed_at=observed_at, version=version),
        ),
        models=models,
        project_context=project_context,
    )


def _connectivity_failure_inventory(
    error: OpenCodeServerError,
    *,
    observed_at: datetime,
    project_context: str | None,
    credentials_configured: bool,
) -> RuntimeInventory:
    connected = CapabilityState.NO
    authorized = CapabilityState.UNKNOWN
    if error.code is ServerFailureCode.AUTHENTICATION_FAILED or error.code is ServerFailureCode.AUTHORIZATION_FAILED:
        connected = CapabilityState.YES
        authorized = CapabilityState.NO
    snapshot = CapabilitySnapshot(
        capability_id=CapabilityId(CAPABILITY_RUNTIME),
        supported=CapabilityState.UNKNOWN,
        configured=CapabilityState.YES,
        enabled=CapabilityState.UNKNOWN,
        available=CapabilityState.NO,
        connected=connected,
        healthy=CapabilityState.NO if connected is CapabilityState.YES else CapabilityState.UNKNOWN,
        authorized=authorized,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            _evidence(
                observation=f"GET /api/info failed code={error.code.value} credentials={credentials_configured}",
                observed_at=observed_at,
                runtime_version=None,
                confidence=EvidenceConfidence.HIGH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
        native_binding="opencode serve",
    )
    return RuntimeInventory(
        runtime_identity=RUNTIME_ID,
        runtime_version=_UNKNOWN_VERSION,
        adapter_version=ADAPTER_VERSION,
        discovered_at=observed_at,
        capabilities=(snapshot, *_filesystem_snapshots(snapshot, observed_at=observed_at, version=None)),
        models=(),
        project_context=project_context,
    )


def _filesystem_snapshots(
    runtime_snapshot: CapabilitySnapshot,
    *,
    observed_at: datetime,
    version: str | None,
) -> tuple[CapabilitySnapshot, ...]:
    """Declare workspace file capabilities following the observed runtime state.

    A session is always scoped to a workspace and the adapter maps file tools
    through restrictive permission rules. These snapshots never claim an axis
    that the runtime snapshot did not observe as ``YES``.
    """
    return tuple(
        _filesystem_snapshot(capability_id, runtime_snapshot, observed_at=observed_at, version=version)
        for capability_id in ("filesystem.read", "filesystem.write")
    )


def _filesystem_snapshot(
    capability_id: str,
    runtime_snapshot: CapabilitySnapshot,
    *,
    observed_at: datetime,
    version: str | None,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        capability_id=CapabilityId(capability_id),
        supported=runtime_snapshot.supported,
        configured=runtime_snapshot.configured,
        enabled=runtime_snapshot.enabled,
        available=runtime_snapshot.available,
        connected=runtime_snapshot.connected,
        healthy=runtime_snapshot.healthy,
        authorized=runtime_snapshot.authorized,
        allowed=runtime_snapshot.allowed,
        evidence=(
            _evidence(
                observation=(
                    f"workspace-scoped file tools follow runtime state {runtime_snapshot.capability_id.value}"
                ),
                observed_at=observed_at,
                runtime_version=version,
                confidence=runtime_snapshot.confidence,
            ),
        ),
        confidence=runtime_snapshot.confidence,
        native_binding="session workspace",
    )


def _runtime_snapshot(
    *,
    version: str | None,
    compatibility: VersionCompatibility,
    observed_at: datetime,
    credentials_configured: bool,
) -> CapabilitySnapshot:
    supported = _compatibility_state(compatibility)
    available = CapabilityState.YES if compatibility is VersionCompatibility.SUPPORTED else supported
    if compatibility is VersionCompatibility.UNKNOWN:
        available = CapabilityState.UNKNOWN
    # A 200 on /api/info after authentication demonstrates acceptance of the
    # configured credentials; anonymous success does not claim authorization.
    authorized = CapabilityState.YES if credentials_configured else CapabilityState.UNKNOWN
    observation = (
        f"GET /api/info version={version or _UNKNOWN_VERSION} "
        f"compatibility={compatibility.value} credentials={credentials_configured}"
    )
    return CapabilitySnapshot(
        capability_id=CapabilityId(CAPABILITY_RUNTIME),
        supported=supported,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=available,
        connected=CapabilityState.YES,
        healthy=CapabilityState.YES,
        authorized=authorized,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            _evidence(
                observation=observation,
                observed_at=observed_at,
                runtime_version=version,
                confidence=EvidenceConfidence.HIGH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
        native_binding="opencode serve",
    )


def _compatibility_state(compatibility: VersionCompatibility) -> CapabilityState:
    if compatibility is VersionCompatibility.SUPPORTED:
        return CapabilityState.YES
    if compatibility is VersionCompatibility.INCOMPATIBLE:
        return CapabilityState.NO
    return CapabilityState.UNKNOWN


def _models_inventory(
    client: ServerClient,
    *,
    observed_at: datetime,
    version: str | None,
) -> tuple[tuple[InventoryModelRef, ...], CapabilitySnapshot]:
    try:
        models = client.models()
    except OpenCodeServerError:
        return (), _channel_snapshot(
            CAPABILITY_MODELS,
            observation="GET /api/model failed or returned an invalid inventory",
            observed_at=observed_at,
            version=version,
        )
    return (
        tuple(InventoryModelRef(model_id=model.as_text()) for model in models),
        _available_snapshot(
            CAPABILITY_MODELS,
            observation=f"GET /api/model returned {len(models)} entries",
            observed_at=observed_at,
            version=version,
        ),
    )


def _agents_inventory(
    client: ServerClient,
    *,
    observed_at: datetime,
    version: str | None,
) -> CapabilitySnapshot:
    try:
        agents = client.agents()
    except OpenCodeServerError:
        return _channel_snapshot(
            CAPABILITY_AGENTS,
            observation="GET /api/agent failed or returned an invalid inventory",
            observed_at=observed_at,
            version=version,
        )
    return _available_snapshot(
        CAPABILITY_AGENTS,
        observation=f"GET /api/agent returned {len(agents)} entries",
        observed_at=observed_at,
        version=version,
    )


def _available_snapshot(
    capability_id: str,
    *,
    observation: str,
    observed_at: datetime,
    version: str | None,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        capability_id=CapabilityId(capability_id),
        supported=CapabilityState.YES,
        configured=CapabilityState.UNKNOWN,
        enabled=CapabilityState.UNKNOWN,
        available=CapabilityState.YES,
        connected=CapabilityState.UNKNOWN,
        healthy=CapabilityState.UNKNOWN,
        authorized=CapabilityState.UNKNOWN,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            _evidence(
                observation=observation,
                observed_at=observed_at,
                runtime_version=version,
                confidence=EvidenceConfidence.HIGH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
        native_binding=capability_id,
    )


def _channel_snapshot(
    capability_id: str,
    *,
    observation: str,
    observed_at: datetime,
    version: str | None,
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
            _evidence(
                observation=observation,
                observed_at=observed_at,
                runtime_version=version,
                confidence=EvidenceConfidence.LOW,
            ),
        ),
        confidence=EvidenceConfidence.LOW,
        native_binding=capability_id,
    )


def _evidence(
    *,
    observation: str,
    observed_at: datetime,
    runtime_version: str | None,
    confidence: EvidenceConfidence,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        source=EvidenceSource.SERVER_API,
        observation=redact_secret(observation),
        observed_at=observed_at,
        confidence=confidence,
        freshness=EvidenceFreshness.FRESH,
        runtime_version=runtime_version,
    )
