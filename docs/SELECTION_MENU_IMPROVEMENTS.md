# Consistent terminal selection menus

## Plan and acceptance criteria

- Single-choice menus keep Enter to choose and show a concise navigation hint.
  Auto-kick adds explicit enable/disable-one actions for the common single-account case.
- All multi-account pickers use square checkboxes, a persistent Space/Enter/Esc
  hint and a live selection count. Enter reviews the marked set; it never toggles
  the highlighted row. Esc discards the selection. Back is not a checkbox.
- Empty selections never mutate settings. Empty eligible lists return cleanly.
- Account choices retain their saved identifiers but drop source/kickability noise;
  auto-kick choices show the current switches and preserve hidden-account markers.
- Visibility, auto-kick, notification and orchestration cancellation flows show
  named targets and exact proposed changes before a separate confirmation, with
  No focused by default. Provider consent requirements remain intact.
- Verify real keyboard input against the installed prompt library as well as
  flow-level regression tests for cancel, empty, review and apply paths. Run the
  full Python suite, lint and macOS integration suite. No production changes,
  CTP, publication or rebuild of the distributable app.

## Existing-test changes

New regression coverage is in `test_selection_menus.py`, including real
prompt-toolkit input and rendered frames rather than only mocked selections.
The following existing tests in `test_cli.py` changed:

- `test_interactive_prompt_wrappers_map_escape_skip_to_back`: the prompt double
  now provides the public layout and selected-choice APIs used by the live count.
  Its Escape mapping and keybinding assertions are unchanged.
- `test_interactive_auto_menu_can_enable_all_accounts`: confirmation now expects
  the safe default (`False`); all persistence assertions remain.
- `test_interactive_notifications_menu_toggles_account_notifications`: likewise
  expects the safe confirmation default while retaining its account-toggle checks.

No tests were removed or skipped. Earlier test changes in this batch are recorded
in `CODEX_LIMIT_DETECTION.md` and `NOTIFICATION_MENU_IMPROVEMENTS.md`.

## Verification (2026-10-13)

- **40 new regression cases** cover real Space/Enter/Esc input, rendered live
  counts, square markers, selection identity, concise account labels, exact
  switch transitions, single-account actions and all five multi-account flows.
- Full Python suite: **1,665 passed**.
- macOS Swift suite against the development CLI: **202 passed**, no failures
  or skips. No distributable app rebuild.
- Repository-wide Ruff and `git diff --check` passed; scoped diff reviewed.

Implementation is in `tokenkick/interactive.py`, with new coverage in
`test_selection_menus.py`, the existing-test updates above and keyboard usage
documented in `TOKENKICK_COMMANDS.md`. Everything remains uncommitted in the
current batch. No provider requests, server settings, CTP or publishing changes.
