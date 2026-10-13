# Provider compatibility follow-up — 1.21.9

Release smoke checks found two current CLI incompatibilities: Claude Code 2.1.296
disables `/usage` when passed `--disable-slash-commands`, and Antigravity CLI 1.3.3
requires a session CSRF token for its loopback quota API.

Repair plan and acceptance criteria:

- Enable built-in commands only for Claude usage capture, retain all cost/tool
  controls, and stop immediately if the usage command is unavailable.
- Launch an owned Antigravity probe with a cryptographically random CSRF token;
  reuse a matching session only when its explicit token is available. Never
  expose tokens in diagnostics or session representations, stop only owned
  processes, and preserve authenticated HTTPS and quota identity validation.
- Verify both Claude transports, authenticated quota requests, independent
  session tokens, preserved interactive processes, and existing fail-closed tests.
- Run the full Python/lint/package and macOS checks before publication. After
  upgrading, require fresh quota reads from all five remaining server accounts.

Existing test modifications:

- `test_find_running_antigravity_cli_session_reuses_matching_binary` now supplies
  an explicit synthetic CSRF token because authenticated reuse requires it; also
  verifies the token is excluded from the session representation.
- `test_fetch_antigravity_cli_https_uses_quota_summary_first` now covers legacy
  empty and authenticated tokens, HTTPS transport, and diagnostic redaction.
- `test_all_claude_probe_paths_apply_cost_controls` additionally distinguishes
  usage commands from generation, where slash commands remain disabled.

New regressions cover private-session fallback, unique CSRF tokens, both real
subprocess transports against a fake Claude CLI, and terminal unknown-command errors.
No existing assertion was removed to accommodate a failure.

The live candidate check also exposed a startup race: the local API can return an
active account before its quota buckets load. Two additional tests cover waiting
for complete buckets and failing closed if they never become available.

Pre-publication results: 1,451 Python tests and 201 Swift tests passed; Ruff,
sdist/wheel builds, bundled runtime startup, and MCP package-resource checks
passed. Candidate code on Linux returned fresh Claude usage and all four live
Antigravity quota windows without errors. Public app distribution remains an
explicitly non-notarized, ad-hoc-signed macOS arm64 beta.

## Antigravity generation follow-up — 1.21.10

The approved native diagnostic returned: `--effort is not supported for model`
when an explicit catalog `(Low)` model was combined with `--effort low`.
Remove that redundant flag; the existing selector still requires a Low variant.

Existing test change: `test_antigravity_probe_request_overrides_interactive_effort`
was renamed to `test_antigravity_low_model_avoids_incompatible_effort_flag` and
parameterized for Gemini and Claude. Its old assertion incorrectly required a
flag rejected by the native CLI. The test now models that rejection and checks
the selected Low model, absence of the incompatible flag, plan mode, bounded
prompt, and lack of permission bypass. No other test was changed for this patch.

The single approved additional diagnostic was used to capture this error. No
further native generation was authorized; corrected-command acceptance and
Antigravity quota anchoring remain unverified live.
