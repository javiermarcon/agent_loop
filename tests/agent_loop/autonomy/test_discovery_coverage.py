"""Coverage-focused discovery contracts that complement the behavior suites."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from agent_loop.autonomy.discovery.compose import ComposeDiscovery
from agent_loop.autonomy.discovery.compose import _compose_candidates
from agent_loop.autonomy.discovery.compose import _container_runtime
from agent_loop.autonomy.discovery.compose import _dockerfile_args
from agent_loop.autonomy.discovery.compose import _dockerfile_workdir
from agent_loop.autonomy.discovery.compose import _documented_service
from agent_loop.autonomy.discovery.compose import _pair_overrides
from agent_loop.autonomy.discovery.compose import _ranked
from agent_loop.autonomy.discovery.compose import _resolve_docker_vars
from agent_loop.autonomy.discovery.compose import _select
from agent_loop.autonomy.discovery.compose import _service_by_hint
from agent_loop.autonomy.discovery.compose import _service_workdir
from agent_loop.autonomy.discovery.compose import _services
from agent_loop.autonomy.discovery.compose import _text_services
from agent_loop.autonomy.discovery.compose import _yaml_data
from agent_loop.autonomy.discovery.compose import discover_compose
from agent_loop.autonomy.discovery.compose import evidence_files
from agent_loop.autonomy.discovery.django_project import _dependency_files
from agent_loop.autonomy.discovery.django_project import _dockerfiles
from agent_loop.autonomy.discovery.django_project import _ini_testpaths
from agent_loop.autonomy.discovery.django_project import _is_local_app
from agent_loop.autonomy.discovery.django_project import _pyproject_testpaths
from agent_loop.autonomy.discovery.django_project import _python_version
from agent_loop.autonomy.discovery.django_project import _read_pyproject
from agent_loop.autonomy.discovery.django_project import _virtualenv
from agent_loop.autonomy.discovery.django_project import _walk_named
from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.discovery.django_settings import _apps_in_file
from agent_loop.autonomy.discovery.django_settings import _branches
from agent_loop.autonomy.discovery.django_settings import _condition
from agent_loop.autonomy.discovery.django_settings import _condition_matches
from agent_loop.autonomy.discovery.django_settings import _import_parts
from agent_loop.autonomy.discovery.django_settings import _local_imports
from agent_loop.autonomy.discovery.django_settings import _module_candidates
from agent_loop.autonomy.discovery.django_settings import _select_by_runtime
from agent_loop.autonomy.discovery.django_settings import _settings_files
from agent_loop.autonomy.discovery.django_settings import discover_settings
from agent_loop.autonomy.discovery.tool_commands import _classify
from agent_loop.autonomy.discovery.tool_commands import _coverage_module
from agent_loop.autonomy.discovery.tool_commands import _parse_command
from agent_loop.autonomy.discovery.tool_commands import _python_module
from agent_loop.autonomy.discovery.tool_commands import discover_documented_commands
from agent_loop.autonomy.discovery.tool_config import _dependency_text
from agent_loop.autonomy.discovery.tool_config import _ini_section
from agent_loop.autonomy.discovery.tool_config import _load_pre_commit_data
from agent_loop.autonomy.discovery.tool_config import _read_toml
from agent_loop.autonomy.discovery.tool_config import config_details_for
from agent_loop.autonomy.discovery.tool_config import is_tool_not_required
from agent_loop.autonomy.discovery.tool_models import ToolKind
from agent_loop.autonomy.discovery.tools import _detect_compose_project
from agent_loop.autonomy.discovery.tools import _probe_external
from agent_loop.autonomy.discovery.tools import module_for
from agent_loop.autonomy.discovery.tools import probe_importable
from agent_loop.autonomy.discovery.tools import tool_order
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write


def _workspace(tmp_path: Path, **kwargs: bool) -> TargetWorkspace:
    return TargetWorkspace.open(make_project(tmp_path / "repo", **kwargs))


def test_tool_command_parsing_covers_separators_invalid_shell_and_modules() -> None:
    """Documented command parsing is deterministic for every accepted form."""
    assert _parse_command("pytest -q && ruff check") == ("pytest", "-q")
    assert _parse_command("pytest -q || true") == ("pytest", "-q")
    assert _parse_command("   ") is None
    assert _parse_command("python -m") == ("python", "-m")
    assert _parse_command("python -m 'unterminated") is None
    assert _classify(("python", "-m", "unknown"), "web") == (None, ("python", "-m", "unknown"), None)
    assert _classify(("python", "-m", "pytest"), "web") == ("pytest", ("python", "-m", "pytest"), None)
    assert _classify(("coverage", "run", "-m", "pytest", "-q"), "web")[0] == "pytest"
    assert _classify(("coverage", "run", "-m", "other", "-q"), "web")[0] is None
    assert _classify(("unknown",), "web") == (None, ("unknown",), None)
    assert _python_module(("python", "-m")) == (None, ("python", "-m"), None)
    assert _python_module(("python", "-m", "coverage", "run", "-m", "pytest")) == (
        "pytest",
        ("python", "-m", "coverage", "run", "-m", "pytest"),
        None,
    )
    assert _python_module(("python", "-m", "coverage", "run", "-m", "other"))[0] is None
    assert _coverage_module(("coverage", "run", "-m")) == (None, ("coverage", "run", "-m"), None)
    assert _coverage_module(("coverage", "run", "-m", "pytest")) == (
        "pytest",
        ("coverage", "run", "-m", "pytest"),
        None,
    )
    assert _coverage_module(("coverage", "run", "pytest", "x")) == (None, ("coverage", "run", "pytest", "x"), None)


def test_documented_commands_skip_other_services_invalid_commands_and_duplicates(tmp_path: Path) -> None:
    """Only the first deterministic command per tool is retained."""
    workspace = _workspace(tmp_path)
    write(
        workspace.root / "README.md",
        "docker compose exec web python -m pytest -q\n"
        "docker compose exec db pytest -q\n"
        "docker compose exec web python -m 'broken\n"
        "docker compose exec web python -m pytest tests\n",
    )
    commands = discover_documented_commands(workspace, ("web",))
    assert len(commands) == 1
    assert commands[0].tool == "pytest"
    assert commands[0].args == ("python", "-m", "pytest", "-q")


def test_tool_configuration_dependency_text_and_precommit_details(tmp_path: Path) -> None:
    """pyproject sections, Poetry dependencies and malformed YAML stay bounded."""
    workspace = _workspace(tmp_path)
    write(
        workspace.root / "pyproject.toml",
        "[project]\ndependencies=['ruff']\n[project.optional-dependencies]\ndev=['mypy']\n"
        "[project.dependency-groups]\ngroup=['pylint']\n[build-system]\nrequires=['setuptools']\n"
        "[tool.poetry.dependencies]\npython='^3.12'\nbandit='*'\n",
    )
    text = _dependency_text(workspace, "pyproject.toml")
    for value in ("ruff", "mypy", "pylint", "setuptools", "bandit"):
        assert value in text
    assert "Django==5.0.1" in _dependency_text(workspace, "requirements.txt")
    assert _read_toml(workspace, "missing.toml") == {}
    write(workspace.root / "broken.toml", "[")
    assert _read_toml(workspace, "broken.toml") == {}
    write(workspace.root / "setup.cfg", "[mypy]\nstrict = true\n")
    assert _ini_section(workspace, "setup.cfg", "mypy") is True
    assert _ini_section(workspace, "setup.cfg", "pylint") is False
    assert _ini_section(workspace, "missing.cfg", "mypy") is False
    (workspace.root / ".pre-commit-config.yaml").unlink()
    assert _load_pre_commit_data(workspace) is None


def test_tool_configuration_precommit_yaml_variants_and_security_failures(tmp_path: Path) -> None:
    """Pre-commit configuration discovery handles both filenames and invalid shapes."""
    workspace = _workspace(tmp_path, pre_commit=False)
    write(
        workspace.root / ".pre-commit-config.yml",
        "repos:\n  - repo: local\n    hooks:\n      - id: ruff\n      - id: 3\n  - repo: 4\n    hooks: nope\n",
    )
    assert config_details_for(workspace, ToolKind.PRE_COMMIT) == ("local:ruff",)
    write(workspace.root / ".pre-commit-config.yaml", "repos: [\n")
    assert _load_pre_commit_data(workspace) is None
    write(workspace.root / ".pre-commit-config.yaml", "repos: invalid\n")
    assert _load_pre_commit_data(workspace) == {"repos": "invalid"}


def test_tool_not_required_uses_dependencies_and_documented_commands(tmp_path: Path) -> None:
    """A configured tool without dependency or invocation evidence is not required."""
    workspace = _workspace(tmp_path, pre_commit=False)
    project = discover_django_project(workspace)
    assert is_tool_not_required(workspace, project, ToolKind.PYTEST) is False
    assert is_tool_not_required(workspace, project, ToolKind.BEHAVE) is False
    assert is_tool_not_required(workspace, project, ToolKind.RUFF) is True
    write(workspace.root / "requirements.txt", "ruff\n")
    project = discover_django_project(workspace)
    assert is_tool_not_required(workspace, project, ToolKind.RUFF) is False


def test_compose_candidates_and_selection_errors(tmp_path: Path) -> None:
    """Override-only, ambiguous and mismatched Compose files fail explicitly."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "docker-compose.override.yml", "services:\n  web: {}\n")
    candidates = _compose_candidates(workspace)
    assert "docker-compose.override.yml" in candidates
    selection, error, _reason = _select(workspace, candidates)
    assert selection == ()
    assert error is not None
    assert "only Docker Compose override" in error
    write(workspace.root / "docker-compose.yml", "services:\n  web: {}\n")
    candidates = _compose_candidates(workspace)
    selection, error, _ = _select(workspace, candidates)
    assert error is None
    assert selection == ("docker-compose.yml", "docker-compose.override.yml")
    write(workspace.root / "compose.yaml", "services:\n  web: {}\n")
    write(workspace.root / "README.md", "docker compose -f docker-compose.yml up\n")
    write(workspace.root / "AGENTS.md", "docker compose -f compose.yaml up\n")
    candidates = _compose_candidates(workspace)
    _selection, error, _ = _select(workspace, candidates)
    assert error is not None
    assert "ambiguous" in error


def test_compose_variant_selection_prefers_non_production_and_reports_reason(tmp_path: Path) -> None:
    """A production-named variant loses to one non-production variant."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "compose.production.yml", "services:\n  web: {}\n")
    write(workspace.root / "compose.local.yml", "services:\n  web: {}\n")
    write(workspace.root / "README.md", "compose.local.yml\n")
    scores: dict[str, tuple[int, tuple[str, ...]]] = {
        "compose.production.yml": (10, ()),
        "compose.local.yml": (10, ()),
    }
    assert _ranked(scores, ("compose.production.yml", "compose.local.yml")) == ("compose.local.yml",)
    selection = discover_compose(workspace)
    assert selection.project_files == ("compose.local.yml",)
    assert selection.reason
    assert _ranked({"a": (0, ())}, ("a",)) == ("a",)
    assert _ranked({"a": (0, ()), "b": (0, ())}, ("a", "b")) == ()
    assert _ranked({}, ()) == ()


def test_compose_override_pairing_rejects_missing_unrelated_and_duplicates(tmp_path: Path) -> None:
    """Override pairing fails closed for missing, unrelated and duplicate files."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "docker-compose.yml", "services:\n  web: {}\n")
    assert _pair_overrides(workspace, "docker-compose.yml", ()) == ()
    assert _pair_overrides(workspace, "docker-compose.yml", ("compose.override.yml",)) is None
    assert _pair_overrides(workspace, "docker-compose.yml", ("unrelated.yml",)) is None
    assert (
        _pair_overrides(
            workspace,
            "docker-compose.yml",
            ("docker-compose.override.yml", "docker-compose.override.yaml"),
        )
        is None
    )


def test_compose_services_and_workdir_evidence(tmp_path: Path) -> None:
    """Service detection uses documentation first, then Django-like names."""
    workspace = _workspace(tmp_path)
    write(
        workspace.root / "compose.yaml",
        "services:\n  web:\n    build:\n      context: .\n      dockerfile: Dockerfile.dev\n  worker: {}\n",
    )
    write(workspace.root / "Dockerfile.dev", "ARG APP_HOME=/app\nWORKDIR ${APP_HOME}\n")
    write(workspace.root / "README.md", "docker compose exec worker pytest\n")
    assert _documented_service(workspace, ("web", "worker")) == "worker"
    assert _service_by_hint(("web", "worker")) == ("web",)
    assert _service_workdir(workspace, ("compose.yaml",), "web") == "/app"
    assert _container_runtime(workspace, ("compose.yaml",))[0] == "worker"
    assert discover_compose(workspace).service == "worker"


def test_compose_service_workdir_uses_working_dir_and_dockerfile_arguments(tmp_path: Path) -> None:
    """Explicit working_dir and Dockerfile ARG defaults resolve deterministically."""
    workspace = _workspace(tmp_path)
    write(
        workspace.root / "compose.yaml",
        "services:\n  web:\n    working_dir: ${APP_HOME:-/srv}\n",
    )
    assert _service_workdir(workspace, ("compose.yaml",), "web") == "/srv"
    assert _resolve_docker_vars("${A}/${B:-fallback}/$C", {"A": "a", "C": "c"}) == "a/fallback/c"


def test_compose_yaml_and_text_service_parsing_handles_invalid_and_blank_entries(tmp_path: Path) -> None:
    """Malformed YAML falls back to the text scanner without inventing services."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "compose.yaml", "services:\n  web:\n    image: x\nvolumes:\n  data:\n")
    assert _yaml_data(workspace, "missing.yaml") == {}
    assert _text_services("services:\n  web:\nvolumes:\n  data:\n") == ("web",)
    assert _text_services("services:\n\n") == ()


def test_compose_text_scanner_does_not_promote_nested_empty_keys(tmp_path: Path) -> None:
    """Nested ``build:``/``ports:`` mappings are body keys, never services."""
    text = (
        "services:\n"
        "  web:\n"
        "    build:\n"
        "      context: .\n"
        "    ports:\n"
        '      - "8000:8000"\n'
        "  worker:\n"
        "    environment:\n"
        "volumes:\n"
        "  data:\n"
    )
    assert _text_services(text) == ("web", "worker")
    workspace = _workspace(tmp_path)
    write(workspace.root / "compose.yaml", text)
    assert _services(workspace, ("compose.yaml",)) == ("web", "worker")


def test_compose_evidence_file_limit_and_large_file_are_bounded(tmp_path: Path) -> None:
    """Documentation evidence stops at the deterministic limit."""
    workspace = _workspace(tmp_path)
    scripts = workspace.root / "scripts"
    scripts.mkdir()
    for index in range(45):
        write(scripts / f"doc_{index}.md", "compose.yaml\n")
    assert len(evidence_files(workspace)) <= 40


def test_compose_candidates_handles_unreadable_root(tmp_path: Path) -> None:
    """An unreadable directory does not crash candidate discovery."""
    root = make_project(tmp_path / "repo")
    workspace = TargetWorkspace.open(root)
    original = Path.iterdir

    def _raise(self: Path) -> Any:
        msg = "denied"
        raise OSError(msg)

    try:
        Path.iterdir = _raise  # type: ignore[method-assign]
        assert _compose_candidates(workspace) == ()
    finally:
        Path.iterdir = original  # type: ignore[method-assign]


def test_django_settings_text_fallback_and_syntax_error(tmp_path: Path) -> None:
    """Non-AST manage.py still exposes the documented settings module."""
    workspace = _workspace(tmp_path)
    manage = workspace.root / "manage.py"
    manage.write_text(
        "this is not python:\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'custom.settings')\n",
        encoding="utf-8",
    )
    candidates, selected, reason = _module_candidates(manage, None)
    assert candidates == ("custom.settings",)
    assert selected == "custom.settings"
    assert reason is None
    manage.write_text("DJANGO_SETTINGS_MODULE", encoding="utf-8")
    assert _module_candidates(manage, None) == ((), None, None)


def test_django_settings_runtime_branch_selection(tmp_path: Path) -> None:
    """equals/contains/default branches select exactly one runtime module."""
    import ast  # noqa: PLC0415

    source = (
        "import os\n"
        "settings = 'a.settings'\n"
        "path = os.getcwd()\n"
        "if path == '/app':\n"
        "    settings = 'b.settings'\n"
        "elif '/worktree' in path:\n"
        "    settings = 'c.settings'\n"
        "else:\n"
        "    settings = 'd.settings'\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings)\n"
    )
    tree = ast.parse(source)
    nodes = tuple(node.args[1] for node in ast.walk(tree) if isinstance(node, ast.Call) and len(node.args) >= 2)
    assert _select_by_runtime(tree, nodes, "/app") == "b.settings"
    assert _select_by_runtime(tree, nodes, "/worktree/x") == "c.settings"
    assert _select_by_runtime(tree, nodes, "/elsewhere") == "d.settings"
    contains_if = cast("ast.If", ast.parse("if 'abc' in x:\n    pass\n").body[0])
    plain_if = cast("ast.If", ast.parse("if x:\n    pass\n").body[0])
    assert _condition(contains_if.test) == ("contains", "abc")
    assert _condition(plain_if.test) is None
    assert _condition_matches(None, "/x") is False
    assert _condition_matches(("equals", "/app/"), "/app") is True
    assert _condition_matches(("contains", "work"), "/worktree") is True
    assert _branches(tree, "settings")


def test_django_settings_ambiguous_runtime_and_module_files(tmp_path: Path) -> None:
    """Ambiguity is explained; settings imports are followed within bounds."""
    workspace = _workspace(tmp_path)
    write(
        workspace.root / "manage.py",
        "import os\nsettings = 'a.settings'\nif os.getcwd() == '/a':\n    settings = 'b.settings'\n"
        "if os.getcwd() == '/c':\n    settings = 'c.settings'\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', settings)\n",  # noqa: E501
    )
    manage = workspace.root / "manage.py"
    candidates, selected, reason = _module_candidates(manage, "/unknown")
    assert len(candidates) == 3
    assert selected is None
    assert "no settings branch matches" in str(reason)
    (workspace.root / "config" / "settings.py").unlink()
    write(workspace.root / "config" / "settings" / "__init__.py", "from .base import *\n")
    write(workspace.root / "config" / "settings" / "base.py", "INSTALLED_APPS = ['django.contrib.auth']\n")
    write(
        workspace.root / "manage.py",
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n",
    )
    settings = discover_settings(workspace, workspace.root / "manage.py")
    assert "config/settings/__init__.py" in settings.files
    assert "config/settings/base.py" in settings.files
    assert settings.apps == ("django.contrib.auth",)
    assert _settings_files(workspace, "config.settings")


def test_django_settings_import_parts_and_local_imports(tmp_path: Path) -> None:
    """Relative imports resolve inside packages and reject escaping levels."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "pkg" / "__init__.py", "from .module import X\nfrom ....outside import Y\n")
    write(workspace.root / "pkg" / "module.py", "X = 1\n")
    assert _import_parts(["pkg"], 0, "a.b") == ["a", "b"]
    assert _import_parts(["pkg", "nested"], 2, "module") == ["pkg", "module"]
    assert _import_parts(["pkg"], 5, "module") is None
    assert _local_imports(workspace, workspace.root / "pkg" / "__init__.py") == (workspace.root / "pkg" / "module.py",)
    write(workspace.root / "pkg" / "broken.py", "from . import (\n")
    assert _local_imports(workspace, workspace.root / "pkg" / "broken.py") == ()


def test_django_settings_installed_apps_forms_and_invalid_syntax(tmp_path: Path) -> None:
    """Constants, concatenation and augmented assignment contribute apps."""
    path = tmp_path / "settings.py"
    path.write_text(
        "BASE = ['django.contrib.auth']\n"
        "EXTRA = ['apps.local']\n"
        "INSTALLED_APPS = BASE + EXTRA\n"
        "INSTALLED_APPS += ['rest_framework']\n",
        encoding="utf-8",
    )
    assert set(_apps_in_file(path)) == {"django.contrib.auth", "apps.local", "rest_framework"}
    path.write_text("INSTALLED_APPS = (\n", encoding="utf-8")
    assert _apps_in_file(path) == []


def test_django_project_versions_dependencies_and_virtualenv(tmp_path: Path) -> None:
    """Dependency and runtime metadata come from deterministic static evidence."""
    workspace = _workspace(tmp_path, venv=False)
    (workspace.root / ".python-version").unlink()
    write(workspace.root / "runtime.txt", "python-3.12.4\n")
    write(workspace.root / "requirements" / "base.txt", "Django==5.0.2\n")
    assert _python_version(workspace) == "3.12.4"
    files = _dependency_files(workspace)
    assert any(item.relative_path == "requirements/base.txt" for item in files)
    assert _virtualenv(workspace) == (None, None)
    (workspace.root / ".venv" / "bin").mkdir(parents=True)
    (workspace.root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    assert _virtualenv(workspace)[0] == ".venv"


def test_django_project_python_version_from_pyproject_and_local_app(tmp_path: Path) -> None:
    """requires-python is used when no version file exists; local apps are dirs."""
    workspace = _workspace(tmp_path)
    (workspace.root / ".python-version").unlink()
    write(workspace.root / "pyproject.toml", "[project]\nrequires-python='>=3.12'\n")
    assert _python_version(workspace) == ">=3.12"
    assert _is_local_app(workspace, "apps.sample") is True
    assert _is_local_app(workspace, "single") is False
    assert _is_local_app(workspace, "missing.app") is False


def test_django_project_testpaths_ini_and_pyproject_variants(tmp_path: Path) -> None:
    """testpaths are read from pytest.ini, pyproject and setup.cfg."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "pytest.ini", "[pytest]\ntestpaths = tests, unit\n")
    assert _ini_testpaths(workspace, "pytest.ini", "pytest") == ("tests", "unit")
    write(workspace.root / "broken.ini", "[pytest\n")
    assert _ini_testpaths(workspace, "broken.ini", "pytest") == ()
    write(workspace.root / "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths='tests'\n")
    assert _pyproject_testpaths(workspace) == ("tests",)
    write(workspace.root / "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths=['tests', 3]\n")
    assert _pyproject_testpaths(workspace) == ("tests",)
    write(workspace.root / "pyproject.toml", "[")
    assert _read_pyproject(workspace) is None


def test_django_project_dockerfile_walk_and_named_limit(tmp_path: Path) -> None:
    """Dockerfiles are found at root and in nested directories."""
    workspace = _workspace(tmp_path)
    write(workspace.root / "Dockerfile", "FROM python\n")
    write(workspace.root / "service" / "Dockerfile", "FROM python\n")
    assert set(_dockerfiles(workspace)) >= {"Dockerfile", "service/Dockerfile"}
    found = _walk_named(workspace, "Dockerfile", limit=1)
    assert len(found) == 1


def test_detect_compose_project_and_probe_external_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Compose project detection and version probes fail closed."""
    assert _detect_compose_project("/bin/false", cast("Any", EdgeWorkspace(tmp_path)), ("compose.yaml",)) is None
    assert _probe_external("/definitely/missing", ("--version",)) is False
    assert module_for(ToolKind.RUFF) == "ruff"
    assert module_for(ToolKind.GIT) is None
    assert "git" in [kind.value for kind in tool_order()]
    assert probe_importable("json") is True


class EdgeWorkspace:
    """Minimal workspace exposing only the root used by detection."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, name: str) -> Path:
        return self.root / name


def test_compose_discovery_for_empty_and_unreadable_documentation(tmp_path: Path) -> None:
    """No compose file yields a neutral discovery result."""
    workspace = _workspace(tmp_path)
    assert discover_compose(workspace) == ComposeDiscovery()
    assert _documented_service(workspace, ("web",)) is None
    write(workspace.root / "README.md", "x" * 300_000)
    assert evidence_files(workspace)


def test_compose_dockerfile_helpers_tolerate_missing_files(tmp_path: Path) -> None:
    """Dockerfile argument/workdir helpers return empty evidence when absent."""
    workspace = _workspace(tmp_path)
    assert _dockerfile_args(workspace, "missing.Dockerfile") == {}
    assert _dockerfile_workdir(workspace, "missing.Dockerfile") is None


def test_discovery_security_errors_are_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Symlink escapes in discovery are treated as absent, never analyzed."""
    workspace = _workspace(tmp_path)
    (workspace.root / "escape.yml").symlink_to(tmp_path / "missing-target.yml")
    assert _yaml_data(workspace, "escape.yml") == {}
    assert os.path.exists(workspace.root)  # noqa: PTH110


def test_compose_additional_failure_and_fallback_branches(tmp_path: Path) -> None:
    """Override pairing, empty evidence and malformed YAML fail closed."""
    from agent_loop.autonomy.discovery import compose as compose_module  # noqa: PLC0415
    from agent_loop.autonomy.discovery.compose import _finalize_selection  # noqa: PLC0415
    from agent_loop.autonomy.discovery.compose import _read_text as compose_read_text  # noqa: PLC0415
    from agent_loop.autonomy.discovery.compose import _score  # noqa: PLC0415
    from agent_loop.autonomy.discovery.compose import _service_config  # noqa: PLC0415
    from agent_loop.autonomy.discovery.compose import _workspace_dir  # noqa: PLC0415

    workspace = _workspace(tmp_path)
    write(workspace.root / "docker-compose.yml", "services:\n  web: {}\n")
    assert _pair_overrides(workspace, "docker-compose.yml", ("docker-compose.override.yml",)) is None
    selection, error, _ = _finalize_selection(workspace, "docker-compose.yml", (), {"docker-compose.yml": (0, ())})
    assert error is None
    assert selection == ("docker-compose.yml",)
    assert _score(workspace, ("docker-compose.yml",))
    assert _container_runtime(workspace, ())[0] is None
    write(workspace.root / "services.yaml", "services: []\n")
    assert _service_config(workspace, ("services.yaml",), "web") == {}

    write(workspace.root / "bad.yaml", "services: [\n")
    assert _yaml_data(workspace, "bad.yaml") == {}
    assert compose_read_text(None) == ""
    write(workspace.root / "huge.md", "x" * 300_000)
    assert compose_read_text(workspace.root / "huge.md") == ""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError("x")))
    assert compose_read_text(workspace.root / "README.md") == ""
    monkeypatch.undo()
    monkeypatch.setattr(TargetWorkspace, "resolve", lambda self, candidate: (_ for _ in ()).throw(SecurityError("x")))
    assert _workspace_dir(workspace, "docs") is None
    monkeypatch.undo()
    sys_modules = __import__("sys").modules
    sys_modules["yaml"] = None
    try:
        assert _yaml_data(workspace, "docker-compose.yml") == {}
    finally:
        del sys_modules["yaml"]
    assert compose_module is not None


def test_django_settings_remaining_helpers(tmp_path: Path) -> None:
    """Unsupported AST nodes, import depth and unreadable files stay bounded."""
    import ast  # noqa: PLC0415

    from agent_loop.autonomy.discovery import django_settings as settings_module  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _follow_imports  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _read_text as settings_read_text  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _resolve_value  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _select_by_runtime  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _string_items  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _walk_named  # noqa: PLC0415
    from agent_loop.autonomy.discovery.django_settings import _workspace_file  # noqa: PLC0415

    workspace = _workspace(tmp_path)
    expression = cast("ast.Expr", ast.parse("1 + 1").body[0]).value
    assert _resolve_value(expression, {}) == []
    assert _select_by_runtime(ast.parse("x = 1"), (), None) is None
    names = (ast.Name(id="a"), ast.Name(id="b"))
    assert _select_by_runtime(ast.parse("x = 1"), names, "/x") is None
    assert _string_items(None) == []
    path = workspace.root / "settings.py"
    path.write_text("INSTALLED_APPS = []\n", encoding="utf-8")
    assert len(_walk_named(workspace, "settings.py", limit=1)) == 1

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings_module, "_IMPORT_DEPTH", 0)
    assert _follow_imports(workspace, (path,)) == (path,)
    monkeypatch.undo()
    assert _follow_imports(workspace, (path, path)) == (path,)
    monkeypatch.setattr(TargetWorkspace, "resolve", lambda self, candidate: (_ for _ in ()).throw(SecurityError("x")))
    assert _workspace_file(workspace, "settings.py") is None
    monkeypatch.undo()
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError("x")))
    assert settings_read_text(path) == ""
    monkeypatch.undo()
    assert path in _settings_files(workspace, "missing.settings")
