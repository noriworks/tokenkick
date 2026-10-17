# Existing Claude windows and quota visibility

## Strengthened acceptance criteria

- Separate suppression from permission: a fresh direct Claude weekly deadline
  whose inferred start is safely in the past suppresses redundant weekly kicks,
  even at rounded zero usage. It does not authorize generation or retire a guard.
- Retire an accepted weekly-request guard against an existing weekly window only
  with a uniquely matching accepted receipt and two fresh direct observations
  with positive weekly usage. Require known identity, the same stable window,
  at least two minutes between observations, bounded minute-rounding tolerance
  against the original deadline (never a moving reference), and account locking.
  Missing, contradictory, stale, synthetic or ambiguous evidence stays blocked.
- Preserve history: record existing_weekly_window, not a new anchor or fabricated
  request confirmation. Retain a polling-interval cooldown and suppress another
  weekly request; future eligible session requests remain possible. No generation
  during recovery. Existing later-window recovery remains available.
- Expose accepted versus uncertain requests and exact recovery requirements in
  terminal and JSON/MCP diagnostics. Account guards remain the dispatch authority.
- Display the separate Argon weekly allowance once per underlying identity in
  default terminal status, including cached/stale indication. No invented session
  limit, new account, auto-kick permission or model-policy change.
- Show stagger ownership/next slot in account actions and exclude managed members
  from unsolicited kick-now banners. Manual explicit actions retain existing policy.
- Regression coverage: captured failure sequence, restart, matching receipts,
  stable versus sliding clocks/rounding, same-pass and repeated dispatch prevention,
  future sessions, Argon deduplication, and managed-account display/banner agreement.
- Verify locally and with read-only production evidence replay. CTP the verified
  batch. Production installation/state repair is separate from this request.


## Verification

- Both existing-week and later-window recovery paths run through the daemon
  regression: recovery sends nothing, cooldown blocks dispatch, active weekly
  quotas suppress another weekly request, and the next eligible session can send
  exactly once. Persisted state is reread between observations.
- Production observations from 17 October, 13:27:50 and 13:41:08 Europe/Berlin,
  were replayed against a temporary copy of the matching accepted weekly guard.
  Recovery completed with `existing_weekly_window`, unchanged history, enforced
  cooldown, redundant weekly suppression, and zero generation calls. No live
  state was changed; deployment remains necessary for unattended recovery there.
- Existing test expectations changed deliberately: an accepted legacy receipt
  now gets accepted-request wording; the daemon regression no longer expects a
  redundant weekly send and instead verifies a future session exactly once.
