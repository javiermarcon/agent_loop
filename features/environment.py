"""Behave environment for the AgentLoop product-level E2E suite.

Every scenario gets an isolated temporary directory and a runtime guard for
heavy operations. A scenario that intentionally needs a real provider or
network operation must carry the ``@heavy`` tag, mirroring the pytest
``heavy_request`` marker.

The guard prints a structured report at the end of the run so the integral
quality runner can prove that it initialized, activated and observed events
instead of inferring safety from silence.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.agent_loop.quality.heavy_requests import HeavyRequestGuard  # noqa: E402
from tests.agent_loop.quality.heavy_requests import install_audit_hook  # noqa: E402
from tests.agent_loop.quality.heavy_requests import record_authorization  # noqa: E402
from tests.agent_loop.quality.heavy_requests import record_violation  # noqa: E402
from tests.agent_loop.quality.heavy_requests import render_report  # noqa: E402
from tests.agent_loop.quality.heavy_requests import reset_statistics  # noqa: E402

if TYPE_CHECKING:
    from behave.model import Scenario


def before_all(context: Any) -> None:
    """Install the process-wide guard and clear session evidence."""
    del context
    reset_statistics()
    install_audit_hook()


def before_scenario(context: Any, scenario: Scenario) -> None:
    """Create isolated state and start heavy-request enforcement."""
    context.root = Path(tempfile.mkdtemp(prefix="agent-loop-behave-"))
    context.heavy = "heavy" in scenario.tags
    context.heavy_expected = "expected-heavy-violation" in scenario.tags
    context.last_result = None
    context.last_payload = None
    context.exit_stack = contextlib.ExitStack()
    name = getattr(scenario, "name", "scenario")
    if context.heavy:
        record_authorization("behave @heavy", detail=name)
    context.guard = context.exit_stack.enter_context(
        HeavyRequestGuard(
            allow=context.heavy,
            reason=f"behave scenario {name}",
            expected=context.heavy_expected,
        ),
    )


def after_scenario(context: Any, scenario: Scenario) -> None:
    """Stop the guard, fail on undeclared heavy work and remove temporary state."""
    guard = context.guard
    context.exit_stack.close()
    root = context.root
    source = getattr(scenario, "name", "scenario")
    try:
        shutil.rmtree(root, ignore_errors=True)
    finally:
        if guard.violations:
            if not guard.expected:
                for violation in guard.violations:
                    record_violation(violation, source=source)
            rendered = "\n".join(violation.render() for violation in guard.violations)
            raise AssertionError(
                "the scenario performed heavy operations without @heavy:\n" + rendered,
            )


def after_all(context: Any) -> None:
    """Publish the guard evidence consumed by the integral runner.

    Behave's capture replaces ``sys.stdout`` even when the configuration asks
    for no capture, so the quality evidence is written to the real process
    stream that the runner already owns.
    """
    del context
    stream = getattr(sys, "__stdout__", None) or sys.stdout
    stream.write(render_report("behave") + "\n")
    stream.flush()
