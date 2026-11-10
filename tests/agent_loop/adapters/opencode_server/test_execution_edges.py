"""Edge-branch tests for OpenCode Server execution helpers.

These cover defensive protocol paths that the main behavioral suite reaches
only through malformed or partial server data.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from typing import cast

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.execution import WaitOutcome
from agent_loop.adapters.opencode_server.execution import _failures_for
from agent_loop.adapters.opencode_server.execution import _handle_event
from agent_loop.adapters.opencode_server.execution import _normalized_status
from agent_loop.adapters.opencode_server.execution import _pump_events
from agent_loop.adapters.opencode_server.execution import _StreamEnded
from agent_loop.adapters.opencode_server.execution import _terminal_outcome
from agent_loop.adapters.opencode_server.execution import _verify_session_workspace
from agent_loop.adapters.opencode_server.execution import _WaitState
from agent_loop.adapters.opencode_server.execution import validate_workspace
from agent_loop.adapters.opencode_server.models import SessionInfo
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.domain.faults import FailureCategory
from agent_loop.ports.agent_runtime import AgentExecutionStatus
from tests.agent_loop.adapters.opencode_server.test_runtime import _request
from tests.agent_loop.adapters.opencode_server.test_runtime import _runtime
from tests.agent_loop.adapters.opencode_server.test_runtime import _ScriptedClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Mapping

    from agent_loop.adapters.opencode_server.client import EventStream
    from agent_loop.adapters.opencode_server.client import ServerClient


class _RaisingStream:
    def iter_events(self) -> Iterator[Mapping[str, object]]:
        message = "broken iterator"
        raise ValueError(message)


class _StoppingStream:
    def __init__(self, stop: threading.Event) -> None:
        self._stop = stop

    def iter_events(self) -> Iterator[Mapping[str, object]]:
        self._stop.set()
        yield {"type": "ignored"}


class _SessionClient:
    def __init__(self, session: SessionInfo) -> None:
        self._session = session

    def get_session(self, session_id: str) -> SessionInfo:
        del session_id
        return self._session


class _NoClient:
    def get_session(self, session_id: str) -> SessionInfo:
        del session_id
        message = "client must not be used for malformed events"
        raise AssertionError(message)

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        del session_id, request_id, decision
        message = "client must not be used for malformed events"
        raise AssertionError(message)

    def interrupt(self, session_id: str) -> bool:
        del session_id
        message = "client must not be used for malformed events"
        raise AssertionError(message)


def _session(directory: str | None = "/workspace", outcome: str | None = None) -> SessionInfo:
    return SessionInfo("ses_1", directory, None, outcome, TokenUsage(), "t")


def test_validate_workspace_rejects_existing_file(tmp_path: Path) -> None:
    """A file is not an authorized workspace directory."""
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(OpenCodeServerError) as captured:
        validate_workspace(str(target))
    assert captured.value.code is ServerFailureCode.WORKSPACE_INVALID


def test_verify_session_rejects_missing_directory() -> None:
    """A reused session without location evidence fails closed."""
    with pytest.raises(OpenCodeServerError) as captured:
        _verify_session_workspace(_session(directory=None), Path("/workspace"))
    assert captured.value.code is ServerFailureCode.INVALID_REQUEST


def test_verify_session_rejects_unresolvable_directory(monkeypatch) -> None:
    """A session path that cannot be resolved is invalid, not accepted."""
    original = Path.resolve

    def _raise(self: Path, *args, **kwargs) -> Path:
        del self, args, kwargs
        message = "cannot resolve"
        raise OSError(message)

    monkeypatch.setattr(Path, "resolve", _raise)
    try:
        with pytest.raises(OpenCodeServerError) as captured:
            _verify_session_workspace(_session(), Path("/workspace"))
        assert captured.value.code is ServerFailureCode.INVALID_REQUEST
    finally:
        monkeypatch.setattr(Path, "resolve", original)


def test_pump_events_wraps_reader_value_error() -> None:
    """A broken event iterator is reported as stream interruption."""
    target: queue.Queue[object] = queue.Queue()
    _pump_events(cast("EventStream", _RaisingStream()), target, threading.Event())
    ended = target.get_nowait()
    assert isinstance(ended, _StreamEnded)
    assert ended.error is not None
    assert ended.error.code is ServerFailureCode.STREAM_INTERRUPTED


def test_pump_events_stops_when_cancelled() -> None:
    """The reader stops delivering events once shutdown is requested."""
    stop = threading.Event()
    target: queue.Queue[object] = queue.Queue()
    _pump_events(cast("EventStream", _StoppingStream(stop)), target, stop)
    ended = target.get_nowait()
    assert isinstance(ended, _StreamEnded)


def test_handle_event_ignores_malformed_payloads() -> None:
    """Missing type/data or a foreign session id never becomes a transition."""
    state = _WaitState()
    client = cast("ServerClient", _NoClient())
    assert _handle_event(client, "ses_1", {}, state) is None
    assert _handle_event(client, "ses_1", {"type": 1, "data": {}}, state) is None
    assert _handle_event(client, "ses_1", {"type": "session.idle", "data": {"sessionID": "ses_2"}}, state) is None


def test_handle_event_tracks_started() -> None:
    """Started events set state without claiming a terminal outcome."""
    state = _WaitState()
    client = cast("ServerClient", _NoClient())
    event = {"type": "session.execution.started", "data": {"sessionID": "ses_1"}}
    assert _handle_event(client, "ses_1", event, state) is None
    assert state.started is True


def test_terminal_outcome_handles_idle_without_terminal_state() -> None:
    """Idle before start or without a recorded outcome is not terminal."""
    client = _SessionClient(_session())
    assert (
        _terminal_outcome(
            cast("ServerClient", _SessionClient(_session())),
            "ses_1",
            {"type": "other", "data": {}},
            "other",
            _WaitState(),
        )
        is None
    )
    assert (
        _terminal_outcome(
            cast("ServerClient", client),
            "ses_1",
            {"type": "session.idle", "data": {}},
            "session.idle",
            _WaitState(),
        )
        is None
    )
    assert (
        _terminal_outcome(
            cast("ServerClient", client),
            "ses_1",
            {"type": "session.idle", "data": {}},
            "session.idle",
            _WaitState(started=True),
        )
        is None
    )


def test_terminal_outcome_recovers_state_after_idle() -> None:
    """An idle session with a recorded outcome yields a terminal event."""
    outcome = _terminal_outcome(
        cast("ServerClient", _SessionClient(_session(outcome="succeeded"))),
        "ses_1",
        {"type": "session.idle", "data": {}},
        "session.idle",
        _WaitState(started=True),
    )
    assert outcome is not None
    assert outcome.event is not None
    assert outcome.event["type"] == "session.execution.succeeded"


def test_normalized_status_fallback_is_failed() -> None:
    """Unknown events without a session outcome never become success."""
    assert _normalized_status(WaitOutcome(event={"type": "unknown"}), _session()) is AgentExecutionStatus.FAILED


def test_normalized_status_uses_recorded_session_outcome() -> None:
    """An unclassified event falls back to the session's observed outcome."""
    assert (
        _normalized_status(WaitOutcome(event={"type": "unknown"}), _session(outcome="succeeded"))
        is AgentExecutionStatus.SUCCEEDED
    )


def test_failures_for_reports_failed_session_without_event() -> None:
    """A failed session without a structured error still reports a reason."""
    assert _failures_for(AgentExecutionStatus.FAILED, WaitOutcome(), _session(outcome="failed")) == (
        "session reported a failed outcome without a structured error",
    )


def test_failures_for_fallback_is_incomplete_result() -> None:
    """A failed status without evidence reports incomplete result."""
    assert _failures_for(AgentExecutionStatus.FAILED, WaitOutcome(), _session()) == (
        ServerFailureCode.INCOMPLETE_RESULT.value,
    )


class _InterruptFailingClient(_ScriptedClient):
    """Client whose interrupt transport always fails."""

    def interrupt(self, session_id: str) -> bool:
        raise OpenCodeServerError(ServerFailureCode.SERVER_UNAVAILABLE, f"cannot interrupt {session_id}")


def test_execute_malformed_permission_request_fails_closed(tmp_path: Path) -> None:
    """A permission request without an identifier is invalid, never approved."""
    client = _ScriptedClient(
        events=(
            {"type": "permission.asked", "data": {"action": "read"}},
            {"type": "session.execution.succeeded", "data": {}},
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert result.failure is not None
    assert result.failure.category is FailureCategory.INVALID_RESULT
    assert any("invalid_response" in failure for failure in result.observed_failures)


def test_execute_idle_event_recovers_terminal_session_state(tmp_path: Path) -> None:
    """An idle event after start reads the recorded session outcome."""
    client = _ScriptedClient(
        outcome="succeeded",
        events=(
            {"type": "session.execution.started", "data": {}},
            {"type": "session.idle", "data": {}},
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "succeeded"
    assert result.session_ref == "ses_1"


def test_execute_ignores_foreign_and_malformed_events(tmp_path: Path) -> None:
    """Events for another session or without payloads do not drive state."""
    client = _ScriptedClient(
        events=(
            {"type": 1, "data": {}},
            {"type": "session.idle", "data": {"sessionID": "ses_other"}},
            {"type": "session.execution.succeeded", "data": {}},
        ),
    )
    result = _runtime(client).execute(_request(tmp_path))
    assert result.status.value == "succeeded"


def test_execute_cancellation_interrupt_failure_is_not_masked(tmp_path: Path) -> None:
    """A failed interrupt during cancellation is an explicit runtime failure."""
    client = _InterruptFailingClient(outcome=None)
    cancel = threading.Event()
    cancel.set()
    result = _runtime(client).execute_with_cancellation(_request(tmp_path), cancel)
    assert result.status.value == "failed"
    assert any("runtime_execution_failure" in failure for failure in result.observed_failures)


def test_execute_timeout_interrupt_failure_is_reported(tmp_path: Path) -> None:
    """A failed timeout interrupt remains a timeout with explicit detail."""
    client = _InterruptFailingClient(outcome=None)
    result = _runtime(client, execution_timeout_seconds=0.1).execute(_request(tmp_path))
    assert result.status.value == "failed"
    assert result.failure is not None
    assert result.failure.category is FailureCategory.TIMEOUT
    rendered = " ".join(result.observed_failures)
    assert "execution_timeout" in rendered
    assert "interrupt failed" in rendered
