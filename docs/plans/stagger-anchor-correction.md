# Recovering disproven Antigravity anchors

An observed fixed deadline can later be contradicted by independently verified sliding,
unused native windows. The controller currently retains the old anchor as a cooldown and
continues its old grid, potentially missing a usable cycle. This differs from an ordinary
expired window, a missed slot, or an uncertain request.

Acceptance and implementation:

- Only fresh, complete paired native evidence proving `available_unanchored` can retract
  an anchor that should still be active. Unknown/stale/error data cannot retract it. A
  backwards clock or ordinary expiration must not create a catch-up request.
- Persist a correction separately from observed anchors and actual request guards. Remove
  the disproven anchor's scheduling constraint, never a confirmed request cooldown or an
  uncertain dispatch guard. Preserve corrections across restart and permission changes.
- Schedule a new start for the unused family at the earliest safe time, respecting its own
  durable five-hour cooldown and at least 150 minutes after the other family's surviving
  anchor/request. Both families becoming unused must not cause simultaneous requests.
- After a confirmed corrective request, use its actual anchor as the recurring phase.
  Preserve active peer windows; wait until both reset and spacing permit its next request.
  Fresh external activity supersedes a correction. Failed/uncertain requests keep the
  existing explicit-recovery requirement; safe refusals must not create a tight retry loop.
- Expose pending corrections and the last correction in terminal and read-only JSON/MCP
  status. Do not call the group aligned until both native anchors establish the offset.
- Replay adoption -> unknown -> unused, test multiple subsequent cycles/restarts, both
  families, cooldowns, quota, controls, malformed state, external activity and dispatch
  failures. Run full Python/macOS suites, lint and local packaging checks.

Keep the change in the unpublished batch. No provider calls, production state changes,
observer restart, CTP, publishing or deployment during this implementation.

## Verification

Implemented persisted anchor corrections, safe replacement scheduling, confirmed-phase
continuation, external-activity supersession and terminal/MCP diagnostics. Also closed the
synthetic-observation path at native-window validation, before classification can discard
the synthetic marker.

Added 27 regression cases to `test_stagger.py`; no existing tests or assertions changed.
Coverage includes the observed adopted -> unknown -> unused sequence, either family,
six alternating actions with state reloads, simultaneous invalidation, peer spacing,
durable and runtime cooldowns, quota/reserve controls, pause, uncertain requests, read and
dispatch failures, malformed persisted corrections, synthetic/stale/error evidence,
clock rollback, external activity and terminal/MCP presentation. Existing missed-slot,
crash-after-claim and bounded-recovery tests remain passing.

Results: 2,075 Python tests passed; 213 macOS tests passed against the rebuilt local runtime.
Ruff, diff checks, bundled isolated-home smoke test, wheel resources and MCP stdio resource
checks passed. Changes remain a local unpublished checkpoint. No production correction has
been exercised by this implementation; the temporary observer remains paused.
