"""Docs coverage checks for advertised command lists."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, ScheduleConfig, WorkSchedule, account_key_string
from tokenkick.reset_calendar import CalendarEvent, calendar_json_payload
from tokenkick.scheduling import PendingKick
from tokenkick.status_rendering import _status_json_payload


def test_readme_command_list_mentions_current_core_commands():
    readme = Path("docs/README.md").read_text()

    for command in [
        "tk status --codex",
        "tk status --account LABEL",
        "tk status --verbose",
        "tk menu",
        "tk run --json-output",
        "tk plan --work-window",
        "tk run --codex",
        "tk wake LABEL",
        "tk history --anchored",
        "tk doctor",
        "tk setup --dry-run",
        "tk notify --telegram TOKEN CHAT_ID",
        "tk update --json-output",
        "tk accounts detail LABEL",
        "tk codex-strategy",
        "tk codex-surfaces LABEL",
        "tk codex-surface-patterns",
    ]:
        assert command in readme


def test_agent_playbook_and_llms_pointer_are_present():
    playbook = Path("docs/AGENT_PLAYBOOK.md").read_text()
    llms = Path("llms.txt").read_text()

    assert "TK_NO_INTERACTIVE=1 tk status --json-output" in playbook
    assert "TK_NO_INTERACTIVE=1 tk status --refresh --json-output" in playbook
    assert "not be polled in a loop" in playbook
    assert "TK_NO_INTERACTIVE=1 tk plan --work-window" in playbook
    assert "TK_NO_INTERACTIVE=1 tk codex-strategy status --json-output" in playbook
    assert "TK_NO_INTERACTIVE=1 tk history --anchored --json-output" in playbook
    assert "usable_session_tier_defaults" in playbook
    assert "rough, unverified starting" in playbook
    assert "no one-shot per-account" in playbook
    assert "docs/AGENT_PLAYBOOK.md" in llms


def test_agent_playbook_recheck_uses_cached_status():
    playbook = Path("docs/AGENT_PLAYBOOK.md").read_text()

    assert "Read cached state with `status --json-output`." in playbook
    assert "re-check later with:\nTK_NO_INTERACTIVE=1 tk status --json-output" in playbook
    assert "Read state with `status --refresh --json-output`" not in playbook


def test_agent_playbook_json_examples_match_real_payload_shapes(monkeypatch):
    playbook = Path("docs/AGENT_PLAYBOOK.md").read_text()
    status_example, calendar_example = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", playbook, flags=re.S)[:2]
    ]
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        auto_kick=True,
        weekly_auto_kick=True,
        session_auto_kick=True,
        provider_home="/home/example/.codex-personal",
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=6.0,
        resets_at=1_781_190_000.0,
        session_used_percent=0.0,
        session_resets_at=1_780_608_420.0,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at="2026-06-04T23:30:00Z",
        created_at="2026-06-04T21:31:00Z",
        reason="align with work window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-06-04T22:30:00Z",
        work_end="2026-06-05T04:30:00Z",
        window_basis="session",
    )
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            accounts={
                account.label: WorkSchedule(
                    enabled=True,
                    weekdays="18:00-23:30",
                )
            },
        ),
    )
    monkeypatch.setattr("tokenkick.cli._status_refresh_lock_active", lambda: False)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-06-04T21:31:00Z")
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=50: [])
    monkeypatch.setattr(
        "tokenkick.status_rendering.load_pending_kicks",
        lambda *_args, **_kwargs: {account_key_string(account): pending},
    )

    status_payload = _status_json_payload(
        accounts=[account],
        statuses=[status],
        metadata_accounts=[account],
        metadata_statuses=[status],
        cached=True,
        refresh_error=None,
        config=config,
        cache_entries={},
    )
    _assert_json_subset_types(status_example, status_payload)

    generated_at = datetime(2026, 6, 4, 21, 31, tzinfo=timezone.utc)
    calendar_payload = calendar_json_payload(
        generated_at=generated_at,
        tz=timezone.utc,
        days_ahead=7,
        events=[
            CalendarEvent(
                account="codex (work)",
                provider="codex",
                type="session_reset",
                predicted_at=datetime(2026, 6, 5, 1, 19, tzinfo=timezone.utc),
                confidence="medium",
                source="countdown_extrapolation",
            )
        ],
        warnings=[],
        pending_kicks=[
            PendingKick(
                account_key="codex-home|codex|/home/example/.codex-reserve",
                account_label="codex (reserve)",
                provider="codex",
                kick_at="2026-06-04T23:30:00Z",
                created_at="2026-06-04T21:31:00Z",
                reason="align with work window",
                windows_needed=1,
                expected_waste_minutes=0,
                waste_location="none",
                work_start="2026-06-04T22:30:00Z",
                work_end="2026-06-05T04:30:00Z",
                window_basis="session",
            )
        ],
    )
    _assert_json_subset_types(calendar_example, calendar_payload)


def _assert_json_subset_types(expected: object, actual: object) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, expected_value in expected.items():
            assert key in actual
            _assert_json_subset_types(expected_value, actual[key])
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        if not expected:
            assert actual == []
            return
        assert actual
        for expected_item, actual_item in zip(expected, actual, strict=False):
            _assert_json_subset_types(expected_item, actual_item)
        return
    if expected is None:
        assert actual is None
        return
    if isinstance(expected, bool):
        assert isinstance(actual, bool)
        return
    if isinstance(expected, float):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool)
        return
    assert isinstance(actual, type(expected))


def test_commands_doc_mentions_app_mode_commands():
    commands_doc = Path("docs/TOKENKICK_COMMANDS.md").read_text()

    for command in [
        "TK_APP_MODE=1",
        "tk app snapshot",
        "tk app setup",
        "tk app doctor",
        "tk daemon --status --json-output",
        "tk accounts list --json-output",
        "tk auto status --json-output",
        "tk schedule show --json-output",
    ]:
        assert command in commands_doc
