"""Parsed public OpenCode Server payloads used inside the adapter.

Only fields consumed by S22 are modeled. Unknown JSON fields stay unknown and
are not promoted into domain contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode


def _invalid(message: str) -> OpenCodeServerError:
    return OpenCodeServerError(ServerFailureCode.INVALID_RESPONSE, message)


def require_mapping(value: object, field: str) -> Mapping[str, object]:
    """Return ``value`` as a JSON object or fail with ``invalid_response``."""
    if not isinstance(value, Mapping):
        message = f"{field} is not a JSON object"
        raise _invalid(message)
    return value


def optional_mapping(value: object) -> Mapping[str, object] | None:
    """Return a JSON object when present, otherwise ``None``."""
    return value if isinstance(value, Mapping) else None


def require_list(value: object, field: str) -> list[object]:
    """Return ``value`` as a JSON array or fail with ``invalid_response``."""
    if not isinstance(value, list):
        message = f"{field} is not a JSON array"
        raise _invalid(message)
    return value


def require_str(value: object, field: str) -> str:
    """Return a non-empty string field or fail with ``invalid_response``."""
    if not isinstance(value, str) or not value:
        message = f"{field} is missing or not a string"
        raise _invalid(message)
    return value


def optional_str(value: object) -> str | None:
    """Return a string when present, otherwise ``None``."""
    return value if isinstance(value, str) and value else None


def optional_int(value: object) -> int | None:
    """Return an integer count when it is a non-negative number, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def optional_float(value: object) -> float | None:
    """Return a finite non-negative float when present, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return None


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Verified server identity returned by ``GET /api/info``."""

    version: str | None
    pid: int | None
    urls: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: object) -> ServerInfo:
        """Parse a ``ServerInfo`` payload, failing closed on malformed data."""
        data = require_mapping(payload, "server info")
        version = optional_str(data.get("version"))
        pid = optional_int(data.get("pid"))
        urls_value = data.get("urls", [])
        urls = tuple(item for item in urls_value if isinstance(item, str)) if isinstance(urls_value, list) else ()
        return cls(version=version, pid=pid, urls=urls)


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Native ``provider/model#variant`` identity."""

    provider_id: str
    model_id: str
    variant: str | None = None

    def as_text(self) -> str:
        """Return ``provider/model`` with an optional ``#variant`` suffix."""
        base = f"{self.provider_id}/{self.model_id}"
        return f"{base}#{self.variant}" if self.variant else base

    @classmethod
    def from_payload(cls, payload: object) -> ModelRef:
        """Parse a ``Model.Ref`` object."""
        data = require_mapping(payload, "model ref")
        return cls(
            provider_id=require_str(data.get("providerID"), "model ref providerID"),
            model_id=require_str(data.get("id"), "model ref id"),
            variant=optional_str(data.get("variant")),
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token and cost observations retained without extrapolation."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Subset of ``Session.Info`` required by the adapter."""

    session_id: str
    directory: str | None
    model: ModelRef | None
    outcome: str | None
    tokens: TokenUsage
    title: str | None

    @classmethod
    def from_payload(cls, payload: object) -> SessionInfo:
        """Parse a ``Session.Info`` payload."""
        data = require_mapping(payload, "session")
        model_raw = data.get("model")
        model = ModelRef.from_payload(model_raw) if isinstance(model_raw, Mapping) else None
        location = optional_mapping(data.get("location"))
        directory = optional_str(location.get("directory")) if location is not None else None
        tokens_raw = optional_mapping(data.get("tokens"))
        tokens = _tokens(tokens_raw)
        return cls(
            session_id=require_str(data.get("id"), "session id"),
            directory=directory,
            model=model,
            outcome=optional_str(data.get("outcome")),
            tokens=tokens,
            title=optional_str(data.get("title")),
        )


def _tokens(payload: Mapping[str, object] | None) -> TokenUsage:
    if payload is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=optional_int(payload.get("input")),
        output_tokens=optional_int(payload.get("output")),
        reasoning_tokens=optional_int(payload.get("reasoning")),
        cost_usd=optional_float(payload.get("cost")),
    )


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """One assistant message with concatenated text and tool failures."""

    message_id: str
    text: str
    tool_failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """One per-file diff entry from ``GET /api/session/{id}/diff``."""

    file: str
    additions: int
    deletions: int
    status: str


def parse_session_envelope(payload: object) -> SessionInfo:
    """Parse the ``{data: Session.Info}`` envelope."""
    data = require_mapping(payload, "session envelope")
    return SessionInfo.from_payload(data.get("data"))


def parse_model_inventory(payload: object) -> tuple[ModelRef, ...]:
    """Parse ``GET /api/model`` into unique native model references."""
    data = require_mapping(payload, "model inventory")
    entries = require_list(data.get("data"), "model inventory data")
    models: list[ModelRef] = []
    seen: set[str] = set()
    for entry in entries:
        item = require_mapping(entry, "model inventory entry")
        model = ModelRef(
            provider_id=require_str(item.get("providerID"), "model providerID"),
            model_id=require_str(item.get("id"), "model id"),
        )
        text = model.as_text()
        if text not in seen:
            seen.add(text)
            models.append(model)
    return tuple(models)


def parse_agent_inventory(payload: object) -> tuple[str, ...]:
    """Parse ``GET /api/agent`` into agent identifiers."""
    data = require_mapping(payload, "agent inventory")
    entries = require_list(data.get("data"), "agent inventory data")
    agents: list[str] = []
    for entry in entries:
        item = require_mapping(entry, "agent inventory entry")
        agents.append(require_str(item.get("id"), "agent id"))
    return tuple(agents)


def parse_messages(payload: object) -> tuple[AssistantMessage, ...]:
    """Parse session messages, retaining assistant text and tool failures."""
    data = require_mapping(payload, "messages")
    entries = require_list(data.get("data"), "messages data")
    messages: list[AssistantMessage] = []
    for entry in entries:
        item = require_mapping(entry, "message")
        if item.get("type") != "assistant":
            continue
        message_id = optional_str(item.get("id")) or "unknown"
        text, failures = _assistant_content(item.get("content"))
        messages.append(AssistantMessage(message_id=message_id, text=text, tool_failures=failures))
    return tuple(messages)


def _assistant_content(content: object) -> tuple[str, tuple[str, ...]]:
    if not isinstance(content, list):
        return "", ()
    texts: list[str] = []
    failures: list[str] = []
    for raw_part in content:
        part = optional_mapping(raw_part)
        if part is None:
            continue
        text_value = part.get("text")
        if part.get("type") == "text" and isinstance(text_value, str):
            texts.append(text_value)
        if part.get("type") == "tool":
            failures.extend(_tool_failures(part))
    return "\n".join(text for text in texts if text).strip(), tuple(failures)


def _tool_failures(part: Mapping[str, object]) -> tuple[str, ...]:
    state = optional_mapping(part.get("state"))
    if state is None or state.get("status") != "error":
        return ()
    error = optional_mapping(state.get("error"))
    name = optional_str(part.get("name")) or "tool"
    message = optional_str(error.get("message")) if error is not None else None
    return (f"{name}:{message or 'failed'}",)


def parse_diffs(payload: object) -> tuple[DiffEntry, ...]:
    """Parse ``GET /api/session/{id}/diff`` into evidence-safe entries."""
    data = require_mapping(payload, "diff")
    entries = require_list(data.get("data"), "diff data")
    diffs: list[DiffEntry] = []
    for entry in entries:
        item = require_mapping(entry, "diff entry")
        diffs.append(
            DiffEntry(
                file=require_str(item.get("file"), "diff file"),
                additions=optional_int(item.get("additions")) or 0,
                deletions=optional_int(item.get("deletions")) or 0,
                status=optional_str(item.get("status")) or "unknown",
            ),
        )
    return tuple(diffs)
