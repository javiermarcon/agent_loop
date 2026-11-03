"""Docker Compose base and override selection tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _project(tmp_path: Path, names: tuple[str, ...]):
    root = make_project(tmp_path / "repo", compose=False)
    for name in names:
        write(root / name, "services:\n  web:\n    image: python:3.12\n")
    return discover_django_project(TargetWorkspace.open(root))


def test_compose_yaml_is_the_deterministic_base(tmp_path: Path) -> None:
    """compose.yaml is recognized as the base file."""
    project = _project(tmp_path, ("compose.yaml",))
    assert project.compose_project_files == ("compose.yaml",)
    assert project.compose_error is None


def test_compose_yml_and_docker_compose_yml_are_supported(tmp_path: Path) -> None:
    """Both Compose V2 and legacy base names remain supported."""
    v2 = _project(tmp_path / "v2", ("compose.yml",))
    v1 = _project(tmp_path / "v1", ("docker-compose.yml",))
    assert v2.compose_project_files == ("compose.yml",)
    assert v1.compose_project_files == ("docker-compose.yml",)


def test_matching_override_is_selected_after_its_base(tmp_path: Path) -> None:
    """An override is never selected as the base and follows it deterministically."""
    project = _project(tmp_path, ("compose.override.yaml", "compose.yaml"))
    assert project.compose_project_files == ("compose.yaml", "compose.override.yaml")
    assert project.compose_error is None


def test_legacy_override_is_selected_after_legacy_base(tmp_path: Path) -> None:
    """Legacy docker-compose override pairing is preserved."""
    project = _project(tmp_path, ("docker-compose.override.yml", "docker-compose.yml"))
    assert project.compose_project_files == ("docker-compose.yml", "docker-compose.override.yml")


def test_only_override_is_an_explicit_error(tmp_path: Path) -> None:
    """An override without a base is not a valid Compose configuration."""
    project = _project(tmp_path, ("compose.override.yaml",))
    assert project.compose_project_files == ()
    assert project.compose_error is not None
    assert "override" in project.compose_error


def test_multiple_overrides_are_ambiguous(tmp_path: Path) -> None:
    """Two overrides for the same base cannot be chosen deterministically."""
    project = _project(tmp_path, ("compose.yaml", "compose.override.yaml", "compose.override.yml"))
    assert project.compose_project_files == ()
    assert project.compose_error is not None
    assert "ambiguous" in project.compose_error


def test_multiple_base_files_are_ambiguous(tmp_path: Path) -> None:
    """Two base candidates are rejected instead of picking alphabetically."""
    project = _project(tmp_path, ("compose.yaml", "docker-compose.yml"))
    assert project.compose_project_files == ()
    assert project.compose_error is not None
    assert "ambiguous" in project.compose_error


def test_unrelated_override_is_an_explicit_error(tmp_path: Path) -> None:
    """An override for a different base cannot be silently combined."""
    project = _project(tmp_path, ("docker-compose.yml", "compose.override.yaml"))
    assert project.compose_project_files == ()
    assert project.compose_error is not None
    assert "do not match" in project.compose_error
