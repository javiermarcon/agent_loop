# Migration record

This repository was extracted from the Shipii monorepo (`nacho/shipii/src/agent_loop`)
on 2026-11-10.

## Source topology

- The source directory was a Git **worktree** of Shipii, not an independent
  repository (`.git` was a file pointing at `Shipii/.git/worktrees/agent_loop`).
- Source branch: `agent_loop-rebuild`, HEAD `de91bf807f815e77f2e4cd3d8b32f0b4fc834265`.
- The worktree contained the full Shipii checkout; Agent Loop code lived in
  `agent_loop/` and `tests/agent_loop/`, with a vendored copy in `standalone/`.

## Extraction strategy

1. Clone the source branch into an isolated temporary clone (no hardlinks).
2. Run `git filter-repo` keeping only the Agent Loop paths:
   `agent_loop/`, `tests/agent_loop/`, `docs/ai/S22_OPENCODE_SERVER_RESEARCH.md`,
   `docs/ai/S23_TELEMETRY.md`, and the standalone-owned documentation, packaging,
   entrypoint, requirements, and fixture paths.
3. Move standalone-owned files to the repository root.
4. Remove the vendored package copy, transformed check copies, obsolete
   vendoring/layout gates, and tests that existed only to verify Shipii-hosted
   `scripts/` wrappers.
5. Add root packaging, architecture documentation, quality configuration, and CI
   in a single explicit bootstrap commit.

No commit was invented to replace history. The 30 migrated commits keep their
original authors, committers, author/commit dates, messages, and order. Their
hashes changed because the parent tree was filtered; `commit-map.tsv` records
the correspondence.

## Scope boundaries

- 1,290 commits existed on the source branch; 30 touch the migrated scope and
  were preserved. The first preserved commit became a root commit because its
  original parent belongs to excluded Shipii history.
- 1,260 commits were excluded: 1,126 Shipii-only commits and 134 commits that
  touch the host-owned legacy agent-loop subsystem under `scripts/`,
  `features/agent_loop*`, and `tests/test_agent_loop*`. Those legacy wrappers
  depend on Shipii modules and are not part of the canonical package.
- The source branch `agent_loop` contained a single monolithic import commit and
  is not migrated as a separate ref; `agent_loop-rebuild` is the authoritative
  history.
- No tags existed in the source. Stashes, checkpoint refs, remote branches, and
  worktree metadata were not migrated.
- No merges exist in the migrated range.

## Validation

See the migration report delivered with this task. Final repository checks:

- `pytest`: 1,142 passed, 2 skipped (environment-gated OpenCode Server E2E).
- Coverage: 92.63% (gate: 92%).
- Ruff, Ruff format, Mypy (package + tests), and Pylint (package + tests): pass.
- Wheel and sdist build; wheel installs and exposes `agent-loop` in an isolated
  virtual environment.
