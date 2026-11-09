"""Tests for read-only inventory construction."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import cast

from agent_loop.adapters.opencode_server.discovery import build_inventory
from agent_loop.adapters.opencode_server.discovery import managed_not_started_inventory
from agent_loop.adapters.opencode_server.discovery import not_configured_inventory
from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.version import VersionPolicy
from agent_loop.domain.capabilities import CapabilitySnapshot
from agent_loop.domain.capabilities import CapabilityState

if TYPE_CHECKING:
    from agent_loop.adapters.opencode_server.client import ServerClient
    from agent_loop.domain.capabilities import RuntimeInventory


def _now() -> datetime:
    return datetime.now(tz=UTC)


class _Client:
    credentials_configured = True
    base_url = "http://127.0.0.1:1"

    def __init__(self, version: str | None = "2.2.6", error: OpenCodeServerError | None = None) -> None:
        self.version = version
        self.error = error

    def info(self) -> ServerInfo:
        if self.error is not None:
            raise self.error
        return ServerInfo(version=self.version, pid=1, urls=(self.base_url,))

    def models(self) -> tuple[ModelRef, ...]:
        return (ModelRef("opencode-go", "model-a"),)

    def agents(self) -> tuple[str, ...]:
        return ("build",)


def _client(
    version: str | None = "2.2.6",
    error: OpenCodeServerError | None = None,
) -> ServerClient:
    return cast("ServerClient", _Client(version=version, error=error))


def _runtime_capability(inventory: RuntimeInventory) -> CapabilitySnapshot:
    return next(item for item in inventory.capabilities if item.capability_id.value == "runtime.opencode.server")


def test_not_configured_inventory_is_explicit() -> None:
    """No configuration yields configured=NO and available=NO."""
    capability = _runtime_capability(not_configured_inventory(observed_at=_now(), project_context=None))
    assert capability.configured is CapabilityState.NO
    assert capability.available is CapabilityState.NO


def test_managed_not_started_inventory_never_claims_availability() -> None:
    """A managed server that was not started is configured but unavailable."""
    capability = _runtime_capability(
        managed_not_started_inventory(observed_at=_now(), project_context=None),
    )
    assert capability.configured is CapabilityState.YES
    assert capability.available is CapabilityState.NO
    assert capability.supported is CapabilityState.UNKNOWN


def test_supported_inventory_reads_models_and_agents() -> None:
    """A supported server exposes model and agent inventory channels."""
    inventory = build_inventory(
        _client(),
        observed_at=_now(),
        project_context="ctx",
        credentials_configured=True,
        policy=VersionPolicy(),
    )
    assert inventory.runtime_version == "2.2.6"
    assert [ref.model_id for ref in inventory.models] == ["opencode-go/model-a"]
    capability = _runtime_capability(inventory)
    assert capability.supported is CapabilityState.YES
    assert capability.authorized is CapabilityState.YES


def test_unknown_version_is_not_available() -> None:
    """Unknown versions keep available/supported UNKNOWN."""
    inventory = build_inventory(
        _client(version=None),
        observed_at=_now(),
        project_context=None,
        credentials_configured=True,
        policy=VersionPolicy(),
    )
    capability = _runtime_capability(inventory)
    assert capability.supported is CapabilityState.UNKNOWN
    assert capability.available is CapabilityState.UNKNOWN


def test_authentication_failure_sets_authorized_no() -> None:
    """A 401 is reachable but unauthorized and unavailable."""
    inventory = build_inventory(
        _client(error=OpenCodeServerError(ServerFailureCode.AUTHENTICATION_FAILED, "bad")),
        observed_at=_now(),
        project_context=None,
        credentials_configured=True,
        policy=VersionPolicy(),
    )
    capability = _runtime_capability(inventory)
    assert capability.connected is CapabilityState.YES
    assert capability.authorized is CapabilityState.NO
    assert capability.available is CapabilityState.NO
