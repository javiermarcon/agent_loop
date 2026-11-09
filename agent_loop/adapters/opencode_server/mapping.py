"""Neutral request/response mapping for the OpenCode Server adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode_server.client import PermissionRule
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import DiffEntry
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import ExecutionUsage

if TYPE_CHECKING:
    from agent_loop.ports.agent_runtime import MutationPolicy
    from agent_loop.ports.agent_runtime import PermissionPolicy

_MAX_ERROR_LENGTH = 300


@dataclass(frozen=True, slots=True)
class MetadataInput:
    """Inputs for opaque diagnostic metadata construction."""

    version: str | None
    session: SessionInfo
    agent: str
    diff_count: int
    usage: TokenUsage
    extra: tuple[tuple[str, str], ...] = ()


def parse_native_model(model: str | None) -> ModelRef | None:
    """Parse ``provider/model[#variant]`` into a native model reference."""
    if model is None:
        return None
    value, _, variant = model.partition("#")
    provider_id, separator, model_id = value.partition("/")
    if not separator or not provider_id.strip() or not model_id.strip():
        message = "model must use the documented provider/model[#variant] form"
        raise OpenCodeServerError(ServerFailureCode.INVALID_REQUEST, message)
    return ModelRef(
        provider_id=provider_id.strip(),
        model_id=model_id.strip(),
        variant=variant.strip() or None,
    )


def agent_for_request(strategy: ExecutionStrategy, mutation: MutationPolicy) -> str:
    """Return the least-privileged OpenCode agent for the requested strategy."""
    if not mutation.allow_workspace_mutation:
        return "plan"
    return "build" if strategy is not ExecutionStrategy.PLAN else "plan"


def permission_rules(
    mutation: MutationPolicy,
    permission: PermissionPolicy,
) -> tuple[PermissionRule, ...]:
    """Return restrictive-only rules; no default permission is expanded."""
    rules = [PermissionRule(action="external_directory", resource="*", effect="deny")]
    if not permission.allow_network:
        rules.extend(
            (
                PermissionRule(action="webfetch", resource="*", effect="deny"),
                PermissionRule(action="websearch", resource="*", effect="deny"),
            ),
        )
    if not mutation.allow_workspace_mutation:
        rules.extend(
            (
                PermissionRule(action="edit", resource="*", effect="deny"),
                PermissionRule(action="shell", resource="*", effect="deny"),
            ),
        )
    return tuple(rules)


def session_title() -> str:
    """Return a secret-free deterministic session title."""
    return "agent-loop execution"


def final_text(messages: tuple[AssistantMessage, ...]) -> str:
    """Join assistant text blocks in server order, preserving unknown gaps."""
    return "\n".join(message.text for message in messages if message.text)


def tool_failures(messages: tuple[AssistantMessage, ...]) -> tuple[str, ...]:
    """Return unique tool failure descriptions reported by assistant messages."""
    failures: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for failure in message.tool_failures:
            if failure not in seen:
                seen.add(failure)
                failures.append(failure)
    return tuple(failures)


def evidence_refs(
    session_id: str,
    messages: tuple[AssistantMessage, ...],
    diffs: tuple[DiffEntry, ...],
) -> tuple[str, ...]:
    """Return stable diagnostic evidence references, never quality verdicts."""
    refs = [f"opencode-server:session:{session_id}"]
    refs.extend(
        f"opencode-server:message:{message.message_id}" for message in messages if message.message_id != "unknown"
    )
    refs.extend(f"opencode-server:diff:{entry.file}" for entry in diffs)
    unique: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return tuple(unique)


def usage_from_session(session: SessionInfo) -> ExecutionUsage | None:
    """Map observed token/cost fields without inventing missing metrics."""
    tokens = session.tokens
    if tokens.input_tokens is None and tokens.output_tokens is None and tokens.cost_usd is None:
        return None
    cost_micros = None if tokens.cost_usd is None else round(tokens.cost_usd * 1_000_000)
    return ExecutionUsage(
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cost_micros=cost_micros,
    )


def status_from_outcome(outcome: str | None) -> AgentExecutionStatus | None:
    """Map a session outcome to a normalized status, or ``None`` if unknown."""
    if outcome == "succeeded":
        return AgentExecutionStatus.SUCCEEDED
    if outcome == "failed":
        return AgentExecutionStatus.FAILED
    if outcome == "interrupted":
        return AgentExecutionStatus.CANCELLED
    return None


def metadata_pairs(data: MetadataInput) -> tuple[tuple[str, str], ...]:
    """Return opaque diagnostic metadata without secrets or invented metrics."""
    session = data.session
    pairs: list[tuple[str, str]] = [
        ("adapter", "opencode-server"),
        ("agent", data.agent),
        ("diff_files", str(data.diff_count)),
        ("session_id", session.session_id),
    ]
    if data.version:
        pairs.append(("server_version", data.version))
    if session.model is not None:
        pairs.append(("model", session.model.as_text()))
    if session.outcome:
        pairs.append(("session_outcome", session.outcome))
    if data.usage.reasoning_tokens is not None:
        pairs.append(("reasoning_tokens", str(data.usage.reasoning_tokens)))
    pairs.extend(data.extra)
    return tuple(pairs)


def execution_failure_detail(event: Mapping[str, object], secret: str | None = None) -> str:
    """Return a redacted, bounded detail string from a failed execution event."""
    data = event.get("data")
    if not isinstance(data, Mapping):
        return "execution failed without a structured error"
    error = data.get("error")
    if not isinstance(error, Mapping):
        return "execution failed without a structured error"
    error_type = error.get("type")
    message = error.get("message")
    parts = [value for value in (error_type, message) if isinstance(value, str) and value]
    detail = ": ".join(parts) or "execution failed"
    return redact_secret(detail, secret)[:_MAX_ERROR_LENGTH]
