# TokenKick Subsystem Review 03: Kick Execution

**Scope:** `kicker.py`, `antigravity_automation.py`, `antigravity_windows.py`, the Antigravity parsing helpers in `antigravity.py`, `model_selection.py`, `codex_surface_stats.py`, `codex_surface_patterns.py`, and the `cli.py` kick-execution slice (lines 1751–6073). I also read the supplied tests `test_antigravity_windows.py` and `test_antigravity_automation.py` in full, plus the inventories for the other tests.

**Method:** This was a read-only trace. Nothing was executed. All repository text was treated as data.

---

## A. Confirmed code defects (prioritized)

### D1 · P2 · Confidence high: pre-send refusals are treated as failed kicks (retry budget burned, failure notifications sent)

**Paths**
- `tokenkick/cli.py` 4580–4645: `_kick_and_notify` returns `KickEvent(success=False, …)` for every refusal before dispatch. Examples:
  - 4586–4592: account removed, or auto-kick disabled
  - 4602–4612: no valid 5h window, or stale native data
  - 4615–4627: recovery history changed, or eligibility now false
  - 4634–4645: stagger blocker, `StateFileLockTimeout`, `UncertainActionError`
- `cli.py` 3839–3868: the due-pending executor
- `cli.py` 3363–3373: `_run_evaluate_account`

**Trigger**
1. A due pending session kick passes the outer checks at 3689–3817.
2. Inside `_kick_and_notify`, the Codex-direct automatic path re-fetches live status (4597–4601). Meanwhile the user has started a session, so eligibility now returns `active` or `recent_session_kick`.
3. Alternatively, a concurrent `tk kick` or MCP kick holds the account lock and produces `StateFileLockTimeout`.

**Wrong outcome**
- `record_pending_kick_failure` is called (3852). After the retry limit, the scheduled kick is marked "gave up" even though no request was ever sent.
- A failure notification fans out on every retry via `notify_kick` with context `scheduled_kick_failure` (3862–3868).
- `tk run` reports `kick failed: active` and returns an error flag (3373).

**Root cause:** `KickEvent` has a single `success` boolean. It cannot distinguish "refused before send" from "send failed".

**Minimal fix**
- Mark refusals explicitly, either with `event.post_kick_status = "refused"` or with a dedicated `refused: bool` field.
- In both callers, a refusal should do one of two things:
  - keep the pending kick with an unchanged retry count (lock or uncertainty cases), or
  - clear it with a logged reason (eligibility lost).
- A refusal should send no failure notification.

**Regression test:** Monkeypatch `_fetch_status` inside `_kick_and_notify` to return an ACTIVE, anchored status. Run `_execute_due_pending_kicks` with a due session pending kick and assert:
- `kick_account` is never called,
- `record_pending_kick_failure` is not called,
- no notifier is invoked,
- the pending kick is cleared with reason `active` (or kept, per policy).

Repeat with `state_file_lock` forced to raise `StateFileLockTimeout`, and assert the pending kick's attempt count is unchanged.

---

### D2 · P2 · Confidence high: the surface-reintroduction recovery kick can never send

**Paths**
- `cli.py` 3892: the history snapshot is taken once
- `cli.py` 3908–3934: reintroduce → `_record_codex_surface_management_event` (which calls `append_kick_event`) → `_kick_and_notify(recovery_history=history)`
- `cli.py` 4613–4622: the recovery comparison

**Trigger:** Any account where `_codex_surface_missed_kept_cluster` matches and demoted surfaces exist.

**Wrong outcome**
1. The management event is appended *after* the snapshot is taken.
2. `load_kick_history(limit=300)` inside the lock therefore differs from `recovery_history`.
3. The call returns `"Recovery history changed; retry after refreshing."` every time.

As a result:
- The surfaces are reintroduced, and the demotion state is cleared (`codex_surface_stats.py` 411–424).
- `result.count` still increments, and a `CodexSurfaceReintroductionRecord` with `recovery_cluster_id=None` is recorded.
- No notification is sent, and nothing reaches the daemon log.
- On the next poll the `demoted` map is empty, so the intended recovery is never retried.

For a second account in the same pass, the snapshot is stale anyway.

**Root cause:** The optimistic-concurrency token (the history snapshot) is invalidated by the caller's own write.

**Minimal fix:**
- Reload the history after recording the management event, and do it per account.
- Better: pass a monotonic history sequence or hash captured after the caller's writes.
- Only count `result.count` when `recovery_event.success` is true.

**Regression test:** Use real `append_kick_event` and `load_kick_history` against a tmp `CONFIG_DIR`, a surface-stats file with one demoted surface, and a matching missed cluster. Stub `kick_account` to return a generated success. Assert:
- `kick_account` is called exactly once,
- `result.records[0].recovery_cluster_id` is not `None`.

Add a second account in the same pass and assert that it also kicks.

---

### D3 · P2 · Confidence medium: phantom recovery can loop without bound when the post-kick read fails, and refusals consume attempts

**Paths**
- `cli.py` 4478–4499: the kick, then `_mark_status_cache_entry_stale`, then an unguarded `_fetch_status` and `_save_status_cache`
- `cli.py` 4563–4571: the recovery state is only written at the end
- `cli.py` 4613–4623: recovery passes `history=[]` to eligibility

**Trigger:** After a sent recovery kick, `_fetch_status` (or the cache save) raises. The codebase itself guards this same call against exceptions at 4281–4290, so raising is a plausible case.

**Wrong outcome**
- The exception escapes before `_update_phantom_recovery_state` runs, so neither `attempts` nor `last_attempt_at` is advanced.
- Remaining accounts in the pass are skipped.
- On the next poll, `_phantom_recovery_defer_seconds` finds no new state, and eligibility ignores history because of the recovery path. The only remaining throttle is `completed_at < 60s`.
- Another quota-consuming kick is sent each poll, and `PHANTOM_RECOVERY_MAX_ATTEMPTS` is never reached.

There is a second, separate problem. When `_kick_and_notify` *refuses* (D1 class), the code still falls through, increments `attempts`, and can emit "Phantom recovery failed after N attempts" without having sent anything.

**Root cause:** The durable attempt bookkeeping happens after fallible I/O, and the outcome is not distinguished.

**Minimal fix:**
1. Persist the recovery state (attempt count and `last_attempt_at = time.time()`) immediately after a *sent* kick, before the post-read.
2. Wrap the post-read in `try`/`except` and treat a failure as "unknown, defer".
3. On a refusal, do not increment attempts.
4. Use a fresh `time.time()` per account instead of the single `now` captured at 4397.

**Regression test:** Set up a phantom-ready Codex account. Stub `kick_account` to succeed, and make `_fetch_status` raise `RuntimeError` after the kick. Call `_execute_verified_phantom_recoveries` twice, simulating more than 60s between calls. Assert:
- exactly one `kick_account` call,
- recovery state `attempts == 1`, with `last_attempt_at` set.

---

### D4 · P2 · Confidence medium: the weekly burst-ladder "awaiting confirmation" guard anchors on a stale absolute reset time

**Paths**
- `cli.py` 2875–2892: `_was_pending_codex_fire_all_in_current_weekly_window` uses `status.resets_at`
- `cli.py` 1862–1880: the eligibility check
- `cli.py` 4743–4784: burst events with `verify_codex=False` (4771), always marked pending (4786–4797)
- `cli.py` 5913–5927: late attribution only considers `session` events, so weekly burst events stay `pending` forever

**Root cause:** When the weekly window is ready, `resets_at = R ≤ now`. The guard window `[R − 7d, R]` therefore:
- **excludes** a burst that was just sent (timestamp > R), so it fails to dedupe, and
- **includes** last cycle's burst (sent at ≈ R − 7d + δ), so it can falsely block.

Compare `_was_kicked_in_current_window` (2789–2791), which uses `now + resets_in_seconds`.

**Triggers and wrong outcomes**
- **(a) Repeated bursts.** The provider keeps reporting weekly-ready for one or more polls after a burst. Each poll sends another full ladder of N requests. `_was_kicked_in_current_window` cannot block this, because it requires confirmed events.
- **(b) Weekly kick suppressed.** If the provider status for an expired window carries the old `resets_at`, the previous week's burst blocks the new weekly kick with `codex_awaiting_confirmation`. Due pending kicks get cleared the same way (3769–3784).

Which of (a) or (b) occurs depends on the provider's representation. Either way, the code cannot dedupe the post-reset burst it was designed for.

**Minimal fix:** When weekly is ready, match pending burst events whose `timestamp ≥ max(resets_at or now, now − WEEKLY_PENDING_GRACE)`. Otherwise use `now + resets_in − window`. Also add weekly late attribution or verification, or a bounded expiry for the weekly `pending` status.

**Regression tests**
1. Status ready with `resets_at = now − 3600`, plus a pending burst event at `now − 120`: eligibility should report `codex_awaiting_confirmation`.
2. Same status, with a pending burst event at `resets_at − 7d + 30`: the account should be kickable.

---

### D5 · P2 · Confidence medium: Antigravity kicks can be authorized by evidence that the confirmation step will never accept, leading to a permanent family lock-out

**Paths**
- `cli.py` 1846–1850: the `resets_in_seconds <= 0` and `session_resets_in_seconds <= 0` branches do not check reset provenance
- `antigravity.py` 695–700: relative prose produces `reset_evidence="relative_estimate"`
- `antigravity_windows.py` 41–42: `native_window` requires `provider_timestamp`
- `antigravity_automation.py` 100–120: for `kick_type="kick"`, confirmation checks only the **weekly** window
- `antigravity_automation.py` 51–76: an unconfirmed attempt blocks until manual recovery

**Trigger:** The weekly bucket is described only by prose. The supplied test fixture at `test_antigravity_windows.py` 254–281 uses exactly this shape. When the prose reads ready or 0, `antigravity_family_status` sets FRESH, and eligibility returns `kick`.

**Wrong outcome**
1. The request is sent.
2. `anchor_confirmed(session=False)` → `native_window(..., "weekly")` returns `None`, so confirmation is impossible.
3. The attempt guard keeps `retry_after=None`.
4. The family's weekly **and** session automation stays blocked until a forced recovery. This repeats at every weekly reset.

**Minimal fix:**
- Authorize via the expired-clock branches only when the deciding window has `provider_timestamp` evidence.
- For `kick_type="kick"`, accept confirmation from the native session window, since a fresh 5h anchor is observable. Weekly-native movement should be an alternative, not a requirement.

**Regression test:** Build a status where the weekly window has `relative_estimate` evidence and `resets_in_seconds=0`, and the session window is native. Assert that eligibility is not kickable, or, under the alternative fix, that a post-kick native session anchor confirms the kick and `attempt_blocked` releases after the session reset.

---

### D6 · P2/P3 · Confidence high (code); trigger depends on provider strings: the Antigravity reset-prose parser treats any "now" substring as "ready"

**Path:** `antigravity.py` 722–727

**Trigger:** A description containing "reset" together with "unknown", "known", or "snow". For example, "Reset time unknown" returns 0 seconds.

**Wrong outcome:**
- On a weekly bucket, this produces FRESH and an automatic weekly kick on fabricated readiness.
- On a session bucket, it produces a session kick.

Both are then caught by D5's unconfirmable dead-end.

**Root cause:** Substring matching instead of token matching.

**Minimal fix:**
- Use word-boundary regexes, for example `\b(ready|now)\b` with negative context.
- Return `None` for anything not positively parsed.
- Never let prose alone produce a 0-second reset; require native evidence.

**Regression test:** Parametrize over "Reset time unknown", "Resets in 3d (known issue)", and "Resets now". Only the last may return 0, and even that must not make the account kickable (see D5).

---

### D7 · P2 · Confidence medium (code) / hypothesis (impact): Claude kicks have no account isolation or identity precondition

**Paths**
- `kicker.py` 52–55: any `provider == "claude"` account is kickable, regardless of source
- `kicker.py` 396–400: the invocation is `claude -p …` with the ambient environment, and there is no per-account config dir or identity check (unlike Codex `CODEX_HOME` at 408–409 and Antigravity at 380–383)
- `cli.py` 5268–5272 and 5326–5332: verification forces `source=CLAUDE_DIRECT` and saves the result under *this* account's cache key

**Trigger:** A second Claude account, for example one discovered via CodexBar by email, whose identity differs from the local `claude` login.

**Wrong outcome:**
- The quota-consuming request goes to the ambient login rather than the selected account.
- The ambient account's `/usage` data may then be written into the other account's cache entry and history. This depends on whether `_fetch_claude_direct` performs identity checks, which is not visible in this packet.

**Minimal fix:** Before sending, under the action lock, require `read_claude_identity()` to match `account.identity_email`. Otherwise refuse, the same way Antigravity does. Alternatively, mark non-direct Claude accounts monitor-only.

**Regression test:** A CodexBar-source Claude account with `identity_email="b@…"` and `read_claude_identity` stubbed to return `"a@…"`. Assert:
- `subprocess.run` is never called,
- the event error mentions identity,
- the cache entry for b is not written.

---

### D8 · P3 · Confidence medium: pending (planner) ownership is not rechecked at send time, and is deleted even when the kick fails

**Paths**
- `cli.py` 4624–4625: the eligibility recheck under the lock omits `pending_kick`
- `cli.py` 4038, 4092, 4126, 4158, 4177, 4198: unconditional `remove_pending_kick(account)` after `_kick_and_notify`

**Trigger**
- **(a)** `tk plan apply` (CLI or MCP) writes an orchestrated pending kick after `_kickable_window_targets` ran but before the lock is acquired.
- **(b)** Any refused or failed immediate kick when targets were built with `suppress_pending=False`, or via `--force`.

**Wrong outcome:**
- The planner-owned slot is preempted.
- The orchestrated or retry-bearing pending entry is erased even though nothing anchored.

**Minimal fix:**
- Inside the lock, load the account's pending entry and pass it to `_kick_eligibility` (unless `force`).
- Remove the pending entry only when `event.success`, or when it was a non-orchestrated entry superseded by an actual send.

**Regression test:** Stub `load_pending_kicks` to return an orchestrated future entry at lock time. Assert that `_schedule_or_kick_target` (no schedule) sends nothing and the entry still exists. Also: with `kick_account` returning failure, assert the pre-existing entry is retained.

---

## B. Hypotheses and minor observations (not counted above)

| # | Path | Note | Status |
|---|---|---|---|
| H1 | `cli.py` 3689–3692 | `_execute_due_pending_kicks` invalidates the pending kicks of any account absent from the *passed* list. If `run_cmd` or the daemon pass visibility- or `--codex-only`-filtered lists (as `_run_refresh` 3402–3404 produces), the planner's pending kicks for hidden or non-Codex accounts are destroyed. | Callers not supplied; needs confirmation |
| H2 | `cli.py` 3694–3695 | When `stagger.owns(account)` is true, a due pending kick is skipped with no log and is never cleared. A planner-vs-controller conflict therefore stays latent until the work window expires. | Depends on `stagger.py` conflict logic (not supplied) |
| H3 | `kicker.py` 256–264 | `subprocess.run(timeout=60)` kills only the direct child. Descendants of `agy` or `codex` (for example language servers) can be orphaned and may still complete the request. `sources.py` already uses `_terminate_process_group` for the Claude probe. | Process trees of the provider CLIs are unverified |
| M1 | `codex_surface_stats.py` 95–107, `cli.py` 4867–4870 | If every surface is force-pruned or demoted, the empty order falls back to `DEFAULT_CODEX_SURFACE_ORDER`. This dispatches surfaces the user explicitly pruned. | Confirmed code path |
| M2 | `cli.py` 2590–2596, 2667–2673, 2543–2556 | Phantom observation and recovery files use non-atomic, unlocked `write_text`, and they are written on read paths (pruning). A crash or concurrent writer loses `cooldown_until` or `attempts`, which lets recovery restart early. | Confirmed code; impact bounded |
| M3 | `kicker.py` 302–319 | `cli_name` is the absolute binary path for Antigravity, and appears in "not found" and "timed out" errors in history and notifications (home-path disclosure). A `FileNotFoundError` raised inside `before_send` is mislabeled "CLI not found". | Confirmed |
| M4 | `cli.py` 4989–5008 | `record_confirmation` succeeds, then `_save_status_cache` raises. The attempt guard is released while the history says "unconfirmed". The two pieces of state are inconsistent, though this fails open only after a session reset. | Confirmed, low impact |
| M5 | `kicker.py` 460–466 | The Codex prompt embeds the account label, which may contain an email under provider-first labels. That label is sent to the provider and stored in `prompt_text`. The nonce alone would suffice. | Confirmed; privacy nit |
| M6 | `model_selection.py` 47–48 | An unavailable override yields the generic "no economical model" error. | UX |

---

## C. Design improvements (up to 5)

1. **A typed kick outcome contract.** Replace the overloaded `success`/`confirmed`/`error` triple with an enum: `REFUSED(reason)`, `SEND_FAILED`, `SENT_UNCONFIRMED`, `SENT_PENDING`, `CONFIRMED`. This structurally removes D1 through D3. Every caller (due pending, `run`, phantom, reintroduction, MCP apply, Swift) should branch on it. Notifications should fan out only for the send-related states.

2. **One "already handled in this window" primitive.** The weekly, session, pending-burst and phantom guards each recompute window bounds differently: `now + resets_in`, absolute `resets_at`, or near-full heuristics. A single function `current_window_bounds(status, kind, now)` with exhaustive tests (ready, expired, stale `resets_at`, missing `resets_at`) would have caught D4.

3. **Send-time preconditions as a single checklist under the lock.** The checklist would cover:
   - fresh native status
   - identity match for every provider (fixes D7)
   - current config flags
   - pending owner (fixes D8)
   - stagger guard
   - attempt guard

   Each item should be logged as a structured refusal. This also makes MCP preview and apply parity auditable.

4. **Provider subprocess hygiene.** Use `start_new_session=True` with `killpg` on timeout, a scrubbed environment, a redacted binary path in errors, and an explicit bound on Antigravity's per-kick process launches. Today a kick causes about six `agy` session starts (re-fetch, then post-read, then verification), which costs quota-adjacent load and time while the per-account lock is held.

5. **Durable state discipline.** Route phantom observation and recovery, the Antigravity attempt guard, and reintroduction bookkeeping through `state_io` atomic writes with transactions. Forbid writes on read paths. Persist "attempt sent" before any post-send I/O, as the Antigravity `before_send` hook already does correctly.

---

## D. Coverage matrix

| Subsystem | Evidence in packet | Depth | Findings |
|---|---|---|---|
| `kicker.py` (invocation, parsing) | full | deep | D7, H3, M3, M5 |
| `model_selection.py` | full | medium | M6 |
| Antigravity automation, windows, parsing | full | deep | D5, D6, M4 |
| Eligibility (`_kick_eligibility` and helpers) | full slice | deep | D4, D5, D8 |
| Due pending executor and `tk run` evaluation | full slice | deep | D1, H1, H2 |
| Phantom observation and recovery | full slice | deep | D3, M2 |
| Codex surface retries, burst, late attribution | full slice | medium-deep | D2, D4 |
| `codex_surface_stats` / patterns | full | medium | M1 |
| Claude session kick and reconciliation | full slice | medium | D7 |
| `stagger.py`, `action_state.py`, `state_io`, `scheduling.py`, `sources.py`, daemon loop, `run_cmd`, MCP core, Swift | symbol map only | not reviewed | caveats H1, H2 |
| Tests | Antigravity tests in full; others as inventory only | partial | (see below) |

---

## E. Limits and residual uncertainty

- **Missing implementations.** I could not see the bodies of `stagger.py`, `account_action`, `state_file_lock`, `load_kick_history` / `append_kick_event`, `_fetch_status` / `sources`, `run_cmd` / `_daemon_poll_once`, or `_kickable_window_targets`. Consequences:
  - Lock ordering and deadlock between `stagger.execution_lock` and `account_action` is unassessed.
  - H1 and H2 depend on callers.
  - D3's trigger depends on whether `_fetch_status` raises.
  - D2 assumes the history read and write go to the same store with no filtering of management events.
- **Unverified provider behavior.** Real provider semantics are assumed nowhere. Specifically unverified:
  - whether Codex reports a stale weekly `resets_at` after expiry (D4a vs D4b),
  - whether Antigravity's weekly bucket is prose-only in production (D5),
  - whether multi-identity Claude configurations exist in practice (D7).
- **Tests.** `test_cli.py` bodies were unavailable, so existing tests may mock `_kick_and_notify` in ways that hide D1 and D2. The reported test counts (1831 Python, 203 Swift) are not evidence against these traces.
- **Other subsystems.** MCP, Swift, packaging and notifications outside kick paths are covered by other review calls.