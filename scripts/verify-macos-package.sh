#!/bin/bash
# Verify a locally built TokenKick.app/DMG package.
#
# Usage: scripts/verify-macos-package.sh [path/to/TokenKick.app] [path/to/TokenKick.dmg]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
APP_DIR="${1:-$REPO_ROOT/dist/macos/TokenKick.app}"
VERSION="$("$PYTHON" -c 'from tokenkick.versioning import installed_version; print(installed_version())')"
DMG_PATH="${2:-$REPO_ROOT/dist/macos/TokenKick-$VERSION-local.dmg}"
RUNTIME="$APP_DIR/Contents/Resources/tokenkick/tk"

if [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found at $APP_DIR" >&2
    exit 1
fi
if [ ! -x "$RUNTIME" ]; then
    echo "error: bundled runtime not executable at $RUNTIME" >&2
    exit 1
fi
if [ ! -f "$APP_DIR/Contents/Resources/TokenKick.icns" ]; then
    echo "error: app icon missing at Contents/Resources/TokenKick.icns" >&2
    exit 1
fi
if ! plutil -extract CFBundleIconFile raw "$APP_DIR/Contents/Info.plist" -o - | grep -qx 'TokenKick'; then
    echo "error: Info.plist CFBundleIconFile is not TokenKick" >&2
    exit 1
fi

runtime_version="$(cat "$APP_DIR/Contents/Resources/tokenkick/RUNTIME_VERSION")"
if [ "$runtime_version" != "$VERSION" ]; then
    echo "error: RUNTIME_VERSION $runtime_version != project version $VERSION" >&2
    exit 1
fi
if ! "$RUNTIME" --version | grep -q "version $VERSION"; then
    echo "error: bundled tk --version does not report $VERSION" >&2
    exit 1
fi

echo "==> App bundle: $APP_DIR"
echo "==> Runtime version: $runtime_version"
echo "==> Nested Mach-O files: $("$REPO_ROOT/scripts/find-macos-mach-o.sh" "$APP_DIR" | wc -l | tr -d ' ')"
"$REPO_ROOT/scripts/find-macos-mach-o.sh" "$APP_DIR" >/dev/null

codesign --verify --strict --verbose=4 "$APP_DIR"

if [ -f "$DMG_PATH" ]; then
    hdiutil verify "$DMG_PATH"
    codesign --verify --verbose=4 "$DMG_PATH" || true
fi

echo "==> Package verification passed"
