#!/bin/sh
# Create a local testing DMG containing TokenKick.app and an Applications
# shortcut. The image is not notarized.
#
# Usage: scripts/create-local-dmg.sh [path/to/TokenKick.app]
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
APP_DIR="${1:-$REPO_ROOT/dist/macos/TokenKick.app}"
DMG_ROOT="${DMG_ROOT:-$REPO_ROOT/build/dmg-root}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist/macos}"

if [ ! -x "$PYTHON" ]; then
    echo "error: no Python at $PYTHON (set PYTHON=... or create .venv)" >&2
    exit 1
fi
if [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found at $APP_DIR" >&2
    exit 1
fi

VERSION="$("$PYTHON" -c 'from tokenkick.versioning import installed_version; print(installed_version())')"
DMG_PATH="$OUTPUT_DIR/TokenKick-$VERSION-local.dmg"
RW_DMG_PATH="$OUTPUT_DIR/TokenKick-$VERSION-layout.dmg"
VOLUME_NAME="TokenKick $VERSION"
MOUNT_DIR="${TMPDIR:-/tmp}/tokenkick-dmg-$VERSION"

rm -rf "$DMG_ROOT" "$MOUNT_DIR" "$RW_DMG_PATH" "$DMG_PATH"
mkdir -p "$DMG_ROOT" "$OUTPUT_DIR"
cp -R "$APP_DIR" "$DMG_ROOT/TokenKick.app"
ln -s /Applications "$DMG_ROOT/Applications"

echo "==> Creating local DMG $DMG_PATH" >&2
hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$DMG_ROOT" \
    -ov \
    -format UDRW \
    "$RW_DMG_PATH" >&2

mkdir -p "$MOUNT_DIR"
hdiutil attach "$RW_DMG_PATH" \
    -mountpoint "$MOUNT_DIR" \
    -nobrowse \
    -noverify >&2

osascript >/dev/null <<OSA
tell application "Finder"
    set dmgFolder to POSIX file "$MOUNT_DIR" as alias
    open dmgFolder
    set dmgWindow to container window of dmgFolder
    set current view of dmgWindow to icon view
    set toolbar visible of dmgWindow to false
    set statusbar visible of dmgWindow to false
    set bounds of dmgWindow to {100, 100, 720, 360}
    set arrangement of icon view options of dmgWindow to not arranged
    set icon size of icon view options of dmgWindow to 64
    set position of item "TokenKick.app" of dmgFolder to {180, 95}
    set position of item "Applications" of dmgFolder to {430, 95}
    close dmgWindow
end tell
OSA

sync
hdiutil detach "$MOUNT_DIR" >&2
rmdir "$MOUNT_DIR" 2>/dev/null || true

hdiutil convert "$RW_DMG_PATH" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "$DMG_PATH" >&2
rm -f "$RW_DMG_PATH"

hdiutil verify "$DMG_PATH" >&2

echo "==> Local DMG ready at $DMG_PATH" >&2
printf '%s\n' "$DMG_PATH"
