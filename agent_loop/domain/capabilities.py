"""Pure vocabulary for effective runtime capability assessment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class CapabilityState(StrEnum):
    """Tri-state observation for one capability axis."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class CapabilityAxis(StrEnum):
    """Named axes that compose a capability snapshot."""

    SUPPORTED = "supported"
    CONFIGURED = "configured"
    ENABLED = "enabled"
    AVAILABLE = "available"
    CONNECTED = "connected"
    HEALTHY = "healthy"
    AUTHORIZED = "authorized"
    ALLOWED = "allowed"


class EvidenceSource(StrEnum):
    """Where a capability observation was obtained."""

    CLI = "cli"
    SERVER_API = "server_api"
    CONFIG = "config"
    PROBE = "probe"
    PROJECT_OVERRIDE = "project_override"
    RUNTIME_OUTPUT = "runtime_output"


class EvidenceConfidence(StrEnum):
    """Ordinal confidence for capability evidence."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceFreshness(StrEnum):
    """Freshness class for a capability observation."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapabilityId:
    """Stable semantic identifier for a capability requirement."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            message = "CapabilityId value must be a non-empty string"
            raise ValueError(message)
        if self.value != self.value.strip():
            message = "CapabilityId value must not include leading or trailing whitespace"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """One observation contributing to a capability snapshot."""

    source: EvidenceSource
    observation: str
    observed_at: datetime
    confidence: EvidenceConfidence
    freshness: EvidenceFreshness
    runtime_version: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Immutable multi-axis observation of one capability."""

    capability_id: CapabilityId
    supported: CapabilityState
    configured: CapabilityState
    enabled: CapabilityState
    available: CapabilityState
    connected: CapabilityState
    healthy: CapabilityState
    authorized: CapabilityState
    allowed: CapabilityState
    evidence: tuple[CapabilityEvidence, ...]
    confidence: EvidenceConfidence
    native_binding: str | None = None

    def axis_state(self, axis: CapabilityAxis) -> CapabilityState:
        """Return the tri-state value for ``axis``."""
        mapping = {
            CapabilityAxis.SUPPORTED: self.supported,
            CapabilityAxis.CONFIGURED: self.configured,
            CapabilityAxis.ENABLED: self.enabled,
            CapabilityAxis.AVAILABLE: self.available,
            CapabilityAxis.CONNECTED: self.connected,
            CapabilityAxis.HEALTHY: self.healthy,
            CapabilityAxis.AUTHORIZED: self.authorized,
            CapabilityAxis.ALLOWED: self.allowed,
        }
        return mapping[axis]


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Declare which capability axes must be YES for effectiveness."""

    capability_id: CapabilityId
    mandatory_axes: frozenset[CapabilityAxis]

    def __post_init__(self) -> None:
        if not self.mandatory_axes:
            message = "CapabilityRequirement.mandatory_axes must not be empty"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class InventoryModelRef:
    """Minimal model reference carried by a runtime inventory."""

    model_id: str


@dataclass(frozen=True, slots=True)
class RuntimeInventory:
    """Immutable inventory discovered for one runtime at one moment."""

    runtime_identity: str
    runtime_version: str
    adapter_version: str
    discovered_at: datetime
    capabilities: tuple[CapabilitySnapshot, ...]
    models: tuple[InventoryModelRef, ...]
    project_context: str | None = None


def is_effective(
    snapshot: CapabilitySnapshot,
    requirement: CapabilityRequirement,
) -> bool:
    """Return whether ``snapshot`` satisfies ``requirement`` axes as YES.

    UNKNOWN and NO never count as YES. Capability id mismatch fails closed.
    """
    if snapshot.capability_id != requirement.capability_id:
        return False
    return all(snapshot.axis_state(axis) is CapabilityState.YES for axis in requirement.mandatory_axes)
