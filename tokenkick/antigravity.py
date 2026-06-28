"""Shared Antigravity helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from .models import AccountState, AccountStatus
from .source_utils import _determine_state, _parse_reset_timestamp, _seconds_until_reset


ANTIGRAVITY_QUOTA_WINDOW_SPECS: dict[str, dict[str, str | int]] = {
    "antigravity-quota-summary-gemini-5h": {
        "family": "gemini",
        "window_kind": "session",
        "window_minutes": 300,
    },
    "antigravity-quota-summary-gemini-weekly": {
        "family": "gemini",
        "window_kind": "weekly",
        "window_minutes": 10080,
    },
    "antigravity-quota-summary-3p-5h": {
        "family": "claude_gpt",
        "window_kind": "session",
        "window_minutes": 300,
    },
    "antigravity-quota-summary-3p-weekly": {
        "family": "claude_gpt",
        "window_kind": "weekly",
        "window_minutes": 10080,
    },
}
ANTIGRAVITY_QUOTA_WINDOW_IDS = tuple(ANTIGRAVITY_QUOTA_WINDOW_SPECS)
ANTIGRAVITY_QUOTA_PARSE_ERROR = (
    "Antigravity quota windows were incomplete or unrecognized in provider data."
)


def antigravity_status_from_extra_rate_windows(
    label: str,
    data: Any,
    *,
    window_source: str,
    source_detail: str | None = None,
) -> AccountStatus | None:
    """Build an Antigravity status from CodexBar-style named quota windows.

    Returns None when the payload has no named Antigravity windows. Returns an
    UNKNOWN status when named windows are present but cannot be mapped safely.
    """
    usage = (
        data.get("usage")
        if isinstance(data, dict) and isinstance(data.get("usage"), dict)
        else data
    )
    if not isinstance(usage, dict):
        return None

    extra = usage.get("extraRateWindows", usage.get("extra_rate_windows"))
    if extra is None:
        return None
    if not isinstance(extra, list):
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=ANTIGRAVITY_QUOTA_PARSE_ERROR,
            source_detail=source_detail,
        )

    quota_windows = _antigravity_quota_windows_from_extra(extra, window_source=window_source)
    if quota_windows is None:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=ANTIGRAVITY_QUOTA_PARSE_ERROR,
            source_detail=source_detail,
        )
    return antigravity_status_from_quota_windows(
        label,
        quota_windows,
        source_detail=source_detail,
    )


def antigravity_status_from_quota_windows(
    label: str,
    quota_windows: list[dict],
    *,
    source_detail: str | None = None,
) -> AccountStatus:
    summary = _most_constrained_antigravity_window(quota_windows)
    session = _most_constrained_antigravity_window(
        [window for window in quota_windows if window.get("window_kind") == "session"]
    )
    used_percent = _to_float_or_none(summary.get("used_percent"))
    resets_in = _to_int_or_none(summary.get("resets_in_seconds"))
    session_used_percent = (
        _to_float_or_none(session.get("used_percent")) if session is not None else None
    )
    session_resets_in = (
        _to_int_or_none(session.get("resets_in_seconds")) if session is not None else None
    )
    return AccountStatus(
        label=label,
        state=_determine_state(used_percent, resets_in),
        used_percent=used_percent,
        resets_in_seconds=resets_in,
        resets_at=_to_float_or_none(summary.get("resets_at")),
        window_minutes=_to_int_or_none(summary.get("window_minutes")),
        session_used_percent=session_used_percent,
        session_resets_in_seconds=session_resets_in,
        session_resets_at=(
            _to_float_or_none(session.get("resets_at")) if session is not None else None
        ),
        session_window_minutes=(
            _to_int_or_none(session.get("window_minutes")) if session is not None else None
        ),
        quota_windows=quota_windows,
        source_detail=source_detail,
    )


def has_complete_antigravity_quota_windows(status: AccountStatus) -> bool:
    quota_windows = status.quota_windows
    if not isinstance(quota_windows, list):
        return False
    ids = {window.get("id") for window in quota_windows if isinstance(window, dict)}
    return ids == set(ANTIGRAVITY_QUOTA_WINDOW_IDS)


def _antigravity_quota_windows_from_extra(
    extra: list,
    *,
    window_source: str,
) -> list[dict] | None:
    windows_by_id: dict[str, dict] = {}
    for entry in extra:
        if not isinstance(entry, dict):
            return None
        window_id = entry.get("id")
        if not isinstance(window_id, str) or window_id not in ANTIGRAVITY_QUOTA_WINDOW_SPECS:
            return None
        if window_id in windows_by_id:
            return None
        spec = ANTIGRAVITY_QUOTA_WINDOW_SPECS[window_id]
        window = entry.get("window")
        if not isinstance(window, dict):
            return None
        used_percent = _to_float_or_none(
            window.get("usedPercent", window.get("used_percent"))
        )
        window_minutes = _to_int_or_none(
            window.get("windowMinutes", window.get("window_minutes"))
        )
        resets_at_raw = window.get("resetsAt", window.get("resets_at"))
        resets_at = _parse_reset_timestamp(resets_at_raw)
        if (
            used_percent is None
            or window_minutes != spec["window_minutes"]
            or resets_at is None
        ):
            return None
        title = entry.get("title")
        windows_by_id[window_id] = {
            "id": window_id,
            "title": title if isinstance(title, str) else window_id,
            "family": spec["family"],
            "window_kind": spec["window_kind"],
            "used_percent": used_percent,
            "resets_at": resets_at,
            "resets_in_seconds": _seconds_until_reset(resets_at=resets_at_raw),
            "window_minutes": window_minutes,
            "source": window_source,
        }

    if set(windows_by_id) != set(ANTIGRAVITY_QUOTA_WINDOW_IDS):
        return None
    return [windows_by_id[window_id] for window_id in ANTIGRAVITY_QUOTA_WINDOW_IDS]


def _most_constrained_antigravity_window(quota_windows: list[dict]) -> dict:
    return min(
        quota_windows,
        key=lambda window: (
            -(_to_float_or_none(window.get("used_percent")) or 0.0),
            _to_float_or_none(window.get("resets_at")) or float("inf"),
        ),
    )


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_process_line(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def is_language_server_command(command: str) -> bool:
    return bool(re.search(r"(?:^|[/\\])language_server(?:_macos)?(?:\s|$)", command.lower()))


def is_antigravity_language_server(command: str) -> bool:
    lower = command.lower()
    return is_language_server_command(lower) and (
        "--app_data_dir" in lower or "/antigravity/" in lower or "\\antigravity\\" in lower
    )


def lsof_binary() -> str | None:
    return next(
        (candidate for candidate in ["/usr/sbin/lsof", "/usr/bin/lsof", shutil.which("lsof")] if candidate),
        None,
    )


def parse_lsof_listening_ports(output: str) -> list[int]:
    ports: set[int] = set()
    for match in re.finditer(r":(\d+)\s+\(LISTEN\)", output):
        ports.add(int(match.group(1)))
    return sorted(ports)


def listening_ports_for_pid(pid: int, *, timeout_seconds: float = 2.0) -> list[int]:
    lsof = lsof_binary()
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_lsof_listening_ports(result.stdout)
