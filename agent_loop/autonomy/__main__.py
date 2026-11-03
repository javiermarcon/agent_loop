"""Allow ``python -m agent_loop.autonomy`` execution."""

from __future__ import annotations

from agent_loop.autonomy.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
