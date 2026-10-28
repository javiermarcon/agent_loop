"""Read-only subprocess runner for OpenCode CLI discovery."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from agent_loop.adapters.opencode.parse import redact_sensitive


@dataclass(frozen=True, slots=True)
class CliCommandResult:
    """Normalized outcome of one CLI invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    missing_binary: bool = False


class CliRunner(Protocol):
    """Injectable runner used by OpenCodeRuntime discovery."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        timeout_seconds: float,
    ) -> CliCommandResult:
        """Execute ``argv`` and return a normalized result."""


def _decode_captured(value: str | bytes | None) -> str:
    """Normalize subprocess capture payloads to text without raising."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class SubprocessCliRunner:
    """Default runner backed by ``subprocess.run`` (no shell)."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        timeout_seconds: float,
    ) -> CliCommandResult:
        """Run ``argv`` capturing stdout/stderr with a hard timeout."""
        try:
            completed = subprocess.run(  # noqa: S603
                list(argv),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return CliCommandResult(
                argv=argv,
                returncode=127,
                stdout="",
                stderr="executable not found",
                missing_binary=True,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = redact_sensitive(_decode_captured(exc.stdout))
            stderr = redact_sensitive(_decode_captured(exc.stderr)) or "timed out"
            return CliCommandResult(
                argv=argv,
                returncode=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )
        return CliCommandResult(
            argv=argv,
            returncode=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
