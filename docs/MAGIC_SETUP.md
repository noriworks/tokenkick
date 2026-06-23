# Magic Setup (Advanced)

This guide is for users who already understand their local provider CLIs,
multiple account homes, background daemons, and the risk of automated access.
It is intentionally separate from the safe quickstart.

Use this flow at your own risk. Auto-kick can send minimal provider requests
automatically on your accounts, and scheduled or automated access may violate a
provider's terms. TokenKick does not increase quota, bypass limits, or make
providers permit automation. You are responsible for your accounts and for
complying with each provider's terms.

## What "Magic Setup" Means

In TokenKick, setup is discovery plus local configuration:

1. Find local Codex and Claude homes/state where available.
2. Save discovered accounts to `~/.tokenkick/config.json`.
3. Save newly discovered accounts with auto-kick disabled.
4. Preserve existing saved auto-kick choices on rediscovery.
5. Offer optional next steps in the interactive TUI.

Setup does not grant TokenKick new provider credentials. It does not move your
provider tokens into TokenKick. It does not start auto-kick for newly discovered
accounts.

## Safe Baseline

Use this first, especially on a new machine:

```bash
pipx install tokenkick
tk
```

For headless setup:

```bash
TK_NO_INTERACTIVE=1 tk setup
TK_NO_INTERACTIVE=1 tk status --json-output
```

Review the saved accounts before enabling automation:

```bash
tk accounts list
tk status --refresh
tk doctor
```

## Advanced Multi-Home Codex Setup

Codex account isolation depends on separate Codex homes. Each account you want
TokenKick to operate should have its own provider home. Validate each home with
Codex before enabling TokenKick automation for it.

Example shape:

```bash
CODEX_HOME=/path/to/codex-personal codex
CODEX_HOME=/path/to/codex-work codex
TK_NO_INTERACTIVE=1 tk setup
TK_NO_INTERACTIVE=1 tk status --refresh --json-output
```

If setup reports duplicate or unhealthy Codex homes, do not enable auto-kick for
them until the status is usable:

```bash
tk auto disable "<label>"
tk accounts hide "<label>"
```

On macOS, Codex direct status or kicks may trigger a system prompt saying your
terminal wants to control `Codex Computer Use.app`. That prompt comes from the
Codex CLI/helper behavior. Allowing it usually gives full Codex direct behavior;
denying it may leave Codex status or kicks degraded or stale.

## Enabling Automation

Auto-kick is off by default. Enable it only after checking status and accepting
the provider-specific risk notice:

```bash
tk auto enable "<label>"
```

TokenKick requires typing `ENABLE` for Codex and Claude before it saves the
setting. That acknowledgment is versioned per provider, so the same approved
text is not shown repeatedly after acceptance.

For scheduled work windows:

```bash
tk schedule set --account "<label>" --weekdays 18:00-23:30 --timezone Europe/Berlin
tk daemon --background
```

Check what the daemon would do before letting it run unattended:

```bash
TK_NO_INTERACTIVE=1 tk run --dry-run --json-output
```

## Agent-Driven Setup

Agents should stay headless and deterministic:

```bash
TK_NO_INTERACTIVE=1 tk setup
TK_NO_INTERACTIVE=1 tk status --json-output
TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-23:30 --json-output
```

Do not let an agent run quota-consuming commands until it has shown you the
exact command and you have approved it. See `docs/AGENT_PLAYBOOK.md` for the
agent operating contract.

## Recovery

If an advanced setup goes wrong:

```bash
tk daemon --stop
tk remote telegram --stop
tk auto disable "<label>"
tk accounts hide "<label>"
tk doctor
```

You can inspect the local state under `~/.tokenkick/`. Do not paste provider
tokens, OAuth files, cookies, or private account data into public issues.
