"""Shared provider-source parsing helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from .models import AccountState, AccountStatus

CODEX_SESSION_WINDOW_MINUTES = 300
CODEX_TINY_SESSION_MAX_USED_PERCENT = 1.0


def _ensure_status_metadata(status: AccountStatus, source_detail: str) -> AccountStatus:
    if status.observed_at is None:
        status.observed_at = _status_observed_at()
    if status.source_detail is None:
        status.source_detail = source_detail
    _fill_status_reset_anchors(status)
    return status


def _status_observed_at() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fill_status_reset_anchors(status: AccountStatus) -> None:
    observed_ts = _parse_reset_timestamp(status.observed_at)
    if observed_ts is None:
        observed_ts = time.time()
    if status.resets_at is None and status.resets_in_seconds is not None:
        status.resets_at = observed_ts + status.resets_in_seconds
    if status.session_resets_at is None and status.session_resets_in_seconds is not None:
        status.session_resets_at = observed_ts + status.session_resets_in_seconds


def _normalize_codex_tiny_session_window(
    *,
    used_percent: float | None,
    resets_in_seconds: int | None,
    window_minutes: int | None,
) -> tuple[float | None, int | None]:
    if (
        window_minutes == CODEX_SESSION_WINDOW_MINUTES
        and used_percent is not None
        and 0.0 < used_percent <= CODEX_TINY_SESSION_MAX_USED_PERCENT
    ):
        return used_percent, CODEX_SESSION_WINDOW_MINUTES * 60
    return used_percent, resets_in_seconds


def _determine_state(used_pct: Optional[float], resets_in: Optional[int]) -> AccountState:
    """Determine account state from usage percentage and reset countdown."""
    if used_pct is not None and used_pct == 0.0:
        # 0% used = fresh window, hasn't been touched yet
        return AccountState.FRESH
    if used_pct is not None and used_pct > 0.0:
        return AccountState.ACTIVE
    if resets_in is not None and resets_in > 0:
        return AccountState.WAITING
    return AccountState.UNKNOWN


def _seconds_until_reset(
    resets_at: Any = None,
    resets_in: Any = None,
    now: float | None = None,
) -> Optional[int]:
    """Convert absolute or relative reset fields to seconds from now."""
    if resets_in is not None:
        return max(0, int(float(resets_in)))
    if resets_at is None:
        return None

    reset_ts = _parse_reset_timestamp(resets_at)
    if reset_ts is None:
        return None
    current_ts = time.time() if now is None else now
    return max(0, int(reset_ts - current_ts))


def _parse_reset_timestamp(value: Any) -> Optional[float]:
    """Parse Codex epoch seconds or CodexBar ISO reset timestamps."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value.isdigit():
            return float(value)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _nested_get(data: dict, keys: tuple) -> Optional[Any]:
    """Safely traverse nested dict keys."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
