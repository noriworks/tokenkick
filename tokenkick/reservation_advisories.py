"""Reserved-account quiet-period advisories for orchestration plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .kicker import KICKABLE_PROVIDERS
from .models import (
    CONFIG_DIR,
    AccountConfig,
    AccountState,
    AccountStatus,
    account_key_string,
    weekly_quota_exhausted,
)
from .scheduling import (
    PendingKick,
    ScheduleReason,
    from_utc_iso,
    pending_kick_gave_up,
    pending_kick_next_action_at,
    to_utc_iso,
)
from .state_io import atomic_write_text, state_file_lock

RESERVATION_ADVISORY_STATE_FILE = CONFIG_DIR / "reserved-account-advisories.json"
QUIET_PERIOD_SOON_SECONDS = 30 * 60
RISK_SAFE = "safe"
RISK_QUIET_PERIOD_SOON = "quiet_period_soon"
RISK_QUIET_PERIOD_ACTIVE = "quiet_period_active"
RISK_PLAN_MAY_BE_COMPROMISED = "plan_may_be_compromised"
ACTIONABLE_RISK_STATES = (
    RISK_QUIET_PERIOD_SOON,
    RISK_QUIET_PERIOD_ACTIVE,
    RISK_PLAN_MAY_BE_COMPROMISED,
)
ADVISORY_STATE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class ReservationAdvisory:
    account_key: str
    account_label: str
    provider: str
    pending_purpose: str
    risk_state: str
    quiet_start: datetime
    kick_at: datetime
    work_start: datetime
    work_end: datetime
    suggestion_label: str | None = None
    suggestion_reason: str | None = None

    @property
    def notification_key(self) -> str:
        return f"{self.account_key}::{to_utc_iso(self.kick_at)}::{self.risk_state}"

    def to_dict(self) -> dict:
        return {
            "account_key": self.account_key,
            "account_label": self.account_label,
            "provider": self.provider,
            "pending_purpose": self.pending_purpose,
            "risk_state": self.risk_state,
            "quiet_start": to_utc_iso(self.quiet_start),
            "kick_at": to_utc_iso(self.kick_at),
            "work_start": to_utc_iso(self.work_start),
            "work_end": to_utc_iso(self.work_end),
            "suggestion_label": self.suggestion_label,
            "suggestion_reason": self.suggestion_reason,
            "message": format_reservation_advisory_message(self),
        }


def build_reservation_advisories(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    pending: dict[str, PendingKick],
    *,
    now: datetime,
    soon_seconds: int = QUIET_PERIOD_SOON_SECONDS,
) -> list[ReservationAdvisory]:
    current = now.astimezone(timezone.utc)
    advisories: list[ReservationAdvisory] = []
    for account in accounts:
        key = account_key_string(account)
        pending_kick = pending.get(key)
        status = statuses_by_key.get(key)
        advisory = _reservation_advisory_for_account(
            account,
            status,
            pending_kick,
            accounts=accounts,
            statuses_by_key=statuses_by_key,
            pending=pending,
            now=current,
            soon_seconds=soon_seconds,
        )
        if advisory is not None:
            advisories.append(advisory)
    return sorted(advisories, key=lambda item: (item.kick_at, item.account_label, item.risk_state))


def _reservation_advisory_for_account(
    account: AccountConfig,
    status: AccountStatus | None,
    pending_kick: PendingKick | None,
    *,
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    pending: dict[str, PendingKick],
    now: datetime,
    soon_seconds: int,
) -> ReservationAdvisory | None:
    if status is None or status.stale or status.state == AccountState.UNKNOWN:
        return None
    if pending_kick is None or pending_kick.reason != ScheduleReason.ORCHESTRATED.value:
        return None
    if pending_kick_gave_up(pending_kick):
        return None
    kick_at = pending_kick_next_action_at(pending_kick)
    if kick_at is None:
        return None
    kick_at = kick_at.astimezone(timezone.utc)
    if kick_at <= now:
        return None
    if status.session_resets_at is None:
        return None
    quiet_start = datetime.fromtimestamp(status.session_resets_at, tz=timezone.utc)
    risk_state = _reservation_risk_state(status, quiet_start, kick_at, now, soon_seconds)
    if risk_state is None:
        return None
    try:
        work_start = from_utc_iso(pending_kick.work_start)
        work_end = from_utc_iso(pending_kick.work_end)
    except ValueError:
        return None
    suggestion_label, suggestion_reason = _reservation_suggestion(
        reserved_account=account,
        accounts=accounts,
        statuses_by_key=statuses_by_key,
        pending=pending,
        now=now,
    )
    return ReservationAdvisory(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        pending_purpose=pending_kick.purpose,
        risk_state=risk_state,
        quiet_start=quiet_start,
        kick_at=kick_at,
        work_start=work_start,
        work_end=work_end,
        suggestion_label=suggestion_label,
        suggestion_reason=suggestion_reason,
    )


def _reservation_risk_state(
    status: AccountStatus,
    quiet_start: datetime,
    kick_at: datetime,
    now: datetime,
    soon_seconds: int,
) -> str | None:
    if _reservation_plan_may_be_compromised(status, kick_at):
        return RISK_PLAN_MAY_BE_COMPROMISED
    if quiet_start >= kick_at:
        return RISK_SAFE
    if quiet_start <= now < kick_at:
        return RISK_QUIET_PERIOD_ACTIVE
    if now < quiet_start <= now + timedelta(seconds=soon_seconds):
        return RISK_QUIET_PERIOD_SOON
    return RISK_SAFE


def _reservation_plan_may_be_compromised(
    status: AccountStatus,
    kick_at: datetime,
) -> bool:
    if status.stale or status.state == AccountState.UNKNOWN:
        return False
    if status.session_resets_at is None:
        return False
    observed_reset = datetime.fromtimestamp(status.session_resets_at, tz=timezone.utc)
    used = float(status.session_used_percent or 0.0)
    return status.state == AccountState.ACTIVE and used > 0 and observed_reset > kick_at


def _reservation_suggestion(
    *,
    reserved_account: AccountConfig,
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    pending: dict[str, PendingKick],
    now: datetime,
) -> tuple[str | None, str | None]:
    candidates: list[tuple[tuple, AccountConfig]] = []
    soon_cutoff = now + timedelta(seconds=QUIET_PERIOD_SOON_SECONDS)
    for account in accounts:
        if account.label == reserved_account.label:
            continue
        if account.orchestration_role == "excluded":
            continue
        if not account.visible or account.provider not in KICKABLE_PROVIDERS:
            continue
        status = statuses_by_key.get(account_key_string(account))
        if status is None or status.stale or status.state == AccountState.UNKNOWN:
            continue
        if weekly_quota_exhausted(status):
            continue
        usability_rank = _reservation_status_usability_rank(status)
        if usability_rank is None:
            continue
        action_at = _future_pending_action_at(pending.get(account_key_string(account)), now)
        if action_at is not None and action_at <= soon_cutoff:
            continue
        orchestrated_action_at = _future_orchestrated_pending_action_at(
            pending.get(account_key_string(account)),
            now,
        )
        unreserved = orchestrated_action_at is None
        same_provider = account.provider == reserved_account.provider
        latest_needed = (
            orchestrated_action_at.timestamp()
            if orchestrated_action_at is not None
            else float("inf")
        )
        headroom = 100.0 - float(status.used_percent or 0.0)
        candidates.append(
            ((unreserved, latest_needed, same_provider, usability_rank, headroom, account.label), account)
        )
    if not candidates:
        return None, None
    _score, selected = max(candidates, key=lambda item: item[0])
    action_at = _future_orchestrated_pending_action_at(pending.get(account_key_string(selected)), now)
    if action_at is None:
        return selected.label, "not reserved for this plan"
    return selected.label, f"not needed until {_format_clock(action_at)}"


def _reservation_status_usability_rank(status: AccountStatus) -> int | None:
    if status.state == AccountState.FRESH:
        return 2
    if status.state != AccountState.ACTIVE:
        return None
    if status.session_used_percent is not None and status.session_used_percent >= 100.0:
        return None
    if status.session_resets_in_seconds is not None and status.session_resets_in_seconds <= 0:
        return None
    return 1


def _future_pending_action_at(pending_kick: PendingKick | None, now: datetime) -> datetime | None:
    if pending_kick is None or pending_kick_gave_up(pending_kick):
        return None
    action_at = pending_kick_next_action_at(pending_kick)
    if action_at is None:
        return None
    action_at = action_at.astimezone(timezone.utc)
    return action_at if action_at > now else None


def _future_orchestrated_pending_action_at(
    pending_kick: PendingKick | None,
    now: datetime,
) -> datetime | None:
    if pending_kick is None or pending_kick.reason != ScheduleReason.ORCHESTRATED.value:
        return None
    return _future_pending_action_at(pending_kick, now)


def format_reservation_advisory_message(advisory: ReservationAdvisory) -> str:
    if advisory.risk_state == RISK_SAFE:
        return (
            f'"{advisory.account_label}" is reserved for an orchestration kick at '
            f"{_format_clock(advisory.kick_at)}, with no active quiet-period warning."
        )
    if advisory.risk_state == RISK_PLAN_MAY_BE_COMPROMISED:
        return (
            f'"{advisory.account_label}" may already be in use during a reserved '
            "orchestration window. Consider `tk plan cancel` and replan."
        )
    suggestion = _format_suggestion(advisory)
    if advisory.risk_state == RISK_QUIET_PERIOD_ACTIVE:
        return (
            f'"{advisory.account_label}" is in its reserved quiet period until '
            f"{_format_clock(advisory.kick_at)}. Avoid using it now, or cancel/replan "
            f"the orchestration.{suggestion}"
        )
    return (
        f'"{advisory.account_label}" is reserved for an orchestration kick at '
        f"{_format_clock(advisory.kick_at)}. Its current session should refresh at "
        f"{_format_clock(advisory.quiet_start)}. Avoid using it from "
        f"{_format_clock(advisory.quiet_start)} to {_format_clock(advisory.kick_at)}."
        f"{suggestion}"
    )


def _format_suggestion(advisory: ReservationAdvisory) -> str:
    if not advisory.suggestion_label:
        return ""
    reason = f"; it is {advisory.suggestion_reason}" if advisory.suggestion_reason else ""
    return f" Use {advisory.suggestion_label} instead{reason}."


def _format_clock(value: datetime) -> str:
    return value.astimezone().strftime("%H:%M")


def load_reservation_advisory_state() -> dict:
    if not RESERVATION_ADVISORY_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(RESERVATION_ADVISORY_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_reservation_advisory_notified(key: str, *, now: datetime) -> None:
    try:
        with state_file_lock(RESERVATION_ADVISORY_STATE_FILE):
            state = load_reservation_advisory_state()
            state = _prune_reservation_advisory_state(state, now=now)
            state[key] = {"notified_at": to_utc_iso(now)}
            RESERVATION_ADVISORY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                RESERVATION_ADVISORY_STATE_FILE,
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
    except OSError:
        return


def _prune_reservation_advisory_state(state: dict, *, now: datetime) -> dict:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=ADVISORY_STATE_RETENTION_DAYS)
    pruned: dict = {}
    for key, value in state.items():
        if _reservation_advisory_state_key_time(key) is not None:
            if _reservation_advisory_state_key_time(key) < cutoff:
                continue
            pruned[key] = value
            continue
        notified_at = _reservation_advisory_state_notified_at(value)
        if notified_at is not None and notified_at < cutoff:
            continue
        pruned[key] = value
    return pruned


def _reservation_advisory_state_key_time(key: str) -> datetime | None:
    parts = key.split("::")
    if len(parts) < 3:
        return None
    try:
        return from_utc_iso(parts[1]).astimezone(timezone.utc)
    except ValueError:
        return None


def _reservation_advisory_state_notified_at(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("notified_at")
    if not isinstance(raw, str):
        return None
    try:
        return from_utc_iso(raw).astimezone(timezone.utc)
    except ValueError:
        return None
