"""Characterization and parity tests for the agent-loop CLI contract."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

import pytest

from agent_loop.cli.arguments import build_argument_parser
from agent_loop.cli.arguments import parse_args as canonical_parse_args
from agent_loop.cli.defaults import DEFAULT_MAX_ITERATIONS
from agent_loop.cli.option_provenance import CLIOptionProvenance
from scripts import agent_loop as legacy_agent_loop
from scripts import cli_option_provenance as legacy_provenance
from scripts.agent_loop import parse_args as legacy_parse_args

if TYPE_CHECKING:
    import argparse

EXPECTED_MAX_ITERATIONS = 30


class ArgumentParser(Protocol):
    """Callable contract shared by canonical and compatibility parsers."""

    def __call__(self, argv: list[str]) -> argparse.Namespace:
        """Parse one explicit argument vector."""


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--requirement", "inline"], {"requirement": "inline"}),
        (["--requirement-file", "one.md"], {"requirement_file": ["one.md"]}),
        (
            ["--requirement-folder", "requirements"],
            {"requirement_folder": ["requirements"], "multi_requirement": True},
        ),
        (
            ["--multi-requirement", "--multi-requirement-max-units", "3"],
            {"multi_requirement": True, "multi_requirement_max_units": 3},
        ),
        (["--resume-run", "run-7"], {"resume_multi_requirement_run": "run-7"}),
        (["--resume-last"], {"resume_last": True}),
        (
            ["--enforce-scope", "--scope-path", "agent_loop", "--scope-path", "tests"],
            {"enforce_scope": True, "scope_path": ["agent_loop", "tests"]},
        ),
        (
            ["--max-iterations", "8", "--max-modified-files", "5"],
            {"max_iterations": 8, "max_modified_files": 5},
        ),
        (
            ["--agent-timeout-seconds", "10", "--check-timeout-seconds", "20"],
            {"agent_timeout_seconds": 10, "check_timeout_seconds": 20},
        ),
        (["--commit_msg", "feat: explicit"], {"commit_msg": "feat: explicit"}),
        (["--no-gate-governor"], {"gate_governor": False}),
        (
            ["--unit-extra-instruction", "unit-1", "keep scope"],
            {"unit_extra_instruction": [("unit-1", "keep scope")]},
        ),
    ],
)
def test_canonical_parser_matches_legacy_contract(
    argv: list[str],
    expected: dict[str, object],
) -> None:
    """Canonical parsing preserves representative legacy values exactly."""
    canonical = canonical_parse_args(argv)
    legacy = legacy_parse_args(argv)

    assert vars(canonical) == vars(legacy)
    for name, value in expected.items():
        assert vars(canonical)[name] == value


def test_defaults_match_legacy_contract() -> None:
    """Canonical defaults remain identical to the current CLI defaults."""
    canonical = canonical_parse_args([])
    legacy = legacy_parse_args([])

    assert vars(canonical) == vars(legacy)
    assert canonical.max_iterations == DEFAULT_MAX_ITERATIONS == EXPECTED_MAX_ITERATIONS
    assert canonical.gate_governor is True
    assert canonical.commit_msg is None


def test_provenance_supports_equals_and_stops_at_terminator() -> None:
    """Long-option provenance handles equals syntax and the argv terminator."""
    provenance = CLIOptionProvenance.from_argv(
        ["--multi-requirement-max-units=2", "--", "--multi-requirement-unit"],
    )

    assert provenance.option_names == frozenset({"--multi-requirement-max-units"})


def test_explicit_parsing_does_not_modify_process_argv() -> None:
    """Canonical parsing consumes only its argument and leaves process state intact."""
    process_argv = tuple(sys.argv)

    parsed = canonical_parse_args(["--requirement", "explicit"])

    assert parsed.requirement == "explicit"
    assert tuple(sys.argv) == process_argv


def test_legacy_cli_symbols_delegate_to_canonical_owners() -> None:
    """Legacy imports preserve compatibility without duplicate implementations."""
    assert vars(legacy_agent_loop)["_build_argument_parser"] is build_argument_parser
    assert legacy_provenance.CLIOptionProvenance is CLIOptionProvenance


def test_canonical_cli_never_imports_legacy_scripts() -> None:
    """CLI ownership points from legacy scripts to the canonical package only."""
    violations: list[str] = []
    for path in sorted(Path("agent_loop/cli").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(
                    alias.name for alias in node.names if alias.name == "scripts" or alias.name.startswith("scripts.")
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "scripts" or node.module.startswith("scripts."):
                    violations.append(node.module)

    assert violations == []


def _parse_failure(
    parser: ArgumentParser,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int | str | None, str]:
    """Capture one argparse failure code and stderr text."""
    with pytest.raises(SystemExit) as exc_info:
        parser(argv)
    return exc_info.value.code, capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--does-not-exist"],
        ["--multi-requirement-manual-policy", "invalid"],
    ],
)
def test_argparse_failures_match_legacy(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown options and invalid choices retain argparse behavior."""
    canonical = _parse_failure(canonical_parse_args, argv, capsys)
    legacy = _parse_failure(legacy_parse_args, argv, capsys)

    assert canonical == legacy


def test_help_matches_legacy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Help exits cleanly and renders the same documented contract."""
    with pytest.raises(SystemExit) as canonical_exit:
        canonical_parse_args(["--help"])
    canonical_output = capsys.readouterr().out

    with pytest.raises(SystemExit) as legacy_exit:
        legacy_parse_args(["--help"])
    legacy_output = capsys.readouterr().out

    assert canonical_exit.value.code == legacy_exit.value.code == 0
    assert canonical_output == legacy_output
