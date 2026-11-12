"""CLI contract tests for the autonomous entrypoint."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agent_loop.autonomy.cli import main
from agent_loop.autonomy.security import git_environment
from tests.agent_loop.autonomy.support import git_executable
from tests.agent_loop.autonomy.support import make_project

_EXIT_USAGE = 2
_EXIT_UNAVAILABLE = 3


def _repo(tmp_path: Path) -> Path:
    return make_project(tmp_path / "repo")


def _repository_run_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "run.py").is_file() and (parent / "agent_loop").is_dir():
            return parent / "run.py"
    message = "run.py could not be located"
    raise RuntimeError(message)


def _commit_all(root: Path) -> None:
    environment = git_environment()
    subprocess.run(  # noqa: S603
        (git_executable(), "add", "-A"),
        cwd=root,
        check=True,
        env=environment,
    )
    subprocess.run(  # noqa: S603
        (git_executable(), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "init"),
        cwd=root,
        check=True,
        env=environment,
    )


def _git_local_identity(root: Path) -> None:
    environment = git_environment()
    subprocess.run(  # noqa: S603
        (git_executable(), "config", "user.name", "Test"),
        cwd=root,
        check=True,
        env=environment,
    )
    subprocess.run(  # noqa: S603
        (git_executable(), "config", "user.email", "test@example.com"),
        cwd=root,
        check=True,
        env=environment,
    )


def _committing_agent(tmp_path: Path) -> str:
    script = tmp_path / "agent.sh"
    script.write_text(
        "#!/bin/sh\nset -eu\nprintf 'agent = 1\\n' > agent_output.py\ngit add agent_output.py\n"
        "git commit -qm 'agent commit'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return shlex.join(("sh", str(script)))


def test_discover_only_json_returns_zero_and_structured_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Discovery-only JSON is machine-readable and succeeds."""
    root = _repo(tmp_path)
    exit_code = main(["--repo", str(root), "--requirement", "Add endpoint", "--discover-only", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"
    assert payload["validation_status"] is None
    assert payload["discovery"]["project"]["is_django"] is True
    assert payload["discovery"]["project"]["manage_py"] == "manage.py"


def test_missing_requirement_is_rejected_by_argparse(tmp_path: Path) -> None:
    """The CLI requires exactly one requirement source."""
    root = _repo(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--repo", str(root), "--discover-only"])
    assert exc.value.code == _EXIT_USAGE


def test_non_git_repository_returns_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A target without a Git work tree is rejected before discovery."""
    plain = tmp_path / "plain"
    plain.mkdir()
    exit_code = main(["--repo", str(plain), "--requirement", "x", "--discover-only"])
    assert exit_code == _EXIT_USAGE
    assert "not a Git work tree" in capsys.readouterr().err


def test_empty_requirement_file_returns_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty requirement file is an explicit usage error."""
    root = _repo(tmp_path)
    requirement = tmp_path / "empty.md"
    requirement.write_text("   \n", encoding="utf-8")
    exit_code = main(["--repo", str(root), "--requirement-file", str(requirement), "--discover-only"])
    assert exit_code == _EXIT_USAGE
    assert "empty" in capsys.readouterr().err


def test_invalid_environment_entry_returns_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Malformed --env entries are rejected deterministically."""
    root = _repo(tmp_path)
    exit_code = main(
        ["--repo", str(root), "--requirement", "x", "--discover-only", "--env", "MISSING_SEPARATOR"],
    )
    assert exit_code == _EXIT_USAGE
    assert "KEY=VALUE" in capsys.readouterr().err


def test_output_file_receives_rendered_report(tmp_path: Path) -> None:
    """--output writes the report instead of only printing it."""
    root = _repo(tmp_path)
    output = tmp_path / "report.md"
    exit_code = main(
        ["--repo", str(root), "--requirement", "x", "--discover-only", "--output", str(output)],
    )
    assert exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "# Agent Loop autonomy report" in text
    assert "Django detected: True" in text


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """--version prints the package version and exits successfully."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_validate_only_skipped_tool_is_not_success(tmp_path: Path) -> None:
    """A skipped validation exits with an unavailable code, never zero."""
    root = _repo(tmp_path)
    exit_code = main(
        ["--repo", str(root), "--requirement", "x", "--validate-only", "--tool", "bandit"],
    )
    assert exit_code == _EXIT_UNAVAILABLE


def test_target_path_variants_are_normalized_or_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Equivalent scopes are accepted while external scopes fail closed."""
    root = _repo(tmp_path)
    exit_code = main(
        [
            "--repo",
            str(root),
            "--requirement",
            "Scoped work",
            "--dry-run",
            "--target-path",
            "./apps/sample",
            "--target-path",
            "apps/sample/",
            "--target-path",
            str(root / "apps" / "sample"),
        ],
    )
    assert exit_code == 0
    capsys.readouterr()
    outside = tmp_path / "outside"
    outside.mkdir()
    exit_code = main(
        [
            "--repo",
            str(root),
            "--requirement",
            "Scoped work",
            "--dry-run",
            "--target-path",
            str(outside),
        ],
    )
    assert exit_code == _EXIT_USAGE
    assert "escapes" in capsys.readouterr().err


def test_standalone_cli_reports_agent_commit_as_undetermined(tmp_path: Path) -> None:
    """A committed change cannot disappear because the working tree is clean."""
    root = _repo(tmp_path)
    _commit_all(root)
    _git_local_identity(root)
    agent_command = _committing_agent(tmp_path)
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(_repository_run_path()),
            "--repo",
            str(root),
            "--requirement",
            "Add health check",
            "--agent-command",
            agent_command,
            "--tool",
            "git",
            "--json",
        ),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == _EXIT_UNAVAILABLE
    assert payload["status"] == "unknown"
    assert payload["validation_status"] == "pass"
    assert payload["final_git"]["changed_files"] == []
    assert payload["git_audit"]["head_changed"] is True
    assert "agent_output.py" in payload["git_audit"]["commit_paths"]
    assert payload["git_audit"]["execution_changes"] == []
    committed = next(change for change in payload["git_audit"]["changes"] if change["path"] == "agent_output.py")
    assert committed["attribution"] == "undetermined"
    assert payload["git_audit"]["warnings"]
