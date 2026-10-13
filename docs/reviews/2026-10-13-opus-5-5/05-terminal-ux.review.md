# TokenKick review 05: terminal UX (interactive TUI, status rendering, CLI presentation and exit contracts)

**Scope and evidence rules**
- I reviewed the supplied slices only:
  - `tokenkick/interactive.py` (full)
  - `tokenkick/status_rendering.py` (full)
  - `tokenkick/cli.py` 6074–11002, excluding 7539–8353
- Statements about Rich and Click behaviour come from library knowledge. I did not execute anything.
- Helpers referenced but not supplied are treated as unknown. Each finding states which conclusions depend on them.
- No provider semantics are assumed.

---

## A. Confirmed or high-likelihood defects (prioritized)

### 1. P2: TUI actions do not contain Click and other exceptions, so one bad input ends the whole session
- **Confidence:** High
- **Location:** `interactive.py:387-393` (`_invoke`), plus direct `cli_module` calls outside `_invoke` (e.g. `interactive.py:1567`, `3479-3491`)

**Trigger and wrong outcome**
1. In the TUI, open Configure → Codex surface strategy → Burst inter-surface gap.
2. Enter `-5` and confirm `Set burst ladder gap to -5s?`.
3. `codex_strategy_gap` raises `click.ClickException("Gap seconds must be >= 0.")` (`cli.py:9543-9547`).
4. `_invoke` only catches `SystemExit` and `click.exceptions.Exit`.
5. The exception unwinds through `_codex_settings_menu`, then `_dispatch_action`, then `run_command_center`.
6. Click's top-level handler prints `Error: …` and the process exits. The user loses the menu session.

Other escape routes:
- **Environment override, read outside `_invoke`.** A bad `CODEX_BURST_LADDER_ORDER` value makes `_codex_burst_ladder_surface_order` (read directly at 1567) raise `ClickException` through `_parse_codex_fire_all_surface_order` (6214-6232).
- **Stagger menu.** `_stagger_menu` catches only `StaggerError` (3490). Lock timeouts, `OSError` and `UncertainActionError` from the new stagger code are not caught. This part is a hypothesis because `stagger.py` was not supplied.
- **Rendering.** Rich `MarkupError` (finding 3) is also uncaught.

**Root cause:** `ctx.invoke` does not run Click's standalone error handling. The TUI wrapper assumes commands only ever exit via `Exit` or `SystemExit`.

**Minimal fix:**
- In `_invoke`:
  - Catch `click.ClickException` and call `exc.show()`.
  - Catch `click.Abort` and echo "Cancelled."
- In `run_command_center`, add a last-resort `except Exception`. It should print a one-line error plus a hint to run with `TK_DEBUG`, then continue the loop.
- Wrap the non-`_invoke` `cli_module.*` calls in the same guard.

**Regression test:** Use CliRunner on `tk menu`, with `_select`, `_text` and `_confirm` monkeypatched to script `configure → codex_settings → gap → "-5" → yes → back → back → exit`. Assert exit code 0 and that the output contains "Gap seconds must be >= 0." Add a second case with a bad environment order.

---

### 2. P2: `tk status` (a read path) sends push notifications, and dedupe can fail open
- **Confidence:** High for the mechanism. Medium for repeated fanout, because `notify_dormant_account_for_account` was not supplied.
- **Location:** `status_rendering.py:788-825` (called at 776 from every human `tk status` and the TUI Status view)

**Trigger and wrong outcome**
- **Unwritable state.** Suppose the dormant-hint state file cannot be written. Examples: a read-only or full disk, or an operator account that can read but not write the state dir of a daemon running as a separate Linux user. Then:
  - `_write_dormant_hint_state` raises `OSError`, which is silently swallowed (822-825).
  - The notification has already been sent (808), before persistence.
  - Every later `tk status` re-prints the hint and re-sends one push per FRESH-and-stale account.
- **Concurrent runs.** Two `tk status` invocations at the same time (TUI plus shell, or a `watch` loop) both read the empty state and both notify. The read-modify-write is unlocked.
- **Misclassification.** "Dormant" is defined as `state == FRESH and stale`. After a daemon outage the whole cache is stale. The first human `tk status` then fans out a "dormant" push for every ready account and recommends `tk wake`, which is quota-consuming, based on stale data.

**Root cause:** External notification side effects live in a renderer. The order is notify-then-persist, there is no lock, and write failure fails open.

**Minimal fix:**
- Persist the "hinted" marker under `state_file_lock` / `locked_update_text` before sending.
- If persistence fails, do not notify.
- Better: keep only the printed hint in `tk status` and move dormant notifications into the daemon, which is the single writer.
- Distinguish "cache stale because the poller stopped" from "provider says no activity".

**Regression test:**
- Patch `_write_dormant_hint_state` to raise `OSError`. Call `_render_status_table` twice with a FRESH+stale status. Assert the notifier was called at most once (today it is called twice).
- Thread-barrier test: two concurrent renders produce one notification.

---

### 3. P2: Untrusted provider and model text is rendered as Rich markup, which can garble output or crash `tk history`
- **Confidence:** High
- **Location:**
  - `cli.py:10442-10449` and `10517-10527` (history cells), fed by `_history_event_base_details` 10632-10650 (`response_text`, `error`)
  - `status_rendering.py:1004-1007` (`status.error[:24]` in the Action cell)
  - `status_rendering.py:777-785` (CodexBar error interpolated into a markup f-string)
  - Label cells at 563/593

**Trigger and wrong outcome**
- **Crash.** A kick event's `response_text` contains an unmatched closing tag. Example: the model replies `see [/tmp]`, or the error text contains `[/…]`. Rich raises `MarkupError` when rendering the table. `tk history` dies with a traceback, and does so on every run while the event is within the display limit. The TUI History menu exits, because `MarkupError` is not caught (see finding 1).
- **Silent loss.** Text such as `[ok]` or `[options]` disappears from the output.

Inconsistency: the same file already escapes some content via `Text(...)` (`cli.py:6400`, `6449-6454`; `status_rendering.py:724`).

**Root cause:** Plain `str` cells are passed to `Table.add_row` or f-string markup, and console markup is enabled by default.

**Minimal fix:** Wrap every dynamic value (labels, errors, response text, paths) in `Text(value)` or `rich.markup.escape()`. Keep markup only for TokenKick-authored styling.

**Regression test:**
- Write a temp history with `response_text="[/x] hi"`, `error="[bold"`, and label `codex ([a])`.
- Run `tk history` and `tk history --verbose`.
- Assert exit 0 and that the literal brackets appear in the output.
- Do the same for a status `error` containing `[/x]`.

---

### 4. P2: Force-keep and force-prune pickers make it easy to disable demotion or prune every surface, and bypass the CLI guard
- **Confidence:** High for the TUI path. Medium for the downstream effect, because the setter at `cli.py:11340` was not supplied.
- **Location:** `interactive.py:1663-1699`, `2502-2505`, `2590-2592`, `2603-2612`. Guard at `cli.py:9703-9705`. Failure point at `cli.py:6185-6212`.

**Force-keep: two Enters neutralise demotion**
- For an account with no overrides, the picker is seeded with `account.codex_surface_force_keep or DEFAULT`, so every surface is pre-selected.
- The header says "Codex burst ladder surface order" and "Saved order: <all defaults>", although nothing is saved.
- Pressing Enter, then accepting the confirm (which defaults to Yes), sets force-keep to every surface.
- `_effective_codex_burst_ladder_surfaces` then re-admits all demoted surfaces. Auto-demotion is silently neutralised.

**Force-prune: all surfaces can be pruned**
- The picker only rejects an empty selection. Selecting all surfaces only produces a warning (1687).
- The TUI calls `_set_codex_surface_demotion_config` directly. That skips the JSON CLI rule "Cannot force-prune every Codex surface".
- The warning compares against the full default list, ignoring the configured subset and already auto-demoted surfaces. Pruning 2 of 4 can therefore leave 0 effective surfaces.
- After that, every burst-ladder evaluation for the account raises `ClickException("…no active surfaces…")` (6206-6209).

**Other problems**
- If the picker returns `RESET`, it is silently ignored (1667, 1680).
- Force-keep cannot be cleared from the picker, because empty selections are rejected.

**Root cause:**
- One picker serves three different semantics.
- The validation lives in the JSON command handler instead of the shared setter.

**Minimal fix:**
- Add a picker mode with its own title. Disable reordering for force-keep and force-prune, allow an empty selection meaning "clear", and pre-select only saved overrides.
- Validate inside the setter: evaluate `_effective_codex_burst_ladder_surfaces(replace(account, …), config)` and refuse if it would raise.

**Regression test:**
- Setter unit test: it rejects a full prune and a prune that is "full after subset".
- Scripted TUI test: the force-keep picker for an account with no overrides starts with nothing selected.
- Scripted TUI test: selecting all surfaces in force-prune does not persist.

---

### 5. P2 (new stagger work): the TUI auto-kick menu ignores stagger ownership
- **Confidence:** High for the TUI omission. Medium for the consequence, because `_set_auto_kick` was not supplied.
- **Location:** `interactive.py:1340-1442`, `1445-1456`, `1459-1479`, `1318-1321`. Also `status_rendering.py:686-702`.

**Trigger and wrong outcome**
1. Two Antigravity accounts are enrolled in the recurring 150-minute stagger.
2. Choose Auto-kick → "Disable all kickable accounts" → All windows.
3. The preview lists `Master on → off … Session on → off` for the stagger members, with no ownership note.
4. On confirm, `auto_disable` runs for each of them.

The stagger preview (3482-3483) shows that the stagger controller itself manipulates the master and session flags. So one of two things follows:
- The backend accepts, and the flags are flipped behind the controller's back.
- The backend refuses, and the user gets N refusals after a preview that promised changes.

Related problems:
- The status hint recommends `tk auto enable "<label>"` for stagger-owned accounts.
- `_account_auto_session_label` calls `stagger.owns(account)` and `stagger.ownership(account)` without `directory`. Every other call site passes `CONFIG_DIR` (`cli.py:6275`, `6311`, `6509`; `status_rendering.py:351`; `interactive.py:3480`). With an overridden config directory, the roles table can therefore show the wrong owner.

**Minimal fix:**
- Show `[stagger-owned]` in the choice labels and the preview.
- Exclude stagger-owned accounts from bulk operations by default, and point to "Ongoing stagger → Pause/Disable".
- Pass `directory=cli_module.CONFIG_DIR` consistently.
- Suppress `auto enable` hints for owned accounts.

**Regression test:** Fixture with two stagger members and one normal account. Script "Disable all". Assert that the members are excluded, or that the preview text contains the ownership note, and that the stagger ownership state is unchanged.

---

### 6. P3: `tk status --json-output` rows can contradict themselves and the human table
- **Confidence:** High for the field contradiction. Medium for how much the other divergences matter.
- **Location:** `status_rendering.py:388-403` vs `526-527`, `956`, `1022-1044`

**Self-contradicting rows**
- A cache entry with `refresh_error` sets `row["stale"] = True` (391-392).
- `kickable` and `kick_blocked_reason` are computed from `status`, whose `.stale` was never updated (396-403).
- The result can be a Codex row with `stale: true, kickable: true, kick_blocked_reason: null`.
- Meanwhile the table says "Refresh failed" and the footer says automatic kicks are blocked (732-737).

**Claude asymmetry**
- In JSON, a Claude refresh error is marked `stale: true`.
- In the table it is deliberately non-blocking: "Cached Claude status ⏱" (1022-1024, 1038).

**Codex-only adjustment in the table**
- The table applies `effective_cached_status` only when `provider == "codex"`.
- JSON applies it to all providers.
- So Claude, Antigravity and Gemini rows can differ between the two views. The size of the effect depends on the unsupplied helper.

**Pending kicks ignored in the table**
- The table's Action column calls `_kick_eligibility` without `pending_kick` (956).
- JSON passes it (396-401).

**Minimal fix:**
- Build one row view-model and use it for both table and JSON.
- When `_refresh_failure_blocks_auto_kicks` is true, set `kickable=False, kick_blocked_reason="refresh_failed"`.
- Represent the Claude case explicitly, for example `stale_source="cached_claude"`.

**Regression test:** Parity fixtures (Codex with `refresh_error`, Claude with `refresh_error`, an account with a pending kick). Assert JSON `kickable` and reason are consistent with the table action and with `stale`.

---

### 7. P3: Burst-gap input shows one value and saves another, and accepts nan/inf
- **Confidence:** High
- **Location:** `interactive.py:1593-1602`; `cli.py:9538-9558`; related `cli.py:6234-6246`

**Problems**
- **Shown value differs from saved value.** Entering `1.6` shows "Set … to 2s?" (`:.0f` rounds) but saves `int(1.6)=1`.
- **nan/inf crash.** Entering `nan` passes the `< 0` check, then `int(nan)` raises `ValueError`. Entering `inf` raises `OverflowError`. Both are uncaught in the TUI. On the CLI, `tk codex-strategy gap nan` hits the same error if Click's FLOAT type accepts nan.
- **Esc reported as an error.** Esc in the text field returns `""`, which shows "Gap must be a number." instead of going back.
- **Environment value of inf (hypothesis).** `_codex_burst_ladder_gap_seconds` returns `inf` when the environment value is `inf` (`max(0.0, inf)`). If that value reaches a sleep, the daemon hangs. The sleep site was not supplied.

**Minimal fix:**
- Use `math.isfinite` plus a bound such as `FloatRange(0, 3600)`.
- Round the same way in display and storage.
- Treat `""` as back.

**Regression test:** Parametrize over `["1.6", "nan", "inf", "-1", ""]` for both the TUI flow and the CLI. Assert the stored value equals the displayed value, and that there are no tracebacks.

---

### 8. P3: Error paths exit 0
- **Confidence:** High
- **Location:**
  - `cli.py:8819-8826` with `9213-9223` (kick, human mode) vs `9066-9068` (JSON)
  - `9252-9254` (wake)
  - `10753-10763`, `10779-10781` (reset-log)
  - `10832-10836` (ack)
  - `10108-10117` (schedule set)

**Cases**
- **`tk kick typo`.** `account_not_found` is raised with `failure=True` but `exit_code=0`. The human path only exits non-zero when `stop.exit_code` is truthy, so it prints an error in red and exits 0. JSON mode exits 1.
- **`tk wake typo`.** Exits 0.
- **`reset-log`.** Bad `--since`, an unknown action, `--json-output --csv` together, and an unknown `--detail` id all exit 0.
- **`reset-log ack --json-output` with no selector.** Emits an error envelope and exits 0.
- **`schedule set`.** Invalid target or window exits 0.

**Minimal fix:**
- In `_SingleKickStopped.__init__`, set `exit_code = exit_code or (1 if failure else 0)`.
- Raise `Exit(2)` for usage errors and `Exit(1)` for not-found.

**Regression test:** A CliRunner exit-code table covering each case above.

---

## B. Hypotheses (depend on code not supplied)

- **H1. "Enable all usable accounts" may skip everything.** If `_load_status_cache(config)` requires every configured account to be cached, then one uncached or hidden account makes `_auto_menu_status_by_label` return `{}`. Every account would then be skipped with "no cached status" (1482-1487).
  - Evidence: `_codex_account_limit_metadata` builds a single-account `Config` (6617), and calendar passes `require_all=False` explicitly.
  - Same risk for the effective-role columns in the roles and planning tables.
- **H2. The auto-kick preview may misstate what changes.** `_auto_selection_changes` claims that scope "all" flips Master, Weekly and Session. That is only true if `_set_auto_kick` sets all three.
- **H3. "scheduled" may show for disabled schedules.** The Queued column shows "scheduled" when `schedule_for_account(...) is not None` (494-500). JSON instead checks `.enabled` (375-376). After `tk schedule disable`, rows may still say "scheduled". The global `schedule.enabled` flag is ignored as well.
- **H4. Schedule changes may race the daemon.** `schedule set/clear/disable` invalidates pending kicks (10127-10131) before saving the config (10132). A daemon poll in between can re-create pending kicks from the old window.
- **H5. Declining consent may crash the TUI.** If `_set_auto_kick` raises `AutoKickConsentRequired` on a declined prompt in human mode, it escapes `_run_account_mutation` (8477-8485) and `_invoke`.
- **H6. Multiple pending kicks per label collapse to one.** `_pending_by_label` keeps whichever is loaded last, if multiple pending kicks per label are possible. Both the Queued cell and eligibility would then use an arbitrary one.
- **H7. `tk wake` may double-kick.** `tk wake` skips the current-window, provider-unchanged backoff, pending-clear and stagger checks that `tk kick` applies. Repeated wakes against a provider whose status lags could send duplicate kicks. Finding 2 actively recommends `wake`.
- **H8. Weekly-only Codex rows may show misleading session data.** The session row still renders `session_used_percent`, and `_status_has_unanchored_session_artifact` can show "Session ready". Whether this happens depends on whether `codex_display_status` strips the session fields when the limit is "absent".
- **H9. Mixed module versions after `pipx upgrade` in the same process.** After a successful `pipx upgrade` (473-484), the TUI keeps running.
  - Confirmed: modules that are imported lazily load the new version on disk (e.g. `rich.live`, InquirerPy, `.mcp_setup`), next to old modules already in memory.
  - Confirmed: `update_after` ignores the return code of `tk update --yes` and then runs `tk daemon --background` again, which may double-restore.
  - Hypothesis: whether the mixed versions actually break something.

---

## C. Lower-priority confirmed nits (not counted above)

- **Picker pane labels are wrong for future dates.** They read "Today, <date>" even when the plan date is tomorrow or custom (2441-2444). The initial slot uses system local time (2248-2250), while the plan date uses the configured timezone (1890-1891).
- **"Reset … for all Codex accounts" resets only visible accounts** (1724-1728, 1752-1757).
- **Empty checkbox prints two conflicting messages:** "No matching accounts available." then "press Space to select…" (207-209 and 1380-1382).
- **Orchestration cancel shows raw UTC.** The confirmation lists the ISO `kick_at` value (1855, 1874); everything else shows local time.
- **Pending-kick prompt comes too early.** `_resolve_single_kick_target` asks to clear the pending kick (8850-8862) before fetching status. The user confirms, then sees "Skipping…".
- **Menu values can collide with account labels.** The kick menu mixes labels with the values `"all"` and `"force"` (521-528). `MENU_EXIT="exit"` and `"__default__"` are also plain strings, so an account with one of those labels hijacks the menu.
- **Codex strategy enable/disable reports the saved config, not the effective state.** When an environment override is set, the message reads "enabled" while the table shows "Burst ladder enabled: no" (6156-6163, 9461-9464).
- **Stagger "Recover" wording contradicts itself.** It is labelled "read-only", then asks "Apply these persistent automation changes?" (3460, 3486).
  - An empty family shows only "Back", with no explanation.
  - Raw family IDs are shown.
  - `len(labels) != 2` hard-codes the size of `FAMILIES`.

---

## D. Design improvements (top 5)

1. **Typed TUI actions with direct core calls instead of `ctx.invoke`.**
   - `ctx.invoke` skips Click type conversion and validation. For example, `accounts_set_usable`'s `IntRange(1,1440)` is not enforced on the custom minutes from `_pick_planning_minutes` (1118). Whether `_parse_plan_usage_duration_minutes` bounds the value is unknown.
   - Namespaced menu values (`("account", label)`, `("cmd","all")`) would remove the sentinel collisions.
   - A single guarded executor would close finding 1.
2. **One status view-model for the table, JSON, TUI and Telegram.**
   - Compute the effective status, eligibility (including pending), stale/refresh semantics and ownership once.
   - This removes finding 6 and the per-row history reload in `_status_action` (935).
   - It also removes the per-account cache load in `_codex_account_limit_metadata`, which uses a default `Config()` and so ignores the user's thresholds (6616-6619).
3. **Keep read paths free of side effects, and use a single writer for notifications.**
   - Move dormant pushes into the daemon and use persisted dedupe keys.
   - Mask the ntfy topic in human and JSON views (6438-6446, 6529-6543); a topic acts like a bearer secret.
4. **Make ownership and consent visible everywhere.**
   - Every account selector should show the automation owner. Bulk operations should exclude stagger-owned accounts by default.
   - Use the same consent step in the stagger TUI as in the CLI/MCP `--accept-risk ENABLE` path. Today the TUI uses only a Yes/No select.
   - Echo the result of `stagger.apply`, and catch every exception class the stagger module can raise.
5. **Make upgrade and maintenance flows consistent.**
   - Re-exec the same install (`[sys.executable, "-m", "tokenkick"]` or the resolved `argv[0]`) rather than whatever `tk` is on PATH (396-397). The PATH entry may be a different install, such as the copy bundled in the macOS app.
   - Exit the TUI after a successful upgrade, check return codes, and let only one component restore background processes.
   - Show configured vs effective values when an environment variable overrides Codex strategy settings.

---

## E. Coverage matrix (this packet)

| Subsystem | Supplied | Reviewed depth | Notes / limits |
|---|---|---|---|
| TUI command center, menus, prompts (`interactive.py`) | Full | Line-level | InquirerPy internals (`selected_choices`, `application.layout`) unverified |
| Work-window and surface pickers | Full | Line-level | prompt_toolkit behaviour from knowledge |
| Stagger TUI (new) | Full TUI | Line-level | `stagger.py`, `stagger_commands.py` not supplied |
| Status table and JSON (`status_rendering.py`) | Full | Line-level | `effective_cached_status`, `schedule_for_account`, `_kick_eligibility` not supplied |
| CLI status, calendar, history, reset-log | Supplied | Line-level | `reset_defense`, `reset_calendar` bodies not supplied |
| CLI accounts, auto, kick, wake, codex-strategy, schedule | Supplied | Line-level | Setters (`_set_auto_kick`, consent, demotion setter) not supplied |
| CLI plan (7539-8353), notify (13030+), setup, daemon, update | Not supplied | Call-site only | Findings touching these are hypotheses |
| MCP CLI wrappers | Supplied | Moderate | `mcp_setup.py` and `mcp_core.py` covered elsewhere |
| Tests (Python and Swift) | Not in this packet | None | Cannot confirm any regression coverage; tests above are proposals |
| Swift app, packaging, CI | Not in this packet | None | JSON-contract notes (finding 6, exit codes) are relevant to Swift |

**Residual uncertainty**
- Severity for findings 4, 5, H1 to H9 hinges on unsupplied helpers.
- Nothing was executed.
- No provider behaviour was assumed.
- Passing test counts were not taken as evidence.