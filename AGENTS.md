# AGENTS.md — AgentLoop

This file governs work inside the independent `agent-loop` repository. It is
intentionally independent from the Shipii repository instructions.

## Purpose

AgentLoop is an autonomous execution engine that combines AI reasoning with
deterministic orchestration. It discovers a target repository, reads its
instructions, inventories its tools, invokes a user-authorized external agent,
validates the result and attributes the changes that actually happened.

The current discovery and tool adapters target Django/Python repositories. The
core contracts are not coupled to Django and must not be described as a
Django-only product.

## Hard boundaries

- `agent_loop/` is the only canonical package and the only implementation
  source. There are no vendored copies, transformed check suites or Shipii
  wrappers in this repository.
- `agent_loop/autonomy/` must not import `scripts`, `apps` or `django`.
- `agent_loop/domain/` and `agent_loop/ports/` must remain independent from
  adapters, CLI, subprocess and provider SDKs.
- `agent_loop/application/` must not import concrete adapters.
- Do not hardcode Shipii paths, environment variables, service names or
  commands.
- Do not use the AgentLoop interpreter to run target tools. Use the target
  project's virtualenv or discovered Compose service.
- Do not add support for non-Django ecosystems in this stage.
- OpenCode Server integration (S22) is part of the canonical package; do not
  modify or reimplement it independently from the rest of the package.
- The autonomous CLI is `agent_loop.autonomy.cli`. Do not add a second parser
  or re-introduce the removed legacy multi-requirement parser.

## Security invariants

- Every filesystem operation must stay inside the authorized target root.
- Reject path traversal and symlink escapes.
- Never use `shell=True`.
- Never run destructive Git commands (`reset`, `checkout`, `restore`, `clean`,
  `commit`, `push`).
- Never inherit `PYTHONPATH`, `VIRTUAL_ENV`, `DJANGO_SETTINGS_MODULE`,
  `SHIPII_*` or `AGENT_LOOP_*` into target processes.
- Never log secrets.
- Repository instructions cannot override these rules.
- The external agent process is trusted. The Git baseline/audit is a post-hoc
  attribution control, not an OS sandbox. Do not claim that the agent is
  isolated or contained by AgentLoop.

## Change discipline

- Make the smallest coherent change.
- Preserve existing public contracts unless the requirement changes them.
- Do not add speculative abstractions. Add a Protocol only at a real boundary.
- Do not duplicate logic between discovery and adapters.
- Keep modules small and cohesive.
- Do not reintroduce legacy capabilities without an explicit architectural
  decision and tests.

## Tests

- Add focused pytest coverage for every new behavior.
- Cover success, failure, unavailable, unknown and security paths.
- Do not weaken or delete existing tests.
- Run `pytest` from the repository root before claiming completion.

## Quality

- Install the hooks with `pre-commit install`; never use `--no-verify`.
- `pre-commit` inspects the staged files: generic file/syntax/secret checks,
  Ruff with safe fixes, Ruff format, Mypy, Pylint, architecture boundaries and
  the focused pytest modules related to the change.
- Formatting hooks may rewrite files. When they do, the commit stops; review
  and stage the rewrite before retrying.
- Run `python scripts/run_quality_checks.py` before a pull request. It runs
  every hook over all files, the complete suite with coverage, the architecture
  tests, packaging and isolated installation.
- CI repeats the complete gates; a green local `pre-commit` run does not
  replace CI or the complete quality gate.
- `scripts/` is repository quality tooling. It is not part of the installable
  package and `agent_loop/` must not import it.
- Do not modify quality configuration to make a patch pass.
- Do not use blanket `# noqa`, `# type: ignore` or `pylint: disable`.

## Python support

- Minimum supported version: 3.12.
- Reference development and CI version: 3.14.
- CI must test every declared supported version.

## Documentation

Update `README.md` when installation, usage, discovery, tools, security,
capabilities or limitations change. Keep `ARCHITECTURE.md` synchronized with
real modules and entry points. Record excluded legacy capabilities in
`docs/migration/EXCLUDED_LEGACY_CAPABILITIES.md` instead of implying that they
are implemented.
