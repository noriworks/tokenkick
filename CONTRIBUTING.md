# Contributing

Thanks for taking the time to improve TokenKick.

## Before You Start

- Keep TokenKick local-first and explicit: no telemetry, hidden network calls,
  or automatic provider requests without a saved user setting.
- Auto-kick must remain off by default. Enabling it must go through the
  consent gate for kickable providers.
- Do not include real account labels, email addresses, local paths, tokens,
  provider homes, `.jsonl` logs, or config files in commits, fixtures, issues,
  or screenshots.
- Keep public examples synthetic.

## Development

Install the project in a virtual environment or with your preferred local Python
workflow, then run the checks before submitting changes:

```bash
ruff check .
pytest
```

For macOS app changes, also run the Swift tests from the repo root when the
bundled runtime is available:

```bash
env TK_BUNDLED_RUNTIME=/path/to/tk swift test --package-path macos/TokenKickKit
```

## Pull Requests

- Keep changes focused and describe the user-visible behavior.
- Add or update tests when behavior changes.
- Update docs when commands, setup behavior, provider support, or safety
  defaults change.
- Avoid broad refactors unless they are necessary for the fix.
- Do not bump versions, create tags, publish packages, or change release
  metadata in ordinary contribution PRs.

## Provider And Terms Safety

TokenKick does not increase quota, bypass rate limits, or evade provider
restrictions. It tracks windows users already have and can optionally send a
minimal provider-native request after explicit consent. Changes that alter
kicking, scheduling, setup defaults, daemon behavior, or provider automation
need extra care and tests.
