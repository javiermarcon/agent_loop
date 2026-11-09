"""Typed client for the documented OpenCode Server HTTP API."""

from __future__ import annotations

import http.client
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Sequence

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.errors import failure_code_for_http_status
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import DiffEntry
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import parse_agent_inventory
from agent_loop.adapters.opencode_server.models import parse_diffs
from agent_loop.adapters.opencode_server.models import parse_messages
from agent_loop.adapters.opencode_server.models import parse_model_inventory
from agent_loop.adapters.opencode_server.models import parse_session_envelope
from agent_loop.adapters.opencode_server.models import require_mapping
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.adapters.opencode_server.transport import HttpResponse
from agent_loop.adapters.opencode_server.transport import HttpTransport

_HTTP_CONFLICT = 409


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """One OpenCode permission rule (action/resource/effect)."""

    action: str
    resource: str
    effect: str

    def as_payload(self) -> dict[str, str]:
        """Return the documented JSON shape."""
        return {"action": self.action, "resource": self.resource, "effect": self.effect}


class EventStream(Protocol):
    """Substitutable event stream used by the execution waiter."""

    def open(self) -> None:
        """Connect and verify the stream."""

    def iter_events(self) -> Iterator[Mapping[str, object]]:
        """Yield decoded event objects until the stream closes."""

    def shutdown(self) -> None:
        """Unblock a reader without closing the response concurrently."""

    def close(self) -> None:
        """Close the stream and release its connection."""


class ServerClient(Protocol):
    """Substitutable boundary over the documented server operations used by S22."""

    @property
    def base_url(self) -> str:
        """Return the server base URL."""

    @property
    def credentials_configured(self) -> bool:
        """Return whether a password is configured for this client."""

    def info(self) -> ServerInfo:
        """Return server identity and version."""

    def models(self) -> tuple[ModelRef, ...]:
        """Return the model inventory."""

    def agents(self) -> tuple[str, ...]:
        """Return configured agent identifiers."""

    def create_session(
        self,
        *,
        directory: str,
        title: str,
        model: ModelRef | None,
        agent: str,
        permissions: Sequence[PermissionRule] = (),
    ) -> SessionInfo:
        """Create one isolated session."""

    def get_session(self, session_id: str) -> SessionInfo:
        """Return current session state."""

    def prompt(self, session_id: str, text: str) -> None:
        """Admit one prompt for the session."""

    def interrupt(self, session_id: str) -> bool:
        """Interrupt active execution."""

    def messages(self, session_id: str) -> tuple[AssistantMessage, ...]:
        """Return projected session messages."""

    def diffs(self, session_id: str) -> tuple[DiffEntry, ...]:
        """Return per-file diffs."""

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        """Reply to a permission request."""

    def open_events(self, types: tuple[str, ...]) -> EventStream:
        """Open the documented SSE stream."""


class OpenCodeServerClient:
    """Thin typed wrapper over the public endpoints used by S22."""

    def __init__(
        self,
        base_url: str,
        *,
        password: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._transport = HttpTransport(
            base_url,
            password=password,
            timeout_seconds=timeout_seconds,
        )
        self._password = password

    @property
    def base_url(self) -> str:
        """Return the server base URL."""
        return self._transport.base_url

    @property
    def credentials_configured(self) -> bool:
        """Return whether a password is configured for this client."""
        return self._password is not None

    def info(self) -> ServerInfo:
        """Return server identity and version."""
        response = self._transport.request_json("GET", "/api/info")
        payload = self._expect(response, http.client.OK)
        return ServerInfo.from_payload(payload)

    def models(self) -> tuple[ModelRef, ...]:
        """Return the model inventory as native references."""
        response = self._transport.request_json("GET", "/api/model")
        return parse_model_inventory(self._expect(response, http.client.OK))

    def agents(self) -> tuple[str, ...]:
        """Return configured agent identifiers."""
        response = self._transport.request_json("GET", "/api/agent")
        return parse_agent_inventory(self._expect(response, http.client.OK))

    def create_session(
        self,
        *,
        directory: str,
        title: str,
        model: ModelRef | None,
        agent: str,
        permissions: Sequence[PermissionRule] = (),
    ) -> SessionInfo:
        """Create one isolated session in ``directory``."""
        body: dict[str, object] = {
            "title": title,
            "agent": agent,
            "location": {"directory": directory},
        }
        if model is not None:
            body["model"] = {
                "providerID": model.provider_id,
                "id": model.model_id,
                **({"variant": model.variant} if model.variant else {}),
            }
        if permissions:
            body["permissions"] = [rule.as_payload() for rule in permissions]
        response = self._transport.request_json("POST", "/api/session", body=body)
        return parse_session_envelope(self._expect(response, http.client.OK))

    def get_session(self, session_id: str) -> SessionInfo:
        """Return current session state."""
        response = self._transport.request_json("GET", f"/api/session/{session_id}")
        return parse_session_envelope(self._expect(response, http.client.OK))

    def prompt(self, session_id: str, text: str) -> None:
        """Durably admit one prompt for the session agent loop."""
        response = self._transport.request_json(
            "POST",
            f"/api/session/{session_id}/prompt",
            body={"text": text},
        )
        if response.status == _HTTP_CONFLICT:
            raise OpenCodeServerError(
                ServerFailureCode.SESSION_CLOSED,
                "session rejected the prompt because it is busy or closed",
            )
        self._expect(response, http.client.OK)

    def interrupt(self, session_id: str) -> bool:
        """Interrupt active execution, returning the server's own flag."""
        response = self._transport.request_json(
            "POST",
            f"/api/session/{session_id}/interrupt",
        )
        payload = self._expect(response, http.client.OK)
        data = require_mapping(payload, "interrupt response")
        return data.get("interrupted") is True

    def messages(self, session_id: str) -> tuple[AssistantMessage, ...]:
        """Return projected messages for ``session_id``."""
        response = self._transport.request_json(
            "GET",
            f"/api/session/{session_id}/message?order=asc",
        )
        return parse_messages(self._expect(response, http.client.OK))

    def diffs(self, session_id: str) -> tuple[DiffEntry, ...]:
        """Return per-file diffs for the session's newest turn."""
        response = self._transport.request_json(
            "GET",
            f"/api/session/{session_id}/diff",
        )
        return parse_diffs(self._expect(response, http.client.OK))

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        """Reply to a permission request with an explicit decision."""
        response = self._transport.request_json(
            "POST",
            f"/api/session/{session_id}/permission/{request_id}/reply",
            body={"decision": decision},
        )
        self._expect(response, http.client.OK, http.client.NO_CONTENT)

    def open_events(self, types: tuple[str, ...]) -> EventStream:
        """Open the documented SSE stream for selected event types."""
        return self._transport.open_event_stream(types)

    def _expect(
        self,
        response: HttpResponse,
        *expected: int,
    ) -> object:
        if response.status in expected:
            if response.payload is None and response.status != http.client.NO_CONTENT:
                raise OpenCodeServerError(
                    ServerFailureCode.INVALID_RESPONSE,
                    "server returned an empty or non-JSON response",
                )
            return response.payload
        raise _http_error(response, self._password)


def _http_error(response: HttpResponse, password: str | None) -> OpenCodeServerError:
    code = failure_code_for_http_status(response.status)
    detail = _error_detail(response.payload)
    message = f"server returned status {response.status}"
    if detail:
        message = f"{message}: {detail}"
    return OpenCodeServerError(code, redact_secret(message, password))


def _error_detail(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    tag = payload.get("_tag")
    message = payload.get("message")
    parts = [value for value in (tag, message) if isinstance(value, str) and value]
    return " ".join(parts)[:200]
