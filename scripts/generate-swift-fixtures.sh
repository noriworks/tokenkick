#!/bin/sh
# Regenerate the JSON fixtures TokenKickKit's decoding tests run against.
# Fixtures are authentic `tk` output captured from an isolated HOME with a
# stripped PATH (no provider CLIs, no external tk) and a seeded config, so
# they are deterministic apart from timestamps and temp paths.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
TK="$REPO_ROOT/.venv/bin/tk"
FIXTURES="$REPO_ROOT/macos/TokenKickKit/Tests/TokenKickKitTests/Fixtures"
FIXTURE_HOME="$(mktemp -d)"
FIXTURE_PATH="/usr/bin:/bin"

if [ ! -x "$TK" ]; then
    echo "error: no tk at $TK (pip install -e . into .venv first)" >&2
    exit 1
fi

mkdir -p "$FIXTURES"

echo "==> Seeding fixture config in $FIXTURE_HOME"
HOME="$FIXTURE_HOME" "$PYTHON" - << 'EOF'
from tokenkick.models import AccountConfig, Config, DataSource, KickEvent, append_kick_event
from tokenkick.reset_defense import ResetEvent, append_reset_event

config = Config(
    accounts=[
        AccountConfig(
            label="codex (fixture)",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/Users/fixture/codex-fixture-home",
            auto_kick=True,
        ),
        AccountConfig(
            label="gemini (fixture)",
            provider="gemini",
            source=DataSource.MANUAL,
        ),
    ]
)
config.save()

# Deterministic kick history for the History screen fixtures.
append_kick_event(
    KickEvent(
        label="codex (fixture)",
        timestamp=1_780_000_000.0,
        success=True,
        confirmed=True,
        kind="kick",
        kick_type="weekly",
        kick_model="gpt-5.4-codex",
        total_tokens=42,
        evidence_response=True,
        evidence_tokens=True,
        evidence_provider_moved=True,
        post_kick_status="moved",
        codex_confirmation_method="provider_moved",
        codex_attribution="strong",
    )
)
append_kick_event(
    KickEvent(
        label="codex (fixture)",
        timestamp=1_780_003_600.0,
        success=True,
        confirmed=False,
        kind="kick",
        kick_type="session",
        error="Codex accepted usage, but session status is still ambiguous",
        evidence_response=True,
        post_kick_status="phantom",
    )
)
append_kick_event(
    KickEvent(
        label="claude (fixture)",
        timestamp=1_780_007_200.0,
        success=False,
        confirmed=False,
        kind="kick",
        kick_type="session",
        error="claude exec failed: rate limited",
        post_kick_status="not_checked",
    )
)

# One reset observation for the Diagnostics screen fixtures.
append_reset_event(
    ResetEvent(
        id="fixture-reset-1",
        detected_at="2026-06-01T06:00:00+00:00",
        provider="codex",
        confidence="confirmed",
        affected_accounts=["codex (fixture)"],
        trigger="weekly_reset_jump",
        account_snapshots=[],
        total_quota_hours_lost=None,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="Weekly reset observed earlier than predicted.",
        detail="Fixture reset event for decoding tests.",
    )
)
EOF

run_tk() {
    HOME="$FIXTURE_HOME" PATH="$FIXTURE_PATH" TK_APP_MODE=1 "$TK" "$@"
}

echo "==> Capturing fixtures"
run_tk app snapshot > "$FIXTURES/snapshot.json"
run_tk daemon --status --json-output > "$FIXTURES/daemon_status.json"
run_tk accounts list --json-output > "$FIXTURES/accounts_list.json"
run_tk accounts hide nope --json-output > "$FIXTURES/mutation_error.json" || true
run_tk frobnicate > "$FIXTURES/usage_error.json" || true
run_tk kick "codex (fixture)" --json-output > "$FIXTURES/kick_skipped.json" || true
run_tk history --json-output > "$FIXTURES/history.json"
run_tk reset-log --json-output > "$FIXTURES/reset_log.json"
run_tk app doctor > "$FIXTURES/app_doctor.json"
run_tk notify --ntfy fixture-topic --json-output > "$FIXTURES/notify_global.json"
run_tk app setup > "$FIXTURES/app_setup_events.jsonl" || true

echo "==> Normalizing machine-specific paths"
"$PYTHON" - "$FIXTURES" "$FIXTURE_HOME" "$REPO_ROOT" << 'EOF'
import sys
from pathlib import Path

fixtures, fixture_home, repo_root = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
replacements = [
    ("/private" + fixture_home, "/Users/fixture"),
    (fixture_home, "/Users/fixture"),
    ("/private" + repo_root, "/Users/fixture/tokenkick"),
    (repo_root, "/Users/fixture/tokenkick"),
]
for path in sorted(fixtures.iterdir()):
    if path.suffix not in {".json", ".jsonl"}:
        continue
    text = path.read_text()
    for needle, placeholder in replacements:
        text = text.replace(needle, placeholder)
    path.write_text(text)
EOF

echo "==> Validating fixtures parse as JSON and are portable"
"$PYTHON" - "$FIXTURES" << 'EOF'
import json
import re
import sys
from pathlib import Path

fixtures = Path(sys.argv[1])
forbidden = re.compile(r"/private/var/folders/|/var/folders/|/tmp/|/Users/(?!fixture\b)[^/\"]+")
for path in sorted(fixtures.glob("*.json")):
    text = path.read_text()
    json.loads(text)
    if match := forbidden.search(text):
        sys.exit(f"{path.name}: machine-specific path left in fixture: {match.group(0)}")
    print(f"  {path.name}: ok")
for path in sorted(fixtures.glob("*.jsonl")):
    text = path.read_text()
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        json.loads(line)
    if match := forbidden.search(text):
        sys.exit(f"{path.name}: machine-specific path left in fixture: {match.group(0)}")
    print(f"  {path.name}: ok ({len(lines)} records)")
EOF

rm -rf "$FIXTURE_HOME"
echo "==> Fixtures written to $FIXTURES"
