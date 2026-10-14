# Codex weekly kick confirmation

The existing delayed confirmation path only matches session events against five-hour reset
clocks. Weekly burst attempts therefore remain pending on accounts with no session limit.

Implement a separate weekly reconciler invoked by existing live-refresh and daemon paths.
Require successful sent generation, the correct native account/bucket, a seven-day absolute
reset deadline matching the request interval within 30 seconds, and two fresh observations
at least 30 seconds apart with the same deadline. Persist the first observation in history;
replaying cached data must not count as independent evidence. Reject known pre-existing
deadlines, moving deadlines, stale/error/fallback data, ambiguous matches and wrong identities.

New attempts record account identity and the pre-request weekly deadline. Legacy attempts
without these fields may be reconciled using the stricter timing/stability evidence and unique
saved label, binding identity on the first observation. Only the current seven-day window is
eligible. No live state is manually marked confirmed and no additional generation is sent.

Confirm one weekly attempt, supersede later attempts from the same burst, retain weekly-specific
diagnostics and leave session confirmation and session surface learning unchanged. A session
request can start an unused weekly window, but this change does not infer weekly confirmation
from a session confirmation alone. Existing session events keep their current confirmation path.

Acceptance: replay the observed Skysolaranlagen timing; prove durable reconciliation across
reloads and idempotence, reject negative evidence, verify refresh integration and presentation,
run regression suites and lint. Keep the work in the unpublished batch: no CTP or deployment.

## Implemented and verified

- Added durable weekly evidence and reconciliation to the existing live-refresh/daemon hook.
- Added 47 regression cases, including the 12:30:42 request / 21 October 12:30:42
  reset replay, restart persistence, duplicate suppression, identity changes, moving clocks,
  actual request recording, live CLI refresh and cached-only status.
- Python suite: 2,048 passed. macOS suite against the rebuilt local runtime: 213 passed.
- Ruff, diff whitespace checks, bundled runtime smoke test, wheel resources and bundled
  MCP documentation reads passed. No live requests, production writes or deployment.

Three existing tests changed deliberately with the confirmation contract:

1. `test_codex_direct_weekly_kick_marks_command_only_when_pre_status_missing` became
   `test_codex_direct_weekly_kick_stays_pending_when_pre_status_missing`: an accepted request
   remains eligible for later weekly proof instead of being labelled merely unchecked.
2. `test_codex_direct_weekly_kick_confirms_when_provider_moves` became
   `test_codex_direct_weekly_kick_does_not_confirm_from_generic_provider_movement`: usage/state
   movement alone is insufficient to prove a newly anchored weekly deadline.
3. `test_fire_all_single_surface_subset_uses_fire_all_path` now expects the weekly-specific
   pending method; the session method remains unchanged for session requests.

This is an unpublished local batch. Production history can reconcile through ordinary fresh
polls after a later authorized deployment, provided its current weekly window is still active.
