# Claude monitoring reliability (unpublished batch)

## Plan and acceptance criteria

1. Separate the latest monitoring attempt from the retained usage observation.
   Display the actual observation source (direct Claude, CodexBar fallback, or
   another source), a safe category-specific failure, and the local retry time in
   table, cache footer, and JSON. A monitoring rate limit is neither an expired
   login nor exhausted generation quota. Never display raw credential/output data.
2. Persist consecutive rate-limit failures per account. Retry after 5, 10, 20, 40,
   then at most 60 minutes; these are local delays, not provider reset promises.
   Existing caches with a rate-limit error start at five minutes. Successful real
   direct reads reset the sequence. Other errors retain the normal minimum interval.
   Forced refreshes and optional generation probes must not bypass a rate-limit
   cooldown. Background refresh scheduling must respect it too.
3. Cache reuse must not count as a new attempt, renew observation timestamps, clear
   a failure, or confirm a `/usage` kick. Preserve failure metadata even when a
   usable CodexBar fallback is returned. Persist metadata under the existing lock;
   reject older probe metadata when a newer attempt has already been saved.
4. Keep existing stale-data and identity safeguards. Test cooldown boundaries,
   capped growth, success recovery, restart persistence, legacy/malformed metadata,
   fallback behavior, forced refreshes, cached kick rejection, concurrent cache
   writes, and source-specific diagnostics using isolated fixtures only.

No credential changes, live generation requests, release, or deployment are part
of this change. Mac CodexBar credential selection remains a separate issue.

## Verification and existing-test changes

Implemented and verified locally:

- 55 new regression cases in `test_claude_monitoring.py`.
- Full Python suite: **1,720 passed**.
- macOS Swift suite: **202 passed**, using the local Python runtime. Swift needed
  normal build-cache access outside the restricted sandbox. No app release built.
- `ruff check .` and `git diff --check` passed.
- Isolated fixture integration exercises successive CLI refreshes: first failure,
  suppressed forced refresh, second failure with a longer cooldown, and eventual
  real success clearing the backoff. No live provider requests were issued.

Existing test changes in `test_cli.py` (no assertions removed or weakened):

1. `test_status_cache_footer_labels_claude_passive_refresh_failure`: expect the
   specific monitoring failure and retained direct source instead of the generic
   passive-refresh message.
2. `test_claude_usage_touch_persists_direct_success_timestamp`: the simulated
   successful read now supplies real-probe context and observation timestamps;
   the implementation no longer manufactures that evidence.
3. `test_claude_session_kick_uses_completion_then_usage_verification`: mark the
   simulated fresh usage read as actually performed; retain movement assertions.
4. `test_claude_session_kick_accepts_completion_evidence_when_usage_lags`: mark
   the simulated unchanged usage read as actually performed, preserving coverage
   of lagging provider data rather than accidentally testing a skipped probe.
5. `test_claude_reconciliation_probe_records_tracked_status_touch`: mark the
   simulated fresh read as performed; keep tracking and no-movement assertions.
6. `test_claude_reconciliation_probe_notifies_when_session_jumps`: mark the
   simulated fresh read as performed; keep notification and movement assertions.

New regressions separately require cached or unproven reads to fail confirmation.

## Files and batch boundary

Implementation: `tokenkick/claude_monitoring.py`, `tokenkick/models.py`,
`tokenkick/sources.py`, `tokenkick/status_cache.py`, `tokenkick/status_rendering.py`,
`tokenkick/cli.py`. Tests: `test_claude_monitoring.py`, `test_cli.py`.
Documentation: this file and `docs/PROVIDERS.md`.

These changes remain uncommitted alongside the previously verified account,
Codex-limit, notification, and selection-menu work. No commit, tag, push,
publication, deployment, credential change, or live generation request was made.
The provider can still return rate limits; this change handles and reports them
truthfully. Mac CodexBar's separate credential-scope issue remains unresolved.

## 2026-10-14: unattended refresh integration repair

Live evidence on Hetzner: the cached direct observation and last successful probe
were both 08:06 CEST, while daemon/helper passes rewrote the cache after 10:30.
No persisted probe error explained the age. One guarded direct read succeeded at
10:42 CEST without logging in or generating a completion.

Acceptance criteria for this repair:

1. Daemon polling and the detached `refresh-cache` helper explicitly permit guarded
   Claude `/usage` monitoring. Merely rendering cached status remains passive.
2. Discovery must not classify a failed/disabled/throttled direct read as an
   identity-only result and replace it with cached success. Persist the probe error,
   attempt time and backoff while retaining the previous observation separately.
3. Honor saved per-account monitoring opt-outs before discovery reads, as well as
   the global setting. Preserve existing minimum intervals and rate-limit backoff.
4. Exercise the actual discovery/merge/cache path with mocked provider boundaries:
   successful unattended refresh, failure, suppressed retry, increasing backoff,
   recovery and global/account opt-outs; verify the daemon refreshes before it
   evaluates automation.

Implementation changes are confined to `tokenkick/cli.py` and
`tokenkick/discovery.py`. Four regression cases were added to
`test_claude_monitoring.py`; no existing tests or assertions were changed.
All four fail against the released implementation and pass with the repair.
Full Python suite: **1,906 passed**. Ruff and diff checks passed.

This fixes TokenKick's unattended refresh and lost-diagnostic paths. Provider
outages/rate limits can still cause stale data; freshness safeguards stay in place.
The code repair is verified locally; a new package release/server deployment is
required to make it permanent on Hetzner. The live read only refreshed current data.

Separate orchestration finding: confirmed stagger requests ran overnight, but a
native unverified-reset result near a deadline led to a skipped cycle. The daemon
recovered and scheduled subsequent work. This repair does not change stagger policy.
