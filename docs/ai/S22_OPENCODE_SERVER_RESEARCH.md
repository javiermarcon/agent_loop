# S22 — OpenCode Server technical research

Date: 2026-11-08
Repository: `src/agent_loop`
Branch: `agent_loop-rebuild`
Installed binary: `opencode` (resolved from `PATH`)

## 1. Verified version and environment

Commands actually executed:

```text
$ opencode --version
opencode v2.2.6

$ opencode serve --help
DESCRIPTION
  Start the v2 API and web server
FLAGS
  --hostname string
  --port integer
  --cors string
  --service
  --stdio
```

A server started for probing printed:

```text
server listening on http://127.0.0.1:4789
server password <redacted>
```

`opencode serve --hostname 127.0.0.1 --port 0` also works: the OS assigns a
port and the server reports the effective URL, so managed startup does not need
to guess a free port.

## 2. Official documentation consulted

- The running server exposes its public contract at `GET /openapi.json`
  (`openapi: 3.1.0`, 139 documented paths). The file was downloaded from the
  verified 2.2.6 server and is the contract source for this slice.
- `opencode --help`, `opencode serve --help`, `opencode api --help`,
  `opencode session --help`, and `opencode run --help` from the installed
  binary.
- `GET /api/agent` and `GET /api/model` from the running server for the
  observed permission actions and model inventory.

No private endpoint, hidden RPC, or undocumented HTTP surface is used.

## 3. Selected transport

- HTTP/1.1 + JSON over the public `/api/**` HTTP API.
- Server-Sent Events (`GET /api/event`, `text/event-stream`) for execution
  progress, terminal state, and permission requests.
- HTTP Basic authentication. The password printed by the server is the secret;
  the verified username is `opencode`. The client sends
  `Authorization: Basic base64("opencode:<password>")`.
- No OpenCode SDK or provider SDK is imported. The adapter uses Python's
  standard library (`http.client`, `json`, `base64`).

Rationale: the HTTP API is the documented server contract, the SSE stream is
the documented event channel, and both avoid shelling out per task or parsing
unstable stdout.

## 4. Public endpoints and contracts used by S22

| Purpose | Method and path | Contract |
|---|---|---|
| Identity, version, readiness | `GET /api/info` | `ServerInfo` (`version`, `pid`, `urls`, `paths`) |
| Model inventory | `GET /api/model` | `{data: [{providerID, id, ...}]}` |
| Agent inventory | `GET /api/agent` | `{data: [{id, name, mode, permissions, ...}]}` |
| Create session | `POST /api/session` | `{title?, agent?, model?, location:{directory}, permissions?}` → `{data: Session.Info}` |
| Read session | `GET /api/session/{id}` | `{data: Session.Info}` |
| Send prompt | `POST /api/session/{id}/prompt` | `{text, metadata?, delivery?, resume?}` |
| Interrupt | `POST /api/session/{id}/interrupt` | `{interrupted: bool}` |
| Messages | `GET /api/session/{id}/message` | `{data: Session.Message.Info[], cursor}` |
| File diffs | `GET /api/session/{id}/diff` | `{data: FileDiff.Info[]}` |
| Events | `GET /api/event?types=...` | SSE `data: V2Event` |
| Permission reply | `POST /api/session/{id}/permission/{requestID}/reply` | `{decision: once|always|reject}` |

`POST /api/experimental/session/{id}/wait` exists and was probed successfully,
but it is explicitly marked experimental. S22 does **not** use it; it waits on
the non-experimental event stream and falls back to session state on stream
loss.

## 5. Event model

Observed SSE frame format:

```text
data: {"id":"evt_...","type":"server.connected","data":{}}

: heartbeat

data: {"id":"evt_...","created":1794159459072,"type":"session.execution.started",
       "data":{"sessionID":"ses_..."},
       "durable":{"aggregateID":"ses_...","seq":2,"version":1}}
```

Relevant documented event types:

- `session.execution.started`
- `session.execution.succeeded` → `data.sessionID`
- `session.execution.failed` → `data.sessionID`, `data.error`
- `session.execution.interrupted` → `data.sessionID`, `data.reason`
- `session.idle` → `data.sessionID`
- `permission.asked` → `data.{id, sessionID, action, resources, ...}`

Heartbeat comments (`: heartbeat`) are ignored. The client filters events by
`data.sessionID`; unrelated sessions on the same server are never consumed as
this execution's terminal event.

## 6. Authentication and authorization

- `/` and the browser UI are served without authentication.
- `/api/**` requires the password; an unauthenticated request returns
  `401 {"_tag":"UnauthorizedError","message":"Authentication required"}`.
- Basic auth with username `opencode` and the printed password succeeds.
- The password is a runtime secret. It is never placed in argv, logs,
  capability evidence, evidence refs, metadata, or exceptions.
- Managed startup captures the password from the server's stdout. Attach mode
  reads it from an explicit configuration/environment value owned by the
  caller.
- A successful connection does not imply authorization: `200` on `/api/info`
  with credentials is treated as authorization evidence, while a `401` yields
  `authorized=NO` and `available=NO`.

## 7. Observed execution flow

A real probe against v2.2.6, using a temporary Git repository:

1. `POST /api/session` with `location.directory` and
   `model={"providerID":"opencode-go","id":"deepseek-v4.1-flash"}` returned
   `ses_...`.
2. `POST /api/session/{id}/prompt` admitted the prompt.
3. `session.execution.succeeded` arrived on `/api/event`.
4. `GET /api/session/{id}` reported `outcome=succeeded` and token usage.
5. `GET /api/session/{id}/message` returned user, assistant (reasoning/text/
   tool), system, and idle messages.
6. `GET /api/session/{id}/diff` returned per-file patches including the
   expected `calc.py` and `test_calc.py` changes.
7. The fixture's pytest suite passed after the agent edits.

This confirms the full "WorkItem → request → session → prompt → terminal event
→ normalized result → evidence" flow is achievable with public APIs only.

## 8. Version compatibility policy

- Parse the semantic version reported by `GET /api/info`.
- Supported: major `2`, minor `>= 2` (verified on `2.2.6`).
- Incompatible: any other parsable semantic version.
- Unknown: missing, non-semantic, or unparsable version.

An unknown version is never reported as supported and `execute` fails closed.
The installed CLI-only discovery path (S09) keeps its own version handling and
is not reused as a server-version proof.

## 9. Known limitations

- `AgentExecutionResult` has no dedicated textual-content field. S22 maps the
  final assistant text to `structured_output` for schema-less requests and
  rejects requests that declare `structured_output_schema` (the server prompt
  API does not expose a documented structured-output contract). A future ADR
  may add a neutral `content` field to the port.
- The server's model catalog lists models, not provider credentials or
  executable health. Inventory presence never proves a model can run.
- Permission rules are harness-enforced. S22 adds only restrictive rules and
  rejects `permission.asked` requests that the control plane did not pre-allow.
  It cannot guarantee that a third-party MCP tool has no side effects.
- The browser UI, PTY, filesystem write, and configuration endpoints are not
  used by the adapter.
- Managed startup inherits the operator's OpenCode configuration (including
  MCP servers). Startup/readiness is bounded by the configured timeout, and
  shutdown terminates the whole owned process group.

## 10. Risks and pending decisions

- Long-running SSE connections need explicit timeouts and connection cleanup;
  implemented with a reader thread, a deadline, and socket shutdown.
- External cancellation is not part of the `AgentRuntime` port. S22 exposes an
  adapter-level interrupt API plus timeout-driven interruption, without
  widening the port.
- S14 failure escalation is not implemented in the current repository. S22
  classifies failures into neutral codes and does not retry or escalate;
  policy remains blocked on S14.
- S13 has declarative compatible-candidate resolution but no runtime selector
  winner. S22 exposes a distinct `opencode-server` inventory identity so the
  existing candidate resolution can recognize it without adding parallel
  routing.
