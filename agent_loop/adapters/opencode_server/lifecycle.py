"""Explicit attach/managed lifecycle for an OpenCode Server process.

Attach mode never owns the process. Managed mode starts exactly one child in a
new process group, captures its printed URL/password, waits for readiness, and
on shutdown terminates only that owned process group.
"""

from __future__ import annotations

import contextlib
import http.client
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import IO
from typing import TYPE_CHECKING
from typing import Self
from urllib.parse import urlsplit

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.transport import HttpTransport

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_DEFAULT_STARTUP_TIMEOUT = 30.0
_DEFAULT_READINESS_TIMEOUT = 15.0
_STOP_GRACE_SECONDS = 5.0
_LINE_QUEUE_TIMEOUT = 0.2
_READINESS_POLL_SECONDS = 0.25
_MAX_PORT = 65535
_LISTENING_PREFIX = "server listening on "
_CREDENTIAL_LINE_PREFIX = "server password "


@dataclass(frozen=True, slots=True)
class AttachEndpoint:
    """An externally owned server endpoint supplied by the operator."""

    base_url: str
    password: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedEndpoint:
    """The endpoint of a server process owned by this adapter instance."""

    base_url: str
    password: str


@dataclass(frozen=True, slots=True)
class ManagedServerConfig:
    """Explicit configuration for an owned ``opencode serve`` process."""

    binary: str = "opencode"
    hostname: str = "127.0.0.1"
    port: int = 0
    working_directory: str | None = None
    startup_timeout_seconds: float = _DEFAULT_STARTUP_TIMEOUT
    readiness_timeout_seconds: float = _DEFAULT_READINESS_TIMEOUT
    environment: Mapping[str, str] | None = None


class AttachedServer:
    """Lifecycle facade for a server owned outside Agent Loop."""

    owned = False

    def __init__(self, endpoint: AttachEndpoint) -> None:
        _validate_connect_url(endpoint.base_url)
        self._endpoint = endpoint

    @property
    def base_url(self) -> str:
        """Return the external server URL."""
        return self._endpoint.base_url

    @property
    def password(self) -> str | None:
        """Return the configured password (never logged)."""
        return self._endpoint.password

    @property
    def running(self) -> bool:
        """Attach mode cannot prove process state from configuration alone."""
        return True

    def start(self) -> None:
        """No-op: attached servers are started by their owner."""

    def stop(self) -> None:
        """No-op: Agent Loop never stops a server it does not own."""


class ManagedServer:
    """Owned ``opencode serve`` child process with bounded startup and cleanup."""

    owned = True

    def __init__(self, config: ManagedServerConfig | None = None) -> None:
        resolved = config or ManagedServerConfig()
        if resolved.hostname not in _LOOPBACK_HOSTS:
            message = f"managed OpenCode Server must bind to loopback, got {resolved.hostname!r}"
            raise ValueError(message)
        if not 0 <= resolved.port <= _MAX_PORT:
            message = "managed OpenCode Server port must be between 0 and 65535"
            raise ValueError(message)
        self._config = resolved
        self._process: subprocess.Popen[str] | None = None
        self._endpoint: ManagedEndpoint | None = None
        self._reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """Return whether the owned child is still alive."""
        process = self._process
        return process is not None and process.poll() is None

    @property
    def pid(self) -> int | None:
        """Return the owned child PID while it is alive."""
        process = self._process
        return None if process is None else process.pid

    @property
    def base_url(self) -> str | None:
        """Return the effective URL once startup succeeded."""
        return None if self._endpoint is None else self._endpoint.base_url

    @property
    def password(self) -> str | None:
        """Return the captured runtime password once startup succeeded."""
        return None if self._endpoint is None else self._endpoint.password

    def start(self) -> ManagedEndpoint:
        """Start the child, capture its endpoint, and wait for readiness."""
        if self._endpoint is not None and self.running:
            return self._endpoint
        self.stop()
        argv = (
            self._config.binary,
            "serve",
            "--hostname",
            self._config.hostname,
            "--port",
            str(self._config.port),
        )
        environment = dict(os.environ)
        if self._config.environment is not None:
            environment.update(self._config.environment)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                list(argv),
                cwd=self._config.working_directory,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise OpenCodeServerError(
                ServerFailureCode.SERVER_START_FAILED,
                "opencode binary was not found",
            ) from exc
        except OSError as exc:
            raise OpenCodeServerError(
                ServerFailureCode.SERVER_START_FAILED,
                redact_secret(f"failed to start OpenCode Server: {exc}"),
            ) from exc
        self._process = process
        try:
            base_url, password = self._capture_endpoint(process)
            self._wait_until_ready(base_url, password)
        except OpenCodeServerError:
            self.stop()
            raise
        self._endpoint = ManagedEndpoint(base_url=base_url, password=password)
        return self._endpoint

    def stop(self) -> None:
        """Terminate only the owned process group; idempotent and orphan-safe."""
        process = self._process
        self._process = None
        self._endpoint = None
        if process is None:
            return
        if process.poll() is None:
            _terminate_process_group(process)
        _close_process_pipes(process)
        self._reader = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.stop()

    def _capture_endpoint(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        if process.stdout is None:
            raise OpenCodeServerError(
                ServerFailureCode.SERVER_START_FAILED,
                "managed server stdout is unavailable",
            )
        line_queue: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(
            target=_pump_lines,
            args=(process.stdout, line_queue),
            daemon=True,
            name="opencode-server-stdout",
        )
        self._reader.start()
        deadline = time.monotonic() + self._config.startup_timeout_seconds
        base_url: str | None = None
        password: str | None = None
        observed: list[str] = []
        while time.monotonic() < deadline:
            line = _next_line(line_queue)
            if line is None:
                exit_code = process.poll()
                detail = " | ".join(observed[-3:])
                message = redact_secret(
                    f"managed server exited before readiness (exit={exit_code}): {detail}",
                    password,
                )
                raise OpenCodeServerError(ServerFailureCode.SERVER_START_FAILED, message)
            observed.append(line)
            if line.startswith(_LISTENING_PREFIX):
                base_url = _validated_listening_url(line.removeprefix(_LISTENING_PREFIX))
            elif line.startswith(_CREDENTIAL_LINE_PREFIX):
                password = line.removeprefix(_CREDENTIAL_LINE_PREFIX).strip() or None
            if base_url is not None and password is not None:
                return base_url, password
        raise OpenCodeServerError(
            ServerFailureCode.SERVER_START_FAILED,
            f"managed server did not report URL and password within {self._config.startup_timeout_seconds}s",
        )

    def _wait_until_ready(self, base_url: str, password: str) -> None:
        deadline = time.monotonic() + self._config.readiness_timeout_seconds
        last_error: OpenCodeServerError | None = None
        while time.monotonic() < deadline:
            transport = HttpTransport(base_url, password=password, timeout_seconds=2.0)
            try:
                response = transport.request_json("GET", "/api/info")
            except OpenCodeServerError as exc:
                last_error = exc
                if exc.code is ServerFailureCode.AUTHENTICATION_FAILED:
                    raise
            else:
                if response.status == http.client.OK:
                    return
                last_error = OpenCodeServerError(
                    ServerFailureCode.SERVER_START_FAILED,
                    f"readiness check returned status {response.status}",
                )
            time.sleep(_READINESS_POLL_SECONDS)
        detail = last_error.message if last_error is not None else "no readiness response"
        raise OpenCodeServerError(
            ServerFailureCode.SERVER_START_FAILED,
            f"managed server readiness timed out: {detail}",
        )


def _pump_lines(stream: IO[str], target: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            target.put(str(line).rstrip("\r\n"))
    except (OSError, ValueError):
        # The child closed its stdout (normal during shutdown); report EOF.
        target.put(None)
        return
    target.put(None)


def _next_line(line_queue: queue.Queue[str | None]) -> str | None:
    try:
        return line_queue.get(timeout=_LINE_QUEUE_TIMEOUT)
    except queue.Empty:
        return ""


def _validated_listening_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK_HOSTS:
        message = f"managed server reported a non-loopback URL: {url!r}"
        raise OpenCodeServerError(ServerFailureCode.SERVER_START_FAILED, message)
    return url


def _validate_connect_url(value: str) -> None:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        message = "attached server URL must be an absolute http(s) URL"
        raise ValueError(message)
    if parts.username or parts.password:
        message = "attached server URL must not embed credentials"
        raise ValueError(message)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _force_kill(process)


def _force_kill(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_STOP_GRACE_SECONDS)


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    for stream in (process.stdout, process.stderr, process.stdin):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()
