# TokenKick

**Reset? Go.**

A tiny utility for developers juggling multiple AI coding accounts.
TokenKick tracks reset windows, highlights which account needs attention next,
and helps you avoid letting fresh quota windows drift unused.

---

## What it does

AI coding tools (Codex, Claude Code, Cursor, etc.) have session and weekly rate limits.
When a quota window resets, many providers only start the new countdown once you
actually *use* the service. If you don't notice the reset happened, you lose hours
of usable quota.

TokenKick watches your rate-limit status using direct provider state first,
with local session files and optional CodexBar compatibility fallbacks where
available, and:

1. **Observes** — shows the current state of each account (fresh, active, waiting, unknown)
2. **Recommends** — tells you which account needs attention next
3. **Acts** (optional) — sends a minimal provider-native request to anchor the new quota window after explicit opt-in

For a deeper operational map of status reads, daemon decisions, kicks, caches,
notifications, phantom recovery, and reset defense, see
[HOW_TOKENKICK_WORKS.md](HOW_TOKENKICK_WORKS.md).

## Quick start

```bash
# Install isolated CLI
pipx install tokenkick

# Beginner path: open the interactive TUI
tk

# On first run, the TUI opens setup automatically when no accounts are saved yet.

# Power path: check status of all discovered accounts
tk status
tk status --refresh

# Optional: save discovered accounts.
# New accounts are saved with auto-kick off until you explicitly enable them.
tk setup

# Interactive setup offers optional next steps: review/enable auto-kick,
# configure notifications, start the background daemon, and read schedule/Codex
# strategy info.
# For scripts or manual daemon control:
tk daemon --background

# Optional: enable notifications
tk notify --ntfy tokenkick-yourname

# Optional: align kicks with your work hours
tk schedule set --account personal --weekdays 14:00-21:00

# Preview upcoming reset moments
tk calendar

# Kick or smart-schedule all ready/fresh enabled windows
tk kick --all
```

Auto-kick is off by default. The first time you enable it for Codex or Claude,
TokenKick shows the provider-specific risk notice and requires `ENABLE` before
saving that acknowledgment.

To upgrade an existing pipx install and restart running TokenKick background
processes onto the new package version:

```bash
pipx upgrade tokenkick
tk update
```

## How it works

TokenKick is a **companion tool**, not a replacement for monitoring.
It consumes rate-limit data from existing sources:

| Data source | How | Best for |
|---|---|---|
| Codex direct | OpenAI Codex provider usage via Codex app-server, plus `~/.codex/sessions/` fallback | Primary Codex path, including headless Linux servers |
| Claude direct | Guarded Claude CLI `/usage` plus passive cache/fallbacks | Primary Claude path |
| Codex session files | `~/.codex/sessions/` JSONL parsing | Local fallback when provider usage is ambiguous or unavailable |
| CodexBar CLI/HTTP | `codexbar --format json` or `codexbar serve --port 8080` | Optional compatibility path for existing CodexBar users and monitor-only providers |
| Manual config | Legacy/static entries with no readable provider source | Troubleshooting only |

CodexBar support is still present for backward compatibility and monitoring,
but it is not the primary Codex source. For configured direct Codex accounts,
fresh Codex app-server provider usage wins over CodexBar. If TokenKick falls
back to CodexBar local status for a direct Codex account, auto-kick is blocked
instead of trusting that fallback as kick authority.

When TokenKick has a direct Codex home, it asks Codex app-server for OpenAI's
`/api/codex/usage` provider state. The 5-hour session bucket comes from
`rateLimits.primary.usedPercent`, `resetsAt`, and `windowDurationMins`; the
weekly bucket comes from `rateLimits.secondary`. Codex owns auth and token
refresh; TokenKick does not expose OAuth tokens or access the keychain.

## Account states

| State | Meaning | Recommended action |
|---|---|---|
| 🟢 Fresh | Reset available, not yet used | **Kick now** |
| Available (not anchored) | Provider reports an unused window offer whose countdown has not been anchored by real use | **Kick now** |
| 🔵 Active | Window is open and counting down | Use if needed |
| 🟡 Waiting | Resets in N hours | Wait |
| ⚪ Unknown | Can't determine status | Check login |

## Zero-config discovery

`tk setup` discovers direct Codex/Claude accounts and can also import old
CodexBar-managed homes or monitor-only entries for compatibility when those
files/tools are present. Newly discovered accounts are saved with auto-kick
disabled by default; enable automation only after checking `tk status` or the
TUI's Auto-kick screen and accepting the provider-specific risk notice. Existing
saved auto-kick choices are preserved on
rediscovery. Once the daemon is running, plain `tk status` reads the latest
local daemon snapshot first so it returns quickly; use `tk status --refresh`
when you explicitly want fresh provider data.

`tk calendar` turns that same cached status into a forward-looking reset
timeline. It can show visible accounts in the terminal, return JSON for scripts,
or export an `.ics` file for calendar apps:

```bash
tk calendar --days 14
tk calendar --json-output
tk calendar --ics > tokenkick-resets.ics
```

TokenKick also reads Codex session files directly. It checks the primary Codex
home at `~/.codex/sessions/` and any imported CodexBar-managed Codex homes
listed in `~/Library/Application Support/CodexBar/managed-codex-accounts.json`.
Direct OpenAI provider usage remains preferred. If provider usage and session
files fail, TokenKick may fall back to CodexBar history as a last-resort
read-only signal.

Run `tk setup` only when you want to save discovered accounts. Use
`tk notify --ntfy <topic>` or `tk notify --telegram <token> <chat_id>` to
enable notifications. On a server, the same Telegram token/chat can power
read-only remote status checks with `tk remote telegram --background`; send
`/status` or `/refresh` to the bot to run a live provider refresh on that
machine. `tk init` remains as a deprecated alias for `tk setup`.
`TK_NO_INTERACTIVE=1 tk setup` is headless and non-prompting.

## Codex multi-account homes

Codex account isolation depends on separate Codex homes. For multi-account
Codex use, each saved account should have its own `provider_home`; TokenKick
sets `CODEX_HOME` to that directory before running Codex kicks or direct status
reads. If multiple accounts point at the same home, Codex may use the wrong
auth identity and TokenKick cannot reliably tell which quota window moved.

Validate the homes before enabling daemon automation:

```bash
tk setup --dry-run
tk accounts list
CODEX_HOME=/path/to/account-home codex   # then run /status in Codex
tk status --refresh --codex
```

If setup reports duplicate Codex homes for the same email, keep both visible
until you understand them. TokenKick can track separate Codex homes, but only
enable auto-kick for homes whose current status is usable. For stale or expired
duplicates, re-auth that home and run `tk status --refresh`, or disable/hide it:

```bash
tk auto disable "<label>"
tk accounts hide "<label>"
```

On macOS, direct Codex homes may cause Codex CLI/app-server to trigger a system
prompt like “Terminal/Ghostty wants to control Codex Computer Use.app.” Allowing
it gives full Codex direct behavior; denying it may leave Codex status or kicks
degraded/stale. The prompt comes from Codex CLI/helper behavior, not TokenKick
using AppleScript directly.

After the homes are correct, test one Codex account at a time:

```bash
tk kick "codex (example)"
tk history --json-output
tk status --refresh --codex
```

Codex kicks use an adaptive surface ladder. New accounts try `repo-skip`, then
`legacy`, then `repo`, then `interactive-like`. The `interactive-like` surface
uses the account's Codex home as cwd to approximate a normal CLI session, but it
is still a daemon-safe `codex exec` probe, not the GUI app. If Codex produces
response/token evidence, TokenKick waits 15 minutes, re-reads provider usage, and
confirms the kick by matching the provider reset clock to the attempted surface.
Once a surface is confirmed, TokenKick stops the cluster and learns that surface
for the account. Learning reorders the four fallbacks.

Codex surface strategy has two separate switches. Burst ladder controls how fast
surfaces are fired: on means TokenKick fires the configured surface set quickly,
off means it uses the patient adaptive ladder. Auto-demotion controls whether
TokenKick may hide redundant surfaces for an account. Burst ladder does not turn
auto-demotion on by itself. With Burst ladder on and auto-demotion off,
TokenKick still fires the configured surfaces every time; with both on, it fires
the effective active set after demotion, force-prune, and force-keep are applied.
With Burst ladder off, the same demotion setting can still apply to the patient
adaptive ladder, but the slow retry/backoff behavior remains.

Use `tk codex-surfaces "<label>"` to inspect the learned order, human-readable
surface labels, per-surface learning scores, and demotion state without kicking
or running Codex. Use `tk codex-surfaces "<label>" demotion evidence` to inspect
the recent strong clusters that caused any current auto-demotions.
Use `tk codex-surfaces "<label>" reset-stats` when you want to clear learned
surface scores/order while keeping kick history and demotion evidence. Use
`tk codex-surfaces "<label>" reset-all` for a per-account clean slate that
clears both learned stats and demotion evidence; demotion settings and force
overrides stay unchanged.

If the provider reset clock appears after the original confirmation wait,
TokenKick can repair the recent history on a later live refresh or daemon poll:
it infers the anchor time from the reset clock and retroactively marks the
closest generated attempt in that unconfirmed surface cluster as the winner.
Generated attempts after the repaired winner stay in history, but are marked
`post=superseded` so the table shows they were run before the late repair was
known.
Reset-clock matches also carry `attribution=strong` or
`attribution=timing_match`. Only strong attribution teaches the adaptive surface
order; timing matches confirm/dedupe the session without training the scorer.
Until that repair happens, status views mark active-looking Codex sessions as
`Codex unconfirmed` when the current reset clock only matches generated but
unconfirmed TokenKick attempts. Confirmation reads also retain reset-clock
diagnostics in history JSON so stale provider reads can be separated from real
surface failures.

For direct Codex weekly kicks, generated output is also not treated as final
proof by itself. When TokenKick has a pre-kick provider status, it re-reads live
provider usage after the command and records `✓ method=provider_moved` only if
state, reset anchor, usage, or anchor state actually moved. Command-only
evidence remains an attempted `~` row so history does not imply that the
provider window moved.

For Codex direct status, TokenKick first asks Codex app-server for provider
usage. If that response looks like a stale fresh phantom session (`0%` weekly
with a tiny full-window session), but the Codex CLI session JSONL shows a newer
active rate-limit reading, TokenKick uses the session JSONL reading. This keeps
Codex closer to Claude's CLI-owned usage path when app-server usage remains
stuck.

The reverse is also important: clean Codex app-server reset data wins over stale
local session evidence. If provider usage reports a clean unused window, such as
`0%` weekly/session usage with an unanchored window, TokenKick treats that as
the current provider truth instead of preserving an older local active-session
artifact.

Codex provider usage bucket IDs are backend quota names, not always the same as
interactive model labels. In `tk codex-usage`, the generic `codex` bucket is the
main/default Codex quota. If the provider exposes a separate Spark quota bucket,
TokenKick discovers it as a sibling account such as `codex-spark (...)`, using
the same `CODEX_HOME` but the Spark bucket/model. TokenKick does not infer Spark
access from plan tier. If the bucket disappears, the saved Spark account becomes
Unknown and is not kickable until it returns.
Orchestration skips Spark until the account has an explicit
`usable_session_minutes` value; the rough `spark` tier placeholder is not used
to tip a plan.
Do not treat `models_cache.json` as authoritative for the current default model
label.

## Configuration

TokenKick stores config in `~/.tokenkick/config.json`:

```json
{
  "accounts": [
    {
      "label": "personal",
      "provider": "codex",
      "source": "codex-direct",
      "provider_home": "/home/example/.codex-personal",
      "auto_kick": true,
      "weekly_auto_kick": true,
      "session_auto_kick": false,
      "visible": true
    },
    {
      "label": "work",
      "provider": "codex",
      "source": "codex-direct",
      "provider_home": "/home/example/.codex-work",
      "auto_kick": false,
      "visible": false
    }
  ],
  "notifications": {
    "enabled": true,
    "backend": "ntfy",
    "ntfy_topic": "tokenkick-yourname"
  },
  "codexbar_staleness_threshold_seconds": 900,
  "codexbar_rejection_threshold_seconds": 86400,
  "schedule": {
    "enabled": true,
    "timezone": "Europe/Berlin",
    "default": {
      "enabled": false,
      "weekdays": null,
      "weekends": null
    },
    "accounts": {
      "personal": {
        "enabled": true,
        "weekdays": "14:00-21:00",
        "weekends": "10:00-16:00"
      }
    }
  }
}
```

The `codexbar_*` thresholds apply only to optional CodexBar fallback data.
CodexBar local fallback data older than
`codexbar_staleness_threshold_seconds` is shown as stale and blocks automatic
kicks. Data older than `codexbar_rejection_threshold_seconds` is rejected as
unavailable. These thresholds are currently configured by editing
`~/.tokenkick/config.json`; `tk config set` is not available in this version.
CodexBar timestamps up to 5 minutes in the future are treated as clock skew;
larger future timestamps are rejected until the system clock or CodexBar
snapshot is corrected.

## Smart Kick Scheduling

Immediate kicks are not always the best kicks. If you work 14:00-21:00 and a
5-hour session window is available at 09:00, kicking at 14:00 makes the second
window spill three hours after work. Smart Scheduling aims the first kick so the
last window expires at the end of your deep-work block.

```text
Naive:  14:00 kick -> 19:00 reset -> 24:00 expiry (3h post-work waste)
Smart:  11:00 kick -> 16:00 reset -> 21:00 expiry (0h post-work waste)
```

Enable it per account or as a default:

```bash
tk schedule set --account personal --weekdays 14:00-21:00 --weekends 10:00-16:00
tk schedule set --default --weekdays 09:00-17:00
tk schedule show
tk schedule disable --account personal
tk schedule clear --account personal
```

Smart Scheduling is opt-in. Existing accounts keep immediate kicks until you run
`tk schedule set`. Pending scheduled kicks survive daemon restarts in
`~/.tokenkick/pending-kicks.json`. The daemon accepts up to one poll interval of
timing slop; use `tk poll 1` for tighter timing. A running daemon reloads the
poll interval on each pass, so poll changes take effect after the current sleep
finishes. Work-window boundaries that land in a spring-forward DST gap are
rejected as invalid for that date instead of being silently shifted.

If a scheduled kick reaches its due time but the provider kick command fails,
TokenKick keeps the pending kick and retries after 5, 15, then 45 minutes. After
the fourth failed attempt it stops retrying and marks the pending kick as failed;
`tk status` and `tk schedule show` display the failure so you can clear or
reschedule it.

MVP Smart Scheduling applies to short rolling windows, such as 5-hour session
windows. If a provider exposes a long primary window plus a short session
window, TokenKick automatically schedules against the session window. For Codex
accounts, that means scheduling targets the 5-hour session window because the
168-hour weekly window is too long for daily work-hour alignment. Providers that
only expose daily or weekly windows still kick on availability for now.

`auto_kick` is the master switch for automatic kicks. When it is enabled,
TokenKick can anchor both fresh long/weekly windows and reset-ready
5-hour/session windows. Use the weekly and session subcommands only when you
want a per-window override:

```bash
tk auto enable personal
tk auto disable personal
tk auto weekly enable personal
tk auto weekly disable personal
tk auto session enable personal
tk auto session disable personal
```

Session auto-kicks reuse the existing schedule if one is configured. Inside the
work window they kick immediately; outside it they create a pending session kick.
Without a schedule, they are opportunistic and run when the daemon sees a
reset-ready session. Manual `tk kick LABEL` can kick either a fresh long window
or a reset-ready session window.

## CLI commands

```
tk                     Open the interactive command center in a terminal
TK_NO_INTERACTIVE=1 tk Print status instead of opening the interactive menu
tk menu                Force the interactive command center
tk status              Show all accounts and recommended actions
tk status --account LABEL Show one account, including hidden accounts
tk status --all        Include hidden accounts
tk status --codex      Show Codex accounts only
tk status --json-output Machine-readable output
tk status --refresh    Force live provider refresh instead of daemon cache
tk status --verbose    Show scheduling/source diagnostics
tk run                 Refresh, kick eligible auto-enabled accounts, summarize
tk run --dry-run       Preview run decisions without kicking
tk run --json-output   Machine-readable run summary
tk run --codex         Limit run decisions to Codex accounts
tk plan --work-window HH:MM-HH:MM Plan multi-account session coverage
tk plan --work-window HH:MM-HH:MM --apply --yes --json-output Apply approved plan
tk plan cancel       Cancel applied orchestration pending kicks
tk plan cancel --account LABEL Cancel one account's orchestration pending kick
tk accounts planning  Show planning defaults, orchestration roles, reserves
tk accounts set-role LABEL use-first Set the single preferred orchestration account
tk accounts set-role LABEL backup Set backup-only orchestration behavior
tk kick <label>        Kick a specific account's quota window
tk wake LABEL          One-time bootstrap kick for a dormant account
tk kick --all          Kick or smart-schedule all fresh enabled accounts
tk kick --auto         Kick or smart-schedule all fresh enabled accounts
tk kick --auto --force Kick immediately, bypassing smart scheduling
tk kick --all --dry-run Preview what would be kicked
tk schedule set        Configure smart kick scheduling
tk schedule show       Show schedules and pending kicks
tk schedule disable    Disable a schedule but keep its hours
tk schedule clear      Revert to kick-on-availability
tk --version           Show installed package version
tk update              Check background process/package version mismatch and offer restart
tk update --check      Check only; exit 1 if a background process needs restart
tk update --yes        Restart stale background processes without prompting
tk accounts            Show account visibility
tk accounts detail LABEL Show read-only details for one account
tk accounts hide LABEL Hide an account from status and bulk kicking
tk accounts show LABEL Show a hidden account again
tk accounts notifications Show per-account notification delivery state
tk accounts enable-notifications LABEL Enable notifications for one account
tk accounts disable-notifications LABEL Disable notifications for one account
tk accounts set-notifications LABEL --ntfy [--telegram] Route one account
tk accounts set-notifications LABEL --global-default Use global routes
tk accounts set-notifications LABEL --none Disable one account
tk model set LABEL MODEL     Override the model used for tiny kick prompts
tk model clear LABEL         Use the provider default model for tiny kick prompts
tk auto status         Show auto-kick settings
tk auto enable LABEL   Enable auto-kick for an account
tk auto disable LABEL  Disable auto-kick for an account
tk auto weekly enable LABEL    Enable weekly auto-kick for an account
tk auto weekly disable LABEL   Disable weekly auto-kick for an account
tk auto session enable LABEL   Enable 5h/session auto-kick
tk auto session disable LABEL  Disable 5h/session auto-kick
tk history             Show recent kick history
tk history --verbose   Show expanded kick evidence
tk history --anchored  Show only confirmed moved anchors
tk history --include-probes Show background reconciliation probes too
tk reset-log           Show detected provider global reset events
tk reset-log --json-output Machine-readable reset events
tk reset-log ack --latest Acknowledge the latest reset event banner
tk codex-usage         Advanced: show sanitized Codex provider buckets
tk codex-strategy      Manage Burst ladder, surface order, gap, and demotion
tk codex-surfaces LABEL Show learned Codex surface order and stats without kicking
tk codex-surfaces LABEL demotion evidence Show stored auto-demotion evidence
tk codex-surfaces LABEL reset-stats Reset learned surface scores/order
tk codex-surfaces LABEL reset-all Reset learned stats and demotion evidence
tk codex-surface-patterns Experimental read-only surface-pattern verdict
tk setup               Auto-discover accounts and save config
tk setup --dry-run     Preview discovered config changes without writing
tk notify --ntfy TOPIC Enable ntfy.sh notifications
tk notify --telegram TOKEN CHAT_ID Enable Telegram notifications
tk poll                Show daemon poll interval
tk poll 5              Poll every 5 minutes
tk init                Deprecated alias for tk setup
tk daemon              Run the daemon in the foreground
tk daemon --background Start daemon in background and avoid duplicates
tk daemon --status     Show daemon pid, uptime, and poll interval
tk daemon --restart    Restart the background daemon
tk daemon --stop       Stop the background daemon and clean up the pidfile after it exits
tk doctor              Diagnose config, cache, daemon, and provider wiring
tk doctor --repair     Clean dead refresh locks before diagnosing
tk update --json-output JSON version status for scripts
```

Bare `tk` is optimized for humans: in an interactive terminal it opens a
command center with top-level Status, Kick, Schedule, Configure, Diagnostics,
History, and Daemon menus. Configure contains setup, account auto-kick, Codex
settings, and notifications. Diagnostics contains detailed status, reset logs,
and read-only Codex analysis. Submenus, confirmations, and value-entry actions
include an explicit Back path, and Esc acts as Back for normal prompts. Scripts
and pipes should use explicit
commands such as `tk status --json-output` or set `TK_NO_INTERACTIVE=1`.

Gemini CLI accounts are monitor-only. Gemini uses daily RPD reset at midnight
Pacific time, so kicking does not anchor a useful window and auto-kick is
disabled automatically. See [PROVIDERS.md](PROVIDERS.md).

Antigravity accounts are rich monitor-only. TokenKick can read the bundled
Gemini and Claude/GPT 5-hour and weekly limits when named quota windows are
available, but it does not kick or anchor Antigravity windows.

Codex provider usage reads are status reads only. They do not kick or anchor a
window; only `tk kick`, `tk run`, or daemon auto-kick sends the tiny Codex model
call that can anchor a fresh provider window. Use `tk codex-usage` when you need
to compare TokenKick's selected backend quota bucket with the interactive Codex
`/status` screen.

Safe diagnostics do not kick or run provider model calls: `tk status`,
`tk accounts detail`, `tk history`, `tk doctor`, `tk reset-log`,
`tk codex-usage`, `tk codex-strategy status`, `tk codex-surfaces`, and
`tk codex-surface-patterns`. Active actions can consume quota or anchor
windows: `tk kick`, force kicks, and the hidden `tk codex-surface-test`
diagnostic.

Claude direct `/usage` refreshes are treated as low-cost but nonzero-cost.
TokenKick keeps the default poll interval at 5 minutes and uses cached status
for routine `tk status`; run `tk status --refresh` when you explicitly want a
fresh provider read. Repeated explicit refreshes within 5 minutes reuse the
recent direct `/usage` result. A normal cached status is considered current
until it is older than `2 x poll_interval`, unless the provider refresh failed
or the account was explicitly marked stale.

## Running as a service

```bash
# Copy the systemd unit file
sudo cp tokenkick@.service /etc/systemd/system/
sudo systemctl enable --now "tokenkick@${USER}"
```

TokenKick will poll every 5 minutes and kick or smart-schedule fresh windows automatically.
`tk poll <minutes>` changes are picked up by a running daemon after its current
sleep finishes; no restart is required.
Daemon logs use ISO UTC timestamps with parse-friendly event names and
key/value fields, for example:

```text
2026-05-22T06:45:52Z [poll] auto_kick_accounts=5 fresh_targets=0 deferred=1
2026-05-22T09:00:01Z [schedule_deferred] account="personal" kick_at="2026-05-22T09:00:00Z"
```

## Notifications

TokenKick can notify you when it detects and kicks a fresh window:

- **ntfy.sh** — free, no account needed, push to any device
- **Telegram** — via bot token

`tk notify --ntfy <topic>` uses an ntfy.sh topic name: the topic you subscribe
to in the ntfy app or web UI. It is not a TokenKick account label. ntfy and
Telegram credentials are global and can both be configured. Use
`tk accounts set-notifications "<label>" --ntfy`, `--telegram`, both flags,
`--global-default`, or `--none` to choose each account's delivery route. Use
`tk accounts notifications` to show the per-account delivery state.

Example notification:
> 🟢 TokenKick: Kicked "personal" at 14:32 CEST. Fresh quota window is now active.

Smart schedule notification:
> 🕐 TokenKick: "personal" is fresh. Scheduled kick at 11:00 CEST (optimal for your 14:00-21:00 workday).

Auto-kick targets Codex and Claude accounts. Gemini and Antigravity are
monitor-only and never kicked. Other providers are unsupported unless documented
otherwise.

TokenKick treats a repeated tiny near-full Codex 5-hour session as a
phantom/probe session only after evidence that the status is ambiguous, such as
repeated observation, a sliding reset timestamp, or provider-accepted work whose
visible session anchor did not settle. A real anchored `1%` session that counts
down normally is active, not kick-ready. If a phantom/probe kick cannot be
confirmed after the provider call, TokenKick records and notifies it as an
attempted kick instead of a confirmed fresh-window anchor.

For normal Codex session kicks, `tk history --verbose` can show which surface
matched reset-clock confirmation, including `method=reset_clock` for live
confirmation, `method=late_reset_clock` for later repair, and the match delta.
`tk doctor` reports the learned Codex surface order per account.
`tk codex-strategy status` shows whether Burst ladder or the patient adaptive
ladder is active. Per-account auto-demotion must be enabled separately if you
want TokenKick to auto-hide surfaces.

## Important

This tool does **not** increase quota, bypass rate limits, or evade provider restrictions.
It helps you track and act on your own available windows — the same thing you'd do
manually, just faster and without forgetting.

## Roadmap

TokenKick is intentionally local-first. Near-term work stays focused on making
the local CLI, TUI, daemon, MCP surface, and beta Mac app easier to inspect and
safer to run.

Public priorities:

- More real-world validation for monitored providers before any provider becomes
  kickable.
- Better local dashboard and history views for explaining why a kick did or did
  not happen.
- Continued Mac app beta polish, including clearer install/update guidance and
  signing/notarization when available.
- Optional TokenKick Cloud for cross-machine coordination, without moving
  provider credentials into TokenKick's cloud.

## License
