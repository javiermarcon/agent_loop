"""S18 regression: agent_loop package boundaries and environment isolation."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path

from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityEvidence
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState
from agent_loop.domain.capabilities import EvidenceConfidence
from agent_loop.domain.capabilities import EvidenceFreshness
from agent_loop.domain.capabilities import EvidenceSource
from agent_loop.domain.capabilities import is_effective
from tests.agent_loop.application.execution_request_ast_inventory import REPO_ROOT
from tests.agent_loop.application.execution_request_ast_inventory import agent_execution_request_ctor_paths
from tests.agent_loop.application.execution_request_ast_inventory import package_name_for_source_file
from tests.agent_loop.application.execution_request_ast_inventory import resolve_import_from

_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "agent_loop"
_UNRESOLVED_RELATIVE = "__unresolved_relative_import__"
_FORBIDDEN_ROOT_PREFIXES = (
    "scripts",
    "apps",
    "django",
)
_LAYER_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "domain": (
        "agent_loop.application",
        "agent_loop.adapters",
        "agent_loop.cli",
        "agent_loop.agent",
    ),
    "ports": (
        "agent_loop.application",
        "agent_loop.adapters",
        "agent_loop.cli",
        "agent_loop.agent",
    ),
    "application": ("agent_loop.adapters",),
}


def _names_from_import_from(package: str | None, node: ast.ImportFrom) -> set[str]:
    """Resolve one ``ImportFrom`` node to absolute module names (or unresolved)."""
    if node.level > 0:
        if package is None:
            return {_UNRESOLVED_RELATIVE}
        resolved = resolve_import_from(package, node)
        if resolved is None:
            return {_UNRESOLVED_RELATIVE}
    else:
        resolved = node.module
    names: set[str] = set()
    if resolved:
        names.add(resolved)
    for alias in node.names:
        if alias.name == "*":
            continue
        names.add(f"{resolved}.{alias.name}" if resolved else alias.name)
    return names


def _imported_modules(path: Path, *, package: str | None = None) -> set[str]:
    """Return resolved absolute module names referenced by imports in ``path``.

    Handles ``import a.b``, ``from a import b`` → ``a.b``, ``from a.b import c``,
    and relative imports using ``package`` (``__package__``). Unresolvable
    relative imports yield ``_UNRESOLVED_RELATIVE`` (never silently allowed).
    """
    if package is None:
        package = package_name_for_source_file(path, repo_root=REPO_ROOT)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(_names_from_import_from(package, node))
    return imported


def _module_forbidden(module: str, forbidden_prefixes: tuple[str, ...]) -> bool:
    if module == _UNRESOLVED_RELATIVE:
        return True
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)


def _offender_lines(path: Path, forbidden: tuple[str, ...], *, package: str | None = None) -> list[str]:
    try:
        rel = str(path.resolve().relative_to(_PACKAGE_ROOT.parent.resolve()))
    except ValueError:
        rel = str(path)
    return [
        f"{rel}:{module}"
        for module in _imported_modules(path, package=package)
        if _module_forbidden(module, forbidden)
    ]


def test_agent_loop_package_does_not_import_scripts_apps_or_django() -> None:
    """Canonical package must not depend on scripts/, apps/, or Django."""
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        offenders.extend(_offender_lines(path, _FORBIDDEN_ROOT_PREFIXES))
    assert offenders == []


def test_agent_loop_layer_imports_respect_ownership_boundaries() -> None:
    """Domain/ports stay free of application/adapters; application avoids adapters."""
    offenders: list[str] = []
    for layer, forbidden in _LAYER_FORBIDDEN.items():
        layer_root = _PACKAGE_ROOT / layer
        if not layer_root.is_dir():
            continue
        for path in sorted(layer_root.rglob("*.py")):
            offenders.extend(_offender_lines(path, forbidden))
    assert offenders == []


def test_ast_inventory_repo_root_is_independent_of_process_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Inventory REPO_ROOT is derived from ``__file__``, not the process cwd."""
    monkeypatch.chdir(tmp_path)
    assert Path.cwd() == tmp_path
    assert REPO_ROOT.is_dir()
    assert (REPO_ROOT / "agent_loop").is_dir()
    found = agent_execution_request_ctor_paths(repo_root=REPO_ROOT)
    assert "agent_loop/application/execution_request_builder.py" in found


def test_agent_loop_domain_imports_cleanly_from_alternate_cwd(tmp_path: Path) -> None:
    """Importing domain modules from another cwd must not pull ``scripts``."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    script = (
        "import sys\n"
        "import agent_loop.domain.work_item\n"
        "import agent_loop.domain.work_item_plan\n"
        "import agent_loop.domain.compatible_candidates\n"
        "import agent_loop.application.execution_request_builder\n"
        "scripts = [m for m in sys.modules if m == 'scripts' or m.startswith('scripts.')]\n"
        "raise SystemExit(0 if not scripts else 1)\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_connected_yes_does_not_imply_healthy_or_authorized_effectiveness() -> None:
    """CONNECTED=YES alone never satisfies HEALTHY/AUTHORIZED mandatory axes."""
    evidence = CapabilityEvidence(
        source=EvidenceSource.CLI,
        observation="connected without health/auth proof",
        observed_at=datetime.now(tz=UTC),
        confidence=EvidenceConfidence.MEDIUM,
        freshness=EvidenceFreshness.FRESH,
    )
    snapshot = CapabilitySnapshot(
        capability_id=CapabilityId(value="mcp.example"),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=CapabilityState.YES,
        connected=CapabilityState.YES,
        healthy=CapabilityState.UNKNOWN,
        authorized=CapabilityState.UNKNOWN,
        allowed=CapabilityState.UNKNOWN,
        evidence=(evidence,),
        confidence=EvidenceConfidence.MEDIUM,
        native_binding=None,
    )
    requirement = CapabilityRequirement(
        capability_id=CapabilityId(value="mcp.example"),
        mandatory_axes=frozenset(
            {
                CapabilityAxis.CONNECTED,
                CapabilityAxis.HEALTHY,
                CapabilityAxis.AUTHORIZED,
            },
        ),
    )
    assert is_effective(snapshot, requirement) is False


def test_compatible_candidate_module_does_not_import_runtimes_or_execute() -> None:
    """Declarative compatibility stays free of adapter/runtime execution coupling."""
    path = _PACKAGE_ROOT / "domain" / "compatible_candidates.py"
    imported = _imported_modules(path)
    for module in imported:
        assert not module.startswith("agent_loop.adapters")
        assert not module.startswith("agent_loop.application")
        assert module != "scripts"
        assert not module.startswith("scripts.")
    source = path.read_text(encoding="utf-8")
    assert "AgentRuntime" not in source
    assert ".execute(" not in source
    tree = ast.parse(source)
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "winner" not in assigned


def test_module_forbidden_prefix_matching_is_exact_and_dotted() -> None:
    """Abuse: similarly named modules must not false-positive as forbidden roots."""
    assert _module_forbidden("scripts", _FORBIDDEN_ROOT_PREFIXES) is True
    assert _module_forbidden("scripts.multi_requirement", _FORBIDDEN_ROOT_PREFIXES) is True
    assert _module_forbidden("apps.orders.models", _FORBIDDEN_ROOT_PREFIXES) is True
    assert _module_forbidden("django.conf", _FORBIDDEN_ROOT_PREFIXES) is True
    assert _module_forbidden("scriptsomething", _FORBIDDEN_ROOT_PREFIXES) is False
    assert _module_forbidden("myapps.helper", _FORBIDDEN_ROOT_PREFIXES) is False
    assert _module_forbidden("django_helpers", _FORBIDDEN_ROOT_PREFIXES) is False
    assert _module_forbidden("agent_loop.domain.work_item", _FORBIDDEN_ROOT_PREFIXES) is False


def test_imported_modules_records_import_and_import_from() -> None:
    """Parser must see both ``import`` and ``from`` forms used in abuse attempts."""
    source = (
        "import scripts.agent_loop as legacy\n"
        "from apps.orders import models\n"
        "from agent_loop.domain import work_item\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="")
    assert "scripts.agent_loop" in imported
    assert "apps.orders" in imported
    assert "apps.orders.models" in imported
    assert "agent_loop.domain" in imported
    assert "agent_loop.domain.work_item" in imported


def test_from_agent_loop_import_adapters_is_reported_as_layer_offender() -> None:
    """``from agent_loop import adapters`` must resolve to ``agent_loop.adapters``."""
    source = "from agent_loop import adapters\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="agent_loop.domain")
        offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["domain"],
            package="agent_loop.domain",
        )
    assert "agent_loop.adapters" in imported
    assert any(line.endswith(":agent_loop.adapters") for line in offenders)


def test_relative_import_adapters_from_domain_is_reported() -> None:
    """``from .. import adapters`` in domain resolves to the adapters package."""
    source = "from .. import adapters\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="agent_loop.domain")
        offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["domain"],
            package="agent_loop.domain",
        )
    assert "agent_loop.adapters" in imported
    assert any(line.endswith(":agent_loop.adapters") for line in offenders)


def test_relative_import_application_from_domain_is_reported() -> None:
    """``from ..application import …`` in domain is a layer violation."""
    source = "from ..application import execution_request_builder\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="agent_loop.domain")
        offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["domain"],
            package="agent_loop.domain",
        )
    assert "agent_loop.application" in imported
    assert "agent_loop.application.execution_request_builder" in imported
    assert any(":agent_loop.application" in line for line in offenders)


def test_absolute_import_agent_loop_adapters_forms_are_detected() -> None:
    """Absolute adapter imports in all common forms are forbidden for domain."""
    sources = (
        "import agent_loop.adapters\n",
        "from agent_loop.adapters import example\n",
    )
    for source in sources:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(source, encoding="utf-8")
            offenders = _offender_lines(
                probe,
                _LAYER_FORBIDDEN["domain"],
                package="agent_loop.domain",
            )
        assert offenders, source


def test_ports_and_application_layer_bans_cover_relative_adapter_imports() -> None:
    """Ports/application relative imports of adapters are reported as offenders."""
    source = "from .. import adapters\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        port_offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["ports"],
            package="agent_loop.ports",
        )
        app_offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["application"],
            package="agent_loop.application",
        )
    assert any(line.endswith(":agent_loop.adapters") for line in port_offenders)
    assert any(line.endswith(":agent_loop.adapters") for line in app_offenders)


def test_same_layer_relative_imports_are_allowed() -> None:
    """Relative imports within domain do not trigger layer offenders."""
    source = "from . import work_item\nfrom .work_item import WorkItem\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="agent_loop.domain")
        offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["domain"],
            package="agent_loop.domain",
        )
    assert "agent_loop.domain" in imported
    assert "agent_loop.domain.work_item" in imported
    assert offenders == []


def test_unresolved_relative_import_is_not_silently_allowed() -> None:
    """Relative imports that cannot be resolved are treated as offenders."""
    source = "from ...... import adapters\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        imported = _imported_modules(probe, package="agent_loop.domain")
        offenders = _offender_lines(
            probe,
            _LAYER_FORBIDDEN["domain"],
            package="agent_loop.domain",
        )
    assert _UNRESOLVED_RELATIVE in imported
    assert any(_UNRESOLVED_RELATIVE in line for line in offenders)


def test_authorized_no_fails_even_when_connected_yes() -> None:
    """Malicious/insufficient signal: CONNECTED=YES with AUTHORIZED=NO is not effective."""
    evidence = CapabilityEvidence(
        source=EvidenceSource.CLI,
        observation="(OAuth) connected without authorization proof",
        observed_at=datetime.now(tz=UTC),
        confidence=EvidenceConfidence.LOW,
        freshness=EvidenceFreshness.UNKNOWN,
    )
    snapshot = CapabilitySnapshot(
        capability_id=CapabilityId(value="mcp.oauth.example"),
        supported=CapabilityState.YES,
        configured=CapabilityState.YES,
        enabled=CapabilityState.YES,
        available=CapabilityState.YES,
        connected=CapabilityState.YES,
        healthy=CapabilityState.YES,
        authorized=CapabilityState.NO,
        allowed=CapabilityState.UNKNOWN,
        evidence=(evidence,),
        confidence=EvidenceConfidence.LOW,
        native_binding=None,
    )
    requirement = CapabilityRequirement(
        capability_id=CapabilityId(value="mcp.oauth.example"),
        mandatory_axes=frozenset({CapabilityAxis.CONNECTED, CapabilityAxis.AUTHORIZED}),
    )
    assert is_effective(snapshot, requirement) is False


def test_planning_and_builder_application_modules_still_avoid_adapters() -> None:
    """S16/S17 application frontier must remain free of adapter/script imports."""
    for relative in (
        "application/execution_request_builder.py",
        "application/planned_execution_requests.py",
        "domain/work_item_plan.py",
    ):
        path = _PACKAGE_ROOT / relative
        for module in _imported_modules(path):
            assert not module.startswith("agent_loop.adapters"), relative
            assert module != "scripts", relative
            assert not module.startswith("scripts."), relative


def test_resolve_compatible_candidates_is_not_wired_into_planning_bridge() -> None:
    """Planning→builder bridge must not silently become a routing/selection layer."""
    bridge = (_PACKAGE_ROOT / "application" / "planned_execution_requests.py").read_text(
        encoding="utf-8",
    )
    assert "resolve_compatible_candidates" not in bridge
    assert "RuntimeInventory" not in bridge
    assert "AgentRuntime" not in bridge


def test_routing_and_failure_modules_do_not_import_adapters() -> None:
    """S13/S14 stay independent from concrete OpenCode/Claude adapters."""
    for relative in (
        "application/execution_routing.py",
        "application/recovery_policy.py",
        "application/execution_orchestrator.py",
        "domain/faults.py",
    ):
        path = _PACKAGE_ROOT / relative
        for module in _imported_modules(path):
            assert not module.startswith("agent_loop.adapters"), relative


def test_opencode_server_failure_mapping_does_not_own_control_plane_policy() -> None:
    """S22 maps failures only; it cannot import routing or recovery policy."""
    path = _PACKAGE_ROOT / "adapters" / "opencode_server" / "faults.py"
    forbidden = (
        "agent_loop.application",
        "agent_loop.domain.compatible_candidates",
        "agent_loop.domain.task_assessment",
    )
    assert _offender_lines(path, forbidden) == []
    source = path.read_text(encoding="utf-8")
    assert "decide_recovery" not in source
    assert "select_routing" not in source


def test_project_profiles_and_skills_avoid_forbidden_dependencies() -> None:
    """S20 project profiles/skills must not import adapters/application/scripts."""
    project_root = _PACKAGE_ROOT / "project"
    forbidden = (
        "agent_loop.adapters",
        "agent_loop.application",
        "agent_loop.cli",
        "scripts",
        "apps",
        "django",
    )
    offenders: list[str] = []
    for path in sorted(project_root.rglob("*.py")):
        offenders.extend(_offender_lines(path, forbidden))
    assert offenders == []


def test_domain_does_not_import_project_profiles_or_skills() -> None:
    """Domain stays free of ProjectProfile/Skills (ADR-039 / ADR-052)."""
    domain_root = _PACKAGE_ROOT / "domain"
    offenders: list[str] = []
    for path in sorted(domain_root.rglob("*.py")):
        offenders.extend(_offender_lines(path, ("agent_loop.project",)))
    assert offenders == []
