"""Ensure the standalone package root is importable from its own checks.

The folder may live inside a larger checkout while it is being prepared. In
that case a parent directory containing another ``agent_loop`` package can
shadow the vendored one, so this conftest removes those entries first.

The cleanup is deferred to ``pytest_configure`` and only runs when the
standalone directory is the pytest root. Otherwise collecting vendored
fixtures from a parent repository would mutate the parent interpreter path and
break unrelated test modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def pytest_configure(config: object) -> None:
    """Isolate the vendored package only for standalone-rooted runs."""
    rootpath = getattr(config, "rootpath", None)
    if rootpath is None or Path(rootpath).resolve() != _ROOT:
        return
    for entry in list(sys.path):
        try:
            candidate = Path(entry or ".").resolve()
        except OSError:
            continue
        if candidate != _ROOT and (candidate / "agent_loop").exists():
            sys.path.remove(entry)
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    for name in list(sys.modules):
        if name == "agent_loop" or name.startswith("agent_loop."):
            module = sys.modules[name]
            file_path = getattr(module, "__file__", "") or ""
            if not file_path.startswith(str(_ROOT)):
                del sys.modules[name]
