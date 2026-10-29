"""Contract tests for ModelProfile and RuntimeModelProfile."""

from __future__ import annotations

import ast
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

from agent_loop.domain.capabilities import InventoryModelRef
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile
from agent_loop.ports.agent_runtime import RuntimeIdentity


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


def test_model_profile_valid_construction() -> None:
    """A logical profile carries stable ids without runtime coupling."""
    profile = _profile()
    assert profile.profile_id == "anthropic.claude-sonnet-4"
    assert profile.provider_id == "anthropic"
    assert profile.model_id == "claude-sonnet-4"


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("profile_id", {"profile_id": ""}),
        ("profile_id", {"profile_id": "   "}),
        ("provider_id", {"provider_id": ""}),
        ("provider_id", {"provider_id": "   "}),
        ("model_id", {"model_id": ""}),
        ("model_id", {"model_id": "   "}),
    ],
)
def test_model_profile_rejects_blank_identifiers(
    field: str,
    kwargs: dict[str, str],
) -> None:
    """Blank identifiers fail closed at the contract boundary."""
    with pytest.raises(ValueError, match=field):
        _profile(**kwargs)


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("profile_id", {"profile_id": " anthropic.claude"}),
        ("profile_id", {"profile_id": "anthropic.claude "}),
        ("provider_id", {"provider_id": " Anthropic"}),
        ("provider_id", {"provider_id": "anthropic "}),
        ("model_id", {"model_id": " claude"}),
        ("model_id", {"model_id": "claude "}),
    ],
)
def test_model_profile_rejects_padded_identifiers(
    field: str,
    kwargs: dict[str, str],
) -> None:
    """Padding is rejected; provider/model ids are never silently trimmed."""
    with pytest.raises(ValueError, match="leading or trailing"):
        _profile(**kwargs)
    assert field in ("profile_id", "provider_id", "model_id")


def test_model_profile_does_not_normalize_provider_case() -> None:
    """Distinct provider spellings remain distinct; no silent case folding."""
    lower = _profile(provider_id="anthropic", profile_id="p-lower")
    mixed = _profile(provider_id="Anthropic", profile_id="p-mixed")
    assert lower.provider_id != mixed.provider_id
    assert lower != mixed


def test_model_profile_equality_follows_field_identity() -> None:
    """Frozen equality is field-wise; profile_id alone is not enough."""
    left = _profile()
    same = _profile()
    different_model = _profile(model_id="claude-opus-4", profile_id="anthropic.claude-opus-4")
    assert left == same
    assert left != different_model
    assert hash(left) == hash(same)


def test_model_profile_is_immutable() -> None:
    """ModelProfile rejects attribute mutation."""
    profile = _profile()
    with pytest.raises(AttributeError):
        profile.model_id = "mutated"  # type: ignore[misc]


def test_model_profile_does_not_infer_capabilities_from_name() -> None:
    """Model names never imply tool or model capability claims in S12."""
    profile = _profile(model_id="claude-with-filesystem-tools")
    assert not hasattr(profile, "declared_capabilities")
    assert not hasattr(profile, "capabilities")


def test_runtime_model_profile_valid_construction() -> None:
    """Runtime association binds a logical profile to a runtime id space."""
    profile = _profile()
    binding = RuntimeModelProfile(
        runtime_id="claude_code",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    assert binding.runtime_id == "claude_code"
    assert binding.profile is profile
    assert binding.native_model_ref == "anthropic/claude-sonnet-4"


def test_runtime_model_profile_allows_omitted_native_ref() -> None:
    """Native harness refs are optional; absence is not unavailability."""
    binding = RuntimeModelProfile(runtime_id="opencode", profile=_profile())
    assert binding.native_model_ref is None


@pytest.mark.parametrize("runtime_id", ["", "   "])
def test_runtime_model_profile_rejects_blank_runtime_id(runtime_id: str) -> None:
    """runtime_id uses the same non-empty stripped rule as RuntimeIdentity."""
    with pytest.raises(ValueError, match="runtime_id"):
        RuntimeModelProfile(runtime_id=runtime_id, profile=_profile())


def test_runtime_model_profile_rejects_padded_runtime_id() -> None:
    """Leading/trailing whitespace on runtime_id is rejected, not trimmed."""
    with pytest.raises(ValueError, match="leading or trailing"):
        RuntimeModelProfile(runtime_id=" opencode", profile=_profile())
    with pytest.raises(ValueError, match="leading or trailing"):
        RuntimeModelProfile(runtime_id="opencode ", profile=_profile())


@pytest.mark.parametrize("native_model_ref", ["", "   ", " native ", "native "])
def test_runtime_model_profile_rejects_blank_or_padded_native_ref(
    native_model_ref: str,
) -> None:
    """When present, native_model_ref must be non-empty and unpadded."""
    with pytest.raises(ValueError, match="native_model_ref"):
        RuntimeModelProfile(
            runtime_id="opencode",
            profile=_profile(),
            native_model_ref=native_model_ref,
        )


def test_runtime_model_profile_equality_and_identity() -> None:
    """Equality is field-wise; identity key is (runtime_id, profile_id)."""
    profile = _profile()
    left = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    same = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    other_runtime = RuntimeModelProfile(runtime_id="claude_code", profile=profile)
    assert left == same
    assert left != other_runtime
    assert left.identity_key() == ("opencode", profile.profile_id)
    assert other_runtime.identity_key() == ("claude_code", profile.profile_id)


def test_runtime_model_profile_is_immutable() -> None:
    """RuntimeModelProfile rejects attribute mutation."""
    binding = RuntimeModelProfile(runtime_id="opencode", profile=_profile())
    with pytest.raises(AttributeError):
        binding.runtime_id = "mutated"  # type: ignore[misc]


def test_runtime_model_profile_does_not_claim_availability_or_auth() -> None:
    """Declaring a binding is not availability, health, auth, or authorization."""
    binding = RuntimeModelProfile(runtime_id="opencode", profile=_profile())
    for forbidden in (
        "available",
        "availability",
        "healthy",
        "authenticated",
        "authorized",
        "allowed",
        "connected",
    ):
        assert not hasattr(binding, forbidden)


def test_runtime_id_compatible_with_runtime_identity_and_inventory() -> None:
    """runtime_id shares the string space used by RuntimeIdentity / inventory."""
    identity = RuntimeIdentity(runtime_id="opencode")
    binding = RuntimeModelProfile(runtime_id=identity.runtime_id, profile=_profile())
    inventory = RuntimeInventory(
        runtime_identity=identity.runtime_id,
        runtime_version="1.0.0",
        adapter_version="1",
        discovered_at=datetime.now(tz=UTC),
        capabilities=(),
        models=(InventoryModelRef(model_id="anthropic/claude-sonnet-4"),),
    )
    assert binding.runtime_id == inventory.runtime_identity
    with_native = RuntimeModelProfile(
        runtime_id=identity.runtime_id,
        profile=_profile(),
        native_model_ref=inventory.models[0].model_id,
    )
    assert with_native.native_model_ref == inventory.models[0].model_id


def test_model_profiles_module_has_no_infrastructure_imports() -> None:
    """Domain model profiles stay free of Django, IO, subprocess, and SDKs."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "domain" / "model_profiles.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "django",
        "subprocess",
        "socket",
        "http",
        "urllib",
        "requests",
        "pathlib",
        "os",
        "sys",
        "agent_loop.adapters",
        "agent_loop.ports",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
                imported.add(alias.name.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert not (imported & forbidden)
    assert "dataclasses" in imported
