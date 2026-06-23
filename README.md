# TokenKick

**Reset? Go.**

TokenKick is a local CLI for developers juggling multiple AI coding accounts.
It tracks quota reset windows, shows which account needs attention next, and
can optionally send a tiny provider request to anchor a newly reset window.

## Quick Start

```bash
pipx install tokenkick
tk
```

The native Mac app is a beta desktop surface for the same local runtime. When
available, beta DMGs are attached to
[GitHub Releases](https://github.com/noriworks/tokenkick/releases). The CLI is
the recommended install path for first-time users; non-notarized beta DMGs may
show a macOS security warning.

On first run, `tk` opens the TUI and starts with setup when no accounts are
saved yet. Setup discovers accounts and saves them with auto-kick off by
default. If you later enable auto-kick for a provider, TokenKick shows a
provider-specific risk notice and requires you to type `ENABLE` before saving
the acknowledgment. Interactive setup also offers notifications, daemon start,
and short schedule/Codex strategy info.

For a manual CLI setup:

```bash
tk setup
tk status --refresh
tk auto enable "<label>"
tk notify --ntfy tokenkick-yourname
tk daemon --background
```

Auto-kick sends minimal provider requests automatically on a schedule. Provider
terms and any account consequences are your responsibility; use it only if you
accept that risk.

For scripts and automation, use explicit commands:

```bash
tk status
tk status --refresh
tk remote telegram --background
tk plan --work-window 18:30-23:30 --json-output
tk history --verbose
tk doctor
```

## Demo

![Synthetic TokenKick status demo](docs/assets/readme-status-demo.svg)

The demo output above is generated from synthetic data with:

```bash
.venv/bin/python scripts/render-readme-demo.py
```

## Documentation

- [How TokenKick works](docs/HOW_TOKENKICK_WORKS.md)
- [Commands](docs/TOKENKICK_COMMANDS.md)
- [Magic setup (advanced)](docs/MAGIC_SETUP.md)
- [Agent playbook](docs/AGENT_PLAYBOOK.md)
- [Providers](docs/PROVIDERS.md)
- [Changelog](docs/CHANGELOG.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Provider Support

Codex and Claude are kickable in this release. Gemini and Antigravity are
monitor-only. Other providers are unsupported until their status shape and safe
kick behavior are verified.

TokenKick does not increase quota, bypass limits, or evade provider
restrictions. It helps you notice and act on quota windows you already have.

Codex-Spark is detected as a separate Codex quota bucket when the provider
exposes it. TokenKick then shows it as a sibling account such as
`codex-spark (...)` with its own session/weekly window. It does not infer Spark
access from your subscription tier. `tk plan` skips Spark until you set an
explicit `usable_session_minutes` for that account, because the rough placeholder
is not enough evidence for orchestration decisions.

## Disclaimer

TokenKick is an independent, open-source project. It is not affiliated with,
endorsed by, or sponsored by OpenAI, Anthropic, Google, or any other provider.
"Codex", "ChatGPT", "Claude", and "Gemini" are trademarks of their respective
owners, used here only to describe compatibility.

TokenKick does not increase your quota, bypass rate limits, or evade any
provider restriction. It helps you track your own reset windows and, optionally,
send a minimal request through a provider's official CLI to anchor a window you
have already paid for.

**Automated or scheduled kicking may violate a provider's Terms of Service.**
Some providers restrict automated or scripted access to their consumer products.
Enabling auto-kick is your decision and your responsibility. You alone are
responsible for how you use TokenKick with your accounts and for complying with
each provider's terms. Use at your own risk.

TokenKick is provided "as is", without warranty of any kind. The authors are
not liable for any consequence arising from its use, including account
restriction, suspension, or loss of access.
