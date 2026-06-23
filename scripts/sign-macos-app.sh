#!/bin/bash
# Explicitly sign nested Mach-O files, then sign TokenKick.app.
#
# Usage:
#   SIGNING_IDENTITY="-" scripts/sign-macos-app.sh dist/macos/TokenKick.app
#   SIGNING_IDENTITY="Developer ID Application: ..." scripts/sign-macos-app.sh dist/macos/TokenKick.app
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-$REPO_ROOT/dist/macos/TokenKick.app}"
IDENTITY="${SIGNING_IDENTITY:--}"
ENTITLEMENTS="${ENTITLEMENTS:-$REPO_ROOT/packaging/macos/TokenKick.entitlements}"
HARDENED_RUNTIME="${HARDENED_RUNTIME:-}"

if [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found at $APP_DIR" >&2
    exit 1
fi
if [ -z "$IDENTITY" ]; then
    echo "error: SIGNING_IDENTITY is empty; use '-' for local ad-hoc signing" >&2
    exit 1
fi

if [ "$IDENTITY" != "-" ] && [ -z "$HARDENED_RUNTIME" ]; then
    HARDENED_RUNTIME=1
fi
if [ -z "$HARDENED_RUNTIME" ]; then
    HARDENED_RUNTIME=0
fi

if [ "$IDENTITY" != "-" ]; then
    if ! security find-identity -v -p codesigning | grep -F "$IDENTITY" >/dev/null; then
        echo "error: signing identity not found in keychain: $IDENTITY" >&2
        echo "available identities:" >&2
        security find-identity -v -p codesigning >&2 || true
        exit 1
    fi
fi

sign_args=(--force --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ]; then
    sign_args+=(--timestamp)
fi
if [ "$HARDENED_RUNTIME" = "1" ]; then
    sign_args+=(--options runtime)
fi

entitlement_args=()
if [ -f "$ENTITLEMENTS" ]; then
    entitlement_args=(--entitlements "$ENTITLEMENTS")
fi

if command -v xattr >/dev/null 2>&1; then
    xattr -cr "$APP_DIR" 2>/dev/null || true
fi

echo "==> Signing nested Mach-O files in $APP_DIR"
while IFS= read -r mach_o; do
    echo "    $mach_o"
    codesign "${sign_args[@]}" "$mach_o"
done < <("$REPO_ROOT/scripts/find-macos-mach-o.sh" "$APP_DIR")

echo "==> Signing app bundle $APP_DIR"
codesign "${sign_args[@]}" "${entitlement_args[@]}" "$APP_DIR"

echo "==> Verifying app signature"
codesign --verify --strict --verbose=4 "$APP_DIR"
echo "==> App signature verified"
