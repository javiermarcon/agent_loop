"""Tests for pure resume candidate repair decisions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from agent_loop.application import resume_candidate_repair as repair_policy


@dataclass(frozen=True)
class _State:
    value: str


def test_candidate_without_repair_requires_no_persistence() -> None:
    """An unchanged candidate produces neither mutation nor checkpoints."""
    state = _State("persisted")

    decision = repair_policy.decide_resume_candidate_repair(
        repair_policy.ResumeCandidateRepairInputs(
            repaired_state=state,
            reconstruction_reason=None,
            act_migration_reason=None,
            gate_identity_normalized=False,
            run_was_failed=False,
        ),
    )

    assert decision.repaired_state is state
    assert decision.requires_persistence is False
    assert decision.checkpoints == ()


def test_repair_decision_preserves_state_and_historical_checkpoints() -> None:
    """All repair signals produce the historical checkpoint sequence."""
    repaired = _State("repaired")

    decision = repair_policy.decide_resume_candidate_repair(
        repair_policy.ResumeCandidateRepairInputs(
            repaired_state=repaired,
            reconstruction_reason=("active_unit_reconstructed+resume_policy_reconstructed"),
            act_migration_reason="prompt-1:retry_act_status_normalized",
            gate_identity_normalized=True,
            run_was_failed=True,
        ),
    )

    assert decision.repaired_state is repaired
    assert decision.requires_persistence is True
    assert decision.checkpoints == (
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.GATE_IDENTITY,
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.RETRY_ACT,
            reason="prompt-1:retry_act_status_normalized",
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.FAILED_RUN_LOADED,
            reason="active_unit_reconstructed+resume_policy_reconstructed",
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.RESUME_POLICY,
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.ACTIVE_UNIT,
            reason="active_unit_reconstructed+resume_policy_reconstructed",
        ),
    )


def test_non_failed_reconstruction_keeps_active_unit_checkpoint() -> None:
    """Non-failed runs retain the legacy primary reconstruction event."""
    decision = repair_policy.decide_resume_candidate_repair(
        repair_policy.ResumeCandidateRepairInputs(
            repaired_state=_State("repaired"),
            reconstruction_reason="active_unit_reconstructed",
            act_migration_reason=None,
            gate_identity_normalized=False,
            run_was_failed=False,
        ),
    )

    assert decision.checkpoints == (
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.ACTIVE_UNIT,
            reason="active_unit_reconstructed",
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.ACTIVE_UNIT,
            reason="active_unit_reconstructed",
        ),
    )


def test_policy_only_reconstruction_omits_extra_active_unit_checkpoint() -> None:
    """Policy recovery does not invent an active-unit reconstruction action."""
    decision = repair_policy.decide_resume_candidate_repair(
        repair_policy.ResumeCandidateRepairInputs(
            repaired_state=_State("policy-repaired"),
            reconstruction_reason="resume_policy_reconstructed",
            act_migration_reason=None,
            gate_identity_normalized=False,
            run_was_failed=True,
        ),
    )

    assert decision.checkpoints == (
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.FAILED_RUN_LOADED,
            reason="resume_policy_reconstructed",
        ),
        repair_policy.ResumeRepairCheckpoint(
            repair_policy.ResumeRepairCheckpointKind.RESUME_POLICY,
        ),
    )


def test_repair_policy_has_no_infrastructure_or_dynamic_dependencies() -> None:
    """Pure recovery decisions remain independent from legacy infrastructure."""
    source = Path(repair_policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    from_imports = (node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    imported = {node.module or "" for node in from_imports}
    direct_imports = (node for node in ast.walk(tree) if isinstance(node, ast.Import))
    imported.update(alias.name for node in direct_imports for alias in node.names)

    assert not {name.split(".", maxsplit=1)[0] for name in imported}.intersection(
        {
            "git",
            "importlib",
            "os",
            "pathlib",
            "scripts",
            "subprocess",
        },
    )
    assert "MultiRequirementRunStore" not in source
    assert "ResumeRunLock" not in source
    assert "getattr(" not in source
