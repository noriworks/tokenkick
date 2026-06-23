#!/bin/sh
# Build the self-contained `tk` runtime that TokenKick.app bundles as
# Contents/Resources/tokenkick/. Produces dist/tokenkick-runtime/ with the
# `tk` executable at its root; the directory is relocatable and does not
# need a system Python.
#
# Usage: scripts/build-bundled-tk.sh [--skip-smoke-test]
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
DIST_DIR="$REPO_ROOT/dist/tokenkick-runtime"
WORK_DIR="$REPO_ROOT/build/pyinstaller"
SKIP_SMOKE_TEST=0
[ "${1:-}" = "--skip-smoke-test" ] && SKIP_SMOKE_TEST=1

if [ ! -x "$PYTHON" ]; then
    echo "error: no Python at $PYTHON (set PYTHON=... or create .venv)" >&2
    exit 1
fi

echo "==> Installing tokenkick + PyInstaller into the build environment"
"$PYTHON" -m pip install --quiet -e "$REPO_ROOT" pyinstaller

echo "==> Building bundled tk with PyInstaller (onedir)"
rm -rf "$DIST_DIR" "$WORK_DIR"
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --name tk \
    --collect-submodules tokenkick \
    --distpath "$REPO_ROOT/dist/pyinstaller-out" \
    --workpath "$WORK_DIR" \
    --specpath "$WORK_DIR" \
    --log-level WARN \
    "$REPO_ROOT/scripts/pyinstaller_entry.py"

mv "$REPO_ROOT/dist/pyinstaller-out/tk" "$DIST_DIR"
rmdir "$REPO_ROOT/dist/pyinstaller-out"

VERSION="$("$PYTHON" -c 'from tokenkick.versioning import installed_version; print(installed_version())')"
printf '%s\n' "$VERSION" > "$DIST_DIR/RUNTIME_VERSION"

# Editable installs create PEP 610 direct_url.json metadata with the local
# repository path. The app runtime does not need it, and shipping it would leak
# a builder-specific path into the bundled resources.
find "$DIST_DIR" -path '*/tokenkick-*.dist-info/direct_url.json' -type f -delete

if [ "$SKIP_SMOKE_TEST" -eq 0 ]; then
    echo "==> Smoke test: bundled tk must answer tk app snapshot in an isolated HOME"
    SMOKE_HOME="$(mktemp -d)"
    SNAPSHOT_OUT="$SMOKE_HOME/snapshot.json"
    if ! HOME="$SMOKE_HOME" TK_APP_MODE=1 "$DIST_DIR/tk" app snapshot > "$SNAPSHOT_OUT" 2> "$SMOKE_HOME/snapshot.stderr"; then
        echo "error: bundled tk app snapshot failed" >&2
        cat "$SMOKE_HOME/snapshot.stderr" >&2
        exit 1
    fi
    if ! grep -q '"ok": true' "$SNAPSHOT_OUT"; then
        echo "error: bundled tk app snapshot did not report ok=true" >&2
        head -20 "$SNAPSHOT_OUT" >&2
        exit 1
    fi
    if ! grep -q "\"version\": \"$VERSION\"" "$SNAPSHOT_OUT"; then
        echo "error: bundled tk reports a different core version than $VERSION" >&2
        exit 1
    fi
    rm -rf "$SMOKE_HOME"
    echo "==> Smoke test passed"
fi

echo "==> Bundled tk v$VERSION ready at $DIST_DIR/tk"
