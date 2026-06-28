"""Shared Antigravity helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pwd
except ImportError:  # pragma: no cover - Windows compatibility
    pwd = None

from .direct import email_from_id_token
from .models import AccountState, AccountStatus
from .source_utils import _determine_state, _parse_reset_timestamp, _seconds_until_reset


ANTIGRAVITY_CLI_SOURCE_DETAIL = "antigravity-cli"
ANTIGRAVITY_CLI_PATH_ENV = "ANTIGRAVITY_CLI_PATH"
ANTIGRAVITY_CLI_LOGIN_FILE = Path(".gemini") / "google_accounts.json"
ANTIGRAVITY_CLI_OAUTH_FILE = Path(".gemini") / "oauth_creds.json"
ANTIGRAVITY_CLI_APP_DIR = Path(".gemini") / "antigravity-cli"
ANTIGRAVITY_CLI_BINARY_NAMES = ("agy", "antigravity")
ANTIGRAVITY_CLI_MARKER_PATHS = (
    ANTIGRAVITY_CLI_LOGIN_FILE,
    ANTIGRAVITY_CLI_OAUTH_FILE,
    ANTIGRAVITY_CLI_APP_DIR,
    Path(".config") / "antigravity",
    Path(".antigravity"),
)
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
ANTIGRAVITY_QUOTA_SUMMARY_PARSE_ERROR = (
    "Antigravity quota summary was incomplete or unrecognized in provider data."
)


@dataclass(frozen=True)
class AntigravityCliProbe:
    binary: str | None
    marker: str | None
    checked_binaries: tuple[str, ...]
    checked_markers: tuple[str, ...]

    @property
    def detected(self) -> bool:
        return bool(self.binary or self.marker)


def antigravity_cli_binary(home: Path | None = None) -> str | None:
    """Return the installed Antigravity CLI executable, if available."""
    return antigravity_cli_probe(home).binary


def antigravity_cli_detected(home: Path | None = None) -> bool:
    """Return whether local Antigravity CLI state is detectable."""
    return antigravity_cli_probe(home).detected


def antigravity_cli_probe(home: Path | None = None) -> AntigravityCliProbe:
    """Return Antigravity CLI detection details for discovery and diagnostics."""
    checked_binaries: list[str] = []
    checked_markers: list[str] = []
    binary = _antigravity_cli_binary_from_env(checked_binaries)
    if binary:
        return AntigravityCliProbe(binary, None, tuple(checked_binaries), tuple(checked_markers))

    binary = _antigravity_cli_binary_from_path(checked_binaries)
    if binary:
        return AntigravityCliProbe(binary, None, tuple(checked_binaries), tuple(checked_markers))

    explicit_home = home is not None
    binary = _antigravity_cli_binary_from_candidates(home, checked_binaries)
    if binary:
        return AntigravityCliProbe(binary, None, tuple(checked_binaries), tuple(checked_markers))

    if not explicit_home:
        binary = _antigravity_cli_binary_from_shell(checked_binaries)
        if binary:
            return AntigravityCliProbe(binary, None, tuple(checked_binaries), tuple(checked_markers))

    marker = _antigravity_cli_marker_from_candidates(home, checked_markers)
    return AntigravityCliProbe(binary, marker, tuple(checked_binaries), tuple(checked_markers))


def _antigravity_cli_binary_from_env(checked_binaries: list[str]) -> str | None:
    raw = os.environ.get(ANTIGRAVITY_CLI_PATH_ENV)
    if not raw:
        return None
    checked_binaries.append(f"{ANTIGRAVITY_CLI_PATH_ENV}:{raw}")
    candidate = Path(raw).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _antigravity_cli_binary_from_path(checked_binaries: list[str]) -> str | None:
    for name in ANTIGRAVITY_CLI_BINARY_NAMES:
        checked_binaries.append(f"PATH:{name}")
        binary = shutil.which(name)
        if binary:
            return binary
    return None


def _antigravity_cli_binary_from_candidates(
    home: Path | None,
    checked_binaries: list[str],
) -> str | None:
    candidates: list[Path] = []
    for candidate_home in _antigravity_candidate_homes(home):
        candidates.extend(
            [
                candidate_home / ".local" / "bin" / "agy",
                candidate_home / ".local" / "bin" / "antigravity",
                candidate_home / "bin" / "agy",
                candidate_home / "bin" / "antigravity",
            ]
        )
    if home is None:
        candidates.extend(
            [
                Path("/usr/local/bin/agy"),
                Path("/usr/local/bin/antigravity"),
                Path("/opt/homebrew/bin/agy"),
                Path("/opt/homebrew/bin/antigravity"),
            ]
        )
    for candidate in _unique_paths(candidates):
        checked_binaries.append(str(candidate))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _antigravity_cli_binary_from_shell(checked_binaries: list[str]) -> str | None:
    shells = [
        os.environ.get("SHELL"),
        "/bin/zsh",
        "/bin/bash",
        "/bin/sh",
    ]
    for shell_raw in shells:
        if not shell_raw:
            continue
        shell = Path(shell_raw)
        if not shell.is_file() or not os.access(shell, os.X_OK):
            continue
        checked_binaries.append(f"{shell}:command -v")
        try:
            result = subprocess.run(
                [str(shell), "-lc", "command -v agy || command -v antigravity"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        binary = (result.stdout or "").splitlines()[0].strip()
        if binary and Path(binary).is_file() and os.access(binary, os.X_OK):
            return binary
    return None


def _antigravity_cli_marker_from_candidates(
    home: Path | None,
    checked_markers: list[str],
) -> str | None:
    for candidate_home in _antigravity_candidate_homes(home):
        for marker_path in ANTIGRAVITY_CLI_MARKER_PATHS:
            marker = candidate_home / marker_path
            checked_markers.append(str(marker))
            if marker.exists():
                return str(marker)
    return None


def _antigravity_candidate_homes(home: Path | None = None) -> list[Path]:
    homes: list[Path] = []
    if home is not None:
        homes.append(home)
    homes.append(Path.home())
    env_home = os.environ.get("HOME")
    if env_home:
        homes.append(Path(env_home))
    expanded_home = os.path.expanduser("~")
    if expanded_home and expanded_home != "~":
        homes.append(Path(expanded_home))
    pw_home = None
    if pwd is not None:
        try:
            pw_home = pwd.getpwuid(os.getuid()).pw_dir
        except (KeyError, OSError):
            pw_home = None
    if pw_home:
        homes.append(Path(pw_home))
    return _unique_paths(homes)


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = os.path.normpath(os.path.expanduser(str(path)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(Path(key))
    return unique


def antigravity_cli_app_dir(home: Path | None = None) -> Path:
    """Return Antigravity CLI's local app-data directory."""
    return (home or Path.home()) / ANTIGRAVITY_CLI_APP_DIR


def read_antigravity_cli_identity(home: Path | None = None) -> str | None:
    """Read the active Antigravity CLI Google account email without token access."""
    home = home or Path.home()
    path = home / ANTIGRAVITY_CLI_LOGIN_FILE
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        data = None
    if isinstance(data, dict):
        active = data.get("active")
        if isinstance(active, str):
            email = active.strip()
            if "@" in email:
                return email

    oauth_path = home / ANTIGRAVITY_CLI_OAUTH_FILE
    try:
        oauth_data = json.loads(oauth_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(oauth_data, dict):
        return None
    email = email_from_id_token(oauth_data.get("id_token"))
    if not email or "@" not in email:
        return None
    return email


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


def antigravity_status_from_quota_summary(
    label: str,
    data: Any,
    *,
    window_source: str,
    source_detail: str | None = None,
) -> AccountStatus | None:
    """Build an Antigravity status from RetrieveUserQuotaSummary payloads.

    Returns None when the payload is not a quota-summary shape. Returns UNKNOWN
    when summary groups are present but cannot be mapped to all four known
    Antigravity quota buckets.
    """
    summary = _antigravity_quota_summary_payload(data)
    if summary is None:
        return None
    groups = summary.get("groups")
    if not isinstance(groups, list):
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=ANTIGRAVITY_QUOTA_SUMMARY_PARSE_ERROR,
            source_detail=source_detail,
        )
    quota_windows = _antigravity_quota_windows_from_summary(
        groups,
        window_source=window_source,
    )
    if quota_windows is None:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=ANTIGRAVITY_QUOTA_SUMMARY_PARSE_ERROR,
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


def _antigravity_quota_summary_payload(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    candidates = [data]
    response = data.get("response")
    if isinstance(response, dict):
        candidates.append(response)
    quota_summary = data.get("quotaSummary", data.get("quota_summary"))
    if isinstance(quota_summary, dict):
        candidates.append(quota_summary)
    if isinstance(response, dict):
        nested_summary = response.get("quotaSummary", response.get("quota_summary"))
        if isinstance(nested_summary, dict):
            candidates.append(nested_summary)
    for candidate in candidates:
        if "groups" in candidate:
            return candidate
    return None


def _antigravity_quota_windows_from_summary(
    groups: list,
    *,
    window_source: str,
) -> list[dict] | None:
    windows_by_id: dict[str, dict] = {}
    saw_bucket = False
    for group in groups:
        if not isinstance(group, dict):
            return None
        group_name = _string_value(
            group.get("displayName", group.get("display_name", group.get("name")))
        )
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            return None
        for bucket in buckets:
            saw_bucket = True
            if not isinstance(bucket, dict):
                return None
            window = _antigravity_summary_bucket_window(
                group_name or "",
                bucket,
                window_source=window_source,
            )
            if window is None:
                return None
            window_id = window["id"]
            if window_id in windows_by_id:
                return None
            windows_by_id[window_id] = window

    if not saw_bucket or set(windows_by_id) != set(ANTIGRAVITY_QUOTA_WINDOW_IDS):
        return None
    return [windows_by_id[window_id] for window_id in ANTIGRAVITY_QUOTA_WINDOW_IDS]


def _antigravity_summary_bucket_window(
    group_name: str,
    bucket: dict,
    *,
    window_source: str,
) -> dict | None:
    bucket_id_raw = _string_value(bucket.get("bucketId", bucket.get("bucket_id"))) or ""
    bucket_name = _string_value(
        bucket.get("displayName", bucket.get("display_name", bucket.get("name")))
    ) or ""
    description = _string_value(bucket.get("description")) or ""
    searchable = " ".join([group_name, bucket_id_raw, bucket_name, description]).lower()
    family = _antigravity_summary_family(searchable)
    window_kind = _antigravity_summary_window_kind(searchable)
    if family is None or window_kind is None:
        return None
    window_id = _antigravity_summary_window_id(family, window_kind)
    if window_id is None:
        return None
    spec = ANTIGRAVITY_QUOTA_WINDOW_SPECS[window_id]
    remaining = bucket.get("remaining")
    remaining_fraction = None
    if isinstance(remaining, dict):
        remaining_fraction = _to_float_or_none(
            remaining.get("remainingFraction", remaining.get("remaining_fraction"))
        )
    if remaining_fraction is None:
        remaining_fraction = _to_float_or_none(
            bucket.get("remainingFraction", bucket.get("remaining_fraction"))
        )
    if remaining_fraction is None:
        return None
    if remaining_fraction < 0.0 or remaining_fraction > 1.0:
        return None
    used_percent = round(100.0 - remaining_fraction * 100.0, 6)
    reset = _antigravity_summary_reset(bucket)
    if reset is None:
        return None
    resets_at, resets_in_seconds = reset
    title = _antigravity_summary_title(family, window_kind)
    return {
        "id": window_id,
        "title": title,
        "family": spec["family"],
        "window_kind": spec["window_kind"],
        "used_percent": used_percent,
        "resets_at": resets_at,
        "resets_in_seconds": resets_in_seconds,
        "window_minutes": spec["window_minutes"],
        "source": window_source,
    }


def _antigravity_summary_family(searchable: str) -> str | None:
    if "gemini" in searchable:
        return "gemini"
    if "claude" in searchable or "gpt" in searchable or "3p" in searchable:
        return "claude_gpt"
    return None


def _antigravity_summary_window_kind(searchable: str) -> str | None:
    if "week" in searchable:
        return "weekly"
    if (
        "5h" in searchable
        or "5 h" in searchable
        or "5-hour" in searchable
        or "five hour" in searchable
        or "five-hour" in searchable
        or "session" in searchable
    ):
        return "session"
    return None


def _antigravity_summary_window_id(family: str, window_kind: str) -> str | None:
    for window_id, spec in ANTIGRAVITY_QUOTA_WINDOW_SPECS.items():
        if spec["family"] == family and spec["window_kind"] == window_kind:
            return window_id
    return None


def _antigravity_summary_title(family: str, window_kind: str) -> str:
    if family == "gemini":
        prefix = "Gemini Models"
    else:
        prefix = "Claude and GPT models"
    suffix = "Weekly Limit" if window_kind == "weekly" else "Five Hour Limit"
    return f"{prefix} {suffix}"


def _antigravity_summary_reset(bucket: dict) -> tuple[float, int] | None:
    timestamp_candidates: list[Any] = [
        bucket.get("resetsAt", bucket.get("resets_at")),
        bucket.get("resetAt", bucket.get("reset_at")),
        bucket.get("resetTime", bucket.get("reset_time")),
        bucket.get("nextResetTime", bucket.get("next_reset_time")),
    ]
    remaining = bucket.get("remaining")
    if isinstance(remaining, dict):
        timestamp_candidates.extend(
            [
                remaining.get("resetsAt", remaining.get("resets_at")),
                remaining.get("resetAt", remaining.get("reset_at")),
                remaining.get("resetTime", remaining.get("reset_time")),
                remaining.get("nextResetTime", remaining.get("next_reset_time")),
            ]
        )
    for raw in timestamp_candidates:
        resets_at = _parse_antigravity_reset_timestamp(raw)
        if resets_at is not None:
            return resets_at, max(0, int(resets_at - time.time()))

    text_candidates = [
        bucket.get("description"),
        bucket.get("resetDescription", bucket.get("reset_description")),
        bucket.get("subtitle"),
    ]
    if isinstance(remaining, dict):
        text_candidates.extend(
            [
                remaining.get("description"),
                remaining.get("resetDescription", remaining.get("reset_description")),
                remaining.get("subtitle"),
            ]
        )
    for raw in text_candidates:
        if not isinstance(raw, str):
            continue
        resets_in = _parse_antigravity_reset_prose_seconds(raw)
        if resets_in is not None:
            return time.time() + resets_in, resets_in
    return None


def _parse_antigravity_reset_timestamp(value: Any) -> float | None:
    parsed = _parse_reset_timestamp(value)
    if parsed is not None:
        return parsed
    if not isinstance(value, dict):
        return None

    seconds = _to_float_or_none(value.get("seconds", value.get("epochSeconds")))
    nanos = _to_float_or_none(value.get("nanos", value.get("nanoseconds"))) or 0.0
    if seconds is not None:
        return seconds + (nanos / 1_000_000_000)

    millis = _to_float_or_none(value.get("millis", value.get("epochMillis")))
    if millis is not None:
        return millis / 1000.0
    return None


def _parse_antigravity_reset_prose_seconds(text: str) -> int | None:
    lowered = text.lower()
    if "reset" not in lowered:
        return None
    if "ready" in lowered or "now" in lowered:
        return 0
    match = re.search(r"resets?\s+in\s+(.+)", lowered)
    if match is None:
        return None
    duration = match.group(1)
    total = 0
    matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)",
        duration,
    )
    for number, unit in matches:
        value = float(number)
        if unit.startswith("d"):
            total += int(value * 86400)
        elif unit.startswith("h"):
            total += int(value * 3600)
        else:
            total += int(value * 60)
    return total if matches else None


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


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


def parse_proc_net_tcp_listening_ports(output: str, socket_inodes: set[str]) -> list[int]:
    ports: set[int] = set()
    for line in output.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 10:
            continue
        local_address = columns[1]
        state = columns[3]
        inode = columns[9]
        if state != "0A" or inode not in socket_inodes:
            continue
        _address, separator, port_hex = local_address.rpartition(":")
        if not separator:
            continue
        try:
            ports.add(int(port_hex, 16))
        except ValueError:
            continue
    return sorted(ports)


def listening_ports_for_pid(pid: int, *, timeout_seconds: float = 2.0) -> list[int]:
    ports, _method = listening_ports_for_pid_with_method(pid, timeout_seconds=timeout_seconds)
    return ports


def listening_ports_for_pid_with_method(
    pid: int,
    *,
    timeout_seconds: float = 2.0,
) -> tuple[list[int], str | None]:
    lsof = lsof_binary()
    if lsof:
        try:
            result = subprocess.run(
                [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None:
            ports = parse_lsof_listening_ports(result.stdout)
            if ports:
                return ports, "lsof"
    ports = _proc_listening_ports_for_pid(pid)
    if ports:
        return ports, "proc"
    return [], None


def _proc_listening_ports_for_pid(pid: int, proc_root: Path = Path("/proc")) -> list[int]:
    fd_dir = proc_root / str(pid) / "fd"
    try:
        fd_paths = list(fd_dir.iterdir())
    except OSError:
        return []
    socket_inodes: set[str] = set()
    for fd_path in fd_paths:
        try:
            target = os.readlink(fd_path)
        except OSError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            socket_inodes.add(match.group(1))
    if not socket_inodes:
        return []
    ports: set[int] = set()
    for name in ("tcp", "tcp6"):
        try:
            text = (proc_root / "net" / name).read_text()
        except OSError:
            continue
        ports.update(parse_proc_net_tcp_listening_ports(text, socket_inodes))
    return sorted(ports)
