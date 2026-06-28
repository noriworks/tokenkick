# Changelog

## v1.1.0

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
