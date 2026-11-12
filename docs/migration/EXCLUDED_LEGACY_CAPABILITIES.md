# Excluded legacy capabilities

This document records the legacy Shipii Agent Loop capabilities that were
**not** migrated into the independent AgentLoop repository, and the small parts
that were represented by a different canonical mechanism.

It is a boundary document, not a roadmap commitment. The 134 legacy-only commits
and the 113 legacy scripts were not imported. Do not copy legacy
implementations into this repository to close a difference by appearance; a
future capability needs its own design, tests and architectural decision.

All paths in the "Legacy evidence" sections are relative to the Shipii source
tree at `de91bf807f815e77f2e4cd3d8b32f0b4fc834265` and are **not** present in
this distribution.

## Classification vocabulary

| Classification | Meaning |
|---|---|
| **Migrated and replaced** | A canonical implementation now owns the responsibility, possibly with a different design. |
| **Partially represented** | A canonical mechanism covers part of the contract, but the legacy capability is not fully available. |
| **Excluded: host dependency** | The capability depends on Shipii modules, services, data or conventions. |
| **Excluded: initial scope** | The capability is intentionally outside the first independent release. |
| **Pending redesign** | The capability may be relevant later, but needs a new design for an independent AgentLoop. |

## 1. Multi-requirement planning and execution

**Classification:** partially represented; the execution composition is
excluded by host dependency and pending redesign.

**Canonical representation:**

- `agent_loop/domain/work_item.py` — control-plane unit of work.
- `agent_loop/domain/work_item_plan.py` — deterministic DAG validation and
  READY/BLOCKED/DONE planning.
- `agent_loop/application/execution_request_builder.py` — schedulable
  WorkItem gate.
- `agent_loop/application/planned_execution_requests.py` — plan-to-request
  bridge.
- Tests under `tests/agent_loop/domain/` and
  `tests/agent_loop/application/`.

**Not migrated:** multi-requirement document segmentation, audience selection,
unit budgets, cross-worktree units, hierarchical run composition, manual-step
policies, durable unit execution, automatic continuation and summary/report
composition. The canonical WorkItem DAG is a control-plane contract, not the
legacy MultiRunner.

**Legacy evidence:**

- `scripts/multi_requirement_mode.py`
- `scripts/multi_requirement_mode_planning.py`
- `scripts/multi_requirement_mode_composition.py`
- `scripts/multi_requirement_mode_initialization.py`
- `scripts/multi_requirement_run.py`
- `scripts/multi_requirement.py`
- `scripts/single_requirement_mode.py`
- `features/agent_loop_multi_requirement.feature`
- `tests/test_multi_requirement*.py`

**Reason:** the legacy runner is a host composition root coupled to Shipii
tooling, state, quality runners and `_al` facades. Reimplementing it inside the
independent package without a new design would reintroduce that coupling.

## 2. Governor

**Classification:** excluded by host dependency and initial scope; pending
redesign.

**Canonical representation:** none equivalent. The autonomous loop has a
deterministic retry/failure policy in
`agent_loop/autonomy/execution/runner.py` and
`agent_loop/autonomy/tools/failure.py`, and the control plane has bounded
recovery in `agent_loop/application/recovery_policy.py`. These are **not** the
legacy gate governor.

**Legacy evidence:**

- `scripts/agent_loop_governor.py`
- `scripts/scope_aware_gate_governor.py`
- `scripts/gate_decision_precedence.py`
- `scripts/gate_failure_classification.py`
- `scripts/gate_failure_kind.py`
- `tests/test_agent_loop_governor.py`

**Reason:** the governor was tied to legacy gate identities, scope review,
mutation budgets and host quality-runner semantics.

## 3. Quality gates

**Classification:** partially represented.

**Canonical representation:**

- `agent_loop/autonomy/discovery/tools.py` — tool inventory.
- `agent_loop/autonomy/tools/adapters.py` — Ruff, Mypy, Pylint, Bandit,
  pytest, Behave, pre-commit, Git and Docker Compose adapters.
- `agent_loop/autonomy/execution/runner.py` — validation reports and retry
  decisions.
- `agent_loop/autonomy/tools/failure.py` — failure classification.

**Not migrated:** gate registry and applicability policy, gate handshake,
streaming quality process management, test-database diagnostics, pre-commit
session management, quality cleanup and the legacy gate evidence model.

**Legacy evidence:**

- `scripts/run_quality_checks.py`
- `scripts/agent_loop_quality.py`
- `scripts/quality_gate_registry.py`
- `scripts/quality_gate_applicability.py`
- `scripts/quality_runner_handshake.py`
- `scripts/quality_runner_capabilities.py`
- `scripts/quality_process_cleanup.py`
- `scripts/quality_streaming_command.py`
- `scripts/quality_test_database_diagnostics.py`
- `tests/test_agent_loop_quality*.py`

**Reason:** the reusable validation primitives were migrated; the host gate
composition was not.

## 4. Execution scope

**Classification:** partially represented.

**Canonical representation:**

- `agent_loop/autonomy/security.py` — workspace and path containment.
- `--target-path` in `agent_loop/autonomy/cli.py` — instruction and Git-audit
  scope.
- `agent_loop/autonomy/discovery/git_attribution.py` — unexpected-path
  detection.
- Tests in `tests/agent_loop/autonomy/test_security.py`,
  `test_git_attribution.py` and `test_cli.py`.

**Not migrated:** explicit/fenced scope contracts, scope expansion guards,
scope-aware staging, mutation enforcement, negative scope, package scope
consistency and cross-worktree scope policy.

**Legacy evidence:**

- `scripts/requirement_execution_scope.py`
- `scripts/requirement_explicit_scope_contract.py`
- `scripts/requirement_fenced_scope.py`
- `scripts/requirement_scope_*.py`
- `scripts/scope_mutation_enforcement.py`
- `scripts/structural_scope.py`
- `features/agent_loop_scope_quality_isolation.feature`
- `tests/test_requirement_scope*.py`

**Reason:** `--target-path` reports out-of-scope changes after execution; it is
not the legacy pre-execution scope authority.

## 5. Persistence, run store and plan logs

**Classification:** excluded by host dependency and initial scope; pending
redesign.

**Canonical representation:** pure, tested contracts only:

- `agent_loop/application/lifecycle.py`
- `agent_loop/application/resume_*.py`
- `agent_loop/application/state_graph.py`
- Tests under `tests/agent_loop/application/`.

These modules do not provide a durable database, run store or plan-log writer,
and they are not wired into the autonomous CLI.

**Not migrated:** run-state repository, state recovery, migrations, physical
invariants, plan logs, planner hash, unit codec, run inspection CLI and durable
resume ownership.

**Legacy evidence:**

- `scripts/state_repository.py`
- `scripts/state_recovery.py`
- `scripts/state_migrations.py`
- `scripts/state_models.py`
- `scripts/state_invariants.py`
- `scripts/state_physical_invariants.py`
- `scripts/state_transition_service.py`
- `scripts/multi_requirement_plan_logs.py`
- `scripts/multi_requirement_planner_hash.py`
- `scripts/multi_requirement_unit_codec.py`
- `scripts/inspect_run_state.py`
- `tests/test_multi_requirement_plan_logs.py`
- `tests/test_requirement_durable_declarations.py`

**Reason:** the legacy store is coupled to Shipii paths, state layout and
MultiRunner semantics. A standalone run store requires a new persistence
decision.

## 6. Auth recovery

**Classification:** excluded by host dependency and initial scope.

**Canonical representation:** none. Provider discovery adapters report
authorization evidence read-only; they do not recover authentication.

**Legacy evidence:**

- `scripts/agent_auth_classify.py`
- `scripts/agent_auth_cli.py`
- `scripts/agent_auth_codex.py`
- `scripts/agent_auth_cursor.py`
- `scripts/agent_auth_models.py`
- `scripts/agent_auth_preflight.py`
- `scripts/agent_auth_redaction.py`
- `scripts/agent_auth_registry.py`
- `scripts/agent_auth_retries.py`
- `scripts/agent_auth_strategy.py`
- `scripts/agent_auth_terminal.py`
- `features/agent_loop_auth_recovery.feature`
- `tests/test_agent_auth*.py`

**Reason:** recovery invoked interactive provider login flows and host-specific
credential handling. The independent package must not automate credentials.

## 7. Discovery artifacts and freshness

**Classification:** excluded by host dependency and initial scope; pending
redesign.

**Canonical representation:** deterministic discovery happens in memory during
each run (`agent_loop/autonomy/discovery/`). There is no artifact publication or
freshness store.

**Not migrated:** discovery artifact bundles, manifests, integrity checks,
transactional evidence, completion freshness, budget state, publication
rejection and discovery result services.

**Legacy evidence:**

- `scripts/discovery_artifact_bundle.py`
- `scripts/discovery_artifact_integrity.py`
- `scripts/discovery_artifact_manifest.py`
- `scripts/discovery_artifact_models.py`
- `scripts/discovery_completion_evaluator.py`
- `scripts/discovery_result_service.py`
- `scripts/discovery_transactional_*.py`
- `scripts/requirement_completion_freshness.py`
- `scripts/requirement_completion_inventory.py`
- `features/agent_loop_discovery_artifact_publication.feature`
- `features/agent_loop_completion_freshness.feature`
- `features/agent_loop_discovery_budget_state.feature`
- `tests/test_discovery_*.py`

**Reason:** the legacy artifact system was durable, host-aware and coupled to
transactional publication semantics that do not exist in this distribution.

## 8. Publication

**Classification:** excluded by host dependency and initial scope.

**Canonical representation:** none. Git changes are observed and reported; no
artifact or branch publication is performed.

**Legacy evidence:**

- `scripts/discovery_artifact_publication.py`
- `scripts/discovery_publication_rejection.py`
- `scripts/discovery_worktree_publication.py`
- `scripts/requirement_artifact_mutation_policy.py`
- `scripts/requirement_artifact_path_policy.py`
- `scripts/requirement_artifact_provenance.py`
- `scripts/requirement_artifact_record_resolution.py`
- `scripts/requirement_artifact_record_tokens.py`
- `features/agent_loop_dependency_dag_artifact_scope.feature`
- `tests/test_requirement_artifact_*.py`

**Reason:** publication is a separate write-authority capability with host
filesystem and Git-worktree assumptions. Importing it would conflict with the
read/audit-only posture of the current extraction.

## 9. Reference path authority and read-only enforcement

**Classification:** partially represented.

**Canonical representation:**

- `agent_loop/autonomy/security.py` — workspace resolution, symlink escape
  rejection, executable checks.
- Discovery adapters are read-only and execute no mutating CLI commands.
- Tests in `tests/agent_loop/autonomy/test_security.py`,
  `test_executable_security.py` and adapter discovery tests.

**Not migrated:** reference path authority, requirement write authority,
read-only reference validation, artifact mutation policy and discovery
transactional Git isolation.

**Legacy evidence:**

- `scripts/reference_path_authority.py`
- `scripts/requirement_write_authority.py`
- `scripts/requirement_worktree_access.py`
- `scripts/requirement_cross_worktree_policy.py`
- `features/agent_loop_reference_authority.feature`
- `tests/test_requirement_discovery_root_authority.py`
- `tests/test_requirement_reference_*.py`

**Reason:** the canonical package enforces containment and read-only discovery,
but it does not implement the full legacy reference-authority model.

## 10. The ten legacy feature contracts

The source contains ten `features/agent_loop_*.feature` files. They are
contract documentation for the legacy host subsystem, not executable tests of
the independent package.

| Feature file | Classification | Canonical relationship |
|---|---|---|
| `agent_loop_auth_recovery.feature` | Excluded: host dependency | No credential recovery; adapters report auth evidence only. |
| `agent_loop_completion_freshness.feature` | Excluded: initial scope | No durable completion/freshness store. |
| `agent_loop_dependency_dag_artifact_scope.feature` | Partially represented | WorkItem DAG planning exists; artifact publication and scope policy do not. |
| `agent_loop_discovery_artifact_publication.feature` | Excluded: initial scope | In-memory discovery only. |
| `agent_loop_discovery_budget_state.feature` | Excluded: initial scope | No discovery budget state machine. |
| `agent_loop_discovery_completion.feature` | Partially represented | Deterministic discovery exists; completion/publication lifecycle does not. |
| `agent_loop_multi_requirement.feature` | Partially represented | WorkItem planning/gating exists; MultiRunner execution does not. |
| `agent_loop_reference_authority.feature` | Partially represented | Workspace containment exists; reference-path authority does not. |
| `agent_loop_scope_quality_isolation.feature` | Partially represented | Target paths and Git audit exist; scope-enforced staging does not. |
| `agent_loop_semantic_closure.feature` | Excluded: initial scope | No requirement semantic closure engine. |

Legacy step definitions under `features/steps/agent_loop_*_steps.py` are host
test infrastructure and were not migrated.

## 11. Orphan contracts removed from the canonical package

Two migrated modules had no canonical importer, no consumer and 0% coverage:

- `agent_loop/application/requirement_execution_gate_models.py` — durable
  `ExecutionGate` / `DiscoveryTransition` JSON contracts for the legacy
  discovery-to-mutation transition.
- `agent_loop/application/requirement_stage2_exclusion_policy.py` — exclusion
  and folder-shape policy for generated Stage2 requirement packages.

They were part of the legacy multi-requirement discovery pipeline. Their only
consumers were `scripts.*` modules and legacy tests that are not part of this
distribution. No canonical implementation consumes them, and keeping them as an
undocumented library API would imply a capability that AgentLoop does not
provide. They were removed, and their responsibilities are recorded in this
manifest as excluded/pending redesign. The deterministic `WorkItem` DAG and
request gate remain the canonical planning contracts.

**Legacy evidence:**

- `scripts/requirement_execution_gate_models.py`
- `scripts/requirement_stage2_exclusion_policy.py`
- `tests/test_requirement_discovery_execution_gate.py`
- `tests/test_requirement_discovery_execution_gate_transition.py`
- `tests/test_requirement_artifact_policy_modules.py`
- `tests/test_monetary_closure_stage2_completion.py`

## 12. Other legacy surfaces

The following legacy surfaces are also intentionally absent:

- **Requirement semantics:** Markdown block parsing, audience roles, metadata
  invariants, semantic lints, clause extraction, epics and dependency graphs
  (`scripts/requirement_*.py`, `scripts/single_requirement_composition.py`).
- **Prompt enhancement and Notion ingress:** `scripts/prompt_enhancer.py`,
  `scripts/agent_loop_prompts.py`, Notion download and clipboard sources.
- **Legacy command wrappers:** `scripts/agent_loop.py`,
  `scripts/agent_loop_models.py`, `scripts/agent_loop_process.py` and the
  remaining host facades.
- **Host quality/CI tooling:** Shipii's architecture audits, Docker pre-commit
  wrappers, documentation generators, Railway and S3 utilities. AgentLoop has
  its own minimal quality tooling under `scripts/`, driven by
  `.pre-commit-config.yaml`, `TESTING.md` and `.github/workflows/ci.yml`; it
  does not reuse the Shipii host wrappers.
- **Legacy test suite:** the root `tests/test_agent_loop*.py`,
  `tests/test_multi_requirement*.py`, `tests/test_requirement_*.py` and the
  transformed `standalone/checks/` suite.

## What was migrated instead

The independent package keeps the reusable, host-neutral parts:

- target workspace security and path containment;
- instruction and Django discovery;
- tool inventory, command execution and validation adapters;
- prompt transport and agent invocation;
- structured reports and Git attribution;
- capability, task, model, WorkItem and fault contracts;
- deterministic routing, request gating and bounded recovery;
- OpenCode CLI, Claude CLI and OpenCode Server adapters;
- project profiles/skills and telemetry.

This is the real scope of the extraction. A capability is not considered
migrated merely because a similarly named contract exists.
