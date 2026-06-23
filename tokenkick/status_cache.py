"""Status fetching and daemon cache helpers for TokenKick CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

from .direct import normalize_source_detail
from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeProbeContext,
    ClaudeProbeError,
    Config,
    DataSource,
    account_key_string,
)
from .migrations import (
    _codex_configured_home_identity_not_mismatched,
    _codex_identity_metadata_matches,
)
from .sources import (
    _claude_direct_status_missing_weekly_reset,
    claude_cli_usage_refresh_allowed,
    polling_pass_cache,
)
from .state_io import atomic_write_text, locked_atomic_write_text, state_file_lock

_STATUS_REFRESH_LOCK_ACQUIRE_ERROR: str | None = None


def _cli():
    from . import cli as cli_mod

    return cli_mod


def _daemon_log(event: str, **fields) -> None:
    _cli()._daemon_log(event, **fields)


def _load_saved_accounts(config: Config) -> list[AccountConfig]:
    """Load saved accounts without live provider discovery."""
    return list(config.accounts or [])


def _load_accounts(config: Config) -> list[AccountConfig]:
    """Load saved account config without live provider discovery."""
    return _load_saved_accounts(config)


def _fetch_status(account: AccountConfig, config: Config | None = None) -> AccountStatus:
    """Fetch status while honoring config thresholds and old test doubles."""
    claude_probe_context = (
        _claude_probe_context_for_account(account, config)
        if config is not None and account.provider == "claude" and account.source == DataSource.CLAUDE_DIRECT
        else None
    )
    if config is None:
        return _cli().fetch_status(account)
    try:
        kwargs = {
            "codexbar_staleness_threshold_seconds": config.codexbar_staleness_threshold_seconds,
            "codexbar_rejection_threshold_seconds": config.codexbar_rejection_threshold_seconds,
        }
        if claude_probe_context is not None:
            kwargs["claude_probe_context"] = claude_probe_context
        status = _cli().fetch_status(account, **kwargs)
    except TypeError:
        status = _cli().fetch_status(account)
    if claude_probe_context is not None:
        setattr(status, "_claude_probe_context", claude_probe_context)
    return status


def _claude_probe_context_for_account(account: AccountConfig, config: Config) -> ClaudeProbeContext:
    entry = _load_status_cache_entries().get(account_key_string(account))
    last_success_status = None
    last_success_at = None
    last_error = None
    last_probe_at = None
    if entry:
        last_probe_at = entry.get("last_direct_probe_at")
        last_success_at = entry.get("last_direct_success_at")
        last_error = entry.get("last_direct_probe_error")
        last_success_status = entry.get("last_direct_success_status")
        status = entry.get("status")
        if (
            last_success_status is None
            and isinstance(status, AccountStatus)
            and _claude_cached_success_status_usable(status)
        ):
            last_success_status = status
            last_success_at = last_success_at or entry.get("cached_at")
        if not _claude_cached_success_status_usable(last_success_status):
            last_success_status = None
            last_success_at = None
    return ClaudeProbeContext(
        direct_usage_enabled=config.claude.direct_usage_enabled,
        last_direct_probe_at=last_probe_at if isinstance(last_probe_at, str) else None,
        last_direct_probe_error=last_error if isinstance(last_error, ClaudeProbeError) else None,
        last_direct_success_at=last_success_at if isinstance(last_success_at, str) else None,
        last_direct_success_status=last_success_status if isinstance(last_success_status, AccountStatus) else None,
    )


def _claude_cached_success_status_usable(status: AccountStatus | None) -> bool:
    return (
        isinstance(status, AccountStatus)
        and status.source_detail == "claude-cli-usage"
        and status.state != AccountState.UNKNOWN
        and not _claude_direct_status_missing_weekly_reset(status)
    )


def _load_saved_account_status_snapshot(
    accounts: list[AccountConfig],
    config: Config | None = None,
) -> tuple[dict[str, AccountStatus], dict[str, str]]:
    """Fetch one live status snapshot plus cache-refresh failures for saved accounts."""
    statuses_by_key: dict[str, AccountStatus] = {}
    failures_by_key: dict[str, str] = {}
    with polling_pass_cache():
        for account in accounts:
            key = account_key_string(account)
            try:
                status = _cli()._fetch_status(account, config)
            except Exception as exc:
                status = AccountStatus(
                    label=account.label,
                    state=AccountState.UNKNOWN,
                    error=str(exc),
                )
                failures_by_key[key] = exc.__class__.__name__
            else:
                error_class = _status_cache_error_class(status)
                if error_class is not None:
                    failures_by_key[key] = error_class
            statuses_by_key[key] = status
    return statuses_by_key, failures_by_key


def _is_antigravity_account(account: AccountConfig) -> bool:
    return account.provider == "antigravity" or account.codexbar_provider == "antigravity"


def _status_cache_observed_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_cache_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_status_cache_observed_at(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _format_status_cache_age(seconds: int) -> str:
    seconds = max(0, seconds)
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


def _format_status_footer_timestamp(value: datetime, *, now: datetime | None = None) -> str:
    local_value = value.astimezone()
    local_now = (now or _cli()._status_cache_now()).astimezone()
    if local_value.date() == local_now.date():
        return local_value.strftime("%H:%M")
    return local_value.strftime("%Y-%m-%d %H:%M")


def _format_status_cache_footer(entries: dict[str, dict], config: Config) -> str:
    freshness = _status_cache_freshness(entries, config)
    if freshness["stale"] == 0:
        cached_at = freshness["fresh_cached_at"]
        if cached_at is None:
            return "Cached status. Run [bold]tk status --refresh[/bold] for live provider data."
        observed_local = _format_status_footer_timestamp(cached_at)
        age_text = _format_status_cache_age(
            int((_cli()._status_cache_now() - cached_at).total_seconds())
        )
        return (
            f"Cached provider data from {observed_local} ({age_text} ago). "
            "Run [bold]tk status --refresh[/bold] for live provider data."
        )

    parts = [f"Cache: {freshness['fresh']} current"]
    if freshness["fresh_cached_at"] is not None:
        fresh_age = _format_status_cache_age(
            int((_cli()._status_cache_now() - freshness["fresh_cached_at"]).total_seconds())
        )
        parts[-1] += f" ({fresh_age} ago)"
    stale_text = f"{freshness['stale']} old"
    if freshness["oldest_stale_at"] is not None:
        stale_age = _format_status_cache_age(
            int((_cli()._status_cache_now() - freshness["oldest_stale_at"]).total_seconds())
        )
        stale_text += f" (oldest {stale_age} ago)"
    stale_details = _status_cache_stale_details(entries, config)
    if stale_details:
        stale_text += f": {stale_details}"
    parts.append(stale_text)
    return f"{', '.join(parts)}. Run [bold]tk status --refresh[/bold] for live provider data."


def _status_cache_stale_details(entries: dict[str, dict], config: Config) -> str:
    details: list[str] = []
    for key, entry in sorted(entries.items(), key=lambda item: _status_cache_entry_label(item[0], item[1])):
        if not _status_cache_entry_is_stale(entry, config):
            continue
        label = _status_cache_entry_label(key, entry)
        age = _status_cache_entry_provider_age(entry)
        reason = _status_cache_stale_reason(entry)
        detail = label
        if age and reason:
            detail += f" ({reason}; last provider read {age} ago)"
        elif age:
            detail += f" (last provider read {age} ago)"
        elif reason:
            detail += f" ({reason})"
        details.append(detail)

    if len(details) > 3:
        remaining = len(details) - 3
        details = [*details[:3], f"+{remaining} more"]
    return "; ".join(details)


def _status_cache_entry_label(key: str, entry: dict) -> str:
    account = entry.get("account")
    if isinstance(account, AccountConfig) and account.label:
        return account.label
    status = entry.get("status")
    if isinstance(status, AccountStatus) and status.label:
        return status.label
    return key


def _status_cache_entry_provider_age(entry: dict) -> str | None:
    observed_at = _status_cache_provider_observed_at(entry)
    observed = _parse_status_cache_observed_at(observed_at) if observed_at else None
    if observed is None:
        return None
    age_seconds = int((_cli()._status_cache_now() - observed).total_seconds())
    return _format_status_cache_age(age_seconds)


def _status_cache_stale_reason(entry: dict) -> str | None:
    if entry.get("needs_refresh"):
        return "needs refresh"
    status = entry.get("status")
    if _status_cache_entry_weekly_exhausted(status):
        return "weekly exhausted"
    refresh_error = entry.get("refresh_error")
    if isinstance(refresh_error, str) and refresh_error:
        account = entry.get("account")
        if isinstance(account, AccountConfig) and account.provider == "claude":
            return "passive refresh unavailable"
        return f"last refresh failed: {refresh_error}"
    return None


def _status_cache_entry_weekly_exhausted(status: object) -> bool:
    return (
        isinstance(status, AccountStatus)
        and status.state != AccountState.FRESH
        and status.used_percent is not None
        and status.used_percent >= 100.0
    )


def _account_status_cache_dict(
    status: AccountStatus,
    *,
    observed_at: str | None = None,
    source_detail: str = "unknown",
) -> dict:
    _fill_status_cache_reset_anchors(status, observed_at)
    data = {
        field.name: getattr(status, field.name)
        for field in fields(AccountStatus)
        if getattr(status, field.name) is not None
    }
    data["state"] = status.state.value
    data.setdefault("observed_at", observed_at or _cli()._status_cache_observed_at())
    data.setdefault("source_detail", source_detail)
    data.setdefault("stale", False)
    data.setdefault("stale_seconds", None)
    return data


def _account_status_from_cache(data: dict) -> AccountStatus | None:
    if not isinstance(data, dict):
        return None
    allowed_fields = {field.name for field in fields(AccountStatus)}
    status_data = {key: value for key, value in data.items() if key in allowed_fields}
    state = status_data.get("state")
    label = status_data.get("label")
    if not isinstance(label, str) or not isinstance(state, str):
        return None
    try:
        status_data["state"] = AccountState(state)
        status = AccountStatus(**status_data)
        status.source_detail = normalize_source_detail(status.source_detail)
        _recompute_status_cache_countdowns(status)
        return status
    except (TypeError, ValueError):
        return None


def _fill_status_cache_reset_anchors(status: AccountStatus, observed_at: str | None = None) -> None:
    observed = observed_at or status.observed_at
    observed_time = _parse_status_cache_observed_at(observed) if isinstance(observed, str) else None
    observed_ts = observed_time.timestamp() if observed_time is not None else time.time()
    if status.resets_at is None and status.resets_in_seconds is not None:
        status.resets_at = observed_ts + status.resets_in_seconds
    if status.session_resets_at is None and status.session_resets_in_seconds is not None:
        status.session_resets_at = observed_ts + status.session_resets_in_seconds


def _recompute_status_cache_countdowns(status: AccountStatus) -> None:
    now = time.time()
    if status.resets_at is not None:
        status.resets_in_seconds = max(0, int(status.resets_at - now))
    if status.session_resets_at is not None:
        status.session_resets_in_seconds = max(0, int(status.session_resets_at - now))


def _status_cache_error_class(status: AccountStatus) -> str | None:
    if _claude_direct_status_missing_weekly_reset(status):
        return "IncompleteClaudeWeeklyReset"
    if status.state != AccountState.UNKNOWN or not status.error:
        return None
    error = status.error.lower()
    if "no session data" in error:
        return None
    if "timed out" in error:
        return "TimeoutExpired"
    if "could not be found" in error or "connection" in error or "network" in error:
        return "NetworkError"
    if "invalid data" in error or "closed stdout" in error:
        return "ParseError"
    if "auth" in error or "credentials" in error or "login" in error:
        return "AuthError"
    return "ProviderError"


def _read_status_cache_data() -> dict | None:
    if not _cli().STATUS_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_cli().STATUS_CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != 2:
        return None
    return data


def _write_status_cache_data(data: dict) -> None:
    _cli().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with state_file_lock(_cli().STATUS_CACHE_FILE):
        _write_status_cache_data_unlocked(data)


def _write_status_cache_data_unlocked(data: dict) -> None:
    atomic_write_text(
        _cli().STATUS_CACHE_FILE,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


def _load_status_cache_entries() -> dict[str, dict]:
    data = _read_status_cache_data()
    if data is None:
        return {}
    raw_accounts = data.get("accounts")
    if not isinstance(raw_accounts, dict):
        return {}
    entries: dict[str, dict] = {}
    for key, entry in raw_accounts.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        account_data = entry.get("account")
        status = _account_status_from_cache(entry.get("status"))
        cached_at = entry.get("cached_at")
        refresh_error = entry.get("refresh_error")
        provider_observed_at = entry.get("provider_observed_at")
        last_direct_probe_at = entry.get("last_direct_probe_at")
        last_direct_success_at = entry.get("last_direct_success_at")
        last_direct_probe_error = ClaudeProbeError.from_dict(entry.get("last_direct_probe_error"))
        last_direct_success_status = _account_status_from_cache(entry.get("last_direct_success_status"))
        needs_refresh = bool(entry.get("needs_refresh", False))
        if not isinstance(account_data, dict) or status is None or not isinstance(cached_at, str):
            continue
        if refresh_error is not None and not isinstance(refresh_error, str):
            refresh_error = None
        try:
            account = AccountConfig.from_dict(account_data)
        except (TypeError, ValueError):
            continue
        if _claude_direct_status_missing_weekly_reset(status):
            refresh_error = "IncompleteClaudeWeeklyReset"
        if not _claude_cached_success_status_usable(last_direct_success_status):
            last_direct_success_status = None
            last_direct_success_at = None
        entries[key] = {
            "account": account,
            "status": status,
            "cached_at": cached_at,
            "provider_observed_at": (
                provider_observed_at
                if isinstance(provider_observed_at, str)
                else status.observed_at or cached_at
            ),
            "refresh_error": refresh_error,
            "last_direct_probe_at": last_direct_probe_at if isinstance(last_direct_probe_at, str) else None,
            "last_direct_probe_error": last_direct_probe_error,
            "last_direct_success_at": last_direct_success_at if isinstance(last_direct_success_at, str) else None,
            "last_direct_success_status": last_direct_success_status,
            "needs_refresh": needs_refresh,
        }
    return entries


def _status_cache_entry_is_stale(entry: dict, config: Config) -> bool:
    if entry.get("needs_refresh"):
        return True
    if entry.get("refresh_error"):
        return True
    provider_observed_at = _status_cache_provider_observed_at(entry)
    if provider_observed_at is None:
        return True
    observed = _parse_status_cache_observed_at(provider_observed_at)
    if observed is None:
        return True
    age_seconds = int((_cli()._status_cache_now() - observed).total_seconds())
    return age_seconds > config.poll_interval_minutes * 60 * 2


def _status_cache_provider_observed_at(entry: dict) -> str | None:
    provider_observed_at = entry.get("provider_observed_at")
    if isinstance(provider_observed_at, str):
        return provider_observed_at
    status = entry.get("status")
    if isinstance(status, AccountStatus) and isinstance(status.observed_at, str):
        return status.observed_at
    cached_at = entry.get("cached_at")
    return cached_at if isinstance(cached_at, str) else None


def _read_dormant_hint_state() -> dict[str, dict]:
    if not _cli().DORMANT_HINTS_FILE.exists():
        return {}
    try:
        data = json.loads(_cli().DORMANT_HINTS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    accounts = data.get("accounts")
    return accounts if isinstance(accounts, dict) else {}


def _write_dormant_hint_state(accounts: dict[str, dict]) -> None:
    _cli().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "accounts": accounts}
    locked_atomic_write_text(
        _cli().DORMANT_HINTS_FILE,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


def _status_cache_freshness(entries: dict[str, dict], config: Config) -> dict:
    fresh = 0
    stale = 0
    fresh_times: list[datetime] = []
    stale_times: list[datetime] = []
    for entry in entries.values():
        observed_at = _status_cache_provider_observed_at(entry)
        observed = _parse_status_cache_observed_at(observed_at) if observed_at else None
        if _status_cache_entry_is_stale(entry, config):
            stale += 1
            if observed is not None:
                stale_times.append(observed)
        else:
            fresh += 1
            if observed is not None:
                fresh_times.append(observed)
    return {
        "fresh": fresh,
        "stale": stale,
        "fresh_cached_at": min(fresh_times) if fresh_times else None,
        "oldest_stale_at": min(stale_times) if stale_times else None,
    }


def _status_cache_needs_refresh(entries: dict[str, dict], config: Config) -> bool:
    if not entries:
        return True
    now = _cli()._status_cache_now()
    for entry in entries.values():
        if entry.get("needs_refresh"):
            return True
        if entry.get("refresh_error"):
            cached_at = entry.get("cached_at")
            observed = _parse_status_cache_observed_at(cached_at) if isinstance(cached_at, str) else None
            if observed is None:
                return True
            age_seconds = int((now - observed).total_seconds())
            if age_seconds > min(config.poll_interval_minutes * 60, 60):
                return True
            continue
        observed_at = _status_cache_provider_observed_at(entry)
        observed = _parse_status_cache_observed_at(observed_at) if observed_at else None
        if observed is None:
            return True
        age_seconds = int((now - observed).total_seconds())
        if age_seconds > config.poll_interval_minutes * 60 * 2:
            return True
    return False


def _cache_statuses_by_key_from_pairs(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
) -> dict[str, AccountStatus]:
    return {
        account_key_string(account): status
        for account, status in zip(accounts, statuses, strict=False)
    }


def _failures_by_key_from_status_pairs(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
) -> dict[str, str]:
    failures = {}
    for account, status in zip(accounts, statuses, strict=False):
        error_class = _status_cache_error_class(status)
        if error_class is not None:
            failures[account_key_string(account)] = error_class
    return failures


def _copy_existing_claude_probe_metadata(existing: dict, entry: dict) -> None:
    for key in (
        "last_direct_probe_at",
        "last_direct_probe_error",
        "last_direct_success_at",
        "last_direct_success_status",
    ):
        if existing.get(key) is not None:
            entry[key] = existing[key]


def _apply_claude_probe_context_to_entry(
    entry: dict,
    context: ClaudeProbeContext | None,
) -> None:
    if context is None:
        return
    entry["last_direct_probe_at"] = context.last_direct_probe_at
    entry["last_direct_probe_error"] = context.last_direct_probe_error
    entry["last_direct_success_at"] = context.last_direct_success_at
    entry["last_direct_success_status"] = context.last_direct_success_status


def _entry_provider_observed_at(entry: dict, fallback: str | None = None) -> str | None:
    provider_observed_at = entry.get("provider_observed_at")
    if isinstance(provider_observed_at, str):
        return provider_observed_at
    status = entry.get("status")
    if isinstance(status, AccountStatus) and isinstance(status.observed_at, str):
        return status.observed_at
    return fallback


def _status_cache_entry_dict(entry: dict) -> dict:
    provider_observed_at = _entry_provider_observed_at(entry, entry["cached_at"])
    return {
        "account": entry["account"].to_dict(),
        "status": _account_status_cache_dict(
            entry["status"],
            observed_at=provider_observed_at,
            source_detail=entry["account"].source.value,
        ),
        "cached_at": entry["cached_at"],
        "provider_observed_at": provider_observed_at,
        "refresh_error": entry.get("refresh_error"),
        "needs_refresh": bool(entry.get("needs_refresh", False)),
        "last_direct_probe_at": entry.get("last_direct_probe_at"),
        "last_direct_probe_error": (
            entry["last_direct_probe_error"].to_dict()
            if isinstance(entry.get("last_direct_probe_error"), ClaudeProbeError)
            else None
        ),
        "last_direct_success_at": entry.get("last_direct_success_at"),
        "last_direct_success_status": (
            _account_status_cache_dict(
                entry["last_direct_success_status"],
                observed_at=entry.get("last_direct_success_at"),
                source_detail="claude-cli-usage",
            )
            if isinstance(entry.get("last_direct_success_status"), AccountStatus)
            else None
        ),
    }


def _mark_status_cache_entry_stale(account: AccountConfig) -> bool:
    """Mark one cached account stale so the normal status path refreshes it."""
    try:
        lock_context = state_file_lock(_cli().STATUS_CACHE_FILE)
        lock_context.__enter__()
    except OSError:
        return False
    try:
        entries = _load_status_cache_entries()
        key = account_key_string(account)
        entry = entries.get(key)
        if entry is None:
            return False
        status = entry.get("status")
        if isinstance(status, AccountStatus):
            entry["status"] = replace(status, stale=True, stale_seconds=0)
        entry["needs_refresh"] = True
        entries[key] = entry
        try:
            _write_status_cache_data_unlocked(_status_cache_data(entries))
        except OSError:
            return False
        return True
    finally:
        lock_context.__exit__(None, None, None)


def _status_cache_data(entries: dict[str, dict]) -> dict:
    return {
        "version": 2,
        "accounts": {
            key: _status_cache_entry_dict(entry)
            for key, entry in entries.items()
        },
    }


def _save_status_cache(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    failures_by_key: dict[str, str] | None = None,
    *,
    daemon_log: bool = False,
) -> None:
    failures_by_key = failures_by_key or {}
    try:
        lock_context = state_file_lock(_cli().STATUS_CACHE_FILE)
        lock_context.__enter__()
    except OSError:
        return
    try:
        entries = _load_status_cache_entries()
        cached_at = _cli()._status_cache_observed_at()
        for account in accounts:
            key = account_key_string(account)
            status = statuses_by_key.get(key)
            claude_probe_context = (
                getattr(status, "_claude_probe_context", None) if status is not None else None
            )
            failure = failures_by_key.get(key) or (status and _status_cache_error_class(status))
            if failure:
                existing = entries.get(key)
                if existing is not None:
                    existing["refresh_error"] = failure
                    existing["account"] = account
                    existing["cached_at"] = cached_at
                    existing["provider_observed_at"] = _entry_provider_observed_at(existing)
                    _apply_claude_probe_context_to_entry(existing, claude_probe_context)
                    entries[key] = existing
                elif status is not None:
                    entry = {
                        "account": account,
                        "status": status,
                        "cached_at": cached_at,
                        "provider_observed_at": status.observed_at or cached_at,
                        "refresh_error": failure,
                    }
                    _apply_claude_probe_context_to_entry(entry, claude_probe_context)
                    entries[key] = entry
                if daemon_log:
                    _daemon_log("cache_refresh_failed", account=account.label, error_class=failure)
                continue
            if status is None:
                continue
            existing = entries.get(key, {})
            entry = {
                "account": account,
                "status": status,
                "cached_at": cached_at,
                "provider_observed_at": status.observed_at or cached_at,
                "refresh_error": None,
            }
            _copy_existing_claude_probe_metadata(existing, entry)
            _apply_claude_probe_context_to_entry(entry, claude_probe_context)
            entries[key] = entry

        try:
            _write_status_cache_data_unlocked(_status_cache_data(entries))
        except OSError:
            return
    finally:
        lock_context.__exit__(None, None, None)


def _refresh_status_cache(
    config: Config,
    *,
    daemon_log: bool = False,
    allow_claude_usage: bool = False,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    refresh_context = claude_cli_usage_refresh_allowed() if allow_claude_usage else nullcontext()
    with refresh_context:
        accounts, statuses, _discovered, _summary, _new_accounts = _cli()._load_account_status_pairs(config)
    _save_status_cache(
        accounts,
        _cache_statuses_by_key_from_pairs(accounts, statuses),
        _failures_by_key_from_status_pairs(accounts, statuses),
        daemon_log=daemon_log,
    )
    return accounts, statuses


def _refresh_status_cache_fast(
    config: Config,
) -> tuple[
    list[AccountConfig],
    list[AccountStatus],
    bool,
    str,
    list[AccountConfig],
    bool,
]:
    if not any(_is_antigravity_account(account) for account in config.accounts):
        accounts, statuses, discovered, summary, new_accounts = _cli()._load_account_status_pairs(config)
        _save_status_cache(
            accounts,
            _cache_statuses_by_key_from_pairs(accounts, statuses),
            _failures_by_key_from_status_pairs(accounts, statuses),
        )
        return accounts, statuses, discovered, summary, new_accounts, False

    statuses_by_key, failures_by_key = _load_saved_account_status_snapshot(config.accounts, config)
    accounts = list(config.accounts)
    statuses = [
        statuses_by_key.get(account_key_string(account))
        or AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Provider refresh returned no status.",
        )
        for account in accounts
    ]
    _save_status_cache(
        accounts,
        statuses_by_key,
        failures_by_key,
    )
    return (
        accounts,
        statuses,
        False,
        "Refreshed saved accounts.",
        [],
        False,
    )


def _read_refresh_lock(path: Path) -> tuple[int | None, int | None] | None:
    if not path.exists():
        return None
    try:
        stat = path.stat()
        age_seconds = max(0, int(time.time() - stat.st_mtime))
        raw_pid = path.read_text().strip()
    except OSError:
        return None

    try:
        pid = int(raw_pid)
    except ValueError:
        pid = None
    return pid, age_seconds


def _reap_dead_refresh_lock(path: Path) -> dict[str, int | None] | None:
    info = _read_refresh_lock(path)
    if info is None:
        return None
    pid, age_seconds = info
    if pid is not None:
        if _pid_is_running(pid):
            return None
        try:
            path.unlink()
        except OSError:
            return None
        return {"pid": pid, "age_seconds": age_seconds}
    return None


def _status_refresh_lock_info() -> dict[str, int | None] | None:
    lock_path = _cli().STATUS_CACHE_REFRESH_LOCK_FILE
    _reap_dead_refresh_lock(lock_path)
    info = _read_refresh_lock(lock_path)
    if info is None:
        if lock_path.exists():
            return {"pid": None, "age_seconds": None}
        return None

    pid, age_seconds = info
    if pid is not None and _pid_is_running(pid):
        return {"pid": pid, "age_seconds": age_seconds}

    if age_seconds is not None and age_seconds <= _cli().STATUS_CACHE_REFRESH_LOCK_MAX_AGE_SECONDS:
        return {"pid": pid, "age_seconds": age_seconds}

    return {"pid": pid, "age_seconds": age_seconds}


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _status_refresh_lock_active() -> bool:
    return _status_refresh_lock_info() is not None


def _status_refresh_lock_acquire_error() -> str | None:
    return _STATUS_REFRESH_LOCK_ACQUIRE_ERROR


def _set_status_refresh_lock_acquire_error(error: str | None) -> None:
    global _STATUS_REFRESH_LOCK_ACQUIRE_ERROR
    _STATUS_REFRESH_LOCK_ACQUIRE_ERROR = error


def _clear_status_refresh_lock_acquire_error() -> None:
    _set_status_refresh_lock_acquire_error(None)


def _acquire_status_refresh_lock() -> bool:
    _clear_status_refresh_lock_acquire_error()
    if _status_refresh_lock_active():
        return False
    try:
        _cli().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            _cli().STATUS_CACHE_REFRESH_LOCK_FILE,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        return False
    except OSError as exc:
        _set_status_refresh_lock_acquire_error(
            f"Background refresh lock could not be acquired: {exc}"
        )
        return False
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
    except OSError as exc:
        _set_status_refresh_lock_acquire_error(
            f"Background refresh lock could not be written: {exc}"
        )
        try:
            _cli().STATUS_CACHE_REFRESH_LOCK_FILE.unlink()
        except OSError:
            pass
        return False
    return True


def _release_status_refresh_lock() -> None:
    try:
        _cli().STATUS_CACHE_REFRESH_LOCK_FILE.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _start_background_status_refresh() -> bool:
    if not _acquire_status_refresh_lock():
        return False
    log = None
    try:
        _cli().CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            log = _cli().DAEMON_LOG_FILE.open("a")
            stdout = log
            stderr = subprocess.STDOUT
        except OSError:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL
        process = subprocess.Popen(
            [sys.executable, "-m", "tokenkick.cli", "refresh-cache"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            _cli().STATUS_CACHE_REFRESH_LOCK_FILE.write_text(str(process.pid))
        except OSError:
            pass
        return True
    except OSError:
        _release_status_refresh_lock()
        return False
    finally:
        if log is not None:
            log.close()


def _background_status_refresh_message(*, started: bool) -> str | None:
    info = _status_refresh_lock_info()
    if info is None and not started:
        return None
    if started:
        return (
            "Background refresh started. Provider refresh usually takes 30-60s; "
            "run [bold]tk status[/bold] again after it finishes."
        )

    age_seconds = info.get("age_seconds") if info is not None else None
    age_text = (
        f" for {_format_status_cache_age(age_seconds)}"
        if isinstance(age_seconds, int)
        else ""
    )
    return (
        f"Background refresh still running{age_text}. Provider refresh usually takes 30-60s; "
        "run [bold]tk status[/bold] again after it finishes."
    )


def _load_status_cache(
    config: Config | None = None,
) -> tuple[list[AccountConfig], list[AccountStatus], dict[str, dict]] | None:
    entries = _load_status_cache_entries()
    if not entries:
        return None

    return _load_status_cache_for_accounts(
        config,
        entries,
        require_all=True,
    )


def _load_status_cache_for_accounts(
    config: Config | None,
    entries: dict[str, dict],
    *,
    require_all: bool,
) -> tuple[list[AccountConfig], list[AccountStatus], dict[str, dict]] | None:
    accounts_for_display = (
        list(config.accounts)
        if config is not None and config.accounts
        else [entry["account"] for entry in entries.values()]
    )
    statuses: list[AccountStatus] = []
    entries_for_display: dict[str, dict] = {}
    for account in accounts_for_display:
        key = account_key_string(account)
        entry = entries.get(key)
        if entry is None:
            if require_all and config is not None and config.accounts:
                return None
            continue
        if not _status_cache_entry_matches_configured_account(account, entry):
            if require_all:
                return None
            continue
        display_status = replace(entry["status"], label=account.label)
        statuses.append(display_status)
        entries_for_display[key] = {**entry, "account": account, "status": display_status}
    if require_all and len(statuses) != len(accounts_for_display):
        return None
    return accounts_for_display, statuses, entries_for_display


def _status_cache_entry_matches_configured_account(
    account: AccountConfig,
    entry: dict,
) -> bool:
    from .discovery import _codex_has_home_scope

    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or not _codex_has_home_scope(account)
    ):
        return True

    cached_account = entry.get("account")
    if isinstance(cached_account, AccountConfig) and not _codex_identity_metadata_matches(
        account,
        cached_account,
    ):
        return False

    return _codex_configured_home_identity_not_mismatched(account)
