# TESTING — AgentLoop quality flow

This document describes the three quality levels and the commands that are
actually wired in this repository. It does not replace the test suite as the
source of truth.

## Level A — pre-commit (staged files)

Install once per clone:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e .
pre-commit install
```

`pre-commit install` writes `.git/hooks/pre-commit`. Every commit then runs the
configured hooks over the **staged** files. Do not bypass the hook with
`git commit --no-verify`.

Run it manually:

```bash
pre-commit run
pre-commit run --files agent_loop/domain/work_item.py
```

The hook order and responsibilities are:

| Hook | Scope | What it validates |
|---|---|---|
| `trailing-whitespace` | text files | trailing whitespace |
| `end-of-file-fixer` | text files | exactly one final newline |
| `check-json` | JSON files | JSON syntax |
| `check-toml` | TOML files | TOML syntax |
| `check-yaml` | YAML files | YAML syntax |
| `check-xml` | XML files | XML syntax |
| `check-case-conflict` | all files | case-only filename collisions |
| `check-docstring-first` | Python files | no code before the module docstring |
| `debug-statements` | Python files | no debugger statements |
| `check-builtin-literals` | Python files | no unnecessary builtin calls |
| `detect-private-key` | all files | no committed private keys |
| `ruff` | changed Python | Ruff lint; safe fixes stop the commit |
| `ruff-format` | changed Python | Ruff format; rewrites stop the commit |
| `mypy-changed-files` | changed Python | Mypy on the changed files |
| `pylint-changed-files` | changed Python | Pylint on the changed files |
| `architecture-boundaries` | package/architecture/config changes | `tests/agent_loop/architecture` |
| `pytest-changed-files` | changed Python | focused test modules mapped from the change |
| `behave-changed-features` | changed features/steps | matching features, or the complete suite when no match is found |
| `quality-gate-regression` | quality tooling/config | `tests/agent_loop/quality` |

`fail_fast: true` stops the chain at the first failing hook.

The `debug-statements` and `check-builtin-literals` hooks are present in the
pipeline; Ruff `T10` and `C408` additionally cover the same classes of defect,
so the generic checks remain as an independent second layer.

The focused pytest hook maps `agent_loop/<path>/<module>.py` to
`tests/agent_loop/<path>/test_<module>.py`, then to sibling `test_<module>_*.py`
suites, then to the parent package suite. Root `conftest.py` maps to the complete
test tree and `run.py` maps to its CLI test. A production change without a
mapped test is reported and skipped there; the complete suite still runs in the
full gate and CI.

If Ruff or Ruff format rewrites a file, pre-commit fails the commit. Review the
rewrite, stage it, and run the hooks again.

## Level B — complete quality gate

```bash
python scripts/run_quality_checks.py
```

The runner validates the complete repository, not only changed files. It never
uses `git diff`, `git status` or a changed-file mapping to choose Python
sources or tests. `pre-commit run --all-files` remains a separate manual sweep;
the runner invokes the individual file-hygiene hooks over a filesystem-discovered
file set so untracked files are covered too.

1. Runs the pre-commit **file-hygiene** hooks over every repository file
   discovered from the filesystem, including untracked files.
2. Runs Ruff lint and `ruff format --check` over the whole repository.
3. Runs Mypy over `agent_loop`, `tests`, `scripts`, `features/steps`,
   `features/environment.py`, `conftest.py` and `run.py`.
4. Runs Pylint over every Python module in that same scope, including root
   entry points.
5. Runs the architecture boundary suite.
6. Runs the complete pytest suite from `tests/agent_loop` with
   `--cov=agent_loop`, `--cov-report=term-missing` and
   `--cov-fail-under=99`.
7. Runs the complete Behave E2E suite from `features/`.
8. Checks the working tree and index with `git diff --check`.
9. Builds wheel and sdist, verifies that both include `LICENSE` and the
   required package members, installs the wheel in an isolated virtualenv,
   verifies the MIT metadata and installed import, runs
   `agent-loop --version`, `agent-loop --help`,
   `python -m agent_loop.autonomy --version`,
   `python -m agent_loop.autonomy --help` and `pip check`.

Every independent gate continues after a failure so one run gives the complete
diagnostic. Each command gate has an explicit timeout; an interrupted gate is
`ERROR` with its partial output preserved, never `PASS` or `NOT EXECUTED`. The
final summary prints each gate with PASS, FAIL, SKIPPED, NOT EXECUTED or ERROR,
its scope, exact command, exit code, duration and reason, plus pytest counts,
Behave counts and coverage against the 99% threshold.

| Status | Meaning |
|---|---|
| `PASS` | The control executed and satisfied its evidence requirements. |
| `FAIL` | The control executed and found a violation. |
| `SKIPPED` | An explicit legitimate condition made it inapplicable. The reason is preserved. |
| `NOT EXECUTED` | A required executable was absent. |
| `ERROR` | The gate mechanism or its output could not be trusted. |

A required gate is blocking unless it passed, with one explicit exception: a
required gate whose applicability condition is false (for example a file-type
hook with no matching files) is `SKIPPED` with `conditional: ...` and does not
block. It is still printed as `SKIPPED`, never as `PASS`. A required
`NOT_EXECUTED`, `ERROR` or non-conditional `SKIPPED` fails the global result.
An optional gate may be `SKIPPED` without failing the run, but it is still
shown with its reason. Unparseable pytest/Behave counts, missing coverage or
missing heavy-request evidence are `ERROR`.

Use `--skip-package` only for local iteration. CI never uses it.

The individual commands remain available for diagnosis:

```bash
pytest --cov=agent_loop --cov-report=term-missing --cov-fail-under=99
behave
ruff check .
ruff format --check .
mypy agent_loop tests scripts features/steps features/environment.py conftest.py run.py
python scripts/pylint_precommit.py $(find agent_loop tests scripts features -name '*.py') conftest.py run.py
pytest -q tests/agent_loop/architecture
```

## Level C — CI

`.github/workflows/ci.yml` does not rely on local hooks:

- the `quality-gate` job runs `python scripts/run_quality_checks.py` on Python
  3.14, including the complete pre-commit hygiene set, Ruff, Mypy, Pylint,
  architecture tests, pytest with the 99% gate, Behave and packaging;
- the `tests` job runs the complete suite with
  `--cov-fail-under=99` on Python 3.12, 3.13 and 3.14;
- the `package` job builds wheel and sdist, verifies `LICENSE`, installs the
  wheel in a fresh virtualenv, checks the installed metadata and import, runs
  both entry points and executes `pip check`.

A local pre-commit run only proves that the staged change passed the local
hooks. It does not replace the complete gate or CI.

## Coverage and architecture

- The global coverage threshold is 99% and is enforced by
  `[tool.coverage.report] fail_under` in `pyproject.toml` and explicitly by
  `--cov-fail-under=99` in the runner and CI.
- `features/` contains the product-level Behave E2E suite. It uses temporary
  repositories, fake agent/tool commands and no external service. The integral
  scenario `features/requirement_planning_e2e.feature` receives a multi-
  instruction requirement bundle, obtains a structured plan from a
  deterministic provider through the real `AgentRuntime` port, validates the
  DAG with `plan_work_items`, builds requests with the real bridge, executes
  the ready frontier through `execute_work_item_with_recovery`, runs the real
  target pytest validation and checks traceability. The provider and the
  file-editing runtime are deterministic test doubles at the external AI/agent
  boundary; the planner, request bridge, orchestrator, recovery policy and
  target validation are the real implementations. Natural-language
  decomposition into `WorkItem` values is not implemented and is not claimed by
  the scenario.
- The heavy-request control is a runtime audit hook for pytest and a Behave
  environment guard. It intercepts `subprocess.Popen`, `os.system`,
  `os.exec*`, `os.posix_spawn`, `os.spawn*` (wrapped because CPython emits no
  audit event for that family), DNS events (`getaddrinfo`, `gethostbyname*`,
  `gethostbyaddr`, `getnameinfo`), socket connect/send events,
  `http.client.connect` and `urllib.Request`. Transparent `env`, `sudo`, `nice`,
  `nohup`, `timeout`, `stdbuf`, `command`, `ionice`, `taskset` and `xargs`
  prefixes are unwrapped before classification. Undeclared provider CLIs,
  package managers, container runtimes, remote Git operations and non-loopback
  network targets fail the test; intentional cases opt in with
  `@pytest.mark.heavy_request` or `@heavy`. That opt-in is scoped to the test
  or scenario lifetime and authorizes the classified operations it performs;
  it is not a global allowlist. The guard prints
  `HEAVY REQUEST GUARD: scope=... initialized=... activated=... observed=...
  violations=... authorized=... expected=...` and one line per violation or
  authorized exception. The integral pytest gate requires a valid report with
  activation, observed audit events, at least one expected-detection event and
  at least one explicit authorization; silence or a report without that
  evidence is `ERROR`. The Behave gate requires initialization, activation and
  observed events, and `@heavy` authorizations are reported when used.
- The guard is a Python-runtime audit control, not an OS sandbox. It does not
  inspect raw `ctypes`/C calls, code that bypasses Python's audit events, or
  network activity performed by an already-spawned child process after `exec`.
  Those limits are documented rather than hidden behind a claim of containment.
- The two OpenCode Server tests that require a real model execution remain
  optional:
  `tests/agent_loop/adapters/opencode_server/test_e2e_work_item.py::test_work_item_reaches_opencode_server_and_changes_fixture`
  and
  `tests/agent_loop/adapters/opencode_server/test_integration.py::test_real_execution_returns_terminal_result`.
  They are marked `heavy_request`, skip when `opencode` is absent or
  `AGENT_LOOP_E2E_OPENCODE_SERVER` is not `1`, and are not executed by CI. Run
  them locally with:

  ```bash
  AGENT_LOOP_E2E_OPENCODE_SERVER=1 pytest -q tests/agent_loop/adapters/opencode_server
  ```

  The real discovery test in `test_integration.py` can run when the binary is
  installed; it is not one of the two environment-gated executions.
- `tests/agent_loop/architecture/test_package_boundaries.py` enforces package
  and layer boundaries with AST import inspection.
- The independence E2E,
  `tests/agent_loop/autonomy/test_standalone_independence.py`, copies the
  package to an isolated directory and runs discovery, dry-run, validate-only,
  `--version` and `--help` without the Shipii checkout.
- The OpenCode Server E2E tests remain environment-gated by
  `AGENT_LOOP_E2E_OPENCODE_SERVER=1`.

Never weaken a gate, add exclusions, skip a failing test or change an expected
value to obtain a green result. Fix the implementation or report the proven
tool limitation separately.

## Before opening a pull request

1. `pre-commit run --all-files` is green (or the changes have been reviewed and
   staged after a formatter rewrite).
2. `python scripts/run_quality_checks.py` is green.
3. `git diff --check` reports no whitespace errors.
4. The wheel and sdist contain `LICENSE`.
5. CI is expected to run the same complete gates; do not assume local hooks are
   sufficient.
