#!/bin/bash
# Print Mach-O files inside a macOS app bundle, one path per line.
#
# Usage: scripts/find-macos-mach-o.sh path/to/TokenKick.app
set -euo pipefail

APP_DIR="${1:-}"
if [ -z "$APP_DIR" ] || [ ! -d "$APP_DIR/Contents" ]; then
    echo "error: app bundle not found: ${APP_DIR:-<missing>}" >&2
    exit 1
fi

find "$APP_DIR" -type f -print0 |
    while IFS= read -r -d '' file_path; do
        if file "$file_path" | grep -q 'Mach-O'; then
            printf '%s\n' "$file_path"
        fi
    done |
    LC_ALL=C sort
