"""Shared Antigravity helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
