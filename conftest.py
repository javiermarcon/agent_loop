"""Keep the repository package importable from its own test run.

A parent directory containing another ``agent_loop`` package can shadow the
one in this repository, so this conftest removes those entries first.

The cleanup is deferred to ``pytest_configure`` and only runs when this
directory is the pytest root. Otherwise collecting tests from a parent
repository would mutate the parent interpreter path and break unrelated test
modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent


def pytest_configure(config: object) -> None:
    """Isolate this repository's package only for root-scoped runs."""
    rootpath = getattr(config, "rootpath", None)
    addinivalue_line = getattr(config, "addinivalue_line", None)
    if callable(addinivalue_line):
        addinivalue_line(
            "markers",
            "heavy_request: allow an intentional external agent/network/package-manager operation",
        )
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


@pytest.fixture(scope="session", autouse=True)
def _heavy_request_session() -> None:
    """Install the audit hook once for the complete session."""
    from tests.agent_loop.quality.heavy_requests import HeavyRequestGuardError  # noqa: PLC0415
    from tests.agent_loop.quality.heavy_requests import install_audit_hook  # noqa: PLC0415
    from tests.agent_loop.quality.heavy_requests import reset_statistics  # noqa: PLC0415

    reset_statistics()
    try:
        install_audit_hook()
    except HeavyRequestGuardError as error:
        sys.stdout.write(f"HEAVY REQUEST ERROR: {error}\n")
        sys.stdout.flush()
        pytest.fail(f"heavy-request guard could not initialize: {error}", pytrace=False)
    # The terminal summary hook prints the final evidence outside pytest capture.


def pytest_terminal_summary(terminalreporter: object) -> None:
    """Write the guard evidence with the terminal reporter, outside capture."""
    from tests.agent_loop.quality.heavy_requests import render_report  # noqa: PLC0415

    write_line = getattr(terminalreporter, "write_line", None)
    if not callable(write_line):
        return
    for line in render_report("pytest").splitlines():
        write_line(line)


@pytest.fixture(autouse=True)
def _heavy_request_guard(request: pytest.FixtureRequest) -> object:
    """Fail tests that perform undeclared heavy operations.

    ``@pytest.mark.heavy_request`` is the only in-test opt-in. It is used by
    environment-gated OpenCode/AgentLoop integration tests and by the guard's
    own violation tests.
    """
    from tests.agent_loop.quality.heavy_requests import HeavyRequestGuard  # noqa: PLC0415
    from tests.agent_loop.quality.heavy_requests import record_authorization  # noqa: PLC0415
    from tests.agent_loop.quality.heavy_requests import record_violation  # noqa: PLC0415

    marker = request.node.get_closest_marker("heavy_request")
    if marker is not None:
        record_authorization("pytest.mark.heavy_request", detail=request.node.nodeid)
        with HeavyRequestGuard(
            allow=True,
            reason=f"pytest.mark.heavy_request {request.node.nodeid}",
        ):
            yield None
        return
    with HeavyRequestGuard(reason=f"pytest test {request.node.nodeid}") as guard:
        yield None
    if guard.violations:
        for violation in guard.violations:
            record_violation(violation, source=request.node.nodeid)
        rendered = "\n".join(violation.render() for violation in guard.violations)
        pytest.fail(
            "the test performed heavy operations without @pytest.mark.heavy_request:\n" + rendered,
            pytrace=False,
        )
