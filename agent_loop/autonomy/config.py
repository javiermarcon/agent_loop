"""Configuration owned by Agent Loop, independent of the target repository."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from agent_loop.autonomy.errors import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_COMMAND_TIMEOUT_SECONDS = 60 * 60
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_OUTPUT_CHARS = 20_000
# Linux caps a single argv entry at MAX_ARG_STRLEN (128 KiB). AUTO keeps a
# conservative margin below that limit so the prompt never becomes a giant
# argument, while small prompts retain positional compatibility with CLIs that
# only accept a message argument.
PROMPT_ARGUMENT_MAX_BYTES = 32 * 1024


class PromptTransport(StrEnum):
    """How the constructed prompt is delivered to the external agent."""

    AUTO = "auto"
    STDIN = "stdin"
    ARGUMENT = "argument"


@dataclass(frozen=True, slots=True)
class AutonomyConfig:
    """Internal runtime policy for one autonomous invocation.

    ``agent_command`` is an explicit, user-supplied executable plus arguments.
    It is never derived from target-repository documentation. ``target_env``
    contains only the environment variables the caller explicitly authorizes
    for the target process.
    """

    agent_command: tuple[str, ...] = ()
    agent_prompt_transport: PromptTransport = PromptTransport.AUTO
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    target_environment: tuple[tuple[str, str], ...] = ()
    log_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            message = "max_iterations must be at least 1"
            raise ConfigurationError(message)
        if not isinstance(self.agent_prompt_transport, PromptTransport):
            message = "agent_prompt_transport must be a PromptTransport value"
            raise ConfigurationError(message)
        if self.command_timeout_seconds < 1:
            message = "command_timeout_seconds must be at least 1"
            raise ConfigurationError(message)
        if self.max_output_chars < 1:
            message = "max_output_chars must be at least 1"
            raise ConfigurationError(message)
        for part in self.agent_command:
            if not part or "\x00" in part:
                message = "agent_command parts must be non-empty and NUL-free"
                raise ConfigurationError(message)
        for key, value in self.target_environment:
            if not key or "\x00" in key or "\x00" in value:
                message = "target_environment entries must be NUL-free"
                raise ConfigurationError(message)

    @property
    def target_env(self) -> dict[str, str]:
        """Return the explicitly authorized target environment mapping."""
        return dict(self.target_environment)
