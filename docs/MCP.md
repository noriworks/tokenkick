# TokenKick MCP Server

TokenKick ships a local stdio MCP server for AI agents:

```bash
tk mcp serve
```

`tk-mcp` remains as a compatibility wrapper, but new client config should use
`tk mcp serve`. The server wraps the public `tk` JSON/app-mode commands. It
does not write TokenKick state directly, does not expose hidden diagnostics,
and does not accept arbitrary `tk` arguments.

## Client Setup

Use TokenKick's setup commands instead of editing MCP client files by hand:

```bash
tk mcp status
tk mcp doctor
tk mcp install --client codex --yes
tk mcp install --client claude-desktop --yes
tk mcp install --client claude-code --yes
```

Supported clients:

- Codex global config: `~/.codex/config.toml`
- Claude Desktop on macOS:
  `~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Code: configured through `claude mcp add-json --scope user`; TokenKick
  never edits `~/.claude.json`.

For bundled macOS app installs, use the stable helper path:

```bash
tk mcp install --client codex --use-helper --yes
```

The helper lives under `~/Library/Application Support/TokenKick/`, reads the
current runtime path, and executes `tk mcp serve`. This avoids pointing Codex or
Claude directly at a movable app bundle path.

To inspect or repair:

```bash
tk mcp status --json-output
tk mcp repair --client codex --yes
tk mcp remove --client codex --yes
```

To generate a manual snippet without writing files:

```bash
tk mcp config-snippet --client codex
```

The generated Codex shape is:

```json
{
  "command": "/absolute/path/to/tk",
  "args": ["mcp", "serve"],
  "env": {"TOKENKICK_TK_PATH": "/absolute/path/to/tk"}
}
```

All mutation commands require `--yes`. TokenKick preserves unrelated MCP
servers and unknown config keys, writes timestamped backups before changing
existing config files, and refuses malformed TOML/JSON instead of overwriting.

## Safety Model

Every tool returns a normalized result with:

```json
{
  "ok": true,
  "risk": "diagnostic_read",
  "provider_refresh": false,
  "may_read_environment": true,
  "command_summary": "tk status --json-output",
  "payload": {},
  "warnings": [],
  "error": null
}
```

Risk levels:

- `cached_read`: no provider refresh and no mutation.
- `diagnostic_read`: read-only diagnostics that may inspect environment,
  paths, binaries, pidfiles, config, and cache. `tokenkick_snapshot` and
  `tokenkick_doctor` use this.
- `live_provider_read`: may contact providers, but does not mutate TokenKick
  state intentionally.
- `low_risk_mutation`: changes TokenKick configuration or pending kicks.
- `quota_consuming`: may consume provider usage.
- `dangerous_quota_operational`: may refresh providers, execute due pending
  kicks, and auto-kick eligible accounts. `tokenkick_run_apply` uses this.
- `dangerous_recovery`: force recovery kicks. Requires the strongest
  acknowledgments.

Mutations and quota-consuming tools require a preview token. Tokens are
single-use, expire after ten minutes, and bind the exact command arguments.
Preview tools only mint tokens when the preview succeeds and represents an
executable next step. Failed, blocked, skipped, or no-op previews return no
token.

`tokenkick_snapshot` redacts local path-like fields by default. Call it with
`include_paths=true` only when an agent needs runtime path diagnostics.

## Normal Workflow

1. Read state:

   ```text
   tokenkick_status
   tokenkick_snapshot
   tokenkick_doctor
   ```

2. Preview a mutation or quota-consuming action:

   ```text
   tokenkick_plan_preview
   tokenkick_kick_preview
   tokenkick_run_dry_run
   ```

3. Execute only with the returned `preview_token` and explicit acknowledgments:

   ```json
   {
     "preview_token": "...",
     "confirm": true
   }
   ```

Quota-consuming tools also require:

```json
{
  "quota_ack": true
}
```

Force recovery also requires:

```json
{
  "force_ack": true
}
```

## Rules For Agents

- Start with `tokenkick://agent-playbook`.
- Prefer cached reads.
- Use live refresh only when freshness matters.
- Treat `tokenkick_snapshot` as runtime/environment-revealing diagnostics even
  though it does not refresh providers.
- Do not call quota-consuming tools unless the user approved the exact preview.
- Do not use force recovery unless the user understands it bypasses local
  guards and consumes a small amount of provider usage.
- Treat `tokenkick_run_apply` as dangerous. It can execute due pending kicks and
  opportunistic auto-kicks.

## Automatic kick controls

- `tokenkick_auto_status()` returns saved account labels, family, identity
  fingerprints and master/weekly/session flags.
- `tokenkick_auto_set_preview(account="<label>", enabled=true, scope="all")`
  returns the exact target/settings, provider disclosure and a one-use token.
- After the user accepts the disclosure, call `tokenkick_auto_set` with the same
  account, enabled and scope, the token, `confirm=true`, `quota_ack=true`, and
  `accept_risk=true`. Enabling may cause future quota consumption as soon as the
  existing daemon evaluates the account. It does not start a daemon itself.
- To disable, preview `enabled=false` and apply with the token and `confirm=true`;
  no quota or provider-risk acknowledgement is needed.

`scope="all"` changes the master, weekly and session flags together. `weekly`
and `session` preserve the master flag. Account identity or setting changes
invalidate a preview. Tokens expire after ten minutes and cannot be replayed.
Antigravity requires a native CLI family account; aggregate compatibility
accounts cannot be enabled.

### Hermes over SSH

Hermes supports a stdio server in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  tokenkick:
    command: /usr/bin/ssh
    args: ["-T", "-o", "BatchMode=yes", "tokenkick@your-tailscale-host", "/home/tokenkick/.local/bin/tk-mcp"]
    connect_timeout: 30
    timeout: 180
```

Use the existing application user's credentials and home. Keep stdout reserved
for MCP messages; do not allocate a TTY. Restart/reload Hermes's MCP connections
after changing this entry or upgrading the server.

Example prompt: “Use TokenKick to show my auto-kick settings, then preview
activating both Antigravity families and my Claude account. Show me the provider
disclosures before applying. Keep my Codex settings unchanged.”
