"""Real end-to-end WorkItem execution through OpenCode Server."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from agent_loop.adapters.opencode_server.lifecycle import ManagedServer
from agent_loop.adapters.opencode_server.lifecycle import ManagedServerConfig
from agent_loop.adapters.opencode_server.runtime import OpenCodeServerRuntime
from agent_loop.adapters.opencode_server.runtime import RuntimeOptions
from agent_loop.application.execution_orchestrator import ExecutionRunOptions
from agent_loop.application.execution_orchestrator import ExecutionRunStatus
from agent_loop.application.execution_orchestrator import RoutingInputs
from agent_loop.application.execution_orchestrator import execute_work_item_with_recovery
from agent_loop.application.execution_routing import RuntimeReadinessPolicy
from agent_loop.application.recovery_policy import RecoveryLimits
from agent_loop.domain.capabilities import CapabilityAxis
from agent_loop.domain.capabilities import CapabilityId
from agent_loop.domain.capabilities import RuntimeInventory
from agent_loop.domain.model_profile_catalog import ModelProfileCatalog
from agent_loop.domain.model_profile_catalog import RuntimeModelProfileCatalog
from agent_loop.domain.model_profiles import ModelProfile
from agent_loop.domain.model_profiles import RuntimeModelProfile
from agent_loop.domain.task_assessment import AcceptanceClarity
from agent_loop.domain.task_assessment import TaskAssessment
from agent_loop.domain.task_assessment import TaskAssessmentRequest
from agent_loop.domain.task_assessment import TaskComplexity
from agent_loop.domain.task_assessment import TaskRisk
from agent_loop.domain.task_assessment import TaskUncertainty
from agent_loop.domain.task_assessment import assess_task
from agent_loop.domain.work_item import WorkItem
from agent_loop.ports.agent_runtime import DiscoveryContext
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

if TYPE_CHECKING:
    from pathlib import Path

_BINARY = shutil.which("opencode")
_GIT = shutil.which("git") or "git"
_DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
_E2E_ENV = "AGENT_LOOP_E2E_OPENCODE_SERVER"
_MODEL_ENV = "AGENT_LOOP_OPENCODE_SERVER_MODEL"

pytestmark = [
    pytest.mark.skipif(_BINARY is None, reason="opencode binary is not installed"),
    pytest.mark.skipif(
        os.environ.get(_E2E_ENV) != "1",
        reason=f"set {_E2E_ENV}=1 to run the real WorkItem end-to-end test",
    ),
]


def _model() -> str:
    return os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed git argv in a temporary fixture
        [_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture_repo(root: Path) -> Path:
    repo = root / "target"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "e2e@example.invalid")
    _git(repo, "config", "user.name", "Agent Loop E2E")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def test_work_item_reaches_opencode_server_and_changes_fixture(tmp_path: Path) -> None:
    """A real WorkItem is routed, executed by OpenCode, and verified by the fixture suite."""
    repo = _fixture_repo(tmp_path)
    managed = ManagedServer(
        ManagedServerConfig(
            binary=_BINARY or "opencode",
            working_directory=str(tmp_path),
            startup_timeout_seconds=60,
            readiness_timeout_seconds=30,
        ),
    )
    runtime = OpenCodeServerRuntime(
        managed=managed,
        options=RuntimeOptions(execution_timeout_seconds=300),
    )
    try:
        runtime.ensure_started()
        inventory = runtime.discover(DiscoveryContext(working_directory=str(repo)))
        work_item = WorkItem(
            work_item_id="wi-e2e-multiply",
            objective=(
                "In this repository, add a function multiply(a, b) to calc.py. "
                "Keep the existing add function. Add a pytest test for multiply in test_calc.py. "
                "Do not modify any other files."
            ),
        )
        outcome = execute_work_item_with_recovery(
            work_item,
            _assessment(),
            options=ExecutionRunOptions(
                working_directory=str(repo),
                mutation_policy=MutationPolicy(allow_workspace_mutation=True),
                permission_policy=PermissionPolicy(allow_network=False),
                limits=RecoveryLimits(max_total_attempts=1),
            ),
            routing=_routing(inventory),
            runtimes=(runtime,),
        )
        assert outcome.status is ExecutionRunStatus.SUCCEEDED, outcome.final_reason
        assert len(outcome.attempts) == 1
        assert outcome.attempts[0].routing.selected is not None
        assert outcome.attempts[0].routing.selected.runtime_id == "opencode-server"
        assert outcome.final_result is not None
        assert outcome.final_result.session_ref is not None
        assert outcome.final_result.session_ref.startswith("ses_")
        assert outcome.final_result.runtime_identity.runtime_id == "opencode-server"
        assert any("calc.py" in ref for ref in outcome.final_result.evidence_refs)
        assert "def multiply" in (repo / "calc.py").read_text(encoding="utf-8")
        tests = subprocess.run(  # noqa: S603 - fixed pytest argv in a temporary fixture
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        assert tests.returncode == 0, f"pytest failed:\n{tests.stdout}\n{tests.stderr}"
    finally:
        pid = managed.pid
        runtime.close()
        assert managed.running is False
        assert not _alive(pid)


def _assessment() -> TaskAssessment:
    return assess_task(
        TaskAssessmentRequest(
            baseline_complexity=TaskComplexity.MECHANICAL,
            baseline_risk=TaskRisk.LOW,
            baseline_uncertainty=TaskUncertainty.LOW,
            acceptance_clarity=AcceptanceClarity.CLEAR,
        ),
    )


def _routing(inventory: RuntimeInventory) -> RoutingInputs:
    provider_id, _, model_id = _model().partition("/")
    profile = ModelProfile(profile_id="opencode-server-e2e", provider_id=provider_id, model_id=model_id)
    return RoutingInputs(
        inventories=(inventory,),
        profiles=ModelProfileCatalog.from_profiles((profile,)),
        bindings=RuntimeModelProfileCatalog.from_bindings(
            (
                RuntimeModelProfile(
                    runtime_id="opencode-server",
                    profile=profile,
                    native_model_ref=_model(),
                ),
            ),
        ),
        readiness=(
            RuntimeReadinessPolicy(
                runtime_id="opencode-server",
                capability_id=CapabilityId("runtime.opencode.server"),
                mandatory_axes=frozenset(
                    {
                        CapabilityAxis.SUPPORTED,
                        CapabilityAxis.AVAILABLE,
                        CapabilityAxis.HEALTHY,
                        CapabilityAxis.AUTHORIZED,
                    },
                ),
            ),
        ),
        strategy=ExecutionStrategy.BUILD,
    )


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
