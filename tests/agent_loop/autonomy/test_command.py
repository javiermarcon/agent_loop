"""Command runner tests: capture, failure, timeout and output bounds."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from agent_loop.autonomy.tools.command import CommandOutcome
from agent_loop.autonomy.tools.command import SubprocessCommandRunner

_EXPECTED_FAILURE_EXIT = 3
_STDIN_FAILURE_EXIT = 9
_LARGE_OUTPUT = 500
_OUTPUT_LIMIT = 50


def _run(script: str, *, timeout: int | None = 10, max_output: int = 20_000) -> CommandOutcome:
    runner = SubprocessCommandRunner(max_output_chars=max_output)
    return runner.run(
        (sys.executable, "-c", script),
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin", "PYTHONNOUSERSITE": "1"},
        timeout_seconds=timeout,
    )


def test_success_captures_stdout_and_exit_code() -> None:
    """A zero exit is captured with stdout and no error."""
    outcome = _run("print('hello')")
    assert outcome.exit_code == 0
    assert outcome.stdout.strip() == "hello"
    assert outcome.error is None
    assert outcome.timed_out is False


def test_failure_captures_exit_code_and_stderr() -> None:
    """A non-zero exit is preserved as a failure, not an execution error."""
    outcome = _run("import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert outcome.exit_code == _EXPECTED_FAILURE_EXIT
    assert outcome.stderr == "boom"
    assert outcome.error is None


def test_missing_executable_is_an_explicit_error(tmp_path: Path) -> None:
    """A missing binary is reported instead of being treated as a failed check."""
    runner = SubprocessCommandRunner()
    outcome = runner.run(
        (str(tmp_path / "missing-binary"),),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
    )
    assert outcome.exit_code is None
    assert outcome.error == "executable not found"


def test_timeout_is_distinguished_from_failure() -> None:
    """Timeouts set the timeout flag and do not fabricate an exit code."""
    outcome = _run("import time; time.sleep(5)", timeout=1)
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert outcome.error == "command timed out"


def test_output_is_truncated_to_the_configured_limit() -> None:
    """Large output is bounded while preserving a head/tail marker."""
    outcome = _run(f"print('a' * {_LARGE_OUTPUT})", max_output=_OUTPUT_LIMIT)
    assert len(outcome.stdout) < _LARGE_OUTPUT
    assert "truncated" in outcome.stdout


def test_environment_is_exactly_the_provided_mapping() -> None:
    """The runner does not inherit Agent Loop environment implicitly."""
    runner = SubprocessCommandRunner()
    outcome = runner.run(
        (sys.executable, "-c", "import os; print(os.environ.get('SHIPII_TOKEN', 'absent'))"),
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin", "PYTHONNOUSERSITE": "1"},
        timeout_seconds=10,
    )
    assert outcome.stdout.strip() == "absent"


_STDIN_ECHO = """
import hashlib, json, sys
data = sys.stdin.read()
print(json.dumps({
    "length": len(data),
    "encoding": "utf-8",
    "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
    "argv": sys.argv[1:],
    "has_newline": chr(10) in data,
}))
"""


def _run_with_stdin(prompt: str, *, timeout: int | None = 30) -> CommandOutcome:
    runner = SubprocessCommandRunner(max_output_chars=1_000_000)
    return runner.run(
        (sys.executable, "-c", _STDIN_ECHO),
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin", "PYTHONNOUSERSITE": "1"},
        timeout_seconds=timeout,
        stdin_text=prompt,
    )


def test_stdin_delivers_prompt_below_max_arg_length() -> None:
    """A small prompt is delivered intact through the pipe."""
    prompt = "small prompt with\nnewlines and 'quotes'"
    outcome = _run_with_stdin(prompt)
    payload = json.loads(outcome.stdout)
    assert outcome.exit_code == 0
    assert payload["length"] == len(prompt)
    assert payload["sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert payload["argv"] == []


def test_stdin_delivers_prompt_above_max_arg_length() -> None:
    """A prompt above 128 KiB is delivered completely and is not in argv."""
    prompt = ("línea con ñ, 漢字 y 🙂\n" * 8_000)[: 140 * 1024]
    outcome = _run_with_stdin(prompt)
    payload = json.loads(outcome.stdout)
    assert payload["length"] == len(prompt)
    assert payload["sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert payload["argv"] == []
    assert payload["has_newline"] is True


def test_stdin_delivers_prompt_of_at_least_300_kib_without_truncation() -> None:
    """The former E2BIG case is reproduced and delivered byte-for-byte."""
    prompt = ("0123456789abcdef" * 20_000) + "終"
    assert len(prompt.encode("utf-8")) >= 300 * 1024
    outcome = _run_with_stdin(prompt)
    payload = json.loads(outcome.stdout)
    assert payload["length"] == len(prompt)
    assert payload["sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert payload["argv"] == []


def test_stdin_delivers_empty_prompt() -> None:
    """An empty payload is valid and closes stdin normally."""
    outcome = _run_with_stdin("")
    payload = json.loads(outcome.stdout)
    assert payload["length"] == 0
    assert payload["sha256"] == hashlib.sha256(b"").hexdigest()


def test_stdin_child_failure_is_preserved() -> None:
    """A child that reads stdin and exits non-zero remains an ordinary failure."""
    runner = SubprocessCommandRunner()
    outcome = runner.run(
        (sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(9)"),
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin"},
        timeout_seconds=10,
        stdin_text="payload" * 1000,
    )
    assert outcome.exit_code == _STDIN_FAILURE_EXIT
    assert outcome.error is None


def test_stdin_timeout_is_reported_and_resources_are_released() -> None:
    """A timeout while streaming input returns a timeout outcome, not E2BIG."""
    runner = SubprocessCommandRunner()
    outcome = runner.run(
        (sys.executable, "-c", "import time; time.sleep(5)"),
        cwd=Path.cwd(),
        env={"PATH": "/usr/bin"},
        timeout_seconds=1,
        stdin_text="x" * 10_000,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert outcome.error == "command timed out"


def test_missing_executable_with_stdin_is_an_explicit_error(tmp_path: Path) -> None:
    """Executable resolution errors are reported before any prompt is consumed."""
    runner = SubprocessCommandRunner()
    outcome = runner.run(
        (str(tmp_path / "missing-binary"),),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_seconds=5,
        stdin_text="payload",
    )
    assert outcome.error == "executable not found"
