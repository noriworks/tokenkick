"""Tests for notification message formatting."""

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from tokenkick.models import KickEvent, NotifyConfig, format_notification_timestamp
from tokenkick.notifier import (
    _format_message,
    _format_quota_constrained_message,
    _format_schedule_decision_message,
    _format_scheduled_kick_message,
    _ntfy_header_value,
    _notify_ntfy_message,
    _notification_needs_attention,
    notify_codex_pending_confirmation_missing,
    notify_kick,
    notify_reservation_advisory,
    notify_reset_event,
    notify_schedule_decision,
    notify_scheduled_kick,
    notify_test,
)
from tokenkick.reset_defense import AccountSnapshot, ResetEvent
from tokenkick.scheduling import ScheduleDecision, ScheduleReason, WasteLocation


def test_format_message_uses_weekly_confirmed_wording():
    event = KickEvent(label="codex", timestamp=1000.0, success=True, kind="kick")

    message = _format_message(event)

    assert message == 'TokenKick: Kicked "codex". Weekly quota window is now active.'


def test_format_message_uses_session_confirmed_wording():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        kind="session",
        kick_type="session",
    )

    message = _format_message(event)

    assert message == 'TokenKick: Kicked "codex". Session window is now active.'


def test_format_message_marks_unconfirmed_attempts():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="probe",
        error="Provider still reports a tiny phantom session after the kick attempt",
    )

    message = _format_message(event)

    assert "Attempted kick" in message
    assert "Fresh quota window is now active" not in message
    assert "phantom session" in message


def test_format_message_marks_codex_attempt_surface_context():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        error="Codex completed without assistant output or token evidence",
        codex_surface="legacy",
        codex_attempt=3,
        codex_max_attempts=3,
    )

    message = _format_message(event)

    assert "Attempted kick" in message
    assert "Surface legacy" in message
    assert "attempt 3/3" in message


def test_format_message_treats_pending_codex_session_as_sent_not_warning():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        post_kick_status="pending",
        codex_confirmation_method="pending_reset_clock",
        codex_surface="interactive-like",
        codex_attempt=4,
        codex_max_attempts=4,
    )

    message = _format_message(event)

    assert "Session kick sent" in message
    assert "waiting for provider confirmation" in message
    assert len(message) < 140
    assert "Attempted kick" not in message
    assert "too stale" not in message
    assert _notification_needs_attention(event) is False


def test_format_message_treats_stale_reset_clock_error_as_pending_confirmation():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        error="Codex generated output, but provider status was too stale to confirm the session anchor",
        evidence_response=True,
        evidence_tokens=True,
        post_kick_status="unchanged",
        codex_confirmation_method="pending_reset_clock",
        codex_surface="repo",
        codex_attempt=4,
        codex_max_attempts=4,
    )

    message = _format_message(event)

    assert "Session kick sent" in message
    assert "waiting for provider confirmation" in message
    assert "Attempted kick" not in message
    assert "too stale" not in message
    assert _notification_needs_attention(event) is False


def test_format_message_uses_phantom_recovery_attempt_wording():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="phantom_recovery",
        kick_type="session",
    )

    message = _format_message(event)

    assert message == (
        'TokenKick: Attempted phantom recovery for "codex". '
        "Waiting for Codex to expose a session anchor."
    )


def test_format_message_uses_phantom_recovery_confirmed_wording():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=True,
        kind="phantom_recovery",
        kick_type="session",
    )

    message = _format_message(event)

    assert message == 'TokenKick: Recovered "codex". Codex session anchor is now visible.'


def test_format_message_uses_provider_accepted_ambiguous_wording():
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="phantom_recovery",
        kick_type="session",
        error="Codex accepted usage, but session status is still ambiguous",
        codex_surface="repo",
        codex_attempt=2,
        codex_max_attempts=3,
    )

    message = _format_message(event)

    assert message == (
        'TokenKick: Attempted phantom recovery for "codex". '
        "Codex accepted usage, but session anchor is still ambiguous. "
        "Surface repo, attempt 2/3."
    )
    assert _notification_needs_attention(event) is True


def test_format_message_keeps_failure_error_style():
    event = KickEvent(label="codex", timestamp=1000.0, success=False, error="boom")

    message = _format_message(event)

    assert format_notification_timestamp(1000.0) in message
    assert message.startswith('TokenKick: Failed to kick "codex"')
    assert "Error: boom" in message


def test_notify_kick_returns_false_when_disabled():
    event = KickEvent(label="codex", timestamp=1000.0, success=True)

    assert notify_kick(event, NotifyConfig(enabled=False)) is False


def test_notify_kick_errors_policy_suppresses_routine_success(monkeypatch):
    calls = []
    event = KickEvent(label="codex", timestamp=1000.0, success=True, confirmed=True)
    monkeypatch.setattr(
        "tokenkick.notifier._notify_ntfy_message",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )

    delivered = notify_kick(
        event,
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic", policy="errors"),
    )

    assert delivered is None
    assert calls == []


def test_notify_kick_errors_policy_sends_failures(monkeypatch):
    calls = []
    event = KickEvent(label="codex", timestamp=1000.0, success=False, error="boom")
    monkeypatch.setattr(
        "tokenkick.notifier._notify_ntfy_message",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )

    delivered = notify_kick(
        event,
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic", policy="errors"),
    )

    assert delivered is True
    assert calls == [True]


def test_notify_kick_errors_policy_suppresses_pending_codex_confirmation(monkeypatch):
    calls = []
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        codex_confirmation_method="pending_reset_clock",
        codex_surface="repo",
        codex_attempt=1,
        codex_max_attempts=2,
    )
    monkeypatch.setattr(
        "tokenkick.notifier._notify_ntfy_message",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )

    delivered = notify_kick(
        event,
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic", policy="errors"),
    )

    assert delivered is None
    assert calls == []


def test_notify_schedule_decision_errors_policy_suppresses_routine_schedule(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tokenkick.notifier._notify_ntfy_message",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )

    delivered = notify_schedule_decision(
        "personal",
        _decision(),
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic", policy="errors"),
    )

    assert delivered is None
    assert calls == []


def test_notify_reservation_advisory_uses_warning_title(monkeypatch):
    calls = []

    def fake_raw(message, config, *, title, priority, tags):
        calls.append((message, config, title, priority, tags))
        return True

    monkeypatch.setattr("tokenkick.notifier._notify_raw", fake_raw)

    delivered = notify_reservation_advisory(
        '"codex" is reserved for an orchestration kick at 18:30.',
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
    )

    assert delivered is True
    message, _config, title, priority, tags = calls[0]
    assert message == 'TokenKick: "codex" is reserved for an orchestration kick at 18:30.'
    assert title == "TokenKick — Reserved account"
    assert priority == "default"
    assert tags == "warning"


def test_notify_codex_pending_confirmation_missing_uses_account_check_wording(monkeypatch):
    calls = []

    def fake_raw(message, config, *, title, priority, tags):
        calls.append((message, config, title, priority, tags))
        return True

    monkeypatch.setattr("tokenkick.notifier._notify_raw", fake_raw)

    delivered = notify_codex_pending_confirmation_missing(
        "codex (work)",
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
    )

    assert delivered is True
    message, _config, title, priority, tags = calls[0]
    assert title == "TokenKick — Check account"
    assert priority == "default"
    assert tags == "warning"
    assert 'could not confirm that Codex opened a new session window for "codex (work)"' in message
    assert "Please check the Codex app before relying on this account." in message


def test_notify_ntfy_message_returns_delivery_status(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))

    delivered = _notify_ntfy_message(
        "hello",
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        title="TokenKick",
        priority="default",
        tags="zap",
    )

    assert delivered is True
    assert calls


def test_notify_ntfy_message_sanitizes_title_header(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))

    delivered = _notify_ntfy_message(
        "hello",
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        title="TokenKick — Check",
        priority="default",
        tags="warning",
    )

    assert delivered is True
    assert calls[0][1]["headers"]["Title"] == "TokenKick - Check"


def test_ntfy_header_value_drops_non_latin1_header_chars():
    assert _ntfy_header_value("TokenKick ✅ — Check") == "TokenKick  - Check"


def test_notify_test_uses_configured_backend(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))

    delivered = notify_test(NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"))

    assert delivered is True
    assert calls[0][1]["headers"]["Title"] == "TokenKick"


def test_notify_reset_event_sends_possible_confidence(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=fake_post))
    event = ResetEvent(
        id="reset-1",
        detected_at="2026-06-04T00:34:00Z",
        provider="codex",
        confidence="possible",
        affected_accounts=["codex (personal)", "codex (work)"],
        trigger="usage_drop",
        account_snapshots=[
            AccountSnapshot(
                account="codex (personal)",
                before_state="active",
                before_weekly_used_pct=50,
                before_weekly_resets_at="2026-06-07T00:34:00Z",
                after_state="active",
                after_weekly_used_pct=0,
                after_weekly_resets_at="2026-06-11T00:34:00Z",
            )
        ],
        total_quota_hours_lost=157.2,
        previous_reset_predictions={"codex (personal)": "2026-06-07T00:34:00Z"},
        new_reset_predictions={"codex (personal)": "2026-06-11T00:34:00Z"},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="Codex global reset possible.",
        detail="Two accounts moved together.",
    )

    delivered = notify_reset_event(
        event,
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
    )

    assert delivered is True
    assert calls[0][1]["headers"]["Title"] == "TokenKick - Global Reset"
    assert calls[0][1]["headers"]["Priority"] == "default"
    assert "Confidence: possible" in calls[0][1]["content"].decode()


def test_notify_ntfy_message_returns_false_for_http_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(post=lambda *_args, **_kwargs: SimpleNamespace(status_code=500)),
    )

    delivered = _notify_ntfy_message(
        "hello",
        NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        title="TokenKick",
        priority="default",
        tags="zap",
    )

    assert delivered is False


def _decision(reason=ScheduleReason.OPTIMAL):
    return ScheduleDecision(
        kick_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
        reason=reason,
        windows_needed=2,
        expected_waste_minutes=0 if reason == ScheduleReason.OPTIMAL else 90,
        waste_location=WasteLocation.NONE if reason == ScheduleReason.OPTIMAL else WasteLocation.POST_WORK,
        work_start=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 5, 22, 19, 0, tzinfo=timezone.utc),
        optimal_kick_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
    )


def test_format_schedule_decision_message():
    message = _format_schedule_decision_message("personal", _decision())

    assert '"personal" is fresh' in message
    assert "Scheduled kick" in message
    assert "workday" in message


def test_format_scheduled_kick_message():
    event = KickEvent(label="personal", timestamp=1_779_450_000, success=True)

    message = _format_scheduled_kick_message(event, _decision())

    assert 'Kicked "personal"' in message
    assert "zero work-time waste" in message


def test_notify_scheduled_kick_uses_pending_wording_for_unconfirmed_attempt(monkeypatch):
    delivered = []
    event = KickEvent(
        label="personal",
        timestamp=1_779_450_000,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        post_kick_status="pending",
        codex_confirmation_method="pending_reset_clock",
        codex_surface="repo",
    )
    config = NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="tokenkick")
    monkeypatch.setattr(
        "tokenkick.notifier._notify_ntfy_message",
        lambda message, *_args, **_kwargs: delivered.append(message) or True,
    )

    assert notify_scheduled_kick(event, _decision(), config) is True

    assert len(delivered) == 1
    assert "Session kick sent" in delivered[0]
    assert 'Kicked "personal"' not in delivered[0]
    assert "zero work-time waste" not in delivered[0]


def test_format_quota_constrained_message():
    event = KickEvent(label="personal", timestamp=1_779_473_400, success=True)

    message = _format_quota_constrained_message(
        event,
        _decision(reason=ScheduleReason.QUOTA_CONSTRAINED),
    )

    assert "Optimal time was" in message
    assert "post-work waste unavoidable" in message
