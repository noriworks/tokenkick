# How TokenKick Works

This is a working internals and behavior guide. It is meant to capture the
practical details behind TokenKick's visible behavior: how status is read, how
the daemon decides to act, how kicks are confirmed, how retries are bounded, and
how the different state files relate to each other.

This file is intentionally more detailed than the README. Later, parts of it
can be split into a user FAQ, troubleshooting walkthroughs, or contributor
architecture notes.

## Core Mental Model

TokenKick follows a simple loop:

```text
provider status -> normalized account status -> cache -> decision -> optional kick -> history -> notification
```

The important distinction is that **status reads** and **kicks** are different
operations.

- A status read asks a provider or local data source what the current quota
  windows look like.
- A kick sends a tiny provider request intended to anchor a fresh first-use
  window.
- A cache entry stores the latest status so routine commands and the daemon do
  not have to refresh every provider every time.
- Kick history records what TokenKick actually attempted.

When the provider status is slow, stale, or ambiguous, TokenKick prefers bounded
state machines over endless retries.

## Main Runtime Surfaces

### `tk status`

`tk status` is the fast view. It reads the local status cache when possible.
With a healthy daemon, the cache should usually be no older than the daemon poll
interval, but a provider-specific refresh can still fail and leave one account
older than the others.

Use this when you want a quick overview.

Cache freshness is based on the last successful provider observation, not just
the time the cache file was written. A normal cache entry is considered current
until it is older than `2 x poll_interval`. For example, with a 10-minute daemon
poll interval, a successful live read can stay current for about 20 minutes.

Refresh failures override that age rule. If a background refresh cannot update
one account, that row can be marked old/stale immediately even when another row
was refreshed recently. Claude often shows this as cached status because passive
background refresh intentionally avoids silently running `/usage`.

### `tk status --refresh`

`tk status --refresh` performs live provider reads and writes a new cache entry.
It is the source of truth when debugging a specific account.

This can be slower than `tk status`, and some providers have refresh paths that
are not purely passive. For example, Claude direct `/usage` is treated as
low-cost but nonzero-cost and should not be confused with a kick-history event.

`tk status --refresh` does not kick accounts. It can update observations,
including Codex phantom-session observations, but the daemon or an explicit
`tk kick` command is what performs automatic or manual kicks.

### Daemon

The daemon continuously:

1. Refreshes provider status.
2. Updates the status cache.
3. Runs global reset defense checks.
4. Runs phantom recovery checks.
5. Executes due pending kicks.
6. Finds fresh auto-kick targets.
7. Sends notifications and writes daemon log events.

The daemon uses the configured poll interval, commonly 10 minutes on a server.
Some internal retry timers are shorter than the poll interval, but in practice
the next daemon poll is often when the next attempt happens. A manual
`tk status --refresh` does not run this daemon decision loop; it only refreshes
status.

## Persistent State Files

TokenKick state lives under `~/.tokenkick/`.

Important files:

| File | Purpose |
|---|---|
| `config.json` | Saved accounts, visibility, auto-kick settings, notification settings, schedules. |
| `status-cache.json` | Latest normalized provider statuses and refresh failures. |
| `history.jsonl` | Kick attempts, confirmations, failures, token proof, and timestamps. |
| `pending-kicks.json` | Smart-scheduled kicks and retry state for due scheduled kicks. |
| `daemon.log` | Structured daemon events for polls, kicks, skips, notifications, reset defense, and recovery. |
| `phantom-sessions.json` | Observed Codex phantom-session candidates and their first/last observations. |
| `phantom-recovery.json` | Active Codex phantom recovery attempts, attempt count, cooldown, and status. |
| `reset-events.jsonl` | Global reset defense events and affected account snapshots. |

The relationship that matters most in debugging:

```text
status-cache says what TokenKick currently believes
kick-history says what TokenKick actually did
daemon.log says why the daemon acted or skipped
phantom-*.json says whether Codex ambiguity is being managed by recovery logic
```

## Source Of Truth

When sources disagree, TokenKick prefers the most provider-native status that is
safe to read.

For Codex direct accounts, the primary truth is Codex app-server provider usage
read through the account's configured `CODEX_HOME`. Local Codex session JSONL is
a fallback and override only when it has stronger evidence than an ambiguous or
stale provider shape. A clean app-server reset, such as `0%` weekly/session
usage with an unanchored window, clears stale local active-session artifacts.

For Claude, the most direct status is the Claude CLI `/usage` path. Because that
path can itself touch Claude, daemon background refresh keeps it passive unless
TokenKick is deliberately running a tracked session kick or reconciliation
probe.

CodexBar and local CodexBar files are optional compatibility fallback data. They
can help with discovery, labels, monitor-only providers, and last-known state,
but they do not beat a fresh direct provider read for a configured direct
account. If a direct Codex account only has CodexBar fallback status, TokenKick
blocks auto-kick instead of treating that fallback as kick authority.

## Codex Homes And Buckets

Each managed Codex account needs its own Codex home. TokenKick sets `CODEX_HOME`
before reading Codex provider usage or running a Codex kick. If two saved
accounts point at the same home, Codex can use the wrong ChatGPT identity and
TokenKick cannot reliably attribute the quota movement.

The easiest provider-truth check is to launch Codex with the same home and run
`/status` inside Codex:

```bash
CODEX_HOME=/path/to/account-home codex
```

Codex backend quota bucket names are not always the same as interactive model
labels. In `tk codex-usage`, `codex` means the main/default Codex quota. If the
provider exposes a separate Spark quota bucket, TokenKick creates a sibling
`codex-spark (...)` account. That sibling has its own session/weekly window and
uses the same Codex home with the Spark model override. Main Codex still reads
the `codex` bucket. Spark is used only when the provider exposes the bucket.
`tk plan` will not schedule Spark from the rough tier default alone; set
`usable_session_minutes` after measuring the account if you want orchestration
to use it. `models_cache.json` is not authoritative for entitlement or the
current default model label.

## Account Status Lifecycle

Providers expose different window models, but TokenKick normalizes them into a
small set of states:

| State | Meaning |
|---|---|
| `fresh` | A quota window appears available and not yet anchored. |
| `active` | A quota window is open and counting down. |
| `waiting` | The account is not ready yet; the reset is in the future. |
| `unknown` | TokenKick could not get trustworthy status. |

Rows can also carry warning context, such as stale cache, refresh failure,
phantom session, or global reset event banners.

The `Action` column is a decision summary. It is not just a mirror of provider
state. It combines status, config, kick history, pending kicks, recovery state,
and provider-specific safety guards.

## Kick Lifecycle

A kick is not considered done just because a subprocess exited successfully.
TokenKick tries to determine whether the visible provider bucket actually
anchored.

High-level flow:

```text
pre-status -> provider kick command -> kick event -> post-status -> confirmation decision -> history + notify
```

Possible outcomes:

| Outcome | Meaning |
|---|---|
| Confirmed | Provider status shows the intended window is active or moved. |
| Attempted / ambiguous | Provider command ran, but TokenKick cannot prove the visible bucket moved. |
| Provider accepted usage | Token usage proves the tiny request was accepted, but status is still ambiguous. |
| Failed | The provider command failed or TokenKick hit a guarded final failure. |

Kick history is the record of user-meaningful attempts by default. Background
provider reconciliation probes are kept in `history.jsonl` for diagnostics, but
normal `tk history` hides them so the table answers "what did TokenKick actually
kick?" Use `tk history --include-probes` or `tk history --kind status_probe`
when diagnosing provider reads.

## Auto-Kick And Scheduling

`auto_kick` is the master switch. Weekly and session auto-kick flags are
per-window overrides beneath it.

Newly discovered accounts are saved with auto-kick off. The first time a user
enables auto-kick for Codex or Claude, TokenKick shows the unified provider risk
notice and requires the user to type `ENABLE`. The saved acknowledgment is
versioned per provider so the prompt does not repeat for the same approved text.

Without a schedule, the daemon kicks eligible fresh windows when it sees them.

With smart scheduling, TokenKick can defer a 5-hour/session kick to better align
with the user's work window. Deferred work is stored in `pending-kicks.json`.

Due pending kicks are handled before normal fresh target selection, so scheduled
work is not lost just because the account no longer looks like a brand-new
fresh target on that poll.

## Notifications

Notifications are generated from kick/reset events, not directly from raw status
rows.

The notification title and tags carry the visual severity. Message bodies should
stay plain and specific enough to explain what happened. For example, repeated
phantom recovery messages should include attempt context so a user can tell the
difference between first try, retry, and final try.

In the public release, ntfy body copy avoids extra colored circle emojis; the
title/tag icon is enough.

## Global Reset Defense

Global reset defense looks for correlated weekly reset anomalies across multiple
accounts on the same provider.

It compares previous cached status with the current daemon poll and asks:

- Did at least two monitored accounts on the same provider change together?
- Did they move from active/waiting to fresh?
- Did weekly reset predictions shift later by more than an hour?
- Did weekly usage drop sharply to near zero?
- Were those accounts kicked by TokenKick during the comparison window?

Events can be `possible`, `likely`, or `confirmed`.

`possible` events are non-destructive: log/banner/doctor only. By default they
do not send push notifications.

`likely` events notify by default and show in recovery views, but they do not
mutate pending kicks automatically.

`confirmed` events can:

- write a reset event,
- send a notification,
- invalidate pending kicks for affected accounts,
- provide failover guidance toward unaffected visible accounts.

Acknowledged events remain in `tk reset-log` but stop appearing in the status
banner. The TUI recovery flow can preview and explicitly apply a new
orchestration plan; the daemon never auto-applies a recovery plan.

## Codex Phantom Sessions

### What is a phantom session?

A Codex phantom session is a provider status shape that looks active, but may
not represent a real anchored 5-hour work window.

The common shape is:

```text
State: active
weekly: existing nonzero usage
session: near a full 5h countdown
session usage: tiny, usually 1-2%
```

Examples:

```text
session in 4h59m, s 1%
session in 4h34m, s 1%
```

This is confusing because it can look like a successful session kick. Sometimes
it really does settle into a real active session after the provider view catches
up. Other times the countdown is a stale or sliding provider artifact and needs
phantom recovery.

### Why can this happen?

Codex can accept a tiny kick request and still report an ambiguous session
status immediately afterward. TokenKick may have proof that Codex accepted work
through token usage, while the provider status still shows a tiny near-full
session that does not clearly prove the visible session bucket has anchored.

In that case TokenKick treats the provider call as real work, but keeps phantom
recovery state until status settles or retry limits are reached.

### How does TokenKick decide it is phantom?

Current TokenKick considers a Codex session a phantom candidate when all of
these are true:

- The account is `fresh` or `active`.
- The weekly window is a normal long window.
- The session window is Codex's 5-hour window.
- Session usage is tiny: greater than `0%` and no more than `2%`.
- The session countdown is still near the start of a 5-hour window.

When Codex reports an explicit `session_window_minutes`, TokenKick uses the
stricter near-full threshold. When Codex omits the session window but still
shows a near-full 5-hour countdown, TokenKick infers the 5-hour window with a
slightly wider threshold so rows like `session in 4h34m, s 1%` are not mistaken
for normal active sessions.

A tiny session is not enough by itself to keep an account phantom forever. If
provider usage later reports a stable anchored session whose reset timestamp is
counting down normally, TokenKick clears the old phantom observation state and
shows the account as active.

### How many phantom kicks can happen?

There are a few guards, depending on what kind of ambiguous state TokenKick is
seeing.

Current behavior:

| Case | Max attempts | Backoff / spacing | Notes |
|---|---:|---|---|
| Verified phantom recovery | 5 recovery attempts | At least 45 seconds internally; usually one daemon poll, roughly 10 minutes | Each recovery attempt can try the patient adaptive Codex surface ladder. New accounts start with `repo-skip`, then `legacy`, then `repo`, then `interactive-like`. After each generated response, TokenKick waits 15 minutes for delayed provider verification before deciding whether to move to the next surface. |
| Provider accepted usage but status is still ambiguous | continues within the 5-attempt recovery cap | 45 minutes after a full ambiguous recovery attempt | TokenKick records that Codex accepted work, keeps recovery state, and waits longer before the next recovery attempt. |
| Ambiguous tiny phantom after a normal kick path | 2 | 40 minutes | Stops repeated normal kicks when status stays ambiguous. |
| Provider unchanged after session kick | 3 | 10 minutes | Lets TokenKick retry a small number of times if the provider status did not move. |
| Recent confirmed session kick | dedupe guard | 30 minutes | Prevents duplicate kicks when provider status is slow to settle. |

The practical outcome is that a user may still see more than one phantom-related
attempt over time, especially if Codex status stays ambiguous. The goal is to
bound retries and avoid wasting quota while still recovering accounts that can
be anchored by a later attempt.

The surface retries are deliberately not instant blind retries. For Codex
session and phantom recovery attempts, generation evidence (`response=yes` or
`tokens=yes`) is useful evidence, but it does not confirm an anchor by itself.
TokenKick waits 15 minutes and re-reads provider status. When provider status
reports an anchored session, TokenKick infers the anchor time from the reset
clock (`session_resets_at - session_window`) and matches it against the current
surface cluster. If one attempted surface matches, TokenKick marks that surface
confirmed and stops the cluster. If no surface matches, it may try the next
surface. For direct Codex accounts, generated-but-unconfirmed session clusters
test all four automatic surfaces before pausing as `pending_reset_clock`, so
later live refreshes can still repair whichever surface actually matched the
provider reset clock.

When Burst ladder is enabled for auto/scheduled Codex kicks, TokenKick uses the
configured Burst surface set instead of the patient retry ladder for that kick.
Burst ladder runs the configured surfaces in serialized order at the configured
gap, without mid-burst early-stop, then relies on the same late reset-clock
attribution path. `tk codex-fire-all` is kept as a deprecated compatibility
alias for this Burst ladder strategy.

Burst ladder and auto-demotion are intentionally separate. Burst ladder controls
firing speed and spacing; auto-demotion controls whether a per-account surface
may be hidden. Burst ladder on with auto-demotion off means the configured
surfaces still fire every time. Burst ladder on with auto-demotion on means the
fast burst fires the effective active set after demoted, force-pruned, and
force-kept surfaces are resolved. Burst ladder off returns to the patient
adaptive ladder; if auto-demotion is enabled for that account, the patient
ladder can still skip demoted surfaces, but it keeps the slow retry/backoff
behavior.

If the reset clock arrives too late for the original confirmation read,
TokenKick does not have to leave the cluster ambiguous forever. A later live
status refresh or daemon poll can infer the anchor time from the current reset
clock and retroactively confirm the closest generated attempt in the recent
unconfirmed cluster. If that repaired winner is earlier than later generated
attempts in the same cluster, those later rows remain in history as attempted
work but are marked `post=superseded`.
Reset-clock matches are classified as `attribution=strong` when the inferred
anchor lands inside or very near the TokenKick attempt, and
`attribution=timing_match` when it is close enough to dedupe but could overlap
with manual Codex use. Only strong attribution updates the adaptive surface
score.

If a status view sees an active-looking Codex session whose current reset clock
only matches generated but unconfirmed attempts, it renders as `Codex
unconfirmed` with `session unconfirmed` instead of plain `Active`. If TokenKick
can prove a confirmation read was stale, that attempt is left pending for later
reset-clock attribution instead of retrying more surfaces immediately.

Codex surface order is adaptive per account. New accounts start with
`repo-skip`, then `legacy`, then `repo`, then `interactive-like`. The
`interactive-like` surface uses the account Codex home as cwd to approximate a
normal CLI session while still running through daemon-safe `codex exec`.
Confirmed surfaces gain score, no-generation or failed surfaces lose score, and
future kicks try the learned order first. Per-account auto-demotion is opt-in:
when enabled, redundant tail surfaces can be hidden after enough strong
clusters, with a configurable active-surface floor, rescue cooldown, and manual
force-keep/force-prune overrides. Force-pruned surfaces are manual overrides and
are not auto-reintroduced on a miss.

### How should phantom notifications read?

The ideal user-facing wording should expose attempt context so repeated
notifications are not mysterious.

Suggested future wording:

```text
TokenKick: Phantom recovery attempt 1/5 for "codex (...)".
Waiting for Codex to expose a session anchor.
```

```text
TokenKick: Phantom recovery attempt 2/5 for "codex (...)".
Waiting for Codex to expose a session anchor.
```

```text
TokenKick: Final phantom recovery attempt 5/5 for "codex (...)".
Waiting for Codex to expose a session anchor.
```

For a provider-accepted-but-ambiguous result:

```text
TokenKick: Phantom recovery attempt 1/5 for "codex (...)".
Codex accepted usage, but session status is still ambiguous. Rechecking after 45m.
```

### How should a user debug a phantom session?

Start with:

```bash
tk status --refresh --codex
tk history --account "codex (account)"
tk doctor "codex (account)"
```

If a row shows a near-full session with tiny usage, wait a few minutes and run:

```bash
tk status --refresh --codex
```

Interpretation:

- If the countdown is decreasing normally, the session is probably genuinely
  active.
- If the countdown stays near full across repeated refreshes, it is likely a
  phantom/provider artifact.
- If `tk history` shows a kick with token usage, Codex accepted work even if the
  visible status has not settled yet.
- If local Codex session JSONL has stronger evidence than appserver status,
  TokenKick can prefer that local session reading. This covers cases where
  appserver still reports an active weekly-used row with a tiny near-full
  session artifact after Codex accepted the kick.

Avoid repeated manual force kicks. If force is needed for diagnostics, use one
force kick, wait a few minutes, then refresh status.

## Claude Status Reads

Claude direct usage reads use the Claude CLI `/usage` path. That path is useful
because it can provide real status for Claude accounts, but it is not purely
passive in the same way as reading a static JSON file.

Operational rule:

- Explicit refresh commands may use Claude direct `/usage`.
- Background daemon status refresh avoids silently touching Claude.
- Repeated explicit refreshes within 5 minutes reuse the recent successful
  direct `/usage` reading instead of running `/usage` again.
- When the daemon needs to anchor a due Claude session, `/usage` is treated as
  an explicit session kick: it must write kick history, update the cache, log
  daemon events, dedupe like other session kicks, and send the normal
  notification.
- If passive Claude status is stale or unavailable, but the last successful
  direct `/usage` reading had a session reset time that has now elapsed, the
  daemon can promote that cached due status and run the tracked session kick on
  time instead of waiting for a later passive refresh.
- When the last direct Claude `/usage` reading is old, the daemon may run a
  tracked reconciliation probe. Today that reconciliation cadence is 2 hours
  from the last successful direct `/usage` read. This is not a silent status
  refresh: it writes a history event, logs daemon reconciliation events, and
  updates the cache. A routine reconciliation does not push-notify; if the
  session window jumps forward enough to indicate a reset/anchor, TokenKick
  treats it as a tracked session event and sends the normal notification.
- Claude auto-kick is off by default. Manual `tk kick` remains available, and
  auto-kick requires the same provider risk-consent gate as Codex before
  TokenKick saves the setting.

This keeps the status cache from being updated by invisible provider touches
that never appear in kick history or notifications.

### Claude cache timing

There are three Claude-specific timing rules that are easy to confuse:

| Rule | Current value | Meaning |
|---|---:|---|
| Direct `/usage` minimum interval | 5 minutes | Repeated explicit refreshes inside this window reuse the recent successful direct read instead of launching another `/usage`. |
| Recent direct success reuse window | 30 minutes | If a recent direct probe failed or was skipped, TokenKick may still use a recent successful direct result rather than worse fallback data. |
| Reconciliation interval | 2 hours | If the daemon only has an old direct Claude reading, it may run a tracked `/usage` reconciliation probe. |

The generic status-cache age rule still applies on top: a normal cache entry is
fresh for `2 x poll_interval` unless it has a refresh error or was explicitly
marked stale.

## Debugging Relationships

When behavior is confusing, inspect state in this order:

```bash
tk status
tk status --refresh
tk history --limit 50
tk doctor
grep -E 'poll|target_scan|kick_start|kick_confirmed|phantom|notification_|global_reset|no_targets' ~/.tokenkick/daemon.log | tail -160
```

Use the outputs together:

- `tk status` tells you what the local cache currently says.
- `tk status --refresh` tells you what providers say now.
- `tk history` tells you what TokenKick actually kicked.
- `daemon.log` tells you why the daemon skipped, deferred, kicked, notified, or
  detected a reset event.
- `tk doctor` tells you whether the config, cache, daemon, and provider wiring
  are healthy.

## Notes For The Later Sweep

During a future full codebase sweep, add or verify sections for:

- exact status cache JSON shape,
- direct provider source precedence,
- optional CodexBar compatibility/fallback behavior,
- kick confirmation rules by provider,
- explicit model override behavior,
- pending kick retry policy,
- notification title/tag/body mapping,
- daemon event taxonomy,
- doctor checks and what each one means,
- setup/onboarding behavior for Claude and Codex,
- hidden or advanced commands that should become documented diagnostics.
