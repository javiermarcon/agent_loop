# S23 — Telemetry event schema

This document defines the implemented telemetry contract for Agent Loop. It
describes observed facts only; it does not grant new execution, routing,
recovery, persistence, or external-service capabilities.

## Responsibilities and limits

- `agent_loop/ports/telemetry.py` owns the immutable event vocabulary, usage
  value object, provenance, cost source, and the outbound `TelemetrySink`
  protocol.
- `agent_loop/telemetry/recorder.py` owns event correlation, sequence
  assignment, defensive redaction, bounded attributes, best-effort delivery,
  and conservative usage aggregation.
- `agent_loop/adapters/telemetry/sinks.py` owns the local sinks:
  `InMemoryTelemetrySink` (bounded, inspectable) and `NullTelemetrySink`
  (disabled). No bundled sink contacts a network service or writes to disk.
- `agent_loop/application/execution_orchestrator.py` publishes lifecycle,
  routing, execution, retry, escalation, failure, and closure facts from the
  S13/S14/S16 control loop.
- `agent_loop/autonomy/telemetry.py` publishes agent-command, validation-tool,
  quality, Git-attribution, and closure facts from the autonomous target
  repository loop.

Telemetry does not select runtimes/models, classify failures for recovery,
decide retries or escalation, execute tools, or alter execution results. Those
decisions remain owned by S13, S14, S16, S22, and the existing autonomy
execution loop.

## Event envelope

Every event is a `TelemetryEvent` with:

| Field | Meaning |
|---|---|
| `event_id` | `"<execution_id>:<sequence>"`; stable within one recorder. |
| `execution_id` | Correlation id shared by all events of one run. |
| `sequence` | Monotonic per-recorder order starting at `0`. |
| `name` | One `TelemetryEventName` value. |
| `occurred_at` | Emission timestamp from the recorder clock (UTC by default). |
| `provenance` | `observed` or `inferred`; inferred values must say so. |
| `target_repository` | Target workspace path supplied by the caller. |
| `work_item_id` | WorkItem identity when the path uses a WorkItem. |
| `attempt` | 1-based iteration/attempt number when applicable. |
| `runtime_id`, `model_id`, `session_ref` | Observed execution identities. |
| `status` | Normalized terminal/phase status string. |
| `duration_ms` | Observed or wall-clock duration when measured. |
| `failure_category`, `execution_state` | Neutral S14 failure facts only. |
| `usage` | Optional token/cost observation (`TelemetryUsage`). |
| `attributes` | Bounded scalar facts, redacted before emission. |

Event names implemented:

```text
work_item_received
routing_completed
execution_started
execution_completed
execution_failed
retry_started
escalation_started
tool_completed
quality_completed
changes_detected
work_item_closed
```

`assessment_completed` remains outside the implemented schema because the
current control loop receives an already-built `TaskAssessment`; emitting a new
assessment event would duplicate S11 without a real generation point.

## Correlation

- One recorder represents one execution identity.
- Every event carries the same `execution_id`; `event_id` and `sequence` make
  order explicit.
- Attempts, retries, and escalations keep the WorkItem identity and use
  increasing `attempt` values.
- S13 routing events report the selected runtime/model/strategy and eligible,
  evidence, and reason counts. They do not re-run routing.
- S14 failure events report the classified category, observed execution state,
  and recovery action. They do not re-run recovery.
- S22 execution events report the runtime’s normalized result identity,
  session, model, evidence count, observed-failure count, and usage.

## Usage and cost model

`TelemetryUsage` preserves each metric independently:

- `input_tokens`, `output_tokens`, `reasoning_tokens`.
- `cost_micros` plus `cost_source` (`declared`, `calculated`, or `unknown`).

Rules:

- Missing metrics are `None`, never zero.
- `cost_source=unknown` requires `cost_micros=None`.
- A known cost source requires `cost_micros`.
- Reasoning tokens are recorded only when the adapter publishes the documented
  `reasoning_tokens` harness-metadata key; they are never inferred.
- A run-level aggregate is produced only when every attempt reported usage.
  Partial attempt data yields an unknown aggregate rather than a misleading
  partial sum.

## Privacy and redaction

Telemetry does not record:

- prompts, requirement text, WorkItem objective text, or repository file content;
- stdout, stderr, tool messages, provider error detail, or command lines;
- credentials, passwords, tokens, authorization headers, or embedded URL
  credentials.

Failure events keep only the structured `FailureCategory` and `ExecutionState`.
Evidence references and free-form failure details stay in the existing result
and reporting paths. Attribute values are still defensively redacted and
length-bounded as defense in depth.

## Failure isolation, disabling, and limits

- `TelemetryRecorder.record` catches sink exceptions. A broken observability
  backend cannot change the outcome of the observed run.
- Passing no sink disables telemetry. `NullTelemetrySink` is an explicit
  disabled sink for composition roots.
- `TelemetryLimits` bounds event volume (`max_events`, including `0` for
  disabled) and attribute length. `InMemoryTelemetrySink` also enforces its own
  retention bound.
- No queue, worker, database, HTTP endpoint, dashboard, or external SaaS is
  part of S23.

## Integration summary

- S13: `routing_completed` consumes the real `RoutingDecision`.
- S14: `execution_failed`, `retry_started`, `escalation_started`, and
  `work_item_closed` consume the real classification/recovery decision.
- S16: the WorkItem identity and schedulability gate are reflected in
  `work_item_received` and blocked closure.
- S22: `execution_started`/`execution_completed` consume the real
  `AgentExecutionRequest` and `AgentExecutionResult`.
- Autonomy: `tool_completed` reports each observed validation-tool result,
  including `skipped`, `unavailable`, and `unknown` states (it does not claim
  successful execution); `quality_completed`, `changes_detected`, and
  agent-command events consume existing `ToolResult`, `ValidationReport`,
  `AgentRunResult`, and `GitAudit` values.

## Explicitly outside S23

- Durable telemetry persistence, resume, export pipelines, and query APIs.
- External observability providers.
- Cost calculation from a pricing catalog; only declared/unknown costs are
  represented.
- Replacing existing logs, reports, or quality evidence.
