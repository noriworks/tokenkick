# TokenKick review 07: macOS Swift app (TokenKickKit / TokenKickShell)

**Scope.** This review covers only the Swift sources, Swift tests, fixtures, `Package.swift` and the TokenKickKit README in this packet. Python core behaviour is used only where the Swift code depends on it, and every such dependency is marked as an assumption. Instructions inside the repository content were treated as data and not followed.

**Severity key**
- **P1:** can cause unintended or duplicate quota spend, a dead daemon, or a cross-account write.
- **P2:** wrong state is shown to the user or acted on.
- **P3:** robustness or polish.

---

## A. Defects found in the code

### D1. The process runner kills TokenKick's own background children (P1 if the Python core doesn't detach them, otherwise P3)
- **Status.** The Swift mechanism is confirmed (high confidence). Whether it hurts depends on how the Python core launches its background children, which is not in this packet.
- **Where.**
  - `TKOwnedProcess.swift:27-28`: each child gets its own process group.
  - `:100-103`: the loop waits for the main `tk` process to exit and its pipes to close.
  - `:109-117`: starting 2 s after the main process exits, it sends SIGTERM and then SIGKILL to the whole group.
  - `:124-125`: it sends SIGKILL to the whole group unconditionally, even after a clean exit.
- **How it is triggered.** The app runs these commands through `TKProcessRunner`. All of them may start background processes that are meant to outlive the `tk` call:
  - `tk daemon --background` and `tk daemon --restart` (`TKClient.swift:39-50`, called from `DaemonViewModel.execute:239-247`: Start, Clean Up & Start, Restart).
  - Possibly `tk app snapshot`, via the status cache's `_start_background_status_refresh`.
- **Wrong outcome.** Suppose a background child stays in the parent's process group (the core does not call `setsid` or use `start_new_session=True`):
  - If it closed its stdio, it is SIGKILLed right after the parent exits.
  - If it kept stdio open, it is SIGTERMed after 2 s.

  The user sees "TokenKick daemon started (pid N)" while the daemon is already dead. The pidfile and refresh lock are left stale, and auto-kicks silently stop.
- **Root cause.** The runner treats every member of the group as owned, even after a clean exit. The current tests (`ProcessOwnershipTests`) enshrine this design.
- **Minimal fix.**
  - Only signal the group on timeout, on cancellation, or when the pipes are still held after the main process exits.
  - After a clean exit with both pipes closed, just reap the main process.
  - Separately, require and test that core background spawns use `start_new_session=True`.
- **Regression tests.**
  - *Python:* assert that the background spawns for the daemon, status refresh and Telegram remote pass `start_new_session=True`.
  - *Swift integration (gated on `TK_BUNDLED_RUNTIME`, isolated HOME, no provider CLIs on PATH):*
    1. Run `daemon --background --json-output` through `TKProcessRunner`.
    2. Wait 3 s.
    3. Assert that `daemonStatus().payload.daemon.running == true`.
    4. Then run `daemon --stop`.
  - *Swift unit:* `sh -c "(sleep 1; touch $M) </dev/null >/dev/null 2>&1 & exit 0"` with clean exit; assert `$M` exists after 1.5 s.
- **Related minor issue.** `:132-133` reports death-by-signal as `exitCode = signalNumber`, which can't be told apart from `exit(15)`.

### D2. The login-item daemon ignores the user's extra PATH entries (P2, high confidence)
- **Where.**
  - `TKLaunchAgent.swift:264-268`: the plist's PATH is built from the built-in defaults only.
  - `DaemonViewModel.swift:45-50`: the manager is built without the user's extras.
  - `TKLaunchAgent.swift:172`: `needsRepair` never compares PATH.
- **How it is triggered.**
  1. The user adds `/custom/bin` (where `claude` or `codex` lives) in Settings → Environment.
  2. Discovery and snapshots run through `LiveTKClient`/`LiveSetupSessionStarter`, which include the extras, so they find the CLI.
  3. The user enables "Run in background at login".
- **Wrong outcome.**
  - The daemon run by launchd can't find the provider CLI, so auto and scheduled kicks fail even though the app shows the accounts as healthy.
  - A daemon started with the Start button (`tk daemon --background`, which inherits the app's environment) works.
  - So behaviour differs depending on how the daemon was started.
- **Minimal fix.**
  - Pass `pathAdditions` into `TKLaunchAgentManager` and write them into the plist.
  - Include a PATH comparison in `needsRepair`.
  - Offer "Repair" when Settings' PATH entries change.
- **Regression test.** Build a manager with extras `["/opt/x/bin"]` and run `install()`. Assert the plist's `EnvironmentVariables.PATH` contains `/opt/x/bin`. Then change the extras and assert `status().needsRepair == true`.

### D3. Enable/Takeover installs files before checking ownership, and judges ownership from stale or missing data (P2, high confidence; duplicate-daemon impact depends on the core's pidfile guard)
- **Where.**
  - `DaemonViewModel.swift:248-250` and `:258-260`: `installAgent()` (which writes the plist with `RunAtLoad=true`) runs before `startAgent` does its ownership check.
  - `TKLaunchAgent.swift:203-212, 337-363`: the ownership check is `classifyDaemon(daemon)` on the daemon status passed in by the caller, and `nil` is treated as `.notRunning` (`:358`).
  - Callers pass `store.snapshot?.daemon`: `SettingsView.swift:66-79`, `SetupFlowViews.swift:174-179`, `DaemonScreen.swift:163`.
- **How it is triggered.**
  - **(a) Unknown-origin daemon.** Ownership is `.unknownRunning`. The Settings toggle is enabled (it is only disabled for `terminalManaged`).
    1. The user toggles it on.
    2. The plist is installed.
    3. Start throws `takeoverRequired`.
    4. The toggle's getter (`agentStatus.installed`) now shows **On** despite the error, and the next login launches a second daemon.
  - **(b) Missing or stale snapshot.** The snapshot is `nil` (first fetch failed or timed out) or out of date, and a terminal daemon started after the last snapshot. The manager sees `.notRunning`, runs bootstrap and kickstart, and two daemons now poll the same `~/.tokenkick`.
- **Wrong outcome.** A leftover login item and/or two daemons running at once. This risks duplicate kicks, duplicate notifications, and contention over the pending-kick and stagger state, unless the core's daemon startup refuses to run when its pidfile is held. That code (`_run_daemon_foreground`, `_prepare_daemon_pidfile_for_start`) is not in this packet.
- **Minimal fix.**
  - Inside `start()`, fetch live status through the daemon client. Add `daemonStatus()` to `TKDaemonCommanding`.
  - Treat a `nil` or unreadable status as "unknown", and refuse to start.
  - Write the managed files only after the ownership check passes, and remove them again if start fails.
- **Regression tests.**
  - `start(daemon: nil)` with a fake client that reports a running terminal daemon: expect `takeoverRequired`, no `bootstrap` call, and no plist on disk.
  - `DaemonViewModel` with an agent that throws `takeoverRequired`: expect `agentStatus.installed == false` afterwards.

### D4. Notification routing is guessed from a display string, so the app shows and edits the wrong route (P2, high confidence; evidence from the fixtures)
- **Where.**
  - `AccountsViewModel.swift:157-168`: `NotificationRoute.from(routeDisplay:enabled:)` guesses the route from the human-readable string.
  - `NotificationsScreen.swift:99-122`: the picker builds on that guess.
  - `AccountConfigRow` ignores `TKAccountNotificationsPayload.Account.backends` (`TKSnapshot.swift:201-215`), which is already loaded.
- **How it is triggered (both cases appear in the committed fixtures).**
  - **(a) Global disabled.** `accounts_list.json` has `notifications_enabled: true` with `notifications_route: "❌ disabled"`. Because the string contains "disabled", the guess returns `.none` and the picker shows **Off**.
  - **(b) Global ntfy.** `notify_global.json` has `backends: null` (the account follows the global default) with `route: "✅ ntfy"`. The guess returns `.ntfy`.
- **Wrong outcome.**
  - In (a), when the user later enables a global destination, an account shown as "Off" starts sending notifications.
  - In (b), the account looks pinned to ntfy, but it follows the global setting if that changes. Picking "ntfy" to pin it is silently skipped by the `guard newRoute != current` check (`:118`), so the account is never actually pinned.
- **Minimal fix.**
  - Derive the route from structured fields: `notifications_enabled` plus `backends` (`nil` means global default; an explicit list means pinned).
  - Join the notifications payload into `AccountConfigRow`, or have the core add a `route_kind` enum.
- **Regression test.** Using the `notify_global.json` and `accounts_list.json` fixtures, assert both accounts map to `.globalDefault`. Then select `.ntfy` and assert the mutation `set-notifications … --ntfy` is sent.

### D5. Advanced screen: account-detail loads race, so overrides can be written to the wrong Codex account (P2, high confidence)
- **Where.**
  - `AdvancedViewModel.swift:160-164`: `selectCodexAccount` starts a details load.
  - `:171-193`: `loadSelectedCodexDetails` assigns results with no check that they still belong to the selected account.
  - Saves use the selected label: `saveForceKeep` (`:240-250`) and `requestSaveForcePrune` (`:252-272`).
  - The form is only disabled while a mutation runs (`AdvancedScreen.swift:80`), not while details load.
- **How it is triggered.**
  1. The user selects account A, whose detail load is slow.
  2. The user quickly selects account B.
  3. B's load returns first, then A's load returns.
- **Wrong outcome.**
  - `surfaces`, `patterns`, `forceKeepText` and `forcePruneText` now hold A's data while `selectedCodexLabel == B`.
  - "Save force-keep" or the destructive "Save force-prune" then writes A's list onto B. The confirmation sheet shows scope **B**, so the cross-account write looks authorised.
  - `isLoadingDetails` is cleared by whichever load finishes first.
- **Minimal fix.**
  - Use a generation counter, or capture the label and return early if `selectedCodexLabel` no longer matches.
  - Disable the demotion controls while `isLoadingDetails` is true.
  - Do the same for `load()` overlapping a reload after a mutation.
- **Regression test.** Use a stub whose `codexSurfaces(label:)` sleeps 100 ms for "A" and 0 ms for "B". Select A, then B, and await both. Assert `surfaces?.label == "B"` and that `forceKeepText` comes from B's fixture.

### D6. A refresh requested while another is running returns early, so the UI shows stale data after a change (P2, high confidence)
- **Where.**
  - `SnapshotStore.swift:100-104`: `guard !isRefreshing else { return }`.
  - Every view model's post-mutation `onMutation` (`AppRoot.swift:29-66`) assumes that awaiting `refresh()` returns fresh data.
- **How it is triggered.**
  1. An auto-refresh (30–300 s interval) or a popover `.task` refresh starts.
  2. The user confirms a kick, schedule change or plan apply.
  3. `onMutation` → `refresh()` returns immediately.
  4. The in-flight snapshot, which was taken before the mutation, lands afterwards.
- **Wrong outcome.**
  - The Kick screen still shows the account as fresh or kickable until the next interval.
  - On the Planner, `PlannerScreen.swift:46-48` (`onChange(of: lastUpdated)`) replaces the active-plan rows that `confirmPendingAction` just set (`PlannerViewModel.swift:443-461`) with the pre-apply snapshot. "Cancel Active Plan" disappears even though orchestrated kicks exist.
  - `confirmPendingAction(snapshot:)` also reads the snapshot captured when the sheet was confirmed, not the refreshed one (`:457-460`).
- **Minimal fix.**
  - Keep a `refreshRequestedDuringFlight` flag or generation counter, and loop once more after the current fetch.
  - Callers that need post-mutation data should await the follow-up fetch.
  - The Planner should re-read `store.snapshot` after `onMutation` instead of using the captured value.
- **Regression test.** Use a provider whose first fetch blocks on a continuation and returns snapshot S0; the second returns S1.
  1. Start refresh #1.
  2. Call refresh #2 (as a mutation would) and await it.
  3. Release #1.
  4. Assert that two fetches ran and that `store.snapshot` is S1 when #2's await returns.

### D7. A timed-out or interrupted kick is reported as "Failed" even though quota may have been spent (P2; confirmed Swift behaviour, kick duration is an assumption)
- **Where.**
  - `KickViewModel.swift:131-136`: any thrown error becomes `.failed`.
  - `LiveKickPerformer` timeout of 300 s (`:31`).
  - `TKProcessRunner.swift:47-49`: the timeout SIGTERMs the process group mid-run.
  - `:47` also throws `.cancelled` even when the process finished successfully but the task was cancelled afterwards, discarding a completed result.
- **How it is triggered.** A kick plus post-kick verification runs longer than 300 s. It is unverified here whether `tk kick` can do this: Codex delayed anchor verification and Claude probing are candidates.
- **Wrong outcome.** The provider CLI may already have sent the prompt, but the user sees a red "tk did not finish within 300s" and may retry, spending more quota. The core may also be killed while writing history or pending-kick state.
- **Minimal fix.**
  - Map `timedOut` and `cancelled` to a distinct "Outcome unknown — check History" result.
  - Don't count that state as a failure, and refresh History for that label.
  - For kicks, prefer no hard kill, or a much longer deadline than the core's own worst case.
- **Regression test.** A `StubKickPerformer` that throws `TKProcessError.timedOut` should leave the row in a not-`.failed` state whose message mentions History.

### D8. Plan dates and work windows are formatted with the user's locale and calendar (P2, high confidence)
- **Where.**
  - `PlannerViewModel.swift:197-212`: `DateFormatter()` with `"HH:mm"` and `"yyyy-MM-dd"`, without setting `locale`, `calendar` or `timeZone`.
  - These strings feed `dateArgument` and `workWindow` → `tk plan --date/--work-window`.
- **How it is triggered.** Any of these user settings:
  - A Thai locale with the Buddhist calendar, giving `yyyy` = 2569.
  - A Japanese-calendar setting.
  - A locale whose default digits are not 0–9, such as Arabic-Indic digits.
  - The 12/24-hour override that Apple QA1480 warns can affect fixed formats.
- **Wrong outcome.** The core rejects the plan, or plans and applies kicks for the wrong date. The same formatter is used for display, so the preview looks consistent and the mistake isn't obvious.
- **Minimal fix.** For the argument strings, set `locale = Locale(identifier: "en_US_POSIX")`, `calendar = Calendar(identifier: .gregorian)` and `timeZone = .current`. Keep the localised formatter for display only.
- **Regression test.** Under a test-injected `Locale("th_TH")` and Buddhist calendar, `dateArgument` for 2026-06-11 must equal `"2026-06-11"`.

---

## B. Minor confirmed issues (P3, one line each)
- `AccountMutation.arguments` (`AccountsViewModel.swift:84-85`) always sends `--accept-risk ENABLE`. It ignores the core-supplied `request.confirmation` and consent `version`, so if the core changes its token, the consent sheet will loop forever.
- `ScheduleViewModel.confirmPendingAction` (`:261-271`) acts on the live `selectedScope`, not the scope shown in the confirmation (`tkArguments`). The Planner captures its request; Schedule should do the same.
- The daemon-down warning (`WarningModel.swift:135-149`) only fires when some row has `auto_kick == true`. Applied orchestration plans, pending kicks or a recurring stagger with the daemon stopped produce no warning.
- `TKClient.plan` and `cancelPlan` (`TKPlanning.swift:237-255`) decode a bare payload with no envelope fallback. App-mode error envelopes surface as raw `keyNotFound` errors.
- `TKLaunchAgentManager.remove()` (`:244-250`) only boots out the launchd job. A daemon started with `tk daemon --background` keeps running, although the sheet says "Stops the daemon".
- The popover's next action (`PopoverModel.swift:46-58`) includes kicks that are overdue or have `gave_up_at`, and labels them "kick now".
- "Not Now" during first-run discovery (`SetupFlowViews.swift:152`) hides the sheet but leaves setup running and writing config. It should call `cancelDiscovery()`.

## C. Hypotheses: verify before acting
- **H1. Reset acknowledgements.** The warning model keys on `acknowledged` (`WarningModel.swift:204`), while the reset log uses `acknowledged_at` (`DiagnosticsViewModel.swift:77`). If the snapshot's `reset_observations` matches the reset-log shape, the advisory never clears after an acknowledgement. No authentic fixture covers this: in `snapshot.json` the list is empty.
- **H2. Status-row schema.** `SnapshotAccountRow` defaults `kickable` to false and `visible` to true, and the warning model reads `auto_kick`. The only authentic snapshot fixture has `accounts: []`; all populated rows are hand-written in `ShellFixtures`. If the core's rows lack these keys, the Kick screen shows everything as ineligible and the daemon warning never fires.
- **H3. `launchctl bootstrap` on an already-loaded agent.** On recent macOS this commonly returns "Bootstrap failed: 5: Input/output error", which `isAlreadyBootstrapped` (`:401-404`) doesn't match. Since `launchAgentLoaded()` already exists, calling it first avoids the issue. Also, `kickstart -k` immediately after a `RunAtLoad` bootstrap kills the first instance mid-startup.
- **H4. Main window lookup.** `MainWindowPresenter.focusExistingWindow` also matches `window.title == "TokenKick"`. The `MenuBarExtra(.window)` panel may carry that title, so "Open TokenKick" could focus the panel instead of opening the main window. SwiftUI window identifiers are typically not exactly `"main"`.
- **H5. Schedule inheritance.** An account scope with no override shows as disabled, and "Enable" sends `schedule set --account X` with no windows. Whether accounts inherit the default schedule, and what that command does, is core behaviour not shown here.
- **H6. Refresh cost.** Auto-refresh every 30–300 s spawns `tk app snapshot`. Whether that can trigger provider probes (Claude `/usage`, AG CLI sessions) and spend quota or defeat the new Claude backoff is not verifiable from this packet.

## D. Design improvements
1. **One ownership and process-lifetime contract.**
   - Define "app-managed" as "launchd job with label X is loaded", not "executable path matches".
   - Have the manager fetch live status itself, and kill process groups only on timeout or cancel (D1, D3).
   - Detect App Translocation before persisting `runtime-path`.
2. **Structured core→app contracts instead of string parsing.**
   - Use enums for notification route, warning codes (instead of `contains("daemon")`/`"status cache"` tiering), reset trigger kind, and kick outcome.
   - Echo consent `confirmation` and `version` back to the core.
   - Generate authentic fixtures for a populated snapshot and for `notify`/`plan` error envelopes.
3. **An "uncertain outcome" state for long mutations.** Kicks, plan apply and takeover should end in a reconcilable "unknown — re-check" state rather than "failed". Add an idempotency or attempt id the app can look up in History.
4. **Secret and runtime hardening.**
   - Pass the Telegram bot token on stdin or in an environment variable, not argv (`AccountsViewModel.swift:111-112`). Redact it in `TKDecodingError` output previews.
   - Honour `TK_APP_RUNTIME` only in debug builds: today a same-user `launchctl setenv` can redirect what gets persisted as the login item's runtime.
   - Shell-quote the copyable commands (`AdvancedScreen.swift:376-377`), and show the bundled runtime path instead of a bare `tk`.
5. **Surface the new core features.**
   - `snapshot.stagger` is decoded but never shown, and there are no app controls for the recurring AG 150-minute stagger.
   - "Session (5 h) auto-kick" is hard-coded and misleading for weekly-only Codex plans and AG windows.
   - Make `pendingConfirmation` per-scene: Kick, Planner and Daemon each bind one model property to two windows (popover and main window, or Settings and Daemon).

## E. Coverage matrix (this packet)

| Subsystem | Read | Findings | Limits |
|---|---|---|---|
| Process runner / line stream | Full | D1, D7 | Python spawn flags not visible |
| LaunchAgent / daemon control | Full | D2, D3, H3, B | Core pidfile guard unseen |
| Envelope / JSON models | Full | B (plan decode) | No populated-snapshot fixture (H2) |
| Snapshot store / warnings | Full | D6, B, H1 | — |
| Kick flow | Full | D7, B | Core kick duration unknown |
| Accounts / notifications / consent | Full | D4, B | Core route semantics inferred from fixtures |
| Planner / schedule | Full | D6, D8, B, H5 | Core plan/schedule CLI unseen |
| Advanced (Codex) | Full | D5 | — |
| Setup / first run | Full | B | — |
| Window / menu bar presentation | Full | H4 | Not run |
| Swift tests | Full | Missing tests for D1–D8 | Not executed |
| Packaging / bundle / signing | README only | — | Build scripts, Info.plist and entitlements not in this packet |

## F. Residual uncertainty
- I did not build, run, or execute any test. All conclusions come from static reading.
- The severities of D1 and D3, and hypotheses H1–H6, depend on Python code covered by other subsystem reviews. Each needs to be confirmed there.
- The passing Swift suite (203 tests) uses hand-written fixtures for account rows. It does not exercise any of the races or locale cases above.