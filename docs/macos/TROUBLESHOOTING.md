# TokenKick.app Troubleshooting

## macOS Says the App Is Damaged or Cannot Be Opened

For public builds, verify that the downloaded DMG is Developer ID signed,
notarized, and stapled. For local beta builds, Gatekeeper warnings can appear
because the app is ad-hoc signed.

Useful checks:

```bash
spctl --assess --type execute --verbose /Applications/TokenKick.app
codesign --verify --strict --verbose=4 /Applications/TokenKick.app
```

## The App Uses the Wrong `tk`

TokenKick.app should use its bundled runtime:

```text
/Applications/TokenKick.app/Contents/Resources/tokenkick/tk
```

The Diagnostics screen shows the runtime path. If it points to `pipx` or another
terminal install, reinstall the app or repair the LaunchAgent from the Daemon
screen.

## Daemon Ownership Looks Wrong

The app protects against duplicate daemons. If it detects a terminal-managed
daemon, stale pidfile, version mismatch, or runtime path mismatch, it should
show that state and offer repair or takeover where safe.

Takeover is explicit and defaults to No.

## Provider CLI Is Missing From the App

Finder-launched apps do not inherit your shell startup files. If `codex` or
`claude` works in Terminal but not in TokenKick.app, check the app Diagnostics
environment section and add the provider CLI location to the app-managed PATH
configuration.

Common locations include:

```text
/opt/homebrew/bin
/usr/local/bin
~/.local/bin
```

## LaunchAgent Does Not Start

Open the Daemon screen and use status/repair. The LaunchAgent helper should live
under:

```text
~/Library/Application Support/TokenKick/
```

If the app was moved or replaced, repair rewrites the helper so launchd resolves
the current app runtime instead of a stale bundle path.

## Reset, History, Or Account State Looks Missing

TokenKick.app and terminal `tk` share:

```text
~/.tokenkick
```

The app should not write separate account state into the app bundle. If state
looks wrong, compare the app Diagnostics snapshot with:

```bash
tk app snapshot
tk status --refresh
```
