"""Standalone extraction layout and independence tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.agent_loop.autonomy.support import git_executable

REPO_ROOT = Path(__file__).resolve().parents[3]
STANDALONE = REPO_ROOT / "standalone"
CANONICAL = REPO_ROOT / "agent_loop"
CANONICAL_TESTS = REPO_ROOT / "tests" / "agent_loop" / "autonomy"
_SUPPORT_IMPORT_PREFIX = "from tests.agent_loop.autonomy.support import "
_STANDALONE_SUPPORT_IMPORT_PREFIX = "from support import "
_EXCLUDED_TESTS = frozenset({"test_standalone_layout.py"})
_STANDALONE_OWNED_CHECKS = frozenset({"check_examples.py"})


def test_standalone_layout_contains_entrypoint_docs_and_package() -> None:
    """The extraction folder carries everything needed to run independently."""
    assert (STANDALONE / "pyproject.toml").is_file()
    assert (STANDALONE / "AGENTS.md").is_file()
    assert (STANDALONE / "README.md").is_file()
    assert (STANDALONE / "VENDORING.md").is_file()
    assert (STANDALONE / "run.py").is_file()
    assert (STANDALONE / "requirements.txt").is_file()
    assert (STANDALONE / "agent_loop" / "autonomy" / "cli.py").is_file()
    assert (STANDALONE / "checks").is_dir()
    assert (STANDALONE / "fixtures" / "project_a" / "manage.py").is_file()
    assert (STANDALONE / "fixtures" / "project_b" / "manage.py").is_file()


def test_vendoring_contract_documents_source_snapshot_checks_and_drift() -> None:
    """The vendoring contract is executable documentation, not a stub file."""
    text = (STANDALONE / "VENDORING.md").read_text(encoding="utf-8")
    assert "agent_loop/" in text
    assert "standalone/agent_loop/" in text
    assert "standalone/checks/" in text
    assert "test_standalone_vendored_core_matches_canonical_python_source" in text
    assert "test_standalone_checks_match_canonical_test_transform" in text
    assert "check_examples.py" in text
    assert "Drift detection" in text
    assert "README.md" in (STANDALONE / "AGENTS.md").read_text(encoding="utf-8")
    assert "VENDORING.md" in (STANDALONE / "README.md").read_text(encoding="utf-8")


def test_standalone_pyproject_exposes_console_entrypoint() -> None:
    """The standalone project declares its installable entry point."""
    text = (STANDALONE / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in text
    assert 'agent-loop = "agent_loop.autonomy.cli:main"' in text
    assert 'requires-python = ">=3.12"' in text


def test_standalone_vendored_core_matches_canonical_python_source() -> None:
    """The vendored copy cannot silently drift from the canonical source."""
    canonical_files = {path.relative_to(CANONICAL) for path in CANONICAL.rglob("*.py")}
    vendored_root = STANDALONE / "agent_loop"
    vendored_files = {path.relative_to(vendored_root) for path in vendored_root.rglob("*.py")}
    assert canonical_files
    assert vendored_files == canonical_files
    for relative in sorted(canonical_files):
        assert (vendored_root / relative).read_bytes() == (CANONICAL / relative).read_bytes(), (
            f"vendored drift: {relative}"
        )


def test_standalone_checks_match_canonical_test_transform() -> None:
    """Every standalone check is the documented transform of one canonical test."""
    _assert_checks_synchronized(STANDALONE)


def test_standalone_checks_sync_gate_detects_drift(tmp_path: Path) -> None:
    """A mutated standalone check is rejected by the synchronization gate."""
    candidate = tmp_path / "standalone"
    shutil.copytree(STANDALONE / "checks", candidate / "checks")
    target = candidate / "checks" / "check_git.py"
    target.write_text(f"{target.read_text(encoding='utf-8')}\n# unsynchronized drift\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="drift"):
        _assert_checks_synchronized(candidate)


def test_standalone_sources_have_no_shipii_imports_or_absolute_paths() -> None:
    """No vendored module depends on Shipii or a developer-specific path."""
    for path in (STANDALONE / "agent_loop").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "from scripts" not in text
        assert "import scripts" not in text
        assert "from apps" not in text
        assert "import apps" not in text
        assert "from django" not in text
        assert "import django" not in text
        assert "/home/javier" not in text
    for path in (STANDALONE / "checks").glob("check_*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "/home/javier" not in text


def test_standalone_checks_use_non_collected_names() -> None:
    """Standalone checks do not collide with the Shipii root pytest collection."""
    check_files = sorted(path.name for path in (STANDALONE / "checks").glob("check_*.py"))
    assert "check_cli.py" in check_files
    assert "check_examples.py" in check_files
    assert not list((STANDALONE / "checks").glob("test_*.py"))


def _assert_checks_synchronized(standalone: Path) -> None:
    checks = standalone / "checks"
    expected = {
        f"check_{path.stem.removeprefix('test_')}.py": path
        for path in CANONICAL_TESTS.glob("test_*.py")
        if path.name not in _EXCLUDED_TESTS
    }
    expected_files = set(expected) | {"support.py"} | _STANDALONE_OWNED_CHECKS
    actual_files = {path.name for path in checks.glob("*.py")}
    assert actual_files == expected_files, f"standalone check file set drift: {actual_files ^ expected_files}"
    assert (checks / "support.py").read_bytes() == (CANONICAL_TESTS / "support.py").read_bytes(), (
        "standalone support.py drift"
    )
    for name, canonical in sorted(expected.items()):
        expected_source = _normalize_check_source(canonical.read_text(encoding="utf-8"))
        actual_source = _normalize_check_source((checks / name).read_text(encoding="utf-8"))
        assert actual_source == expected_source, f"standalone checks drift: {name}"


def _normalize_check_source(text: str) -> str:
    imports: list[str] = []
    body: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith(("import ", "from ")):
            imports.append(line.replace(_SUPPORT_IMPORT_PREFIX, _STANDALONE_SUPPORT_IMPORT_PREFIX))
        else:
            body.append(line)
    return "\n".join([*sorted(imports), *body]).strip()


def _write_target_repository(root: Path) -> None:
    root.mkdir(parents=True)
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
    (venv / "pyvenv.cfg").write_text(f"home = {Path(sys.executable).parent}\n", encoding="utf-8")
    (bin_dir / "python").symlink_to(sys.executable)
    (bin_dir / "ruff").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "ruff").chmod(0o755)
    (bin_dir / "pytest").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pytest "$@"\n', encoding="utf-8")
    (bin_dir / "pytest").chmod(0o755)
    subprocess.run((git_executable(), "init", "-q"), cwd=root, check=True, capture_output=True)  # noqa: S603


def _standalone_env() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "DJANGO_SETTINGS_MODULE"}
    }


def _run_standalone(copy: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "run.py", *args),
        cwd=copy,
        env=_standalone_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_extracted_standalone_runs_without_the_shipii_checkout(tmp_path: Path) -> None:
    """A copied standalone tree discovers and validates an external repository."""
    copy = tmp_path / "agent-loop-standalone"
    shutil.copytree(STANDALONE, copy, ignore=shutil.ignore_patterns("__pycache__", ".mypy_cache", ".pytest_cache"))
    target = tmp_path / "target"
    _write_target_repository(target)
    discover = _run_standalone(
        copy,
        "--repo",
        str(target),
        "--requirement",
        "Add a check",
        "--discover-only",
        "--json",
    )
    assert discover.returncode == 0, discover.stderr
    payload = json.loads(discover.stdout)
    assert payload["discovery"]["project"]["is_django"] is True
    assert payload["requirement_source"]["origin"] == "inline"
    virtualenv = payload["discovery"]["project"]["virtualenv"]
    assert virtualenv == ".venv"
    dry_run = _run_standalone(copy, "--repo", str(target), "--requirement", "Add a check", "--dry-run")
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Target repository instruction: keep changes small." in dry_run.stdout
    validate = _run_standalone(
        copy,
        "--repo",
        str(target),
        "--requirement",
        "Add a check",
        "--validate-only",
        "--tool",
        "pytest",
        "--json",
    )
    assert validate.returncode == 0, validate.stderr
    result = json.loads(validate.stdout)
    assert result["status"] == "pass"
    command = result["validation_reports"][0]["results"][0]["invocation"]["command"]
    assert command[0] == str(target / ".venv" / "bin" / "pytest")
    assert str(REPO_ROOT) not in json.dumps(result)
    version = _run_standalone(copy, "--version")
    assert version.returncode == 0
    assert version.stdout.strip() == "0.1.0"
    help_result = _run_standalone(copy, "--help")
    assert help_result.returncode == 0
    assert "--requirement-file" in help_result.stdout
