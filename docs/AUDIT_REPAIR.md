# Reliability repair checklist

This batch addresses the sixteen findings from the October 2026 repository audit.
Provider requests in regression tests use synthetic data and fake executables.
Server deployment is a separate step after local verification.

## Acceptance criteria

- [x] 1. Partial caches preserve account identity and missing observations.
- [x] 2. Concurrent configuration, plan, and statistics updates cannot lose changes.
- [x] 3. Failed/stale predictions cannot authorize automatic requests.
- [x] 4. Optional Codex windows and broken fallbacks preserve valid native readings.
- [x] 5. MCP TOML edits preserve unrelated tables and content.
- [x] 6. Exports cannot overwrite source aliases or unfamiliar destinations.
- [x] 7. Process control rejects invalid/reused/unverified PIDs.
- [x] 8. Every ordinary kick path applies the same safety policy.
- [x] 9. Expired work and retries never trigger obsolete requests.
- [x] 10. Daemon ownership and account actions serialize across processes.
- [x] 11. All status interfaces agree on observation freshness.
- [x] 12. Telegram transport errors retry safely with bounded backoff.
- [x] 13. Process and stream deadlines include descendants and pipe draining.
- [x] 14. Installed wheel and bundled MCP resources are readable.
- [x] 15. Overnight schedules preserve yesterday's active window.
- [x] 16. Planner application is bound to the preview the user confirmed.

## Verification

Verified locally on 2026-10-13:

- Python: **1,390 passed**, including 50 new audit regression cases.
- Swift: **201 passed, zero skipped**, including eight new regressions and all five
  bundled-runtime integration tests.
- Ruff and `git diff --check`: passed.
- Built the 1.21.6 wheel, source distribution, and PyInstaller runtime.
- Imported MCP documentation directly from the wheel outside the checkout and read both
  bundled resources through a real MCP stdio session. Both checks passed.
- Regression subprocesses exercise lock contention, crash evidence, lost-update protection,
  inherited output pipes, ignored termination signals, and stream cancellation.
- Reviewed the complete source, tests, packaging, and workflow diff. No production dependency
  was added. CI now runs Python 3.10/3.13 on Linux and bundled Swift checks on macOS; the release
  workflow requires those checks before packaging/publication.

Reproduce packaging checks with `scripts/build-bundled-tk.sh`, build the wheel, then run
`scripts/verify-package-resources.py --wheel PATH.whl --runtime PATH_TO_BUNDLED_TK`.
Run Swift integration tests with `TK_BUNDLED_RUNTIME=PATH_TO_BUNDLED_TK swift test
--package-path macos/TokenKickKit`.

The Hetzner installation and real provider request flows were not changed or used for
verification. Live authentication, provider compatibility, and the service upgrade need
an explicit deployment step. Stop the existing legacy daemon through its verified service
manager before starting this version; new process controls intentionally refuse unverified
legacy PID files. Preserve and inspect any uncertain action journal before forced recovery.

## Existing test changes

No tests were deleted or skipped. Three existing expectations were deliberately changed
because they encoded the unsafe behavior being repaired:

- `test_codex_predicted_due_status_replaces_failed_direct_refresh` became
  `test_codex_prediction_preserves_failed_direct_refresh`: a failed live reading stays unknown
  and stale instead of becoming actionable through a prediction.
- `test_codex_predicted_due_status_allows_stale_weekly_reset_ready` became
  `test_codex_prediction_rejects_stale_weekly_reset_ready`: an old prediction cannot authorize
  a request.
- `test_wake_kicks_stale_fresh_account_without_prompt_and_logs_wake_type` became
  `test_wake_refuses_stale_fresh_account_without_consuming_quota`: stale wake requests must not
  reach the provider.

The following existing Python tests retain their original behavioral assertions, with the
fixture/interface updates described beside each name:

| File and test | Update |
| --- | --- |
| `test_cli.py::test_status_summary_excludes_session_cooldown_accounts` | Freeze the observation clock as well as the fixture timestamp; dedicated regressions now cover old observations. |
| `test_cli.py::test_status_refresh_codex_direct_failure_flows_through_codexbar_fallback` | Freeze the observation clock as well as the fixture timestamp; dedicated regressions now cover old observations. |
| `test_cli.py::test_status_json_account_row_includes_agent_planning_fields` | Freeze the observation clock as well as the fixture timestamp; dedicated regressions now cover old observations. |
| `test_cli.py::test_run_refreshes_and_kicks_fresh_auto_account` | Assert the explicit automatic-action flag passed to the guarded executor. |
| `test_cli.py::test_run_session_auto_kick_requires_session_auto_enabled` | Assert the explicit automatic-action flag passed to the guarded executor. |
| `test_cli.py::test_kick_and_notify_uses_disabled_account_notification_config` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_kick_and_notify_fans_out_to_account_notification_backends` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_kick_and_notify_does_not_send_account_telegram_when_backend_disabled` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_kick_and_notify_non_daemon_codex_pending_prints_confirmation_later` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_cli.py::test_codex_phantom_kick_stays_unconfirmed_when_exec_reports_tokens` | Provide the verified-phantom prerequisite omitted by the old target-selection mock; token evidence still cannot imply confirmation. |
| `test_cli.py::test_kick_and_notify_notifies_only_final_codex_retry_result` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_kick_and_notify_does_not_retry_codex_hard_failure` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_daemon_deferred_codex_session_records_pending_confirmation_state` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_daemon_deferred_codex_phantom_records_pending_confirmation_state` | Exercise the authorized execution layer for notification/retry/confirmation behavior; new action-boundary tests separately enforce safety and ownership. |
| `test_cli.py::test_status_json_always_includes_freshness_fields` | Freeze the observation clock as well as the fixture timestamp; dedicated regressions now cover old observations. |
| `test_cli.py::test_daemon_background_starts_process_and_writes_pid` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_cli.py::test_daemon_stop_kills_pid_and_removes_pidfile` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_cli.py::test_daemon_stop_keeps_pidfile_when_process_does_not_exit` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_cli.py::test_daemon_restart_stops_running_and_starts_new_background` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_cli.py::test_daemon_restart_starts_when_not_running` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_app_mode.py::test_telegram_remote_background_starts_process_and_writes_pid` | Model an OS creation identity and child-owned startup announcement instead of an unverified legacy PID. |
| `test_app_mode.py::test_kick_json_attempted_confirmed` | Mock accepts the new force/stale-consent arguments; JSON outcome assertions remain unchanged. |
| `test_app_mode.py::test_kick_json_attempted_unconfirmed` | Mock accepts the new force/stale-consent arguments; JSON outcome assertions remain unchanged. |
| `test_app_mode.py::test_kick_json_attempted_failed` | Mock accepts the new force/stale-consent arguments; JSON outcome assertions remain unchanged. |
| `test_app_mode.py::test_kick_json_stale_with_yes_proceeds` | Mock accepts the new force/stale-consent arguments; JSON outcome assertions remain unchanged. |

Additional fixture and integration updates:

- `test_mcp_core.py::_fake_tk` supplies a plan fingerprint and build-time clock for the
  state-bound apply contract. Existing token, confirmation, and argument checks remain.
  `test_plan_apply_requires_preview_token_and_exact_arguments` additionally verifies that
  the stored fingerprint and original build-time clock reach the apply subprocess.
- `PlannerScheduleTests.swift::StubPlannerService` accepts the fingerprint/build-time arguments,
  supplies preview fingerprints, and can delay previews to exercise completion ordering.
  `testApplyConfirmationAndSuccessRefresh` additionally checks the exact fingerprint and
  build-time clock delivered to the service. Existing planner assertions remain; three new tests
  cover changed inputs, old completions,
  and missing preview identity.
- `BundledRuntimeIntegrationTests.swift::testSetupStreamThroughBundledRuntime` now selects an
  explicit fake Antigravity executable and checks the one synthetic saved account with auto-kick
  disabled. The old assumption that hiding PATH prevented discovery was incorrect because the
  provider detector also searches standard locations and the passwd home. Setup event and exit
  assertions remain. The other four bundled integration tests are unchanged.

## Release boundary

Version metadata is 1.21.6. The repair branch is suitable for review and subsequent server
validation. A numeric tag push publishes to PyPI, so publication and the Hetzner upgrade remain
separate from this local repair/verification batch.
