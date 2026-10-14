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

Standalone Gemini and legacy aggregate Antigravity accounts remain monitor-only.
Native Antigravity CLI Gemini and Claude families have separate auto-kick controls.
Unconfirmed Antigravity requests block retries until explicit operator recovery.

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
tk auto enable "<label>"       # Resume saved weekly/session choices.
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
tk notify --policy errors      # Suppress routine success notifications.
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

Terminal menus show their keyboard controls. For one choice, use arrows and
Enter; Esc returns to the previous menu. Multi-account pickers use **Space to
toggle**, **Enter to review**, and **Esc to cancel**, with square checkboxes and
a live selection count. Back is never a checkbox, and Enter on an unmarked row
does not select it. Empty selections make no changes.

Bulk visibility, auto-kick, notification and orchestration cancellation actions
list the named targets and proposed changes before a separate confirmation, with
No selected by default. Auto-kick also offers **Enable one account** and
**Disable one account** for Enter-only selection. Its review shows the master,
weekly and session switches individually; choosing one window scope preserves
the master switch. Existing provider consent requirements still apply.

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
`Disable selected accounts` includes hidden kickable accounts, and
`Disable all kickable accounts` turns off auto-kick for both visible and hidden
Codex or Claude accounts.
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
tk plan --work-window 18:30-23:30 --expected-plan "<plan_fingerprint>" --preview-at "<built_at>" --apply --yes --json-output
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
tk plan --work-window HH:MM-HH:MM --expected-plan "<plan_fingerprint>" --preview-at "<built_at>" --apply
TK_NO_INTERACTIVE=1 tk plan --work-window HH:MM-HH:MM --expected-plan "<plan_fingerprint>" --preview-at "<built_at>" --apply --yes --json-output
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

- `tk auto enable` resumes master without changing saved weekly/session choices.
- `tk auto disable` pauses master, preserving window choices; selected windows display as paused.
- `tk auto weekly enable` enables Weekly and master, preserving Session.
- `tk auto session enable` enables Session and master, preserving Weekly.
- Disabling a window preserves master and the other window. If neither window is selected,
  nothing runs; enabling master alone does not select a window.
- For weekly-only automation: enable Weekly, then disable Session. Reverse for session-only.
- MCP `scope="all"` controls master (pause/resume), not window selection. Previews show
  exact before/after flags and expire when the control policy or account settings change.
- Per-account controls do not resume a paused stagger group; use the stagger controls.
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
tk notify                       # Show saved destinations and effective push state.
tk notify --ntfy <topic>
tk notify --telegram <token> <chat_id>
tk notify --enable-backend telegram  # Reuse saved credentials.
tk notify --disable-backend ntfy     # Keep saved topic.
tk notify --save-ntfy <topic>         # Edit topic without changing push state.
tk notify --save-telegram <token> <chat_id>
tk notify --policy errors
tk notify --policy all
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
- `tk notify --policy errors` keeps error/check/warning pushes and suppresses
  routine success notifications. Use `tk notify --policy all` to restore every
  notification.
- In the TUI, use `Configure -> Notifications -> Notification volume` to switch
  between the same two policies.
- The notification overview shows saved ntfy/Telegram destinations, masked
  Telegram token presence, effective push switches and the local Telegram
  listener state. `tk notify --json-output` returns the same metadata without
  the token. Use the menu's secret input to replace a token without entering
  it as a shell argument.
- Destination submenus let you edit saved settings, enable push with existing
  credentials, disable while retaining credentials, or test the destination.
  Saving credentials alone preserves push and remote-command settings. Telegram
  push and remote commands share credentials; restart the listener after editing
  the token or chat ID. Remote commands remain independent of push switches.
- `tk notify test` sends to enabled push destinations only. The menu names those
  destinations before confirmation. Individual tests can use a disabled
  destination's saved credentials without enabling push. Explicit tests bypass
  notification volume and account routes.
- Tests report each backend as `sent`, `failed` or `skipped`, with a reason for
  failure or skipping. Disabled push backends are skipped; missing credentials
  on an enabled backend count as a failure. A partial delivery or no delivery
  returns exit status 1. JSON includes a `results` array and `delivered` is true
  only when every selected backend succeeds.
- Account routes show inherited/custom/disabled settings and effective
  destinations side by side. Custom routes do not enable a globally disabled
  backend; blocked destinations are explained. Routes are visible on entry to
  the Accounts notifications submenu, alongside the edit controls.

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
tk accounts set-session-limit "<codex label>" auto
tk accounts set-session-limit "<codex label>" present
tk accounts set-session-limit "<codex label>" absent
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

- `tk accounts list` shows saved settings, including separate master, weekly,
  and session auto-kick switches. Weekly/session switches cannot run automation
  while the master switch is off. Antigravity quota families are identified
  alongside the provider without changing saved account labels.
- Automatic kick model descriptions show the selection policy, not a resolved
  live model. Antigravity prefers Gemini Flash Low or Claude Haiku Low, falling
  back to Sonnet Low for Claude. Standalone Claude shows low effort and thinking
  disabled. The live catalog is consulted when a request is prepared.
- The Accounts menu separates **Show account settings** from **Show live usage
  and resets**, which refreshes provider status for visible accounts.
- Codex accounts show the provider's detected subscription and five-hour limit
  capability. Detection uses window durations, not plan names or slot ordering;
  a weekly window can occupy the provider's primary slot. Missing, stale, or
  ambiguous data is shown as unknown, rather than interpreted as unlimited.
  Account settings/detail use cached observations; `tk status --refresh` reads
  current provider data. Detected subscription metadata does not overwrite
  the user-owned planning tier.
- `set-session-limit` is the manual fallback, also available under **Accounts →
  Codex five-hour limit override**. `auto` follows provider evidence; `absent`
  blocks session actions while retaining weekly tracking; `present` still
  requires valid current five-hour reset data and cannot invent a window.
  Overrides survive rediscovery and do not enable or disable the saved master,
  weekly, or session automation switches. Conflicting evidence produces a
  warning. Returning to `auto` uses the retained provider observations.
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
- `kick_model`: resolved model requested for the kick; `null` in older history means provider default. An unset account override now selects economically at request time.
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
- Spark is retired. Config loading removes saved Spark entries and clears retired
  Spark pins on ordinary accounts. Discovery ignores stale Spark buckets; homes
  and historical records are retained.
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
- Claude direct `/usage` reads quota status; it does not anchor a session by
  itself.
- Explicit refreshes may use it, but repeated refreshes within 5 minutes reuse
  the recent direct result.
- Background daemon status refresh avoids silently running `/usage`.
- Due Claude session kicks send the same tiny completion as other Claude kicks,
  then force a fresh `/usage` read to verify the reset clock. The completion is
  tracked with normal history, logging, and notification semantics.

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

- Leave the override unset for economical selection: Codex queries its account's
  live catalog, preferring GPT-6 Luna and the lowest supported effort; Claude uses
  the current Haiku alias with low effort and thinking off.
- Explicit Codex overrides must be advertised by that account and support an
  economical effort. Missing models/catalogs fail clearly instead of inheriting
  an expensive interactive default. Actual selected models appear in history.
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


## Preview and recovery safeguards

`tk plan --json-output` includes `plan_fingerprint` and `built_at`. To apply a reviewed
preview, pass those values as `--expected-plan HASH --preview-at TIMESTAMP` alongside
the same work window, date, timezone, and usage arguments, plus `--apply --yes`.
TokenKick rereads configuration and pending work under locks before applying. Changed
plans and expired previews require another review. The macOS Planner and MCP tools
supply these fields automatically.

Ordinary kicks, including `wake`, require eligible, current provider observations.
A failed refresh or a predicted reset is insufficient. Explicit manual stale-data
confirmation remains available through `tk kick`; `--force` is an explicit override.
After an interrupted action, inspect `tk history` and refresh the account before using
that override: the provider may already have received the previous request.

New daemon and Telegram PID files include a process creation identity. TokenKick
refuses to signal legacy or mismatched PID files. During an upgrade, first stop a
legacy process through its verified service manager, then start the new runtime.

`scripts/create-public-export.sh DESTINATION` requires a new destination outside the
repository. It refuses existing destinations, source aliases, and overlapping paths.

## Persistent Antigravity orchestration

`tk stagger` maintains a recurring 150-minute phase difference between Gemini and
Claude/GPT through the TokenKick daemon. It is separate from `tk plan`, which
plans a finite work window. Roles and expected-use minutes do not enable it.

```sh
tk stagger status --json-output
tk stagger preview --action enable --gemini 'antigravity' --claude 'antigravity (Claude)' --json-output
# Review the changes and disclosure, then use the returned fingerprint:
tk stagger apply --action enable --gemini 'antigravity' --claude 'antigravity (Claude)' --expected FINGERPRINT --yes --accept-risk ENABLE --json-output
```

For an existing group, preview/apply `--action pause`, `resume`, or `disable`
without account arguments. Every apply requires the exact preview fingerprint and
`--yes`; enable/resume additionally requires `--accept-risk ENABLE`. The terminal
menu exposes the same flow at Configure → Ongoing stagger. These controls do not
start a stopped daemon: check `tk daemon --status` and start it explicitly.

Enable/resume assigns persistent ownership and enables both members' master and
session permissions. Weekly settings are preserved, but weekly kicks cannot
start a group member ahead of its session slot. Pause/disable turns off master
and session permissions; it never restores independent auto-kicks. Existing
conflicting schedules must be cancelled before activation. Unrelated accounts
are unchanged. Manual requests may alter the phase; the daemon observes the
actual windows and realigns without forcing requests into active windows.

Status shows controller ownership, target/observed offset, next action, daemon
availability, last evaluation and blockers. Scheduling is best effort: outages,
weekly exhaustion, reserve thresholds and manual activity can delay alignment.
The daemon wakes for deadlines without refreshing unrelated providers each minute.
If an already scheduled slot encounters complete native data with an inconclusive
session clock, the daemon retains that slot and rechecks after 30, 60 and 120 seconds.
These are at most three extra read-only checks, bounded by five minutes after the
original slot; the counter and deadline survive restarts. A verified idle family
may start late within this grace only while quota, permission and dispatch guards
permit it. The confirmed anchor becomes the scheduling reference for later slots.
External activity, expired grace or control changes cancel the retained recovery;
outage slots are not replayed. Authentication, stale-data and malformed-data errors
do not trigger these rapid checks.

During recovery, status shows `rechecking`, a separate `next_check_at` read deadline,
and `verification_retry` details (family, original slot, cutoff and retry count).
`last_recovery` records whether the slot was recovered, skipped or left unconfirmed.
The terminal displays the same information. These read deadlines do not authorize
a generation request and do not change the normal healthy polling interval.

If a previously accepted anchor should still be active but fresh paired native reads prove
an unused sliding window, the daemon retracts that observed anchor and persists an
`anchor_corrections` entry. It schedules a new start respecting actual request cooldowns,
quota and permissions, and at least 150 minutes after the other family's surviving
anchor/request. It never treats unknown or synthetic readings as proof of an unused window.
Once the corrective request and its anchor are confirmed, ongoing slots use that actual
anchor. External activity can supersede a pending correction. Terminal and MCP status show
the pending corrections and `last_anchor_correction`; alignment is reported only from both
current native windows. Ordinary expiration and missed slots do not trigger this path.

A zero-use deadline advancing a known anchor by whole five-hour periods is provisional,
even if two reads six seconds apart appear fixed. Status shows `verifying_rollover`,
`provisional_rollovers` and a separate `next_check_at`; it does not claim alignment or
replace the previous phase. Read-only checks run every minute for five minutes, then
every two minutes until a fixed 45-minute cutoff. The original cutoff survives restarts
and repeated rollovers. Afterward normal polling continues without renewing the fast
check budget. Positive native usage or a distinct non-rollover anchor can establish
activity; verified sliding idle evidence enters the correction path. No elapsed-time
threshold authorizes a kick. Unknown evidence, outages or provider delays can still
prevent exact 150-minute spacing; actual confirmed starts determine safe realignment.

Continuous staggering can consume significant weekly quota, especially for Claude/GPT.
At one percentage point per kick, 168/5 kicks would use about 34% weekly. Actual metering
varies; this is an estimate, not a fixed per-request price. Model selection already
prefers an available Haiku Low before Sonnet Low. Pause the group when continuous
automation is unnecessary; work-period orchestration and automatic kick budgets are
not currently part of this controller.

A failed/unconfirmed generation does not automatically retry. After inspecting
history, `--action recover` performs native read-only reconciliation after a full
five-hour cooldown and leaves the group paused. Explicit resume is then required.
Recovery never sends a replacement generation.

MCP agents use `tokenkick_stagger_status`, `tokenkick_stagger_preview` and
`tokenkick_stagger_apply`. Enable/resume requires `confirm`, `quota_ack` and
`accept_risk` with the one-use preview token. The CLI, menu and MCP all change the
same persisted policy. Do not create a Hermes/Codex cron job to maintain recurrence.

### Codex weekly confirmation

Weekly kicks and session kicks send minimal generation requests, but their confirmation evidence
is separate. A successful session request can start an unused weekly window too; it does not move
an already-running weekly deadline. Weekly-only Pro accounts do not need a five-hour window to
confirm a weekly kick.

Weekly history becomes confirmed after two fresh native readings, at least 30 seconds apart,
retain a seven-day deadline whose inferred start matches the successful request within 30 seconds.
Zero rounded usage does not prevent confirmation. A known pre-existing deadline, changing clock,
wrong account/bucket, stale data or ambiguous requests cannot confirm it. Ordinary daemon polling
or live status refreshes reconcile pending history; checking cached status alone does not.

History records `weekly_reset_clock` with the weekly deadline and observation timestamps. Earlier
pending records can reconcile during their current seven-day window; future requests also store
an account fingerprint and pre-request weekly deadline. Weekly confirmation does not confirm a
session or train session surface rankings. No extra generation is sent to obtain confirmation.
