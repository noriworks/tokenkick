# Notification settings and test reporting

## Plan and acceptance criteria

- Show saved destinations, masked token presence, effective push switches,
  notification volume and the local Telegram listener state on menu entry.
- Provide separate controls to edit saved credentials, enable with existing
  credentials, disable while retaining them, and test each destination. Editing
  credentials preserves push and remote-command settings. Tokens are never echoed.
- Name the actual destinations targeted by the combined test. Individual tests
  explain that they can send while push is disabled, without enabling it.
- Return a result for each backend. Any selected backend failure makes the test
  fail; disabled backends are reported as skipped. Missing credentials and delivery
  failures are distinct, and one failure must not stop the other backend's test.
- Show inherited/custom/disabled account routes beside their effective destination,
  including routes blocked by globally disabled backends; keep editing nearby.
- Explain that errors/checks mode suppresses routine events but never explicit tests,
  and that Telegram remote commands are independent of notification switches.
- Add regression coverage for persistence, cancellation, secret redaction, menu
  navigation, routing and partial delivery. Verify Python, lint and macOS integration
  with mocked notification delivery. No real notification, production configuration,
  release, commit, tag or push changes.

## Implemented behavior

The overview is also available through read-only `tk notify` and
`tk notify --json-output`. New `--enable-backend`, `--save-ntfy` and
`--save-telegram` controls reuse or edit saved settings without conflating
credential changes with enabling push. Existing configure-and-enable commands
remain supported. Notification test JSON now includes per-backend `results`;
the combined `delivered` value requires all selected destinations to succeed.
Human-readable test failures now return exit status 1 as well.
The Telegram remote menu shares the same credential editor and test flow;
editing credentials there no longer silently disables existing push routes.
Its separate remote-only action remains available.

## Existing-test changes

New regression cases are in `test_notification_settings.py`. Existing assertions
were updated only where the intended interface or implementation boundary changed:

- `test_cli.py::test_interactive_notifications_menu_saves_ntfy` follows the new
  topic submenu and separate enable action. It checks that saving a topic leaves
  push disabled, enabling then turns it on, and both retain notification volume.
- `test_cli.py::test_interactive_remote_telegram_configure_is_remote_only` became
  `test_interactive_remote_telegram_configure_preserves_push_settings`. It now
  verifies that the shared editor updates credentials while preserving enabled
  ntfy/Telegram routes and remote startup. Disabling push is a separate action.
- `test_cli.py::test_interactive_remote_telegram_test_targets_telegram_only`
  retains its Telegram-only assertion and includes the shared test flow's new
  explicitly unset credential-edit options.
- `test_app_mode.py::test_notify_json_usage_error_without_backend` became
  `test_notify_json_overview_without_backend`, because a bare `notify` command
  now provides the read-only overview instead of a usage error.
- `test_app_mode.py::test_notify_test_json_failure_when_disabled`,
  `test_notify_test_json_selected_backend_only`, and
  `test_notify_test_json_selected_backend_reports_missing_credentials` retain
  their success/failure assertions and additionally require the exact new
  per-backend result data.
- `test_app_mode.py::test_notify_test_json_success` now seeds enabled ntfy
  credentials and mocks the delivery boundary (`notify_test`), instead of the
  aggregate helper that previously hid individual failures.

No tests were removed or skipped. Earlier changes in this unpublished batch are
documented separately in `CODEX_LIMIT_DETECTION.md`.

## Verification (2026-10-13)

- 39 new notification regression cases passed.
- Full Python suite: **1,625 passed**.
- macOS Swift suite against the development CLI: **202 passed**, no failures
  or skips. No distributable app rebuild.
- Repository-wide Ruff and `git diff --check` passed; final scoped diff reviewed.
- Delivery was mocked throughout the new notification regressions. No real
  notifications were sent and no server or installed-app settings were changed.

Implementation files: `tokenkick/cli.py` and `tokenkick/interactive.py`.
This work and the preceding fixes remain uncommitted in the current batch;
there was no CTP, publishing, version bump or deployment.
