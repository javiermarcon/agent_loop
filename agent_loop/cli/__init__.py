"""Canonical command-line contracts for the agent-loop application."""

from agent_loop.cli.arguments import build_argument_parser
from agent_loop.cli.arguments import parse_args
from agent_loop.cli.option_provenance import MULTI_REQUIREMENT_ONLY_OPTIONS
from agent_loop.cli.option_provenance import CLIOptionProvenance
from agent_loop.cli.option_provenance import CLIOptionProvenancePort

__all__ = [
    "MULTI_REQUIREMENT_ONLY_OPTIONS",
    "CLIOptionProvenance",
    "CLIOptionProvenancePort",
    "build_argument_parser",
    "parse_args",
]
