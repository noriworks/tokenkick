"""Smart kick scheduling and pending-kick persistence."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    CONFIG_DIR,
    Config,
    ScheduleConfig,
    WorkSchedule,
    account_key_string,
)
from .state_io import atomic_write_text, state_file_lock

PENDING_KICKS_FILE = CONFIG_DIR / "pending-kicks.json"
PENDING_KICK_MAX_AGE_SECONDS = 24 * 60 * 60
PENDING_KICK_RETRY_BACKOFF_SECONDS = (5 * 60, 15 * 60, 45 * 60)
PENDING_KICK_MAX_ATTEMPTS = len(PENDING_KICK_RETRY_BACKOFF_SECONDS) + 1
MAX_DAILY_SCHEDULE_WINDOW_MINUTES = 24 * 60
PENDING_KICK_PURPOSE_COVERAGE = "coverage"
PENDING_KICK_PURPOSE_SPECIALIST_READINESS = "specialist_readiness"
PENDING_KICK_PURPOSES = (
    PENDING_KICK_PURPOSE_COVERAGE,
    PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
)


class PendingKickStateError(Exception):
    """Raised when pending-kick state cannot be persisted safely."""


def _warn_pending_kick_state(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


class ScheduleReason(str, Enum):
    OPTIMAL = "optimal"
    QUOTA_CONSTRAINED = "quota_constrained"
    SINGLE_WINDOW = "single_window"
    ALREADY_KICKED = "already_kicked"
    ORCHESTRATED = "orchestrated"


class WasteLocation(str, Enum):
    PRE_WORK = "pre_work"
    POST_WORK = "post_work"
    NONE = "none"
    BOTH = "both"


class SchedulingWindowBasis(str, Enum):
    PRIMARY = "primary"
    SESSION = "session"


@dataclass(frozen=True)
class SchedulingWindowSelection:
    basis: SchedulingWindowBasis
    window_minutes: int
    resets_in_seconds: int | None


@dataclass(frozen=True)
class ScheduleDecision:
    kick_at: datetime
    reason: ScheduleReason
    windows_needed: int
    expected_waste_minutes: int
    waste_location: WasteLocation
    work_start: datetime
    work_end: datetime
    optimal_kick_at: datetime


@dataclass
class PendingKick:
    account_key: str
    account_label: str
    provider: str
    kick_at: str
    created_at: str
    reason: str
    windows_needed: int
    expected_waste_minutes: int
    waste_location: str
    work_start: str
    work_end: str
    window_basis: str = SchedulingWindowBasis.PRIMARY.value
    purpose: str = PENDING_KICK_PURPOSE_COVERAGE
    notified: bool = False
    attempt_count: int = 0
    last_attempt_at: str | None = None
    last_error: str | None = None
    next_retry_at: str | None = None
    gave_up_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> PendingKick | None:
        try:
            attempt_count = int(data.get("attempt_count", 0))
            return cls(
                account_key=str(data["account_key"]),
                account_label=str(data["account_label"]),
                provider=str(data["provider"]),
                kick_at=str(data["kick_at"]),
                created_at=str(data["created_at"]),
                reason=str(data["reason"]),
                windows_needed=int(data["windows_needed"]),
                expected_waste_minutes=int(data["expected_waste_minutes"]),
                waste_location=str(data["waste_location"]),
                work_start=str(data["work_start"]),
                work_end=str(data["work_end"]),
                window_basis=str(data.get("window_basis", SchedulingWindowBasis.PRIMARY.value)),
                purpose=_normalize_pending_kick_purpose(data.get("purpose")),
                notified=bool(data.get("notified", False)),
                attempt_count=max(0, attempt_count),
                last_attempt_at=_optional_str(data.get("last_attempt_at")),
                last_error=_optional_str(data.get("last_error")),
                next_retry_at=_optional_str(data.get("next_retry_at")),
                gave_up_at=_optional_str(data.get("gave_up_at")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CancelPendingKicksResult:
    removed: list[PendingKick]
    kept_count: int
    unmatched_account_labels: list[str]

    def to_dict(self) -> dict:
        return {
            "removed_count": len(self.removed),
            "kept_count": self.kept_count,
            "unmatched_account_labels": self.unmatched_account_labels,
            "removed": [pending.to_dict() for pending in self.removed],
        }


def _optional_str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_pending_kick_purpose(value: object) -> str:
    purpose = str(value or PENDING_KICK_PURPOSE_COVERAGE).strip().lower()
    return purpose if purpose in PENDING_KICK_PURPOSES else PENDING_KICK_PURPOSE_COVERAGE


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def pending_kick_next_action_at(pending: PendingKick) -> datetime | None:
    if pending.gave_up_at:
        return None
    retry_at = _parse_optional_utc(pending.next_retry_at)
    if retry_at is not None:
        return retry_at
    return _parse_optional_utc(pending.kick_at)


def pending_kick_retry_ready(pending: PendingKick, now: datetime) -> bool:
    if pending.gave_up_at:
        return False
    retry_at = _parse_optional_utc(pending.next_retry_at)
    return retry_at is None or retry_at <= now.astimezone(timezone.utc)


def pending_kick_gave_up(pending: PendingKick) -> bool:
    return bool(pending.gave_up_at)


def pending_kick_blocks_auto_kick(pending: PendingKick | None, now: datetime) -> bool:
    if pending is None or pending_kick_gave_up(pending):
        return False
    action_at = pending_kick_next_action_at(pending)
    if action_at is None:
        return False
    return action_at > now.astimezone(timezone.utc)


def pending_kick_retry_backoff_seconds(attempt_count: int) -> int | None:
    if attempt_count >= PENDING_KICK_MAX_ATTEMPTS:
        return None
    index = max(0, attempt_count - 1)
    return PENDING_KICK_RETRY_BACKOFF_SECONDS[min(index, len(PENDING_KICK_RETRY_BACKOFF_SECONDS) - 1)]


def record_pending_kick_failure(
    account: AccountConfig,
    error: str | None,
    now: datetime | None = None,
) -> PendingKick | None:
    current_time = now or utc_now()
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked(current_time)
            key = account_key_string(account)
            pending = data.get(key)
            if pending is None:
                return None
            attempt_count = pending.attempt_count + 1
            pending.attempt_count = attempt_count
            pending.last_attempt_at = to_utc_iso(current_time)
            pending.last_error = error or "kick failed"
            pending.notified = False
            backoff_seconds = pending_kick_retry_backoff_seconds(attempt_count)
            if backoff_seconds is None:
                pending.next_retry_at = None
                pending.gave_up_at = to_utc_iso(current_time)
            else:
                pending.next_retry_at = to_utc_iso(current_time + timedelta(seconds=backoff_seconds))
                pending.gave_up_at = None
            data[key] = pending
            _save_pending_kicks_unlocked(data)
            return pending
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(
            f'Kick failure for "{account.label}" was not recorded ({exc}); '
            "retry/backoff state may be stale."
        )
        return None


def _parse_optional_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return from_utc_iso(value)
    except ValueError:
        return None


def local_timezone(config: ScheduleConfig) -> tzinfo:
    if config.timezone:
        try:
            return ZoneInfo(config.timezone)
        except ZoneInfoNotFoundError:
            pass
    local = datetime.now().astimezone().tzinfo
    key = getattr(local, "key", None)
    if key:
        return ZoneInfo(key)
    return local or timezone.utc


def parse_work_window(value: str, day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    try:
        start_text, end_text = value.split("-", 1)
        start_time = _parse_hhmm(start_text)
        end_time = _parse_hhmm(end_text)
    except ValueError as exc:
        raise ValueError("Work window must use HH:MM-HH:MM format") from exc

    start = datetime.combine(day, start_time, tzinfo=tz)
    end = datetime.combine(day, end_time, tzinfo=tz)
    _validate_existing_local_datetime(start, tz)
    if end == start:
        raise ValueError(
            "Work window start and end must differ; "
            "overnight windows like 21:00-02:00 are supported"
        )
    if end < start:
        end += timedelta(days=1)
    _validate_existing_local_datetime(end, tz)
    return start, end


def _validate_existing_local_datetime(value: datetime, tz: tzinfo) -> None:
    roundtrip = value.astimezone(timezone.utc).astimezone(tz)
    if (
        roundtrip.date() == value.date()
        and roundtrip.hour == value.hour
        and roundtrip.minute == value.minute
    ):
        return
    timezone_name = getattr(tz, "key", str(tz))
    raise ValueError(
        f"Work window time {value.strftime('%H:%M')} does not exist in "
        f"{timezone_name} on {value.date()} due to a DST transition"
    )


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.strip().split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid HH:MM time")
    return time(hour, minute)


def schedule_for_account(config: ScheduleConfig, account_label: str) -> WorkSchedule | None:
    if not config.enabled:
        return None
    if account_label in config.accounts:
        override = config.accounts[account_label]
        return override if override.enabled else None
    return config.default if config.default.enabled else None


def select_scheduling_window(
    status: AccountStatus,
    target: str = "auto",
) -> SchedulingWindowSelection | None:
    target = target if target in {"auto", "primary", "session"} else "auto"

    primary = _primary_scheduling_window(status)
    session = _session_scheduling_window(status)

    if target == "primary":
        return primary
    if target == "session":
        return session
    return primary or session


def _primary_scheduling_window(status: AccountStatus) -> SchedulingWindowSelection | None:
    if status.window_minutes is None or status.window_minutes >= MAX_DAILY_SCHEDULE_WINDOW_MINUTES:
        return None
    return SchedulingWindowSelection(
        basis=SchedulingWindowBasis.PRIMARY,
        window_minutes=status.window_minutes,
        resets_in_seconds=status.resets_in_seconds,
    )


def _session_scheduling_window(status: AccountStatus) -> SchedulingWindowSelection | None:
    if (
        status.session_window_minutes is None
        or status.session_window_minutes >= MAX_DAILY_SCHEDULE_WINDOW_MINUTES
    ):
        return None
    return SchedulingWindowSelection(
        basis=SchedulingWindowBasis.SESSION,
        window_minutes=status.session_window_minutes,
        resets_in_seconds=status.session_resets_in_seconds,
    )


def compute_quota_available_at(status: AccountStatus, now: datetime) -> datetime | None:
    if status.state == AccountState.FRESH:
        return now
    if status.resets_in_seconds is None:
        return None
    return now + timedelta(seconds=max(0, status.resets_in_seconds))


def compute_selected_window_available_at(
    status: AccountStatus,
    selection: SchedulingWindowSelection,
    now: datetime,
) -> datetime | None:
    if selection.basis == SchedulingWindowBasis.PRIMARY:
        return compute_quota_available_at(status, now)

    if selection.resets_in_seconds is not None:
        return now + timedelta(seconds=max(0, selection.resets_in_seconds))

    session_idle = (
        status.session_window_minutes is not None
        and (status.session_used_percent is None or status.session_used_percent == 0)
    )
    if session_idle:
        return now

    return None


def resolve_today_work_window(
    schedule: WorkSchedule,
    now: datetime,
    tz: tzinfo,
) -> tuple[datetime, datetime] | None:
    local_now = now.astimezone(tz)
    window = schedule.weekends if local_now.weekday() >= 5 else schedule.weekdays
    if not window:
        return None
    try:
        return parse_work_window(window, local_now.date(), tz)
    except ValueError:
        # A stored window that is invalid today (equal start/end saved by an
        # older version, or a DST-nonexistent time) means no usable window.
        return None


def compute_schedule_decision(
    *,
    work_start: datetime,
    work_end: datetime,
    window_minutes: int,
    quota_available_at: datetime,
    now: datetime,
    already_kicked: bool = False,
) -> ScheduleDecision:
    if already_kicked:
        return ScheduleDecision(
            kick_at=now.astimezone(timezone.utc),
            reason=ScheduleReason.ALREADY_KICKED,
            windows_needed=0,
            expected_waste_minutes=0,
            waste_location=WasteLocation.NONE,
            work_start=work_start.astimezone(timezone.utc),
            work_end=work_end.astimezone(timezone.utc),
            optimal_kick_at=now.astimezone(timezone.utc),
        )

    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")

    work_start_utc = work_start.astimezone(timezone.utc)
    work_end_utc = work_end.astimezone(timezone.utc)
    quota_available_utc = quota_available_at.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    work_minutes = max(0, int((work_end_utc - work_start_utc).total_seconds() // 60))
    if work_minutes <= 0:
        raise ValueError("work_end must be after work_start")

    windows_needed = max(1, math.ceil(work_minutes / window_minutes))
    if work_minutes <= window_minutes:
        kick_at = max(work_start_utc, quota_available_utc, now_utc)
        expected_waste = 0
        waste_location = WasteLocation.NONE
        if kick_at > work_start_utc:
            expires_at = kick_at + timedelta(minutes=window_minutes)
            expected_waste = max(0, int((expires_at - work_end_utc).total_seconds() // 60))
            waste_location = WasteLocation.POST_WORK if expected_waste else WasteLocation.NONE
        return ScheduleDecision(
            kick_at=kick_at,
            reason=ScheduleReason.SINGLE_WINDOW,
            windows_needed=1,
            expected_waste_minutes=expected_waste,
            waste_location=waste_location,
            work_start=work_start_utc,
            work_end=work_end_utc,
            optimal_kick_at=max(work_start_utc, quota_available_utc),
        )

    optimal = work_end_utc - timedelta(minutes=windows_needed * window_minutes)
    if optimal >= quota_available_utc and optimal > now_utc:
        kick_at = optimal
        expected_waste = max(0, int((work_start_utc - optimal).total_seconds() // 60))
        waste_location = WasteLocation.PRE_WORK if expected_waste else WasteLocation.NONE
        reason = ScheduleReason.OPTIMAL
    elif optimal < quota_available_utc:
        kick_at = max(quota_available_utc, now_utc)
        expected_waste = max(0, int((kick_at - optimal).total_seconds() // 60))
        waste_location = WasteLocation.POST_WORK if expected_waste else WasteLocation.NONE
        reason = ScheduleReason.QUOTA_CONSTRAINED
    else:
        kick_at = now_utc
        expected_waste = max(0, int((now_utc - optimal).total_seconds() // 60))
        waste_location = WasteLocation.POST_WORK if expected_waste else WasteLocation.NONE
        reason = ScheduleReason.QUOTA_CONSTRAINED

    return ScheduleDecision(
        kick_at=kick_at,
        reason=reason,
        windows_needed=windows_needed,
        expected_waste_minutes=expected_waste,
        waste_location=waste_location,
        work_start=work_start_utc,
        work_end=work_end_utc,
        optimal_kick_at=optimal,
    )


def recompute(
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    now: datetime,
) -> ScheduleDecision | None:
    schedule = schedule_for_account(config.schedule, account.label)
    if schedule is None:
        return None
    tz = local_timezone(config.schedule)
    work_window = resolve_today_work_window(schedule, now, tz)
    if work_window is None:
        return None
    selection = select_scheduling_window(status, config.schedule.scheduling_target)
    if selection is None:
        return None
    quota_available_at = compute_selected_window_available_at(status, selection, now)
    if quota_available_at is None:
        return None
    return compute_schedule_decision(
        work_start=work_window[0],
        work_end=work_window[1],
        window_minutes=selection.window_minutes,
        quota_available_at=quota_available_at,
        now=now,
    )


def _pending_kicks_quarantine_path(now: datetime | None = None) -> Path:
    timestamp = (now or utc_now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = PENDING_KICKS_FILE.with_name(f"{PENDING_KICKS_FILE.name}.corrupt-{timestamp}")
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}.{counter}")
        counter += 1
    return candidate


def _quarantine_corrupt_pending_kicks_unlocked() -> Path:
    quarantine = _pending_kicks_quarantine_path()
    PENDING_KICKS_FILE.rename(quarantine)
    return quarantine


def _pending_kicks_payload_issue() -> str | None:
    """Return why the existing pending-kicks file is unusable, or None if usable."""
    try:
        data = json.loads(PENDING_KICKS_FILE.read_text())
    except json.JSONDecodeError as exc:
        return f"invalid JSON: {exc}"
    except OSError as exc:
        return f"unreadable: {exc}"
    if not isinstance(data, dict):
        return "top-level value must be a JSON object"
    return None


def _load_pending_kicks_unlocked(now: datetime | None = None) -> dict[str, PendingKick]:
    if not PENDING_KICKS_FILE.exists():
        return {}
    try:
        raw = PENDING_KICKS_FILE.read_text()
    except OSError as exc:
        _warn_pending_kick_state(
            f"Pending kicks could not be read from {PENDING_KICKS_FILE} ({exc}); "
            "treating pending kicks as empty for this run."
        )
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("top-level value must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        try:
            quarantine = _quarantine_corrupt_pending_kicks_unlocked()
        except OSError as rename_exc:
            _warn_pending_kick_state(
                f"Pending kicks state at {PENDING_KICKS_FILE} is corrupt ({exc}) and "
                f"could not be moved aside ({rename_exc}); treating pending kicks as "
                "empty for this run."
            )
            return {}
        _warn_pending_kick_state(
            f"Pending kicks state at {PENDING_KICKS_FILE} was corrupt ({exc}); "
            f"moved it aside to {quarantine}. Scheduled and orchestrated pending "
            "kicks were reset; re-apply plans or wait for the next schedule pass."
        )
        return {}

    pending: dict[str, PendingKick] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        pending_kick = PendingKick.from_dict(value)
        if pending_kick is not None:
            pending[str(key)] = pending_kick

    current = now or utc_now()
    pruned = prune_pending_kicks(pending, current)
    if pruned != pending:
        try:
            _save_pending_kicks_unlocked(pruned)
        except PendingKickStateError as exc:
            _warn_pending_kick_state(str(exc))
    return pruned


def load_pending_kicks(now: datetime | None = None) -> dict[str, PendingKick]:
    if not PENDING_KICKS_FILE.exists():
        return {}
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            return _load_pending_kicks_unlocked(now)
    except OSError as exc:
        _warn_pending_kick_state(
            f"Pending kicks could not be read from {PENDING_KICKS_FILE} ({exc}); "
            "treating pending kicks as empty for this run."
        )
        return {}


def _save_pending_kicks_unlocked(data: dict[str, PendingKick]) -> None:
    if PENDING_KICKS_FILE.exists():
        issue = _pending_kicks_payload_issue()
        if issue is not None:
            try:
                quarantine = _quarantine_corrupt_pending_kicks_unlocked()
            except OSError as exc:
                raise PendingKickStateError(
                    f"Pending kicks were not saved: existing state at "
                    f"{PENDING_KICKS_FILE} is corrupt ({issue}) and could not be "
                    f"moved aside ({exc})."
                ) from exc
            _warn_pending_kick_state(
                f"Pending kicks state at {PENDING_KICKS_FILE} was corrupt ({issue}); "
                f"moved it aside to {quarantine} before saving new state."
            )
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        rendered = {key: value.to_dict() for key, value in data.items()}
        atomic_write_text(PENDING_KICKS_FILE, json.dumps(rendered, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        raise PendingKickStateError(
            f"Pending kicks could not be saved to {PENDING_KICKS_FILE}: {exc}"
        ) from exc


def save_pending_kicks(data: dict[str, PendingKick]) -> None:
    """Persist pending kicks; raises PendingKickStateError when persistence fails."""
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            _save_pending_kicks_unlocked(data)
    except OSError as exc:
        raise PendingKickStateError(
            f"Pending kicks could not be saved to {PENDING_KICKS_FILE}: {exc}"
        ) from exc


def upsert_pending_kick(
    account: AccountConfig,
    decision: ScheduleDecision,
    window_basis: str = SchedulingWindowBasis.PRIMARY.value,
    now: datetime | None = None,
) -> PendingKick:
    key = account_key_string(account)
    kick_at = to_utc_iso(decision.kick_at)
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked(now)
            current = data.get(key)
            if current is not None and current.reason == ScheduleReason.ORCHESTRATED.value:
                # Orchestrated pending kicks belong to applied plans. Smart-schedule
                # decisions must not downgrade them; plan apply and tk plan cancel
                # are the only paths that replace or remove them.
                return current
            notified = bool(current.notified) if current and current.kick_at == kick_at else False
            failure_state = current if current and current.kick_at == kick_at else None
            pending = PendingKick(
                account_key=key,
                account_label=account.label,
                provider=account.provider,
                kick_at=kick_at,
                created_at=current.created_at if current else to_utc_iso(utc_now()),
                reason=decision.reason.value,
                windows_needed=decision.windows_needed,
                expected_waste_minutes=decision.expected_waste_minutes,
                waste_location=decision.waste_location.value,
                work_start=to_utc_iso(decision.work_start),
                work_end=to_utc_iso(decision.work_end),
                window_basis=window_basis,
                notified=notified,
                attempt_count=failure_state.attempt_count if failure_state else 0,
                last_attempt_at=failure_state.last_attempt_at if failure_state else None,
                last_error=failure_state.last_error if failure_state else None,
                next_retry_at=failure_state.next_retry_at if failure_state else None,
                gave_up_at=failure_state.gave_up_at if failure_state else None,
            )
            data[key] = pending
            _save_pending_kicks_unlocked(data)
            return pending
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(
            f'Scheduled kick for "{account.label}" was not persisted ({exc}); '
            "it applies to this run only."
        )
        return PendingKick(
            account_key=key,
            account_label=account.label,
            provider=account.provider,
            kick_at=kick_at,
            created_at=to_utc_iso(utc_now()),
            reason=decision.reason.value,
            windows_needed=decision.windows_needed,
            expected_waste_minutes=decision.expected_waste_minutes,
            waste_location=decision.waste_location.value,
            work_start=to_utc_iso(decision.work_start),
            work_end=to_utc_iso(decision.work_end),
            window_basis=window_basis,
            notified=False,
        )


def remove_pending_kick(account: AccountConfig) -> PendingKick | None:
    if not PENDING_KICKS_FILE.exists():
        return None
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked()
            key = account_key_string(account)
            removed = data.pop(key, None)
            if removed is not None:
                _save_pending_kicks_unlocked(data)
            return removed
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(
            f'Pending kick for "{account.label}" could not be removed ({exc}).'
        )
        return None


def cancel_orchestrated_pending_kicks(
    account_labels: set[str] | None = None,
    now: datetime | None = None,
) -> CancelPendingKicksResult:
    requested = set(account_labels) if account_labels else None
    if not PENDING_KICKS_FILE.exists():
        return CancelPendingKicksResult(
            removed=[],
            kept_count=0,
            unmatched_account_labels=sorted(requested or set()),
        )
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked(now)
            removed: list[PendingKick] = []
            kept: dict[str, PendingKick] = {}
            matched_labels: set[str] = set()
            for key, pending in data.items():
                label_match = requested is None or pending.account_label in requested
                orchestrated = pending.reason == ScheduleReason.ORCHESTRATED.value
                if label_match and orchestrated:
                    removed.append(pending)
                    matched_labels.add(pending.account_label)
                else:
                    kept[key] = pending
            if removed:
                _save_pending_kicks_unlocked(kept)
            unmatched = sorted((requested or set()) - matched_labels)
            return CancelPendingKicksResult(
                removed=removed,
                kept_count=len(kept),
                unmatched_account_labels=unmatched,
            )
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(
            f"Orchestration pending kicks were not cancelled ({exc})."
        )
        return CancelPendingKicksResult(
            removed=[],
            kept_count=0,
            unmatched_account_labels=sorted(requested or set()),
        )


def mark_pending_notified(
    account: AccountConfig,
    now: datetime | None = None,
) -> PendingKick | None:
    if not PENDING_KICKS_FILE.exists():
        return None
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked(now)
            key = account_key_string(account)
            pending = data.get(key)
            if pending is None:
                return None
            pending.notified = True
            data[key] = pending
            _save_pending_kicks_unlocked(data)
            return pending
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(
            f'Pending kick for "{account.label}" could not be marked notified ({exc}).'
        )
        return None


def prune_pending_kicks(
    data: dict[str, PendingKick],
    now: datetime,
) -> dict[str, PendingKick]:
    cutoff = now.astimezone(timezone.utc) - timedelta(seconds=PENDING_KICK_MAX_AGE_SECONDS)
    pruned: dict[str, PendingKick] = {}
    for key, pending in data.items():
        try:
            kick_at = from_utc_iso(pending.kick_at)
        except ValueError:
            continue
        if kick_at < cutoff:
            continue
        pruned[key] = pending
    return pruned


def invalidate_pending_kicks(
    account_label: str | None = None,
    provider: str | None = None,
    *,
    exclude_orchestrated: bool = False,
) -> list[PendingKick]:
    if not PENDING_KICKS_FILE.exists():
        return []
    try:
        with state_file_lock(PENDING_KICKS_FILE):
            data = _load_pending_kicks_unlocked()
            removed: list[PendingKick] = []
            kept: dict[str, PendingKick] = {}
            for key, pending in data.items():
                label_match = account_label is None or pending.account_label == account_label
                provider_match = provider is None or pending.provider == provider
                orchestrated = pending.reason == ScheduleReason.ORCHESTRATED.value
                if label_match and provider_match and not (exclude_orchestrated and orchestrated):
                    removed.append(pending)
                else:
                    kept[key] = pending
            if removed:
                _save_pending_kicks_unlocked(kept)
            return removed
    except (OSError, PendingKickStateError) as exc:
        _warn_pending_kick_state(f"Pending kicks were not invalidated ({exc}).")
        return []
