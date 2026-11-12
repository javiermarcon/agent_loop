# agent_loop/AGENTS.md — AgentLoop core contract

This file refines the root `AGENTS.md` for `agent_loop/**`.

## Ownership

`agent_loop/` is the canonical core and the only implementation source.

- There are no legacy `scripts/agent_loop*.py`,
  `scripts/multi_requirement*.py` or `scripts/requirement_*.py` owners in this
  repository.
- Core packages must not import host compatibility wrappers, `scripts`, `apps`
  or Django.
- Host-specific process, Docker and quality-runner integration belongs behind
  the explicit `autonomy/` or adapter boundaries.

## Entry points

- `agent_loop.autonomy.cli:main` is the only CLI entry point.
- `agent_loop.application.execution_orchestrator` is a library-level control
  plane API, not the autonomous CLI.
- Do not add a second parser or expose options that have no executable
  implementation.

## Refactor discipline

- Refactor one bounded capability at a time.
- Preserve public/runtime compatibility with small delegators, not duplicated
  implementations.
- Do not mechanically move a giant module to another package and call it a
  refactor.
- Do not split by arbitrary `part1`/`part2`, `utils`, `helpers` or catch-all
  modules.
- Do not create dozens of modules simply to reduce line counts; each extraction
  needs a stable responsibility and clear owner.
- Do not start the next capability while the current slice has deterministic
  static/test failures.

## Typing rules specific to agent_loop

Forbidden repair techniques:

- local `.pyi` shadow files for internal wrappers/modules;
- `Any`-returning string-based service locators (`getattr` by arbitrary name)
  used to evade typing or dependency ownership;
- mass `TYPE_CHECKING`/re-export shims whose only purpose is to silence Mypy;
- blanket `noqa`, `type: ignore` or `pylint: disable`.

Use concrete typed collaborators. A `Protocol` is appropriate only at a real
boundary with a stable contract; do not manufacture one for every extracted
helper.

## Quality-tool firewall

A refactor under `agent_loop/**` must not modify Ruff, Mypy, Pylint, coverage or
architecture tests to make itself pass. If a tool defect is independently
proven, handle it as a separate task with explicit evidence.

## Fast fail

After each coherent extraction:

1. parse/import check;
2. targeted Ruff;
3. targeted Ruff format check;
4. targeted Mypy;
5. targeted Pylint;
6. targeted architecture tests;
7. focused pytest when behavior is affected.

Before a pull request, run `python scripts/run_quality_checks.py`. The complete
gate and CI are the only evidence of repository-wide green; a local pre-commit
run validates the staged change, not the whole repository.

Any newly introduced deterministic failure blocks further extraction. If the
change causes a large cascade of typing/import failures, re-plan that slice
rather than repairing unrelated modules.

## Compatibility

Compatibility wrappers, when they exist, must be small delegators to a canonical
owner. Preserve runtime identity or monkeypatch seams only when current tests or
callers demonstrate that the identity is part of the contract; do not infer such
a contract and build a stub layer around it without evidence.
