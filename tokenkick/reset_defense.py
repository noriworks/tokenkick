"""Global provider reset detection and event persistence."""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    CODEX_DEFAULT_RATE_LIMIT_ID,
    CONFIG_DIR,
    KickEvent,
    account_key_string,
    codex_rate_limit_id_for_account,
    is_synthetic_status,
)
from .scheduling import PendingKick, invalidate_pending_kicks
from .state_io import state_file_lock

RESET_EVENTS_FILE = CONFIG_DIR / "reset-events.jsonl"
RESET_EVENT_MAX_COUNT = 1000
RESET_EVENT_MAX_AGE_DAYS = 365
RESET_EVENT_DEDUP_SECONDS = 10 * 60
RESET_EVENT_RECENT_HOURS = 24
RESET_EVENT_DOCTOR_DAYS = 7
RESET_EVENT_POLL_TOLERANCE_SECONDS = 60
RESET_SHIFT_THRESHOLD_SECONDS = 60 * 60
USAGE_DROP_THRESHOLD_POINTS = 40
USAGE_DROP_CURRENT_MAX = 10
SINGLE_ACCOUNT_USAGE_DROP_PREVIOUS_MIN = 15
SINGLE_ACCOUNT_USAGE_DROP_CURRENT_MAX = 5
SINGLE_ACCOUNT_USAGE_DROP_THRESHOLD_POINTS = 10
SINGLE_ACCOUNT_WEEKLY_RESET_SHIFT_SECONDS = 24 * 60 * 60
SINGLE_ACCOUNT_FULL_WEEKLY_WINDOW_SECONDS = 6 * 24 * 60 * 60
SINGLE_ACCOUNT_OBSERVATION_TRIGGERS = {
    "single_account_usage_drop",
    "single_account_weekly_reset",
}


@dataclass
class AccountSnapshot:
    account: str
    before_state: str
    before_weekly_used_pct: float | None
    before_weekly_resets_at: str | None
    after_state: str
    after_weekly_used_pct: float | None
    after_weekly_resets_at: str | None
    before_session_resets_at: str | None = None
    after_session_resets_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AccountSnapshot | None:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                account=str(data["account"]),
                before_state=str(data["before_state"]),
                before_weekly_used_pct=_optional_float(data.get("before_weekly_used_pct")),
                before_weekly_resets_at=_optional_str(data.get("before_weekly_resets_at")),
                after_state=str(data["after_state"]),
                after_weekly_used_pct=_optional_float(data.get("after_weekly_used_pct")),
                after_weekly_resets_at=_optional_str(data.get("after_weekly_resets_at")),
                before_session_resets_at=_optional_str(data.get("before_session_resets_at")),
                after_session_resets_at=_optional_str(data.get("after_session_resets_at")),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class ResetEvent:
    id: str
    detected_at: str
    provider: str
    confidence: str
    affected_accounts: list[str]
    trigger: str
    account_snapshots: list[AccountSnapshot]
    total_quota_hours_lost: float | None
    previous_reset_predictions: dict[str, str | None]
    new_reset_predictions: dict[str, str | None]
    pending_kicks_invalidated: list[str]
    notification_sent: bool
    summary: str
    detail: str
    failover_guidance: str | None = None
    notification_skip_reason: str | None = None
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None
    recovery_action: str | None = None
    recovery_action_at: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["account_snapshots"] = [snapshot.to_dict() for snapshot in self.account_snapshots]
        data["account_impacts"] = account_impacts(self)
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, data: dict) -> ResetEvent | None:
        if not isinstance(data, dict):
            return None
        snapshots = [
            snapshot
            for raw in data.get("account_snapshots", [])
            if (snapshot := AccountSnapshot.from_dict(raw)) is not None
        ]
        try:
            return cls(
                id=str(data["id"]),
                detected_at=str(data["detected_at"]),
                provider=str(data["provider"]),
                confidence=str(data["confidence"]),
                affected_accounts=[str(value) for value in data.get("affected_accounts", [])],
                trigger=str(data["trigger"]),
                account_snapshots=snapshots,
                total_quota_hours_lost=_optional_float(data.get("total_quota_hours_lost")),
                previous_reset_predictions=_optional_str_dict(
                    data.get("previous_reset_predictions")
                ),
                new_reset_predictions=_optional_str_dict(data.get("new_reset_predictions")),
                pending_kicks_invalidated=[
                    str(value) for value in data.get("pending_kicks_invalidated", [])
                ],
                notification_sent=bool(data.get("notification_sent", False)),
                summary=str(data["summary"]),
                detail=str(data["detail"]),
                failover_guidance=_optional_str(data.get("failover_guidance")),
                notification_skip_reason=_optional_str(data.get("notification_skip_reason")),
                acknowledged_at=_optional_str(data.get("acknowledged_at")),
                acknowledged_by=_optional_str(data.get("acknowledged_by")),
                recovery_action=_optional_str(data.get("recovery_action")),
                recovery_action_at=_optional_str(data.get("recovery_action_at")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def detect_global_reset_event(
    *,
    previous_entries: dict[str, dict],
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    kick_history: list[KickEvent],
    now: datetime,
    restarted_from_disk: bool = False,
) -> ResetEvent | None:
    now = now.astimezone(timezone.utc)
    previous_by_key = _valid_previous_entries(previous_entries)
    if not previous_by_key:
        return None

    provider_accounts: dict[str, list[AccountConfig]] = {}
    for account in accounts:
        provider_accounts.setdefault(account.provider, []).append(account)

    recently_kicked = _recently_kicked_labels(kick_history, previous_by_key, now)
    best: ResetEvent | None = None
    for provider, provider_group in provider_accounts.items():
        provider_group = _independent_provider_reset_accounts(provider_group)
        if len(provider_group) < 2:
            continue
        candidates = _provider_reset_candidates(
            provider_group,
            previous_by_key,
            statuses_by_key,
            recently_kicked,
        )
        event = _event_from_candidates(
            provider,
            candidates,
            accounts,
            statuses_by_key,
            now,
            restarted_from_disk=restarted_from_disk,
        )
        if event is not None and _event_rank(event) > _event_rank(best):
            best = event
    return best


def detect_reset_events(
    *,
    previous_entries: dict[str, dict],
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    kick_history: list[KickEvent],
    now: datetime,
    restarted_from_disk: bool = False,
) -> list[ResetEvent]:
    global_event = detect_global_reset_event(
        previous_entries=previous_entries,
        accounts=accounts,
        statuses_by_key=statuses_by_key,
        kick_history=kick_history,
        now=now,
        restarted_from_disk=restarted_from_disk,
    )
    suppressed = set(global_event.affected_accounts) if global_event is not None else set()
    observations = detect_provider_reset_observations(
        previous_entries=previous_entries,
        accounts=accounts,
        statuses_by_key=statuses_by_key,
        kick_history=kick_history,
        now=now,
        suppress_accounts=suppressed,
    )
    return ([global_event] if global_event is not None else []) + observations


def detect_provider_reset_observations(
    *,
    previous_entries: dict[str, dict],
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    kick_history: list[KickEvent],
    now: datetime,
    suppress_accounts: set[str] | None = None,
) -> list[ResetEvent]:
    now = now.astimezone(timezone.utc)
    previous_by_key = _valid_previous_entries(previous_entries)
    if not previous_by_key:
        return []
    suppressed = suppress_accounts or set()
    recently_kicked = _recently_kicked_labels(kick_history, previous_by_key, now)
    events: list[ResetEvent] = []
    for account in accounts:
        if account.label in suppressed:
            continue
        key = account_key_string(account)
        previous_entry = previous_by_key.get(key)
        current = statuses_by_key.get(key)
        if previous_entry is None or not isinstance(current, AccountStatus):
            continue
        previous = previous_entry["status"]
        if not _single_account_observation_eligible(account, previous_entry, current, recently_kicked):
            continue
        trigger = _single_account_observation_trigger(previous, current, now)
        if trigger is None:
            continue
        event = _single_account_observation_event(account, previous, current, now, trigger)
        events.append(event)
    return events


def is_provider_reset_observation(event: ResetEvent) -> bool:
    return event.trigger in SINGLE_ACCOUNT_OBSERVATION_TRIGGERS


def _independent_provider_reset_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
    selected: dict[tuple[str, str], AccountConfig] = {}
    ordered: list[tuple[str, str]] = []
    for account in accounts:
        key = _global_reset_independence_key(account)
        current = selected.get(key)
        if current is None:
            selected[key] = account
            ordered.append(key)
            continue
        if _global_reset_account_preference(account) > _global_reset_account_preference(current):
            selected[key] = account
    return [selected[key] for key in ordered]


def _global_reset_independence_key(account: AccountConfig) -> tuple[str, str]:
    if account.provider == "codex" and account.provider_home:
        return (account.provider, str(account.provider_home))
    return (account.provider, account_key_string(account))


def _global_reset_account_preference(account: AccountConfig) -> int:
    if account.provider == "codex" and codex_rate_limit_id_for_account(account) == CODEX_DEFAULT_RATE_LIMIT_ID:
        return 2
    return 1


def invalidate_event_pending_kicks(event: ResetEvent) -> list[PendingKick]:
    removed: list[PendingKick] = []
    if is_provider_reset_observation(event):
        return removed
    if event.confidence != "confirmed":
        return removed
    for label in event.affected_accounts:
        removed.extend(invalidate_pending_kicks(account_label=label))
    event.pending_kicks_invalidated = sorted({pending.account_label for pending in removed})
    _refresh_event_text(event)
    return removed


def acknowledge_reset_events(
    *,
    event_ids: Iterable[str] | None = None,
    latest: bool = False,
    all_events: bool = False,
    acknowledged_by: str = "cli",
    path: Path | None = None,
    now: datetime | None = None,
) -> list[ResetEvent]:
    """Acknowledge reset events with a locked atomic JSONL rewrite."""
    target_ids = {str(event_id) for event_id in (event_ids or [])}
    acknowledged_at = iso_utc(now or datetime.now(timezone.utc))
    updated: list[ResetEvent] = []

    def apply(events: list[ResetEvent]) -> list[ResetEvent]:
        candidates = [event for event in events if event.acknowledged_at is None]
        if all_events:
            targets = candidates
        elif latest:
            targets = candidates[-1:] if candidates else []
        elif target_ids:
            targets = [event for event in events if event.id in target_ids]
        else:
            targets = []
        for event in targets:
            event.acknowledged_at = acknowledged_at
            event.acknowledged_by = acknowledged_by
            updated.append(event)
        return events

    _update_reset_events(apply, path=path)
    return updated


def record_reset_event_recovery_action(
    event_id: str,
    action: str,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> ResetEvent | None:
    """Record recovery metadata for one reset event with a locked atomic rewrite."""
    action_at = iso_utc(now or datetime.now(timezone.utc))
    updated: ResetEvent | None = None

    def apply(events: list[ResetEvent]) -> list[ResetEvent]:
        nonlocal updated
        for event in events:
            if event.id == event_id:
                event.recovery_action = action
                event.recovery_action_at = action_at
                updated = event
                break
        return events

    _update_reset_events(apply, path=path)
    return updated


def _update_reset_events(
    update: Callable[[list[ResetEvent]], list[ResetEvent]],
    *,
    path: Path | None = None,
) -> tuple[list[ResetEvent], list[ResetEvent]]:
    path = path or RESET_EVENTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        before = _load_reset_events_unlocked(path)
        after = update([event for event in before])
        _write_reset_events_unlocked(path, after)
    return before, after


def append_reset_event(event: ResetEvent, path: Path | None = None) -> bool:
    path = path or RESET_EVENTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        events = _load_reset_events_unlocked(path)
        if _is_duplicate_event(event, events):
            return False
        events.append(event)
        events = _retained_events(events, datetime.now(timezone.utc))
        _write_reset_events_unlocked(path, events)
    return True


def has_duplicate_reset_event(event: ResetEvent, path: Path | None = None) -> bool:
    path = path or RESET_EVENTS_FILE
    try:
        with state_file_lock(path):
            return _is_duplicate_event(event, _load_reset_events_unlocked(path))
    except OSError:
        return False


def load_reset_events(path: Path | None = None, limit: int | None = None) -> list[ResetEvent]:
    path = path or RESET_EVENTS_FILE
    try:
        with state_file_lock(path):
            events = _load_reset_events_unlocked(path)
    except OSError:
        return []
    if limit is not None and limit >= 0:
        events = events[-limit:]
    return events


def filter_reset_events(
    events: Iterable[ResetEvent],
    *,
    since: datetime | None = None,
    provider: str | None = None,
    unacknowledged: bool = False,
) -> list[ResetEvent]:
    provider_filter = provider.lower() if provider else None
    filtered = []
    for event in events:
        detected_at = parse_utc(event.detected_at)
        if since is not None and (detected_at is None or detected_at < since):
            continue
        if provider_filter and event.provider.lower() != provider_filter:
            continue
        if unacknowledged and event.acknowledged_at is not None:
            continue
        filtered.append(event)
    return filtered


def recent_reset_events(
    hours: int = RESET_EVENT_RECENT_HOURS,
    *,
    unacknowledged: bool = False,
) -> list[ResetEvent]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return filter_reset_events(load_reset_events(), since=cutoff, unacknowledged=unacknowledged)


def reset_events_csv(events: Iterable[ResetEvent]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "detected_at",
            "provider",
            "confidence",
            "affected_accounts",
            "total_quota_hours_lost",
            "summary",
        ],
    )
    writer.writeheader()
    for event in events:
        writer.writerow(
            {
                "id": event.id,
                "detected_at": event.detected_at,
                "provider": event.provider,
                "confidence": event.confidence,
                "affected_accounts": ", ".join(event.affected_accounts),
                "total_quota_hours_lost": event.total_quota_hours_lost,
                "summary": event.summary,
            }
        )
    return output.getvalue()


def account_impacts(event: ResetEvent) -> list[dict[str, object]]:
    detected = parse_utc(event.detected_at)
    impacts: list[dict[str, object]] = []
    for snapshot in event.account_snapshots:
        previous_reset = snapshot.before_weekly_resets_at or event.previous_reset_predictions.get(
            snapshot.account
        )
        new_reset = snapshot.after_weekly_resets_at or event.new_reset_predictions.get(
            snapshot.account
        )
        quota_hours_lost = None
        if not is_provider_reset_observation(event):
            quota_hours_lost = _quota_hours_lost_from_snapshot(
                detected,
                snapshot.before_weekly_used_pct,
                previous_reset,
            )
        impacts.append(
            {
                "account": snapshot.account,
                "before_state": snapshot.before_state,
                "after_state": snapshot.after_state,
                "before_weekly_used_pct": snapshot.before_weekly_used_pct,
                "after_weekly_used_pct": snapshot.after_weekly_used_pct,
                "previous_reset_prediction": previous_reset,
                "new_reset_prediction": new_reset,
                "quota_hours_lost": quota_hours_lost,
            }
        )
    return impacts


def _quota_hours_lost_from_snapshot(
    detected: datetime | None,
    used_percent: float | None,
    previous_reset: str | None,
) -> float | None:
    reset = parse_utc(previous_reset)
    if detected is None or reset is None or used_percent is None:
        return None
    remaining_hours = max(0.0, (reset - detected).total_seconds() / 3600)
    remaining_quota = max(0.0, 100.0 - min(100.0, used_percent)) / 100
    return round(remaining_hours * remaining_quota, 1)


def parse_since(value: str | None, now: datetime | None = None) -> datetime | None:
    if not value:
        return None
    now = now or datetime.now(timezone.utc)
    raw = value.strip().lower()
    try:
        if raw.endswith("d"):
            return now - timedelta(days=int(raw[:-1]))
        if raw.endswith("h"):
            return now - timedelta(hours=int(raw[:-1]))
        return parse_utc(raw)
    except (TypeError, ValueError):
        return None


def format_event_age(event: ResetEvent, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    detected = parse_utc(event.detected_at)
    if detected is None:
        return "recently"
    seconds = max(0, int((now.astimezone(timezone.utc) - detected).total_seconds()))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_utc(value: datetime | float | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(float(value), timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_previous_entries(entries: dict[str, dict]) -> dict[str, dict]:
    valid = {}
    for key, entry in entries.items():
        status = entry.get("status")
        account = entry.get("account")
        if (
            not isinstance(account, AccountConfig)
            or not isinstance(status, AccountStatus)
            or status.state == AccountState.UNKNOWN
            or status.stale
            or entry.get("refresh_error")
            or status.resets_at is None
        ):
            continue
        valid[key] = entry
    return valid


def _different_poll_cycles(times: list[datetime]) -> bool:
    if len(times) < 2:
        return False
    return int((max(times) - min(times)).total_seconds()) > RESET_EVENT_POLL_TOLERANCE_SECONDS


def _recently_kicked_labels(
    history: list[KickEvent],
    previous_entries: dict[str, dict],
    now: datetime,
) -> set[str]:
    previous_times = [
        parse_utc(entry.get("provider_observed_at") or entry.get("cached_at"))
        for entry in previous_entries.values()
    ]
    previous_times = [value for value in previous_times if value is not None]
    since = (
        min(previous_times) - timedelta(minutes=10)
        if previous_times
        else now - timedelta(minutes=10)
    )
    return {
        event.label
        for event in history
        if event.success
        and event.timestamp >= since.timestamp()
        and event.timestamp <= now.timestamp()
    }


def _provider_reset_candidates(
    accounts: list[AccountConfig],
    previous_by_key: dict[str, dict],
    statuses_by_key: dict[str, AccountStatus],
    recently_kicked: set[str],
) -> dict[str, list[tuple[AccountConfig, AccountStatus, AccountStatus]]]:
    candidates = {
        "simultaneous_fresh": [],
        "reset_shift": [],
        "usage_drop": [],
    }
    for account in accounts:
        key = account_key_string(account)
        previous_entry = previous_by_key.get(key)
        current = statuses_by_key.get(key)
        if previous_entry is None or not isinstance(current, AccountStatus):
            continue
        if (
            account.label in recently_kicked
            or current.state == AccountState.UNKNOWN
            or current.stale
            or is_synthetic_status(current)
        ):
            continue
        previous = previous_entry["status"]
        if previous.state in {AccountState.ACTIVE, AccountState.WAITING} and current.state == AccountState.FRESH:
            candidates["simultaneous_fresh"].append((account, previous, current))
        if (
            previous.resets_at is not None
            and current.resets_at is not None
            and current.resets_at - previous.resets_at > RESET_SHIFT_THRESHOLD_SECONDS
        ):
            candidates["reset_shift"].append((account, previous, current))
        if (
            previous.used_percent is not None
            and current.used_percent is not None
            and previous.used_percent - current.used_percent >= USAGE_DROP_THRESHOLD_POINTS
            and current.used_percent <= USAGE_DROP_CURRENT_MAX
        ):
            candidates["usage_drop"].append((account, previous, current))
    return candidates


def _single_account_observation_eligible(
    account: AccountConfig,
    previous_entry: dict,
    current: AccountStatus,
    recently_kicked: set[str],
) -> bool:
    if (
        account.label in recently_kicked
        or current.state == AccountState.UNKNOWN
        or current.stale
        or is_synthetic_status(current)
    ):
        return False
    previous_observed = parse_utc(previous_entry.get("provider_observed_at") or previous_entry.get("cached_at"))
    current_observed = parse_utc(current.observed_at)
    if previous_observed is None or current_observed is None:
        return False
    if current_observed <= previous_observed:
        return False
    return True


def _single_account_observation_trigger(
    previous: AccountStatus,
    current: AccountStatus,
    now: datetime,
) -> str | None:
    if previous.used_percent is None or current.used_percent is None:
        return None
    drop = previous.used_percent - current.used_percent
    if (
        previous.used_percent < SINGLE_ACCOUNT_USAGE_DROP_PREVIOUS_MIN
        or current.used_percent > SINGLE_ACCOUNT_USAGE_DROP_CURRENT_MAX
        or drop < SINGLE_ACCOUNT_USAGE_DROP_THRESHOLD_POINTS
    ):
        return None
    if _single_account_weekly_reset_changed(previous, current, now):
        return "single_account_weekly_reset"
    return "single_account_usage_drop"


def _single_account_weekly_reset_changed(
    previous: AccountStatus,
    current: AccountStatus,
    now: datetime,
) -> bool:
    if current.resets_at is None:
        return False
    if (
        previous.resets_at is not None
        and current.resets_at - previous.resets_at >= SINGLE_ACCOUNT_WEEKLY_RESET_SHIFT_SECONDS
    ):
        return True
    return current.resets_at - now.timestamp() >= SINGLE_ACCOUNT_FULL_WEEKLY_WINDOW_SECONDS


def _single_account_observation_event(
    account: AccountConfig,
    previous: AccountStatus,
    current: AccountStatus,
    now: datetime,
    trigger: str,
) -> ResetEvent:
    event = ResetEvent(
        id=str(uuid.uuid4()),
        detected_at=iso_utc(now) or "",
        provider=account.provider,
        confidence="possible",
        affected_accounts=[account.label],
        trigger=trigger,
        account_snapshots=[_snapshot(account, previous, current)],
        total_quota_hours_lost=None,
        previous_reset_predictions={account.label: iso_utc(previous.resets_at)},
        new_reset_predictions={account.label: iso_utc(current.resets_at)},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="",
        detail="",
        failover_guidance=None,
    )
    _refresh_event_text(event)
    return event


def _event_from_candidates(
    provider: str,
    candidates: dict[str, list[tuple[AccountConfig, AccountStatus, AccountStatus]]],
    all_accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    now: datetime,
    *,
    restarted_from_disk: bool,
) -> ResetEvent | None:
    trigger, rows = max(candidates.items(), key=lambda item: (len(item[1]), _trigger_priority(item[0])))
    if len(rows) < 2:
        return None
    if _different_poll_cycles([parse_utc(after.observed_at) or now for _account, _before, after in rows]):
        return None
    confidence = _confidence(rows, restarted_from_disk=restarted_from_disk)
    snapshots = [_snapshot(account, before, after) for account, before, after in rows]
    affected = [account.label for account, _before, _after in rows]
    previous_resets = {
        account.label: iso_utc(before.resets_at)
        for account, before, _after in rows
    }
    new_resets = {
        account.label: iso_utc(after.resets_at)
        for account, _before, after in rows
    }
    losses = [_quota_hours_lost(before, now) for _account, before, _after in rows]
    total_loss = round(sum(losses), 1) if losses else None
    event = ResetEvent(
        id=str(uuid.uuid4()),
        detected_at=iso_utc(now) or "",
        provider=provider,
        confidence=confidence,
        affected_accounts=affected,
        trigger=trigger,
        account_snapshots=snapshots,
        total_quota_hours_lost=total_loss,
        previous_reset_predictions=previous_resets,
        new_reset_predictions=new_resets,
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="",
        detail="",
        failover_guidance=_failover_guidance(all_accounts, statuses_by_key, set(affected)),
    )
    _refresh_event_text(event)
    return event


def _trigger_priority(trigger: str) -> int:
    return {"simultaneous_fresh": 3, "reset_shift": 2, "usage_drop": 1}.get(trigger, 0)


def _confidence(
    rows: list[tuple[AccountConfig, AccountStatus, AccountStatus]],
    *,
    restarted_from_disk: bool,
) -> str:
    if restarted_from_disk:
        return "possible"
    if len(rows) >= 3:
        return "confirmed"
    spread = _previous_reset_spread_seconds(rows)
    if spread > 48 * 60 * 60:
        return "confirmed"
    if spread > 24 * 60 * 60:
        return "likely"
    return "possible"


def _previous_reset_spread_seconds(
    rows: list[tuple[AccountConfig, AccountStatus, AccountStatus]],
) -> float:
    resets = [before.resets_at for _account, before, _after in rows if before.resets_at is not None]
    if len(resets) < 2:
        return 0
    return max(resets) - min(resets)


def _snapshot(
    account: AccountConfig,
    before: AccountStatus,
    after: AccountStatus,
) -> AccountSnapshot:
    return AccountSnapshot(
        account=account.label,
        before_state=before.state.value,
        before_weekly_used_pct=before.used_percent,
        before_weekly_resets_at=iso_utc(before.resets_at),
        after_state=after.state.value,
        after_weekly_used_pct=after.used_percent,
        after_weekly_resets_at=iso_utc(after.resets_at),
        before_session_resets_at=iso_utc(before.session_resets_at),
        after_session_resets_at=iso_utc(after.session_resets_at),
    )


def _quota_hours_lost(status: AccountStatus, now: datetime) -> float:
    if status.resets_at is None or status.used_percent is None:
        return 0
    remaining_hours = max(0.0, (status.resets_at - now.timestamp()) / 3600)
    remaining_quota = max(0.0, 100.0 - min(100.0, status.used_percent)) / 100
    return remaining_hours * remaining_quota


def _failover_guidance(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    affected: set[str],
) -> str | None:
    options = []
    for account in accounts:
        if account.label in affected or not account.visible:
            continue
        status = statuses_by_key.get(account_key_string(account))
        if (
            not isinstance(status, AccountStatus)
            or status.state == AccountState.UNKNOWN
            or status.stale
            or is_synthetic_status(status)
        ):
            continue
        used = status.used_percent
        remaining = None if used is None else max(0.0, 100.0 - used)
        if remaining is None or remaining <= 0:
            continue
        usability_rank = {
            AccountState.ACTIVE: 3,
            AccountState.FRESH: 2,
            AccountState.WAITING: 1,
        }.get(status.state, 0)
        options.append((usability_rank, remaining, account, status))
    if not options:
        return "No other accounts have significant remaining quota. Wait for the new windows to anchor."
    options.sort(key=lambda item: (item[0], item[1]), reverse=True)
    rendered = []
    for _rank, remaining, account, status in options[:2]:
        reset = status.resets_at_local
        rendered.append(f"{account.label} ({account.provider}) has {remaining:.0f}% weekly remaining, resets {reset}")
    return "Failover: " + "; ".join(rendered) + "."


def _refresh_event_text(event: ResetEvent) -> None:
    if is_provider_reset_observation(event):
        _refresh_provider_observation_text(event)
        return
    loss = (
        f", ~{event.total_quota_hours_lost:g}h saved quota lost"
        if event.total_quota_hours_lost is not None and event.total_quota_hours_lost > 0
        else ""
    )
    event.summary = (
        f"{event.provider.title()} global reset detected. "
        f"{len(event.affected_accounts)} accounts affected{loss}."
    )
    pending = ""
    if event.pending_kicks_invalidated:
        pending = "\nInvalidated pending kicks: " + ", ".join(event.pending_kicks_invalidated) + "."
    event.detail = (
        f"Trigger: {event.trigger}\n"
        f"Confidence: {event.confidence}\n"
        f"Affected: {', '.join(event.affected_accounts)}.\n"
        f"{event.failover_guidance or ''}"
        f"{pending}"
    ).strip()


def _refresh_provider_observation_text(event: ResetEvent) -> None:
    snapshot = event.account_snapshots[0] if event.account_snapshots else None
    account = event.affected_accounts[0] if event.affected_accounts else "account"
    before = _format_optional_percent(snapshot.before_weekly_used_pct if snapshot else None)
    after = _format_optional_percent(snapshot.after_weekly_used_pct if snapshot else None)
    reset_moved = event.trigger == "single_account_weekly_reset"
    reset_sentence = (
        "Weekly reset prediction moved materially later, so this looks like an account/provider reset observation."
        if reset_moved
        else "Weekly reset prediction did not materially change, so this may be provider-side usage recalculation or stale-cache correction."
    )
    session_sentence = _session_supporting_detail(snapshot)
    event.summary = (
        f"{event.provider.title()} provider reset observation: "
        f"{account} weekly usage changed {before} -> {after}."
    )
    event.detail = (
        f"Trigger: {event.trigger}\n"
        f"Confidence: {event.confidence}\n"
        f"Affected: {account}.\n"
        f"Weekly usage: {before} -> {after}.\n"
        f"Weekly reset prediction: "
        f"{event.previous_reset_predictions.get(account) or '—'} -> "
        f"{event.new_reset_predictions.get(account) or '—'}.\n"
        f"{reset_sentence}"
        f"{session_sentence}"
    ).strip()


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}%"


def _session_supporting_detail(snapshot: AccountSnapshot | None) -> str:
    if snapshot is None:
        return ""
    before = parse_utc(snapshot.before_session_resets_at)
    after = parse_utc(snapshot.after_session_resets_at)
    if before is None or after is None:
        return ""
    if after - before <= timedelta(hours=1):
        return ""
    return (
        "\nSession reset prediction also moved later: "
        f"{snapshot.before_session_resets_at} -> {snapshot.after_session_resets_at}."
    )


def _event_rank(event: ResetEvent | None) -> tuple[int, int]:
    if event is None:
        return (0, 0)
    confidence_rank = {"possible": 1, "likely": 2, "confirmed": 3}.get(event.confidence, 0)
    return (confidence_rank, len(event.affected_accounts))


def _is_duplicate_event(event: ResetEvent, events: list[ResetEvent]) -> bool:
    detected = parse_utc(event.detected_at)
    affected = set(event.affected_accounts)
    if detected is None:
        return False
    for existing in reversed(events):
        existing_detected = parse_utc(existing.detected_at)
        if existing_detected is None:
            continue
        if abs((detected - existing_detected).total_seconds()) > RESET_EVENT_DEDUP_SECONDS:
            continue
        if (
            existing.provider == event.provider
            and existing.trigger == event.trigger
            and set(existing.affected_accounts) == affected
        ):
            return True
    return False


def _retained_events(events: list[ResetEvent], now: datetime) -> list[ResetEvent]:
    cutoff = now - timedelta(days=RESET_EVENT_MAX_AGE_DAYS)
    retained = [
        event
        for event in events
        if (detected := parse_utc(event.detected_at)) is not None and detected >= cutoff
    ]
    return retained[-RESET_EVENT_MAX_COUNT:]


def _load_reset_events_unlocked(path: Path) -> list[ResetEvent]:
    if not path.exists():
        return []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = ResetEvent.from_dict(data)
        if event is not None:
            events.append(event)
    return events


def _write_reset_events_unlocked(path: Path, events: list[ResetEvent]) -> None:
    text = "".join(json.dumps(item.to_dict()) + "\n" for item in events)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _optional_str(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str_dict(value) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _optional_str(item) for key, item in value.items()}
