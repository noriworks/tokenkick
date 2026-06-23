"""Tests for smart kick scheduling."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, ScheduleConfig, WorkSchedule
from tokenkick.scheduling import (
    PendingKick,
    PendingKickStateError,
    ScheduleReason,
    SchedulingWindowBasis,
    WasteLocation,
    compute_schedule_decision,
    from_utc_iso,
    load_pending_kicks,
    parse_work_window,
    prune_pending_kicks,
    recompute,
    resolve_today_work_window,
    save_pending_kicks,
    select_scheduling_window,
    upsert_pending_kick,
)


BERLIN = ZoneInfo("Europe/Berlin")


def test_standard_two_window_workday_kicks_before_work():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "11:00"
    assert decision.reason == ScheduleReason.OPTIMAL
    assert decision.windows_needed == 2
    assert decision.expected_waste_minutes == 180
    assert decision.waste_location == WasteLocation.PRE_WORK


def test_single_window_workday_kicks_at_work_start():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-18:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "14:00"
    assert decision.reason == ScheduleReason.SINGLE_WINDOW
    assert decision.windows_needed == 1


def test_three_window_workday():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("09:00-23:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "08:00"
    assert decision.windows_needed == 3


def test_workday_crosses_midnight():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("22:00-06:00", day, BERLIN)

    assert work_end.date() != work_start.date()
    assert int((work_end - work_start).total_seconds() // 3600) == 8


def test_workday_crosses_midnight_with_half_hour_times():
    day = datetime(2026, 6, 9, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("22:30-02:00", day, BERLIN)

    assert work_start.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M") == "2026-06-09 22:30"
    assert work_end.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M") == "2026-06-10 02:00"


def test_work_window_rejects_nonexistent_dst_time():
    day = datetime(2026, 3, 29, tzinfo=BERLIN).date()

    with pytest.raises(ValueError, match="does not exist.*DST"):
        parse_work_window("02:30-04:00", day, BERLIN)

    previous_day = datetime(2026, 3, 28, tzinfo=BERLIN).date()
    with pytest.raises(ValueError, match="does not exist.*DST"):
        parse_work_window("22:00-02:30", previous_day, BERLIN)


def test_quota_constrained_kicks_when_available_and_reports_post_work_waste():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 11, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 11, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "13:00"
    assert decision.reason == ScheduleReason.QUOTA_CONSTRAINED
    assert decision.expected_waste_minutes == 120
    assert decision.waste_location == WasteLocation.POST_WORK


def test_quota_available_before_optimal_defers():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "11:00"
    assert decision.reason == ScheduleReason.OPTIMAL


def test_no_work_window_today_returns_none():
    schedule = WorkSchedule(enabled=True, weekdays="09:00-17:00")
    now = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)

    assert resolve_today_work_window(schedule, now, BERLIN) is None


def test_equal_work_window_string_is_rejected():
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()

    with pytest.raises(ValueError, match="start and end must differ"):
        parse_work_window("09:00-09:00", day, BERLIN)


def test_stored_invalid_work_window_resolves_to_no_window_today():
    # Older versions could persist an equal start/end window; today's resolution
    # must degrade to "no window" instead of raising into daemon/run flows.
    schedule = WorkSchedule(enabled=True, weekdays="09:00-09:00")
    now = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)

    assert resolve_today_work_window(schedule, now, BERLIN) is None


def test_timezone_handles_dst_transition():
    day = datetime(2026, 3, 30, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc),
    )

    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M %Z") == "11:00 CEST"


def test_invalid_work_window_is_rejected():
    with pytest.raises(ValueError):
        parse_work_window("9-17", datetime(2026, 5, 22).date(), BERLIN)


def test_select_scheduling_window_primary_short_wins():
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        window_minutes=300,
        resets_in_seconds=1200,
        session_window_minutes=300,
        session_resets_in_seconds=2400,
    )

    selection = select_scheduling_window(status)

    assert selection is not None
    assert selection.basis == SchedulingWindowBasis.PRIMARY
    assert selection.window_minutes == 300
    assert selection.resets_in_seconds == 1200


def test_select_scheduling_window_uses_session_when_primary_too_long():
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        window_minutes=10080,
        resets_in_seconds=86400,
        session_window_minutes=300,
        session_resets_in_seconds=1800,
    )

    selection = select_scheduling_window(status)

    assert selection is not None
    assert selection.basis == SchedulingWindowBasis.SESSION
    assert selection.window_minutes == 300
    assert selection.resets_in_seconds == 1800


def test_select_scheduling_window_skips_when_no_short_window():
    status = AccountStatus(
        label="daily",
        state=AccountState.ACTIVE,
        window_minutes=10080,
        session_window_minutes=1440,
    )

    assert select_scheduling_window(status) is None


def test_select_scheduling_window_skips_when_session_missing():
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        window_minutes=10080,
    )

    assert select_scheduling_window(status) is None


def test_select_scheduling_window_boundary_is_strict():
    skipped = AccountStatus(label="daily", state=AccountState.ACTIVE, window_minutes=1440)
    allowed = AccountStatus(label="short", state=AccountState.ACTIVE, window_minutes=1439)

    assert select_scheduling_window(skipped) is None
    assert select_scheduling_window(allowed).window_minutes == 1439


def test_select_scheduling_window_respects_manual_targets():
    status = AccountStatus(
        label="mixed",
        state=AccountState.ACTIVE,
        window_minutes=300,
        resets_in_seconds=1200,
        session_window_minutes=180,
        session_resets_in_seconds=600,
    )

    assert select_scheduling_window(status, "primary").basis == SchedulingWindowBasis.PRIMARY
    assert select_scheduling_window(status, "session").basis == SchedulingWindowBasis.SESSION
    assert select_scheduling_window(status, "invalid").basis == SchedulingWindowBasis.PRIMARY


def test_recompute_codex_shaped_status_uses_session_window():
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"codex": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )

    decision = recompute(
        account,
        status,
        config,
        datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    assert decision is not None
    assert decision.kick_at.astimezone(BERLIN).strftime("%H:%M") == "11:00"
    assert decision.windows_needed == 2


def test_recompute_skips_used_session_with_missing_reset():
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=None,
        session_window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"codex": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )

    decision = recompute(
        account,
        status,
        config,
        datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    assert decision is None


def test_pending_kicks_persist_and_prune(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    account = AccountConfig(label="personal", provider="codex", codexbar_account="personal@example.test")
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=status.window_minutes,
        quota_available_at=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    pending = upsert_pending_kick(account, decision)
    loaded = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))

    assert pending_file.exists()
    assert list(loaded) == [pending.account_key]
    assert from_utc_iso(loaded[pending.account_key].kick_at) == decision.kick_at

    save_pending_kicks({pending.account_key: pending})
    pruned = prune_pending_kicks(
        load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)),
        datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert pruned == {}
    assert config.schedule.accounts["personal"].weekdays == "14:00-21:00"


def test_pending_kick_missing_window_basis_loads_as_primary(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    pending_file.write_text(
        """
{
  "account|codex|personal": {
    "account_key": "account|codex|personal",
    "account_label": "personal",
    "provider": "codex",
    "kick_at": "2026-05-22T09:00:00Z",
    "created_at": "2026-05-22T07:00:00Z",
    "reason": "optimal",
    "windows_needed": 2,
    "expected_waste_minutes": 180,
    "waste_location": "pre_work",
    "work_start": "2026-05-22T12:00:00Z",
    "work_end": "2026-05-22T19:00:00Z",
    "notified": true
  }
}
""".strip()
        + "\n"
    )

    pending = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))

    assert pending["account|codex|personal"].window_basis == "primary"


def _pending_kick_fixture(label: str = "personal") -> PendingKick:
    return PendingKick(
        account_key=f"manual|codex|{label}",
        account_label=label,
        provider="codex",
        kick_at="2099-05-22T17:00:00Z",
        created_at="2099-05-22T15:31:00Z",
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2099-05-22T17:00:00Z",
        work_end="2099-05-22T21:00:00Z",
        window_basis="session",
    )


def _isolate_pending_kicks(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    return pending_file


def test_corrupt_pending_kicks_file_is_quarantined_on_load(monkeypatch, tmp_path, capsys):
    pending_file = _isolate_pending_kicks(monkeypatch, tmp_path)
    pending_file.write_text("{not valid json")

    loaded = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))

    assert loaded == {}
    assert not pending_file.exists()
    quarantined = list(tmp_path.glob("pending-kicks.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{not valid json"
    err = capsys.readouterr().err
    assert "was corrupt" in err
    assert str(quarantined[0]) in err


def test_non_dict_pending_kicks_payload_is_quarantined_on_load(monkeypatch, tmp_path, capsys):
    pending_file = _isolate_pending_kicks(monkeypatch, tmp_path)
    pending_file.write_text("[1, 2, 3]\n")

    loaded = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))

    assert loaded == {}
    assert not pending_file.exists()
    assert len(list(tmp_path.glob("pending-kicks.json.corrupt-*"))) == 1
    assert "JSON object" in capsys.readouterr().err


def test_repeated_quarantines_do_not_overwrite_each_other(monkeypatch, tmp_path):
    pending_file = _isolate_pending_kicks(monkeypatch, tmp_path)
    now = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)

    pending_file.write_text("first corrupt payload")
    load_pending_kicks(now)
    pending_file.write_text("second corrupt payload")
    load_pending_kicks(now)

    quarantined = sorted(tmp_path.glob("pending-kicks.json.corrupt-*"))
    assert len(quarantined) == 2
    contents = {path.read_text() for path in quarantined}
    assert contents == {"first corrupt payload", "second corrupt payload"}


def test_save_quarantines_corrupt_file_before_writing(monkeypatch, tmp_path, capsys):
    pending_file = _isolate_pending_kicks(monkeypatch, tmp_path)
    pending_file.write_text("{not valid json")
    pending = _pending_kick_fixture()

    save_pending_kicks({pending.account_key: pending})

    quarantined = list(tmp_path.glob("pending-kicks.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{not valid json"
    loaded = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))
    assert list(loaded) == [pending.account_key]
    assert "before saving new state" in capsys.readouterr().err


def test_save_raises_and_preserves_corrupt_file_when_quarantine_fails(
    monkeypatch,
    tmp_path,
):
    from pathlib import Path

    pending_file = _isolate_pending_kicks(monkeypatch, tmp_path)
    pending_file.write_text("{not valid json")
    pending = _pending_kick_fixture()

    def refuse_rename(self, target):
        raise OSError("rename refused")

    monkeypatch.setattr(Path, "rename", refuse_rename)

    with pytest.raises(PendingKickStateError, match="could not be moved aside"):
        save_pending_kicks({pending.account_key: pending})

    assert pending_file.read_text() == "{not valid json"


def test_save_pending_kicks_raises_on_write_failure(monkeypatch, tmp_path):
    _isolate_pending_kicks(monkeypatch, tmp_path)

    def refuse_write(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("tokenkick.scheduling.atomic_write_text", refuse_write)
    pending = _pending_kick_fixture()

    with pytest.raises(PendingKickStateError, match="disk full"):
        save_pending_kicks({pending.account_key: pending})


def test_upsert_pending_kick_does_not_overwrite_orchestrated(monkeypatch, tmp_path):
    _isolate_pending_kicks(monkeypatch, tmp_path)
    account = AccountConfig(label="personal", provider="codex")
    orchestrated = _pending_kick_fixture()
    orchestrated.reason = ScheduleReason.ORCHESTRATED.value
    save_pending_kicks({orchestrated.account_key: orchestrated})
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    result = upsert_pending_kick(account, decision)

    assert result.reason == ScheduleReason.ORCHESTRATED.value
    assert result.kick_at == orchestrated.kick_at
    stored = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))
    assert stored[orchestrated.account_key].reason == ScheduleReason.ORCHESTRATED.value
    assert stored[orchestrated.account_key].kick_at == orchestrated.kick_at


def test_upsert_pending_kick_still_replaces_smart_schedule_pending(monkeypatch, tmp_path):
    _isolate_pending_kicks(monkeypatch, tmp_path)
    account = AccountConfig(label="personal", provider="codex")
    existing = _pending_kick_fixture()
    save_pending_kicks({existing.account_key: existing})
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    result = upsert_pending_kick(account, decision)

    assert result.reason == decision.reason.value
    assert result.kick_at != existing.kick_at
    stored = load_pending_kicks(datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc))
    assert stored[existing.account_key].kick_at == result.kick_at


def test_upsert_pending_kick_warns_and_returns_fallback_on_save_failure(
    monkeypatch,
    tmp_path,
    capsys,
):
    _isolate_pending_kicks(monkeypatch, tmp_path)

    def refuse_write(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("tokenkick.scheduling.atomic_write_text", refuse_write)
    account = AccountConfig(label="personal", provider="codex")
    day = datetime(2026, 5, 22, tzinfo=BERLIN).date()
    work_start, work_end = parse_work_window("14:00-21:00", day, BERLIN)
    decision = compute_schedule_decision(
        work_start=work_start,
        work_end=work_end,
        window_minutes=300,
        quota_available_at=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    pending = upsert_pending_kick(account, decision)

    assert pending.account_label == "personal"
    err = capsys.readouterr().err
    assert "was not persisted" in err
    assert "disk full" in err
