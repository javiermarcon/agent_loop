# ARCHITECTURE_DECISIONS

Agent Loop architectural decisions. ADR-037 through ADR-056 (plus the
autonomy-era ADR-039 on prompt transport) were extracted verbatim from the
Shipii monorepo during the independent-repository migration. Evidence paths
that refer to `scripts/`, `REPO_MAP.md`, or other host-only files document the
historical repository and are not part of this distribution.

## ADR-037: Agent Loop Uses Responsibility Packages and Compatibility Adapters

- Status: Accepted
- Context: orchestration code and tests had grown in flat `scripts/` and
  `tests/` namespaces; file extraction alone did not establish ownership or a
  dependency direction.
- Decision: `agent_loop/` is the canonical internal package. CLI and provider
  adapters depend on application orchestration; domain remains independent of
  infrastructure and legacy `scripts`. Historical script imports use thin
  identity-preserving adapters while each migrated responsibility has exactly
  one implementation. Migration proceeds in green vertical slices.
- Consequences: `scripts/agent_loop.py` remains the compatible command, while
  new code and tests are routed to responsibility owners. Temporary access to
  not-yet-migrated owners is centralized in the compatibility façade and must
  shrink in later slices; it is not a license for new flat modules.
- Evidence: `agent_loop/application/orchestrator.py`,
  `agent_loop/application/lifecycle.py`, `agent_loop/cli/arguments.py`,
  `agent_loop/agent/providers/cursor.py`,
  `agent_loop/agent/providers/codex.py`,
  `tests/agent_loop/application/test_package_architecture.py`.

## ADR-038: Agent Instructions Prefer Enforceable Minimalism Over Ceremonial Architecture

- Status: Accepted
- Context: The agent instruction set accumulated duplicated mandatory SOLID/GoF reporting,
  universal multi-line docstrings, and broad verification/output requirements. In large
  refactors this encouraged abstraction churn, compatibility stubs, typing workarounds and
  token-heavy reporting instead of small typed slices. Quality rules also allowed a refactor
  to touch the tooling that judged the same refactor, weakening the separation between code
  and its gates.
- Decision:
  - DRY/SOLID remain engineering principles, but agents do not need a letter-by-letter
    compliance proof for every change.
  - GoF review is conditional on demonstrated design pressure; ordinary changes have no
    mandatory GoF catalogue review or exact response heading.
  - Protocols/ports/patterns are introduced only at real boundaries or variation points, not
    to satisfy a checklist or static analysis.
  - Public/non-obvious behavior requires useful documentation; trivial private helpers do not
    require ceremonial multi-line docstrings. A module has at most one module docstring.
  - Feature/refactor changes may not alter quality tooling/configuration, structural
    thresholds/baselines, test exclusions, or add internal `.pyi` shadow stubs merely to make
    themselves pass. Tooling changes are separate work unless explicitly requested.
  - Agent-loop refactors proceed one bounded capability at a time and fail fast on new
    deterministic typing/import/architecture errors. Cascading failures trigger re-plan, not
    repo-wide repair.
  - Interactive agents stop after host pre-commit is GREEN. The user runs
    `scripts/run_quality_checks.py --no-act` unless they explicitly request the agent to run
    it; final GREEN is claimed only after that output is reviewed.
- Consequences:
  - fewer speculative abstractions and compatibility layers;
  - static-analysis failures must be corrected in the real implementation/contract;
  - quality gates remain independent from the code they validate;
  - instruction context is smaller and higher-salience;
  - ADR-025's requirement for explicit per-change SOLID mapping is no longer binding.
- Evidence:
  - `AGENTS.md`
  - `agent_loop/AGENTS.md`
  - `STYLE.md`
  - `TESTING.md`
  - `ARCHITECTURE.md`
  - `ARCHITECTURE_GUARDRAILS.md`
  - `docs/ai/ENGINEERING_POLICY.md`
  - `docs/ai/WORKFLOW_AND_VERIFICATION.md`

## ADR-039: Agent Loop Is a Control Plane Over Agent Runtimes

- Status: Accepted
- Context: Incremental MultiRunner extractions (`_al(...)` helpers) risked
  turning `agent_loop` into a second agent framework (own Plan/Build agents,
  MCP/LSP, Notion REST, direct Codex/Cursor invocation) instead of a
  deterministic control plane that decides WHAT/WHEN/WHETHER while harnesses
  decide HOW TO EXECUTE.
- Decision:
  - `agent_loop` owns workflow state, durable resume, dependency scheduling,
    capability requirements, strategy/harness/model routing policy, failure
    classification, scope/mutation budgets, authoritative quality policy,
    WorkItem semantic lifecycle, and DONE.
  - Generative Plan/Build/Review execution, MCP/LSP protocol work, generic
    tool runtimes, and provider APIs belong to AgentRuntime adapters
    (OpenCode, Claude Code, future harnesses), not the core.
  - Project-specific rules (Shipii quality commands, Notion status strings,
    domain boundaries) live in ProjectProfile / Skills, not in domain or
    application core concepts.
  - Continuing `_al()` extraction is not the primary roadmap axis; each
    remaining helper is classified KEEP_CORE / port / adapter / profile /
    delete before moving.
  - Slice 7 introduces the pure effective-capability vocabulary in
    `agent_loop/domain/capabilities.py` before any runtime adapter or
    AgentRuntime port. Capability tri-states are YES/NO/UNKNOWN; UNKNOWN
    never becomes YES. Semantic capability ids must not embed harness-specific
    branching in domain code.
  - Own MCP/LSP implementations, Plan/Build agents, and Notion REST adapters
    are cancelled while harness MCP covers the work-item case.
- Consequences:
  - next bounded work is the minimal AgentRuntime port (`discover` /
    `execute`), then real OpenCode/Claude discovery adapters;
  - MultiRunner survivor cleanup waits until control-plane boundaries exist;
  - ADR-034 repository/audience "capability" axes remain a separate concern
    from runtime effective capabilities.
- Evidence:
  - `agent_loop/domain/capabilities.py`
  - `tests/agent_loop/domain/test_capabilities.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-040: AgentRuntime Port Is Discover Plus Execute Only

- Status: Accepted
- Context: After ADR-039, the control plane needed a real outbound boundary for
  harnesses without inventing tool-level methods (filesystem, shell, MCP, LSP)
  that belong inside OpenCode/Claude adapters.
- Decision:
  - `agent_loop/ports/agent_runtime.py` owns the minimal `AgentRuntime`
    Protocol: `identity`, `discover(context) -> RuntimeInventory`, and
    `execute(request) -> AgentExecutionResult`.
  - `AgentExecutionRequest` carries task, strategy, model, required
    capabilities, working directory, mutation/permission policies, optional
    structured-output schema, optional session ref, and context refs.
  - `AgentExecutionResult` normalizes status, runtime/model identity, optional
    session ref, structured output, evidence refs, usage, observed failures,
    and opaque harness metadata.
  - No OpenCode/Claude adapter lands in this slice; adapters arrive later and
    must satisfy this contract without expanding the port surface.
- Consequences:
  - application/routing code depends on the port, not on harness CLIs;
  - capability effectiveness (ADR-039) remains the vocabulary used in
    `required_capabilities` and discover inventories;
  - next slices implement discovery adapters against documented CLI surfaces.
- Evidence:
  - `agent_loop/ports/agent_runtime.py`
  - `tests/agent_loop/ports/test_agent_runtime.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-041: OpenCode Discovery Is Read-Only CLI Evidence

- Status: Accepted
- Context: After ADR-040, the first harness adapter needed real discovery
  without turning `agent_loop` into an OpenCode client SDK or mutating the
  operator's MCP/auth state. Local OpenCode v2.1.4 exposes
  `opencode --version`, `opencode mcp list`, `opencode models --standalone`,
  and `opencode debug agents`. The documented `opencode mcp auth list`
  subcommand is not safely available on that version (`mcp auth list` starts
  auth for a server named `list`).
- Decision:
  - `agent_loop/adapters/opencode/` owns `OpenCodeRuntime` implementing
    `AgentRuntime.discover` via those read-only CLI commands only.
  - Observations normalize into S7 `RuntimeInventory` / `CapabilitySnapshot`
    with YES reserved for demonstrated axes; UNKNOWN on parse/timeout gaps.
  - MCP/agent parsers distinguish valid (including empty) from invalid
    stdout; unreadable formats become channel-level UNKNOWN
    (`runtime.mcp` / `runtime.agents`), never a silent empty inventory.
  - `opencode --version` must match the observed single-line
    ``opencode vX.Y.Z`` form; embedded/bare semver is rejected.
    Detectable version may set ``runtime.opencode`` supported/available=YES
    only; configured/enabled/connected/healthy stay UNKNOWN.
  - Agent arrays require every element to have capability-safe string
    ``id`` (``[A-Za-z0-9._-]+``) and non-empty ``name``; mixed,
    invalid-only, unsafe ids, or duplicate ids (exact, case-sensitive) are
    INVALID. MCP rows: only recognized status rows or the observed empty
    message ``No MCP servers configured`` are accepted; any other non-empty
    line, unknown status, or duplicate server name invalidates the list
    (no partial inventory).
  - `opencode models --standalone` lines must all match observed
    ``provider/model`` ids (model segment may include ``:``). Empty or
    whitespace-only stdout is INVALID: v2.1.4 was not observed to emit an
    empty inventory that way (including under an isolated empty HOME). Mixed
    or illegible stdout is also INVALID. Exit code 0 alone does not set
    ``runtime.models`` available=YES; INVALID parse yields channel UNKNOWN
    and an empty models inventory (no partial list).
  - `opencode mcp list` `connected` means connected only: healthy stays
    UNKNOWN without a safe tool probe. `(OAuth)` is retained in evidence but
    does not set authorized=YES; explicit `needs authentication` may set
    authorized=NO.
  - Timeout partial stdout/stderr are decoded (``str|bytes|None``) and
    secret-redacted before exposure; timeouts never count as successful
    discovery.
  - `execute` fails closed in this slice (discovery-only); no HTTP/OpenAPI
    private API substitute for discovery; no `mcp add`/`auth`/`logout`.
- Consequences:
  - control-plane routing can consume OpenCode inventories without importing
    OpenCode types into domain;
  - LSP/tool health remains undocumented by CLI and stays out of YES claims;
  - later slices may add execute / Claude adapters without widening the port.
- Evidence:
  - `agent_loop/adapters/opencode/runtime.py`
  - `tests/agent_loop/adapters/opencode/`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-042: Claude Code Discovery Is Read-Only CLI Evidence

- Status: Accepted
- Context: After ADR-041, the Claude Code harness needed the same discover-only
  `AgentRuntime` surface without inventing OpenCode parity. Local Claude Code
  v2.1.283 exposes `claude --version`, `claude auth status --json`,
  `claude mcp list`, `claude mcp get`, and `claude plugin list`. There is no
  verified read-only models inventory CLI. `claude agents --json` lists
  background sessions, not agent definitions, so it is not used for discovery.
  `type: ws` MCP servers are accepted by config/`mcp get` but the `mcp list`
  row formatter returns null, yielding a health header with no rows.
- Decision:
  - `agent_loop/adapters/claude/` owns `ClaudeCodeRuntime` implementing
    `AgentRuntime.discover` via `--version`, `auth status --json`, `mcp list`,
    and `plugin list` only (no login/logout, no `mcp add`/`login`/`remove`,
    no `mcp get` fan-out in the default probe set).
  - Observations normalize into S7 `RuntimeInventory` / `CapabilitySnapshot`
    with YES reserved for demonstrated axes; UNKNOWN on parse/timeout gaps.
  - `claude --version` must match the observed single-line
    ``X.Y.Z (Claude Code)`` form. Detectable version may set
    ``runtime.claude`` supported/available=YES only; other axes stay UNKNOWN.
  - Auth JSON is trusted only when ``auth status --json`` exits successfully
    (``CliCommandResult.ok``). Non-zero exit, timeout, or missing binary yield
    ``runtime.auth`` UNKNOWN even if stdout is JSON-shaped. Valid JSON then
    requires boolean ``loggedIn`` and non-empty string ``authMethod``. Email,
    org ids, and filesystem paths are never retained on observations.
    ``loggedIn=false`` may set authorized/available=NO; configured stays
    UNKNOWN because local config may still exist.
  - MCP rows follow the observed ``name: body - status`` form with recognized
    status vocabulary (Connected, Pending approval, Disabled, Needs
    authentication, Failed to connect, Rejected, tools-fetch-failed, Not
    configured). After stripping leading status glyphs, the status phrase must
    match a known alias exactly: em-dash issue suffixes (`` — …``) and other
    trailing junk are not stripped away to force a known state; they
    invalidate the row and therefore the whole list. Duplicate names, unknown
    statuses, trailing garbage, empty or whitespace-only stdout, or arbitrary
    non-row text also invalidate the whole list. The observed empty message is
    the only VALID empty inventory. Combining that empty-inventory message
    with one or more server rows is contradictory and INVALID (fail-closed),
    not a silent preference for either side. Only the exact observed
    health-check lines may be ignored as auxiliaries (unicode or ASCII
    ellipsis); prefix matches must not swallow unrecognized text.
    Health-header-only stdout (WebSocket-invisible case) is INVALID →
    ``runtime.mcp`` UNKNOWN, never a silent empty inventory.
  - MCP `Connected` means connected only: healthy stays UNKNOWN without a
    safe tool probe. Pending approval sets configured=YES with
    connected/available=NO and allowed=NO.
  - `plugin list` accepts only the observed empty-install message as VALID;
    non-empty plugin formats are unobserved and become ``runtime.plugins``
    UNKNOWN.
  - Models/LSP/skills have no verified read-only inventory CLI → channel
    UNKNOWN and empty `models` tuple (no fabricated catalogs).
  - Observation and timeout exposure redact credentials, emails, and
    home-directory usernames in absolute paths (``/home/<user>``,
    ``/Users/<user>``). Redaction runs at the observation/evidence boundary
    (and on timeout captures); parsers still receive original CLI stdout so
    path-like text cannot change parse outcomes by premature sanitization.
  - `execute` fails closed in this slice (discovery-only).
- Consequences:
  - control-plane routing can consume Claude inventories without importing
    Claude types into domain;
  - Claude and OpenCode inventories are not assumed to be capability-parity;
  - later slices may add execute without widening the port.
- Evidence:
  - `agent_loop/adapters/claude/runtime.py`
  - `tests/agent_loop/adapters/claude/`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-043: TaskAssessment Is Deterministic and Discovery-Free

- Status: Accepted
- Context: After S7–S10 capability vocabulary and harness discovery, the control
  plane needed a pure task evaluation step that declares what a task requires
  before any runtime inventory or model/harness routing (S12–S14) runs.
- Decision:
  - `agent_loop/domain/task_assessment.py` owns `TaskAssessmentRequest`,
    `TaskAssessment`, and `assess_task()`.
  - Inputs are declared, verifiable signals (baseline complexity/risk/
    uncertainty, acceptance clarity, impact tri-states, domains, integrations).
    The module does not NLP-parse free text, scan the repo, call CLI/discovery,
    or select models/harnesses.
  - Complexity (`MECHANICAL`/`LOCAL`/`CROSS_MODULE`/`ARCHITECTURAL`), risk
    (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`), and uncertainty (`LOW`/`MEDIUM`/`HIGH`)
    use explicit floors. Deterministic signals may only raise baselines:
    schema/security/authentication/payments → risk ≥ `HIGH`; concurrency →
    complexity ≥ `CROSS_MODULE` and risk ≥ `HIGH`; dependency inversion or
    architecture impact YES → `ARCHITECTURAL`. Acceptance clarity supplies an
    uncertainty floor only (`CLEAR` → ≥ `LOW`, `INCOMPLETE` → ≥ `MEDIUM`,
    `MAJOR_AMBIGUITY` → ≥ `HIGH`); the final uncertainty is
    ``max(baseline_uncertainty, floor)`` and never drops below the baseline
    (so `CLEAR` cannot reduce a prior `MEDIUM`/`HIGH`).
  - `CRITICAL` is reserved; S11 floors never invent it without a later
    explicit contract.
  - `ImpactPresence.UNKNOWN` is preserved and recorded; it is not treated as
    `NO` or as a floor.
  - `estimated_files` may be `None` (unknown); no numeric file-count thresholds.
  - Requirement derivation reuses `CapabilityRequirement` / `CapabilityId`.
    Change tasks require `filesystem.read` / `filesystem.write` with
    supported+available axes. Declared work-item signals may require existing
    `work_item.notion.*` ids. Named `external_integrations` become
    `required_context` entries and gap reasons; they do not invent `mcp.*`
    requirements or assert availability/connected/authorized.
  - Reasons are stable, ordered explanations of baselines, floors, context,
    and gaps for later routing/escalation slices.
- Consequences:
  - S12+ can consume `TaskAssessment.required_capabilities` against inventories
    without coupling assessment to OpenCode/Claude adapters;
  - vocabulary gaps for unnamed integrations stay explicit until mapped.
- Evidence:
  - `agent_loop/domain/task_assessment.py`
  - `tests/agent_loop/domain/test_task_assessment.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-044: ModelProfile Is Logical; Runtime Binding Is Declarative Only

- Status: Accepted
- Context: After S11 `TaskAssessment`, the control plane needed typed contracts
  for logical model profiles and the association of those profiles to a runtime
  that *declares* them, before any selection/routing (S13+) or execution.
  `InventoryModelRef` remains a minimal discovery payload field
  (`model_id` only) and is not a second logical profile owner.
- Decision:
  - `agent_loop/domain/model_profiles.py` owns `ModelProfile` and
    `RuntimeModelProfile`.
  - `ModelProfile` identity is the logical `profile_id`. Equality is the
    frozen dataclass field-wise contract (`profile_id`, `provider_id`,
    `model_id`). Identifiers must be non-empty and must not include leading or
    trailing whitespace; provider/model strings are opaque and are never
    case-folded or otherwise silently normalized.
  - `RuntimeModelProfile` associates `runtime_id` (same string space as
    `RuntimeInventory.runtime_identity` / `RuntimeIdentity.runtime_id`) with a
    `ModelProfile`, plus optional `native_model_ref` for a harness-native id
    as declared. Association identity is `(runtime_id, profile.profile_id)` via
    `identity_key()`.
  - Tool/runtime capabilities stay on `CapabilityId` /
    `CapabilityRequirement` / `CapabilitySnapshot` / `RuntimeInventory`.
    `ModelProfile` does **not** carry `CapabilityId` declarations: current ids
    name harness/tool axes (filesystem, work items, …), not model skills.
    A separate model-capability vocabulary may be introduced when selection
    needs it; S12 does not invent one or mix the two layers.
  - Existence of a `RuntimeModelProfile` does not mean the model is available,
    the runtime is healthy, auth is valid, or execution is authorized. S12 does
    not add availability/auth/authorization taxonomies; those remain on
    existing capability axes and later slices.
  - S12 does not select models, rank providers, route by
    complexity/risk/uncertainty, execute tasks, or extend OpenCode/Claude
    discovery.
- Consequences:
  - Later routing can relate `TaskAssessment.required_capabilities` to
    runtime inventories without treating model catalogs as capability evidence;
  - Adapters may later map inventory model ids into `RuntimeModelProfile`
    without changing S12 semantics.
- Evidence:
  - `agent_loop/domain/model_profiles.py`
  - `tests/agent_loop/domain/test_model_profiles.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-045: Model Profile Catalogs Are Declarative and Fail-Closed

- Status: Accepted
- Context: S12 defined `ModelProfile` / `RuntimeModelProfile` with logical
  `profile_id` identity and association `identity_key()`, but no owner for
  deterministic collections, duplicate detection, or lookup. S14 routing needs
  those guarantees without discovery or selection in S13.
- Decision:
  - `agent_loop/domain/model_profile_catalog.py` owns `ModelProfileCatalog` and
    `RuntimeModelProfileCatalog`. S12 types remain the value-object owners in
    `model_profiles.py`; public equality/hashing semantics are unchanged.
  - **Logical vs structural identity:** catalog lookup keys on `profile_id`
    (logical). `ModelProfile` equality remains field-wise (`profile_id`,
    `provider_id`, `model_id`). Two definitions with the same `profile_id` but
    different provider/model are a **conflict** (`ValueError`), not a silent
    overwrite. Exact duplicate `profile_id` entries are also rejected
    (fail-closed; no collapse by insertion order).
  - `profile_id` is unique globally within a `ModelProfileCatalog`.
  - The same logical profile may bind to many runtimes. A
    `RuntimeModelProfile` duplicate is the same `identity_key()`
    `(runtime_id, profile_id)`; equal duplicates and unequal conflicts both
    raise `ValueError`.
  - Optional `RuntimeModelProfileCatalog.from_bindings(..., profiles=)` may
    require each binding's profile to exist and structurally equal the profile
    catalog entry; it does not invent profiles from native refs.
  - Enumeration is deterministic: profiles by `profile_id`, bindings by
    `identity_key()`. Collections are frozen tuples.
  - Registered entries are definitions only—not installed, available, healthy,
    authenticated, authorized, or selectable. No CLI/IO/adapters in this
    module. No model selection, routing, or capability inference.
- Consequences:
  - S14 can consume catalogs as declared inputs alongside `TaskAssessment`
    and inventories without treating catalog membership as runtime evidence.
- Evidence:
  - `agent_loop/domain/model_profile_catalog.py`
  - `tests/agent_loop/domain/test_model_profile_catalog.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-046: Compatible Candidates Are Declarative and Inventory-Gated

- Status: Accepted
- Context: After S11–S13, the control plane needed a pure step that lists which
  catalogued runtime/model bindings are *compatible* with a
  `TaskAssessment` given discovered inventories, before any execution or
  single-winner selection.
- Decision:
  - `agent_loop/domain/compatible_candidates.py` owns
    `CompatibleModelCandidate`, `CompatibleCandidateResolution`, and
    `resolve_compatible_candidates()`.
  - Required capabilities come only from
    `TaskAssessment.required_capabilities` and are satisfied only by
    `RuntimeInventory.capabilities` through `is_effective`. `ModelProfile`
    still does not declare capabilities (ADR-044).
  - A binding is accepted only when: (1) its profile exists and structurally
    equals the `ModelProfileCatalog` entry; (2) an inventory exists with
    `runtime_identity == binding.runtime_id`; (3) that inventory satisfies
    every required capability; (4) `native_model_ref` is present and equals an
    `InventoryModelRef.model_id` in that same inventory. Logical
    `profile_id`/`model_id` are never substituted for native refs.
  - Empty `required_capabilities` passes the capability check vacuously but
    still requires catalog + inventory model membership.
  - Duplicate `RuntimeInventory.runtime_identity` inputs raise `ValueError`.
    Catalog conflicts remain fail-closed at catalog construction (ADR-045).
  - Output is the full sorted candidate set by `(runtime_id, profile_id)` plus
    stable sorted `reasons`. No winner, fallback, randomness, or operational
    availability/auth claims.
- Consequences:
  - Later slices may choose among candidates without redefining compatibility;
  - Discovery adapters remain outside this domain step.
- Evidence:
  - `agent_loop/domain/compatible_candidates.py`
  - `tests/agent_loop/domain/test_compatible_candidates.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-047: WorkItem Is the Control-Plane Unit of Work

- Status: Accepted
- Context: ADR-039 assigns WorkItem semantic lifecycle to the control plane,
  but after S7–S14 no domain type existed. Legacy `RequirementUnit` remains a
  MultiRunner/document extraction concept and must not become the canonical
  WorkItem. Notion page URLs/status labels remain project/adapter concerns.
- Decision:
  - `agent_loop/domain/work_item.py` owns `WorkItem`, `WorkItemStatus`,
    `WorkItemExternalRef`, `is_schedulable()`, and `execution_task_text()`.
  - Required fields: stable `work_item_id`, non-empty unpadded `objective`,
    and `status` (`OPEN` / `BLOCKED` / `DONE`). Optional opaque
    `external_ref` (`system` + `reference`), `depends_on` (other work item
    ids, unique, no self), and `context_refs` (opaque handles).
  - Equality is frozen field-wise. No silent identifier normalization.
  - `is_schedulable` is true only for `OPEN` items whose dependencies are in
    the provided completed-id set. It does not probe runtimes or externals.
  - `execution_task_text` exposes objective text for later
    `AgentExecutionRequest.task` handoff and rejects non-`OPEN` items. S15
    does not change the AgentRuntime port or implement Claude/OpenCode
    `execute`.
  - Capability requirements stay on `TaskAssessment` /
    `CapabilityRequirement`. `work_item.notion.*` capability ids remain
    inventory axes, not fields of `WorkItem`.
  - No Notion SDK/REST, no MultiRunner migration, no planner DAG rewrite.
- Consequences:
  - Later slices can schedule and execute using this contract without
    importing `scripts.multi_requirement` or Notion clients into domain.
- Evidence:
  - `agent_loop/domain/work_item.py`
  - `tests/agent_loop/domain/test_work_item.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-048: Schedulable WorkItem Gate Before AgentExecutionRequest

- Status: Accepted (gate implemented; dispatch frontier exercised by the
  S13/S14 application orchestrator)
- Context: ADR-047 defines `WorkItem`, `is_schedulable()`, and
  `execution_task_text()`, but no control-plane path constructed
  `AgentExecutionRequest` from that contract. Dual legacy paths
  (MultiRunner / script adapters) must not become a second WorkItem owner.
  `TaskAssessment` and the AgentRuntime port remain separate concerns.
  Inventory after S16: the only production `AgentExecutionRequest(...)`
  constructor is this builder; Claude/OpenCode adapters receive requests and
  the CLI adapters currently fail-closed on `execute` (discovery-only). The
  S13/S14 orchestrator (`execution_orchestrator.py`) is now an application
  consumer of this gate. Tests may still construct the port type directly for
  contract checks.
- Decision:
  - `agent_loop/application/execution_request_builder.py` owns
    `build_execution_request()` and `ExecutionRequestOptions`: the application
    gate that constructs `AgentExecutionRequest` only when
    `is_schedulable(work_item, completed_ids=...)` is true.
  - Annotations are evaluated at definition time (no postponed evaluation /
    no `TYPE_CHECKING`-only type imports) so `typing.get_type_hints` resolves
    class and function annotations from the module namespace without cycles
    (application → domain/ports only).
  - `completed_ids` is an explicit caller-supplied set of already-`DONE`
    work-item ids. Absence of completion evidence fails closed; planning or
    prior dispatch alone does not satisfy dependencies.
  - Task text comes from `execution_task_text(work_item)` (objective). Opaque
    `context_refs` are copied onto the request. Port-side fields
    (strategy, policies, model, capabilities) travel via
    `ExecutionRequestOptions`. Capability requirements remain a separate input
    (typically from `TaskAssessment`) and are not inferred from `WorkItem`.
  - `AgentExecutionRequest`, `TaskAssessment`, S14
    `resolve_compatible_candidates`, and runtime adapters are unchanged.
  - This slice does not invent a fake orchestrator, wire MultiRunner, Notion
    sync, planner/DAG rewrite, adapter `execute`, or new persistence. The
    builder is a **frontier for future callers**, not a system-wide shield:
    any future production path that builds `AgentExecutionRequest` should use
    it; until then ineligible `WorkItem` values are only blocked at this gate.
- Consequences:
  - Non-schedulable items (`BLOCKED`, `DONE`, pending deps, missing
    completion evidence) cannot produce an executable request **through this
    gate**.
  - Direct `AgentExecutionRequest(...)` in tests remains possible; the
    application orchestrator is the production consumer that dispatches via
    the builder.
  - Static inventory helpers
    (`tests/agent_loop/application/execution_request_ast_inventory.py`)
    distinguish two scan scopes (do not conflate them):
    - `agent_execution_request_ctor_paths()` walks `agent_loop/`, `scripts/`,
      and `tests/agent_loop/` for recognized `AgentExecutionRequest(...)`
      constructors.
    - `application_modules_importing_builder()` and
      `application_modules_invoking_builder()` walk **only**
      `agent_loop/application/` (excluding the builder module itself) for
      builder imports vs `build_execution_request` invocations.
    Within those scopes the helpers separately report builder **imports**,
    known-symbol **references**, and invocations (absolute/relative/aliased
    imports; Python `import a.b.c` binds `a` and is accessed as `a.b.c`).
    A bare load of an imported module name is not a symbol reference.
    Builder `import *` counts as an import but does not invent resolvable
    symbol aliases for reference/invocation. Attribute constructors of
    `AgentExecutionRequest` require a recognized `agent_runtime` module
    prefix. The inventory does not analyze dataflow from builder results to
    `AgentRuntime.execute` and does not prove runtime integration or its
    absence. Not covered: assignment aliases, `getattr`, `eval`, wildcard
    symbol expansion, dynamic imports. Not a global runtime shield.
    Planning + request construction (S17 bridge) still do **not** equal
    productive dispatch by themselves; the S13/S14 orchestrator adds the
    bounded dispatch path (ADR-054/055).
- Evidence:
  - `agent_loop/application/execution_request_builder.py`
  - `tests/agent_loop/application/test_execution_request_builder.py`
  - `tests/agent_loop/application/execution_request_ast_inventory.py`
  - `tests/agent_loop/application/test_execution_request_ast_inventory.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop application entries

## ADR-049: Deterministic WorkItem DAG Planning

- Status: Accepted (planning available; automatic dispatch not implemented)
- Context: ADR-047/048 provide `WorkItem`, `is_schedulable`, and
  `build_execution_request`, but no set-level planner validated dependencies as
  a DAG or classified READY/BLOCKED/DONE with traceable reasons. Legacy
  MultiRunner requirement graphs must not become the control-plane planner.
- Decision:
  - `agent_loop/domain/work_item_plan.py` owns DAG validation
    (`validate_work_item_dag`) and `plan_work_items()`.
  - **Validation algorithm (fail closed):**
    1. Index items by `work_item_id`; duplicate ids →
       `InvalidWorkItemDagError`.
    2. Every `depends_on` target must exist in the same collection; missing
       targets and self-dependencies → `InvalidWorkItemDagError`
       (self-deps are also rejected at `WorkItem` construction; the planner
       re-checks as defense in depth).
    3. Detect cycles with deterministic **explicit-stack** DFS (not CPython
       call recursion): nodes and adjacency visited in sorted id order;
       report one explicit path (`cycle detected: a -> b -> a`). No product
       WorkItem depth/count cap and no “best effort” cyclic resolution.
       Stack depth is bounded by graph size on the heap, independent of
       `sys.getrecursionlimit()`.
  - **Classification algorithm:**
    1. `completed_ids` = `{id | status is DONE}` in this plan only.
    2. `DONE` → plan DONE (terminal; pending deps cleared in the entry).
    3. Semantic `BLOCKED` → plan BLOCKED (never READY), still listing unmet
       deps for traceability.
    4. `OPEN` with unmet deps → plan BLOCKED + sorted
       `pending_dependency_ids` + reason
       `BLOCKED: pending dependencies: …`.
    5. `OPEN` with all deps in `completed_ids` → must pass
       `is_schedulable`; then READY. Disagreement raises `RuntimeError`
       (internal invariant). Unsupported status objects raise `TypeError`.
  - **Determinism:** `entries` / `ready_ids` / `blocked_ids` / `done_ids` and
    pending-dep tuples are sorted by `work_item_id`. Input iteration order
    must not change equality of the plan.
  - **Non-evidence (explicitly rejected as completion):** presence of an
    `AgentExecutionRequest`, started/partial execution, model/LLM text,
    external refs, or OPEN/BLOCKED neighbors.
  - `agent_loop/application/planned_execution_requests.py` bridges
    READY ids → `build_execution_request` only. Empty/invalid plans yield
    empty tuples or propagate DAG errors. No adapter calls, no mutation, no
    persistence, no retries/sessions/Notion.
  - **Out of scope / not claimed:** HTTP APIs, monetary flows, Behave
    user workflows, automatic `AgentRuntime.execute` dispatch, concurrent
    scheduling. This planning slice leaves dispatch to later decisions
    (now ADR-054/055).
  - Adapters, MultiRunner, `TaskAssessment`, and Notion remain unchanged.
- Consequences:
  - Callers can obtain a reproducible ready frontier and optional request
    envelopes without claiming automatic execution or product-level
    orchestration.
  - Pytest owns this contract; Behave is not applicable until an externally
    observable workflow exposes planning.
- Evidence:
  - `agent_loop/domain/work_item_plan.py`
  - `agent_loop/application/planned_execution_requests.py`
  - `tests/agent_loop/domain/test_work_item_plan.py`
  - `tests/agent_loop/application/test_planned_execution_requests.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop domain/application entries

## ADR-050: Agent-loop Boundary Audit (S18) — Standalone Core, PARTIAL E2E

- Status: Accepted (boundaries verified; runtime E2E now implemented by
  ADR-054/055; Notion ingress and durable resume remain pending)
- Context: After S7–S17 the control-plane vocabulary, discovery adapters,
  compatibility resolution, WorkItem DAG planning, and request construction
  existed. At that audit point product orchestration (Notion sync, runtime
  selection winner, `AgentRuntime.execute`, retries/escalation, quality-evidence
  closure) was not a single wired pipeline. S18 audited dependency and
  quality-environment limits without rewriting MultiRunner or inventing a
  plugin framework. S13/S14 later added the operational selector, neutral
  failure handling, and the bounded application loop.
- Decision:
  - **Independence:** The `agent_loop/` package must not import `scripts`,
    `apps`, or `django`. Domain/ports must not import application/adapters;
    application must not import adapters. Legacy MultiRunner /
    `RequirementUnit` remain outside the WorkItem control plane. Host
    entrypoints under `scripts/` may wrap `agent_loop` (not the reverse).
  - **Capabilities vs operations:** `TaskAssessment` declares requirements;
    `resolve_compatible_candidates` is declarative only (no winner, no
    auth/health inference beyond inventory axes via `is_effective`).
    `CONNECTED=YES` does not imply `HEALTHY`/`AUTHORIZED`. Compatibility
    does not prove executability. S22 owns real server execution; S9/S10 stay
    discovery-only and fail closed on `execute`.
  - **Quality isolation:** Static inventory helpers derive repo roots from
    `__file__` (not process cwd). Boundary regressions live under
    `tests/agent_loop/architecture/`.
  - **E2E matrix (honest status):**
    - Notion ingress/egress: **pending**
    - WorkItem DAG planning: **implemented** (S17)
    - TaskAssessment / capabilities: **implemented** (S11)
    - Inventories + catalogs + compatible candidates: **implemented**
      (S9–S14)
    - Operational routing/selection: **implemented** (S13 / ADR-054)
    - Request construction gate: **implemented** (S16)
    - Runtime dispatch + failure escalation: **implemented** for the
      application loop (S14/S22 / ADR-055); CLI adapters remain discovery-only
    - Quality-evidence closure loop: **pending**
  - No new business rules beyond locking audited boundaries at S18. Later
    ADR-054/055 advance the dispatch frontier without weakening these bans.
  - **Explicitly out of S18 verification surface (not claimed):**
    - Behave scenarios: no new externally observable product workflow.
    - HTTP/`curl` and monetary amount checks: no API or pricing surface.
    - Full monorepo line coverage of unrelated apps: owned by existing
      quality gates, not by this boundary slice.
    - Product Notion sync and durable resume: still pending (runtime
      execute/escalation are now covered by ADR-054/055).
- Consequences:
  - Callers may compose assessment → candidates → plan → builder without
    claiming a full orchestrator.
  - Further slices must not silently reintroduce `scripts`/`apps`/`django`
    imports into `agent_loop/` or treat compatibility as availability.
  - Regression suite under `tests/agent_loop/architecture/` covers import
    bans (absolute `import` / `from pkg import name` / `from pkg.sub import`,
    and relative imports resolved via the analyzed module's package), layer
    ownership, cwd-independent inventory roots, CONNECTED≠HEALTHY/AUTHORIZED,
    and non-wiring of compatibility into the planning bridge. Unresolvable
    relative imports are treated as offenders (never silently allowed).
- Evidence:
  - `tests/agent_loop/architecture/test_package_boundaries.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop architecture entries
  - Prior ADRs 040–049 for component ownership

## ADR-051: MultiRunner Survivor Cleanup Is Ownership-Clearing, Not E2E (S19)

- Status: Accepted
- Context: ADR-039 deferred MultiRunner survivor cleanup until control-plane
  boundaries existed. After S7–S18 those boundaries exist, but
  `MultiRequirementModeRunner` remains the host CLI orchestrator under
  `scripts/` (~2805 LOC) with a contracted `_al`/`getattr` facade seam that
  tests and monkeypatches still require. S19 must not invent execute/routing/
  Notion closure or silently migrate MultiRunner into `agent_loop/`.
- Decision:
  - Treat `scripts/multi_requirement_mode.py::MultiRequirementModeRunner` as the
    canonical name for the legacy MultiRunner survivor; composition stays in
    `scripts/multi_requirement_mode_composition.py` without re-exporting the
    runner class.
  - Restore corrupted UTF-8 Spanish user-facing strings in the runner (no
    semantic change beyond correct accents).
  - Document ownership explicitly in module docstring, `REPO_MAP.md`, and
    `ARCHITECTURE.md`: MultiRunner ≠ control plane.
  - Do **not** rewire `_al` helpers to direct imports in this slice (would break
    demonstrated monkeypatch contracts).
  - S19 itself must not change ADR-048/050 PARTIAL/E2E claims; later
    ADR-054/055 advance the dispatch frontier as separate accepted decisions.
  - Behave/curl/monetary gates do not apply: no new externally observable
    product workflow and no HTTP/pricing surface.
- Consequences:
  - Further MultiRunner extractions remain classified KEEP_CORE / port /
    adapter / profile / delete per ADR-039 before any move.
  - Callers must not treat S19 as MultiRunner→control-plane migration.
- Evidence:
  - `scripts/multi_requirement_mode.py`
  - `tests/test_multi_requirement_mode_composition.py`
  - `tests/test_multi_requirement_mode_coverage.py`
  - `REPO_MAP.md` multi_requirement_mode entry
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries

## ADR-052: ProjectProfile and Skills Are Declarative Project Contracts (S20)

- Status: Accepted
- Context: ADR-039 places project-specific rules in ProjectProfile / Skills
  rather than hardcoding them into domain or application core. `ModelProfile`
  (S12–S14) already covers logical model identity; it is not a project profile.
  Harness `runtime.skills` inventory axes remain adapter discovery signals and
  are not project Skills.
- Decision:
  -     `agent_loop/project/` owns `Skill`, `SkillCatalog`, `ProjectProfile`,
    `ProjectProfileCatalog`, `assess_profile_against_inventory`,
    `resolve_skills_for_profile`, and `diagnose_requirement_against_inventory`.
  - Skills declare `CapabilityRequirement` values; profiles reference skill ids
    in order without duplicates. Catalogs reject duplicate/conflicting ids and
    enumerate deterministically.
  - Compatibility against `RuntimeInventory` uses `is_effective` for the boolean
    gate. Incompatibility reasons are deterministic and distinguish
    `capability_absent`, per-axis `axis_no` / `axis_unknown` (sorted axis
    names), and `no_single_snapshot` when axes are individually YES on
    different snapshots. UNKNOWN is never reported as confirmed NO; UNKNOWN/NO
    never become YES. Compatibility never implies executability.
  - `ProjectProfile.skill_ids`, `Skill.required_capabilities`, catalogs, and
    assessment result collections are normalized to immutable tuples (copies),
    so caller-owned mutable inputs cannot alias into the value objects.
  - Domain must not import `agent_loop.project`. Project must not import
    adapters, application, CLI, `scripts`, `apps`, or `django`.
  - No Shipii quality-command strings, Notion labels, or provider APIs are
    embedded in these modules; those remain data supplied by callers.
  - Out of scope for S20: `AgentRuntime.execute`, routing winners, WorkItem
    dispatch, new adapters, Notion, retries/escalation, packaging, telemetry.
    Those remain outside the project/skills contract; ADR-054/055 later own
    operational routing and bounded dispatch.
- Consequences:
  - Callers may compose catalogs → resolve skills → assess inventory without a
    control-plane orchestrator.
  - Later slices may load project data into these contracts without moving
    ownership into domain.
- Evidence:
  - `agent_loop/project/skills.py`
  - `agent_loop/project/project_profiles.py`
  - `agent_loop/project/profile_skill_compatibility.py`
  - `tests/agent_loop/project/`
  - `tests/agent_loop/architecture/test_package_boundaries.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` project entries

## ADR-039: Bounded Prompt Transport and Evidence-Based Target Environments

- Status: Accepted
- Context: The autonomous runtime built prompts large enough to exceed Linux
  `MAX_ARG_STRLEN` and appended them as one `argv` entry, so the agent never
  started. Discovery also treated only four fixed Compose names as configured
  and fell back to "no virtualenv" for projects whose tools run in containers.
- Decision:
  - The prompt is a payload, not a configuration argument. `CommandRunner`
    streams it through stdin; `PromptTransport.AUTO` uses stdin above a
    conservative size threshold and preserves positional delivery for small
    prompts and CLIs that require it. The prompt is never truncated.
  - Compose selection is evidence-based: standard names, environment variants
    and documented overrides are candidates; `justfile`, `Makefile`, scripts,
    CI and documentation identify the selected file. Ties stay ambiguous.
  - Django settings are resolved statically from `manage.py` literals,
    variables and cwd branches, using the Compose service working directory as
    runtime evidence. Installed apps are classified as project, third-party or
    framework without importing target code.
  - Tools without a local virtualenv are probed inside the discovered Compose
    service before being reported available; documented project commands take
    precedence over invented equivalents. A configuration file alone does not
    make a tool required when the project neither installs nor invokes it.
- Consequences: prompts of any size are deliverable, discovery reports the real
  execution environment, and unavailable/unknown distinctions remain explicit.
- Evidence: `agent_loop/autonomy/tools/command.py`,
  `agent_loop/autonomy/execution/agent.py`,
  `agent_loop/autonomy/discovery/compose.py`,
  `agent_loop/autonomy/discovery/django_settings.py`,
  `agent_loop/autonomy/discovery/tool_commands.py`,
  `agent_loop/autonomy/discovery/tools.py`,
  `tests/agent_loop/autonomy/test_command.py`,
  `tests/agent_loop/autonomy/test_agent.py`,
  `tests/agent_loop/autonomy/test_django_project_compose_evidence.py`,
  `tests/agent_loop/autonomy/test_django_project_settings.py`,
  `tests/agent_loop/autonomy/test_tools_container.py`.

## ADR-053: OpenCode Server Runtime Is a Separate Public-API Adapter (S22)

- Status: Accepted (adapter implemented and E2E-verified; S13 operational
  routing and S14 escalation now consume it — ADR-054/055)
- Context: S09 implements `OpenCodeRuntime` as read-only CLI discovery and
  keeps `execute` fail-closed (ADR-041). S22 must execute real work through an
  OpenCode Server without coupling the domain to OpenCode, without replacing
  the CLI adapter, and without inventing routing or retry policy that belongs
  to other slices. Local OpenCode v2.2.6 exposes a documented HTTP/JSON server
  (`/api/**`), a public `GET /openapi.json` contract, Basic authentication, SSE
  events, sessions, prompts, interrupts, messages, and per-file diffs.
- Decision:
  - **Separate adapter.** `agent_loop/adapters/opencode_server/` owns
    `OpenCodeServerRuntime`, runtime identity `opencode-server`, and
    capability id `runtime.opencode.server`. It is distinct from the S09 CLI
    adapter (`opencode`) and does not alter S09 semantics. Domain and ports do
    not import OpenCode types; OpenCode request/response models stay inside the
    adapter package.
  - **Transport.** Use the documented public HTTP API with
    `http.client`/`json` (no SDK dependency). Authenticate with HTTP Basic
    (`opencode:<password>`). Use `GET /api/event` SSE for terminal execution
    events and permission requests. Do not use `experimental.*` endpoints,
    the browser UI, PTY, filesystem-write, or configuration endpoints.
  - **Version policy.** `GET /api/info` supplies the semantic version.
    Major `2`, minor `>= 2` is supported; other parsable versions are
    incompatible; missing/unparsable versions are unknown. Unknown is never
    supported and `execute` fails closed.
  - **Discovery is read-only.** `discover` performs only `GET /api/info`,
    `GET /api/model`, and `GET /api/agent`. It never starts a managed server,
    creates sessions, mutates configuration, or infers authorization from a
    socket. Axes are explicit: not configured, unreachable, unauthorized,
    reachable/healthy, version-supported, authorized, and model inventory
    availability. `connected=YES` does not imply `healthy` or `authorized`.
  - **Server lifecycle ownership.** Attach mode connects to an externally
    owned server and never stops it. Managed mode starts
    `opencode serve --hostname <loopback> --port <port>` with a new process
    session, captures the printed URL/password, waits for readiness, records
    the PID, and on close terminates only the process group it started
    (SIGTERM, then SIGKILL after a bounded wait). Discovery never starts a
    process; managed startup is an explicit lifecycle call. Non-loopback bind
    addresses are rejected.
  - **Session lifecycle.** Each execution creates its own session unless the
    request carries an explicit `session_ref`. A supplied `session_ref` must
    exist and its `location.directory` must resolve to the request workspace;
    otherwise execution fails closed (no silent re-creation). Sessions are
    never shared between unrelated executions. Sessions are kept after
    execution for diagnosis; the adapter does not delete them. Session
    references are returned on results, evidence, and metadata.
  - **Request mapping.** `AgentExecutionRequest.task` becomes the prompt text;
    `strategy` selects the OpenCode agent (`plan`/`build`, review is read-only
    `plan`); `model` must be `provider/model` (or `provider/model#variant`);
    `working_directory` becomes the session location and is validated as an
    existing absolute directory; `mutation_policy`/`permission_policy` become
    explicit *restrictive* permission rules only (never expands permissions);
    `context_refs` are preserved as opaque session/prompt metadata;
    `required_capabilities` are routing inputs and are not reinterpreted as
    tools. `structured_output_schema` is rejected because the documented
    prompt API has no structured-output contract.
  - **Security.** Password never enters argv, logs, evidence, metadata, or
    exceptions. `external_directory` is denied. When workspace mutation is
    disallowed, `edit` and `shell` are denied. When network is disallowed,
    `webfetch` and `websearch` are denied. `permission.asked` events are
    answered `reject` (fail-closed) rather than auto-approved. Workspace must
    exist and be a directory. Instructions found in the target repository are
    harness input, not control-plane configuration; the adapter never parses
    them to change server security.
  - **Concurrency.** Executions are bounded by an adapter-level semaphore
    (default 1, configurable). Each execution owns one session and one event
    subscription. No unrestricted parallelism and no cross-execution session
    reuse.
  - **Failure handling.** Adapter errors map to neutral observed-failure codes
    (connection refused, server unavailable, unsupported version,
    authentication/authorization failure, invalid request, session not found,
    timeout, cancellation, stream interruption, execution failure, invalid
    response, incomplete result). Retries and escalation are **not**
    implemented; S14 owns that policy.
  - **Result normalization.** Map terminal state to
    `AgentExecutionStatus`; preserve model identity, session ref, evidence
    refs, usage, observed failures, and opaque harness metadata. The final
    assistant text is carried in `structured_output` because the port has no
    dedicated content field; schema-declared requests fail closed. Missing
    outcome, timeout, disconnect, or incomplete stream never becomes success.
- Consequences:
  - S09 CLI discovery remains unchanged and usable; server execution is a
    separate opt-in runtime.
  - Existing `resolve_compatible_candidates` can recognize `opencode-server`
    inventories by runtime identity; operational selection and escalation are
    owned by S13/S14 (ADR-054/055), not by this adapter.
  - Full control-plane routing/selection and failure escalation now exist in
    application; ADR-048/050 dispatch frontier is exercised, while durable
    resume and Notion ingress remain pending.
  - `AgentExecutionResult` has no textual content field; a future ADR may add
    a neutral `content` field and remove the `structured_output` mapping.
- Evidence:
  - `agent_loop/adapters/opencode_server/`
  - `tests/agent_loop/adapters/opencode_server/`
  - `docs/ai/S22_OPENCODE_SERVER_RESEARCH.md`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop adapter entries

## ADR-054: S13 Operational Routing Requires Explicit Readiness Evidence

- Status: Accepted (operational selector implemented; adapter-specific routing
  is explicitly excluded)
- Context: ADR-046 already resolved *declarative* compatible candidates from
  `TaskAssessment`, catalogs, and discovered inventories. That output was not a
  routing decision: it could not distinguish a configured runtime from an
  available, healthy, authorized one, and callers could not explain why one
  candidate was selected. S22 needed a concrete runtime/model/strategy decision
  without creating a second router.
- Alternatives considered:
  - Treat the first compatible candidate as the selection inside adapters.
    Rejected: adapters would own control-plane policy and could not see global
    alternatives.
  - Infer availability from catalog membership or `configured=YES`. Rejected:
    violates the evidence-before-assertion contract and ADR-039/046.
  - Add a separate routing mechanism for OpenCode Server. Rejected: duplicates
    S13 and couples the control plane to one adapter.
  - Require explicit per-runtime readiness policies and extend the existing
    candidate resolution. Chosen.
- Decision:
  - `agent_loop/application/execution_routing.py` owns operational routing:
    `RuntimeReadinessPolicy`, `RoutingConstraints`, `RoutingDecision`, and
    `select_routing`.
  - Routing calls `resolve_compatible_candidates` first; it never redefines
    compatibility, catalog membership, or capability matching.
  - Each runtime must have an explicit readiness policy naming an inventory
    capability id and mandatory axes. A candidate is eligible only when that
    snapshot exists and every mandatory axis is `YES`. Missing policy, missing
    snapshot, `NO`, and `UNKNOWN` all reject the candidate.
  - Selection is deterministic: candidates are already sorted by
    `(runtime_id, profile_id)`; eligibility preserves that order and the first
    eligible candidate is selected. Ties do not depend on input order.
  - `RoutingConstraints` supports attempted-combination exclusions plus
    required/forbidden runtime ids so S14 can request model- or runtime-scoped
    escalation without selecting a candidate itself.
  - `RoutingDecision` carries the strategy, selected candidate, full eligible
    set, capability evidence, and stable selection/rejection reasons. Empty
    selection yields an explicit terminal reason; no fallback winner is
    invented.
  - Execution strategy is an explicit control-plane input (`RoutingConstraints`)
    and is carried into the decision. S13 does not infer strategy from model
    names or task text.
  - S22 discovery declares `filesystem.read`/`filesystem.write` snapshots whose
    axes follow the observed runtime snapshot. This makes S11 task requirements
    evaluable without inventing capability evidence beyond the runtime state.
- Consequences:
  - S14 escalation has one selector to call and cannot bypass readiness,
    exclusions, or original task capability constraints.
  - Callers must supply readiness policies; absence fails closed instead of
    silently selecting a configured runtime.
  - Compatibility remains declarative in domain; operational selection lives in
    application and cannot import adapters.
- Evidence:
  - `agent_loop/application/execution_routing.py`
  - `tests/agent_loop/application/test_execution_routing.py`
  - `tests/agent_loop/adapters/opencode_server/test_e2e_control_plane.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop application entries

## ADR-055: S14 Failure Escalation Is Neutral Classification Plus Bounded Policy

- Status: Accepted (control-plane policy and loop implemented; durable
  persistence/resume remains out of scope)
- Context: Runtime adapters could report `AgentExecutionResult.status=FAILED`
  and free-form `observed_failures`, but the control plane had no neutral
  category, execution-state evidence, retry-safety rule, or bounded recovery
  policy. Adapter-specific retries would compete with the control plane and
  could repeat non-idempotent work.
- Alternatives considered:
  - Classify by parsing adapter/OpenCode error messages. Rejected: provider
    coupling and brittle.
  - Let each adapter implement retry/escalation. Rejected: duplicated policy,
    no global attempt budget, risk of nested retries.
  - Add a generic retry-until-success loop. Rejected: unsafe for non-idempotent
    work and has no deterministic bound.
  - Expose a neutral failure object and split classification (domain) from
    policy (application). Chosen.
- Decision:
  - `agent_loop/domain/faults.py` owns `FailureCategory`, `ExecutionState`,
    `ExecutionFailure`, `FailureClassification`, and `classify_failure`.
    Categories cover runtime unavailable/unhealthy, authorization, model
    unavailable, execution failure, timeout, invalid request/result, quality
    failure, cancellation, retry exhaustion, and unknown.
  - `AgentExecutionResult` gains one additive neutral field, `failure:
    ExecutionFailure | None = None`. This is provider-neutral and does not widen
    the AgentRuntime port with OpenCode details. S22 maps its codes to categories
    and records the observed execution state; it performs no retry/escalation.
  - `classify_failure` is deterministic. Unknown execution state is never
    retry-safe. A non-idempotent operation may only be repeated automatically
    when execution demonstrably did not start (`NOT_STARTED`); otherwise
    `effects_possible=True` and automatic recovery stops.
  - `agent_loop/application/recovery_policy.py` owns `RecoveryLimits`,
    `RecoveryContext`, `EscalationAvailability`, `RecoveryAction`, and
    `decide_recovery`. Evaluation order is: total-budget exhaustion, unknown
    recoverability, non-recoverable terminal, unsafe automatic retry, safe
    same-combination retry, escalation, then human intervention. When the
    failure is runtime-level (unavailable, unhealthy, authorization), runtime
    escalation is preferred; model escalation is used for model/result/quality
    failures before falling back to another runtime. All budgets are explicit
    positive integers.
  - `agent_loop/application/execution_orchestrator.py` owns the bounded loop.
    It builds requests only through S16, executes only through injected
    AgentRuntime ports, preserves attempts/evidence/routing history in memory,
    and re-enters S13 for escalation with attempted combinations excluded.
  - Quality assessment itself remains S18. S14 only consumes a
    `QUALITY_FAILURE`; a quality failure never becomes success or a silent
    terminal success.
  - There is no persistence/resume system in S14; durable attempt history is a
    later concern. No Notion ingress or telemetry is added.
- Consequences:
  - Adapters remain policy-free; the control plane owns retry/escalation.
  - Non-idempotent failures after start require human verification instead of
    duplicate execution.
  - No alternatives yields human intervention or explicit terminal closure, not
    a fallback.
  - ADR-048/050 dispatch frontier is now exercised by an application consumer;
    Notion ingress and durable resume remain outside this slice.
- Evidence:
  - `agent_loop/domain/faults.py`
  - `agent_loop/application/recovery_policy.py`
  - `agent_loop/application/execution_orchestrator.py`
  - `agent_loop/adapters/opencode_server/faults.py`
  - `tests/agent_loop/domain/test_faults.py`
  - `tests/agent_loop/application/test_recovery_policy.py`
  - `tests/agent_loop/application/test_execution_orchestrator.py`
  - `tests/agent_loop/adapters/opencode_server/test_e2e_control_plane.py`
  - `ARCHITECTURE.md` § Agent-loop Package Boundaries
  - `REPO_MAP.md` agent_loop application entries
