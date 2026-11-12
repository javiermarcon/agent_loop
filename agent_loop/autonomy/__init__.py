"""Autonomous, target-repository-aware agent-loop runtime.

This package owns discovery, tool execution and the standalone CLI for
operating AgentLoop against an external repository. It must not import
``scripts``, ``apps`` or Django.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
