# Provider Behavior

TokenKick separates monitoring from kicking. Monitoring reads provider quota
state. Kicking sends a tiny provider-native request only when that provider has
a first-use anchored window where a kick can meaningfully start the next
countdown.

TokenKick does not increase quota, bypass rate limits, or evade provider
restrictions.

## Verified Provider Matrix

| Provider/tool | Status source of truth | Kickable? | Windows | Caveats |
|---|---|---:|---|---|
| OpenAI Codex CLI / ChatGPT Codex | Codex app-server provider usage through the account's configured `CODEX_HOME`; local Codex session JSONL as fallback/override for ambiguous stale shapes | Yes | 5h session + weekly, first-use anchored | Each account needs its own Codex home. Interactive Codex model labels and provider quota bucket names are not always the same. If a separate Spark bucket is exposed, TokenKick discovers it as a sibling `codex-spark (...)` account. |
| Claude Code | Claude CLI `/usage` when direct usage is enabled; passive fallback/cache otherwise | Yes | 5h session + weekly, first-use anchored | `/usage` is useful but not purely passive. Daemon background refresh avoids silently running it; due session anchors are tracked as kick-history events. |
| Antigravity | Antigravity CLI login state plus local backend named quota windows; CodexBar named quota windows as compatibility fallback | Monitor-only today | Gemini 5h + weekly, Claude/GPT 5h + weekly | The CLI currently has no documented non-interactive quota command. Kicking is not implemented in this release. |
| Gemini CLI | Optional CodexBar monitoring data where configured | No | Daily RPD, fixed midnight PT reset | Monitor-only. Not first-use anchored, so a kick does not start a useful rolling window. |
| Cursor / Copilot / other coding tools | Not directly verified in this release | Monitor-only or unsupported | Provider dependent | Add only after a stable identity source, readable status, and legitimate tiny anchor request are verified. |

## Codex

Codex has the broadest direct-account support in this release.

Spark is bucket-detected. If Codex provider usage exposes the Spark bucket,
TokenKick shows it as its own account/window and uses the Spark model override
for kicks. If the bucket is absent, no Spark account is created; if a previously
saved Spark account loses the bucket, it becomes Unknown and auto-kick is
blocked. TokenKick does not infer Spark entitlement from subscription tier.
Orchestration skips Spark until `usable_session_minutes` is explicitly set for
that account, so rough placeholders do not steer a plan.

- TokenKick reads provider usage through the Codex app-server for the configured
  `CODEX_HOME`.
- TokenKick can run a tiny Codex CLI request to anchor weekly/session windows
  after explicit user configuration.
- New accounts try `repo-skip`, then `legacy`, then `repo`, then
  `interactive-like`. Learned per-account scores can reorder those surfaces,
  and opt-in per-account auto-demotion can hide redundant tail surfaces after
  enough strong clusters.
- Burst ladder is an optional advanced Codex strategy for auto/scheduled kicks.
  It runs the configured surface set sequentially at the configured gap, then
  uses normal reset-clock attribution. `tk codex-fire-all` is the deprecated
  compatibility alias for this strategy.
- Burst ladder and auto-demotion are separate switches. Burst ladder controls
  fast firing versus the patient ladder. Auto-demotion controls whether surfaces
  may be hidden per account. Turning on Burst ladder does not enable
  auto-demotion; enable demotion per account, or with `--all`, when you want
  TokenKick to auto-hide redundant surfaces.
- The `interactive-like` surface uses the account Codex home as cwd to
  approximate a normal CLI session while still running as daemon-safe
  `codex exec`, not the GUI app.
- Generated output/token evidence is useful evidence, not automatic proof.
  After generation evidence, TokenKick waits 15 minutes, re-reads provider
  usage, and confirms the winning attempt by matching the reset-clock anchor
  (`session_resets_at - session_window_minutes`) to an attempt in the current
  surface cluster.
- If direct Codex still reports unchanged provider state after all four
  automatic generated surfaces, TokenKick pauses the cluster as
  `pending_reset_clock`.
- For direct Codex weekly kicks, TokenKick also verifies provider movement when
  pre-kick status is available. Command acceptance without observed provider
  movement stays `~` in history; confirmed provider movement is recorded as
  `✓ method=provider_moved`.
- If that reset clock only becomes visible later, a fresh live status refresh or
  daemon poll can retroactively mark the closest generated attempt in the recent
  unconfirmed cluster as the winning surface.
- Reset-clock confirmations include attribution. Strong attribution updates the
  learned surface order; timing matches confirm/dedupe the session without
  teaching the scorer, because nearby manual Codex use can be the real cause.
- Once reset-clock confirmation or provider movement identifies a winning
  surface, the current cluster stops instead of running later fallbacks.
- Demotion settings are per-account. Force-kept surfaces remain active even if
  auto-demotion would hide them; force-pruned surfaces are manual overrides and
  are not auto-reintroduced on a miss.
- Phantom-session recovery is bounded and records ambiguous attempts as
  attempted, not confirmed.
- `tk history --verbose`, `tk doctor`, `tk codex-strategy status`, and
  `tk codex-surfaces --json-output` include Codex confirmation method, inferred
  anchor, match delta, active strategy, learned surface order, and demotion
  state where available.
- CodexBar is optional compatibility/fallback only. Direct provider state wins
  for direct accounts, and CodexBar fallback status does not authorize
  auto-kicks for direct Codex accounts.

Use these diagnostics when Codex status looks wrong:

```bash
tk status --refresh --codex
tk codex-usage
tk codex-surfaces "codex (account)"
tk history --verbose --account "codex (account)"
```

For the provider-native truth, launch Codex with the same home and run `/status`
inside Codex:

```bash
CODEX_HOME=/path/to/account-home codex
```

## Claude Code

Claude direct status uses the Claude CLI `/usage` path. That path can activate
or refresh Claude's own view of a session, so TokenKick treats it carefully:

- Explicit `tk status --refresh` may use `/usage`.
- Repeated explicit refreshes within 5 minutes reuse a recent successful direct
  result.
- Background daemon refresh avoids silently running `/usage`.
- When a due Claude session needs a kick, `/usage` is treated as the tracked
  session anchor and gets history, daemon logs, cache update, and notification
  semantics.
- Old direct readings can be reconciled on a slower cadence, currently 2 hours,
  as a tracked probe rather than silent polling.

Claude auto-kick is off by default. Manual kicks remain available, and enabling
auto-kick requires the same provider risk-consent gate used for Codex.

## Gemini CLI

Gemini is monitor-only in this release. TokenKick may show Gemini accounts when
optional monitoring data is available, but Gemini accounts are never kickable
and are rejected by manual kick, run, schedule, and auto-kick paths.

The useful Gemini reset shape is a fixed daily RPD reset rather than a
first-use anchored rolling window, so a tiny request does not create the kind of
window TokenKick is designed to anchor. Hide or remove Gemini entries if you do
not want to see them in status views.

## Antigravity

Antigravity is rich monitor-only in this release. TokenKick can read the
logged-in Antigravity CLI account on Linux and macOS without CodexBar. Rich
quota display first uses the `agy` CLI's local HTTPS quota endpoint, then falls
back to local Antigravity backend or CodexBar compatibility data that exposes
named quota buckets:

- Gemini models 5-hour limit
- Gemini models weekly limit
- Claude/GPT models 5-hour limit
- Claude/GPT models weekly limit

Antigravity accounts are never kickable and are rejected by manual kick, run,
schedule, and auto-kick paths.

TokenKick includes a hidden, explicit evidence diagnostic:

```bash
tk antigravity probe-kick --family gemini
tk antigravity probe-kick --family claude-gpt
```

This command asks for confirmation, reads all four buckets before and after one
minimal `agy --print` request, and stores only sanitized local evidence. It is
not a user-facing kick path and is not used by auto-kick, schedules, run, or
orchestration.
