"""OpenCode Server AgentRuntime adapter (S22).

This adapter is separate from the S09 CLI discovery adapter. It implements the
existing ``AgentRuntime`` port with a documented public HTTP/SSE transport and
never imports OpenCode types into domain code.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode_server.client import OpenCodeServerClient
from agent_loop.adapters.opencode_server.constants import ADAPTER_VERSION
from agent_loop.adapters.opencode_server.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS
from agent_loop.adapters.opencode_server.constants import DEFAULT_MAX_CONCURRENT_EXECUTIONS
from agent_loop.adapters.opencode_server.constants import DEFAULT_REQUEST_TIMEOUT_SECONDS
from agent_loop.adapters.opencode_server.constants import RUNTIME_ID
from agent_loop.adapters.opencode_server.discovery import build_inventory
from agent_loop.adapters.opencode_server.discovery import managed_not_started_inventory
from agent_loop.adapters.opencode_server.discovery import not_configured_inventory
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.execution import ExecutionPolicy
from agent_loop.adapters.opencode_server.execution import execute_request
from agent_loop.adapters.opencode_server.execution import failure_result
from agent_loop.adapters.opencode_server.lifecycle import AttachedServer
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.version import VersionCompatibility
from agent_loop.adapters.opencode_server.version import VersionPolicy
from agent_loop.adapters.opencode_server.version import classify_server_version
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_loop.adapters.opencode_server.client import ServerClient
    from agent_loop.domain.capabilities import RuntimeInventory
    from agent_loop.ports.agent_runtime import AgentExecutionRequest
    from agent_loop.ports.agent_runtime import DiscoveryContext


@dataclass(frozen=True, slots=True)
class RuntimeOptions:
    """Optional runtime policy and diagnostics configuration."""

    version_policy: VersionPolicy | None = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    execution_timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    max_concurrent_executions: int = DEFAULT_MAX_CONCURRENT_EXECUTIONS
    auto_start_managed: bool = True
    on_session_started: Callable[[str], None] | None = None
    now: datetime | None = None


class OpenCodeServerRuntime:
    """AgentRuntime implementation backed by one OpenCode Server endpoint."""

    def __init__(
        self,
        *,
        endpoint: AttachEndpoint | None = None,
        managed: ManagedServer | None = None,
        client: ServerClient | None = None,
        options: RuntimeOptions | None = None,
    ) -> None:
        resolved = options or RuntimeOptions()
        if sum(item is not None for item in (endpoint, managed, client)) > 1:
            message = "at most one of endpoint, managed, or client may be provided"
            raise ValueError(message)
        if resolved.max_concurrent_executions < 1:
            message = "max_concurrent_executions must be positive"
            raise ValueError(message)
        self._lifecycle = _lifecycle_for(endpoint, managed)
        self._client = client
        self._client_key: tuple[str, str | None] | None = None
        self._version_policy = resolved.version_policy or VersionPolicy()
        self._request_timeout_seconds = resolved.request_timeout_seconds
        self._execution_timeout_seconds = resolved.execution_timeout_seconds
        self._auto_start_managed = resolved.auto_start_managed
        self._semaphore = threading.BoundedSemaphore(resolved.max_concurrent_executions)
        self._lifecycle_lock = threading.Lock()
        self._on_session_started = resolved.on_session_started
        self._now = resolved.now
        self._identity = RuntimeIdentity(runtime_id=RUNTIME_ID)

    @classmethod
    def attached(
        cls,
        base_url: str,
        *,
        password: str | None = None,
        options: RuntimeOptions | None = None,
    ) -> OpenCodeServerRuntime:
        """Build an attach-mode runtime for an externally owned server."""
        return cls(
            endpoint=AttachEndpoint(base_url=base_url, password=password),
            options=options,
        )

    @property
    def identity(self) -> RuntimeIdentity:
        """Return the stable runtime identity for this adapter."""
        return self._identity

    @property
    def adapter_version(self) -> str:
        """Return the adapter implementation version."""
        return ADAPTER_VERSION

    def discover(self, context: DiscoveryContext) -> RuntimeInventory:
        """Probe only documented read-only endpoints; never start a process."""
        observed_at = self._now or datetime.now(tz=UTC)
        client = self._discovery_client()
        if client is None:
            if isinstance(self._lifecycle, ManagedServer):
                return managed_not_started_inventory(
                    observed_at=observed_at,
                    project_context=context.project_context,
                )
            return not_configured_inventory(
                observed_at=observed_at,
                project_context=context.project_context,
            )
        return build_inventory(
            client,
            observed_at=observed_at,
            project_context=context.project_context,
            credentials_configured=client.credentials_configured,
            policy=self._version_policy,
        )

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        """Execute one request through the server and normalize the outcome."""
        return self._execute(request, cancel_event=None)

    def execute_with_cancellation(
        self,
        request: AgentExecutionRequest,
        cancel_event: threading.Event,
    ) -> AgentExecutionResult:
        """Execute while honoring an external cancellation signal."""
        return self._execute(request, cancel_event=cancel_event)

    def ensure_started(self) -> str | None:
        """Start an authorized managed server and return its base URL."""
        lifecycle = self._lifecycle
        if isinstance(lifecycle, ManagedServer):
            with self._lifecycle_lock:
                endpoint = lifecycle.start()
            return endpoint.base_url
        if isinstance(lifecycle, AttachedServer):
            return lifecycle.base_url
        return None

    def interrupt(self, session_ref: str) -> bool:
        """Interrupt an active session owned by this adapter's server."""
        client = self._discovery_client()
        if client is None:
            raise OpenCodeServerError(
                ServerFailureCode.SERVER_NOT_CONFIGURED,
                "cannot interrupt a session without a running server",
            )
        return client.interrupt(session_ref)

    def close(self) -> None:
        """Stop only an owned managed server; attached servers are untouched."""
        with self._lifecycle_lock:
            if self._lifecycle is not None:
                self._lifecycle.stop()
            self._client = None
            self._client_key = None

    def _execute(
        self,
        request: AgentExecutionRequest,
        *,
        cancel_event: threading.Event | None,
    ) -> AgentExecutionResult:
        if not self._semaphore.acquire(timeout=self._execution_timeout_seconds):
            return failure_result(
                self._identity,
                error=OpenCodeServerError(
                    ServerFailureCode.SERVER_UNAVAILABLE,
                    "concurrency limit reached before execution could start",
                ),
                session_ref=None,
                server_version=None,
            )
        try:
            client = self._execution_client()
            info = client.info()
            compatibility = classify_server_version(info.version, self._version_policy)
            if compatibility is not VersionCompatibility.SUPPORTED:
                error = OpenCodeServerError(
                    ServerFailureCode.UNSUPPORTED_VERSION,
                    f"server version {info.version!r} is {compatibility.value}",
                )
                return failure_result(
                    self._identity,
                    error=error,
                    session_ref=None,
                    server_version=info.version,
                )
            return execute_request(
                client,
                request,
                identity=self._identity,
                server_version=info.version,
                policy=ExecutionPolicy(
                    timeout_seconds=self._execution_timeout_seconds,
                    cancel_event=cancel_event,
                    on_session_started=self._on_session_started,
                ),
            )
        except OpenCodeServerError as exc:
            return failure_result(
                self._identity,
                error=exc,
                session_ref=None,
                server_version=None,
            )
        finally:
            self._semaphore.release()

    def _discovery_client(self) -> ServerClient | None:
        lifecycle = self._lifecycle
        if isinstance(lifecycle, ManagedServer) and not lifecycle.running:
            self._client = None
            self._client_key = None
            return None
        if self._client is not None:
            return self._client
        endpoint = self._available_endpoint()
        if endpoint is None:
            return None
        return self._client_for(*endpoint)

    def _execution_client(self) -> ServerClient:
        client = self._discovery_client()
        if client is not None:
            return client
        lifecycle = self._lifecycle
        if isinstance(lifecycle, ManagedServer) and self._auto_start_managed:
            self.ensure_started()
            client = self._discovery_client()
            if client is not None:
                return client
        raise OpenCodeServerError(
            ServerFailureCode.SERVER_NOT_CONFIGURED,
            "no OpenCode Server endpoint is configured or running",
        )

    def _available_endpoint(self) -> tuple[str, str | None] | None:
        lifecycle = self._lifecycle
        if isinstance(lifecycle, AttachedServer):
            return lifecycle.base_url, lifecycle.password
        if isinstance(lifecycle, ManagedServer) and lifecycle.running:
            base_url = lifecycle.base_url
            if base_url is not None:
                return base_url, lifecycle.password
        return None

    def _client_for(self, base_url: str, password: str | None) -> ServerClient:
        key = (base_url, password)
        client = self._client
        if client is not None and self._client_key == key:
            return client
        client = OpenCodeServerClient(
            base_url,
            password=password,
            timeout_seconds=self._request_timeout_seconds,
        )
        self._client = client
        self._client_key = key
        return client


def _lifecycle_for(
    endpoint: AttachEndpoint | None,
    managed: ManagedServer | None,
) -> AttachedServer | ManagedServer | None:
    if endpoint is not None:
        return AttachedServer(endpoint)
    return managed
