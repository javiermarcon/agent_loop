"""Durable execution-gate and discovery-transition contracts for plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ExecutionGateState(StrEnum):
    """Lifecycle states for mutation units pending repository discovery."""

    BLOCKED_PENDING_DISCOVERY = "blocked_pending_discovery"
    DISCOVERY_RUNNING = "discovery_running"
    DISCOVERY_RESULT_AVAILABLE = "discovery_result_available"
    READY = "ready"
    BLOCKED_DISCOVERY_FAILED = "blocked_discovery_failed"
    BLOCKED_DISCOVERY_INCOMPLETE = "blocked_discovery_incomplete"


class DiscoveryTransitionState(StrEnum):
    """Durable transition states between discovery and bounded mutation."""

    AWAITING_DISCOVERY = "awaiting_discovery"
    RESOLVED = "resolved"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class ExecutionGate:
    """Persist whether a mutation unit may execute without discovery."""

    state: ExecutionGateState
    blocking_codes: tuple[str, ...] = ()
    discovery_unit_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the gate for durable plan JSON."""
        return {
            "state": self.state.value,
            "blocking_codes": list(self.blocking_codes),
            "discovery_unit_id": self.discovery_unit_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ExecutionGate | None:
        """Reconstruct a gate from persisted JSON, or ``None`` when absent."""
        if not isinstance(payload, Mapping) or not payload:
            return None
        state_raw = str(payload.get("state", ""))
        if not state_raw:
            return None
        codes = payload.get("blocking_codes", ())
        blocking = tuple(str(item) for item in codes) if isinstance(codes, list | tuple) else ()
        return cls(
            state=ExecutionGateState(state_raw),
            blocking_codes=blocking,
            discovery_unit_id=str(payload.get("discovery_unit_id", "")),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryTransition:
    """Persist discovery artifact linkage for one mutation unit."""

    state: DiscoveryTransitionState
    discovery_unit_id: str
    result_artifact_path: str
    result_schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize the transition for durable plan JSON."""
        return {
            "state": self.state.value,
            "discovery_unit_id": self.discovery_unit_id,
            "result_artifact_path": self.result_artifact_path,
            "result_schema_version": self.result_schema_version,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | None,
    ) -> DiscoveryTransition | None:
        """Reconstruct a transition from persisted JSON, or ``None``."""
        if not isinstance(payload, Mapping) or not payload:
            return None
        state_raw = str(payload.get("state", ""))
        discovery_unit_id = str(payload.get("discovery_unit_id", ""))
        result_path = str(payload.get("result_artifact_path", ""))
        if not state_raw or not discovery_unit_id or not result_path:
            return None
        version_raw = payload.get("result_schema_version", 1)
        version = int(version_raw) if isinstance(version_raw, int | str) else 1
        return cls(
            state=DiscoveryTransitionState(state_raw),
            discovery_unit_id=discovery_unit_id,
            result_artifact_path=result_path,
            result_schema_version=version,
        )


def discovery_unit_id_for(mutation_unit_id: str) -> str:
    """Return the deterministic synthetic discovery step id."""
    return f"discovery:{mutation_unit_id}"


def discovery_result_artifact_path(plan_id: str, mutation_unit_id: str) -> str:
    """Return the deterministic discovery result artifact relative path."""
    safe_unit = mutation_unit_id.replace("/", "__").replace(":", "__")
    return f".agent-loop-logs/discovery/{plan_id}/{safe_unit}.json"
