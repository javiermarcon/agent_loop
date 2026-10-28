# Claude Code discovery fixtures

- `observed_v2_1_283_*.txt` / `*.json`: captures from local Claude Code CLI
  v2.1.283 (`/home/test-user/.local/bin/claude` →
  `~/.local/share/claude/versions/2.1.283`, 2026-10-28). Not full dumps.
  Personal absolute paths in these fixtures were anonymized to the neutral
  `/home/test-user/...` prefix; field names, types, and parse-relevant values
  are otherwise preserved.
  - `observed_v2_1_283_version.txt`: exact `claude --version` stdout.
  - `observed_v2_1_283_auth_status_not_logged_in.json`: exact
    `claude auth status --json` while logged out (paths anonymized).
  - `observed_v2_1_283_auth_status_not_logged_in.txt`: exact
    `claude auth status --text` while logged out.
  - `observed_v2_1_283_mcp_list_empty.txt`: empty MCP inventory message
    (only this observed message is a VALID empty inventory; blank stdout is
    INVALID).
  - `observed_v2_1_283_mcp_list_pending*.txt`: project `.mcp.json` servers in
    a temporary directory (not the operator's home config) showing
    `⏸ Pending approval`. Transports HTTP/SSE/stdio observed.
  - `observed_v2_1_283_mcp_list_ws_invisible.txt`: project with only
    `type: ws` yielded the health header and **no rows** (`mcp list`
    formatter returns null for WebSocket; live stdout was the header plus
    trailing blank lines, normalized to a single trailing newline by the
    repo EOF fixer). Server still visible via `mcp get`
    (`observed_v2_1_283_mcp_get_pending_ws.txt`).
  - `observed_v2_1_283_plugin_list_empty.txt`: exact empty plugin list.
  - **OPEN:** no observed logged-in `auth status` JSON (would contain email);
    no observed Connected/Disabled/Failed live MCP rows; no observed
    non-empty `plugin list` format; no read-only models inventory CLI.
- `synthetic_*.txt` / `synthetic_*.json`: hand-authored edge cases and status
  vocabulary derived from Claude Code 2.1.283 binary/help strings, not live
  captures. Includes Connected/Disabled/needs-auth/Failed/Rejected and
  malformed/duplicate/partial auth JSON.
