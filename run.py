"""Executable entrypoint that works without installing the package."""

from __future__ import annotations

from agent_loop.autonomy.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
