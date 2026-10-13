# Antigravity reset evidence and stagger repair (unpublished)

## Plan and acceptance criteria

The native response currently returns full, unused five-hour buckets whose
absolute reset timestamps advance with every read. TokenKick must distinguish
this from an active window with a fixed deadline. Zero usage alone is not proof.

1. Pause the existing Hermes ten-minute controller and verify its persisted state.
2. Preserve reset provenance: provider timestamps versus timestamps estimated
   from relative prose. Never use relative estimates as anchor evidence.
3. Observe an unused full-horizon native bucket twice, at least five seconds
   apart, using separate owned CLI processes to avoid their cached responses.
   Timestamp the quota response before identity lookup. Only a deadline that moves with observation time is classified as
   available/unanchored. Stable deadlines remain active, including at zero use.
   Failed, stale, incomplete, or conflicting observations remain ineligible.
4. Expose that session state through status/JSON and normal kick eligibility,
   retaining identity, weekly quota, freshness, and durable attempt guards.
5. Confirm a kick only when subsequent independent native reads show a fixed
   reset deadline. Sliding timestamps and successful output alone cannot confirm
   an anchor. Unconfirmed sends remain suppressed, without generation retries.
6. Verify with regressions and full checks. For the requested stagger, start only
   the first eligible family, verify its actual anchor, then schedule exactly one
   second-family action 150 minutes after the first anchor's start. Recheck both
   families at execution; preserve intervening user activity. No recurring Hermes
   controller, no forced kicks, no changes to standalone Claude/Codex, no release.

The fixed-window assumption remains subject to native verification after the
first real request. A provider that exposes a rolling replenishment horizon
instead of an anchored window cannot honestly be reported as staggered.

## Regression inventory

This repair changes `tokenkick/antigravity.py`, `antigravity_automation.py`,
`cli.py`, `sources.py`, and `status_rendering.py`; adds `antigravity_windows.py`
and `antigravity_stagger.py`; updates `test_antigravity_automation.py`; adds
`test_antigravity_windows.py`; and updates `docs/PROVIDERS.md` plus this note.
Earlier batch changes in overlapping files and other dirty files are retained.

Added `test_antigravity_windows.py` (63 cases): independent native clock evidence,
normal kick/preview eligibility, family and account isolation, stale/failed reads,
weekly exhaustion, durable attempt suppression, malformed/tampered evidence,
native versus relative reset provenance, process cleanup and cache isolation,
observation timing, presentation/cache round trips, and true versus false anchor
confirmation. One-time follow-up cases cover early/late execution, intervening
user activity, conflicting automation/schedules, missing/ambiguous accounts,
dry-run behavior, one-request-only execution, and failed/unconfirmed responses.

Existing `test_antigravity_automation.py` changes: its shared status fixture now
marks provider timestamps explicitly. The five cases of
`test_post_request_verification_requires_correct_family_reset_clock` now advance
a simulated clock and return independently timestamped observations. This models
the strengthened verification contract; the expected success/failure outcomes
remain unchanged. No existing test was removed or weakened.

## Live investigation

Hermes controller `59d6a66ba59f` was paused. Its recorded execution is complete.
The native five-hour deadlines move with read time while both session buckets are
unused. Repeated reads within one CLI process can retain an older deadline,
which is why the first implementation's same-process verification was rejected.
After switching to independent processes, both native family statuses classified
as available/unanchored and passed normal eligibility. Dynamic selection chose
Gemini Flash Low and Claude Sonnet Low. Both families' daemon controls remain off.

Validation uses a temporary source archive on Hetzner. The installed 1.22.0
package and running service are unchanged; the unpublished batch is not released.

## Stagger activation on 2026-10-13

One normal Gemini Flash Low request succeeded and was confirmed. Its fixed
session reset is `2026-10-13T18:52:41+02:00`, implying a start at 13:52:41 CEST.
A subsequent independent native check still returned that deadline and 1.01%
session usage. Claude/GPT remained at zero session use with a sliding deadline.

Hermes job `b8f428c3c928` is scheduled **once**, at
`2026-10-13T16:22:41+02:00`, for the Claude/GPT follow-up. It runs a script without
an LLM and leaves local execution output; it does not poll or reschedule itself.
The script checks the temporary runtime's SHA-256 before importing it, then uses
`tokenkick.antigravity_stagger.run_once`. Both daemon auto controls remain off.
The former ten-minute Hermes job remains paused.

The script is saved at
`~/.hermes/scripts/tokenkick-stagger-once-20261013.py`. It uses the temporary
`/tmp/tokenkick-agy-validation-20261013.zip` on Hetzner. Missing/changed runtime,
unavailable Mac/gateway/SSH, changed quota state, or arrival more than ten minutes
late blocks the request. A live transport/checksum smoke test returned `not_due`
without generation. The second request has **not yet run**, so the full stagger
is still pending. Ongoing five-hour recurrence is not enabled by this one-shot.

Verification: full Python suite 1,783 tests; macOS Swift suite 202 tests; Ruff and
`git diff --check`. No commit, tag, push, package release, app release, installed
server update, or service restart.
