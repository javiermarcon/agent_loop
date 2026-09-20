"""Tests for resume candidate preparation orchestration."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pytest

from agent_loop.application import resume_candidate_preparation

SELECTION_EXIT = 3
REPAIR_EXIT = 7
VALIDATION_EXIT = 4


@dataclass
class _PreparationFake:
    selected: str | None = "selected"
    selection_exit: int | None = None
    repaired: str | None = "repaired"
    repair_exit: int | None = None
    validation_exit: int | None = None
    fail_store: bool = False
    events: list[str] = field(default_factory=list)
    validated_candidate: str | None = None

    def select_candidate(
        self,
        request: str,
        catalog: str,
        target: str,
    ) -> tuple[str | None, int | None]:
        self.events.append(f"selection:{request}:{catalog}:{target}")
        return self.selected, self.selection_exit

    def open_store(self, candidate: str) -> str:
        self.events.append(f"store:{candidate}")
        if self.fail_store:
            message = "corrupt store"
            raise ValueError(message)
        return f"store-for:{candidate}"

    def repair_candidate(
        self,
        candidate: str,
        store: str,
    ) -> tuple[str | None, int | None]:
        self.events.append(f"repair:{candidate}:{store}")
        return self.repaired, self.repair_exit

    def validate_candidate(self, candidate: str, target: str) -> int | None:
        self.events.append(f"validation:{candidate}:{target}")
        self.validated_candidate = candidate
        return self.validation_exit

    def collaborators(
        self,
    ) -> resume_candidate_preparation.ResumePreparationCollaborators[
        str,
        str,
        str,
        str,
        str,
    ]:
        return resume_candidate_preparation.ResumePreparationCollaborators(
            select_candidate=self.select_candidate,
            open_store=self.open_store,
            repair_candidate=self.repair_candidate,
            validate_candidate=self.validate_candidate,
        )


def _prepare(
    fake: _PreparationFake,
) -> resume_candidate_preparation.ResumePreparationResult[str, str]:
    return resume_candidate_preparation.prepare_resume_candidate(
        "request",
        "catalog",
        "target",
        fake.collaborators(),
    )


def test_selection_failure_stops_before_store() -> None:
    """A selection exit prevents every later preparation stage."""
    fake = _PreparationFake(selected=None, selection_exit=SELECTION_EXIT)

    result = _prepare(fake)

    assert result == resume_candidate_preparation.ResumePreparationResult(
        candidate=None,
        store=None,
        exit_code=SELECTION_EXIT,
    )
    assert fake.events == ["selection:request:catalog:target"]


def test_repair_failure_stops_before_validation() -> None:
    """Store opens after selection and failed repair prevents validation."""
    fake = _PreparationFake(repaired=None, repair_exit=REPAIR_EXIT)

    result = _prepare(fake)

    assert result.exit_code == REPAIR_EXIT
    assert fake.events == [
        "selection:request:catalog:target",
        "store:selected",
        "repair:selected:store-for:selected",
    ]


def test_validation_receives_repaired_candidate_and_preserves_exit() -> None:
    """Validation runs after repair and its failure is returned unchanged."""
    fake = _PreparationFake(repaired="normalized", validation_exit=VALIDATION_EXIT)

    result = _prepare(fake)

    assert result.candidate is None
    assert result.store is None
    assert result.exit_code == VALIDATION_EXIT
    assert fake.validated_candidate == "normalized"
    assert fake.events[-2:] == [
        "repair:selected:store-for:selected",
        "validation:normalized:target",
    ]


def test_happy_path_returns_repaired_candidate_and_opened_store() -> None:
    """Successful preparation returns the repaired value and original store."""
    fake = _PreparationFake(repaired="normalized")

    result = _prepare(fake)

    assert result == resume_candidate_preparation.ResumePreparationResult(
        candidate="normalized",
        store="store-for:selected",
        exit_code=None,
    )
    assert fake.events == [
        "selection:request:catalog:target",
        "store:selected",
        "repair:selected:store-for:selected",
        "validation:normalized:target",
    ]


def test_store_failure_propagates_without_repair_or_validation() -> None:
    """Corrupt store behavior remains owned by the calling legacy boundary."""
    fake = _PreparationFake(fail_store=True)

    with pytest.raises(ValueError, match="corrupt store"):
        _prepare(fake)

    assert fake.events == [
        "selection:request:catalog:target",
        "store:selected",
    ]


def test_preparation_orchestration_has_no_legacy_or_dynamic_dependencies() -> None:
    """Application coordination imports no scripts, filesystem, or lock owner."""
    source = Path(resume_candidate_preparation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)

    assert not {name.split(".", maxsplit=1)[0] for name in imported}.intersection(
        {"importlib", "os", "pathlib", "scripts", "subprocess"},
    )
    assert "ResumeRunLock" not in source
    assert "getattr(" not in source
