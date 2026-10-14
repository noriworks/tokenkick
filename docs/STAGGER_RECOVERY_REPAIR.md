# Bounded recovery for uncertain Antigravity reset observations

## Evidence and revised policy

On 2026-10-14 Gemini was due at 03:21:46 CEST. At 03:22:05 the
controller rejected unverified native session evidence. Its ten-minute normal
polling interval exceeded the two-minute alignment tolerance, so the next read
skipped the slot. Withholding generation was correct; the slow evidence retry
was avoidable. Logs do not establish the exact provider-side cause.

A broad late-kick grace would risk replaying missed slots after an outage. Instead:

- Retain only an already scheduled slot delayed by valid native data whose session
  anchor is inconclusive, within two minutes before / five minutes after its due time.
- Recheck the native pair after 30, 60 and 120 seconds, at most three early rechecks.
  The absolute five-minute deadline and counter persist across restarts; repeated
  failures cannot extend the grace. Auth errors, missing/invalid identity, stale or
  malformed data and permission/schedule conflicts do not get this fast retry.
- A fresh successful read may recover only that slot, within its grace, if the
  family is idle, native attempt cooldowns permit it and no intervening activity
  changed the scheduling reference. Never retry an uncertain generation.
- Use the actual confirmed new anchor to realign subsequent slots; do not fire into
  active windows, bypass reserves or immediately catch up multiple old slots.
- Cancel retained recovery on control edits. Expired/exhausted recovery falls back
  to normal future scheduling. Persist a small safe diagnostic record (family,
  original slot/deadline, reason, retry count and recovered/skipped outcome).
- Terminal/MCP distinguish evidence rechecks from next generation, with retry time
  and bounded deadline. Normal healthy polling cadence is unchanged.

## Acceptance criteria

Reproduce the overnight read failure and recover within the grace, including after
process restart; verify deadline/counter bounds, slow observations, clock jumps,
external usage and phase changes, pause/disable, quota reserves, native attempt
cooldowns and uncertain dispatch guards. Verify no duplicate, early or burst
requests across subsequent cycles. Verify terminal and MCP status diagnostics.
Existing long-outage and safe-refusal behavior must remain unchanged. Provider
calls and generation are mocked; no production policy change is needed to test it.

This repair joins the verified, unreleased Claude polling fix. Publication and
production deployment remain a separate release step.

## Verification

Implemented with 31 new parametrized regression cases in `test_stagger.py`;
existing cases are unchanged. The incident reproduction fails against the prior
implementation and passes with the repair, including four subsequent alternating
slots. Coverage includes persisted retries, budget exhaustion, clock jumps, slow
reads and dispatch preflight, external activity, cooldowns, reserves, controls,
invalid state, uncertain dispatch and terminal/MCP diagnostics.

Final local verification: 1,937 Python tests passed; repository Ruff and
`git diff --check` passed. No live generation, server configuration changes,
publication or deployment were performed for this repair. Provider-side causes
of the original inconclusive response remain unproven; sustained uncertainty
still deliberately skips a slot rather than forcing a request.
