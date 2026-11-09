"""Execution orchestration for the OpenCode Server adapter.

The runner owns one session, one event subscription, and one terminal-state
decision per call. It performs no retries and no escalation.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.adapters.opencode_server.constants import TERMINAL_EVENT_TYPES
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.mapping import MetadataInput
from agent_loop.adapters.opencode_server.mapping import agent_for_request
from agent_loop.adapters.opencode_server.mapping import evidence_refs
from agent_loop.adapters.opencode_server.mapping import execution_failure_detail
from agent_loop.adapters.opencode_server.mapping import final_text
from agent_loop.adapters.opencode_server.mapping import metadata_pairs
from agent_loop.adapters.opencode_server.mapping import parse_native_model
from agent_loop.adapters.opencode_server.mapping import permission_rules
from agent_loop.adapters.opencode_server.mapping import session_title
from agent_loop.adapters.opencode_server.mapping import status_from_outcome
from agent_loop.adapters.opencode_server.mapping import usage_from_session
from agent_loop.adapters.opencode_server.redaction import redact_secret
from agent_loop.ports.agent_runtime import AgentExecutionResult
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from agent_loop.ports.agent_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_loop.adapters.opencode_server.client import EventStream
    from agent_loop.adapters.opencode_server.client import PermissionRule
    from agent_loop.adapters.opencode_server.client import ServerClient
    from agent_loop.adapters.opencode_server.models import ModelRef
    from agent_loop.adapters.opencode_server.models import SessionInfo
    from agent_loop.ports.agent_runtime import AgentExecutionRequest

_HTTP_EVENT_QUEUE_TIMEOUT = 0.2
_READER_JOIN_SECONDS = 2.0
_REJECT_DECISION = "reject"
_TERMINAL_EXECUTION_EVENTS = frozenset(
    {
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
    },
)


@dataclass(frozen=True, slots=True)
class _StreamEnded:
    """Sentinel carrying an optional stream failure to the waiter."""

    error: OpenCodeServerError | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Per-call timeout, cancellation, and observer inputs."""

    timeout_seconds: float
    cancel_event: threading.Event | None = None
    on_session_started: Callable[[str], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Validated session inputs derived from one execution request."""

    workspace: Path
    agent: str
    model: ModelRef | None
    rules: tuple[PermissionRule, ...] = ()


@dataclass(frozen=True, slots=True)
class WaitOutcome:
    """Result of waiting for one session's terminal event."""

    event: Mapping[str, object] | None = None
    timed_out: bool = False
    cancelled: bool = False
    stream_error: OpenCodeServerError | None = None
    permission_rejections: int = 0


@dataclass(frozen=True, slots=True)
class ResultContext:
    """Identity and diagnostics shared by success and failure results."""

    identity: RuntimeIdentity
    server_version: str | None
    agent: str


@dataclass(slots=True)
class _WaitState:
    """Mutable counters used only inside one wait loop."""

    rejections: int = 0
    stream_error: OpenCodeServerError | None = None
    started: bool = False


def execute_request(
    client: ServerClient,
    request: AgentExecutionRequest,
    *,
    identity: RuntimeIdentity,
    server_version: str | None,
    policy: ExecutionPolicy,
) -> AgentExecutionResult:
    """Execute one request and normalize the outcome without retrying."""
    workspace = validate_workspace(request.working_directory)
    context = ResultContext(identity=identity, server_version=server_version, agent="build")
    session_ref: str | None = None
    try:
        _reject_unsupported_output(request)
        model = parse_native_model(request.model)
        agent = agent_for_request(request.strategy, request.mutation_policy)
        context = ResultContext(identity=identity, server_version=server_version, agent=agent)
        spec = SessionSpec(
            workspace=workspace,
            agent=agent,
            model=model,
            rules=permission_rules(request.mutation_policy, request.permission_policy),
        )
        session = _resolve_session(client, request, spec)
        session_ref = session.session_id
        _notify_started(policy.on_session_started, session_ref)
        outcome = _run_session(client, session, request, policy=policy)
        return _result_from_outcome(
            client,
            context=context,
            session=session,
            outcome=outcome,
        )
    except OpenCodeServerError as exc:
        return failure_result(
            identity,
            error=exc,
            session_ref=session_ref,
            server_version=server_version,
        )


def validate_workspace(value: str) -> Path:
    """Resolve and validate the authorized workspace directory."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise OpenCodeServerError(
            ServerFailureCode.WORKSPACE_INVALID,
            "working_directory must be an absolute path",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise OpenCodeServerError(
            ServerFailureCode.WORKSPACE_INVALID,
            f"working_directory cannot be resolved: {exc}",
        ) from exc
    if not resolved.is_dir():
        raise OpenCodeServerError(
            ServerFailureCode.WORKSPACE_INVALID,
            "working_directory must be an existing directory",
        )
    return resolved


def _reject_unsupported_output(request: AgentExecutionRequest) -> None:
    if request.structured_output_schema is not None:
        raise OpenCodeServerError(
            ServerFailureCode.UNSUPPORTED_REQUEST,
            "structured_output_schema is not supported by the documented prompt API",
        )


def _resolve_session(
    client: ServerClient,
    request: AgentExecutionRequest,
    spec: SessionSpec,
) -> SessionInfo:
    if request.session_ref is not None:
        session = client.get_session(request.session_ref)
        _verify_session_workspace(session, spec.workspace)
        return session
    return client.create_session(
        directory=str(spec.workspace),
        title=session_title(),
        model=spec.model,
        agent=spec.agent,
        permissions=spec.rules,
    )


def _verify_session_workspace(session: SessionInfo, workspace: Path) -> None:
    if session.directory is None:
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_REQUEST,
            "reused session has no location directory",
        )
    try:
        session_directory = Path(session.directory).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_REQUEST,
            f"reused session directory cannot be resolved: {exc}",
        ) from exc
    if session_directory != workspace:
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_REQUEST,
            "reused session belongs to a different workspace",
        )


def _run_session(
    client: ServerClient,
    session: SessionInfo,
    request: AgentExecutionRequest,
    *,
    policy: ExecutionPolicy,
) -> WaitOutcome:
    stream = client.open_events(TERMINAL_EVENT_TYPES)
    stop = threading.Event()
    events: queue.Queue[object] = queue.Queue()
    reader = threading.Thread(
        target=_pump_events,
        args=(stream, events, stop),
        daemon=True,
        name=f"opencode-server-events-{session.session_id}",
    )
    try:
        stream.open()
        reader.start()
        client.prompt(session.session_id, request.task)
        return _wait_for_terminal(
            client,
            session.session_id,
            events,
            policy=policy,
        )
    finally:
        stop.set()
        stream.shutdown()
        reader.join(timeout=_READER_JOIN_SECONDS)
        stream.close()


def _pump_events(
    stream: EventStream,
    target: queue.Queue[object],
    stop: threading.Event,
) -> None:
    error: OpenCodeServerError | None = None
    try:
        for event in stream.iter_events():
            if stop.is_set():
                break
            target.put(event)
    except OpenCodeServerError as exc:
        error = exc
    except ValueError as exc:
        error = OpenCodeServerError(
            ServerFailureCode.STREAM_INTERRUPTED,
            f"event stream reader stopped: {exc}",
        )
    finally:
        target.put(_StreamEnded(error=error))


def _wait_for_terminal(
    client: ServerClient,
    session_id: str,
    events: queue.Queue[object],
    *,
    policy: ExecutionPolicy,
) -> WaitOutcome:
    deadline = time.monotonic() + policy.timeout_seconds
    state = _WaitState()
    while time.monotonic() < deadline:
        if policy.cancel_event is not None and policy.cancel_event.is_set():
            return _cancel_outcome(client, session_id, state.rejections)
        item = _next_event(events, max(0.0, deadline - time.monotonic()))
        if isinstance(item, _StreamEnded):
            state.stream_error = item.error
            break
        if isinstance(item, Mapping):
            terminal = _handle_event(client, session_id, item, state)
            if terminal is not None:
                return terminal
    return _after_event_loop(client, session_id, state)


def _handle_event(
    client: ServerClient,
    session_id: str,
    event: Mapping[str, object],
    state: _WaitState,
) -> WaitOutcome | None:
    event_type, data = _event_parts(event, session_id)
    if event_type is None or data is None:
        return None
    if event_type == "permission.asked":
        state.rejections += _reject_permission(client, session_id, data)
        return None
    if event_type == "session.execution.started":
        state.started = True
        return None
    return _terminal_outcome(client, session_id, event, event_type, state)


def _event_parts(
    event: Mapping[str, object],
    session_id: str,
) -> tuple[str | None, Mapping[str, object] | None]:
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, Mapping):
        return None, None
    if data.get("sessionID") != session_id:
        return None, None
    return event_type, data


def _terminal_outcome(
    client: ServerClient,
    session_id: str,
    event: Mapping[str, object],
    event_type: str,
    state: _WaitState,
) -> WaitOutcome | None:
    if event_type in _TERMINAL_EXECUTION_EVENTS:
        return WaitOutcome(event=event, permission_rejections=state.rejections)
    if event_type != "session.idle" or not state.started:
        return None
    terminal = _terminal_from_session_state(client, session_id)
    return None if terminal is None else WaitOutcome(event=terminal, permission_rejections=state.rejections)


def _after_event_loop(
    client: ServerClient,
    session_id: str,
    state: _WaitState,
) -> WaitOutcome:
    stream_error = state.stream_error
    if stream_error is not None and stream_error.code is ServerFailureCode.STREAM_INTERRUPTED:
        terminal = _terminal_from_session_state(client, session_id)
        if terminal is not None:
            return WaitOutcome(
                event=terminal,
                permission_rejections=state.rejections,
                stream_error=stream_error,
            )
    if stream_error is not None:
        raise stream_error
    return _timeout_outcome(client, session_id)


def _next_event(events: queue.Queue[object], timeout: float) -> object:
    try:
        return events.get(timeout=min(timeout, _HTTP_EVENT_QUEUE_TIMEOUT) if timeout > 0 else 0.0)
    except queue.Empty:
        return None


def _reject_permission(
    client: ServerClient,
    session_id: str,
    data: Mapping[str, object],
) -> int:
    request_id = data.get("id")
    if not isinstance(request_id, str) or not request_id:
        raise OpenCodeServerError(
            ServerFailureCode.INVALID_RESPONSE,
            "permission request is missing its identifier",
        )
    client.reply_permission(session_id, request_id, _REJECT_DECISION)
    return 1


def _terminal_from_session_state(
    client: ServerClient,
    session_id: str,
) -> Mapping[str, object] | None:
    session = client.get_session(session_id)
    status = status_from_outcome(session.outcome)
    if status is None:
        return None
    suffix = {
        AgentExecutionStatus.SUCCEEDED: "succeeded",
        AgentExecutionStatus.FAILED: "failed",
        AgentExecutionStatus.CANCELLED: "interrupted",
    }[status]
    return {"type": f"session.execution.{suffix}", "data": {"sessionID": session_id}}


def _cancel_outcome(
    client: ServerClient,
    session_id: str,
    rejections: int,
) -> WaitOutcome:
    interrupt_error = _interrupt(client, session_id)
    if interrupt_error is not None:
        raise OpenCodeServerError(
            ServerFailureCode.RUNTIME_EXECUTION_FAILURE,
            f"cancellation requested but interrupt failed: {interrupt_error.message}",
        )
    return WaitOutcome(
        event={"type": "session.execution.interrupted", "data": {"sessionID": session_id, "reason": "user"}},
        cancelled=True,
        permission_rejections=rejections,
    )


def _timeout_outcome(
    client: ServerClient,
    session_id: str,
) -> WaitOutcome:
    interrupt_error = _interrupt(client, session_id)
    message = "execution timed out before a terminal event"
    if interrupt_error is not None:
        message = f"{message}; interrupt failed: {interrupt_error.message}"
    raise OpenCodeServerError(ServerFailureCode.EXECUTION_TIMEOUT, message)


def _interrupt(client: ServerClient, session_id: str) -> OpenCodeServerError | None:
    try:
        client.interrupt(session_id)
    except OpenCodeServerError as exc:
        return exc
    return None


def _result_from_outcome(
    client: ServerClient,
    *,
    context: ResultContext,
    session: SessionInfo,
    outcome: WaitOutcome,
) -> AgentExecutionResult:
    final_session = client.get_session(session.session_id)
    messages = client.messages(session.session_id)
    diffs = client.diffs(session.session_id)
    status = _normalized_status(outcome, final_session)
    failures = _failures_for(status, outcome, final_session)
    text = final_text(messages)
    metadata = metadata_pairs(
        MetadataInput(
            version=context.server_version,
            session=final_session,
            agent=context.agent,
            diff_count=len(diffs),
            usage=final_session.tokens,
            extra=_permission_metadata(outcome.permission_rejections),
        ),
    )
    return AgentExecutionResult(
        status=status,
        runtime_identity=context.identity,
        model_id=None if final_session.model is None else final_session.model.as_text(),
        session_ref=final_session.session_id,
        structured_output=text or None,
        evidence_refs=evidence_refs(final_session.session_id, messages, diffs),
        usage=usage_from_session(final_session),
        observed_failures=failures,
        harness_metadata=metadata,
    )


def _normalized_status(outcome: WaitOutcome, session: SessionInfo) -> AgentExecutionStatus:
    state_status = status_from_outcome(session.outcome)
    event = outcome.event
    event_type = None if event is None else event.get("type")
    if event_type == "session.execution.succeeded":
        return AgentExecutionStatus.SUCCEEDED if state_status is None else state_status
    if event_type == "session.execution.failed":
        return AgentExecutionStatus.FAILED
    if event_type == "session.execution.interrupted":
        return AgentExecutionStatus.CANCELLED
    if state_status is not None:
        return state_status
    return AgentExecutionStatus.FAILED


def _failures_for(
    status: AgentExecutionStatus,
    outcome: WaitOutcome,
    session: SessionInfo,
) -> tuple[str, ...]:
    if status is AgentExecutionStatus.SUCCEEDED:
        return ()
    event = outcome.event
    if event is not None and event.get("type") == "session.execution.failed":
        return (execution_failure_detail(event),)
    if status is AgentExecutionStatus.CANCELLED:
        return ()
    if session.outcome == "failed":
        return ("session reported a failed outcome without a structured error",)
    return (ServerFailureCode.INCOMPLETE_RESULT.value,)


def failure_result(
    identity: RuntimeIdentity,
    *,
    error: OpenCodeServerError,
    session_ref: str | None,
    server_version: str | None,
) -> AgentExecutionResult:
    """Build the neutral failure result for an adapter-level error."""
    metadata: list[tuple[str, str]] = [("adapter", "opencode-server")]
    if session_ref is not None:
        metadata.append(("session_id", session_ref))
    if server_version is not None:
        metadata.append(("server_version", server_version))
    return AgentExecutionResult(
        status=AgentExecutionStatus.FAILED,
        runtime_identity=identity,
        session_ref=session_ref,
        evidence_refs=() if session_ref is None else (f"opencode-server:session:{session_ref}",),
        observed_failures=(f"{error.code.value}: {redact_secret(error.message)}",),
        harness_metadata=tuple(metadata),
    )


def _permission_metadata(rejections: int) -> tuple[tuple[str, str], ...]:
    if rejections <= 0:
        return ()
    return (("permission_rejections", str(rejections)),)


def _notify_started(callback: Callable[[str], None] | None, session_ref: str) -> None:
    if callback is not None:
        callback(session_ref)
