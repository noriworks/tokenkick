# TokenKick Subsystem Review 06: MCP and Notifications

**Scope.** This review covers `mcp_server.py`, `mcp_core.py`, `mcp_setup.py`, `notifier.py`, `telegram_remote.py`, `app_mode.py`, `app_commands.py` and `doctor.py`. It also covers the `cli.py` slices supplied for notify, remote telegram, codex-surfaces, setup and antigravity probe.

**Method.** I did static reading only and ran nothing. Repository text was treated as data.

**Evidence labels.** Each item is marked as one of:
- **Confirmed**: the code path is fully visible in this packet.
- **Likely**: strong indirect evidence.
- **Hypothesis**: depends on code or provider behaviour not in this packet.

---

## A. Defects (8, prioritized)

### 1. [P2 · Confirmed mechanism, high confidence] MCP timeouts SIGKILL `tk` mid-operation, orphan provider children and report a plain "timeout"

**Paths**
- `tokenkick/mcp_core.py` 36–40 (timeouts).
- `tokenkick/mcp_core.py` 1036–1075 (`_run`: `subprocess.run(..., timeout=...)` and the `TimeoutExpired` branch).
- `tokenkick/mcp_setup.py` 32 and 659–665 (`tool_timeout_sec = 180`).

**Trigger.** Any of these runs past its timeout:
- `tokenkick_kick_account`, `tokenkick_force_recovery_kick` or `tokenkick_run_apply` past 180 s. Plausible causes are a burst ladder with gaps, delayed Codex verification, or several accounts in `tk run`.
- `tokenkick_stagger_apply(recover)` past 180 s.

**Wrong outcome**
- `subprocess.run` handles `TimeoutExpired` by calling `process.kill()` on the `tk` PID only. That is SIGKILL, not a process-group kill.
- `tk` gets no chance to run `finally` or ownership cleanup. Any `codex exec`, `claude` or `agy` child it spawned keeps running and may finish generating without `tk` recording history.
- MCP returns `error.code="timeout"`. Nothing says the quota outcome is unknown, and the preview token is already consumed.
- The Codex client's tool timeout (180 s) equals `QUOTA_TIMEOUT_SECONDS` (180 s). The client can give up before the MCP returns. An agent that re-previews and re-applies can cause a duplicate kick.
- If `action_state.account_action` persists an in-flight marker, SIGKILL may leave it stale. That part is a hypothesis because `action_state.py` is not in this packet.

**Root cause.** A blocking `subprocess.run` with a hard kill, plus no notion of an "uncertain outcome" for quota-risk calls.

**Minimal fix**
1. Use `Popen(..., start_new_session=True)`.
2. On timeout, send `os.killpg(SIGTERM)`, wait a grace period, then SIGKILL.
3. For risks in {`quota_consuming`, `dangerous_*`}, return `error.code="outcome_unknown"` with `retry_safe: false` and guidance to read `tokenkick_history` before retrying.
4. Keep the subprocess timeout clearly below the client tool timeout (for example 150 vs 180), or raise `tool_timeout_sec`.

**Regression test.** Write a fake `tk` that starts `sleep 60 &`, records the child PID, then sleeps. Patch `QUOTA_TIMEOUT_SECONDS=1`. Assert:
- the child PID is gone after `_run` returns;
- `error.code == "outcome_unknown"`;
- `tool_timeout_sec` in `_codex_toml_block` is greater than the largest MCP subprocess timeout.

### 2. [P2 · Likely, medium-high confidence] `tokenkick_kick_preview` is labelled `cached_read` but performs a live provider read under a 20 s timeout

**Paths**
- `tokenkick/mcp_core.py` 720–732 (`risk="cached_read"`, default timeout).
- `tokenkick/mcp_server.py` 368–371 (docstring "Risk: cached_read").

**Evidence.** `test_app_mode.py::test_kick_json_dry_run_reports_would_kick` has to monkeypatch `tokenkick.cli._fetch_status` for `kick … --dry-run --json-output`. That means the dry-run path fetches live status.

**Trigger.** A Claude account whose `/usage` PTY probe, or a Codex app-server read, takes more than 20 s.

**Wrong outcome**
- The preview times out and `tk` is SIGKILLed mid-probe, which ties into Defect 1.
- A PTY `claude` child or the refresh lock may be orphaned.
- The agent was told the call was cache-only, with `provider_refresh: false`.

**Fix.** Choose one:
- Classify the preview as `live_provider_read` with `provider_refresh=True` and `LIVE_PROVIDER_TIMEOUT_SECONDS`.
- Or add a CLI `--cached` mode for dry-run and use it here.

**Test.** With a fake `tk`, assert that the kick preview reports `provider_refresh is True` and runs with timeout ≥ 90 s. Add a CLI-level test asserting that `kick --dry-run` either calls `_fetch_status` or honours `--cached`, so the label and the behaviour cannot drift apart.

### 3. [P2 · Confirmed] `tokenkick_run_apply` is not bound to what the dry run showed

**Path.** `tokenkick/mcp_core.py` 673–718.

**Trigger**
1. A dry run shows `kicked: [A]` and the user approves.
2. Within the 10-minute TTL, account B becomes due, or the daemon's state changes.
3. `run_apply` executes.

**Wrong outcome.** `tk run --json-output` kicks whatever is due at apply time, including B. The token binds only the argv `["run","--json-output"]`. In contrast, the plan path binds `expected_plan`/`preview_at` (488–490) and auto/stagger bind fingerprints (186–191, 238–241).

**Root cause.** `_attach_preview_token` passes `expected_plan` only for `tokenkick_plan_apply` (1024–1025). The `run` command takes no expected-target argument.

**Fix.**
1. Fingerprint the dry-run kick set: (account key, kick_type, purpose).
2. Pass it via a new `tk run --expected-targets FP`, or restrict apply to `--account` for each approved label.
3. The CLI recomputes the set and refuses on mismatch.

**Test.** A fake `tk` returns `kicked:[A]` for the dry run and `[A,B]` for the apply-time recompute. Assert that apply refuses with a "targets changed" error and never invokes the mutating command.

### 4. [P2 · Confirmed structure; example trigger medium confidence] A poison Telegram update crash-loops the remote listener, and `/refresh` replays live provider reads

**Paths**
- `tokenkick/telegram_remote.py` 584–590 (`handle_update` runs before `save_last_update_id`, with no exception containment).
- `tokenkick/telegram_remote.py` 386–389 (`round(value)`).
- `tokenkick/cli.py` 13716–13722 (only `TelegramRemoteConfigError` and `KeyboardInterrupt` are caught).
- `tokenkick/cli.py` 13552 ("daemon will restart it on the next poll").

**Trigger.** Any exception inside `handle_update`. Examples:
- The status JSON contains `NaN` or `Infinity`. Python's `json.loads` accepts them, and `round(nan)` raises `ValueError` while `round(inf)` raises `OverflowError`.
- A non-dict item in `result` causes `AttributeError` at `update.get`.

**Wrong outcome**
1. The listener exits with a traceback before the offset is acknowledged.
2. The supervisor restarts it.
3. Telegram redelivers the same update and the listener crashes again.
4. If the poison message is `/refresh`, every cycle runs `tk status --refresh`, which means repeated live provider reads.

**Fix**
- Wrap `handle_update` in `try/except Exception`, log it, and still save the offset. At-most-once delivery is the right semantics for a read-only status bot.
- Guard `_percent_text` with `math.isfinite` and exclude `bool`.

**Test.** Use `FakeClient` with one `/refresh` update. Have `refresh_runner` return `{"accounts":[{"label":"x","weekly_used_percent":NaN}]}`. Run two loop iterations, stopping with `StopLoop`. Assert the offset was saved, a reply (or error reply) was sent, and the runner was called exactly once.

### 5. [P3 · Confirmed] Ntfy topic exposed in full by doctor, and so through MCP and the app

**Paths**
- `tokenkick/doctor.py` 674–680 (`"ntfy_topic": notifications.ntfy_topic`).
- `tokenkick/doctor.py` 685–691 (the topic is also embedded in the check message).
- MCP `tokenkick_doctor` (`mcp_core.py` 311–331) applies only path redaction.

**Trigger.** A user with a public ntfy.sh topic calls `tokenkick_doctor`, either directly or via `check_tokenkick_health`.

**Wrong outcome**
- On public ntfy.sh the topic name is the only access control.
- It is copied into the LLM context and the provider transcript. Anyone holding it can subscribe to account and kick notifications, or spoof them.
- This is inconsistent with the same function masking `telegram_chat_id` (`_mask_last4`) and the bot token.

**Fix**
- Mask the topic (`***last4`) in `info` and in the message.
- Add `ntfy_topic` and `destination` to an MCP secret-key redaction list. That also covers `accounts notifications` and `snapshot.notifications.destination`.

**Test.** Seed `ntfy_topic="fixture-secret-topic"`. Assert the string is absent from `build_doctor_report().to_dict()` JSON and from the MCP doctor and snapshot results, using a fake `tk` that echoes it.

### 6. [P3 · Confirmed] Rotating Telegram credentials leaves the running remote listener authorized for the old chat and token

**Paths**
- `tokenkick/cli.py` 13193–13217 (the `--telegram` and `--telegram-remote` branches print no restart hint; only `--save-telegram`, 13180–13183, does).
- `tokenkick/cli.py` 13687–13714 (credentials are bound once at listener start).

**Trigger.** The user runs `tk notify --telegram-remote NEWTOKEN NEWCHAT` to revoke a leaked or shared chat while the listener is running.

**Wrong outcome**
- The listener keeps answering `/status` and `/refresh` from the old chat, exposing account labels, usage and errors.
- Nothing tells the user to restart, and the daemon supervisor only restarts on death.

**Fix.** Either:
- have the listener re-read and fingerprint the credentials each loop iteration and exit (letting the supervisor restart it) on change;
- or have `notify` restart the listener when its credentials change.

At minimum, print or emit the restart hint on all three paths.

**Test.** Start `TelegramRemoteListener` via a factory that re-reads `Config`. Change `telegram_chat_id` between two `get_updates` calls. Assert that an update from the old chat is rejected (`telegram_remote_unauthorized`).

### 7. [P3 · Confirmed] Telegram `/status` can hide every warning row

**Paths**
- `tokenkick/telegram_remote.py` 24 (`ACCOUNT_LIMIT=12`).
- `tokenkick/telegram_remote.py` 322–324 (render order Ready → Accounts → Warnings).
- `tokenkick/telegram_remote.py` 346–355 (shared remaining budget).

**Trigger.** 12 or more healthy accounts plus at least one stale or errored account.

**Wrong outcome.** The reply shows "⚠️ Warnings: 1" with zero rows. The actionable failure is the part that gets dropped.

**Fix.** Render Warnings first, or reserve slots for warnings before filling the other sections.

**Test.** 13 `kickable` rows plus one `{"stale":true,"label":"codex (x)"}` row. Assert `"codex · x"` appears in `format_status_reply` output.

### 8. [P3 · Confirmed] `tokenkick_recovery_hint` always returns `ok: true`, masking doctor failures

**Paths**
- `tokenkick/mcp_core.py` 802–824. `doctor.get("ok")` is never checked.
- Consumed at 753–778 by the force-recovery preview.

**Trigger.** Either:
- an unknown label (`tk doctor LABEL` exits 2, per `test_doctor_unknown_label_exits_two`);
- or a doctor timeout.

**Wrong outcome.** The result is `ok: true, hints: []`. The force-recovery preview then says "No stale-refresh recovery hint was found… Use the CLI directly if you intentionally need to override", which steers the agent toward a `--force` CLI kick instead of reporting "account not found".

**Fix.** If the doctor result is not ok, return it with its error, and mark the risk as `diagnostic_read`.

**Test.** A fake `tk` whose doctor exits 2 with an error. Assert `recovery_hint["ok"] is False` and that the force preview returns that error with no `no_token_reason`.

### Minor confirmed observations (below the bar for full write-up)

- **Pending-kicks resource hides errors.** `mcp_server.py` 440–444: `tokenkick://pending-kicks` returns `[]` when the snapshot fails or times out, which reads as "no pending kicks". Return the error envelope instead.
- **Unconfirmed quota-constrained kicks reported as kicked.** `notifier.py` 61–72: `notify_quota_constrained_kick` sends "Kicked … at …" for `success and not confirmed`. Compare `notify_scheduled_kick` at 53, which routes unconfirmed events to `notify_kick`.
- **Doctor reports Telegram OK without a token.** `doctor.py` 692–700: "telegram configured" is OK when `chat_id` is set even if `telegram_bot_token` is empty.
- **JSON stdout contamination.** `cli.py` 13451–13453: "did not acquire ownership" is printed even with `quiet=True`. Under `remote telegram --background --json-output` without `TK_APP_MODE`, this pollutes the JSON on stdout.
- **MCP config backups widen permissions.** `mcp_setup.py` 706–711: backups are written with default umask, not the source file's mode, and accumulate without limit. Codex and Claude Desktop configs may hold other servers' API keys in `env`. Copy `st_mode`, or use 0600.
- **`TK_APP_MODE=off` enables app mode.** `app_mode.py` 41–43. Doctor's boolean parsing treats `off` as false.

---

## B. Hypotheses (code needed to confirm is outside this packet)

1. **Daemon or remote launched via MCP inherits `TK_APP_MODE=1`, `TK_NO_INTERACTIVE` and the MCP client's environment** (`mcp_core.py` 1049–1053, `daemon --background`).
   - Claude Desktop launches servers with a minimal PATH, so a daemon started this way may not find `codex` or `claude`.
   - A supervised Telegram remote would then run `tk status --json-output` in app mode. If app mode envelopes status output, `format_status_reply` reads top-level `accounts` and finds none, replying "No accounts returned".
   - **Verify:** `_start_daemon_background` env handling, and status JSON shape under `TK_APP_MODE`.
2. **Auto fingerprint drift.** `_auto_target_fingerprint` hashes the whole `auto status` row. If that row ever includes volatile fields, apply always fails after a daemon poll. Byte-for-byte parity with the CLI's `_require_auto_preview_fingerprint` is also unverified.
3. **MCP argv vs the real CLI.** `test_mcp_core.py` uses `_fake_tk`. Whether these are accepted is not shown:
   - `auto weekly|session enable … --accept-risk ENABLE --expected-auto-fingerprint`;
   - `stagger apply --action … --expected … --accept-risk ENABLE`.
4. **Stagger `recover` risk class.** It runs with `provider_refresh=True` and a 180 s timeout but is classed `low_risk_mutation` with no `quota_ack`. If recover can clear a send guard and allow a re-send, it is quota-relevant.
5. **Claude Code MCP status and repair.**
   - `"tokenkick" in stdout` is a substring match (`mcp_setup.py` 332), so `tokenkick-dev` counts.
   - It cannot detect a stale command path.
   - Repair always runs `add-json` (396–400). If `claude mcp add-json` refuses existing names (a provider assumption, not verified), repair fails while status shows "configured".
6. **Schedule pending impact** (`mcp_core.py` 1298–1326).
   - With `default=True`, every pending kick is listed as removable, including accounts that have their own schedules.
   - Ownership is keyed on `item["reason"] == "orchestrated"`. The pending-kick payload may use `purpose`.
7. **Stagger notifications.** If `stagger.tick` calls `notify_stagger(msg, config.notifications)` directly, it notifies only the primary `backend`. That ignores `enabled_backends` fan-out and account routing.
8. **`tokenkick_status` labelled `cached_read`** may spawn the detached `refresh-cache` helper (`_start_background_status_refresh`) when the cache is stale, which means live probes.
9. **Symlink resolution.** `_resolve_tk_path` resolves symlinks. Under a Homebrew-style Cellar install, it pins a versioned path that breaks after upgrade.

---

## C. Design improvements (5)

1. **One preview/apply contract.**
   - Every mutating MCP tool passes a state fingerprint (`--expected-*`) that the CLI verifies. MCP stays thin, and the CLI is the single verifier.
   - Add an idempotency key (`--request-id`) that the CLI persists with `action_state`, so a retry after `outcome_unknown` is deduplicated rather than re-kicked.
   - Make `mcp_preview` metadata uniform across tools; stagger currently omits it.
2. **Real-CLI contract tests.** For every argv builder in `mcp_core`, parse the argv with `cli.make_context(...)` against the real Click tree. Add a fingerprint-parity test between `mcp_core` and the CLI. This closes the gap left by fake-`tk` tests.
3. **Consent is not human-verified over MCP.**
   - `confirm`, `quota_ack` and `accept_risk` are booleans the agent can set by itself, yet `auto enable --accept-risk ENABLE` records provider consent.
   - Use MCP elicitation, or require an out-of-band confirmation, for consent and quota operations. Record consent provenance (`source: mcp`, timestamp, disclosure hash).
   - Include host, user and `CONFIG_DIR` identity in stagger, daemon and auto previews. An MCP server on the Mac otherwise mutates Mac-local state while the docs talk about the "server daemon" running as another Linux user.
4. **Telegram remote hardening.**
   - Drop or coalesce backlog updates older than N minutes (`message.date`); after downtime, queued `/refresh` commands replay serially.
   - Add a `/refresh` cooldown that shares the Claude monitoring backoff.
   - In group chats, allowlist senders by `from.id` and pass `bot_username` from `getMe` (it is never passed, `cli.py` 13707–13714). Only parse bare-word commands in private chats.
5. **A single notification fan-out and secret model.**
   - One API that resolves route, policy and per-account backends, and returns per-backend results. All notify paths, including stagger and reset events, use it.
   - A shared secret classifier (ntfy topic, bot token, chat id, provider homes) used by doctor, snapshot, MCP redaction and logs.
   - Support a configurable ntfy server URL and validate the topic.

---

## D. Coverage matrix

| Subsystem | Read | Depth | Notes |
|---|---|---|---|
| `mcp_server.py` | full | high | Tools and resources traced to the core. |
| `mcp_core.py` | full | high | Token store, gating, `_run`, redaction. |
| `mcp_setup.py` | full | medium | TOML/JSON edit paths; Claude Code CLI semantics unverified. |
| `notifier.py` | full | high | Fan-out callers in `cli.py` not supplied. |
| `telegram_remote.py` | full | high | |
| `cli.py` notify / remote | slices | medium | Supervisor (`_supervise_telegram_remote`) not supplied. |
| `doctor.py` | full | medium | |
| `app_mode` / `app_commands` | full | medium | |
| `stagger*`, `action_state`, `state_io` | symbols only | none | Hypotheses B1, B4, B7 depend on these. |
| Swift app | not in packet | none | |
| Tests (mcp_core, telegram, notifier, doctor, mcp_setup) | inventories only | low | `test_app_mode.py` read in full. |

## E. Limits and residual uncertainty

- Unchanged test bodies were provided as inventories only, so some defects may already be partly covered.
- No real CLI, provider or Telegram behaviour was exercised.
- Provider semantics are not asserted: Claude Code `add-json` duplicate handling, Telegram privacy mode and ntfy.sh access model are all assumptions.
- Line numbers are from the supplied source.
- Defects 1 and 4 rest on Python stdlib behaviour (`subprocess.run` kill-on-timeout; `json.loads` accepting `NaN`), which I am confident in. Their real-world frequency depends on upstream code not shown here.