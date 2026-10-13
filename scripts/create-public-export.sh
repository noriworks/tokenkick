#!/bin/sh
# Export tracked working-tree files into a new directory; never replace output.
set -eu
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" "$REPO_ROOT/scripts/create-public-export.py" "$REPO_ROOT" "${1:-/private/tmp/tokenkick-public-export}"
