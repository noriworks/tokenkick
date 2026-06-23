"""Notification backends for TokenKick."""

from __future__ import annotations

from datetime import datetime

from .models import KickEvent, NotifyConfig, format_notification_timestamp
from .reset_defense import ResetEvent
from .scheduling import PendingKick, ScheduleDecision, from_utc_iso

PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR = "Codex accepted usage, but session status is still ambiguous"
CODEX_SESSION_ANCHOR_PENDING_ERROR = (
    "Codex generated output, but provider status was too stale to confirm the session anchor"
)


def notify_kick(event: KickEvent, config: NotifyConfig) -> bool:
    """Send a notification about a kick event."""
    if not config.enabled:
        return False

    if config.backend == "ntfy":
        return _notify_ntfy(event, config)
    if config.backend == "telegram":
        return _notify_telegram(event, config)
    return False


def notify_schedule_decision(
    account_label: str,
    decision: ScheduleDecision | PendingKick,
    config: NotifyConfig,
) -> bool:
    """Notify that a fresh window has been scheduled for a later kick."""
    if not config.enabled:
        return False
    message = _format_schedule_decision_message(account_label, decision)
    return _notify_raw(message, config, title="TokenKick — Scheduled", priority="default", tags="clock1")


def notify_scheduled_kick(
    event: KickEvent,
    decision: ScheduleDecision | PendingKick,
    config: NotifyConfig,
) -> bool:
    """Notify that a scheduled kick ran."""
    if not config.enabled:
        return False
    if not event.success or not event.confirmed:
        return notify_kick(event, config)
    message = _format_scheduled_kick_message(event, decision)
    return _notify_raw(message, config, title="TokenKick", priority="default", tags="zap")


def notify_quota_constrained_kick(
    event: KickEvent,
    decision: ScheduleDecision,
    config: NotifyConfig,
) -> bool:
    """Notify that a kick ran late because quota became available after the optimum."""
    if not config.enabled:
        return False
    if not event.success:
        return notify_kick(event, config)
    message = _format_quota_constrained_message(event, decision)
    return _notify_raw(message, config, title="TokenKick — Check", priority="default", tags="warning")


def notify_dormant_account(account_label: str, config: NotifyConfig) -> bool:
    """Notify that a dormant account needs a one-time wake kick."""
    if not config.enabled:
        return False
    message = (
        f'TokenKick: Account "{account_label}" is dormant. '
        f'Run `tk wake "{account_label}"` to bootstrap it.'
    )
    return _notify_raw(message, config, title="TokenKick — Dormant", priority="default", tags="warning")


def notify_codex_pending_confirmation_missing(account_label: str, config: NotifyConfig) -> bool:
    """Notify that a pending Codex session kick did not confirm after recovery chances."""
    if not config.enabled:
        return False
    message = (
        f'TokenKick could not confirm that Codex opened a new session window for "{account_label}". '
        "Please check the Codex app before relying on this account."
    )
    return _notify_raw(
        message,
        config,
        title="TokenKick — Check account",
        priority="default",
        tags="warning",
    )


def notify_reservation_advisory(message: str, config: NotifyConfig) -> bool:
    """Notify that an orchestration-reserved account should be left idle."""
    if not config.enabled:
        return False
    return _notify_raw(
        f"TokenKick: {message}",
        config,
        title="TokenKick — Reserved account",
        priority="default",
        tags="warning",
    )


def notify_test(config: NotifyConfig) -> bool:
    """Send a test notification with the configured backend."""
    if not config.enabled:
        return False
    return _notify_raw(
        "TokenKick: Test notification.",
        config,
        title="TokenKick",
        priority="default",
        tags="white_check_mark",
    )


def notify_reset_event(event: ResetEvent, config: NotifyConfig) -> bool:
    """Notify that a likely or confirmed global provider reset was detected."""
    if not config.enabled:
        return False
    message = _format_reset_event_message(event)
    priority = "high" if event.confidence == "confirmed" else "default"
    return _notify_raw(
        message,
        config,
        title="TokenKick - Global Reset",
        priority=priority,
        tags="warning",
    )


def _format_message(event: KickEvent) -> str:
    """Format a kick event into a notification message."""
    if not event.success:
        ts = format_notification_timestamp(event.timestamp)
        return f'TokenKick: Failed to kick "{event.label}" at {ts}. Error: {event.error}'

    kind = event.kind
    kick_type = event.kick_type or kind
    if kind == "phantom_recovery":
        codex_context = _codex_attempt_context(event)
        if not event.confirmed:
            if event.error == PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR:
                return (
                    f'TokenKick: Attempted phantom recovery for "{event.label}". '
                    "Codex accepted usage, but session anchor is still ambiguous."
                    f"{codex_context}"
                )
            return (
                f'TokenKick: Attempted phantom recovery for "{event.label}". '
                f"Waiting for Codex to expose a session anchor.{codex_context}"
            )
        return f'TokenKick: Recovered "{event.label}". Codex session anchor is now visible.'

    if not event.confirmed:
        if _codex_session_confirmation_pending(event):
            return (
                f'TokenKick: Session kick sent for "{event.label}". '
                "Codex accepted the work; confirmation should appear on the next provider read. "
                f"No action needed for now.{_codex_attempt_context(event)}"
            )
        detail = event.error or "provider status was still ambiguous after the attempt"
        return f'TokenKick: Attempted kick for "{event.label}". {detail}.{_codex_attempt_context(event)}'

    if kick_type == "session":
        return f'TokenKick: Kicked "{event.label}". Session window is now active.'
    if kick_type == "kick":
        return f'TokenKick: Kicked "{event.label}". Weekly quota window is now active.'
    return f'TokenKick: Kicked "{event.label}". Kick confirmed.'


def _codex_attempt_context(event: KickEvent) -> str:
    if not event.codex_surface and event.codex_attempt is None:
        return ""
    parts = []
    if event.codex_surface:
        parts.append(f"surface {event.codex_surface}")
    if event.codex_attempt is not None and event.codex_max_attempts is not None:
        parts.append(f"attempt {event.codex_attempt}/{event.codex_max_attempts}")
    elif event.codex_attempt is not None:
        parts.append(f"attempt {event.codex_attempt}")
    if not parts:
        return ""
    return " " + ", ".join(parts).capitalize() + "."


def _codex_session_confirmation_pending(event: KickEvent) -> bool:
    pending_reset_clock = (
        event.codex_confirmation_method == "pending_reset_clock"
        or event.error == CODEX_SESSION_ANCHOR_PENDING_ERROR
    )
    return (
        event.success
        and not event.confirmed
        and (event.kick_type or event.kind) == "session"
        and bool(event.codex_surface or event.codex_attempt is not None)
        and pending_reset_clock
        and _codex_generation_evidence(event)
    )


def _codex_generation_evidence(event: KickEvent) -> bool:
    return bool(event.evidence_response or event.evidence_tokens or event.response_text)


def _format_reset_event_message(event: ResetEvent) -> str:
    loss = ""
    if event.total_quota_hours_lost is not None and event.total_quota_hours_lost > 0:
        loss = f" ~{event.total_quota_hours_lost:g}h saved quota may be lost."
    affected = ", ".join(event.affected_accounts)
    guidance = f" {event.failover_guidance}" if event.failover_guidance else ""
    return (
        f"⚠️ TokenKick: Global reset detected on {event.provider.title()}. "
        f"Affected: {affected}.{loss} Confidence: {event.confidence}. "
        f"Run `tk reset-log` for details.{guidance}"
    )


def _format_schedule_decision_message(
    account_label: str,
    decision: ScheduleDecision | PendingKick,
) -> str:
    kick_at = _decision_timestamp(decision, "kick_at")
    work_start = _decision_timestamp(decision, "work_start")
    work_end = _decision_timestamp(decision, "work_end")
    return (
        f'🕐 TokenKick: "{account_label}" is fresh. Scheduled kick at '
        f"{format_notification_timestamp(kick_at)} (optimal for your "
        f"{_format_clock_range(work_start, work_end)} workday)."
    )


def _format_scheduled_kick_message(
    event: KickEvent,
    decision: ScheduleDecision | PendingKick,
) -> str:
    ts = format_notification_timestamp(event.timestamp)
    work_start = _decision_timestamp(decision, "work_start")
    work_end = _decision_timestamp(decision, "work_end")
    waste = _decision_value(decision, "expected_waste_minutes")
    waste_text = "zero work-time waste" if int(waste) == 0 else f"{_format_hours(waste)} expected waste"
    return (
        f'TokenKick: Kicked "{event.label}" at {ts}. Your windows cover '
        f"{_format_clock_range(work_start, work_end)} with {waste_text}."
    )


def _format_quota_constrained_message(event: KickEvent, decision: ScheduleDecision) -> str:
    ts = format_notification_timestamp(event.timestamp)
    optimal = format_notification_timestamp(decision.optimal_kick_at.timestamp())
    waste = _format_hours(decision.expected_waste_minutes)
    return (
        f'TokenKick: Kicked "{event.label}" at {ts}. Optimal time was {optimal} '
        f"— {waste} of post-work waste unavoidable today."
    )


def _decision_timestamp(decision: ScheduleDecision | PendingKick, field: str) -> float:
    value = getattr(decision, field)
    if isinstance(value, str):
        return from_utc_iso(value).timestamp()
    return value.timestamp()


def _decision_value(decision: ScheduleDecision | PendingKick, field: str):
    return getattr(decision, field)


def _format_clock_range(start_ts: float, end_ts: float) -> str:
    start = datetime.fromtimestamp(start_ts).astimezone()
    end = datetime.fromtimestamp(end_ts).astimezone()
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def _format_hours(minutes: int | float) -> str:
    minutes = int(minutes)
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    hours = minutes / 60
    return f"{hours:.1f}h"


def _notify_raw(
    message: str,
    config: NotifyConfig,
    *,
    title: str,
    priority: str,
    tags: str,
) -> bool:
    if config.backend == "ntfy":
        return _notify_ntfy_message(message, config, title=title, priority=priority, tags=tags)
    if config.backend == "telegram":
        return _notify_telegram_message(message, config)
    return False


def _notify_ntfy(event: KickEvent, config: NotifyConfig) -> bool:
    """Send notification via ntfy.sh."""
    topic = config.ntfy_topic
    if not topic:
        return False

    message = _format_message(event)
    check = _notification_needs_attention(event)
    if event.success and not check:
        title = "TokenKick"
    elif event.success:
        title = "TokenKick — Check"
    else:
        title = "TokenKick — Error"
    priority = "default" if event.success else "high"
    tags = "zap" if event.success and not check else "warning"

    return _notify_ntfy_message(message, config, title=title, priority=priority, tags=tags)


def _notification_needs_attention(event: KickEvent) -> bool:
    if _codex_session_confirmation_pending(event):
        return False
    return not event.confirmed or (event.kind == "phantom_recovery" and bool(event.error))


def _notify_ntfy_message(
    message: str,
    config: NotifyConfig,
    title: str,
    priority: str,
    tags: str,
) -> bool:
    """Send a raw notification message via ntfy.sh."""
    import httpx

    topic = config.ntfy_topic
    if not topic:
        return False

    try:
        response = httpx.post(
            f"https://ntfy.sh/{topic}",
            content=message.encode(),
            headers={
                "Title": _ntfy_header_value(title),
                "Priority": _ntfy_header_value(priority),
                "Tags": _ntfy_header_value(tags),
            },
            timeout=10,
        )
        return 200 <= response.status_code < 300
    except Exception:
        return False  # Notifications are best-effort


def _ntfy_header_value(value: str) -> str:
    """Return a header-safe value for httpx/ntfy.

    HTTP header values must be Latin-1 encodable in httpx. Keep notification
    bodies rich, but make metadata headers conservative.
    """
    normalized = value.replace("—", "-").replace("–", "-")
    return normalized.encode("latin-1", "ignore").decode("latin-1")


def _notify_telegram(event: KickEvent, config: NotifyConfig) -> bool:
    """Send notification via Telegram bot."""
    token = config.telegram_bot_token
    chat_id = config.telegram_chat_id
    if not token or not chat_id:
        return False

    message = _format_message(event)
    return _notify_telegram_message(message, config)


def _notify_telegram_message(message: str, config: NotifyConfig) -> bool:
    """Send a raw notification message via Telegram bot."""
    import httpx

    token = config.telegram_bot_token
    chat_id = config.telegram_chat_id
    if not token or not chat_id:
        return False

    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        return 200 <= response.status_code < 300
    except Exception:
        return False  # Best-effort
