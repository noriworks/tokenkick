"""Predicted reset calendar helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .models import AccountConfig, AccountState, AccountStatus, Config, WorkSchedule, account_key_string
from .scheduling import (
    PendingKick,
    compute_schedule_decision,
    parse_work_window,
    pending_kick_next_action_at,
    schedule_for_account,
)

WEEKLY_CASCADE_SECONDS = 7 * 24 * 60 * 60
GEMINI_RESET_TZ = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class CalendarEvent:
    """A predicted account reset or recommended kick moment."""

    account: str
    provider: str
    type: str
    predicted_at: datetime
    confidence: str
    source: str
    estimated: bool = False
    optimal_kick_at: datetime | None = None
    schedule: str | None = None
    immediate_kick_best: bool = False

    def predicted_at_local(self, tz: tzinfo) -> datetime:
        return self.predicted_at.astimezone(tz)

    def optimal_kick_at_local(self, tz: tzinfo) -> datetime | None:
        if self.optimal_kick_at is None:
            return None
        return self.optimal_kick_at.astimezone(tz)


@dataclass(frozen=True)
class CalendarResult:
    events: list[CalendarEvent]
    warnings: list[str]


def build_reset_calendar(
    *,
    config: Config,
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    cache_entries: dict[str, dict],
    now: datetime,
    tz: tzinfo,
    days_ahead: int,
    account_label: str | None = None,
    provider: str | None = None,
    show_all: bool = False,
    missing_accounts: list[AccountConfig] | None = None,
) -> CalendarResult:
    """Build reset predictions from already-loaded status cache entries."""
    now = now.astimezone(timezone.utc)
    horizon = now + timedelta(days=max(0, days_ahead))
    events: list[CalendarEvent] = []
    warnings: list[str] = []

    for account in missing_accounts or []:
        if _account_in_scope(account, account_label=account_label, provider=provider, show_all=show_all):
            warnings.append(f'No cached status for "{account.label}". Run tk status --refresh.')

    for account, status in zip(accounts, statuses, strict=False):
        if not _account_in_scope(account, account_label=account_label, provider=provider, show_all=show_all):
            continue
        if status.state == AccountState.UNKNOWN:
            continue

        key = account_key_string(account)
        entry = cache_entries.get(key, {})
        account_events: list[CalendarEvent] = []
        if account.provider == "gemini":
            account_events.extend(_gemini_reset_events(account, now, horizon))
        else:
            weekly = _weekly_reset_event(account, status, entry, now)
            if weekly is not None:
                account_events.append(weekly)
                cascade = _weekly_cascade_event(account, weekly, status, horizon)
                if cascade is not None:
                    account_events.append(cascade)
            session = _session_reset_event(account, status, entry, now)
            if session is not None:
                account_events.append(session)

        for event in account_events:
            if not _inside_window(event.predicted_at, now, horizon):
                continue
            if event.type == "weekly_reset":
                event = _with_smart_schedule(event, account, status, config, tz, now)
            events.append(event)

    return CalendarResult(
        events=sorted(events, key=lambda event: (event.predicted_at, event.account, event.type)),
        warnings=warnings,
    )


def calendar_json_payload(
    *,
    generated_at: datetime,
    tz: tzinfo,
    days_ahead: int,
    events: list[CalendarEvent],
    warnings: list[str],
    pending_kicks: list[PendingKick] | None = None,
) -> dict:
    """Return the machine-readable calendar payload."""
    timezone_name = _timezone_name(tz)
    return {
        "schema_version": 1,
        "generated_at": _format_utc(generated_at),
        "timezone": timezone_name,
        "days_ahead": days_ahead,
        "warnings": warnings,
        "pending_kicks": [
            _pending_kick_json_payload(pending, tz)
            for pending in sorted(
                pending_kicks or [],
                key=lambda item: pending_kick_next_action_at(item) or _parse_utc(item.kick_at) or generated_at,
            )
        ],
        "events": [
            {
                "account": event.account,
                "provider": event.provider,
                "type": event.type,
                "predicted_at": _format_utc(event.predicted_at),
                "predicted_at_local": event.predicted_at.astimezone(tz).isoformat(),
                "confidence": event.confidence,
                "source": event.source,
                "optimal_kick_at": (
                    _format_utc(event.optimal_kick_at)
                    if event.optimal_kick_at is not None
                    else None
                ),
                "optimal_kick_at_local": (
                    event.optimal_kick_at.astimezone(tz).isoformat()
                    if event.optimal_kick_at is not None
                    else None
                ),
                "schedule": event.schedule,
            }
            for event in events
        ],
    }


def _pending_kick_json_payload(pending: PendingKick, tz: tzinfo) -> dict:
    next_action_at = pending_kick_next_action_at(pending)
    data = pending.to_dict()
    data["next_action_at"] = _format_utc(next_action_at) if next_action_at is not None else None
    data["next_action_at_local"] = (
        next_action_at.astimezone(tz).isoformat() if next_action_at is not None else None
    )
    return data


def render_ics(events: list[CalendarEvent], tz: tzinfo) -> str:
    """Render reset events as a simple iCalendar document."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TokenKick//Reset Calendar//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:TokenKick Resets",
    ]
    for event in events:
        uid = _ics_uid(event)
        summary = f"TokenKick: {event.account} {_event_type_name(event)} reset"
        description = _ics_description(event, tz)
        alarm_description = f"{summary} in 30 minutes"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_ics_escape(uid)}",
                f"DTSTAMP:{_ics_timestamp(datetime.now(timezone.utc))}",
                f"DTSTART:{_ics_timestamp(event.predicted_at)}",
                "DURATION:PT15M",
                f"SUMMARY:{_ics_escape(summary)}",
                f"DESCRIPTION:{_ics_escape(description)}",
                "BEGIN:VALARM",
                "TRIGGER:-PT30M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_ics_escape(alarm_description)}",
                "END:VALARM",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def format_event_description(event: CalendarEvent) -> str:
    if event.type == "weekly_reset":
        text = "weekly reset available"
    elif event.type == "weekly_reset_estimated":
        text = "weekly reset available (estimated, depends on kick timing)"
    elif event.type == "session_reset":
        text = "session reset (estimated)"
    elif event.type == "daily_reset":
        text = "daily reset"
    else:
        text = event.type.replace("_", " ")
    return text


def schedule_text(schedule: WorkSchedule, predicted_at: datetime, tz: tzinfo) -> str | None:
    window = schedule.weekends if predicted_at.astimezone(tz).weekday() >= 5 else schedule.weekdays
    if not window:
        return None
    scope = "weekends" if predicted_at.astimezone(tz).weekday() >= 5 else "weekdays"
    return f"{window} {scope}"


def _weekly_reset_event(
    account: AccountConfig,
    status: AccountStatus,
    entry: dict,
    now: datetime,
) -> CalendarEvent | None:
    predicted, source, confidence = _predicted_at(
        absolute=status.resets_at,
        countdown=status.resets_in_seconds,
        entry=entry,
        stale=status.stale or bool(entry.get("refresh_error")),
        exact_source="provider_reset_timestamp",
        fallback_source="countdown_extrapolation",
    )
    if predicted is None or predicted <= now:
        return None
    return CalendarEvent(
        account=account.label,
        provider=account.provider,
        type="weekly_reset",
        predicted_at=predicted,
        confidence=confidence,
        source=source,
        estimated=source != "provider_reset_timestamp",
    )


def _weekly_cascade_event(
    account: AccountConfig,
    first: CalendarEvent,
    status: AccountStatus,
    horizon: datetime,
) -> CalendarEvent | None:
    if not _likely_prompt_kick(account, status):
        return None
    predicted = first.predicted_at + timedelta(seconds=WEEKLY_CASCADE_SECONDS)
    if predicted > horizon:
        return None
    return CalendarEvent(
        account=account.label,
        provider=account.provider,
        type="weekly_reset_estimated",
        predicted_at=predicted,
        confidence="medium",
        source="weekly_cascade_estimate",
        estimated=True,
    )


def _session_reset_event(
    account: AccountConfig,
    status: AccountStatus,
    entry: dict,
    now: datetime,
) -> CalendarEvent | None:
    if status.session_resets_in_seconds is not None and status.session_resets_in_seconds <= 0:
        return None
    if status.session_used_percent == 0.0:
        return None
    predicted, source, _confidence = _predicted_at(
        absolute=status.session_resets_at,
        countdown=status.session_resets_in_seconds,
        entry=entry,
        stale=status.stale or bool(entry.get("refresh_error")),
        exact_source="provider_reset_timestamp",
        fallback_source="countdown_extrapolation",
    )
    if predicted is None or predicted <= now:
        return None
    return CalendarEvent(
        account=account.label,
        provider=account.provider,
        type="session_reset",
        predicted_at=predicted,
        confidence="medium",
        source="countdown_extrapolation" if source == "provider_reset_timestamp" else source,
        estimated=True,
    )


def _gemini_reset_events(
    account: AccountConfig,
    now: datetime,
    horizon: datetime,
) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    local = now.astimezone(GEMINI_RESET_TZ)
    reset = datetime.combine(local.date(), time.min, tzinfo=GEMINI_RESET_TZ)
    if reset <= now.astimezone(GEMINI_RESET_TZ):
        reset += timedelta(days=1)
    while reset.astimezone(timezone.utc) <= horizon:
        events.append(
            CalendarEvent(
                account=account.label,
                provider=account.provider,
                type="daily_reset",
                predicted_at=reset.astimezone(timezone.utc),
                confidence="high",
                source="fixed_provider_schedule",
            )
        )
        reset += timedelta(days=1)
    return events


def _with_smart_schedule(
    event: CalendarEvent,
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    tz: tzinfo,
    now: datetime,
) -> CalendarEvent:
    schedule = schedule_for_account(config.schedule, account.label)
    if schedule is None:
        return event
    schedule_label = schedule_text(schedule, event.predicted_at, tz)
    if schedule_label is None:
        return event
    window = _schedule_work_window(schedule, event.predicted_at, tz)
    if window is None:
        return event
    window_minutes = _short_window_minutes(status)
    if window_minutes is None:
        return event
    try:
        decision = compute_schedule_decision(
            work_start=window[0],
            work_end=window[1],
            window_minutes=window_minutes,
            quota_available_at=event.predicted_at,
            now=now,
        )
    except ValueError:
        return event
    optimal = decision.kick_at.astimezone(timezone.utc)
    return CalendarEvent(
        account=event.account,
        provider=event.provider,
        type=event.type,
        predicted_at=event.predicted_at,
        confidence=event.confidence,
        source=event.source,
        estimated=event.estimated,
        optimal_kick_at=optimal,
        schedule=schedule_label,
        immediate_kick_best=optimal == event.predicted_at,
    )


def _schedule_work_window(
    schedule: WorkSchedule,
    predicted_at: datetime,
    tz: tzinfo,
) -> tuple[datetime, datetime] | None:
    local_predicted = predicted_at.astimezone(tz)
    window = schedule.weekends if local_predicted.weekday() >= 5 else schedule.weekdays
    if not window:
        return None
    try:
        return parse_work_window(window, local_predicted.date(), tz)
    except ValueError:
        return None


def _short_window_minutes(status: AccountStatus) -> int | None:
    candidates = [
        value
        for value in (status.session_window_minutes, status.window_minutes)
        if value is not None and 0 < value < 24 * 60
    ]
    return min(candidates) if candidates else None


def _predicted_at(
    *,
    absolute: float | None,
    countdown: int | None,
    entry: dict,
    stale: bool,
    exact_source: str,
    fallback_source: str,
) -> tuple[datetime | None, str, str]:
    if absolute is not None:
        return datetime.fromtimestamp(float(absolute), timezone.utc), exact_source, "high"
    anchor = _entry_anchor_time(entry)
    if countdown is not None and anchor is not None:
        confidence = "medium" if stale else "high"
        return anchor + timedelta(seconds=max(0, int(countdown))), fallback_source, confidence
    return None, "unavailable", "low"


def _entry_anchor_time(entry: dict) -> datetime | None:
    for key in ("provider_observed_at", "cached_at"):
        value = entry.get(key)
        if isinstance(value, str):
            parsed = _parse_utc(value)
            if parsed is not None:
                return parsed
    status = entry.get("status")
    if isinstance(status, AccountStatus) and isinstance(status.observed_at, str):
        return _parse_utc(status.observed_at)
    return None


def _parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _inside_window(value: datetime, now: datetime, horizon: datetime) -> bool:
    value = value.astimezone(timezone.utc)
    return now <= value <= horizon


def _account_in_scope(
    account: AccountConfig,
    *,
    account_label: str | None,
    provider: str | None,
    show_all: bool,
) -> bool:
    if not show_all and not account.visible:
        return False
    if account_label is not None and account.label != account_label:
        return False
    if provider is not None and account.provider != provider:
        return False
    return True


def _likely_prompt_kick(account: AccountConfig, status: AccountStatus) -> bool:
    return bool(account.auto_kick and account.weekly_auto_kick or status.state == AccountState.FRESH)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ics_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _ics_uid(event: CalendarEvent) -> str:
    account = re.sub(r"[^a-z0-9]+", "-", event.account.lower()).strip("-") or "account"
    stamp = event.predicted_at.strftime("%Y%m%dT%H%M%SZ")
    return f"tokenkick-{account}-{event.type}-{stamp}@tokenkick"


def _ics_description(event: CalendarEvent, tz: tzinfo) -> str:
    description = (
        f"{event.account} {_event_type_name(event)} reset predicted. "
        f"Confidence: {event.confidence}."
    )
    if event.optimal_kick_at is not None:
        local = event.optimal_kick_at.astimezone(tz)
        if event.immediate_kick_best:
            description += " Immediate kick at reset is best."
        else:
            description += f" Optimal kick at {local.strftime('%H:%M %Z')}."
    return description


def _event_type_name(event: CalendarEvent) -> str:
    if event.type in {"weekly_reset", "weekly_reset_estimated"}:
        return "weekly"
    if event.type == "session_reset":
        return "session"
    if event.type == "daily_reset":
        return "daily"
    return event.type.replace("_", " ")


def _timezone_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if key:
        return str(key)
    name = tz.tzname(datetime.now(tz))
    if name:
        return name
    offset = tz.utcoffset(datetime.now(tz))
    if offset is None:
        return "UTC"
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"
