from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    DataSource,
    KickEvent,
    account_key_string,
    mark_synthetic_status,
)
from tokenkick.reset_defense import (
    AccountSnapshot,
    ResetEvent,
    acknowledge_reset_events,
    append_reset_event,
    detect_global_reset_event,
    detect_provider_reset_observations,
    detect_reset_events,
    filter_reset_events,
    invalidate_event_pending_kicks,
    is_provider_reset_observation,
    load_reset_events,
    reset_events_csv,
)
from tokenkick.scheduling import PendingKick, save_pending_kicks, to_utc_iso


NOW = datetime(2026, 6, 4, 14, 32, tzinfo=timezone.utc)


def _account(label: str, provider: str = "codex") -> AccountConfig:
    return AccountConfig(
        label=label,
        provider=provider,
        source=DataSource.CODEX_DIRECT if provider == "codex" else DataSource.CLAUDE_DIRECT,
        visible=True,
    )


def _status(
    label: str,
    state: AccountState,
    *,
    used: float | None,
    resets_at: datetime | None,
    session_resets_at: datetime | None = None,
    observed_at: datetime = NOW,
) -> AccountStatus:
    return AccountStatus(
        label=label,
        state=state,
        used_percent=used,
        resets_at=resets_at.timestamp() if resets_at else None,
        session_resets_at=session_resets_at.timestamp() if session_resets_at else None,
        observed_at=to_utc_iso(observed_at),
    )


def _entry(account: AccountConfig, status: AccountStatus, observed_at: datetime = NOW) -> dict:
    return {
        "account": account,
        "status": status,
        "cached_at": to_utc_iso(observed_at),
        "provider_observed_at": to_utc_iso(observed_at),
        "refresh_error": None,
    }


def _detect(
    accounts: list[AccountConfig],
    previous_statuses: list[AccountStatus],
    current_statuses: list[AccountStatus],
    *,
    history: list[KickEvent] | None = None,
    restarted_from_disk: bool = False,
):
    previous = {
        account_key_string(account): _entry(account, status)
        for account, status in zip(accounts, previous_statuses, strict=False)
    }
    statuses_by_key = {
        account_key_string(account): status
        for account, status in zip(accounts, current_statuses, strict=False)
    }
    return detect_global_reset_event(
        previous_entries=previous,
        accounts=accounts,
        statuses_by_key=statuses_by_key,
        kick_history=history or [],
        now=NOW,
        restarted_from_disk=restarted_from_disk,
    )


def test_two_codex_accounts_go_fresh_detected_likely():
    accounts = [_account("secondary"), _account("reserve")]
    previous = [
        _status("secondary", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("reserve", AccountState.WAITING, used=20, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("secondary", AccountState.FRESH, used=0, resets_at=None),
        _status("reserve", AccountState.FRESH, used=0, resets_at=None),
    ]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.provider == "codex"
    assert event.trigger == "simultaneous_fresh"
    assert event.confidence == "likely"
    assert event.affected_accounts == ["secondary", "reserve"]


def test_three_accounts_confirmed():
    accounts = [_account("a"), _account("b"), _account("c")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=2)),
        _status("c", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
    ]
    current = [_status(account.label, AccountState.FRESH, used=0, resets_at=None) for account in accounts]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.confidence == "confirmed"


def test_codex_main_and_spark_same_home_do_not_count_as_two_accounts():
    main = replace(_account("codex-main"), provider_home="/tmp/codex-home")
    spark = replace(
        _account("codex-spark"),
        provider_home="/tmp/codex-home",
        codex_rate_limit_id="codex_bengalfox",
    )
    accounts = [main, spark]
    previous = [
        _status("codex-main", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("codex-spark", AccountState.ACTIVE, used=10, resets_at=NOW + timedelta(days=2)),
    ]
    current = [
        _status("codex-main", AccountState.FRESH, used=0, resets_at=None),
        _status("codex-spark", AccountState.FRESH, used=0, resets_at=None),
    ]

    assert _detect(accounts, previous, current) is None


def test_one_account_change_is_not_global_reset():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=30, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.FRESH, used=0, resets_at=None),
        _status("b", AccountState.ACTIVE, used=30, resets_at=NOW + timedelta(days=3)),
    ]

    assert _detect(accounts, previous, current) is None


def test_different_providers_do_not_correlate():
    accounts = [_account("codex", "codex"), _account("claude", "claude")]
    previous = [
        _status("codex", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("claude", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("codex", AccountState.FRESH, used=0, resets_at=None),
        _status("claude", AccountState.FRESH, used=0, resets_at=None),
    ]

    assert _detect(accounts, previous, current) is None


def test_recent_tokenkick_kick_excludes_account_from_correlation():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.FRESH, used=0, resets_at=None),
        _status("b", AccountState.FRESH, used=0, resets_at=None),
    ]
    history = [KickEvent(label="a", timestamp=(NOW - timedelta(minutes=1)).timestamp(), success=True)]

    assert _detect(accounts, previous, current, history=history) is None


def test_reset_timestamp_shift_detected():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=4)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=6)),
    ]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.trigger == "reset_shift"


def test_weekly_usage_drop_detected():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=55, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=70, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.ACTIVE, used=5, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=0, resets_at=NOW + timedelta(days=3)),
    ]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.trigger == "usage_drop"


def test_single_account_usage_drop_observation_detected():
    account = _account("claude", "claude")
    previous = _status(
        "claude",
        AccountState.ACTIVE,
        used=16,
        resets_at=NOW + timedelta(days=2, hours=14),
    )
    current = _status(
        "claude",
        AccountState.ACTIVE,
        used=1,
        resets_at=NOW + timedelta(days=2, hours=14),
        observed_at=NOW + timedelta(minutes=9),
    )
    previous_entries = {account_key_string(account): _entry(account, previous)}
    statuses_by_key = {account_key_string(account): current}

    events = detect_provider_reset_observations(
        previous_entries=previous_entries,
        accounts=[account],
        statuses_by_key=statuses_by_key,
        kick_history=[],
        now=NOW + timedelta(minutes=9),
    )

    assert len(events) == 1
    event = events[0]
    assert is_provider_reset_observation(event)
    assert event.trigger == "single_account_usage_drop"
    assert event.confidence == "possible"
    assert event.total_quota_hours_lost is None
    assert "provider reset observation" in event.summary
    assert "16% -> 1%" in event.detail
    assert "did not materially change" in event.detail


def test_single_account_weekly_reset_observation_detected():
    account = _account("claude", "claude")
    previous = _status(
        "claude",
        AccountState.ACTIVE,
        used=20,
        resets_at=NOW + timedelta(days=2),
    )
    current = _status(
        "claude",
        AccountState.ACTIVE,
        used=0,
        resets_at=NOW + timedelta(days=6, hours=23),
        observed_at=NOW + timedelta(minutes=5),
    )
    previous_entries = {account_key_string(account): _entry(account, previous)}
    statuses_by_key = {account_key_string(account): current}

    events = detect_provider_reset_observations(
        previous_entries=previous_entries,
        accounts=[account],
        statuses_by_key=statuses_by_key,
        kick_history=[],
        now=NOW + timedelta(minutes=5),
    )

    assert len(events) == 1
    assert events[0].trigger == "single_account_weekly_reset"
    assert "moved materially later" in events[0].detail


def test_single_account_observation_exclusions():
    account = _account("claude", "claude")
    previous = _status(
        "claude",
        AccountState.ACTIVE,
        used=16,
        resets_at=NOW + timedelta(days=2),
    )
    base_current = _status(
        "claude",
        AccountState.ACTIVE,
        used=1,
        resets_at=NOW + timedelta(days=2),
        observed_at=NOW + timedelta(minutes=5),
    )
    previous_entries = {account_key_string(account): _entry(account, previous)}

    stale = replace(base_current, stale=True)
    unknown = replace(base_current, state=AccountState.UNKNOWN)
    synthetic = mark_synthetic_status(base_current, "codex_session_due_from_cache")
    recently_kicked = [KickEvent(label="claude", timestamp=(NOW + timedelta(minutes=1)).timestamp(), success=True)]

    for status, history in (
        (_status("claude", AccountState.ACTIVE, used=10, resets_at=NOW + timedelta(days=2), observed_at=NOW + timedelta(minutes=5)), []),
        (stale, []),
        (unknown, []),
        (synthetic, []),
        (base_current, recently_kicked),
        (replace(base_current, observed_at=to_utc_iso(NOW)), []),
    ):
        assert (
            detect_provider_reset_observations(
                previous_entries=previous_entries,
                accounts=[account],
                statuses_by_key={account_key_string(account): status},
                kick_history=history,
                now=NOW + timedelta(minutes=5),
            )
            == []
        )


def test_detect_reset_events_suppresses_single_observation_for_global_accounts():
    accounts = [_account("a"), _account("b")]
    previous_entries = {
        account_key_string(accounts[0]): _entry(
            accounts[0],
            _status("a", AccountState.ACTIVE, used=55, resets_at=NOW + timedelta(days=1)),
        ),
        account_key_string(accounts[1]): _entry(
            accounts[1],
            _status("b", AccountState.ACTIVE, used=70, resets_at=NOW + timedelta(days=3)),
        ),
    }
    statuses_by_key = {
        account_key_string(accounts[0]): _status(
            "a",
            AccountState.ACTIVE,
            used=5,
            resets_at=NOW + timedelta(days=1),
            observed_at=NOW + timedelta(minutes=5),
        ),
        account_key_string(accounts[1]): _status(
            "b",
            AccountState.ACTIVE,
            used=0,
            resets_at=NOW + timedelta(days=3),
            observed_at=NOW + timedelta(minutes=5),
        ),
    }

    events = detect_reset_events(
        previous_entries=previous_entries,
        accounts=accounts,
        statuses_by_key=statuses_by_key,
        kick_history=[],
        now=NOW + timedelta(minutes=5),
    )

    assert len(events) == 1
    assert events[0].trigger == "usage_drop"


def test_synthetic_predicted_due_status_excluded_from_reset_correlation():
    accounts = [_account("predicted"), _account("live")]
    previous = [
        _status("predicted", AccountState.ACTIVE, used=55, resets_at=NOW + timedelta(days=1)),
        _status("live", AccountState.ACTIVE, used=70, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        mark_synthetic_status(
            _status("predicted", AccountState.ACTIVE, used=5, resets_at=NOW + timedelta(days=4)),
            "codex_session_due_from_cache",
        ),
        _status("live", AccountState.ACTIVE, used=0, resets_at=NOW + timedelta(days=6)),
    ]

    assert _detect(accounts, previous, current) is None


def test_session_only_changes_ignored():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=55, resets_at=NOW + timedelta(days=1), session_resets_at=NOW),
        _status("b", AccountState.ACTIVE, used=70, resets_at=NOW + timedelta(days=3), session_resets_at=NOW),
    ]
    current = [
        _status("a", AccountState.ACTIVE, used=55, resets_at=NOW + timedelta(days=1), session_resets_at=NOW + timedelta(hours=5)),
        _status("b", AccountState.ACTIVE, used=70, resets_at=NOW + timedelta(days=3), session_resets_at=NOW + timedelta(hours=5)),
    ]

    assert _detect(accounts, previous, current) is None


def test_restart_from_disk_caps_confidence_at_possible():
    accounts = [_account("a"), _account("b"), _account("c")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
        _status("c", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=5)),
    ]
    current = [_status(account.label, AccountState.FRESH, used=0, resets_at=None) for account in accounts]

    event = _detect(accounts, previous, current, restarted_from_disk=True)

    assert event is not None
    assert event.confidence == "possible"


def test_different_current_poll_cycles_not_correlated():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.FRESH, used=0, resets_at=None, observed_at=NOW),
        _status("b", AccountState.FRESH, used=0, resets_at=None, observed_at=NOW + timedelta(minutes=2)),
    ]

    assert _detect(accounts, previous, current) is None


def test_impact_uses_remaining_quota_not_used_quota():
    accounts = [_account("a"), _account("b")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
        _status("b", AccountState.ACTIVE, used=100, resets_at=NOW + timedelta(days=3)),
    ]
    current = [
        _status("a", AccountState.FRESH, used=0, resets_at=None),
        _status("b", AccountState.FRESH, used=0, resets_at=None),
    ]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.total_quota_hours_lost == 36.0


def test_failover_guidance_recommends_non_affected_account():
    accounts = [_account("a"), _account("b"), _account("backup", "claude")]
    previous = [
        _status("a", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=1)),
        _status("b", AccountState.ACTIVE, used=50, resets_at=NOW + timedelta(days=3)),
        _status("backup", AccountState.ACTIVE, used=20, resets_at=NOW + timedelta(days=2)),
    ]
    current = [
        _status("a", AccountState.FRESH, used=0, resets_at=None),
        _status("b", AccountState.FRESH, used=0, resets_at=None),
        _status("backup", AccountState.ACTIVE, used=20, resets_at=NOW + timedelta(days=2)),
    ]

    event = _detect(accounts, previous, current)

    assert event is not None
    assert event.failover_guidance is not None
    assert "backup" in event.failover_guidance


def test_possible_event_does_not_invalidate_pending_kicks(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    account = _account("a")
    pending = PendingKick(
        account_key="identity|codex|a",
        account_label="a",
        provider="codex",
        kick_at=to_utc_iso(NOW + timedelta(hours=1)),
        created_at=to_utc_iso(NOW),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(NOW),
        work_end=to_utc_iso(NOW + timedelta(hours=8)),
    )
    save_pending_kicks({pending.account_key: pending})
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="possible",
        affected_accounts=[account.label],
        trigger="simultaneous_fresh",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="possible",
        detail="possible",
    )

    removed = invalidate_event_pending_kicks(event)

    assert removed == []
    assert pending_file.exists()


def test_likely_event_does_not_invalidate_pending_kicks(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    future_kick_at = datetime.now(timezone.utc) + timedelta(hours=1)
    pending = PendingKick(
        account_key="identity|codex|a",
        account_label="a",
        provider="codex",
        kick_at=to_utc_iso(future_kick_at),
        created_at=to_utc_iso(NOW),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(NOW),
        work_end=to_utc_iso(NOW + timedelta(hours=8)),
    )
    save_pending_kicks({pending.account_key: pending})
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="likely",
        affected_accounts=["a"],
        trigger="simultaneous_fresh",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="likely",
        detail="likely",
    )

    removed = invalidate_event_pending_kicks(event)

    assert removed == []
    assert event.pending_kicks_invalidated == []
    assert pending_file.exists()


def test_confirmed_event_invalidates_pending_kicks(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    pending = PendingKick(
        account_key="identity|codex|a",
        account_label="a",
        provider="codex",
        kick_at=to_utc_iso(future),
        created_at=to_utc_iso(future - timedelta(minutes=5)),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(future),
        work_end=to_utc_iso(future + timedelta(hours=8)),
    )
    save_pending_kicks({pending.account_key: pending})
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="confirmed",
        affected_accounts=["a"],
        trigger="simultaneous_fresh",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="confirmed",
        detail="confirmed",
    )

    removed = invalidate_event_pending_kicks(event)

    assert [pending.account_label for pending in removed] == ["a"]
    assert event.pending_kicks_invalidated == ["a"]


def test_single_account_observation_never_invalidates_pending_kicks(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    pending = PendingKick(
        account_key="identity|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at=to_utc_iso(future),
        created_at=to_utc_iso(future - timedelta(minutes=5)),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(future),
        work_end=to_utc_iso(future + timedelta(hours=8)),
    )
    save_pending_kicks({pending.account_key: pending})
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="claude",
        confidence="confirmed",
        affected_accounts=["claude"],
        trigger="single_account_usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=None,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="observation",
        detail="observation",
    )

    removed = invalidate_event_pending_kicks(event)

    assert removed == []
    assert event.pending_kicks_invalidated == []


def test_event_log_dedup_and_exports(monkeypatch, tmp_path):
    path = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", path)
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="likely",
        affected_accounts=["a", "b"],
        trigger="usage_drop",
        account_snapshots=[
            AccountSnapshot("a", "active", 50, to_utc_iso(NOW), "fresh", 0, None)
        ],
        total_quota_hours_lost=12,
        previous_reset_predictions={"a": to_utc_iso(NOW)},
        new_reset_predictions={"a": None},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="summary",
        detail="detail",
    )

    assert append_reset_event(event) is True
    assert append_reset_event(event) is False
    loaded = load_reset_events()

    assert len(loaded) == 1
    assert filter_reset_events(loaded, provider="codex") == loaded
    assert "affected_accounts" in reset_events_csv(loaded)
    assert loaded[0].acknowledged_at is None
    assert loaded[0].recovery_action is None
    assert loaded[0].to_dict()["account_impacts"][0]["quota_hours_lost"] == 0.0


def test_ack_rewrite_is_atomic_when_replace_fails(monkeypatch, tmp_path):
    path = tmp_path / "reset-events.jsonl"
    event = ResetEvent(
        id="event",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="likely",
        affected_accounts=["a"],
        trigger="usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="summary",
        detail="detail",
    )
    assert append_reset_event(event, path=path)
    before = path.read_text()

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("tokenkick.reset_defense.os.replace", fail_replace)

    try:
        acknowledge_reset_events(event_ids=["event"], path=path, now=NOW)
    except OSError:
        pass

    assert path.read_text() == before
    loaded = load_reset_events(path=path)
    assert len(loaded) == 1
    assert loaded[0].acknowledged_at is None


def test_ack_rewrite_and_append_share_lock_without_losing_events(tmp_path):
    path = tmp_path / "reset-events.jsonl"
    first = ResetEvent(
        id="first",
        detected_at=to_utc_iso(NOW),
        provider="codex",
        confidence="likely",
        affected_accounts=["a"],
        trigger="usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="first",
        detail="first",
    )
    second = ResetEvent(
        id="second",
        detected_at=to_utc_iso(NOW + timedelta(minutes=20)),
        provider="codex",
        confidence="confirmed",
        affected_accounts=["b"],
        trigger="usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=0,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="second",
        detail="second",
    )
    assert append_reset_event(first, path=path)

    ack_thread = threading.Thread(
        target=lambda: acknowledge_reset_events(event_ids=["first"], path=path, now=NOW)
    )
    append_thread = threading.Thread(target=lambda: append_reset_event(second, path=path))

    ack_thread.start()
    append_thread.start()
    ack_thread.join()
    append_thread.join()

    loaded = load_reset_events(path=path)
    assert [event.id for event in loaded] == ["first", "second"]
    assert loaded[0].acknowledged_at == to_utc_iso(NOW)
