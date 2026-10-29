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


def test_model_profile_equality_and_hash_for_identical_fields() -> None:
    """Identical field triples are equal and share the same hash."""
    left = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    right = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    assert left == right
    assert hash(left) == hash(right)


def test_model_profile_inequality_when_only_provider_id_differs() -> None:
    """Structural equality changes when only provider_id differs."""
    left = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    right = _profile(
        profile_id="shared.profile",
        provider_id="Anthropic",
        model_id="claude-sonnet-4",
    )
    assert left.profile_id == right.profile_id
    assert left.model_id == right.model_id
    assert left != right


def test_model_profile_inequality_when_only_model_id_differs() -> None:
    """Structural equality changes when only model_id differs."""
    left = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    right = _profile(
        profile_id="shared.profile",
        provider_id="anthropic",
        model_id="claude-sonnet-4-20250514",
    )
    assert left.profile_id == right.profile_id
    assert left.provider_id == right.provider_id
    assert left != right


def test_model_profile_inequality_when_only_profile_id_differs() -> None:
    """Logical identity differs when only profile_id changes; equality follows fields."""
    left = _profile(
        profile_id="profile.a",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    right = _profile(
        profile_id="profile.b",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
    )
    assert left.provider_id == right.provider_id
    assert left.model_id == right.model_id
    assert left != right


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
    assert hash(left) == hash(same)
    assert left != other_runtime
    assert left.identity_key() == ("opencode", profile.profile_id)
    assert other_runtime.identity_key() == ("claude_code", profile.profile_id)


def test_runtime_model_profile_identity_key_ignores_native_ref() -> None:
    """identity_key stays (runtime_id, profile_id); native_model_ref is orthogonal."""
    profile = _profile()
    without_ref = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    with_ref = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="anthropic/claude-sonnet-4",
    )
    assert without_ref.identity_key() == with_ref.identity_key()
    assert without_ref != with_ref


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


_FORBIDDEN_INFRASTRUCTURE_ROOTS = frozenset(
    {
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
    },
)

_MODEL_PROFILES_MODULE_NAME = "agent_loop.domain.model_profiles"
_UNRESOLVED_RELATIVE_IMPORT = "__unresolved_relative_import__"


def _package_of(module_name: str) -> str:
    """Return ``__package__`` for a non-package module (its parent dotted path)."""
    if "." not in module_name:
        return ""
    return module_name.rsplit(".", maxsplit=1)[0]


def _resolve_import_from_base(
    *,
    package: str,
    level: int,
    module: str | None,
) -> str | None:
    """Resolve an ``ImportFrom`` base to an absolute module path.

    Absolute imports use ``level == 0``. Relative imports climb ``level - 1``
    parents from the importing module's ``__package__`` (importlib semantics).
    """
    if level == 0:
        return module
    if not package:
        return None
    parts = package.split(".")
    climb = level - 1
    if climb > len(parts):
        return None
    base = ".".join(parts[: len(parts) - climb]) if climb else package
    if not base:
        return None
    if module:
        return f"{base}.{module}"
    return base


def _imported_module_names(source: str, *, module_name: str) -> frozenset[str]:
    """Return absolute module targets referenced by ``import`` / ``from``."""
    package = _package_of(module_name)
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolve_import_from_base(
            package=package,
            level=node.level,
            module=node.module,
        )
        if base is None and node.level > 0:
            imported.add(_UNRESOLVED_RELATIVE_IMPORT)
            continue
        if base is not None:
            imported.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.add(f"{base}.{alias.name}")
    return frozenset(imported)


def _forbidden_import_hits(
    imported: frozenset[str],
    forbidden_roots: frozenset[str],
) -> frozenset[str]:
    """Return imports that match a forbidden root or any descendant module."""
    hits: set[str] = set()
    for module in imported:
        if module == _UNRESOLVED_RELATIVE_IMPORT:
            hits.add(module)
            continue
        for root in forbidden_roots:
            if module == root or module.startswith(f"{root}."):
                hits.add(module)
                break
    return frozenset(hits)


def test_detector_rejects_exact_forbidden_root() -> None:
    """Exact forbidden roots (for example ``subprocess``) are detected."""
    imported = _imported_module_names(
        "import subprocess\n",
        module_name=_MODEL_PROFILES_MODULE_NAME,
    )
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert hits == frozenset({"subprocess"})


def test_detector_rejects_adapter_submodule_import() -> None:
    """Descendants of ``agent_loop.adapters`` are forbidden."""
    source = "from agent_loop.adapters.open_code import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters.open_code" in hits


def test_detector_rejects_ports_submodule_import() -> None:
    """Descendants of ``agent_loop.ports`` are forbidden."""
    source = "from agent_loop.ports.agent_runtime import AgentRuntime\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports.agent_runtime" in hits


def test_detector_rejects_from_agent_loop_import_adapters() -> None:
    """``from agent_loop import adapters`` resolves to the forbidden package."""
    source = "from agent_loop import adapters\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters" in hits


def test_detector_rejects_relative_adapter_import() -> None:
    """Relative adapter imports resolve from ``agent_loop.domain``."""
    source = "from ..adapters.open_code import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters.open_code" in hits


def test_detector_rejects_relative_ports_import() -> None:
    """Relative ports imports resolve from ``agent_loop.domain``."""
    source = "from ..ports.agent_runtime import AgentRuntime\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports.agent_runtime" in hits


def test_detector_rejects_from_agent_loop_import_ports() -> None:
    """``from agent_loop import ports`` resolves to the forbidden ports package."""
    source = "from agent_loop import ports\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports" in hits
    assert "agent_loop" in imported


def test_detector_rejects_relative_import_adapters_name() -> None:
    """``from .. import adapters`` resolves when ImportFrom.module is None."""
    source = "from .. import adapters\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters" in hits


def test_detector_unresolved_relative_import_is_not_silently_allowed() -> None:
    """Relative imports that climb past the package top are fail-closed."""
    source = "from ....adapters import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert _UNRESOLVED_RELATIVE_IMPORT in imported
    assert _UNRESOLVED_RELATIVE_IMPORT in hits


def test_detector_allows_domain_model_profiles_self_reference() -> None:
    """Allowed domain imports must not produce false positives."""
    source = "from agent_loop.domain.model_profiles import ModelProfile\n"
    imported = _imported_module_names(source, module_name=_MODEL_PROFILES_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.domain.model_profiles" in imported
    assert hits == frozenset()


def test_model_profiles_module_has_no_infrastructure_imports() -> None:
    """Domain model profiles stay free of Django, IO, subprocess, and SDKs."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "domain" / "model_profiles.py"
    imported = _imported_module_names(
        path.read_text(encoding="utf-8"),
        module_name=_MODEL_PROFILES_MODULE_NAME,
    )
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert hits == frozenset()
    assert "dataclasses" in imported
