# TokenKick Commands

This file is a command reference. Start with Main Commands and Common Workflows;
use Advanced And Diagnostics only when debugging, testing provider behavior, or
working on TokenKick itself.

## Main Commands

### Human TUI

```
tk                             # Open the interactive command center.
TK_NO_INTERACTIVE=1 tk         # Print status instead of opening the menu.
tk menu                        # Force the interactive command center.
```

In the TUI, submenus, confirmations, and value-entry actions include an explicit
Back path. Esc also acts as Back for normal prompts. Auto-kick controls are
under `Configure` -> `Auto-kick`. Account status, visibility, notification
routes, persistent planning defaults, and orchestration roles are under
`Configure` -> `Accounts`. Codex Burst ladder, surface order, gap, and demotion
controls are under `Configure` -> `Codex surface strategy`. Telegram remote
status controls are under `Configure` -> `Remote status (Telegram)`. The
`Schedule` menu contains orchestration planning, recurring smart schedule
configuration, and schedule status. Orchestration previews first and only
writes pending kicks after an explicit apply confirmation.
Compact/verbose history is a top-level `History` menu. Verbose status details
and read-only Codex surface stats are under `Diagnostics`.

### Status

```
tk status                      # Show visible accounts from daemon/cache.
tk status --refresh            # Fetch live provider data now.
tk status --account "<label>"  # Show one account, including hidden accounts.
tk status --codex              # Show Codex accounts only.
tk status --all                # Include hidden accounts.
tk calendar                    # Show predicted resets for the next 7 days.
```

`tk status --refresh` is the live provider check. It updates status only; it
does not run the daemon decision loop and does not kick accounts.

### Run And Kick

```
tk run                         # Refresh, kick eligible auto-enabled accounts, summarize.
tk run --dry-run               # Preview run decisions without kicking.
tk kick "<label>"              # Kick one account now if eligible.
tk kick --all --dry-run        # Preview bulk kick decisions.
tk wake "<label>"              # One-time bootstrap kick for a dormant account.
```

Use `--force` sparingly and only when you understand why local guards are in the
way:

```
tk kick "<label>" --force
```

Gemini and Antigravity are monitor-only; kick and auto-kick commands reject
those accounts.

`tk plan` estimates usable coverage from per-account `usable_session_minutes`.
If that is unset, `plan_tier` and `schedule.usable_session_tier_defaults` provide
rough, unverified starting guesses only; measured/user-set
`usable_session_minutes` should be treated as the source of truth. For one
specific plan, use `tk plan --usage "<label>=3h"` to override that estimate
without changing saved account calibration.

### Configure

```
tk setup                       # Auto-discover and save config.
tk auto status                 # Show auto-kick settings.
tk auto enable "<label>"       # Enable master, weekly, and session auto-kick.
tk accounts planning           # Show account planning defaults and roles.
tk accounts set-usable "<label>" 150
                                # Set measured usable planning minutes.
tk accounts set-role "<label>" backup
                                # Set orchestration role.
tk accounts set-weekly-reserve "<label>" 70
                                # Demote to backup at 70% weekly usage.
tk accounts clear-weekly-reserve "<label>"
                                # Remove reserve threshold.
tk schedule show               # Show schedules and pending kicks.
tk notify test                 # Send a test notification to all enabled backends.
tk notify test --backend telegram
                                # Send a Telegram-only test notification.
tk daemon --background         # Start daemon in background.
tk remote telegram --background
                                # Start read-only Telegram status listener.
tk update                      # Restart stale background processes after upgrade.
```

Interactive setup saves newly discovered accounts with auto-kick disabled by
default, then offers optional next steps: review/enable auto-kick, configure
notifications, start the background daemon, and read schedule/Codex strategy
info. Existing saved auto-kick choices are preserved on rediscovery.
The first enable for Codex or Claude shows a provider-specific risk notice and
requires typing `ENABLE`; the acknowledgment is saved so it does not repeat for
the same approved text.
`TK_NO_INTERACTIVE=1 tk setup` stays headless and never prompts; start the daemon
explicitly with `tk daemon --background` when automation should run on that
machine.
Bare `tk` opens the TUI; on first run with no saved accounts, it opens setup
before the normal main menu.

## Common Workflows

### First Setup

```
tk setup
tk status --refresh
tk auto enable "<label>"
tk daemon --background
```

In the TUI, use `Configure -> Auto-kick -> Enable all usable accounts` only
after reviewing setup/status. That bulk action skips accounts whose current
status is unknown, stale, auth-expired, or otherwise unhealthy.
For stale duplicate homes, re-auth and run `tk status --refresh`, or disable/hide
the unwanted entry:

```
tk auto disable "<label>"
tk accounts hide "<label>"
```

### Check Health

```
tk status
tk doctor
tk update --check
tk daemon --status
```

### Plan A Coding Window

```
tk calendar
tk schedule show
tk plan --work-window 18:30-23:30 --json-output
tk plan --work-window 18:30-23:30 --apply --yes --json-output
tk plan cancel
tk run --dry-run
```

`tk plan` is read-only unless `--apply` is present. It uses cached status only,
does not refresh providers, and writes only orchestrated pending session kicks
that later execute through the normal daemon/scheduler guards. Future
orchestrated pending kicks suppress opportunistic auto-kick for the same
account until they are due, cleared, or explicitly overridden.
When an applied orchestration kick reserves an account, `tk status` and `tk run`
warn if that account is entering a quiet period before the planned kick and may
suggest a safer account to use instead.
Use `tk plan cancel` to remove applied orchestration pending kicks without
clearing smart schedules.

### Configure A Work Window

```
tk schedule set --default --weekdays 09:00-17:00
tk schedule set --account "<label>" --weekdays 14:00-21:00 --weekends 10:00-16:00
tk schedule show
```

### After Updating TokenKick

```
pipx upgrade tokenkick
tk update
tk doctor
```

## Command Reference

### Status And Calendar

Commands:

```
tk status
tk status --refresh
tk status --account "<label>"
tk status --account "<label>" --refresh
tk status --codex
tk status --all
tk status --verbose
tk status --json-output
tk status --json-output --codex

tk calendar
tk calendar --days 14
tk calendar --account "<label>"
tk calendar --codex
tk calendar --all
tk calendar --json-output
tk calendar --ics
```

Notes:

- `tk status` is the stable direct status command for power users and scripts.
- With a running daemon, cached status is usually as fresh as the poll interval.
- `tk calendar` is read-only. It uses local cache and schedule config; it never
  refreshes providers and never kicks accounts.

### Run And Kicking

Commands:

```
tk run
tk run --dry-run
tk run --json-output
tk run --codex

tk plan --work-window HH:MM-HH:MM
tk plan --work-window HH:MM-HH:MM --date YYYY-MM-DD --timezone Europe/Berlin
tk plan --work-window HH:MM-HH:MM --usage "codex (work)=3h" --usage "claude=90m"
tk plan --work-window HH:MM-HH:MM --json-output
tk plan --work-window HH:MM-HH:MM --apply
TK_NO_INTERACTIVE=1 tk plan --work-window HH:MM-HH:MM --apply --yes --json-output
tk plan cancel
tk plan cancel --account "<label>"
tk plan cancel --json-output --yes

tk kick "<label>"
tk kick "<label>" --force
tk kick --all
tk kick --auto
tk kick --auto --force
tk kick --all --dry-run
tk wake "<label>"
```

Notes:

- `tk run` is useful for cron-style one-shot execution.
- The daemon is better for continuous local polling.
- `tk wake` is for onboarding an account that was already dormant before
  TokenKick was configured.
- After auto-kick is enabled, normal kicks should keep the account anchored.
- `tk plan` respects account orchestration roles and reserve thresholds from
  `tk accounts planning`.

### Auto-Kick

Commands:

```
tk auto status
tk auto enable "<label>"
tk auto disable "<label>"
tk auto weekly enable "<label>"
tk auto weekly disable "<label>"
tk auto session enable "<label>"
tk auto session disable "<label>"
```

Notes:

- `tk auto enable` enables the master switch plus weekly and session kicks.
- Use `tk auto weekly disable` for session-only automation.
- Use `tk auto session disable` for weekly-only automation.
- The TUI supports all-account and selected-account auto-kick actions under
  `Configure` -> `Auto-kick`.

### Scheduling

Commands:

```
tk schedule set --default --weekdays 09:00-17:00
tk schedule set --default --weekdays 09:00-17:00 --timezone Europe/Berlin
tk schedule set --account "<label>" --weekdays 14:00-21:00 --weekends 10:00-16:00
tk schedule show
tk schedule show --account "<label>"
tk schedule disable --default
tk schedule disable --account "<label>"
tk schedule clear --default
tk schedule clear --account "<label>"
```

Notes:

- Smart scheduling applies to short rolling windows.
- For providers with both weekly and 5h/session windows, TokenKick schedules
  against the short session window when available.
- Future smart-schedule pending kicks suppress opportunistic auto-kick for the
  same account. Use `tk kick --auto --force` only when you intentionally want to
  override pending timing.

### Notifications

Commands:

```
tk notify --ntfy <topic>
tk notify --telegram <token> <chat_id>
tk notify test
tk notify test --backend ntfy
tk notify test --backend telegram
tk accounts notifications
tk accounts enable-notifications "<label>"
tk accounts disable-notifications "<label>"
tk accounts set-notifications "<label>" --ntfy
tk accounts set-notifications "<label>" --telegram
tk accounts set-notifications "<label>" --ntfy --telegram
tk accounts set-notifications "<label>" --global-default
tk accounts set-notifications "<label>" --none
```

Notes:

- The ntfy topic is the ntfy.sh topic name you subscribe to, not your
  TokenKick account label.
- Notification backend credentials are global and can store both ntfy and
  Telegram. Account notification routes control whether each account sends to
  ntfy, Telegram, both, the global default, or neither.

### Remote Status (Telegram)

Commands:

```
tk notify --telegram <token> <chat_id>
tk remote telegram
tk remote telegram --background
tk remote telegram --status
tk remote telegram --restart
tk remote telegram --stop
```

Notes:

- The Telegram remote listener is read-only. It accepts `/status`, `/refresh`,
  `/ping`, and `/help`; it never kicks, wakes, plans, or mutates accounts.
- The listener reuses the global Telegram notification token and chat ID. Run
  `tk notify --telegram <token> <chat_id>` first.
- `/status` and `/refresh` run `tk status --refresh --json-output` on the same
  server and send a compact summary back to the configured chat.
- Only the configured chat ID is allowed. Messages from other chats are ignored.
- Long polling cannot run while the bot has a webhook configured; remove the
  webhook before starting the listener.
- For boot persistence on Linux servers, install the packaged
  `tokenkick-telegram@.service` template and enable it for the TokenKick user:
  `sudo systemctl enable --now tokenkick-telegram@<user>.service`.

### Accounts

Commands:

```
tk accounts list
tk accounts detail "<label>"
tk accounts hide "<label>"
tk accounts show "<label>"
tk accounts planning
tk accounts set-usable "<label>" 150
tk accounts set-role "<label>" use-first
tk accounts set-role "<label>" normal
tk accounts set-role "<label>" backup
tk accounts set-role "<label>" specialist
tk accounts set-role "<label>" excluded
tk accounts set-weekly-reserve "<label>" 70
tk accounts clear-weekly-reserve "<label>"
tk accounts notifications
tk accounts enable-notifications "<label>"
tk accounts disable-notifications "<label>"
tk accounts set-notifications "<label>" --ntfy
tk accounts set-notifications "<label>" --telegram
tk accounts set-notifications "<label>" --ntfy --telegram
tk accounts set-notifications "<label>" --global-default
tk accounts set-notifications "<label>" --none
```

Notes:

- Hidden accounts are excluded from normal status and bulk kicking.
- `tk accounts detail` is read-only.
- Orchestration roles affect `tk plan`, not manual `tk kick` or daemon
  auto-kick eligibility.
- Only one account can be `Use first`; setting a new account to `Use first`
  demotes the previous one to `Normal`.
- `specialist` accounts are prepared in a separate readiness lane when timing
  allows, without counting as main coverage.
- Weekly reserve thresholds demote an account to backup behavior once cached
  weekly usage reaches the chosen percentage.

### Setup

Commands:

```
tk setup
tk setup --dry-run
tk setup --rename-label "<label>"
```

Notes:

- Setup preserves visibility choices where possible.
- Setup preserves existing auto-kick choices, but new accounts start with
  auto-kick disabled.
- Enabling auto-kick later can automate minimal provider requests on your
  accounts. Provider terms and any account consequences are your responsibility;
  TokenKick requires explicit `ENABLE` consent before saving that setting for
  Codex or Claude.
- On macOS, direct Codex discovery may cause Codex CLI/app-server to trigger a
  “control Codex Computer Use.app” permission prompt. Allow enables full direct
  Codex behavior; denying may leave status/kicks degraded or stale. TokenKick
  does not manage that permission directly.
- `--rename-label` opts a saved label into provider-first format.

### Daemon, Polling, And Update

Commands:

```
tk daemon --background
tk daemon --status
tk daemon --restart
tk daemon --stop
tk daemon

tk remote telegram --background
tk remote telegram --status
tk remote telegram --restart
tk remote telegram --stop
tk remote telegram

tk poll
tk poll 2
tk poll 5

tk --version
tk update
tk update --check
tk update --yes
tk update --json-output
```

Notes:

- Running daemons pick up `tk poll` changes after the current sleep finishes.
- Use `tk update` after `pipx upgrade tokenkick`; it checks whether background
  processes are stale and restarts them if needed. Use `tk update --yes` to
  restart stale background processes without a confirmation prompt.
- The Telegram remote listener is a separate read-only process and does not run
  the auto-kick daemon loop.

### MCP Server

```
tk mcp serve
tk mcp status
tk mcp doctor
tk mcp config-snippet --client codex
tk mcp install --client codex --yes
tk mcp repair --client codex --yes
tk mcp remove --client codex --yes
tk-mcp
```

`tk mcp serve` starts TokenKick's local stdio MCP server for coding agents.
`tk-mcp` remains as a compatibility wrapper. The server wraps existing
JSON/app-mode `tk` commands, uses argv-only command construction, and requires
preview tokens plus explicit acknowledgments for mutations, quota consumption,
and force recovery.

`tk mcp install`, `repair`, and `remove` configure Codex global
`~/.codex/config.toml`, Claude Desktop's macOS config, or Claude Code through
the `claude mcp` CLI. Mutation commands require `--yes`, preserve unrelated MCP
servers, write backups before changing existing config files, and refuse
malformed TOML/JSON. See `docs/MCP.md`.

### Doctor, History, And Reset Log

Commands:

```
tk doctor
tk doctor "<label>"
tk doctor --json-output
tk doctor "<label>" --json-output
tk doctor --repair

tk history
tk history --limit 50
tk history --account "<label>"
tk history --verbose
tk history --include-probes
tk history --kind status_probe
tk history --anchored
tk history --json-output

tk reset-log
tk reset-log --since 7d
tk reset-log --provider codex
tk reset-log --detail <event-id>
tk reset-log --unacknowledged
tk reset-log ack <event-id>
tk reset-log ack --latest
tk reset-log ack --all
tk reset-log --json-output
tk reset-log --csv
```

Notes:

- Use `tk doctor` after setup, after upgrades, and whenever status disagrees
  with provider UI.
- In the TUI, `History` is top-level. It has fast `All recent compact`,
  `All recent verbose`, `Account recent compact`, and `Account recent verbose`
  actions, plus `Custom limit compact` and `Custom limit verbose`.
- `tk history` hides background reconciliation probes by default.
- `tk history --anchored` shows only confirmed moved anchors. It excludes
  superseded, pending, unchanged, and failed rows.
- `tk reset-log` shows both correlated global reset events and single-account
  provider reset observations, such as a weekly usage drop to near zero.
- Single-account provider reset observations notify but never invalidate pending
  kicks. `possible` global reset events remain diagnostic by default. `likely`
  events notify but do not automatically invalidate pending kicks. Only
  `confirmed` global events mutate pending kicks automatically.
- Use `tk reset-log ack ...` to hide handled events from the status banner while
  keeping them in reset history.
- In the TUI, `Diagnostics -> Reset recovery` shows recent reset events and
  provider observations. Orchestration preview/apply is offered only for
  correlated global reset events.

## Advanced And Diagnostics

### History JSON Fields

Useful `tk history --json-output` fields:

- `success`: whether the provider command exited successfully.
- `confirmed`: whether TokenKick believes the kick anchored the visible bucket.
- `kick_model`: model requested for the kick; `null` means provider default.
- `input_tokens` / `output_tokens`: proof the provider accepted work.
- `codex_attribution`: whether a Codex reset-clock match was strong enough to
  teach the surface scorer (`strong`) or only timing evidence for dedupe/history.

### Codex Diagnostics

Commands:

```
tk codex-usage
tk codex-usage "<label>"
tk codex-usage --json-output
tk codex-surfaces "<label>"
tk codex-surfaces "<label>" --json-output
tk codex-surfaces "<label>" demotion evidence
tk codex-surfaces "<label>" demotion evidence --json-output
tk codex-surfaces "<label>" reset-stats
tk codex-surfaces "<label>" reset-all
tk codex-surfaces reset-stats --all
tk codex-surface-patterns
tk codex-surface-patterns "<label>"
tk codex-surface-patterns --json-output
tk codex-surface-patterns "<label>" --json-output
```

Notes:

- `tk codex-usage` is a status diagnostic; it does not kick.
- The selected `codex` bucket means the main/default Codex quota. Interactive
  model labels and backend quota bucket names are not always the same.
- If the provider exposes a separate Spark quota bucket, TokenKick saves it as a
  sibling account such as `codex-spark (...)`, with auto-kick disabled until
  explicitly enabled.
- Spark is bucket-detected, not tier-inferred. If a saved Spark account no
  longer exposes the bucket, status becomes Unknown and auto-kick is blocked
  until the provider exposes it again.
- `tk plan` does not use Spark from the rough `spark` tier default alone. Set a
  measured/user-chosen `usable_session_minutes` on the Spark account before
  allowing orchestration to schedule it.
- If you do not want Spark in orchestration at all, use
  `tk accounts set-role "<spark label>" excluded`.
- `tk codex-surfaces` is read-only. It shows the current adaptive attempt
  order, human-readable surface labels, capped learning scores, strong
  wins/tries, issue counters, and skipped surfaces.
- `tk codex-surfaces "<label>" demotion evidence` is read-only. It shows the
  stored strong-cluster evidence behind any current auto-demotions, including
  recent cluster winners and attempted surfaces.
- `tk codex-surfaces "<label>" reset-stats` clears learned surface
  scores/order for that account. It does not delete kick history and does not
  change demotion settings, force overrides, or demotion evidence.
- `tk codex-surfaces "<label>" reset-all` clears learned surface stats and
  demotion evidence for that account. It still leaves kick history, demotion
  settings, and force overrides unchanged.
- `tk codex-surfaces reset-stats --all` clears learned surface stats for every
  Codex account.
- `tk codex-surface-patterns` is experimental and read-only. It backtests
  friendly prediction rules over history, prints a plain-English verdict first,
  never changes live ranking, and reports that the current per-account learning
  score should stay in place unless enough strong historical clusters show
  stable lift.

Codex surface behavior:

- With Burst ladder disabled, Codex kicks use the patient adaptive surface
  ladder.
- New accounts start with `repo-skip`, then `legacy`, then `repo`, then
  `interactive-like`.
- Learned per-account scores can reorder those four surfaces.
- Per-account auto-demotion is opt-in. When enabled, redundant tail surfaces can
  be hidden from the ladder after enough strong clusters, while force-keep and
  force-prune remain manual controls.
- `interactive-like` uses the account Codex home as cwd to approximate a normal
  CLI session.
- Only `codex_attribution=strong` updates learned surface scores.
- Timing-only matches confirm/dedupe history but do not train the scorer.
- Later refreshes can repair recent ambiguous clusters by marking the closest
  generated attempt as `method=late_reset_clock`.
- Generated attempts after a repaired winner remain in history with
  `post=superseded`.

### Codex Surface Strategy

Commands:

```
tk codex-strategy status
tk codex-strategy status --json-output
tk codex-strategy enable
tk codex-strategy disable
tk codex-strategy order legacy repo-skip repo interactive-like
tk codex-strategy order repo legacy
tk codex-strategy order --reset
tk codex-strategy gap 90
tk codex-strategy demotion enable "<label>"
tk codex-strategy demotion disable "<label>"
tk codex-strategy demotion enable --all
tk codex-strategy demotion disable --all
tk codex-strategy demotion set "<label>" --after-strong-clusters 5 --min-active-surfaces 2
tk codex-strategy demotion force-keep "<label>" legacy repo-skip
tk codex-strategy demotion force-prune "<label>" interactive-like
tk codex-strategy demotion clear-overrides "<label>"
tk codex-strategy demotion evidence "<label>"
tk codex-strategy demotion reset-evidence "<label>"
```

Notes:

- The same controls are available in the TUI under `Configure` ->
  `Codex surface strategy`. The read-only `tk codex-surfaces` report is under
  `Diagnostics`.
- `tk codex-strategy enable` turns on Burst ladder for auto/scheduled Codex
  kicks. `disable` returns to the patient adaptive ladder.
- Burst ladder and auto-demotion are separate switches:

  | Burst ladder | Auto-demotion | Meaning |
  | --- | --- | --- |
  | On | Off | Fast burst fires your configured surfaces every time. It may still learn scores, but it will not auto-hide surfaces. |
  | On | On | Fast burst fires only the effective active set: configured surfaces minus demoted/pruned ones, plus force-kept ones. Strong confirmations can train future demotion. |
  | Off | Off | Patient adaptive ladder. It uses learned scoring/order, but does not auto-hide surfaces. |
  | Off | On | Patient adaptive ladder plus auto-demotion. It can skip demoted surfaces, but still uses the slow retry/backoff behavior. |

- Burst ladder is off by default. It runs the configured surface set in
  serialized order at the configured gap, without mid-burst early-stop, then
  waits for normal status polling and late reset-clock attribution.
- Burst ladder default gap is `90s`.
- Burst ladder default surface set is `legacy, repo-skip, repo,
  interactive-like`.
- The `order` command controls both which surfaces fire and their order. A
  subset fires only those surfaces.
- Strong late reset-clock attribution from Burst ladder trains the same
  scorer/demotion evidence as the patient ladder.
- Per-account demotion settings apply wherever that account's surface set is
  resolved. Force-pruned surfaces are manual overrides and are not
  auto-reintroduced on a miss.
- `tk codex-fire-all ...` remains as a deprecated compatibility alias for
  `tk codex-strategy ...`.

Headless config and env overrides:

```
codex_burst_ladder_enabled
codex_burst_ladder_gap_seconds
codex_burst_ladder_surface_order
TK_CODEX_BURST_LADDER_ENABLED
TK_CODEX_BURST_LADDER_GAP_SECONDS
TK_CODEX_BURST_LADDER_SURFACE_ORDER
```

The old `codex_fire_all_*` config keys and `TK_CODEX_FIRE_ALL_*` env overrides
are still accepted as compatibility aliases.

### Claude Direct Usage

Commands:

```
tk claude direct-usage enable
tk claude direct-usage disable
```

Notes:

- Per-account direct-usage controls are under `tk accounts set-direct-usage`.
- Claude direct `/usage` is not a purely passive read.
- Explicit refreshes may use it, but repeated refreshes within 5 minutes reuse
  the recent direct result.
- Background daemon status refresh avoids silently running `/usage`.
- When `/usage` is needed to anchor a due Claude session, TokenKick treats it as
  a tracked session kick with history/logging/notification semantics.

### Advanced Account Switches

Commands:

```
tk accounts enable-probe "<label>"
tk accounts disable-probe "<label>"
tk accounts set-direct-usage "<label>" --enable
tk accounts set-direct-usage "<label>" --disable
```

Notes:

- Explicit probes may consume provider quota. Keep them disabled unless you are
  deliberately testing provider status behavior.

### Kick Model Overrides

Commands:

```
tk model set "<label>" <model>
tk model clear "<label>"
tk accounts set-kick-model "<label>" <model>
tk accounts clear-kick-model "<label>"
```

Notes:

- Prefer the provider default unless you are deliberately testing a
  model-specific quota bucket.
- `tk accounts set-kick-model` and `clear-kick-model` are legacy/advanced
  equivalents.

### App Mode And `tk app` (Native App Integration)

The native TokenKick.app drives the bundled `tk` with `TK_APP_MODE=1`. App
mode keeps stdout reserved for JSON:

- the interactive menu and confirmation prompts are disabled (prompts resolve
  to their default answer; use explicit flags like `--yes` to confirm),
- Rich tables and progress render to stderr,
- CLI errors become JSON envelopes with stable `error_code` values instead of
  tracebacks.

App-facing commands answer with one envelope:

```
{
  "schema_version": 1,
  "ok": true,
  "error_code": null,
  "message": null,
  "warnings": [],
  "payload": { ... }
}
```

Commands:

```
# One-call state snapshot: runtime/daemon versions and mismatch warnings,
# cached status, pending kicks, schedule, advisories, reset observations,
# notification routes, Codex strategy, and update status.
tk app snapshot

# Non-interactive setup streaming JSON-lines progress events; ends with a
# final setup_completed/setup_failed/setup_cancelled record. Never prompts
# and never starts the daemon.
tk app setup

# App-environment diagnosis: provider CLIs on PATH, state-directory
# writability, daemon health, and the core doctor report.
tk app doctor
```

Enveloped `--json-output` is also available on:

```
tk daemon --status --json-output
tk daemon --background --json-output
tk daemon --stop --json-output
tk daemon --restart --json-output
tk remote telegram --status --json-output
tk remote telegram --background --json-output
tk remote telegram --stop --json-output
tk remote telegram --restart --json-output
tk accounts list --json-output
tk accounts notifications --json-output
tk accounts planning --json-output
tk auto status --json-output
tk schedule show --json-output

# Single-label kick for the app: never prompts. Prompts the CLI would ask
# (stale status, clearing a planned kick) require --yes; --dry-run previews.
# payload.decision: skipped | would_kick | attempted | confirmation_required
# payload.result for attempted kicks: confirmed | unconfirmed | failed
tk kick "<label>" --json-output --yes
tk kick "<label>" --dry-run --json-output

# Global notification configuration with envelope results.
tk notify --ntfy <topic> --json-output
tk notify --telegram <token> <chat_id> --json-output
tk notify test --json-output
tk notify test --backend telegram --json-output
```

Notes:

- Account/settings mutations (`tk accounts hide/show/set-…`, `tk auto …
  enable/disable`, `tk model set/clear`, `tk claude direct-usage …`) accept
  `--json-output` and answer with the updated account in `payload.account`.
- Under `TK_APP_MODE=1` these commands emit the envelope even without
  `--json-output`.
- Existing JSON outputs (`tk status --json-output`, `tk doctor --json-output`,
  `tk update --json-output`, …) keep their bare-payload style. `tk update`
  includes both daemon and Telegram remote process version fields.

## Experimental / Quota-Consuming

### Codex Surface Test

```
tk codex-surface-test "<label>" --mode repo-skip --poll-timeout 1200 --poll-interval 60 --yes --json-output
```

Notes:

- This command is hidden and quota-consuming.
- It runs `codex exec` and can anchor a window.
- It tests exactly one Codex surface, then polls the provider reset clock until
  it first moves or the timeout expires.
- Use safe diagnostics first: `tk status --refresh --codex`,
  `tk history --verbose --account "<label>"`, `tk doctor "<label>"`, and
  `tk codex-surfaces "<label>"`.

### Codex Retry Backoff Experiment

For measurement runs only, set `codex_surface_retry_backoff_seconds` in
`config.json` or override it with `TK_CODEX_SURFACE_RETRY_BACKOFF_SECONDS`.

This changes the delay between within-cluster delayed verification attempts. It
does not change the surface scorer or learned ordering. The live default remains
900 seconds.

## Troubleshooting

### Status Looks Stale

```
tk daemon --status
tk update --check
tk status --refresh
```

### Daemon Is Stuck

```
tk daemon --stop
tk daemon --status
```

### Daemon Is Stale After Upgrade

```
pipx upgrade tokenkick
tk update
```

### Stale Prompt Cannot Read Terminal Input

```
tk kick "<label>" --force
```

### Codex Shows A Phantom `4h59m / 1%` Session

Use one force kick only, wait at least 5 minutes, then refresh:

```
tk kick "codex (account)" --force
tk status --refresh --codex
```

If the countdown moves, the session is anchored. If it stays exactly `4h59m`,
inspect:

```
tk history --json-output
tk doctor "codex (account)" --json-output
```

## Development

Development setup and contribution checks live in
[`CONTRIBUTING.md`](../CONTRIBUTING.md). This command reference is for installed
TokenKick usage.
