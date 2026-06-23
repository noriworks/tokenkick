# Installing TokenKick.app

TokenKick for macOS is distributed as a beta `.dmg` containing `TokenKick.app`
and an Applications shortcut.

Beta DMGs are attached to
[GitHub Releases](https://github.com/noriworks/tokenkick/releases) when
available. The CLI install remains the recommended first-time path:

```bash
pipx install tokenkick
```

Non-notarized beta DMGs may show a macOS security warning.

## Install

1. Open the downloaded `TokenKick-<version>.dmg`.
2. Drag `TokenKick.app` onto `Applications`.
3. Open `TokenKick.app` from `/Applications`.
4. If macOS asks for confirmation, choose Open.

The app uses the bundled `tk` runtime inside:

```text
TokenKick.app/Contents/Resources/tokenkick/tk
```

It does not require a separate `pipx` install for app workflows. A terminal
`pipx` install can still exist for CLI use, but it is updated separately.

## First Launch

On first launch, TokenKick checks:

- bundled `tk` runtime version
- daemon ownership and LaunchAgent status
- provider CLI discovery for `codex` and `claude`
- access to the shared `~/.tokenkick` state directory

If the app reports that `codex` or `claude` is missing from the Finder
environment, use the app diagnostics to inspect PATH. GUI apps do not inherit
your interactive shell PATH automatically.

## Daemon Ownership

TokenKick.app can install and manage its own LaunchAgent. If a terminal or
`pipx` daemon is already running, the app should report that ownership mismatch
instead of taking over silently.

Takeover is always explicit. The default confirmation is No.

## Local Beta Builds

Local beta DMGs may be ad-hoc signed and not notarized. Public DMGs should be
Developer ID signed, notarized, stapled, and verified with Gatekeeper before
distribution.
