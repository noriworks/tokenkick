"""Tests for reset calendar predictions."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    ScheduleConfig,
    WorkSchedule,
    account_key_string,
)
from tokenkick.reset_calendar import (
    build_reset_calendar,
    calendar_json_payload,
    render_ics,
)
from tokenkick.scheduling import PendingKick


NOW = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


def _entry(
    account: AccountConfig,
    status: AccountStatus,
    *,
    observed_at: str = "2026-05-27T12:00:00Z",
    refresh_error: str | None = None,
) -> dict:
    return {
        "account": account,
        "status": status,
        "cached_at": observed_at,
        "provider_observed_at": observed_at,
        "refresh_error": refresh_error,
    }


def _calendar(
    account: AccountConfig,
    status: AccountStatus,
    *,
    config: Config | None = None,
    now: datetime = NOW,
    tz=timezone.utc,
    days_ahead: int = 7,
    entry: dict | None = None,
    **kwargs,
):
    key = account_key_string(account)
    return build_reset_calendar(
        config=config or Config(accounts=[account]),
        accounts=[account],
        statuses=[status],
        cache_entries={key: entry or _entry(account, status)},
        now=now,
        tz=tz,
        days_ahead=days_ahead,
        **kwargs,
    )


def test_weekly_reset_from_absolute_timestamp_is_high_confidence():
    account = AccountConfig(label="codex", provider="codex")
    reset_at = NOW + timedelta(days=2, minutes=32)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_at=reset_at.timestamp(),
        window_minutes=10080,
    )

    result = _calendar(account, status)

    assert [(event.type, event.predicted_at, event.confidence, event.source) for event in result.events] == [
        ("weekly_reset", reset_at, "high", "provider_reset_timestamp")
    ]


def test_weekly_reset_falls_back_to_countdown_and_stale_downgrades_confidence():
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_in_seconds=3600,
        window_minutes=10080,
    )
    entry = _entry(
        account,
        status,
        observed_at="2026-05-27T12:00:00Z",
        refresh_error="TimeoutExpired",
    )

    result = _calendar(account, status, entry=entry)

    assert result.events[0].predicted_at == datetime(2026, 5, 27, 13, 0, tzinfo=timezone.utc)
    assert result.events[0].confidence == "medium"
    assert result.events[0].source == "countdown_extrapolation"


def test_session_reset_prediction_is_estimated_and_does_not_schedule_kick():
    account = AccountConfig(label="codex", provider="codex")
    reset_at = NOW + timedelta(hours=3)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_used_percent=12.0,
        session_resets_at=reset_at.timestamp(),
        session_window_minutes=300,
    )

    result = _calendar(account, status)

    assert result.events[0].type == "session_reset"
    assert result.events[0].estimated is True
    assert result.events[0].confidence == "medium"
    assert result.events[0].optimal_kick_at is None


def test_calendar_skips_hidden_unknown_missing_and_filters_scope():
    visible = AccountConfig(label="visible", provider="codex")
    hidden = AccountConfig(label="hidden", provider="codex", visible=False)
    claude = AccountConfig(label="claude", provider="claude")
    statuses = [
        AccountStatus(
            label="visible",
            state=AccountState.ACTIVE,
            resets_at=(NOW + timedelta(hours=1)).timestamp(),
        ),
        AccountStatus(
            label="hidden",
            state=AccountState.ACTIVE,
            resets_at=(NOW + timedelta(hours=2)).timestamp(),
        ),
        AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            resets_at=(NOW + timedelta(hours=3)).timestamp(),
        ),
    ]
    accounts = [visible, hidden, claude]
    entries = {
        account_key_string(account): _entry(account, status)
        for account, status in zip(accounts, statuses, strict=False)
    }

    result = build_reset_calendar(
        config=Config(accounts=accounts),
        accounts=accounts,
        statuses=statuses,
        cache_entries=entries,
        now=NOW,
        tz=timezone.utc,
        days_ahead=7,
        provider="codex",
        missing_accounts=[AccountConfig(label="missing", provider="codex")],
    )

    assert [event.account for event in result.events] == ["visible"]
    assert result.warnings == ['No cached status for "missing". Run tk status --refresh.']


def test_days_filter_and_weekly_cascade():
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_at=(NOW + timedelta(days=2)).timestamp(),
        window_minutes=10080,
    )

    short = _calendar(account, status, days_ahead=7)
    long = _calendar(account, status, days_ahead=10)

    assert [event.type for event in short.events] == ["weekly_reset"]
    assert [event.type for event in long.events] == ["weekly_reset", "weekly_reset_estimated"]
    assert long.events[1].confidence == "medium"


def test_smart_schedule_predictions_handle_berlin_utc_us_and_dst():
    account = AccountConfig(label="codex", provider="codex")
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"codex": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_at=datetime(2026, 5, 29, 8, 0, tzinfo=timezone.utc).timestamp(),
        window_minutes=10080,
        session_window_minutes=300,
    )

    berlin = _calendar(
        account,
        status,
        config=config,
        now=datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc),
        tz=ZoneInfo("Europe/Berlin"),
    )
    utc = _calendar(
        account,
        status,
        config=Config(
            schedule=ScheduleConfig(
                enabled=True,
                timezone="UTC",
                accounts={"codex": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
            )
        ),
        now=datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc),
        tz=timezone.utc,
    )
    ny = _calendar(
        account,
        AccountStatus(
            label="codex",
            state=AccountState.ACTIVE,
            resets_at=datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc).timestamp(),
            window_minutes=10080,
            session_window_minutes=300,
        ),
        config=Config(
            schedule=ScheduleConfig(
                enabled=True,
                timezone="America/New_York",
                accounts={"codex": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
            )
        ),
        now=datetime(2026, 3, 30, 11, 0, tzinfo=timezone.utc),
        tz=ZoneInfo("America/New_York"),
    )

    assert berlin.events[0].optimal_kick_at == datetime(2026, 5, 29, 9, 0, tzinfo=timezone.utc)
    assert utc.events[0].optimal_kick_at == datetime(2026, 5, 29, 11, 0, tzinfo=timezone.utc)
    assert ny.events[0].optimal_kick_at.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M %Z") == "11:00 EDT"


def test_gemini_daily_midnight_pacific_reset():
    account = AccountConfig(label="gemini", provider="gemini")
    status = AccountStatus(label="gemini", state=AccountState.FRESH)

    result = _calendar(
        account,
        status,
        now=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        days_ahead=2,
    )

    assert [event.type for event in result.events] == ["daily_reset", "daily_reset"]
    assert result.events[0].predicted_at == datetime(2026, 5, 28, 7, 0, tzinfo=timezone.utc)
    assert result.events[0].source == "fixed_provider_schedule"


def test_json_payload_and_ics_export():
    event_time = datetime(2026, 5, 29, 12, 32, tzinfo=timezone.utc)
    account = AccountConfig(label="codex, personal", provider="codex")
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        resets_at=event_time.timestamp(),
        window_minutes=10080,
    )
    result = _calendar(account, status, tz=ZoneInfo("Europe/Berlin"))

    payload = calendar_json_payload(
        generated_at=NOW,
        tz=ZoneInfo("Europe/Berlin"),
        days_ahead=7,
        events=result.events,
        warnings=[],
        pending_kicks=[
            PendingKick(
                account_key=account_key_string(account),
                account_label=account.label,
                provider=account.provider,
                kick_at="2026-05-27T13:00:00Z",
                created_at="2026-05-27T12:00:00Z",
                reason="align with work window",
                windows_needed=1,
                expected_waste_minutes=0,
                waste_location="none",
                work_start="2026-05-27T12:30:00Z",
                work_end="2026-05-27T18:00:00Z",
                window_basis="session",
            )
        ],
    )
    ics = render_ics(result.events, ZoneInfo("Europe/Berlin"))

    assert payload["schema_version"] == 1
    assert payload["pending_kicks"][0]["next_action_at"] == "2026-05-27T13:00:00Z"
    assert payload["pending_kicks"][0]["next_action_at_local"] == "2026-05-27T15:00:00+02:00"
    assert payload["events"][0]["predicted_at"] == "2026-05-29T12:32:00Z"
    assert "BEGIN:VCALENDAR" in ics
    assert "DTSTART:20260529T123200Z" in ics
    assert "UID:tokenkick-codex-personal-weekly_reset-20260529T123200Z@tokenkick" in ics
    assert "DURATION:PT15M" in ics
    assert "TRIGGER:-PT30M" in ics
    assert "SUMMARY:TokenKick: codex\\, personal weekly reset" in ics
