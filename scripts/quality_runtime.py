"""Deterministic process, parsing and file-discovery primitives for the gate.

This module is repository tooling. It is imported by ``run_quality_checks.py``
and by the regression tests of the quality system; the installable package must
never import it.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

HEAVY_REQUEST_MARKER = "HEAVY REQUEST"
HEAVY_GUARD_MARKER = "HEAVY REQUEST GUARD:"
HEAVY_VIOLATION_MARKER = "HEAVY REQUEST VIOLATION:"
HEAVY_ALLOWED_MARKER = "HEAVY REQUEST ALLOWED:"
HEAVY_ERROR_MARKER = "HEAVY REQUEST ERROR:"
PRE_COMMIT_SKIPPED_MARKER = "(no files to check)Skipped"
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".agent-loop-logs",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
    },
)
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".a"})
EXCLUDED_FILE_NAMES = frozenset({".coverage", ".DS_Store"})


class GateStatus(StrEnum):
    """Final disposition of one integral quality gate.

    ``SKIPPED`` means the gate did not run because an explicit, legitimate
    condition made it inapplicable; it is never a synonym for ``PASS``.
    ``NOT_EXECUTED`` means a required executable was absent.
    ``ERROR`` means the gate mechanism or its output could not be interpreted.
    """

    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    NOT_EXECUTED = "NOT EXECUTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class HeavyRequestReport:
    """Evidence emitted by the pytest/Behave heavy-request guard."""

    initialized: bool
    activated: int
    observed: int
    violations: tuple[str, ...]
    authorized: tuple[str, ...]
    errors: tuple[str, ...]
    expected: int = 0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome and captured output of one external command."""

    argv: tuple[str, ...]
    exit_code: int | None
    output: str
    duration_seconds: float
    error: str | None = None
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class GateSpec:
    """Declare one integral gate independently from how it is executed."""

    name: str
    scope: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    required: bool = True
    kind: str = "command"
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    """Final, printable outcome for one gate.

    ``conditional`` marks a required gate that did not run because its explicit
    applicability condition was false (for example, a file-type hook with no
    matching files). It stays visible as ``SKIPPED`` but does not fail the run.
    """

    name: str
    scope: str
    status: GateStatus
    duration_seconds: float
    reason: str
    required: bool
    output: str = ""
    metrics: Mapping[str, object] = field(default_factory=dict)
    conditional: bool = False
    command: str = ""
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class TestCounts:
    """Counts parsed from a pytest or Behave run."""

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    undefined: int = 0

    @property
    def total(self) -> int:
        """Return every classified item."""
        return self.passed + self.failed + self.skipped + self.errors + self.undefined


def discover_repository_files(repo_root: Path) -> tuple[str, ...]:
    """Return every regular repository file without consulting Git.

    The walk is deterministic and excludes only generated/VCS environments,
    caches and compiled artifacts. Untracked and new files are included.
    """
    files: list[str] = []
    resolved_root = repo_root.resolve()
    for directory, dirnames, filenames in os.walk(repo_root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if name not in EXCLUDED_DIRECTORY_NAMES and not name.endswith(".egg-info")
        )
        current = Path(directory)
        for name in sorted(filenames):
            path = current / name
            if (
                path.suffix in EXCLUDED_FILE_SUFFIXES
                or path.name in EXCLUDED_FILE_NAMES
                or path.name.startswith(".coverage.")
            ):
                continue
            if path.is_symlink():
                try:
                    path.resolve().relative_to(resolved_root)
                except (OSError, ValueError):
                    continue
            relative = path.relative_to(repo_root)
            files.append(relative.as_posix())
    return tuple(files)


def discover_python_files(repo_root: Path) -> tuple[str, ...]:
    """Return every repository Python file in the integral analysis scope."""
    return tuple(path for path in discover_repository_files(repo_root) if path.endswith(".py"))


def run_command(
    argv: Iterable[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    stream: bool = True,
    timeout: float | None = None,
) -> CommandResult:
    """Execute one command without a shell, streaming and capturing output.

    ``timeout`` bounds the complete run. A timed-out process is killed and its
    partial output is preserved, but the result is marked ``timed_out`` so the
    caller cannot classify an interrupted run as a successful control.
    """
    command = tuple(argv)
    if not command:
        return CommandResult(
            argv=command,
            exit_code=None,
            output="",
            duration_seconds=0.0,
            error="empty command",
        )
    executable = shutil.which(command[0])
    if executable is None:
        return CommandResult(
            argv=command,
            exit_code=None,
            output="",
            duration_seconds=0.0,
            error=f"executable not found on PATH: {command[0]}",
        )
    environment = os.environ.copy()
    if env is not None:
        environment.update(env)
    started = time.monotonic()
    process, start_error = _start_process(executable, command, cwd=cwd, environment=environment)
    if process is None:
        return CommandResult(
            argv=command,
            exit_code=None,
            output="",
            duration_seconds=time.monotonic() - started,
            error=start_error,
        )
    chunks: list[str] = []

    def _pump() -> None:
        stream_handle = process.stdout
        if stream_handle is None:
            return
        try:
            for line in stream_handle:
                chunks.append(line)
                if stream:
                    sys.stdout.write(line)
                    sys.stdout.flush()
        finally:
            stream_handle.close()

    reader = threading.Thread(target=_pump, name="quality-command-reader", daemon=True)
    reader.start()
    exit_code, timed_out = _wait_for_process(process, timeout)
    reader_error = _finish_reader(reader, process)
    error = f"timed out after {timeout:g}s" if timed_out else None
    if reader_error is not None:
        error = f"{error}; {reader_error}" if error else reader_error
    return CommandResult(
        argv=command,
        exit_code=exit_code,
        output="".join(chunks),
        duration_seconds=time.monotonic() - started,
        error=error,
        timed_out=timed_out,
    )


def _start_process(
    executable: str,
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> tuple[subprocess.Popen[str] | None, str | None]:
    """Start one process and return either the handle or its OS error."""
    try:
        process = subprocess.Popen(  # noqa: S603 - explicit argv, never a shell
            (executable, *command[1:]),
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return None, str(exc)
    return process, None


def _finish_reader(reader: threading.Thread, process: subprocess.Popen[str]) -> str | None:
    """Join the output reader and close a pipe that a detached child kept open."""
    reader.join(timeout=30.0)
    if not reader.is_alive():
        return None
    with contextlib.suppress(OSError):
        if process.stdout is not None:
            process.stdout.close()
    reader.join(timeout=1.0)
    return "output reader did not terminate"


def _wait_for_process(process: subprocess.Popen[str], timeout: float | None) -> tuple[int, bool]:
    """Wait for a process, killing it and preserving the timeout signal."""
    try:
        return process.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            with contextlib.suppress(OSError):
                process.kill()
        return process.wait(), True


_PYTEST_SUMMARY_RE = re.compile(
    r"^(?:\d+\s+(?:passed|failed|skipped|error|errors|warning|warnings|deselected|xfailed|xpassed)"
    r"(?:,\s*)?)+\s*in\s+\d+(?:\.\d+)?s$",
)


def parse_pytest_counts(output: str) -> TestCounts:
    """Parse the terminal counts printed by pytest.

    Only a complete pytest summary line (counts followed by ``in <duration>s``)
    is accepted. Captured test output that merely contains a number and the word
    ``passed`` cannot fabricate evidence.
    """

    def _count(label: str, text: str) -> int:
        match = re.search(rf"(\d+)\s+{label}\b", text)
        return int(match.group(1)) if match else 0

    summary = ""
    for line in reversed(output.splitlines()):
        candidate = line.strip().strip("=").strip()
        if _PYTEST_SUMMARY_RE.match(candidate):
            summary = candidate
            break
    if not summary:
        return TestCounts()
    return TestCounts(
        passed=_count("passed", summary),
        failed=_count("failed", summary),
        skipped=_count("skipped", summary),
        errors=_count("error", summary) + _count("errors", summary),
    )


def parse_behave_counts(output: str) -> TestCounts:
    """Parse the last scenario/step summary printed by Behave."""
    scenario_line = ""
    for line in output.splitlines():
        if re.search(r"\d+\s+scenarios?\s+(?:passed|failed|skipped)", line):
            scenario_line = line
    values: dict[str, int] = {}
    for count, state in re.findall(r"(\d+)\s+(?:scenarios?\s+)?(passed|failed|skipped)\b", scenario_line):
        values[state] = int(count)
    undefined = re.search(r"(\d+)\s+undefined\b", output)
    return TestCounts(
        passed=values.get("passed", 0),
        failed=values.get("failed", 0),
        skipped=values.get("skipped", 0),
        undefined=int(undefined.group(1)) if undefined else 0,
    )


def parse_coverage_percent(output: str) -> float | None:
    """Parse the final coverage percentage from a coverage table or summary."""
    summary = re.findall(r"Total coverage:\s*(?P<percent>\d{1,3}(?:\.\d+)?)%", output)
    if summary:
        return float(summary[-1])
    matches = re.findall(
        r"^TOTAL\s+\d+\s+\d+\s+(?P<percent>\d{1,3}(?:\.\d+)?)%",
        output,
        flags=re.MULTILINE,
    )
    if not matches:
        return None
    return float(matches[-1])


def heavy_request_lines(output: str) -> tuple[str, ...]:
    """Return unique heavy-request lines (evidence, violations and exceptions)."""
    seen: set[str] = set()
    found: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if HEAVY_REQUEST_MARKER not in line:
            continue
        if line in seen:
            continue
        seen.add(line)
        found.append(line)
    return tuple(found)


def parse_heavy_request_report(output: str) -> HeavyRequestReport | None:
    """Parse the guard's explicit evidence from pytest/Behave output.

    A missing report is ``None``. The caller must not treat that as success:
    the control was not demonstrated to be initialized or active.
    """
    report_line = ""
    violations: list[str] = []
    authorized: list[str] = []
    errors: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if HEAVY_GUARD_MARKER in line:
            report_line = line
        elif HEAVY_VIOLATION_MARKER in line:
            violations.append(line)
        elif HEAVY_ALLOWED_MARKER in line:
            authorized.append(line)
        elif HEAVY_ERROR_MARKER in line:
            errors.append(line)
    if not report_line:
        return None
    values = dict(re.findall(r"(\w+)=([^\s]+)", report_line))

    def _integer(name: str) -> int:
        raw = values.get(name, "0")
        return int(raw) if raw.isdigit() else 0

    _append_missing_detail(violations, _integer("violations"), HEAVY_VIOLATION_MARKER, "violation")
    _append_missing_detail(authorized, _integer("authorized"), HEAVY_ALLOWED_MARKER, "authorization")
    _append_missing_detail(errors, _integer("errors"), HEAVY_ERROR_MARKER, "error")
    return HeavyRequestReport(
        initialized=values.get("initialized", "no").lower() in {"yes", "true", "1"},
        activated=_integer("activated"),
        observed=_integer("observed"),
        violations=tuple(dict.fromkeys(violations)),
        authorized=tuple(dict.fromkeys(authorized)),
        errors=tuple(dict.fromkeys(errors)),
        expected=_integer("expected"),
    )


def _append_missing_detail(entries: list[str], declared: int, marker: str, label: str) -> None:
    """Preserve an unparsed declared count instead of silently dropping it."""
    if declared > len(entries):
        entries.append(f"{marker} guard reported {declared} {label}(s); details were not parsed")


def pre_commit_reported_skip(output: str) -> bool:
    """Return whether pre-commit explicitly skipped a hook for lack of files."""
    return PRE_COMMIT_SKIPPED_MARKER.replace(" ", "") in output.replace(" ", "")


def format_counts(label: str, counts: TestCounts) -> str:
    """Render parsed counts in a stable one-line form."""
    return (
        f"{label}: {counts.passed} passed, {counts.failed} failed, "
        f"{counts.skipped} skipped, {counts.errors} errors"
        + (f", {counts.undefined} undefined" if counts.undefined else "")
    )
