"""Contract tests for ModelProfileCatalog and RuntimeModelProfileCatalog."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile


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


def test_empty_model_profile_catalog() -> None:
    """An empty catalog is valid and exposes no profiles."""
    catalog = ModelProfileCatalog.from_profiles(())
    assert len(catalog) == 0
    assert catalog.profiles == ()
    assert "missing" not in catalog


def test_populated_catalog_get_and_contains() -> None:
    """Lookup returns the declared profile; membership uses profile_id."""
    sonnet = _profile()
    opus = _profile(profile_id="anthropic.claude-opus-4", model_id="claude-opus-4")
    declared = (sonnet, opus)
    catalog = ModelProfileCatalog.from_profiles(declared)
    assert catalog.get("anthropic.claude-sonnet-4") == sonnet
    assert "anthropic.claude-opus-4" in catalog
    assert len(catalog) == len(declared)


def test_get_missing_profile_raises_key_error() -> None:
    """Missing profile_id fails with KeyError, not a silent default."""
    catalog = ModelProfileCatalog.from_profiles((_profile(),))
    with pytest.raises(KeyError, match="not found"):
        catalog.get("missing.profile")


def test_duplicate_identical_profile_id_rejected() -> None:
    """Exact duplicate profile_id entries are rejected, not collapsed."""
    profile = _profile()
    with pytest.raises(ValueError, match=r"duplicate ModelProfile\.profile_id"):
        ModelProfileCatalog.from_profiles((profile, profile))


def test_conflicting_profile_definitions_rejected() -> None:
    """Same profile_id with different provider/model is a hard conflict."""
    left = _profile(provider_id="anthropic", model_id="claude-sonnet-4")
    right = _profile(provider_id="anthropic", model_id="claude-sonnet-4-20250514")
    with pytest.raises(ValueError, match="conflicting ModelProfile"):
        ModelProfileCatalog.from_profiles((left, right))


def test_enumeration_is_deterministic_by_profile_id() -> None:
    """Catalog order is sorted by profile_id regardless of insertion order."""
    zebra = _profile(profile_id="zebra.model", provider_id="zebra", model_id="z")
    alpha = _profile(profile_id="alpha.model", provider_id="alpha", model_id="a")
    catalog = ModelProfileCatalog.from_profiles((zebra, alpha))
    assert [item.profile_id for item in catalog.profiles] == [
        "alpha.model",
        "zebra.model",
    ]


def test_catalog_profiles_tuple_is_immutable() -> None:
    """Exposed profiles collection cannot be mutated in place."""
    catalog = ModelProfileCatalog.from_profiles((_profile(),))
    with pytest.raises(AttributeError):
        catalog.profiles = ()  # type: ignore[misc]
    assert isinstance(catalog.profiles, tuple)
    with pytest.raises(AttributeError):
        catalog.profiles.append(_profile(profile_id="other", model_id="other"))  # type: ignore[attr-defined]


def test_logical_identity_separated_from_structural_equality() -> None:
    """Catalog keys by profile_id; structural equality still compares all fields."""
    left = _profile(profile_id="shared", provider_id="a", model_id="m1")
    right = _profile(profile_id="shared", provider_id="a", model_id="m2")
    assert left != right
    assert left.profile_id == right.profile_id
    with pytest.raises(ValueError, match="conflicting"):
        ModelProfileCatalog.from_profiles((left, right))


def test_empty_runtime_binding_catalog() -> None:
    """An empty association catalog is valid."""
    catalog = RuntimeModelProfileCatalog.from_bindings(())
    assert len(catalog) == 0
    assert catalog.bindings == ()


def test_same_profile_may_bind_multiple_runtimes() -> None:
    """One logical profile can associate with several runtimes."""
    profile = _profile()
    bindings = (
        RuntimeModelProfile(runtime_id="opencode", profile=profile),
        RuntimeModelProfile(runtime_id="claude_code", profile=profile),
    )
    catalog = RuntimeModelProfileCatalog.from_bindings(bindings)
    assert len(catalog) == len(bindings)
    assert catalog.get("opencode", profile.profile_id).runtime_id == "opencode"
    assert catalog.get("claude_code", profile.profile_id).runtime_id == "claude_code"
    assert catalog.for_runtime("opencode") == (bindings[0],)


def test_duplicate_runtime_binding_identity_rejected() -> None:
    """Same identity_key with equal bindings is an explicit duplicate."""
    profile = _profile()
    binding = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    with pytest.raises(ValueError, match="duplicate RuntimeModelProfile"):
        RuntimeModelProfileCatalog.from_bindings((binding, binding))


def test_conflicting_runtime_binding_rejected() -> None:
    """Same identity_key with different native_model_ref is a conflict."""
    profile = _profile()
    left = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="provider/a",
    )
    right = RuntimeModelProfile(
        runtime_id="opencode",
        profile=profile,
        native_model_ref="provider/b",
    )
    with pytest.raises(ValueError, match="conflicting RuntimeModelProfile"):
        RuntimeModelProfileCatalog.from_bindings((left, right))


def test_runtime_binding_enumeration_deterministic() -> None:
    """Bindings enumerate sorted by (runtime_id, profile_id)."""
    p_a = _profile(profile_id="a.model", model_id="a")
    p_b = _profile(profile_id="b.model", model_id="b")
    catalog = RuntimeModelProfileCatalog.from_bindings(
        (
            RuntimeModelProfile(runtime_id="z-runtime", profile=p_b),
            RuntimeModelProfile(runtime_id="a-runtime", profile=p_a),
            RuntimeModelProfile(runtime_id="a-runtime", profile=p_b),
        ),
    )
    keys = [binding.identity_key() for binding in catalog.bindings]
    assert keys == [
        ("a-runtime", "a.model"),
        ("a-runtime", "b.model"),
        ("z-runtime", "b.model"),
    ]


def test_get_missing_binding_raises_key_error() -> None:
    """Missing association key fails closed."""
    catalog = RuntimeModelProfileCatalog.from_bindings(())
    with pytest.raises(KeyError, match="not found"):
        catalog.get("opencode", "missing")


def test_bindings_cross_check_against_profile_catalog() -> None:
    """Optional profiles catalog rejects unknown or divergent profile payloads."""
    profile = _profile()
    profiles = ModelProfileCatalog.from_profiles((profile,))
    ok = RuntimeModelProfile(runtime_id="opencode", profile=profile)
    assert RuntimeModelProfileCatalog.from_bindings((ok,), profiles=profiles)

    unknown = RuntimeModelProfile(
        runtime_id="opencode",
        profile=_profile(profile_id="other", model_id="other"),
    )
    with pytest.raises(ValueError, match="unknown profile_id"):
        RuntimeModelProfileCatalog.from_bindings((unknown,), profiles=profiles)

    divergent = RuntimeModelProfile(
        runtime_id="opencode",
        profile=_profile(model_id="claude-sonnet-4-alt"),
    )
    with pytest.raises(ValueError, match="conflicts with catalog"):
        RuntimeModelProfileCatalog.from_bindings((divergent,), profiles=profiles)


def test_catalog_does_not_claim_availability() -> None:
    """Registered definitions are not availability or auth evidence."""
    profiles = ModelProfileCatalog.from_profiles((_profile(),))
    bindings = RuntimeModelProfileCatalog.from_bindings(
        (RuntimeModelProfile(runtime_id="opencode", profile=_profile()),),
    )
    for obj in (profiles, bindings):
        for forbidden in (
            "available",
            "healthy",
            "authenticated",
            "authorized",
            "select",
            "route",
        ):
            assert not hasattr(obj, forbidden)


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

# Module under inspection for relative-import resolution (catalog lives here).
_CATALOG_MODULE_NAME = "agent_loop.domain.model_profile_catalog"

# Recorded when a relative import cannot be resolved from the given package.
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

    Absolute imports use ``level == 0`` and return ``module`` unchanged.

    Relative imports use the importing module's ``__package__`` (``package``).
    ``level`` matches ``ast.ImportFrom.level``: climb ``level - 1`` parents
    from ``package`` (same parent-count rule as importlib relative resolution),
    then append ``module`` when present. Returns ``None`` when the climb would
    leave the top-level package (unsafe / beyond-top).
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


def _imported_module_names(
    source: str,
    *,
    module_name: str,
) -> frozenset[str]:
    """Return absolute module targets referenced by ``import`` / ``from``.

    ``module_name`` is the fully qualified name of the file under analysis; its
    parent package is used to resolve relative imports.
    """
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
    """Return imported modules that match a forbidden root or its descendants."""
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


def test_legacy_set_intersection_misses_adapter_submodule() -> None:
    """Prefix matching is required; bare set intersection misses descendants."""
    imported = frozenset({"agent_loop.adapters.open_code", "agent_loop"})
    assert not (imported & _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert hits == frozenset({"agent_loop.adapters.open_code"})


def test_detector_rejects_adapter_root_import() -> None:
    """Direct ``agent_loop.adapters`` imports are forbidden."""
    imported = _imported_module_names(
        "import agent_loop.adapters\n",
        module_name=_CATALOG_MODULE_NAME,
    )
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters" in hits


def test_detector_rejects_adapter_submodule_from_import() -> None:
    """Submodules under ``agent_loop.adapters`` are forbidden."""
    source = "from agent_loop.adapters.open_code import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters.open_code" in hits


def test_detector_rejects_ports_submodule_from_import() -> None:
    """Submodules under ``agent_loop.ports`` are forbidden."""
    source = "from agent_loop.ports.agent_runtime import AgentRuntime\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports.agent_runtime" in hits


def test_detector_rejects_exact_forbidden_root() -> None:
    """Exact matches against existing roots (for example ``subprocess``) fail."""
    imported = _imported_module_names(
        "import subprocess\n",
        module_name=_CATALOG_MODULE_NAME,
    )
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert hits == frozenset({"subprocess"})


def test_detector_rejects_relative_adapter_submodule() -> None:
    """Relative adapter imports resolve against the catalog package."""
    source = "from ..adapters.open_code import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters.open_code" in hits


def test_detector_rejects_relative_ports_submodule() -> None:
    """Relative ports imports resolve against the catalog package."""
    source = "from ..ports.agent_runtime import AgentRuntime\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports.agent_runtime" in hits


def test_detector_rejects_from_agent_loop_import_adapters() -> None:
    """``from agent_loop import adapters`` targets the forbidden adapters package."""
    source = "from agent_loop import adapters\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters" in hits


def test_detector_rejects_from_agent_loop_import_ports() -> None:
    """``from agent_loop import ports`` targets the forbidden ports package."""
    source = "from agent_loop import ports\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.ports" in hits


def test_detector_rejects_relative_import_adapters_name() -> None:
    """``from .. import adapters`` resolves to ``agent_loop.adapters``."""
    source = "from .. import adapters\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.adapters" in hits


def test_detector_allows_domain_model_profiles_import() -> None:
    """Allowed domain imports must not produce false positives."""
    source = "from agent_loop.domain.model_profiles import ModelProfile\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.domain.model_profiles" in imported
    assert hits == frozenset()


def test_detector_allows_from_agent_loop_import_domain() -> None:
    """``from agent_loop import domain`` is allowed; base ``agent_loop`` alone is not forbidden."""
    source = "from agent_loop import domain\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert "agent_loop.domain" in imported
    assert "agent_loop" in imported
    assert hits == frozenset()


def test_detector_unresolved_relative_import_is_not_silently_allowed() -> None:
    """Relative imports that climb past the package top are fail-closed."""
    # Catalog package is ``agent_loop.domain`` (2 parts); level 4 climbs too far.
    source = "from ....adapters import OpenCodeAdapter\n"
    imported = _imported_module_names(source, module_name=_CATALOG_MODULE_NAME)
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert _UNRESOLVED_RELATIVE_IMPORT in imported
    assert _UNRESOLVED_RELATIVE_IMPORT in hits


def test_catalog_module_has_no_infrastructure_imports() -> None:
    """Catalog domain module stays free of IO, subprocess, adapters, and ports."""
    path = Path(__file__).resolve().parents[3] / "agent_loop" / "domain" / "model_profile_catalog.py"
    imported = _imported_module_names(
        path.read_text(encoding="utf-8"),
        module_name=_CATALOG_MODULE_NAME,
    )
    hits = _forbidden_import_hits(imported, _FORBIDDEN_INFRASTRUCTURE_ROOTS)
    assert hits == frozenset()
    assert "agent_loop.domain.model_profiles" in imported
