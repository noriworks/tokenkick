# TokenKick review 01: state and config subsystem

**Scope.** This review covers:
- `tokenkick/consent.py`, `action_state.py`, `state_io.py`, `models.py` and `migrations.py`, all in full.
- `tokenkick/cli.py` lines 1–1750 only.
- `test_models.py` in full.
- `test_consent.py` and `test_state_io.py` as name inventories only, with no current diff.

**Not reviewed.** These are referenced but were not supplied, so findings that depend on them are marked as hypotheses:
- `scheduling.save_pending_kicks` / `remove_pending_kick`
- `_kick_eligibility`
- the callers of `account_action`
- the click command wrappers (for example `auto_session_enable` at 9351)
- `stagger.py`, the daemon loop, `status_cache.py`, MCP, Swift

All line numbers refer to the original source.

---

## A. Confirmed code defects (prioritized)

### 1. P2: Config backups are written without restricted permissions and can expose the Telegram bot token and ntfy topic
**Confidence:** high on the mechanism. Actual exposure depends on the home directory's permissions.

**Where**
- `migrations.py` 349–353 (`_write_config_backup`)
- `migrations.py` 853–857 (`_write_label_format_config_backup`)
- `models.py` 929 (`CONFIG_DIR.mkdir` with no mode)

**Trigger**
1. A migration runs, for example the label-format migration or Codex home repair.
2. The backup is written with `path.write_text(CONFIG_FILE.read_text())`.
3. The file gets mode `0666 & ~umask`, typically 0644. `~/.tokenkick` itself is created 0755.
4. The live `config.json` is written through `NamedTemporaryFile` and ends up 0600. The backups are the exception.

**Wrong outcome**
- Any local user who can traverse the home directory can read `notifications.telegram_bot_token`, `ntfy_topic` and identity emails. This matters most on the shared Linux server where the daemon runs as a separate user.
- Backups are written once (`if path.exists(): return`). Later repairs print "Backup: …" but point to an older snapshot, and a token rotated since then stays on disk.

**Fix**
- Write backups through `atomic_write_text`, which yields 0600, or `os.open(..., 0o600)`.
- Create `CONFIG_DIR` with mode 0700 and tighten it if it already exists.
- Use timestamped backups so the printed path matches what was actually saved.

**Regression test**
- Set `os.umask(0o022)`, write a config containing a bot token, call `_write_config_backup(tmp/"b")` and `_write_label_format_config_backup()`.
- Assert `stat.S_IMODE(mode) == 0o600`.
- Assert that a second repair produces a new backup containing the current content.

### 2. P2: The v0.4 direct-source migration resets user-owned account settings and orphans pending kicks
**Confidence:** high for the code path; medium for how often it is reached.

**Where**
- `migrations.py` 131–144 (`replace(direct_account, …)`)
- `migrations.py` 157–163 (only the status cache is re-keyed)

**Trigger.** The migration matches a `codexbar-cli` account to a direct account. It runs in two cases:
- on the recheck path (`recheck_skipped=True`), for any current CodexBar-sourced account, including accounts converted back by `_codexbar_account_from_direct`;
- for any config that lacks the `v0.4-direct-sources-v2` flag.

**Wrong outcome.** The migration starts from the discovered account and copies back only nine fields. These user-owned fields silently return to defaults:
- `notifications_enabled`, `notification_backends`: an opted-out account starts notifying again.
- `orchestration_role`: an account set to `excluded` becomes `normal`, and the planner starts scheduling kicks for it.
- `weekly_reserve_threshold_percent`, `usable_session_minutes`, `plan_tier`, `codex_session_limit_override`, `stagger_group_id`, and all `codex_surface_*` overrides.

In addition, `old_keys` are pruned from the status cache only. Pending kicks stay under the old account key. Compare `_migrate_codex_home_keys_if_needed`, which re-keys them.

**Root cause.** The migration predates the field ownership classification (`ACCOUNT_*_FIELDS`, models.py 351–407) and `merge_discovered_account`, and was never switched over to them.

**Fix**
- Build the migrated account with `merge_discovered_account(account, direct_account, preserve_label=True)`.
- Call `_migrate_pending_keys({old: new}, …)` alongside the cache prune.

**Regression test**
- Start from a legacy `codexbar-cli` Codex account with non-default values in every user-owned field.
- Stub `_discover_direct_accounts` to return a readable match.
- Assert every field in `ACCOUNT_USER_OWNED_FIELDS ∪ FALLBACK` survives, reusing the classification fixture from `test_models.py`.
- Assert that a pending kick under the old key is re-keyed.

### 3. P2: Codex identity-drift repair repoints `provider_home` when the identity is merely unreadable, and never verifies the target home
**Confidence:** medium-high on the code; impact is a hypothesis. This path is macOS-only because it reads CodexBar's file under `~/Library`.

**Where**
- `migrations.py` 479–498
- `migrations.py` 551–555 (`_codex_configured_home_identity_current` returns `False` when the identity is `None`)

**Trigger**
1. The configured home's `auth.json` is briefly missing or unreadable, for example after `codex logout` or while the Codex CLI is rewriting it.
2. CodexBar's `managed-codex-accounts.json` lists the same email with a different `managedHomePath`.
3. The repair permanently switches `provider_home` and `session_path` to the managed home.
4. It copies identity values from CodexBar's metadata without calling `read_codex_identity(managed_home)`.

**Wrong outcome**
- Kicks and status reads now use whatever credentials sit in the managed home. If CodexBar's metadata is stale, those may belong to a different login, while results are still attributed to the original label.
- The account key changes, so the status cache is pruned.
- Because the repair is not gated by a migration key, it can recur on any later run.

**Root cause.** The safer predicate already exists but is unused: `_codex_configured_home_identity_not_mismatched` (558–562) treats an unreadable identity as "not drifted".

**Fix**
- Repair only when there is positive mismatch evidence: the actual identity was read and differs.
- Read the managed home's identity and require it to equal the configured identity.
- Otherwise, report the problem as a doctor finding and leave the config alone.

**Regression tests**
- Configured home has no `auth.json`, and the managed JSON maps the email to home B: assert no change.
- Home B's `auth.json` has a different provider ID: assert no change.
- Only a verified mismatch with a verified match in B produces a repair.

### 4. P2: Pending-kick read-modify-write is not atomic and can lose updates or resurrect executed or cancelled kicks
**Confidence:** medium. The internals of `save_pending_kicks` were not supplied; its name and the existence of `_save_pending_kicks_unlocked` suggest it locks only the write.

**Where**
- `cli.py` 1307–1324
- `migrations.py` 650–674, 917–952, 1005–1045

**Trigger**
1. The user runs `tk accounts set-usable A 120`. `_invalidate_orchestrated_pending_for_account` loads the full pending map at 1308.
2. In parallel, the daemon executes and removes B's due orchestrated kick, or records a failure or backoff.
3. The CLI then saves its stale copy at 1316, which still contains B's entry.

The migrations follow the same pattern and run in whatever process starts first after an upgrade, possibly while the old daemon is still running.

**Wrong outcome**
- B's entry comes back, or the daemon's retry and backoff bookkeeping is overwritten.
- If history-based deduplication does not catch it, B is kicked a second time.

**Fix**
- Run the load, mutation and save inside a single lock. Use `state_transaction` around `_load_pending_kicks_unlocked` and `_save_pending_kicks_unlocked`, or the existing `remove_pending_kick` with a "reason == orchestrated" guard.
- Route all migration re-keying through one locked helper.

**Regression test**
- Monkeypatch `load_pending_kicks` so that, after returning its snapshot, it removes B from disk to simulate the daemon.
- Run `_set_usable_session_minutes`.
- Assert B is absent from disk.
- `test_pending_kicks_upsert_from_multiple_processes` covers upsert only, not this path.

### 5. P2: The action lease has no terminal "failed before dispatch" state and no expiry
**Confidence:** high on the mechanism; medium on impact, because the callers were not supplied.

**Where.** `action_state.py` 16–36.

**Trigger**
- Any exception raised inside `with account_action(...)` leaves the record `in_flight`. That includes validation errors, `ModelSelectionError`, `StateFileError`, consent refusal, `KeyboardInterrupt`, and a SIGTERM during `tk update` or a daemon restart.
- The generator has no try/finally, and the "completed" write at 35–36 never runs.

**Wrong outcome**
- Every later action for that key raises `UncertainActionError` until someone passes `--force`, even when no request ever left the machine.
- If the daemon never passes `recover=True`, auto-kicks for that account stop for good after one crash. There is no age-based expiry.
- When two processes contend, the loser gets `StateFileLockTimeout` (a `TimeoutError`) rather than `UncertainActionError`. Callers likely don't map that type, so in app or MCP mode it may surface as `ERROR_INTERNAL`.

**Fix**
- Yield a lease object with `mark_dispatched()`.
- In `except`, write `aborted` if not dispatched, or `uncertain` plus error class and time if dispatched.
- Convert lock contention into a typed "busy" error.
- Let the daemon auto-recover once a fresh provider status observed after `started_at` exists.

**Regression tests**
- `with pytest.raises(RuntimeError): with account_action(d, "k"): raise RuntimeError` followed by a clean second use: should not raise.
- The same body after `lease.mark_dispatched()`: should raise `UncertainActionError`.
- Contention from a second process: should produce the typed "busy" error.

### 6. P2: Config save strips unknown fields and rejects newer enum values, which breaks mixed-version installs
**Confidence:** high on the mechanism.

**Where**
- `models.py` 292–293 (`from_dict` drops unknown keys)
- `models.py` 937–944 (`current` is re-parsed through `_from_payload(...)._data()` before the merge)
- strict validators at 1217–1240, 116–137 and 210–211

**Trigger.** Two CLI versions share `~/.tokenkick`: the CLI bundled in the Swift app and a pipx `tk`, or a server daemon not yet upgraded. The newer one writes a field the older one doesn't know. For example, `stagger_group_id` from this unpublished batch, against a released 1.22.0 binary.

**Wrong outcome**
- Any save by the older binary, even toggling visibility, normalizes `current` and writes it back without those fields.
- New settings, unknown `auto_kick_consents` providers and future top-level keys disappear without warning.
- A newer orchestration role or Codex surface name makes the older binary fail to load the config entirely.
- The existing test `test_unknown_fields_are_ignored_for_forward_compatibility` checks only the load side.

**Fix**
- Keep unknown keys in a raw `_extra` dict, at top level and per account, and emit them again on save.
- Run the three-way merge on raw JSON, not on the normalized form.
- Add `schema_version`, and refuse to write a newer schema with a clear error instead of silently stripping it.

**Regression test**
- Write config JSON with `"future_top": {...}` and `accounts[0].future_field`.
- Load it, call `_save_config_like(config, accounts=...)`, and assert both keys are still on disk.
- Write a config with `orchestration_role: "future"`: assert it loads with a warning rather than failing hard.

### 7. P3: `auto session|weekly enable` reports success but is a no-op while the master flag is off
**Confidence:** medium. The command wrapper at 9351 might also set `auto_kick`; it was not supplied.

**Where**
- `cli.py` 1439 and 1479 (only the sub-flag is set)
- `cli.py` 1058–1063 (the `account.auto_kick` filter excludes the account entirely)
- `cli.py` 1636–1637 and 1660–1661 (redundant master checks, unreachable)

**Trigger**
- The account has `auto_kick=False`. The user runs session-enable and accepts the risk prompt.
- The CLI prints "Session auto-kick enabled", but `_auto_kick_kickable_accounts` drops the account, so it is never targeted or even logged by `target_scan`.

The reverse also happens: `_set_auto_kick(True)` (1394–1399) overwrites a deliberate `weekly_auto_kick=False`.

**Fix**
- Either have sub-flag enable also set the master flag, or refuse with "enable auto-kick first".
- Use one `effective_auto_kick(account, window)` predicate in the setters, display and target selection.

**Regression test**
- Account with all flags off. Call `_set_session_auto_kick(..., consent_token="ENABLE")`, then `_kickable_window_targets` with a session-eligible status.
- Assert the account is targeted, or that the setter refused.

### 8. P3: Schema validation gaps let malformed config crash outside `StateFileError`
**Confidence:** high.

**Where**
- `models.py` 1086: `data.get("migrations", {}).items()`
- `models.py` 797 and 805: `.items()` on `schedule.accounts` and `usable_session_tier_defaults`
- `models.py` 1046: `poll_interval_minutes` is not validated
- `models.py` 1080, 623–624 and 721: `bool("false")` evaluates to True

**Trigger**
- `"migrations": null` or `"schedule": {"accounts": null}` raises `AttributeError`. Only `TypeError` and `ValueError` are caught at 1093, so the CLI shows a traceback and app/MCP mode reports `ERROR_INTERNAL` instead of `ERROR_STATE_FILE`.
- `"poll_interval_minutes": 0`, `"5"` or `null` loads successfully and fails later inside the daemon.
- `"telegram_remote_enabled": "false"` is read as enabled.

**Fix**
- Type-check containers and also catch `AttributeError`.
- Coerce and range-check the poll interval (int, ≥1).
- Use a strict boolean parser.

**Regression test.** Parametrize these payloads and assert `StateFileError` with path and detail.

---

## B. Hypotheses and lower-confidence observations (verify before acting)

1. **Consent is enforced only when auto-kick is enabled, not when it runs.**
   - `_auto_kick_kickable_accounts` (1058) never checks `config.has_auto_kick_consent`.
   - Configs from before the consent feature, hand edits, and migration-carried `auto_kick` (migrations 136–138) would all keep kicking without a recorded consent.
   - Verify `_kick_eligibility` and the upgrade path. There is also no way to revoke consent.
2. **Kick history is keyed by label.**
   - `KickEvent` (636–689) has no account key, and `_codex_pending_confirmation_has_anchor` filters on `event.label` (1014).
   - Label renames detach the account from its history: provider-first migration, `--rename-label`, and the Antigravity split, which keeps the legacy label for Gemini (1006–1018).
   - A reused label inherits another account's history. If `_was_kicked_in_current_window` filters by label, this can cause duplicate or missed kicks.
3. **Setters persist whatever account list the caller passes in** (1132–1597).
   - The account-list merge (1157–1162) drops any key missing from `proposed`.
   - If a caller passes visible-only or discovery-augmented lists (for example via `_load_visible_accounts`), hidden accounts are deleted or unsaved accounts are persisted.
4. **`_set_direct_usage` mutates the caller's config** (1542–1543), unlike the other setters.
   - Reusing a stale `accounts` list in a loop then looks to the merge like an intentional revert of the previous save.
5. **A Codex pending-confirmation follow-up is dropped even when delivery fails** (982–984).
   - `acknowledged` is ignored, and `sent` is incremented anyway.
6. **One notification backend failing can block the rest.**
   - `_send_account_notifications` has no per-backend exception isolation (692), whereas the test-notification path has it (665–669).
   - An ntfy exception could skip Telegram and abort the rest of the daemon step.
7. **`_weekly_quota_window_active` prefers the snapshot `resets_in_seconds` over the absolute `resets_at`** (538–543). `resets_in_human` uses the opposite order (500–502).
   - A cached status that was not recomputed could block weekly kicks after the reset has passed.
8. **The Antigravity split stamps the CLI's current identity onto legacy entries** (999).
   - Two legacy entries can then collapse to the same keys, and the second is dropped silently (1004–1005).
   - Children also have auto-kick reset to off without any notice (1001–1003).
9. **Migration flags are committed even when re-keying pending kicks failed** (`_migrate_codex_home_keys_if_needed` at 402–407 with 677–684). The result is permanently orphaned pending entries.
10. **Accounts with duplicate keys collapse during a concurrent merge** (`keyed()` at 1158–1159), and one of them is lost.
11. **Nested `account_action` for the same key in the same thread** passes the re-entrant lock but then reads its own `in_flight` record and raises. This would bite if the surface/burst ladder wraps each attempt in a lease.
12. **History becomes a bottleneck and a single point of failure as it grows.**
   - `append_kick_event` rewrites the entire, unbounded history file on every append (1302–1312).
   - One malformed line makes `load_kick_history` raise, which stops daemon target scans. This fails closed, which is good for deduplication, but there is no quarantine path like the one pending kicks have.

---

## C. Design improvements (top 5)

1. **One locked transaction API for every JSON state store.** Pending kicks, Codex confirmations, the status cache and action leases should all go through `state_transaction(path, mutate)`. Add a lint or test that forbids the bare load-then-save pattern.
2. **Stable account identity everywhere.** Give each account an immutable `account_id`, stored in config. Write it into `KickEvent`, `schedule.accounts`, stagger membership and lease keys, so a rename only changes metadata.
3. **Versioned, raw-preserving config.** Add `schema_version`, round-trip unknown keys, and centralize typed validation. Enforce 0700 on the directory and 0600 on every file including backups, and keep a migration journal with timestamped backups.
4. **A lease state machine shared by CLI, MCP and daemon.** States: `preparing → dispatched → completed | failed | uncertain`. Record owner PID, host and boot ID. Surface uncertain leases, with their age, in `doctor`, and provide a recovery command.
5. **One flag model with consent built in.** Replace the `auto_kick` / `weekly` / `session` triad with `enabled` plus `windows: {session, weekly}`. Evaluate it through a single predicate that also checks consent. Store consent with timestamp and text hash, and make it revocable.

---

## D. Coverage matrix

| Subsystem | Material seen | Depth | Main residual uncertainty |
|---|---|---|---|
| consent.py | full | full | runtime enforcement sites |
| action_state.py | full | full | callers, `recover` usage, daemon handling |
| state_io.py | full | full | cross-user lock-file permissions |
| models.py (config, account, history) | full | full | the other readers of `weekly_quota_exhausted` |
| migrations.py | full | full | which entry points pass `recheck_skipped` |
| cli.py 1–1750 (notifications, setters, targets) | partial | full for the supplied range | wrappers, `_kick_eligibility`, daemon loop |
| scheduling / pending store | signatures only | none | semantics of `save_pending_kicks` locking |
| stagger, MCP, Swift, packaging | not in packet | none | covered by other reviews |
| Tests | `test_models` full; consent and state_io inventories only | partial | `test_cli` not seen |

**Limits.** I ran no code and made no provider requests. The 1831 Python and 203 Swift tests were not re-executed. Findings 4 and 5 and all of section B depend on code outside this packet and should be checked against the scheduling, daemon and command-wrapper reviews.