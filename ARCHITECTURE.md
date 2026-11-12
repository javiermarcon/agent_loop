# ARCHITECTURE

AgentLoop is an autonomous execution engine built from two cooperating parts:

1. **An autonomous target-repository loop** (`agent_loop/autonomy/`) that
   discovers a repository, prepares an isolated context, invokes an authorized
   agent command, runs project validations and audits the resulting Git changes.
2. **A deterministic control plane** (`agent_loop/domain/`,
   `agent_loop/ports/`, `agent_loop/application/`, `agent_loop/adapters/`) that
   models capabilities and work, selects a runtime/model from explicit
   evidence, gates execution requests, classifies failures and bounds recovery.

AI reasoning is used for interpretation, planning and code changes. Everything
that can be verified mechanically is handled by deterministic code.

The architecture in this document describes the current independent package. It
does not describe the historical Shipii monorepo layout.

## Repository layout

```text
agent_loop/
  __init__.py
  domain/                    pure contracts and deterministic decisions
    capabilities.py          capability ids, snapshots, inventory, is_effective
    task_assessment.py       deterministic task classification
    model_profiles.py        logical and runtime model bindings
    model_profile_catalog.py immutable catalogs with conflict rejection
    compatible_candidates.py declarative candidate resolution
    work_item.py             control-plane unit of work
    work_item_plan.py        deterministic DAG validation and planning
    faults.py                provider-neutral failure vocabulary
    errors.py, exit_codes.py
  ports/
    agent_runtime.py         AgentRuntime discover/execute boundary
    telemetry.py             TelemetrySink boundary and event schema
  application/
    execution_routing.py     S13 operational routing
    execution_request_builder.py S16 WorkItem request gate
    planned_execution_requests.py plan-to-request bridge
    recovery_policy.py       S14 bounded recovery decisions
    execution_orchestrator.py bounded control-plane loop
    lifecycle.py             retained lifecycle contracts (not wired to autonomy CLI)
    resume_*.py              retained resume contracts (not durable storage)
    state_graph.py           declarative transition graph contracts
  adapters/
    opencode/                OpenCode CLI discovery adapter
    claude/                  Claude Code CLI discovery adapter
    opencode_server/         OpenCode Server runtime adapter
    telemetry/               in-memory and null telemetry sinks
  project/
    skills.py                declarative Skill/SkillCatalog
    project_profiles.py      declarative ProjectProfile/ProjectProfileCatalog
    profile_skill_compatibility.py fail-closed profile assessment
  telemetry/
    recorder.py              neutral recorder, redaction, bounds, aggregation
  agent/
    providers/               Codex and Cursor command adapters
  autonomy/
    __main__.py              python -m agent_loop.autonomy
    cli.py                   agent-loop console entry point
    config.py                execution policy and prompt transport
    security.py              target workspace and environment isolation
    requirement.py           requirement source and provenance
    report.py                structured report and Markdown rendering
    telemetry.py             autonomy-specific telemetry facts
    discovery/
      instructions.py        scoped instruction discovery
      django_project.py      static Django project discovery
      django_settings.py     static settings resolution
      compose.py             Compose discovery and selection
      tools.py               tool inventory
      tool_config.py         project tool configuration
      tool_commands.py       documented command discovery
      git.py                 read-only Git state
      git_attribution.py     baseline, windows, change attribution
    tools/
      base.py                tool contracts and states
      command.py             process runner and prompt transport
      registry.py            adapter selection
      failure.py             failure classification
      adapters.py            concrete tool adapters
    execution/
      environment.py         target interpreter and environment
      agent.py               external agent invocation
      runner.py              validation and retry policy
      loop.py                autonomous discovery/execute/validate loop
```

## Dependency direction

```text
domain  <-  ports  <-  application  <-  adapters
   ^                       ^
   |                       |
project ----------------> domain/ports

autonomy  ->  autonomy.discovery / autonomy.tools / autonomy.execution
autonomy  ->  ports.telemetry
autonomy  -/-> scripts, apps, django
```

Hard rules:

- `agent_loop/domain/` cannot import application, adapters, CLI, providers,
  subprocess, Git, Docker or Django.
- `agent_loop/ports/` cannot import application, adapters, CLI or providers.
- `agent_loop/application/` cannot import concrete adapters.
- `agent_loop/project/` may import pure domain contracts but cannot import
  application, adapters or CLI.
- `agent_loop/autonomy/` cannot import `scripts`, `apps` or Django.
- No part of the package may depend on the Shipii checkout.
- Target tools run with the target project's environment, never with the
  AgentLoop interpreter.

These rules are enforced by `tests/agent_loop/architecture/` and by AST import
audits in the application test suite.

## Entry points

| Entry point | Purpose |
|---|---|
| `agent-loop` | Installed console script mapped to `agent_loop.autonomy.cli:main`. |
| `python -m agent_loop.autonomy` | Module invocation of the same CLI. |
| `python run.py` | Checkout-local launcher; no installation required. |
| `agent_loop.autonomy.cli:build_parser` | CLI contract without consuming `sys.argv`. |
| `agent_loop.application.execution_orchestrator` | Library-level control-plane execution loop. |

There is no second parser or hidden legacy CLI in the package. The old
multi-requirement parser was removed because none of its options had an
executable implementation in this distribution.

## Autonomous execution flow

```text
CLI arguments
  -> RequirementSource
  -> TargetWorkspace (normalize, authorize, require Git worktree)
  -> AutonomyConfig
  -> AutonomyLoop.run
       1. discover instructions, Django project, tools and Git state
       2. build the deterministic prompt
       3. capture the Git baseline
       4. invoke the external agent (unless dry-run/discover-only)
       5. run focused or full validations
       6. capture Git windows and attribute changes
       7. decide whether another bounded cycle is allowed
  -> AutonomyResult
  -> Markdown or JSON report
```

Important properties:

- `--discover-only` stops after discovery.
- `--dry-run` renders the prompt without invoking the agent.
- `--validate-only` runs validations without an agent.
- A missing agent command produces an explicit `unavailable` result.
- A tool that cannot run is never reported as `pass`.
- `--target-path` normalizes equivalent relative/absolute spellings and rejects
  paths outside the workspace.
- Git attribution is post-hoc: changes are classified against captured
  baselines and windows, but an agent cannot be treated as OS-isolated.

## Control-plane flow

The control plane is a deterministic library pipeline:

```text
WorkItem
  -> validate_work_item_dag / plan_work_items
  -> TaskAssessment
  -> resolve_compatible_candidates
  -> select_routing (explicit runtime readiness evidence)
  -> build_execution_request (schedulable WorkItem gate)
  -> AgentRuntime.execute
  -> classify_failure
  -> decide_recovery (retry, model escalation, runtime escalation,
                      human intervention or terminal closure)
  -> telemetry facts
```

Key invariants:

- `CONFIGURED` capability state or declarative compatibility never implies
  availability.
- Missing readiness evidence, missing snapshots and `UNKNOWN`/`NO` axes reject a
  candidate.
- Routing is stable by `(runtime_id, profile_id)` and honors explicit
  exclusions, required runtimes and forbidden runtimes.
- An empty selection is a valid terminal outcome; no fallback is invented.
- A non-idempotent operation is not repeated automatically after execution
  started or effects may exist.
- Failure classification is provider-neutral and does not live in adapters.
- Attempt history is kept in memory; durable resume is not part of the
  control-plane loop.

The control plane is currently a tested library surface. It is not wired into
the autonomous CLI in this release.

## Runtime adapters

### OpenCode CLI (`adapters/opencode/`)

Read-only discovery through `--version`, `mcp list`, `models --standalone` and
`debug agents`. It does not call mutating/auth MCP commands or private HTTP
APIs as a discovery substitute. Parsers distinguish a valid empty inventory
from unreadable stdout.

### Claude Code CLI (`adapters/claude/`)

Read-only discovery through `--version`, `auth status --json`, `mcp list` and
`plugin list`. Auth JSON is trusted only after a successful exit. The adapter
does not call login/logout or mutating MCP commands. When a read-only models
inventory is unavailable, the inventory stays empty and the corresponding axes
remain `UNKNOWN`.

### OpenCode Server (`adapters/opencode_server/`)

A separate runtime identity (`opencode-server`) using the documented public
HTTP/SSE API. It supports attach mode and managed mode on loopback, validates
version support fail-closed, maps `AgentExecutionRequest` without widening
permissions, rejects permission asks and does not retry or own recovery policy.
Discovery never starts a process.

## Capability and project contracts

- `domain/capabilities.py` owns the effective-capability vocabulary.
- `domain/task_assessment.py` derives capability requirements from declared
  task signals; floors can only raise complexity, risk or uncertainty.
- `domain/model_profiles.py` and `model_profile_catalog.py` own logical models
  and runtime bindings without inferring availability.
- `domain/compatible_candidates.py` answers what could be compatible.
- `application/execution_routing.py` answers what may actually be selected now.
- `project/` owns declarative `ProjectProfile`, `Skill` and fail-closed
  compatibility assessment. It does not infer executability.

## Telemetry

Telemetry is an outbound port, not a domain dependency.

- `ports/telemetry.py` defines immutable events, provenance, usage/cost sources
  and the `TelemetrySink` protocol.
- `telemetry/recorder.py` correlates by execution id, assigns monotonic
  sequences, redacts and bounds attributes, aggregates usage conservatively and
  contains sink failures.
- `adapters/telemetry/` provides only local in-memory and null sinks.
- The control-plane loop publishes routing, execution, retry, escalation,
  failure and closure facts from real decisions.
- The autonomy loop publishes agent, tool, quality, Git-attribution and closure
  facts from observed reports.
- Prompts, requirement text, outputs, command lines, evidence references,
  provider messages and credentials are not recorded.
- Unknown costs and token counts are absent, never zero.

The schema and privacy rules are documented in `docs/ai/S23_TELEMETRY.md`.

## Historical context

The package was extracted from the Shipii monorepo. Historical modules under
`scripts/`, host-only `features/agent_loop*.feature` files and the
`standalone/agent_loop/` vendored copy are **not** part of this architecture.
Their capabilities are classified in
`docs/migration/EXCLUDED_LEGACY_CAPABILITIES.md`.

`agent_loop/application/lifecycle.py`, `resume_*` and `state_graph.py` were
extracted from the legacy lifecycle surface. They are retained as tested
library contracts but are not used by the autonomous CLI and are not a durable
resume store. They must not be described as a finished orchestration runtime.

## Testing and quality

- Canonical tests live in `tests/agent_loop/`.
- Architecture tests enforce package boundaries and forbidden imports.
- Security tests cover path traversal, symlink escapes, environment isolation
  and executable resolution.
- Adapter tests use observed CLI fixtures plus explicit synthetic edge cases.
- Coverage, Ruff, Ruff format, Mypy and Pylint are configured in
  `pyproject.toml`.
- Pre-commit validates staged files, including file syntax, secrets, formatting,
  architecture boundaries and focused tests. `TESTING.md` documents the three
  quality levels.
- `scripts/run_quality_checks.py` runs the file-hygiene hooks over a
  filesystem-discovered file set, then Ruff, Ruff format, Mypy, Pylint, the
  architecture tests, the complete suite with the 99% coverage gate, the
  complete Behave suite, the Git whitespace checks and packaging with isolated
  installation.
- CI validates Python 3.12, 3.13 and 3.14, runs the complete quality runner,
  builds wheel and sdist, verifies `LICENSE` and required package members in
  both, installs the wheel in an isolated environment, exercises both installed
  entry points with `--version` and `--help` and runs `pip check`.
