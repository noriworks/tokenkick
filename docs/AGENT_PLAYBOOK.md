# TokenKick Agent Playbook

This guide is for coding agents operating TokenKick headlessly on a user's
server. TokenKick is a quota-window planner, not a natural-language scheduler.
Your job is to translate the user's intent into safe, existing `tk` commands.

## First Principles

- Optimize usable coding coverage, not the earliest possible kick.
- Prefer already-active sessions before consuming a fresh window.
- Treat the user's stated work window as approximate. If they say "home in 1
  hour, coding for 5-6 hours", plan around that future interval.
- Use TokenKick's primitives only: read state, run deterministic `tk plan`,
  toggle auto-kick, configure recurring smart-schedule windows, inspect pending
  kicks, and run `tk run`.
- Codex surface strategy is configuration, not normal schedule planning. Read it
  when needed, but do not change Burst ladder, surface order, gap, or demotion
  settings unless the user explicitly asks.
- Smart scheduling is recurring work-window scheduling. It times eligible kicks
  inside configured windows; it is not a one-shot "kick this account at 17:30"
  command.
- `tk plan` can compute a deterministic multi-account session timeline from an
  explicit work window and cached provider state. It writes only pending session
  kicks when the user explicitly approves `--apply`.
- TokenKick still has no one-shot direct-fire scheduler and no full autonomous
  mode. Due kicks execute later through the normal daemon/scheduler guardrails.
- Codex-Spark is a detected Codex quota bucket, not a subscription assumption.
  If `codex-spark (...)` appears in status, treat it as a separate account/window.
  If it does not appear, do not invent Spark plans. Do not rely on Spark in
  `tk plan` unless that account has explicit `usable_session_minutes`; the rough
  placeholder is intentionally skipped by orchestration.

## Hard Safety Constraints

- Always set `TK_NO_INTERACTIVE=1`.
- Use `--json-output` whenever a command supports it.
- Never run `tk kick --force`.
- Never run hidden or active diagnostics such as `tk codex-surface-test`.
- Never run quota-consuming commands until you have shown the user the exact
  command first. Quota-consuming commands include `tk run` and `tk kick`.
- First-time auto-kick enable may return `auto_kick_consent_required` with
  provider-specific consent text. Show that text to the user and wait for
  explicit approval before rerunning with `--accept-risk ENABLE`; never add that
  flag on the user's behalf.
- Prefer `tk run --dry-run --json-output` before `tk run --json-output`.
- Do not invent commands. In particular, there is no one-shot per-account
  direct-fire scheduler and no autonomous multi-account choreography mode.

## Setup And Onboarding

For a fresh machine, setup is discovery plus save. It is not permission to
enable automation for every discovered account:

```bash
TK_NO_INTERACTIVE=1 tk setup
TK_NO_INTERACTIVE=1 tk status --json-output
```

`TK_NO_INTERACTIVE=1 tk setup` is headless and non-prompting. It saves newly
discovered accounts with auto-kick disabled by default and preserves existing
saved auto-kick settings on rediscovery. After setup, review status before
enabling anything. Enable only accounts whose cached or refreshed state is
usable; unknown, stale, auth-expired, or duplicate Codex homes should be left
disabled until the user resolves them.

If duplicate Codex homes appear for the same email/identity, explain that
TokenKick can track separate Codex homes, but automation should only be enabled
for homes that currently work. Suggested cleanup commands after user approval:

```bash
TK_NO_INTERACTIVE=1 tk auto disable "<label>"
TK_NO_INTERACTIVE=1 tk accounts hide "<label>"
```

On macOS, direct Codex discovery/status may trigger a system prompt saying the
terminal wants to control `Codex Computer Use.app`. This comes from Codex
CLI/helper behavior, not TokenKick using AppleScript directly. Allowing it gives
full Codex direct behavior; denying it may leave Codex status/kicks degraded or
stale.

If the provider exposes the Codex-Spark bucket, setup may save a sibling account
like `codex-spark (name)`. It uses the same Codex home but a separate quota
bucket and its own status window. New Spark accounts follow normal onboarding:
auto-kick stays disabled until explicitly enabled.

## Safe Read Commands

Read cached state for routine checks and repeated rechecks:

```bash
TK_NO_INTERACTIVE=1 tk status --json-output
```

Use a live refresh only when you genuinely need current provider state, usually
once at the start of a planning task. `tk status --refresh` can run live provider
reads, including Claude `/usage`; that is low-cost but nonzero-cost and should
not be polled in a loop.

```bash
TK_NO_INTERACTIVE=1 tk status --refresh --json-output
```

Relevant fields from the actual JSON shape:

```json
{
  "schema_version": 1,
  "cached": true,
  "cached_at": "2026-06-04T21:31:00Z",
  "refresh_error": null,
  "refresh_in_progress": false,
  "accounts": [
    {
      "label": "codex (personal)",
      "provider": "codex",
      "account_key": "codex-home|codex|/home/example/.codex-personal",
      "state": "active",
      "used_percent": 6.0,
      "weekly_used_percent": 6.0,
      "weekly_headroom_percent": 94.0,
      "resets_at": 1781190000.0,
      "session_used_percent": 0.0,
      "session_resets_at": 1780608420.0,
      "auto_kick": true,
      "weekly_auto_kick": true,
      "session_auto_kick": true,
      "schedule_enabled": true,
      "schedule_weekdays": "18:00-23:30",
      "schedule_weekends": null,
      "kickable": false,
      "kick_type": null,
      "kick_blocked_reason": "pending_kick",
      "kick_cooldown_remaining_seconds": null,
      "next_kick_at": "2026-06-04T23:30:00Z",
      "pending_kick": {
        "kick_at": "2026-06-04T23:30:00Z",
        "next_action_at": "2026-06-04T23:30:00Z",
        "window_basis": "session"
      }
    }
  ]
}
```

Read reset and schedule timeline from the cache:

```bash
TK_NO_INTERACTIVE=1 tk calendar --json-output
```

Relevant fields from the actual JSON shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-04T21:31:00Z",
  "timezone": "Europe/Berlin",
  "days_ahead": 7,
  "warnings": [],
  "pending_kicks": [
    {
      "account_label": "codex (reserve)",
      "kick_at": "2026-06-04T23:30:00Z",
      "next_action_at": "2026-06-04T23:30:00Z",
      "window_basis": "session",
      "work_start": "2026-06-04T22:30:00Z",
      "work_end": "2026-06-05T04:30:00Z"
    }
  ],
  "events": [
    {
      "account": "codex (work)",
      "type": "session_reset",
      "predicted_at": "2026-06-05T01:19:00Z",
      "optimal_kick_at": null,
      "schedule": null
    }
  ]
}
```

Inspect configured recurring work windows and pending kicks:

```bash
TK_NO_INTERACTIVE=1 tk schedule show
```

Plan a multi-account coding window from cached state:

```bash
TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-23:30 --json-output
```

Use `--date YYYY-MM-DD` and `--timezone Europe/Berlin` when the user's natural
language intent is not for the local current day/timezone.

Preview what TokenKick would do without consuming quota:

```bash
TK_NO_INTERACTIVE=1 tk run --dry-run --json-output
```

Inspect recent actions:

```bash
TK_NO_INTERACTIVE=1 tk history --json-output
TK_NO_INTERACTIVE=1 tk history --anchored --json-output
TK_NO_INTERACTIVE=1 tk doctor --json-output
```

Inspect Codex surface strategy without changing behavior:

```bash
TK_NO_INTERACTIVE=1 tk codex-strategy status --json-output
```

Codex strategy has two separate switches. Burst ladder controls firing mode:
enabled means fast serialized configured surfaces, disabled means the patient
adaptive ladder. Auto-demotion controls pruning: it is per-account and decides
whether TokenKick may hide redundant surfaces. Do not assume Burst ladder means
auto-demotion is enabled. With Burst ladder on and auto-demotion off, TokenKick
still fires the configured surfaces every time. With both on, it fires the
effective active set after demotion, force-prune, and force-keep are applied.

## Commands That Can Change State

Enable or disable auto-kick:

```bash
TK_NO_INTERACTIVE=1 tk auto enable "codex (personal)" --json-output
TK_NO_INTERACTIVE=1 tk auto disable "codex (personal)" --json-output
TK_NO_INTERACTIVE=1 tk auto session enable "codex (personal)" --json-output
TK_NO_INTERACTIVE=1 tk auto session disable "codex (personal)" --json-output
```

If a first-time enable returns `auto_kick_consent_required`, show the returned
`payload.consent.text` to the user. Run the consented command only after the
user explicitly approves the risk and the exact command:

```bash
TK_NO_INTERACTIVE=1 tk auto enable "codex (personal)" --accept-risk ENABLE --json-output
TK_NO_INTERACTIVE=1 tk auto session enable "codex (personal)" --accept-risk ENABLE --json-output
```

Set or clear recurring smart-schedule windows:

```bash
TK_NO_INTERACTIVE=1 tk schedule set --account "codex (personal)" --weekdays 18:00-23:30 --timezone Europe/Berlin
TK_NO_INTERACTIVE=1 tk schedule disable --account "codex (personal)"
TK_NO_INTERACTIVE=1 tk schedule clear --account "codex (personal)"
```

Apply an approved orchestration plan. Show this exact command to the user and
wait for approval before running it:

```bash
TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-23:30 --apply --yes --json-output
```

Change Codex surface strategy only after an explicit user request. These are
configuration mutations, not routine planning actions:

```bash
TK_NO_INTERACTIVE=1 tk codex-strategy enable
TK_NO_INTERACTIVE=1 tk codex-strategy disable
TK_NO_INTERACTIVE=1 tk codex-strategy order repo-skip legacy repo
TK_NO_INTERACTIVE=1 tk codex-strategy gap 90
TK_NO_INTERACTIVE=1 tk codex-strategy demotion enable --all
TK_NO_INTERACTIVE=1 tk codex-strategy demotion disable --all
TK_NO_INTERACTIVE=1 tk codex-strategy demotion enable "codex (personal)"
TK_NO_INTERACTIVE=1 tk codex-strategy demotion disable "codex (personal)"
```

The deprecated `tk codex-fire-all ...` commands are aliases for
`tk codex-strategy ...`; prefer the strategy commands.

Run TokenKick after showing the command to the user:

```bash
TK_NO_INTERACTIVE=1 tk run --json-output
```

## Worked Example: Driving Home

User: "I'm driving home, will be there in 1 hour. I'll code for about 5-6
hours. Make sure my sessions are optimally set up."

Safe agent flow:

1. Read cached state with `status --json-output`.
2. Read timeline with `calendar --json-output`.
3. Translate the natural language into an explicit work window, for example
   `18:30-00:30`.
4. Run `tk plan --work-window ... --json-output`.
5. Explain the timeline, planned pending kicks, coverage gaps, skipped accounts,
   and pending-kick diff.
6. Show the exact `tk plan --work-window ... --apply --yes --json-output`
   command. Run it only after the user approves.
7. If the cached status is stale or conflicts with the user's app view, do one
   `status --refresh --json-output` before deciding.

Possible response:

```text
I translated this into a 18:30-00:30 work window. The cached plan uses the
currently active work session first, then schedules reserve when that
usable coverage is nearly spent. It reports one uncovered gap after 23:45
because the currently eligible accounts do not fully cover six hours.

I will not change anything unless you approve this exact command:
TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-00:30 --apply --yes --json-output
```

## Worked Example: Already Covered

User: "I can code for 3 hours now."

If cached `status --json-output` shows an active Codex or Claude session with
`session_resets_at` after the planned end time, do not kick just because another
account is fresh. Fresh windows are valuable later. Use `--refresh` only if the
cache is stale or the user reports a conflicting app view.

Possible response:

```text
Your current active session covers the 3-hour block. I will not kick another
account. I can leave auto-kick unchanged and re-check later with:
TK_NO_INTERACTIVE=1 tk status --json-output
```

## Planning Limits

- `tk plan` consumes only explicit time windows and cached provider state.
  Agents may add smarter context such as calendar events, traffic, or location,
  but they must translate that context into a concrete `--work-window`.
- Provider state can go stale. Use cached `status` for routine reads and one
  explicit `status --refresh` only when fresh provider state is genuinely needed.
- `usable_session_minutes` is the persistent source of truth when configured per
  account. `plan_tier` and `usable_session_tier_defaults` are rough, unverified starting
  guesses, not measured caps. Use `tk plan --usage "<label>=3h"` for a one-plan
  assumption without changing saved calibration. Automatic burn-rate learning is
  future work.
- Active-session coverage is approximate: TokenKick estimates remaining usable
  coverage from cached `session_used_percent`.
- TokenKick cannot force the user to switch accounts; it prints the timeline and
  schedules pending kicks only.
- Manual app usage can change the plan. Due-pending execution rechecks status
  before kicking.
