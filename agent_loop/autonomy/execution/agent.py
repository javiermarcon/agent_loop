"""Invoke a user-authorized external agent command inside the target repo."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

from agent_loop.autonomy.config import PROMPT_ARGUMENT_MAX_BYTES
from agent_loop.autonomy.config import PromptTransport
from agent_loop.autonomy.tools.base import ToolStatus

if TYPE_CHECKING:
    from agent_loop.autonomy.tools.base import ToolContext
    from agent_loop.autonomy.tools.command import CommandOutcome
    from agent_loop.autonomy.tools.command import CommandRunner


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Structured outcome of one external agent invocation."""

    status: ToolStatus
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    message: str
    prompt_transport: str = ""
    prompt_bytes: int = 0
    prompt_sha256: str = ""

    @property
    def succeeded(self) -> bool:
        """Return whether the agent command exited successfully."""
        return self.status is ToolStatus.PASS

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "status": self.status.value,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
            "prompt_transport": self.prompt_transport,
            "prompt_bytes": self.prompt_bytes,
            "prompt_sha256": self.prompt_sha256,
        }


class AgentInvoker(Protocol):
    """Boundary for an external coding agent."""

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        """Run the agent with the prompt and return a structured result."""


class CommandAgentInvoker:
    """Invoke an explicit command and deliver the prompt through a bounded channel.

    ``ARGUMENT`` preserves the historical contract of appending the prompt as
    the last argument for CLIs that require a positional message. ``STDIN``
    streams the prompt through a pipe. ``AUTO`` selects stdin once the prompt
    would exceed a conservative fraction of ``MAX_ARG_STRLEN``; this keeps
    small invocations compatible while making large prompts impossible to
    deliver as one oversized ``argv`` entry.
    """

    def __init__(
        self,
        command: tuple[str, ...],
        runner: CommandRunner,
        *,
        transport: PromptTransport = PromptTransport.AUTO,
    ) -> None:
        self._command = command
        self._runner = runner
        self._transport = transport

    def invoke(self, prompt: str, context: ToolContext) -> AgentRunResult:
        """Execute the configured agent command in the target workspace."""
        if not self._command:
            return AgentRunResult(
                status=ToolStatus.UNAVAILABLE,
                command=(),
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                message="no agent command configured; use --agent-command",
            )
        transport = resolve_prompt_transport(prompt, self._transport)
        command = self._command if transport is PromptTransport.STDIN else (*self._command, prompt)
        stdin_text = prompt if transport is PromptTransport.STDIN else None
        outcome = self._runner.run(
            command,
            cwd=context.workspace.root,
            env=context.environment.variables,
            timeout_seconds=context.config.command_timeout_seconds,
            stdin_text=stdin_text,
        )
        return _interpret(
            command,
            outcome,
            transport=transport,
            prompt=prompt,
        )


def resolve_prompt_transport(prompt: str, configured: PromptTransport) -> PromptTransport:
    """Resolve the effective transport, never allowing a giant argv entry."""
    if configured is not PromptTransport.AUTO:
        return configured
    if "\x00" in prompt:
        return PromptTransport.STDIN
    if len(prompt.encode("utf-8")) > PROMPT_ARGUMENT_MAX_BYTES:
        return PromptTransport.STDIN
    return PromptTransport.ARGUMENT


def _interpret(
    command: tuple[str, ...],
    outcome: CommandOutcome,
    *,
    transport: PromptTransport,
    prompt: str,
) -> AgentRunResult:
    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if outcome.error is not None:
        status = ToolStatus.UNKNOWN if outcome.timed_out else ToolStatus.UNAVAILABLE
        return AgentRunResult(
            status=status,
            command=command,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            duration_seconds=outcome.duration_seconds,
            message=outcome.error,
            prompt_transport=transport.value,
            prompt_bytes=prompt_bytes,
            prompt_sha256=prompt_sha256,
        )
    status = ToolStatus.PASS if outcome.exit_code == 0 else ToolStatus.FAIL
    return AgentRunResult(
        status=status,
        command=command,
        exit_code=outcome.exit_code,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        duration_seconds=outcome.duration_seconds,
        message=f"agent exited with code {outcome.exit_code}",
        prompt_transport=transport.value,
        prompt_bytes=prompt_bytes,
        prompt_sha256=prompt_sha256,
    )
