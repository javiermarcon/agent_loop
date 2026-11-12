"""Regression tests for the complete, repository-wide quality runner."""

from __future__ import annotations

import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts import quality_runtime
from scripts import run_quality_checks
from scripts.quality_runtime import CommandResult
from scripts.quality_runtime import GateResult
from scripts.quality_runtime import GateSpec
from scripts.quality_runtime import GateStatus
from scripts.quality_runtime import discover_python_files
from scripts.quality_runtime import discover_repository_files
from scripts.quality_runtime import format_counts
from scripts.quality_runtime import parse_behave_counts
from scripts.quality_runtime import parse_coverage_percent
from scripts.quality_runtime import parse_heavy_request_report
from scripts.quality_runtime import parse_pytest_counts
from scripts.quality_runtime import pre_commit_reported_skip
from scripts.quality_runtime import run_command

_HEAVY_REPORT = (
    "HEAVY REQUEST GUARD: scope=pytest initialized=yes activated=12 observed=40 "
    "violations=0 authorized=2 expected=1 errors=0\n"
    "HEAVY REQUEST ALLOWED: pytest.mark.heavy_request (test_example)\n"
)
_PYTEST_OUTPUT = _HEAVY_REPORT + "1 passed in 0.1s\nTOTAL 100 0 100%\n"
_BEHAVE_OUTPUT = _HEAVY_REPORT + "3 scenarios passed, 0 failed, 0 skipped\n"


def _plan(tmp_path: Path) -> tuple[GateSpec, ...]:
    (tmp_path / "agent_loop").mkdir()
    (tmp_path / "agent_loop" / "module.py").write_text("x = 1\n", encoding="utf-8")
    return run_quality_checks.build_gate_plan(python="/usr/bin/python3", repo_root=tmp_path)


def test_plan_covers_the_complete_repository_not_changed_files(tmp_path: Path) -> None:
    """Every integral control is present and no gate filters by Git state."""
    gates = _plan(tmp_path)
    by_name = {gate.name: gate for gate in gates}
    assert "ruff-check" in by_name
    assert "ruff-format-check" in by_name
    assert "mypy" in by_name
    assert "pylint" in by_name
    assert "architecture-boundaries" in by_name
    assert "pytest-full" in by_name
    assert "behave-e2e" in by_name
    assert "package-build-install" in by_name
    for gate in gates:
        if gate.kind != "command":
            continue
        argv = gate.argv
        assert "--all-files" not in argv
        assert "status" not in argv
        if argv[0] == "git":
            assert argv[1] == "diff"
            assert "--check" in argv
    pytest_argv = by_name["pytest-full"].argv
    assert "--cov=agent_loop" in pytest_argv
    assert "--cov-fail-under=99" in pytest_argv
    assert "tests/agent_loop" in pytest_argv
    assert by_name["pytest-full"].env["COVERAGE_FILE"].startswith(tempfile.gettempdir())
    assert by_name["behave-e2e"].argv[-1] == "features"
    assert by_name["mypy"].argv[-7:] == (
        "agent_loop",
        "tests",
        "scripts",
        "features/steps",
        "features/environment.py",
        "conftest.py",
        "run.py",
    )
    assert all(gate.timeout_seconds is None or gate.timeout_seconds > 0 for gate in gates)


def test_filesystem_discovery_includes_untracked_files_and_excludes_caches(tmp_path: Path) -> None:
    """New/untracked files are analyzed; generated directories are not."""
    (tmp_path / "agent_loop").mkdir()
    (tmp_path / "agent_loop" / "new.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "cache.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "package.egg-info").mkdir()
    (tmp_path / "package.egg-info" / "PKG-INFO").write_text("x\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    (tmp_path / ".coverage.host").write_text("generated\n", encoding="utf-8")
    assert "agent_loop/new.py" in discover_repository_files(tmp_path)
    assert "README.md" in discover_repository_files(tmp_path)
    assert "agent_loop/new.py" in discover_python_files(tmp_path)
    assert all(".venv" not in path for path in discover_repository_files(tmp_path))
    assert all(".pytest_cache" not in path for path in discover_repository_files(tmp_path))
    assert all("egg-info" not in path for path in discover_repository_files(tmp_path))
    assert ".coverage.host" not in discover_repository_files(tmp_path)


def test_filesystem_discovery_does_not_follow_escaping_symlinks(tmp_path: Path) -> None:
    """An in-repository symlink to an outside file is not inspected."""
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "inside.py").write_text("x = 1\n", encoding="utf-8")
    (repository / "escape.py").symlink_to(outside)
    discovered = discover_repository_files(repository)
    assert "inside.py" in discovered
    assert "escape.py" not in discovered


def test_pytest_and_behave_gates_are_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner never substitutes focused or related tests for full suites."""
    calls: list[tuple[str, ...]] = []

    def _run(argv: tuple[str, ...], *, cwd: Path, env: object = None, timeout: object = None) -> CommandResult:
        del cwd, env, timeout
        calls.append(tuple(argv))
        return CommandResult(argv=tuple(argv), exit_code=0, output=_PYTEST_OUTPUT, duration_seconds=0.1)

    monkeypatch.setattr(run_quality_checks, "run_command", _run)
    gate = GateSpec(name="pytest-full", scope="tests", argv=("/usr/bin/python3", "-m", "pytest"))
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="/usr/bin/python3")
    assert result.status is GateStatus.PASS
    assert calls == [("/usr/bin/python3", "-m", "pytest")]


def test_execute_gate_classifies_success_failure_and_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit codes become PASS/FAIL; a missing executable is NOT EXECUTED."""
    gate = GateSpec(name="probe", scope="scope", argv=("probe",))

    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("probe",), exit_code=0, output="", duration_seconds=0.2),
    )
    assert run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python").status is GateStatus.PASS

    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("probe",), exit_code=2, output="", duration_seconds=0.2),
    )
    failed = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert failed.status is GateStatus.FAIL
    assert "exit code 2" in failed.reason

    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("probe",),
            exit_code=None,
            output="",
            duration_seconds=0.0,
            error="executable not found on PATH: probe",
        ),
    )
    missing = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert missing.status is GateStatus.NOT_EXECUTED
    assert "executable not found" in missing.reason

    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("probe",),
            exit_code=None,
            output="",
            duration_seconds=0.0,
            error="permission denied",
        ),
    )
    broken = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert broken.status is GateStatus.ERROR


@pytest.mark.parametrize("hook", ["check-xml", "check-json"])
def test_pre_commit_hook_without_applicable_files_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    hook: str,
) -> None:
    """A hook that did not run is SKIPPED, never PASS."""
    gate = GateSpec(name=f"pre-commit:{hook}", scope="all repository files", argv=("pre-commit", "run", hook))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=gate.argv,
            exit_code=0,
            output=f"{hook.replace('-', ' ')}........................(no files to check)Skipped\n",
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.SKIPPED
    assert "no applicable files" in result.reason
    assert pre_commit_reported_skip("check xml....(no files to check)Skipped") is True


def test_conditional_required_skip_is_visible_and_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A required hook with no matching files is SKIPPED with its condition."""
    gate = GateSpec(name="pre-commit:check-xml", scope="all", argv=("pre-commit", "run", "check-xml"))
    monkeypatch.setattr(run_quality_checks, "build_gate_plan", lambda *, python, repo_root: (gate,))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=gate.argv,
            exit_code=0,
            output="check xml...(no files to check)Skipped\n",
            duration_seconds=0.1,
        ),
    )
    assert run_quality_checks.main(["--repo-root", str(Path.cwd())]) == 0
    captured = capsys.readouterr()
    assert "[SKIPPED] pre-commit:check-xml" in captured.out
    assert "no applicable files" in captured.out
    assert "condition: not applicable" in captured.out
    assert "Overall: PASS" in captured.out


def test_required_non_conditional_skip_blocks_the_global_result() -> None:
    """A required gate omitted for any other reason still blocks."""
    result = GateResult(
        name="mypy",
        scope="all",
        status=GateStatus.SKIPPED,
        duration_seconds=0.0,
        reason="excluded by --only",
        required=True,
    )
    assert run_quality_checks.is_blocking_result(result) is True
    conditional = GateResult(
        name="pre-commit:check-xml",
        scope="all",
        status=GateStatus.SKIPPED,
        duration_seconds=0.0,
        reason="conditional: no applicable files",
        required=True,
        conditional=True,
    )
    assert run_quality_checks.is_blocking_result(conditional) is False


def test_missing_heavy_request_report_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence from the guard is not evidence of a clean run."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("pytest",),
            exit_code=0,
            output="1 passed in 0.1s\nTOTAL 100 0 100%\n",
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "no report" in result.reason


def test_pytest_heavy_request_guard_requires_detection_and_authorization_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean pytest report must still prove detection and authorization ran."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))

    def _install(output: str) -> None:
        monkeypatch.setattr(
            run_quality_checks,
            "run_command",
            lambda *args, **kwargs: CommandResult(
                argv=("pytest",),
                exit_code=0,
                output=output,
                duration_seconds=0.1,
            ),
        )

    base = "HEAVY REQUEST GUARD: scope=pytest initialized=yes activated=1 observed=3 "
    _install(
        base + "violations=0 authorized=1 expected=0 errors=0\n"
        "HEAVY REQUEST ALLOWED: pytest.mark.heavy_request\n"
        "1 passed in 0.1s\nTOTAL 100 0 100%\n",
    )
    no_detection = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert no_detection.status is GateStatus.ERROR
    assert "positive detection" in no_detection.reason

    _install(
        base + "violations=0 authorized=0 expected=1 errors=0\n1 passed in 0.1s\nTOTAL 100 0 100%\n",
    )
    no_authorization = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert no_authorization.status is GateStatus.ERROR
    assert "authorization evidence" in no_authorization.reason


def test_heavy_request_guard_without_activation_or_events_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialization alone is insufficient; activation and observation must be shown."""
    gate = GateSpec(name="behave-e2e", scope="features", argv=("behave",))
    output = (
        "HEAVY REQUEST GUARD: scope=behave initialized=yes activated=0 observed=0 "
        "violations=0 authorized=0 expected=0 errors=0\n"
        "3 scenarios passed, 0 failed, 0 skipped\n"
    )
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("behave",), exit_code=0, output=output, duration_seconds=0.1),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "never activated" in result.reason


def test_heavy_request_guard_authorization_is_reported_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorized exceptions are traceable and do not fail a clean gate."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("pytest",),
            exit_code=0,
            output=_PYTEST_OUTPUT,
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.PASS
    summary = run_quality_checks.render_summary((result,))
    assert "heavy requests: initialized=yes activated=12 observed=40 violations=0 authorized=2" in summary
    assert "HEAVY REQUEST ALLOWED: pytest.mark.heavy_request" in summary


def test_zero_exit_code_with_failed_counts_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contradictory zero exit code cannot hide failed pytest/Behave counts."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("pytest",),
            exit_code=0,
            output=_HEAVY_REPORT + "1 failed, 0 passed in 0.1s\nTOTAL 100 0 100%\n",
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "failed/error tests" in result.reason

    behave_gate = GateSpec(name="behave-e2e", scope="features", argv=("behave",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("behave",),
            exit_code=0,
            output=_HEAVY_REPORT + "1 scenario passed, 1 failed, 0 skipped\n",
            duration_seconds=0.1,
        ),
    )
    behave_result = run_quality_checks.execute_gate(behave_gate, repo_root=Path.cwd(), python="python")
    assert behave_result.status is GateStatus.ERROR
    assert "failed scenarios" in behave_result.reason


def test_success_below_coverage_threshold_is_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero exit code that reports coverage below 99% is a failed gate."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("pytest",),
            exit_code=0,
            output=_HEAVY_REPORT + "1 passed in 0.1s\nTOTAL 100 1 98.50%\n",
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.FAIL
    assert "98.50%" in result.reason


def test_unparseable_successful_tool_output_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero exit code with no parseable evidence is ERROR, not PASS."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("pytest",), exit_code=0, output="", duration_seconds=0.1),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "could not parse pytest" in result.reason


def test_run_command_times_out_with_partial_evidence(tmp_path: Path) -> None:
    """A hanging command is killed, marked timed_out and keeps its output."""
    result = run_command(
        (sys.executable, "-c", "import time; print('started', flush=True); time.sleep(5)"),
        cwd=tmp_path,
        stream=False,
        timeout=0.2,
    )
    assert result.timed_out is True
    assert result.error is not None
    assert "timed out" in result.error
    assert "started" in result.output


def test_run_command_handles_paths_with_spaces_and_non_ascii(tmp_path: Path) -> None:
    """The runner passes argv directly and never reparses paths through a shell."""
    directory = tmp_path / "scripts with spaces" / "ñandú"
    directory.mkdir(parents=True)
    executable = directory / "tool name.sh"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\n', encoding="utf-8")
    executable.chmod(0o755)
    result = run_command((str(executable), "valor con espacios"), cwd=tmp_path, stream=False)
    assert result.exit_code == 0
    assert "valor con espacios" in result.output


def test_run_command_rejects_empty_and_unstartable_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty argv and OS start failures are explicit errors, never silent passes."""
    empty = run_command((), cwd=tmp_path, stream=False)
    assert empty.error == "empty command"
    assert empty.exit_code is None

    def _explode(*args: object, **kwargs: object) -> object:
        del args, kwargs
        message = "permission denied"
        raise OSError(message)

    monkeypatch.setattr(quality_runtime.subprocess, "Popen", _explode)
    failed = run_command((sys.executable, "-c", "pass"), cwd=tmp_path, stream=False)
    assert failed.exit_code is None
    assert failed.error == "permission denied"


def test_timed_out_gate_is_error_not_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted control cannot be classified as PASS or NOT EXECUTED."""
    gate = GateSpec(name="probe", scope="scope", argv=("probe",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("probe",),
            exit_code=-9,
            output="partial",
            duration_seconds=0.2,
            error="timed out after 0.2s",
            timed_out=True,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "timed out" in result.reason
    assert result.exit_code == -9


def test_unexpected_execution_failure_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken gate mechanism is ERROR, not a hidden success."""

    def _explode(*args: object, **kwargs: object) -> CommandResult:
        del args, kwargs
        message = "runner exploded"
        raise RuntimeError(message)

    monkeypatch.setattr(run_quality_checks, "run_command", _explode)
    gate = GateSpec(name="ruff-check", scope=".", argv=("ruff", "check", "."))
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.ERROR
    assert "runner exploded" in result.reason


def test_parse_heavy_request_report_keeps_reasons() -> None:
    """The parser preserves initialization, counts and explicit exceptions."""
    report = parse_heavy_request_report(_HEAVY_REPORT)
    assert report is not None
    assert report.initialized is True
    assert report.activated == 12
    assert report.observed == 40
    assert report.expected == 1
    assert report.violations == ()
    assert report.authorized
    assert "pytest.mark.heavy_request" in report.authorized[0]
    assert parse_heavy_request_report("no guard evidence") is None


def test_parse_heavy_request_report_preserves_unparsed_declared_counts() -> None:
    """Declared violations, authorizations and errors without detail stay visible."""
    report = parse_heavy_request_report(
        "HEAVY REQUEST GUARD: scope=pytest initialized=yes activated=1 observed=2 "
        "violations=2 authorized=3 expected=1 errors=1\n",
    )
    assert report is not None
    assert len(report.violations) == 1
    assert len(report.authorized) == 1
    assert len(report.errors) == 1
    assert "details were not parsed" in report.violations[0]
    assert "details were not parsed" in report.authorized[0]
    assert "details were not parsed" in report.errors[0]


def test_execute_gate_fails_when_heavy_request_marker_is_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero exit code cannot hide a heavy-request violation."""
    gate = GateSpec(name="pytest-full", scope="tests", argv=("pytest",))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(
            argv=("pytest",),
            exit_code=0,
            output=(
                "HEAVY REQUEST GUARD: scope=pytest initialized=yes activated=1 observed=3 "
                "violations=1 authorized=0 expected=0 errors=0\n"
                "HEAVY REQUEST VIOLATION: command - heavy executable: docker\n"
                "1 passed in 0.1s\nTOTAL 100 0 100%\n"
            ),
            duration_seconds=0.1,
        ),
    )
    result = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python")
    assert result.status is GateStatus.FAIL
    assert "heavy request" in result.reason
    assert result.metrics["heavy_requests"]


def test_execute_package_gate_reports_success_and_failure() -> None:
    """Packaging errors are FAIL results with their real cause."""
    gate = GateSpec(name="package-build-install", scope="dist", kind="package")

    def _success(*, python: str, repo_root: Path) -> None:
        del python, repo_root

    passed = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python", package_runner=_success)
    assert passed.status is GateStatus.PASS

    def _failure(*, python: str, repo_root: Path) -> None:
        del python, repo_root
        msg = "LICENSE missing"
        raise run_quality_checks.QualityCheckError(msg)

    failed = run_quality_checks.execute_gate(gate, repo_root=Path.cwd(), python="python", package_runner=_failure)
    assert failed.status is GateStatus.FAIL
    assert "LICENSE missing" in failed.reason


def test_summary_distinguishes_every_status_and_reports_metrics() -> None:
    """The summary includes scope, duration, reasons, coverage and counts."""
    results = (
        GateResult(
            name="ruff-check",
            scope=".",
            status=GateStatus.PASS,
            duration_seconds=0.5,
            reason="ok",
            required=True,
            command="ruff check .",
            exit_code=0,
        ),
        GateResult(
            name="pytest-full",
            scope="tests",
            status=GateStatus.FAIL,
            duration_seconds=1.5,
            reason="exit code 1",
            required=True,
            command="python -m pytest tests/agent_loop",
            exit_code=1,
            metrics={
                "pytest": parse_pytest_counts("1 failed, 2 passed, 1 skipped in 0.2s"),
                "coverage": 98.2,
            },
        ),
        GateResult(
            name="behave-e2e",
            scope="features",
            status=GateStatus.SKIPPED,
            duration_seconds=0.0,
            reason="excluded by --only",
            required=False,
        ),
        GateResult(
            name="mypy",
            scope="all",
            status=GateStatus.NOT_EXECUTED,
            duration_seconds=0.0,
            reason="executable not found",
            required=True,
        ),
    )
    summary = run_quality_checks.render_summary(results)
    assert "[PASS] ruff-check" in summary
    assert "[FAIL] pytest-full" in summary
    assert "[SKIPPED] behave-e2e" in summary
    assert "[NOT EXECUTED] mypy" in summary
    assert "coverage: 98.20% (threshold 99%)" in summary
    assert "pytest: 2 passed, 1 failed, 1 skipped, 0 errors" in summary
    assert "command: ruff check ." in summary
    assert "exit code: 0" in summary
    assert "command: python -m pytest tests/agent_loop" in summary
    assert "exit code: 1" in summary
    assert "Not executed gates: mypy" in summary
    assert "Overall: FAIL" in summary


def test_main_fails_when_a_required_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A required failure returns non-zero and never prints the success line."""
    gate = GateSpec(name="probe", scope="scope", argv=("probe",))
    monkeypatch.setattr(run_quality_checks, "build_gate_plan", lambda *, python, repo_root: (gate,))
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("probe",), exit_code=1, output="boom", duration_seconds=0.1),
    )
    assert run_quality_checks.main(["--repo-root", str(Path.cwd())]) == 1
    captured = capsys.readouterr()
    assert "Overall: FAIL" in captured.out
    assert "all required gates passed" not in captured.out


def test_main_fails_when_a_required_gate_cannot_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing mandatory tool is NOT EXECUTED and fails the run."""
    gate = GateSpec(name="probe", scope="scope", argv=("definitely-missing-executable",))
    monkeypatch.setattr(run_quality_checks, "build_gate_plan", lambda *, python, repo_root: (gate,))
    assert run_quality_checks.main(["--repo-root", str(Path.cwd())]) == 1


def test_main_optional_package_skip_is_reported_as_optional(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only an explicit --skip-package may omit packaging, and it stays visible."""
    gate = GateSpec(name="package-build-install", scope="dist", kind="package")
    monkeypatch.setattr(run_quality_checks, "build_gate_plan", lambda *, python, repo_root: (gate,))
    assert run_quality_checks.main(["--repo-root", str(Path.cwd()), "--skip-package"]) == 0
    captured = capsys.readouterr()
    assert "[SKIPPED] package-build-install" in captured.out
    assert "gate: optional" in captured.out


def test_main_only_selection_marks_omitted_required_gates_as_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """--only is diagnostic; omitted required gates are visible and block success."""
    gates = (
        GateSpec(name="one", scope="scope", argv=("one",)),
        GateSpec(name="two", scope="scope", argv=("two",)),
    )
    monkeypatch.setattr(run_quality_checks, "build_gate_plan", lambda *, python, repo_root: gates)
    monkeypatch.setattr(
        run_quality_checks,
        "run_command",
        lambda *args, **kwargs: CommandResult(argv=("one",), exit_code=0, output="", duration_seconds=0.1),
    )
    assert run_quality_checks.main(["--repo-root", str(Path.cwd()), "--only", "one"]) == 1


def test_parsers_extract_counts_and_coverage() -> None:
    """Tool output parsing is stable and never invents a successful count."""
    pytest_counts = parse_pytest_counts("1260 passed, 1 failed, 2 skipped, 1 error in 4.2s")
    assert pytest_counts.passed == 1260
    assert pytest_counts.failed == 1
    assert pytest_counts.skipped == 2
    assert pytest_counts.errors >= 1
    behave_counts = parse_behave_counts("17 scenarios passed, 2 failed, 1 skipped\n93 steps passed")
    assert behave_counts.passed == 17
    assert behave_counts.failed == 2
    assert behave_counts.skipped == 1
    assert parse_coverage_percent("TOTAL 8000 10 99.88%\nRequired test coverage of 99.0% reached.") == 99.88
    assert parse_coverage_percent("no coverage output") is None
    assert parse_coverage_percent("TOTAL 8000 10\n") is None
    assert format_counts("pytest", pytest_counts).startswith("pytest: 1260 passed")
    assert parse_pytest_counts("captured output says 999 passed\n").total == 0
    assert parse_pytest_counts("1 passed in\n").total == 0
    assert (
        parse_behave_counts(
            "21 scenarios passed, 0 failed, 0 skipped\n1 scenario passed, 0 failed, 0 skipped\n"
        ).passed
        == 1
    )
    assert parse_behave_counts("no summary\n").total == 0


def test_archive_contains_license_accepts_wheel_and_sdist(tmp_path: Path) -> None:
    """Both distribution formats must expose a LICENSE member."""
    wheel = tmp_path / "package-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package-1.0.dist-info/licenses/LICENSE", "MIT")
    sdist = tmp_path / "package-1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        license_file = tmp_path / "LICENSE"
        license_file.write_text("MIT", encoding="utf-8")
        archive.add(license_file, arcname="package-1.0/LICENSE")

    assert run_quality_checks.archive_contains_license(wheel) is True
    assert run_quality_checks.archive_contains_license(sdist) is True
    assert run_quality_checks.archive_contains_license(tmp_path / "unknown.bin") is False


def test_archive_missing_members_accepts_prefixed_sdist_layout(tmp_path: Path) -> None:
    """Required paths may be at the archive root or below the sdist prefix."""
    wheel = tmp_path / "package-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agent_loop/__init__.py", "")
        archive.writestr("agent_loop/autonomy/cli.py", "")
    assert run_quality_checks.archive_missing_members(wheel, ("agent_loop/__init__.py",)) == ()
    assert run_quality_checks.archive_missing_members(wheel, ("agent_loop/autonomy/__main__.py",)) == (
        "agent_loop/autonomy/__main__.py",
    )

    sdist = tmp_path / "package-1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        source = tmp_path / "pyproject.toml"
        source.write_text("", encoding="utf-8")
        archive.add(source, arcname="package-1.0/pyproject.toml")
    assert run_quality_checks.archive_missing_members(sdist, ("pyproject.toml",)) == ()


def test_archive_without_license_is_rejected(tmp_path: Path) -> None:
    """A wheel without LICENSE cannot pass the gate."""
    wheel = tmp_path / "package-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("package-1.0/METADATA", "Name: package")
    assert run_quality_checks.archive_contains_license(wheel) is False


def test_installed_environment_cannot_redirect_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source-checkout import redirectors and product configuration are removed."""
    monkeypatch.setenv("PYTHONPATH", "/source/checkout")
    monkeypatch.setenv("PYTHONHOME", "/source/home")
    monkeypatch.setenv("VIRTUAL_ENV", "/source/venv")
    monkeypatch.setenv("AGENT_LOOP_CODEX_COMMAND", "source")
    monkeypatch.setenv("SHIPII_TOKEN", "secret")
    monkeypatch.setenv("KEEP_ME", "value")
    environment = run_quality_checks._installed_environment()  # noqa: SLF001
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "AGENT_LOOP_CODEX_COMMAND" not in environment
    assert "SHIPII_TOKEN" not in environment
    assert environment["KEEP_ME"] == "value"


def test_distribution_artifacts_requires_exactly_one_of_each(tmp_path: Path) -> None:
    """Missing or duplicated artifacts fail instead of selecting one silently."""
    with pytest.raises(run_quality_checks.QualityCheckError, match="exactly one wheel"):
        run_quality_checks.distribution_artifacts(tmp_path)
    (tmp_path / "package-1.0-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "package-1.0.tar.gz").write_bytes(b"")
    wheel, sdist = run_quality_checks.distribution_artifacts(tmp_path)
    assert wheel.suffix == ".whl"
    assert sdist.name.endswith(".tar.gz")
    (tmp_path / "duplicate-1.0-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(run_quality_checks.QualityCheckError, match="exactly one wheel"):
        run_quality_checks.distribution_artifacts(tmp_path)


def test_run_step_reports_failure() -> None:
    """A non-zero command stops the legacy step helper with the real exit code."""
    step = run_quality_checks.Command(name="probe", argv=(sys.executable, "-c", "raise SystemExit(3)"))
    with pytest.raises(run_quality_checks.QualityCheckError, match="exit code 3"):
        run_quality_checks.run_step(step, cwd=Path.cwd())


def test_build_steps_keeps_complete_coverage_threshold() -> None:
    """The compatibility step view also enforces 99% coverage."""
    steps = run_quality_checks.build_steps(python="/usr/bin/python3")
    assert "--cov-fail-under=99" in steps[-1].argv
    assert steps[-1].argv[0] == "/usr/bin/python3"
