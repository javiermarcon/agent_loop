# OpenCode discovery fixtures

- `observed_v2_1_4_*.txt` / `observed_v2_1_4_debug_agents.json`: truncated
  captures from a local OpenCode CLI v2.1.4 (`opencode` resolved from `PATH`,
  2026-10-26 / 2026-10-27). Not full dumps.
  - `observed_v2_1_4_models_sample.txt` keeps a short `provider/model` sample
    including one real Ollama tag with `:` (`ollama/gemma3:4b`).
  - `observed_v2_1_4_debug_agents.json` is the exact stdout of
    `opencode debug agents` (OpenCode v2.1.4): an empty JSON array (`[]\n`).
    Re-checked 2026-10-27 with user config and empty cwd; still `[]`.
    **OPEN:** no observed non-empty agents capture yet. Populated agent object
    shape remains covered only by `synthetic_agents*.json` until a real
    non-empty CLI response can be captured without destructive config changes.
  - `observed_v2_1_4_mcp_list_empty.txt` is the exact empty-list message
    (`No MCP servers configured`) observed when no MCP servers are configured.
- `synthetic_*.txt` / `synthetic_*.json`: hand-authored scenarios for statuses
  and edge cases not present in the observed capture. Not real CLI output.
  Includes invalid/mixed agent arrays, MCP rows with unknown statuses or
  trailing garbage, duplicate identifiers, and illegible models lines.
