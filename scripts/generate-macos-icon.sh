#!/bin/sh
# Generate TokenKick.icns from the refined TokenKick app icon source.
#
# With no arguments, this uses the refined tkicons app icon source committed
# under packaging/macos/tkicons, cleans exported preview/checker backgrounds
# into transparent corners, then generates every .iconset size and .icns.
#
# Usage: scripts/generate-macos-icon.sh [source.png] [output.icns]
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_SOURCE="$REPO_ROOT/packaging/macos/tkicons/Assets.xcassets/AppIcon.appiconset/1024.png"
FALLBACK_SOURCE="$REPO_ROOT/packaging/macos/TokenKickIcon-source.png"
if [ "$#" -ge 1 ]; then
    SOURCE="$1"
elif [ -f "$DEFAULT_SOURCE" ]; then
    SOURCE="$DEFAULT_SOURCE"
else
    SOURCE="$FALLBACK_SOURCE"
fi
OUTPUT="${2:-$REPO_ROOT/dist/macos/TokenKick.icns}"
WORK_DIR="${ICON_WORK_DIR:-$REPO_ROOT/build/macos-icon}"
ICONSET="$WORK_DIR/TokenKick.iconset"
PREPARED_SOURCE="$WORK_DIR/TokenKickIcon-source.png"

if [ ! -f "$SOURCE" ]; then
    echo "error: icon source missing at $SOURCE" >&2
    exit 1
fi
if ! command -v swift >/dev/null 2>&1; then
    echo "error: swift is required to prepare the macOS icon master" >&2
    exit 1
fi
if ! command -v sips >/dev/null 2>&1; then
    echo "error: sips is required to generate macOS icon PNG sizes" >&2
    exit 1
fi
if ! command -v iconutil >/dev/null 2>&1; then
    echo "error: iconutil is required to build .icns" >&2
    exit 1
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET" "$(dirname "$OUTPUT")"
mkdir -p "$WORK_DIR/clang-module-cache"
CLANG_MODULE_CACHE_PATH="$WORK_DIR/clang-module-cache" \
    swift "$REPO_ROOT/packaging/macos/prepare_tkicons_icon.swift" "$SOURCE" "$PREPARED_SOURCE"

make_icon() {
    size="$1"
    name="$2"
    sips -s format png -z "$size" "$size" "$PREPARED_SOURCE" --out "$ICONSET/$name" >/dev/null
}

make_icon 16 "icon_16x16.png"
make_icon 32 "icon_16x16@2x.png"
make_icon 32 "icon_32x32.png"
make_icon 64 "icon_32x32@2x.png"
make_icon 128 "icon_128x128.png"
make_icon 256 "icon_128x128@2x.png"
make_icon 256 "icon_256x256.png"
make_icon 512 "icon_256x256@2x.png"
make_icon 512 "icon_512x512.png"
make_icon 1024 "icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "$OUTPUT"
echo "==> TokenKick icon ready at $OUTPUT" >&2
printf '%s\n' "$OUTPUT"
