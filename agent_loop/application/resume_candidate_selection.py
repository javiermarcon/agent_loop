"""Pure policy for selecting from materialized resume candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResumeCandidateSelectionOutcome(StrEnum):
    """Describe selection without infrastructure exit codes."""

    SELECTED = "selected"
    NOT_FOUND = "not_found"
    NOT_RESUMABLE = "not_resumable"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResumeCandidateSelectionRequest:
    """Identify an explicit run or request the first ordered candidate."""

    explicit_identifier: str | None = None


@dataclass(frozen=True)
class ResumeCandidateValue:
    """Minimal identity for one candidate materialized by infrastructure."""

    token: int
    identifiers: tuple[str, ...]


@dataclass(frozen=True)
class ResumeCandidateSelection:
    """Return a structured outcome and selected candidate token."""

    outcome: ResumeCandidateSelectionOutcome
    candidate_token: int | None = None


def select_resume_candidate(
    request: ResumeCandidateSelectionRequest,
    candidates: tuple[ResumeCandidateValue, ...],
) -> ResumeCandidateSelection:
    """Select an explicit identity or the first infrastructure-ordered value."""
    if request.explicit_identifier is not None:
        matches = tuple(candidate for candidate in candidates if request.explicit_identifier in candidate.identifiers)
        if not matches:
            return ResumeCandidateSelection(
                outcome=ResumeCandidateSelectionOutcome.NOT_FOUND,
            )
        if len(matches) > 1:
            return ResumeCandidateSelection(
                outcome=ResumeCandidateSelectionOutcome.AMBIGUOUS,
            )
        selected = matches[0]
    else:
        if not candidates:
            return ResumeCandidateSelection(
                outcome=ResumeCandidateSelectionOutcome.NOT_RESUMABLE,
            )
        selected = candidates[0]
    return ResumeCandidateSelection(
        outcome=ResumeCandidateSelectionOutcome.SELECTED,
        candidate_token=selected.token,
    )
