#!/bin/sh
# Create a clean source export for the public repository.
#
# The export contains tracked working-tree files only, excludes private git
# history, and omits internal launch/operator docs that need a separate review.
#
# Usage: scripts/create-public-export.sh [/path/to/export]
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPORT_DIR="${1:-/private/tmp/tokenkick-public-export}"

case "$EXPORT_DIR" in
    ""|"/"|"$REPO_ROOT"|"$REPO_ROOT"/*)
        echo "error: refusing to export into the source repository: $EXPORT_DIR" >&2
        exit 1
        ;;
esac

rm -rf "$EXPORT_DIR"
mkdir -p "$EXPORT_DIR"

(
    cd "$REPO_ROOT"
    git ls-files -z | rsync -a --from0 --files-from=- ./ "$EXPORT_DIR/"
)

rm -f \
    "$EXPORT_DIR/docs/APP_UX_PLAN.md" \
    "$EXPORT_DIR/docs/RELEASE.md"

if [ -d "$EXPORT_DIR/.git" ]; then
    echo "error: export unexpectedly contains .git" >&2
    exit 1
fi

echo "==> Public export ready at $EXPORT_DIR" >&2
printf '%s\n' "$EXPORT_DIR"
