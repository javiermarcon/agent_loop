"""Pure decisions for repairing one persisted resume candidate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResumeRepairCheckpointKind(StrEnum):
    """Auditable checkpoint selected by resume repair policy."""

    GATE_IDENTITY = "legacy_retry_gate_identity_normalized"
    RETRY_ACT = "retry_act_checkpoint_normalized"
    FAILED_RUN_LOADED = "failed_run_loaded"
    ACTIVE_UNIT = "active_unit_reconstructed"
    RESUME_POLICY = "resume_policy_reconstructed"


@dataclass(frozen=True)
class ResumeRepairCheckpoint:
    """Infrastructure-neutral checkpoint emitted for a repair decision."""

    kind: ResumeRepairCheckpointKind
    reason: str | None = None


@dataclass(frozen=True)
class ResumeCandidateRepairInputs[StateT]:
    """Reconstructed state and recovery evidence used by repair policy."""

    repaired_state: StateT
    reconstruction_reason: str | None
    act_migration_reason: str | None
    gate_identity_normalized: bool
    run_was_failed: bool


@dataclass(frozen=True)
class ResumeCandidateRepairDecision[StateT]:
    """Repaired state plus the exact durable actions it requires."""

    repaired_state: StateT
    requires_persistence: bool
    checkpoints: tuple[ResumeRepairCheckpoint, ...]


def decide_resume_candidate_repair[StateT](
    inputs: ResumeCandidateRepairInputs[StateT],
) -> ResumeCandidateRepairDecision[StateT]:
    """Select persistence and audit checkpoints without performing I/O."""
    checkpoints: list[ResumeRepairCheckpoint] = []
    if inputs.gate_identity_normalized:
        checkpoints.append(
            ResumeRepairCheckpoint(ResumeRepairCheckpointKind.GATE_IDENTITY),
        )
    if inputs.act_migration_reason is not None:
        checkpoints.append(
            ResumeRepairCheckpoint(
                ResumeRepairCheckpointKind.RETRY_ACT,
                reason=inputs.act_migration_reason,
            ),
        )
    if inputs.reconstruction_reason is not None:
        primary_kind = (
            ResumeRepairCheckpointKind.FAILED_RUN_LOADED
            if inputs.run_was_failed
            else ResumeRepairCheckpointKind.ACTIVE_UNIT
        )
        checkpoints.append(
            ResumeRepairCheckpoint(
                primary_kind,
                reason=inputs.reconstruction_reason,
            ),
        )
        if "resume_policy_reconstructed" in inputs.reconstruction_reason:
            checkpoints.append(
                ResumeRepairCheckpoint(
                    ResumeRepairCheckpointKind.RESUME_POLICY,
                ),
            )
        if "active_unit_reconstructed" in inputs.reconstruction_reason:
            checkpoints.append(
                ResumeRepairCheckpoint(
                    ResumeRepairCheckpointKind.ACTIVE_UNIT,
                    reason=inputs.reconstruction_reason,
                ),
            )
    return ResumeCandidateRepairDecision(
        repaired_state=inputs.repaired_state,
        requires_persistence=bool(checkpoints),
        checkpoints=tuple(checkpoints),
    )
