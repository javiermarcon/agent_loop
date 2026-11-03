"""Subprocess execution with structured, bounded results."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

from agent_loop.autonomy.config import DEFAULT_MAX_OUTPUT_CHARS

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Raw outcome of one subprocess invocation."""

    command: tuple[str, ...]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    error: str | None = None


class CommandRunner(Protocol):
    """Boundary for executing one command inside the target workspace."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int | None = None,
        stdin_text: str | None = None,
    ) -> CommandOutcome:
        """Execute a command without a shell and capture bounded output.

        ``stdin_text`` is streamed to the child through an anonymous pipe and
        closed when written. It is never turned into an argument.
        """


class SubprocessCommandRunner:
    """Default runner backed by ``subprocess.run`` with no shell."""

    def __init__(self, *, max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> None:
        self._max_output_chars = max_output_chars

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int | None = None,
        stdin_text: str | None = None,
    ) -> CommandOutcome:
        argv = tuple(command)
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - explicit argv, no shell
                argv,
                cwd=cwd,
                env=dict(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=stdin_text,
                stdin=subprocess.DEVNULL if stdin_text is None else None,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return _timeout_outcome(argv, cwd, exc, started, self._max_output_chars)
        except FileNotFoundError as exc:
            return _error_outcome(argv, cwd, exc, started, "executable not found")
        except OSError as exc:
            return _error_outcome(argv, cwd, exc, started, str(exc))
        return CommandOutcome(
            command=argv,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout=_truncate(completed.stdout, self._max_output_chars),
            stderr=_truncate(completed.stderr, self._max_output_chars),
            duration_seconds=time.monotonic() - started,
        )


def _timeout_outcome(
    argv: tuple[str, ...],
    cwd: Path,
    exc: subprocess.TimeoutExpired,
    started: float,
    limit: int,
) -> CommandOutcome:
    stdout = _as_text(exc.stdout)
    stderr = _as_text(exc.stderr)
    return CommandOutcome(
        command=argv,
        cwd=str(cwd),
        exit_code=None,
        stdout=_truncate(stdout, limit),
        stderr=_truncate(stderr, limit),
        duration_seconds=time.monotonic() - started,
        timed_out=True,
        error="command timed out",
    )


def _error_outcome(
    argv: tuple[str, ...],
    cwd: Path,
    exc: OSError,
    started: float,
    message: str,
) -> CommandOutcome:
    return CommandOutcome(
        command=argv,
        cwd=str(cwd),
        exit_code=None,
        stdout="",
        stderr=_truncate(str(exc), DEFAULT_MAX_OUTPUT_CHARS),
        duration_seconds=time.monotonic() - started,
        error=message,
    )


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _truncate(value: str, limit: int) -> str:
    if limit < 1 or len(value) <= limit:
        return value
    head = limit // 2
    tail = limit - head
    return f"{value[:head]}\n...[truncated]...\n{value[-tail:]}"
