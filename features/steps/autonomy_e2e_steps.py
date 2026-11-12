"""Step definitions for AgentLoop product-level end-to-end scenarios."""

# behave decorators mutate the registry; Pylint misreads them as non-callable.
# pylint: disable=not-callable

from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from typing import TYPE_CHECKING

from behave import given
from behave import then
from behave import when

from agent_loop.application.execution_request_builder import ExecutionRequestOptions
from agent_loop.application.planned_execution_requests import build_execution_requests_for_ready_work_items
from agent_loop.autonomy.cli import main
from agent_loop.autonomy.security import git_environment
from agent_loop.domain.work_item import WorkItem
from agent_loop.domain.work_item import WorkItemStatus
from agent_loop.domain.work_item_plan import InvalidWorkItemDagError
from agent_loop.domain.work_item_plan import plan_work_items
from agent_loop.ports.agent_runtime import ExecutionStrategy
from agent_loop.ports.agent_runtime import MutationPolicy
from agent_loop.ports.agent_runtime import PermissionPolicy

if TYPE_CHECKING:
    from pathlib import Path

    import behave

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_UNAVAILABLE = 3
_EXIT_VALIDATION_FAILED = 4
_WORK_ITEMS = {
    "design": ("design the module", WorkItemStatus.OPEN, ()),
    "implement": ("implement the module", WorkItemStatus.OPEN, ("design",)),
    "docs": ("document the module", WorkItemStatus.BLOCKED, ("implement",)),
    "audit": ("audit the module", WorkItemStatus.DONE, ()),
}


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(  # noqa: S603 - fixed argv in a temporary fixture
        ("git", *args),  # noqa: S607
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=git_environment(),
    )
    if completed.returncode != 0:
        msg = f"git {' '.join(args)} failed: {completed.stderr}"
        raise AssertionError(msg)


def _fake_pytest(root: Path, state: Path) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    script = _write(
        root / ".venv" / "bin" / "pytest",
        "#!/bin/sh\n"
        f'state="{state.as_posix()}"\n'
        'mkdir -p "$state"\n'
        'if [ -f "$state/fail-once" ]; then\n'
        '  rm -f "$state/fail-once"\n'
        '  echo "1 failed, 0 passed"\n'
        "  exit 1\n"
        "fi\n"
        'if [ -f "$state/exit-code" ]; then\n'
        '  exit "$(cat "$state/exit-code")"\n'
        "fi\n"
        'echo "1 passed, 0 failed"\n'
        "exit 0\n",
    )
    script.chmod(0o755)
    return script


def _fake_agent(root: Path) -> Path:
    script = _write(
        root / "fake_agent.sh",
        "#!/bin/sh\n"
        "count=0\n"
        'if [ -f .agent-count ]; then count="$(cat .agent-count)"; fi\n'
        "count=$((count + 1))\n"
        'printf "%s" "$count" > .agent-count\n'
        'printf "agent run %s\\n" "$count" >> agent-runs.log\n'
        "if [ -f .agent-fail ]; then exit 1; fi\n"
        'printf "def add(a, b):\\n    return a + b\\n" > agent_output.py\n'
        "exit 0\n",
    )
    script.chmod(0o755)
    return script


def _target(context: behave.Context) -> Path:
    existing = getattr(context, "target", None)
    if existing is not None:
        return existing
    root = context.root / "target"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "e2e@example.invalid")
    _git(root, "config", "user.name", "AgentLoop E2E")
    _write(
        root / "manage.py",
        'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n',
    )
    _write(root / "config" / "settings.py", 'INSTALLED_APPS = ["apps.sample"]\n')
    _write(root / "requirements.txt", "Django==5.0.1\n")
    _write(
        root / "pyproject.toml",
        "[project]\nname = 'target'\nrequires-python = '>=3.12'\n"
        "[tool.ruff]\nline-length = 100\n"
        "[tool.mypy]\nstrict = true\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
    )
    _write(root / "AGENTS.md", "Target repository instruction: keep changes small.\n")
    _write(root / "apps" / "sample" / "__init__.py", "")
    _write(root / "tests" / "test_sample.py", "def test_sample():\n    assert True\n")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write(root / ".venv" / "pyvenv.cfg", f"home = {sys.base_prefix}\n")
    (venv_bin / "python").symlink_to(sys.executable)
    _fake_pytest(root, context.root / "fake-state")
    _fake_agent(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    context.target = root
    return root


def _run_cli(context: behave.Context, args: list[str]) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 0
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    context.exit_code = code
    context.stdout = stdout.getvalue()
    context.stderr = stderr.getvalue()
    context.payload = None
    if context.stdout.strip().startswith("{"):
        context.payload = json.loads(context.stdout)


def _base_args(context: behave.Context) -> list[str]:
    root = _target(context)
    return [
        "--repo",
        str(root),
        "--agent-command",
        shlex.join(("sh", str(root / "fake_agent.sh"))),
        "--max-iterations",
        "2",
        "--tool",
        "pytest",
        "--json",
    ]


@given("a target Django repository with a fake validation environment")
def step_target(context: behave.Context) -> None:
    _target(context)


@given("the target validation fails once before passing")
def step_fail_once(context: behave.Context) -> None:
    _write(context.root / "fake-state" / "fail-once", "")


@given("the target validation exits with code {code:d}")
def step_validation_exit(context: behave.Context, code: int) -> None:
    _write(context.root / "fake-state" / "exit-code", str(code))


@given("the target agent fails")
def step_agent_fails(context: behave.Context) -> None:
    _write(_target(context) / ".agent-fail", "")


@given('a requirement file containing "{text}"')
def step_requirement_file(context: behave.Context, text: str) -> None:
    context.requirement_file = _write(context.root / "requirement.md", text)


@when('I run AgentLoop for the requirement "{requirement}"')
def step_run_agent(context: behave.Context, requirement: str) -> None:
    _run_cli(context, [*_base_args(context), "--requirement", requirement])


@when('I run discovery only for the requirement "{requirement}"')
def step_discover(context: behave.Context, requirement: str) -> None:
    _run_cli(context, [*_base_args(context), "--requirement", requirement, "--discover-only"])


@when('I run a dry run for the requirement "{requirement}"')
def step_dry_run(context: behave.Context, requirement: str) -> None:
    _run_cli(context, [*_base_args(context), "--requirement", requirement, "--dry-run"])


@when("I run discovery only from the requirement file")
def step_discover_file(context: behave.Context) -> None:
    root = _target(context)
    _run_cli(
        context,
        [
            "--repo",
            str(root),
            "--requirement-file",
            str(context.requirement_file),
            "--discover-only",
            "--json",
        ],
    )


@when('I run validate-only for tool "{tool}"')
def step_validate_only(context: behave.Context, tool: str) -> None:
    root = _target(context)
    _run_cli(
        context,
        ["--repo", str(root), "--requirement", "validate", "--validate-only", "--tool", tool, "--json"],
    )


@when("I run AgentLoop with a missing agent command")
def step_missing_agent(context: behave.Context) -> None:
    root = _target(context)
    _run_cli(
        context,
        [
            "--repo",
            str(root),
            "--requirement",
            "missing provider",
            "--agent-command",
            "definitely-missing-agent-command",
            "--tool",
            "pytest",
            "--json",
        ],
    )


@when("I run the same dry run twice")
def step_two_dry_runs(context: behave.Context) -> None:
    root = _target(context)
    args = ["--repo", str(root), "--requirement", "deterministic prompt", "--dry-run", "--json"]
    outputs: list[str] = []
    for _ in range(2):
        _run_cli(context, args)
        outputs.append(context.payload["prompt"])
    context.prompts = outputs


@then("the exit code is {code:d}")
def step_exit_code(context: behave.Context, code: int) -> None:
    assert context.exit_code == code, (context.exit_code, context.stderr)


@then('the JSON status is "{status}"')
def step_json_status(context: behave.Context, status: str) -> None:
    assert context.payload is not None, context.stderr
    assert context.payload["status"] == status


@then('the requirement source origin is "{origin}"')
def step_requirement_origin(context: behave.Context, origin: str) -> None:
    assert context.payload["requirement_source"]["origin"] == origin


@then("the requirement source path points to the requirement file")
def step_requirement_path(context: behave.Context) -> None:
    assert context.payload["requirement_source"]["path"] == str(context.requirement_file)


@then('the prompt contains "{text}"')
def step_prompt_contains(context: behave.Context, text: str) -> None:
    prompt = context.payload.get("prompt") or context.stdout
    assert text in prompt


@then("no agent was invoked")
def step_no_agent(context: behave.Context) -> None:
    log = _target(context) / "agent-runs.log"
    assert not log.exists() or not log.read_text(encoding="utf-8").strip()


@then("the agent run count is {count:d}")
def step_agent_count(context: behave.Context, count: int) -> None:
    assert len(context.payload["agent_runs"]) == count


@then("no agent run is reported as successful")
def step_no_successful_agent(context: behave.Context) -> None:
    assert all(run["status"] != "pass" for run in context.payload["agent_runs"])


@then("the validation report count is {count:d}")
def step_validation_count(context: behave.Context, count: int) -> None:
    assert len(context.payload["validation_reports"]) == count


@then('the validation status is "{status}"')
def step_validation_status(context: behave.Context, status: str) -> None:
    assert context.payload["validation_status"] == status


@then('the final message contains "{text}"')
def step_message_contains(context: behave.Context, text: str) -> None:
    assert text in context.payload["message"], context.payload["message"]


@then("the report exposes the complete traceability chain")
def step_traceability(context: behave.Context) -> None:
    for key in (
        "requirement_source",
        "discovery",
        "agent_runs",
        "validation_reports",
        "final_git",
        "git_audit",
    ):
        assert key in context.payload, key
    assert context.payload["requirement_source"]["origin"] in {"inline", "file"}
    assert context.payload["prompt"]


@then("both prompts are identical")
def step_prompts_identical(context: behave.Context) -> None:
    assert context.prompts[0] == context.prompts[1]


@then('the error output contains "{text}"')
def step_error_contains(context: behave.Context, text: str) -> None:
    assert text in context.stderr


# --- WorkItem DAG and planning -------------------------------------------


def _items(specification: list[tuple[str, WorkItemStatus]] | None = None) -> list[WorkItem]:
    selected = specification or [(key, value[1]) for key, value in _WORK_ITEMS.items()]
    return [
        WorkItem(
            work_item_id=key,
            objective=_WORK_ITEMS[key][0],
            status=status,
            depends_on=_WORK_ITEMS[key][2],
        )
        for key, status in selected
    ]


@given("a standard work item DAG")
def step_standard_dag(context: behave.Context) -> None:
    context.work_items = _items()


@given("a cyclic work item DAG")
def step_cyclic_dag(context: behave.Context) -> None:
    context.work_items = [
        WorkItem(work_item_id="a", objective="a", depends_on=("b",)),
        WorkItem(work_item_id="b", objective="b", depends_on=("a",)),
    ]


@given('a work item DAG with a missing dependency "{missing}"')
def step_missing_dependency(context: behave.Context, missing: str) -> None:
    context.work_items = [WorkItem(work_item_id="a", objective="a", depends_on=(missing,))]


@when("I plan the work items")
def step_plan(context: behave.Context) -> None:
    try:
        context.plan = plan_work_items(context.work_items)
        context.plan_error = None
    except InvalidWorkItemDagError as error:
        context.plan = None
        context.plan_error = str(error)


@then('the ready work items are "{ids}"')
def step_ready(context: behave.Context, ids: str) -> None:
    assert context.plan.ready_ids == tuple(part.strip() for part in ids.split(",") if part.strip())


@then('the blocked work items are "{ids}"')
def step_blocked(context: behave.Context, ids: str) -> None:
    assert context.plan.blocked_ids == tuple(part.strip() for part in ids.split(",") if part.strip())


@then('the done work items are "{ids}"')
def step_done(context: behave.Context, ids: str) -> None:
    assert context.plan.done_ids == tuple(part.strip() for part in ids.split(",") if part.strip())


@then('the plan reports a pending dependency "{dependency}" for "{work_item}"')
def step_pending(context: behave.Context, dependency: str, work_item: str) -> None:
    entry = next(entry for entry in context.plan.entries if entry.work_item_id == work_item)
    assert entry.pending_dependency_ids == (dependency,)


@then('planning fails with "{message}"')
def step_plan_fails(context: behave.Context, message: str) -> None:
    assert context.plan_error is not None
    assert message in context.plan_error


@when("I complete the work item {work_item_id}")
def step_complete(context: behave.Context, work_item_id: str) -> None:
    context.work_items = [
        WorkItem(
            work_item_id=item.work_item_id,
            objective=item.objective,
            status=WorkItemStatus.DONE if item.work_item_id == work_item_id else item.status,
            depends_on=item.depends_on,
        )
        for item in context.work_items
    ]
    context.plan = plan_work_items(context.work_items)


@when("I build execution requests for the ready work items")
def step_build_requests(context: behave.Context) -> None:
    options = ExecutionRequestOptions(
        strategy=ExecutionStrategy.BUILD,
        working_directory="/tmp/target",  # noqa: S108
        mutation_policy=MutationPolicy(allow_workspace_mutation=True),
        permission_policy=PermissionPolicy(allow_network=False),
    )
    context.requests = build_execution_requests_for_ready_work_items(context.work_items, options=options)


@then("the execution requests contain {count:d} task")
def step_request_count(context: behave.Context, count: int) -> None:
    assert len(context.requests) == count


@then('the execution request task is "{task}"')
def step_request_task(context: behave.Context, task: str) -> None:
    assert context.requests[0].task == task


@then('the execution request working directory is "{directory}"')
def step_request_directory(context: behave.Context, directory: str) -> None:
    assert context.requests[0].working_directory == directory
