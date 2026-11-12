"""Independent-tree E2E: AgentLoop runs without the Shipii checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_loop.autonomy.security import git_environment

REPO_ROOT = Path(__file__).resolve().parents[3]
_COPY_IGNORES = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def _copy_product(tmp_path: Path) -> Path:
    """Copy the installable runtime tree to an isolated directory."""
    copy = tmp_path / "agent-loop-independent"
    shutil.copytree(REPO_ROOT / "agent_loop", copy / "agent_loop", ignore=_COPY_IGNORES)
    shutil.copy2(REPO_ROOT / "run.py", copy / "run.py")
    return copy


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    """Build an environment with no Shipii or AgentLoop host configuration."""
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(tmp_path / "tmp"),
    }
    (tmp_path / "tmp").mkdir()
    return environment


def _run(
    copy: Path,
    environment: dict[str, str],
    *args: str,
    module: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the copied product, never the source checkout."""
    command = [sys.executable, "-m", "agent_loop.autonomy", *args] if module else [sys.executable, "run.py", *args]
    return subprocess.run(  # noqa: S603 - fixed interpreter and argv
        command,
        cwd=copy,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_target_repository(root: Path) -> None:
    """Create a self-contained Django-shaped target with a local tool venv."""
    root.mkdir()
    (root / "manage.py").write_text(
        'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n',
        encoding="utf-8",
    )
    (root / "config").mkdir()
    (root / "config" / "settings.py").write_text('INSTALLED_APPS = ["apps.sample"]\n', encoding="utf-8")
    (root / "requirements.txt").write_text("Django==5.0.1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='target'\nrequires-python='>=3.12'\n"
        "[tool.ruff]\nline-length=100\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("Target repository instruction: keep changes small.\n", encoding="utf-8")
    (root / "apps" / "sample").mkdir(parents=True)
    (root / "apps" / "sample" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    venv = root / ".venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {sys.base_prefix}\n", encoding="utf-8")
    (bin_dir / "python").symlink_to(sys.executable)
    git = shutil.which("git")
    assert git is not None
    environment = git_environment()
    subprocess.run(  # noqa: S603 - resolved git executable
        (git, "init", "-q"),
        cwd=root,
        check=True,
        capture_output=True,
        env=environment,
    )
    subprocess.run(  # noqa: S603 - resolved git executable
        (git, "-c", "user.name=Test", "-c", "user.email=test@example.com", "add", "-A"),
        cwd=root,
        check=True,
        capture_output=True,
        env=environment,
    )
    subprocess.run(  # noqa: S603 - resolved git executable
        (git, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"),
        cwd=root,
        check=True,
        capture_output=True,
        env=environment,
    )


def _assert_no_developer_path(payload: str, copy: Path) -> None:
    """The run must not depend on the source checkout or its absolute path."""
    assert str(REPO_ROOT) not in payload
    assert str(copy) not in payload


def test_copied_tree_runs_without_shipii_checkout(tmp_path: Path) -> None:
    """Discovery, dry-run, validation, version and help run from an isolated copy."""
    copy = _copy_product(tmp_path)
    environment = _isolated_environment(tmp_path)
    target = tmp_path / "target"
    _write_target_repository(target)

    import_probe = subprocess.run(
        [sys.executable, "-c", "import agent_loop; print(agent_loop.__file__)"],
        cwd=copy,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_probe.returncode == 0, import_probe.stderr
    assert Path(import_probe.stdout.strip()).resolve() == (copy / "agent_loop" / "__init__.py").resolve()

    version = _run(copy, environment, "--version")
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == "0.1.0"

    module_version = _run(copy, environment, "--version", module=True)
    assert module_version.returncode == 0, module_version.stderr
    assert module_version.stdout.strip() == "0.1.0"

    help_result = _run(copy, environment, "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--requirement-file" in help_result.stdout
    assert "--validate-only" in help_result.stdout

    discover = _run(
        copy,
        environment,
        "--repo",
        str(target),
        "--requirement",
        "Add a check",
        "--discover-only",
        "--json",
    )
    assert discover.returncode == 0, discover.stderr
    discovery_payload = json.loads(discover.stdout)
    assert discovery_payload["discovery"]["project"]["is_django"] is True
    assert discovery_payload["requirement_source"]["origin"] == "inline"
    _assert_no_developer_path(discover.stdout + discover.stderr, copy)

    dry_run = _run(
        copy,
        environment,
        "--repo",
        str(target),
        "--requirement",
        "Add a check",
        "--dry-run",
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Target repository instruction: keep changes small." in dry_run.stdout
    _assert_no_developer_path(dry_run.stdout + dry_run.stderr, copy)

    validate = _run(
        copy,
        environment,
        "--repo",
        str(target),
        "--requirement",
        "Add a check",
        "--validate-only",
        "--tool",
        "git",
        "--json",
    )
    assert validate.returncode == 0, validate.stderr
    validation_payload = json.loads(validate.stdout)
    assert validation_payload["status"] == "pass"
    result = validation_payload["validation_reports"][0]["results"][0]
    assert result["status"] == "pass"
    assert result["invocation"]["command"][1] == "status"
    assert result["invocation"]["command"][0].endswith("git")
    _assert_no_developer_path(validate.stdout + validate.stderr, copy)
