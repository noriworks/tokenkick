#!/bin/sh
# Assemble a local TokenKick.app bundle around the Swift executable and the
# PyInstaller tk runtime. This is a local-beta bundle, not notarized.
#
# Usage: scripts/assemble-macos-app.sh
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
APP_ROOT="${APP_ROOT:-$REPO_ROOT/dist/macos}"
APP_NAME="${APP_NAME:-TokenKick}"
APP_DIR="${APP_DIR:-$APP_ROOT/$APP_NAME.app}"
RUNTIME_DIR="${RUNTIME_DIR:-$REPO_ROOT/dist/tokenkick-runtime}"
ICON_FILE="${ICON_FILE:-}"

if [ ! -x "$PYTHON" ]; then
    echo "error: no Python at $PYTHON (set PYTHON=... or create .venv)" >&2
    exit 1
fi
if [ ! -x "$RUNTIME_DIR/tk" ]; then
    echo "error: bundled runtime missing at $RUNTIME_DIR/tk" >&2
    echo "run scripts/build-bundled-tk.sh first" >&2
    exit 1
fi

VERSION="$("$PYTHON" -c 'from tokenkick.versioning import installed_version; print(installed_version())')"
SWIFT_EXECUTABLE="$("$REPO_ROOT/scripts/build-swift-app.sh")"
if [ -z "$ICON_FILE" ]; then
    ICON_FILE="$("$REPO_ROOT/scripts/generate-macos-icon.sh")"
fi
if [ ! -f "$ICON_FILE" ]; then
    echo "error: app icon missing at $ICON_FILE" >&2
    exit 1
fi

echo "==> Assembling $APP_DIR" >&2
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cp "$SWIFT_EXECUTABLE" "$APP_DIR/Contents/MacOS/$APP_NAME"
chmod 755 "$APP_DIR/Contents/MacOS/$APP_NAME"
cp -R "$RUNTIME_DIR" "$APP_DIR/Contents/Resources/tokenkick"
cp "$ICON_FILE" "$APP_DIR/Contents/Resources/TokenKick.icns"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>TokenKick</string>
  <key>CFBundleExecutable</key>
  <string>TokenKick</string>
  <key>CFBundleIdentifier</key>
  <string>com.tokenkick.app</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleIconFile</key>
  <string>TokenKick</string>
  <key>CFBundleName</key>
  <string>TokenKick</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.utilities</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key>
  <true/>
</dict>
</plist>
EOF

printf 'APPL????' > "$APP_DIR/Contents/PkgInfo"

if [ ! -x "$APP_DIR/Contents/Resources/tokenkick/tk" ]; then
    echo "error: runtime not executable inside app bundle" >&2
    exit 1
fi

echo "==> TokenKick.app ready at $APP_DIR" >&2
printf '%s\n' "$APP_DIR"
