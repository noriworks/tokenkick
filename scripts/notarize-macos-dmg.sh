#!/bin/bash
# Submit a signed DMG to Apple's notarization service.
#
# Usage:
#   NOTARY_PROFILE=tokenkick-notary scripts/notarize-macos-dmg.sh dist/macos/TokenKick-1.13.0.dmg
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DMG_PATH="${1:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
SKIP_NOTARIZATION="${SKIP_NOTARIZATION:-0}"
LOG_DIR="${NOTARY_LOG_DIR:-$REPO_ROOT/dist/macos}"

if [ "$SKIP_NOTARIZATION" = "1" ]; then
    echo "==> SKIP_NOTARIZATION=1; skipping Apple notarization"
    exit 0
fi
if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ]; then
    echo "error: DMG not found: ${DMG_PATH:-<missing>}" >&2
    exit 1
fi
if [ -z "$NOTARY_PROFILE" ]; then
    echo "error: NOTARY_PROFILE is required for public notarization" >&2
    echo "create one with: xcrun notarytool store-credentials tokenkick-notary ..." >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_PATH="$LOG_DIR/notary-submit-$(basename "$DMG_PATH" .dmg).json"

echo "==> Submitting $DMG_PATH to Apple notarization"
xcrun notarytool submit "$DMG_PATH" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait \
    --output-format json | tee "$LOG_PATH"

if ! grep -q '"status"[[:space:]]*:[[:space:]]*"Accepted"' "$LOG_PATH"; then
    echo "error: notarization was not accepted; see $LOG_PATH" >&2
    exit 1
fi

echo "==> Notarization accepted; log saved to $LOG_PATH"
