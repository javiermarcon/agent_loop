"""Minimal HTTP/SSE transport for the public OpenCode Server API.

The transport owns connection handling and authentication header injection. It
never logs or serializes the configured password.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Self
from urllib.parse import urlsplit

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.redaction import redact_url_credentials

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One unary HTTP response with an optional decoded JSON payload."""

    status: int
    payload: object


def _transport_error(code: ServerFailureCode, message: str, secret: str | None) -> OpenCodeServerError:
    return OpenCodeServerError(code, redact_secret(message, secret))


class HttpTransport:
    """HTTP client bound to one server base URL and optional password."""

    def __init__(
        self,
        base_url: str,
        *,
        password: str | None = None,
        username: str = "opencode",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = _validated_base_url(base_url)
        self._password = password
        self._username = username
        self._timeout_seconds = timeout_seconds
        parts = urlsplit(self._base_url)
        self._scheme = parts.scheme
        self._host = parts.hostname or ""
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")

    @property
    def base_url(self) -> str:
        """Return the normalized server base URL without credentials."""
        return self._base_url

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        """Execute one JSON request and return its status plus decoded payload."""
        connection = self.new_connection()
        try:
            connection.request(
                method,
                self._path(path),
                body=json.dumps(body).encode("utf-8") if body is not None else None,
                headers=self.headers(json_content_type=body is not None),
            )
            response = connection.getresponse()
            raw = response.read()
        except ConnectionRefusedError as exc:
            raise _transport_error(
                ServerFailureCode.CONNECTION_REFUSED,
                "connection refused",
                self._password,
            ) from exc
        except TimeoutError as exc:
            raise _transport_error(
                ServerFailureCode.SERVER_UNAVAILABLE,
                "request timed out",
                self._password,
            ) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise _transport_error(
                ServerFailureCode.SERVER_UNAVAILABLE,
                f"http transport failure: {exc}",
                self._password,
            ) from exc
        finally:
            connection.close()
        payload = _decode_payload(raw)
        return HttpResponse(status=response.status, payload=payload)

    def open_event_stream(self, types: tuple[str, ...]) -> SseStream:
        """Open the documented SSE event stream as a context-managed object."""
        query = "&".join(f"types={type_name}" for type_name in types)
        path = self._path("/api/event")
        if query:
            path = f"{path}?{query}"
        return SseStream(self, path)

    def new_connection(self) -> http.client.HTTPConnection:
        """Create one connection with the configured timeout and scheme."""
        if self._scheme == "https":
            context = ssl.create_default_context()
            return http.client.HTTPSConnection(
                self._host,
                self._port,
                timeout=self._timeout_seconds,
                context=context,
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout_seconds)

    def headers(self, *, json_content_type: bool) -> dict[str, str]:
        """Build request headers, attaching Basic auth only when configured."""
        headers = {"Accept": "application/json"}
        if json_content_type:
            headers["Content-Type"] = "application/json"
        if self._password:
            token = base64.b64encode(f"{self._username}:{self._password}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _path(self, path: str) -> str:
        return f"{self._prefix}{path}" if self._prefix else path


def _validated_base_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        message = "server base URL must be an absolute http(s) URL"
        raise ValueError(message)
    if parts.username or parts.password:
        message = "server base URL must not embed credentials"
        raise ValueError(message)
    normalized = f"{parts.scheme}://{parts.hostname}"
    if parts.port is not None:
        normalized = f"{normalized}:{parts.port}"
    if parts.path and parts.path != "/":
        normalized = f"{normalized}{parts.path.rstrip('/')}"
    return normalized


def _decode_payload(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class SseStream:
    """Blocking SSE reader with an explicit close path for cancellation."""

    def __init__(self, transport: HttpTransport, path: str) -> None:
        self._transport = transport
        self._path = path
        self._connection: http.client.HTTPConnection | None = None
        self._response: http.client.HTTPResponse | None = None
        self._socket: socket.socket | None = None
        self._closed = False

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def open(self) -> None:
        """Connect and verify the stream response before returning."""
        if self._connection is not None:
            return
        connection = self._transport.new_connection()
        headers = self._transport.headers(json_content_type=False)
        headers["Accept"] = "text/event-stream"
        try:
            connection.request("GET", self._path, headers=headers)
            response = connection.getresponse()
            self._socket = connection.sock
        except ConnectionRefusedError as exc:
            connection.close()
            raise _transport_error(
                ServerFailureCode.CONNECTION_REFUSED,
                "connection refused",
                None,
            ) from exc
        except TimeoutError as exc:
            connection.close()
            raise _transport_error(
                ServerFailureCode.SERVER_UNAVAILABLE,
                "event stream timed out",
                None,
            ) from exc
        except (http.client.HTTPException, OSError) as exc:
            connection.close()
            raise _transport_error(
                ServerFailureCode.SERVER_UNAVAILABLE,
                f"event stream failure: {exc}",
                None,
            ) from exc
        if response.status != http.client.OK:
            connection.close()
            raise _transport_error(
                ServerFailureCode.SERVER_UNAVAILABLE,
                f"event stream rejected with status {response.status}",
                None,
            )
        self._connection = connection
        self._response = response

    def iter_events(self) -> Iterator[Mapping[str, object]]:
        """Yield decoded event objects until the stream closes or fails."""
        data_lines: list[str] = []
        while not self._closed:
            line = self._read_line_text()
            if line is None:
                break
            if not line:
                if data_lines:
                    yield _decode_event("\n".join(data_lines))
                    data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield _decode_event("\n".join(data_lines))

    def _read_line_text(self) -> str | None:
        response = self._response
        if response is None:
            message = "SSE stream is not open"
            raise RuntimeError(message)
        while True:
            try:
                raw_line = response.readline()
            except TimeoutError:
                continue
            except (http.client.HTTPException, OSError) as exc:
                if self._closed:
                    return None
                raise _transport_error(
                    ServerFailureCode.STREAM_INTERRUPTED,
                    f"event stream interrupted: {exc}",
                    None,
                ) from exc
            if not raw_line:
                if self._closed:
                    return None
                raise _transport_error(
                    ServerFailureCode.STREAM_INTERRUPTED,
                    "event stream closed by server",
                    None,
                )
            return raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

    def shutdown(self) -> None:
        """Unblock a reader thread without closing the response concurrently."""
        if self._closed:
            return
        self._closed = True
        sock = self._socket
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    def close(self) -> None:
        """Release the response and connection after the reader has exited."""
        self.shutdown()
        response = self._response
        if response is not None:
            response.close()
        connection = self._connection
        if connection is not None:
            connection.close()


def _decode_event(raw: str) -> Mapping[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_RESPONSE,
            "event stream contained invalid JSON",
        ) from exc
    if not isinstance(payload, Mapping):
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_RESPONSE,
            "event stream payload is not a JSON object",
        )
    return payload


def display_base_url(url: str) -> str:
    """Return a credential-free display form of ``url``."""
    return redact_url_credentials(url)
