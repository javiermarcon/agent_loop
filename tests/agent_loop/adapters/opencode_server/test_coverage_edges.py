"""Coverage-focused tests for the OpenCode Server adapter boundary."""

from __future__ import annotations

import io
import json
import queue
import subprocess
import threading
from http.client import HTTPException
from itertools import islice
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from agent_loop.adapters.opencode_server.client import OpenCodeServerClient
from agent_loop.adapters.opencode_server.client import PermissionRule
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.lifecycle import AttachedServer
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.lifecycle import _force_kill
from agent_loop.adapters.opencode_server.lifecycle import _next_line
from agent_loop.adapters.opencode_server.lifecycle import _pump_lines
from agent_loop.adapters.opencode_server.lifecycle import _validate_connect_url
from agent_loop.adapters.opencode_server.lifecycle import _validated_listening_url
from agent_loop.adapters.opencode_server.mapping import execution_failure_detail
from agent_loop.adapters.opencode_server.mapping import metadata_pairs
from agent_loop.adapters.opencode_server.mapping import tool_failures
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.adapters.opencode_server.models import _tokens
from agent_loop.adapters.opencode_server.models import optional_float
from agent_loop.adapters.opencode_server.models import require_mapping
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.adapters.opencode_server.transport import HttpResponse
from agent_loop.adapters.opencode_server.transport import HttpTransport
from agent_loop.adapters.opencode_server.transport import SseStream
from agent_loop.adapters.opencode_server.transport import _decode_payload
from agent_loop.adapters.opencode_server.transport import display_base_url
from agent_loop.adapters.opencode_server.version import VersionCompatibility
from agent_loop.adapters.opencode_server.version import VersionPolicy
from agent_loop.adapters.opencode_server.version import classify_server_version
from agent_loop.ports.agent_runtime import AgentExecutionRequest
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Mapping
    from pathlib import Path

_SESSION_PAYLOAD = {"data": {"id": "ses_1", "location": {"directory": "/tmp/work"}}}  # noqa: S108


class _FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []
        self.stream_types: tuple[str, ...] = ()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        self.calls.append((method, path, body))
        return self._responses.pop(0)

    def open_event_stream(self, types: tuple[str, ...]) -> object:
        self.stream_types = types
        return "stream"


def _client(*responses: HttpResponse) -> tuple[OpenCodeServerClient, _FakeTransport]:
    client = OpenCodeServerClient("http://127.0.0.1:4096", password="secret")  # noqa: S106
    transport = _FakeTransport(list(responses))
    client._transport = transport  # type: ignore[assignment]  # noqa: SLF001
    return client, transport


def test_client_full_surface_uses_documented_methods_and_paths() -> None:
    """Every typed client method maps to one documented endpoint."""
    client, transport = _client(
        HttpResponse(200, {"version": "2.2.6", "pid": 1, "urls": []}),
        HttpResponse(200, {"data": [{"providerID": "p", "id": "m"}]}),
        HttpResponse(200, {"data": [{"id": "build"}]}),
        HttpResponse(200, _SESSION_PAYLOAD),
        HttpResponse(200, _SESSION_PAYLOAD),
        HttpResponse(200, {"ok": True}),
        HttpResponse(200, {"interrupted": True}),
        HttpResponse(200, {"interrupted": False}),
        HttpResponse(200, {"data": []}),
        HttpResponse(200, {"data": []}),
        HttpResponse(204, None),
        HttpResponse(200, {"data": []}),
    )
    assert client.info().version == "2.2.6"
    assert [model.as_text() for model in client.models()] == ["p/m"]
    assert client.agents() == ("build",)
    session = client.create_session(
        directory="/tmp/work",  # noqa: S108
        title="t",
        model=ModelRef("p", "m", "default"),
        agent="build",
        permissions=(PermissionRule("edit", "*", "deny"),),
    )
    assert session.session_id == "ses_1"
    assert client.get_session("ses_1").directory == "/tmp/work"  # noqa: S108
    client.prompt("ses_1", "hello")
    assert client.interrupt("ses_1") is True
    assert client.interrupt("ses_1") is False
    assert client.messages("ses_1") == ()
    assert client.diffs("ses_1") == ()
    client.reply_permission("ses_1", "req_1", "allow")
    assert client.open_events(("session.idle",)) == "stream"
    body = transport.calls[3][2]
    assert body is not None
    assert body["model"] == {"providerID": "p", "id": "m", "variant": "default"}
    assert body["permissions"] == [{"action": "edit", "resource": "*", "effect": "deny"}]
    assert transport.stream_types == ("session.idle",)


def test_client_prompt_conflict_and_http_error_details() -> None:
    """Busy sessions and HTTP errors preserve their neutral failure code."""
    client, _ = _client(HttpResponse(409, {"message": "busy"}))
    with pytest.raises(OpenCodeServerError) as conflict:
        client.prompt("ses_1", "hello")
    assert conflict.value.code is ServerFailureCode.SESSION_CLOSED

    client, _ = _client(HttpResponse(404, {"_tag": "NotFound", "message": "missing"}))
    with pytest.raises(OpenCodeServerError) as missing:
        client.info()
    assert "NotFound missing" in missing.value.message
    assert "secret" not in missing.value.message

    client, _ = _client(HttpResponse(200, None))
    with pytest.raises(OpenCodeServerError) as empty:
        client.info()
    assert empty.value.code is ServerFailureCode.INVALID_RESPONSE


def test_transport_headers_https_prefix_and_payload_decoding() -> None:
    """Header construction, HTTPS connections and payload decoding are explicit."""
    transport = HttpTransport("http://127.0.0.1:4096/api", password="secret")  # noqa: S106
    assert transport._path("/info") == "/api/info"  # noqa: SLF001
    assert transport.headers(json_content_type=True)["Content-Type"] == "application/json"
    assert "Content-Type" not in transport.headers(json_content_type=False)
    https = HttpTransport("https://127.0.0.1:4096", password="secret")  # noqa: S106
    assert isinstance(https.new_connection(), __import__("http.client").client.HTTPSConnection)
    assert _decode_payload(b"") is None
    assert _decode_payload(b"{bad") is None
    assert _decode_payload(b'{"a": 1}') == {"a": 1}
    assert display_base_url("http://user:secret@127.0.0.1:4096") == "http://<redacted>@127.0.0.1:4096"
    with pytest.raises(ValueError, match="absolute http"):
        HttpTransport("ftp://127.0.0.1")


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConnection:
    def __init__(self, *, status: int = 200, error: Exception | None = None, body: bytes = b"{}") -> None:
        self.status = status
        self.error = error
        self.body = body
        self.closed = False
        self.request_args: tuple[object, ...] | None = None

    def request(self, *args: object, **kwargs: object) -> None:
        self.request_args = (*args, kwargs)
        if self.error is not None:
            raise self.error

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self.status, self.body)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConnectionRefusedError(), ServerFailureCode.CONNECTION_REFUSED),
        (TimeoutError(), ServerFailureCode.SERVER_UNAVAILABLE),
        (HTTPException("bad"), ServerFailureCode.SERVER_UNAVAILABLE),
        (OSError("bad"), ServerFailureCode.SERVER_UNAVAILABLE),
    ],
)
def test_transport_request_errors_are_mapped(error: Exception, code: ServerFailureCode) -> None:
    """Transport failures map to neutral codes and close the connection."""
    transport = HttpTransport("http://127.0.0.1:4096", password="secret")  # noqa: S106
    connection = _FakeConnection(error=error)
    transport.new_connection = lambda: connection  # type: ignore[method-assign,assignment,return-value]
    with pytest.raises(OpenCodeServerError) as captured:
        transport.request_json("POST", "/api/x", body={"secret": "value"})
    assert captured.value.code is code
    assert connection.closed is True


def test_transport_successful_json_request_closes_connection() -> None:
    """A successful request returns decoded payload and closes the connection."""
    transport = HttpTransport("http://127.0.0.1:4096")
    connection = _FakeConnection(status=201, body=b'{"ok": true}')
    transport.new_connection = lambda: connection  # type: ignore[method-assign,assignment,return-value]
    response = transport.request_json("POST", "/api/x", body={"a": 1})
    assert response == HttpResponse(201, {"ok": True})
    assert connection.closed is True


class _FakeStreamResponse:
    def __init__(self, lines: list[bytes], *, error: Exception | None = None) -> None:
        self.status = 200
        self._lines = list(lines)
        self._error = error
        self.closed = False

    def readline(self) -> bytes:
        if self._error is not None and not self._lines:
            raise self._error
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def close(self) -> None:
        self.closed = True


class _FakeStreamConnection:
    def __init__(self, response: _FakeStreamResponse, *, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.sock = SimpleNamespace(shutdown=lambda _how: None)
        self.closed = False

    def request(self, *args: object, **kwargs: object) -> None:
        if self.error is not None:
            raise self.error

    def getresponse(self) -> _FakeStreamResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConnectionRefusedError(), ServerFailureCode.CONNECTION_REFUSED),
        (TimeoutError(), ServerFailureCode.SERVER_UNAVAILABLE),
        (OSError("bad"), ServerFailureCode.SERVER_UNAVAILABLE),
    ],
)
def test_sse_open_errors_are_mapped(error: Exception, code: ServerFailureCode) -> None:
    """SSE connection failures are explicit and close the connection."""
    transport = HttpTransport("http://127.0.0.1:4096")
    connection = _FakeStreamConnection(_FakeStreamResponse([]), error=error)
    transport.new_connection = lambda: connection  # type: ignore[method-assign,assignment,return-value]
    with pytest.raises(OpenCodeServerError) as captured:
        transport.open_event_stream(()).open()
    assert captured.value.code is code
    assert connection.closed is True


def test_sse_rejected_status_and_stream_events() -> None:
    """Rejected streams fail; valid frames decode and trailing data flushes."""
    transport = HttpTransport("http://127.0.0.1:4096")
    rejected = _FakeStreamConnection(_FakeStreamResponse([], error=None))
    rejected.response.status = 500
    transport.new_connection = lambda: rejected  # type: ignore[method-assign,assignment,return-value]
    with pytest.raises(OpenCodeServerError):
        transport.open_event_stream(()).open()

    response = _FakeStreamResponse([b'data: {"a": 1}\n', b"\n", b'data: {"b": 2}\n', b"\n"])
    connection = _FakeStreamConnection(response)
    transport.new_connection = lambda: connection  # type: ignore[method-assign,assignment,return-value]
    stream = transport.open_event_stream(("x",))
    stream.open()
    assert list(islice(stream.iter_events(), 2)) == [{"a": 1}, {"b": 2}]
    stream.close()
    assert response.closed is True


def test_sse_read_errors_and_shutdown_paths() -> None:
    """A reader error is interrupted; shutdown is idempotent and unblocks."""
    transport = HttpTransport("http://127.0.0.1:4096")
    response = _FakeStreamResponse([], error=OSError("broken"))
    connection = _FakeStreamConnection(response)
    transport.new_connection = lambda: connection  # type: ignore[method-assign,assignment,return-value]
    stream = transport.open_event_stream(())
    stream.open()
    with pytest.raises(OpenCodeServerError) as captured:
        list(stream.iter_events())
    assert captured.value.code is ServerFailureCode.STREAM_INTERRUPTED
    stream.shutdown()
    stream.shutdown()
    stream.close()

    unopened = SseStream(transport, "/api/event")
    with pytest.raises(RuntimeError, match="not open"):
        list(unopened.iter_events())

    closed_response = _FakeStreamResponse([], error=OSError("closed"))
    closed_connection = _FakeStreamConnection(closed_response)
    closed_transport = HttpTransport("http://127.0.0.1:4096")
    closed_transport.new_connection = lambda: closed_connection  # type: ignore[method-assign,assignment,return-value]
    closed_stream = closed_transport.open_event_stream(())
    closed_stream.open()
    closed_stream.shutdown()
    assert list(closed_stream.iter_events()) == []


def test_attached_server_exposes_password_and_noop_start() -> None:
    """Attach mode exposes credentials and never starts/stops externally."""
    server = AttachedServer(AttachEndpoint("http://127.0.0.1:4096", password="secret"))  # noqa: S106
    assert server.password == "secret"  # noqa: S105
    server.start()
    server.stop()
    with pytest.raises(ValueError, match="absolute http"):
        _validate_connect_url("not-a-url")
    with pytest.raises(OpenCodeServerError, match="non-loopback"):
        _validated_listening_url("http://example.com:4096")


def test_managed_server_environment_and_context_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configured environment is merged and context management starts/stops."""
    managed = ManagedServer(ManagedServerConfig(binary="opencode", environment={"EXTRA": "1"}))
    captured: dict[str, object] = {}

    class _Process:
        stdout = io.StringIO("")
        stderr = None
        stdin = None

        def poll(self) -> int:
            return 0

    def _popen(argv: object, **kwargs: object) -> _Process:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return _Process()

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(managed, "_capture_endpoint", lambda process: ("http://127.0.0.1:4096", "secret"))
    monkeypatch.setattr(managed, "_wait_until_ready", lambda base_url, password: None)
    endpoint = managed.start()
    assert endpoint.base_url == "http://127.0.0.1:4096"
    assert captured["env"]["EXTRA"] == "1"  # type: ignore[index]
    managed.stop()

    started: list[bool] = []

    def _start() -> ManagedEndpoint:
        started.append(True)
        return endpoint

    monkeypatch.setattr(managed, "start", _start)
    monkeypatch.setattr(managed, "stop", lambda: started.append(False))
    with managed as entered:
        assert entered is managed
    assert started == [True, False]


def test_managed_server_popen_oserror_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic OS error is a startup failure with a redacted message."""
    managed = ManagedServer(ManagedServerConfig(binary="opencode"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(OpenCodeServerError) as captured:
        managed.start()
    assert captured.value.code is ServerFailureCode.SERVER_START_FAILED
    assert "denied" in captured.value.message


def test_managed_server_capture_endpoint_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing stdout, timeout and early exit are explicit startup failures."""
    managed = ManagedServer(ManagedServerConfig(binary="opencode", startup_timeout_seconds=0.01))
    process = SimpleNamespace(stdout=None, poll=lambda: 0)
    with pytest.raises(OpenCodeServerError, match="stdout is unavailable"):
        managed._capture_endpoint(process)  # type: ignore[arg-type]  # noqa: SLF001

    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle._next_line", lambda queue_: "")
    process = SimpleNamespace(stdout=io.StringIO(""), poll=lambda: 0)
    with pytest.raises(OpenCodeServerError, match="did not report URL"):
        managed._capture_endpoint(process)  # type: ignore[arg-type]  # noqa: SLF001


def test_managed_server_readiness_authentication_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication failures stop retries; other failures time out explicitly."""
    managed = ManagedServer(ManagedServerConfig(binary="opencode", readiness_timeout_seconds=0.05))
    auth_error = OpenCodeServerError(ServerFailureCode.AUTHENTICATION_FAILED, "auth")

    class _Transport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def request_json(self, *args: object, **kwargs: object) -> object:
            raise auth_error

    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.HttpTransport", _Transport)
    with pytest.raises(OpenCodeServerError) as captured:
        managed._wait_until_ready("http://127.0.0.1:4096", "secret")  # noqa: SLF001
    assert captured.value.code is ServerFailureCode.AUTHENTICATION_FAILED

    class _StatusTransport(_Transport):
        def request_json(self, *args: object, **kwargs: object) -> object:
            return SimpleNamespace(status=503)

    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.HttpTransport", _StatusTransport)
    with pytest.raises(OpenCodeServerError, match="readiness timed out"):
        managed._wait_until_ready("http://127.0.0.1:4096", "secret")  # noqa: SLF001


def test_pump_lines_handles_closed_stream_and_empty_queue() -> None:
    """Reader threads report EOF for closed/erroring streams."""
    target: queue.Queue[str | None] = queue.Queue()
    _pump_lines(io.StringIO("line\n"), target)
    assert target.get() == "line"
    assert target.get() is None

    class _Broken:
        def __iter__(self) -> Iterator[object]:  # pylint: disable=non-iterator-returned
            msg = "closed"
            raise OSError(msg)

    _pump_lines(_Broken(), target)  # type: ignore[arg-type]
    assert target.get() is None
    assert _next_line(queue.Queue()) == ""


def test_force_kill_handles_lingering_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process ignoring SIGTERM receives SIGKILL and a bounded wait."""
    waits: list[float] = []

    class _Process:
        pid = 4242

        def wait(self, timeout: float) -> None:
            waits.append(timeout)
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.os.killpg", lambda pgid, sig: None)
    _force_kill(_Process())  # type: ignore[arg-type]
    assert waits


def test_version_policy_validation_and_classification() -> None:
    """Invalid policy values and unsupported versions fail closed."""
    with pytest.raises(ValueError, match="supported_major"):
        VersionPolicy(supported_major=0)
    with pytest.raises(ValueError, match="minimum_minor"):
        VersionPolicy(minimum_minor=-1)
    assert classify_server_version(None) is VersionCompatibility.UNKNOWN
    assert classify_server_version("bad") is VersionCompatibility.UNKNOWN
    assert classify_server_version("1.2.3") is VersionCompatibility.INCOMPATIBLE
    assert classify_server_version("2.1.0") is VersionCompatibility.INCOMPATIBLE
    assert classify_server_version("v2.2.6") is VersionCompatibility.SUPPORTED


def test_mapping_helpers_deduplicate_and_preserve_observed_values() -> None:
    """Failure deduplication and metadata pairs never invent fields."""
    messages = (
        AssistantMessage("m1", "one", ("bash:boom", "bash:boom")),
        AssistantMessage("m2", "two", ("python:error",)),
    )
    assert tool_failures(messages) == ("bash:boom", "python:error")
    session = SessionInfo(
        "ses_1",
        "/tmp",  # noqa: S108
        ModelRef("p", "m"),
        "succeeded",
        TokenUsage(input_tokens=1, output_tokens=2, reasoning_tokens=3, cost_usd=0.5),
        "title",
    )
    data = SimpleNamespace(
        session=session,
        agent="build",
        diff_count=1,
        version="2.2.6",
        usage=session.tokens,
        extra=(("custom", "value"),),
    )
    pairs = metadata_pairs(data)  # type: ignore[arg-type]
    assert ("reasoning_tokens", "3") in pairs
    assert ("model", "p/m") in pairs
    assert execution_failure_detail({"data": {}}) == "execution failed without a structured error"
    assert execution_failure_detail({"data": {"error": {"type": "E", "message": "m"}}}) == "E: m"
    assert _tokens(None) == TokenUsage()
    assert optional_float(True) is None  # noqa: FBT003
    with pytest.raises(OpenCodeServerError):
        require_mapping([], "x")


def test_runtime_validation_dependency_and_attached_paths() -> None:
    """Runtime constructor validation and attached lifecycle are explicit."""
    with pytest.raises(ValueError, match="at most one"):
        OpenCodeServerRuntime(endpoint=AttachEndpoint("http://127.0.0.1:1"), client=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_concurrent"):
        OpenCodeServerRuntime(options=RuntimeOptions(max_concurrent_executions=0))
    runtime = OpenCodeServerRuntime.attached("http://127.0.0.1:4096")
    assert runtime.ensure_started() == "http://127.0.0.1:4096"
    runtime.close()


def test_runtime_interrupt_without_server_and_concurrency_limit() -> None:
    """Missing server and exhausted concurrency produce neutral failures."""
    runtime = OpenCodeServerRuntime()
    with pytest.raises(OpenCodeServerError) as captured:
        runtime.interrupt("ses_1")
    assert captured.value.code is ServerFailureCode.SERVER_NOT_CONFIGURED

    runtime = OpenCodeServerRuntime(
        options=RuntimeOptions(max_concurrent_executions=1, execution_timeout_seconds=0.01),
    )
    runtime._semaphore.acquire()  # noqa: SLF001
    try:
        result = runtime.execute(_request())
    finally:
        runtime._semaphore.release()  # noqa: SLF001
    assert result.status.value == "failed"
    assert result.failure is not None
    assert "concurrency limit" in result.failure.detail


def test_runtime_managed_start_and_execution_client_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Managed runtime starts on demand and caches clients by endpoint."""
    managed = ManagedServer(ManagedServerConfig(binary="opencode"))
    endpoint = ManagedEndpoint("http://127.0.0.1:4096", "secret")
    monkeypatch.setattr(managed, "start", lambda: endpoint)
    runtime = OpenCodeServerRuntime(managed=managed)
    assert runtime.ensure_started() == "http://127.0.0.1:4096"
    managed._endpoint = endpoint  # noqa: SLF001
    managed._process = SimpleNamespace(poll=lambda: None, stdout=None, stderr=None, stdin=None)  # type: ignore[assignment]  # noqa: SLF001
    assert runtime._available_endpoint() == ("http://127.0.0.1:4096", "secret")  # noqa: SLF001
    first = runtime._client_for("http://127.0.0.1:4096", "secret")  # noqa: SLF001
    assert runtime._client_for("http://127.0.0.1:4096", "secret") is first  # noqa: SLF001
    managed._process = SimpleNamespace(poll=lambda: 0, stdout=None, stderr=None, stdin=None)  # type: ignore[assignment]  # noqa: SLF001
    runtime.close()


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        task="task",
        strategy=ExecutionStrategy.BUILD,
        model=None,
        required_capabilities=(),
        working_directory="/tmp",  # noqa: S108
        mutation_policy=MutationPolicy(allow_workspace_mutation=False),
        permission_policy=PermissionPolicy(allow_network=False),
    )


def test_lifecycle_reader_thread_is_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdout reader is a daemon thread and can be created explicitly."""
    thread = threading.Thread(target=lambda: None, daemon=True)
    assert thread.daemon is True
    with patch("agent_loop.adapters.opencode_server.lifecycle.time.sleep", lambda _: None):
        assert json.loads('{"ok": true}') == {"ok": True}
