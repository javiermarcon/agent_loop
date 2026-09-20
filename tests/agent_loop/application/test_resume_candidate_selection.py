"""Tests for pure resume candidate selection policy."""

from __future__ import annotations

import ast
from pathlib import Path

from agent_loop.application import resume_candidate_selection

FIRST_TOKEN = 7


def _candidate(
    token: int,
    run_id: str,
) -> resume_candidate_selection.ResumeCandidateValue:
    return resume_candidate_selection.ResumeCandidateValue(
        token=token,
        identifiers=(run_id,),
    )


def test_explicit_candidate_is_selected_by_exact_identity() -> None:
    """Explicit selection chooses one exact materialized identity."""
    selection = resume_candidate_selection.select_resume_candidate(
        resume_candidate_selection.ResumeCandidateSelectionRequest(
            explicit_identifier="run-b",
        ),
        (_candidate(0, "run-a"), _candidate(1, "run-b")),
    )

    assert selection.outcome is resume_candidate_selection.ResumeCandidateSelectionOutcome.SELECTED
    assert selection.candidate_token == 1


def test_explicit_missing_and_ambiguous_results_are_distinct() -> None:
    """Missing and duplicate materialized identities remain deterministic."""
    missing = resume_candidate_selection.select_resume_candidate(
        resume_candidate_selection.ResumeCandidateSelectionRequest(
            explicit_identifier="missing",
        ),
        (_candidate(0, "run-a"),),
    )
    ambiguous = resume_candidate_selection.select_resume_candidate(
        resume_candidate_selection.ResumeCandidateSelectionRequest(
            explicit_identifier="duplicate",
        ),
        (_candidate(0, "duplicate"), _candidate(1, "duplicate")),
    )

    assert missing.outcome is resume_candidate_selection.ResumeCandidateSelectionOutcome.NOT_FOUND
    assert missing.candidate_token is None
    assert ambiguous.outcome is resume_candidate_selection.ResumeCandidateSelectionOutcome.AMBIGUOUS
    assert ambiguous.candidate_token is None


def test_implicit_selection_preserves_infrastructure_order() -> None:
    """Resume-last selects the first candidate from the ordered projection."""
    selection = resume_candidate_selection.select_resume_candidate(
        resume_candidate_selection.ResumeCandidateSelectionRequest(),
        (_candidate(FIRST_TOKEN, "first"), _candidate(3, "second")),
    )

    assert selection.outcome is resume_candidate_selection.ResumeCandidateSelectionOutcome.SELECTED
    assert selection.candidate_token == FIRST_TOKEN


def test_empty_implicit_projection_is_not_resumable() -> None:
    """An empty eligible projection retains the no-resumable result."""
    selection = resume_candidate_selection.select_resume_candidate(
        resume_candidate_selection.ResumeCandidateSelectionRequest(),
        (),
    )

    assert selection.outcome is resume_candidate_selection.ResumeCandidateSelectionOutcome.NOT_RESUMABLE
    assert selection.candidate_token is None


def test_selection_is_deterministic_and_infrastructure_free() -> None:
    """Equal values produce equal decisions without infrastructure dependencies."""
    request = resume_candidate_selection.ResumeCandidateSelectionRequest()
    candidates = (_candidate(0, "run-a"), _candidate(1, "run-b"))

    assert resume_candidate_selection.select_resume_candidate(
        request,
        candidates,
    ) == resume_candidate_selection.select_resume_candidate(request, candidates)

    source = Path(resume_candidate_selection.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    forbidden_roots = {
        "importlib",
        "os",
        "pathlib",
        "shutil",
        "subprocess",
        "scripts",
    }

    assert not {name.split(".", maxsplit=1)[0] for name in imported}.intersection(forbidden_roots)
    assert "MultiRequirementRunCatalog" not in source
    assert "ResumeRunLock" not in source
    assert "input(" not in source
    assert "print(" not in source
