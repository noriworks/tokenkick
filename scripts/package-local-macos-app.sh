#!/bin/sh
# Build the local TokenKick.app beta bundle end to end.
#
# Usage: scripts/package-local-macos-app.sh
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$REPO_ROOT/scripts/build-bundled-tk.sh"
APP_DIR="$("$REPO_ROOT/scripts/assemble-macos-app.sh")"
"$REPO_ROOT/scripts/sign-local-macos-app.sh" "$APP_DIR"
DMG_PATH="$("$REPO_ROOT/scripts/create-local-dmg.sh" "$APP_DIR")"

echo "==> Package complete"
echo "app: $APP_DIR"
echo "dmg: $DMG_PATH"
