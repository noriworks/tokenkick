#!/bin/sh
# Ad-hoc sign TokenKick.app for local testing using the same explicit nested
# Mach-O signing path as public packaging. This does not use a Developer ID
# certificate and is not notarization.
#
# Usage: scripts/sign-local-macos-app.sh [path/to/TokenKick.app]
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-$REPO_ROOT/dist/macos/TokenKick.app}"

if [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found at $APP_DIR" >&2
    exit 1
fi

SIGNING_IDENTITY="-" HARDENED_RUNTIME="${HARDENED_RUNTIME:-0}" \
    "$REPO_ROOT/scripts/sign-macos-app.sh" "$APP_DIR"
