"""OpenCode Server AgentRuntime adapter (S22)."""

from agent_loop.adapters.opencode_server.constants import ADAPTER_VERSION
from agent_loop.adapters.opencode_server.constants import RUNTIME_ID
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.lifecycle import AttachedServer
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime

__all__ = (
    "ADAPTER_VERSION",
    "RUNTIME_ID",
    "AttachEndpoint",
    "AttachedServer",
    "ManagedServer",
    "ManagedServerConfig",
    "OpenCodeServerError",
    "OpenCodeServerRuntime",
    "ServerFailureCode",
)
