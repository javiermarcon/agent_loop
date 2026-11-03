"""External agent invocation tests."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from agent_loop.autonomy.config import PromptTransport
from agent_loop.autonomy.execution.agent import CommandAgentInvoker
from agent_loop.autonomy.tools.base import ToolStatus
from agent_loop.autonomy.tools.command import CommandOutcome
from tests.agent_loop.autonomy.support import RecordingRunner
from tests.agent_loop.autonomy.support import make_context
from tests.agent_loop.autonomy.support import make_project

_AGENT_FAILURE_EXIT = 7
_LARGE_PROMPT_BYTES = 300 * 1024

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_agent_command_is_unavailable(tmp_path: Path) -> None:
    """No command means no execution, never a simulated success."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    result = CommandAgentInvoker((), runner).invoke("prompt", context)
    assert result.status is ToolStatus.UNAVAILABLE
    assert runner.calls == []


def test_agent_success_appends_prompt_and_uses_target_cwd(tmp_path: Path) -> None:
    """The prompt is the last argument and execution runs in the target root."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    result = CommandAgentInvoker(("fake-agent", "--flag"), runner).invoke("do work", context)
    command, cwd, env, _timeout = runner.calls[0]
    assert command == ("fake-agent", "--flag", "do work")
    assert cwd == context.workspace.root
    assert env["VIRTUAL_ENV"] == str(context.workspace.root / ".venv")
    assert result.status is ToolStatus.PASS


def test_agent_failure_preserves_exit_and_output(tmp_path: Path) -> None:
    """A non-zero agent exit is a structured failure."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("fake-agent",),
                cwd=str(context.workspace.root),
                exit_code=7,
                stdout="out",
                stderr="err",
                duration_seconds=0.1,
            ),
        ],
    )
    result = CommandAgentInvoker(("fake-agent",), runner).invoke("prompt", context)
    assert result.status is ToolStatus.FAIL
    assert result.exit_code == _AGENT_FAILURE_EXIT
    assert result.stderr == "err"


def test_agent_timeout_is_unknown_not_failure(tmp_path: Path) -> None:
    """A timeout cannot be classified as a successful or ordinary failed run."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner(
        outcomes=[
            CommandOutcome(
                command=("fake-agent",),
                cwd=str(context.workspace.root),
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=1.0,
                timed_out=True,
                error="command timed out",
            ),
        ],
    )
    result = CommandAgentInvoker(("fake-agent",), runner).invoke("prompt", context)
    assert result.status is ToolStatus.UNKNOWN
    assert result.message == "command timed out"


def test_large_prompt_uses_stdin_and_is_not_appended_to_argv(tmp_path: Path) -> None:
    """A prompt above MAX_ARG_STRLEN is streamed, never passed as one argument."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    prompt = "meta " + ("áéíóú\n'\"$`" * ((_LARGE_PROMPT_BYTES // 14) + 1))
    result = CommandAgentInvoker(("fake-agent", "--flag"), runner).invoke(prompt, context)
    command, _cwd, _env, _timeout = runner.calls[0]
    assert command == ("fake-agent", "--flag")
    assert prompt not in command
    assert runner.inputs == [prompt]
    assert result.prompt_transport == PromptTransport.STDIN.value
    assert result.prompt_bytes == len(prompt.encode("utf-8"))
    assert result.prompt_sha256 == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert result.status is ToolStatus.PASS


def test_unicode_prompt_is_preserved_through_stdin(tmp_path: Path) -> None:
    """Multi-byte characters and shell metacharacters survive transport unchanged."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    prompt = "ñandú 漢字 🙂\n$(rm -rf /); `echo no`; 'quote' \"double\""
    CommandAgentInvoker(("fake-agent",), runner, transport=PromptTransport.STDIN).invoke(prompt, context)
    assert runner.inputs == [prompt]
    assert runner.calls[0][0] == ("fake-agent",)


def test_explicit_argument_transport_keeps_legacy_contract(tmp_path: Path) -> None:
    """Adapters that require a positional message can still opt into argv delivery."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    result = CommandAgentInvoker(("fake-agent",), runner, transport=PromptTransport.ARGUMENT).invoke(
        "small prompt",
        context,
    )
    assert runner.calls[0][0] == ("fake-agent", "small prompt")
    assert runner.inputs == [None]
    assert result.prompt_transport == PromptTransport.ARGUMENT.value


def test_empty_prompt_is_allowed_through_stdin(tmp_path: Path) -> None:
    """An explicit stdin contract can deliver an empty payload without a fake argument."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    result = CommandAgentInvoker(("fake-agent",), runner, transport=PromptTransport.STDIN).invoke("", context)
    assert runner.calls[0][0] == ("fake-agent",)
    assert runner.inputs == [""]
    assert result.prompt_bytes == 0
    assert result.status is ToolStatus.PASS


def test_prompt_containing_nul_is_forced_through_stdin(tmp_path: Path) -> None:
    """NUL cannot exist in argv, so AUTO must resolve it to stdin."""
    context = make_context(make_project(tmp_path / "repo"))
    runner = RecordingRunner()
    CommandAgentInvoker(("fake-agent",), runner).invoke("before\x00after", context)
    assert runner.inputs == ["before\x00after"]
    assert runner.calls[0][0] == ("fake-agent",)
