"""Final narrow contracts to keep the complete product at the 99% gate."""

from __future__ import annotations

import configparser
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_loop.adapters.opencode_server.client import OpenCodeServerClient
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.lifecycle import _terminate_process_group
from agent_loop.adapters.opencode_server.mapping import execution_failure_detail
from agent_loop.adapters.opencode_server.models import AssistantMessage
from agent_loop.adapters.opencode_server.models import TokenUsage
from agent_loop.adapters.opencode_server.models import _assistant_content
from agent_loop.adapters.opencode_server.models import _tool_failures
from agent_loop.adapters.opencode_server.models import optional_float
from agent_loop.adapters.opencode_server.models import optional_int
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.transport import HttpResponse
from agent_loop.autonomy.discovery.compose import _compose_service_dockerfile
from agent_loop.autonomy.discovery.compose import _dockerfile_arguments
from agent_loop.autonomy.discovery.compose import _dockerfile_workdir
from agent_loop.autonomy.discovery.compose import _score
from agent_loop.autonomy.discovery.compose import _yaml_services
from agent_loop.autonomy.discovery.tool_config import _ini_section
from agent_loop.autonomy.discovery.tool_config import _read_text
from agent_loop.autonomy.discovery.tool_config import _workspace_dir
from agent_loop.autonomy.discovery.tool_config import _workspace_file
from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import TargetWorkspace
from tests.agent_loop.autonomy.support import make_project
from tests.agent_loop.autonomy.support import write


def test_autonomy_module_entrypoint() -> None:
    """``python -m agent_loop.autonomy`` dispatches to the CLI main."""
    with patch("agent_loop.autonomy.cli.main", return_value=0), pytest.raises(SystemExit) as captured:
        runpy.run_module("agent_loop.autonomy", run_name="__main__")
    assert captured.value.code == 0


def test_tool_config_ini_parser_and_io_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ConfigParser/OSError/security failures return neutral empty evidence."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo"))
    write(workspace.root / "setup.cfg", "[mypy]\nstrict = true\n")
    monkeypatch.setattr(
        configparser.ConfigParser,
        "read",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(configparser.Error("bad")),
    )
    assert _ini_section(workspace, "setup.cfg", "mypy") is False
    monkeypatch.undo()
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError("x")))
    assert _read_text(workspace.root / "setup.cfg") == ""
    monkeypatch.undo()
    monkeypatch.setattr(TargetWorkspace, "resolve", lambda self, candidate: (_ for _ in ()).throw(SecurityError("x")))
    assert _workspace_file(workspace, "setup.cfg") is None
    assert _workspace_dir(workspace, "scripts") is None


def test_compose_empty_evidence_and_yaml_shapes(tmp_path: Path) -> None:
    """Empty evidence, non-mapping services and missing Dockerfiles stay neutral."""
    workspace = TargetWorkspace.open(make_project(tmp_path / "repo", compose=False))
    write(workspace.root / "README.md", "")
    assert _score(workspace, ("compose.yaml",)) == {"compose.yaml": (0, ())}
    write(workspace.root / "services.yaml", "services: []\n")
    assert _yaml_services(workspace, "services.yaml") == ()
    assert _compose_service_dockerfile(workspace, ("compose.yaml",), {"build": {"context": 1}}) is None
    assert _dockerfile_arguments(workspace, ("compose.yaml",), "web") == {}
    write(workspace.root / "Dockerfile", "FROM python\n")
    assert _dockerfile_workdir(workspace, "Dockerfile") is None


def test_opencode_optional_parsers_and_assistant_content() -> None:
    """Optional numeric/assistant parsing never invents values."""
    assert optional_int(True) is None  # noqa: FBT003
    assert optional_int("3") is None
    assert optional_float(1.5) == 1.5
    assert _assistant_content("not-a-list") == ("", ())
    assert _assistant_content([1, {"type": "text", "text": "ok"}]) == ("ok", ())
    assert _tool_failures({"state": {"status": "running"}}) == ()
    assert execution_failure_detail({"data": {"error": {}}}) == "execution failed"
    assert AssistantMessage("m", "text").tool_failures == ()
    assert TokenUsage().input_tokens is None


def test_opencode_client_error_detail_non_mapping() -> None:
    """A non-mapping error body has no detail but still fails with the status."""
    client = OpenCodeServerClient("http://127.0.0.1:4096")
    client._transport = SimpleNamespace(  # type: ignore[assignment]  # noqa: SLF001
        request_json=lambda *args, **kwargs: HttpResponse(500, ["bad"]),
        base_url="http://127.0.0.1:4096",
    )
    with pytest.raises(OpenCodeServerError) as captured:
        client.info()
    assert "status 500" in captured.value.message


def test_runtime_none_interrupt_and_auto_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime branches for no lifecycle, cached client and managed auto-start."""
    runtime = OpenCodeServerRuntime()
    assert runtime.ensure_started() is None
    fake_client = SimpleNamespace(interrupt=lambda session: session == "ses_1")
    runtime._client = fake_client  # noqa: SLF001
    runtime._client_key = ("base", None)  # noqa: SLF001
    assert runtime.interrupt("ses_1") is True

    managed = ManagedServer(ManagedServerConfig(binary="opencode"))
    monkeypatch.setattr(managed, "start", lambda: SimpleNamespace(base_url="http://127.0.0.1:4096"))
    managed_runtime = OpenCodeServerRuntime(managed=managed)
    calls = {"count": 0}

    def _discovery_client() -> object:
        calls["count"] += 1
        return None if calls["count"] == 1 else fake_client

    monkeypatch.setattr(managed_runtime, "_discovery_client", _discovery_client)
    assert managed_runtime._execution_client() is fake_client  # noqa: SLF001

    attached = OpenCodeServerRuntime.attached("http://127.0.0.1:4096", password="secret")  # noqa: S106
    assert attached._available_endpoint() == ("http://127.0.0.1:4096", "secret")  # noqa: SLF001


def test_terminate_process_group_falls_back_to_force_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process ignoring SIGTERM is force-killed."""
    waits: list[float] = []

    class _Process:
        pid = 123

        def wait(self, timeout: float) -> None:
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("agent_loop.adapters.opencode_server.lifecycle.os.killpg", lambda pgid, sig: None)
    _terminate_process_group(_Process())  # type: ignore[arg-type]
    assert len(waits) == 2
