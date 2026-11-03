"""Errors raised by the autonomous agent-loop runtime."""

from __future__ import annotations


class AutonomyError(RuntimeError):
    """Base error for autonomous runtime failures."""


class SecurityError(AutonomyError):
    """Raised when a path or command violates the workspace policy."""


class DiscoveryError(AutonomyError):
    """Raised when required target discovery cannot be completed."""


class ConfigurationError(AutonomyError):
    """Raised when the autonomy configuration is invalid."""
