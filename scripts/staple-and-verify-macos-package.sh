#!/bin/bash
# Staple notarization tickets and run Gatekeeper/signature verification.
#
# Usage: scripts/staple-and-verify-macos-package.sh dist/macos/TokenKick.app dist/macos/TokenKick-1.13.0.dmg
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-$REPO_ROOT/dist/macos/TokenKick.app}"
DMG_PATH="${2:-}"
SKIP_NOTARIZATION="${SKIP_NOTARIZATION:-0}"

if [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found at $APP_DIR" >&2
    exit 1
fi
if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ]; then
    echo "error: DMG not found: ${DMG_PATH:-<missing>}" >&2
    exit 1
fi

codesign --verify --strict --verbose=4 "$APP_DIR"
codesign --verify --verbose=4 "$DMG_PATH"

if [ "$SKIP_NOTARIZATION" != "1" ]; then
    echo "==> Stapling app and DMG"
    xcrun stapler staple "$APP_DIR"
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$APP_DIR"
    xcrun stapler validate "$DMG_PATH"

    echo "==> Gatekeeper assessment"
    spctl --assess --type execute --verbose=4 "$APP_DIR"
    spctl --assess --type open --verbose=4 "$DMG_PATH"
else
    echo "==> SKIP_NOTARIZATION=1; skipping stapler and strict Gatekeeper acceptance checks"
    spctl --assess --type execute --verbose=4 "$APP_DIR" || true
    spctl --assess --type open --verbose=4 "$DMG_PATH" || true
fi

echo "==> Staple/verification step complete"
