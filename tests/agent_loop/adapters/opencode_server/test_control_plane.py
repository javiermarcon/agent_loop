"""S13 integration: the server inventory drives existing candidate resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

from agent_loop.adapters.opencode_server.models import ModelRef
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import CapabilityRequirement
from agent_loop.domain.compatible_candidates import resolve_compatible_candidates
from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile
from agent_loop.domain.task_assessment import AutonomyRequired
from agent_loop.domain.task_assessment import ImpactPresence
from agent_loop.domain.task_assessment import TaskAssessment
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.ports.agent_runtime import DiscoveryContext

if TYPE_CHECKING:
    from pathlib import Path

    from agent_loop.adapters.opencode_server.client import ServerClient


class _DiscoveryOnlyClient:
    base_url = "http://127.0.0.1:1"
    credentials_configured = True

    def info(self) -> ServerInfo:
        return ServerInfo(version="2.2.6", pid=1, urls=(self.base_url,))

    def models(self) -> tuple[ModelRef, ...]:
        return (ModelRef("opencode-go", "model-a"),)

    def agents(self) -> tuple[str, ...]:
        return ("build", "plan")


def _assessment() -> TaskAssessment:
    return TaskAssessment(
        complexity=TaskComplexity.LOCAL,
        risk=TaskRisk.LOW,
        uncertainty=TaskUncertainty.LOW,
        impacted_domains=(),
        estimated_files=None,
        architecture_impact=ImpactPresence.UNKNOWN,
        schema_impact=ImpactPresence.UNKNOWN,
        security_impact=ImpactPresence.UNKNOWN,
        authentication_impact=ImpactPresence.UNKNOWN,
        payments_impact=ImpactPresence.UNKNOWN,
        concurrency_impact=ImpactPresence.UNKNOWN,
        external_integrations=(),
        frontend_backend_boundary=ImpactPresence.UNKNOWN,
        required_capabilities=(
            CapabilityRequirement(
                capability_id=CapabilityId("runtime.opencode.server"),
                mandatory_axes=frozenset({CapabilityAxis.SUPPORTED, CapabilityAxis.AVAILABLE}),
            ),
        ),
        required_context=(),
        autonomy_required=AutonomyRequired.LOW,
        reasons=(),
    )


def test_server_inventory_is_consumed_by_existing_candidate_resolution(tmp_path: Path) -> None:
    """OpenCode Server is identifiable to S13 without parallel routing logic."""
    runtime = OpenCodeServerRuntime(
        client=cast("ServerClient", _DiscoveryOnlyClient()),
        options=RuntimeOptions(),
    )
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    profile = ModelProfile(profile_id="server-fast", provider_id="opencode-go", model_id="model-a")
    resolution = resolve_compatible_candidates(
        _assessment(),
        inventories=(inventory,),
        profiles=ModelProfileCatalog.from_profiles((profile,)),
        bindings=RuntimeModelProfileCatalog.from_bindings(
            (
                RuntimeModelProfile(
                    runtime_id="opencode-server",
                    profile=profile,
                    native_model_ref="opencode-go/model-a",
                ),
            ),
        ),
    )
    assert [candidate.runtime_id for candidate in resolution.candidates] == ["opencode-server"]
    assert any(reason.startswith("accepted=opencode-server/") for reason in resolution.reasons)


def test_cli_and_server_runtime_identities_are_not_interchangeable(tmp_path: Path) -> None:
    """A CLI-only binding is not accepted by a server inventory."""
    runtime = OpenCodeServerRuntime(client=cast("ServerClient", _DiscoveryOnlyClient()))
    inventory = runtime.discover(DiscoveryContext(working_directory=str(tmp_path)))
    profile = ModelProfile(profile_id="server-fast", provider_id="opencode-go", model_id="model-a")
    resolution = resolve_compatible_candidates(
        _assessment(),
        inventories=(inventory,),
        profiles=ModelProfileCatalog.from_profiles((profile,)),
        bindings=RuntimeModelProfileCatalog.from_bindings(
            (
                RuntimeModelProfile(
                    runtime_id="opencode",
                    profile=profile,
                    native_model_ref="opencode-go/model-a",
                ),
            ),
        ),
    )
    assert resolution.candidates == ()
    assert any("inventory_missing" in reason for reason in resolution.reasons)
