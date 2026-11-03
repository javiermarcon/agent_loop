"""Tool taxonomy and discovery result models for the target repository."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ToolKind(StrEnum):
    """Supported tool families for the Django slice."""

    BANDIT = "bandit"
    BEHAVE = "behave"
    DOCKER_COMPOSE = "docker_compose"
    GIT = "git"
    MYPY = "mypy"
    PRE_COMMIT = "pre_commit"
    PYLINT = "pylint"
    PYTEST = "pytest"
    RUFF = "ruff"


class ToolState(StrEnum):
    """Tri-state availability of a configured or unconfigured tool."""

    CONFIGURED_AVAILABLE = "configured_available"
    CONFIGURED_UNAVAILABLE = "configured_unavailable"
    NOT_CONFIGURED = "not_configured"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ToolDiscovery:
    """One tool's configuration and availability evidence."""

    kind: ToolKind
    state: ToolState
    config_paths: tuple[str, ...] = ()
    executable: str | None = None
    command_prefix: tuple[str, ...] = ()
    container_service: str | None = None
    details: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_available(self) -> bool:
        """Return whether the tool is configured and executable."""
        return self.state is ToolState.CONFIGURED_AVAILABLE

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "kind": self.kind.value,
            "state": self.state.value,
            "config_paths": list(self.config_paths),
            "executable": self.executable,
            "command_prefix": list(self.command_prefix),
            "container_service": self.container_service,
            "details": list(self.details),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ToolInventory:
    """Deterministically ordered discovery results for every supported tool."""

    entries: tuple[ToolDiscovery, ...]

    def get(self, kind: ToolKind) -> ToolDiscovery | None:
        """Return the discovery entry for a tool kind, if present."""
        for entry in self.entries:
            if entry.kind is kind:
                return entry
        return None

    def configured(self) -> tuple[ToolDiscovery, ...]:
        """Return tools that the target repository configures."""
        return tuple(
            entry
            for entry in self.entries
            if entry.state in {ToolState.CONFIGURED_AVAILABLE, ToolState.CONFIGURED_UNAVAILABLE}
        )

    def available(self) -> tuple[ToolDiscovery, ...]:
        """Return tools that are configured and executable by discovery.

        Execution additionally requires a registered adapter. A tool can be
        available here and still be reported as UNKNOWN by ValidationRunner if
        no adapter exists for its kind.
        """
        return tuple(entry for entry in self.entries if entry.state is ToolState.CONFIGURED_AVAILABLE)

    def to_dict(self) -> list[dict[str, object]]:
        """Return a JSON-serializable representation."""
        return [entry.to_dict() for entry in self.entries]


MODULE_NAMES: dict[ToolKind, str] = {
    ToolKind.BANDIT: "bandit",
    ToolKind.BEHAVE: "behave",
    ToolKind.MYPY: "mypy",
    ToolKind.PRE_COMMIT: "pre_commit",
    ToolKind.PYLINT: "pylint",
    ToolKind.PYTEST: "pytest",
    ToolKind.RUFF: "ruff",
}

EXECUTABLE_NAMES: dict[ToolKind, str] = {
    ToolKind.BANDIT: "bandit",
    ToolKind.BEHAVE: "behave",
    ToolKind.MYPY: "mypy",
    ToolKind.PRE_COMMIT: "pre-commit",
    ToolKind.PYLINT: "pylint",
    ToolKind.PYTEST: "pytest",
    ToolKind.RUFF: "ruff",
}

DEFAULT_TOOL_ORDER: tuple[ToolKind, ...] = (
    ToolKind.GIT,
    ToolKind.PRE_COMMIT,
    ToolKind.RUFF,
    ToolKind.MYPY,
    ToolKind.PYLINT,
    ToolKind.BANDIT,
    ToolKind.PYTEST,
    ToolKind.BEHAVE,
    ToolKind.DOCKER_COMPOSE,
)
