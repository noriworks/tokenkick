# TokenKick review 02: provider monitoring

**Scope:** `sources.py`, `direct.py`, `codexbar_source.py`, `status_cache.py`, `claude_monitoring.py`, `codex_limits.py`, `claude_setup.py`, plus the supplied tests (`test_codex_limits.py` and `test_claude_monitoring.py` in full; `test_sources.py`, `test_direct.py` and `test_claude_setup.py` as inventories only).

**Method:** Static reading only. Nothing was executed and no provider was contacted. Line numbers refer to the original source. Any claim about how a provider or CodexBar behaves is marked as an assumption.

---

## Part A: Confirmed code defects (8)

### B1 · P1 · Confidence: high (code path) · The quota-spending Claude probe can run as the wrong account, without throttling, on ordinary status reads

**Where**
- `tokenkick/sources.py:1081-1084`: the probe trigger in `_fetch_claude_direct`.
- `sources.py:1047-1051`: the identity "gate" result is downgraded to an ordinary failure.
- `sources.py:2008-2024`: `_fetch_claude_probe`.
- `direct.py:387-411`: `probe_claude_status`, which runs `claude -p`.
- `status_cache.py:68-69`: the `config is None` path.

**Trigger**
1. The account has `status_probe_enabled=True`.
2. The active Claude CLI login is a different account (or is unreadable, or is logged out).
3. CodexBar has no usable data. This is the normal case on a Linux server, where CodexBar is a macOS app.

**What goes wrong**
- `_claude_cli_usage_gate_status` correctly returns `IDENTITY_MISMATCH` (lines 1125-1133).
- `_fetch_claude_direct` stores that only as `usage_failure` and falls through.
- The only guard left before the probe (1081) is `cooling_down`, which is only about rate limits.
- `claude -p "...1+1"` then runs a real generation as whichever account the CLI is logged into. That is not the configured account.
- The result is a fabricated `ACTIVE` status (`session_used_percent=1.0`, `session_window_minutes=300`) attributed to the configured account.

**Secondary triggers**
- Outside `claude_cli_usage_refresh_allowed()`, the gate is skipped entirely. That includes `_refresh_status_cache(allow_claude_usage=False)`, the default at `status_cache.py:806-812`. Every such fetch reaches the probe if CodexBar is unavailable.
- `_fetch_status(account, None)` passes no `ClaudeProbeContext`. A fresh empty context makes `cooling_down` always False, so even the rate-limit cooldown is bypassed.
- The probe records nothing: no attempt time, no minimum interval. Every poll that reaches it spends a generation.
- Because a generation can start a provider session window, "monitoring" effectively becomes recurring kicking.

**Root cause:** Monitoring and generation share one fallback chain. Identity, authentication, consent and throttle decisions made by the gate are not carried into the probe branch.

**Minimal fix**
- Run `_fetch_claude_probe` only when all of these hold:
  - the call is inside an explicit probe-authorized context (not plain `fetch_status`);
  - `_claude_identity_probe_error(account) is None`;
  - the gate result is not `NOT_AUTHENTICATED` or `DISABLED`;
  - a persisted per-account minimum interval has elapsed.
- Record each probe in the context, or in the kick history.
- Do not synthesize `session_used_percent=1.0`.

**Regression tests**
- `test_probe_never_runs_on_identity_mismatch`:
  - Set the identity probe to return `IDENTITY_MISMATCH`, CodexBar to `UNKNOWN`, `status_probe_enabled=True`, and make `_fetch_claude_probe` call `pytest.fail`.
  - Run with and without `claude_cli_usage_refresh_allowed(force=True)`.
  - Assert `UNKNOWN` with the mismatch text.
- `test_probe_not_reached_from_plain_fetch_status_or_config_none`.
- `test_probe_throttled_across_process_restarts`: persist, reload, assert one call per interval.

**Note:** Existing `test_claude_monitoring` fixtures fail on generation only during cooldown. Nothing covers mismatch or unauthenticated states.

---

### B2 · P2 · Confidence: medium · Claude CodexBar fallback is not tied to the account, and is used after an explicit identity mismatch

**Where**
- `sources.py:1058-1079`: the fallback account is built with no `codexbar_account` or identity.
- `codexbar_source.py:144-150` and `352-354`: Claude reads local history and the widget snapshot.
- `codexbar_source.py:421-427`: history chooses `unscoped`, then `preferredAccountKey`, then the first account.

**Trigger:** The configured account is A. `~/.claude.json` is now account B, so the gate returns `IDENTITY_MISMATCH`. CodexBar has Claude data.

**What goes wrong:** Whatever Claude account CodexBar holds is returned as A's status (`claude-codexbar-fallback`). The status table, the planner and the reservation advisories all consume B's quota as A's.
- **Assumption:** CodexBar on the same host most likely follows the same Claude login. If so, after a login switch the fallback is very probably B's data, exactly the account the gate rejected.

**Root cause:** The fallback is selected by provider only. The identity verdict from the gate is not consulted.

**Minimal fix**
- When the gate returns `IDENTITY_MISMATCH` or `IDENTITY_UNREADABLE`, fail closed (`UNKNOWN`).
- Otherwise, pass `codexbar_account=account.identity_email` and require an email match in the CodexBar entry.

**Product decision needed:** `test_sources.py:1899 test_claude_direct_identity_mismatch_falls_back` appears to encode the current behaviour. Only its name was supplied, not the body. Changing this is a deliberate contract change.

**Regression test:** `test_identity_mismatch_does_not_return_unscoped_codexbar`. The CodexBar double returns `ACTIVE` for `other@x`. Assert the result is `UNKNOWN`, or at least that `identity_verified=False` and automation is blocked.

---

### B3 · P2 · Confidence: high (logic) · An expired session JSONL reading overrides a fresh native Codex read

**Where**
- `sources.py:362-365`: `_fetch_codex_direct`.
- `sources.py:423-441`: `_codex_session_status_should_override_appserver`.
- `sources.py:504-519`: `_codex_session_status_has_stronger_session_anchor`.
- `sources.py:2998-3023`: `_find_latest_rate_limit` throws away the event timestamp.

**Counterexample (traced through the code)**
1. The native read shows a genuinely new session: provider-usage, `ACTIVE`, weekly 30% used (anchored), session 1% used, `session_resets_in=17500`, capability `present`.
   - The "clean unanchored reset" check is False because the state is not `FRESH` (line 431).
   - The "clean session reset" check is False because session usage is 1%, not 0% (433).
   - `looks_like_phantom` is True, because 17500 ≥ 0.95 × 18000 (444-467).
2. The newest local JSONL `token_count` is 8 hours old. Usage came from another surface. It shows session 60% used, resetting 3 hours ago, and weekly 25%.
3. At line 437, max used (60) is greater than 30, so the override returns True.

**What goes wrong:** The returned status shows a 60% session from an expired window. The weekly figure also regresses from the live 30% to the stale 25%. Even without the usage comparison, line 519 treats the expired reset (0 + 120 < 17500) as a "stronger anchor".

**Root cause:** The override never checks that the JSONL windows are still current, or that the JSONL event is newer than the native window start. The event timestamp is discarded at 3016-3019.

**Minimal fix**
- Return the event timestamp from `_find_latest_rate_limit`.
- Only allow an override if:
  - `session_status.session_resets_at > now`,
  - the JSONL weekly `resets_at` matches the native weekly reset within tolerance, and
  - the event is newer than `native_session_resets_at - 300 min`.
- Set the JSONL status `observed_at` to the event time.

**Regression test:** `test_expired_jsonl_session_cannot_override_live_native_read`. Use the fixture above and assert the native status is returned. Add a positive control where the JSONL is current and the override still happens.

---

### B4 · P2 · Confidence: high · The status-refresh lock never expires: background refresh can stay disabled permanently

**Where**
- `status_cache.py:902-918`: `_status_refresh_lock_info`. The final `return` at line 918 makes the age check at 915 meaningless.
- `status_cache.py:886-898`: reaping only happens when a PID was parsed.
- `status_cache.py:948-976` and `1010`: non-atomic writes.

**Trigger**
- A crash or kill between the `O_EXCL` create (about 954-957) and the PID write (about 966) leaves an empty file. The PID is `None`, so it is never reaped, and line 918 reports the lock as active forever.
- Other ways to reach the same state:
  - an unreadable lock file (`_read_refresh_lock` returns `None` while the file exists, line 907-908);
  - a crash during `write_text(child_pid)` at line 1010, which truncates before writing;
  - PID reuse by a live, unrelated process (line 912 has no age cap).

**What goes wrong:** `_start_background_status_refresh` never starts again. Users see "Background refresh still running for 3h…" indefinitely.

**Minimal fix**
- Return `None`, or unlink, when `age_seconds > STATUS_CACHE_REFRESH_LOCK_MAX_AGE_SECONDS` and the PID is `None`.
- Apply a hard age cap even for a live PID.
- Write the lock atomically, ideally with a PID plus process start time or a nonce.

**Regression tests**
- Create an empty lock file with an old mtime. Assert `_status_refresh_lock_active()` is False and `_acquire_status_refresh_lock()` is True.
- Same check for a live PID with age greater than 3 × MAX.

---

### B5 · P2 · Confidence: high (race), medium (frequency) · The Antigravity refresh lock can be stolen, and is removed without an ownership check

**Where**
- `sources.py:2188-2224`: `_antigravity_cli_refresh_lock`.
- `sources.py:2227-2232`: `_antigravity_cli_lock_stale` (staleness by mtime only).
- `sources.py:2133-2153`: the observation sleep and second session run while the lock is held.

**Triggers**
1. **Holder runs past 45 s.** One session can take far longer than its 12 s ready timeout. The deadline is only checked between ports, and each port can try 3 endpoints with an 8 s timeout each (2349-2370, 2381-2414). Add stops of up to 4 s, `OBSERVATION_DELAY_SECONDS`, and a second session. A waiting process then sees the lock as stale, unlinks it and acquires it.
2. **System sleep.** macOS sleep during `time.sleep` has the same effect, because staleness is measured on the wall clock.
3. **Two waiters (time-of-check/time-of-use).** Both see stale, A unlinks and creates, then B's `unlink()` (2201) deletes A's fresh lock and B creates. Both proceed.

**What goes wrong**
- The original holder's `finally` (about 2219-2224) unconditionally unlinks the *new* owner's lock.
- Concurrent `agy` sessions run idle-observation pairs that interleave. This contaminates the sliding-reset classification used by the recurring Antigravity stagger. That impact is inferred, since `antigravity_windows.py` was not supplied.

**Minimal fix:** Use an `fcntl.flock` on a persistent lock file (the repository already has `state_io.state_file_lock`). Otherwise, write a nonce, check it before unlinking, and check PID liveness.

**Regression test:** Hold the lock in context A. Make `_antigravity_cli_lock_stale` return True so context B steals it. Exit A. Assert B's lock still exists, and that no second holder can enter while B holds it.

---

### B6 · P2 · Confidence: medium · Codex "identity unavailable" still uses home-scoped native and JSONL data without verifying the account

**Where**
- `sources.py:332-337`: the "unavailable" case is downgraded to a fallback error.
- `sources.py:339`: `_fetch_codex_appserver_ratelimits`.
- `sources.py:622-628`: only checks that `auth.json` exists.
- `direct.py:67-91`: `read_codex_identity` returns `None` on a JSON parse error or `OSError`, or when there is no `tokens` dict, `account_id` or email.

**Trigger:** `auth.json` exists but no identity can be read from it (malformed or partially written, or a token layout without email or ID), and `codexbar_account` is set.

**What goes wrong**
- The code intends to go to CodexBar, keyed by email.
- Instead it still starts `codex app-server` in that home and returns its result if that is not `UNKNOWN`, or returns the JSONL reading.
- Either way, the data comes from whichever identity the home currently holds, labelled as the configured account. This is an account-isolation gap.

**Related cases**
- Accounts without `provider_home` skip the identity check completely (595-600) and read `~/.codex/sessions` (397-401).
- There is also a time-of-check/time-of-use gap: identity is read before the app-server starts.

**Minimal fix**
- When `_codex_auth_identity_unavailable(...)` is True, skip the native and JSONL reads and use only the scoped CodexBar path.
- Record `identity_verified` on the status.

**Regression test:** Use `provider_home` with `auth.json = "{}"` and set `codexbar_account`. Make `_read_codex_appserver_ratelimits_for_account` and `_fetch_codex_session_jsonl_status` call `pytest.fail`. Assert the result comes from CodexBar or is `UNKNOWN`.

---

### B7 · P3 · Confidence: high · A slow but successful Codex read is treated as a failure

**Where**
- `sources.py:786-790`: a latency note is written into `error`.
- `codex_limits.py:21-24` and `46-48`.
- `direct.py:110-128`: elapsed time includes app-server startup and the up-to-2 s `_stop_codex_appserver_process` run in the `finally` block.

**Trigger:** The response arrives at about 9 s and SIGTERM takes about 1 s, so `elapsed_ms` exceeds 10000.

**What goes wrong**
- `session_limit_info` requires `not status.error`. A weekly-only plan therefore shows `unknown` instead of `absent`, and `plan_stale=True`.
- The automatic native preflight blocks kicks when `error` is set (`test_codex_limits.py:434-449` asserts this for `error`).
- Result: spurious warnings and missed automatic kicks on slow but valid reads.

**Minimal fix:** Put the latency into `source_diagnostics["elapsed_ms"]`, and measure it around the request only.

**Regression test:** `_parse_codex_appserver_ratelimits(..., elapsed_ms=10500)` with a weekly-only payload. Assert `error is None`, `session_limit_effective == "absent"` and that the latency is present in diagnostics.

---

### B8 · P3 · Confidence: medium · CodexBar-sourced Codex statuses bypass the new duration and capability policy

**Where**
- `codex_limits.py:59-69`.
- `codexbar_source.py:867-879`: no `codex_session_limit` or limits-version field is set.
- `codexbar_source.py:897-906`, `932-934`.

**Trigger A:** A weekly-only Codex plan is read through CodexBar (CLI, history or snapshot).
- `session_limit_info` reports detected/effective `unknown`.
- `session_actions_allowed` returns **True**, because `None` is not in `{"absent","unknown"}` and the window is `None`.
- The two functions now disagree about the same status.

**Trigger B:** The windows lack `windowMinutes`. This is possible from history, see 497-502.
- `_select_codexbar_rate_window` and `_select_codexbar_session_window` both return the same dict (`usage.secondary`).
- The weekly usage and reset are then reported as a "session".

**Bounding:** Automatic kicks may already be blocked by `_auto_kick_blocked_by_codexbar_fallback` (`cli.py`, not supplied), so the impact is mainly on display, the planner and manual flows.

**Minimal fix**
- For Codex, run CodexBar windows through `_classify_codex_windows(native=False)`.
- Otherwise, set `codex_session_limit="unknown"` unless a 300-minute window is explicitly present.
- In `session_actions_allowed`, treat `None` plus a non-legacy source as unknown.

**Regression test:** Parse CodexBar JSON with only a 10080-minute window. Assert `session_actions_allowed(acct, s) is False`. Also check that a payload without `windowMinutes` never produces a session equal to the weekly window.

---

## Part B: Lower-priority observations and hypotheses (not counted above)

| # | Kind | Location | Note |
|---|---|---|---|
| H1 | Hypothesis, P2 if concurrent | `sources.py:198-219`, `codexbar_source.py:101-109` | Module-global caches are saved and restored without thread safety. If two passes interleave (A enters, B enters, A exits, B exits), A's cache dict stays installed outside any pass, so native reads are reused indefinitely. The Claude state is thread-local, which suggests threads exist; whether passes really overlap is unverified. The send-time preflight that swaps these globals (from `test_codex_limits.py:399-420`) shares the same risk. |
| H2 | Hypothesis, P3 | `direct.py:135-142`, `182-215` | `codex app-server` stderr is a pipe that is never read. Verbose stderr over the pipe buffer could stall the child, which would show up as a timeout. Use `DEVNULL` or drain it. |
| H3 | Provider assumption | `sources.py:2998-3038` | The latest JSONL `token_count` is not filtered by limit ID. If the last turn used a non-default bucket (for example Spark), the main account could take that bucket's data. |
| H4 | Secret handling | `sources.py:2311-2312`, `2610-2650` | `agy --csrf_token <hex>` is visible in `ps` to other users on a shared server. `ps -ax` is not filtered by user, so the language server of another local user could be selected. The identity check only mitigates this when `identity_email` is set (`sources.py:2543-2545` returns `None` otherwise). |
| O1 | Confirmed, P3 | `codexbar_source.py:602-609` | A missing `observed_at` is treated as fresh (age `None`, so not stale; stamped as now). Widget-snapshot selection uses `max` on raw strings (564), which mis-orders mixed timestamp formats. |
| O2 | Confirmed, P3 | `codexbar_source.py:769-771` | When the requested account is not found, the single entry without an email is returned for any account. This risks mixing accounts. |
| O3 | Confirmed, P3 | `sources.py:905-912` | `_codex_appserver_rate_limit_key` ranks buckets by slot position (secondary as weekly), which is inconsistent with the new duration-based mapping. |
| O4 | Confirmed, P3 | `sources.py:953-955`, `3051` | The 10079→10080 normalization is used only for classification. The returned `window_minutes` stays 10079, so any `== 10080` check further down silently misses it. |
| O5 | Confirmed, P3 (stale clocks) | `status_cache.py:645-650`, `claude_monitoring.py:36-40` | After a backward wall-clock step, newer results are dropped as "older writer", which pins a stale `RATE_LIMITED` error. Meanwhile `cooling_down` is False because the attempt time is in the future, and the minimum interval is skipped (`age<0`). Result: `/usage` is called on every poll until the clock catches up. |
| O6 | Confirmed, P3 | `sources.py:2144-2153` | If the second idle-observation read fails, the valid first read is discarded and the status becomes `UNKNOWN`. Lines 2068-2071 have a redundant branch. |
| O7 | Hypothesis | `sources.py:1449-1460`, `1594-1596` | The pipe capture doesn't run in its own session, and the PTY cleanup only signals the process group while the leader is still alive. Grandchildren spawned by Claude may survive. |
| O8 | Packaging, out of scope | `status_cache.py:1003` | `sys.executable -m tokenkick.cli` may not work for a frozen CLI bundled in the Swift app. |

---

## Part C: Design improvements (5)

1. **Make the status layer side-effect free.** Move `status_probe_enabled` / `_fetch_claude_probe` out of `fetch_status` into the kick pipeline. That gives it consent, a ledger, ownership, throttling and identity verification. This separates checking credentials from monitoring, and removes the root of B1.
2. **Record provenance on every status.**
   - Add `observed_at` (provider event time), `source_kind` (native, fallback or replay), `identity_verified` and `notes`.
   - Reserve `error` for failures.
   - Gate automation on `source_kind=native ∧ identity_verified ∧ fresh`.
   - This addresses B2, B3, B6, B7 and O1.
3. **Use one cross-process lock primitive.** Replace the `O_EXCL` + mtime and PID-file schemes (status refresh, Antigravity refresh) with `flock` plus an owner nonce. Add a per-account `/usage` probe lock, so the daemon and `tk status --refresh` can't send duplicate requests.
4. **Make Claude backoff cover all failure categories.** Apply category-specific backoff to timeout and provider errors too, not only rate limits. Currently a timeout resets the streak (1281-1284), so alternating 429/timeout responses collapse to probing every pass. Persist an explicit `next_retry_at` clamped to now + max cap, so clock skew can neither lock monitoring out nor flood it.
5. **Replace module-global caches with an explicit `PollContext`.** Pass it through the fetchers. This removes the save/restore race (H1), the global swaps in the send-time preflight, and the single-slot Claude and Antigravity caches that ignore the account.

---

## Part D: Coverage matrix

| Subsystem | Source reviewed | Tests reviewed | Depth | Main residual risk |
|---|---|---|---|---|
| Codex native (`direct.py`, `sources.py` 315-1032) | Full | `test_codex_limits` full; `test_sources`/`test_direct` names only | Deep | Provider payload semantics; app-server teardown |
| Codex JSONL / session file | Full | Names only | Medium | Event formats, bucket attribution (H3) |
| Claude direct / `/usage` / backoff | Full | `test_claude_monitoring` full | Deep | TUI output formats; `cli.py` consumers not supplied |
| Claude probe | Full | Fixture only | Deep | Daemon call sites of `fetch_status` (in `cli.py`) |
| Antigravity source | `sources.py` full; `antigravity.py` and `antigravity_windows.py` not supplied | Names only | Medium | `OBSERVATION_DELAY_SECONDS`, classification logic |
| CodexBar | Full | Names only | Medium | CodexBar's real identity behaviour (an assumption) |
| Status cache / locks | Full | Partial | Deep | `state_io` lock semantics not supplied |
| `codex_limits` / `claude_monitoring` | Full | Full | Deep | — |
| `claude_setup` | Full | Names only | Light | — |

**Limits:**
- `cli.py` (kick preflight, daemon loop, `_auto_kick_blocked_by_codexbar_fallback`), `models.py`, `source_utils.py`, `state_io.py`, `antigravity*.py` and the Swift code were not in this packet.
- Severity estimates that depend on those consumers are flagged as such.
- Nothing was executed. The reported pass count of 1831 tests does not cover the counterexamples in B1-B3, B5 and B6. Each one has a specific regression test listed above.