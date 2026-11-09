"""Lifecycle tests for attach and managed OpenCode Server ownership."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.lifecycle import AttachedServer
from agent_loop.adapters.opencode_server.lifecycle import AttachEndpoint
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig

if TYPE_CHECKING:
    from pathlib import Path

_TEST_CREDENTIAL = "test-secret"
_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - intentional invalid bind probe


def _fake_binary(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-opencode"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_attach_mode_never_owns_or_stops_the_process() -> None:
    """Attach mode is explicitly external ownership."""
    server = AttachedServer(AttachEndpoint("http://127.0.0.1:4096", password=_TEST_CREDENTIAL))
    assert server.owned is False
    assert server.running is True
    assert server.base_url == "http://127.0.0.1:4096"
    server.stop()
    assert server.base_url == "http://127.0.0.1:4096"


def test_attached_server_rejects_credential_urls() -> None:
    """Attach URLs must not embed credentials."""
    with pytest.raises(ValueError, match="must not embed credentials"):
        AttachedServer(AttachEndpoint("http://user:secret@127.0.0.1:4096"))


@pytest.mark.parametrize("hostname", [_ALL_INTERFACES, "192.168.1.10", "example.com"])
def test_managed_server_rejects_non_loopback_bind(hostname: str) -> None:
    """Managed processes never bind a non-loopback address."""
    with pytest.raises(ValueError, match="loopback"):
        ManagedServer(ManagedServerConfig(hostname=hostname))


def test_managed_server_rejects_invalid_port() -> None:
    """Port bounds are validated before process creation."""
    with pytest.raises(ValueError, match="between 0 and 65535"):
        ManagedServer(ManagedServerConfig(port=70000))


def test_managed_server_missing_binary_fails_without_orphan(tmp_path: Path) -> None:
    """A missing binary is a startup failure, not a running server."""
    managed = ManagedServer(ManagedServerConfig(binary=str(tmp_path / "missing")))
    with pytest.raises(OpenCodeServerError) as captured:
        managed.start()
    assert captured.value.code is ServerFailureCode.SERVER_START_FAILED
    assert managed.running is False
    assert managed.pid is None


def test_managed_server_early_exit_is_startup_failure(tmp_path: Path) -> None:
    """A child that exits before readiness is reported and cleaned up."""
    binary = _fake_binary(tmp_path, "exit 3")
    managed = ManagedServer(ManagedServerConfig(binary=str(binary), startup_timeout_seconds=2))
    with pytest.raises(OpenCodeServerError) as captured:
        managed.start()
    assert captured.value.code is ServerFailureCode.SERVER_START_FAILED
    assert managed.running is False


def test_managed_server_start_and_stop_owns_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed startup captures URL/password and stop terminates its own child."""
    binary = _fake_binary(
        tmp_path,
        f'echo "server listening on http://127.0.0.1:65535"\necho "server password {_TEST_CREDENTIAL}"\nsleep 30',
    )
    monkeypatch.setattr(ManagedServer, "_wait_until_ready", lambda self, base_url, password: None)
    managed = ManagedServer(ManagedServerConfig(binary=str(binary), startup_timeout_seconds=5))
    endpoint = managed.start()
    try:
        assert endpoint.base_url == "http://127.0.0.1:65535"
        assert endpoint.password == _TEST_CREDENTIAL
        pid = managed.pid
        assert pid is not None
        assert managed.running is True
    finally:
        managed.stop()
    assert managed.running is False
    assert managed.pid is None
    assert not _pid_alive(pid)


def test_managed_server_start_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated start returns the same endpoint while the owned child lives."""
    binary = _fake_binary(
        tmp_path,
        f'echo "server listening on http://127.0.0.1:65534"\necho "server password {_TEST_CREDENTIAL}"\nsleep 30',
    )
    monkeypatch.setattr(ManagedServer, "_wait_until_ready", lambda self, base_url, password: None)
    managed = ManagedServer(ManagedServerConfig(binary=str(binary), startup_timeout_seconds=5))
    try:
        first = managed.start()
        second = managed.start()
        assert first == second
    finally:
        managed.stop()


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
