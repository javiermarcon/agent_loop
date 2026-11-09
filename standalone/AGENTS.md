# AGENTS.md — Agent Loop autonomous edition

This file governs work inside the standalone `agent-loop` distribution. It is
intentionally independent from the Shipii repository instructions.

## Purpose

`agent_loop` is an autonomous control plane for external Django repositories.
It discovers the target project, reads its instructions, inventories its tools,
invokes a user-authorized external agent, and validates the result inside the
target repository.

## Hard boundaries

- `agent_loop/autonomy/` must not import `scripts`, `apps` or `django`.
- `agent_loop/domain/` and `agent_loop/ports/` must remain independent from
  adapters, CLI, subprocess and provider SDKs.
- Do not hardcode Shipii paths, environment variables, service names or
  commands.
- Do not use the Agent Loop interpreter to run target tools. Use the target
  project's virtualenv.
- Do not add support for non-Django ecosystems in this stage.
- OpenCode Server integration (S22) is vendored from the canonical package; do not
  modify or reimplement it independently here.

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
  isolated or contained by Agent Loop.

## Change discipline

- Make the smallest coherent change.
- Preserve existing public contracts unless the requirement changes them.
- Do not add speculative abstractions. Add a Protocol only at a real boundary.
- Do not duplicate logic between discovery and adapters.
- Keep modules small and cohesive.

## Tests

- Add focused pytest coverage for every new behavior.
- Cover success, failure, unavailable, unknown and security paths.
- Do not weaken or delete existing tests.
- Run `pytest` from the standalone root before claiming completion.

## Quality

- Run Ruff, Ruff format, Mypy and Pylint on changed files.
- Do not modify quality configuration to make a patch pass.
- Do not use blanket `# noqa`, `# type: ignore` or `pylint: disable`.

## Vendoring

`standalone/agent_loop/` is a vendored snapshot of the canonical `agent_loop/`
package. Make production changes in the canonical tree first, then follow
`VENDORING.md` to refresh the snapshot. Do not edit the snapshot as an
independent source of truth.

## Documentation

Update `README.md` when installation, usage, discovery, tools, security or
limitations change. Keep the architecture section synchronized with real
modules.
