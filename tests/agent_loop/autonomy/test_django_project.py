"""Django project discovery tests."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def test_discovery_reads_manage_settings_apps_versions_and_venv(tmp_path: Path) -> None:
    """A conventional Django project is described without importing it."""
    root = make_project(tmp_path / "repo", compose=True)
    workspace = TargetWorkspace.open(root)
    project = discover_django_project(workspace)
    assert project.is_django is True
    assert project.manage_py == "manage.py"
    assert project.settings_module == "config.settings"
    assert project.settings_files == ("config/settings.py",)
    assert project.apps == ("apps.sample", "django.contrib.contenttypes")
    assert project.python_version == "3.12"
    assert project.django_version == "5.0.1"
    assert project.virtualenv == ".venv"
    assert project.python_executable == ".venv/bin/python"
    assert project.compose_files == ("docker-compose.yml",)
    assert any(item.relative_path == "requirements.txt" for item in project.dependency_files)


def test_installed_apps_supports_concatenated_lists(tmp_path: Path) -> None:
    """INSTALLED_APPS may be composed from multiple list literals."""
    root = make_project(tmp_path / "repo")
    write(
        root / "config" / "settings.py",
        'BASE_APPS = ["django.contrib.contenttypes"]\nINSTALLED_APPS = BASE_APPS + ["apps.extra"]\n',
    )
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.apps == ("apps.extra", "django.contrib.contenttypes")


def test_settings_module_falls_back_to_text_when_ast_fails(tmp_path: Path) -> None:
    """Malformed manage.py still allows a deterministic regex fallback."""
    root = make_project(tmp_path / "repo")
    write(
        root / "manage.py",
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.local")\nthis is invalid python\n',
    )
    write(root / "config" / "local.py", 'INSTALLED_APPS = ["apps.local"]\n')
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.settings_module == "config.local"
    assert "config/local.py" in project.settings_files


def test_non_django_repository_is_reported_without_error(tmp_path: Path) -> None:
    """A Git repository without Django markers is not treated as Django."""
    root = make_project(tmp_path / "plain")
    for name in ("manage.py", "requirements.txt", "pyproject.toml"):
        (root / name).unlink()
    (root / "config" / "settings.py").unlink()
    write(root / "README.md", "not a django project\n")
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.is_django is False
    assert project.manage_py is None
    assert project.apps == ()


def test_discovery_does_not_follow_external_symlinks(tmp_path: Path) -> None:
    """Django discovery never reads settings, manage.py or venv outside the workspace."""
    root = make_project(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manage.py").write_text(
        'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "outside.settings")\n',
        encoding="utf-8",
    )
    (outside / "settings.py").write_text('INSTALLED_APPS = ["outside.app"]\n', encoding="utf-8")
    (outside / ".venv" / "bin").mkdir(parents=True)
    (outside / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    (root / "manage.py").unlink()
    (root / "config" / "settings.py").unlink()
    (root / "manage.py").symlink_to(outside / "manage.py")
    (root / "linked").symlink_to(outside)
    shutil.rmtree(root / ".venv")
    (root / ".venv").symlink_to(outside / ".venv")
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.manage_py is None
    assert project.settings_module is None
    assert project.apps == ()
    assert project.virtualenv is None
    assert all("linked" not in path for path in project.settings_files)


def test_pytest_testpaths_are_discovered(tmp_path: Path) -> None:
    """Configured pytest testpaths are exposed for target selection."""
    root = make_project(tmp_path / "repo")
    write(
        root / "pyproject.toml",
        '[project]\nname = "fixture"\n[tool.pytest.ini_options]\ntestpaths = ["specs", "tests"]\n',
    )
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.pytest_testpaths == ("specs", "tests")


def test_pytest_ini_testpaths_are_discovered(tmp_path: Path) -> None:
    """pytest.ini testpaths are exposed for target selection."""
    root = make_project(tmp_path / "repo")
    write(root / "pytest.ini", "[pytest]\ntestpaths = specs\n")
    project = discover_django_project(TargetWorkspace.open(root))
    assert project.pytest_testpaths == ("specs",)
