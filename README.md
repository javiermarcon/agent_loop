# AgentLoop — Autonomous Execution Engine

> **AI where reasoning matters. Determinism everywhere else.**

AgentLoop is an autonomous execution engine for software repositories. It
combines AI reasoning with deterministic orchestration to interpret a
requirement, plan the work, invoke an authorized agent, validate the result and
attribute the changes that actually happened.

The engine is designed around one rule: a model should be used only where
interpretation, planning, complex decisions or problem solving are required.
Discovery, command construction, environment isolation, validation,
Git attribution, routing, retry policy and evidence collection are deterministic
and do not consume model tokens. When AgentLoop invokes an external AI agent,
that agent may consume tokens according to its own provider and configuration.

This repository is the independent AgentLoop distribution. It is self-contained:
it does **not** require a Shipii checkout, Shipii imports, Shipii scripts or
Shipii runtime configuration.

## 1. Responsibility split

| Responsibility | Owner | Why |
|---|---|---|
| Understand an ambiguous requirement and propose an implementation | AI agent | Requires interpretation and code reasoning |
| Edit source files and repair failing work | AI agent | Requires synthesis and problem solving |
| Discover instructions, project layout, Django settings and tool inventory | Deterministic orchestrator | Repeatable, inspectable and testable |
| Select prompt transport and build provider commands | Deterministic orchestrator | Removes shell-quoting and argument-size ambiguity |
| Build an isolated target environment | Deterministic orchestrator | Enforces the security allowlist |
| Run tests, linters and project commands | Deterministic orchestrator | Produces structured, comparable evidence |
| Capture the Git baseline and attribute changes | Deterministic orchestrator | Post-hoc audit, not an AI judgement |
| Route work to a runtime/model and decide retry or escalation | Deterministic control plane | Fail-closed policy with explicit evidence |
| Emit telemetry from observed facts | Deterministic control plane | Correlated, redacted, bounded events |

The AI is not asked to perform work that a deterministic mechanism can verify.
The deterministic mechanisms never pretend that an unavailable or unknown
result is a success.

## 2. Requirements

- Python **3.12 or newer**. The minimum is required by PEP 695 `type` aliases
  used in the package. Python 3.14 is the reference development and CI
  environment; the suite is also tested on Python 3.12 and 3.13.
- Git available on `PATH`.
- A virtualenv owned by AgentLoop (do not reuse the target project's virtualenv).
- A target repository that already exposes an executable environment for its
  tools (a local virtualenv or a configured Docker Compose service).
- Optional: Docker with Compose when the target project uses it.
- An authorized external agent command for execution mode (for example
  `opencode run`, `codex exec` or `cursor-agent`). Discovery, dry-run and
  validation-only do not require an agent command.

## 3. Installation

```bash
cd agent_loop
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

You can also run the checkout without installing the entry point:

```bash
python run.py --help
```

The distribution name is `agent-loop-autonomous`, the import package is
`agent_loop`, and the installed console script is `agent-loop`.

```bash
agent-loop --help
```

The equivalent module entry point is:

```bash
python -m agent_loop.autonomy --help
```

## 4. Quick start

### Discover a target repository

```bash
agent-loop \
  --repo /path/to/target-repository \
  --requirement "Add a health-check endpoint" \
  --discover-only
```

### Render the prompt without executing anything

```bash
agent-loop \
  --repo /path/to/target-repository \
  --requirement-file requirement.md \
  --dry-run
```

### Run only the implemented validations

```bash
agent-loop \
  --repo /path/to/target-repository \
  --requirement "Validate the current state" \
  --validate-only \
  --tool pytest \
  --full
```

### Invoke an external agent

```bash
agent-loop \
  --repo /path/to/target-repository \
  --requirement "Implement the requirement" \
  --agent-command "codex exec" \
  --max-iterations 3
```

If no agent command is configured, execution mode fails explicitly with an
`unavailable` result. It never simulates a successful run.

## 5. Implemented capabilities

### 5.1 Available

- Autonomous discovery of a target Git worktree:
  - project instructions (`AGENTS.md`, `README.md`, architecture/testing/style
    documents);
  - Django markers, settings, `INSTALLED_APPS` classification and dependency
    files;
  - virtualenv and Docker Compose environments;
  - documented project commands and tool inventory.
- Read-only Git baseline and post-hoc change attribution (`preexisting`,
  `execution`, `validation`, `undetermined`).
- Deterministic prompt construction and transport (`stdin`, positional
  argument, or automatic selection for large prompts).
- Tool adapters for Git, pre-commit, Ruff, Mypy, Pylint, Bandit, pytest,
  Behave and Docker Compose.
- Structured reports in Markdown or JSON, with `pass`, `fail`, `skipped`,
  `unavailable` and `unknown` states.
- Control-plane library contracts:
  - effective-capability vocabulary and `RuntimeInventory`;
  - deterministic `TaskAssessment`;
  - logical and runtime model profiles plus catalogs;
  - declarative compatible-candidate resolution;
  - operational routing with explicit readiness evidence;
  - `WorkItem` validation and deterministic DAG planning;
  - request gating before an `AgentExecutionRequest` is built;
  - provider-neutral failure classification and bounded recovery policy;
  - a bounded control-plane execution loop with escalation.
- Runtime adapters:
  - OpenCode CLI discovery (read-only evidence);
  - Claude Code CLI discovery (read-only evidence);
  - OpenCode Server runtime over the documented public HTTP/SSE API.
- Declarative project profiles and skills with fail-closed compatibility
  assessment.
- Telemetry as an outbound port with in-memory and null sinks; events correlate
  observed routing, execution, tool, quality, change and closure facts.
- Installable wheel and sdist with the `agent-loop` console entry point.

### 5.2 Partial or intentionally not wired

- The autonomous CLI (`agent-loop`) uses the `autonomy/` loop and deterministic
  tool/validation adapters. The control-plane execution loop
  (`application/execution_orchestrator.py`) is a tested library API; it is not
  wired into the autonomous CLI in this release.
- Resume and lifecycle contracts under `application/resume_*`,
  `application/lifecycle.py` and `application/state_graph.py` are retained,
  tested library-level contracts. They are not a promise of durable resume in
  the autonomous CLI.
- Telemetry ships local sinks only. There is no mandatory database, queue,
  dashboard or external observability vendor.
- Docker Compose is discovered and can run validations inside the discovered
  service. It is not a general remote execution platform.
- The current discovery and tool adapters target Django/Python repositories.

### 5.3 Out of scope for this release

- Non-Django ecosystems.
- Multi-requirement planning/execution from the legacy Shipii host.
- Legacy gate governor, execution-scope enforcement, plan logs, run store and
  Notion ingress.
- Discovery artifact publication, freshness contracts and reference-path
  authority from the legacy host.
- A web UI, dashboard, hosted service or release automation.
- PyPI publication as part of this repository's CI.

See `docs/migration/EXCLUDED_LEGACY_CAPABILITIES.md` for the explicit
classification of legacy capabilities that were not migrated.

## 6. CLI options

The entry point is `agent_loop.autonomy.cli:main`.

| Option | Description |
|---|---|
| `--repo` | Target Git repository. Required. |
| `--requirement` | Inline requirement. |
| `--requirement-file` | UTF-8 file containing the requirement. |
| `--agent-command` | External agent command. |
| `--prompt-transport` | `auto` (stdin for large prompts), `stdin` or `argument`. Default: `auto`. |
| `--max-iterations` | Agent → validation cycles. Default: 3. |
| `--timeout-seconds` | Per-command timeout. Default: 3600. |
| `--max-output-chars` | Captured output limit per stream. |
| `--dry-run` | Discover and print the prompt without executing. |
| `--discover-only` | Discover and report only. |
| `--validate-only` | Run validations without an agent. |
| `--tool` | Restrict validation to a tool; may be repeated. |
| `--full` | Run full validations instead of focused ones. |
| `--json` | Emit a JSON report. |
| `--output` | Write the report to a file. |
| `--target-path` | Instruction scope and Git-audit scope; may be repeated. |
| `--env KEY=VALUE` | Explicit variable for the target process. |
| `--log-dir` | Store `prompt.md` and `result.json` artifacts per run. |
| `--version` | Print the installed version. |

`--dry-run`, `--discover-only` and `--validate-only` are mutually exclusive.
Exactly one requirement source (`--requirement` or `--requirement-file`) is
required.

## 7. Discovery

Instructions are resolved by scope: for a target path, AgentLoop includes the
root instructions and the `AGENTS.md` chain of its ancestor directories.
Target-repository instructions govern development inside that repository, but
they cannot override AgentLoop's security invariants.

Django discovery is static and does not import target code. It recognizes:

- `manage.py` and `DJANGO_SETTINGS_MODULE` (literal, variable or branch);
- settings modules and packages, including local imports;
- `INSTALLED_APPS` composition;
- project, third-party and framework app classification;
- Python and Django versions declared by the project;
- dependency and lock files;
- virtualenvs;
- standard and environment-specific Compose files selected from project
  evidence;
- the Django Compose service and effective working directory;
- documented project commands.

Tool inventory uses explicit states: `configured_available`,
`configured_unavailable`, `not_configured`, `not_required` and `unknown`.
A tool that cannot be verified is never reported as available.

## 8. Execution and validation

Every validation records the tool, command, working directory, interpreter,
environment variable names (never values), exit code, bounded stdout/stderr and
the interpreted state.

- `fail` results are classified as `implementation`, `usage`, `configuration`,
  `infrastructure` or `execution`.
- Only `implementation` failures can start another agent/validation cycle.
- A tool that could not run is never interpreted as `pass`.
- An empty report or a report with every validation skipped is not `pass`.
- The report distinguishes operational `status` from `validation_status`.
- `--discover-only` and `--dry-run` may return `pass` with
  `validation_status: null`; that is not a validation success claim.

Focused validation rules include:

- pytest: changed tests run directly; module-to-test mapping is deterministic,
  ambiguity falls back to the full suite, and exit code 5 is `skipped`;
- Ruff, Mypy and Bandit: changed Python files, or the whole project with
  `--full`;
- Pylint: changed Python files;
- pre-commit: changed files, or `--all-files` with `--full`;
- Behave: changed features or `features/`;
- Docker Compose: configuration validation and, when discovered, execution
  inside the service.

## 9. Control-plane library surface

The control plane is available as a library independent of the autonomous CLI.
The main contracts live in:

- `agent_loop/domain/` — pure capabilities, task assessment, model profiles,
  WorkItem DAG, failures and exit codes;
- `agent_loop/ports/` — `AgentRuntime` and telemetry ports;
- `agent_loop/application/` — routing, request gating, recovery policy,
  bounded execution orchestration and planning bridges;
- `agent_loop/adapters/` — OpenCode CLI, Claude Code CLI, OpenCode Server and
  telemetry sinks;
- `agent_loop/project/` — declarative profiles and skills.

This library surface does not import concrete adapters from application code.
Callers inject an `AgentRuntime` and telemetry sink explicitly. See
`ARCHITECTURE.md` for the dependency direction and the complete flow.

## 10. Security

- The target root is normalized and must be a real Git worktree.
- Every read/write path must stay inside the authorized repository.
- Symlinks cannot escape the workspace; broken links and cycles are ignored.
- The target process environment is built from an allowlist. `PYTHONPATH`,
  `VIRTUAL_ENV`, `DJANGO_SETTINGS_MODULE`, `SHIPII_*` and `AGENT_LOOP_*` are
  never inherited from the host.
- `shell=True` is never used.
- Destructive Git operations are never executed.
- Internal Git calls discard inherited `GIT_*` redirection variables.
- Repository instructions cannot broaden permissions or commands.
- Secrets are not logged.
- Git attribution is post-hoc. The external agent is trusted; AgentLoop does
  not claim to sandbox or contain it at the OS level.

## 11. Environment variables

Provider configuration uses product-owned names:

| Variable | Purpose |
|---|---|
| `AGENT_LOOP_CODEX_COMMAND` | Override the Codex provider command. |
| `AGENT_LOOP_CURSOR_COMMAND` | Override the Cursor provider command. |
| `AGENT_LOOP_CURSOR_MODEL` | Select the Cursor model. |
| `AGENT_LOOP_CURSOR_MODE` | Select the Cursor mode. |
| `AGENT_LOOP_CURSOR_AUTO_FALLBACK_MODEL` | Fallback model for Cursor retry. |

Legacy `SHIPII_*` provider variables are no longer read. The `SHIPII_*` and
`AGENT_LOOP_*` patterns remain in the security denylist so they are stripped
from target processes; that is a security rule, not configuration
compatibility.

## 12. Architecture

```text
agent_loop/
  domain/            pure control-plane contracts and decisions
  ports/             AgentRuntime and telemetry boundaries
  application/       routing, gating, recovery, orchestration, lifecycle
  adapters/          OpenCode CLI, Claude CLI, OpenCode Server, telemetry sinks
  project/           ProjectProfile / Skill catalogs and compatibility
  telemetry/         neutral recorder and event construction
  autonomy/          autonomous target-repository execution
    config.py        execution policy
    security.py      workspace, paths and isolated environment
    requirement.py   requirement provenance
    discovery/       instructions, Django, Git, tools, Git attribution
    tools/           contracts, command runner, registry, failure, adapters
    execution/       target environment, agent invocation, runner, loop
    report.py        structured report and Markdown rendering
    cli.py           `agent-loop` entry point
  agent/providers/   Codex and Cursor command adapters
```

`agent_loop/domain/` and `agent_loop/ports/` are independent of adapters, CLI,
subprocess and provider SDKs. `agent_loop/autonomy/` does not import `scripts`,
`apps` or Django. The complete architecture, entry points and execution flows
are documented in `ARCHITECTURE.md`.

## 13. Repository independence

- `agent_loop/` is the only canonical package.
- `tests/agent_loop/` is the canonical test suite.
- `ARCHITECTURE.md` and `ARCHITECTURE_DECISIONS.md` document AgentLoop.
- `docs/ai/` contains the OpenCode Server research and telemetry schema.
- `docs/migration/` records the extraction, the migrated history and the
  capabilities that were intentionally excluded.
- Vendored copies (`standalone/agent_loop/`), transformed checks
  (`standalone/checks/`) and Shipii-hosted `scripts/` wrappers are not part of
  this distribution.

## 14. Development and quality gates

Install the pinned development toolchain and the Git hook once per clone:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e .
pre-commit install
```

`pre-commit install` writes `.git/hooks/pre-commit`. Every `git commit` then
runs the configured hooks against the **staged** files. Never bypass them with
`git commit --no-verify`; a commit that skipped the hooks is not validated.

Run the hooks manually:

```bash
# staged files only
pre-commit run

# every tracked file (manual full sweep; the integral runner invokes the
# individual hygiene hooks over a filesystem-discovered file set instead)
pre-commit run --all-files

# specific staged files
pre-commit run --files agent_loop/domain/work_item.py tests/agent_loop/domain/test_work_item.py
```

The pre-commit hook order is:

1. generic file checks: trailing whitespace, final newline, JSON/TOML/YAML/XML
   syntax, case conflicts, docstring-first, debugger statements, builtin
   literals and private keys;
2. Ruff lint with safe fixes and Ruff format;
3. Mypy on the changed Python files;
4. Pylint on the changed Python files;
5. the architecture boundary tests;
6. the focused pytest modules related to the changed files;
7. the Behave scenarios related to changed features/steps;
8. the regression tests for the quality tooling itself.

Ruff and Ruff format may rewrite a file. When that happens pre-commit stops the
commit; review the rewrite and stage it before retrying. The focused pytest
hook never replaces the complete suite: a changed production module without a
mapped test is reported, a root `conftest.py` change runs the complete test
tree, and the full gate and CI still run every test.

Run the complete quality gate before opening a pull request:

```bash
python scripts/run_quality_checks.py
```

That command validates the **complete repository**, including files that are
untracked, unchanged or not staged. It never chooses Python sources or tests
from `git diff`/`git status` or from a changed-file-to-test mapping. It runs the
file-hygiene hooks over a filesystem-discovered file set, Ruff and Ruff format
over the repository, Mypy and Pylint over all product/test/tool/BDD modules
(including root `conftest.py` and `run.py`), the architecture suite, the
complete pytest suite from `tests/agent_loop` with `--cov-fail-under=99`, the
complete Behave E2E suite, `git diff --check` for the working tree and index,
and the wheel/sdist build, required artifact members, isolated install,
metadata, import, `--version`/`--help` entry points and `pip check`
verification. Every command gate has a bounded timeout. The final summary
reports every gate as PASS, FAIL, SKIPPED, NOT EXECUTED or ERROR with its
scope, exact command, exit code, duration and reason.
A hook that finds no applicable files is SKIPPED with an explicit conditional
reason, never PASS; any other required omission is blocking.

The individual tools remain available for diagnosis:

```bash
pytest --cov=agent_loop --cov-report=term-missing --cov-fail-under=99
behave
ruff check .
ruff format --check .
mypy agent_loop tests scripts features/steps features/environment.py conftest.py run.py
python scripts/pylint_precommit.py $(find agent_loop tests scripts features -name '*.py') conftest.py run.py
pytest -q tests/agent_loop/architecture
```

The coverage gate is 99% and is configured in `pyproject.toml` plus the
explicit `--cov-fail-under=99` argument in the runner and CI.

The Behave suite under `features/` validates requirement interpretation, the
WorkItem DAG, planning/retry behavior, quality-gate interpretation, final
traceability and deterministic no-agent dry runs. It also contains an integral
requirement-bundle scenario that turns related instructions into a validated
DAG, executes the ready frontier through the control-plane orchestrator and
runs the real target pytest validation. The interpretation boundary and the
file-editing runtime are deterministic test doubles; the planner, request gate,
orchestrator, recovery policy and validation flow are real. The suite uses temporary repositories and
fake commands, so it needs no credentials, provider tokens or external
services. Tests that intentionally perform a real provider/network operation
must opt in with `@pytest.mark.heavy_request` or the `@heavy` Behave tag; the
default is a failing heavy-request violation. The guard intercepts process
creation (`subprocess.Popen`, `os.system`, `os.exec*`, `os.posix_spawn`,
`os.spawn*`), DNS/socket/HTTP events, provider CLIs, package managers, container
runtimes and remote Git operations. Transparent `env`/`sudo`/`nice`/`timeout`
style prefixes are unwrapped before classification. It emits an explicit report
with initialization, activation, observed events, violations, expected
detections and authorized exceptions; the integral pytest gate treats a
missing report, missing detection evidence or missing authorization evidence as
ERROR. The guard is a Python-runtime audit control, not an OS sandbox: raw
`ctypes`/C calls and network work performed by an already-spawned child process
after `exec` are outside its visibility.

Build and verify the distribution manually when needed:

```bash
python -m build
python -m venv /tmp/agent-loop-wheel
/tmp/agent-loop-wheel/bin/python -m pip install dist/*.whl
/tmp/agent-loop-wheel/bin/python -m pip check
/tmp/agent-loop-wheel/bin/agent-loop --version
/tmp/agent-loop-wheel/bin/agent-loop --help
/tmp/agent-loop-wheel/bin/python -m agent_loop.autonomy --version
/tmp/agent-loop-wheel/bin/python -m agent_loop.autonomy --help
```

CI does not trust local hooks:

- a `quality-gate` job runs `python scripts/run_quality_checks.py` on Python
  3.14 (complete hygiene, Ruff, Mypy, Pylint, architecture, pytest with the 99%
  gate, Behave and packaging);
- a tests matrix runs the complete suite with `--cov-fail-under=99` on Python
  3.12, 3.13 and 3.14;
- a packaging job builds wheel and sdist, verifies `LICENSE` and required
  package members in both, installs the wheel into an isolated virtualenv,
  verifies MIT metadata and the installed import, runs `--version` and
  `--help` for both entry points and executes `pip check`.

Development tooling is pinned exactly in `requirements-dev.txt` and in the
`dev` optional dependency group. Pre-commit hook repositories are pinned by
revision in `.pre-commit-config.yaml`. Runtime dependencies keep a compatible
lower bound instead of being locked to a workstation-specific version.
`TESTING.md` documents the complete flow.

The OpenCode Server end-to-end tests are environment-gated and do not run by
default:

```bash
AGENT_LOOP_E2E_OPENCODE_SERVER=1 pytest tests/agent_loop/adapters/opencode_server
```

Two tests remain intentionally optional: `test_e2e_work_item.py` (a real
WorkItem execution) and `test_integration.py::test_real_execution_returns_terminal_result`.
Both require the `opencode` binary, a reachable model and
`AGENT_LOOP_E2E_OPENCODE_SERVER=1`; CI does not set that environment. They are
reported as skipped, never as passed.

## 15. Known limitations

- Discovery and tool adapters currently target Django/Python repositories.
- The autonomous CLI does not yet consume the control-plane execution
  orchestrator.
- The control-plane planner consumes `WorkItem` values; natural-language
  decomposition of a requirement into `WorkItem` values and ambiguity/clarification
  handling are not implemented. Invalid or empty work items fail closed.
- Durable resume, plan logs, run store, multi-requirement execution, Notion
  ingress, artifact publication and legacy gate governance are not implemented.
- Telemetry is local-only; no persistence, queue or dashboard is included.
- Git attribution is a post-hoc audit, not an isolation boundary.
- The external agent remains responsible for its own planning quality and edits;
  AgentLoop supplies context, deterministic orchestration, execution and
  validation.

## 16. License and migration record

AgentLoop is distributed under the MIT License. The full text is in `LICENSE`,
and `pyproject.toml` declares `license = "MIT"` with `LICENSE` included in the
wheel and sdist.

The copyright notice is exactly:

```text
Copyright (c) 2026 Javier Marcon
```

The migrated commit history is preserved unchanged, including author metadata;
no commit was rewritten to change authorship. See `docs/migration/README.md`
for the migration record.
