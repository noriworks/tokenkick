#!/bin/bash
# Build a public macOS package. Supports local dry-runs with:
#   SIGNING_IDENTITY="-" SKIP_NOTARIZATION=1 scripts/package-public-macos-app.sh
#
# For public release, SIGNING_IDENTITY must be a Developer ID Application
# identity and NOTARY_PROFILE must be configured with xcrun notarytool.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
SIGNING_IDENTITY="${SIGNING_IDENTITY:-}"
SKIP_NOTARIZATION="${SKIP_NOTARIZATION:-0}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist/macos}"

if [ ! -x "$PYTHON" ]; then
    echo "error: no Python at $PYTHON (set PYTHON=... or create .venv)" >&2
    exit 1
fi
if [ -z "$SIGNING_IDENTITY" ]; then
    echo "error: SIGNING_IDENTITY is required; use '-' for local dry-run signing" >&2
    exit 1
fi
if [ "$SIGNING_IDENTITY" != "-" ] && [ -z "$NOTARY_PROFILE" ]; then
    echo "error: NOTARY_PROFILE is required for public Developer ID packaging" >&2
    exit 1
fi
if [ "$SIGNING_IDENTITY" = "-" ] && [ "$SKIP_NOTARIZATION" != "1" ]; then
    echo "error: ad-hoc public package requires SKIP_NOTARIZATION=1" >&2
    exit 1
fi

"$REPO_ROOT/scripts/build-bundled-tk.sh"

VERSION="$(cat "$REPO_ROOT/dist/tokenkick-runtime/RUNTIME_VERSION")"
PUBLIC_DMG="$OUTPUT_DIR/TokenKick-$VERSION.dmg"

APP_DIR="$("$REPO_ROOT/scripts/assemble-macos-app.sh")"

SIGNING_IDENTITY="$SIGNING_IDENTITY" \
    "$REPO_ROOT/scripts/sign-macos-app.sh" "$APP_DIR"

DMG_PATH="$("$REPO_ROOT/scripts/create-local-dmg.sh" "$APP_DIR")"
mv "$DMG_PATH" "$PUBLIC_DMG"

SIGNING_IDENTITY="$SIGNING_IDENTITY" "$REPO_ROOT/scripts/sign-macos-dmg.sh" "$PUBLIC_DMG"
NOTARY_PROFILE="$NOTARY_PROFILE" SKIP_NOTARIZATION="$SKIP_NOTARIZATION" \
    "$REPO_ROOT/scripts/notarize-macos-dmg.sh" "$PUBLIC_DMG"
SKIP_NOTARIZATION="$SKIP_NOTARIZATION" \
    "$REPO_ROOT/scripts/staple-and-verify-macos-package.sh" "$APP_DIR" "$PUBLIC_DMG"
"$REPO_ROOT/scripts/verify-macos-package.sh" "$APP_DIR" "$PUBLIC_DMG"

echo "==> Public package complete"
echo "app: $APP_DIR"
echo "dmg: $PUBLIC_DMG"
shasum -a 256 "$PUBLIC_DMG"
