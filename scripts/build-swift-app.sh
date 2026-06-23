#!/bin/sh
# Build the native Swift executable used inside TokenKick.app.
#
# Usage: scripts/build-swift-app.sh
# Prints the built executable path on stdout. Build logs go to stderr so other
# packaging scripts can capture the path safely.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_DIR="$REPO_ROOT/macos/TokenKickKit"
BUILD_DIR="${SWIFT_BUILD_DIR:-$REPO_ROOT/build/macos-swift}"
CONFIGURATION="${SWIFT_CONFIGURATION:-release}"
PRODUCT="${SWIFT_PRODUCT:-TokenKick}"

echo "==> Building Swift product $PRODUCT ($CONFIGURATION)" >&2
swift build \
    --package-path "$PACKAGE_DIR" \
    --build-path "$BUILD_DIR" \
    -c "$CONFIGURATION" \
    --product "$PRODUCT" >&2

BIN_DIR="$(swift build \
    --package-path "$PACKAGE_DIR" \
    --build-path "$BUILD_DIR" \
    -c "$CONFIGURATION" \
    --show-bin-path)"

EXECUTABLE="$BIN_DIR/$PRODUCT"
if [ ! -x "$EXECUTABLE" ]; then
    echo "error: Swift executable was not built at $EXECUTABLE" >&2
    exit 1
fi

printf '%s\n' "$EXECUTABLE"
