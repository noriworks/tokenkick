# Auto-kick control consistency

Implement one switch policy shared by CLI mutations, terminal confirmations and MCP previews.
Enabling Weekly or Session enables master and preserves the other window. Disabling a window
preserves master and the other window. Master pauses/resumes without changing window preferences.
No migration activates saved accounts. Stagger ownership and consent gates remain authoritative.

Acceptance criteria:

- Weekly-only configuration survives save/load and repeated pause/resume without session activation.
- Paused selected windows say "paused" across terminal and macOS; an empty selection explicitly says
  that no windows will run. A saved preference is never presented as active while master is off.
- MCP previews expose exact before/after switches; previews made under old semantics are rejected.
- Menu labels distinguish master pause/resume from individual window selection. Suggested commands
  enable the intended window explicitly.
- Account changes cannot resume a paused stagger group or send a generation request themselves.
- Regression tests cover all switch combinations, persisted CLI/MCP actions, consent, stale previews,
  terminal rendering and macOS presentation. Run Python tests, lint and Swift tests.

Keep this work local: no tag, push, publication, server change or installer release in this task.

## Verification

- Python: 2,001 tests passed; Ruff passed.
- Swift: 213 tests passed with the rebuilt bundled CLI, including a real process-boundary
  weekly-only enable/pause/resume test. An initial incremental build crashed with signal 11;
  a clean package rebuild passed the entire suite without changing or skipping tests.
- Added 64 Python regression cases: all 48 persisted switch transitions, repeated weekly-only
  pause/resume, empty-selection and paused labels, six MCP preview/apply cases, old-policy preview
  rejection, consent preservation, and three paused-stagger ownership cases.
- Added three Swift tests: all eight presentation combinations, paused stagger ownership, and
  the bundled-runtime pause/resume flow. No provider generation requests or live controls changed.

## Existing test changes

These expectations intentionally change with the approved control behavior; no tests were removed,
skipped or weakened. All other existing tests retain their original assertions.

In `test_cli.py`:

- `test_status_summary_suggests_session_auto_enable_when_manual_session_ready`: suggests the explicit
  session-enable command, which now also enables master.
- `test_auto_enable_and_disable_toggle_auto_kick`: uses weekly-only preferences and verifies master
  resume/pause preserves them.
- `test_auto_session_enable_and_disable_toggle_session_auto_kick`: enabling Session also enables master.
- `test_auto_weekly_enable_and_disable_toggle_weekly_auto_kick`: enabling Weekly also enables master.
- `test_interactive_auto_menu_saves_auto_kick`: master resume preserves both unselected windows.
- `test_interactive_auto_menu_disable_selected_includes_hidden_accounts`: selected-account pause
  preserves saved window choices, including hidden accounts.
- `test_interactive_auto_menu_disable_all_includes_hidden_accounts`: bulk pause preserves both windows.
- `test_interactive_auto_menu_enable_all_skips_unusable_accounts`: bulk resume preserves unselected
  windows and continues to skip unusable accounts.

In `test_selection_menus.py`:

- `test_account_labels_are_concise_keep_identity_and_explain_families`: expects enabled/paused/disabled
  window labels rather than raw on/off preferences.
- `test_auto_review_describes_exact_switch_transitions`: checks the new transitions for all six actions
  and the explicit paused explanation.

In `test_account_presentation.py`:

- `test_settings_table_and_json_distinguish_all_auto_switches`: expects paused master/preferences and
  enabled/disabled window labels while retaining JSON checks.
