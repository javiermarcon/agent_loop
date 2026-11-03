"""Compose variant discovery and evidence-backed selection tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path

_SERVICES = "services:\n  django:\n    image: python:3.12\n"


def _project(tmp_path: Path, files: dict[str, str]):
    root = make_project(tmp_path / "repo", compose=False)
    for name, content in files.items():
        write(root / name, content)
    return discover_django_project(TargetWorkspace.open(root))


def test_docker_compose_local_variant_is_recognized(tmp_path: Path) -> None:
    """docker-compose.local.yml is a real candidate, not an unknown override."""
    project = _project(tmp_path, {"docker-compose.local.yml": _SERVICES})
    assert "docker-compose.local.yml" in project.compose_files
    assert project.compose_project_files == ("docker-compose.local.yml",)
    assert project.compose_error is None


def test_project_marker_selects_variant_over_other_variants(tmp_path: Path) -> None:
    """An explicit COMPOSE_FILE assignment identifies the project configuration."""
    files = {
        "docker-compose.local.yml": _SERVICES,
        "docker-compose.production.yml": _SERVICES,
        "justfile": 'export COMPOSE_FILE := "docker-compose.local.yml"\n',
    }
    project = _project(tmp_path, files)
    assert project.compose_project_files == ("docker-compose.local.yml",)
    assert project.compose_reason is not None
    assert "justfile" in project.compose_reason


def test_script_constant_selects_variant(tmp_path: Path) -> None:
    """A documented constant in project scripts is configuration evidence."""
    files = {
        "docker-compose.ci.yml": _SERVICES,
        "docker-compose.dev.yml": _SERVICES,
        "scripts/runtime.py": 'DOCKER_COMPOSE_FILE = "docker-compose.ci.yml"\n',
    }
    project = _project(tmp_path, files)
    assert project.compose_project_files == ("docker-compose.ci.yml",)


def test_multiple_variants_without_evidence_are_ambiguous(tmp_path: Path) -> None:
    """Two plausible variants are never chosen alphabetically."""
    files = {
        "docker-compose.local.yml": _SERVICES,
        "docker-compose.dev.yml": _SERVICES,
    }
    project = _project(tmp_path, files)
    assert project.compose_project_files == ()
    assert project.compose_error is not None
    assert "ambiguous" in project.compose_error


def test_missing_compose_file_reports_not_configured(tmp_path: Path) -> None:
    """No Compose file means no Compose configuration."""
    project = _project(tmp_path, {})
    assert project.compose_files == ()
    assert project.compose_project_files == ()
    assert project.compose_error is None


def test_documented_service_and_workdir_are_resolved(tmp_path: Path) -> None:
    """Service choice comes from docs and the runtime cwd from the Dockerfile."""
    files = {
        "docker-compose.local.yml": (
            "services:\n"
            "  django:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: ./compose/local/Dockerfile\n"
            "  postgres:\n"
            "    image: postgres:16\n"
        ),
        "compose/local/Dockerfile": "ARG APP_HOME=/app\nWORKDIR ${APP_HOME}\n",
        "TESTING.md": "docker compose -f docker-compose.local.yml exec -T django pytest\n",
    }
    project = _project(tmp_path, files)
    assert project.compose_service == "django"
    assert project.compose_runtime_cwd == "/app"


def test_regression_docker_compose_local_is_not_an_override(tmp_path: Path) -> None:
    """The historical S21 defect: local variant was reported as not configured."""
    project = _project(
        tmp_path,
        {
            "docker-compose.local.yml": _SERVICES,
            "README.md": "docker compose -f docker-compose.local.yml exec -T django pytest\n",
        },
    )
    assert project.compose_project_files == ("docker-compose.local.yml",)
    assert project.compose_service == "django"
