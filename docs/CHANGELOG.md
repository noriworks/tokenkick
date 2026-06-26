# Changelog

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
