"""Regression tests for the pytest/Behave heavy-request control."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from features import environment as behave_environment
from tests.agent_loop.quality import heavy_requests
from tests.agent_loop.quality.heavy_requests import HeavyRequestGuard
from tests.agent_loop.quality.heavy_requests import classify_address
from tests.agent_loop.quality.heavy_requests import classify_audit_event
from tests.agent_loop.quality.heavy_requests import classify_command
from tests.agent_loop.quality.heavy_requests import classify_host
from tests.agent_loop.quality.heavy_requests import install_audit_hook
from tests.agent_loop.quality.heavy_requests import record_authorization
from tests.agent_loop.quality.heavy_requests import render_report
from tests.agent_loop.quality.heavy_requests import statistics

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def isolated_statistics() -> Iterator[None]:
    """Restore session statistics after a test that fabricates report entries."""
    saved = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in heavy_requests._STATS.items()  # noqa: SLF001 - test isolation seam
    }
    yield
    heavy_requests._STATS.clear()  # noqa: SLF001 - test isolation seam
    heavy_requests._STATS.update(saved)  # noqa: SLF001 - test isolation seam


def _stat_int(name: str) -> int:
    value = statistics()[name]
    assert isinstance(value, int)
    return value


def _stat_list(name: str) -> list[object]:
    value = statistics()[name]
    assert isinstance(value, list)
    return value


def _fake_heavy_executable(tmp_path: Path) -> Path:
    """Create a harmless executable whose basename classifies as heavy."""
    executable = tmp_path / "docker"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize(
    "command",
    [
        ("docker", "compose", "up"),
        ("opencode", "run"),
        ("claude", "-p", "x"),
        ("git", "fetch", "origin"),
        ("git", "push"),
        ("python", "-m", "pip", "install", "x"),
        ("sh", "-c", "docker compose up"),
        ("pip3", "install", "x"),
        ("env", "FOO=bar", "docker", "ps"),
        ("env", "-S", "docker ps"),
        ("timeout", "5", "docker", "ps"),
        ("sudo", "-u", "builder", "docker", "ps"),
        ("nice", "-n", "10", "git", "fetch", "origin"),
        ("dash", "-c", "docker ps"),
        ("busybox", "sh", "-c", "docker ps"),
    ],
)
def test_heavy_commands_are_rejected(command: tuple[str, ...]) -> None:
    """Provider CLIs, containers, package installs and remote Git are heavy."""
    violation = classify_command(command)
    assert violation is not None
    assert violation.render().startswith("HEAVY REQUEST VIOLATION:")


@pytest.mark.parametrize(
    "command",
    [
        ("git", "status", "--porcelain"),
        ("git", "diff", "--check"),
        (sys.executable, "-c", "print('safe')"),
        ("sh", "-c", "printf safe"),
        ("pytest", "-q"),
        ("env", "FOO=bar", "python", "-c", "print('safe')"),
        ("timeout", "5", "python", "-c", "print('safe')"),
        ("dash", "-c", "printf safe"),
    ],
)
def test_local_commands_are_allowed(command: tuple[str, ...]) -> None:
    """Local Git, Python and shell operations are not heavy requests."""
    assert classify_command(command) is None


def test_non_loopback_hosts_are_heavy_but_localhost_is_not() -> None:
    """Only real network destinations are rejected."""
    assert classify_host("127.0.0.1") is None
    assert classify_host("::1") is None
    assert classify_host("localhost") is None
    assert classify_host("localhost.") is None
    assert classify_host("api.openai.com") is not None
    assert classify_address(("127.0.0.1", 8080)) is None
    assert classify_address(("203.0.113.10", 443)) is not None


@pytest.mark.heavy_request
def test_guard_records_real_subprocess_audit_events(tmp_path: Path) -> None:
    """Detection uses Python audit events, not a text search."""
    expected_before = _stat_int("expected")
    violations_before = len(_stat_list("violations"))
    executable = _fake_heavy_executable(tmp_path)
    guard = HeavyRequestGuard(expected=True)
    with guard:
        subprocess.run(  # noqa: S603 - temporary fixture executable
            (str(executable), "ps"),
            check=False,
            capture_output=True,
            text=True,
        )
    assert guard.violations
    assert guard.violations[0].operation == "command"
    assert guard.observed >= 1
    assert len(_stat_list("violations")) == violations_before
    assert _stat_int("expected") >= expected_before + 1


@pytest.mark.heavy_request
def test_guard_allows_an_explicit_heavy_opt_in(tmp_path: Path) -> None:
    """An explicit opt-in records a traceable authorized exception."""
    executable = _fake_heavy_executable(tmp_path)
    guard = HeavyRequestGuard(allow=True, reason="test opt-in")
    with guard:
        subprocess.run(  # noqa: S603 - temporary fixture executable
            (str(executable), "ps"),
            check=False,
            capture_output=True,
            text=True,
        )
    assert guard.violations == []
    assert guard.authorized
    report = render_report("unit")
    assert "initialized=yes" in report
    assert "HEAVY REQUEST ALLOWED: test opt-in" in report


def test_guard_report_distinguishes_missing_activation_from_clean_run() -> None:
    """A clean run is not evidence until initialization and observation are shown."""
    activated_before = _stat_int("activated")
    observed_before = _stat_int("observed")
    with HeavyRequestGuard():
        subprocess.run(
            (sys.executable, "-c", "print('safe')"),
            check=False,
            capture_output=True,
            text=True,
        )
    report = render_report("unit")
    assert "initialized=yes" in report
    assert f"activated={activated_before + 1}" in report
    assert _stat_int("observed") > observed_before


def test_guard_records_non_loopback_network_events() -> None:
    """The audit classifier sees a non-loopback address as a violation."""
    expected_before = _stat_int("expected")
    guard = HeavyRequestGuard(expected=True)
    with guard:
        guard.check("socket.connect", (None, ("203.0.113.10", 443)))
    assert guard.violations
    assert guard.violations[0].operation == "network"
    assert _stat_int("expected") == expected_before + 1


def test_guard_records_dns_and_datagram_audit_events() -> None:
    """DNS variants and UDP/address events are classified, not silently allowed."""
    guard = HeavyRequestGuard(expected=True)
    with guard:
        guard.check("socket.gethostbyname", ("api.openai.com",))
        guard.check("socket.gethostbyname_ex", ("api.openai.com",))
        guard.check("socket.gethostbyaddr", ("203.0.113.10",))
        guard.check("socket.getnameinfo", (("203.0.113.10", 53), 0))
        guard.check("socket.sendto", (None, ("203.0.113.10", 53)))
        guard.check("socket.sendmsg", (None, (), (), 0, ("203.0.113.10", 53)))
    assert len(guard.violations) == 6
    assert all(violation.operation == "network" for violation in guard.violations)
    assert classify_audit_event("subprocess.Popen", ()) is None
    assert classify_audit_event("socket.connect", ()) is None
    assert classify_audit_event("subprocess.Popen", ("docker", ["safe"], None, None)) is not None
    assert classify_audit_event("os.exec", ("docker", ["safe"], None)) is not None
    assert classify_audit_event("os.system", ("docker ps",)) is not None
    assert (
        classify_audit_event(
            "subprocess.Popen",
            ("/usr/bin/docker", ["/usr/bin/docker", "compose", "version"], None, None),
        )
        is None
    )


@pytest.mark.heavy_request
def test_spawn_wrapper_detects_os_spawn_without_a_cpython_audit_event(tmp_path: Path) -> None:
    """``os.spawn*`` has no audit event, so the guard wraps the callable."""
    install_audit_hook()
    executable = tmp_path / "docker"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    guard = HeavyRequestGuard(expected=True)
    with guard:
        os.spawnv(os.P_WAIT, str(executable), [str(executable)])  # noqa: S606 - wrapper coverage
    assert guard.violations
    assert guard.violations[0].operation == "command"
    assert "docker" in guard.violations[0].detail


def test_spawn_wrapper_keeps_safe_local_commands_allowed(tmp_path: Path) -> None:
    """Wrapping ``os.spawn*`` keeps local commands and version probes allowed."""
    install_audit_hook()
    executable = _fake_heavy_executable(tmp_path)
    guard = HeavyRequestGuard()
    with guard:
        os.spawnv(os.P_WAIT, sys.executable, [sys.executable, "-c", "pass"])  # noqa: S606 - wrapper coverage
        os.spawnv(  # noqa: S606 - wrapper coverage
            os.P_WAIT,
            str(executable),
            [str(executable), "compose", "version"],
        )
    assert guard.violations == []


def test_repository_authorization_is_visible(isolated_statistics: object) -> None:
    """The report keeps every authorized reason."""
    del isolated_statistics
    record_authorization("pytest.mark.heavy_request", detail="tests/example.py::test_real")
    report = render_report("unit")
    assert "HEAVY REQUEST ALLOWED: pytest.mark.heavy_request" in report
    assert "tests/example.py::test_real" in report


def test_guard_does_not_flag_safe_local_operations() -> None:
    """Ordinary local subprocesses stay allowed."""
    guard = HeavyRequestGuard()
    with guard:
        completed = subprocess.run(
            (sys.executable, "-c", "print('ok')"),
            check=False,
            capture_output=True,
            text=True,
        )
    assert completed.stdout.strip() == "ok"
    assert guard.violations == []


def test_behave_environment_enforces_the_guard_and_cleans_up(tmp_path: Path) -> None:
    """The Behave hook fails undeclared heavy work after cleanup."""
    violations_before = len(_stat_list("violations"))
    context = SimpleNamespace(root=tmp_path / "scenario")
    context.root.mkdir()
    scenario = SimpleNamespace(tags=["expected-heavy-violation"], name="expected")
    behave_environment.before_scenario(context, scenario)
    context.guard.check("subprocess.Popen", (None, ("docker", "ps"), None, None))
    with pytest.raises(AssertionError, match="HEAVY REQUEST VIOLATION"):
        behave_environment.after_scenario(context, scenario)
    assert not context.root.exists()
    assert len(_stat_list("violations")) == violations_before


def test_behave_environment_allows_tagged_heavy_scenarios(tmp_path: Path, isolated_statistics: object) -> None:
    """``@heavy`` is the explicit Behave exception and records its reason."""
    del isolated_statistics
    context = SimpleNamespace(root=tmp_path / "scenario")
    context.root.mkdir()
    scenario = SimpleNamespace(tags=["heavy"], name="real provider")
    behave_environment.before_scenario(context, scenario)
    context.guard.check("subprocess.Popen", (None, ("docker", "ps"), None, None))
    behave_environment.after_scenario(context, scenario)
    assert context.guard.violations == []
    assert context.guard.authorized
    report = render_report("behave")
    assert "HEAVY REQUEST ALLOWED: behave @heavy (real provider)" in report
