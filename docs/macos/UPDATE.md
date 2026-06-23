# Updating TokenKick.app

TokenKick.app and terminal `tk` installs are updated differently.

## Update the macOS App

1. Quit `TokenKick.app`.
2. Open the new `TokenKick-<version>.dmg`.
3. Drag the new `TokenKick.app` onto `Applications`.
4. Choose Replace if Finder asks.
5. Open the app again.

If the daemon was enabled, TokenKick should detect the app/runtime path change
and offer LaunchAgent repair. Repair keeps the shared `~/.tokenkick` state and
points the app-managed daemon at the new bundled runtime.

## Update the Terminal CLI

If you also use a `pipx` CLI install, update it separately:

```bash
pipx upgrade tokenkick
tk update
```

These commands do not replace `TokenKick.app`. They only update the terminal
runtime and daemon managed from the CLI.

## After Updating

Open the app and check:

- Status loads.
- Daemon ownership is app-managed, or the app clearly reports terminal-managed.
- Diagnostics show the bundled runtime version matching the app version.
- Provider CLI diagnostics still find `codex` and `claude` if configured.

If the app reports a runtime path mismatch, use the Daemon screen repair action.
