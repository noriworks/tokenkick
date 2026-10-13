"""Codex quota capability policy, independent of subscription marketing names."""

from copy import copy
import re

from .models import AccountConfig, AccountState, AccountStatus

LIMITS_VERSION = 1
SESSION_MINUTES = 300
WEEKLY_MINUTES = 10080


def plan_type(value: object) -> str | None:
    # Keep unknown provider enum values, but never arbitrary response text.
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) else None


def session_limit_info(account: AccountConfig, status: AccountStatus | None) -> dict:
    detected = "unknown"
    if (
        status is not None
        and not status.stale
        and not status.error
        and status.state != AccountState.UNKNOWN
    ):
        if status.session_window_minutes == SESSION_MINUTES and status.codex_session_limit in {
            None,
            "present",
        }:
            detected = "present"
        elif (
            status.codex_session_limit == "absent"
            and status.source_detail == "codex-provider-usage"
        ):
            detected = "absent"
    override = account.codex_session_limit_override
    effective = detected if override == "auto" else override
    warning = None
    if override != "auto" and detected != "unknown" and override != detected:
        warning = "Manual override conflicts with the provider observation."
    if override == "present" and detected != "present":
        warning = "Session window forced present, but no valid current five-hour window is available; session kicks remain blocked."
    return {
        "detected_plan_type": status.detected_plan_type if status is not None else None,
        "plan_observed_at": status.observed_at if status is not None else None,
        "plan_stale": bool(
            status and (status.stale or status.error or status.state == AccountState.UNKNOWN)
        ),
        "session_limit_detected": detected,
        "session_limit_effective": effective,
        "session_limit_override": override,
        "session_limit_source": "manual"
        if override != "auto"
        else (status.source_detail if status else "unavailable"),
        "session_limit_warning": warning,
    }


def session_actions_allowed(account: AccountConfig | None, status: AccountStatus) -> bool:
    if account is not None and account.provider != "codex":
        return True
    if account is not None and account.codex_session_limit_override == "absent":
        return False
    if status.codex_session_limit in {"absent", "unknown"}:
        return False
    # Preserve legacy readings with an omitted duration for the existing bounded
    # phantom inference, but never reinterpret a known non-five-hour window.
    return status.session_window_minutes in {None, SESSION_MINUTES}


def display_status(account: AccountConfig, status: AccountStatus) -> AccountStatus:
    if account.provider != "codex" or session_actions_allowed(account, status):
        return status
    displayed = copy(status)
    displayed.session_used_percent = None
    displayed.session_resets_in_seconds = None
    displayed.session_resets_at = None
    displayed.session_window_minutes = None
    return displayed
