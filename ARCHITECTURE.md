# ARCHITECTURE

Agent Loop architecture. This section was extracted verbatim from the Shipii
monorepo during the independent-repository migration; references to host-only
paths (`scripts/`, `REPO_MAP.md`) describe the historical repository and are not
part of this distribution.

## Agent-loop Package Boundaries

El tooling de orquestación se implementa en `agent_loop/`, no en la carpeta
plana `scripts/`. `cli` y los adapters de proveedor apuntan a `application`;
application coordina `planning`, `quality`, `persistence`, `scope`,
`semantics` y `discovery`. El dominio es el centro independiente y no puede
importar Django, subprocess, Git, Docker, CLIs de agentes ni `scripts.*`.
`agent_loop/domain/capabilities.py` owns the pure effective-capability
vocabulary (requirements, snapshots, inventory); runtime probing and harness
adapters must not leak into domain. `agent_loop/domain/task_assessment.py`
owns deterministic `TaskAssessment` classification and capability-requirement
derivation from declared task signals (no discovery, no model/harness
selection). Floors may only raise complexity/risk/uncertainty; `UNKNOWN`
impact is not treated as absence. `agent_loop/domain/model_profiles.py` owns
logical `ModelProfile` and runtime-declared `RuntimeModelProfile` associations
(identity only; no selection, availability, auth, or capability inference).
`agent_loop/domain/model_profile_catalog.py` owns immutable
`ModelProfileCatalog` / `RuntimeModelProfileCatalog` (unique logical ids,
explicit duplicate/conflict rejection, deterministic enumeration; no discovery
or selection).
`agent_loop/domain/compatible_candidates.py` owns deterministic
`resolve_compatible_candidates()`: intersects `TaskAssessment.required_capabilities`
with `RuntimeInventory` via `is_effective`, gated by catalogs and
`native_model_ref` inventory membership (no execution, discovery, auth, or
capability inference).
`agent_loop/application/execution_routing.py` owns operational S13 selection
(`select_routing`, `RoutingConstraints`, `RuntimeReadinessPolicy`,
`RoutingDecision`). It consumes ADR-046 candidates and requires an explicit
per-runtime readiness capability with mandatory axes (for example supported,
available, healthy, authorized). `CONFIGURED`/compatibility never implies
availability; missing policies, missing snapshots, and `UNKNOWN`/`NO` axes
reject the candidate. Selection is stable by `(runtime_id, profile_id)`, honors
explicit exclusions/required/forbidden runtimes for recovery escalation, and
returns the selected runtime/model/strategy plus eligible candidates,
capability evidence, and rejection reasons. An empty selection is terminal; no
fallback is invented. Strategy is a control-plane input carried into the
decision, not inferred from model names (ADR-054).
`agent_loop/domain/faults.py` owns the neutral S14 failure vocabulary
(`FailureCategory`, `ExecutionState`, `ExecutionFailure`,
`FailureClassification`, `classify_failure`). Failure classification is
provider-neutral: unknown execution state is never recoverable, and a
non-idempotent operation may not be repeated automatically when execution
started or effects may exist.
`agent_loop/application/recovery_policy.py` owns `RecoveryLimits`,
`RecoveryContext`, `RecoveryAction`, and `decide_recovery`: bounded retry of the
same combination, model escalation, runtime escalation, human intervention, or
explicit terminal closure. `agent_loop/application/execution_orchestrator.py`
owns the bounded control-plane loop (`execute_work_item_with_recovery`) that
composes S13 routing, the S16 request gate, an injected AgentRuntime, and S14
recovery. Escalation re-enters S13 with attempted-combination exclusions and
runtime scope; it never selects a model outside the router. Attempt history
(routing, request, result, classification, recovery, evidence) is preserved
in-memory; durable resume/persistence is not part of S14 (ADR-055).
`agent_loop/domain/work_item.py` owns the control-plane `WorkItem` contract
(identity, objective, semantic status, opaque external/context refs,
dependency readiness via `is_schedulable`, execution handoff text); no Notion
SDK, harness CLI, or MultiRunner coupling.
`agent_loop/domain/work_item_plan.py` owns deterministic DAG validation
(`validate_work_item_dag`) and `plan_work_items()` (READY/BLOCKED/DONE with
sorted pending-dep traceability and data-derived reasons). Completion evidence
is `WorkItemStatus.DONE` in-plan only—never requests, partial runs, or LLM
output. Duplicate ids, missing deps, self-deps, and cycles fail closed
(ADR-049). Planning is not HTTP, not monetary, and not automatic dispatch.
`agent_loop/application/execution_request_builder.py` owns the application gate
that builds `AgentExecutionRequest` only for schedulable `WorkItem` values
(`is_schedulable` + explicit `completed_ids` + `execution_task_text`); it does
not invoke adapters, rewrite planners, or change `TaskAssessment` semantics.
It is a frontier for orchestration callers, not a global interception
of every `AgentExecutionRequest` construction (ADR-048; the S13/S14
orchestrator is the production consumer).
`agent_loop/application/planned_execution_requests.py` bridges plan READY ids
to that builder only; it does not call adapters or mutate WorkItems.
Static inventory tests may report builder imports/references/invocations
separately; they do not analyze dataflow to `AgentRuntime.execute` and
none of those signals alone proves runtime integration.
S18 boundary audit (ADR-050): `agent_loop/` must not import `scripts`/`apps`/
`django`; domain/ports stay above adapters; compatibility via
`resolve_compatible_candidates` alone is not runtime selection or dispatch
(ADR-054 adds the operational selector). Runtime execution and failure
escalation are now implemented at the application boundary (ADR-055); Notion
ingress, durable resume, and quality-evidence closure remain pending.
Regression tests live under `tests/agent_loop/architecture/`.
S19 MultiRunner survivor cleanup (ADR-051): the historical MultiRunner is
`scripts/multi_requirement_mode.py::MultiRequirementModeRunner` (host CLI
composition + `_al` facade seam). It is not the `agent_loop/` control plane and
must not absorb WorkItem DAG / request-builder ownership. S19 restores UTF-8
user-facing Spanish messages and ownership docs without rewiring `_al`, without
`AgentRuntime.execute` dispatch, and without closing ADR-048/050 E2E gaps.
(ADR-054/055 later add that dispatch as separate decisions.)
S20 ProjectProfile & Skills (ADR-052): `agent_loop/project/` owns declarative
`Skill` / `SkillCatalog`, `ProjectProfile` / `ProjectProfileCatalog`,
`resolve_skills_for_profile` (fail-closed on missing skill ids), and
`assess_profile_against_inventory` (per-skill `is_effective` against a
`RuntimeInventory` snapshot; UNKNOWN/NO never YES; no winner). Incompatibility
reasons distinguish `capability_absent` from per-axis `axis_no` /
`axis_unknown` and `no_single_snapshot` when YES is split across duplicate
snapshots. Collections are copied to tuples. Distinct from
`ModelProfile` (logical models) and from harness `runtime.skills` discovery.
Domain must not import `agent_loop.project`; project must not import
adapters/application/CLI/`scripts`/`apps`/`django`. Shipii command strings and
Notion labels remain caller-supplied data. Compatibility never implies
executability; operational routing/dispatch are owned by ADR-054/055, not by
project profiles.
`agent_loop/ports/agent_runtime.py` owns
the minimal AgentRuntime boundary (`discover` / `execute`); harness-internal
tools stay inside adapters and must not widen that port.
`agent_loop/adapters/opencode/` implements the OpenCode AgentRuntime via
read-only CLI discovery (`--version`, `mcp list`, `models --standalone`,
`debug agents`); it must not call mutating/auth MCP commands or private HTTP
APIs as a discovery substitute. Parsers distinguish valid empty inventories
from unreadable stdout (channel-level UNKNOWN). MCP `connected` does not
imply verified tool health; `(OAuth)` does not imply authorized=YES.
`agent_loop/adapters/claude/` implements the Claude Code AgentRuntime via
read-only CLI discovery (`--version`, `auth status --json`, `mcp list`,
`plugin list`); it must not call login/logout, `mcp add`/`login`, or private
HTTP APIs as a discovery substitute. Auth JSON is trusted only on successful
CLI exit. Claude has no verified read-only models inventory CLI in v2.1.283,
so `models` stays empty and `runtime.models` / `runtime.lsp` /
`runtime.skills` remain UNKNOWN. MCP empty inventory requires the observed
empty message; blank stdout or health-header-only (WebSocket-invisible)
lists are INVALID → channel UNKNOWN. MCP `Connected` does not imply verified
tool health; pending approval is configured without connected/allowed.
`agent_loop/adapters/opencode_server/` implements the OpenCode Server
AgentRuntime (S22) as a separate runtime identity (`opencode-server`,
capability `runtime.opencode.server`) using the documented public HTTP API and
SSE events only: `GET /api/info`, `/api/model`, `/api/agent`,
`POST /api/session`, `/prompt`, `/interrupt`, `/message`, `/diff`, and
`GET /api/event`. It does not use `experimental.*` endpoints, the browser UI,
PTY, filesystem-write, or configuration endpoints. Version support is
fail-closed (major 2, minor >= 2; unknown never supported). Attach mode never
stops an external server; managed mode starts `opencode serve` on loopback in
an owned process group and terminates only that group. Discovery never starts
a process. Execution maps `AgentExecutionRequest` fields without widening
permissions (restrictive permission rules only; permission asks are rejected),
keeps one session per execution unless an explicit `session_ref` is reused in
the same workspace, and normalizes terminal state/evidence/usage into the
existing port result. `AgentExecutionResult.failure` carries a neutral
`ExecutionFailure` (category + observed execution state + evidence) so the
control plane can classify and escalate; the adapter performs no retries and
owns no recovery policy (S14). Discovery declares `filesystem.read` /
`filesystem.write` snapshots that follow the observed runtime state, so S11
task requirements can be evaluated without inferring availability from names.
S13 consumes the inventory through `resolve_compatible_candidates` and the
operational selector; no adapter-specific routing exists (ADR-054/055).
S23 Telemetry (ADR-056): observability is an outbound port, not a domain
dependency. `agent_loop/ports/telemetry.py` owns immutable events, provenance,
usage/cost source, and `TelemetrySink`. `agent_loop/telemetry/recorder.py`
correlates events by execution id, assigns monotonic sequences, redacts and
bounds attributes, aggregates usage conservatively, and swallows sink failures
so observability cannot alter execution. `agent_loop/adapters/telemetry/`
provides only local `InMemoryTelemetrySink`/`NullTelemetrySink`; no external
service, persistence, queue, API, or dashboard is mandatory. The S13/S14/S16
loop publishes routing, execution, retry, escalation, failure, and closure
facts from its real decisions and results. The autonomy loop publishes agent,
tool, quality, Git-attribution, and closure facts from its observed reports.
Telemetry never re-runs routing/recovery, never records prompts, requirement or
objective text, outputs, command lines, provider messages, evidence refs, or
credentials, and represents unknown costs/tokens as absent rather than zero.
Event schema and privacy limits are documented in `docs/ai/S23_TELEMETRY.md`.

Durante la migración incremental, módulos históricos de `scripts/` pueden ser
owners todavía no migrados. Un módulo ya migrado conserva únicamente un
wrapper de identidad; nunca una copia. La fachada
`agent_loop/application/orchestrator.py` existe para compatibilidad de imports
y monkeypatches, no como catch-all de lógica.
