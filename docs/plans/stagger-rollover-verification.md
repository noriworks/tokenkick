# Verify rollover windows before claiming alignment

## Evidence and limits

The 14 October daemon evaluated Gemini at 20:21:22, 20:31:59 and 20:43:10
CEST and continued to accept the apparent rollover. Only at 20:55 did its
paired native reads prove an unused sliding horizon and allow a corrective
request. The delay was not request execution time: six-second fixed-looking
reads repeatedly postponed the slot and ordinary polling added detection lag.
Saved evidence does not identify the exact instant provider evidence changed.

A provider may retain a fixed-looking idle deadline for minutes. Polling faster
cannot establish an anchor it does not expose, and elapsed time alone cannot
justify a generation. Exact continuous 150-minute spacing cannot be guaranteed
through ambiguous evidence, external usage, quota exhaustion or outages.

## Implementation and acceptance

- Treat a zero-use deadline advancing a known anchor by one or more exact
  five-hour periods as provisional, not established activity. Preserve the
  previous phase and anchors; never claim aligned based on that rollover.
- Persist the candidate and original detection time. Use read-only rechecks
  every 60 seconds for five minutes, then every 120 seconds up to a fixed
  45-minute deadline (covering the observed 34–42 minute ambiguity), then normal polling with an explicit
  unresolved-rollover status. Reload/repeated identical readings must not renew
  the fast-check budget. No extra generations or retries are allowed.
- Positive native usage or a changed, non-rollover native anchor may supersede
  the candidate. Independently verified unused sliding evidence retracts it and
  feeds the existing correction path, preserving all dispatch cooldowns,
  permissions, reserves, identity checks, and uncertain-request guards.
- Expose provisional evidence and its next check through terminal, JSON and MCP.
  Test both families, restart, repeated fixed clocks, bounded reads, uncertainty,
  invalid state, external usage, cooldowns and multiple real recurring cycles.
- Keep existing stable zero-usage classification for ordinary windows; the new
  safeguard is contextual to the controller's known rollover, not a claim that
  zero usage always means idle.

## Quota recommendation (not a control change)

The observed Claude/GPT weekly delta near one Sonnet Low request was 0.97984
percentage points. At 168/5 requests weekly, that extrapolates to about 32.9%
if representative; provider metering and concurrent use can change the amount.
The existing request already asks only for OK with Low model selection. No
unverified cheaper model or unsupported effort flag should be substituted.
Recommend opt-in work-period orchestration, skipping idle nights/weekends and
letting useful user work start windows. A weekly kick budget would bound cost;
strict continuous staggering must become secondary when that budget is reached.
Work-period policy is a proposal only in this batch; all live controls remain.

No CTP, publishing, deployment, or production configuration changes.

## Verification of this unpublished batch

- Added 25 regression cases in `test_stagger.py`; no existing tests/assertions
  were modified. Includes both families, repeated/restarted evidence budgets,
  daemon wake timing, 34/42-minute plateaus, six alternating confirmed actions,
  external activity, simultaneous provisional windows, clock rollback, malformed
  state, permission/quota/uncertain-dispatch guards, and terminal/MCP output.
- Final full Python suite: 2,100 passed. Ruff and diff checks passed.
- Final locally rebuilt runtime: 213 macOS tests passed; wheel and bundled MCP
  resources passed. Wheel/sdist built with the existing Hatchling backend.
- Production was inspected read-only for timing; no requests, controls, services,
  installations, commits, tags, pushes or publications were changed by this batch.
- The replay bounds detection lag after usable evidence appears; it cannot prove
  the provider will expose usable evidence sooner in production. Work-period
  orchestration and budgets remain proposals per the user's explicit choice.

## Release authorization

The user subsequently authorized CTP, deployment and the updated macOS installer.
Ship this verified batch as 1.23.5; work-period policy remains a proposal and
production controls are to be preserved. Production rollout records are private.
