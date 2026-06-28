# Changelog

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
