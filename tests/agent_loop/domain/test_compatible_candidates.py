"""Contract tests for deterministic compatible-candidate resolution."""

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
from agent_loop.domain.compatible_candidates import CompatibleModelCandidate
from agent_loop.domain.compatible_candidates import resolve_compatible_candidates
from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile
from agent_loop.domain.task_assessment import AutonomyRequired
from agent_loop.domain.task_assessment import ImpactPresence
from agent_loop.domain.task_assessment import TaskAssessment
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty

_READ = "filesystem.read"
_WRITE = "filesystem.write"
_BASE_AXES = frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE})


def _observed_at() -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=1)


def _snapshot(
    capability: str,
    *,
    available: CapabilityState = CapabilityState.YES,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        capability_id=CapabilityId(capability),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=available,
        connected=CapabilityState.UNKNOWN,
        healthy=CapabilityState.UNKNOWN,
        authorized=CapabilityState.UNKNOWN,
        allowed=CapabilityState.UNKNOWN,
        evidence=(
            CapabilityEvidence(
                source=EvidenceSource.CLI,
                observation="fixture",
                observed_at=_observed_at(),
                confidence=EvidenceConfidence.HIGH,
                freshness=EvidenceFreshness.FRESH,
            ),
        ),
        confidence=EvidenceConfidence.HIGH,
    )


def _requirement(capability: str) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=CapabilityId(capability),
        mandatory_axes=_BASE_AXES,
    )


def _assessment(
    *capabilities: str,
) -> TaskAssessment:
    return TaskAssessment(
        complexity=TaskComplexity.LOCAL,
        risk=TaskRisk.LOW,
        uncertainty=TaskUncertainty.LOW,
        impacted_domains=(),
        estimated_files=None,
        architecture_impact=ImpactPresence.NO,
        schema_impact=ImpactPresence.NO,
        security_impact=ImpactPresence.NO,
        authentication_impact=ImpactPresence.NO,
        payments_impact=ImpactPresence.NO,
        concurrency_impact=ImpactPresence.NO,
        external_integrations=(),
        frontend_backend_boundary=ImpactPresence.NO,
        required_capabilities=tuple(_requirement(item) for item in capabilities),
        required_context=(),
        autonomy_required=AutonomyRequired.LOW,
        reasons=(),
    )


def _profile(
    *,
    profile_id: str = "anthropic.claude-sonnet-4",
    provider_id: str = "anthropic",
    model_id: str = "claude-sonnet-4",
) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id=provider_id,
        model_id=model_id,
    )


def _inventory(
    *,
    runtime_id: str = "opencode",
    capabilities: tuple[CapabilitySnapshot, ...] = (),
    models: tuple[str, ...] = ("anthropic/claude-sonnet-4",),
) -> RuntimeInventory:
    return RuntimeInventory(
        runtime_identity=runtime_id,
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=_observed_at(),
        capabilities=capabilities,
        models=tuple(InventoryModelRef(model_id=model_id) for model_id in models),
    )


def _resolve(
    assessment: TaskAssessment,
    *,
    inventories: tuple[RuntimeInventory, ...],
    profiles: tuple[ModelProfile, ...],
    bindings: tuple[RuntimeModelProfile, ...],
):
    profile_catalog = ModelProfileCatalog.from_profiles(profiles)
    binding_catalog = RuntimeModelProfileCatalog.from_bindings(
        bindings,
        profiles=profile_catalog,
    )
    return resolve_compatible_candidates(
        assessment,
        inventories=inventories,
        profiles=profile_catalog,
        bindings=binding_catalog,
    )


def test_accepts_candidate_satisfying_all_required_capabilities() -> None:
    """Inventory-effective capabilities plus catalogued inventory model accept."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    inventory = _inventory(
        capabilities=(_snapshot(_READ), _snapshot(_WRITE)),
    )
    resolution = _resolve(
        _assessment(_READ, _WRITE),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == (
        CompatibleModelCandidate(
            runtime_id="opencode",
            profile=profile,
            native_model_ref="anthropic/claude-sonnet-4",
        ),
    )
    assert any(reason.startswith("accepted=") for reason in resolution.reasons)


def test_rejects_candidate_missing_required_capability() -> None:
    """Missing a mandatory capability axis fails closed for that binding."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    inventory = _inventory(capabilities=(_snapshot(_READ),))
    resolution = _resolve(
        _assessment(_READ, _WRITE),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert any("capabilities_unsatisfied" in reason for reason in resolution.reasons)
    assert any(_WRITE in reason for reason in resolution.reasons)


def test_empty_required_capabilities_still_needs_inventory_model() -> None:
    """Vacuous capability match does not invent inventory membership."""
    profile = _profile()
    with_model = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    resolution = _resolve(
        _assessment(),
        inventories=(_inventory(capabilities=()),),
        profiles=(profile,),
        bindings=(with_model,),
    )
    assert resolution.candidates == (
        CompatibleModelCandidate(
            runtime_id="opencode",
            profile=profile,
            native_model_ref="anthropic/claude-sonnet-4",
        ),
    )


def test_multiple_compatible_candidates_are_deterministically_ordered() -> None:
    """Several matches keep catalog identity order by (runtime_id, profile_id)."""
    alpha = _profile(profile_id="alpha.model", provider_id="a", model_id="a")
    beta = _profile(profile_id="beta.model", provider_id="b", model_id="b")
    bindings = (
        RuntimeModelProfile(
            runtime_id="z-runtime",
            profile=beta,
            native_model_ref="b/b",
        ),
        RuntimeModelProfile(
            runtime_id="a-runtime",
            profile=beta,
            native_model_ref="b/b",
        ),
        RuntimeModelProfile(
            runtime_id="a-runtime",
            profile=alpha,
            native_model_ref="a/a",
        ),
    )
    inventories = (
        _inventory(
            runtime_id="z-runtime",
            capabilities=(_snapshot(_READ),),
            models=("b/b",),
        ),
        _inventory(
            runtime_id="a-runtime",
            capabilities=(_snapshot(_READ),),
            models=("a/a", "b/b"),
        ),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=inventories,
        profiles=(alpha, beta),
        bindings=bindings,
    )
    keys = [item.identity_key() for item in resolution.candidates]
    assert keys == [
        ("a-runtime", "alpha.model"),
        ("a-runtime", "beta.model"),
        ("z-runtime", "beta.model"),
    ]


def test_no_compatible_candidates_has_empty_result_without_fallback() -> None:
    """Empty resolution is valid; no default winner is invented."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(_inventory(capabilities=()),),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert not hasattr(resolution, "winner")
    assert not hasattr(resolution, "selected")


def test_profile_in_catalog_but_absent_from_inventory_is_rejected() -> None:
    """Catalog declaration alone is not inventory evidence."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    inventory = _inventory(
        capabilities=(_snapshot(_READ),),
        models=("other/model",),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert any("native_model_ref_absent_from_inventory" in reason for reason in resolution.reasons)


def test_runtime_id_must_match_inventory_runtime_identity() -> None:
    """Bindings for another runtime do not consume a foreign inventory."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="claude_code",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    inventory = _inventory(
        runtime_id="opencode",
        capabilities=(_snapshot(_READ),),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert any("inventory_missing" in reason for reason in resolution.reasons)


def test_native_model_ref_distinct_from_logical_profile_identity() -> None:
    """Logical profile_id/model_id are not substituted for native inventory ids."""
    profile = _profile(model_id="claude-sonnet-4")
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    # Inventory only lists the logical model_id, not the native harness ref.
    inventory = _inventory(
        capabilities=(_snapshot(_READ),),
        models=("claude-sonnet-4",),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert any("native_model_ref_absent_from_inventory" in reason for reason in resolution.reasons)


def test_missing_native_model_ref_rejects_binding() -> None:
    """Without native_model_ref there is no inventory match rule."""
    profile = _profile()
    binding = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    inventory = _inventory(capabilities=(_snapshot(_READ),))
    resolution = _resolve(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()
    assert any("native_model_ref_missing" in reason for reason in resolution.reasons)


def test_catalog_conflict_on_construction_is_not_silenced() -> None:
    """Conflicting catalog definitions still raise at the catalog boundary."""
    left = _profile(model_id="claude-sonnet-4")
    right = _profile(model_id="claude-sonnet-4-alt")
    with pytest.raises(ValueError, match="conflicting ModelProfile"):
        ModelProfileCatalog.from_profiles((left, right))


def test_equivalent_inputs_with_shuffled_order_yield_identical_resolution() -> None:
    """Incidental input order does not change candidates or sorted reasons."""
    alpha = _profile(profile_id="alpha.model", provider_id="a", model_id="a")
    beta = _profile(profile_id="beta.model", provider_id="b", model_id="b")
    bindings_a = (
        RuntimeModelProfile(runtime_id="r1", profile=beta, native_model_ref="b/b"),
        RuntimeModelProfile(runtime_id="r1", profile=alpha, native_model_ref="a/a"),
    )
    bindings_b = tuple(reversed(bindings_a))
    inventories_a = (_inventory(runtime_id="r1", capabilities=(_snapshot(_READ),), models=("a/a", "b/b")),)
    inventories_b = inventories_a
    first = _resolve(
        _assessment(_READ),
        inventories=inventories_a,
        profiles=(beta, alpha),
        bindings=bindings_a,
    )
    second = _resolve(
        _assessment(_READ),
        inventories=inventories_b,
        profiles=(alpha, beta),
        bindings=bindings_b,
    )
    assert first == second


def test_does_not_infer_capabilities_from_model_or_provider_names() -> None:
    """Names never satisfy TaskAssessment capability requirements."""
    profile = _profile(model_id="filesystem-capable-model")
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/filesystem-capable-model",
    )
    inventory = _inventory(
        capabilities=(),
        models=("anthropic/filesystem-capable-model",),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=(profile,),
        bindings=(binding,),
    )
    assert resolution.candidates == ()


def test_multiple_candidates_are_not_collapsed_to_a_winner() -> None:
    """Resolution returns the full compatible set, not a single selected model."""
    one = _profile(profile_id="one", model_id="one")
    two = _profile(profile_id="two", model_id="two")
    bindings = (
        RuntimeModelProfile(
            runtime_id="opencode",
            profile=one,
            native_model_ref="anthropic/one",
        ),
        RuntimeModelProfile(
            runtime_id="opencode",
            profile=two,
            native_model_ref="anthropic/two",
        ),
    )
    resolution = _resolve(
        _assessment(_READ),
        inventories=(
            _inventory(
                capabilities=(_snapshot(_READ),),
                models=("anthropic/one", "anthropic/two"),
            ),
        ),
        profiles=(one, two),
        bindings=bindings,
    )
    assert resolution.candidates == (
        CompatibleModelCandidate(
            runtime_id="opencode",
            profile=one,
            native_model_ref="anthropic/one",
        ),
        CompatibleModelCandidate(
            runtime_id="opencode",
            profile=two,
            native_model_ref="anthropic/two",
        ),
    )
    assert not hasattr(resolution, "winner")


def test_duplicate_inventory_runtime_identity_is_rejected() -> None:
    """Ambiguous inventories for one runtime_id fail closed."""
    profile = _profile()
    with pytest.raises(ValueError, match="duplicate RuntimeInventory.runtime_identity"):
        resolve_compatible_candidates(
            _assessment(),
            inventories=(
                _inventory(runtime_id="opencode"),
                _inventory(runtime_id="opencode"),
            ),
            profiles=ModelProfileCatalog.from_profiles((profile,)),
            bindings=RuntimeModelProfileCatalog.from_bindings(()),
        )


def test_profile_absent_from_catalog_is_rejected() -> None:
    """Bindings whose profile is missing from the profile catalog are rejected."""
    profile = _profile()
    other = _profile(profile_id="other", model_id="other")
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    # Build binding catalog without cross-check, then resolve with a different profile set.
    binding_catalog = RuntimeModelProfileCatalog.from_bindings((binding,))
    resolution = resolve_compatible_candidates(
        _assessment(_READ),
        inventories=(_inventory(capabilities=(_snapshot(_READ),)),),
        profiles=ModelProfileCatalog.from_profiles((other,)),
        bindings=binding_catalog,
    )
    assert resolution.candidates == ()
    assert any("profile_not_in_catalog" in reason for reason in resolution.reasons)


def test_profile_conflicts_with_catalog_is_rejected() -> None:
    """Same profile_id with divergent fields rejects via structural conflict."""
    binding_profile = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    catalog_profile = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4-alt",
    )
    binding = RuntimeModelProfile(
        runtime_id="opencode",
        profile=binding_profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    binding_catalog = RuntimeModelProfileCatalog.from_bindings((binding,))
    inventory = _inventory(
        capabilities=(_snapshot(_READ),),
        models=("anthropic/claude-sonnet-4",),
    )
    resolution = resolve_compatible_candidates(
        _assessment(_READ),
        inventories=(inventory,),
        profiles=ModelProfileCatalog.from_profiles((catalog_profile,)),
        bindings=binding_catalog,
    )
    assert resolution.candidates == ()
    assert any("profile_conflicts_with_catalog" in reason for reason in resolution.reasons)
    assert not any("inventory_missing" in reason for reason in resolution.reasons)
    assert not any("capabilities_unsatisfied" in reason for reason in resolution.reasons)
    assert not any("native_model_ref" in reason for reason in resolution.reasons)
