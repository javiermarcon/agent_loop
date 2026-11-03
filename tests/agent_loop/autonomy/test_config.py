"""Autonomy configuration validation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agent_loop.autonomy.config import AutonomyConfig
from agent_loop.autonomy.config import PromptTransport
from agent_loop.autonomy.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DEFAULT_MAX_ITERATIONS = 3
_DEFAULT_TIMEOUT_SECONDS = 3600


def test_defaults_are_explicit_and_safe() -> None:
    """The default configuration does not authorize an agent command."""
    config = AutonomyConfig()
    assert config.agent_command == ()
    assert config.agent_prompt_transport is PromptTransport.AUTO
    assert config.max_iterations == _DEFAULT_MAX_ITERATIONS
    assert config.command_timeout_seconds == _DEFAULT_TIMEOUT_SECONDS
    assert config.target_env == {}


def test_target_environment_is_materialized_as_a_copy() -> None:
    """The mapping is derived from immutable pairs."""
    config = AutonomyConfig(target_environment=(("TARGET_FLAG", "1"),))
    environment = config.target_env
    environment["TARGET_FLAG"] = "2"
    assert config.target_env == {"TARGET_FLAG": "1"}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AutonomyConfig(max_iterations=0),
        lambda: AutonomyConfig(command_timeout_seconds=0),
        lambda: AutonomyConfig(max_output_chars=0),
        lambda: AutonomyConfig(agent_command=("",)),
        lambda: AutonomyConfig(agent_command=("agent\x00",)),
        lambda: AutonomyConfig(target_environment=(("KEY", "value\x00"),)),
    ],
)
def test_invalid_configuration_fails_closed(factory: Callable[[], AutonomyConfig]) -> None:
    """Invalid numeric or NUL-containing values are rejected."""
    with pytest.raises(ConfigurationError):
        factory()


def test_log_dir_is_preserved(tmp_path: Path) -> None:
    """An explicit log directory remains part of the configuration."""
    config = AutonomyConfig(log_dir=tmp_path / "logs")
    assert config.log_dir == tmp_path / "logs"
