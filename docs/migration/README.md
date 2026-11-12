# Migration record

This repository was extracted from the Shipii monorepo
(`nacho/shipii/src/agent_loop`) on 2026-11-10.

## Source topology

The source directory was a Git **worktree** of Shipii, not an independent
repository (`.git` was a file pointing at
`Shipii/.git/worktrees/agent_loop`).

The source had two relevant histories:

- `agent_loop-rebuild` — the authoritative rebuild branch used for this
  extraction. HEAD: `de91bf807f815e77f2e4cd3d8b32f0b4fc834265`.
- `agent_loop` — an earlier and divergent restructuring branch:
  - 1,248 commits reachable from its tip;
  - tip `33034293` (`refactor: Reestructurar agent_loop como paquete modular
    con límites de directorio reales`);
  - the tip is **not** an ancestor of `agent_loop-rebuild`;
  - the merge base is `3239b0fd`;
  - `agent_loop` is one commit ahead of that merge base;
  - `agent_loop-rebuild` contains 43 commits that are not in `agent_loop`.

`agent_loop` was not a single monolithic import commit. It is an alternative
divergent restructuring history and was not migrated as a separate ref.
`agent_loop-rebuild` is the authoritative history for this repository.

## Extraction strategy

1. Clone the source branch into an isolated temporary clone (no hardlinks).
2. Filter the history to the Agent Loop scope:
   `agent_loop/`, `tests/agent_loop/`, `docs/ai/S22_OPENCODE_SERVER_RESEARCH.md`,
   `docs/ai/S23_TELEMETRY.md`, and the standalone-owned documentation,
   packaging, entrypoint, requirements and fixture paths.
3. Move standalone-owned files to the repository root.
4. Remove the vendored package copy, transformed check copies, obsolete
   vendoring/layout gates and tests that existed only to verify Shipii-hosted
   `scripts/` wrappers.
5. Add root packaging, architecture documentation, quality configuration and CI
   in one explicit bootstrap commit.

No commit was invented to replace history. The 30 migrated commits keep their
original authors, committers, author/commit dates, messages and order. Their
hashes changed because the parent tree was filtered; `commit-map.tsv` records
the correspondence.

## Preserved history

- 30 commits touch the migrated scope and were preserved.
- The first preserved commit became a root commit because its original parent
  belongs to excluded Shipii history.
- The bootstrap commit is `cfa1e76`
  (`chore: bootstrap independent agent_loop repository`). It is a descendant of
  the 30 migrated commits and contains only repository-level packaging,
  documentation, quality configuration and test adjustments.
- No merge commits exist in the migrated range.
- No tags existed in the source. Stashes, checkpoint refs, remote branches and
  worktree metadata were not migrated.

## Excluded history

The source branch contained 1,290 commits. 1,260 were excluded:

- **1,126 Shipii-only commits** that do not touch the migrated scope.
- **134 legacy-only commits** that touch the host-owned legacy agent-loop
  subsystem under `scripts/`, `features/agent_loop*` and
  `tests/test_agent_loop*`/`tests/test_multi_requirement*`/
  `tests/test_requirement_*`. Those wrappers depend on Shipii modules and are
  not part of the canonical package.
- **11 commits touched both canonical code and legacy code.** Only the canonical
  part was preserved; the legacy part was excluded with the host subsystem.

The 134 legacy commits and the 113 legacy scripts are **not** imported by this
repository. Their capabilities are recorded in
`EXCLUDED_LEGACY_CAPABILITIES.md` so the extraction boundary remains explicit.

## Test-suite adjustments

Three test files were modified during extraction:

- `tests/agent_loop/application/test_lifecycle.py` — replaced imports from
  `scripts.agent_loop_models` with local contract fixtures.
- `tests/agent_loop/autonomy/test_cli.py` — removed the vendored
  `standalone/run.py` lookup and pointed the subprocess test at the repository
  `run.py`.
- `tests/agent_loop/cli/test_arguments.py` — removed parity assertions against
  the legacy `scripts.*` parser during extraction; the file and the legacy
  parser it tested were later removed as dead code (see below).

Two test files were deleted:

- `tests/agent_loop/application/test_compatibility_ownership.py` — it only
  asserted that `scripts.*` modules and canonical modules were the same object.
  There is no `scripts.*` package in this repository.
- `tests/agent_loop/autonomy/test_standalone_layout.py` — it verified the
  vendored `standalone/agent_loop/` copy and the transformed
  `standalone/checks/` suite, both intentionally removed. Its valuable
  independence E2E intent was restored in
  `tests/agent_loop/autonomy/test_standalone_independence.py`, which copies the
  real package to an isolated directory and runs discovery, dry-run,
  validate-only, `--version` and `--help` without the Shipii checkout.

The transformed `standalone/checks/` suite was excluded entirely. It was a
duplicate transform of the canonical tests and would have created a second
source of truth.

## Post-migration corrections

The extraction audit identified additional corrections applied on `main` after
the bootstrap commit:

- Restored ADR-056 verbatim and renumbered the historical duplicate autonomy
  ADR-039 to ADR-057.
- Removed two orphaned legacy contracts with no canonical consumers:
  `application/requirement_execution_gate_models.py` and
  `application/requirement_stage2_exclusion_policy.py`.
- Removed the unconnected legacy parser `agent_loop/cli/` and its tests. It
  exposed more than 60 options with no executable implementation and was not
  the `agent-loop` entry point.
- Renamed functional provider variables from `SHIPII_*` to
  `AGENT_LOOP_CURSOR_*` and `AGENT_LOOP_CODEX_COMMAND`. Security denylist
  entries for `SHIPII_*` remain intentionally.
- Made Python 3.14 a first-class development and CI environment while keeping
  Python 3.12 as the minimum supported version. The minimum is justified by the
  use of PEP 695 `type` aliases, which require Python 3.12. The suite and
  coverage gate were validated on Python 3.12, 3.13 and 3.14.
- Updated the Ruff pin from 0.12.9 to 0.17.4. Ruff 0.17.4 reported 14 real
  findings (union ordering, string concatenation, regex escaping and one unused
  directive); the findings were fixed and no rule was disabled to obtain a pass.
  Ruff `target-version` remains `py312` to enforce the declared minimum.
- Pinned the development toolchain exactly in `requirements-dev.txt` and the
  `dev` optional dependency group. No lockfile is used; runtime dependencies
  keep a compatible lower bound.
- Replaced the inherited license attribution with the confirmed owner notice
  `Copyright (c) 2026 Javier Marcon`, declared MIT through the PEP 639
  `license` and `license-files` metadata, and verified that the wheel and sdist
  include `LICENSE`.

## Validation

Final repository checks for this correction pass:

- every pre-commit hook over all files;
- the canonical pytest suite on Python 3.12, 3.13 and 3.14;
- coverage gate (99%);
- the complete Behave E2E suite and heavy-request enforcement;
- Ruff check and Ruff format check;
- Mypy on package, tests and repository quality tooling;
- Pylint on package, tests and repository quality tooling;
- `git diff --check`;
- wheel and sdist build;
- isolated wheel installation, installed MIT metadata, installed import and
  both installed entry points;
- `pip check` in the isolated environment;
- `LICENSE` inclusion in wheel and sdist;
- the independence E2E that runs without the Shipii checkout.

OpenCode Server E2E tests that require
`AGENT_LOOP_E2E_OPENCODE_SERVER=1` are environment-gated and were not executed
as part of the default validation.

## Privacy and legal notes

### Author email in history

The migrated commits contain a personal author/committer email address
(`javiermarcon@gmail.com`). The commits were not rewritten. Changing author
metadata would require rewriting history and is a privacy decision reserved for
the repository owner.

### LICENSE attribution

`LICENSE` is the standard MIT license text. The inherited copyright line
attributed `Ignacio Fernandez Garcia`; the host file history also shows an
earlier attribution to `Franco Loncarica`. The migrated AgentLoop commits are
authored by `Javier Marcon`.

The owner of the independent AgentLoop repository and code is **Javier
Marcon**, and the definitive notice is:

```text
Copyright (c) 2026 Javier Marcon
```

The migrated Git history was not rewritten; author and committer metadata of
the migrated commits remain unchanged. The attribution change applies to the
distribution license, not to historical commit authorship.
