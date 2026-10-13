# TokenKick review 04: orchestration

**Scope:** `stagger.py`, `stagger_commands.py`, `antigravity_stagger.py`, `orchestration.py`, `scheduling.py`, `reservation_advisories.py`, `reset_calendar.py`, and the supplied `cli.py` sections:

- plan, cancel and render (7540–8255)
- run (8256–8353)
- poll and daemon lifecycle (13822–14216)
- update (14589–14683)
- daemon loop (14684–15026)

Tests: `test_stagger.py` was read in full. The other test files were supplied as function inventories only, with no diff bodies.

**Basis:** I read the code only. Nothing was executed, and no provider behaviour is assumed.

---

## A. Confirmed code defects

### 1. P2: Stagger "blocked" notification crashes on the exact states it should report, and can abort the whole daemon poll

**Confidence:** High.

**Where**
- `tokenkick/stagger.py` 482–514, specifically the `members(config, data["group"])` call at 501.
- `tokenkick/cli.py` 14878: the unguarded `stagger.daemon_step(...)` call.
- `tokenkick/stagger.py` 557–561: the only guarded caller.

**Trigger**
1. A stagger is enabled.
2. A member account is then removed, re-keyed, or its `identity_email` changes. For example, the user re-logs into a different Google account.

**Trace**
1. `tick()` calls `members()` at line 402. It raises `StaggerError("Member missing or ambiguous…")` or `"Member identity changed…"`.
2. The exception is captured as `read_error` and re-raised inside the lock.
3. The `except` handler persists `state="blocked"`.
4. `daemon_step` maps "blocked" to `kind="blocked"`. Because the signature differs, it calls strict `members()` again to compute routes.
5. That call raises the same `StaggerError` before `notified` is saved.

**Wrong outcome**
- The user is never notified about the most important failure class. The notification is retried, and fails, on every evaluation.
- When the failing `daemon_step` is the one inside `_daemon_poll_once` (14878), there is no try/except. The exception skips everything that follows in that poll iteration:
  - status refresh;
  - reset detection;
  - due pending kicks;
  - reservation advisories;
  - Codex and Claude auto-kicks.
- The poll loop then logs `poll_error`.
- This always happens on the first poll after a daemon start or restart. After that it happens whenever `_daemon_sleep_seconds` ends a wait before the stagger's poll interval has elapsed, for example when Codex deferrals shorten the sleep.

**Minimal fix**
- In `daemon_step`, resolve notification routes tolerantly. Use member accounts whose keys still match; otherwise fall back to the global routes from `_notification_configs_for_global`.
- Wrap the notification block in `try/except Exception` and log the error.
- At `cli.py` 14878, wrap `stagger.daemon_step` in `try/except Exception` that logs `stagger_error`, so the stagger can never take down the poll for unrelated providers.

**Regression tests**
- Activate the stagger, then save a config with `accounts[:1]`. Monkeypatch `notify_stagger`. Call `stagger.daemon_step(Config.load())` twice. Assert:
  - no exception;
  - exactly one message per backend containing "Member missing";
  - `runtime["notified"]` is set.
- Monkeypatch `stagger.daemon_step` to raise. Assert that `_daemon_poll_once` still calls `_refresh_status_cache` and `_kick_all_enabled_accounts`.

### 2. P2: Applying a plan for another window silently overwrites an already-applied plan's kick for the same account

**Confidence:** High on the mechanism, medium on intent. The docstring says other plans' kicks are kept.

**Where**
- `tokenkick/orchestration.py` 381–430 (`build_pending_kick_diff`).
- `tokenkick/orchestration.py` 551–555 (apply loop).
- `tokenkick/orchestration.py` 456–464 (docstring).
- Pending storage is a dict keyed by `account_key_string`: `scheduling.py` 660–700 and `_load_pending_kicks_unlocked`.

**Trigger**
1. Apply tonight's plan (18:00–23:00) that includes account A.
2. Preview and apply tomorrow's plan (`--date`, 09:00–17:00) that also includes A.

**Trace**
1. `pending.get(kick.account_key)` returns tonight's orchestrated kick.
2. `_pending_matches_planned` is false because the `kick_at` and work window differ.
3. The entry is classified as `replaces_orchestrated`, and apply overwrites it.
4. `_stale_orchestrated_removals` uses window overlap as the "other plan" ownership signal. It only spares accounts absent from the new plan.

**Wrong outcome**
- Tonight's coverage kick disappears.
- The only signal is `replaces_orchestrated=1` in the dim diff line, or a field in the MCP JSON.
- Reservation advisories for tonight also vanish.

**Minimal fix**
- In `build_pending_kick_diff`, when an existing orchestrated pending's work window does not overlap the new plan's window, classify it as a new conflict kind, `conflict_other_plan`, and refuse to apply. An explicit `--replace-other-plans` flag could override this.
- Longer term, see design item D3.

**Regression test**
- Apply plan X for day D. Build plan Y for day D+1 using the same account.
- Assert that `diff.has_conflicts()` is true, or that a dedicated field is set.
- Assert that after a refused apply, the day-D pending is unchanged.

### 3. P2: False "plan may be compromised" advisory for natural-reset-reuse kicks

**Confidence:** Medium-high. It requires `session_resets_at` to carry a fractional or drifting value.

**Where**
- `tokenkick/reservation_advisories.py` 180–190, the strict `observed_reset > kick_at` comparison.
- `orchestration.py` 1196–1215 and 1558–1563: `kick_at` is derived from the fractional `session_resets_at`.
- `scheduling.py` 168–169: `to_utc_iso` truncates sub-second values.

**Trigger**
1. Account A is ACTIVE with `session_used_percent > 0`.
2. The planner uses A's active segment up to its reset, then plans `natural_reset_reuse` with `kick_at = reset_at`.
3. The stored `kick_at` is truncated to the second, while `session_resets_at` is, for example, `…:00.4`. Any +1 s provider jitter between polls has the same effect.

**Wrong outcome**
- `observed_reset > kick_at` holds, so the state becomes `RISK_PLAN_MAY_BE_COMPROMISED`.
- The daemon notifies: "may already be in use during a reserved orchestration window. Consider `tk plan cancel`".
- This fires while the user is following the plan exactly. It can push users to cancel valid plans.

**Minimal fix**
- Require `observed_reset > kick_at + tolerance`, for example 120 s.
- Optionally, round `kick_at` up (ceil) when deriving it from a reset timestamp, so a kick is never scheduled before the reset.

**Regression test**
- `_reservation_risk_state(status(ACTIVE, used=30, session_resets_at=K+0.4), quiet_start=…, kick_at=K, …)` should not return compromised.
- Then run an end-to-end case: plan, apply, and advisories built with the unchanged status. Assert no actionable advisory.

### 4. P2: Plan tables and daemon advisory notifications use the process timezone, not the plan or schedule timezone, and give no zone label

**Confidence:** High.

**Where**
- `cli.py` 8121–8157 (`_format_plan_time_range`, `_format_plan_day_offset`, `_format_plan_timestamp`) and 7986–7997.
- `orchestration.py` 444 and 1543–1544. Note that `_format_reset_boundary_time` output is embedded in segment notes, so it also feeds the plan fingerprint.
- `reservation_advisories.py` 310–311.

**Trigger**
- The daemon runs as a separate Linux user on a server, which is typically UTC.
- The schedule timezone or `--timezone` is Europe/Berlin.

**Wrong outcome**
- The notification says "Avoid using it from 12:00 to 14:00" when the reserved window is 14:00–16:00 Berlin time, with no zone shown.
- `tk plan --timezone X` renders segment clocks in the host timezone.
- The plan fingerprint can differ between processes with different `TZ` settings.

**Minimal fix**
- Thread an explicit `tzinfo` through these formatters: `plan.timezone` for plans, and `local_timezone(config.schedule)` for advisories and notifications.
- Include `%Z` in every notification and unlabeled clock.
- Remove timezone-dependent text from the fingerprinted payload.

**Regression test**
- Set `TZ=UTC` and call `time.tzset()`, with the schedule timezone set to `Europe/Berlin`.
- Assert that `format_reservation_advisory_message` shows Berlin clock times with `CET`/`CEST`.
- Assert that the plan fingerprint is identical under `TZ=UTC` and `TZ=America/New_York`.

### 5. P3: A graceful daemon stop during a stagger send produces a permanent "unconfirmed" block

**Confidence:** High on the mechanism.

**Where**
- `cli.py` 14779–14782: the SIGTERM handler throws `KeyboardInterrupt` at any bytecode.
- `stagger.py` 437–446: `attempting` is persisted, then `send` runs.

**Trigger**
- `tk daemon --restart`, `tk update --yes`, or a systemd stop arrives while `send()` is in flight.

**Wrong outcome**
- `attempt.outcome` stays `"attempting"`, and `tick` blocks with "Previous request unconfirmed".
- Recovery requires all of the following:
  - at least 5 h after the latest possible attempt;
  - an explicit `recover`;
  - the stagger is left paused;
  - `resume` with a renewed risk acknowledgement.
- A routine upgrade therefore costs at least one full cycle plus manual work. Failing closed is correct, but the ambiguity is self-inflicted.

**Minimal fix**
- Make the SIGTERM handler set a stop flag.
- Raise only at safe points: the poll boundary and `wait_between_polls`.
- Or defer the signal inside a `critical_send()` context that ends after the outcome is persisted.
- Ensure `DAEMON_STOP_TIMEOUT_SECONDS` exceeds the kick timeout.

**Regression test**
- Install the daemon handler. Inside a fake `send`, invoke the handler and then return a success event.
- Assert that the outcome is `"confirmed"` and that the loop exits afterwards.

### 6. P3: `disable` cannot clear stray `stagger_group_id` marks, permanently locking out the account

**Confidence:** Medium. It depends on whether an Antigravity account key can drift while its config fields are preserved, for example through migration or a merge during re-setup.

**Where**
- `stagger.py` 223–227: only accounts whose current key is in `group["members"]` are updated.
- `stagger.py` 166–172 and 175–178.
- `owns()` at 96–97 returns True for any account with `stagger_group_id`.

**Trigger**
1. A member's `account_key_string` changes while `stagger_group_id` is preserved.
2. `members()` now fails, so the stagger is blocked.
3. The user runs `tk stagger preview/apply --action disable`.

**Wrong outcome**
- `keys` does not contain the new key, so the mark survives.
- `owns()` remains True, independent auto-kick stays blocked, and the planner keeps reporting `stagger_managed`.
- No CLI path clears it. Repeating `disable` behaves the same, so a manual edit of `config.json` is required.

**Minimal fix**
- On `disable`, also clear `stagger_group_id` and force `auto_kick=False` and `session_auto_kick=False` on every Antigravity account carrying the group id.
- Show these accounts in the preview `changes` list.

**Regression test**
- Activate the stagger, then mutate a key-bearing field of `accounts[0]` while keeping `stagger_group_id`. Run disable.
- Assert that no account has `stagger_group_id`, that `owns()` is False for all accounts, and that `auto_kick` is False.

### 7. P3: `tk stagger … --json-output` breaks the app/MCP JSON contract on any non-`StaggerError` failure

**Confidence:** Medium-high. The handling inside `mcp_core._run` was not supplied.

**Where:** `stagger_commands.py` 37–55 and 64–80. Only `StaggerError` is caught.

**Triggers**
- `Config.save()` raises `OSError`. `test_interrupted_permission_save` shows this escapes `stagger.apply`.
- A lock timeout while the daemon tick holds the stagger lock during a send or verification.
- `PermissionError` when the CLI user differs from the owner of the daemon's state directory.

**Wrong outcome**
- A traceback is printed and no app envelope is emitted.
- The MCP apply layer receives non-JSON output, and the agent cannot tell whether the policy is paused or unchanged.

**Minimal fix**
- Add an `except Exception` branch that emits `emit_app_error("stagger_apply_failed", …)`.
- Include the persisted `stagger.status()` in the payload, which is the paused owner after a partial apply.

**Regression test**
- Monkeypatch `Config.save` to raise `OSError`, then run `stagger apply … --json-output`.
- Assert that the output parses as an error envelope and that `payload.mode == "paused"`.

### 8. P3: Calendar "optimal kick" advice is wrong for resets after the day's work window and for overnight windows

**Confidence:** High on the mechanism. The impact is low because the output is advisory only.

**Where**
- `reset_calendar.py` 334–376 and 379–391.

**Triggers**
- A weekly reset predicted at 23:00 with a 10:00–18:00 window.
  - `quota_available > work_end`, so `kick_at = predicted` and `immediate_kick_best=True`.
  - The calendar and ICS say "Immediate kick at reset is best", even though no work remains that day.
- A reset at 01:00 with a 21:00–02:00 window.
  - The function picks the same calendar date's window, which starts at 21:00 the next evening.
  - It ignores the "yesterday" logic in `resolve_today_work_window`.

**Fix**
- Use the same yesterday/today resolution as scheduling.
- If the reset is at or after `work_end`, roll over to the next applicable window, or omit the annotation.

**Regression test**
- Saturday 23:00 reset with a 10:00–18:00 weekend window: expect no `immediate_kick_best`, or a next-day kick.
- 01:00 reset with a 21:00–02:00 window: expect the previous-night window.

---

## B. Hypotheses: need code outside this packet

- **H1, retry backoff reset (scheduling).** `upsert_pending_kick` (`scheduling.py` 677–698) preserves `attempt_count`, `next_retry_at` and `gave_up_at` only when `kick_at` is identical. `compute_schedule_decision` returns `now_utc` in three branches (lines 454, 479 and 483–484), so `kick_at` drifts on each poll.
  - If `_schedule_or_kick_target` (`cli.py` 3993+) upserts after a failed due kick, the 5/15/45-minute backoff and give-up never take effect.
  - **Check:** do a fail, then recompute, then upsert, then assert `attempt_count` persists.
- **H2, one-shot tool bypasses stagger ownership.** `antigravity_stagger.run_once` (68–101) calls `_kick_and_notify` without `automatic=True`. `send_guard` (343–348) returns `None` for non-automatic calls.
  - For a paused stagger member with `weekly_auto_kick=False`, `prepare` passes, and the tool can send outside the durable attempt record. This includes the window where an `"attempting"` guard awaits recovery.
  - **Fix:** refuse when `stagger.owns(account)` for either family.
- **H3, lock ordering (stagger vs config).** `tick` takes the stagger lock, then config and pending locks inside `send`. `apply` takes the stagger lock, then config. If a manual `_kick_and_notify` takes `permission_lock` (config) before `execution_lock` (stagger), it can deadlock or time out against `tick`.
  - Both `tick` and plan apply also rely on `state_file_lock` being re-entrant. `orchestration.apply_orchestration_plan` re-enters it through `load_pending_kicks` and `save_pending_kicks`. `state_io` was not supplied.
- **H4, post-reset ambiguity treated as hard "blocked".** Provider-dependent. If a native read at the exact reset still reports the old window, `observation()` raises "Session anchor is unverified".
  - The stagger becomes "blocked" with `next_at=None` and is re-evaluated only after the poll interval. That delays the slot by up to 5 minutes, re-bases the phase for both families, and sends a blocked/aligned notification pair.

---

## C. Design and UX improvements (top 5)

- **D1: Separate transient from terminal blockers in the stagger.**
  - Today any `observation()` failure on one family blocks both, in the dict comprehension at 422. Read errors set `next_at=None`, so retries wait a full poll.
  - Proposal: keep the policy "never generate on bad evidence", but:
    - classify per-family read failures as `degraded` for that family;
    - schedule a short retry (60–120 s) near slot time;
    - notify only after N consecutive failures;
    - keep durable-attempt ambiguity as the only hard block.
- **D2: Make conflict semantics explicit.** `conflicts()` (141–144) treats all of the following as blocking:
  - the global *default* smart schedule;
  - gave-up pending kicks;
  - expired pending kicks.

  Enabling `tk schedule set --default` for Codex or Claude, which the daemon actively advertises, silently blocks the AG stagger with "Cancel conflicting schedules first". Proposal:
  - smart scheduling and the planner exclude stagger-owned accounts;
  - `conflicts()` ignores gave-up and past pendings;
  - errors name the exact remediation command, e.g. `tk schedule disable --account X`.
- **D3: Orchestration plan identity.**
  - Add `plan_id` and the work window to `PendingKick`, and allow multiple orchestrated entries per account. This fixes defect 2.
  - Compute `fingerprint()` over decision-relevant data only (planned kicks, diff, config revision), not raw `weekly_headroom_percent` or `available_at` floats. Daemon refreshes every 5 minutes currently make "plan changed since preview" likely within the 15-minute apply window.
- **D4: No slow I/O under control locks.** These operations hold control locks during slow or network I/O:
  - `_recover_guard` runs `read_pair` (≥ 6 s plus four native reads) while holding the stagger lock *and* the config lock;
  - `tick` verification holds the stagger lock;
  - `daemon_step` notifies while holding the stagger lock.

  Use claim-then-release with revisioned compare-and-set, and combine this with the SIGTERM-safe critical section from defect 5. Record a recovered attempt as `outcome="recovered"`, not `"confirmed"`, so the audit trail does not claim confirmation that never happened.
- **D5: Operability for split-user deployments and broken state.**
  - Status should report the effective `CONFIG_DIR` and owner. Today a CLI or MCP running as the operator user edits its own `stagger.json`, and reports `daemon_running: false`, without explaining why.
  - Add an explicit, previewed repair for a corrupt `stagger.json`. Currently preview and apply both fail in `load()`, so all Antigravity automation stays blocked until the file is deleted by hand.
  - Preview `enable` should reject hidden or non-kickable members up front instead of enabling into an immediate "blocked".

---

## D. Coverage matrix

| Subsystem | Material read | Depth | Main residual risk |
|---|---|---|---|
| `stagger.py` (policy, `tick`, `decide`, guards, recover) | Full | Deep trace, including concurrency and drift | `antigravity_windows`, `antigravity_automation`, `action_state` not supplied |
| `stagger_commands.py` | Full | Full | MCP `_run` error handling unseen |
| `antigravity_stagger.py` (one-shot) | Full | Full | `_kick_and_notify` and `_kick_eligibility` bodies unseen (H2) |
| `orchestration.py` planner and apply | Full | Search-pruning soundness checked; diff and apply traced | Scoring heuristics judged for logic only, not optimality |
| `scheduling.py` | Full | Full | Caller `_schedule_or_kick_target` unseen (H1) |
| `reservation_advisories.py` | Full | Full | — |
| `reset_calendar.py` | Full | Full | Gemini and Pacific reset semantics not verified (provider assumption) |
| `cli.py` plan, cancel, run, daemon, update | Supplied ranges | Traced | `_execute_due_pending_kicks`, `_kick_and_notify`, `_process_lock`, `_wait_for_owned_start` unseen |
| `state_io` locks and re-entrancy | Not supplied | Inferred | H3 |
| Tests | `test_stagger.py` full; others inventory only | Gap analysis | No tests cover: member-removed notification; `_daemon_poll_once` isolation; fractional reset advisories; TZ ≠ schedule TZ; cross-day plan replacement; SIGTERM mid-send; non-zero provider latency |

**Uncertainty:** These findings rest on static reading of the supplied snapshot and on reported, not observed, test results.

- Defect 1 follows directly from the code shown and depends on nothing unseen.
- Defects 3 and 6 depend on data shapes I could not observe: fractional reset timestamps, and which fields form an account key.
- No real provider requests were made, and nothing is claimed about actual Antigravity, Codex or Claude reset semantics.