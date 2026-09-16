# agent_loop/AGENTS.md — Agent-loop core contract

This file refines the root `AGENTS.md` for `agent_loop/**`.

## Ownership

`agent_loop/` is the canonical core. Legacy `scripts/agent_loop*.py`,
`scripts/multi_requirement*.py`, and `scripts/requirement_*.py` may be thin
entrypoints/compatibility wrappers or host adapters; they are not alternate owners of core
policy.

Core packages must not import legacy compatibility wrappers. Host-specific process/Docker/
quality-runner integration belongs behind the existing project/adapter boundary.

## Refactor discipline

- Refactor one bounded capability at a time.
- Preserve public/runtime compatibility with thin facades, not duplicated implementations.
- Do not mechanically move a giant module to another package and call it a refactor.
- Do not split by arbitrary `part1`/`part2`, `utils`, `helpers`, or catch-all modules.
- Do not create dozens of modules simply to reduce line counts; each extraction needs a
  stable responsibility and clear owner.
- Do not start the next capability while the current slice has deterministic static/test
  failures.

## Typing rules specific to agent_loop

Forbidden repair techniques:

- local `.pyi` shadow files for internal wrappers/modules;
- `Any`-returning string-based service locators (`getattr` by arbitrary name) used to evade
  typing or dependency ownership;
- mass `TYPE_CHECKING`/re-export shims whose only purpose is to silence Mypy;
- blanket `noqa`, `type: ignore`, or `pylint: disable`.

Use concrete typed collaborators. A `Protocol` is appropriate only at a real boundary with
a stable contract; do not manufacture one for every extracted helper.

## Quality-tool firewall

A refactor under `agent_loop/**` must not modify pre-commit, Mypy, Ruff, Pylint, coverage,
architecture thresholds, baselines, audit runners, or compatibility-stub generation to make
itself pass. If a tool defect is independently proven, handle it as a separate task.

## Fast fail

After each coherent extraction:

1. parse/import check;
2. targeted Ruff;
3. targeted Mypy using the canonical wrapper;
4. targeted architecture audit;
5. focused pytest/Behave when behavior is affected.

Any newly introduced deterministic failure blocks further extraction. If the change causes a
large cascade of typing/import failures, revert/re-plan that slice rather than repairing
unrelated modules.

## Compatibility

Compatibility wrappers should normally be small delegators to a canonical owner. Preserve
runtime identity/monkeypatch seams only when current tests or callers demonstrate that the
identity is part of the contract; do not infer such a contract and build a stub layer around
it without evidence.
