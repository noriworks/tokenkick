# Changelog

## Unreleased

- Correct adopted Antigravity anchors when fresh paired native reads prove the window is
  unused again. Persist a safe new start, retain request cooldowns and peer spacing, and
  continue recurring staggering from the confirmed replacement anchor. Terminal/MCP status
  explains pending and completed corrections; synthetic observations cannot authorize them.

- Confirm Codex weekly-only kicks from a matching seven-day native reset deadline that remains
  fixed across fresh observations. Reconcile pending history without another request, distinguish
  weekly evidence from session confirmation, and reject stale, moving or ambiguous reset clocks.

- Auto-kick window enablement now resumes master. Master pause/resume preserves saved weekly/session
  choices, including weekly-only setups. Paused preferences and empty selections are explicit in
  terminal and macOS UI; MCP previews show exact changes and reject previews from the old policy.

## v1.23.3

- Recover scheduled Antigravity stagger slots after inconclusive native reset
  observations with up to three bounded read retries and a five-minute deadline.
  Persist the retry budget across restarts, preserve all dispatch/quota guards,
  and realign later slots from the confirmed start. Uncertain requests still
  require explicit recovery; missed outage slots are not replayed.
- Expose separate reset-recheck deadlines and recovered/skipped slot diagnostics
  in terminal and MCP status. Validate both families before retrying evidence.
- Restore unattended Claude usage refresh in daemon and detached helper passes.
- Preserve failed direct-probe metadata through discovery so rate-limit backoff
  and diagnostics survive cached-data fallback.
- Honor saved per-account monitoring opt-outs before discovery performs a read.
- Add four discovery/cache integration regression cases; existing tests unchanged.

## v1.23.2

- Make the kick/cache regression deterministic across second boundaries using
  a controlled clock, retaining its zero-age assertion and checking later aging.
  The 1.23.1 tagged verification caught this race before PyPI publication.
- Includes the 1.23.0 feature batch and 1.23.1 background-refresh fix.

## v1.23.1

- Fix Python-installed background refresh, which invoked a module without an
  executable entry point. Add `python -m tokenkick` and use it for child refreshes.
- Correct release-test portability: use an explicit Berlin timezone for the
  Berlin rendering fixture and launch the real CLI through the active Python
  interpreter instead of assuming a repository-local virtual environment.
- Includes the 1.23.0 feature batch; its Linux verification gate prevented PyPI
  publication. Existing test assertions are unchanged.

## v1.23.0

- Add persistent daemon-owned Antigravity Gemini/Claude orchestration with a
  2h30m target stagger, explicit enable/pause/resume/recovery controls, MCP
  previews and clear terminal/macOS ownership and next-action status.
- Detect Codex plan and quota-window capabilities from provider evidence, parse
  weekly-only windows correctly, and offer a manual session-limit override.
- Improve notification configuration, per-backend test results, existing-value
  visibility and consistent multi-select menus. Explain Claude monitoring
  failures accurately and back off rate-limited usage reads.
- Harden account identity checks, approved-action binding, dispatch evidence,
  uncertain-outcome protection, pending-action transactions and migrations.
  Preserve configuration extensions while rejecting malformed safety controls.
- Fix stale-source reconciliation, background refresh locks, frozen-runtime
  refresh and process identity checks. Isolate recurring-controller failures.
- Fix macOS account-selection and refresh races, immutable confirmation targets,
  launch-agent ownership/rollback, notification decoding and warning visibility.
- Harden fixture isolation, public exports, packaging cleanup and exact-artifact
  release verification. Add systemd installation guidance.

## v1.21.10

- Use Antigravity's catalog-selected Low model variant without a redundant
  `--effort low` flag, which CLI 1.3.3 rejects for those explicit variants.
  Dynamic Low-only selection and plan-mode diagnostics remain in force.

## v1.21.9

- Keep Claude's built-in `/usage` command enabled for quota reads while preserving
  Haiku, low effort, disabled thinking, and empty tool/MCP settings. Report an
  unavailable usage command immediately instead of repeatedly submitting it.
- Authenticate private Antigravity quota sessions with a fresh CSRF token. Leave
  interactive sessions without an available token untouched; retain HTTPS and
  identity/completeness checks before accepting quota data. Wait for all four
  quota buckets during CLI startup instead of accepting an empty account status.

## v1.21.8

- Select Codex kick models from each account's live catalog, preferring GPT-6 Luna
  with the lowest supported economical effort and standard service tier. Stop on
  missing models or catalog failures instead of falling back to expensive defaults.
- Use Haiku, low effort, and thinking/fast mode off for Claude kicks and probes.
- Select current Low-effort Flash or Claude Haiku/Sonnet models for Antigravity
  diagnostics. Antigravity automatic kicking remains disabled pending validation.
- Remove retired Spark config entries and stale model pins, preserving credentials
  and history, and prevent rediscovery or generation against the retired bucket.

## v1.21.7

- Bound the existing MCP dependency to its compatible v1 API so fresh installations cannot
  unexpectedly load the incompatible v2 server/client interfaces.
- Honor explicit IANA timezones in Claude reset times, including daylight-saving changes,
  instead of interpreting provider timestamps in the server's local timezone.
- Make doctor and bundled setup tests independent of provider tools installed on the test host.

## v1.21.6

- Preserve account identity in partial caches and consistently mark expired observations stale.
  Failed reads and predicted resets cannot authorize automatic kicks.
- Merge independent configuration edits, reject conflicting edits, and serialize statistics,
  pending-plan updates, daemon lifetimes, and per-account provider actions. Interrupted actions
  require a refreshed status/history review before explicit `--force` recovery.
- Verify process creation identity before stopping a daemon or Telegram listener. Legacy PID
  files remain readable; stop an existing legacy process through its verified service manager
  before upgrading and restarting it.
- Apply the common quota/freshness policy to manual and scheduled kicks, drop expired work,
  and preserve active overnight schedules across weekday/weekend boundaries.
- Accept independent optional Codex quota windows and isolate malformed historical fallbacks.
- Preserve unrelated TOML during MCP setup, ship MCP documentation in wheels and app runtimes,
  and require exports to use a new directory that does not overlap the source repository.
- Retry Telegram transport failures with bounded backoff and preserve valid HTML on truncation.
- Bound macOS process cancellation and pipe draining, including descendants. Planner apply now
  requires unchanged preview inputs and state; CLI clients can pass `--expected-plan` with the
  preview's `plan_fingerprint`. MCP and the macOS app do this automatically.

## v1.21.5

- Allow hidden Codex and Claude accounts, including Codex Spark surfaces, to
  be selected and disabled from the Auto-kick TUI.
- Make the bulk disable action cover every kickable account while keeping
  auto-kick enable actions limited to visible accounts.

## v1.21.4

- Make Claude session auto-kicks send a real tiny completion before checking
  `/usage`, instead of treating the read-only usage panel as the kick itself.
- Force the post-kick `/usage` check past the normal five-minute probe cache
  and retain valid completion evidence while the provider reset clock catches
  up.

## v1.21.3

- Add notification volume control to the interactive TUI, so `errors` versus
  `all` notifications can be changed from `Configure -> Notifications`.

## v1.21.2

- Remove Python 3.15 deprecation warnings from Claude `/usage` reset-date
  parsing by attaching candidate years before parsing month/day reset strings.

## v1.21.1

- Keep quota-constrained `TokenKick — Check` notifications enabled under the
  `errors` notification policy.
- Make the reset-log CLI test independent of the current calendar date.

## v1.21.0

- Add notification policy control with `tk notify --policy errors` to suppress
  routine success notifications while still sending errors and check/warning
  notifications.
- Shorten Codex pending-confirmation notification wording so routine accepted
  session kicks are easier to scan.

## v1.20.2

- Prefer Claude Code's piped `/usage` output when it contains complete quota
  windows, avoiding stale cached Claude status when the interactive PTY usage
  panel hangs on loading.

## v1.20.1

- Allow `tk antigravity probe-kick` to run for anonymous CLI-discovered
  Antigravity accounts when complete local quota buckets are available, while
  still failing closed for configured identity mismatches.

## v1.20.0

- Add hidden manual Antigravity evidence probes with `tk antigravity
  probe-kick --family gemini` and `--family claude-gpt`. The command reads all
  four quota buckets before and after one explicit `agy --print` request, then
  stores sanitized local evidence.
- Keep Antigravity monitor-only: `tk kick antigravity`, auto-kick, schedules,
  orchestration, and pending-kick paths still reject or skip Antigravity.
- Preserve Antigravity CLI identity metadata during HTTPS quota reads so probe
  evidence stays tied to the verified CLI account.

## v1.19.0

- Expand Antigravity status rows into Gemini and Claude/GPT quota targets, each
  showing its 5-hour and weekly bucket instead of compressing all four buckets
  into one account summary.
- Add JSON `quota_targets` for Antigravity rows while preserving raw
  `quota_windows` and monitor-only safety.

## v1.18.9

- Keep Antigravity monitor-only refresh failures out of the automatic-kick
  blocking banner.
- Stop labeling Antigravity quota buckets as a kickable "Session ready" state.

## v1.18.8

- Read Antigravity rich quota buckets from the `agy` CLI HTTPS quota endpoint
  before falling back to local app or CodexBar compatibility data.
- Add Antigravity quota-summary parsing for Gemini 5-hour, Gemini weekly,
  Claude/GPT 5-hour, and Claude/GPT weekly windows.
- Keep Antigravity monitor-only while preserving kick and auto-kick rejection
  paths.

## v1.18.7

- Broaden Antigravity CLI discovery to use user-home markers as well as the
  `agy` executable, and check common home sources when setup runs with a
  reduced environment.

## v1.18.6

- Discover Antigravity as a monitor-only account when the `agy` CLI is present
  even if the CLI does not expose a readable account email file.

## v1.18.5

- Find Antigravity CLI installs in common user-local paths such as
  `~/.local/bin/agy` even when TokenKick's subprocess PATH omits them.
- Fall back to the Google OAuth `id_token` email when Antigravity CLI identity
  is not present in `~/.gemini/google_accounts.json`.

## v1.18.4

- Guard PyPI release publishing so version tags must point to commits already
  reachable from `main`.

## v1.18.3

- Refuse to attach local Antigravity API quota data to a CLI-discovered account
  unless the local API identity matches that CLI account.

## v1.18.2

- Show known Antigravity and Gemini rows as `Monitor only` in status tables
  instead of using kickable-account action text.

## v1.18.1

- Discover logged-in Antigravity CLI accounts directly from local Google
  account state, so Linux servers do not require CodexBar to show Antigravity
  as a monitor-only account.
- Prefer direct Antigravity quota data before CodexBar compatibility data, while
  still requiring complete named quota windows for rich bucket status.

## v1.18.0

- Add rich monitor-only Antigravity quota windows for Gemini and Claude/GPT
  5-hour and weekly limits when named bucket data is available.
- Keep Antigravity blocked from manual kicks, schedules, and auto-kick paths
  until a safe provider-native anchor request is verified.

## v1.0.7

- Link the agent playbook to the dedicated MCP documentation and summarize the
  MCP safety model for MCP-capable agents.
- Add docs coverage so the agent playbook keeps its MCP pointer and
  preview-token guidance.

## v1.0.6

- Point the README at a new generated plan demo SVG filename so GitHub's image
  proxy cannot reuse the stale pre-branding asset.

## v1.0.5

- Add a README cache-busting version query for the plan demo SVG so GitHub's
  image proxy refreshes the corrected branding.

## v1.0.4

- Match the README plan demo's `TokenKick` header styling to the status demo's
  branded bold/green treatment.

## v1.0.3

- Fix README demo gallery rendering so CLI/TUI screenshots are full-width and
  readable on GitHub.
- Add explicit intrinsic dimensions to generated terminal demo SVGs so GitHub
  does not render them as tiny default-size SVG thumbnails.

## v1.0.2

- Expand the GitHub README with clearer product positioning, agent-safe command
  guidance, provider support notes, macOS app links, and a synthetic demo
  gallery.
- Add generated README demo assets for work-window planning and the beta macOS
  app, alongside the existing synthetic CLI status screenshot.
- Clarify the `llms.txt` entry point so agents start from the playbook and
  avoid undocumented scheduling or quota-consuming actions.

## v1.0.1

- Detect logged-out Claude CLI state with `claude auth status` before opening
  the `/usage` TUI, and tell users to run `claude auth login --claudeai` as the
  TokenKick user before refreshing status.
- Persist failed Claude `/usage` session touches into the status cache so the
  daemon stops rebuilding stale due-session auto-kick targets after a newer
  direct probe failure.

## v1.0.0

Initial public release of TokenKick.

- Ships TokenKick as a local-first CLI, interactive TUI, beta macOS app, and
  MCP surface for tracking AI coding quota windows.
- Documents the Mac app beta as a GitHub Releases DMG path, with the CLI kept
  as the recommended first-time install.
- Tracks Codex and Claude from local provider state where available, with
  local session and CodexBar compatibility fallbacks.
- Keeps auto-kick off by default; enabling Codex or Claude auto-kick requires
  explicit provider-specific risk consent.
- Supports manual kicks, daemon polling, smart schedules, pending kicks,
  reset calendar, history, doctor diagnostics, notifications, and read-only
  Telegram remote status.
- Provides MCP tools for cached status reads, plan previews, guarded schedule
  changes, and approved orchestration.
- Keeps Gemini and Antigravity monitor-only; other unverified providers remain
  unsupported unless documented otherwise.
- Includes README disclaimers, `SECURITY.md`, and `CONTRIBUTING.md`.
