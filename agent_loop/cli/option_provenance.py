"""Preserve explicit CLI option provenance for requirement-mode policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable

MULTI_REQUIREMENT_ONLY_OPTIONS = (
    "--multi-requirement-unit",
    "--multi-requirement-from-unit",
    "--multi-requirement-state",
    "--multi-requirement-max-units",
    "--multi-requirement-audience",
    "--multi-requirement-manual-policy",
    "--multi-requirement-validation-policy",
    "--multi-requirement-plan-overrides",
    "--multi-requirement-auto-continue",
    "--multi-requirement-max-total-modified-files",
    "--allow-cross-worktree-units",
)


class CLIOptionProvenancePort(Protocol):
    """Expose only the explicit-option queries required by CLI policies."""

    def was_explicitly_provided(self, option: str) -> bool:
        """Return whether the exact long option occurred in the original argv."""

    def explicit_options(self, options: Iterable[str]) -> tuple[str, ...]:
        """Return explicit options in the registry order supplied by the caller."""


@dataclass(frozen=True, slots=True)
class CLIOptionProvenance:
    """Record explicitly supplied long options independently of parsed values."""

    option_names: frozenset[str]

    @classmethod
    def from_argv(cls, argv: Iterable[str]) -> CLIOptionProvenance:
        """Capture long options, including ``--option=value`` spelling."""
        option_names: set[str] = set()
        for argument in argv:
            if argument == "--":
                break
            if argument.startswith("--"):
                option_names.add(argument.partition("=")[0])
        return cls(frozenset(option_names))

    def was_explicitly_provided(self, option: str) -> bool:
        """Return whether the exact long option occurred in the original argv."""
        return option in self.option_names

    def explicit_options(self, options: Iterable[str]) -> tuple[str, ...]:
        """Return explicit options in the registry order supplied by the caller."""
        return tuple(option for option in options if option in self.option_names)


EMPTY_CLI_OPTION_PROVENANCE = CLIOptionProvenance(frozenset())


def normalize_parsed_args(parsed: argparse.Namespace) -> argparse.Namespace:
    """Apply requirement-mode normalization after argparse validation."""
    if parsed.requirement_folder:
        parsed.multi_requirement = True
    parsed.unit_extra_instruction = [(str(unit_id), str(text)) for unit_id, text in parsed.unit_extra_instruction]
    return parsed


def attach_cli_option_provenance(
    parsed: argparse.Namespace,
    argv: Iterable[str],
) -> argparse.Namespace:
    """Attach immutable raw-option evidence to a parsed namespace."""
    parsed.cli_option_provenance = CLIOptionProvenance.from_argv(argv)
    return normalize_parsed_args(parsed)


def explicit_multi_only_options(args: argparse.Namespace) -> tuple[str, ...]:
    """Return multi-only flags explicitly supplied for one invocation."""
    provenance: CLIOptionProvenancePort = getattr(
        args,
        "cli_option_provenance",
        EMPTY_CLI_OPTION_PROVENANCE,
    )
    return provenance.explicit_options(MULTI_REQUIREMENT_ONLY_OPTIONS)
