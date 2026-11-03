"""Static Django settings and installed-app discovery tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.django_project import discover_django_project
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write

if TYPE_CHECKING:
    from pathlib import Path


def _discover(root: Path):
    return discover_django_project(TargetWorkspace.open(root))


def test_settings_module_from_variable_assignment(tmp_path: Path) -> None:
    """DJANGO_SETTINGS_MODULE may receive a named constant instead of a literal."""
    root = make_project(tmp_path / "repo")
    write(
        root / "manage.py",
        'import os\nsettings_module = "config.settings.local"\n'
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)\n',
    )
    write(root / "config" / "settings" / "local.py", 'INSTALLED_APPS = ["apps.local"]\n')
    project = _discover(root)
    assert project.settings_module == "config.settings.local"


def test_settings_package_follows_base_import_for_apps(tmp_path: Path) -> None:
    """A settings package resolves its base module without importing it."""
    root = make_project(tmp_path / "repo")
    write(
        root / "manage.py",
        'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")\n',
    )
    write(
        root / "config" / "settings" / "base.py",
        'DJANGO_APPS = ["django.contrib.contenttypes"]\n'
        'LOCAL_APPS = ["apps.orders"]\n'
        "INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS\n",
    )
    write(root / "config" / "settings" / "local.py", "from .base import *\n")
    write(root / "apps" / "orders" / "__init__.py", "")
    project = _discover(root)
    assert project.settings_files == ("config/settings/base.py", "config/settings/local.py")
    assert project.apps == ("apps.orders", "django.contrib.contenttypes")


def test_installed_apps_classification(tmp_path: Path) -> None:
    """Project, third-party and framework apps are distinguished."""
    root = make_project(tmp_path / "repo")
    write(
        root / "config" / "settings.py",
        'INSTALLED_APPS = [\n    "django.contrib.auth",\n    "rest_framework",\n    "apps.orders",\n]\n',
    )
    write(root / "apps" / "orders" / "__init__.py", "")
    project = _discover(root)
    assert project.framework_apps == ("django.contrib.auth",)
    assert project.third_party_apps == ("rest_framework",)
    assert project.local_apps == ("apps.orders",)


def test_apps_are_detected_without_a_local_virtualenv(tmp_path: Path) -> None:
    """Missing local virtualenv never means zero Django applications."""
    root = make_project(tmp_path / "repo", venv=False)
    project = _discover(root)
    assert project.virtualenv is None
    assert project.apps == ("apps.sample", "django.contrib.contenttypes")


def test_branching_manage_py_is_resolved_with_runtime_cwd(tmp_path: Path) -> None:
    """A cwd-driven settings branch is selected from static Compose evidence."""
    root = make_project(tmp_path / "repo", venv=False, compose=False)
    write(
        root / "manage.py",
        "import os\nfrom pathlib import Path\n"
        "current_dir_str = str(Path.cwd())\n"
        'if current_dir_str == "/app":\n'
        '    settings_module = "config.settings.local"\n'
        "else:\n"
        '    settings_module = "config.settings.production"\n'
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)\n',
    )
    write(
        root / "docker-compose.local.yml",
        "services:\n  django:\n    build:\n      context: .\n      dockerfile: ./compose/Dockerfile\n",
    )
    write(root / "compose" / "Dockerfile", "ARG APP_HOME=/app\nWORKDIR ${APP_HOME}\n")
    write(root / "README.md", "docker compose -f docker-compose.local.yml exec -T django pytest\n")
    write(root / "config" / "settings" / "local.py", 'INSTALLED_APPS = ["apps.orders"]\n')
    write(root / "config" / "settings" / "production.py", 'INSTALLED_APPS = ["apps.production"]\n')
    write(root / "apps" / "orders" / "__init__.py", "")
    write(root / "apps" / "production" / "__init__.py", "")
    project = _discover(root)
    assert project.compose_runtime_cwd == "/app"
    assert project.settings_module == "config.settings.local"
    assert project.settings_candidates == (
        "config.settings.local",
        "config.settings.production",
    )
    assert project.local_apps == ("apps.orders",)


def test_multiple_settings_without_runtime_evidence_is_ambiguous(tmp_path: Path) -> None:
    """Multiple settings modules are reported as ambiguous instead of guessed."""
    root = make_project(tmp_path / "repo", venv=False)
    write(
        root / "manage.py",
        "import os\nfrom pathlib import Path\n"
        "current_dir_str = str(Path.cwd())\n"
        'if current_dir_str == "/app":\n'
        '    settings_module = "config.settings.local"\n'
        "else:\n"
        '    settings_module = "config.settings.production"\n'
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)\n',
    )
    write(root / "config" / "settings" / "local.py", 'INSTALLED_APPS = ["apps.orders"]\n')
    write(root / "config" / "settings" / "production.py", 'INSTALLED_APPS = ["apps.production"]\n')
    project = _discover(root)
    assert project.settings_module is None
    assert project.settings_reason is not None
    assert "ambiguous" in project.settings_reason
    assert project.settings_candidates == (
        "config.settings.local",
        "config.settings.production",
    )


def test_discovery_does_not_execute_manage_py(tmp_path: Path) -> None:
    """Static discovery must not run side effects from the target project."""
    root = make_project(tmp_path / "repo")
    marker = root / "executed.txt"
    write(
        root / "manage.py",
        f'open({str(marker)!r}, "w").write("executed")\n'
        'import os\nos.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"\n',
    )
    _discover(root)
    assert marker.exists() is False
