"""Transport tests against a controlled HTTP/SSE fixture server."""

from __future__ import annotations

import base64
import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from itertools import islice
from typing import TYPE_CHECKING

import pytest

from agent_loop.adapters.opencode_server.client import EventStream
from agent_loop.adapters.opencode_server.client import OpenCodeServerClient
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.transport import HttpTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

_TEST_CREDENTIAL = "test-password-value"


class _Handler(BaseHTTPRequestHandler):
    server_version = "Fixture/1.0"

    def do_GET(self) -> None:
        if self.path == "/api/info":
            self._json(200, {"version": "2.2.6", "pid": 1, "urls": []})
        elif self.path == "/api/echo-auth":
            self._json(200, {"authorization": self.headers.get("Authorization")})
        elif self.path == "/api/badjson":
            self._raw(200, b"{not-json")
        elif self.path == "/api/slow":
            time.sleep(0.5)
            self._json(200, {})
        elif self.path.startswith("/api/event"):
            self._sse()
        else:
            self._json(404, {"_tag": "NotFoundError", "message": "missing"})

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self._raw(status, body)

    def _raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError):
            self.wfile.write(body)

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError):
            self._write_sse_frames()

    def _write_sse_frames(self) -> None:
        if "bad-json" in self.path:
            self.wfile.write(b"data: {not-json}\n\n")
        elif "closed" in self.path:
            self.wfile.write(b": heartbeat\n\n")
        else:
            self.wfile.write(
                b'data: {"type":"session.execution.succeeded","data":{"sessionID":"ses_1"}}\n\n',
            )
            self.wfile.write(b": heartbeat\n\n")
            self.wfile.write(
                b'data: {"type":"session.idle","data":{"sessionID":"ses_1"}}\n\n',
            )
        self.wfile.flush()

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args


class _UnauthorizedHandler(_Handler):
    def do_GET(self) -> None:
        self._json(401, {"_tag": "UnauthorizedError", "message": "Authentication required"})


@pytest.fixture
def server_url() -> Iterator[str]:
    yield from _serve(_Handler)


@pytest.fixture
def unauthorized_server_url() -> Iterator[str]:
    yield from _serve(_UnauthorizedHandler)


def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unary_request_parses_json_and_sends_basic_auth(server_url: str) -> None:
    """The documented Basic header is attached to API requests."""
    transport = HttpTransport(server_url, password=_TEST_CREDENTIAL)
    response = transport.request_json("GET", "/api/echo-auth")
    expected = "Basic " + base64.b64encode(f"opencode:{_TEST_CREDENTIAL}".encode()).decode()
    assert response.payload == {"authorization": expected}


def test_client_maps_unauthorized_to_neutral_code(unauthorized_server_url: str) -> None:
    """401 becomes authentication_failed, not a generic failure."""
    client = OpenCodeServerClient(unauthorized_server_url, password=_TEST_CREDENTIAL)
    with pytest.raises(OpenCodeServerError) as captured:
        client.info()
    assert captured.value.code is ServerFailureCode.AUTHENTICATION_FAILED


def test_client_maps_invalid_json_to_invalid_response(server_url: str) -> None:
    """Malformed JSON never becomes an empty success."""
    transport = HttpTransport(server_url)
    response = transport.request_json("GET", "/api/badjson")
    assert response.payload is None


def test_request_timeout_is_server_unavailable(server_url: str) -> None:
    """A request exceeding its timeout is unavailable, not successful."""
    transport = HttpTransport(server_url, timeout_seconds=0.1)
    with pytest.raises(OpenCodeServerError) as captured:
        transport.request_json("GET", "/api/slow")
    assert captured.value.code is ServerFailureCode.SERVER_UNAVAILABLE


def test_connection_refused_is_explicit() -> None:
    """A closed port maps to connection_refused."""
    transport = HttpTransport("http://127.0.0.1:1", timeout_seconds=0.2)
    with pytest.raises(OpenCodeServerError) as captured:
        transport.request_json("GET", "/api/info")
    assert captured.value.code is ServerFailureCode.CONNECTION_REFUSED


def test_sse_stream_decodes_events_and_ignores_heartbeats(server_url: str) -> None:
    """Documented SSE frames are decoded and heartbeats skipped."""
    transport = HttpTransport(server_url)
    with transport.open_event_stream(()) as stream:
        events = list(islice(stream.iter_events(), 2))
    assert [event["type"] for event in events] == ["session.execution.succeeded", "session.idle"]


def test_sse_invalid_json_fails_closed(server_url: str) -> None:
    """Malformed event JSON is invalid_response."""
    transport = HttpTransport(server_url)
    with transport.open_event_stream(("bad-json",)) as stream, pytest.raises(OpenCodeServerError) as captured:
        list(stream.iter_events())
    assert captured.value.code is ServerFailureCode.INVALID_RESPONSE


def test_sse_closed_stream_is_interrupted(server_url: str) -> None:
    """A stream that ends without a terminal event is interrupted."""
    transport = HttpTransport(server_url)
    with transport.open_event_stream(("closed",)) as stream, pytest.raises(OpenCodeServerError) as captured:
        list(stream.iter_events())
    assert captured.value.code is ServerFailureCode.STREAM_INTERRUPTED


def test_client_rejects_unsafe_base_urls() -> None:
    """Credentials must not be embedded in the configured URL."""
    with pytest.raises(ValueError, match="must not embed credentials"):
        HttpTransport("http://user:secret@127.0.0.1:4096")
    with pytest.raises(ValueError, match="absolute http"):
        HttpTransport("127.0.0.1:4096")


def test_client_never_includes_password_in_error_text(unauthorized_server_url: str) -> None:
    """Error messages do not contain the configured password."""
    client = OpenCodeServerClient(unauthorized_server_url, password=_TEST_CREDENTIAL)
    with pytest.raises(OpenCodeServerError) as captured:
        client.info()
    assert _TEST_CREDENTIAL not in str(captured.value)


def test_sse_close_unblocks_reader(server_url: str) -> None:
    """Closing the stream releases a reader blocked on the socket."""
    transport = HttpTransport(server_url)
    stream = transport.open_event_stream(("closed",))
    stream.open()
    reader = threading.Thread(target=_drain, args=(stream,), daemon=True)
    reader.start()
    time.sleep(0.05)
    stream.close()
    reader.join(timeout=2)
    assert not reader.is_alive()


def _drain(stream: EventStream) -> None:
    try:
        for _event in stream.iter_events():
            pass
    except OpenCodeServerError:
        pass


def test_unary_parser_accepts_expected_statuses(server_url: str) -> None:
    """The fixture's valid info response is accepted by the typed client."""
    client = OpenCodeServerClient(server_url)
    info = client.info()
    assert info.version == "2.2.6"
