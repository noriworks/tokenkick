# TokenKickKit

Swift foundation for the native TokenKick.app. Two libraries plus
executables:

- **TokenKickKit** — the app/core boundary (process runner, runtime
  locator, environment, JSON models, LaunchAgent manager).
- **TokenKickShell** — the native app shell:
  `SnapshotStore` (global refresh/degraded state), warning tier
  derivation, daemon chip/ownership presentation, popover/navigation/
  settings models, and the SwiftUI views (menu bar popover, main window
  with sidebar, Status + read-only Daemon screens, Settings).
- **TokenKick** — the `@main` app executable (menu bar + main window +
  Settings scenes). Run during development with:

  ```sh
  swift build && TK_APP_RUNTIME=$PWD/../../dist/tokenkick-runtime/tk ./.build/debug/TokenKick
  ```

- **tkapp-probe** — CLI proof that the bundled runtime answers through
  the process boundary.

TokenKickKit proves the app/core boundary:

- **TKRuntimeLocator** resolves the bundled `tk`
  (`Contents/Resources/tokenkick/tk`, or the `TK_APP_RUNTIME` override for
  development). It never consults `PATH`: an external `pipx tk` is
  informational only and is never executed by the app.
- **TKEnvironment** builds the subprocess environment for Finder-launched
  apps: preserves the inherited PATH, appends standard CLI locations
  (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, …) so provider
  discovery finds `codex`/`claude`, and always sets `TK_APP_MODE=1`.
- **TKProcessRunner** runs one `tk` invocation with timeout and
  cancellation; SIGTERM → grace → SIGKILL, always reaped.
- **TKEnvelope / TKSnapshotPayload / TKSetupStream** decode the app JSON
  contract (`schema_version`, `ok`, `error_code`, `message`, `warnings`,
  `payload`) and the `tk app setup` JSON-lines stream.
- **tkapp-probe** is the end-to-end proof executable.

## Dev workflow

```sh
# 1. Build the self-contained bundled tk (PyInstaller onedir, ~28 MB)
scripts/build-bundled-tk.sh            # → dist/tokenkick-runtime/tk

# 2. Unit + decoding tests (fixtures are committed)
cd macos/TokenKickKit
swift test

# 3. Include the integration tests against the real bundled runtime
TK_BUNDLED_RUNTIME=$PWD/../../dist/tokenkick-runtime/tk swift test

# 4. End-to-end boundary proof (snapshot through the process boundary,
#    bundled runtime answers, external pipx tk reported but not executed)
swift run tkapp-probe --runtime ../../dist/tokenkick-runtime/tk --isolate-home
```

Fixtures under `Tests/TokenKickKitTests/Fixtures/` are authentic `tk`
output. Regenerate after changing the app JSON contract:

```sh
scripts/generate-swift-fixtures.sh
```

## Packaging notes

The public release path builds a bundled `TokenKick.app` with its own `tk`
runtime under `Contents/Resources/tokenkick/tk`. Local beta DMGs are useful for
testing and GitHub release assets, but they are not notarized unless the public
Developer ID packaging flow is run with Apple signing credentials.
