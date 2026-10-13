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
| OpenAI Codex CLI / ChatGPT Codex | Codex app-server provider usage through the account's configured `CODEX_HOME`; identity-scoped local JSONL as fallback; override only with provably newer, recent observations | Yes | Weekly; 5h session when reported by the provider | Each account needs its own Codex home. Interactive Codex model labels and provider quota bucket names are not always the same. Retired Spark entries are removed during config loading; historical quota records remain readable. |
| Claude Code | Claude CLI `/usage` when direct usage is enabled; passive fallback/cache otherwise | Yes | 5h session + weekly, first-use anchored | `/usage` is useful but not purely passive. Daemon background refresh avoids silently running it; due session anchors are tracked as kick-history events. |
| Antigravity | Antigravity CLI login state plus local backend named quota windows; CodexBar named quota windows as compatibility fallback | Native CLI Gemini and Claude family kicks | Gemini 5h + weekly, Claude/GPT 5h + weekly | Separate opt-in automation per quota family; complete fresh quota data and native identity required. |
| Gemini CLI | Optional CodexBar monitoring data where configured | No | Daily RPD, fixed midnight PT reset | Monitor-only. Not first-use anchored, so a kick does not start a useful rolling window. |
| Cursor / Copilot / other coding tools | Not directly verified in this release | Monitor-only or unsupported | Provider dependent | Add only after a stable identity source, readable status, and legitimate tiny anchor request are verified. |

## Codex

Codex has the broadest direct-account support in this release.

TokenKick detects the subscription from native `planType` metadata, separately
from the user-owned planning tier. Window durations determine actual behavior:
300 minutes is a session window and 10080 minutes is weekly, regardless of which
slot contains them. A complete weekly-only native response establishes that no
five-hour window is available. Unknown plans remain readable; plan names alone
never decide whether session automation is allowed. Historical session logs
cannot establish current absence, and malformed or stale data remains unknown.

Use `tk accounts set-session-limit "<label>" auto|present|absent` (choose one
mode) or the Accounts menu for a manual fallback. `absent` suppresses session
kicks, phantom recovery and session planning while preserving weekly behavior.
`present` cannot fabricate reset data or bypass fresh provider verification.
Every automatic direct Codex request rechecks native quota after acquiring the
account lock. Queued session actions cannot become weekly kicks after a plan
change. Existing automation switches are preserved. See
[detection design and verification](CODEX_LIMIT_DETECTION.md).

Codex kicks query the account's live `model/list` catalog before each request.
Automatic selection prefers `gpt-6-luna`, then the newest available numeric Luna
version (including `gpt-5.6-luna` for older clients). It uses the lowest advertised
`none`, `minimal`, or `low` effort and explicitly selects the standard service
tier. It never automatically escalates to Sol, Terra, Astra, or medium/high effort.
Catalog errors, unavailable explicit overrides, and missing economical models
stop the kick with an error. Update Codex if its catalog does not expose newer
models. The resolved model is recorded in kick history; status displays the
selection policy without querying providers.

Spark retired on September 14, 2026. Loading configuration removes entries for
`codex_bengalfox` and clears retired Spark model pins on ordinary Codex accounts.
Credential homes and history are retained. Discovery ignores stale Spark buckets,
and direct attempts to kick Spark fail before generation. This migration runs
when the upgraded application first loads the configuration.

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

Completion kicks use the provider's `haiku` alias, `--effort low`, thinking off,
and fast mode off. Explicit per-account model overrides remain available; low
cost settings still apply. Setup probes and `/usage` launches use the same Haiku
settings. Built-in tools and configured MCP servers are disabled for these
minimal requests. Interactive model preferences are not changed on disk.
The alias follows the provider's Haiku updates instead of pinning a retired version.

Claude direct status uses the Claude CLI `/usage` path. That path can activate
or refresh Claude's own view of a session, so TokenKick treats it carefully:

- Explicit `tk status --refresh` may use `/usage`.
- Repeated explicit refreshes within 5 minutes reuse a recent successful direct
  result.
- Rate-limited `/usage` attempts wait 5, 10, 20, 40, then at most 60 minutes.
  This per-account cooldown survives restarts and cannot be bypassed by forced
  refresh or an optional generation probe. A successful real direct read resets
  the sequence; other monitoring errors use the usual minimum interval.
- Status output identifies direct versus CodexBar fallback data, distinguishes
  authentication failures from monitoring errors, and shows the local retry time.
  Cached readings keep their observation age and cannot confirm a new kick.
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

Antigravity CLI accounts on Linux and macOS expose separate Gemini and Claude
accounts, each with its own 5-hour and weekly controls. Legacy aggregate CLI
accounts migrate once: the original label becomes Gemini, and a `(Claude)`
sibling is added. Both start with automation off; other providers and their
settings are preserved. Existing schedules are copied to the new sibling.

Native quota reads validate all four named buckets and the logged-in identity.
Each family uses only its own weekly and session data. Legacy CodexBar-only
accounts remain monitor-only until native CLI family accounts are discovered.

Each kick reads `agy models` and selects the newest Gemini Flash (Low), or Claude
Haiku (Low) when available, otherwise Sonnet (Low). There is no automatic Opus
or GPT-OSS fallback. Explicit overrides must be listed Low models from the
selected family. Low effort is encoded by the model label; `--effort` must not
be passed with these labels. These are economical preferences, not measured
subscription quota prices.

Requests run with `--sandbox`, disabled slash-command expansion, closed stdin,
a dedicated workspace, a fixed no-tools prompt, a 45-second provider deadline
and a 60-second process timeout. Sandbox terminal restrictions do not imply
that every provider tool is disabled. Permission bypass is never enabled.

A zero-use session with a deadline that tracks the current time plus five hours
is an available, unanchored candidate. TokenKick requires two native observations
from separate CLI processes before making it eligible; a fixed deadline remains
active even at zero use. Relative reset estimates cannot establish this evidence.
Independent processes matter because each CLI process can cache its quota response.

A successful exit or usage increase alone does not confirm an anchor. TokenKick
requires a new reset clock that stays fixed across independent native reads.
Sliding deadlines cannot confirm an anchor. Normal kick previews report the
specific eligibility blocker instead of a generic `not_fresh` result. A persistent
family-scoped attempt record prevents retries after errors, timeouts, or missing
confirmation, even if history rotates or an account is renamed. Inspect provider
status and history before explicit `tk kick "<label>" --force` recovery. Confirmed
requests cannot run again until the observed session has reset and at least five
hours have elapsed.

Enable controls with `tk auto enable "<label>"`, or preview/apply them through
[MCP](MCP.md). Weekly and session scopes can be toggled separately. Auto-kick
requires provider risk consent, is off by default, and needs a running daemon.
Standalone Gemini daily quotas remain monitor-only.

The hidden `tk antigravity probe-kick --family gemini|claude-gpt --account "<label>"`
diagnostic remains an explicit, quota-consuming investigation tool; automatic
kicks use the guarded normal kick path described above.


### Request evidence and triggers

Weekly and session kicks send the same minimal provider request. The trigger determines
which window is due and which anchor is verified; CLI success alone does not establish
either reset. Accepted requests without a verified anchor remain unconfirmed. Ambiguous
sends require explicit recovery, while a refusal before dispatch preserves pending work
without consuming an attempt. A stale JSONL observation cannot replace a newer native read.
