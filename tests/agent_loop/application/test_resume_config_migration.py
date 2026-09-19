"""Tests for pure durable resume config migration."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from agent_loop.application import resume_config_migration
from agent_loop.application.resume_compatibility import ResumeCompatibilityDecision

CURRENT_COMPATIBILITY_VERSION = 2


def _decision(
    *,
    compatible: bool = True,
    requires_migration: bool = True,
    migration_path: tuple[str, ...] = ("v1_to_v2",),
) -> ResumeCompatibilityDecision:
    return ResumeCompatibilityDecision(
        compatible=compatible,
        requires_migration=requires_migration,
        from_version=1,
        to_version=CURRENT_COMPATIBILITY_VERSION,
        migration_path=migration_path,
        reason_codes=("STATE_MIGRATION_REQUIRED",),
        explanation="migration required",
    )


def _config() -> resume_config_migration.ResumeConfigMetadata:
    return resume_config_migration.ResumeConfigMetadata(
        orchestrator_root="/repo/target",
        external_orchestrator_mode=False,
        orchestrator_version="legacy",
        resume_compatibility_version=1,
        orchestrator_fingerprint=None,
        orchestrator_git_commit="old-commit",
        orchestrator_dirty=False,
        orchestrator_history=(
            {
                "version": "earlier",
                "resume_compatibility_version": 1,
                "fingerprint": "earlier-fingerprint",
                "root": "/repo/earlier",
            },
        ),
    )


def _inputs(
    *,
    decision: ResumeCompatibilityDecision | None = None,
) -> resume_config_migration.ResumeMigrationInputs:
    return resume_config_migration.ResumeMigrationInputs(
        decision=decision or _decision(),
        persisted_orchestrator_identity="/repo/target",
        current_orchestrator_identity="/repo/orchestrator",
        current_orchestrator_root="/repo/orchestrator",
        external_orchestrator_mode=True,
        orchestrator_version="current",
        resume_compatibility_version=CURRENT_COMPATIBILITY_VERSION,
        orchestrator_fingerprint="current-fingerprint",
        orchestrator_git_commit="current-commit",
        orchestrator_dirty=True,
    )


def test_supported_migration_produces_exact_metadata() -> None:
    """A registered migration updates provenance and preserves prior history."""
    migrated = resume_config_migration.migrate_resume_config(_config(), _inputs())

    assert migrated == resume_config_migration.ResumeConfigMetadata(
        orchestrator_root="/repo/orchestrator",
        external_orchestrator_mode=True,
        orchestrator_version="current",
        resume_compatibility_version=CURRENT_COMPATIBILITY_VERSION,
        orchestrator_fingerprint="current-fingerprint",
        orchestrator_git_commit="current-commit",
        orchestrator_dirty=True,
        orchestrator_history=(
            {
                "version": "earlier",
                "resume_compatibility_version": 1,
                "fingerprint": "earlier-fingerprint",
                "root": "/repo/earlier",
            },
            {
                "version": "legacy",
                "resume_compatibility_version": 1,
                "fingerprint": None,
                "root": "/repo/target",
            },
            {
                "version": "current",
                "resume_compatibility_version": CURRENT_COMPATIBILITY_VERSION,
                "fingerprint": "current-fingerprint",
                "root": "/repo/orchestrator",
                "migration": "v1_to_v2",
            },
        ),
    )


def test_migration_copies_resolved_git_inputs() -> None:
    """Commit and dirty state are copied from values supplied by infrastructure."""
    migrated = resume_config_migration.migrate_resume_config(
        _config(),
        replace(
            _inputs(),
            orchestrator_git_commit=None,
            orchestrator_dirty=None,
        ),
    )

    assert migrated.orchestrator_git_commit is None
    assert migrated.orchestrator_dirty is None


def test_current_metadata_is_returned_unchanged() -> None:
    """Equivalent normalized identity, version and fingerprint are idempotent."""
    config = replace(
        _config(),
        orchestrator_root="relative/orchestrator",
        orchestrator_version="current",
        resume_compatibility_version=CURRENT_COMPATIBILITY_VERSION,
        orchestrator_fingerprint="current-fingerprint",
    )
    inputs = replace(
        _inputs(decision=replace(_decision(), requires_migration=False, migration_path=())),
        persisted_orchestrator_identity="/repo/orchestrator",
    )

    migrated = resume_config_migration.migrate_resume_config(config, inputs)

    assert migrated is config
    assert migrated.orchestrator_root == "relative/orchestrator"
    assert migrated.orchestrator_git_commit == "old-commit"
    assert migrated.orchestrator_dirty is False


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (
            _decision(compatible=False, requires_migration=False, migration_path=()),
            "decisión de resume incompatible",
        ),
        (_decision(migration_path=("unknown",)), "Migración de resume desconocida"),
    ],
)
def test_migration_rejects_invalid_decisions(
    decision: ResumeCompatibilityDecision,
    message: str,
) -> None:
    """Existing validation errors remain stable in pure policy."""
    with pytest.raises(ValueError, match=message):
        resume_config_migration.migrate_resume_config(
            _config(),
            _inputs(decision=decision),
        )


def test_migration_is_deterministic_and_deduplicates_history() -> None:
    """Equal inputs yield equal values and repeated migration adds no history."""
    config = _config()
    inputs = _inputs()

    first = resume_config_migration.migrate_resume_config(config, inputs)
    repeated = resume_config_migration.migrate_resume_config(config, inputs)
    replayed = resume_config_migration.migrate_resume_config(
        replace(config, orchestrator_history=first.orchestrator_history),
        inputs,
    )
    second = resume_config_migration.migrate_resume_config(
        first,
        replace(
            inputs,
            persisted_orchestrator_identity=inputs.current_orchestrator_identity,
        ),
    )

    assert repeated == first
    assert replayed.orchestrator_history == first.orchestrator_history
    assert second.orchestrator_history == first.orchestrator_history


def test_migration_policy_has_no_infrastructure_imports() -> None:
    """The transformation cannot access Git, filesystem, environment or scripts."""
    source = Path(resume_config_migration.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)

    forbidden_roots = {"importlib", "os", "pathlib", "shutil", "subprocess", "scripts"}
    assert not {name.split(".", maxsplit=1)[0] for name in imported}.intersection(forbidden_roots)
