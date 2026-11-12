#!/usr/bin/env python3
"""Run the complete, repository-wide AgentLoop quality gate.

Two mechanisms are intentionally different:

* ``pre-commit`` validates the files staged for one commit and stays fast.
* this runner validates the whole repository regardless of Git state. It never
  uses ``git diff``/``git status`` to choose Python sources or tests.

Gate sequence:

1. Pre-commit file-hygiene hooks over every repository file discovered from
   the filesystem (tracked, modified and untracked alike).
2. Ruff lint and format check over the whole repository.
3. Mypy and Pylint over every production, test, tool and BDD module.
4. Architecture boundary tests.
5. The complete pytest suite with the 99% coverage threshold.
6. The complete Behave end-to-end suite, including heavy-request enforcement.
7. Git whitespace checks for the index and working tree.
8. Wheel/sdist build, isolated installation and installed-artifact checks.

Every independent gate runs even when an earlier gate fails, so one run gives
the complete diagnostic. The summary distinguishes PASS, FAIL, SKIPPED,
NOT EXECUTED and ERROR:

* ``PASS`` means the control executed and satisfied its evidence requirements.
* ``FAIL`` means the control executed and found a violation.
* ``SKIPPED`` means an explicit legitimate condition made it inapplicable.
* ``NOT EXECUTED`` means a required executable was absent.
* ``ERROR`` means the gate mechanism or its output could not be trusted.

A required gate is blocking unless its status is ``PASS``, except for an
explicit conditional non-applicability (a file-type hook with no matching
files), which stays ``SKIPPED`` and visible. In particular, such a hook is
never reported as ``PASS``, and a pytest/Behave run without heavy-request guard
evidence is ``ERROR``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.quality_runtime import CommandResult  # noqa: E402
from scripts.quality_runtime import GateResult  # noqa: E402
from scripts.quality_runtime import GateSpec  # noqa: E402
from scripts.quality_runtime import GateStatus  # noqa: E402
from scripts.quality_runtime import HeavyRequestReport  # noqa: E402
from scripts.quality_runtime import TestCounts  # noqa: E402
from scripts.quality_runtime import discover_python_files  # noqa: E402
from scripts.quality_runtime import discover_repository_files  # noqa: E402
from scripts.quality_runtime import format_counts  # noqa: E402
from scripts.quality_runtime import heavy_request_lines  # noqa: E402
from scripts.quality_runtime import parse_behave_counts  # noqa: E402
from scripts.quality_runtime import parse_coverage_percent  # noqa: E402
from scripts.quality_runtime import parse_heavy_request_report  # noqa: E402
from scripts.quality_runtime import parse_pytest_counts  # noqa: E402
from scripts.quality_runtime import pre_commit_reported_skip  # noqa: E402
from scripts.quality_runtime import run_command  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "agent-loop-autonomous"
EXPECTED_VERSION = "0.1.0"
EXPECTED_LICENSE = "MIT"
COVERAGE_THRESHOLD = 99.0
RUFF_SCOPE = "."
PYTHON_ANALYSIS_PATHS = (
    "agent_loop",
    "tests",
    "scripts",
    "features/steps",
    "features/environment.py",
    "conftest.py",
    "run.py",
)
FILE_HYGIENE_HOOKS = (
    "trailing-whitespace",
    "end-of-file-fixer",
    "check-json",
    "check-toml",
    "check-xml",
    "check-yaml",
    "check-case-conflict",
    "check-docstring-first",
    "debug-statements",
    "check-builtin-literals",
    "detect-private-key",
)
ARCHITECTURE_TESTS = "tests/agent_loop/architecture"
PYTEST_PATH = "tests/agent_loop"
BEHAVE_PATH = "features"
HEAVY_REQUEST_GATES = frozenset({"pytest-full", "behave-e2e"})
PRE_COMMIT_GATES = frozenset(f"pre-commit:{hook}" for hook in FILE_HYGIENE_HOOKS)
PRE_COMMIT_TIMEOUT_SECONDS = 600.0
ANALYSIS_TIMEOUT_SECONDS = 900.0
PYTEST_TIMEOUT_SECONDS = 3600.0
BEHAVE_TIMEOUT_SECONDS = 1800.0
PACKAGE_STEP_TIMEOUT_SECONDS = 1800.0
WHEEL_REQUIRED_MEMBERS = (
    "agent_loop/__init__.py",
    "agent_loop/autonomy/cli.py",
    "agent_loop/autonomy/__main__.py",
)
SDIST_REQUIRED_MEMBERS = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "agent_loop/__init__.py",
    "agent_loop/autonomy/cli.py",
)


class QualityCheckError(RuntimeError):
    """Raised when a quality step fails."""


@dataclass(frozen=True, slots=True)
class Command:
    """Backward-compatible description of one deterministic command.

    Attributes:
        name: Human-readable step name.
        argv: Executable and arguments.
        env: Variables added to the inherited environment.
    """

    name: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


def _command_gate(
    name: str,
    scope: str,
    argv: Sequence[str],
    *,
    timeout_seconds: float = ANALYSIS_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
) -> GateSpec:
    return GateSpec(
        name=name,
        scope=scope,
        argv=tuple(argv),
        timeout_seconds=timeout_seconds,
        env=dict(env or {}),
    )


def build_gate_plan(*, python: str, repo_root: Path) -> tuple[GateSpec, ...]:
    """Return every integral gate for the complete repository.

    The plan never derives its inputs from Git. File lists come from a
    deterministic filesystem walk; Python analysis targets are explicit
    directories and the complete test suites are invoked without filters.
    """
    files = discover_repository_files(repo_root)
    python_files = discover_python_files(repo_root)
    gates: list[GateSpec] = [
        _command_gate(
            f"pre-commit:{hook}",
            "all repository files",
            ("pre-commit", "run", hook, "--files", *files),
            timeout_seconds=PRE_COMMIT_TIMEOUT_SECONDS,
        )
        for hook in FILE_HYGIENE_HOOKS
    ]
    gates.extend(
        (
            _command_gate("ruff-check", f"{RUFF_SCOPE}/**/*.py", ("ruff", "check", RUFF_SCOPE)),
            _command_gate(
                "ruff-format-check",
                f"{RUFF_SCOPE}/**/*.py",
                ("ruff", "format", "--check", RUFF_SCOPE),
            ),
            _command_gate(
                "mypy",
                "agent_loop + tests + scripts + features + root entrypoints",
                (python, "-m", "mypy", *PYTHON_ANALYSIS_PATHS),
            ),
            _command_gate(
                "pylint",
                f"{len(python_files)} Python modules",
                (python, "scripts/pylint_precommit.py", *python_files),
            ),
            _command_gate(
                "architecture-boundaries",
                ARCHITECTURE_TESTS,
                (python, "-m", "pytest", "-q", ARCHITECTURE_TESTS),
            ),
            _command_gate(
                "pytest-full",
                f"{PYTEST_PATH} (complete suite)",
                (
                    python,
                    "-m",
                    "pytest",
                    PYTEST_PATH,
                    "--cov=agent_loop",
                    "--cov-report=term-missing",
                    f"--cov-fail-under={COVERAGE_THRESHOLD:g}",
                ),
                timeout_seconds=PYTEST_TIMEOUT_SECONDS,
                env={
                    "COVERAGE_FILE": str(
                        Path(tempfile.gettempdir()) / f"agent-loop-coverage-{os.getpid()}.data",
                    ),
                },
            ),
            _command_gate(
                "behave-e2e",
                f"{BEHAVE_PATH} (complete E2E suite)",
                (python, "-m", "behave", BEHAVE_PATH),
                timeout_seconds=BEHAVE_TIMEOUT_SECONDS,
            ),
            _command_gate("git-whitespace-worktree", "working tree", ("git", "diff", "--check")),
            _command_gate("git-whitespace-index", "index", ("git", "diff", "--cached", "--check")),
            GateSpec(
                name="package-build-install",
                scope="wheel + sdist + isolated install",
                required=True,
                kind="package",
                timeout_seconds=PACKAGE_STEP_TIMEOUT_SECONDS,
            ),
        ),
    )
    return tuple(gates)


def build_steps(*, python: str) -> tuple[Command, ...]:
    """Return the legacy compact view of the host-side command sequence.

    This helper is retained for compatibility with repository tooling tests; the
    authoritative integral plan is :func:`build_gate_plan`.
    """
    return (
        Command(
            name="pre-commit hooks (all files)",
            argv=("pre-commit", "run", "--all-files", "--show-diff-on-failure"),
        ),
        Command(name="git whitespace (working tree)", argv=("git", "diff", "--check")),
        Command(name="git whitespace (index)", argv=("git", "diff", "--cached", "--check")),
        Command(
            name="tests and coverage",
            argv=(
                python,
                "-m",
                "pytest",
                "--cov=agent_loop",
                "--cov-report=term-missing",
                f"--cov-fail-under={COVERAGE_THRESHOLD:g}",
            ),
        ),
    )


def archive_contains_license(path: Path) -> bool:
    """Return whether a wheel or sdist includes a top-level license file.

    Args:
        path: Wheel (``.whl``) or source distribution (``.tar.gz``) path.

    Returns:
        ``True`` when a ``LICENSE`` member is present.
    """
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return any(member.rsplit("/", 1)[-1] == "LICENSE" for member in archive.namelist())
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return any(member.name.rsplit("/", 1)[-1] == "LICENSE" for member in archive.getmembers())
    return False


def archive_member_names(path: Path) -> tuple[str, ...]:
    """Return every member name from a wheel or sdist archive."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return tuple(member.name for member in archive.getmembers())
    return ()


def archive_missing_members(path: Path, required: Sequence[str]) -> tuple[str, ...]:
    """Return required members absent from an archive.

    Source distributions normally wrap their content in a
    ``<name>-<version>/`` directory, so each required relative path may appear
    either at the archive root or below exactly one prefix directory.
    """
    names = archive_member_names(path)
    return tuple(
        member for member in required if not any(name == member or name.endswith(f"/{member}") for name in names)
    )


def distribution_artifacts(distribution_dir: Path) -> tuple[Path, Path]:
    """Resolve the single wheel and sdist produced by a build.

    Args:
        distribution_dir: Directory containing build output.

    Returns:
        ``(wheel, sdist)``.

    Raises:
        QualityCheckError: If either artifact is missing or duplicated.
    """
    wheels = sorted(distribution_dir.glob("*.whl"))
    sdists = sorted(distribution_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        message = f"Expected exactly one wheel, found {len(wheels)} in {distribution_dir}."
        raise QualityCheckError(message)
    if len(sdists) != 1:
        message = f"Expected exactly one sdist, found {len(sdists)} in {distribution_dir}."
        raise QualityCheckError(message)
    return wheels[0], sdists[0]


def run_step(step: Command, *, cwd: Path) -> None:
    """Execute one command and fail with a bounded diagnostic.

    Args:
        step: Command to run.
        cwd: Working directory.

    Raises:
        QualityCheckError: If the command exits non-zero.
    """
    result = run_command(step.argv, cwd=cwd, env=step.env)
    if result.error is not None:
        message = f"{step.name}: {result.error}"
        raise QualityCheckError(message)
    if result.exit_code != 0:
        message = f"{step.name} failed with exit code {result.exit_code}."
        raise QualityCheckError(message)


def _installed_environment() -> dict[str, str]:
    """Return an environment that cannot redirect imports to a source checkout."""
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith(("AGENT_LOOP_", "SHIPII_")):
            del environment[name]
    return environment


def _run_checked(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    label: str,
    env: Mapping[str, str] | None = None,
) -> None:
    """Run an internal packaging command and capture its output.

    Args:
        argv: Command and arguments.
        cwd: Working directory.
        label: Human-readable step label.

    Raises:
        QualityCheckError: If the command exits non-zero.
    """
    sys.stdout.write(f"[quality] {label}: {shlex.join(argv)}\n")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=PACKAGE_STEP_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as error:
        message = f"{label} timed out after {PACKAGE_STEP_TIMEOUT_SECONDS:g}s."
        raise QualityCheckError(message) from error
    if completed.returncode != 0:
        message = f"{label} failed with exit code {completed.returncode}."
        if completed.stdout:
            message = f"{message}\nstdout:\n{completed.stdout[-4000:]}"
        if completed.stderr:
            message = f"{message}\nstderr:\n{completed.stderr[-4000:]}"
        raise QualityCheckError(message)


def _verify_installed_distribution(venv_python: Path, venv_bin: Path, *, cwd: Path) -> None:
    """Verify metadata, imports and entry points of the installed wheel.

    Args:
        venv_python: Interpreter of the isolated environment.
        venv_bin: Console-script directory of the isolated environment.
        cwd: Directory outside the checkout, so local imports cannot shadow it.

    Raises:
        QualityCheckError: If any installed artifact is inconsistent.
    """
    metadata_probe = (
        "import importlib.metadata as metadata; "
        "import agent_loop; "
        f"package = metadata.distribution({PACKAGE_NAME!r}); "
        f"assert package.metadata['Name'] == {PACKAGE_NAME!r}, package.metadata['Name']; "
        f"assert package.version == {EXPECTED_VERSION!r}, package.version; "
        f"assert package.metadata['License-Expression'] == {EXPECTED_LICENSE!r}; "
        "assert package.metadata['License-File'] == 'LICENSE'; "
        "assert package.metadata['Requires-Python'] == '>=3.12'; "
        "print(agent_loop.__file__)"
    )
    installed_env = _installed_environment()
    _run_checked(
        (str(venv_python), "-c", metadata_probe),
        cwd=cwd,
        label="installed metadata and import",
        env=installed_env,
    )
    _run_checked(
        (str(venv_bin / "agent-loop"), "--version"),
        cwd=cwd,
        label="installed console script version",
        env=installed_env,
    )
    _run_checked(
        (str(venv_bin / "agent-loop"), "--help"),
        cwd=cwd,
        label="installed console script help",
        env=installed_env,
    )
    _run_checked(
        (str(venv_python), "-m", "agent_loop.autonomy", "--version"),
        cwd=cwd,
        label="installed module entry point version",
        env=installed_env,
    )
    _run_checked(
        (str(venv_python), "-m", "agent_loop.autonomy", "--help"),
        cwd=cwd,
        label="installed module entry point help",
        env=installed_env,
    )
    _run_checked(
        (str(venv_python), "-m", "pip", "check"),
        cwd=cwd,
        label="pip check",
        env=installed_env,
    )


def verify_distribution(*, python: str, repo_root: Path) -> None:
    """Build, install and verify the wheel and sdist.

    Args:
        python: Interpreter used to build and create the isolated environment.
        repo_root: Repository root.

    Raises:
        QualityCheckError: If any packaging or installation check fails.
    """
    with tempfile.TemporaryDirectory(prefix="agent-loop-quality-") as temporary:
        workspace = Path(temporary)
        distribution_dir = workspace / "dist"
        _run_checked(
            (python, "-m", "build", "--outdir", str(distribution_dir)),
            cwd=repo_root,
            label="build wheel and sdist",
        )
        wheel, sdist = distribution_artifacts(distribution_dir)
        for artifact, required in ((wheel, WHEEL_REQUIRED_MEMBERS), (sdist, SDIST_REQUIRED_MEMBERS)):
            if not archive_contains_license(artifact):
                message = f"{artifact.name} does not include LICENSE."
                raise QualityCheckError(message)
            missing = archive_missing_members(artifact, required)
            if missing:
                message = f"{artifact.name} is missing required member(s): {', '.join(missing)}."
                raise QualityCheckError(message)

        venv_dir = workspace / "venv"
        install_env = _installed_environment()
        _run_checked(
            (python, "-m", "venv", str(venv_dir)),
            cwd=workspace,
            label="create isolated environment",
            env=install_env,
        )
        venv_python = venv_dir / "bin" / "python"
        venv_bin = venv_dir / "bin"
        _run_checked(
            (str(venv_python), "-m", "pip", "install", "--quiet", str(wheel)),
            cwd=workspace,
            label="install wheel",
            env=install_env,
        )
        _verify_installed_distribution(venv_python, venv_bin, cwd=workspace)


def _gate_metrics(name: str, output: str) -> dict[str, object]:
    metrics: dict[str, object] = {}
    if name == "pytest-full":
        metrics["pytest"] = parse_pytest_counts(output)
        coverage = parse_coverage_percent(output)
        if coverage is not None:
            metrics["coverage"] = coverage
    elif name == "behave-e2e":
        metrics["behave"] = parse_behave_counts(output)
    if name in HEAVY_REQUEST_GATES:
        metrics["heavy_request_report"] = parse_heavy_request_report(output)
    heavy = heavy_request_lines(output)
    if heavy:
        metrics["heavy_requests"] = heavy
    return metrics


def _test_count_interpretation_error(name: str, metrics: dict[str, object]) -> str | None:
    """Return an interpretation error when a successful gate has no evidence."""
    if name == "pytest-full":
        counts = metrics.get("pytest")
        if not isinstance(counts, TestCounts) or counts.total == 0:
            return "could not parse pytest result counts from the output"
        if counts.failed or counts.errors:
            return "pytest reported failed/error tests with a zero exit code"
        if metrics.get("coverage") is None:
            return "could not parse the coverage percentage from the pytest output"
    if name == "behave-e2e":
        counts = metrics.get("behave")
        if not isinstance(counts, TestCounts) or counts.total == 0:
            return "could not parse Behave scenario counts from the output"
        if counts.failed:
            return "Behave reported failed scenarios with a zero exit code"
    return None


def _heavy_request_error(name: str, metrics: dict[str, object]) -> tuple[GateStatus, str] | None:
    """Classify the heavy-request control evidence, never by silence."""
    if name not in HEAVY_REQUEST_GATES:
        return None
    report = metrics.get("heavy_request_report")
    if not isinstance(report, HeavyRequestReport):
        return GateStatus.ERROR, "heavy request guard produced no report; control was not demonstrated"
    checks = [
        (not report.initialized, GateStatus.ERROR, "heavy request guard did not initialize"),
        (report.activated < 1, GateStatus.ERROR, "heavy request guard was never activated"),
        (
            bool(report.errors),
            GateStatus.ERROR,
            f"heavy request guard error: {report.errors[0]}" if report.errors else "",
        ),
        (
            bool(report.violations),
            GateStatus.FAIL,
            f"heavy request violation: {report.violations[0]}" if report.violations else "",
        ),
        (
            report.observed < 1,
            GateStatus.ERROR,
            "heavy request guard observed no audit events; observation was not demonstrated",
        ),
    ]
    if name == "pytest-full":
        checks.extend(
            (
                (
                    report.expected < 1,
                    GateStatus.ERROR,
                    "heavy request guard produced no positive detection evidence",
                ),
                (
                    len(report.authorized) < 1,
                    GateStatus.ERROR,
                    "heavy request guard produced no authorization evidence",
                ),
            ),
        )
    for condition, status, message in checks:
        if condition:
            return status, message
    return None


def _base_command_status(gate: GateSpec, result: CommandResult) -> tuple[GateStatus, str, bool]:
    """Classify process execution before interpreting tool-specific output."""
    if result.timed_out:
        return GateStatus.ERROR, result.error or "command timed out", False
    if result.error is not None:
        status = GateStatus.NOT_EXECUTED if result.error.startswith("executable not found") else GateStatus.ERROR
        return status, result.error, False
    if result.exit_code != 0:
        return GateStatus.FAIL, f"exit code {result.exit_code}", False
    if gate.name in PRE_COMMIT_GATES and pre_commit_reported_skip(result.output):
        return (
            GateStatus.SKIPPED,
            "conditional: pre-commit skipped the hook because no applicable files matched",
            True,
        )
    return GateStatus.PASS, "command completed successfully", False


def _command_gate_result(gate: GateSpec, result: CommandResult, metrics: dict[str, object]) -> GateResult:
    """Apply evidence requirements and heavy-request policy to one command."""
    status, reason, conditional = _base_command_status(gate, result)
    interpretation_error = _test_count_interpretation_error(gate.name, metrics) if status is GateStatus.PASS else None
    if interpretation_error is not None:
        status = GateStatus.ERROR
        reason = interpretation_error
        conditional = False
    if status is GateStatus.PASS and gate.name == "pytest-full":
        coverage = metrics.get("coverage")
        if isinstance(coverage, int | float) and float(coverage) < COVERAGE_THRESHOLD:
            status = GateStatus.FAIL
            reason = f"coverage {float(coverage):.2f}% is below the {COVERAGE_THRESHOLD:g}% threshold"
    heavy_result = _heavy_request_error(gate.name, metrics)
    if heavy_result is not None:
        heavy_status, heavy_reason = heavy_result
        if heavy_status is GateStatus.FAIL or status is GateStatus.PASS:
            status = heavy_status
        reason = f"{reason}; {heavy_reason}"
        conditional = False
    return GateResult(
        name=gate.name,
        scope=gate.scope,
        status=status,
        duration_seconds=result.duration_seconds,
        reason=reason,
        required=gate.required,
        output=result.output,
        metrics=metrics,
        conditional=conditional,
        command=shlex.join(result.argv),
        exit_code=result.exit_code,
    )


def _package_gate_result(
    gate: GateSpec,
    *,
    python: str,
    repo_root: Path,
    package_runner: Callable[..., None],
) -> GateResult:
    """Run the packaging gate and preserve mechanism failures as ERROR."""
    started = time.monotonic()
    command = (
        f"{shlex.quote(python)} -m build + isolated venv install + "
        "agent-loop --version/--help + python -m agent_loop.autonomy --version/--help + pip check"
    )
    try:
        package_runner(python=python, repo_root=repo_root)
    except QualityCheckError as error:
        return GateResult(
            name=gate.name,
            scope=gate.scope,
            status=GateStatus.FAIL,
            duration_seconds=time.monotonic() - started,
            reason=str(error),
            required=gate.required,
            command=command,
        )
    except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return GateResult(
            name=gate.name,
            scope=gate.scope,
            status=GateStatus.ERROR,
            duration_seconds=time.monotonic() - started,
            reason=f"packaging gate mechanism failed: {error}",
            required=gate.required,
            command=command,
        )
    return GateResult(
        name=gate.name,
        scope=gate.scope,
        status=GateStatus.PASS,
        duration_seconds=time.monotonic() - started,
        reason="wheel and sdist built, installed and verified",
        required=gate.required,
        command=command,
        exit_code=0,
    )


def execute_gate(
    gate: GateSpec,
    *,
    repo_root: Path,
    python: str,
    package_runner: Callable[..., None] = verify_distribution,
) -> GateResult:
    """Execute one gate and classify its result without hiding failures."""
    if gate.kind == "package":
        return _package_gate_result(gate, python=python, repo_root=repo_root, package_runner=package_runner)
    try:
        result = run_command(gate.argv, cwd=repo_root, env=gate.env, timeout=gate.timeout_seconds)
        metrics = _gate_metrics(gate.name, result.output)
    except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return GateResult(
            name=gate.name,
            scope=gate.scope,
            status=GateStatus.ERROR,
            duration_seconds=0.0,
            reason=f"gate execution or output interpretation failed: {error}",
            required=gate.required,
            command=shlex.join(gate.argv),
        )
    return _command_gate_result(gate, result, metrics)


def _status_reason(result: GateResult) -> str:
    if result.status is GateStatus.PASS:
        return result.reason
    return f"{result.reason} ({result.duration_seconds:.2f}s)" if result.duration_seconds else result.reason


def is_blocking_result(result: GateResult) -> bool:
    """Return whether one result must fail the global quality decision.

    A required gate is blocking unless it passed. The documented exception is
    a conditional skip: the gate was applicable only when its file-type/search
    condition matched, and that condition was explicitly false. The skip stays
    visible in the summary and is never rendered as PASS.
    """
    if not result.required or result.status is GateStatus.PASS:
        return False
    return not (result.status is GateStatus.SKIPPED and result.conditional)


def _result_summary_lines(result: GateResult) -> list[str]:
    """Render the stable per-gate block."""
    requirement = "required" if result.required else "optional"
    lines = [
        f"[{result.status}] {result.name}",
        f"  scope: {result.scope}",
        f"  gate: {requirement}",
        f"  command: {result.command or 'not executed'}",
        f"  exit code: {result.exit_code if result.exit_code is not None else 'n/a'}",
        f"  duration: {result.duration_seconds:.2f}s",
    ]
    if result.conditional:
        lines.append("  condition: not applicable (explicit applicability condition was false)")
    lines.append(f"  reason: {_status_reason(result)}")
    if result.name == "pytest-full":
        counts = result.metrics.get("pytest")
        if isinstance(counts, TestCounts):
            lines.append(f"  {format_counts('pytest', counts)}")
        coverage = result.metrics.get("coverage")
        if isinstance(coverage, int | float):
            lines.append(f"  coverage: {float(coverage):.2f}% (threshold {COVERAGE_THRESHOLD:g}%)")
    if result.name == "behave-e2e":
        counts = result.metrics.get("behave")
        if isinstance(counts, TestCounts):
            lines.append(f"  {format_counts('behave', counts)}")
    _append_heavy_request_report(lines, result)
    return lines


def _summary_tail_lines(results: Sequence[GateResult], *, blocking: Sequence[GateResult]) -> list[str]:
    """Render the cross-gate omissions and the final decision."""
    lines: list[str] = []
    skipped = [result for result in results if result.status is GateStatus.SKIPPED]
    not_executed = [result for result in results if result.status is GateStatus.NOT_EXECUTED]
    errors = [result for result in results if result.status is GateStatus.ERROR]
    if skipped:
        lines.append(f"Skipped gates: {', '.join(result.name for result in skipped)}")
    conditional_skips = [result for result in skipped if result.conditional]
    if conditional_skips:
        lines.append(
            "Conditional non-applicable gates: " + ", ".join(result.name for result in conditional_skips),
        )
    if not_executed:
        lines.append(f"Not executed gates: {', '.join(result.name for result in not_executed)}")
    if errors:
        lines.append(f"Error gates: {', '.join(result.name for result in errors)}")
    lines.append("Overall: " + ("FAIL" if blocking else "PASS"))
    return lines


def render_summary(results: Sequence[GateResult]) -> str:
    """Render the consolidated, verifiable end-of-run summary."""
    lines = ["", "Quality gate summary", "=" * 20]
    for result in results:
        lines.extend(_result_summary_lines(result))
    blocking = [result for result in results if is_blocking_result(result)]
    lines.extend(_summary_tail_lines(results, blocking=blocking))
    return "\n".join(lines)


def _append_heavy_request_report(lines: list[str], result: GateResult) -> None:
    """Render the guard evidence for a pytest/Behave gate."""
    if result.name not in HEAVY_REQUEST_GATES:
        return
    report = result.metrics.get("heavy_request_report")
    if not isinstance(report, HeavyRequestReport):
        lines.append("  heavy requests: no guard report (control not demonstrated)")
        return
    initialized = "yes" if report.initialized else "no"
    lines.append(
        "  heavy requests: "
        f"initialized={initialized} activated={report.activated} "
        f"observed={report.observed} violations={len(report.violations)} "
        f"authorized={len(report.authorized)} expected={report.expected}",
    )
    lines.extend(f"    {violation}" for violation in report.violations)
    lines.extend(f"    {authorization}" for authorization in report.authorized)
    lines.extend(f"    {error}" for error in report.errors)


def main(argv: list[str] | None = None) -> int:
    """Run the complete quality gate.

    Args:
        argv: Optional command-line arguments.

    Returns:
        ``0`` when every required gate passes, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--skip-package",
        action="store_true",
        help="Skip build/install verification as an explicitly optional local gate; CI never uses it.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Run only the named gate(s); the summary still marks the omitted gates as SKIPPED.",
    )
    arguments = parser.parse_args(argv)
    repo_root = arguments.repo_root.resolve()
    gates = list(build_gate_plan(python=arguments.python, repo_root=repo_root))
    selected = set(arguments.only)
    results: list[GateResult] = []
    for gate in gates:
        if selected and gate.name not in selected:
            results.append(
                GateResult(
                    name=gate.name,
                    scope=gate.scope,
                    status=GateStatus.SKIPPED,
                    duration_seconds=0.0,
                    reason="excluded by --only",
                    required=gate.required,
                    command=shlex.join(gate.argv) if gate.argv else "package build/install",
                ),
            )
            continue
        if gate.kind == "package" and arguments.skip_package:
            results.append(
                GateResult(
                    name=gate.name,
                    scope=gate.scope,
                    status=GateStatus.SKIPPED,
                    duration_seconds=0.0,
                    reason="excluded by --skip-package (optional local gate; CI never skips it)",
                    required=False,
                    command="package build/install",
                ),
            )
            continue
        rendered_command = shlex.join(gate.argv) if gate.argv else "package build/install"
        sys.stdout.write(f"\n[quality] {gate.name} ({gate.scope})\n[quality] command: {rendered_command}\n")
        results.append(execute_gate(gate, repo_root=repo_root, python=arguments.python))
    sys.stdout.write(render_summary(results) + "\n")
    blocking = [result for result in results if is_blocking_result(result)]
    if blocking:
        sys.stderr.write(f"[quality] FAILED: {len(blocking)} required gate(s) did not pass\n")
        return 1
    sys.stdout.write("[quality] all required gates passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
