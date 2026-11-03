"""Classify tool failures so the loop retries only code-correctable defects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.tools import ToolKind
from agent_loop.autonomy.tools.base import FailureKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_loop.autonomy.tools.command import CommandOutcome

_PYTEST_COLLECTION_ERROR_MARKERS = ("ERROR collecting", "error during collection")
_PYTEST_INTERRUPTED = 2
_PYTEST_INTERNAL = 3
_PYTEST_USAGE = 4
_PYTEST_NO_TESTS = 5
_PYLINT_USAGE_EXIT_CODE = 32
_IMPLEMENTATION_EXIT_CODE = 1
_EXIT_NONE = 0


def classify_outcome(kind: ToolKind, outcome: CommandOutcome) -> FailureKind:
    """Return the retry-relevant class of one command outcome."""
    if outcome.error is not None or outcome.exit_code is None:
        return FailureKind.INFRASTRUCTURE
    return classify_exit_code(kind, outcome.exit_code, _output(outcome))


def classify_exit_code(kind: ToolKind, exit_code: int, output: str = "") -> FailureKind:
    """Map a known tool exit code to an explicit failure class."""
    if exit_code == _EXIT_NONE:
        return FailureKind.NONE
    if exit_code < 0:
        return FailureKind.INFRASTRUCTURE
    classifier = _CLASSIFIERS.get(kind)
    if classifier is None:
        return FailureKind.INFRASTRUCTURE
    return classifier(exit_code, output)


def _classify_pytest(exit_code: int, output: str) -> FailureKind:
    if exit_code == _IMPLEMENTATION_EXIT_CODE:
        return FailureKind.IMPLEMENTATION
    if exit_code == _PYTEST_INTERRUPTED:
        return _classify_pytest_interruption(output)
    if exit_code == _PYTEST_INTERNAL:
        return FailureKind.INFRASTRUCTURE
    if exit_code == _PYTEST_USAGE:
        return FailureKind.USAGE
    if exit_code == _PYTEST_NO_TESTS:
        return FailureKind.NONE
    return FailureKind.INFRASTRUCTURE


def _classify_pytest_interruption(output: str) -> FailureKind:
    lowered = output.lower()
    if any(marker.lower() in lowered for marker in _PYTEST_COLLECTION_ERROR_MARKERS):
        return FailureKind.IMPLEMENTATION
    return FailureKind.INFRASTRUCTURE


def _classify_ruff(exit_code: int, output: str) -> FailureKind:
    del output
    return _implementation_or(exit_code, FailureKind.USAGE)


def _classify_mypy(exit_code: int, output: str) -> FailureKind:
    del output
    return _implementation_or(exit_code, FailureKind.CONFIGURATION)


def _classify_pylint(exit_code: int, output: str) -> FailureKind:
    del output
    if exit_code >= _PYLINT_USAGE_EXIT_CODE:
        return FailureKind.USAGE
    return FailureKind.IMPLEMENTATION


def _classify_bandit(exit_code: int, output: str) -> FailureKind:
    del output
    return _implementation_or(exit_code, FailureKind.USAGE)


def _classify_behave(exit_code: int, output: str) -> FailureKind:
    del output
    return _implementation_or(exit_code, FailureKind.CONFIGURATION)


def _classify_pre_commit(exit_code: int, output: str) -> FailureKind:
    del output
    return _implementation_or(exit_code, FailureKind.INFRASTRUCTURE)


def _implementation_or(exit_code: int, alternative: FailureKind) -> FailureKind:
    return FailureKind.IMPLEMENTATION if exit_code == _IMPLEMENTATION_EXIT_CODE else alternative


_CLASSIFIERS: dict[ToolKind, Callable[[int, str], FailureKind]] = {
    ToolKind.PYTEST: _classify_pytest,
    ToolKind.RUFF: _classify_ruff,
    ToolKind.MYPY: _classify_mypy,
    ToolKind.PYLINT: _classify_pylint,
    ToolKind.BANDIT: _classify_bandit,
    ToolKind.BEHAVE: _classify_behave,
    ToolKind.PRE_COMMIT: _classify_pre_commit,
}


def _output(outcome: CommandOutcome) -> str:
    return f"{outcome.stdout}\n{outcome.stderr}"
