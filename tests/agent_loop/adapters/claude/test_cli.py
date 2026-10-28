"""Tests for the Claude Code subprocess CLI runner."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from agent_loop.adapters.claude.cli import SubprocessCliRunner

_TIMEOUT_SECONDS = 1.5
_MISSING_RETURNCODE = 127
_TIMEOUT_RETURNCODE = 124
_WORKSPACE = "workspace-fixture"


def test_subprocess_cli_runner_captures_stdout() -> None:
    """Successful subprocess runs return stdout and exit code."""
    runner = SubprocessCliRunner()
    completed = subprocess.CompletedProcess(
        args=["claude", "--version"],
        returncode=0,
        stdout="2.1.283 (Claude Code)\n",
        stderr="",
    )
    with patch("agent_loop.adapters.claude.cli.subprocess.run", return_value=completed):
        result = runner.run(
            ("claude", "--version"),
            cwd=_WORKSPACE,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "2.1.283 (Claude Code)"
    assert result.missing_binary is False
    assert result.timed_out is False


def test_subprocess_cli_runner_missing_binary() -> None:
    """FileNotFoundError becomes a missing-binary result."""
    runner = SubprocessCliRunner()
    with patch(
        "agent_loop.adapters.claude.cli.subprocess.run",
        side_effect=FileNotFoundError("claude"),
    ):
        result = runner.run(
            ("claude", "--version"),
            cwd=_WORKSPACE,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
    assert result.missing_binary is True
    assert result.returncode == _MISSING_RETURNCODE


def test_subprocess_cli_runner_timeout_with_str_output() -> None:
    """TimeoutExpired with str captures preserves and redacts text."""
    runner = SubprocessCliRunner()
    timed_out = subprocess.TimeoutExpired(
        cmd=["claude"],
        timeout=_TIMEOUT_SECONDS,
        output="partial stdout token=secret-str",
        stderr="Authorization: Bearer abc.def",
    )
    with patch(
        "agent_loop.adapters.claude.cli.subprocess.run",
        side_effect=timed_out,
    ):
        result = runner.run(
            ("claude", "mcp", "list"),
            cwd=_WORKSPACE,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
    assert result.timed_out is True
    assert result.returncode == _TIMEOUT_RETURNCODE
    assert "secret-str" not in result.stdout
    assert "abc.def" not in result.stderr
    assert "partial stdout" in result.stdout


def test_subprocess_cli_runner_timeout_with_bytes_and_invalid_utf8() -> None:
    """TimeoutExpired bytes are decoded safely and redacted."""
    runner = SubprocessCliRunner()
    timed_out = subprocess.TimeoutExpired(
        cmd=["claude"],
        timeout=_TIMEOUT_SECONDS,
        output=b"stdout api_key=bytes-secret\xff",
        stderr=b"password=bytes-pass",
    )
    with patch(
        "agent_loop.adapters.claude.cli.subprocess.run",
        side_effect=timed_out,
    ):
        result = runner.run(
            ("claude", "plugin", "list"),
            cwd=_WORKSPACE,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
    assert result.timed_out is True
    assert "bytes-secret" not in result.stdout
    assert "bytes-pass" not in result.stderr
    assert "stdout" in result.stdout


def test_subprocess_cli_runner_timeout_with_none_captures() -> None:
    """TimeoutExpired with None stdout/stderr still returns a timed_out result."""
    runner = SubprocessCliRunner()
    timed_out = subprocess.TimeoutExpired(
        cmd=["claude"],
        timeout=_TIMEOUT_SECONDS,
        output=None,
        stderr=None,
    )
    with patch(
        "agent_loop.adapters.claude.cli.subprocess.run",
        side_effect=timed_out,
    ):
        result = runner.run(
            ("claude", "auth", "status", "--json"),
            cwd=_WORKSPACE,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
    assert result.timed_out is True
    assert result.stdout == ""
    assert result.stderr == "timed out"
