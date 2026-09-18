"""Provider adapters for agent command-line implementations."""

from __future__ import annotations

from typing import Protocol


class AgentLoopApplication(Protocol):
    """Execute one explicit agent-loop application invocation in-process."""

    def __call__(self, argv: tuple[str, ...], /) -> int:
        """Return the lifecycle exit code for explicit application arguments."""


def invoke_agent_loop_application(
    application: AgentLoopApplication,
    agent_command: str,
    remaining_argv: list[str],
    *,
    commit_msg: str | None = None,
) -> int:
    """Build provider-owned lifecycle arguments and execute them in-process."""
    forwarded_argv = ["--agent-command", agent_command]
    if commit_msg is not None:
        forwarded_argv.extend(["--commit_msg", commit_msg])
    forwarded_argv.extend(remaining_argv)
    return application(tuple(forwarded_argv))
