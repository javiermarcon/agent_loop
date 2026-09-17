"""Exclude generated Stage2 requirements from Phase 1 auto-execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

STAGE2_SUBPATH = "stage2_requirements"
ARTIFACT_LOG_PREFIX = ".agent-loop-logs/"
GENERATED_STAGE2_EXCLUSION = "generated_stage2_not_auto_executable"
MIN_STAGE2_EXECUTABLE_UNITS = 2


def is_generated_stage2_source(source_file: str) -> bool:
    """Return whether a unit source path points to generated Stage2 artifacts."""
    normalized = source_file.replace("\\", "/").removeprefix("./")
    return STAGE2_SUBPATH in normalized and ARTIFACT_LOG_PREFIX in normalized


def stage2_exclusion_reason(source_file: str) -> str | None:
    """Return the stable exclusion reason for generated Stage2 units."""
    if is_generated_stage2_source(source_file):
        return GENERATED_STAGE2_EXCLUSION
    return None


def is_stage2_folder_malformed(folder: Path) -> bool:
    """Return whether a generated Stage2 folder lacks durable executable units."""
    if not folder.is_dir():
        return True
    markdown_units = sorted(path for path in folder.glob("*.md") if path.is_file())
    return len(markdown_units) < MIN_STAGE2_EXECUTABLE_UNITS


def discover_stage2_folder_units(folder: Path) -> tuple[str, ...]:
    """Return markdown unit filenames under an explicit Stage2 folder."""
    if not folder.is_dir():
        return ()
    return tuple(
        path.name for path in sorted(folder.glob("*.md"), key=lambda item: item.name.casefold()) if path.is_file()
    )
