"""Contract tests for effective capability vocabulary."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.capabilities import is_effective

_ALL_AXES = frozenset(CapabilityAxis)
_DEFAULT_CAPABILITY = "work_item.notion.read"


def _observed_at() -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=1)


def _evidence(
    *,
    source: EvidenceSource = EvidenceSource.CLI,
    confidence: EvidenceConfidence = EvidenceConfidence.HIGH,
    freshness: EvidenceFreshness = EvidenceFreshness.FRESH,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        source=source,
        observation="fixture observation",
        observed_at=_observed_at(),
        confidence=confidence,
        freshness=freshness,
        runtime_version="1.0.0",
    )


def _axis_states(
    overrides: dict[CapabilityAxis, CapabilityState] | None = None,
) -> dict[CapabilityAxis, CapabilityState]:
    states = dict.fromkeys(CapabilityAxis, CapabilityState.YES)
    if overrides:
        states.update(overrides)
    return states


def _snapshot(
    *,
    capability_id: CapabilityId | None = None,
    axis_overrides: dict[CapabilityAxis, CapabilityState] | None = None,
    confidence: EvidenceConfidence = EvidenceConfidence.HIGH,
) -> CapabilitySnapshot:
    states = _axis_states(axis_overrides)
    resolved_id = capability_id or CapabilityId(_DEFAULT_CAPABILITY)
    return CapabilitySnapshot(
        capability_id=resolved_id,
        supported=states[CapabilityAxis.SUPPORTED],
        configured=states[CapabilityAxis.CONFIGURED],
        enabled=states[CapabilityAxis.ENABLED],
        available=states[CapabilityAxis.AVAILABLE],
        connected=states[CapabilityAxis.CONNECTED],
        healthy=states[CapabilityAxis.HEALTHY],
        authorized=states[CapabilityAxis.AUTHORIZED],
        allowed=states[CapabilityAxis.ALLOWED],
        evidence=(_evidence(),),
        confidence=confidence,
        native_binding="adapter-opaque-binding",
    )


def _requirement(
    *,
    capability_id: CapabilityId | None = None,
    mandatory_axes: frozenset[CapabilityAxis] | None = None,
) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=capability_id or CapabilityId(_DEFAULT_CAPABILITY),
        mandatory_axes=mandatory_axes or _ALL_AXES,
    )


def test_capability_state_is_tri_state() -> None:
    """CapabilityState exposes only YES, NO, and UNKNOWN."""
    assert set(CapabilityState) == {
        CapabilityState.YES,
        CapabilityState.NO,
        CapabilityState.UNKNOWN,
    }


def test_capability_id_rejects_blank_values() -> None:
    """CapabilityId fails closed on empty or whitespace-only values."""
    with pytest.raises(ValueError, match="CapabilityId"):
        CapabilityId("")
    with pytest.raises(ValueError, match="CapabilityId"):
        CapabilityId("   ")


def test_requirement_rejects_empty_mandatory_axes() -> None:
    """A requirement without mandatory axes is invalid."""
    with pytest.raises(ValueError, match="mandatory_axes"):
        CapabilityRequirement(
            capability_id=CapabilityId("filesystem.read"),
            mandatory_axes=frozenset(),
        )


def test_is_effective_when_all_mandatory_axes_are_yes() -> None:
    """Effective is true only when every mandatory axis is YES."""
    snapshot = _snapshot()
    requirement = _requirement()
    assert is_effective(snapshot, requirement) is True


def test_is_effective_false_when_mandatory_axis_is_no() -> None:
    """A mandatory NO axis makes the capability not effective."""
    snapshot = _snapshot(
        axis_overrides={CapabilityAxis.AUTHORIZED: CapabilityState.NO},
    )
    assert is_effective(snapshot, _requirement()) is False


def test_is_effective_false_when_mandatory_axis_is_unknown() -> None:
    """UNKNOWN on a mandatory axis must not be promoted to YES."""
    snapshot = _snapshot(
        axis_overrides={CapabilityAxis.CONNECTED: CapabilityState.UNKNOWN},
    )
    assert is_effective(snapshot, _requirement()) is False


def test_is_effective_ignores_non_mandatory_unknown_axes() -> None:
    """Axes outside the mandatory set do not block effectiveness."""
    snapshot = _snapshot(
        axis_overrides={
            CapabilityAxis.CONNECTED: CapabilityState.UNKNOWN,
            CapabilityAxis.HEALTHY: CapabilityState.NO,
        },
    )
    requirement = _requirement(
        mandatory_axes=frozenset(
            {
                CapabilityAxis.SUPPORTED,
                CapabilityAxis.CONFIGURED,
                CapabilityAxis.ALLOWED,
            },
        ),
    )
    assert is_effective(snapshot, requirement) is True


def test_is_effective_false_on_capability_id_mismatch() -> None:
    """A snapshot for a different capability cannot satisfy a requirement."""
    snapshot = _snapshot(capability_id=CapabilityId("filesystem.read"))
    requirement = _requirement(
        capability_id=CapabilityId("work_item.notion.write"),
    )
    assert is_effective(snapshot, requirement) is False


def test_evidence_preserves_source_and_confidence() -> None:
    """Evidence keeps source, confidence, and freshness as declared."""
    evidence = _evidence(
        source=EvidenceSource.PROBE,
        confidence=EvidenceConfidence.LOW,
        freshness=EvidenceFreshness.STALE,
    )
    assert evidence.source is EvidenceSource.PROBE
    assert evidence.confidence is EvidenceConfidence.LOW
    assert evidence.freshness is EvidenceFreshness.STALE
    assert evidence.runtime_version == "1.0.0"


def test_runtime_inventory_is_immutable() -> None:
    """RuntimeInventory freezes capability and model collections."""
    inventory = RuntimeInventory(
        runtime_identity="runtime-fixture",
        runtime_version="0.1.0",
        adapter_version="0.1.0",
        discovered_at=_observed_at(),
        capabilities=(_snapshot(),),
        models=(InventoryModelRef(model_id="model-fixture"),),
        project_context="project-fixture",
    )
    assert inventory.capabilities[0].capability_id.value == (_DEFAULT_CAPABILITY)
    assert inventory.models[0].model_id == "model-fixture"
    with pytest.raises(AttributeError):
        inventory.runtime_identity = "mutated"  # type: ignore[misc]


def test_snapshot_axis_state_covers_all_axes() -> None:
    """axis_state returns the declared tri-state for every CapabilityAxis."""
    snapshot = _snapshot(
        axis_overrides={
            CapabilityAxis.SUPPORTED: CapabilityState.YES,
            CapabilityAxis.CONFIGURED: CapabilityState.NO,
            CapabilityAxis.ENABLED: CapabilityState.UNKNOWN,
            CapabilityAxis.AVAILABLE: CapabilityState.YES,
            CapabilityAxis.CONNECTED: CapabilityState.NO,
            CapabilityAxis.HEALTHY: CapabilityState.UNKNOWN,
            CapabilityAxis.AUTHORIZED: CapabilityState.YES,
            CapabilityAxis.ALLOWED: CapabilityState.NO,
        },
    )
    assert snapshot.axis_state(CapabilityAxis.SUPPORTED) is CapabilityState.YES
    assert snapshot.axis_state(CapabilityAxis.CONFIGURED) is CapabilityState.NO
    assert snapshot.axis_state(CapabilityAxis.ENABLED) is CapabilityState.UNKNOWN
    assert snapshot.axis_state(CapabilityAxis.AVAILABLE) is CapabilityState.YES
    assert snapshot.axis_state(CapabilityAxis.CONNECTED) is CapabilityState.NO
    assert snapshot.axis_state(CapabilityAxis.HEALTHY) is CapabilityState.UNKNOWN
    assert snapshot.axis_state(CapabilityAxis.AUTHORIZED) is CapabilityState.YES
    assert snapshot.axis_state(CapabilityAxis.ALLOWED) is CapabilityState.NO
