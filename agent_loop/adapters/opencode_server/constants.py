"""Shared constants for the OpenCode Server adapter."""

from __future__ import annotations

RUNTIME_ID = "opencode-server"
ADAPTER_VERSION = "0.1.0"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 15.0
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 1800.0
DEFAULT_MAX_CONCURRENT_EXECUTIONS = 1
DEFAULT_STOP_GRACE_SECONDS = 5.0

CAPABILITY_RUNTIME = "runtime.opencode.server"
CAPABILITY_MODELS = "runtime.models"
CAPABILITY_AGENTS = "runtime.agents"

TERMINAL_EVENT_TYPES = (
    "session.execution.succeeded",
    "session.execution.failed",
    "session.execution.interrupted",
    "session.idle",
    "permission.asked",
)
