# Antigravity automation and MCP controls

## Implementation plan and acceptance criteria

- Represent Gemini and Claude/GPT quota families as separate accounts, preserving
  existing account settings and keeping newly introduced automation off. Migrate
  legacy aggregate CLI accounts once, with distinct stable identities.
- Use complete, fresh, identity-checked native quota data. Select the live
  catalog's cheapest supported Low model and send a bounded minimal request.
  Confirm a reset anchor from provider clocks, not merely a successful exit or
  consumed quota. Suppress repeated attempts after uncertain outcomes.
- Expose auto status and preview/apply controls through MCP, binding consent,
  target and scope to a one-use token. Enabling requires quota acknowledgement;
  disabling remains available without provider consent.
- Add regressions for migration, discovery, family isolation, model selection,
  freshness, ambiguous outcomes, and MCP consent/replay protection. Exercise the
  actual stdio transport using Hermes's MCP client.
- Run Python and macOS checks, review the full diff, publish a minor release,
  retain a protected and restore-tested server backup, then update and verify the
  server and non-notarized macOS beta.

## Verification

Release 1.22.0, verified on 2026-10-13:

- 1,512 Python tests pass (60 new parametrized cases), including real MCP stdio
  enable/disable operations against an isolated HOME and replay rejection.
- 202 macOS tests pass, including bundled runtime setup and a new test that
  keeps Antigravity Gemini/Claude consent targets distinct.
- Ruff and diff whitespace checks pass. Wheel and bundled-runtime MCP resources
  are verified through the actual stdio transport.
- Hermes's installed native MCP client discovered 32 tools and performed six
  enable/disable roundtrips across two Antigravity families and Claude using
  isolated test state. Replayed mutation tokens were rejected. No provider
  generation was performed by these control tests.
- Hetzner's native Gemini Flash Low and Claude Sonnet Low requests exited
  successfully. Gemini's punctuated acknowledgement was also checked through
  structured native output. The windows were already active: these checks do
  **not** prove new reset anchors. Missing clock movement remains unconfirmed
  and blocks automatic retries. No repeated requests were sent to move clocks.
- A full protected server backup was extracted and 68,364 regular files were
  hash-verified; the restored runtime reported 1.21.10. Native AGY credentials
  received a separate protected restore check. SHA-256-matching off-host copies
  are retained in the operator's private release records.

## Existing test changes

No tests were removed or skipped. These existing expectations changed because
Antigravity now exposes two families and is supported through the native CLI:

- `test_discover_direct_accounts_reads_antigravity_cli_identity` and
  `test_discover_direct_accounts_adds_antigravity_cli_without_identity` now
  require both family identities instead of one aggregate account.
- `test_kick_enable_rejects_non_kickable_accounts` expects the updated supported
  provider message; its rejection/no-save assertions remain.
- The `_merge_existing_account` and `_merge_discovered_account_fixture` fixtures
  in `test_models.py` include differing family values, so the existing exhaustive
  field-ownership tests verify the new field instead of ignoring it.
- Swift `testSetupStreamThroughBundledRuntime` requires two distinct, disabled
  family accounts instead of one aggregate account. It retains the process,
  successful setup, saved config, and exit-status checks.

Production installation and macOS artifact checksums are retained in private
release records; no credentials or account-specific data are published here.
