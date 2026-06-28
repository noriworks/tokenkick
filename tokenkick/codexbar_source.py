"""CodexBar provider source and compatibility fallbacks.

Active paths:
- CodexBar CLI (`codexbar usage ...` and legacy `codexbar --format json`)
- CodexBar HTTP (`/usage`)

Compatibility-only fallbacks:
- CodexBar local history files
- CodexBar widget snapshots
- CodexBar managed-account mapping for historical CodexBar-managed homes
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .antigravity import (
    antigravity_status_from_extra_rate_windows,
    has_complete_antigravity_quota_windows,
)
from .models import AccountConfig, AccountState, AccountStatus
from .source_utils import (
    _determine_state,
    _ensure_status_metadata,
    _nested_get,
    _normalize_codex_tiny_session_window,
    _parse_reset_timestamp,
    _seconds_until_reset,
    _status_observed_at,
    _to_float,
)

CODEXBAR_ALL_ACCOUNTS_CMD = [
    "codexbar",
    "usage",
    "--provider",
    "codex",
    "--all-accounts",
    "--format",
    "json",
]
CODEXBAR_PROVIDER_USAGE_CMD_PREFIX = ["codexbar", "usage", "--provider"]
CODEXBAR_PROVIDER_USAGE_CMD_SUFFIX = ["--format", "json"]
CODEXBAR_LEGACY_CMD = ["codexbar", "--format", "json", "--pretty"]
CODEXBAR_TIMEOUT_SECONDS = 20
CODEXBAR_STALENESS_THRESHOLD_SECONDS = 900
CODEXBAR_REJECTION_THRESHOLD_SECONDS = 86400
CODEXBAR_FUTURE_SKEW_TOLERANCE_SECONDS = 300
CODEXBAR_WIDGET_SNAPSHOT_SCHEMA_VERSION = 1
CODEXBAR_HISTORY_SCHEMA_VERSION = 1
CODEXBAR_MANAGED_ACCOUNTS_SCHEMA_VERSION = 2
CODEXBAR_NOT_INSTALLED_MESSAGE = (
    "CodexBar not installed. Install CodexBar, then run tk setup or configure another data source."
)
CODEXBAR_NOT_RUNNING_MESSAGE = (
    "CodexBar not running. Open CodexBar and wait for it to refresh, then run tk status --refresh."
)
CODEXBAR_SNAPSHOT_STALE_MESSAGE = (
    'CodexBar snapshot is stale beyond the configured threshold. Open CodexBar and wait for '
    '"Updated just now", then run tk status --refresh.'
)
CODEXBAR_SNAPSHOT_FUTURE_MESSAGE = (
    "CodexBar snapshot timestamp is more than 5 minutes in the future. "
    "Check your system clock or refresh CodexBar, then run tk status --refresh."
)
CODEXBAR_APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "CodexBar"
CODEXBAR_HISTORY_DIR = (
    Path.home() / "Library" / "Application Support" / "com.steipete.codexbar" / "history"
)
CODEXBAR_MANAGED_ACCOUNTS_FILE = CODEXBAR_APP_SUPPORT_DIR / "managed-codex-accounts.json"
CODEXBAR_WIDGET_SNAPSHOT_FILES = [
    Path.home()
    / "Library"
    / "Group Containers"
    / "Y5PE65HELJ.com.steipete.codexbar"
    / "widget-snapshot.json",
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.com.steipete.codexbar"
    / "widget-snapshot.json",
]
_CODEXBAR_JSON_CACHE: dict[tuple[str, ...], tuple[Any | None, str | None]] | None = None


@dataclass
class _CodexBarLocalRead:
    data: dict | None = None
    observed_at: str | None = None
    source_detail: str | None = None
    failure: AccountStatus | None = None


@contextmanager
def codexbar_json_cache() -> Iterator[None]:
    global _CODEXBAR_JSON_CACHE
    previous_cache = _CODEXBAR_JSON_CACHE
    _CODEXBAR_JSON_CACHE = {}
    try:
        yield
    finally:
        _CODEXBAR_JSON_CACHE = previous_cache


def codexbar_json_cache_active() -> bool:
    return _CODEXBAR_JSON_CACHE is not None


def _codexbar_fallback_error(codexbar_error: str | None, direct_error: str | None) -> str | None:
    if direct_error and codexbar_error:
        return f"Antigravity direct probe failed: {direct_error} CodexBar fallback failed: {codexbar_error}"
    if direct_error:
        return f"Antigravity direct probe failed: {direct_error}"
    return codexbar_error


# ---------------------------------------------------------------------------
# CodexBar CLI source
# ---------------------------------------------------------------------------

def _fetch_codexbar_cli(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
) -> AccountStatus:
    """Read rate-limit data via CodexBar's CLI."""
    provider = account.codexbar_provider or account.provider
    if provider == "antigravity":
        return _fetch_antigravity_codexbar_cli(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )

    if provider != "codex":
        local_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        if local_status is not None:
            return local_status

    if provider == "codex":
        data, _error = _load_codexbar_all_accounts_json()
        if _codexbar_all_account_entries(data):
            status = _parse_codexbar_json(
                account.label,
                data,
                provider="codex",
                account=account.codexbar_account,
            )
            if status.state != AccountState.UNKNOWN:
                return _ensure_status_metadata(status, "codexbar-cli")

        local_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        if local_status is not None:
            return local_status

    data, error = _load_codexbar_legacy_json()
    if data is None:
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
            error=error or "Could not parse rate limit data from CodexBar response",
        )

    status = _parse_codexbar_json(
        account.label,
        data,
        provider=account.codexbar_provider,
        account=account.codexbar_account,
    )
    if status.state != AccountState.UNKNOWN:
        return _ensure_status_metadata(status, "codexbar-cli")
    local_status = _fetch_codexbar_local_status(
        account,
        codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
        codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
    )
    return local_status or _ensure_status_metadata(status, "codexbar-cli")


def _fetch_antigravity_codexbar_cli(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int,
    codexbar_rejection_threshold_seconds: int,
) -> AccountStatus:
    """Prefer direct Antigravity data, with complete CodexBar buckets as fallback."""
    provider = "antigravity"
    direct_summary: AccountStatus | None = None
    summary_status: AccountStatus | None = None
    first_error: str | None = None

    from .sources import _fetch_antigravity_direct

    direct_status = _fetch_antigravity_direct(account)
    if direct_status.state != AccountState.UNKNOWN:
        if has_complete_antigravity_quota_windows(direct_status):
            return direct_status
        direct_summary = direct_status
    else:
        first_error = first_error or direct_status.error

    for loader in (
        lambda: _load_codexbar_provider_json(provider),
        _load_codexbar_legacy_json,
    ):
        data, error = loader()
        first_error = first_error or error
        if data is None:
            continue
        status = _parse_codexbar_json(
            account.label,
            data,
            provider=provider,
            account=account.codexbar_account,
        )
        if status.state == AccountState.UNKNOWN:
            first_error = first_error or status.error
            continue
        status = _ensure_status_metadata(status, "codexbar-cli")
        if has_complete_antigravity_quota_windows(status):
            return status
        summary_status = summary_status or status

    local_status = _fetch_codexbar_local_status(
        account,
        codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
        codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
    )
    if local_status is not None:
        if (
            local_status.state != AccountState.UNKNOWN
            and has_complete_antigravity_quota_windows(local_status)
        ):
            return local_status
        if local_status.state != AccountState.UNKNOWN:
            summary_status = summary_status or local_status
        else:
            first_error = first_error or local_status.error

    return direct_summary or summary_status or AccountStatus(
        label=account.label,
        state=AccountState.UNKNOWN,
        error=_codexbar_fallback_error(first_error, direct_status.error)
        or "Could not parse Antigravity quota data.",
    )


# ---------------------------------------------------------------------------
# CodexBar HTTP source
# ---------------------------------------------------------------------------

def _fetch_codexbar_http(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
) -> AccountStatus:
    """Read rate-limit data via CodexBar's HTTP server."""
    import httpx

    base_url = account.codexbar_url or "http://localhost:8080"
    url = f"{base_url}/usage"
    if account.codexbar_provider:
        url += f"?provider={account.codexbar_provider}"

    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        status = _parse_codexbar_json(
            account.label,
            data,
            provider=account.codexbar_provider,
            account=account.codexbar_account,
        )
        if status.state != AccountState.UNKNOWN:
            return _ensure_status_metadata(status, "codexbar-http")
        local_status = _fetch_codexbar_local_status(
            account,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
        return local_status or _ensure_status_metadata(status, "codexbar-http")
    except (httpx.HTTPError, ValueError) as exc:
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
            error=CODEXBAR_NOT_RUNNING_MESSAGE if isinstance(exc, httpx.RequestError) else str(exc),
        )


def _load_codexbar_all_accounts_json() -> tuple[Any | None, str | None]:
    """Load CodexBar's full multi-account Codex usage output when available."""
    return _run_codexbar_json(CODEXBAR_ALL_ACCOUNTS_CMD)


def _load_codexbar_provider_json(provider: str) -> tuple[Any | None, str | None]:
    """Load CodexBar's provider-specific usage output when available."""
    return _run_codexbar_json(
        [*CODEXBAR_PROVIDER_USAGE_CMD_PREFIX, provider, *CODEXBAR_PROVIDER_USAGE_CMD_SUFFIX]
    )


def _load_codexbar_legacy_json() -> tuple[Any | None, str | None]:
    """Load CodexBar's legacy all-provider CLI output."""
    return _run_codexbar_json(CODEXBAR_LEGACY_CMD)


def _fetch_codexbar_local_status(
    account: AccountConfig,
    *,
    codexbar_staleness_threshold_seconds: int = CODEXBAR_STALENESS_THRESHOLD_SECONDS,
    codexbar_rejection_threshold_seconds: int = CODEXBAR_REJECTION_THRESHOLD_SECONDS,
) -> AccountStatus | None:
    """Read CodexBar's local app snapshots when the CLI cannot refresh."""
    provider = account.codexbar_provider or account.provider
    reads: list[_CodexBarLocalRead] = []
    if provider == "codex":
        reads.append(_codexbar_local_history_entry(provider, account.codexbar_account, account.label))
        if not account.codexbar_account:
            reads.append(_codexbar_widget_snapshot_entry(provider, account.label))
    elif provider == "claude":
        reads.append(_codexbar_local_history_entry(provider, account.codexbar_account, account.label))
        reads.append(_codexbar_widget_snapshot_entry(provider, account.label))
    else:
        reads.append(_codexbar_widget_snapshot_entry(provider, account.label))

    first_failure: AccountStatus | None = None
    schema_failure: AccountStatus | None = None
    for read in reads:
        if read.failure is not None:
            first_failure = first_failure or read.failure
            if _is_schema_mismatch(read.failure):
                schema_failure = schema_failure or read.failure
            continue
        if read.data is None:
            continue
        status = _parse_codexbar_json(
            account.label,
            read.data,
            provider=provider,
            account=account.codexbar_account,
        )
        if status.state == AccountState.UNKNOWN:
            first_failure = first_failure or status
            continue
        return _apply_codexbar_freshness(
            status,
            observed_at=read.observed_at,
            source_detail=read.source_detail,
            codexbar_staleness_threshold_seconds=codexbar_staleness_threshold_seconds,
            codexbar_rejection_threshold_seconds=codexbar_rejection_threshold_seconds,
        )
    return schema_failure or first_failure


def _codexbar_local_history_entry(
    provider: str,
    account: str | None = None,
    label: str | None = None,
) -> _CodexBarLocalRead:
    path = CODEXBAR_HISTORY_DIR / f"{provider}.json"
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _CodexBarLocalRead()
    if not isinstance(data, dict):
        return _CodexBarLocalRead(failure=_malformed_codexbar_status(label or provider, "CodexBar history"))

    failure = _validate_codexbar_schema(
        label or provider,
        data,
        expected=CODEXBAR_HISTORY_SCHEMA_VERSION,
        source_detail="CodexBar history",
    )
    if failure is not None:
        return _CodexBarLocalRead(failure=failure)

    window_sets = None
    account_email = account
    if account and provider == "codex":
        key_read = _codexbar_account_key_for_email(account, label or provider)
        if key_read.failure is not None:
            return key_read
        account_key = key_read.data.get("account_key") if key_read.data else None
        if account_key is None:
            return _CodexBarLocalRead()
        window_sets = (data.get("accounts") or {}).get(account_key) if account_key else None
        if window_sets is None:
            return _CodexBarLocalRead()
    if window_sets is None and isinstance(data.get("unscoped"), list):
        window_sets = data.get("unscoped")
    if window_sets is None and isinstance(data.get("preferredAccountKey"), str):
        window_sets = (data.get("accounts") or {}).get(data["preferredAccountKey"])
    if window_sets is None:
        accounts = data.get("accounts")
        if isinstance(accounts, dict) and accounts:
            window_sets = next(iter(accounts.values()))

    usage, observed_at = _usage_from_history_window_sets(window_sets)
    if usage is None or observed_at is None:
        return _CodexBarLocalRead(
            failure=_malformed_codexbar_status(label or provider, "CodexBar history")
        )
    if account_email:
        usage["accountEmail"] = account_email
    return _CodexBarLocalRead(
        data={"provider": provider, "usage": usage},
        observed_at=observed_at,
        source_detail="codexbar-history",
    )


def _codexbar_account_key_for_email(email: str, label: str) -> _CodexBarLocalRead:
    try:
        data = json.loads(CODEXBAR_MANAGED_ACCOUNTS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _CodexBarLocalRead()
    if not isinstance(data, dict):
        return _CodexBarLocalRead(
            failure=_malformed_codexbar_status(label, "CodexBar managed accounts")
        )
    failure = _validate_codexbar_schema(
        label,
        data,
        expected=CODEXBAR_MANAGED_ACCOUNTS_SCHEMA_VERSION,
        source_detail="CodexBar managed accounts",
    )
    if failure is not None:
        return _CodexBarLocalRead(failure=failure)
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return _CodexBarLocalRead(
            failure=_malformed_codexbar_status(label, "CodexBar managed accounts")
        )
    for entry in data.get("accounts", []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("email", "")).lower() != email.lower():
            continue
        provider_id = entry.get("providerAccountID")
        if isinstance(provider_id, str) and provider_id:
            return _CodexBarLocalRead(
                data={"account_key": f"codex:v1:provider-account:{provider_id}"}
            )
    return _CodexBarLocalRead()


def _usage_from_history_window_sets(window_sets: Any) -> tuple[dict | None, str | None]:
    if not isinstance(window_sets, list):
        return None, None
    usage: dict[str, dict] = {}
    observed_at: str | None = None
    for window_set in window_sets:
        if not isinstance(window_set, dict):
            continue
        name = window_set.get("name")
        entries = window_set.get("entries")
        if not isinstance(name, str) or not isinstance(entries, list) or not entries:
            continue
        latest = entries[-1]
        if not isinstance(latest, dict):
            continue
        entry_observed_at = _codexbar_observed_at(latest) or _codexbar_observed_at(window_set)
        if entry_observed_at and (observed_at is None or entry_observed_at > observed_at):
            observed_at = entry_observed_at
        target = "secondary" if name in {"weekly", "secondary"} else "primary"
        usage[target] = {
            "usedPercent": latest.get("usedPercent"),
            "resetsAt": latest.get("resetsAt"),
            "windowMinutes": window_set.get("windowMinutes"),
        }
    return usage or None, observed_at


def _codexbar_widget_snapshot_entry(provider: str, label: str | None = None) -> _CodexBarLocalRead:
    snapshot_read = _load_latest_codexbar_widget_snapshot(label or provider)
    if snapshot_read.failure is not None:
        return snapshot_read
    snapshot = snapshot_read.data
    if not snapshot:
        return _CodexBarLocalRead()
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        return _CodexBarLocalRead(
            failure=_malformed_codexbar_status(label or provider, "CodexBar widget snapshot")
        )
    for entry in entries:
        if isinstance(entry, dict) and entry.get("provider") == provider:
            usage = dict(entry)
            usage.pop("provider", None)
            return _CodexBarLocalRead(
                data={"provider": provider, "usage": usage},
                observed_at=_codexbar_observed_at(entry) or snapshot_read.observed_at,
                source_detail="codexbar-snapshot",
            )
    return _CodexBarLocalRead()


def _load_latest_codexbar_widget_snapshot(label: str) -> _CodexBarLocalRead:
    snapshots: list[tuple[dict, str | None]] = []
    schema_failure: AccountStatus | None = None
    malformed_failure: AccountStatus | None = None
    for path in CODEXBAR_WIDGET_SNAPSHOT_FILES:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            malformed_failure = malformed_failure or _malformed_codexbar_status(
                label,
                "CodexBar widget snapshot",
            )
            continue
        failure = _validate_codexbar_schema(
            label,
            data,
            expected=CODEXBAR_WIDGET_SNAPSHOT_SCHEMA_VERSION,
            source_detail="CodexBar widget snapshot",
            missing_means=CODEXBAR_WIDGET_SNAPSHOT_SCHEMA_VERSION,
        )
        if failure is not None:
            schema_failure = schema_failure or failure
            continue
        entries = data.get("entries")
        if not isinstance(entries, list):
            malformed_failure = malformed_failure or _malformed_codexbar_status(
                label,
                "CodexBar widget snapshot",
            )
            continue
        snapshots.append((data, _codexbar_observed_at(data)))
    if not snapshots:
        return _CodexBarLocalRead(failure=schema_failure or malformed_failure)
    snapshot, observed_at = max(snapshots, key=lambda item: item[1] or "")
    return _CodexBarLocalRead(
        data=snapshot,
        observed_at=observed_at,
        source_detail="CodexBar widget snapshot",
    )


def _apply_codexbar_freshness(
    status: AccountStatus,
    *,
    observed_at: str | None,
    source_detail: str | None,
    codexbar_staleness_threshold_seconds: int,
    codexbar_rejection_threshold_seconds: int,
) -> AccountStatus:
    future_skew_seconds = _codexbar_future_skew_seconds(observed_at)
    if future_skew_seconds is not None:
        return AccountStatus(
            label=status.label,
            state=AccountState.UNKNOWN,
            error=CODEXBAR_SNAPSHOT_FUTURE_MESSAGE,
            observed_at=observed_at or _status_observed_at(),
            source_detail=source_detail,
            stale=True,
            stale_seconds=None,
        )
    age_seconds = _codexbar_age_seconds(observed_at)
    if age_seconds is not None and age_seconds > codexbar_rejection_threshold_seconds:
        return AccountStatus(
            label=status.label,
            state=AccountState.UNKNOWN,
            error=CODEXBAR_SNAPSHOT_STALE_MESSAGE,
            observed_at=observed_at or _status_observed_at(),
            source_detail=source_detail,
            stale=True,
            stale_seconds=age_seconds,
        )
    status.observed_at = observed_at or _status_observed_at()
    status.source_detail = source_detail
    status.stale_seconds = age_seconds
    status.stale = (
        age_seconds is not None
        and age_seconds > codexbar_staleness_threshold_seconds
    )
    return status


def _codexbar_observed_at(data: dict) -> str | None:
    for key in ("observedAt", "observed_at", "updatedAt", "generatedAt", "capturedAt"):
        value = data.get(key)
        if isinstance(value, str) and _parse_reset_timestamp(value) is not None:
            return value
    return None


def _codexbar_future_skew_seconds(observed_at: str | None) -> int | None:
    if observed_at is None:
        return None
    observed_ts = _parse_reset_timestamp(observed_at)
    if observed_ts is None:
        return None
    future_seconds = int(observed_ts - time.time())
    if future_seconds > CODEXBAR_FUTURE_SKEW_TOLERANCE_SECONDS:
        return future_seconds
    return None


def _codexbar_age_seconds(observed_at: str | None) -> int | None:
    if observed_at is None:
        return None
    observed_ts = _parse_reset_timestamp(observed_at)
    if observed_ts is None:
        return None
    return max(0, int(time.time() - observed_ts))


def _validate_codexbar_schema(
    label: str,
    data: dict,
    *,
    expected: int,
    source_detail: str,
    missing_means: int | None = None,
) -> AccountStatus | None:
    detected = data.get("schemaVersion", data.get("version"))
    if detected is None and missing_means is not None:
        detected = missing_means
    if detected != expected:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=_schema_version_mismatch_message(expected, detected),
            source_detail=source_detail,
        )
    return None


def _schema_version_mismatch_message(expected: int, detected: Any) -> str:
    detected_text = "missing" if detected is None else str(detected)
    expected_value = _schema_version_number(expected)
    detected_value = _schema_version_number(detected)
    if detected_value is not None and expected_value is not None and detected_value > expected_value:
        next_step = "Update TokenKick, then run tk status --refresh."
    elif detected_value is not None and expected_value is not None and detected_value < expected_value:
        next_step = "Update CodexBar, then run tk status --refresh."
    else:
        next_step = "Update CodexBar, then run tk status --refresh."
    return (
        "CodexBar data schema version mismatch: "
        f"expected {expected}, got {detected_text}. {next_step}"
    )


def _schema_version_number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _malformed_codexbar_status(label: str, source_detail: str) -> AccountStatus:
    return AccountStatus(
        label=label,
        state=AccountState.UNKNOWN,
        error=f"{source_detail} is missing required fields.",
        source_detail=source_detail,
    )


def _is_schema_mismatch(status: AccountStatus) -> bool:
    return bool(status.error and status.error.startswith("CodexBar data schema version mismatch:"))


def _run_codexbar_json(cmd: list[str]) -> tuple[Any | None, str | None]:
    cache_key = tuple(cmd)
    if _CODEXBAR_JSON_CACHE is not None and cache_key in _CODEXBAR_JSON_CACHE:
        return _CODEXBAR_JSON_CACHE[cache_key]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CODEXBAR_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        parsed = (None, CODEXBAR_NOT_INSTALLED_MESSAGE)
    except subprocess.TimeoutExpired:
        parsed = (None, CODEXBAR_NOT_RUNNING_MESSAGE)
    else:
        try:
            parsed = (json.loads(result.stdout), None)
        except json.JSONDecodeError:
            stderr = result.stderr.strip()[:200]
            parsed = (None, f"codexbar exited {result.returncode}: {stderr}")
    if _CODEXBAR_JSON_CACHE is not None:
        _CODEXBAR_JSON_CACHE[cache_key] = parsed
    return parsed


def _codexbar_json_entries(data: Any) -> list[dict]:
    """Return provider entries from known CodexBar JSON envelope shapes."""
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("accounts", "providers", "results", "data"):
            entries = data.get(key)
            if isinstance(entries, list):
                return [entry for entry in entries if isinstance(entry, dict)]
        return [data]
    return []


def _codexbar_all_account_entries(data: Any) -> list[dict]:
    """Return Codex entries from `codexbar usage --provider codex --all-accounts`."""
    return [
        entry
        for entry in _codexbar_json_entries(data)
        if _codexbar_entry_provider(entry) == "codex"
    ]


def _codexbar_entry_provider(entry: dict) -> str | None:
    usage = entry.get("usage") or {}
    identity = usage.get("identity") or {}
    provider = entry.get("provider") or identity.get("providerID")
    return provider if isinstance(provider, str) else None


def _select_codexbar_entry(entries: list, provider: str | None, account: str | None) -> dict:
    """Choose the relevant provider entry from CodexBar's list output."""
    if provider and account:
        provider_entries_without_email = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if _codexbar_entry_provider(entry) != provider:
                continue
            email = _codexbar_entry_email(entry)
            if email and email.lower() == account.lower():
                return entry
            if email is None:
                provider_entries_without_email.append(entry)
        if len(provider_entries_without_email) == 1:
            return provider_entries_without_email[0]
        return {}

    if provider:
        for entry in entries:
            if isinstance(entry, dict) and _codexbar_entry_provider(entry) == provider:
                return entry

    for entry in entries:
        if isinstance(entry, dict) and _codexbar_entry_provider(entry) == "codex":
            return entry

    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("usage"), dict):
            return entry

    return entries[0] if isinstance(entries[0], dict) else {}


def _codexbar_entry_email(entry: dict) -> str | None:
    usage = entry.get("usage") or {}
    identity = usage.get("identity") or {}
    dashboard = entry.get("openaiDashboard") or {}
    email = (
        entry.get("account")
        or entry.get("accountEmail")
        or usage.get("accountEmail")
        or identity.get("accountEmail")
        or dashboard.get("signedInEmail")
    )
    return email if isinstance(email, str) else None


def _parse_codexbar_json(
    label: str,
    data: Any,
    provider: str | None = None,
    account: str | None = None,
) -> AccountStatus:
    """Parse CodexBar JSON output into an AccountStatus.

    CodexBar's JSON structure can vary by version — this handles the
    common patterns. When the format is unrecognized, returns UNKNOWN.
    """
    entries = _codexbar_json_entries(data)
    if isinstance(data, list) or (isinstance(data, dict) and entries and entries[0] is not data):
        if not entries:
            return AccountStatus(label=label, state=AccountState.UNKNOWN, error="Empty response")
        data = _select_codexbar_entry(entries, provider, account)

    if not isinstance(data, dict):
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="Could not parse rate limit data from CodexBar response",
        )

    error = data.get("error")
    if isinstance(error, dict):
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error=error.get("message") or "CodexBar provider returned an error",
        )

    if provider == "antigravity":
        antigravity_status = antigravity_status_from_extra_rate_windows(
            label,
            data,
            window_source="codexbar",
        )
        if antigravity_status is not None:
            return antigravity_status

    window = _select_codexbar_rate_window(data)
    session_window = _select_codexbar_session_window(data)
    used_pct = _window_used_percent(window)
    resets_in = _window_resets_in_seconds(window)
    window_min = _window_minutes(window)

    if used_pct is None and resets_in is None:
        return AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="Could not parse rate limit data from CodexBar response",
        )

    state = _determine_state(used_pct, resets_in)
    session_used_pct = _window_used_percent(session_window)
    session_resets_in = _window_resets_in_seconds(session_window)
    session_window_min = _window_minutes(session_window)
    if provider == "codex":
        session_used_pct, session_resets_in = _normalize_codex_tiny_session_window(
            used_percent=session_used_pct,
            resets_in_seconds=session_resets_in,
            window_minutes=session_window_min,
        )
    return AccountStatus(
        label=label,
        state=state,
        used_percent=used_pct,
        resets_in_seconds=resets_in,
        window_minutes=window_min,
        session_used_percent=session_used_pct,
        session_resets_in_seconds=session_resets_in,
        session_window_minutes=session_window_min,
        balance_remaining=_openrouter_balance_remaining(data),
        balance_limit=_openrouter_balance_limit(data),
        balance_spent_percent=_openrouter_balance_spent_percent(data),
    )


def _select_codexbar_rate_window(data: dict) -> dict:
    """Select the quota window TokenKick should act on.

    Codex and Claude expose a 5-hour window plus a weekly window. TokenKick's
    state machine keys off the weekly window, so prefer the 10080-minute window
    whenever CodexBar provides it, regardless of primary/secondary ordering.
    """
    candidates = _codexbar_rate_window_candidates(data)
    weekly = [window for window in candidates if (_window_minutes(window) or 0) >= 10080]
    for window in weekly + candidates:
        if _window_used_percent(window) is not None or _window_resets_in_seconds(window) is not None:
            return window
    return {}


def _select_codexbar_session_window(data: dict) -> dict:
    """Select the short session window used to decide whether a kick can run now."""
    candidates = _codexbar_rate_window_candidates(data)
    session_windows = [window for window in candidates if (_window_minutes(window) or 0) == 300]
    for window in session_windows + candidates:
        if _window_minutes(window) == 10080:
            continue
        if _window_used_percent(window) is not None or _window_resets_in_seconds(window) is not None:
            return window
    return {}


def _codexbar_rate_window_candidates(data: dict) -> list[dict]:
    windows: list[dict] = []
    for key_path in [
        ("usage", "secondary"),
        ("openaiDashboard", "secondaryLimit"),
        ("weekly",),
        ("secondary",),
        ("rate_limits", "secondary"),
        ("usage", "primary"),
        ("openaiDashboard", "primaryLimit"),
        ("primary",),
        ("rate_limits", "primary"),
    ]:
        window = _nested_get(data, key_path)
        if isinstance(window, dict):
            windows.append(window)
    return windows


def _window_used_percent(window: dict) -> Optional[float]:
    return _to_float(window.get("usedPercent", window.get("used_percent")))


def _window_minutes(window: dict) -> Optional[int]:
    value = window.get("windowMinutes", window.get("window_minutes"))
    return int(value) if value is not None else None


def _window_resets_in_seconds(window: dict) -> Optional[int]:
    return _seconds_until_reset(
        resets_at=window.get("resetsAt", window.get("resets_at")),
        resets_in=window.get("resets_in_seconds"),
    )


def _openrouter_balance_remaining(data: dict) -> Optional[float]:
    usage = _openrouter_usage(data)
    key_limit = _to_float(usage.get("keyLimit"))
    key_usage = _to_float(usage.get("keyUsage"))
    if key_limit is not None and key_usage is not None:
        return max(0.0, key_limit - key_usage)
    return _to_float(usage.get("balance"))


def _openrouter_balance_limit(data: dict) -> Optional[float]:
    return _to_float(_openrouter_usage(data).get("keyLimit"))


def _openrouter_balance_spent_percent(data: dict) -> Optional[float]:
    usage = _openrouter_usage(data)
    key_limit = _to_float(usage.get("keyLimit"))
    key_usage = _to_float(usage.get("keyUsage"))
    if key_limit and key_usage is not None:
        return (key_usage / key_limit) * 100
    return _to_float(usage.get("usedPercent"))


def _openrouter_usage(data: dict) -> dict:
    usage = _nested_get(data, ("usage", "openRouterUsage"))
    return usage if isinstance(usage, dict) else {}
