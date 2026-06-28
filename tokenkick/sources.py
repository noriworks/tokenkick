"""Data sources for reading rate-limit status."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import ssl
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .antigravity import (
    ANTIGRAVITY_CLI_SOURCE_DETAIL,
    antigravity_status_from_extra_rate_windows,
    has_complete_antigravity_quota_windows,
    is_antigravity_language_server,
    is_language_server_command,
    lsof_binary,
    parse_lsof_listening_ports,
    parse_process_line,
)
from .codexbar_source import (
    CODEXBAR_REJECTION_THRESHOLD_SECONDS,
    CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    _fetch_codexbar_cli,
    _fetch_codexbar_http,
    _fetch_codexbar_local_status,
    codexbar_json_cache,
    codexbar_json_cache_active,
)
from .claude_setup import ensure_claude_probe_ready
from .direct import (
    CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
    CODEX_PROVIDER_USAGE_TIMEOUT_SECONDS,
    CodexProviderUsageError,
    CodexProviderUsageRead,
    claude_auth_status,
    codex_login_status,
    probe_claude_status,
    read_codex_provider_usage,
    read_claude_identity,
    read_codex_identity,
)
from .models import (
    CONFIG_DIR,
    CODEX_DEFAULT_RATE_LIMIT_ID,
    CODEX_SPARK_RATE_LIMIT_ID,
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeProbeContext,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    DataSource,
    codex_rate_limit_id_for_account,
)
from .source_utils import (
    _determine_state,
    _ensure_status_metadata,
    _nested_get,
    _parse_reset_timestamp,
    _seconds_until_reset,
    _status_observed_at,
    _to_float,
)

try:
    import pty
    import termios
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms.
    pty = None
    termios = None

CODEX_APPSERVER_RATELIMITS_SCHEMA_VERSION = 1
CODEX_APPSERVER_UNANCHORED_RESET_TOLERANCE_SECONDS = 120
CODEX_PHANTOM_SESSION_MAX_USED_PERCENT = 2.0
CODEX_PHANTOM_SESSION_FULL_RESET_RATIO = 0.95
ANTIGRAVITY_TIMEOUT_SECONDS = 8.0
ANTIGRAVITY_SOURCE_DETAIL = "antigravity-local"
CLAUDE_CLI_USAGE_TIMEOUT_SECONDS = 5.0
CLAUDE_CLI_USAGE_RETRY_TIMEOUT_SECONDS = 10.0
CLAUDE_CLI_USAGE_TOTAL_TIMEOUT_SECONDS = (
    CLAUDE_CLI_USAGE_TIMEOUT_SECONDS + CLAUDE_CLI_USAGE_RETRY_TIMEOUT_SECONDS
)
CLAUDE_CLI_USAGE_STARTUP_DELAY_SECONDS = 2.0
CLAUDE_CLI_USAGE_COMMAND_RETRY_SECONDS = 1.5
CLAUDE_CLI_USAGE_ENTER_INTERVAL_SECONDS = 0.8
CLAUDE_CLI_USAGE_SOURCE_DETAIL = "claude-cli-usage"
CLAUDE_DIRECT_MIN_INTERVAL = timedelta(minutes=5)
CLAUDE_DIRECT_STALE_REUSE_WINDOW = timedelta(minutes=30)
_CODEX_APPSERVER_CACHE: dict[str, AccountStatus] | None = None
_CODEX_APPSERVER_USAGE_CACHE: dict[str, CodexProviderUsageRead] | None = None
_CLAUDE_CLI_USAGE_REFRESH_STATE = threading.local()
_CLAUDE_CLI_USAGE_CACHE_STATE = threading.local()
_ANTIGRAVITY_DIRECT_CACHE: AccountStatus | None = None


@dataclass
class _AntigravityProcessInfo:
    pid: int
    csrf_token: str
    extension_port: int | None


class _AntigravityProbeError(Exception):
    """Expected Antigravity local probe failure."""


def _claude_cli_usage_refresh_allowed() -> bool:
    return bool(getattr(_CLAUDE_CLI_USAGE_REFRESH_STATE, "allowed", False))


def _claude_cli_usage_cache() -> AccountStatus | None:
    cached = getattr(_CLAUDE_CLI_USAGE_CACHE_STATE, "status", None)
    return cached if isinstance(cached, AccountStatus) else None


def _set_claude_cli_usage_cache(status: AccountStatus | None) -> None:
    if status is None:
        if hasattr(_CLAUDE_CLI_USAGE_CACHE_STATE, "status"):
            delattr(_CLAUDE_CLI_USAGE_CACHE_STATE, "status")
        return
    _CLAUDE_CLI_USAGE_CACHE_STATE.status = status


@contextmanager
def polling_pass_cache() -> Iterator[None]:
    """Reuse expensive provider reads inside a single polling pass."""
    global _CODEX_APPSERVER_CACHE
    global _CODEX_APPSERVER_USAGE_CACHE
    global _ANTIGRAVITY_DIRECT_CACHE
    previous_appserver_cache = _CODEX_APPSERVER_CACHE
    previous_appserver_usage_cache = _CODEX_APPSERVER_USAGE_CACHE
    previous_claude_cache = _claude_cli_usage_cache()
    previous_antigravity_cache = _ANTIGRAVITY_DIRECT_CACHE
    _CODEX_APPSERVER_CACHE = {}
    _CODEX_APPSERVER_USAGE_CACHE = {}
    _ANTIGRAVITY_DIRECT_CACHE = None
    if not _claude_cli_usage_refresh_allowed():
        _set_claude_cli_usage_cache(None)
    try:
        with codexbar_json_cache():
            yield
    finally:
        _CODEX_APPSERVER_CACHE = previous_appserver_cache
        _CODEX_APPSERVER_USAGE_CACHE = previous_appserver_usage_cache
        _set_claude_cli_usage_cache(previous_claude_cache)
        _ANTIGRAVITY_DIRECT_CACHE = previous_antigravity_cache


@contextmanager
def claude_cli_usage_refresh_allowed() -> Iterator[None]:
    """Allow one polling context to spend the low-but-nonzero Claude /usage cost."""
    previous = _claude_cli_usage_refresh_allowed()
    previous_cache = _claude_cli_usage_cache()
    _CLAUDE_CLI_USAGE_REFRESH_STATE.allowed = True
    _set_claude_cli_usage_cache(None)
    try:
        yield
    finally:
        _CLAUDE_CLI_USAGE_REFRESH_STATE.allowed = previous
        _set_claude_cli_usage_cache(previous_cache)


def fetch_status(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
    claude_probe_context: ClaudeProbeContext | None = None,
) -> AccountStatus:
    """Fetch the current status for an account from its configured source."""
    try:
        if account.source == DataSource.CODEXBAR_CLI:
            return _ensure_status_metadata(
                _fetch_codexbar_cli(
                    account,
                    codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
                    codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
                ),
                "codexbar-cli",
            )
        if account.source == DataSource.CODEXBAR_HTTP:
            return _ensure_status_metadata(
                _fetch_codexbar_http(
                    account,
                    codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
                    codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
                ),
                "codexbar-http",
            )
        if account.source == DataSource.CODEX_DIRECT:
            return _ensure_status_metadata(
                _fetch_codex_direct(
                    account,
                    codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
                    codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
                ),
                "codex-session-jsonl",
            )
        if account.source == DataSource.CLAUDE_DIRECT:
            return _ensure_status_metadata(
                _fetch_claude_direct(
                    account,
                    codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
                    codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
                    claude_probe_context=claude_probe_context,
                ),
                "claude-config-json",
            )
        if account.source == DataSource.ANTIGRAVITY_CLI:
            return _ensure_status_metadata(
                _fetch_antigravity_cli(account),
                ANTIGRAVITY_CLI_SOURCE_DETAIL,
            )
        if account.source == DataSource.CODEX_SESSION_FILE:
            return _ensure_status_metadata(
                _fetch_codex_session_file(
                    account,
                    codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
                    codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
                ),
                "codex-session-file",
            )
        return _ensure_status_metadata(_fetch_manual(account), "manual")
    except Exception as e:
        return _ensure_status_metadata(
            AccountStatus(
                label=account.label,
                state=AccountState.UNKNOWN,
                error=str(e),
            ),
            account.source.value,
        )


# ---------------------------------------------------------------------------
# Direct provider sources
# ---------------------------------------------------------------------------

def _fetch_codex_direct(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
) -> AccountStatus:
    """Read Codex identity from auth.json and status from direct provider-owned state."""
    if not account.identity_provider_id and not account.identity_email:
        diagnostic = codex_login_status()
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=f"Codex identity not found in auth.json. {diagnostic}",
            source_detail="codex-auth-json",
        )

    fallback_errors: list[AccountStatus] = []
    identity_mismatch = _codex_direct_identity_mismatch_status(account)
    if identity_mismatch is not None:
        if account.codexbar_account and _codex_auth_identity_unavailable(identity_mismatch):
            fallback_errors.append(identity_mismatch)
        else:
            return identity_mismatch

    session_status, session_failure = _fetch_codex_session_jsonl_status(account)
    appserver_status = _fetch_codex_appserver_ratelimits(account)
    if codex_rate_limit_id_for_account(account) != CODEX_DEFAULT_RATE_LIMIT_ID:
        if appserver_status is not None:
            return appserver_status
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=(
                f'Codex provider bucket "{codex_rate_limit_id_for_account(account)}" was not available; '
                "auto-kick is blocked until the provider exposes it again."
            ),
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
            codex_rate_limit_id=codex_rate_limit_id_for_account(account),
            codex_rate_limit_name=account.codex_rate_limit_name,
        )
    if appserver_status is not None:
        if appserver_status.state != AccountState.UNKNOWN:
            if _codex_session_status_should_override_appserver(appserver_status, session_status):
                return session_status
            return appserver_status
        fallback_errors.append(appserver_status)

    if session_status is not None:
        return session_status
    if session_failure is not None:
        fallback_errors.append(session_failure)

    if account.codexbar_account:
        codexbar_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        if codexbar_status is not None and codexbar_status.state != AccountState.UNKNOWN:
            return codexbar_status
        if codexbar_status is not None:
            fallback_errors.append(codexbar_status)

    if fallback_errors:
        return _combine_codex_direct_failures(account.label, fallback_errors)
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error="No Codex direct status source was available.",
        source_detail="codex-session-jsonl",
    )


def _fetch_codex_session_jsonl_status(
    account: AccountConfig,
) -> tuple[AccountStatus | None, AccountStatus | None]:
    sessions_dir = Path(
        account.session_path
        or Path(account.provider_home or Path.home() / ".codex") / "sessions"
    )
    if sessions_dir.exists():
        latest_rate_limit = _find_latest_rate_limit(sessions_dir)
        if latest_rate_limit is not None:
            status = _parse_session_rate_limit(account.label, latest_rate_limit)
            status.source_detail = "codex-session-jsonl"
            status.window_anchor_state = status.window_anchor_state or "anchored"
            return status, None
        return None, AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="No Codex rate limit data found in session files.",
            source_detail="codex-session-jsonl",
        )
    return None, AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=f"No Codex session data found at {sessions_dir}. Use this account to start tracking.",
        source_detail="codex-session-jsonl",
    )


def _codex_session_status_should_override_appserver(
    appserver_status: AccountStatus,
    session_status: AccountStatus | None,
) -> bool:
    if session_status is None or session_status.state == AccountState.UNKNOWN:
        return False
    if _codex_appserver_status_is_clean_unanchored_reset(appserver_status):
        return False
    if _codex_appserver_status_has_clean_session_reset(appserver_status):
        return False
    if not _codex_appserver_status_looks_like_phantom(appserver_status):
        return False
    if _codex_status_max_used_percent(session_status) > _codex_status_max_used_percent(
        appserver_status
    ):
        return True
    return _codex_session_status_has_stronger_session_anchor(appserver_status, session_status)


def _codex_appserver_status_looks_like_phantom(status: AccountStatus) -> bool:
    if status.source_detail != CODEX_PROVIDER_USAGE_SOURCE_DETAIL:
        return False
    if status.state not in {AccountState.FRESH, AccountState.ACTIVE}:
        return False
    weekly_used = status.used_percent or 0.0
    if weekly_used >= 100.0:
        return False
    session_used = status.session_used_percent or 0.0
    if session_used > CODEX_PHANTOM_SESSION_MAX_USED_PERCENT:
        return False
    if status.window_anchor_state == "available_unanchored":
        return True
    weekly_window = status.window_minutes or 0
    if weekly_window < 10080:
        return False
    session_window = status.session_window_minutes or 0
    if session_window != 300:
        return False
    if status.session_resets_in_seconds is None:
        return False
    return status.session_resets_in_seconds >= int(
        session_window * 60 * CODEX_PHANTOM_SESSION_FULL_RESET_RATIO
    )


def _codex_appserver_status_is_clean_unanchored_reset(status: AccountStatus) -> bool:
    if status.source_detail != CODEX_PROVIDER_USAGE_SOURCE_DETAIL:
        return False
    if status.state != AccountState.FRESH:
        return False
    if status.window_anchor_state != "available_unanchored":
        return False
    if status.used_percent != 0.0 or status.session_used_percent != 0.0:
        return False
    session_window = status.session_window_minutes or 0
    if session_window != 300 or status.session_resets_in_seconds is None:
        return False
    full_window_seconds = session_window * 60
    return (
        abs(status.session_resets_in_seconds - full_window_seconds)
        <= CODEX_APPSERVER_UNANCHORED_RESET_TOLERANCE_SECONDS
    )


def _codex_appserver_status_has_clean_session_reset(status: AccountStatus) -> bool:
    if status.source_detail != CODEX_PROVIDER_USAGE_SOURCE_DETAIL:
        return False
    if status.session_used_percent != 0.0:
        return False
    session_window = status.session_window_minutes or 0
    if session_window != 300 or status.session_resets_in_seconds is None:
        return False
    full_window_seconds = session_window * 60
    return (
        abs(status.session_resets_in_seconds - full_window_seconds)
        <= CODEX_APPSERVER_UNANCHORED_RESET_TOLERANCE_SECONDS
    )


def _codex_session_status_has_stronger_session_anchor(
    appserver_status: AccountStatus,
    session_status: AccountStatus,
) -> bool:
    appserver_session_used = appserver_status.session_used_percent or 0.0
    session_used = session_status.session_used_percent or 0.0
    if session_used > CODEX_PHANTOM_SESSION_MAX_USED_PERCENT:
        return True
    if session_used > appserver_session_used:
        return True

    appserver_resets_in = appserver_status.session_resets_in_seconds
    session_resets_in = session_status.session_resets_in_seconds
    if appserver_resets_in is None or session_resets_in is None:
        return False
    return session_resets_in + CODEX_APPSERVER_UNANCHORED_RESET_TOLERANCE_SECONDS < appserver_resets_in


def _codex_status_max_used_percent(status: AccountStatus) -> float:
    return max(
        float(status.used_percent or 0.0),
        float(status.session_used_percent or 0.0),
    )


def _combine_codex_direct_failures(
    label: str,
    failures: list[AccountStatus],
) -> AccountStatus:
    schema_failure = next(
        (
            failure
            for failure in failures
            if failure.error
            and failure.error.startswith("Codex provider usage schema mismatch:")
        ),
        None,
    )
    if schema_failure is not None:
        return schema_failure
    provider_failure = next(
        (failure for failure in failures if failure.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL),
        None,
    )
    if provider_failure is not None:
        return provider_failure
    return failures[-1] if failures else AccountStatus(
        label=label,
        state=AccountState.UNKNOWN,
        error="No Codex direct status source was available.",
        source_detail="codex-session-jsonl",
    )


def _codex_direct_identity_mismatch_status(account: AccountConfig) -> AccountStatus | None:
    codex_home = _codex_home_for_direct_account(account)
    if codex_home is None:
        return None
    identity = read_codex_identity(codex_home)
    if identity is None:
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=(
                "Codex auth identity is unavailable for "
                f"{codex_home}. Open Codex once for this account, then run tk status --refresh."
            ),
            source_detail="codex-auth-json",
        )

    expected_id = account.identity_provider_id
    actual_id = identity.provider_account_id
    if expected_id and actual_id and expected_id != actual_id:
        return _codex_identity_mismatch_status(account, identity, codex_home)

    expected_email = account.identity_email.lower() if account.identity_email else None
    actual_email = identity.email.lower() if identity.email else None
    if expected_email and actual_email and expected_email != actual_email:
        return _codex_identity_mismatch_status(account, identity, codex_home)

    return None


def _codex_auth_identity_unavailable(status: AccountStatus) -> bool:
    return (
        status.source_detail == "codex-auth-json"
        and status.error is not None
        and "Codex auth identity is unavailable" in status.error
    )


def _codex_home_for_direct_account(account: AccountConfig) -> Path | None:
    if account.provider != "codex" or account.source != DataSource.CODEX_DIRECT:
        return None
    if account.provider_home:
        return Path(account.provider_home)
    return None


def _codex_identity_mismatch_status(
    account: AccountConfig,
    actual_identity,
    codex_home: Path,
) -> AccountStatus:
    expected = account.identity_email or account.identity_provider_id or "configured account"
    actual = actual_identity.email or actual_identity.provider_account_id or "current auth identity"
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=(
            "Codex identity mismatch: "
            f"{codex_home} is logged in as {actual}, but TokenKick account "
            f"{account.label} expects {expected}. Status is blocked to avoid account mixing."
        ),
        source_detail="codex-auth-json",
    )


def _fetch_codex_appserver_ratelimits(account: AccountConfig) -> AccountStatus | None:
    codex_home = Path(account.provider_home) if account.provider_home else None
    if codex_home is None:
        return None
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        return None
    cache_key = f"{codex_home}|{codex_rate_limit_id_for_account(account)}"
    if _CODEX_APPSERVER_CACHE is not None and cache_key in _CODEX_APPSERVER_CACHE:
        return _CODEX_APPSERVER_CACHE[cache_key]

    status = _read_codex_appserver_ratelimits_for_account(account, codex_home)
    if _CODEX_APPSERVER_CACHE is not None:
        _CODEX_APPSERVER_CACHE[cache_key] = status
    return status


def _read_codex_provider_usage_cached(codex_home: Path) -> CodexProviderUsageRead:
    cache_key = str(codex_home)
    if _CODEX_APPSERVER_USAGE_CACHE is not None and cache_key in _CODEX_APPSERVER_USAGE_CACHE:
        return _CODEX_APPSERVER_USAGE_CACHE[cache_key]
    usage = read_codex_provider_usage(
        codex_home,
        timeout_seconds=CODEX_PROVIDER_USAGE_TIMEOUT_SECONDS,
    )
    if _CODEX_APPSERVER_USAGE_CACHE is not None:
        _CODEX_APPSERVER_USAGE_CACHE[cache_key] = usage
    return usage


def _read_codex_appserver_ratelimits_for_account(
    account: AccountConfig,
    codex_home: Path | None = None,
) -> AccountStatus:
    rate_limit_id = codex_rate_limit_id_for_account(account)
    if rate_limit_id == CODEX_DEFAULT_RATE_LIMIT_ID and account.codex_rate_limit_name is None:
        return _read_codex_appserver_ratelimits(
            account.label,
            codex_home or Path(account.provider_home or Path.home() / ".codex"),
        )
    return _read_codex_appserver_ratelimits(
        account.label,
        codex_home or Path(account.provider_home or Path.home() / ".codex"),
        rate_limit_id=rate_limit_id,
        rate_limit_name=account.codex_rate_limit_name,
    )


def _read_codex_appserver_ratelimits(
    label: str,
    codex_home: Path,
    *,
    rate_limit_id: str | None = None,
    rate_limit_name: str | None = None,
) -> AccountStatus:
    try:
        usage = _read_codex_provider_usage_cached(codex_home)
    except CodexProviderUsageError as exc:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=str(exc),
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )
    response = usage.response
    if isinstance(response.get("error"), dict):
        message = response["error"].get("message") or response["error"]
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=f"Codex provider usage read failed: {message}",
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )
    return _parse_codex_appserver_ratelimits(
        label,
        response,
        elapsed_ms=usage.elapsed_ms,
        rate_limit_id=rate_limit_id,
        rate_limit_name=rate_limit_name,
    )


def _parse_codex_appserver_ratelimits(
    label: str,
    response: dict,
    *,
    elapsed_ms: int | None = None,
    rate_limit_id: str | None = None,
    rate_limit_name: str | None = None,
) -> AccountStatus:
    resolved_rate_limit_id = (rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip() or CODEX_DEFAULT_RATE_LIMIT_ID
    issue = _codex_appserver_schema_issue(response, rate_limit_id=resolved_rate_limit_id)
    if issue is not None:
        if resolved_rate_limit_id != CODEX_DEFAULT_RATE_LIMIT_ID and issue.startswith("missing Codex"):
            return AccountStatus(
                label=label,
                state=AccountState.UNKNOWN,
                error=(
                    f'{issue}; auto-kick is blocked until the provider exposes it again.'
                ),
                source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                codex_rate_limit_id=resolved_rate_limit_id,
                codex_rate_limit_name=rate_limit_name,
            )
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=(
                "Codex provider usage schema mismatch: "
                f"expected v{CODEX_APPSERVER_RATELIMITS_SCHEMA_VERSION}, got {issue}. "
                "Falling back to CodexBar history."
            ),
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )

    rate_limits = _codex_appserver_rate_limits(response, rate_limit_id=resolved_rate_limit_id)
    assert rate_limits is not None
    selected_rate_limit_id = str(rate_limits.get("limitId") or resolved_rate_limit_id)
    selected_rate_limit_name = (
        str(rate_limits.get("limitName"))
        if isinstance(rate_limits.get("limitName"), str)
        else rate_limit_name
    )
    weekly = rate_limits["secondary"]
    session = rate_limits["primary"]
    now = time.time()
    resets_at = _parse_reset_timestamp(weekly.get("resetsAt"))
    session_resets_at = _parse_reset_timestamp(session.get("resetsAt"))
    used_pct = _to_float(weekly.get("usedPercent"))
    resets_in = _seconds_until_reset(resets_at=resets_at, now=now)
    window_min = int(weekly["windowDurationMins"])
    session_used_pct = _to_float(session.get("usedPercent"))
    session_resets_in = _seconds_until_reset(resets_at=session_resets_at, now=now)
    session_window_min = int(session["windowDurationMins"])
    anchor_state = _codex_appserver_anchor_state(
        used_pct=used_pct,
        resets_in_seconds=resets_in,
        window_minutes=window_min,
    )
    state = _codex_rate_limit_state(
        used_pct=used_pct,
        resets_in_seconds=resets_in,
        window_anchor_state=anchor_state,
    )
    return AccountStatus(
        label=label,
        state=state,
        used_percent=used_pct,
        resets_in_seconds=resets_in,
        resets_at=resets_at,
        window_minutes=window_min,
        session_used_percent=session_used_pct,
        session_resets_in_seconds=session_resets_in,
        session_resets_at=session_resets_at,
        session_window_minutes=session_window_min,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        codex_rate_limit_id=selected_rate_limit_id,
        codex_rate_limit_name=selected_rate_limit_name,
        window_anchor_state=anchor_state,
        error=(
            f"Codex provider usage read completed in {elapsed_ms}ms."
            if elapsed_ms is not None and elapsed_ms > CODEX_PROVIDER_USAGE_TIMEOUT_SECONDS * 1000
            else None
        ),
    )


def _codex_appserver_rate_limits(
    response: dict,
    *,
    rate_limit_id: str | None = None,
) -> dict | None:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    selected_rate_limit_id = (rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip() or CODEX_DEFAULT_RATE_LIMIT_ID
    by_limit = result.get("rateLimitsByLimitId")
    if isinstance(by_limit, dict):
        if selected_rate_limit_id in by_limit:
            value = by_limit[selected_rate_limit_id]
            if isinstance(value, dict) and _codex_appserver_rate_limit_issue(value) is None:
                return value
            return None
        if selected_rate_limit_id != CODEX_DEFAULT_RATE_LIMIT_ID:
            return None
        candidates = []
        for key, value in by_limit.items():
            if not isinstance(value, dict):
                continue
            limit_id = str(value.get("limitId") or key)
            if _codex_appserver_rate_limit_issue(value) is None:
                candidates.append((limit_id, value))
        if candidates:
            return max(candidates, key=lambda item: _codex_appserver_rate_limit_key(*item))[1]
    rate_limits = result.get("rateLimits")
    if isinstance(rate_limits, dict) and _codex_appserver_rate_limit_issue(rate_limits) is None:
        return rate_limits
    return None


def codex_appserver_bucket_metadata(codex_home: Path) -> list[dict[str, str | None]]:
    """Return valid Codex app-server quota buckets without exposing provider payloads."""
    try:
        usage = _read_codex_provider_usage_cached(codex_home)
    except CodexProviderUsageError:
        return []
    return _codex_appserver_bucket_metadata_from_response(usage.response)


def _codex_appserver_bucket_metadata_from_response(response: dict) -> list[dict[str, str | None]]:
    result = response.get("result") if isinstance(response, dict) else None
    by_limit = result.get("rateLimitsByLimitId") if isinstance(result, dict) else None
    if not isinstance(by_limit, dict):
        rate_limits = result.get("rateLimits") if isinstance(result, dict) else None
        if isinstance(rate_limits, dict) and _codex_appserver_rate_limit_issue(rate_limits) is None:
            return [
                {
                    "key": CODEX_DEFAULT_RATE_LIMIT_ID,
                    "limit_id": str(rate_limits.get("limitId") or CODEX_DEFAULT_RATE_LIMIT_ID),
                    "limit_name": (
                        rate_limits.get("limitName")
                        if isinstance(rate_limits.get("limitName"), str)
                        else None
                    ),
                    "display_name": _codex_appserver_bucket_display_name(rate_limits),
                }
            ]
        return []

    buckets: list[dict[str, str | None]] = []
    for key, value in by_limit.items():
        if not isinstance(value, dict) or _codex_appserver_rate_limit_issue(value) is not None:
            continue
        limit_id = str(value.get("limitId") or key)
        limit_name = value.get("limitName") if isinstance(value.get("limitName"), str) else None
        buckets.append(
            {
                "key": str(key),
                "limit_id": limit_id,
                "limit_name": limit_name,
                "display_name": _codex_appserver_bucket_display_name(value),
            }
        )
    return buckets


def _codex_appserver_bucket_display_name(bucket: dict | None) -> str:
    if not isinstance(bucket, dict):
        return "-"
    limit_id = bucket.get("limitId") or bucket.get("limit_id") or bucket.get("key")
    limit_name = bucket.get("limitName") or bucket.get("limit_name")
    if limit_id == CODEX_DEFAULT_RATE_LIMIT_ID:
        return "main/default Codex quota"
    if limit_id == CODEX_SPARK_RATE_LIMIT_ID:
        return "GPT-5.3-Codex-Spark quota"
    if isinstance(limit_name, str) and limit_name.strip():
        return limit_name.strip()
    if isinstance(limit_id, str) and limit_id:
        return limit_id
    return "-"


def codex_appserver_spark_bucket(metadata: list[dict[str, str | None]]) -> dict[str, str | None] | None:
    for bucket in metadata:
        limit_id = str(bucket.get("limit_id") or bucket.get("key") or "")
        display = " ".join(
            str(value)
            for value in [
                bucket.get("limit_name"),
                bucket.get("display_name"),
            ]
            if value
        ).lower()
        if limit_id == CODEX_SPARK_RATE_LIMIT_ID or "codex-spark" in display:
            return bucket
    return None


def _codex_appserver_rate_limit_key(limit_id: str, rate_limits: dict) -> tuple[bool, float, float, int]:
    secondary = rate_limits.get("secondary") if isinstance(rate_limits.get("secondary"), dict) else {}
    primary = rate_limits.get("primary") if isinstance(rate_limits.get("primary"), dict) else {}
    used_pct = _float_or_zero(secondary.get("usedPercent"))
    session_used_pct = _float_or_zero(primary.get("usedPercent"))
    active = used_pct > 0.0 or session_used_pct > 0.0
    generic = 1 if limit_id == "codex" else 0
    return (active, used_pct, session_used_pct, generic)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _codex_appserver_schema_issue(
    response: dict,
    *,
    rate_limit_id: str | None = None,
) -> str | None:
    if response.get("id") != 2:
        return f"unexpected id {response.get('id')!r}"
    selected_rate_limit_id = (rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip() or CODEX_DEFAULT_RATE_LIMIT_ID
    rate_limits = _codex_appserver_rate_limits(response, rate_limit_id=selected_rate_limit_id)
    if rate_limits is None:
        if selected_rate_limit_id != CODEX_DEFAULT_RATE_LIMIT_ID:
            return f'missing Codex provider bucket "{selected_rate_limit_id}"'
        return "missing valid result.rateLimitsByLimitId entry or result.rateLimits"
    return _codex_appserver_rate_limit_issue(rate_limits)


def _codex_appserver_rate_limit_issue(rate_limits: dict) -> str | None:
    for path in [
        ("primary", "usedPercent"),
        ("primary", "windowDurationMins"),
        ("primary", "resetsAt"),
        ("secondary", "usedPercent"),
        ("secondary", "windowDurationMins"),
        ("secondary", "resetsAt"),
    ]:
        current: Any = rate_limits
        for part in path:
            if not isinstance(current, dict) or part not in current:
                return "missing result.rateLimitsByLimitId entry." + ".".join(path)
            current = current[part]
        if path[-1] == "usedPercent":
            try:
                float(current)
            except (TypeError, ValueError):
                return "invalid result.rateLimitsByLimitId entry." + ".".join(path)
        if path[-1] == "windowDurationMins":
            try:
                int(current)
            except (TypeError, ValueError):
                return "invalid result.rateLimitsByLimitId entry." + ".".join(path)
        if path[-1] == "resetsAt" and _parse_reset_timestamp(current) is None:
            return "invalid result.rateLimitsByLimitId entry." + ".".join(path)
    return None


def _codex_appserver_anchor_state(
    *,
    used_pct: float | None,
    resets_in_seconds: int | None,
    window_minutes: int | None,
) -> str:
    if used_pct is None:
        return "unknown"
    if used_pct > 0.0:
        return "anchored"
    if resets_in_seconds is None or window_minutes is None:
        return "unknown"
    full_window_seconds = window_minutes * 60
    if abs(resets_in_seconds - full_window_seconds) <= CODEX_APPSERVER_UNANCHORED_RESET_TOLERANCE_SECONDS:
        return "available_unanchored"
    return "anchored"


def _codex_rate_limit_state(
    *,
    used_pct: float | None,
    resets_in_seconds: int | None,
    window_anchor_state: str,
) -> AccountState:
    if used_pct == 0.0 and window_anchor_state == "anchored":
        return AccountState.ACTIVE
    return _determine_state(used_pct, resets_in_seconds)


def _fetch_claude_direct(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
    claude_probe_context: ClaudeProbeContext | None = None,
) -> AccountStatus:
    """Read Claude identity directly and quota via guarded Claude CLI /usage."""
    claude_probe_context = claude_probe_context or ClaudeProbeContext()
    usage_failure: AccountStatus | None = None
    if _claude_cli_usage_refresh_allowed():
        gate_status = _claude_cli_usage_gate_status(account, claude_probe_context)
        if gate_status is not None:
            if gate_status.state != AccountState.UNKNOWN:
                return gate_status
            usage_failure = gate_status
        else:
            usage = _fetch_claude_cli_usage(account, claude_probe_context)
            if usage.state != AccountState.UNKNOWN:
                return usage
            usage_failure = usage

    fallback_account = AccountConfig(
        label=account.label,
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="claude",
    )
    try:
        fallback = _fetch_codexbar_cli(
            fallback_account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
    except Exception as exc:
        fallback = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=f"Claude CodexBar fallback failed: {exc}",
            source_detail="claude-codexbar-fallback",
        )
    if fallback.state != AccountState.UNKNOWN:
        fallback.source_detail = "claude-codexbar-fallback"
        return fallback

    if account.status_probe_enabled:
        return _fetch_claude_probe(account)

    if usage_failure is not None:
        return replace(
            usage_failure,
            error=_claude_direct_unavailable_message(fallback.error, usage_failure.error),
        )

    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=_claude_direct_unavailable_message(fallback.error, None),
        source_detail="claude-config-json",
        stale=fallback.stale,
        stale_seconds=fallback.stale_seconds,
    )


def _claude_cli_usage_gate_status(
    account: AccountConfig,
    context: ClaudeProbeContext,
) -> AccountStatus | None:
    if not context.direct_usage_enabled or not account.direct_usage_enabled:
        context.last_direct_probe_error = ClaudeProbeError(
            ClaudeProbeErrorCategory.DISABLED,
            "Claude direct /usage is disabled.",
        )
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Claude direct /usage is disabled.",
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )

    if (
        isinstance(context.last_direct_probe_error, ClaudeProbeError)
        and context.last_direct_probe_error.category == ClaudeProbeErrorCategory.DISABLED
    ):
        context.last_direct_probe_error = None
        context.last_direct_probe_at = None

    identity_error = _claude_identity_probe_error(account)
    if identity_error is not None:
        context.last_direct_probe_error = identity_error
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=identity_error.message,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )

    last_probe = _parse_claude_probe_time(context.last_direct_probe_at)
    if last_probe is None:
        return None
    age = datetime.now(timezone.utc) - last_probe
    if age >= CLAUDE_DIRECT_MIN_INTERVAL:
        return None

    if context.last_direct_probe_error is None and _claude_cached_direct_status_usable(
        context.last_direct_success_status
    ):
        return replace(context.last_direct_success_status, label=account.label)

    # A recent failed direct probe should not hammer /usage again. If the last
    # successful direct reading is still recent enough, prefer it over CodexBar;
    # beyond 30 minutes Claude session usage can drift enough that fallback data
    # is less misleading than a trusted but stale direct result.
    if _claude_recent_success_available(context):
        return replace(context.last_direct_success_status, label=account.label)

    error = context.last_direct_probe_error
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=error.message if error else "Claude direct /usage skipped by minimum interval.",
        source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
    )


def _claude_identity_probe_error(account: AccountConfig) -> ClaudeProbeError | None:
    active = read_claude_identity()
    if active is None:
        return ClaudeProbeError(
            ClaudeProbeErrorCategory.IDENTITY_UNREADABLE,
            "Claude CLI identity is not readable from ~/.claude.json.",
        )

    has_saved_id = bool(account.identity_provider_id)
    has_active_id = bool(active.provider_account_id)
    if has_saved_id and has_active_id:
        if account.identity_provider_id != active.provider_account_id:
            return ClaudeProbeError(
                ClaudeProbeErrorCategory.IDENTITY_MISMATCH,
                "Active Claude CLI account differs from this TokenKick account.",
            )
        if (
            account.identity_org_id
            and active.organization_id
            and account.identity_org_id != active.organization_id
        ):
            return ClaudeProbeError(
                ClaudeProbeErrorCategory.IDENTITY_MISMATCH,
                "Active Claude CLI organization differs from this TokenKick account.",
            )
        return None

    if account.identity_email and active.email:
        if account.identity_email.lower() != active.email.lower():
            return ClaudeProbeError(
                ClaudeProbeErrorCategory.IDENTITY_MISMATCH,
                "Active Claude CLI email differs from this TokenKick account.",
            )
        return None

    return ClaudeProbeError(
        ClaudeProbeErrorCategory.IDENTITY_UNREADABLE,
        "Not enough Claude identity metadata to validate direct /usage.",
    )


def _parse_claude_probe_time(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _claude_cached_direct_status_usable(status: AccountStatus | None) -> bool:
    return (
        isinstance(status, AccountStatus)
        and status.state != AccountState.UNKNOWN
        and status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
        and not _claude_direct_status_missing_weekly_reset(status)
    )


def _claude_direct_status_missing_weekly_reset(status: AccountStatus) -> bool:
    return (
        status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
        and status.window_minutes == 10080
        and status.used_percent is not None
        and status.resets_in_seconds is None
        and status.resets_at is None
    )


def _claude_recent_success_available(context: ClaudeProbeContext) -> bool:
    if not _claude_cached_direct_status_usable(context.last_direct_success_status):
        return False
    success_at = _parse_claude_probe_time(context.last_direct_success_at)
    if success_at is None:
        return False
    return datetime.now(timezone.utc) - success_at < CLAUDE_DIRECT_STALE_REUSE_WINDOW


def _record_claude_probe_success(context: ClaudeProbeContext, status: AccountStatus) -> None:
    observed_at = _status_observed_at()
    status.observed_at = status.observed_at or observed_at
    status.source_detail = CLAUDE_CLI_USAGE_SOURCE_DETAIL
    context.last_direct_probe_at = observed_at
    context.last_direct_probe_error = None
    context.last_direct_success_at = observed_at
    context.last_direct_success_status = replace(status)


def _record_claude_probe_error(
    context: ClaudeProbeContext,
    category: ClaudeProbeErrorCategory,
    message: str,
    *,
    raw: str | None = None,
) -> None:
    context.last_direct_probe_at = _status_observed_at()
    context.last_direct_probe_error = ClaudeProbeError(
        category=category,
        message=message,
        raw=_redact_claude_usage_raw(raw) if raw else None,
    )


def _claude_probe_error_category_from_message(message: str) -> ClaudeProbeErrorCategory:
    lower = message.lower()
    compact = "".join(lower.split())
    if "not found" in lower or "no such file" in lower:
        return ClaudeProbeErrorCategory.BINARY_MISSING
    if "token_expired" in lower or "token has expired" in lower or "auth" in lower or "login" in lower:
        return ClaudeProbeErrorCategory.NOT_AUTHENTICATED
    if "rate_limit" in lower or "rate limited" in lower or "ratelimited" in compact:
        return ClaudeProbeErrorCategory.RATE_LIMITED
    if "parse" in lower or "missing current session" in lower or "current session" in lower:
        return ClaudeProbeErrorCategory.PARSE_FAILED
    if "timed out" in lower or "timeout" in lower:
        return ClaudeProbeErrorCategory.TIMEOUT
    return ClaudeProbeErrorCategory.PROVIDER_ERROR


def _redact_claude_usage_raw(raw: str) -> str:
    text = _strip_ansi(raw)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(
        r"\b(?:Pro|Max|Team|Enterprise|Free|Plus|Claude\s+Code)\b",
        "[plan]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:org|organization|account|user)[_-]?[A-Za-z0-9-]{6,}\b", "[id]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{13,}\b", "[id]", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1000]


def _fetch_claude_cli_usage(
    account: AccountConfig,
    claude_probe_context: ClaudeProbeContext | None = None,
) -> AccountStatus:
    """Fetch Claude Code usage through the interactive /usage panel."""
    claude_probe_context = claude_probe_context or ClaudeProbeContext()
    use_cache = _claude_cli_usage_refresh_allowed() or codexbar_json_cache_active()
    cached_status = _claude_cli_usage_cache()
    if use_cache and cached_status is not None:
        cached = replace(cached_status, label=account.label)
        if cached.state == AccountState.UNKNOWN:
            _record_claude_probe_error(
                claude_probe_context,
                _claude_probe_error_category_from_message(cached.error or ""),
                cached.error or "Claude CLI /usage failed.",
            )
        else:
            _record_claude_probe_success(claude_probe_context, cached)
        return cached

    binary = shutil.which("claude")
    if binary is None:
        _record_claude_probe_error(
            claude_probe_context,
            ClaudeProbeErrorCategory.BINARY_MISSING,
            "Claude CLI not found.",
        )
        status = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Claude CLI not found.",
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )
        if use_cache:
            _set_claude_cli_usage_cache(status)
        return status

    auth_status = claude_auth_status(binary)
    if auth_status is not None and auth_status.logged_in is False:
        message = auth_status.message or (
            "Claude CLI is not logged in. Run `claude auth login --claudeai` as "
            "the same user that runs TokenKick, then run `tk status --refresh`."
        )
        _record_claude_probe_error(
            claude_probe_context,
            ClaudeProbeErrorCategory.NOT_AUTHENTICATED,
            message,
        )
        status = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=message,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )
        if use_cache:
            _set_claude_cli_usage_cache(status)
        return status

    try:
        raw = _capture_claude_usage(binary)
        status = _parse_claude_usage_output(account.label, raw)
    except TimeoutError as exc:
        message = str(exc) or f"Claude CLI /usage timed out after {CLAUDE_CLI_USAGE_TOTAL_TIMEOUT_SECONDS:g}s."
        _record_claude_probe_error(
            claude_probe_context,
            ClaudeProbeErrorCategory.TIMEOUT,
            message,
            raw=exc.raw if isinstance(exc, _ClaudeUsageCaptureTimeout) else None,
        )
        status = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=message,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )
    except RuntimeError as exc:
        _record_claude_probe_error(
            claude_probe_context,
            _claude_probe_error_category_from_message(str(exc)),
            str(exc),
        )
        status = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=str(exc),
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )
    else:
        if status.state == AccountState.UNKNOWN:
            _record_claude_probe_error(
                claude_probe_context,
                _claude_probe_error_category_from_message(status.error or ""),
                status.error or "Claude CLI /usage failed.",
                raw=raw,
            )
        else:
            _record_claude_probe_success(claude_probe_context, status)
    if use_cache:
        _set_claude_cli_usage_cache(status)
    return status


def _capture_claude_usage(binary: str) -> str:
    if os.name == "nt" or pty is None or termios is None:
        return _capture_claude_usage_pipe(binary)
    return _capture_claude_usage_pty(binary)


class _ClaudeUsageCaptureTimeout(TimeoutError):
    def __init__(self, raw: str, message: str = "Claude CLI /usage timed out."):
        super().__init__(message)
        self.raw = raw


def _capture_claude_usage_pipe(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--allowed-tools", ""],
            input="/usage\n/exit\n",
            capture_output=True,
            text=True,
            timeout=CLAUDE_CLI_USAGE_TIMEOUT_SECONDS,
            cwd=_claude_probe_cwd(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Claude CLI /usage timed out after {CLAUDE_CLI_USAGE_TIMEOUT_SECONDS:g}s.") from exc
    return (result.stdout or "") + "\n" + (result.stderr or "")


def _capture_claude_usage_pty(binary: str) -> str:
    try:
        raw = _capture_claude_usage_pty_once(binary, timeout_seconds=CLAUDE_CLI_USAGE_TIMEOUT_SECONDS)
    except _ClaudeUsageCaptureTimeout as exc:
        raw = exc.raw
        if _claude_usage_capture_has_complete_window_data(_strip_ansi(raw)):
            return raw
        return _capture_claude_usage_pty_retry(binary)

    clean = _strip_ansi(raw)
    if _claude_usage_capture_has_complete_window_data(clean):
        return raw
    if _claude_usage_output_still_loading(clean) or not _claude_usage_output_looks_relevant(clean):
        return _capture_claude_usage_pty_retry(binary)
    return raw


def _capture_claude_usage_pty_retry(binary: str) -> str:
    try:
        return _capture_claude_usage_pty_once(
            binary,
            timeout_seconds=CLAUDE_CLI_USAGE_RETRY_TIMEOUT_SECONDS,
        )
    except _ClaudeUsageCaptureTimeout as exc:
        if _claude_usage_capture_has_complete_window_data(_strip_ansi(exc.raw)):
            return exc.raw
        raise _ClaudeUsageCaptureTimeout(
            exc.raw,
            "Claude CLI /usage timed out waiting for the usage panel "
            f"after loading-screen retry ({CLAUDE_CLI_USAGE_TOTAL_TIMEOUT_SECONDS:g}s).",
        ) from exc


def _capture_claude_usage_pty_once(binary: str, *, timeout_seconds: float) -> str:
    assert pty is not None
    assert termios is not None
    master_fd, slave_fd = pty.openpty()
    output = bytearray()
    process: subprocess.Popen[bytes] | None = None
    start = time.monotonic()
    sent_usage_count = 0
    last_usage_send = start
    welcome_dismissed = False
    last_welcome_dismiss = start
    last_enter = start
    try:
        try:
            import fcntl

            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        except OSError:
            pass
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        process = subprocess.Popen(
            [binary, "--allowed-tools", ""],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=_claude_probe_cwd(),
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        selector = selectors.DefaultSelector()
        selector.register(master_fd, selectors.EVENT_READ)
        cursor_query = b"\x1b[6n"
        prompt_sends = {
            "show plan usage limits": b"\r",
            "show plan": b"\r",
            "press enter to continue": b"\r",
        }
        triggered_prompts: set[str] = set()
        while time.monotonic() - start < timeout_seconds:
            for _key, _events in selector.select(timeout=0.1):
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                output.extend(chunk)
                if cursor_query in chunk or cursor_query in bytes(output[-32:]):
                    _write_pty(master_fd, b"\x1b[1;1R")

            clean = _strip_ansi(output.decode("utf-8", "replace"))
            usage_error = _extract_claude_usage_error(clean)
            if usage_error:
                raise RuntimeError(usage_error)
            if _claude_usage_capture_has_complete_window_data(clean):
                time.sleep(0.25)
                return output.decode("utf-8", "replace")
            if _claude_usage_waiting_for_trust(clean):
                raise RuntimeError(
                    "Claude CLI is waiting for a folder trust prompt. Open `claude` once "
                    "in the probe directory and approve the prompt, then retry."
                )
            normalized = _normalize_claude_usage_label(clean)
            for prompt, keys in prompt_sends.items():
                prompt_key = _normalize_claude_usage_label(prompt)
                if prompt_key in normalized and prompt_key not in triggered_prompts:
                    _write_pty(master_fd, keys)
                    triggered_prompts.add(prompt_key)

            now = time.monotonic()
            next_input = _claude_usage_next_pty_input(
                clean,
                now=now,
                start=start,
                sent_usage_count=sent_usage_count,
                last_usage_send=last_usage_send,
                welcome_dismissed=welcome_dismissed,
                last_welcome_dismiss=last_welcome_dismiss,
                last_enter=last_enter,
            )
            if next_input is not None:
                _write_pty_input(master_fd, next_input)
                if b"/usage" in next_input:
                    sent_usage_count += 1
                    last_usage_send = now
                if _claude_usage_input_dismisses_welcome(next_input):
                    welcome_dismissed = True
                    last_welcome_dismiss = now
                last_enter = now

            if process.poll() is not None:
                break
        raise _ClaudeUsageCaptureTimeout(output.decode("utf-8", "replace"))
    finally:
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        if slave_fd >= 0:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass


def _claude_usage_next_pty_input(
    text: str,
    *,
    now: float,
    start: float,
    sent_usage_count: int,
    last_usage_send: float,
    welcome_dismissed: bool,
    last_welcome_dismiss: float,
    last_enter: float,
) -> bytes | None:
    if now - start < CLAUDE_CLI_USAGE_STARTUP_DELAY_SECONDS:
        return None
    if _claude_usage_output_is_welcome_screen(text):
        if not welcome_dismissed:
            return b"\r"
        if now - last_welcome_dismiss < CLAUDE_CLI_USAGE_ENTER_INTERVAL_SECONDS:
            return None
    if not _claude_usage_output_looks_relevant(text):
        if (
            sent_usage_count == 0
            or now - last_usage_send >= CLAUDE_CLI_USAGE_COMMAND_RETRY_SECONDS
        ):
            return b"/usage\r"
        return None
    if sent_usage_count > 0 and now - last_enter >= CLAUDE_CLI_USAGE_ENTER_INTERVAL_SECONDS:
        return b"\r"
    return None


def _claude_usage_output_is_welcome_screen(text: str) -> bool:
    compact = _normalize_claude_usage_label(_strip_ansi(text))
    return (
        "claudecode" in compact
        and (
            "welcomeback" in compact
            or "tipsforgettingstarted" in compact
            or "whatsnew" in compact
            or "forshortcuts" in compact
        )
        and "currentsession" not in compact
        and "loadingusagedata" not in compact
    )


def _claude_usage_input_dismisses_welcome(data: bytes) -> bool:
    return data in {b"\r", b"\n", b"\x1b"}


def _write_pty_input(fd: int, data: bytes) -> None:
    if b"/usage" not in data:
        _write_pty(fd, data)
        return
    for byte in data:
        _write_pty(fd, bytes([byte]))
        time.sleep(0.02)


def _write_pty(fd: int, data: bytes) -> None:
    try:
        os.write(fd, data)
    except OSError:
        pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _claude_probe_cwd() -> str:
    return str(ensure_claude_probe_ready(CONFIG_DIR))


def _parse_claude_usage_output(
    label: str,
    text: str,
    *,
    now: float | None = None,
) -> AccountStatus:
    clean = _trim_to_latest_claude_usage_panel(_strip_ansi(text))
    usage_error = _extract_claude_usage_error(clean)
    if usage_error:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=usage_error,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )

    session = _extract_claude_usage_window(clean, "Current session", now=now)
    weekly = _extract_claude_usage_window(clean, "Current week", now=now)
    if session["used_percent"] is None:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="Could not parse Claude /usage output: missing Current session usage.",
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )
    if weekly["used_percent"] is not None and weekly["resets_in_seconds"] is None:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="Could not parse Claude /usage output: missing Current week reset.",
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        )

    used_pct = weekly["used_percent"] if weekly["used_percent"] is not None else session["used_percent"]
    resets_in = (
        weekly["resets_in_seconds"]
        if weekly["used_percent"] is not None
        else session["resets_in_seconds"]
    )
    window_minutes = 10080 if weekly["used_percent"] is not None else 300
    state = _determine_state(used_pct, resets_in)
    if (
        state == AccountState.FRESH
        and weekly["used_percent"] is not None
        and session["resets_in_seconds"] is not None
        and session["resets_in_seconds"] > 0
    ):
        state = AccountState.ACTIVE
    return AccountStatus(
        label=label,
        state=state,
        used_percent=used_pct,
        resets_in_seconds=resets_in,
        window_minutes=window_minutes,
        session_used_percent=session["used_percent"],
        session_resets_in_seconds=session["resets_in_seconds"],
        session_window_minutes=300,
        source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
    )


def _extract_claude_usage_window(
    text: str,
    label: str,
    *,
    now: float | None,
) -> dict[str, float | int | None]:
    lines = text.replace("\r", "\n").splitlines()
    for index, line in enumerate(lines):
        if not _claude_usage_line_matches_label(line, label):
            continue
        window = lines[index : index + 14]
        used_percent: float | None = None
        resets_in_seconds: int | None = None
        for candidate_index, candidate in enumerate(window):
            if (
                candidate_index > 0
                and _claude_usage_line_is_current_header(candidate)
                and not _claude_usage_line_matches_label(candidate, label)
            ):
                break
            if used_percent is None:
                used_percent = _claude_percent_used_from_line(candidate)
            if resets_in_seconds is None:
                resets_in_seconds = _claude_reset_seconds_from_line(candidate, now=now)
        return {"used_percent": used_percent, "resets_in_seconds": resets_in_seconds}
    return {"used_percent": None, "resets_in_seconds": None}


def _strip_ansi(text: str) -> str:
    ansi_pattern = re.compile(
        r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
    )
    control_pattern = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    return control_pattern.sub("", ansi_pattern.sub("", text))


def _trim_to_latest_claude_usage_panel(text: str) -> str:
    for marker in ("Settings:", "Current session"):
        index = text.lower().rfind(marker.lower())
        if index >= 0:
            tail = text[index:]
            if "current session" in tail.lower():
                return tail
    return text


def _claude_percent_used_from_line(line: str) -> float | None:
    match = re.search(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%", line)
    if match is None:
        return None
    percent = max(0.0, min(100.0, float(match.group(1))))
    lower = line.lower()
    if any(word in lower for word in ("left", "remaining", "available")):
        return 100.0 - percent
    if any(word in lower for word in ("used", "spent", "consumed")):
        return percent
    return None


def _claude_reset_seconds_from_line(line: str, *, now: float | None) -> int | None:
    if "reset" not in line.lower():
        return None
    current = time.time() if now is None else now
    lower = line.lower()
    relative = re.search(
        r"in\s+(?:(\d+)\s*d(?:ays?)?)?\s*(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?",
        lower,
    )
    if relative and (relative.group(1) or relative.group(2) or relative.group(3)):
        days = int(relative.group(1) or 0)
        hours = int(relative.group(2) or 0)
        minutes = int(relative.group(3) or 0)
        return ((days * 24 + hours) * 60 + minutes) * 60

    raw = re.sub(r"(?i)^.*?resets?\s*(?:at|on|:)?\s*", "", line).strip()
    raw = re.sub(r"\([^)]*\)", "", raw).strip()
    raw = re.sub(r"\s+", " ", raw)
    base = datetime.fromtimestamp(current).astimezone()
    today = base.date()
    if raw.lower().startswith("tomorrow"):
        parsed = _parse_claude_time(raw[len("tomorrow") :].strip(" ,at"))
        if parsed is None:
            return None
        target = datetime.combine(today + timedelta(days=1), parsed, tzinfo=base.tzinfo)
        return max(0, int(target.timestamp() - current))

    compact_date = re.match(r"(?i)^([a-z]{3,9})(\d{1,2})(?:at)?(.+)$", raw)
    if compact_date:
        raw = f"{compact_date.group(1)} {compact_date.group(2)} {compact_date.group(3)}"
    raw = re.sub(r"(?i)(\d)(am|pm)$", r"\1 \2", raw)

    for fmt in (
        "%b %d, %I:%M %p",
        "%b %d %I:%M %p",
        "%b %d, %I %p",
        "%b %d %I %p",
        "%B %d, %I:%M %p",
        "%B %d %I:%M %p",
        "%B %d, %I %p",
        "%B %d %I %p",
        "%b %d, %H:%M",
        "%b %d %H:%M",
        "%B %d, %H:%M",
        "%B %d %H:%M",
    ):
        try:
            parsed_dt = datetime.strptime(raw, fmt).replace(year=base.year, tzinfo=base.tzinfo)
        except ValueError:
            continue
        if parsed_dt.timestamp() < current:
            parsed_dt = parsed_dt.replace(year=base.year + 1)
        return max(0, int(parsed_dt.timestamp() - current))

    parsed_time = _parse_claude_time(raw)
    if parsed_time is None:
        return None
    target = datetime.combine(today, parsed_time, tzinfo=base.tzinfo)
    if target.timestamp() < current:
        target += timedelta(days=1)
    return max(0, int(target.timestamp() - current))


def _parse_claude_time(raw: str) -> Any | None:
    raw = raw.strip()
    raw = re.sub(r"(?i)^at\s+", "", raw)
    raw = re.sub(r"(?i)(\d)(am|pm)$", r"\1 \2", raw)
    for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%H"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _normalize_claude_usage_label(text: str) -> str:
    return "".join(char for char in text.lower() if char.isalnum())


def _claude_usage_line_matches_label(line: str, label: str) -> bool:
    compact = _normalize_claude_usage_label(line)
    target = _normalize_claude_usage_label(label)
    if target in compact:
        return True
    # Claude's TUI sometimes paints "Current session" with cursor positioning that
    # strips to "Curret session" in raw PTY captures. Treat that as the same header.
    if target == "currentsession":
        return "curretsession" in compact or (compact.startswith("curret") and "session" in compact)
    if target == "currentweek":
        return "curretweek" in compact or (compact.startswith("curret") and "week" in compact)
    return False


def _claude_usage_line_is_current_header(line: str) -> bool:
    compact = _normalize_claude_usage_label(line)
    return compact.startswith("current") or compact.startswith("curret")


def _claude_usage_output_looks_relevant(text: str) -> bool:
    compact = _normalize_claude_usage_label(_strip_ansi(text))
    return any(
        marker in compact
        for marker in (
            "currentsession",
            "curretsession",
            "currentweek",
            "curretweek",
            "loadingusagedata",
            "failedtoloadusagedata",
            "usagecredits",
        )
    )


def _claude_usage_output_still_loading(text: str) -> bool:
    compact = _normalize_claude_usage_label(_strip_ansi(text))
    return (
        "loadingusagedata" in compact
        and not _claude_usage_capture_has_session_value(text)
        and re.search(r"\d{1,3}(?:\.\d+)?\s*%", text) is None
    )


def _claude_usage_capture_has_session_value(text: str) -> bool:
    if not _claude_usage_line_matches_label(text, "Current session"):
        return False
    return _extract_claude_usage_window(text, "Current session", now=time.time())[
        "used_percent"
    ] is not None


def _claude_usage_capture_has_complete_window_data(text: str) -> bool:
    clean = _strip_ansi(text)
    now = time.time()
    session = _extract_claude_usage_window(clean, "Current session", now=now)
    if session["used_percent"] is None or session["resets_in_seconds"] is None:
        return False

    weekly = _extract_claude_usage_window(clean, "Current week", now=now)
    if weekly["used_percent"] is not None:
        return weekly["resets_in_seconds"] is not None

    compact = _normalize_claude_usage_label(clean)
    return "usagecredits" in compact or "whatscontributingtoyourlimitsusage" in compact


def _claude_usage_waiting_for_trust(text: str) -> bool:
    return "do you trust the files in this folder" in text.lower()


def _extract_claude_usage_error(text: str) -> str | None:
    lower = text.lower()
    compact = "".join(lower.split())
    if "token_expired" in lower or "token has expired" in lower:
        return "Claude CLI token expired. Run `claude auth login --claudeai` to refresh."
    if "authentication_error" in lower or "not authenticated" in lower:
        return "Claude CLI authentication error. Run `claude auth login --claudeai`."
    if "rate_limit_error" in lower or "rate limited" in lower or "ratelimited" in compact:
        return "Claude CLI usage endpoint is rate limited right now. Please try again later."
    if "failed to load usage data" in lower or "failedtoloadusagedata" in compact:
        return "Claude CLI could not load usage data. Open the CLI and retry `/usage`."
    return None


def _fetch_claude_probe(account: AccountConfig) -> AccountStatus:
    success, error = probe_claude_status()
    if success:
        return AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            session_used_percent=1.0,
            session_window_minutes=300,
            error="Claude probe succeeded. This status was produced by an explicit quota-consuming probe.",
            source_detail="claude-probe",
        )
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=error or "Claude probe failed.",
        source_detail="claude-probe",
    )


def _claude_direct_unavailable_message(
    codexbar_error: str | None = None,
    usage_error: str | None = None,
) -> str:
    base = (
        "Claude identity was read from ~/.claude.json, but TokenKick could not read "
        "Claude CLI /usage directly."
    )
    options = (
        "Re-authenticate Claude with `claude auth login --claudeai` as the same user "
        "that runs TokenKick, then run `tk status --refresh`. If you intentionally "
        "avoid direct /usage, install or refresh CodexBar for the v0.4 fallback or "
        "enable the explicit Claude probe for this account (consumes quota)."
    )
    if usage_error and codexbar_error:
        return (
            f"{base} Claude CLI /usage failed: {usage_error} "
            f"CodexBar fallback is unavailable: {codexbar_error} {options}"
        )
    if usage_error:
        return f"{base} Claude CLI /usage failed: {usage_error} {options}"
    if codexbar_error:
        return f"{base} CodexBar fallback is unavailable: {codexbar_error} {options}"
    return f"{base} {options}"


# ---------------------------------------------------------------------------
# Antigravity direct local source
# ---------------------------------------------------------------------------

def _fetch_antigravity_cli(account: AccountConfig) -> AccountStatus:
    """Read Antigravity CLI quota data when the local backend exposes it."""
    status = _fetch_antigravity_direct(account)
    if status.state != AccountState.UNKNOWN:
        identity_error = _antigravity_cli_identity_error(account, status)
        if identity_error is not None:
            return AccountStatus(
                label=account.label,
                state=AccountState.UNKNOWN,
                error=identity_error,
                source_detail=ANTIGRAVITY_CLI_SOURCE_DETAIL,
            )
        if has_complete_antigravity_quota_windows(status):
            return replace(status, source_detail=ANTIGRAVITY_CLI_SOURCE_DETAIL)
        return replace(status, source_detail=status.source_detail or ANTIGRAVITY_CLI_SOURCE_DETAIL)
    detail = (
        "Antigravity CLI account detected, but the installed CLI does not expose a "
        "non-interactive quota command. TokenKick can show Antigravity CLI quota "
        "only when the local Antigravity backend is running and returns named quota windows."
    )
    if status.error:
        detail = f"{detail} Local API probe: {status.error}"
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=detail,
        source_detail=ANTIGRAVITY_CLI_SOURCE_DETAIL,
    )


def _antigravity_cli_identity_error(
    account: AccountConfig,
    status: AccountStatus,
) -> str | None:
    expected = (account.identity_email or "").strip().lower()
    if not expected:
        return None
    observed_raw = getattr(status, "_antigravity_identity_email", None)
    observed = observed_raw.strip().lower() if isinstance(observed_raw, str) else None
    if observed is None:
        return (
            "Antigravity local API returned quota data, but TokenKick could not verify "
            f"that it belongs to CLI account {account.identity_email}."
        )
    if observed != expected:
        return (
            "Antigravity local API identity mismatch: "
            f"CLI account is {account.identity_email}, local API is {observed_raw}."
        )
    return None


def _replace_antigravity_status_label(status: AccountStatus, label: str) -> AccountStatus:
    updated = replace(status, label=label)
    identity_email = getattr(status, "_antigravity_identity_email", None)
    if identity_email is not None:
        setattr(updated, "_antigravity_identity_email", identity_email)
    return updated


def _fetch_antigravity_direct(account: AccountConfig) -> AccountStatus:
    """Read Antigravity quotas from the local language server."""
    global _ANTIGRAVITY_DIRECT_CACHE

    if _ANTIGRAVITY_DIRECT_CACHE is not None:
        return _replace_antigravity_status_label(_ANTIGRAVITY_DIRECT_CACHE, account.label)

    try:
        process_info = _detect_antigravity_process()
        ports = _antigravity_listening_ports(process_info.pid)
        connect_port = _antigravity_find_connect_port(ports, process_info.csrf_token)
        data = _antigravity_request_json(
            "https",
            connect_port,
            "/exa.language_server_pb.LanguageServerService/GetUserStatus",
            _antigravity_default_request_body(),
            process_info.csrf_token,
        )
        try:
            status = _parse_antigravity_user_status(account.label, data)
        except _AntigravityProbeError:
            fallback_data = _antigravity_fetch_command_model_configs(process_info, connect_port)
            status = _parse_antigravity_command_model_configs(account.label, fallback_data)
    except _AntigravityProbeError as exc:
        status = AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=str(exc),
            source_detail=ANTIGRAVITY_SOURCE_DETAIL,
        )

    if codexbar_json_cache_active():
        _ANTIGRAVITY_DIRECT_CACHE = status
    return status


def _detect_antigravity_process() -> _AntigravityProcessInfo:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=ANTIGRAVITY_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise _AntigravityProbeError("ps is not available for Antigravity process detection.") from exc
    except PermissionError as exc:
        raise _AntigravityProbeError(
            "Antigravity process detection was blocked by system permissions."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _AntigravityProbeError("Antigravity process detection timed out.") from exc
    except OSError as exc:
        raise _AntigravityProbeError(f"Antigravity process detection failed: {exc}") from exc

    saw_antigravity_server = False
    for line in result.stdout.splitlines():
        match = parse_process_line(line)
        if match is None:
            continue
        pid, command = match
        if not is_language_server_command(command):
            continue
        if not is_antigravity_language_server(command):
            continue
        saw_antigravity_server = True
        token = _extract_antigravity_flag(command, "--csrf_token")
        if not token:
            continue
        return _AntigravityProcessInfo(
            pid=pid,
            csrf_token=token,
            extension_port=(
                _extract_antigravity_port(command, "--extension_server_port")
                or _extract_antigravity_port(command, "--https_server_port")
            ),
        )

    if saw_antigravity_server:
        raise _AntigravityProbeError("Antigravity language server is running without a CSRF token.")
    raise _AntigravityProbeError("Antigravity language server not detected. Launch Antigravity and retry.")


def _extract_antigravity_flag(command: str, flag: str) -> str | None:
    pattern = rf"{re.escape(flag)}(?:=|\s+)([^\s]+)"
    match = re.search(pattern, command, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_antigravity_port(command: str, flag: str) -> int | None:
    value = _extract_antigravity_flag(command, flag)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _antigravity_listening_ports(pid: int) -> list[int]:
    lsof = lsof_binary()
    if not lsof:
        raise _AntigravityProbeError("lsof is not available for Antigravity port detection.")
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=ANTIGRAVITY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise _AntigravityProbeError("Antigravity port detection timed out.") from exc
    except PermissionError as exc:
        raise _AntigravityProbeError(
            "Antigravity port detection was blocked by system permissions."
        ) from exc
    except OSError as exc:
        raise _AntigravityProbeError(f"Antigravity port detection failed: {exc}") from exc

    ports = parse_lsof_listening_ports(result.stdout)
    if not ports:
        raise _AntigravityProbeError("Antigravity is running but no language-server ports are listening yet.")
    return ports


def _antigravity_find_connect_port(ports: list[int], csrf_token: str) -> int:
    for port in ports:
        try:
            _antigravity_request_json(
                "https",
                port,
                "/exa.language_server_pb.LanguageServerService/GetUnleashData",
                _antigravity_unleash_request_body(),
                csrf_token,
            )
            return port
        except _AntigravityProbeError:
            continue
    raise _AntigravityProbeError("Antigravity local API port was not found.")


def _antigravity_fetch_command_model_configs(
    process_info: _AntigravityProcessInfo,
    connect_port: int,
) -> dict:
    path = "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs"
    body = _antigravity_default_request_body()
    try:
        return _antigravity_request_json("https", connect_port, path, body, process_info.csrf_token)
    except _AntigravityProbeError:
        if process_info.extension_port is None or process_info.extension_port == connect_port:
            raise
        return _antigravity_request_json(
            "http",
            process_info.extension_port,
            path,
            body,
            process_info.csrf_token,
        )


def _antigravity_default_request_body() -> dict:
    return {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "ideVersion": "unknown",
            "locale": "en",
        }
    }


def _antigravity_unleash_request_body() -> dict:
    return {
        "context": {
            "properties": {
                "devMode": "false",
                "extensionVersion": "unknown",
                "hasAnthropicModelAccess": "true",
                "ide": "antigravity",
                "ideVersion": "unknown",
                "installationId": "tokenkick",
                "language": "UNSPECIFIED",
                "os": "macos",
                "requestedModelId": "MODEL_UNSPECIFIED",
            }
        }
    }


def _antigravity_request_json(
    scheme: str,
    port: int,
    path: str,
    body: dict,
    csrf_token: str,
) -> dict:
    url = f"{scheme}://127.0.0.1:{port}{path}"
    body_bytes = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body_bytes)),
            "Connect-Protocol-Version": "1",
            "X-Codeium-Csrf-Token": csrf_token,
        },
    )
    context = ssl._create_unverified_context() if scheme == "https" else None
    try:
        with urllib.request.urlopen(
            request,
            timeout=ANTIGRAVITY_TIMEOUT_SECONDS,
            context=context,
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise _AntigravityProbeError(f"Antigravity API HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _AntigravityProbeError(f"Antigravity API request failed: {exc}") from exc

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _AntigravityProbeError("Antigravity API returned malformed JSON.") from exc
    if not isinstance(parsed, dict):
        raise _AntigravityProbeError("Antigravity API returned an unexpected payload.")
    return parsed


def _parse_antigravity_user_status(label: str, data: dict) -> AccountStatus:
    _raise_for_antigravity_response_code(data)
    user_status = data.get("userStatus")
    if not isinstance(user_status, dict):
        raise _AntigravityProbeError("Antigravity GetUserStatus response is missing userStatus.")
    email = user_status.get("email")
    named_status = _parse_antigravity_named_windows(label, data, user_status)
    if named_status is not None:
        if isinstance(email, str) and email.strip():
            setattr(named_status, "_antigravity_identity_email", email.strip())
        return named_status
    configs = _nested_get(user_status, ("cascadeModelConfigData", "clientModelConfigs"))
    quotas = _antigravity_model_quotas(configs if isinstance(configs, list) else [])
    if not quotas:
        raise _AntigravityProbeError("Antigravity GetUserStatus response has no quota models.")
    status = _antigravity_status_from_quotas(label, quotas)
    if isinstance(email, str) and email.strip():
        setattr(status, "_antigravity_identity_email", email.strip())
    return status


def _parse_antigravity_command_model_configs(label: str, data: dict) -> AccountStatus:
    _raise_for_antigravity_response_code(data)
    named_status = _parse_antigravity_named_windows(label, data)
    if named_status is not None:
        return named_status
    configs = data.get("clientModelConfigs")
    quotas = _antigravity_model_quotas(configs if isinstance(configs, list) else [])
    if not quotas:
        raise _AntigravityProbeError("Antigravity model config response has no quota models.")
    return _antigravity_status_from_quotas(label, quotas)


def _parse_antigravity_named_windows(label: str, *payloads: dict) -> AccountStatus | None:
    for payload in payloads:
        status = antigravity_status_from_extra_rate_windows(
            label,
            payload,
            window_source=ANTIGRAVITY_SOURCE_DETAIL,
            source_detail=ANTIGRAVITY_SOURCE_DETAIL,
        )
        if status is None:
            continue
        if status.state == AccountState.UNKNOWN:
            raise _AntigravityProbeError(
                status.error or "Antigravity named quota windows could not be parsed."
            )
        return status
    return None


def _raise_for_antigravity_response_code(data: dict) -> None:
    code = data.get("code")
    if code is None or code == 0:
        return
    if isinstance(code, str) and code.lower() in {"0", "ok", "success"}:
        return
    raise _AntigravityProbeError(f"Antigravity API returned code {code}.")


def _antigravity_model_quotas(configs: list) -> list[dict]:
    quotas: list[dict] = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        quota = config.get("quotaInfo")
        model_or_alias = config.get("modelOrAlias")
        if not isinstance(quota, dict) or not isinstance(model_or_alias, dict):
            continue
        remaining_fraction = _to_float_or_none(quota.get("remainingFraction"))
        if remaining_fraction is None:
            continue
        label = config.get("label")
        model_id = model_or_alias.get("model")
        quotas.append(
            {
                "label": label if isinstance(label, str) else "",
                "model_id": model_id if isinstance(model_id, str) else "",
                "remaining_fraction": max(0.0, min(1.0, remaining_fraction)),
                "reset_time": quota.get("resetTime"),
            }
        )
    return quotas


def _antigravity_status_from_quotas(label: str, quotas: list[dict]) -> AccountStatus:
    ordered = _select_antigravity_quotas(quotas)
    primary = ordered[0]
    secondary = ordered[1] if len(ordered) > 1 else None
    used_percent = 100.0 - primary["remaining_fraction"] * 100.0
    resets_in = _seconds_until_reset(resets_at=primary.get("reset_time"))
    session_used_percent = (
        100.0 - secondary["remaining_fraction"] * 100.0
        if secondary is not None
        else None
    )
    session_resets_in = (
        _seconds_until_reset(resets_at=secondary.get("reset_time"))
        if secondary is not None
        else None
    )
    return AccountStatus(
        label=label,
        state=_determine_state(round(used_percent, 2), resets_in),
        used_percent=round(used_percent, 2),
        resets_in_seconds=resets_in,
        session_used_percent=round(session_used_percent, 2)
        if session_used_percent is not None
        else None,
        session_resets_in_seconds=session_resets_in,
        source_detail=ANTIGRAVITY_SOURCE_DETAIL,
    )


def _select_antigravity_quotas(quotas: list[dict]) -> list[dict]:
    ordered: list[dict] = []
    predicates = [
        lambda value: "claude" in value and "thinking" not in value,
        lambda value: "pro" in value and "low" in value,
        lambda value: "gemini" in value and "flash" in value,
    ]
    for predicate in predicates:
        quota = next((item for item in quotas if predicate(item["label"].lower())), None)
        if quota is not None and quota not in ordered:
            ordered.append(quota)
    if not ordered:
        ordered.extend(sorted(quotas, key=lambda item: item["remaining_fraction"]))
    else:
        for quota in sorted(quotas, key=lambda item: item["remaining_fraction"]):
            if quota not in ordered:
                ordered.append(quota)
    return ordered


def _to_float_or_none(value: Any) -> float | None:
    try:
        return _to_float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Codex session file source
# ---------------------------------------------------------------------------

def _fetch_codex_session_file(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
) -> AccountStatus:
    """Parse Codex session files directly."""
    sessions_dir = Path(account.session_path or Path.home() / ".codex" / "sessions")
    if not sessions_dir.exists():
        local_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        if local_status is not None:
            return local_status
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error=f"Sessions directory not found: {sessions_dir}",
        )

    # Find the most recent session file with rate limit data
    latest_rate_limit = _find_latest_rate_limit(sessions_dir)
    if latest_rate_limit is None:
        local_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        if local_status is not None:
            return local_status
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="No rate limit data found in session files",
        )

    return _ensure_status_metadata(
        _parse_session_rate_limit(account.label, latest_rate_limit),
        "codex-session-file",
    )


def _find_latest_rate_limit(sessions_dir: Path) -> Optional[dict]:
    """Walk session JSONL files to find the most recent token_count payload."""
    latest = None
    latest_ts = ""

    jsonl_files = sorted(
        sessions_dir.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for jsonl_path in jsonl_files[:20]:
        for line in reversed(jsonl_path.read_text(errors="replace").splitlines()):
            try:
                event = json.loads(line)
                payload = _extract_session_token_count(event)
                if payload is None:
                    continue
                ts = event.get("timestamp", "")
                if ts > latest_ts:
                    latest_ts = ts
                    latest = payload
            except json.JSONDecodeError:
                continue

    return latest


def _extract_session_token_count(event: dict) -> Optional[dict]:
    """Return a Codex token_count payload from a session JSONL event."""
    if event.get("type") != "event_msg":
        return None

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "token_count":
        return None
    if not isinstance(payload.get("rate_limits"), dict):
        return None
    return payload


def _parse_session_rate_limit(label: str, payload: dict) -> AccountStatus:
    """Parse a token_count payload from a Codex session file."""
    rate_limits = payload.get("rate_limits", {})

    # Use the weekly (secondary) window as the primary concern
    weekly = rate_limits.get("secondary", {})
    session = rate_limits.get("primary", {})
    used_pct = _to_float(weekly.get("used_percent"))
    resets_in = _seconds_until_reset(
        resets_at=weekly.get("resets_at"),
        resets_in=weekly.get("resets_in_seconds"),
    )
    window_min = weekly.get("window_minutes")
    session_resets_in = _seconds_until_reset(
        resets_at=session.get("resets_at"),
        resets_in=session.get("resets_in_seconds"),
    )
    session_used_pct = _to_float(session.get("used_percent"))
    anchor_state = _codex_appserver_anchor_state(
        used_pct=used_pct,
        resets_in_seconds=resets_in,
        window_minutes=window_min,
    )
    state = _codex_rate_limit_state(
        used_pct=used_pct,
        resets_in_seconds=resets_in,
        window_anchor_state=anchor_state,
    )

    return AccountStatus(
        label=label,
        state=state,
        used_percent=used_pct,
        resets_in_seconds=resets_in,
        window_minutes=window_min,
        session_used_percent=session_used_pct,
        session_resets_in_seconds=session_resets_in,
        session_window_minutes=session.get("window_minutes"),
        window_anchor_state=anchor_state,
    )


# ---------------------------------------------------------------------------
# Manual source (no auto-detection)
# ---------------------------------------------------------------------------

def _fetch_manual(account: AccountConfig) -> AccountStatus:
    """Manual accounts are retained for legacy configs but cannot refresh themselves."""
    return AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=(
            "Manual account has no readable provider source. Run `tk setup` after logging in "
            "or configure a supported source."
        ),
    )


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------
