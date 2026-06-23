#!/bin/bash
# Sign a DMG for public distribution. Ad-hoc signing is allowed for local
# dry-runs, but public packaging should pass a Developer ID identity.
#
# Usage: SIGNING_IDENTITY="Developer ID Application: ..." scripts/sign-macos-dmg.sh dist/macos/TokenKick-1.13.0.dmg
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DMG_PATH="${1:-}"
IDENTITY="${SIGNING_IDENTITY:--}"

if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ]; then
    echo "error: DMG not found: ${DMG_PATH:-<missing>}" >&2
    exit 1
fi
if [ -z "$IDENTITY" ]; then
    echo "error: SIGNING_IDENTITY is empty; use '-' for local ad-hoc signing" >&2
    exit 1
fi
if [ "$IDENTITY" != "-" ]; then
    if ! security find-identity -v -p codesigning | grep -F "$IDENTITY" >/dev/null; then
        echo "error: signing identity not found in keychain: $IDENTITY" >&2
        exit 1
    fi
fi

sign_args=(--force --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ]; then
    sign_args+=(--timestamp)
fi

echo "==> Signing DMG $DMG_PATH"
codesign "${sign_args[@]}" "$DMG_PATH"
codesign --verify --verbose=4 "$DMG_PATH"
echo "==> DMG signature verified"
