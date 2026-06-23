"""Read-only diagnostics for TokenKick configuration, cache, and providers.

DoctorReport JSON field names are stable for downstream tooling:
summary, config, cache, daemon, schedule, notifications, accounts, checks.
Nested account fields are the DoctorAccountReport dataclass fields; checks use
level, code, message, and optional fix.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.table import Table

from .antigravity import (
    is_antigravity_language_server,
    listening_ports_for_pid,
    parse_process_line,
)
from . import scheduling
from .direct import normalize_source_detail, read_claude_identity, read_codex_identity
from .claude_setup import claude_probe_git_present, claude_settings_present
from .codex_surface_stats import learned_codex_surface_order_summary
from .kicker import CODEX_NO_GENERATION_EVIDENCE_ERROR, kick_model_for_account
from .models import (
    CODEX_FIRE_ALL_SURFACE_NAMES,
    CONFIG_DIR,
    CONFIG_FILE,
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    Config,
    DataSource,
    KickEvent,
    account_key_string,
    load_kick_history,
    normalize_notification_backends,
)
from .recovery_hints import codex_refresh_recovery_hint
from .codexbar_source import (
    CODEXBAR_HISTORY_DIR,
    CODEXBAR_WIDGET_SNAPSHOT_FILES,
)
from .reset_defense import filter_reset_events, is_provider_reset_observation, load_reset_events
from .versioning import installed_version, read_daemon_pidfile

STATUS_CACHE_FILE = CONFIG_DIR / "status-cache.json"
CODEX_SURFACE_STATS_FILE = CONFIG_DIR / "codex-surface-stats.json"
STATUS_CACHE_REFRESH_LOCK_FILE = CONFIG_DIR / "status-cache-refresh.pid"
STATUS_CACHE_REFRESH_LOCK_MAX_AGE_SECONDS = 120
DAEMON_PID_FILE = CONFIG_DIR / "daemon.pid"
GEMINI_OAUTH_CREDS_FILE = Path.home() / ".gemini" / "oauth_creds.json"
CLAUDE_CONFIG_FILE = Path.home() / ".claude.json"
CLAUDE_HOME = Path.home()
CODEX_IDENTITY_MISMATCH_FIX = (
    "run `tk setup`; if still mismatched, open the matching Codex account once and rerun `tk setup`"
)
CODEX_ATTRIBUTION_TIMING_MATCH = "timing_match"
CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE = "external_possible"


@dataclass
class DoctorCheck:
    level: Literal["OK", "WARN", "FAIL", "INFO"]
    code: str
    message: str
    fix: str | None = None

    def __post_init__(self) -> None:
        if self.level == "FAIL" and not self.fix:
            raise ValueError(f"FAIL check {self.code!r} requires a fix hint")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DoctorSummary:
    ok: int
    warn: int
    fail: int
    accounts: int
    cache_status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DoctorAccountReport:
    label: str
    provider: str
    source: str
    source_detail: str | None
    state: str
    stale: bool
    refresh_error: str | None
    visible: bool
    auto_kick: bool
    weekly_auto_kick: bool
    session_auto_kick: bool
    kick_model: str | None
    checks: list[DoctorCheck]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["checks"] = [check.to_dict() for check in self.checks]
        return data


@dataclass
class DoctorReport:
    """Stable JSON shape: summary, config, cache, daemon, schedule, notifications, accounts, checks."""

    summary: DoctorSummary
    config: dict
    cache: dict
    daemon: dict
    schedule: dict
    notifications: dict
    accounts: list[DoctorAccountReport]
    checks: list[DoctorCheck]

    def to_dict(self) -> dict:
        return {
            "summary": self.summary.to_dict(),
            "config": self.config,
            "cache": self.cache,
            "daemon": self.daemon,
            "schedule": self.schedule,
            "notifications": self.notifications,
            "accounts": [account.to_dict() for account in self.accounts],
            "checks": [check.to_dict() for check in self.checks],
        }


def build_doctor_report(label: str | None = None, *, repair: bool = False) -> DoctorReport:
    config = Config.load()
    selected_accounts = _select_accounts(config.accounts, label)
    reaped_refresh_lock = _reap_dead_refresh_lock()
    cache_entries = _load_status_cache_entries()
    global_checks: list[DoctorCheck] = []

    config_info = _doctor_config(config)
    cache_info, cache_checks = _doctor_cache(cache_entries, config)
    daemon_info, daemon_checks = _doctor_daemon()
    schedule_info, schedule_checks = _doctor_schedule(config, cache_entries)
    notifications_info, notification_checks = _doctor_notifications(config)
    codexbar_checks = _doctor_codexbar()
    reset_defense_checks = _doctor_reset_defense(config)
    global_checks.append(_doctor_codex_surface_dispatch_mode(config))

    if reaped_refresh_lock is not None:
        global_checks.append(
            DoctorCheck(
                "OK",
                "refresh_lock_reaped",
                f"Removed stale refresh lock for dead PID {reaped_refresh_lock.get('pid')}.",
            )
        )
    elif repair:
        global_checks.append(
            DoctorCheck(
                "OK",
                "refresh_lock_repair_checked",
                "No dead refresh lock needed cleanup.",
            )
        )
    global_checks.extend(cache_checks)
    global_checks.extend(daemon_checks)
    global_checks.extend(schedule_checks)
    global_checks.extend(notification_checks)
    global_checks.extend(codexbar_checks)
    global_checks.extend(reset_defense_checks)
    if not config.claude.direct_usage_enabled:
        global_checks.append(
            DoctorCheck(
                "INFO",
                "claude_direct_usage_global_disabled",
                "claude: direct /usage disabled globally",
            )
        )
    if not config.accounts:
        global_checks.append(
            DoctorCheck(
                "INFO",
                "no_accounts_configured",
                "No accounts configured. Run `tk setup` to discover local accounts.",
            )
        )

    accounts = [
        _doctor_account_report(account, cache_entries.get(account_key_string(account)))
        for account in selected_accounts
    ]
    summary = _doctor_summary(global_checks, accounts, cache_info["status"])
    return DoctorReport(
        summary=summary,
        config=config_info,
        cache=cache_info,
        daemon=daemon_info,
        schedule=schedule_info,
        notifications=notifications_info,
        accounts=accounts,
        checks=global_checks,
    )


def _select_accounts(accounts: list[AccountConfig], label: str | None) -> list[AccountConfig]:
    if label is None:
        return list(accounts)
    matches = [account for account in accounts if account.label == label]
    if not matches:
        raise ValueError(f'Account "{label}" not found.')
    return matches


def _doctor_config(config: Config) -> dict:
    order, order_error = _doctor_safe_codex_fire_all_surface_order(config)
    return {
        "path": str(CONFIG_FILE),
        "accounts": len(config.accounts),
        "poll_interval_minutes": config.poll_interval_minutes,
        "notifications_enabled": config.notifications.enabled,
        "schedule_enabled": config.schedule.enabled,
        "claude_direct_usage_enabled": config.claude.direct_usage_enabled,
        "codex_fire_all_surfaces": config.codex_fire_all_surfaces,
        "codex_fire_all_surfaces_active": _doctor_codex_fire_all_surfaces_enabled(config),
        "codex_fire_all_surface_gap_seconds": config.codex_fire_all_surface_gap_seconds,
        "codex_fire_all_surface_order": config.codex_fire_all_surface_order,
        "codex_fire_all_surface_order_active": list(order),
        "codex_fire_all_surface_order_error": order_error,
    }


def _doctor_codex_surface_dispatch_mode(config: Config) -> DoctorCheck:
    order_values, order_error = _doctor_safe_codex_fire_all_surface_order(config)
    if order_error is not None:
        return DoctorCheck(
            "FAIL",
            "codex_surface_dispatch_mode",
            f"Codex fire-all surface order is invalid: {order_error}",
            fix="Fix codex_fire_all_surface_order or TK_CODEX_FIRE_ALL_SURFACE_ORDER.",
        )
    order = ", ".join(order_values)
    if _doctor_codex_fire_all_surfaces_enabled(config):
        return DoctorCheck(
            "INFO",
            "codex_surface_dispatch_mode",
            "Codex surface dispatch: fire-all enabled "
            f"(order: {order}); scorer is not updated for fire-all bursts.",
        )
    return DoctorCheck(
        "INFO",
        "codex_surface_dispatch_mode",
        f"Codex surface dispatch: sequential adaptive ladder; fire-all order ignored ({order}).",
    )


def _doctor_safe_codex_fire_all_surface_order(config: Config) -> tuple[tuple[str, ...], str | None]:
    try:
        return _doctor_codex_fire_all_surface_order(config), None
    except ValueError as exc:
        return (), str(exc)


def _doctor_codex_fire_all_surfaces_enabled(config: Config) -> bool:
    raw = os.environ.get("TK_CODEX_FIRE_ALL_SURFACES")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return config.codex_fire_all_surfaces


def _doctor_codex_fire_all_surface_order(config: Config) -> tuple[str, ...]:
    raw = os.environ.get("TK_CODEX_FIRE_ALL_SURFACE_ORDER")
    if raw is not None:
        order = _doctor_parse_codex_fire_all_surface_order(raw.split(","))
        return tuple(order) if order else CODEX_FIRE_ALL_SURFACE_NAMES
    if config.codex_fire_all_surface_order:
        return tuple(config.codex_fire_all_surface_order)
    return CODEX_FIRE_ALL_SURFACE_NAMES


def _doctor_parse_codex_fire_all_surface_order(values: list[str]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    valid = set(CODEX_FIRE_ALL_SURFACE_NAMES)
    for value in values:
        surface = value.strip()
        if not surface:
            continue
        if surface not in valid:
            raise ValueError(
                f'Unknown Codex fire-all surface "{surface}". Valid surfaces: '
                f"{', '.join(CODEX_FIRE_ALL_SURFACE_NAMES)}."
            )
        if surface in seen:
            raise ValueError(f'Duplicate Codex fire-all surface "{surface}".')
        seen.add(surface)
        order.append(surface)
    return order


def _doctor_summary(
    checks: list[DoctorCheck],
    accounts: list[DoctorAccountReport],
    cache_status: str,
) -> DoctorSummary:
    all_checks = list(checks)
    for account in accounts:
        all_checks.extend(account.checks)
    return DoctorSummary(
        ok=sum(1 for check in all_checks if check.level == "OK"),
        warn=sum(1 for check in all_checks if check.level == "WARN"),
        fail=sum(1 for check in all_checks if check.level == "FAIL"),
        accounts=len(accounts),
        cache_status=cache_status,
    )


def _load_status_cache_entries() -> dict[str, dict]:
    data = _read_status_cache_data()
    raw_accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw_accounts, dict):
        return {}

    entries: dict[str, dict] = {}
    for key, entry in raw_accounts.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        account_data = entry.get("account")
        status_data = entry.get("status")
        cached_at = entry.get("cached_at")
        provider_observed_at = entry.get("provider_observed_at")
        last_direct_probe_at = entry.get("last_direct_probe_at")
        last_direct_success_at = entry.get("last_direct_success_at")
        last_direct_probe_error = ClaudeProbeError.from_dict(entry.get("last_direct_probe_error"))
        last_direct_success_status = None
        if not isinstance(account_data, dict) or not isinstance(status_data, dict):
            continue
        if not isinstance(cached_at, str):
            continue
        try:
            account = AccountConfig.from_dict(account_data)
            status = _account_status_from_dict(status_data)
            success_status_data = entry.get("last_direct_success_status")
            if isinstance(success_status_data, dict):
                last_direct_success_status = _account_status_from_dict(success_status_data)
        except (TypeError, ValueError):
            continue
        refresh_error = entry.get("refresh_error")
        entries[key] = {
            "account": account,
            "status": status,
            "cached_at": cached_at,
            "provider_observed_at": (
                provider_observed_at
                if isinstance(provider_observed_at, str)
                else status.observed_at or cached_at
            ),
            "refresh_error": refresh_error if isinstance(refresh_error, str) else None,
            "last_direct_probe_at": last_direct_probe_at if isinstance(last_direct_probe_at, str) else None,
            "last_direct_probe_error": last_direct_probe_error,
            "last_direct_success_at": last_direct_success_at if isinstance(last_direct_success_at, str) else None,
            "last_direct_success_status": last_direct_success_status,
        }
    return entries


def _read_status_cache_data() -> dict | None:
    try:
        data = json.loads(STATUS_CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("version") == 2 else None


def _account_status_from_dict(data: dict) -> AccountStatus:
    allowed = {field.name for field in fields(AccountStatus)}
    status_data = {key: value for key, value in data.items() if key in allowed}
    state = status_data.get("state")
    if isinstance(state, str):
        status_data["state"] = AccountState(state)
    status = AccountStatus(**status_data)
    status.source_detail = normalize_source_detail(status.source_detail)
    return status


def _doctor_cache(entries: dict[str, dict], config: Config) -> tuple[dict, list[DoctorCheck]]:
    exists = STATUS_CACHE_FILE.exists()
    checks: list[DoctorCheck] = []
    state_counts = {"fresh": 0, "active": 0, "waiting": 0, "unknown": 0}
    current_count = 0
    stale_count = 0
    oldest_age_seconds: int | None = None
    for entry in entries.values():
        status = entry["status"]
        state_counts[status.state.value] = state_counts.get(status.state.value, 0) + 1
        age = _cache_entry_age_seconds(entry)
        if age is not None:
            oldest_age_seconds = age if oldest_age_seconds is None else max(oldest_age_seconds, age)
        if _cache_entry_is_stale(entry, config):
            stale_count += 1
        else:
            current_count += 1

    if not exists:
        cache_status = "missing"
        checks.append(DoctorCheck("WARN", "cache_missing", "Status cache is missing.", fix="run `tk status --refresh`"))
    elif stale_count and current_count:
        cache_status = "mixed"
        checks.append(DoctorCheck("WARN", "cache_mixed", "Status cache has mixed current and stale entries."))
    elif stale_count:
        cache_status = "stale"
        checks.append(DoctorCheck("WARN", "cache_stale", "Status cache is stale.", fix="run `tk status --refresh`"))
    else:
        cache_status = "current"
        checks.append(DoctorCheck("OK", "cache_current", "Status cache is current."))

    lock = _refresh_lock_info()
    if lock["status"] == "running":
        checks.append(
            DoctorCheck(
                "INFO",
                "refresh_lock_running",
                f"Background refresh running with PID {lock.get('pid')} for {_format_age(lock.get('age_seconds'))}.",
            )
        )
    elif lock["status"] == "stale":
        checks.append(
            DoctorCheck(
                "WARN",
                "refresh_lock_stale",
                "Refresh lock appears stale.",
                fix=f"remove {STATUS_CACHE_REFRESH_LOCK_FILE} or run `tk daemon --restart`",
            )
        )

    return (
        {
            "path": str(STATUS_CACHE_FILE),
            "exists": exists,
            "status": cache_status,
            "state_counts": state_counts,
            "fresh": state_counts["fresh"],
            "current": current_count,
            "stale": stale_count,
            "unknown": state_counts["unknown"],
            "oldest_entry_age_seconds": oldest_age_seconds,
            "oldest_entry_age": _format_age(oldest_age_seconds),
            "refresh_lock": lock,
        },
        checks,
    )


def _cache_entry_age_seconds(entry: dict) -> int | None:
    observed = _parse_iso(entry.get("provider_observed_at") or entry.get("cached_at"))
    if observed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))


def _cache_entry_is_stale(entry: dict, config: Config) -> bool:
    if entry.get("refresh_error"):
        return True
    age = _cache_entry_age_seconds(entry)
    if age is None:
        return True
    return age > config.poll_interval_minutes * 60 * 2


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _reap_dead_refresh_lock() -> dict[str, int | None] | None:
    if not STATUS_CACHE_REFRESH_LOCK_FILE.exists():
        return None
    try:
        stat = STATUS_CACHE_REFRESH_LOCK_FILE.stat()
        age = max(0, int(time.time() - stat.st_mtime))
        raw_pid = STATUS_CACHE_REFRESH_LOCK_FILE.read_text().strip()
        pid = int(raw_pid)
    except (OSError, ValueError):
        return None
    if _pid_is_running(pid):
        return None
    try:
        STATUS_CACHE_REFRESH_LOCK_FILE.unlink()
    except OSError:
        return None
    return {"pid": pid, "age_seconds": age}


def _refresh_lock_info() -> dict:
    if not STATUS_CACHE_REFRESH_LOCK_FILE.exists():
        return {"status": "not_running", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE)}
    try:
        stat = STATUS_CACHE_REFRESH_LOCK_FILE.stat()
        age = max(0, int(time.time() - stat.st_mtime))
        raw_pid = STATUS_CACHE_REFRESH_LOCK_FILE.read_text().strip()
    except OSError:
        return {"status": "stale", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE), "pid": None, "age_seconds": None}
    try:
        pid = int(raw_pid)
    except ValueError:
        pid = None
    if pid is not None:
        if _pid_is_running(pid):
            return {"status": "running", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE), "pid": pid, "age_seconds": age}
        return {"status": "stale", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE), "pid": pid, "age_seconds": age}
    if age <= STATUS_CACHE_REFRESH_LOCK_MAX_AGE_SECONDS:
        return {"status": "running", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE), "pid": pid, "age_seconds": age}
    return {"status": "stale", "path": str(STATUS_CACHE_REFRESH_LOCK_FILE), "pid": pid, "age_seconds": age}


def _doctor_daemon() -> tuple[dict, list[DoctorCheck]]:
    installed = installed_version()
    info = {
        "path": str(DAEMON_PID_FILE),
        "status": "not_running",
        "pid": None,
        "installed_version": installed,
        "daemon_version": None,
    }
    if not DAEMON_PID_FILE.exists():
        return info, [DoctorCheck("INFO", "daemon_not_running", "daemon: not running")]
    pidfile = read_daemon_pidfile(DAEMON_PID_FILE)
    if pidfile is None:
        info["status"] = "stale"
        return info, [
            DoctorCheck(
                "FAIL",
                "daemon_pidfile_invalid",
                "daemon: pidfile present but invalid",
                fix="run `tk daemon --restart`",
            )
        ]
    pid = pidfile.pid
    info["pid"] = pid
    info["daemon_version"] = pidfile.version
    if _pid_is_running(pid):
        info["status"] = "running"
        if pidfile.version != installed:
            return info, [
                DoctorCheck(
                    "WARN",
                    "daemon_version_mismatch",
                    f"daemon: running v{pidfile.version} but installed v{installed}",
                    fix="run `tk update` to restart with the new version",
                )
            ]
        return info, [
            DoctorCheck(
                "OK",
                "daemon_running",
                f"daemon: running PID {pid} v{pidfile.version}",
            )
        ]
    info["status"] = "stale"
    return info, [
        DoctorCheck(
            "FAIL",
            "daemon_pidfile_stale",
            f"daemon: pidfile present (PID {pid}) but process not running",
            fix="run `tk daemon --restart`",
        )
    ]


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _doctor_schedule(config: Config, cache_entries: dict[str, dict]) -> tuple[dict, list[DoctorCheck]]:
    pending = _read_pending_kicks_readonly()
    checks: list[DoctorCheck] = []
    status_by_label = {
        entry["status"].label: entry["status"]
        for entry in cache_entries.values()
        if isinstance(entry.get("status"), AccountStatus)
    }
    pending_rows = []
    for pending_kick in pending.values():
        row = {
            "account": pending_kick.account_label,
            "kick_at": pending_kick.kick_at,
            "window_basis": pending_kick.window_basis,
        }
        pending_rows.append(row)
        cached_status = status_by_label.get(pending_kick.account_label)
        if cached_status is not None and cached_status.stale:
            checks.append(
                DoctorCheck(
                    "WARN",
                    "schedule_pending_blocked_stale",
                    f"pending kick for {pending_kick.account_label} blocked by stale provider data",
                    fix="run `tk status --refresh`",
                )
            )
    if config.schedule.enabled:
        checks.append(DoctorCheck("OK", "schedule_enabled", "schedule: enabled"))
    else:
        checks.append(DoctorCheck("INFO", "schedule_disabled", "schedule: disabled"))
    return (
        {
            "enabled": config.schedule.enabled,
            "account_schedules": len(config.schedule.accounts),
            "pending_kicks": len(pending_rows),
            "pending": pending_rows,
            "scheduling_target": config.schedule.scheduling_target,
        },
        checks,
    )


def _read_pending_kicks_readonly() -> dict[str, scheduling.PendingKick]:
    try:
        data = json.loads(scheduling.PENDING_KICKS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    pending: dict[str, scheduling.PendingKick] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        pending_kick = scheduling.PendingKick.from_dict(value)
        if pending_kick is not None:
            pending[str(key)] = pending_kick
    return pending


def _doctor_notifications(config: Config) -> tuple[dict, list[DoctorCheck]]:
    notifications = config.notifications
    enabled_backends = normalize_notification_backends(notifications.enabled_backends)
    if enabled_backends is None:
        enabled_backends = [notifications.backend] if notifications.backend in {"ntfy", "telegram"} else []
    info = {
        "enabled": notifications.enabled,
        "backend": notifications.backend,
        "enabled_backends": enabled_backends,
        "ntfy_topic": notifications.ntfy_topic,
        "telegram_chat_id": _mask_last4(notifications.telegram_chat_id),
        "telegram_bot_token": "configured" if notifications.telegram_bot_token else None,
    }
    if not notifications.enabled:
        return info, [DoctorCheck("INFO", "notifications_disabled", "notifications: disabled")]
    checks: list[DoctorCheck] = []
    if "ntfy" in enabled_backends and notifications.ntfy_topic:
        checks.append(
            DoctorCheck(
                "OK",
                "notifications_ntfy_configured",
                f"notifications: ntfy configured (topic: {notifications.ntfy_topic})",
            )
        )
    if "telegram" in enabled_backends and notifications.telegram_chat_id:
        checks.append(
            DoctorCheck(
                "OK",
                "notifications_telegram_configured",
                f"notifications: telegram configured (chat_id: {_mask_last4(notifications.telegram_chat_id)})",
            )
        )
    if checks:
        return info, checks
    return info, [
        DoctorCheck(
            "WARN",
            "notifications_incomplete",
            "notifications: enabled but incomplete",
            fix="run `tk notify --ntfy TOPIC` or configure Telegram credentials",
        )
    ]


def _doctor_reset_defense(config: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = filter_reset_events(load_reset_events(), since=cutoff)
    for event in recent[-5:]:
        level: Literal["OK", "WARN", "FAIL", "INFO"] = (
            "INFO"
            if is_provider_reset_observation(event)
            else ("WARN" if event.confidence in {"likely", "confirmed"} else "INFO")
        )
        message = (
            f"provider reset observation on {event.provider}: "
            f"{', '.join(event.affected_accounts)}"
            if is_provider_reset_observation(event)
            else (
                f"global reset {event.confidence} on {event.provider}: "
                f"{', '.join(event.affected_accounts)}"
            )
        )
        checks.append(
            DoctorCheck(
                level,
                "global_reset_recent",
                message,
                fix=f"run `tk reset-log --detail {event.id}`",
            )
        )
    visible_by_provider: dict[str, int] = {}
    for account in config.accounts:
        if account.visible:
            visible_by_provider[account.provider] = visible_by_provider.get(account.provider, 0) + 1
    for provider, count in sorted(visible_by_provider.items()):
        if count < 2:
            checks.append(
                DoctorCheck(
                    "INFO",
                    "global_reset_correlation_limited",
                    f"{provider}: global reset correlation needs 2+ visible accounts; found {count}",
                )
            )
    return checks


def _mask_last4(value: str | None) -> str | None:
    if not value:
        return None
    return f"***{value[-4:]}"


def _doctor_account_report(account: AccountConfig, cache_entry: dict | None) -> DoctorAccountReport:
    status = cache_entry.get("status") if cache_entry else None
    status = status if isinstance(status, AccountStatus) else None
    checks = _doctor_provider_account(account, cache_entry)
    if cache_entry is None:
        checks.append(
            DoctorCheck(
                "WARN",
                "account_cache_missing",
                f"{account.label}: no cached status available",
                fix="run `tk status --refresh`",
            )
        )
    elif cache_entry.get("refresh_error"):
        hint = codex_refresh_recovery_hint(account, cache_entry)
        message = f"{account.label}: last refresh failed ({cache_entry['refresh_error']})"
        if hint is not None:
            message = f"{message}; last provider read {hint.age_text} ago"
        checks.append(
            DoctorCheck(
                "WARN",
                "account_refresh_error",
                message,
                fix=hint.doctor_fix if hint is not None else None,
            )
        )
    return DoctorAccountReport(
        label=account.label,
        provider=account.provider,
        source=account.source.value,
        source_detail=status.source_detail if status else None,
        state=status.state.value if status else AccountState.UNKNOWN.value,
        stale=bool(status.stale) if status else False,
        refresh_error=cache_entry.get("refresh_error") if cache_entry else None,
        visible=account.visible,
        auto_kick=account.auto_kick,
        weekly_auto_kick=account.weekly_auto_kick,
        session_auto_kick=account.session_auto_kick,
        kick_model=account.kick_model or kick_model_for_account(account),
        checks=checks,
    )


def _doctor_provider_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    provider = account.provider
    if provider == "codex":
        return _doctor_codex_account(account, cache_entry)
    if provider == "claude":
        return _doctor_claude_account(account, cache_entry)
    if provider == "gemini":
        return _doctor_gemini_account(account, cache_entry)
    if provider == "antigravity":
        return _doctor_antigravity_account(account, cache_entry)
    if provider == "openrouter":
        return _doctor_openrouter_account(account, cache_entry)
    return [DoctorCheck("INFO", "provider_generic", f"{account.label}: no provider-specific diagnostics")]


def _doctor_codex_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    codex_home = _codex_home_for_account(account)
    if codex_home is None:
        checks.append(DoctorCheck("INFO", "codex_no_home_scope", f"{account.label}: no Codex home scope configured"))
        return checks
    if codex_home.exists():
        checks.append(DoctorCheck("OK", "codex_home_exists", f"{account.label}: Codex home exists"))
    else:
        checks.append(
            DoctorCheck(
                "FAIL",
                "codex_home_missing",
                f"{account.label}: configured Codex home does not exist",
                fix="run `tk setup`",
            )
        )
    sessions_dir = Path(account.session_path) if account.session_path else codex_home / "sessions"
    if sessions_dir.exists():
        checks.append(DoctorCheck("OK", "codex_sessions_exists", f"{account.label}: sessions directory exists"))
    else:
        checks.append(DoctorCheck("WARN", "codex_sessions_missing", f"{account.label}: sessions directory missing"))
    identity = read_codex_identity(codex_home)
    if identity is None:
        checks.append(DoctorCheck("WARN", "codex_identity_missing", f"{account.label}: Codex identity not readable"))
        return checks
    mismatch = False
    if account.identity_provider_id and identity.provider_account_id:
        mismatch = mismatch or account.identity_provider_id != identity.provider_account_id
    if account.identity_email and identity.email:
        mismatch = mismatch or account.identity_email.lower() != identity.email.lower()
    if mismatch:
        checks.append(
            DoctorCheck(
                "FAIL",
                "codex_identity_mismatch",
                f"{account.label}: configured home identity does not match saved account",
                fix=CODEX_IDENTITY_MISMATCH_FIX,
            )
        )
    else:
        checks.append(DoctorCheck("OK", "codex_identity_matches", f"{account.label}: Codex identity matches config"))
    checks.append(_doctor_codex_surface_order(account))
    checks.extend(_doctor_codex_kick_history(account))
    return checks


def _doctor_codex_surface_order(account: AccountConfig) -> DoctorCheck:
    try:
        summary = learned_codex_surface_order_summary(account, CODEX_SURFACE_STATS_FILE)
    except Exception:
        summary = (
            "repo-skip score=0.00 confirmed=0/0, legacy score=0.00 confirmed=0/0, "
            "repo score=0.00 confirmed=0/0, interactive-like score=0.00 confirmed=0/0"
        )
    return DoctorCheck(
        "INFO",
        "codex_surface_order",
        f"{account.label}: Codex surface order: {summary}",
    )


def _doctor_codex_kick_history(account: AccountConfig) -> list[DoctorCheck]:
    try:
        history = load_kick_history(limit=200)
        recent_no_evidence = _codex_no_evidence_recent_events(history, account.label)
        recent_unconfirmed_clusters = _codex_unconfirmed_generation_clusters(history, account.label)
        recent_late_attributed_clusters = _codex_late_attributed_ambiguous_clusters(
            history,
            account.label,
        )
    except Exception:
        return []
    if len(recent_no_evidence) < 2:
        if recent_unconfirmed_clusters:
            cluster = recent_unconfirmed_clusters[-1]
            return [
                DoctorCheck(
                    "WARN",
                    "codex_unconfirmed_generation_cluster",
                    (
                        f"{account.label}: recent Codex cluster generated output on "
                        f"{len(cluster)} surfaces but did not confirm provider movement; "
                        "run a live refresh so reset-clock late attribution can repair it"
                    ),
                    fix=(
                        "`tk status --refresh --codex`; if it remains unconfirmed, "
                        f'inspect `tk codex-surfaces "{account.label}"`'
                    ),
                )
            ]
        if recent_late_attributed_clusters:
            cluster = recent_late_attributed_clusters[-1]
            winner = _codex_late_attribution_winner(cluster)
            surface = winner.codex_surface if winner and winner.codex_surface else "unknown surface"
            delta = (
                round(winner.codex_anchor_match_delta_seconds, 1)
                if winner and winner.codex_anchor_match_delta_seconds is not None
                else "unknown"
            )
            return [
                DoctorCheck(
                    "WARN",
                    "codex_late_attribution_ambiguous",
                    (
                        f"{account.label}: recent Codex cluster matched a provider reset clock via "
                        f"late attribution on {surface} with delta={delta}s. Timing match; "
                        "manual Codex use near that time can make causality ambiguous"
                    ),
                    fix=(
                        "if you used Codex manually near that time, treat learned surface scores "
                        f'as suggestive; inspect `tk history --verbose --account "{account.label}"`'
                    ),
                )
            ]
        return []
    return [
        DoctorCheck(
            "WARN",
            "codex_repeated_no_evidence_kicks",
            f"{account.label}: {len(recent_no_evidence)} recent Codex kicks completed without generation evidence",
            fix=f'run `tk codex-surfaces "{account.label}"` and `tk status --refresh --codex`',
        )
    ]


def _codex_no_evidence_recent_events(
    history: list[KickEvent],
    label: str,
    *,
    now: float | None = None,
) -> list[KickEvent]:
    current = time.time() if now is None else now
    cutoff = current - 24 * 60 * 60
    return [
        event
        for event in history
        if event.label == label
        and event.timestamp >= cutoff
        and event.success
        and not event.confirmed
        and event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR
    ]


def _codex_unconfirmed_generation_clusters(
    history: list[KickEvent],
    label: str,
    *,
    now: float | None = None,
) -> list[list[KickEvent]]:
    current = time.time() if now is None else now
    cutoff = current - 24 * 60 * 60
    clusters: dict[str, list[KickEvent]] = {}
    for event in history:
        if event.label != label or event.timestamp < cutoff:
            continue
        if not event.codex_cluster_id:
            continue
        if not event.success:
            continue
        if not event.confirmed and not (event.response_text or _event_has_token_usage(event)):
            continue
        clusters.setdefault(event.codex_cluster_id, []).append(event)
    return [
        [event for event in cluster if not event.confirmed]
        for cluster in clusters.values()
        if not any(event.confirmed for event in cluster)
        and len({event.codex_surface for event in cluster}) >= 3
    ]


def _codex_late_attributed_ambiguous_clusters(
    history: list[KickEvent],
    label: str,
    *,
    now: float | None = None,
) -> list[list[KickEvent]]:
    current = time.time() if now is None else now
    cutoff = current - 24 * 60 * 60
    clusters: dict[str, list[KickEvent]] = {}
    for event in history:
        if event.label != label or event.timestamp < cutoff:
            continue
        if not event.codex_cluster_id or not event.success:
            continue
        clusters.setdefault(event.codex_cluster_id, []).append(event)
    return [
        cluster
        for cluster in clusters.values()
        if _codex_late_attribution_winner(cluster) is not None
    ]


def _codex_late_attribution_winner(cluster: list[KickEvent]) -> KickEvent | None:
    for event in cluster:
        if (
            event.confirmed
            and event.codex_confirmation_method == "late_reset_clock"
            and event.codex_attribution
            in {CODEX_ATTRIBUTION_TIMING_MATCH, CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE}
        ):
            return event
    return None


def _event_has_token_usage(event: KickEvent) -> bool:
    return any(
        value is not None and value > 0
        for value in (event.input_tokens, event.output_tokens, event.total_tokens)
    )


def _codex_home_for_account(account: AccountConfig) -> Path | None:
    if account.provider != "codex" or account.source != DataSource.CODEX_DIRECT:
        return None
    if account.provider_home:
        return Path(account.provider_home)
    if account.session_path:
        session_path = Path(account.session_path)
        return session_path.parent if session_path.name == "sessions" else session_path
    return None


def _doctor_claude_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    checks = []
    checks.append(
        DoctorCheck(
            "OK" if claude_probe_git_present(CONFIG_DIR) else "WARN",
            "claude_probe_git_present" if claude_probe_git_present(CONFIG_DIR) else "claude_probe_git_missing",
            (
                f"{account.label}: claude-probe git metadata present"
                if claude_probe_git_present(CONFIG_DIR)
                else f"{account.label}: claude-probe missing .git"
            ),
            fix=None if claude_probe_git_present(CONFIG_DIR) else "run `tk setup` to fix",
        )
    )
    checks.append(
        DoctorCheck(
            "OK" if claude_settings_present(CLAUDE_HOME) else "WARN",
            "claude_settings_present" if claude_settings_present(CLAUDE_HOME) else "claude_settings_missing",
            (
                f"{account.label}: Claude CLI settings present"
                if claude_settings_present(CLAUDE_HOME)
                else f"{account.label}: Claude CLI first-run settings missing"
            ),
            fix=None if claude_settings_present(CLAUDE_HOME) else "run `tk setup` to fix",
        )
    )
    if not account.direct_usage_enabled:
        checks.append(
            DoctorCheck(
                "INFO",
                "claude_direct_usage_account_disabled",
                f"{account.label}: direct /usage disabled at account level",
            )
        )
    checks.append(
        DoctorCheck(
            "OK" if shutil.which("claude") else "WARN",
            "claude_binary_found" if shutil.which("claude") else "claude_binary_missing",
            f"{account.label}: claude binary {'found' if shutil.which('claude') else 'not found'}",
        )
    )
    identity = read_claude_identity(CLAUDE_CONFIG_FILE)
    checks.append(
        DoctorCheck(
            "OK" if identity else "WARN",
            "claude_identity_readable" if identity else "claude_identity_missing",
            f"{account.label}: Claude identity {'readable' if identity else 'not readable'}",
        )
    )
    if account.status_probe_enabled:
        checks.append(
            DoctorCheck(
                "WARN",
                "claude_status_probe_enabled",
                f"{account.label}: explicit quota-consuming status probe is enabled",
                fix=f'run `tk accounts disable-probe "{account.label}"`',
            )
        )
    _append_claude_direct_probe_checks(checks, account, cache_entry)
    _append_source_detail_check(checks, account, cache_entry)
    return checks


def _append_claude_direct_probe_checks(
    checks: list[DoctorCheck],
    account: AccountConfig,
    cache_entry: dict | None,
) -> None:
    if cache_entry is None:
        checks.append(DoctorCheck("INFO", "claude_direct_probe_missing", f"{account.label}: no direct probe recorded yet"))
        return
    probe_at = cache_entry.get("last_direct_probe_at")
    probe_error = cache_entry.get("last_direct_probe_error")
    status = cache_entry.get("status")
    age = _age_since_iso(probe_at if isinstance(probe_at, str) else None)
    if isinstance(probe_error, ClaudeProbeError):
        if probe_error.category == ClaudeProbeErrorCategory.DISABLED and account.direct_usage_enabled:
            checks.append(DoctorCheck("INFO", "claude_direct_probe_missing", f"{account.label}: no direct probe recorded yet"))
            return
        level = _claude_probe_error_level(probe_error, status)
        checks.append(
            DoctorCheck(
                level,
                f"claude_direct_{probe_error.category.value}",
                _claude_probe_error_message(account, probe_error, age),
                fix=_claude_probe_error_fix(probe_error.category) if level in {"WARN", "FAIL"} else None,
            )
        )
        return
    if isinstance(probe_at, str):
        age_text = _format_age(age)
        checks.append(
            DoctorCheck(
                "OK",
                "claude_direct_probe_current",
                f"{account.label}: direct /usage source current (last probe {age_text} ago)",
            )
        )
    else:
        checks.append(DoctorCheck("INFO", "claude_direct_probe_missing", f"{account.label}: no direct probe recorded yet"))


def _age_since_iso(value: str | None) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))


def _claude_probe_error_level(error: ClaudeProbeError, status: Any) -> Literal["INFO", "WARN", "FAIL"]:
    if error.category in {ClaudeProbeErrorCategory.DISABLED, ClaudeProbeErrorCategory.IDENTITY_MISMATCH}:
        return "INFO"
    if isinstance(status, AccountStatus) and status.state == AccountState.UNKNOWN:
        return "FAIL"
    return "WARN"


def _claude_probe_error_message(
    account: AccountConfig,
    error: ClaudeProbeError,
    age_seconds: int | None,
) -> str:
    suffix = f", {_format_age(age_seconds)} ago" if age_seconds is not None else ""
    if error.category == ClaudeProbeErrorCategory.DISABLED:
        return f"{account.label}: direct /usage disabled, using CodexBar fallback"
    if error.category == ClaudeProbeErrorCategory.IDENTITY_MISMATCH:
        return f"{account.label}: identity mismatch with active CLI account, using CodexBar fallback"
    return f"{account.label}: last direct /usage probe failed ({error.category.value}{suffix}), using CodexBar fallback"


def _claude_probe_error_fix(category: ClaudeProbeErrorCategory) -> str:
    return {
        ClaudeProbeErrorCategory.BINARY_MISSING: "install Claude Code or update PATH",
        ClaudeProbeErrorCategory.NOT_AUTHENTICATED: "run `claude` once and complete login",
        ClaudeProbeErrorCategory.TIMEOUT: "run `tk setup`, then retry with `tk status --refresh`; `/usage` should respond within 5s",
        ClaudeProbeErrorCategory.PARSE_FAILED: "`/usage` output format may have changed; file an issue with `tk doctor --json-output`",
        ClaudeProbeErrorCategory.RATE_LIMITED: "retry in a few minutes",
        ClaudeProbeErrorCategory.IDENTITY_UNREADABLE: "check `~/.claude.json` is present and readable",
        ClaudeProbeErrorCategory.PROVIDER_ERROR: "check `claude` is responsive; see logs",
    }.get(category, "")


def _doctor_gemini_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            "INFO",
            "gemini_monitor_only",
            f"{account.label}: monitor-only (daily RPD reset at midnight PT; kicks have no effect)",
        ),
        DoctorCheck(
            "OK" if shutil.which("gemini") else "WARN",
            "gemini_binary_found" if shutil.which("gemini") else "gemini_binary_missing",
            f"{account.label}: gemini binary {'found' if shutil.which('gemini') else 'not found'}",
        ),
        DoctorCheck(
            "OK" if GEMINI_OAUTH_CREDS_FILE.exists() else "WARN",
            "gemini_oauth_creds_found" if GEMINI_OAUTH_CREDS_FILE.exists() else "gemini_oauth_creds_missing",
            f"{account.label}: Gemini OAuth credentials {'found' if GEMINI_OAUTH_CREDS_FILE.exists() else 'not found'}",
        ),
        DoctorCheck("INFO", "gemini_direct_not_implemented", f"{account.label}: Gemini direct quota is not implemented yet"),
    ]
    _append_source_detail_check(checks, account, cache_entry)
    return checks


def _doctor_antigravity_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    meta = _antigravity_process_metadata()
    if meta["app_process_found"]:
        checks.append(DoctorCheck("OK", "antigravity_app_process_found", f"{account.label}: Antigravity app process found"))
    else:
        checks.append(DoctorCheck("INFO", "antigravity_app_not_running", f"{account.label}: Antigravity app process not running"))
    if meta["language_server_found"]:
        shape = meta.get("language_server_shape") or "unknown"
        checks.append(DoctorCheck("OK", "antigravity_language_server_found", f"{account.label}: language server found ({shape})"))
        checks.append(
            DoctorCheck(
                "OK" if meta["csrf_present"] else "WARN",
                "antigravity_csrf_present" if meta["csrf_present"] else "antigravity_csrf_missing",
                f"{account.label}: CSRF flag {'present' if meta['csrf_present'] else 'missing'}",
            )
        )
        checks.append(
            DoctorCheck(
                "OK" if meta["listening_ports"] else "WARN",
                "antigravity_ports_found" if meta["listening_ports"] else "antigravity_ports_missing",
                f"{account.label}: listening ports {'observed' if meta['listening_ports'] else 'not observed'}",
            )
        )
    else:
        checks.append(DoctorCheck("INFO", "antigravity_language_server_not_running", f"{account.label}: language server not running"))
    source_detail = _cache_source_detail(cache_entry)
    if source_detail == "codexbar-snapshot":
        checks.append(
            DoctorCheck(
                "WARN",
                "antigravity_using_codexbar_snapshot",
                f"{account.label}: using CodexBar snapshot; local endpoint probe is only run during refresh",
                fix="run `tk status --refresh`",
            )
        )
    elif source_detail == "antigravity-local":
        checks.append(DoctorCheck("OK", "antigravity_local_cached", f"{account.label}: cached source is antigravity-local"))
    return checks


def _doctor_openrouter_account(account: AccountConfig, cache_entry: dict | None) -> list[DoctorCheck]:
    checks = [DoctorCheck("INFO", "openrouter_monitor_only", f"{account.label}: monitor only")]
    status = cache_entry.get("status") if cache_entry else None
    if isinstance(status, AccountStatus) and status.balance_remaining is not None:
        checks.append(DoctorCheck("OK", "openrouter_balance_cached", f"{account.label}: balance cached"))
    _append_source_detail_check(checks, account, cache_entry)
    return checks


def _append_source_detail_check(
    checks: list[DoctorCheck],
    account: AccountConfig,
    cache_entry: dict | None,
) -> None:
    source_detail = _cache_source_detail(cache_entry)
    if source_detail:
        checks.append(DoctorCheck("INFO", "account_source_detail", f"{account.label}: latest source_detail is {source_detail}"))


def _cache_source_detail(cache_entry: dict | None) -> str | None:
    status = cache_entry.get("status") if cache_entry else None
    return status.source_detail if isinstance(status, AccountStatus) else None


def _antigravity_process_metadata() -> dict:
    metadata = {
        "app_process_found": False,
        "language_server_found": False,
        "language_server_shape": None,
        "csrf_present": False,
        "listening_ports": [],
        "error": None,
    }
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        metadata["error"] = str(exc)
        return metadata
    language_pid: int | None = None
    for line in result.stdout.splitlines():
        parsed = parse_process_line(line)
        if parsed is None:
            continue
        pid, command = parsed
        lower = command.lower()
        if "antigravity.app" in lower or "/.antigravity/" in lower:
            metadata["app_process_found"] = True
        if is_antigravity_language_server(command):
            metadata["language_server_found"] = True
            metadata["language_server_shape"] = "language_server_macos" if "language_server_macos" in lower else "language_server"
            metadata["csrf_present"] = "--csrf_token" in lower
            language_pid = pid
    if language_pid is not None:
        metadata["listening_ports"] = listening_ports_for_pid(language_pid)
    return metadata


def _doctor_codexbar() -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            "OK" if shutil.which("codexbar") else "WARN",
            "codexbar_binary_found" if shutil.which("codexbar") else "codexbar_binary_missing",
            f"CodexBar binary {'found' if shutil.which('codexbar') else 'not found'}",
        )
    ]
    snapshot_files = [path for path in CODEXBAR_WIDGET_SNAPSHOT_FILES if path.exists()]
    if snapshot_files:
        checks.append(DoctorCheck("OK", "codexbar_snapshot_found", "CodexBar widget snapshot found"))
    else:
        checks.append(DoctorCheck("INFO", "codexbar_snapshot_missing", "CodexBar widget snapshot not found"))
    if CODEXBAR_HISTORY_DIR.exists():
        checks.append(DoctorCheck("OK", "codexbar_history_found", "CodexBar history directory found"))
    else:
        checks.append(DoctorCheck("INFO", "codexbar_history_missing", "CodexBar history directory not found"))
    return checks


def render_doctor_report(report: DoctorReport, console: Console | None = None) -> None:
    console = console or Console(width=120)
    console.print(
        f"[green]{report.summary.ok} OK[/green] · "
        f"[yellow]{report.summary.warn} WARN[/yellow] · "
        f"[red]{report.summary.fail} FAIL[/red] · "
        f"{report.summary.accounts} accounts · cache {report.summary.cache_status}"
    )
    _render_dict_section(console, "Config", report.config)
    _render_dict_section(console, "Cache", report.cache)
    _render_dict_section(console, "Daemon", report.daemon)
    _render_dict_section(console, "Schedule", report.schedule)
    _render_dict_section(console, "Notifications", report.notifications)
    _render_accounts_section(console, report.accounts)
    _render_checks_section(console, "Provider Diagnostics", report.checks, report.accounts)


def _render_dict_section(console: Console, title: str, data: dict) -> None:
    table = Table(title=title, show_header=True)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for key, value in data.items():
        if key == "telegram_bot_token":
            value = "configured" if value else "missing"
        table.add_row(str(key), _format_value(value))
    console.print(table)


def _render_accounts_section(console: Console, accounts: list[DoctorAccountReport]) -> None:
    table = Table(title="Accounts", show_header=True)
    for column in [
        "Account",
        "Provider",
        "Source",
        "Detail",
        "State",
        "Stale",
        "Refresh error",
        "Auto",
        "Weekly",
        "Session",
        "Model",
    ]:
        table.add_column(column)
    for account in accounts:
        table.add_row(
            account.label,
            account.provider,
            account.source,
            account.source_detail or "—",
            account.state,
            "yes" if account.stale else "no",
            account.refresh_error or "—",
            "yes" if account.auto_kick else "no",
            "yes" if account.weekly_auto_kick else "no",
            "yes" if account.session_auto_kick else "no",
            account.kick_model or "default",
        )
    console.print(table)


def _render_checks_section(
    console: Console,
    title: str,
    checks: list[DoctorCheck],
    accounts: list[DoctorAccountReport],
) -> None:
    all_checks = list(checks)
    for account in accounts:
        all_checks.extend(account.checks)
    table = Table(title=title, show_header=True)
    table.add_column("Level")
    table.add_column("Check")
    for check in all_checks:
        table.add_row(_styled_level(check.level), check.message)
        if check.fix:
            table.add_row("", f"fix: {check.fix}")
    console.print(table)


def _styled_level(level: str) -> str:
    return {
        "OK": "[green]OK[/green]",
        "WARN": "[yellow]WARN[/yellow]",
        "FAIL": "[red]FAIL[/red]",
        "INFO": "[blue]INFO[/blue]",
    }.get(level, level)


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return "—"
    return str(value)
