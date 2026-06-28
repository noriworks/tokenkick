"""TokenKick CLI — the `tk` command."""

from __future__ import annotations

import io
import json
import os
import re
import signal
import shutil as shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path as Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .antigravity import (
    antigravity_cli_binary,
    has_complete_antigravity_quota_windows,
    read_antigravity_cli_identity,
)
from .consent import (
    AUTO_KICK_CONSENT_ERROR,
    AUTO_KICK_CONSENT_TOKEN,
    AutoKickConsentRequired,
    auto_kick_consent_text,
    provider_display_name,
)
from .direct import (
    CodexProviderUsageError as CodexProviderUsageError,
    email_from_id_token as email_from_id_token,
    read_claude_identity as read_claude_identity,
    read_codex_identity as read_codex_identity,
    read_codex_provider_usage as read_codex_provider_usage,
)
from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeProbeContext,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    CODEX_FIRE_ALL_SURFACE_NAMES,
    CONFIG_DIR,
    CONFIG_FILE,
    Config,
    CODEX_DEFAULT_RATE_LIMIT_ID,
    DataSource,
    DEFAULT_ORCHESTRATION_ROLE,
    KickEvent,
    NOTIFICATION_BACKENDS,
    NotifyConfig,
    ScheduleConfig,
    StateFileError,
    WorkSchedule,
    account_key_string,
    append_kick_event,
    load_kick_history,
    mark_synthetic_status,
    merge_discovered_account,
    normalize_orchestration_role,
    normalize_notification_backends,
    update_kick_history,
    weekly_quota_exhausted,
)
from .sources import (
    _codex_appserver_bucket_display_name as _codex_appserver_bucket_display_name,
    _codex_appserver_rate_limit_issue as _codex_appserver_rate_limit_issue,
    _parse_codex_appserver_ratelimits as _parse_codex_appserver_ratelimits,
    _read_codex_appserver_ratelimits as _read_codex_appserver_ratelimits,
    _read_codex_appserver_ratelimits_for_account as _read_codex_appserver_ratelimits_for_account,
    claude_cli_usage_refresh_allowed,
    fetch_status as fetch_status,
)
from .versioning import installed_version, read_daemon_pidfile, write_daemon_pidfile
from .telegram_remote import (
    TelegramRemoteClient,
    TelegramRemoteConfigError,
    TelegramRemoteListener,
    run_status_cached_command,
    run_status_refresh_command,
    telegram_remote_credentials,
)
from .doctor import build_doctor_report, render_doctor_report
from .codex_surface_stats import (
    DEFAULT_CODEX_SURFACE_ORDER,
    apply_codex_surface_late_confirmation,
    codex_surface_order_for_account,
    codex_surface_stats_for_account,
    reintroduce_codex_surfaces_after_miss,
    reset_codex_surface_demotion_evidence,
    reset_codex_surface_learning_stats,
    update_codex_surface_stats,
)
from .codex_surface_patterns import build_codex_surface_patterns_report
from .kicker import (
    ANTIGRAVITY_AUTO_KICK_DISABLED_MESSAGE,
    ANTIGRAVITY_MONITOR_ONLY_MESSAGE,
    CODEX_NO_GENERATION_EVIDENCE_ERROR,
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_SURFACES,
    GEMINI_AUTO_KICK_DISABLED_MESSAGE,
    GEMINI_MONITOR_ONLY_MESSAGE,
    KICKABLE_PROVIDERS,
    MONITOR_ONLY_PROVIDERS,
    codex_phantom_recovery_model_ladder,
    kick_account,
    kick_invocation_for_account,
    kick_model_for_account,
)
from .reset_calendar import (
    CalendarEvent,
    build_reset_calendar,
    calendar_json_payload,
    format_event_description,
    render_ics,
)
from .migrations import (
    CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE,
    CODEX_HOME_KEY_MIGRATION_KEY,
    DIRECT_SOURCE_APPSERVER_BACKUP_FILE,
    DIRECT_SOURCE_BACKUP_FILE,
    DIRECT_SOURCE_MIGRATION_KEY,
    LABEL_FORMAT_BACKUP_FILE,
    LABEL_FORMAT_MIGRATION_KEY,
    _migrate_codex_home_keys_if_needed,
    _migrate_pending_kick_keys,
    _migrate_provider_first_labels_if_needed,
    _migrate_v04_direct_sources_if_needed,
    _provider_first_account_component,
    _rename_saved_labels,
    _repair_codex_home_identity_drift_if_needed,
)
from .status_cache import (
    _account_status_cache_dict as _account_status_cache_dict,
    _account_status_from_cache as _account_status_from_cache,
    _acquire_status_refresh_lock as _acquire_status_refresh_lock,
    _apply_claude_probe_context_to_entry as _apply_claude_probe_context_to_entry,
    _background_status_refresh_message,
    _cache_statuses_by_key_from_pairs,
    _claude_probe_context_for_account as _claude_probe_context_for_account,
    _clear_status_refresh_lock_acquire_error,
    _copy_existing_claude_probe_metadata as _copy_existing_claude_probe_metadata,
    _entry_provider_observed_at as _entry_provider_observed_at,
    _failures_by_key_from_status_pairs,
    _fetch_status,
    _fill_status_cache_reset_anchors as _fill_status_cache_reset_anchors,
    _format_status_cache_age as _format_status_cache_age,
    _format_status_cache_footer,
    _format_status_footer_timestamp,
    _is_antigravity_account as _is_antigravity_account,
    _load_accounts,
    _load_saved_account_status_snapshot as _load_saved_account_status_snapshot,
    _load_saved_accounts as _load_saved_accounts,
    _load_status_cache,
    _load_status_cache_entries as _load_status_cache_entries,
    _load_status_cache_for_accounts as _load_status_cache_for_accounts,
    _mark_status_cache_entry_stale,
    _parse_status_cache_observed_at as _parse_status_cache_observed_at,
    _read_dormant_hint_state as _read_dormant_hint_state,
    _read_status_cache_data,
    _recompute_status_cache_countdowns as _recompute_status_cache_countdowns,
    _refresh_status_cache,
    _refresh_status_cache_fast,
    _reap_dead_refresh_lock as _reap_dead_status_refresh_lock,
    _release_status_refresh_lock,
    _save_status_cache,
    _status_cache_data as _status_cache_data,
    _status_cache_entry_dict as _status_cache_entry_dict,
    _status_cache_entry_is_stale as _status_cache_entry_is_stale,
    _status_cache_entry_matches_configured_account as _status_cache_entry_matches_configured_account,
    _status_cache_error_class as _status_cache_error_class,
    _status_cache_freshness as _status_cache_freshness,
    _status_cache_needs_refresh,
    _status_cache_now as _status_cache_now,
    _status_cache_observed_at as _status_cache_observed_at,
    _status_cache_provider_observed_at as _status_cache_provider_observed_at,
    _status_refresh_lock_acquire_error,
    _status_refresh_lock_active,
    _status_refresh_lock_info as _status_refresh_lock_info,
    _start_background_status_refresh,
    _write_dormant_hint_state as _write_dormant_hint_state,
    _write_status_cache_data as _write_status_cache_data,
)
from .status_rendering import (
    _filter_status_pairs_by_provider as _filter_status_pairs_by_provider,
    _format_duration as _format_duration,
    _format_openrouter_balance as _format_openrouter_balance,
    _format_pending_kick_cell as _format_pending_kick_cell,
    _format_relative_reset as _format_relative_reset,
    _format_session_reset as _format_session_reset,
    _format_used_cell as _format_used_cell,
    _format_used_labeled_cell as _format_used_labeled_cell,
    _format_used_percent as _format_used_percent,
    _format_weekly_reset as _format_weekly_reset,
    _is_user_facing_codexbar_error as _is_user_facing_codexbar_error,
    _oldest_status_observed_at as _oldest_status_observed_at,
    _pending_by_label as _pending_by_label,
    _render_status_table as _render_status_table,
    _sort_statuses as _sort_statuses,
    _stale_status_reason as _stale_status_reason,
    _status_action as _status_action,
    _status_json_payload as _status_json_payload,
    _status_rows_as_dict as _status_rows_as_dict,
    _status_session_cooldown_remaining as _status_session_cooldown_remaining,
    _status_sort_key as _status_sort_key,
    _status_state_display as _status_state_display,
    _surface_dormant_account_hints as _surface_dormant_account_hints,
    _usage_color as _usage_color,
)
from .discovery import (
    _account_key as _account_key,
    _append_claude_direct_account as _append_claude_direct_account,
    _append_codex_direct_account as _append_codex_direct_account,
    _append_codex_direct_accounts as _append_codex_direct_accounts,
    _append_codex_session_account as _append_codex_session_account,
    _append_codexbar_account_entry as _append_codexbar_account_entry,
    _apply_display_labels as _apply_display_labels,
    _codex_has_home_scope as _codex_has_home_scope,
    _codexbar_email as _codexbar_email,
    _codexbar_label as _codexbar_label,
    _codexbar_provider as _codexbar_provider,
    _discover_accounts_and_statuses as _discover_accounts_and_statuses,
    _discover_codex_session_accounts as _discover_codex_session_accounts,
    _discover_codexbar_accounts as _discover_codexbar_accounts,
    _discover_direct_accounts as _discover_direct_accounts,
    _discovery_identity_aliases as _discovery_identity_aliases,
    _discovery_key as _discovery_key,
    _discovery_score as _discovery_score,
    _display_base_label as _display_base_label,
    _format_discovery_summary as _format_discovery_summary,
    _format_new_account_note,
    _label_from_email as _label_from_email,
    _load_account_status_pairs,
    _load_account_status_pairs_cached as _load_account_status_pairs_cached,
    _merge_discovered_accounts as _merge_discovered_accounts,
    _pair_for_configured_account as _pair_for_configured_account,
    _phantom_session_key,
    _primary_codex_email as _primary_codex_email,
    _sanitize_label as _sanitize_label,
    _setup_footer,
    _status_detail_score as _status_detail_score,
    _unique_label as _unique_label,
)
from .notifier import (
    notify_codex_pending_confirmation_missing,
    notify_dormant_account as notify_dormant_account,
    notify_kick,
    notify_quota_constrained_kick,
    notify_reservation_advisory,
    notify_reset_event,
    notify_schedule_decision,
    notify_scheduled_kick,
    notify_test,
)
from .reservation_advisories import (
    ACTIONABLE_RISK_STATES,
    ReservationAdvisory,
    build_reservation_advisories,
    format_reservation_advisory_message,
    load_reservation_advisory_state,
    mark_reservation_advisory_notified,
)
from .orchestration import (
    AccountPlanInput,
    OrchestrationPlan,
    PlannedKick,
    apply_orchestration_plan,
    build_orchestration_plan,
    effective_orchestration_role,
    usable_session_minutes_for_account,
)
from .reset_defense import (
    ResetEvent,
    acknowledge_reset_events,
    account_impacts,
    append_reset_event,
    detect_reset_events,
    filter_reset_events,
    format_event_age,
    has_duplicate_reset_event,
    invalidate_event_pending_kicks,
    is_provider_reset_observation,
    load_reset_events,
    parse_utc,
    parse_since,
    recent_reset_events,
    record_reset_event_recovery_action as _record_reset_event_recovery_action,
    reset_events_csv,
)
from .scheduling import (
    CancelPendingKicksResult,
    PENDING_KICK_PURPOSE_COVERAGE,
    PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
    PendingKick,
    PendingKickStateError,
    ScheduleDecision,
    ScheduleReason,
    SchedulingWindowBasis,
    from_utc_iso,
    invalidate_pending_kicks,
    load_pending_kicks,
    local_timezone,
    mark_pending_notified,
    pending_kick_blocks_auto_kick,
    pending_kick_gave_up,
    pending_kick_next_action_at,
    pending_kick_retry_ready,
    record_pending_kick_failure,
    parse_work_window,
    recompute,
    remove_pending_kick,
    resolve_today_work_window,
    save_pending_kicks,
    schedule_for_account,
    select_scheduling_window,
    to_utc_iso,
    upsert_pending_kick,
    cancel_orchestrated_pending_kicks,
)
from .state_io import locked_atomic_write_text, locked_update_text
from .app_mode import (
    APP_SCHEMA_VERSION as APP_SCHEMA_VERSION,
    ERROR_ABORTED,
    ERROR_COMMAND,
    ERROR_INTERNAL,
    ERROR_STATE_FILE,
    ERROR_USAGE,
    app_mode_enabled,
    emit_app_error,
    emit_app_event as emit_app_event,
    emit_app_success,
)
from .mcp_setup import MCPSetupError, MCPSetupManager

# In app mode stdout is reserved for JSON; Rich rendering moves to stderr.
console = Console(width=120, stderr=app_mode_enabled())
_SETUP_PROGRESS_CALLBACK: Callable[[str | None], None] | None = None


def _setup_progress(message: str | None) -> bool:
    callback = _SETUP_PROGRESS_CALLBACK
    if callback is None:
        return False
    callback(message)
    return True


class TokenKickGroup(click.Group):
    """Click group that renders state-file corruption without a traceback."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except StateFileError as exc:
            raise click.ClickException(str(exc)) from exc

    def main(self, *args, standalone_mode=True, **kwargs):
        if not app_mode_enabled() or not standalone_mode:
            return super().main(*args, standalone_mode=standalone_mode, **kwargs)
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
        except click.exceptions.Abort:
            emit_app_error(ERROR_ABORTED, "Operation aborted.")
            sys.exit(1)
        except click.exceptions.UsageError as exc:
            emit_app_error(ERROR_USAGE, exc.format_message())
            sys.exit(exc.exit_code)
        except click.ClickException as exc:
            emit_app_error(ERROR_COMMAND, exc.format_message())
            sys.exit(exc.exit_code)
        except StateFileError as exc:
            emit_app_error(ERROR_STATE_FILE, str(exc))
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001 — app mode must never leak a traceback to stdout
            emit_app_error(ERROR_INTERNAL, f"{exc.__class__.__name__}: {exc}")
            sys.exit(1)
        if isinstance(result, int) and result != 0:
            sys.exit(result)
        return result


DAEMON_PID_FILE = CONFIG_DIR / "daemon.pid"
DAEMON_LOG_FILE = CONFIG_DIR / "daemon.log"
TELEGRAM_REMOTE_PID_FILE = CONFIG_DIR / "telegram-remote.pid"
TELEGRAM_REMOTE_LOG_FILE = CONFIG_DIR / "telegram-remote.log"
TELEGRAM_REMOTE_STATE_FILE = CONFIG_DIR / "telegram-remote-state.json"
UPGRADE_BACKGROUND_STATE_FILE = CONFIG_DIR / "upgrade-background-processes.json"
STATUS_CACHE_FILE = CONFIG_DIR / "status-cache.json"
STATUS_CACHE_REFRESH_LOCK_FILE = CONFIG_DIR / "status-cache-refresh.pid"
STATUS_CACHE_REFRESH_LOCK_MAX_AGE_SECONDS = 120
DAEMON_STOP_TIMEOUT_SECONDS = 5.0
DAEMON_STOP_POLL_SECONDS = 0.1
PHANTOM_SESSION_FILE = CONFIG_DIR / "phantom-sessions.json"
PHANTOM_RECOVERY_FILE = CONFIG_DIR / "phantom-recovery.json"
CODEX_PENDING_CONFIRMATIONS_FILE = CONFIG_DIR / "codex-pending-confirmations.json"
ANTIGRAVITY_PROBE_PROMPT_ID = "tokenkick-antigravity-probe-v1"
ANTIGRAVITY_PROBE_PROMPT = "Reply with exactly: OK"
ANTIGRAVITY_PROBE_DEFAULT_MODELS = {
    "gemini": "Gemini 3.5 Flash (Low)",
    "claude_gpt": "GPT-OSS 120B (Medium)",
}
ANTIGRAVITY_PROBE_RESET_CHANGE_TOLERANCE_SECONDS = 30.0
PHANTOM_SESSION_MIN_SECONDS = 20 * 60
PHANTOM_SESSION_MAX_USED_PERCENT = 2.0
PHANTOM_SESSION_FULL_RESET_RATIO = 0.95
PHANTOM_SESSION_INFERRED_FULL_RESET_RATIO = 0.90
PHANTOM_SESSION_MAX_AGE_SECONDS = 48 * 60 * 60
CODEX_PENDING_CONFIRMATION_MAX_AGE_SECONDS = 48 * 60 * 60
PHANTOM_SESSION_STUCK_MIN_SECONDS = 5 * 60
PHANTOM_SESSION_STUCK_TOLERANCE_SECONDS = 60
SESSION_KICK_WINDOW_START_GRACE_SECONDS = 120
RECENT_SESSION_KICK_DEDUP_SECONDS = 30 * 60
CLAUDE_RECONCILIATION_INTERVAL_SECONDS = 2 * 60 * 60
CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS = 15 * 60
AMBIGUOUS_PHANTOM_KICK_ERROR = "Provider still reports a tiny phantom session after the kick attempt"
PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR = "Codex accepted usage, but session status is still ambiguous"
CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR = "Codex generated output, but session anchor was not observed"
CODEX_SESSION_ANCHOR_MISALIGNED_ERROR = (
    "Codex generated output, but session timer did not start at kick time"
)
CODEX_SESSION_ANCHOR_PENDING_ERROR = (
    "Codex generated output, but provider status was too stale to confirm the session anchor"
)
CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR = (
    "Codex generated output, but provider movement was not confirmed"
)
CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR = (
    "Claude /usage completed, but session anchor was not observed"
)
AMBIGUOUS_PHANTOM_KICK_BACKOFF_SECONDS = 40 * 60
AMBIGUOUS_PHANTOM_KICK_MAX_ATTEMPTS = 2
PROVIDER_UNCHANGED_PHANTOM_KICK_BACKOFF_SECONDS = 10 * 60
PROVIDER_UNCHANGED_PHANTOM_KICK_MAX_ATTEMPTS = 3
PROVIDER_ACCEPTED_PHANTOM_BACKOFF_SECONDS = 45 * 60
CODEX_SESSION_ANCHOR_VERIFY_ATTEMPTS = 3
CODEX_SESSION_ANCHOR_VERIFY_DELAY_SECONDS = 5.0
CODEX_SESSION_ANCHOR_DELAYED_VERIFY_SECONDS = 900.0
CODEX_SURFACE_RETRY_BACKOFF_ENV = "TK_CODEX_SURFACE_RETRY_BACKOFF_SECONDS"
CODEX_FIRE_ALL_SURFACES_ENV = "TK_CODEX_FIRE_ALL_SURFACES"
CODEX_FIRE_ALL_SURFACE_GAP_ENV = "TK_CODEX_FIRE_ALL_SURFACE_GAP_SECONDS"
CODEX_FIRE_ALL_SURFACE_ORDER_ENV = "TK_CODEX_FIRE_ALL_SURFACE_ORDER"
CODEX_BURST_LADDER_ENABLED_ENV = "TK_CODEX_BURST_LADDER_ENABLED"
CODEX_BURST_LADDER_GAP_ENV = "TK_CODEX_BURST_LADDER_GAP_SECONDS"
CODEX_BURST_LADDER_ORDER_ENV = "TK_CODEX_BURST_LADDER_SURFACE_ORDER"
CODEX_CLUSTER_ORIGIN_BURST = "burst"
CODEX_CLUSTER_ORIGIN_ADAPTIVE = "adaptive"
CODEX_STRONG_ATTRIBUTION_DELTA_SECONDS = 30.0
CODEX_SESSION_ANCHOR_MATCH_TOLERANCE_SECONDS = 240.0
CODEX_LATE_ATTRIBUTION_LOOKBACK_SECONDS = 24 * 60 * 60
CODEX_DIRECT_GENERATED_PENDING_SURFACE_LIMIT = 4
CODEX_ATTRIBUTION_STRONG = "strong"
CODEX_ATTRIBUTION_TIMING_MATCH = "timing_match"
CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE = "external_possible"
PHANTOM_RECOVERY_MAX_ATTEMPTS = 5
PHANTOM_RECOVERY_ATTEMPT_INTERVAL_SECONDS = 45
PHANTOM_RECOVERY_COOLDOWN_SECONDS = 45 * 60
PHANTOM_RECOVERY_MAX_AGE_SECONDS = 48 * 60 * 60
PHANTOM_RECOVERY_DAEMON_SLEEP_FLOOR_SECONDS = 60
CODEX_KICK_STAGGER_SECONDS = 90
CODEX_KICK_RETRY_SURFACES = (
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
)
CODEX_FIRE_ALL_DEFAULT_SURFACES = (
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
)
CODEX_FIRE_ALL_SURFACES = CODEX_FIRE_ALL_DEFAULT_SURFACES
DORMANT_HINTS_FILE = CONFIG_DIR / "dormant-hints.json"
CODEXBAR_STATUS_SOURCE_DETAILS = {
    "codexbar-cli",
    "codexbar-history",
    "codexbar-http",
    "codexbar-snapshot",
}
_MIGRATION_COMPAT_EXPORTS = (
    CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE,
    CODEX_HOME_KEY_MIGRATION_KEY,
    DIRECT_SOURCE_APPSERVER_BACKUP_FILE,
    DIRECT_SOURCE_BACKUP_FILE,
    DIRECT_SOURCE_MIGRATION_KEY,
    LABEL_FORMAT_BACKUP_FILE,
    LABEL_FORMAT_MIGRATION_KEY,
)


def _utc_log_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote_log_value(value: str) -> str:
    return json.dumps(value)


def _format_log_line(event: str, **fields) -> str:
    parts = [f"{_utc_log_timestamp()} [{event}]"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = _quote_log_value(str(value))
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _daemon_log(event: str, **fields) -> None:
    console.print(_format_log_line(event, **fields), markup=False)


def _notification_skip_reason(config: NotifyConfig) -> str | None:
    if not config.enabled:
        return "disabled"
    if config.backend == "ntfy" and not config.ntfy_topic:
        return "missing_ntfy_topic"
    if config.backend == "telegram" and (
        not config.telegram_bot_token or not config.telegram_chat_id
    ):
        return "missing_telegram_credentials"
    if config.backend not in {"ntfy", "telegram"}:
        return "unknown_backend"
    return None


def _configured_notification_backends(config: NotifyConfig) -> list[str]:
    configured = normalize_notification_backends(config.enabled_backends)
    if configured is not None:
        return configured
    backend = (config.backend or "ntfy").strip().lower()
    return [backend] if backend in NOTIFICATION_BACKENDS else []


def _notification_backend_has_credentials(config: NotifyConfig, backend: str) -> bool:
    if backend == "ntfy":
        return bool(config.ntfy_topic)
    if backend == "telegram":
        return bool(config.telegram_bot_token and config.telegram_chat_id)
    return False


def _enabled_backends_for_notification_update(config: NotifyConfig) -> list[str]:
    configured = normalize_notification_backends(config.enabled_backends)
    if configured is not None:
        return configured
    backend = (config.backend or "ntfy").strip().lower()
    if config.enabled and backend in NOTIFICATION_BACKENDS and _notification_backend_has_credentials(config, backend):
        return [backend]
    return []


def _with_enabled_notification_backend(config: NotifyConfig, backend: str) -> list[str]:
    backends = _enabled_backends_for_notification_update(config)
    if backend not in backends:
        backends.append(backend)
    return backends


def _without_enabled_notification_backend(config: NotifyConfig, backend: str) -> list[str]:
    return [
        configured_backend
        for configured_backend in _enabled_backends_for_notification_update(config)
        if configured_backend != backend
    ]


def _primary_notification_backend(backends: list[str], fallback: str) -> str:
    if backends:
        return backends[-1]
    normalized = (fallback or "ntfy").strip().lower()
    return normalized if normalized in NOTIFICATION_BACKENDS else "ntfy"


def _notification_backends_for_account(account: AccountConfig, config: NotifyConfig) -> list[str]:
    if not config.enabled or not account.notifications_enabled:
        return []
    configured_backends = _configured_notification_backends(config)
    account_backends = normalize_notification_backends(account.notification_backends)
    if account_backends is not None:
        return [backend for backend in account_backends if backend in configured_backends]
    return configured_backends


def _notification_configs_for_account(
    account: AccountConfig,
    config: NotifyConfig,
) -> list[NotifyConfig]:
    backends = _notification_backends_for_account(account, config)
    if not backends:
        return [replace(config, enabled=False)]
    return [replace(config, enabled=True, backend=backend) for backend in backends]


def _notification_configs_for_global(config: NotifyConfig) -> list[NotifyConfig]:
    if not config.enabled:
        return [replace(config, enabled=False)]
    backends = _configured_notification_backends(config)
    if not backends:
        return [replace(config, enabled=False)]
    return [replace(config, backend=backend) for backend in backends]


def _send_global_notifications(
    config: NotifyConfig,
    send: Callable[[NotifyConfig], bool],
) -> bool:
    delivered_any = False
    for notification_config in _notification_configs_for_global(config):
        delivered_any = send(notification_config) or delivered_any
    return delivered_any


def _send_test_notification(
    config: NotifyConfig,
    backend: str,
    send: Callable[[NotifyConfig], bool],
) -> tuple[bool, str | None]:
    if backend == "all":
        return _send_global_notifications(config, send), None

    notification_config = replace(config, enabled=True, backend=backend)
    delivered = send(notification_config)
    if delivered:
        return True, None
    return False, _notification_skip_reason(notification_config) or "delivery_failed"


def _send_account_notifications(
    account: AccountConfig,
    config: NotifyConfig,
    send: Callable[[NotifyConfig], bool],
    *,
    daemon_log: bool,
    context: str,
) -> tuple[bool, bool]:
    delivered_any = False
    attempted = False
    skipped_only = True
    for notification_config in _notification_configs_for_account(account, config):
        attempted = True
        delivered = send(notification_config)
        _log_notification_result(
            account.label,
            notification_config,
            delivered,
            daemon_log=daemon_log,
            context=context,
        )
        delivered_any = delivered_any or bool(delivered)
        if _notification_skip_reason(notification_config) is None:
            skipped_only = False
    acknowledged = delivered_any or (attempted and skipped_only)
    return delivered_any, acknowledged


def _notification_route_display(account: AccountConfig, config: NotifyConfig) -> str:
    if not config.enabled or not account.notifications_enabled:
        return "❌ disabled"
    backends = _notification_backends_for_account(account, config)
    if not backends:
        return "❌ disabled"
    return "✅ " + "+".join(backends)


def _log_notification_result(
    account_label: str,
    config: NotifyConfig,
    delivered: bool | None,
    *,
    daemon_log: bool,
    context: str,
) -> None:
    if not daemon_log or delivered is None:
        return
    fields = {
        "account": account_label,
        "backend": config.backend,
        "context": context,
    }
    if delivered:
        _daemon_log("notification_sent", **fields)
        return
    reason = _notification_skip_reason(config)
    if reason is not None:
        _daemon_log("notification_skipped", **fields, reason=reason)
    else:
        _daemon_log("notification_failed", **fields, reason="delivery_failed")


def notify_dormant_account_for_account(account: AccountConfig, config: Config) -> bool:
    delivered, _acknowledged = _send_account_notifications(
        account,
        config.notifications,
        lambda notifications: notify_dormant_account(account.label, notifications),
        daemon_log=False,
        context="dormant",
    )
    return delivered


def _codex_pending_confirmation_key(account: AccountConfig, cluster_id: str) -> str:
    return f"{account_key_string(account)}::{cluster_id}"


def _load_codex_pending_confirmations() -> dict:
    if not CODEX_PENDING_CONFIRMATIONS_FILE.exists():
        return {}
    try:
        data = json.loads(CODEX_PENDING_CONFIRMATIONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    pruned = _prune_stale_codex_pending_confirmations(data)
    if pruned != data:
        _save_codex_pending_confirmations(pruned)
    return pruned


def _prune_stale_codex_pending_confirmations(data: dict) -> dict:
    cutoff = time.time() - CODEX_PENDING_CONFIRMATION_MAX_AGE_SECONDS
    pruned = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        last_touched = _to_float(
            value.get("recovery_attempt_finished_at")
            or value.get("last_attempt_finished_at")
            or value.get("notified_at")
        )
        if last_touched is None or last_touched < cutoff:
            continue
        pruned[key] = value
    return pruned


def _save_codex_pending_confirmations(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        locked_atomic_write_text(
            CODEX_PENDING_CONFIRMATIONS_FILE,
            json.dumps(data, indent=2, sort_keys=True) + "\n",
        )
    except OSError:
        return


def _codex_pending_session_confirmation_event(event: KickEvent) -> bool:
    return (
        event.success
        and not event.confirmed
        and (event.kick_type or event.kind) == "session"
        and bool(event.codex_cluster_id)
        and bool(event.codex_surface or event.codex_attempt is not None)
        and event.post_kick_status == "pending"
        and event.codex_confirmation_method == "pending_reset_clock"
        and bool(event.evidence_response or event.evidence_tokens or event.response_text)
    )


def _record_codex_pending_confirmation_notification(
    account: AccountConfig,
    event: KickEvent,
    *,
    delivered: bool | None,
    daemon_log: bool,
) -> None:
    if not daemon_log or delivered is not True:
        return
    if not _codex_pending_session_confirmation_event(event):
        return
    cluster_id = event.codex_cluster_id
    if not cluster_id:
        return
    data = _load_codex_pending_confirmations()
    key = _codex_pending_confirmation_key(account, cluster_id)
    data[key] = {
        "account_key": account_key_string(account),
        "account_label": account.label,
        "cluster_id": cluster_id,
        "notified_at": time.time(),
        "last_attempt_finished_at": event.codex_attempt_finished_at or event.timestamp,
        "surface": event.codex_surface,
        "attempt": event.codex_attempt,
        "max_attempts": event.codex_max_attempts,
        "recovery_in_flight": False,
        "recovery_cluster_id": None,
        "recovery_attempt_finished_at": None,
        "followup_sent_at": None,
    }
    _save_codex_pending_confirmations(data)
    _daemon_log(
        "codex_pending_confirmation_recorded",
        account=account.label,
        cluster_id=cluster_id,
        surface=event.codex_surface,
        attempt=event.codex_attempt,
    )


def _mark_codex_pending_confirmations_recovery_in_flight(
    result: "CodexSurfaceReintroductionResult",
    *,
    daemon_log: bool,
) -> None:
    if not result.records:
        return
    data = _load_codex_pending_confirmations()
    if not data:
        return
    changed = False
    for record in result.records:
        for key, value in list(data.items()):
            if not isinstance(value, dict):
                continue
            if value.get("account_key") != record.account_key:
                continue
            cluster_id = value.get("cluster_id")
            if (
                record.missed_cluster_id
                and cluster_id
                and str(cluster_id) != record.missed_cluster_id
            ):
                continue
            value["recovery_in_flight"] = True
            value["recovery_cluster_id"] = record.recovery_cluster_id
            value["recovery_attempt_finished_at"] = record.recovery_attempt_finished_at
            changed = True
            if record.recovery_cluster_id:
                recovery_key = f"{record.account_key}::{record.recovery_cluster_id}"
                if recovery_key != key and recovery_key in data:
                    data.pop(recovery_key, None)
            if daemon_log:
                _daemon_log(
                    "codex_pending_confirmation_deferred_for_recovery",
                    account=record.account_label,
                    cluster_id=cluster_id,
                    recovery_cluster_id=record.recovery_cluster_id,
                )
    if changed:
        _save_codex_pending_confirmations(data)


def _execute_codex_pending_confirmation_followups(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    config: Config,
    reintroductions: "CodexSurfaceReintroductionResult | None" = None,
    *,
    daemon_log: bool = False,
) -> int:
    if reintroductions is not None:
        _mark_codex_pending_confirmations_recovery_in_flight(
            reintroductions,
            daemon_log=daemon_log,
        )
    data = _load_codex_pending_confirmations()
    if not data:
        return 0
    accounts_by_key = {account_key_string(account): account for account in accounts}
    history = load_kick_history(limit=500)
    changed = False
    sent = 0
    for key, value in list(data.items()):
        if not isinstance(value, dict):
            data.pop(key, None)
            changed = True
            continue
        account_key = str(value.get("account_key") or "")
        account = accounts_by_key.get(account_key)
        if account is None:
            data.pop(key, None)
            changed = True
            if daemon_log:
                _daemon_log(
                    "codex_pending_confirmation_cleared",
                    account=value.get("account_label"),
                    cluster_id=value.get("cluster_id"),
                    reason="account_missing",
                )
            continue
        cluster_ids = {
            str(cluster_id)
            for cluster_id in (value.get("cluster_id"), value.get("recovery_cluster_id"))
            if cluster_id
        }
        if _codex_pending_confirmation_has_anchor(history, account.label, cluster_ids):
            data.pop(key, None)
            changed = True
            if daemon_log:
                _daemon_log(
                    "codex_pending_confirmation_cleared",
                    account=account.label,
                    cluster_id=value.get("cluster_id"),
                    reason="confirmed_anchor",
                )
            continue
        status = statuses_by_key.get(account_key)
        if status is None or status.stale or status.state == AccountState.UNKNOWN:
            if daemon_log:
                _daemon_log(
                    "codex_pending_confirmation_followup_skipped",
                    account=account.label,
                    cluster_id=value.get("cluster_id"),
                    reason="status_stale_or_unknown",
                )
            continue
        wait_after = _codex_pending_confirmation_wait_after(value)
        if wait_after is None:
            data.pop(key, None)
            changed = True
            continue
        if time.time() - wait_after < _codex_surface_retry_backoff_seconds(config):
            continue
        observed = _parse_status_cache_observed_at(status.observed_at) if status.observed_at else None
        if observed is None or observed.timestamp() <= wait_after:
            continue
        delivered, _acknowledged = _send_account_notifications(
            account,
            config.notifications,
            lambda notifications: notify_codex_pending_confirmation_missing(
                account.label,
                notifications,
            ),
            daemon_log=daemon_log,
            context="codex_pending_confirmation_followup",
        )
        data.pop(key, None)
        changed = True
        sent += 1
        if daemon_log:
            _daemon_log(
                "codex_pending_confirmation_followup_sent",
                account=account.label,
                cluster_id=value.get("cluster_id"),
                recovery_cluster_id=value.get("recovery_cluster_id"),
                delivered=bool(delivered),
            )
    if changed:
        _save_codex_pending_confirmations(data)
    return sent


def _codex_pending_confirmation_wait_after(value: dict) -> float | None:
    recovery_finished = _to_float(value.get("recovery_attempt_finished_at"))
    if value.get("recovery_in_flight") and recovery_finished is not None:
        return recovery_finished
    return _to_float(value.get("last_attempt_finished_at"))


def _codex_pending_confirmation_has_anchor(
    history: list[KickEvent],
    label: str,
    cluster_ids: set[str],
) -> bool:
    if not cluster_ids:
        return False
    reset_clock_methods = {"reset_clock", "late_reset_clock", "provider_moved"}
    for event in history:
        if event.label != label or event.codex_cluster_id not in cluster_ids:
            continue
        if not event.confirmed:
            continue
        if event.post_kick_status == "moved" or event.evidence_provider_moved:
            return True
        if event.codex_confirmation_method in reset_clock_methods:
            return True
    return False


def _codex_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
    return [account for account in accounts if account.provider == "codex"]


def _kickable_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
    return [account for account in accounts if account.provider in KICKABLE_PROVIDERS]


def _is_monitor_only_provider(provider: str) -> bool:
    return provider in MONITOR_ONLY_PROVIDERS


def _monitor_only_message(provider: str) -> str:
    if provider == "gemini":
        return GEMINI_MONITOR_ONLY_MESSAGE
    if provider == "antigravity":
        return ANTIGRAVITY_MONITOR_ONLY_MESSAGE
    return f'{provider} is monitor-only; kicking is disabled.'


def _monitor_only_auto_kick_message(provider: str) -> str:
    if provider == "gemini":
        return GEMINI_AUTO_KICK_DISABLED_MESSAGE
    if provider == "antigravity":
        return ANTIGRAVITY_AUTO_KICK_DISABLED_MESSAGE
    return f'{provider} is monitor-only; auto-kick cannot be enabled.'


def _auto_kick_kickable_accounts(accounts: list[AccountConfig]) -> list[AccountConfig]:
    return [
        account
        for account in _kickable_accounts(accounts)
        if account.auto_kick and account.visible
    ]


def _with_setup_auto_kick_defaults(
    accounts: list[AccountConfig],
    existing: Config,
) -> list[AccountConfig]:
    """Carry user-owned settings from saved accounts onto rediscovered ones.

    Known accounts merge through the shared ownership classification so user
    settings survive rediscovery; the pipeline label is kept because it
    already went through display-label rules. New accounts start with
    auto-kick disabled.
    """
    existing_by_key = {account_key_string(account): account for account in existing.accounts}
    updated_accounts: list[AccountConfig] = []
    for account in accounts:
        existing_account = existing_by_key.get(account_key_string(account))
        if existing_account is None:
            updated_accounts.append(
                replace(
                    account,
                    auto_kick=False,
                    weekly_auto_kick=False,
                    session_auto_kick=False,
                )
            )
            continue
        updated_accounts.append(
            merge_discovered_account(existing_account, account, preserve_label=False)
        )
    return updated_accounts


def _filter_status_pairs_by_visibility(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    show_all: bool,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    if show_all or not accounts:
        return accounts, statuses
    visible_pairs = [
        (account, status)
        for account, status in zip(accounts, statuses, strict=False)
        if account.visible
    ]
    return (
        [account for account, _status in visible_pairs],
        [status for _account, status in visible_pairs],
    )


def _save_config_like(config: Config, **overrides: object) -> None:
    data = dict(overrides)
    if "codex_burst_ladder_enabled" in overrides:
        data["codex_fire_all_surfaces"] = overrides["codex_burst_ladder_enabled"]
    if "codex_burst_ladder_gap_seconds" in overrides:
        data["codex_fire_all_surface_gap_seconds"] = overrides["codex_burst_ladder_gap_seconds"]
    if "codex_burst_ladder_surface_order" in overrides:
        data["codex_fire_all_surface_order"] = overrides["codex_burst_ladder_surface_order"]
    if "codex_fire_all_surfaces" in overrides:
        data["codex_burst_ladder_enabled"] = overrides["codex_fire_all_surfaces"]
    if "codex_fire_all_surface_gap_seconds" in overrides:
        data["codex_burst_ladder_gap_seconds"] = overrides["codex_fire_all_surface_gap_seconds"]
    if "codex_fire_all_surface_order" in overrides:
        data["codex_burst_ladder_surface_order"] = overrides["codex_fire_all_surface_order"]
    replace(config, **data).save()


def _set_account_visibility(
    label: str,
    visible: bool,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None

    updated = replace(account, visible=visible)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    state = "shown" if visible else "hidden"
    console.print(f'[green]Account "{label}" is now {state}.[/green]')
    return updated


def _set_account_notifications(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None

    updated = replace(account, notifications_enabled=enabled)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    state = "enabled" if enabled else "disabled"
    console.print(f'[green]Notifications {state} for "{label}".[/green]')
    return updated


def _set_account_notification_backends(
    label: str,
    backends: list[str] | None,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None

    normalized = normalize_notification_backends(backends)
    enabled = normalized is None or bool(normalized)
    updated = replace(account, notifications_enabled=enabled, notification_backends=normalized)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    route = "global default" if normalized is None else "+".join(normalized) if normalized else "disabled"
    console.print(f'[green]Notifications for "{label}" set to {route}.[/green]')
    return updated


def _set_usable_session_minutes(
    label: str,
    minutes: int,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None

    changed = account.usable_session_minutes != minutes
    updated = replace(account, usable_session_minutes=minutes)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    removed: list[PendingKick] = []
    if changed:
        removed = _invalidate_orchestrated_pending_for_account(account)
    console.print(f'[green]Usable session minutes for "{label}" set to {minutes}.[/green]')
    _print_orchestrated_pending_removals(removed)
    return updated


def _set_orchestration_role(
    label: str,
    role: str,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None

    normalized = normalize_orchestration_role(role)
    changed_accounts: list[AccountConfig] = []
    demoted_use_first: list[AccountConfig] = []
    updated_accounts: list[AccountConfig] = []
    updated: AccountConfig | None = None
    for candidate in accounts:
        if candidate.label == label:
            if candidate.orchestration_role != normalized:
                changed_accounts.append(candidate)
            updated = replace(candidate, orchestration_role=normalized)
            updated_accounts.append(updated)
            continue
        if normalized == "use_first" and candidate.orchestration_role == "use_first":
            changed_accounts.append(candidate)
            demoted_use_first.append(candidate)
            updated_accounts.append(
                replace(candidate, orchestration_role=DEFAULT_ORCHESTRATION_ROLE)
            )
            continue
        updated_accounts.append(candidate)

    _save_config_like(config, accounts=updated_accounts)
    removed: list[PendingKick] = []
    for changed_account in changed_accounts:
        removed.extend(_invalidate_orchestrated_pending_for_account(changed_account))
    console.print(
        f'[green]Orchestration role for "{label}" set to {_display_orchestration_role(normalized)}.[/green]'
    )
    if demoted_use_first:
        demoted_labels = ", ".join(f'"{candidate.label}"' for candidate in demoted_use_first)
        console.print(
            "[yellow]Only one account can be Use first; demoted "
            f"{demoted_labels} to {_display_orchestration_role(DEFAULT_ORCHESTRATION_ROLE)}.[/yellow]"
        )
    _print_orchestrated_pending_removals(removed)
    return updated


def _set_weekly_reserve_threshold(
    label: str,
    threshold: int | None,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if threshold is not None and not 1 <= threshold <= 99:
        console.print("[red]Weekly reserve threshold must be between 1 and 99.[/red]")
        raise click.exceptions.Exit(2)

    changed = account.weekly_reserve_threshold_percent != threshold
    updated = replace(account, weekly_reserve_threshold_percent=threshold)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    removed: list[PendingKick] = []
    if changed:
        removed = _invalidate_orchestrated_pending_for_account(account)
    if threshold is None:
        console.print(f'[green]Weekly reserve threshold cleared for "{label}".[/green]')
    else:
        console.print(f'[green]Weekly reserve threshold for "{label}" set to {threshold}%.[/green]')
    _print_orchestrated_pending_removals(removed)
    return updated


def _invalidate_orchestrated_pending_for_account(account: AccountConfig) -> list[PendingKick]:
    pending = load_pending_kicks(datetime.now(timezone.utc))
    key = account_key_string(account)
    current = pending.get(key)
    if current is None or current.reason != ScheduleReason.ORCHESTRATED.value:
        return []
    updated = dict(pending)
    removed = updated.pop(key)
    try:
        save_pending_kicks(updated)
    except PendingKickStateError as exc:
        console.print(
            f'[yellow]Orchestrated pending kick for "{account.label}" could not be '
            f"removed: {exc}[/yellow]"
        )
        return []
    return [removed]


def _print_orchestrated_pending_removals(removed: list[PendingKick]) -> None:
    if not removed:
        return
    noun = "kick" if len(removed) == 1 else "kicks"
    labels = ", ".join(f'"{pending.account_label}"' for pending in removed)
    console.print(
        "[yellow]"
        f"Removed {len(removed)} orchestrated pending {noun} for {labels} "
        "because account planning settings changed."
        "[/yellow]"
    )


def _ensure_auto_kick_consent(
    config: Config,
    provider: str,
    *,
    allow_prompt: bool,
    consent_token: str | None,
) -> bool:
    if config.has_auto_kick_consent(provider):
        return True
    if consent_token == AUTO_KICK_CONSENT_TOKEN:
        config.record_auto_kick_consent(provider)
        return True
    if not allow_prompt:
        raise AutoKickConsentRequired(provider)

    click.echo(auto_kick_consent_text(provider))
    response = click.prompt("", prompt_suffix="", default="", show_default=False)
    if response != AUTO_KICK_CONSENT_TOKEN:
        console.print(
            f"[yellow]Auto-kick remains off for {provider_display_name(provider)}.[/yellow]"
        )
        return False
    config.record_auto_kick_consent(provider)
    return True


def _set_auto_kick(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
    *,
    allow_consent_prompt: bool = True,
    consent_token: str | None = None,
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_auto_kick_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider not in KICKABLE_PROVIDERS:
        console.print(
            f'[yellow]Skipping "{label}": only Codex and Claude accounts support auto-kick.[/yellow]'
        )
        return None
    if enabled and not _ensure_auto_kick_consent(
        config,
        account.provider,
        allow_prompt=allow_consent_prompt,
        consent_token=consent_token,
    ):
        return None

    updated = replace(
        account,
        auto_kick=enabled,
        weekly_auto_kick=enabled,
        session_auto_kick=enabled,
    )
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    state = "enabled" if enabled else "disabled"
    console.print(f'[green]Auto-kick {state} for "{label}".[/green]')
    return updated


def _set_session_auto_kick(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
    *,
    allow_consent_prompt: bool = True,
    consent_token: str | None = None,
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_auto_kick_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider not in KICKABLE_PROVIDERS:
        console.print(
            f'[yellow]Skipping "{label}": only Codex and Claude accounts support session auto-kick.[/yellow]'
        )
        return None
    if enabled and not _ensure_auto_kick_consent(
        config,
        account.provider,
        allow_prompt=allow_consent_prompt,
        consent_token=consent_token,
    ):
        return None

    updated = replace(account, session_auto_kick=enabled)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    state = "enabled" if enabled else "disabled"
    console.print(f'[green]Session auto-kick {state} for "{label}".[/green]')
    return updated


def _set_weekly_auto_kick(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
    *,
    allow_consent_prompt: bool = True,
    consent_token: str | None = None,
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_auto_kick_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider not in KICKABLE_PROVIDERS:
        console.print(
            f'[yellow]Skipping "{label}": only Codex and Claude accounts support weekly auto-kick.[/yellow]'
        )
        return None
    if enabled and not _ensure_auto_kick_consent(
        config,
        account.provider,
        allow_prompt=allow_consent_prompt,
        consent_token=consent_token,
    ):
        return None

    updated = replace(account, weekly_auto_kick=enabled)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    state = "enabled" if enabled else "disabled"
    console.print(f'[green]Weekly auto-kick {state} for "{label}".[/green]')
    return updated


def _set_status_probe(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if account.provider != "claude":
        console.print("[yellow]Status probe is only supported for Claude accounts.[/yellow]")
        return None

    updated = replace(account, status_probe_enabled=enabled)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    if enabled:
        console.print(
            f'[yellow]Claude status probe enabled for "{label}". Routine status refreshes '
            "will consume a tiny amount of Claude quota.[/yellow]"
        )
    else:
        console.print(f'[green]Claude status probe disabled for "{label}".[/green]')
    return updated


def _set_direct_usage(
    label: str,
    enabled: bool,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_auto_kick_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider != "claude":
        console.print("[yellow]Direct /usage is only supported for Claude accounts.[/yellow]")
        return None

    updated = replace(account, direct_usage_enabled=enabled)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    config.accounts = updated_accounts
    config.save()
    state = "enabled" if enabled else "disabled"
    console.print(f'[green]Claude direct /usage {state} for "{label}".[/green]')
    return updated


def _set_global_claude_direct_usage(enabled: bool) -> Config:
    config = Config.load()
    config.claude.direct_usage_enabled = enabled
    config.claude.direct_usage_explicit = True
    config.save()
    state = "enabled" if enabled else "disabled"
    console.print(f"[green]Claude direct /usage globally {state}.[/green]")
    return config


def _set_kick_model(
    label: str,
    model: str | None,
    config: Config,
    accounts: list[AccountConfig],
) -> AccountConfig | None:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return None
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_auto_kick_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider not in KICKABLE_PROVIDERS:
        console.print(
            f'[yellow]Skipping "{label}": only Codex and Claude accounts support kick models.[/yellow]'
        )
        return None

    normalized = model.strip() if model is not None else None
    if model is not None and not normalized:
        console.print("[red]Kick model cannot be empty.[/red]")
        return None

    updated = replace(account, kick_model=normalized)
    updated_accounts = [
        updated if candidate.label == label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated_accounts)
    if normalized:
        console.print(f'[green]Kick model for "{label}" set to {normalized}.[/green]')
    else:
        default_model = kick_model_for_account(replace(account, kick_model=None))
        suffix = f" ({default_model})" if default_model else ""
        console.print(f'[green]Kick model for "{label}" reset to default{suffix}.[/green]')
    return updated


def _kickable_window_targets(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus] | None = None,
    config: Config | None = None,
    *,
    record_observations: bool = True,
    manage_phantom_recovery: bool = False,
    suppress_pending: bool = True,
) -> tuple[
    list[tuple[AccountConfig, AccountStatus]],
    list[tuple[AccountConfig, AccountStatus, int]],
]:
    history = load_kick_history(limit=200)
    targets: list[tuple[AccountConfig, AccountStatus]] = []
    deferred: list[tuple[AccountConfig, AccountStatus, int]] = []
    candidate_accounts = _auto_kick_kickable_accounts(accounts)

    now = datetime.now(timezone.utc)
    pending = load_pending_kicks(now)
    for account in candidate_accounts:
        key = account_key_string(account)
        pending_kick = pending.get(key)
        if suppress_pending and pending_kick_blocks_auto_kick(pending_kick, now):
            continue
        status = (
            statuses_by_key.get(key)
            if statuses_by_key is not None
            else None
        )
        if status is None:
            status = _fetch_status(account, config)
        if _auto_kick_blocked_by_codexbar_fallback(account, status):
            continue
        if status.stale:
            continue
        if record_observations:
            _observe_phantom_session_state(account, status)
        if _weekly_reset_ready(status):
            if not account.auto_kick:
                continue
            if not account.weekly_auto_kick:
                continue
            eligibility = _kick_eligibility(account, status, history=history)
            cooldown_remaining = eligibility.cooldown_remaining
            if cooldown_remaining is not None:
                deferred.append((account, status, cooldown_remaining))
                continue
            if eligibility.kickable:
                targets.append((account, status))
            continue
        if (
            manage_phantom_recovery
            and account.provider == "codex"
            and account.session_auto_kick
            and _is_phantom_session_candidate(status)
        ):
            if _phantom_recovery_should_manage(account, status):
                cooldown_remaining = _phantom_recovery_defer_seconds(account)
                if cooldown_remaining is not None:
                    deferred.append((account, status, cooldown_remaining))
            continue
        if _long_kick_eligible(status):
            if not account.auto_kick:
                continue
            if not account.weekly_auto_kick:
                continue
            eligibility = _kick_eligibility(account, status, history=history)
            cooldown_remaining = eligibility.cooldown_remaining
            if cooldown_remaining is not None:
                deferred.append((account, status, cooldown_remaining))
                continue
            if eligibility.kickable:
                targets.append((account, status))
            continue
        if not account.session_auto_kick:
            continue
        eligibility = _kick_eligibility(account, status, history=history)
        if not eligibility.kickable or eligibility.kick_type != "session":
            continue
        targets.append((account, status))
    return targets, deferred


def _daemon_log_target_scan(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    *,
    manage_phantom_recovery: bool = False,
) -> None:
    history = load_kick_history(limit=200)
    pending = load_pending_kicks(datetime.now(timezone.utc))
    for account in _auto_kick_kickable_accounts(accounts):
        status = statuses_by_key.get(account_key_string(account))
        if status is None:
            _daemon_log("target_scan", account=account.label, reason="missing_status")
            continue
        phantom_management_reason = None
        phantom_management_cooldown = None
        if (
            manage_phantom_recovery
            and account.provider == "codex"
            and account.session_auto_kick
            and _is_phantom_session_candidate(status)
            and not _weekly_quota_exhausted(status)
        ):
            if _phantom_recovery_should_manage(account, status):
                phantom_management_cooldown = _phantom_recovery_defer_seconds(account)
                phantom_management_reason = (
                    "phantom_recovery_deferred"
                    if phantom_management_cooldown is not None
                    else "phantom_recovery_managed"
                )
            else:
                phantom_management_reason = "phantom_observing"
        pending_kick = pending.get(account_key_string(account))
        eligibility = _kick_eligibility(
            account,
            status,
            history=history,
            pending_kick=pending_kick,
        )
        if phantom_management_reason is not None:
            eligibility = KickEligibility(
                False,
                reason=phantom_management_reason,
                cooldown_remaining=phantom_management_cooldown,
            )
        if not _should_log_target_scan(status, eligibility):
            continue
        _daemon_log(
            "target_scan",
            account=account.label,
            state=status.state.value,
            reason=eligibility.reason or ("kickable" if eligibility.kickable else "not_kickable"),
            kick_type=eligibility.kick_type,
            cooldown_remaining=eligibility.cooldown_remaining,
            session_resets_in=status.session_resets_in_seconds,
            session_used=status.session_used_percent,
            window_anchor_state=status.window_anchor_state,
        )


def _should_log_target_scan(status: AccountStatus, eligibility: KickEligibility) -> bool:
    if eligibility.kickable or status.state == AccountState.FRESH:
        return True
    if status.session_resets_in_seconds is None:
        return False
    return (
        status.session_resets_in_seconds <= 20 * 60
        or _session_reset_is_near_full(status)
    )


@dataclass(frozen=True)
class KickEligibility:
    kickable: bool
    kick_type: str | None = None
    cooldown_remaining: int | None = None
    reason: str | None = None


@dataclass
class KickStaggerState:
    last_codex_kick_at: float | None = None


@dataclass
class CodexSurfaceReintroductionRecord:
    account_key: str
    account_label: str
    missed_cluster_id: str | None
    recovery_cluster_id: str | None
    recovery_attempt_finished_at: float


@dataclass
class CodexSurfaceReintroductionResult:
    count: int = 0
    records: list[CodexSurfaceReintroductionRecord] = dataclass_field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.count == other
        return super().__eq__(other)

    def __bool__(self) -> bool:
        return self.count > 0


def _stagger_codex_kick_if_needed(
    account: AccountConfig,
    stagger_state: KickStaggerState | None,
    *,
    daemon_log: bool = False,
) -> None:
    if stagger_state is None or account.provider != "codex":
        return

    now = time.monotonic()
    if stagger_state.last_codex_kick_at is not None:
        wait_seconds = CODEX_KICK_STAGGER_SECONDS - (now - stagger_state.last_codex_kick_at)
        if wait_seconds > 0:
            rounded_wait = int(wait_seconds + 0.999)
            if daemon_log:
                _daemon_log(
                    "codex_kick_stagger",
                    account=account.label,
                    sleep_seconds=rounded_wait,
                )
            else:
                console.print(
                    f"[dim]Waiting {_format_duration(rounded_wait)} before next Codex kick.[/dim]"
                )
            time.sleep(wait_seconds)

    stagger_state.last_codex_kick_at = time.monotonic()


def _kick_eligibility(
    account: AccountConfig | None,
    status: AccountStatus,
    provider: str | None = None,
    *,
    history: list[KickEvent] | None = None,
    pending_kick: PendingKick | None = None,
    allow_stale: bool = False,
) -> KickEligibility:
    resolved_provider = provider or (account.provider if account is not None else None)
    if resolved_provider not in KICKABLE_PROVIDERS:
        return KickEligibility(False, reason="provider_not_kickable")
    if status.state != AccountState.FRESH and _weekly_quota_exhausted(status):
        return KickEligibility(False, reason="weekly_exhausted")
    if _session_quota_exhausted(status):
        return KickEligibility(False, reason="session_exhausted")
    if status.stale and not allow_stale:
        return KickEligibility(False, reason="stale_status")
    if _auto_kick_blocked_by_codexbar_fallback(account, status):
        return KickEligibility(False, reason="codexbar_fallback")
    if pending_kick is not None:
        return KickEligibility(False, reason="pending_kick")
    if _weekly_reset_ready(status):
        if (
            account is not None
            and history is not None
            and _was_kicked_in_current_window(account, status, history)
        ):
            return KickEligibility(False, reason="already_kicked")
        if (
            account is not None
            and history is not None
            and _was_pending_codex_fire_all_in_current_window(
                account,
                status,
                history,
                kick_type="kick",
            )
        ):
            return KickEligibility(False, reason="codex_awaiting_confirmation")
        return KickEligibility(True, kick_type="kick")
    phantom_session = account is not None and _is_phantom_session_candidate(status)
    confirmed_phantom_session = (
        account is not None
        and phantom_session
        and _phantom_session_ready(account, status, record_observation=False)
    )
    if account is not None and phantom_session:
        recovery_defer = _phantom_recovery_defer_seconds(account)
        if recovery_defer is not None:
            return KickEligibility(
                False,
                reason="phantom_recovery_backoff",
                cooldown_remaining=recovery_defer,
            )
    if (
        account is not None
        and history is not None
        and status.state != AccountState.FRESH
        and not phantom_session
        and _recent_confirmed_session_kick(account, history)
    ):
        return KickEligibility(False, reason="recent_session_kick")
    if (
        account is not None
        and history is not None
        and phantom_session
        and _was_kicked_in_current_session_window(account, status, history)
    ):
        if _phantom_session_ready(account, status, record_observation=False):
            return KickEligibility(False, reason="provider_unchanged")
        return KickEligibility(False, reason="already_session_kicked")
    provider_unchanged_retry_ready = False
    if account is not None and history is not None and phantom_session:
        if (
            _ambiguous_phantom_kick_attempt_count(account, history)
            >= AMBIGUOUS_PHANTOM_KICK_MAX_ATTEMPTS
        ):
            return KickEligibility(False, reason="phantom_unresolved")
        if _ambiguous_phantom_kick_backoff_until(account, history) is not None:
            return KickEligibility(False, reason="phantom_backoff")
        provider_unchanged_attempts = _provider_unchanged_phantom_kick_attempt_count(
            account,
            status,
            history,
        )
        if provider_unchanged_attempts >= PROVIDER_UNCHANGED_PHANTOM_KICK_MAX_ATTEMPTS:
            return KickEligibility(False, reason="phantom_unresolved")
        if provider_unchanged_attempts > 0:
            if _provider_unchanged_phantom_kick_backoff_until(account, status, history) is not None:
                return KickEligibility(False, reason="provider_unchanged_backoff")
            provider_unchanged_retry_ready = True
    if _session_kick_eligible(account, status, resolved_provider, allow_stale=allow_stale):
        if (
            account is not None
            and history is not None
            and not provider_unchanged_retry_ready
            and _was_kicked_in_current_session_window(account, status, history)
        ):
            return KickEligibility(False, reason="already_session_kicked")
        if (
            account is not None
            and history is not None
            and not provider_unchanged_retry_ready
            and _was_pending_codex_fire_all_in_current_window(
                account,
                status,
                history,
                kick_type="session",
            )
        ):
            return KickEligibility(False, reason="codex_awaiting_confirmation")
        return KickEligibility(True, kick_type="session")
    if (
        account is not None
        and status.state != AccountState.FRESH
        and phantom_session
    ):
        if not confirmed_phantom_session and not provider_unchanged_retry_ready:
            return KickEligibility(False, reason=status.state.value)
        if (
            history is not None
            and not provider_unchanged_retry_ready
            and _was_kicked_in_current_session_window(account, status, history)
        ):
            return KickEligibility(False, reason="already_session_kicked")
        if (
            history is not None
            and not provider_unchanged_retry_ready
            and _was_pending_codex_fire_all_in_current_window(
                account,
                status,
                history,
                kick_type="session",
            )
        ):
            return KickEligibility(False, reason="codex_awaiting_confirmation")
        return KickEligibility(True, kick_type="session")
    if status.state != AccountState.FRESH:
        return KickEligibility(False, reason=status.state.value)
    if (
        account is not None
        and history is not None
        and _was_kicked_in_current_window(account, status, history)
    ):
        return KickEligibility(False, reason="already_kicked")
    return KickEligibility(True, kick_type="kick")


def _auto_kick_blocked_by_codexbar_fallback(
    account: AccountConfig | None,
    status: AccountStatus,
) -> bool:
    return (
        account is not None
        and account.provider == "codex"
        and account.source == DataSource.CODEX_DIRECT
        and status.source_detail in CODEXBAR_STATUS_SOURCE_DETAILS
    )


def _weekly_quota_exhausted(status: AccountStatus) -> bool:
    return weekly_quota_exhausted(status)


def _session_quota_exhausted(status: AccountStatus) -> bool:
    return (
        status.state != AccountState.FRESH
        and status.session_used_percent is not None
        and status.session_used_percent >= 100.0
        and status.session_resets_in_seconds is not None
        and status.session_resets_in_seconds > 0
    )


def _long_kick_eligible(status: AccountStatus) -> bool:
    return status.state == AccountState.FRESH or _weekly_reset_ready(status)


def _weekly_reset_ready(status: AccountStatus) -> bool:
    return (
        status.state != AccountState.UNKNOWN
        and status.resets_in_seconds is not None
        and status.resets_in_seconds <= 0
    )


def _session_kick_eligible(
    account: AccountConfig | None,
    status: AccountStatus,
    provider: str | None = None,
    *,
    allow_stale: bool = False,
) -> bool:
    resolved_provider = provider or (account.provider if account is not None else None)
    if resolved_provider not in KICKABLE_PROVIDERS:
        return False
    if status.state in {AccountState.FRESH, AccountState.UNKNOWN}:
        return False
    if status.stale and not allow_stale:
        return False
    if status.session_window_minutes is None:
        return False
    if _is_unanchored_session_candidate(status, resolved_provider):
        return True
    if status.session_resets_in_seconds is not None:
        return status.session_resets_in_seconds <= 0
    if resolved_provider == "claude":
        return False
    return status.session_used_percent == 0.0


def _session_boundary_grace_active(status: AccountStatus) -> bool:
    return (
        status.state == AccountState.ACTIVE
        and status.session_window_minutes is not None
        and status.session_resets_in_seconds is not None
        and 0 < status.session_resets_in_seconds <= SESSION_KICK_WINDOW_START_GRACE_SECONDS
    )


def _claude_predicted_session_due_status(
    account: AccountConfig,
    entries: dict[str, dict],
) -> AccountStatus | None:
    if account.provider != "claude" or not account.session_auto_kick:
        return None
    entry = entries.get(account_key_string(account))
    if not isinstance(entry, dict):
        return None
    if _claude_cached_due_blocked_by_direct_probe_error(entry):
        return None
    status = entry.get("status")
    if not isinstance(status, AccountStatus):
        return None
    if status.source_detail != "claude-cli-usage":
        return None
    if status.state in {AccountState.FRESH, AccountState.UNKNOWN}:
        return None
    if status.session_window_minutes != 300:
        return None
    if status.session_resets_in_seconds is None or status.session_resets_in_seconds > 0:
        return None
    return mark_synthetic_status(
        replace(status, label=account.label, stale=False, stale_seconds=None, error=None),
        "claude_session_due_from_cache",
    )


def _claude_cached_due_blocked_by_direct_probe_error(entry: dict) -> bool:
    error = entry.get("last_direct_probe_error")
    if not isinstance(error, ClaudeProbeError):
        return False
    if error.category == ClaudeProbeErrorCategory.DISABLED:
        return True

    last_probe = _cached_claude_probe_time(entry.get("last_direct_probe_at"))
    last_success = _cached_claude_probe_time(entry.get("last_direct_success_at"))
    if last_probe is None:
        return False
    if last_success is None:
        return True
    return last_probe >= last_success


def _cached_claude_probe_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return from_utc_iso(value)
    except ValueError:
        return None


def _apply_claude_predicted_session_due_statuses(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    entries: dict[str, dict],
    *,
    daemon_log: bool = False,
) -> None:
    for account in accounts:
        key = account_key_string(account)
        current = statuses_by_key.get(key)
        if current is not None and current.state != AccountState.UNKNOWN and not current.stale:
            continue
        predicted = _claude_predicted_session_due_status(account, entries)
        if predicted is None:
            continue
        statuses_by_key[key] = predicted
        if daemon_log:
            _daemon_log(
                "claude_session_due_from_cache",
                account=account.label,
                session_resets_in=predicted.session_resets_in_seconds,
            )


def _codex_predicted_session_due_status(
    account: AccountConfig,
    entries: dict[str, dict],
) -> AccountStatus | None:
    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or not account.auto_kick
        or not account.session_auto_kick
    ):
        return None
    entry = entries.get(account_key_string(account))
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    if not isinstance(status, AccountStatus):
        return None
    if _auto_kick_blocked_by_codexbar_fallback(account, status):
        return None
    if _weekly_quota_exhausted(status):
        return None
    if status.state in {AccountState.FRESH, AccountState.UNKNOWN}:
        return None
    if status.session_resets_in_seconds is None or status.session_resets_in_seconds > 0:
        return None
    if _effective_session_window_minutes(status) != 300:
        return None
    return mark_synthetic_status(
        replace(status, label=account.label, stale=False, stale_seconds=None, error=None),
        "codex_session_due_from_cache",
    )


def _apply_codex_predicted_session_due_statuses(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    entries: dict[str, dict],
    *,
    daemon_log: bool = False,
) -> None:
    for account in accounts:
        key = account_key_string(account)
        current = statuses_by_key.get(key)
        if current is not None and current.state != AccountState.UNKNOWN and not current.stale:
            continue
        predicted = _codex_predicted_session_due_status(account, entries)
        if predicted is None:
            continue
        statuses_by_key[key] = predicted
        if daemon_log:
            _daemon_log(
                "codex_session_due_from_cache",
                account=account.label,
                session_resets_in=predicted.session_resets_in_seconds,
                stale_seconds=predicted.stale_seconds,
            )


def _claude_reconciliation_due(
    account: AccountConfig,
    entries: dict[str, dict],
    config: Config,
    *,
    now: datetime | None = None,
) -> AccountStatus | None:
    if (
        account.provider != "claude"
        or not account.auto_kick
        or not account.session_auto_kick
        or not account.direct_usage_enabled
        or not config.claude.direct_usage_enabled
    ):
        return None
    entry = entries.get(account_key_string(account))
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    if not isinstance(status, AccountStatus):
        return None
    if status.source_detail != "claude-cli-usage":
        return None
    if _session_kick_eligible(account, status):
        return None
    last_direct_at = (
        entry.get("last_direct_success_at")
        or status.observed_at
        or entry.get("provider_observed_at")
    )
    observed = _parse_status_cache_observed_at(last_direct_at) if isinstance(last_direct_at, str) else None
    if observed is None:
        return None
    now = now or _status_cache_now()
    age_seconds = int((now - observed).total_seconds())
    if age_seconds < CLAUDE_RECONCILIATION_INTERVAL_SECONDS:
        return None
    return replace(status, label=account.label, stale=False, stale_seconds=None, error=None)


def _execute_claude_reconciliation_probes(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    config: Config,
    entries: dict[str, dict],
    *,
    daemon_log: bool = False,
) -> int:
    executed = 0
    now = _status_cache_now()
    for account in accounts:
        key = account_key_string(account)
        cached_status = _claude_reconciliation_due(account, entries, config, now=now)
        if cached_status is None:
            continue
        current_status = statuses_by_key.get(key)
        if current_status is not None and _session_kick_eligible(account, current_status):
            continue
        if daemon_log:
            _daemon_log("claude_reconcile_start", account=account.label)
        event, status = _run_claude_usage_touch(
            account,
            config,
            kind="reconcile",
            kick_type="status_probe",
            success_response="Claude /usage reconciliation completed.",
            failure_prefix="Claude /usage reconciliation failed",
            daemon_log=daemon_log,
        )
        if status is not None and event.success:
            anchored = _claude_reconciliation_anchored_session(cached_status, status)
            if anchored:
                event.kind = "session"
                event.kick_type = "session"
                event.response_text = "Claude /usage reconciliation anchored session window."
                event.evidence_provider_moved = True
                event.post_kick_status = "moved"
            else:
                event.confirmed = False
                event.error = CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR
                event.evidence_provider_moved = False
                event.post_kick_status = "unchanged"
            statuses_by_key[key] = replace(status, label=account.label)
        append_kick_event(event)
        executed += 1
        if daemon_log:
            event_name = (
                "claude_reconcile_confirmed"
                if event.success and event.confirmed
                else "claude_reconcile_attempted"
                if event.success
                else "claude_reconcile_failed"
            )
            _daemon_log(
                event_name,
                account=account.label,
                anchored=event.kind == "session",
                error=event.error,
                **_kick_evidence_log_fields(event),
            )
        if event.success and event.kind == "session":
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(event, notifications),
                daemon_log=daemon_log,
                context="claude_reconcile",
            )
    return executed


def _claude_reconciliation_anchored_session(
    before: AccountStatus,
    after: AccountStatus,
) -> bool:
    before_resets = before.session_resets_in_seconds
    after_resets = after.session_resets_in_seconds
    if before_resets is None or after_resets is None:
        return False
    return after_resets - before_resets >= CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS


def _kick_type_for_status(status: AccountStatus) -> str:
    return "kick" if _long_kick_eligible(status) else "session"


def _session_cooldown_remaining(
    account: AccountConfig | None,
    status: AccountStatus,
    *,
    record_observation: bool = True,
) -> int | None:
    if status.window_anchor_state == "available_unanchored":
        if account is not None and record_observation:
            _clear_phantom_session_observation(account)
        return None
    if status.session_resets_in_seconds is None or status.session_resets_in_seconds <= 0:
        if account is not None and record_observation:
            _clear_phantom_session_observation(account)
        return None
    if status.session_used_percent == 0.0:
        if account is not None and record_observation:
            _clear_phantom_session_observation(account)
        return None
    if (
        account is not None
        and _phantom_session_ready(account, status, record_observation=record_observation)
    ):
        return None
    return status.session_resets_in_seconds


def _observe_phantom_session_state(account: AccountConfig, status: AccountStatus) -> None:
    if status.window_anchor_state == "available_unanchored":
        _clear_phantom_session_observation(account)
        return
    if status.session_resets_in_seconds is None or status.session_resets_in_seconds <= 0:
        _clear_phantom_session_observation(account)
        return
    if status.session_used_percent == 0.0:
        _clear_phantom_session_observation(account)
        return
    observation = _load_phantom_session_observations().get(_phantom_session_key(account))
    if isinstance(observation, dict) and _phantom_session_observation_resolved(
        observation,
        status,
    ):
        _clear_phantom_session_observation(account)
        return
    age_seconds = 0.0
    if isinstance(observation, dict):
        age_seconds = time.time() - float(observation.get("first_seen_at", time.time()))
    countdown_stuck = (
        isinstance(observation, dict)
        and _phantom_session_countdown_stuck(observation, status, age_seconds=age_seconds)
    )
    if (
        _is_phantom_session_observable(account, status)
        and (_is_phantom_session_candidate(status) or countdown_stuck)
    ):
        _record_phantom_session_observation(account, status)
        return
    _clear_phantom_session_observation(account)


def _observe_phantom_session_states(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
) -> None:
    for account, status in zip(accounts, statuses, strict=False):
        _observe_phantom_session_state(account, status)


def _phantom_session_ready(
    account: AccountConfig,
    status: AccountStatus,
    *,
    record_observation: bool,
) -> bool:
    if not _is_phantom_session_observable(account, status):
        if record_observation:
            _clear_phantom_session_observation(account)
        return False

    if record_observation:
        observation = _record_phantom_session_observation(account, status)
    else:
        observation = _load_phantom_session_observations().get(_phantom_session_key(account))
    if not observation:
        return False

    first_seen_at = float(observation.get("first_seen_at", 0))
    age_seconds = time.time() - first_seen_at
    return (
        (
            age_seconds >= PHANTOM_SESSION_MIN_SECONDS
            and _session_reset_is_near_full(status)
        )
        or _phantom_session_countdown_stuck(observation, status, age_seconds=age_seconds)
    )


def _phantom_session_observation_resolved(
    observation: dict,
    status: AccountStatus,
    *,
    now: float | None = None,
) -> bool:
    if status.window_anchor_state != "anchored":
        return False
    previous_reset_at = _to_float(observation.get("session_resets_at"))
    current_reset_at = _to_float(status.session_resets_at)
    last_seen_at = _to_float(observation.get("last_seen_at"))
    if previous_reset_at is None or current_reset_at is None or last_seen_at is None:
        return False
    current = time.time() if now is None else now
    elapsed = current - last_seen_at
    if elapsed < PHANTOM_SESSION_STUCK_TOLERANCE_SECONDS:
        return False
    allowed_shift = max(
        5.0,
        min(PHANTOM_SESSION_STUCK_TOLERANCE_SECONDS, elapsed * 0.25),
    )
    return abs(current_reset_at - previous_reset_at) <= allowed_shift


def _phantom_session_countdown_stuck(
    observation: dict,
    status: AccountStatus,
    *,
    age_seconds: float,
) -> bool:
    if int(observation.get("observations", 0)) < 2:
        return False
    first_resets = _to_float(
        observation.get("first_session_resets_in_seconds")
        or observation.get("session_resets_in_seconds")
    )
    current_resets = _to_float(status.session_resets_in_seconds)
    if first_resets is None or current_resets is None:
        return False
    if int(observation.get("observations", 0)) >= 2:
        first_reset_at = _to_float(observation.get("first_session_resets_at"))
        current_reset_at = _to_float(status.session_resets_at)
        if (
            first_reset_at is not None
            and current_reset_at is not None
            and current_reset_at - first_reset_at > PHANTOM_SESSION_STUCK_TOLERANCE_SECONDS
        ):
            return True
    if age_seconds < PHANTOM_SESSION_STUCK_MIN_SECONDS:
        return False
    actual_drop = max(0.0, first_resets - current_resets)
    expected_drop = min(age_seconds, first_resets)
    tolerated_drop = max(
        PHANTOM_SESSION_STUCK_TOLERANCE_SECONDS,
        expected_drop * 0.5,
    )
    return actual_drop < tolerated_drop


def _is_phantom_session_candidate(status: AccountStatus) -> bool:
    weekly_window = status.window_minutes or 0
    session_window = _effective_session_window_minutes(status) or 0
    session_resets = status.session_resets_in_seconds or 0
    full_reset_ratio = _phantom_session_full_reset_ratio(status)
    return (
        status.state in {AccountState.FRESH, AccountState.ACTIVE}
        and weekly_window >= 10080
        and session_window == 300
        and status.session_used_percent is not None
        and 0.0 < status.session_used_percent <= PHANTOM_SESSION_MAX_USED_PERCENT
        and session_resets >= int(session_window * 60 * full_reset_ratio)
    )


def _is_phantom_session_observable(
    account: AccountConfig,
    status: AccountStatus,
) -> bool:
    weekly_window = status.window_minutes or 0
    session_window = _effective_session_window_minutes(status) or 0
    session_resets = status.session_resets_in_seconds or 0
    return (
        account.provider == "codex"
        and status.window_anchor_state != "available_unanchored"
        and status.state in {AccountState.FRESH, AccountState.ACTIVE}
        and weekly_window >= 10080
        and session_window == 300
        and status.session_used_percent is not None
        and 0.0 < status.session_used_percent <= PHANTOM_SESSION_MAX_USED_PERCENT
        and session_resets > 0
    )


def _is_unanchored_session_candidate(
    status: AccountStatus,
    provider: str | None = None,
) -> bool:
    session_window = status.session_window_minutes or 0
    session_resets = status.session_resets_in_seconds or 0
    return (
        provider == "codex"
        and status.state == AccountState.ACTIVE
        and session_window == 300
        and status.session_used_percent == 0.0
        and session_resets >= int(session_window * 60 * PHANTOM_SESSION_FULL_RESET_RATIO)
    )


def _load_phantom_session_observations() -> dict:
    if not PHANTOM_SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(PHANTOM_SESSION_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    pruned = _prune_stale_phantom_session_observations(data)
    if pruned != data:
        _save_phantom_session_observations(pruned)
    return pruned


def _prune_stale_phantom_session_observations(data: dict) -> dict:
    cutoff = time.time() - PHANTOM_SESSION_MAX_AGE_SECONDS
    pruned = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        last_seen_at = _to_float(value.get("last_seen_at"))
        if last_seen_at is None or last_seen_at < cutoff:
            continue
        pruned[key] = value
    return pruned


def _prune_phantom_session_observations_for_accounts(accounts: list[AccountConfig]) -> None:
    data = _load_phantom_session_observations()
    if not data:
        return
    active_keys = {_phantom_session_key(account) for account in accounts}
    pruned = {key: value for key, value in data.items() if key in active_keys}
    if pruned != data:
        _save_phantom_session_observations(pruned)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _save_phantom_session_observations(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PHANTOM_SESSION_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def _record_phantom_session_observation(account: AccountConfig, status: AccountStatus) -> dict:
    data = _load_phantom_session_observations()
    key = _phantom_session_key(account)
    now = time.time()
    current = data.get(key) if isinstance(data.get(key), dict) else {}
    first_seen_at = float(current.get("first_seen_at", now))
    observation = {
        "first_seen_at": first_seen_at,
        "last_seen_at": now,
        "observations": int(current.get("observations", 0)) + 1,
        "first_session_resets_in_seconds": current.get(
            "first_session_resets_in_seconds",
            status.session_resets_in_seconds,
        ),
        "first_session_resets_at": current.get(
            "first_session_resets_at",
            status.session_resets_at,
        ),
        "session_used_percent": status.session_used_percent,
        "session_resets_in_seconds": status.session_resets_in_seconds,
        "session_resets_at": status.session_resets_at,
        "weekly_used_percent": status.used_percent,
    }
    data[key] = observation
    _save_phantom_session_observations(data)
    return observation


def _clear_phantom_session_observation(account: AccountConfig) -> None:
    data = _load_phantom_session_observations()
    key = _phantom_session_key(account)
    if key not in data:
        return
    data.pop(key)
    _save_phantom_session_observations(data)


def _load_phantom_recovery_state() -> dict:
    if not PHANTOM_RECOVERY_FILE.exists():
        return {}
    try:
        data = json.loads(PHANTOM_RECOVERY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    pruned = _prune_stale_phantom_recovery_state(data)
    if pruned != data:
        _save_phantom_recovery_state(pruned)
    return pruned


def _prune_stale_phantom_recovery_state(data: dict) -> dict:
    cutoff = time.time() - PHANTOM_RECOVERY_MAX_AGE_SECONDS
    pruned = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        last_seen_at = _to_float(value.get("last_seen_at"))
        cooldown_until = _to_float(value.get("cooldown_until"))
        if cooldown_until is not None and cooldown_until >= time.time():
            pruned[key] = value
            continue
        if last_seen_at is None or last_seen_at < cutoff:
            continue
        pruned[key] = value
    return pruned


def _save_phantom_recovery_state(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PHANTOM_RECOVERY_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        return


def _phantom_recovery_state_for(account: AccountConfig) -> dict | None:
    state = _load_phantom_recovery_state().get(_phantom_session_key(account))
    return state if isinstance(state, dict) else None


def _clear_phantom_recovery_state(account: AccountConfig) -> None:
    data = _load_phantom_recovery_state()
    key = _phantom_session_key(account)
    if key not in data:
        return
    data.pop(key)
    _save_phantom_recovery_state(data)


def _phantom_recovery_should_manage(account: AccountConfig, status: AccountStatus) -> bool:
    if account.provider != "codex" or not account.auto_kick or not account.session_auto_kick:
        return False
    if _phantom_recovery_state_for(account) is not None:
        return True
    return _is_phantom_session_candidate(status) and _phantom_session_ready(
        account,
        status,
        record_observation=False,
    )


def _phantom_recovery_defer_seconds(account: AccountConfig, now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    state = _phantom_recovery_state_for(account)
    if not state:
        return None
    cooldown_until = _to_float(state.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > now:
        return max(1, int(cooldown_until - now))
    last_attempt_at = _to_float(state.get("last_attempt_at"))
    if last_attempt_at is None:
        return None
    next_attempt_at = last_attempt_at + PHANTOM_RECOVERY_ATTEMPT_INTERVAL_SECONDS
    if next_attempt_at > now:
        return max(1, int(next_attempt_at - now))
    return None


def _phantom_recovery_model_for_attempt(
    account: AccountConfig,
    attempt_number: int,
) -> str | None:
    ladder = codex_phantom_recovery_model_ladder(account)
    if not ladder:
        return None
    if attempt_number <= 2:
        return ladder[0]
    if attempt_number <= 4:
        return ladder[min(1, len(ladder) - 1)]
    return ladder[min(2, len(ladder) - 1)]


def _update_phantom_recovery_state(
    account: AccountConfig,
    *,
    status: str,
    attempts: int,
    now: float,
    model: str | None = None,
    error: str | None = None,
    cooldown_until: float | None = None,
) -> dict:
    data = _load_phantom_recovery_state()
    key = _phantom_session_key(account)
    current = data.get(key) if isinstance(data.get(key), dict) else {}
    state = {
        "first_started_at": current.get("first_started_at", now),
        "last_seen_at": now,
        "last_attempt_at": now,
        "attempts": attempts,
        "status": status,
    }
    if model:
        state["last_model"] = model
    if error:
        state["last_error"] = error
    if cooldown_until is not None:
        state["cooldown_until"] = cooldown_until
    data[key] = state
    _save_phantom_recovery_state(data)
    return state


def _phantom_recovery_status_action(account: AccountConfig) -> str | None:
    state = _phantom_recovery_state_for(account)
    if not state:
        return None
    now = time.time()
    cooldown_until = _to_float(state.get("cooldown_until"))
    if cooldown_until is not None and cooldown_until > now:
        retry_at = datetime.fromtimestamp(cooldown_until, timezone.utc).astimezone()
        if state.get("status") == "provider_accepted":
            return f"Provider accepted; recheck after {retry_at.strftime('%H:%M %Z')}"
        return f"Recovery cooldown until {retry_at.strftime('%H:%M %Z')}"
    attempts = int(state.get("attempts", 0))
    if attempts > 0:
        return f"Phantom recovery {attempts}/{PHANTOM_RECOVERY_MAX_ATTEMPTS}"
    return "Phantom recovery"


def _was_kicked_in_current_window(
    account: AccountConfig,
    status: AccountStatus,
    history,
) -> bool:
    if status.resets_in_seconds is None or status.window_minutes is None:
        return False

    now = time.time()
    reset_at = now + status.resets_in_seconds
    window_started_at = reset_at - (status.window_minutes * 60)
    for event in reversed(history):
        if event.label != account.label or not event.success or not event.confirmed:
            continue
        if event.kind in {"probe", "session", "status_probe"}:
            continue
        if event.kick_type in {"probe", "session", "status_probe"}:
            continue
        if window_started_at <= event.timestamp <= reset_at:
            return True
        if event.timestamp < window_started_at:
            return False
    return False


def _was_kicked_in_current_session_window(
    account: AccountConfig,
    status: AccountStatus,
    history,
) -> bool:
    if status.session_resets_in_seconds is None:
        return False

    now = time.time()
    effective_session_window = _effective_session_window_minutes(status)
    if effective_session_window is None:
        return False
    window_seconds = effective_session_window * 60
    if _session_reset_is_near_full(status) and status.session_resets_at is None:
        window_started_at = now - window_seconds
        window_ends_at = now
    else:
        reset_at = _session_reset_at_for_history(status, now)
        window_started_at = reset_at - window_seconds
        window_ends_at = reset_at
    if _session_reset_is_near_full(status) and status.session_resets_at is not None:
        window_started_at -= SESSION_KICK_WINDOW_START_GRACE_SECONDS
    for event in reversed(history):
        if event.label != account.label or not event.success or not event.confirmed:
            continue
        if event.kind != "session" and event.kick_type != "session":
            continue
        if window_started_at <= event.timestamp <= window_ends_at:
            return True
        if event.timestamp < window_started_at:
            return False
    return False


def _recent_confirmed_session_kick(
    account: AccountConfig,
    history: list[KickEvent],
    *,
    now: float | None = None,
) -> bool:
    current = time.time() if now is None else now
    cutoff = current - RECENT_SESSION_KICK_DEDUP_SECONDS
    for event in reversed(history):
        if event.timestamp < cutoff:
            return False
        if event.timestamp > current + SESSION_KICK_WINDOW_START_GRACE_SECONDS:
            continue
        if event.label != account.label or not event.success or not event.confirmed:
            continue
        if event.kind == "session" or event.kick_type == "session":
            return True
    return False


def _was_pending_codex_fire_all_in_current_window(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
    *,
    kick_type: str,
) -> bool:
    if account.provider != "codex":
        return False
    if kick_type == "session":
        return _was_pending_codex_fire_all_in_current_session_window(account, status, history)
    return _was_pending_codex_fire_all_in_current_weekly_window(account, status, history)


def _was_pending_codex_fire_all_in_current_weekly_window(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
) -> bool:
    if status.resets_at is None or status.window_minutes is None:
        return False
    reset_at = status.resets_at
    window_started_at = reset_at - (status.window_minutes * 60)
    for event in reversed(history):
        if not _pending_codex_fire_all_event(account, event, kick_type="kick"):
            continue
        if window_started_at <= event.timestamp <= reset_at:
            return True
        if event.timestamp < window_started_at:
            return False
    return False


def _was_pending_codex_fire_all_in_current_session_window(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
) -> bool:
    if status.session_resets_in_seconds is None:
        return False
    now = time.time()
    effective_session_window = _effective_session_window_minutes(status)
    if effective_session_window is None:
        return False
    window_seconds = effective_session_window * 60
    if _session_reset_is_near_full(status) and status.session_resets_at is None:
        window_started_at = now - window_seconds
        window_ends_at = now + SESSION_KICK_WINDOW_START_GRACE_SECONDS
    else:
        reset_at = _session_reset_at_for_history(status, now)
        window_started_at = reset_at - window_seconds
        window_ends_at = reset_at
    if _session_reset_is_near_full(status) and status.session_resets_at is not None:
        window_started_at -= SESSION_KICK_WINDOW_START_GRACE_SECONDS
    for event in reversed(history):
        if not _pending_codex_fire_all_event(account, event, kick_type="session"):
            continue
        if window_started_at <= event.timestamp <= window_ends_at:
            return True
        if event.timestamp < window_started_at:
            return False
    return False


def _pending_codex_fire_all_event(
    account: AccountConfig,
    event: KickEvent,
    *,
    kick_type: str,
) -> bool:
    return (
        event.label == account.label
        and event.success
        and not event.confirmed
        and (event.kind == kick_type or event.kick_type == kick_type)
        and event.codex_surface in CODEX_FIRE_ALL_SURFACE_NAMES
        and event.codex_confirmation_method == "pending_reset_clock"
        and event.post_kick_status == "pending"
    )


def _session_reset_at_for_history(status: AccountStatus, now: float) -> float:
    computed_reset_at = now + (status.session_resets_in_seconds or 0)
    if status.session_resets_at is None:
        return computed_reset_at
    if abs(status.session_resets_at - computed_reset_at) > SESSION_KICK_WINDOW_START_GRACE_SECONDS:
        return computed_reset_at
    return status.session_resets_at


def _session_reset_is_near_full(status: AccountStatus) -> bool:
    session_window = _effective_session_window_minutes(status) or 0
    session_resets = status.session_resets_in_seconds or 0
    return (
        session_window > 0
        and session_resets >= int(session_window * 60 * _phantom_session_full_reset_ratio(status))
    )


def _effective_session_window_minutes(status: AccountStatus) -> int | None:
    if status.session_window_minutes is not None:
        return status.session_window_minutes
    session_resets = status.session_resets_in_seconds
    if session_resets is None:
        return None
    codex_session_seconds = 300 * 60
    if (
        session_resets >= int(codex_session_seconds * PHANTOM_SESSION_INFERRED_FULL_RESET_RATIO)
        and session_resets <= codex_session_seconds + SESSION_KICK_WINDOW_START_GRACE_SECONDS
    ):
        return 300
    return None


def _phantom_session_full_reset_ratio(status: AccountStatus) -> float:
    if status.session_window_minutes is None:
        return PHANTOM_SESSION_INFERRED_FULL_RESET_RATIO
    return PHANTOM_SESSION_FULL_RESET_RATIO


def _ambiguous_phantom_kick_backoff_until(
    account: AccountConfig,
    history: list[KickEvent],
    now: float | None = None,
) -> float | None:
    now = time.time() if now is None else now
    for event in reversed(history):
        if event.label != account.label:
            continue
        if (
            event.success
            and not event.confirmed
            and event.error == AMBIGUOUS_PHANTOM_KICK_ERROR
            and not _event_has_token_usage(event)
        ):
            until = event.timestamp + AMBIGUOUS_PHANTOM_KICK_BACKOFF_SECONDS
            return until if until > now else None
        return None
    return None


def _ambiguous_phantom_kick_attempt_count(
    account: AccountConfig,
    history: list[KickEvent],
) -> int:
    count = 0
    for event in reversed(history):
        if event.label != account.label:
            continue
        if (
            event.success
            and not event.confirmed
            and event.error == AMBIGUOUS_PHANTOM_KICK_ERROR
            and not _event_has_token_usage(event)
        ):
            count += 1
            continue
        break
    return count


def _provider_unchanged_phantom_kick_backoff_until(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
    now: float | None = None,
) -> float | None:
    now = time.time() if now is None else now
    event = _latest_provider_unchanged_phantom_kick(account, status, history, now=now)
    if event is None:
        return None
    until = event.timestamp + PROVIDER_UNCHANGED_PHANTOM_KICK_BACKOFF_SECONDS
    return until if until > now else None


def _provider_unchanged_phantom_kick_attempt_count(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
    now: float | None = None,
) -> int:
    now = time.time() if now is None else now
    window_started_at = _near_full_session_window_started_at(status, now)
    if window_started_at is None:
        return 0
    count = 0
    for event in reversed(history):
        if event.timestamp < window_started_at:
            break
        if _is_provider_unchanged_phantom_kick_event(account, event):
            count += 1
    return count


def _latest_provider_unchanged_phantom_kick(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
    now: float | None = None,
) -> KickEvent | None:
    now = time.time() if now is None else now
    window_started_at = _near_full_session_window_started_at(status, now)
    if window_started_at is None:
        return None
    for event in reversed(history):
        if event.timestamp < window_started_at:
            return None
        if _is_provider_unchanged_phantom_kick_event(account, event):
            return event
    return None


def _near_full_session_window_started_at(status: AccountStatus, now: float) -> float | None:
    session_window = _effective_session_window_minutes(status)
    if session_window is None or not _session_reset_is_near_full(status):
        return None
    return now - (session_window * 60)


def _is_provider_unchanged_phantom_kick_event(account: AccountConfig, event: KickEvent) -> bool:
    if event.label != account.label or not event.success or not event.confirmed:
        return False
    if event.kind != "session" and event.kick_type != "session":
        return False
    return _event_has_token_usage(event)


def _event_has_token_usage(event: KickEvent) -> bool:
    return any(
        value is not None and value > 0
        for value in (event.input_tokens, event.output_tokens, event.total_tokens)
    )


def _event_has_generation_evidence(event: KickEvent) -> bool:
    return bool(event.response_text) or _event_has_token_usage(event)




def _format_run_scheduled_reason(pending: PendingKick) -> str:
    orchestrated = pending.reason == ScheduleReason.ORCHESTRATED.value
    prefix = "orchestrated kick scheduled" if orchestrated else "scheduled"
    try:
        kick_at = (pending_kick_next_action_at(pending) or from_utc_iso(pending.kick_at)).astimezone()
    except ValueError:
        return prefix
    now = datetime.now(kick_at.tzinfo)
    delta_seconds = max(0, int((kick_at - now).total_seconds()))
    return f"{prefix} for {kick_at.strftime('%H:%M')} (in {_format_duration(delta_seconds)})"


def _run_due_pending_entry(item: dict) -> tuple[dict, bool]:
    kind = (
        "orchestrated"
        if item.get("pending_reason") == ScheduleReason.ORCHESTRATED.value
        else "scheduled"
    )
    entry = {
        "label": item.get("label"),
        "provider": item.get("provider"),
    }
    if item.get("success"):
        entry["reason"] = f"{kind} pending kick executed"
        return entry, False
    entry["reason"] = f"{kind} pending kick failed: {item.get('error') or 'unknown error'}"
    return entry, True


def _run_skip(
    account: AccountConfig,
    reason: str,
    reason_code: str | None = None,
) -> dict:
    data = {
        "label": account.label,
        "provider": account.provider,
        "reason": reason,
    }
    if reason_code:
        data["reason_code"] = reason_code
    return data


def _run_kicked(
    account: AccountConfig,
    reason: str,
    *,
    dry_run: bool,
) -> dict:
    data = {
        "label": account.label,
        "provider": account.provider,
        "reason": reason,
    }
    if dry_run:
        data["dry_run"] = True
    return data


def _run_schedule_decision(
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    *,
    dry_run: bool,
    now: datetime,
) -> PendingKick | None:
    schedule = schedule_for_account(config.schedule, account.label)
    if schedule is None:
        return None

    work_window = resolve_today_work_window(schedule, now, local_timezone(config.schedule))
    if work_window is None:
        return None

    selection = select_scheduling_window(status, config.schedule.scheduling_target)
    if selection is None:
        return None

    decision = recompute(account, status, config, now)
    if decision is None or decision.kick_at <= now:
        return None

    if dry_run:
        return PendingKick(
            account_key=account_key_string(account),
            account_label=account.label,
            provider=account.provider,
            kick_at=to_utc_iso(decision.kick_at),
            created_at=to_utc_iso(now),
            reason=decision.reason.value,
            windows_needed=decision.windows_needed,
            expected_waste_minutes=decision.expected_waste_minutes,
            waste_location=decision.waste_location.value,
            work_start=to_utc_iso(decision.work_start),
            work_end=to_utc_iso(decision.work_end),
            window_basis=selection.basis.value,
        )
    return upsert_pending_kick(account, decision, selection.basis.value, now)


def _run_evaluate_account(
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    *,
    dry_run: bool,
    history: list[KickEvent],
    pending: dict[str, PendingKick],
    now: datetime,
    stagger_state: KickStaggerState | None = None,
) -> tuple[str, dict, bool]:
    key = account_key_string(account)
    if account.provider == "gemini":
        return (
            "skipped",
            _run_skip(
                account,
                "monitor-only (daily RPD reset)",
                reason_code="monitor_only_daily_rpd",
            ),
            False,
        )
    if account.provider not in KICKABLE_PROVIDERS:
        return "skipped", _run_skip(account, "not kickable (monitor-only)"), False
    if status.stale:
        return "skipped", _run_skip(account, _stale_status_reason(status)), False
    if _auto_kick_blocked_by_codexbar_fallback(account, status):
        return (
            "skipped",
            _run_skip(
                account,
                "CodexBar fallback data is monitor-only for automatic kicks",
                reason_code="codexbar_fallback_auto_kick_blocked",
            ),
            False,
        )

    weekly_reset_ready = _weekly_reset_ready(status)
    long_ready = _long_kick_eligible(status)
    phantom_session = not weekly_reset_ready and _is_phantom_session_candidate(status)
    if phantom_session and _was_kicked_in_current_session_window(account, status, history):
        if _phantom_session_ready(account, status, record_observation=False):
            return "skipped", _run_skip(account, "provider status unchanged after session kick"), False
        return "skipped", _run_skip(account, "already kicked in this session window"), False

    phantom_attempts = _ambiguous_phantom_kick_attempt_count(account, history) if phantom_session else 0
    if phantom_attempts >= AMBIGUOUS_PHANTOM_KICK_MAX_ATTEMPTS:
        return (
            "skipped",
            _run_skip(
                account,
                f"phantom-session unresolved after {phantom_attempts} attempts",
                reason_code="phantom_session_unresolved",
            ),
            False,
        )

    backoff_until = _ambiguous_phantom_kick_backoff_until(account, history) if phantom_session else None
    if backoff_until is not None:
        retry_at = datetime.fromtimestamp(backoff_until, timezone.utc).astimezone()
        return (
            "skipped",
            _run_skip(account, f"phantom-session backoff until {retry_at.strftime('%H:%M %Z')}"),
            False,
        )

    pending_kick = pending.get(key)
    if pending_kick_blocks_auto_kick(pending_kick, now):
        return "skipped", _run_skip(account, _format_run_scheduled_reason(pending_kick)), False
    if (
        pending_kick is not None
        and pending_kick.reason == ScheduleReason.ORCHESTRATED.value
        and not pending_kick_gave_up(pending_kick)
    ):
        # A due orchestrated pending kick that the shared due-pending pass
        # deferred (stale status, boundary grace, cooldowns, ...) owns this
        # account until it executes, retries, gives up, or is cancelled.
        if dry_run:
            return (
                "kicked",
                _run_kicked(account, "orchestrated pending kick would be executed", dry_run=True),
                False,
            )
        return (
            "skipped",
            _run_skip(
                account,
                "orchestrated pending kick owns this account",
                reason_code="orchestrated_pending_owns_account",
            ),
            False,
        )

    session_ready = _session_kick_eligible(account, status) or phantom_session
    if long_ready:
        if not account.auto_kick:
            return "skipped", _run_skip(account, "auto-kick disabled"), False
        if not account.weekly_auto_kick:
            return "skipped", _run_skip(account, "weekly auto-kick disabled"), False
        if _was_kicked_in_current_window(account, status, history):
            return "skipped", _run_skip(account, "already kicked in this window"), False
        kick_type = "kick"
        success_reason = "weekly window anchored"
    elif session_ready:
        if not account.auto_kick:
            return "skipped", _run_skip(account, "auto-kick disabled"), False
        if not account.session_auto_kick:
            return "skipped", _run_skip(account, "session auto-kick disabled"), False
        if _was_kicked_in_current_session_window(account, status, history):
            return "skipped", _run_skip(account, "already kicked in this session window"), False
        kick_type = "session"
        success_reason = "session window anchored"
    else:
        if status.state == AccountState.ACTIVE:
            return "skipped", _run_skip(account, "already active"), False
        if status.state == AccountState.UNKNOWN:
            return "skipped", _run_skip(account, status.error or "status unknown"), False
        return "skipped", _run_skip(account, status.state.action.lower()), False

    schedule = schedule_for_account(config.schedule, account.label)
    if schedule is not None and kick_type == "session":
        work_window = resolve_today_work_window(
            schedule,
            now,
            local_timezone(config.schedule),
        )
        if work_window is None:
            return "skipped", _run_skip(account, "no scheduled session auto-kick window today"), False

    scheduled = _run_schedule_decision(account, status, config, dry_run=dry_run, now=now)
    if scheduled is not None:
        return "skipped", _run_skip(account, _format_run_scheduled_reason(scheduled)), False

    if dry_run:
        dry_reason = (
            "session window would be anchored"
            if kick_type == "session"
            else "weekly window would be anchored"
        )
        return "kicked", _run_kicked(account, dry_reason, dry_run=True), False

    event = _kick_and_notify(
        account,
        config,
        status,
        send_notification=False,
        kick_type=kick_type,
        stagger_state=stagger_state,
    )
    if pending_kick is not None:
        if event.success:
            remove_pending_kick(account)
        else:
            record_pending_kick_failure(account, event.error, now)
    if event.success:
        return "kicked", _run_kicked(account, success_reason, dry_run=False), False
    return "skipped", _run_skip(account, f"kick failed: {event.error or 'unknown error'}"), True


def _run_refresh(
    *,
    codex_only: bool,
) -> tuple[Config, list[AccountConfig], list[AccountStatus], int, str | None]:
    started = time.monotonic()
    config = Config.load()
    refresh_error: str | None = None
    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    try:
        with claude_cli_usage_refresh_allowed():
            config = _migrate_v04_direct_sources_if_needed(config, recheck_skipped=True)
            config = _migrate_provider_first_labels_if_needed(config)
            config = _migrate_codex_home_keys_if_needed(config)
            config = _repair_codex_home_identity_drift_if_needed(config)
            live_pairs = _refresh_status_cache_fast(config)
        accounts, statuses = live_pairs[0], live_pairs[1]
    except Exception as exc:
        refresh_error = str(exc)

    if accounts and statuses:
        _apply_codex_late_attribution(
            accounts,
            _cache_statuses_by_key_from_pairs(accounts, statuses),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    accounts, statuses = _filter_status_pairs_by_visibility(accounts, statuses, False)
    if codex_only:
        accounts, statuses = _filter_status_pairs_by_provider(accounts, statuses, "codex")
    return config, accounts, statuses, duration_ms, refresh_error


def _run_payload(
    *,
    refreshed_count: int,
    refresh_duration_ms: int,
    refresh_error: str | None,
    dry_run: bool,
    kicked: list[dict],
    skipped: list[dict],
    reservation_advisories: Sequence[ReservationAdvisory] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "refreshed_count": refreshed_count,
        "refresh_duration_ms": refresh_duration_ms,
        "refresh_error": refresh_error,
        "dry_run": dry_run,
        "kicked": kicked,
        "skipped": skipped,
        "reservation_advisories": [
            advisory.to_dict()
            for advisory in (reservation_advisories or [])
        ],
    }


def _render_run_summary(
    *,
    refreshed_count: int,
    refresh_duration_ms: int,
    refresh_error: str | None,
    dry_run: bool,
    kicked: list[dict],
    skipped: list[dict],
    reservation_advisories: Sequence[ReservationAdvisory] | None = None,
) -> None:
    console.print(f"Refreshed {refreshed_count} accounts in {refresh_duration_ms / 1000:.1f}s")
    if refresh_error:
        console.print(f"[red]Refresh failed:[/red] {refresh_error}")

    kicked_title = "Would kick" if dry_run else "Kicked"
    if kicked:
        console.print(f"{kicked_title} {len(kicked)} accounts:")
        for item in kicked:
            console.print(f"  [green]✓[/green] {item['label']} — {item['reason']}")
    else:
        console.print(f"{kicked_title} 0 accounts.")

    if skipped:
        console.print(f"Skipped {len(skipped)} accounts:")
        for item in skipped:
            console.print(f"  [dim]·[/dim] {item['label']} — {item['reason']}")
    _render_reservation_advisories(reservation_advisories or [])


def _report_timestamp_text(prefix: str, now: datetime | None = None) -> str:
    value = now or _status_cache_now()
    return f"{prefix} {_format_status_footer_timestamp(value, now=value)}."


def _print_report_timestamp(prefix: str) -> None:
    console.print(f"\n[dim]{_report_timestamp_text(prefix)}[/dim]")


def _confirm_prompt(prompt: str, *, default: bool = False) -> bool:
    """Confirm from the controlling TTY when available, falling back for tests/pipes."""
    if app_mode_enabled():
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    tty_fd: int | None = None
    restore_tty_pgrp: int | None = None
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        diagnostics = _prompt_process_group_diagnostics(tty_fd)
        click.echo(_format_prompt_process_group_diagnostics(diagnostics), err=True)
        restore_tty_pgrp = _ensure_prompt_foreground(tty_fd, diagnostics)
        click.echo("Using prompt method: dev-tty", err=True)
        while True:
            os.write(tty_fd, f"{prompt}{suffix}".encode())
            answer = _read_tty_line(tty_fd)
            normalized = answer.strip().lower()
            if not normalized:
                return default
            if normalized in {"y", "yes"}:
                return True
            if normalized in {"n", "no"}:
                return False
            os.write(tty_fd, b"Please enter y or n.\n")
    except OSError as exc:
        click.echo(f"Using prompt method: click-stdin (/dev/tty failed: {exc!r})", err=True)
        return click.confirm(prompt, default=default)
    finally:
        if tty_fd is not None:
            if restore_tty_pgrp is not None:
                _restore_prompt_foreground(tty_fd, restore_tty_pgrp)
            try:
                os.close(tty_fd)
            except OSError:
                pass


def _prompt_process_group_diagnostics(tty_fd: int) -> dict[str, int | None]:
    stdin_tty_pgrp: int | None
    try:
        stdin_tty_pgrp = os.tcgetpgrp(sys.stdin.fileno())
    except (OSError, ValueError):
        stdin_tty_pgrp = None
    try:
        dev_tty_pgrp = os.tcgetpgrp(tty_fd)
    except OSError:
        dev_tty_pgrp = None
    return {
        "pid": os.getpid(),
        "pgrp": os.getpgrp(),
        "stdin_tty_pgrp": stdin_tty_pgrp,
        "dev_tty_pgrp": dev_tty_pgrp,
    }


def _format_prompt_process_group_diagnostics(diagnostics: dict[str, int | None]) -> str:
    return (
        "Prompt process groups: "
        f"pid={diagnostics['pid']} "
        f"pgrp={diagnostics['pgrp']} "
        f"stdin_tty_pgrp={diagnostics['stdin_tty_pgrp']} "
        f"dev_tty_pgrp={diagnostics['dev_tty_pgrp']}"
    )


def _ensure_prompt_foreground(
    tty_fd: int,
    diagnostics: dict[str, int | None],
) -> int | None:
    process_group = diagnostics["pgrp"]
    tty_process_group = diagnostics["dev_tty_pgrp"]
    if (
        process_group is None
        or tty_process_group is None
        or process_group == tty_process_group
    ):
        return None

    old_handler = signal.getsignal(signal.SIGTTOU)
    try:
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(tty_fd, process_group)
    except OSError as exc:
        raise click.ClickException(
            "Cannot move TokenKick into the foreground terminal process group "
            f"for confirmation input: {exc}. Use `tk wake <label>` for dormant "
            "accounts or `tk kick --force <label>` if you intend to bypass confirmation."
        ) from exc
    finally:
        signal.signal(signal.SIGTTOU, old_handler)
    return tty_process_group


def _restore_prompt_foreground(tty_fd: int, process_group: int) -> None:
    old_handler = signal.getsignal(signal.SIGTTOU)
    try:
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(tty_fd, process_group)
    except OSError:
        return
    finally:
        signal.signal(signal.SIGTTOU, old_handler)


def _read_tty_line(fd: int) -> str:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1)
        if not chunk or chunk == b"\n":
            break
        if chunk == b"\r":
            continue
        chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace")


def _status_actionable_now(
    account: AccountConfig | None,
    status: AccountStatus,
    provider: str,
    *,
    history: list[KickEvent],
    pending_kick: PendingKick | None,
) -> bool:
    return _kick_eligibility(
        account,
        status,
        provider,
        history=history,
        pending_kick=pending_kick,
    ).kickable


def _status_auto_enabled_for_action(
    account: AccountConfig,
    status: AccountStatus,
    provider: str,
) -> bool:
    if _auto_kick_blocked_by_codexbar_fallback(account, status):
        return False
    if not account.auto_kick:
        return False
    if _session_kick_eligible(account, status, provider):
        return account.session_auto_kick
    return account.weekly_auto_kick


def _status_provider(
    status: AccountStatus,
    providers_by_label: dict[str, str] | None = None,
) -> str:
    if providers_by_label and status.label in providers_by_label:
        return providers_by_label[status.label]
    if status.label.endswith(")") and "(" in status.label:
        return status.label.rsplit("(", 1)[1].rstrip(")")
    return "codex"


def _schedule_log_fields(decision: ScheduleDecision) -> dict:
    return {
        "kick_at": to_utc_iso(decision.kick_at),
        "reason": decision.reason.value,
        "windows_needed": decision.windows_needed,
        "expected_waste_minutes": decision.expected_waste_minutes,
        "waste_location": decision.waste_location.value,
    }


def _execute_due_pending_kicks(
    accounts: list[AccountConfig],
    config: Config,
    *,
    daemon_log: bool = False,
    statuses_by_key: dict[str, AccountStatus] | None = None,
    stagger_state: KickStaggerState | None = None,
    results_sink: list[dict] | None = None,
) -> int:
    now = datetime.now(timezone.utc)
    accounts_by_key = {account_key_string(account): account for account in accounts}
    executed = 0
    for key, pending in list(load_pending_kicks(now).items()):
        try:
            scheduled_for = from_utc_iso(pending.kick_at)
        except ValueError:
            continue
        action_at = pending_kick_next_action_at(pending) or scheduled_for
        if action_at > now:
            continue
        if pending_kick_gave_up(pending):
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_gave_up",
                    account=pending.account_label,
                    attempts=pending.attempt_count,
                    error=pending.last_error,
                )
            continue
        if not pending_kick_retry_ready(pending, now):
            if daemon_log:
                retry_at = pending_kick_next_action_at(pending)
                _daemon_log(
                    "scheduled_kick_retry_waiting",
                    account=pending.account_label,
                    attempts=pending.attempt_count,
                    retry_at=to_utc_iso(retry_at) if retry_at is not None else None,
                    error=pending.last_error,
                )
            continue

        account = accounts_by_key.get(key)
        if account is None:
            invalidate_pending_kicks(account_label=pending.account_label, provider=pending.provider)
            continue

        status = statuses_by_key.get(key) if statuses_by_key is not None else None
        if status is None:
            status = _fetch_status(account, config)
        if status.stale:
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_waiting",
                    account=account.label,
                    reason="stale_status",
                    error=_stale_status_reason(status),
                )
            continue
        session_pending = pending.window_basis == SchedulingWindowBasis.SESSION.value
        pending_allowed = account.auto_kick and (
            account.session_auto_kick if session_pending else account.weekly_auto_kick
        )
        if not pending_allowed:
            if daemon_log:
                reason = (
                    "session_auto_kick_disabled"
                    if session_pending and account.auto_kick
                    else "weekly_auto_kick_disabled"
                    if account.auto_kick
                    else "auto_kick_disabled"
                )
                _daemon_log("scheduled_kick_cleared", account=account.label, reason=reason)
            remove_pending_kick(account)
            continue
        long_ready = not session_pending and _long_kick_eligible(status)
        session_ready = session_pending and _session_kick_eligible(account, status)
        cooldown_remaining = _session_cooldown_remaining(account, status) if session_pending else None
        if status.state == AccountState.UNKNOWN or (
            status.state == AccountState.WAITING and not session_ready
        ):
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_waiting",
                    account=account.label,
                    reason=status.state.value,
                    error=status.error,
                )
            continue
        if session_pending and not session_ready and _session_boundary_grace_active(status):
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_waiting",
                    account=account.label,
                    reason="session_boundary_grace",
                    session_resets_in=status.session_resets_in_seconds,
                )
            continue
        if not long_ready and status.state != AccountState.FRESH and not session_ready:
            if daemon_log:
                _daemon_log("scheduled_kick_cleared", account=account.label, reason=status.state.value)
            remove_pending_kick(account)
            continue
        history = load_kick_history(limit=200)
        if not session_ready and _was_kicked_in_current_window(account, status, history):
            if daemon_log:
                _daemon_log("scheduled_kick_cleared", account=account.label, reason="already_kicked")
            remove_pending_kick(account)
            continue
        if (
            not session_ready
            and _was_pending_codex_fire_all_in_current_window(
                account,
                status,
                history,
                kick_type="kick",
            )
        ):
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_cleared",
                    account=account.label,
                    reason="codex_awaiting_confirmation",
                )
            remove_pending_kick(account)
            continue
        if session_ready and _was_kicked_in_current_session_window(account, status, history):
            if daemon_log:
                _daemon_log("scheduled_kick_cleared", account=account.label, reason="already_session_kicked")
            remove_pending_kick(account)
            continue
        if (
            session_ready
            and _was_pending_codex_fire_all_in_current_window(
                account,
                status,
                history,
                kick_type="session",
            )
        ):
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_cleared",
                    account=account.label,
                    reason="codex_awaiting_confirmation",
                )
            remove_pending_kick(account)
            continue
        if cooldown_remaining is not None:
            if daemon_log:
                _daemon_log("scheduled_kick_deferred", account=account.label, reason="phantom_session")
            continue

        _stagger_codex_kick_if_needed(account, stagger_state, daemon_log=daemon_log)
        actual = datetime.now(timezone.utc)
        if daemon_log:
            _daemon_log(
                "scheduled_kick_executed",
                account=account.label,
                scheduled_for=to_utc_iso(scheduled_for),
                retry_for=to_utc_iso(action_at) if action_at != scheduled_for else None,
                actual_kick_at=to_utc_iso(actual),
                slop_seconds=max(0, int((actual - action_at).total_seconds())),
            )
        event = _kick_and_notify(
            account,
            config,
            status,
            daemon_log=False,
            send_notification=False,
            kick_type="session" if session_ready else "kick",
            allow_codex_fire_all=True,
        )
        if event.success:
            remove_pending_kick(account)
            if daemon_log:
                event_name = "scheduled_kick_confirmed" if event.confirmed else "scheduled_kick_attempted"
                _daemon_log(event_name, account=account.label, reason=event.error)
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_scheduled_kick(event, pending, notifications),
                daemon_log=daemon_log,
                context="scheduled_kick",
            )
        else:
            updated_pending = record_pending_kick_failure(account, event.error, actual)
            if daemon_log:
                _daemon_log(
                    "scheduled_kick_failed",
                    account=account.label,
                    attempts=updated_pending.attempt_count if updated_pending else None,
                    next_retry_at=updated_pending.next_retry_at if updated_pending else None,
                    gave_up_at=updated_pending.gave_up_at if updated_pending else None,
                    error=event.error,
                )
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(event, notifications),
                daemon_log=daemon_log,
                context="scheduled_kick_failure",
            )
        if results_sink is not None:
            results_sink.append(
                {
                    "label": account.label,
                    "provider": account.provider,
                    "pending_reason": pending.reason,
                    "scheduled_for": pending.kick_at,
                    "success": event.success,
                    "error": event.error,
                }
            )
        executed += 1
    return executed


def _execute_codex_surface_reintroductions(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    config: Config,
    *,
    daemon_log: bool = False,
    stagger_state: KickStaggerState | None = None,
) -> CodexSurfaceReintroductionResult:
    history = load_kick_history(limit=300)
    result = CodexSurfaceReintroductionResult()
    for account in accounts:
        if account.provider != "codex" or not account.codex_surface_auto_demote:
            continue
        status = statuses_by_key.get(account_key_string(account))
        if status is None or status.stale or status.state == AccountState.UNKNOWN:
            continue
        report = codex_surface_stats_for_account(account, _codex_surface_stats_file())
        demoted = report.get("demotion", {}).get("demoted", {})
        if not isinstance(demoted, dict) or not demoted:
            continue
        cluster = _codex_surface_missed_kept_cluster(account, status, history, config)
        if cluster is None:
            continue
        missed_cluster_id = cluster[0].codex_cluster_id if cluster else None
        reintroduced = reintroduce_codex_surfaces_after_miss(
            _codex_surface_stats_file(),
            account,
            reason="kept surfaces missed on fresh provider read",
        )
        for item in reintroduced:
            surfaces = [str(surface) for surface in item.get("surfaces", [])]
            _record_codex_surface_management_event(
                account,
                kind="codex_surface_reintroduce",
                surfaces=surfaces,
                reason=str(item.get("reason") or "kept surfaces missed"),
                daemon_log=daemon_log,
            )
        if reintroduced:
            recovery_event = _kick_and_notify(
                account,
                config,
                status,
                daemon_log=daemon_log,
                send_notification=True,
                kick_type="session",
                stagger_state=stagger_state,
                allow_codex_fire_all=False,
            )
            result.count += 1
            result.records.append(
                CodexSurfaceReintroductionRecord(
                    account_key=account_key_string(account),
                    account_label=account.label,
                    missed_cluster_id=missed_cluster_id,
                    recovery_cluster_id=recovery_event.codex_cluster_id,
                    recovery_attempt_finished_at=(
                        recovery_event.codex_attempt_finished_at or recovery_event.timestamp
                    ),
                )
            )
    return result


def _codex_surface_missed_kept_cluster(
    account: AccountConfig,
    status: AccountStatus,
    history: list[KickEvent],
    config: Config,
) -> list[KickEvent] | None:
    kept_surfaces = set(_codex_retry_surfaces_for_account(account))
    if not kept_surfaces:
        return None
    clusters: dict[str, list[KickEvent]] = {}
    for event in history:
        if event.label != account.label or not event.codex_cluster_id or not event.codex_surface:
            continue
        clusters.setdefault(event.codex_cluster_id, []).append(event)
    for cluster in sorted(
        clusters.values(),
        key=lambda events: max(event.codex_attempt_finished_at or event.timestamp for event in events),
        reverse=True,
    ):
        if any(event.confirmed for event in cluster):
            continue
        surfaces = {event.codex_surface for event in cluster}
        if not kept_surfaces.issubset(surfaces) or not surfaces.issubset(kept_surfaces):
            continue
        if not any(
            event.post_kick_status in {"unchanged", "phantom", "misaligned"}
            or event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR
            for event in cluster
        ):
            continue
        last_attempt_at = max(
            event.codex_attempt_finished_at or event.timestamp
            for event in cluster
        )
        if time.time() - last_attempt_at < _codex_surface_retry_backoff_seconds(config):
            continue
        observed = _parse_status_cache_observed_at(status.observed_at) if status.observed_at else None
        if observed is None or observed.timestamp() <= last_attempt_at:
            continue
        return cluster
    return None


def _schedule_or_kick_target(
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    *,
    dry_run: bool,
    daemon_log: bool,
    force: bool,
    stagger_state: KickStaggerState | None = None,
) -> bool:
    kick_type = _kick_type_for_status(status)
    if status.stale and not force:
        reason = _stale_status_reason(status)
        if daemon_log:
            _daemon_log("kick_skipped", account=account.label, reason="stale_status", error=reason)
        else:
            console.print(f'[yellow]Skipping "{account.label}": {reason}[/yellow]')
        return False

    if not force and not dry_run and kick_type == "kick":
        live_status = _codex_daemon_live_status_before_weekly_kick(
            account,
            status,
            config,
            daemon_log=daemon_log,
        )
        if live_status is None:
            return False
        status = live_status
        kick_type = _kick_type_for_status(status)

    if force:
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            stagger_state=stagger_state,
        )
        remove_pending_kick(account)
        return True

    history = load_kick_history(limit=200)
    weekly_reset_ready = _weekly_reset_ready(status)
    phantom_session = not weekly_reset_ready and _is_phantom_session_candidate(status)
    phantom_attempts = _ambiguous_phantom_kick_attempt_count(account, history) if phantom_session else 0
    if phantom_attempts >= AMBIGUOUS_PHANTOM_KICK_MAX_ATTEMPTS:
        if daemon_log:
            _daemon_log(
                "kick_skipped",
                account=account.label,
                reason="phantom_session_unresolved",
                attempts=phantom_attempts,
            )
        else:
            console.print(
                f'[dim]Skipping "{account.label}": phantom session unresolved after '
                f"{phantom_attempts} attempts.[/dim]"
            )
        return False

    backoff_until = _ambiguous_phantom_kick_backoff_until(account, history) if phantom_session else None
    if backoff_until is not None:
        until = datetime.fromtimestamp(backoff_until, timezone.utc)
        if daemon_log:
            _daemon_log(
                "kick_backoff",
                account=account.label,
                until=to_utc_iso(until),
                reason="ambiguous_phantom_after_kick",
            )
        else:
            console.print(
                f'[dim]Skipping "{account.label}": recent ambiguous phantom kick attempt; '
                f"retry after {until.astimezone().strftime('%H:%M %Z')}.[/dim]"
            )
        return False

    schedule = schedule_for_account(config.schedule, account.label)
    if schedule is None:
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            stagger_state=stagger_state,
            allow_codex_fire_all=True,
        )
        remove_pending_kick(account)
        return True

    now = datetime.now(timezone.utc)
    tz = local_timezone(config.schedule)
    work_window = resolve_today_work_window(schedule, now, tz)
    if work_window is None:
        if kick_type == "session":
            if daemon_log:
                _daemon_log("schedule_skipped", account=account.label, reason="no_work_window_today")
            else:
                console.print(
                    f'[dim]Skipping "{account.label}": no scheduled session auto-kick window today.[/dim]'
                )
            return False
        if daemon_log:
            _daemon_log("schedule_fallback", account=account.label, reason="no_work_window_today")
        else:
            console.print(
                f'[dim]Scheduling fallback for "{account.label}": no work window today.[/dim]'
            )
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            stagger_state=stagger_state,
            allow_codex_fire_all=True,
        )
        remove_pending_kick(account)
        return True

    selection = select_scheduling_window(status, config.schedule.scheduling_target)
    if selection is None:
        if daemon_log:
            _daemon_log(
                "schedule_skipped",
                account=account.label,
                reason="no_suitable_window",
                primary_window_minutes=status.window_minutes,
                session_window_minutes=status.session_window_minutes,
            )
        else:
            console.print(
                f'[dim]Scheduling skipped for "{account.label}": '
                "no suitable short rolling window was found.[/dim]"
            )
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            stagger_state=stagger_state,
            allow_codex_fire_all=True,
        )
        remove_pending_kick(account)
        return True

    decision = recompute(account, status, config, now)
    if decision is None:
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            stagger_state=stagger_state,
            allow_codex_fire_all=True,
        )
        remove_pending_kick(account)
        return True

    if daemon_log:
        _daemon_log("schedule_decision", account=account.label, **_schedule_log_fields(decision))

    if decision.kick_at <= now:
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            return False
        event = _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            send_notification=False,
            kick_type=kick_type,
            stagger_state=stagger_state,
            allow_codex_fire_all=True,
        )
        remove_pending_kick(account)
        if decision.reason == ScheduleReason.QUOTA_CONSTRAINED:
            def send(notifications: NotifyConfig) -> bool:
                return notify_quota_constrained_kick(event, decision, notifications)

            context = "quota_constrained_kick"
        else:
            def send(notifications: NotifyConfig) -> bool:
                return notify_scheduled_kick(event, decision, notifications)

            context = "scheduled_kick"
        _send_account_notifications(
            account,
            config.notifications,
            send,
            daemon_log=daemon_log,
            context=context,
        )
        return True

    if dry_run:
        console.print(
            f'[dim]Would schedule:[/dim] {account.label} at '
            f'{decision.kick_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")}'
        )
        return False

    current_pending = load_pending_kicks(now).get(account_key_string(account))
    pending = upsert_pending_kick(account, decision, selection.basis.value, now)
    if pending.reason == ScheduleReason.ORCHESTRATED.value:
        if daemon_log:
            _daemon_log(
                "schedule_skipped",
                account=account.label,
                reason="orchestrated_pending_owns_account",
                kick_at=pending.kick_at,
            )
        else:
            console.print(
                f'[dim]Skipping smart scheduling for "{account.label}": '
                f"{_format_run_scheduled_reason(pending)}.[/dim]"
            )
        return False
    session_pending_created_or_changed = (
        selection.basis == SchedulingWindowBasis.SESSION
        and (current_pending is None or current_pending.kick_at != pending.kick_at)
    )
    if daemon_log:
        if session_pending_created_or_changed:
            _daemon_log(
                "schedule_session_window",
                account=account.label,
                primary_window_minutes=status.window_minutes,
                session_window_minutes=status.session_window_minutes,
            )
        _daemon_log("schedule_deferred", account=account.label, kick_at=pending.kick_at)
    if not pending.notified:
        _delivered, acknowledged = _send_account_notifications(
            account,
            config.notifications,
            lambda notifications: notify_schedule_decision(account.label, pending, notifications),
            daemon_log=daemon_log,
            context="schedule_decision",
        )
        if acknowledged:
            mark_pending_notified(account, now)
    return False


def _codex_daemon_live_status_before_weekly_kick(
    account: AccountConfig,
    status: AccountStatus,
    config: Config,
    *,
    daemon_log: bool,
) -> AccountStatus | None:
    if (
        not daemon_log
        or account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or not _weekly_reset_ready(status)
    ):
        return status
    try:
        refreshed = _fetch_status(account, config)
    except Exception as exc:
        _daemon_log(
            "kick_skipped",
            account=account.label,
            reason="live_status_refresh_failed",
            error=f"{exc.__class__.__name__}: {exc}",
        )
        return None
    _save_status_cache(
        [account],
        {account_key_string(account): refreshed},
        _failures_by_key_from_status_pairs([account], [refreshed]),
        daemon_log=daemon_log,
    )
    history = load_kick_history(limit=200)
    eligibility = _kick_eligibility(account, refreshed, history=history)
    if eligibility.kickable and eligibility.kick_type == "kick":
        _daemon_log(
            "kick_live_recheck",
            account=account.label,
            state=refreshed.state.value,
            used_percent=refreshed.used_percent,
            resets_in=refreshed.resets_in_seconds,
            source_detail=refreshed.source_detail,
        )
        return refreshed
    _daemon_log(
        "kick_skipped",
        account=account.label,
        reason="live_status_changed",
        state=refreshed.state.value,
        eligibility=eligibility.reason,
        used_percent=refreshed.used_percent,
        resets_in=refreshed.resets_in_seconds,
        source_detail=refreshed.source_detail,
    )
    return None


def _kick_all_enabled_accounts(
    accounts: list[AccountConfig],
    config: Config,
    *,
    dry_run: bool = False,
    daemon_log: bool = False,
    force: bool = False,
    targets: list[tuple[AccountConfig, AccountStatus]] | None = None,
    deferred: list[tuple[AccountConfig, AccountStatus, int]] | None = None,
    stagger_state: KickStaggerState | None = None,
    suppress_pending: bool = True,
) -> tuple[int, int]:
    if targets is None or deferred is None:
        targets, deferred = _kickable_window_targets(
            accounts,
            config=config,
            record_observations=not dry_run,
            suppress_pending=suppress_pending,
        )
    for account, _status, cooldown_remaining in deferred:
        if daemon_log:
            _daemon_log(
                "deferred",
                account=account.label,
                session_resets_in=_format_duration(cooldown_remaining),
            )
        else:
            message = (
                f"Waiting to kick {account.label} — session resets in "
                f"{_format_duration(cooldown_remaining)}, will kick after."
            )
            console.print(f"[dim]{message}[/dim]")

    if not targets:
        if daemon_log:
            _daemon_log("no_targets")
        else:
            console.print("[dim]No fresh kickable windows found.[/dim]")
        return 0, len(deferred)

    for account, status in targets:
        try:
            _schedule_or_kick_target(
                account,
                status,
                config,
                dry_run=dry_run,
                daemon_log=daemon_log,
                force=force,
                stagger_state=stagger_state,
            )
        except Exception as exc:
            if daemon_log:
                _daemon_log(
                    "kick_failed",
                    account=account.label,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            else:
                console.print(f'[red]Skipping "{account.label}": {exc}[/red]')
            continue

    return len(targets), len(deferred)


def _execute_verified_phantom_recoveries(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    config: Config,
    *,
    daemon_log: bool,
    stagger_state: KickStaggerState | None,
) -> tuple[int, list[tuple[AccountConfig, int]]]:
    executed = 0
    deferred: list[tuple[AccountConfig, int]] = []
    now = time.time()
    for account in _auto_kick_kickable_accounts(accounts):
        status = statuses_by_key.get(account_key_string(account))
        if status is None or status.stale:
            continue
        if _weekly_quota_exhausted(status):
            continue
        if account.provider != "codex" or not account.session_auto_kick:
            continue
        if _auto_kick_blocked_by_codexbar_fallback(account, status):
            continue
        if not _is_phantom_session_candidate(status):
            _clear_phantom_recovery_state(account)
            continue
        if not _phantom_session_ready(account, status, record_observation=False):
            continue
        cooldown_remaining = _phantom_recovery_defer_seconds(account, now=now)
        if cooldown_remaining is not None:
            deferred.append((account, cooldown_remaining))
            if daemon_log:
                _daemon_log(
                    "phantom_recovery_deferred",
                    account=account.label,
                    retry_in=_format_duration(cooldown_remaining),
                )
            continue

        state = _phantom_recovery_state_for(account) or {}
        attempts = int(state.get("attempts", 0))
        cooldown_until = _to_float(state.get("cooldown_until"))
        if (
            attempts >= PHANTOM_RECOVERY_MAX_ATTEMPTS
            and cooldown_until is not None
            and cooldown_until <= now
        ):
            attempts = 0
            state = {}
        if attempts >= PHANTOM_RECOVERY_MAX_ATTEMPTS:
            cooldown_until = now + PHANTOM_RECOVERY_COOLDOWN_SECONDS
            failure_event = KickEvent(
                label=account.label,
                success=False,
                kind="phantom_recovery",
                kick_type="session",
                error=f"Phantom recovery failed after {attempts} attempts; Codex did not expose a session anchor.",
            )
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(failure_event, notifications),
                daemon_log=daemon_log,
                context="phantom_recovery",
            )
            _update_phantom_recovery_state(
                account,
                status="cooldown",
                attempts=attempts,
                now=now,
                error="max attempts reached",
                cooldown_until=cooldown_until,
            )
            deferred.append((account, PHANTOM_RECOVERY_COOLDOWN_SECONDS))
            if daemon_log:
                _daemon_log(
                    "phantom_recovery_failed",
                    account=account.label,
                    attempts=attempts,
                    cooldown_seconds=PHANTOM_RECOVERY_COOLDOWN_SECONDS,
                )
            continue

        attempt_number = attempts + 1
        model_override = _phantom_recovery_model_for_attempt(account, attempt_number)
        if daemon_log:
            _daemon_log(
                "phantom_recovery_attempt",
                account=account.label,
                attempt=attempt_number,
                max_attempts=PHANTOM_RECOVERY_MAX_ATTEMPTS,
                model=model_override or "default",
            )
        event = _kick_and_notify(
            account,
            config,
            status,
            daemon_log=daemon_log,
            send_notification=attempt_number == 1,
            kick_type="session",
            stagger_state=stagger_state,
            model_override=model_override,
        )
        executed += 1
        _mark_status_cache_entry_stale(account)
        post_status = _fetch_status(account, config)
        statuses_by_key[account_key_string(account)] = post_status
        _save_status_cache(
            [account],
            {account_key_string(account): post_status},
            _failures_by_key_from_status_pairs([account], [post_status]),
            daemon_log=daemon_log,
        )
        provider_accepted_but_ambiguous = (
            event.success
            and event.error == PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR
        )
        if event.success and event.confirmed and not provider_accepted_but_ambiguous:
            _clear_phantom_recovery_state(account)
            _clear_phantom_session_observation(account)
            if attempt_number > 1:
                _send_account_notifications(
                    account,
                    config.notifications,
                    lambda notifications: notify_kick(event, notifications),
                    daemon_log=daemon_log,
                    context="phantom_recovery",
                )
            if daemon_log:
                _daemon_log(
                    "phantom_recovery_confirmed",
                    account=account.label,
                    attempts=attempt_number,
                )
            continue

        if not _is_phantom_session_candidate(post_status):
            _clear_phantom_recovery_state(account)
            _clear_phantom_session_observation(account)
            if daemon_log:
                _daemon_log(
                    "phantom_recovery_resolved",
                    account=account.label,
                    attempts=attempt_number,
                )
            continue

        cooldown_until = None
        recovery_status = "recovering"
        if provider_accepted_but_ambiguous:
            recovery_status = "provider_accepted"
            cooldown_until = now + PROVIDER_ACCEPTED_PHANTOM_BACKOFF_SECONDS
        if attempt_number >= PHANTOM_RECOVERY_MAX_ATTEMPTS:
            recovery_status = "cooldown"
            cooldown_until = now + PHANTOM_RECOVERY_COOLDOWN_SECONDS
            failure_event = KickEvent(
                label=account.label,
                success=False,
                kind="phantom_recovery",
                kick_type="session",
                error=f"Phantom recovery failed after {attempt_number} attempts; Codex did not expose a session anchor.",
            )
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(failure_event, notifications),
                daemon_log=daemon_log,
                context="phantom_recovery",
            )
            if daemon_log:
                _daemon_log(
                    "phantom_recovery_failed",
                    account=account.label,
                    attempts=attempt_number,
                    cooldown_seconds=PHANTOM_RECOVERY_COOLDOWN_SECONDS,
                )
        _update_phantom_recovery_state(
            account,
            status=recovery_status,
            attempts=attempt_number,
            now=now,
            model=model_override,
            error=event.error,
            cooldown_until=cooldown_until,
        )
    return executed, deferred


def _kick_and_notify(
    account: AccountConfig,
    config: Config,
    pre_status: AccountStatus | None = None,
    *,
    daemon_log: bool = False,
    send_notification: bool = True,
    kick_type: str = "kick",
    stagger_state: KickStaggerState | None = None,
    model_override: str | None = None,
    allow_codex_fire_all: bool = False,
) -> KickEvent:
    _stagger_codex_kick_if_needed(account, stagger_state, daemon_log=daemon_log)
    if not daemon_log:
        console.print(f'[bold]Kicking "{account.label}"...[/bold]', end=" ")
    phantom_recovery = pre_status is not None and _is_phantom_session_candidate(pre_status)

    if (
        account.provider == "codex"
        and allow_codex_fire_all
        and not phantom_recovery
        and _codex_fire_all_surfaces_enabled(config)
        and kick_type in {"kick", "session"}
    ):
        event = _kick_codex_fire_all_surfaces(
            account,
            config,
            pre_status=pre_status,
            daemon_log=daemon_log,
            kick_type=kick_type,
            model_override=model_override,
        )
    elif account.provider == "codex":
        event = _kick_codex_with_surface_retries(
            account,
            config,
            pre_status=pre_status,
            phantom_recovery=phantom_recovery,
            daemon_log=daemon_log,
            kick_type=kick_type,
            model_override=model_override,
        )
    else:
        event = _run_kick_attempt(
            account,
            config,
            phantom_recovery=phantom_recovery,
            daemon_log=daemon_log,
            kick_type=kick_type,
            model_override=model_override,
            pre_status=pre_status,
        )

    if event.success:
        if kick_type == "kick":
            _mark_status_cache_entry_stale(account)
            if event.confirmed and account.provider == "codex":
                _clear_phantom_session_observation(account)
                _clear_phantom_recovery_state(account)
        if not daemon_log and event.confirmed:
            console.print("[green]✓ Done[/green]")
        elif not daemon_log:
            console.print("[yellow]~ Attempted[/yellow]")
            if _codex_session_confirmation_pending_for_user(event):
                console.print(
                    "[yellow]Codex accepted the session kick; provider confirmation "
                    "will be checked on the next status refresh or daemon poll.[/yellow]"
                )
        if send_notification:
            delivered, _acknowledged = _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(event, notifications),
                daemon_log=daemon_log,
                context=event.kind,
            )
            _record_codex_pending_confirmation_notification(
                account,
                event,
                delivered=delivered,
                daemon_log=daemon_log,
            )
    else:
        if not daemon_log:
            console.print(f"[red]✗ {event.error}[/red]")
        if send_notification:
            _send_account_notifications(
                account,
                config.notifications,
                lambda notifications: notify_kick(event, notifications),
                daemon_log=daemon_log,
                context=event.kind,
            )
    return event


def _kick_codex_fire_all_surfaces(
    account: AccountConfig,
    config: Config,
    *,
    pre_status: AccountStatus | None,
    daemon_log: bool,
    kick_type: str,
    model_override: str | None,
) -> KickEvent:
    cluster_id = uuid.uuid4().hex
    events: list[KickEvent] = []
    final_event: KickEvent | None = None
    surfaces = _effective_codex_burst_ladder_surfaces(account, config)
    gap_seconds = _codex_burst_ladder_gap_seconds(config)
    for index, surface in enumerate(surfaces, start=1):
        event = _run_kick_attempt(
            account,
            config,
            phantom_recovery=False,
            daemon_log=daemon_log,
            kick_type=kick_type,
            model_override=model_override,
            codex_surface=surface,
            attempt=index,
            max_attempts=len(surfaces),
            pre_status=pre_status,
            record_event=False,
            log_result=False,
            verify_codex=False,
        )
        event.codex_cluster_id = cluster_id
        event.codex_cluster_origin = CODEX_CLUSTER_ORIGIN_BURST
        _mark_codex_fire_all_pending_attempt(event)
        events.append(event)
        final_event = event
        if index < len(surfaces) and gap_seconds > 0:
            time.sleep(gap_seconds)
    if final_event is None:
        raise RuntimeError("Codex burst ladder surfaces were empty")
    _record_codex_fire_all_cluster(events, daemon_log=daemon_log)
    return final_event


def _mark_codex_fire_all_pending_attempt(event: KickEvent) -> None:
    if not event.success:
        if event.codex_confirmation_method is None:
            event.codex_confirmation_method = "none"
        return
    event.confirmed = False
    event.evidence_provider_moved = False
    event.post_kick_status = "pending"
    event.codex_confirmation_method = "pending_reset_clock"
    if event.error is None or event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR:
        event.error = CODEX_SESSION_ANCHOR_PENDING_ERROR


def _record_codex_fire_all_cluster(
    events: list[KickEvent],
    *,
    daemon_log: bool,
) -> None:
    for event in events:
        if event.codex_confirmation_method is None:
            event.codex_confirmation_method = "none"
        append_kick_event(event)
        _log_kick_result(
            event,
            daemon_log=daemon_log,
            surface=event.codex_surface,
            attempt=event.codex_attempt,
            max_attempts=event.codex_max_attempts,
        )


def _kick_codex_with_surface_retries(
    account: AccountConfig,
    config: Config,
    *,
    pre_status: AccountStatus | None,
    phantom_recovery: bool,
    daemon_log: bool,
    kick_type: str,
    model_override: str | None,
) -> KickEvent:
    surfaces = _codex_retry_surfaces_for_account(account)
    cluster_id = uuid.uuid4().hex
    events: list[KickEvent] = []
    final_event: KickEvent | None = None
    for index, surface in enumerate(surfaces, start=1):
        event = _run_kick_attempt(
            account,
            config,
            phantom_recovery=phantom_recovery,
            daemon_log=daemon_log,
            kick_type=kick_type,
            model_override=model_override,
            codex_surface=surface,
            attempt=index,
            max_attempts=len(surfaces),
            pre_status=pre_status,
            record_event=False,
            log_result=False,
        )
        event.codex_cluster_id = cluster_id
        event.codex_cluster_origin = CODEX_CLUSTER_ORIGIN_ADAPTIVE
        events.append(event)
        winner = _apply_codex_cluster_reset_clock_match(events, event)
        if winner is not None:
            final_event = winner
            break
        final_event = event
        if not _codex_surface_retry_needed(account, event, kick_type=kick_type):
            break
        if index < len(surfaces) and not daemon_log:
            console.print(
                f"[yellow]{surface} gave no generation evidence; trying {surfaces[index]}...[/yellow]",
                end=" ",
            )
    if final_event is None:
        raise RuntimeError("Codex retry surfaces were empty")
    _finalize_codex_surface_cluster(account, events, daemon_log=daemon_log)
    return final_event


def _codex_retry_surfaces_for_account(account: AccountConfig) -> tuple[str, ...]:
    stats_file = _codex_surface_stats_file()
    order = codex_surface_order_for_account(account, stats_file)
    return order or DEFAULT_CODEX_SURFACE_ORDER


def _codex_surface_stats_file() -> Path:
    return CONFIG_DIR / "codex-surface-stats.json"


def _finalize_codex_surface_cluster(
    account: AccountConfig,
    events: list[KickEvent],
    *,
    daemon_log: bool,
) -> None:
    for event in events:
        if event.codex_confirmation_method is None:
            event.codex_confirmation_method = "none"
        append_kick_event(event)
        _log_kick_result(
            event,
            daemon_log=daemon_log,
            surface=event.codex_surface,
            attempt=event.codex_attempt,
            max_attempts=event.codex_max_attempts,
        )
    try:
        demotions = update_codex_surface_stats(_codex_surface_stats_file(), account, events) or []
    except Exception:
        demotions = []
    for demotion in demotions:
        _record_codex_surface_management_event(
            account,
            kind="codex_surface_demote",
            surfaces=[str(demotion.get("surface"))],
            reason=str(demotion.get("reason") or "surface auto-demoted"),
            daemon_log=daemon_log,
        )


def _record_codex_surface_management_event(
    account: AccountConfig,
    *,
    kind: str,
    surfaces: list[str],
    reason: str,
    daemon_log: bool,
) -> None:
    append_kick_event(
        KickEvent(
            label=account.label,
            success=True,
            confirmed=True,
            kind=kind,
            kick_type=kind,
            response_text=f"{kind}: {', '.join(surfaces)}. {reason}",
        )
    )
    if daemon_log:
        _daemon_log(
            kind,
            account=account.label,
            surfaces=",".join(surfaces),
            reason=reason,
        )


def _run_kick_attempt(
    account: AccountConfig,
    config: Config,
    *,
    phantom_recovery: bool,
    daemon_log: bool,
    kick_type: str,
    model_override: str | None,
    codex_surface: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    pre_status: AccountStatus | None = None,
    record_event: bool = True,
    log_result: bool = True,
    verify_codex: bool = True,
) -> KickEvent:
    _log_kick_start(
        account,
        daemon_log=daemon_log,
        surface=codex_surface,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    if account.provider == "claude" and kick_type == "session" and not phantom_recovery:
        event = _kick_claude_usage_session(
            account,
            config,
            pre_status=pre_status,
            daemon_log=daemon_log,
        )
    else:
        kwargs = {
            "record": False,
            "phantom_recovery": phantom_recovery,
            "model_override": model_override,
        }
        if account.provider == "codex":
            kwargs["codex_surface"] = codex_surface or CODEX_KICK_SURFACE_REPO
        attempt_started_at = time.time()
        event = kick_account(account, **kwargs)
        attempt_finished_at = event.timestamp or time.time()

    if account.provider == "codex":
        event.codex_surface = codex_surface or event.codex_surface or CODEX_KICK_SURFACE_REPO
        event.codex_attempt = attempt
        event.codex_max_attempts = max_attempts
        event.codex_attempt_started_at = attempt_started_at
        event.codex_attempt_finished_at = attempt_finished_at
    if event.success and phantom_recovery:
        event = _verify_phantom_kick(account, event, config, daemon_log=daemon_log)
    elif event.success and account.provider == "codex" and kick_type == "session" and verify_codex:
        event = _verify_codex_session_anchor(
            account,
            event,
            pre_status,
            config,
            daemon_log=daemon_log,
        )
    elif (
        event.success
        and account.provider == "codex"
        and verify_codex
        and kick_type == "kick"
        and _codex_kick_provider_movement_verification_possible(account)
    ):
        event = _verify_codex_kick_provider_movement(account, event, pre_status, config)
    _apply_kick_event_kind(event, phantom_recovery=phantom_recovery, kick_type=kick_type)
    if record_event:
        append_kick_event(event)
    if log_result:
        _log_kick_result(
            event,
            daemon_log=daemon_log,
            surface=codex_surface,
            attempt=attempt,
            max_attempts=max_attempts,
        )
    return event


def _apply_kick_event_kind(
    event: KickEvent,
    *,
    phantom_recovery: bool,
    kick_type: str,
) -> None:
    if phantom_recovery:
        event.kind = "phantom_recovery"
        event.kick_type = "session"
    elif kick_type != "kick":
        event.kind = kick_type
        event.kick_type = kick_type


def _codex_session_confirmation_pending_for_user(event: KickEvent) -> bool:
    return (
        event.success
        and not event.confirmed
        and (event.kick_type or event.kind) == "session"
        and event.post_kick_status == "pending"
        and event.codex_confirmation_method == "pending_reset_clock"
        and bool(event.evidence_response or event.evidence_tokens or event.response_text)
    )


def _codex_surface_retry_needed(
    account: AccountConfig,
    event: KickEvent,
    *,
    kick_type: str,
) -> bool:
    if not (
        event.success
        and not event.confirmed
        and event.error
        in {
            CODEX_NO_GENERATION_EVIDENCE_ERROR,
            PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
            CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
        }
    ):
        return False
    if _codex_direct_generated_session_should_pause_for_late_attribution(
        account,
        event,
        kick_type=kick_type,
    ):
        event.post_kick_status = "pending"
        event.codex_confirmation_method = "pending_reset_clock"
        event.error = CODEX_SESSION_ANCHOR_PENDING_ERROR
        return False
    return True


def _codex_direct_generated_session_should_pause_for_late_attribution(
    account: AccountConfig,
    event: KickEvent,
    *,
    kick_type: str,
) -> bool:
    return (
        kick_type == "session"
        and account.provider == "codex"
        and account.source == DataSource.CODEX_DIRECT
        and bool(account.provider_home)
        and _event_has_generation_evidence(event)
        and event.error
        in {
            CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
        }
        and (event.codex_attempt or 0) >= CODEX_DIRECT_GENERATED_PENDING_SURFACE_LIMIT
    )


def _log_kick_start(
    account: AccountConfig,
    *,
    daemon_log: bool,
    surface: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> None:
    if not daemon_log:
        return
    fields = _kick_log_fields(
        account.label,
        surface=surface,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    _daemon_log("kick_start", **fields)


def _log_kick_result(
    event: KickEvent,
    *,
    daemon_log: bool,
    surface: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> None:
    if not daemon_log:
        return
    fields = _kick_log_fields(
        event.label,
        surface=surface,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    if event.success:
        event_name = "kick_confirmed" if event.confirmed else "kick_attempted"
        fields.update(_kick_evidence_log_fields(event))
        if event.error:
            fields["reason"] = event.error
        _daemon_log(event_name, **fields)
        return
    fields.update(_kick_evidence_log_fields(event))
    if event.error:
        fields["error"] = event.error
    _daemon_log("kick_failed", **fields)


def _kick_log_fields(
    label: str,
    *,
    surface: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {"account": label}
    if surface is not None:
        fields["surface"] = surface
    if attempt is not None:
        fields["attempt"] = attempt
    if max_attempts is not None:
        fields["max_attempts"] = max_attempts
    return fields


def _kick_evidence_log_fields(event: KickEvent) -> dict[str, object]:
    fields: dict[str, object] = {}
    if event.evidence_response is not None:
        fields["response_evidence"] = event.evidence_response
    if event.evidence_tokens is not None:
        fields["token_evidence"] = event.evidence_tokens
    if event.evidence_provider_moved is not None:
        fields["provider_moved"] = event.evidence_provider_moved
    if event.post_kick_status is not None:
        fields["post_status"] = event.post_kick_status
    if event.codex_confirmation_method is not None:
        fields["confirmation_method"] = event.codex_confirmation_method
    if (
        event.codex_confirmation_method in {"reset_clock", "late_reset_clock"}
        and event.codex_anchor_match_delta_seconds is not None
    ):
        fields["anchor_delta_seconds"] = round(event.codex_anchor_match_delta_seconds, 3)
    return fields


def _kick_claude_usage_session(
    account: AccountConfig,
    config: Config,
    *,
    pre_status: AccountStatus | None = None,
    daemon_log: bool = False,
) -> KickEvent:
    """Use Claude /usage as an explicit tracked session anchor."""
    event, status = _run_claude_usage_touch(
        account,
        config,
        kind="session",
        kick_type="session",
        success_response="Claude /usage session anchor completed.",
        failure_prefix="Claude /usage session kick failed",
        daemon_log=daemon_log,
    )
    if event.success:
        event = _verify_claude_session_anchor(event, pre_status, status)
    return event


def _run_claude_usage_touch(
    account: AccountConfig,
    config: Config,
    *,
    kind: str,
    kick_type: str,
    success_response: str,
    failure_prefix: str,
    daemon_log: bool = False,
) -> tuple[KickEvent, AccountStatus | None]:
    event_timestamp = time.time()
    probe_account = (
        account
        if account.source == DataSource.CLAUDE_DIRECT
        else replace(account, source=DataSource.CLAUDE_DIRECT)
    )
    try:
        with claude_cli_usage_refresh_allowed():
            status = _fetch_status(probe_account, config)
    except Exception as exc:
        return KickEvent(
            label=account.label,
            timestamp=event_timestamp,
            success=False,
            confirmed=False,
            kind=kind,
            kick_type=kick_type,
            prompt_text="/usage",
            error=f"{failure_prefix}: {exc}",
        ), None

    if status.state == AccountState.UNKNOWN:
        _save_status_cache(
            [account],
            {account_key_string(account): status},
            _failures_by_key_from_status_pairs([account], [status]),
            daemon_log=daemon_log,
        )
        return KickEvent(
            label=account.label,
            timestamp=event_timestamp,
            success=False,
            confirmed=False,
            kind=kind,
            kick_type=kick_type,
            prompt_text="/usage",
            error=status.error or f"{failure_prefix}: Claude /usage did not return usable status.",
        ), status
    if status.source_detail != "claude-cli-usage":
        return KickEvent(
            label=account.label,
            timestamp=event_timestamp,
            success=False,
            confirmed=False,
            kind=kind,
            kick_type=kick_type,
            prompt_text="/usage",
            error=f"{failure_prefix}: Claude /usage fell back to non-direct status.",
        ), status

    status = replace(status, label=account.label)
    _attach_claude_usage_probe_context(status)
    _save_status_cache(
        [account],
        {account_key_string(account): status},
        _failures_by_key_from_status_pairs([account], [status]),
        daemon_log=daemon_log,
    )
    return KickEvent(
        label=account.label,
        timestamp=event_timestamp,
        success=True,
        confirmed=True,
        kind=kind,
        kick_type=kick_type,
        prompt_text="/usage",
        response_text=success_response,
    ), status


def _attach_claude_usage_probe_context(status: AccountStatus) -> None:
    observed_at = status.observed_at or _status_cache_observed_at()
    status.observed_at = observed_at
    context = getattr(status, "_claude_probe_context", None)
    if not isinstance(context, ClaudeProbeContext):
        context = ClaudeProbeContext()
    context.last_direct_probe_at = observed_at
    context.last_direct_probe_error = None
    context.last_direct_success_at = observed_at
    context.last_direct_success_status = replace(status)
    setattr(status, "_claude_probe_context", context)


def _verify_claude_session_anchor(
    event: KickEvent,
    pre_status: AccountStatus | None,
    post_status: AccountStatus | None,
) -> KickEvent:
    if post_status is None or post_status.state == AccountState.UNKNOWN:
        event.confirmed = False
        event.evidence_provider_moved = False
        event.post_kick_status = "unknown"
        event.error = (
            post_status.error
            if post_status is not None and post_status.error
            else "TokenKick could not verify the provider status after the Claude session kick"
        )
        return event

    if _claude_session_anchor_moved_by_kick(pre_status, post_status):
        event.confirmed = True
        event.evidence_provider_moved = True
        event.post_kick_status = "moved"
        if event.error == CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR:
            event.error = None
        return event
    if _claude_session_already_active(post_status):
        event.confirmed = True
        event.evidence_provider_moved = False
        event.post_kick_status = "already_active"
        event.response_text = "Claude /usage confirmed the session window is already active."
        if event.error == CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR:
            event.error = None
        return event

    event.confirmed = False
    event.evidence_provider_moved = False
    event.post_kick_status = "unchanged"
    event.error = CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR
    return event


def _claude_session_anchor_moved_by_kick(
    pre_status: AccountStatus | None,
    post_status: AccountStatus,
) -> bool:
    if pre_status is None:
        return False
    if pre_status.state != AccountState.ACTIVE and post_status.state == AccountState.ACTIVE:
        return True
    if _reset_anchor_moved(pre_status.session_resets_at, post_status.session_resets_at):
        return True
    if _usage_percent_increased(pre_status.used_percent, post_status.used_percent):
        return True
    if _is_phantom_session_candidate(post_status):
        return False
    if _usage_percent_increased(pre_status.session_used_percent, post_status.session_used_percent):
        return True
    if (
        pre_status.window_anchor_state == "available_unanchored"
        and post_status.window_anchor_state not in {None, "available_unanchored"}
        and not _is_phantom_session_candidate(post_status)
    ):
        return True
    pre_resets = pre_status.session_resets_in_seconds
    post_resets = post_status.session_resets_in_seconds
    return (
        pre_resets is not None
        and post_resets is not None
        and post_resets >= pre_resets + CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS
        and not _is_phantom_session_candidate(post_status)
    )


def _claude_session_already_active(status: AccountStatus) -> bool:
    return (
        status.state == AccountState.ACTIVE
        and status.session_resets_in_seconds is not None
        and status.session_resets_in_seconds > SESSION_KICK_WINDOW_START_GRACE_SECONDS
        and status.session_used_percent is not None
        and status.session_used_percent > 0.0
        and status.used_percent is not None
        and status.used_percent > 0.0
    )


def _verify_codex_session_anchor(
    account: AccountConfig,
    event: KickEvent,
    pre_status: AccountStatus | None,
    config: Config | None = None,
    *,
    daemon_log: bool = False,
) -> KickEvent:
    post_status = _poll_codex_session_anchor_status(account, pre_status)
    if (
        post_status is not None
        and post_status.state != AccountState.UNKNOWN
        and not _session_anchor_moved(pre_status, post_status, reject_zero_usage_near_full=True)
        and _event_has_generation_evidence(event)
        and _codex_should_defer_delayed_verification(account, event, daemon_log=daemon_log)
    ):
        # Do not block interactive/cron or daemon contexts on the long delayed
        # provider verification. A later status refresh or daemon poll can
        # confirm this attempt through reset-clock late attribution.
        return _mark_codex_pending_reset_clock(
            account,
            event,
            post_status,
            config,
            daemon_log=daemon_log,
        )
    if post_status is None or post_status.state == AccountState.UNKNOWN:
        event.confirmed = False
        event.evidence_provider_moved = False
        event.post_kick_status = "unknown"
        event.codex_confirmation_method = "none"
        if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
            event.error = "TokenKick could not verify the provider status after the Codex session kick"
        return event
    _attach_codex_provider_confirmation_diagnostics(event, post_status)
    if event.codex_provider_stale is True and _event_has_generation_evidence(event):
        return _mark_codex_pending_reset_clock(
            account,
            event,
            None,
            config,
            daemon_log=daemon_log,
        )

    if _session_anchor_moved(
        pre_status,
        post_status,
        reject_zero_usage_near_full=True,
    ) and _codex_session_anchor_matches_event(
        event,
        post_status,
    ):
        event.confirmed = True
        event.evidence_provider_moved = True
        event.post_kick_status = "moved"
        event.codex_confirmation_method = "reset_clock"
        event.codex_attribution = _codex_attribution_for_anchor_delta(
            event.codex_anchor_match_delta_seconds
        )
        if event.error in {
            CODEX_NO_GENERATION_EVIDENCE_ERROR,
            CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
            CODEX_SESSION_ANCHOR_PENDING_ERROR,
        }:
            event.error = None
        return event
    if _session_anchor_moved(pre_status, post_status, reject_zero_usage_near_full=True):
        event.confirmed = False
        event.evidence_provider_moved = False
        event.post_kick_status = "misaligned"
        event.codex_confirmation_method = "none"
        event.error = CODEX_SESSION_ANCHOR_MISALIGNED_ERROR
        return event

    event.confirmed = False
    event.evidence_provider_moved = False
    event.post_kick_status = "phantom" if _is_phantom_session_candidate(post_status) else "unchanged"
    event.codex_confirmation_method = "none"
    if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
        event.error = CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR
    return event


def _mark_codex_pending_reset_clock(
    account: AccountConfig,
    event: KickEvent,
    status: AccountStatus | None,
    config: Config | None = None,
    *,
    daemon_log: bool = False,
) -> KickEvent:
    if status is not None:
        _attach_codex_provider_confirmation_diagnostics(event, status)
    event.confirmed = False
    event.evidence_provider_moved = False
    event.post_kick_status = "pending"
    event.codex_confirmation_method = "pending_reset_clock"
    event.error = CODEX_SESSION_ANCHOR_PENDING_ERROR
    if daemon_log:
        _daemon_log(
            "codex_delayed_verification_deferred",
            account=account.label,
            surface=event.codex_surface,
            cluster_id=event.codex_cluster_id,
            backoff_seconds=_codex_surface_retry_backoff_seconds(config),
        )
    return event


def _codex_delayed_verification_deferrable(account: AccountConfig) -> bool:
    return account.source == DataSource.CODEX_DIRECT and bool(account.provider_home)


def _codex_should_defer_delayed_verification(
    account: AccountConfig,
    event: KickEvent,
    *,
    daemon_log: bool,
) -> bool:
    if not daemon_log:
        return True
    if not _codex_delayed_verification_deferrable(account):
        return False
    if (
        event.codex_attempt is not None
        and event.codex_max_attempts is not None
        and event.codex_attempt < event.codex_max_attempts
    ):
        return False
    return True


def _codex_kick_provider_movement_verification_possible(account: AccountConfig) -> bool:
    return account.source == DataSource.CODEX_DIRECT and bool(account.provider_home)


def _verify_codex_kick_provider_movement(
    account: AccountConfig,
    event: KickEvent,
    pre_status: AccountStatus | None,
    config: Config | None,
) -> KickEvent:
    if pre_status is None or pre_status.state == AccountState.UNKNOWN or pre_status.stale:
        event.confirmed = False
        event.evidence_provider_moved = None
        event.post_kick_status = "not_checked"
        event.codex_confirmation_method = "none"
        if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
            event.error = CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR
        return event

    post_status = _fetch_codex_provider_movement_status(account, config)
    if post_status is None or post_status.state == AccountState.UNKNOWN:
        event.confirmed = False
        event.evidence_provider_moved = False
        event.post_kick_status = "unknown"
        event.codex_confirmation_method = "none"
        if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
            event.error = CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR
        return event

    _attach_codex_provider_confirmation_diagnostics(event, post_status)
    if event.codex_provider_stale is True:
        event.confirmed = False
        event.evidence_provider_moved = False
        event.post_kick_status = "pending"
        event.codex_confirmation_method = "none"
        if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
            event.error = CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR
        return event

    if _provider_status_moved(pre_status, post_status):
        event.confirmed = True
        event.evidence_provider_moved = True
        event.post_kick_status = "moved"
        event.codex_confirmation_method = "provider_moved"
        event.codex_attribution = CODEX_ATTRIBUTION_STRONG
        if event.error in {
            CODEX_NO_GENERATION_EVIDENCE_ERROR,
            CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR,
        }:
            event.error = None
        return event

    event.confirmed = False
    event.evidence_provider_moved = False
    event.post_kick_status = "unchanged"
    event.codex_confirmation_method = "none"
    if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
        event.error = CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR
    return event


def _fetch_codex_provider_movement_status(
    account: AccountConfig,
    config: Config | None,
) -> AccountStatus | None:
    if account.source == DataSource.CODEX_DIRECT and account.provider_home:
        return _read_codex_appserver_ratelimits_for_account(account, Path(account.provider_home))
    return _fetch_status(account, config)


def _provider_status_moved(pre_status: AccountStatus, post_status: AccountStatus) -> bool:
    if pre_status.state != AccountState.ACTIVE and post_status.state == AccountState.ACTIVE:
        return True
    if _reset_anchor_moved(pre_status.resets_at, post_status.resets_at):
        return True
    if _usage_percent_increased(pre_status.used_percent, post_status.used_percent):
        return True
    if (
        pre_status.window_anchor_state == "available_unanchored"
        and post_status.window_anchor_state not in {None, "available_unanchored"}
    ):
        return True
    return False


def _reset_anchor_moved(pre_reset_at: float | None, post_reset_at: float | None) -> bool:
    if pre_reset_at is None or post_reset_at is None:
        return False
    return abs(post_reset_at - pre_reset_at) > SESSION_KICK_WINDOW_START_GRACE_SECONDS


def _usage_percent_increased(pre_used: float | None, post_used: float | None) -> bool:
    if pre_used is None or post_used is None:
        return False
    return post_used > pre_used + 0.01


def _delayed_codex_session_anchor_status(
    account: AccountConfig,
    pre_status: AccountStatus | None,
    fallback_status: AccountStatus,
    config: Config | None = None,
) -> AccountStatus:
    if account.source != DataSource.CODEX_DIRECT or not account.provider_home:
        return fallback_status
    delay_seconds = _codex_surface_retry_backoff_seconds(config)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    delayed_status = _fetch_codex_session_anchor_status(account)
    if delayed_status is None or delayed_status.state == AccountState.UNKNOWN:
        return fallback_status
    if _session_anchor_moved(pre_status, delayed_status, reject_zero_usage_near_full=True):
        return delayed_status
    return delayed_status


def _codex_session_anchor_matches_event(
    event: KickEvent,
    post_status: AccountStatus,
) -> bool:
    inferred_anchor_at = _codex_status_inferred_anchor_at(post_status)
    if inferred_anchor_at is None:
        return False
    delta = _codex_anchor_match_delta_seconds(event, inferred_anchor_at)
    event.codex_inferred_anchor_at = inferred_anchor_at
    event.codex_anchor_match_delta_seconds = delta
    return delta <= CODEX_SESSION_ANCHOR_MATCH_TOLERANCE_SECONDS


def _codex_attribution_for_anchor_delta(delta: float | None) -> str:
    if delta is None:
        return CODEX_ATTRIBUTION_STRONG
    if delta <= CODEX_STRONG_ATTRIBUTION_DELTA_SECONDS:
        return CODEX_ATTRIBUTION_STRONG
    return CODEX_ATTRIBUTION_TIMING_MATCH


def _attach_codex_provider_confirmation_diagnostics(
    event: KickEvent,
    post_status: AccountStatus,
) -> None:
    event.codex_provider_observed_at = post_status.observed_at
    event.codex_provider_session_resets_at = post_status.session_resets_at
    event.codex_provider_session_used_percent = post_status.session_used_percent
    event.codex_provider_stale = _codex_provider_status_stale_for_event(event, post_status)
    inferred_anchor_at = _codex_status_inferred_anchor_at(post_status)
    if inferred_anchor_at is None:
        return
    event.codex_inferred_anchor_at = inferred_anchor_at
    event.codex_anchor_match_delta_seconds = _codex_anchor_match_delta_seconds(
        event,
        inferred_anchor_at,
    )


def _codex_provider_status_stale_for_event(event: KickEvent, status: AccountStatus) -> bool | None:
    if status.observed_at is None or event.codex_attempt_started_at is None:
        return None
    observed = _parse_status_cache_observed_at(status.observed_at)
    if observed is None:
        return None
    return observed.timestamp() < event.codex_attempt_started_at


def _codex_status_inferred_anchor_at(post_status: AccountStatus) -> float | None:
    window_minutes = _effective_session_window_minutes(post_status)
    if window_minutes is None:
        return None
    window_seconds = window_minutes * 60
    if post_status.session_resets_at is not None:
        reset_at = post_status.session_resets_at
    elif post_status.session_resets_in_seconds is not None:
        reset_at = time.time() + post_status.session_resets_in_seconds
    else:
        return None
    return reset_at - window_seconds


def _codex_anchor_match_delta_seconds(event: KickEvent, inferred_anchor_at: float) -> float:
    candidates = [
        event.codex_attempt_started_at,
        event.codex_attempt_finished_at,
        event.timestamp,
    ]
    numeric_candidates = [value for value in candidates if isinstance(value, (int, float))]
    if not numeric_candidates:
        return float("inf")
    start = event.codex_attempt_started_at
    finish = event.codex_attempt_finished_at
    if isinstance(start, (int, float)) and isinstance(finish, (int, float)):
        lower = min(start, finish)
        upper = max(start, finish)
        if lower <= inferred_anchor_at <= upper:
            return 0.0
    return min(abs(inferred_anchor_at - value) for value in numeric_candidates)


def _apply_codex_cluster_reset_clock_match(
    events: list[KickEvent],
    source_event: KickEvent,
) -> KickEvent | None:
    inferred_anchor_at = source_event.codex_inferred_anchor_at
    if inferred_anchor_at is None:
        return source_event if source_event.success and source_event.confirmed else None
    if source_event.post_kick_status not in {"moved", "misaligned"}:
        return source_event if source_event.success and source_event.confirmed else None
    matches = [
        (event, _codex_anchor_match_delta_seconds(event, inferred_anchor_at))
        for event in events
        if event.success and _event_has_generation_evidence(event)
    ]
    if not matches:
        return None
    winner, delta = min(matches, key=lambda item: item[1])
    if delta > CODEX_SESSION_ANCHOR_MATCH_TOLERANCE_SECONDS:
        return source_event if source_event.success and source_event.confirmed else None
    winner.confirmed = True
    winner.evidence_provider_moved = True
    winner.post_kick_status = "moved"
    winner.codex_inferred_anchor_at = inferred_anchor_at
    winner.codex_anchor_match_delta_seconds = delta
    winner.codex_confirmation_method = "reset_clock"
    winner.codex_attribution = _codex_attribution_for_anchor_delta(delta)
    if winner.error in {
        CODEX_NO_GENERATION_EVIDENCE_ERROR,
        CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
        CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
        CODEX_SESSION_ANCHOR_PENDING_ERROR,
        PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
    }:
        winner.error = None
    for event in events:
        if event is winner:
            continue
        if _codex_cluster_event_was_superseded(event, winner):
            event.post_kick_status = "superseded"
            if event.codex_confirmation_method is None:
                event.codex_confirmation_method = "none"
            continue
        if event.success and event.confirmed:
            event.confirmed = False
            event.evidence_provider_moved = False
            event.post_kick_status = "misaligned"
            if event.error is None:
                event.error = CODEX_SESSION_ANCHOR_MISALIGNED_ERROR
        if event.codex_confirmation_method is None:
            event.codex_confirmation_method = "none"
    return winner


def _apply_codex_late_attribution(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    *,
    daemon_log: bool = False,
) -> int:
    """Retroactively confirm the closest recent Codex attempt from a live reset clock."""
    if not accounts or not statuses_by_key:
        return 0
    account_by_key = {account_key_string(account): account for account in accounts}
    confirmed = 0
    for key, status in statuses_by_key.items():
        account = account_by_key.get(key)
        if account is None or account.provider != "codex":
            continue
        if status.stale or status.state == AccountState.UNKNOWN:
            continue
        inferred_anchor_at = _codex_status_inferred_anchor_at(status)
        if inferred_anchor_at is None:
            continue
        confirmed += _apply_codex_late_attribution_for_account(
            account,
            status,
            inferred_anchor_at,
            daemon_log=daemon_log,
        )
    return confirmed


def _apply_codex_late_attribution_for_account(
    account: AccountConfig,
    status: AccountStatus,
    inferred_anchor_at: float,
    *,
    daemon_log: bool = False,
) -> int:
    winner: KickEvent | None = None
    winning_cluster: list[KickEvent] = []

    def update(events: list[KickEvent]) -> bool:
        nonlocal winner, winning_cluster
        candidates = _codex_late_attribution_candidates(
            events,
            account.label,
            inferred_anchor_at,
        )
        if not candidates:
            return False
        event, delta = min(candidates, key=lambda item: item[1])
        if delta > CODEX_SESSION_ANCHOR_MATCH_TOLERANCE_SECONDS:
            return False
        _mark_codex_late_attribution_winner(event, inferred_anchor_at, delta, status)
        _mark_codex_late_attribution_superseded_attempts(events, event)
        winner = event
        winning_cluster = [
            candidate
            for candidate in events
            if candidate.label == account.label
            and candidate.codex_cluster_id == event.codex_cluster_id
        ]
        return True

    try:
        history_changed = update_kick_history(update)
    except (OSError, StateFileError):
        return 0
    if not history_changed or winner is None:
        return 0
    if winner.codex_cluster_origin == CODEX_CLUSTER_ORIGIN_BURST:
        try:
            demotions = update_codex_surface_stats(
                _codex_surface_stats_file(),
                account,
                winning_cluster,
            ) or []
        except Exception:
            demotions = []
        for demotion in demotions:
            _record_codex_surface_management_event(
                account,
                kind="codex_surface_demote",
                surfaces=[str(demotion.get("surface"))],
                reason=str(demotion.get("reason") or "surface auto-demoted"),
                daemon_log=daemon_log,
            )
    else:
        try:
            apply_codex_surface_late_confirmation(_codex_surface_stats_file(), account, winner)
        except Exception:
            pass
    if daemon_log:
        _daemon_log(
            "codex_late_attribution",
            account=account.label,
            surface=winner.codex_surface,
            cluster_id=winner.codex_cluster_id,
            inferred_anchor_at=round(inferred_anchor_at, 3),
            anchor_delta_seconds=round(winner.codex_anchor_match_delta_seconds or 0.0, 3),
        )
    return 1


def _codex_late_attribution_candidates(
    events: list[KickEvent],
    label: str,
    inferred_anchor_at: float,
) -> list[tuple[KickEvent, float]]:
    cutoff = time.time() - CODEX_LATE_ATTRIBUTION_LOOKBACK_SECONDS
    clusters: dict[str, list[KickEvent]] = {}
    for event in events:
        if event.label != label:
            continue
        if event.timestamp < cutoff:
            continue
        if event.kind != "session" and event.kick_type != "session":
            continue
        if event.codex_cluster_id is None or event.codex_surface is None:
            continue
        clusters.setdefault(event.codex_cluster_id, []).append(event)

    candidates: list[tuple[KickEvent, float]] = []
    for cluster in clusters.values():
        if any(event.success and event.confirmed for event in cluster):
            continue
        for event in cluster:
            if not event.success or not _event_has_generation_evidence(event):
                continue
            candidates.append((event, _codex_anchor_match_delta_seconds(event, inferred_anchor_at)))
    return candidates


def _codex_unconfirmed_current_session_candidate(
    account: AccountConfig | None,
    status: AccountStatus,
    history: list[KickEvent],
) -> KickEvent | None:
    if account is None or account.provider != "codex":
        return None
    if status.state == AccountState.UNKNOWN or status.stale:
        return None
    inferred_anchor_at = _codex_status_inferred_anchor_at(status)
    if inferred_anchor_at is None:
        return None
    candidates = _codex_late_attribution_candidates(history, account.label, inferred_anchor_at)
    if not candidates:
        return None
    event, delta = min(candidates, key=lambda item: item[1])
    if delta > CODEX_SESSION_ANCHOR_MATCH_TOLERANCE_SECONDS:
        return None
    return event


def _mark_codex_late_attribution_winner(
    event: KickEvent,
    inferred_anchor_at: float,
    delta: float,
    status: AccountStatus,
) -> None:
    event.confirmed = True
    event.evidence_provider_moved = True
    event.post_kick_status = "moved"
    event.codex_inferred_anchor_at = inferred_anchor_at
    event.codex_anchor_match_delta_seconds = delta
    event.codex_confirmation_method = "late_reset_clock"
    event.codex_attribution = _codex_attribution_for_anchor_delta(delta)
    event.codex_provider_observed_at = status.observed_at
    event.codex_provider_session_resets_at = status.session_resets_at
    event.codex_provider_session_used_percent = status.session_used_percent
    event.codex_provider_stale = _codex_provider_status_stale_for_event(event, status)
    if event.error in {
        CODEX_NO_GENERATION_EVIDENCE_ERROR,
        CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
        CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
        CODEX_SESSION_ANCHOR_PENDING_ERROR,
        PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
    }:
        event.error = None


def _mark_codex_late_attribution_superseded_attempts(
    events: list[KickEvent],
    winner: KickEvent,
) -> None:
    for event in events:
        if _codex_cluster_event_was_superseded(event, winner):
            event.post_kick_status = "superseded"


def _codex_cluster_event_was_superseded(event: KickEvent, winner: KickEvent) -> bool:
    if event is winner:
        return False
    if not event.success or event.confirmed:
        return False
    if not _event_has_generation_evidence(event):
        return False
    if event.codex_cluster_id is None or event.codex_cluster_id != winner.codex_cluster_id:
        return False
    if event.label != winner.label:
        return False
    return event.timestamp > winner.timestamp


def _poll_codex_session_anchor_status(
    account: AccountConfig,
    pre_status: AccountStatus | None,
) -> AccountStatus | None:
    attempts = max(1, CODEX_SESSION_ANCHOR_VERIFY_ATTEMPTS)
    for index in range(attempts):
        post_status = _fetch_codex_session_anchor_status(account)
        if post_status is None:
            return None
        if post_status.state == AccountState.UNKNOWN:
            return post_status
        if _session_anchor_moved(pre_status, post_status, reject_zero_usage_near_full=True):
            return post_status
        if index < attempts - 1:
            if account.source != DataSource.CODEX_DIRECT or not account.provider_home:
                return post_status
            time.sleep(CODEX_SESSION_ANCHOR_VERIFY_DELAY_SECONDS)
    return post_status


def _fetch_codex_session_anchor_status(account: AccountConfig) -> AccountStatus | None:
    if account.source == DataSource.CODEX_DIRECT and account.provider_home:
        return _read_codex_appserver_ratelimits_for_account(account, Path(account.provider_home))
    return _fetch_status(account)


def _session_anchor_moved(
    pre_status: AccountStatus | None,
    post_status: AccountStatus,
    *,
    reject_zero_usage_near_full: bool = False,
) -> bool:
    post_resets = post_status.session_resets_in_seconds
    if post_resets is None or post_resets <= SESSION_KICK_WINDOW_START_GRACE_SECONDS:
        return False
    if (
        reject_zero_usage_near_full
        and
        post_status.session_used_percent == 0.0
        and _session_reset_is_near_full(post_status)
    ):
        return False
    pre_resets = pre_status.session_resets_in_seconds if pre_status is not None else None
    if (
        pre_resets is not None
        and post_resets >= pre_resets + CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS
        and not _is_phantom_session_candidate(post_status)
    ):
        return True
    if (
        post_status.window_anchor_state == "available_unanchored"
        or _is_phantom_session_candidate(post_status)
    ):
        return False
    if _session_reset_is_near_full(post_status):
        return True
    if pre_resets is None:
        return False
    return post_resets >= pre_resets + CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS


def _verify_phantom_kick(
    account: AccountConfig,
    event: KickEvent,
    config: Config | None = None,
    *,
    daemon_log: bool = False,
) -> KickEvent:
    post_status = _fetch_status(account, config)
    if (
        post_status.state != AccountState.UNKNOWN
        and _is_phantom_session_candidate(post_status)
        and account.provider == "codex"
        and _event_has_generation_evidence(event)
        and daemon_log
        and _codex_delayed_verification_deferrable(account)
    ):
        return _mark_codex_pending_reset_clock(
            account,
            event,
            post_status,
            config,
            daemon_log=daemon_log,
        )
    if post_status.state == AccountState.UNKNOWN:
        event.confirmed = False
        event.kind = "probe"
        event.evidence_provider_moved = False
        event.post_kick_status = "unknown"
        event.error = "TokenKick could not verify the provider status after the kick attempt"
    elif _is_phantom_session_candidate(post_status):
        event.evidence_provider_moved = False
        event.post_kick_status = "phantom"
        if account.provider == "codex" and _event_has_generation_evidence(event):
            event.confirmed = False
            event.error = PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR
        else:
            event.confirmed = False
            event.kind = "probe"
            if event.error != CODEX_NO_GENERATION_EVIDENCE_ERROR:
                event.error = AMBIGUOUS_PHANTOM_KICK_ERROR
    else:
        event.confirmed = True
        event.evidence_provider_moved = True
        event.post_kick_status = "moved"
        if account.provider == "codex":
            event.codex_confirmation_method = "provider_moved"
            event.codex_attribution = CODEX_ATTRIBUTION_STRONG
        if event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR:
            event.error = None
        _clear_phantom_session_observation(account)
        _clear_phantom_recovery_state(account)
    return event


def _delayed_codex_phantom_status(
    account: AccountConfig,
    fallback_status: AccountStatus,
    config: Config | None,
) -> AccountStatus:
    if account.source != DataSource.CODEX_DIRECT or not account.provider_home:
        return fallback_status
    delay_seconds = _codex_surface_retry_backoff_seconds(config)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    delayed_status = _fetch_status(account, config)
    if delayed_status.state == AccountState.UNKNOWN:
        return fallback_status
    return delayed_status


def _codex_surface_retry_backoff_seconds(config: Config | None = None) -> float:
    raw = os.environ.get(CODEX_SURFACE_RETRY_BACKOFF_ENV)
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return CODEX_SESSION_ANCHOR_DELAYED_VERIFY_SECONDS
    if config is None:
        return CODEX_SESSION_ANCHOR_DELAYED_VERIFY_SECONDS
    return max(0.0, float(config.codex_surface_retry_backoff_seconds))


def _codex_burst_ladder_enabled(config: Config | None = None) -> bool:
    raw = os.environ.get(CODEX_BURST_LADDER_ENABLED_ENV)
    if raw is None:
        raw = os.environ.get(CODEX_FIRE_ALL_SURFACES_ENV)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(config is not None and config.codex_burst_ladder_enabled)


def _codex_fire_all_surfaces_enabled(config: Config | None = None) -> bool:
    return _codex_burst_ladder_enabled(config)


def _codex_burst_ladder_surface_order(config: Config | None = None) -> tuple[str, ...]:
    raw = os.environ.get(CODEX_BURST_LADDER_ORDER_ENV)
    if raw is None:
        raw = os.environ.get(CODEX_FIRE_ALL_SURFACE_ORDER_ENV)
    if raw is not None:
        order = _parse_codex_fire_all_surface_order(raw.split(","))
        return tuple(order) if order else CODEX_FIRE_ALL_DEFAULT_SURFACES
    if config is None or not config.codex_burst_ladder_surface_order:
        return CODEX_FIRE_ALL_DEFAULT_SURFACES
    return tuple(config.codex_burst_ladder_surface_order)


def _codex_fire_all_surface_order(config: Config | None = None) -> tuple[str, ...]:
    return _codex_burst_ladder_surface_order(config)


def _effective_codex_burst_ladder_surfaces(
    account: AccountConfig,
    config: Config | None = None,
) -> tuple[str, ...]:
    configured = list(_codex_burst_ladder_surface_order(config))
    force_pruned = set(account.codex_surface_force_prune or [])
    force_kept = set(account.codex_surface_force_keep or []) - force_pruned
    demoted: set[str] = set()
    if account.codex_surface_auto_demote:
        report = codex_surface_stats_for_account(account, _codex_surface_stats_file())
        raw_demoted = report.get("demotion", {}).get("demoted", {})
        if isinstance(raw_demoted, dict):
            demoted = {surface for surface in raw_demoted if surface in CODEX_FIRE_ALL_SURFACE_NAMES}
    surfaces = [
        surface
        for surface in configured
        if surface not in force_pruned and (surface not in demoted or surface in force_kept)
    ]
    for surface in CODEX_FIRE_ALL_DEFAULT_SURFACES:
        if surface in force_kept and surface not in surfaces:
            surfaces.append(surface)
    if not surfaces:
        raise click.ClickException(
            "Codex burst ladder has no active surfaces after applying order/subset, "
            "auto-demotion, and force-prune settings."
        )
    return tuple(surfaces)


def _parse_codex_fire_all_surface_order(values: list[str]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    valid = set(CODEX_FIRE_ALL_SURFACE_NAMES)
    for value in values:
        surface = value.strip()
        if not surface:
            continue
        if surface not in valid:
            raise click.ClickException(
                f'Unknown Codex surface "{surface}". Valid surfaces: '
                f"{', '.join(CODEX_FIRE_ALL_SURFACE_NAMES)}."
            )
        if surface in seen:
            raise click.ClickException(f'Duplicate Codex surface "{surface}".')
        seen.add(surface)
        order.append(surface)
    return order


def _codex_burst_ladder_gap_seconds(config: Config | None = None) -> float:
    raw = os.environ.get(CODEX_BURST_LADDER_GAP_ENV)
    if raw is None:
        raw = os.environ.get(CODEX_FIRE_ALL_SURFACE_GAP_ENV)
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 90.0
    if config is None:
        return 90.0
    return max(0.0, float(config.codex_burst_ladder_gap_seconds))


def _codex_fire_all_surface_gap_seconds(config: Config | None = None) -> float:
    return _codex_burst_ladder_gap_seconds(config)


def _render_auto_status_table(accounts: list[AccountConfig]) -> None:
    table = Table(title="TokenKick Auto-Kick", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Visible")
    table.add_column("Auto-kick")
    table.add_column("Weekly")
    table.add_column("Session")

    for account in accounts:
        if _is_monitor_only_provider(account.provider):
            state = "❌ monitor-only"
            weekly_state = "❌ monitor-only"
            session_state = "❌ monitor-only"
        elif account.provider not in KICKABLE_PROVIDERS:
            state = "❌ disabled (not kickable)"
            weekly_state = "❌ disabled (not kickable)"
            session_state = "❌ disabled (not kickable)"
        else:
            state = "✅ enabled" if account.auto_kick else "❌ disabled"
            weekly_state = "✅ enabled" if account.weekly_auto_kick else "❌ disabled"
            session_state = "✅ enabled" if account.session_auto_kick else "❌ disabled"
        visible = "visible" if account.visible else "hidden"
        table.add_row(account.label, account.provider, visible, state, weekly_state, session_state)

    console.print(table)


def _render_accounts_table(config: Config, accounts: list[AccountConfig]) -> None:
    table = Table(title="TokenKick Accounts", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Visible")
    table.add_column("Notifications")
    table.add_column("Auto-kick")
    table.add_column("Kick model")
    table.add_column("Status probe")

    for account in accounts:
        visible = "✅ visible" if account.visible else "❌ hidden"
        notifications = _notification_route_display(account, config.notifications)
        if _is_monitor_only_provider(account.provider):
            auto_kick = "❌ monitor-only"
        elif account.provider not in KICKABLE_PROVIDERS:
            auto_kick = "❌ disabled (not kickable)"
        elif account.auto_kick:
            auto_kick = "✅ enabled"
        else:
            auto_kick = "❌ disabled"
        kick_model = _kick_model_display(account)
        probe = "⚠ enabled" if account.status_probe_enabled else "—"
        table.add_row(
            account.label,
            account.provider,
            visible,
            notifications,
            auto_kick,
            kick_model,
            probe,
        )

    console.print(table)


def _render_account_notifications_table(config: Config, accounts: list[AccountConfig]) -> None:
    table = Table(title="TokenKick Account Notifications", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Account notifications")
    table.add_column("Destination")

    destination = _notification_destination_display(config.notifications)
    for account in accounts:
        account_state = _notification_route_display(account, config.notifications)
        table.add_row(account.label, account.provider, account_state, destination)

    console.print(table)


def _render_account_planning_table(config: Config, accounts: list[AccountConfig]) -> None:
    table = Table(title="TokenKick Account Planning", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Visible")
    table.add_column("Auto/session")
    table.add_column("Usage")
    table.add_column("Orchestration role")
    table.add_column("Effective role")
    table.add_column("Weekly reserve")

    statuses_by_key: dict[str, AccountStatus] = {}
    cached = _load_status_cache(config)
    if cached is not None:
        cached_accounts, statuses, _entries = cached
        statuses_by_key = {
            account_key_string(account): status
            for account, status in zip(cached_accounts, statuses, strict=False)
        }

    for account in accounts:
        key = account_key_string(account)
        status = statuses_by_key.get(key)
        visible = "✅ visible" if account.visible else "❌ hidden"
        auto_session = _account_auto_session_display(account)
        usage_minutes = usable_session_minutes_for_account(account, config)
        usage = f"{usage_minutes}m"
        role = _display_orchestration_role(account.orchestration_role)
        effective = _display_orchestration_role(effective_orchestration_role(account, status))
        threshold = (
            f"{account.weekly_reserve_threshold_percent}%"
            if account.weekly_reserve_threshold_percent is not None
            else "—"
        )
        table.add_row(
            account.label,
            account.provider,
            visible,
            auto_session,
            usage,
            role,
            effective,
            threshold,
        )

    console.print(table)


def _account_auto_session_display(account: AccountConfig) -> str:
    if account.provider not in KICKABLE_PROVIDERS:
        return "monitor-only"
    auto = "✅" if account.auto_kick else "❌"
    session = "✅" if account.session_auto_kick else "❌"
    return f"auto {auto} / session {session}"


def _display_orchestration_role(role: str) -> str:
    labels = {
        "use_first": "Use first",
        "normal": "Normal",
        "backup": "Backup",
        "specialist": "Specialist",
        "excluded": "Excluded",
    }
    return labels.get(role, role)


def _notification_destination_display(notifications: NotifyConfig) -> str:
    if not notifications.enabled:
        return "global disabled"
    parts = []
    for backend in _configured_notification_backends(notifications):
        if backend == "ntfy":
            parts.append(f"ntfy:{notifications.ntfy_topic or 'missing topic'}")
        elif backend == "telegram":
            chat_id = notifications.telegram_chat_id or "missing chat ID"
            parts.append(f"telegram:{chat_id}")
    return ", ".join(parts) if parts else "no enabled destinations"


def _account_by_label_or_exit(label: str, accounts: list[AccountConfig]) -> AccountConfig:
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        raise click.exceptions.Exit(1)
    return account


def _account_detail_payload(account: AccountConfig) -> dict:
    payload = account.to_dict()
    payload["notifications_enabled"] = account.notifications_enabled
    payload["kickable"] = account.provider in KICKABLE_PROVIDERS
    payload["monitor_only"] = _is_monitor_only_provider(account.provider)
    payload["kick_model_effective"] = None if not payload["kickable"] else kick_model_for_account(account)
    return payload


def _render_account_detail(account: AccountConfig) -> None:
    payload = _account_detail_payload(account)
    table = Table(title=f"TokenKick Account — {account.label}", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    fields = [
        ("Label", account.label),
        ("Provider", account.provider),
        ("Source", account.source.value),
        ("Visible", "yes" if account.visible else "no"),
        ("Kickable", "yes" if payload["kickable"] else "no"),
        ("Auto-kick", "yes" if account.auto_kick else "no"),
        ("Weekly auto-kick", "yes" if account.weekly_auto_kick else "no"),
        ("Session auto-kick", "yes" if account.session_auto_kick else "no"),
        ("Orchestration role", _display_orchestration_role(account.orchestration_role)),
        (
            "Weekly reserve threshold",
            f"{account.weekly_reserve_threshold_percent}%"
            if account.weekly_reserve_threshold_percent is not None
            else "—",
        ),
        ("Kick model", _kick_model_display(account)),
        ("Status probe", "enabled" if account.status_probe_enabled else "disabled"),
        ("Direct usage", "enabled" if account.direct_usage_enabled else "disabled"),
        ("Codex quota bucket", account.codex_rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID),
        ("Codex quota name", account.codex_rate_limit_name or "—"),
        ("Provider home", account.provider_home or "—"),
        ("Session path", account.session_path or "—"),
        ("CodexBar provider", account.codexbar_provider or "—"),
        ("CodexBar account", account.codexbar_account or "—"),
        ("CodexBar URL", account.codexbar_url or "—"),
        ("Identity email", account.identity_email or "—"),
        ("Identity provider id", account.identity_provider_id or "—"),
        ("Identity org id", account.identity_org_id or "—"),
    ]
    for field, value in fields:
        table.add_row(field, str(value))
    console.print(table)


def _kick_model_display(account: AccountConfig) -> str:
    if _is_monitor_only_provider(account.provider):
        return "—"
    if account.provider not in KICKABLE_PROVIDERS:
        return "—"
    model = kick_model_for_account(account)
    if not model:
        return "default"
    if account.kick_model:
        return model
    return f"{model} (default)"


def _app_json_requested(as_json: bool) -> bool:
    """JSON output is explicit via --json-output and implicit under TK_APP_MODE=1."""
    return as_json or app_mode_enabled()


@contextmanager
def _console_redirected_to_stderr():
    """Keep stdout JSON-only while helpers print human progress text."""
    global console
    previous_console = console
    if not app_mode_enabled():
        console = Console(width=120, stderr=True)
    try:
        yield
    finally:
        console = previous_console


def _run_mutation_json(
    mutator: Callable[[], object | None],
    payload_builder: Callable[[object], dict],
) -> None:
    """Run a mutation helper, capture its console messages, and emit an app envelope.

    Mutation helpers return the updated object on success and ``None`` when the
    mutation was refused; their explanation goes through the module console,
    which is captured here so the envelope message matches the CLI wording.
    """
    global console
    previous_console = console
    buffer = io.StringIO()
    console = Console(width=120, file=buffer)
    exit_code: int | None = None
    consent_required: AutoKickConsentRequired | None = None
    updated: object | None = None
    try:
        updated = mutator()
    except AutoKickConsentRequired as exc:
        consent_required = exc
    except click.exceptions.Exit as exc:
        exit_code = exc.exit_code
    finally:
        console = previous_console
    message = buffer.getvalue().strip() or None
    if consent_required is not None:
        emit_app_error(
            AUTO_KICK_CONSENT_ERROR,
            consent_required.message,
            payload=consent_required.payload,
        )
        sys.exit(1)
    if exit_code is not None and exit_code != 0:
        emit_app_error("mutation_rejected", message or "Mutation rejected.")
        sys.exit(exit_code)
    if updated is None:
        emit_app_error("mutation_failed", message or "Mutation failed.")
        sys.exit(1)
    emit_app_success(payload_builder(updated), message=message)


def _account_mutation_json(mutator: Callable[[], AccountConfig | None]) -> None:
    _run_mutation_json(mutator, lambda account: {"account": _account_detail_payload(account)})


def _accounts_list_payload(config: Config, accounts: list[AccountConfig]) -> list[dict]:
    return [
        {
            "label": account.label,
            "provider": account.provider,
            "visible": account.visible,
            "kickable": account.provider in KICKABLE_PROVIDERS,
            "monitor_only": _is_monitor_only_provider(account.provider),
            "auto_kick": account.auto_kick,
            "weekly_auto_kick": account.weekly_auto_kick,
            "session_auto_kick": account.session_auto_kick,
            "notifications_enabled": account.notifications_enabled,
            "notifications_route": _notification_route_display(account, config.notifications),
            "kick_model": _kick_model_display(account),
            "status_probe_enabled": account.status_probe_enabled,
            "direct_usage_enabled": account.direct_usage_enabled,
        }
        for account in accounts
    ]


def _account_notifications_payload(config: Config, accounts: list[AccountConfig]) -> dict:
    return {
        "global_enabled": config.notifications.enabled,
        "destination": _notification_destination_display(config.notifications),
        "backends": _configured_notification_backends(config.notifications),
        "accounts": [
            {
                "label": account.label,
                "provider": account.provider,
                "notifications_enabled": account.notifications_enabled,
                "backends": account.notification_backends,
                "route": _notification_route_display(account, config.notifications),
            }
            for account in accounts
        ],
    }


def _account_planning_payload(config: Config, accounts: list[AccountConfig]) -> list[dict]:
    statuses_by_key: dict[str, AccountStatus] = {}
    cached = _load_status_cache(config)
    if cached is not None:
        cached_accounts, statuses, _entries = cached
        statuses_by_key = {
            account_key_string(account): status
            for account, status in zip(cached_accounts, statuses, strict=False)
        }
    return [
        {
            "label": account.label,
            "provider": account.provider,
            "visible": account.visible,
            "auto_kick": account.auto_kick,
            "session_auto_kick": account.session_auto_kick,
            "usable_session_minutes": usable_session_minutes_for_account(account, config),
            "orchestration_role": account.orchestration_role,
            "effective_orchestration_role": effective_orchestration_role(
                account,
                statuses_by_key.get(account_key_string(account)),
            ),
            "weekly_reserve_threshold_percent": account.weekly_reserve_threshold_percent,
        }
        for account in accounts
    ]


def _auto_status_payload(accounts: list[AccountConfig]) -> list[dict]:
    return [
        {
            "label": account.label,
            "provider": account.provider,
            "visible": account.visible,
            "kickable": account.provider in KICKABLE_PROVIDERS,
            "monitor_only": _is_monitor_only_provider(account.provider),
            "auto_kick": account.auto_kick,
            "weekly_auto_kick": account.weekly_auto_kick,
            "session_auto_kick": account.session_auto_kick,
        }
        for account in accounts
    ]


def _pending_kicks_payload(
    pending: dict[str, PendingKick],
    *,
    account_label: str | None = None,
) -> list[dict]:
    items = [
        (key, item)
        for key, item in pending.items()
        if account_label is None or item.account_label == account_label
    ]
    items.sort(key=lambda pair: (pair[1].kick_at, pair[1].account_label, pair[1].purpose))
    return [{"key": key, **item.to_dict()} for key, item in items]


def _schedule_show_payload(
    config: Config,
    pending: dict[str, PendingKick],
    account_label: str | None = None,
) -> dict:
    schedule_config = config.schedule
    payload = {
        "enabled": schedule_config.enabled,
        "timezone": schedule_config.timezone,
        "scheduling_target": schedule_config.scheduling_target,
        "default": schedule_config.default.to_dict(),
        "accounts": {
            label: schedule_value.to_dict()
            for label, schedule_value in sorted(schedule_config.accounts.items())
        },
        "pending_kicks": _pending_kicks_payload(pending, account_label=account_label),
    }
    if account_label is not None:
        payload["accounts"] = {
            label: value
            for label, value in payload["accounts"].items()
            if label == account_label
        }
    return payload


def _version_option_callback(ctx, _param, value):
    if not value or ctx.resilient_parsing:
        return None
    click.echo(f"tk, version {installed_version()}")
    ctx.exit()


@click.group(cls=TokenKickGroup, invoke_without_command=True)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_version_option_callback,
    help="Show the installed TokenKick version and exit.",
)
@click.pass_context
def cli(ctx):
    """TokenKick — Reset? Go.

    Track your AI coding quota windows and kick them the moment they reset.
    """
    if ctx.invoked_subcommand is None:
        if _should_open_interactive_menu():
            _open_interactive_menu(ctx)
        else:
            ctx.invoke(status)


def _should_open_interactive_menu() -> bool:
    if app_mode_enabled():
        return False
    if os.environ.get("TK_NO_INTERACTIVE"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _open_interactive_menu(ctx: click.Context) -> None:
    from .interactive import run_command_center

    run_command_center(ctx, first_run_setup=True)


@cli.command("menu")
@click.pass_context
def menu_cmd(ctx):
    """Open the basic interactive TokenKick helper."""
    from .interactive import run_command_center

    run_command_center(ctx, first_run_setup=False)


# ---------------------------------------------------------------------------
# tk mcp
# ---------------------------------------------------------------------------

@cli.group("mcp")
def mcp_group():
    """Configure or run TokenKick's local MCP server."""


@mcp_group.command("serve")
def mcp_serve_cmd():
    """Start TokenKick's stdio MCP server."""
    from .mcp_server import main as mcp_main

    mcp_main()


def _mcp_manager() -> MCPSetupManager:
    return MCPSetupManager()


def _mcp_json_rejection(operation: str, *, client: str | None, use_helper: bool) -> dict:
    return {
        "schema_version": 1,
        "ok": False,
        "read_only": True,
        "operation": operation,
        "client": client or "all",
        "use_helper": use_helper,
        "message": f"`tk mcp {operation}` requires --yes before it writes client config.",
    }


def _render_mcp_status(payload: dict) -> None:
    _print_mcp_heading(" Agent Tools (MCP)")
    table = Table(show_header=True)
    table.add_column("Client")
    table.add_column("State")
    table.add_column("Config")
    table.add_column("Action")
    for client in payload.get("clients", []):
        table.add_row(
            str(client.get("client_display") or client.get("client")),
            _mcp_state_display(str(client.get("state") or "unknown")),
            str(client.get("config_path") or client.get("config_method") or "—"),
            str(client.get("recommended_action") or client.get("message") or "—"),
        )
    console.print(table)
    helper = payload.get("helper") or {}
    console.print(
        "[dim]Canonical server: "
        f"{payload.get('canonical_command', {}).get('command')} mcp serve[/dim]"
    )
    if helper:
        console.print(
            "[dim]Stable helper: "
            f"{helper.get('path')} "
            f"({'needs repair' if helper.get('needs_repair') else 'ready'})[/dim]"
        )


def _render_mcp_result(payload: dict) -> None:
    _print_mcp_heading(f" MCP {str(payload.get('operation', 'status')).title()}")
    table = Table(show_header=True)
    table.add_column("Client")
    table.add_column("Changed")
    table.add_column("State")
    table.add_column("Backup")
    table.add_column("Message")
    for client in payload.get("clients", []):
        table.add_row(
            str(client.get("client")),
            "yes" if client.get("changed") else "no",
            str(client.get("state") or client.get("status", {}).get("state") or "—"),
            str(client.get("backup_path") or "—"),
            str(client.get("message") or "—"),
        )
    console.print(table)


def _print_mcp_heading(suffix: str) -> None:
    from rich.align import Align

    title = Text()
    title.append("Token", style="bold white")
    title.append("Kick", style="bold green")
    title.append(suffix, style="bold white")
    console.print(Align.center(title))


def _mcp_state_display(state: str) -> str:
    mapping = {
        "configured": "[green]✅ configured[/green]",
        "missing": "[red]❌ missing[/red]",
        "unsupported": "[dim]⚪ unsupported[/dim]",
        "needs_repair": "[yellow]⚠ needs repair[/yellow]",
        "malformed": "[yellow]⚠ malformed[/yellow]",
        "unknown": "[yellow]? unknown[/yellow]",
        "skipped": "[dim]· skipped[/dim]",
        "failed": "[red]❌ failed[/red]",
        "removed": "[green]✅ removed[/green]",
    }
    return mapping.get(state, state)


@mcp_group.command("status")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_status_cmd(client: str, as_json: bool):
    """Show MCP client setup status."""
    payload = _mcp_manager().status(client=client)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    _render_mcp_status(payload)


@mcp_group.command("doctor")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_doctor_cmd(as_json: bool):
    """Diagnose MCP client setup without mutating anything."""
    payload = _mcp_manager().doctor()
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    _render_mcp_status(payload)
    for check in payload.get("checks", []):
        level = str(check.get("level") or "INFO")
        color = "yellow" if level == "WARN" else "cyan"
        console.print(
            f"[{color}]{level}[/{color}] {check.get('client')}: "
            f"{check.get('message')}"
        )


@mcp_group.command("config-snippet")
@click.option("--client", type=click.Choice(["codex", "claude-desktop", "claude-code"]), required=True)
@click.option("--use-helper", is_flag=True, help="Use the stable TokenKick helper path")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_config_snippet_cmd(client: str, use_helper: bool, as_json: bool):
    """Print a manual MCP config snippet without writing files."""
    payload = _mcp_manager().config_snippet(client=client, use_helper=use_helper)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    console.print(f"[bold]{client} TokenKick MCP snippet[/bold]")
    console.print(str(payload["snippet"]), markup=False)


def _mcp_mutation(
    operation: str,
    *,
    client: str,
    yes: bool,
    as_json: bool,
    use_helper: bool = False,
) -> None:
    manager = _mcp_manager()
    if not yes:
        payload = _mcp_json_rejection(operation, client=client, use_helper=use_helper)
        if as_json:
            click.echo(json.dumps(payload, indent=2))
            sys.exit(2)
        raise click.ClickException(payload["message"])
    try:
        if operation == "install":
            payload = manager.install(client=client, use_helper=use_helper, repair_only=False)
        elif operation == "repair":
            payload = manager.install(client=client, use_helper=use_helper, repair_only=True)
        elif operation == "remove":
            payload = manager.remove(client=client)
        else:  # pragma: no cover - defensive
            raise click.ClickException(f"Unknown MCP operation: {operation}")
    except MCPSetupError as exc:
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "schema_version": 1,
                        "ok": False,
                        "operation": operation,
                        "error": str(exc),
                    },
                    indent=2,
                )
            )
            sys.exit(1)
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps({"ok": True, **payload}, indent=2))
        return
    _render_mcp_result(payload)


@mcp_group.command("install")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--use-helper", is_flag=True, help="Use the stable TokenKick helper path")
@click.option("--yes", is_flag=True, help="Confirm writing MCP client config")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_install_cmd(client: str, use_helper: bool, yes: bool, as_json: bool):
    """Install TokenKick MCP config into supported clients."""
    _mcp_mutation("install", client=client, use_helper=use_helper, yes=yes, as_json=as_json)


@mcp_group.command("repair")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--use-helper", is_flag=True, help="Use the stable TokenKick helper path")
@click.option("--yes", is_flag=True, help="Confirm writing MCP client config")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_repair_cmd(client: str, use_helper: bool, yes: bool, as_json: bool):
    """Repair TokenKick MCP config for supported clients."""
    _mcp_mutation("repair", client=client, use_helper=use_helper, yes=yes, as_json=as_json)


@mcp_group.command("remove")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--yes", is_flag=True, help="Confirm removing TokenKick MCP client config")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def mcp_remove_cmd(client: str, yes: bool, as_json: bool):
    """Remove TokenKick MCP config from supported clients."""
    _mcp_mutation("remove", client=client, yes=yes, as_json=as_json)


# ---------------------------------------------------------------------------
# tk status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--codex", "codex_only", is_flag=True, help="Show only Codex provider accounts")
@click.option("--all", "show_all", is_flag=True, help="Show hidden accounts too")
@click.option("--account", "account_label", metavar="LABEL", help="Show one account")
@click.option("--refresh", is_flag=True, help="Fetch live provider status instead of daemon cache")
@click.option("--verbose", is_flag=True, help="Show scheduling basis and diagnostic annotations")
def status(
    as_json: bool,
    codex_only: bool,
    show_all: bool,
    account_label: str | None,
    refresh: bool,
    verbose: bool,
):
    """Show all accounts and recommended actions."""
    config = Config.load()
    live_refresh_context = claude_cli_usage_refresh_allowed() if refresh else nullcontext()
    with live_refresh_context:
        config = _migrate_v04_direct_sources_if_needed(config, recheck_skipped=refresh)
        config = _migrate_provider_first_labels_if_needed(config)
        config = _migrate_codex_home_keys_if_needed(config)
        config = _repair_codex_home_identity_drift_if_needed(config)
        live_pairs = None
        if refresh:
            live_pairs = _refresh_status_cache_fast(config)
    cache_entries: dict[str, dict] = {}
    background_refresh_started = False
    background_refresh_message: str | None = None
    global_refresh_error: str | None = None
    cached = None if refresh else _load_status_cache(config)
    used_cache = cached is not None
    if not refresh and cached is None and STATUS_CACHE_FILE.exists() and _read_status_cache_data() is None:
        global_refresh_error = "Status cache is unreadable."
    if cached is None and not refresh:
        config = _migrate_v04_direct_sources_if_needed(config, recheck_skipped=True)
        config = _migrate_codex_home_keys_if_needed(config)
        config = _repair_codex_home_identity_drift_if_needed(config)
        cached = _load_status_cache(config)
        used_cache = cached is not None
    if cached is not None:
        accounts, statuses, cache_entries = cached
        if _status_cache_needs_refresh(cache_entries, config):
            _clear_status_refresh_lock_acquire_error()
            background_refresh_started = _start_background_status_refresh()
            if not background_refresh_started and not _status_refresh_lock_active():
                global_refresh_error = (
                    _status_refresh_lock_acquire_error()
                    or "Background refresh could not start."
                )
        background_refresh_message = _background_status_refresh_message(
            started=background_refresh_started
        )
        discovered = False
        summary = "Loaded cached daemon status."
        new_accounts: list[AccountConfig] = []
    else:
        if live_pairs is None:
            accounts, statuses, discovered, summary, new_accounts = _load_account_status_pairs(config)
            _save_status_cache(
                accounts,
                _cache_statuses_by_key_from_pairs(accounts, statuses),
                _failures_by_key_from_status_pairs(accounts, statuses),
            )
        elif len(live_pairs) == 6:
            (
                accounts,
                statuses,
                discovered,
                summary,
                new_accounts,
                background_refresh_started,
            ) = live_pairs
            background_refresh_message = _background_status_refresh_message(
                started=background_refresh_started
            )
        else:
            accounts, statuses, discovered, summary, new_accounts = live_pairs
        refreshed_cache = _load_status_cache(replace(config, accounts=accounts))
        if refreshed_cache is not None:
            accounts, statuses, cache_entries = refreshed_cache
    metadata_accounts = list(accounts)
    metadata_statuses = list(statuses)
    if not used_cache:
        _apply_codex_late_attribution(
            metadata_accounts,
            _cache_statuses_by_key_from_pairs(metadata_accounts, metadata_statuses),
        )
        _observe_phantom_session_states(metadata_accounts, metadata_statuses)
    accounts, statuses = _filter_status_pairs_by_visibility(
        accounts,
        statuses,
        show_all or bool(account_label),
    )
    if cache_entries:
        visible_keys = {account_key_string(account) for account in accounts}
        cache_entries = {key: entry for key, entry in cache_entries.items() if key in visible_keys}
    accounts, statuses = _filter_status_pairs_by_provider(
        accounts,
        statuses,
        "codex" if codex_only else None,
    )
    if cache_entries:
        provider_keys = {account_key_string(account) for account in accounts}
        cache_entries = {key: entry for key, entry in cache_entries.items() if key in provider_keys}
    if codex_only:
        new_accounts = [account for account in new_accounts if account.provider == "codex"]
    if account_label:
        accounts, statuses = _filter_status_pairs_by_account_label(
            accounts,
            statuses,
            account_label,
        )
        if cache_entries:
            account_keys = {account_key_string(account) for account in accounts}
            cache_entries = {key: entry for key, entry in cache_entries.items() if key in account_keys}
        new_accounts = [account for account in new_accounts if account.label in {a.label for a in accounts}]

    if as_json:
        click.echo(
            json.dumps(
                _status_json_payload(
                    accounts=accounts,
                    statuses=statuses,
                    metadata_accounts=metadata_accounts,
                    metadata_statuses=metadata_statuses,
                    cached=used_cache,
                    refresh_error=global_refresh_error,
                    config=config,
                    cache_entries=cache_entries,
                ),
                indent=2,
            )
        )
        return

    if not statuses:
        if account_label:
            console.print(f'[dim]No status for "{account_label}".[/dim]')
        elif config.accounts and not show_all:
            console.print(
                "[dim]No visible accounts. Run [bold]tk accounts list[/bold] "
                "or [bold]tk status --all[/bold].[/dim]"
            )
        else:
            console.print(f"[dim]{summary} Run [bold]tk setup[/bold] after logging in.[/dim]")
        return

    _render_recent_reset_banner()
    _render_status_table(statuses, accounts, config, cache_entries, verbose=verbose)
    _render_reservation_advisories_for_pairs(accounts, statuses, config)
    if verbose and _codex_burst_ladder_enabled(config):
        order = ", ".join(_codex_burst_ladder_surface_order(config))
        console.print(
            "[yellow]Codex surface strategy: burst ladder enabled "
            f"(order {order}; gap {_codex_burst_ladder_gap_seconds(config):.0f}s).[/yellow]"
        )
    _print_report_timestamp("Status printed at")
    if cache_entries:
        console.print(f"[dim]{_format_status_cache_footer(cache_entries, config)}[/dim]")
        if background_refresh_message:
            console.print(f"[dim]{background_refresh_message}[/dim]")
        if global_refresh_error:
            console.print(f"[yellow]{global_refresh_error}[/yellow]")
        if new_accounts:
            console.print(f"[dim]{_format_new_account_note(new_accounts)}[/dim]")
    elif discovered:
        console.print(f"[dim]{_setup_footer(config)}[/dim]")
        if background_refresh_message:
            console.print(f"[dim]{background_refresh_message}[/dim]")
    elif new_accounts:
        console.print(f"[dim]{_format_new_account_note(new_accounts)}[/dim]")


def _filter_status_pairs_by_account_label(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    account_label: str,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    labels = _history_filter_labels(account_label, accounts)
    filtered_accounts = []
    filtered_statuses = []
    for account, status in zip(accounts, statuses):
        if account.label in labels or status.label in labels:
            filtered_accounts.append(account)
            filtered_statuses.append(status)
    return filtered_accounts, filtered_statuses


def _render_recent_reset_banner() -> None:
    events = recent_reset_events(hours=24, unacknowledged=True)
    if not events:
        return
    event = events[-1]
    if is_provider_reset_observation(event):
        console.print(
            f"[yellow]Provider reset observation on {event.provider.title()} "
            f"{format_event_age(event)}. Run [bold]tk reset-log --detail {event.id}[/bold] "
            f"for details.[/yellow]\n"
        )
        return
    if event.confidence == "possible":
        console.print(
            f"[dim]Possible global reset on {event.provider.title()} "
            f"{format_event_age(event)}. Run [bold]tk reset-log[/bold] for details.[/dim]\n"
        )
    else:
        console.print(
            f"[yellow]⚠ Global reset detected on {event.provider.title()} "
            f"{format_event_age(event)}. Run [bold]tk reset-log[/bold] for details.[/yellow]\n"
        )


# ---------------------------------------------------------------------------
# tk calendar
# ---------------------------------------------------------------------------

def _calendar_cache_warning(cache_entries: dict[str, dict], config: Config) -> str | None:
    if not cache_entries:
        return None
    freshness = _status_cache_freshness(cache_entries, config)
    if freshness["stale"] == 0:
        return None
    oldest = freshness["oldest_stale_at"]
    if oldest is None:
        return "Status cache is stale. Run tk status --refresh for current predictions."
    age_text = _format_status_cache_age(
        int((_status_cache_now() - oldest).total_seconds())
    )
    return f"Status cache is {age_text} old. Run tk status --refresh for current predictions."


def _calendar_missing_accounts(
    config: Config,
    cache_entries: dict[str, dict],
) -> list[AccountConfig]:
    if not config.accounts:
        return []
    cached_keys = set(cache_entries)
    return [
        account
        for account in config.accounts
        if account_key_string(account) not in cached_keys
    ]


def _format_calendar_day(event: CalendarEvent, tz) -> str:
    return event.predicted_at.astimezone(tz).strftime("%a %b %d")


def _format_calendar_time(event: CalendarEvent, tz) -> str:
    return event.predicted_at.astimezone(tz).strftime("%H:%M %Z")


def _render_calendar(
    events: list[CalendarEvent],
    *,
    tz,
    warnings: list[str],
) -> None:
    if not events:
        console.print("[dim]No predicted resets in the selected window.[/dim]")
        for warning in warnings:
            console.print(f"[yellow]{warning}[/yellow]")
        return

    table = Table(title="TokenKick — Reset Calendar", show_header=True)
    table.add_column("Day", no_wrap=True)
    table.add_column("Time", no_wrap=True)
    table.add_column("Account", style="bold", no_wrap=True, min_width=24)
    table.add_column("Event")

    previous_day = None
    for event in events:
        day = _format_calendar_day(event, tz)
        table.add_row(
            "" if day == previous_day else day,
            _format_calendar_time(event, tz),
            event.account,
            format_event_description(event),
        )
        previous_day = day
    console.print(table)

    scheduled = [event for event in events if event.optimal_kick_at is not None]
    if scheduled:
        schedule_table = Table(title="Smart Schedule predictions", show_header=True)
        schedule_table.add_column("Day", no_wrap=True)
        schedule_table.add_column("Time", no_wrap=True)
        schedule_table.add_column("Account", style="bold", no_wrap=True, min_width=24)
        schedule_table.add_column("Event")
        previous_day = None
        for event in sorted(scheduled, key=lambda item: item.optimal_kick_at or item.predicted_at):
            kick_event = CalendarEvent(
                account=event.account,
                provider=event.provider,
                type="optimal_kick",
                predicted_at=event.optimal_kick_at or event.predicted_at,
                confidence=event.confidence,
                source=event.source,
            )
            day = _format_calendar_day(kick_event, tz)
            schedule = event.schedule or "configured schedule"
            description = (
                "optimal kick (immediate at reset)"
                if event.immediate_kick_best
                else f"optimal kick (for {schedule})"
            )
            schedule_table.add_row(
                "" if day == previous_day else day,
                _format_calendar_time(kick_event, tz),
                event.account,
                description,
            )
            previous_day = day
        console.print()
        console.print(schedule_table)

    for warning in warnings:
        console.print(f"\n[yellow]{warning}[/yellow]")


@cli.command("calendar")
@click.option("--days", default=7, show_default=True, type=click.IntRange(1, 366), help="Days ahead to show")
@click.option("--account", "account_label", metavar="LABEL", help="Only show one account")
@click.option("--codex", "codex_only", is_flag=True, help="Show only Codex provider accounts")
@click.option("--all", "show_all", is_flag=True, help="Show hidden accounts too")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--ics", "as_ics", is_flag=True, help="Output iCalendar data")
def calendar_cmd(
    days: int,
    account_label: str | None,
    codex_only: bool,
    show_all: bool,
    as_json: bool,
    as_ics: bool,
):
    """Show predicted reset events from the local status cache."""
    if as_json and as_ics:
        raise click.ClickException("--json-output and --ics cannot be used together")

    config = Config.load()
    entries = _load_status_cache_entries()
    loaded = _load_status_cache_for_accounts(config, entries, require_all=False) if entries else None
    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    cache_entries: dict[str, dict] = {}
    if loaded is not None:
        accounts, statuses, cache_entries = loaded

    now = _status_cache_now()
    tz = local_timezone(config.schedule)
    warnings: list[str] = []
    if STATUS_CACHE_FILE.exists() and _read_status_cache_data() is None:
        warnings.append("Status cache is unreadable. Run tk status --refresh for current predictions.")
    elif not entries:
        warnings.append("No status cache found. Run tk status --refresh for current predictions.")
    stale_warning = _calendar_cache_warning(cache_entries, config)
    if stale_warning:
        warnings.append(stale_warning)

    result = build_reset_calendar(
        config=config,
        accounts=accounts,
        statuses=statuses,
        cache_entries=cache_entries,
        now=now,
        tz=tz,
        days_ahead=days,
        account_label=account_label,
        provider="codex" if codex_only else None,
        show_all=show_all,
        missing_accounts=_calendar_missing_accounts(config, cache_entries),
    )
    warnings.extend(result.warnings)

    if as_json:
        pending_kicks = list(load_pending_kicks(now).values())
        click.echo(
            json.dumps(
                calendar_json_payload(
                    generated_at=now,
                    tz=tz,
                    days_ahead=days,
                    events=result.events,
                    warnings=warnings,
                    pending_kicks=pending_kicks,
                ),
                indent=2,
            )
        )
        return

    if as_ics:
        click.echo(render_ics(result.events, tz), nl=False)
        return

    _render_calendar(result.events, tz=tz, warnings=warnings)
    _print_report_timestamp("Calendar generated at")


# ---------------------------------------------------------------------------
# tk plan
# ---------------------------------------------------------------------------

@cli.group("plan", invoke_without_command=True)
@click.option("--work-window", metavar="HH:MM-HH:MM", help="Work window to cover")
@click.option("--date", "date_text", metavar="YYYY-MM-DD", help="Date for the work-window start")
@click.option("--timezone", "timezone_text", metavar="TZ", help="IANA timezone for the work window")
@click.option(
    "--usage",
    "usage_overrides",
    multiple=True,
    metavar="LABEL=DURATION",
    help="Per-plan expected usage, e.g. 'codex (home)=3h' or 'claude=90m'",
)
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--apply", "apply_plan", is_flag=True, help="Write orchestrated pending session kicks")
@click.option("--yes", is_flag=True, help="Confirm --apply in non-interactive mode")
@click.pass_context
def plan_cmd(
    ctx: click.Context,
    work_window: str,
    date_text: str | None,
    timezone_text: str | None,
    usage_overrides: tuple[str, ...],
    as_json: bool,
    apply_plan: bool,
    yes: bool,
):
    """Plan multi-account session coverage from cached state."""
    if ctx.invoked_subcommand is not None:
        return
    if not work_window:
        raise click.UsageError("Missing option '--work-window'.")
    plan, now = _build_plan_from_options(
        work_window=work_window,
        date_text=date_text,
        timezone_text=timezone_text,
        usage_overrides=usage_overrides,
    )

    if apply_plan:
        if not yes and (os.environ.get("TK_NO_INTERACTIVE") or as_json):
            plan.message = "not applied; --apply requires --yes for JSON or non-interactive mode"
            _emit_plan(plan, as_json=as_json)
            ctx.exit(1)
        if not yes:
            _render_plan(plan)
            if not _confirm_prompt("Apply this orchestration plan?", default=False):
                console.print("\n[bold]Read-only:[/bold] not applied; user declined apply")
                return
        plan = apply_orchestration_plan(
            plan,
            now=now,
            current_time=_status_cache_now().astimezone(timezone.utc),
        )

    _emit_plan(plan, as_json=as_json)


@plan_cmd.command("cancel")
@click.option("--account", "account_labels", multiple=True, metavar="LABEL", help="Cancel one account's orchestration pending kick")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--yes", is_flag=True, help="Confirm cancellation")
@click.pass_context
def plan_cancel(ctx: click.Context, account_labels: tuple[str, ...], as_json: bool, yes: bool):
    """Cancel applied orchestration pending kicks."""
    requested = set(account_labels) if account_labels else None
    now = datetime.now(timezone.utc)
    pending = _orchestrated_pending_kicks(account_labels=requested, now=now)
    if not pending:
        payload = _plan_cancel_payload(
            read_only=True,
            applied=False,
            message="no applied orchestration pending kicks found",
            result=CancelPendingKicksResult(
                removed=[],
                kept_count=len(load_pending_kicks(now)),
                unmatched_account_labels=sorted(requested or set()),
            ),
        )
        if as_json:
            click.echo(json.dumps(payload, indent=2))
        else:
            console.print("[dim]No applied orchestration pending kicks found.[/dim]")
        return

    if as_json and not yes:
        payload = _plan_cancel_payload(
            read_only=True,
            applied=False,
            message="not cancelled; --json-output requires --yes to mutate",
            result=CancelPendingKicksResult(
                removed=[],
                kept_count=len(load_pending_kicks(now)),
                unmatched_account_labels=[],
            ),
            matching=pending,
        )
        click.echo(json.dumps(payload, indent=2))
        ctx.exit(1)

    if not yes:
        _render_orchestrated_pending_kicks(pending, title="Applied orchestration pending kicks")
        if not _confirm_prompt(f"Cancel {len(pending)} orchestration pending kick(s)?", default=False):
            console.print("\n[bold]Read-only:[/bold] not cancelled; user declined cancellation")
            return

    result = cancel_orchestrated_pending_kicks(account_labels=requested, now=now)
    message = f"cancelled {len(result.removed)} orchestration pending kick(s)"
    if as_json:
        click.echo(
            json.dumps(
                _plan_cancel_payload(
                    read_only=False,
                    applied=True,
                    message=message,
                    result=result,
                ),
                indent=2,
            )
        )
    else:
        console.print(f"[green]{message}.[/green]")
        if result.unmatched_account_labels:
            console.print(
                "[yellow]No matching orchestration pending kick for: "
                + ", ".join(result.unmatched_account_labels)
                + ".[/yellow]"
            )


def _build_plan_from_options(
    *,
    work_window: str,
    date_text: str | None,
    timezone_text: str | None,
    usage_overrides: Sequence[str] | None = None,
) -> tuple[OrchestrationPlan, datetime]:
    config = Config.load()
    config = _migrate_provider_first_labels_if_needed(config)
    config = _migrate_codex_home_keys_if_needed(config)
    config = _repair_codex_home_identity_drift_if_needed(config)
    usage_overrides_by_key = _parse_plan_usage_overrides(config.accounts, usage_overrides or ())

    tz = _plan_timezone(config, timezone_text)
    try:
        day = (
            datetime.strptime(date_text, "%Y-%m-%d").date()
            if date_text
            else _status_cache_now().astimezone(tz).date()
        )
        work_start, work_end = parse_work_window(work_window, day, tz)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    now = _status_cache_now().astimezone(timezone.utc)
    if work_end <= now:
        ended_local = work_end.astimezone(tz).strftime("%H:%M %Z")
        raise click.ClickException(
            f"Work window {work_window} on {day.isoformat()} already ended at "
            f"{ended_local}. Choose a later window or pass --date for a future day."
        )
    entries = _load_status_cache_entries()
    loaded = _load_status_cache_for_accounts(config, entries, require_all=False) if entries else None
    status_by_key: dict[str, AccountStatus] = {}
    if loaded is not None:
        accounts, statuses, _cache_entries = loaded
        status_by_key = {
            account_key_string(account): status
            for account, status in zip(accounts, statuses, strict=False)
        }
    inputs = [
        AccountPlanInput(
            account=account,
            status=status_by_key.get(account_key_string(account)),
            cache_stale=(
                _status_cache_entry_is_stale(entries[account_key_string(account)], config)
                if account_key_string(account) in entries
                else False
            ),
        )
        for account in config.accounts
    ]
    pending = load_pending_kicks(now)
    plan = build_orchestration_plan(
        config=config,
        inputs=inputs,
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name=getattr(tz, "key", str(tz)),
        pending=pending,
        cache_age_seconds=_plan_cache_age_seconds(entries),
        usage_overrides_by_key=usage_overrides_by_key,
    )
    return plan, now


def _orchestrated_pending_kicks(
    *,
    account_labels: set[str] | None = None,
    now: datetime | None = None,
) -> list[PendingKick]:
    pending = load_pending_kicks(now or datetime.now(timezone.utc))
    items = [
        item
        for item in pending.values()
        if item.reason == ScheduleReason.ORCHESTRATED.value
        and (account_labels is None or item.account_label in account_labels)
    ]
    return sorted(items, key=lambda item: (item.kick_at, item.account_label, item.purpose))


def _render_orchestrated_pending_kicks(
    pending: Sequence[PendingKick],
    *,
    title: str = "Orchestration pending kicks",
) -> None:
    table = Table(title=title, show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Purpose")
    table.add_column("Kick at", no_wrap=True)
    table.add_column("Covers", no_wrap=True)
    for item in pending:
        kick_at = from_utc_iso(item.kick_at)
        work_start = from_utc_iso(item.work_start)
        work_end = from_utc_iso(item.work_end)
        table.add_row(
            item.account_label,
            _format_kick_purpose(item.purpose),
            _format_plan_timestamp(kick_at, reference=work_start),
            _format_plan_time_range(work_start, work_end, reference=work_start),
        )
    console.print(table)


def _plan_cancel_payload(
    *,
    read_only: bool,
    applied: bool,
    message: str,
    result: CancelPendingKicksResult,
    matching: Sequence[PendingKick] | None = None,
) -> dict:
    payload = {
        "read_only": read_only,
        "applied": applied,
        "message": message,
        "result": result.to_dict(),
    }
    if matching is not None:
        payload["matching"] = [item.to_dict() for item in matching]
    return payload


def _parse_plan_usage_overrides(
    accounts: Sequence[AccountConfig],
    values: Sequence[str],
) -> dict[str, int]:
    by_label = {account.label: account for account in accounts}
    result: dict[str, int] = {}
    seen_labels: set[str] = set()
    for raw in values:
        if "=" not in raw:
            raise click.ClickException(
                f'Invalid --usage "{raw}". Use LABEL=DURATION, for example '
                '"codex (home)=3h".'
            )
        label, duration = raw.split("=", 1)
        label = label.strip()
        duration = duration.strip()
        if not label or not duration:
            raise click.ClickException(f'Invalid --usage "{raw}". Use LABEL=DURATION.')
        if label in seen_labels:
            raise click.ClickException(f'Duplicate --usage for account "{label}".')
        account = by_label.get(label)
        if account is None:
            raise click.ClickException(f'Unknown account in --usage: "{label}".')
        minutes = _parse_plan_usage_duration_minutes(duration)
        result[account_key_string(account)] = minutes
        seen_labels.add(label)
    return result


def _parse_plan_usage_duration_minutes(value: str) -> int:
    raw = re.sub(r"\s+", "", value.strip().lower())
    minutes: int
    if re.fullmatch(r"\d+", raw):
        minutes = int(raw)
    elif match := re.fullmatch(r"(\d+)m", raw):
        minutes = int(match.group(1))
    elif match := re.fullmatch(r"(\d+(?:\.\d+)?)h", raw):
        minutes = int(round(float(match.group(1)) * 60))
    elif match := re.fullmatch(r"(\d+)h(\d+)m", raw):
        minutes = int(match.group(1)) * 60 + int(match.group(2))
    else:
        raise click.ClickException(
            f'Invalid usage duration "{value}". Use forms like 180, 180m, 3h, 2.5h, or 1h30m.'
        )
    if not 1 <= minutes <= 1440:
        raise click.ClickException("Usage duration must be between 1 and 1440 minutes.")
    return minutes


def _plan_timezone(config: Config, timezone_text: str | None):
    if timezone_text:
        try:
            return ZoneInfo(timezone_text)
        except ZoneInfoNotFoundError as exc:
            raise click.ClickException(f"Unknown timezone: {timezone_text}") from exc
    return local_timezone(config.schedule)


def _plan_cache_age_seconds(entries: dict[str, dict]) -> int | None:
    observed_times = []
    for entry in entries.values():
        observed_at = _status_cache_provider_observed_at(entry)
        observed = _parse_status_cache_observed_at(observed_at) if observed_at else None
        if observed is not None:
            observed_times.append(observed)
    if not observed_times:
        return None
    return max(0, int((_status_cache_now() - min(observed_times)).total_seconds()))


def _emit_plan(plan: OrchestrationPlan, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(plan.to_dict(), indent=2))
        return
    _render_plan(plan)


def _render_plan(plan: OrchestrationPlan) -> None:
    console.print(_tokenkick_title(" — Orchestration Plan"))
    table = Table(show_header=True)
    table.add_column("Time", no_wrap=True)
    table.add_column("Account", style="bold", min_width=20)
    table.add_column("Source", no_wrap=True)
    table.add_column("Notes")
    for segment in plan.segments:
        account = segment.account_label or "—"
        note = segment.note or ""
        if segment.kick_at is not None:
            kick_note = f"kick at {_format_plan_timestamp(segment.kick_at, reference=segment.start)}"
            note = f"{kick_note}; {note}" if note else kick_note
        table.add_row(
            _format_plan_time_range(segment.start, segment.end, reference=plan.work_start),
            account,
            _format_plan_source(segment.source),
            note,
        )
    if not plan.segments:
        table.add_row("—", "—", "no coverage", "No eligible accounts from cached state.")
    console.print(table)

    if plan.planned_kicks:
        console.print(_plain_section_title("Planned pending session kicks"))
        usage_sources = {
            str(row.get("account_key")): str(row.get("usage_source"))
            for row in plan.accounts_considered
            if row.get("account_key") is not None
        }
        kick_table = Table(show_header=True)
        kick_table.add_column("Kick at", no_wrap=True)
        kick_table.add_column("Account", style="bold")
        kick_table.add_column("Purpose")
        kick_table.add_column("Covers", no_wrap=True)
        kick_table.add_column("Usage", no_wrap=True)
        for kick in plan.planned_kicks:
            kick_at = _format_plan_timestamp(kick.kick_at, reference=plan.work_start)
            covers = _format_plan_kick_covers(kick, reference=plan.work_start)
            usage = _format_plan_kick_usage(kick, usage_sources)
            kick_table.add_row(
                kick_at,
                kick.account_label,
                _format_kick_purpose(kick.purpose),
                covers,
                usage,
            )
        console.print(kick_table)

    for gap in plan.coverage_gaps:
        start = from_utc_iso(gap["start"])
        end = from_utc_iso(gap["end"])
        console.print(
            f"[yellow]Coverage gap: {_format_plan_time_range(start, end, reference=plan.work_start)} "
            f"({gap['reason']}).[/yellow]"
        )

    if plan.skipped_accounts:
        skipped = ", ".join(
            f"{item.account_label}={item.reason}" for item in plan.skipped_accounts
        )
        console.print(f"[dim]Skipped: {skipped}[/dim]")
        _render_skipped_specialist_notes(plan)

    diff = plan.diff
    console.print(
        "[dim]Diff: "
        f"adds={len(diff.adds)}, "
        f"replaces_orchestrated={len(diff.replaces_orchestrated)}, "
        f"unchanged_orchestrated={len(diff.unchanged_orchestrated)}, "
        f"conflicts_unmanaged={len(diff.conflicts_unmanaged)}, "
        f"removes_orchestrated={len(diff.removes_orchestrated)}[/dim]"
    )
    if diff.conflicts_unmanaged:
        console.print("[yellow]Unmanaged pending kicks exist; orchestration will not replace them.[/yellow]")
    if diff.removes_orchestrated:
        details = ", ".join(
            _format_stale_orchestrated_removal(item) for item in diff.removes_orchestrated
        )
        count = len(diff.removes_orchestrated)
        noun = "kick" if count == 1 else "kicks"
        verb = "Removed" if plan.applied else "Applying removes"
        console.print(
            f"[yellow]{verb} {count} stale orchestrated pending {noun} "
            f"not in this plan: {details}.[/yellow]"
        )

    mode = "Applied" if plan.applied else "Read-only"
    console.print(f"\n[bold]{mode}:[/bold] {plan.message}")


def _format_stale_orchestrated_removal(item: dict) -> str:
    existing = item.get("existing") if isinstance(item.get("existing"), dict) else {}
    label = existing.get("account_label") or "unknown account"
    kick_at_text = existing.get("kick_at")
    try:
        kick_at = from_utc_iso(kick_at_text) if isinstance(kick_at_text, str) else None
    except ValueError:
        kick_at = None
    if kick_at is None:
        return str(label)
    return f"{label} (kick {kick_at.astimezone().strftime('%H:%M %Z')})"


def _tokenkick_title(suffix: str = "") -> Text:
    title = Text()
    title.append("Token", style="bold white")
    title.append("Kick", style="bold green")
    title.append(suffix, style="bold")
    return title


def _plain_section_title(value: str) -> Text:
    return Text(value, style="bold")


def _render_skipped_specialist_notes(plan: OrchestrationPlan) -> None:
    specialists_by_key = {
        str(row.get("account_key")): row
        for row in plan.accounts_considered
        if row.get("effective_orchestration_role") == "specialist"
    }
    skipped_specialists = [
        item
        for item in plan.skipped_accounts
        if item.account_key in specialists_by_key
    ]
    if not skipped_specialists:
        return
    console.print(_plain_section_title("Skipped specialist"))
    for item in skipped_specialists:
        reason = _format_specialist_skip_reason(item.reason)
        refresh_hint = (
            " Run tk status --refresh, then rebuild the plan if you want it included."
            if item.reason == "stale_status"
            else ""
        )
        console.print(
            f"[dim]{item.account_label}: could not be prepared for this plan "
            f"because {reason}. It remains outside the orchestration plan, "
            f"so normal auto-kick can still run.{refresh_hint}[/dim]"
        )


def _format_specialist_skip_reason(reason: str) -> str:
    labels = {
        "specialist_early_kick_window_missed": "the early-kick window has already passed",
        "specialist_early_kick_after_work_start": "the required early kick would happen after the work starts",
        "specialist_not_available_for_early_kick": (
            "its current session timing does not make the early kick available"
        ),
        "no_session_window": "TokenKick does not know its session window length",
        "hidden": "the account is hidden",
        "provider_not_kickable": "the provider is monitor-only",
        "auto_kick_disabled": "auto-kick is disabled for the account",
        "session_auto_kick_disabled": "session auto-kick is disabled for the account",
        "stale_status": "the cached provider status is stale",
        "unknown_status": "the provider status is unknown",
        "weekly_exhausted": "the weekly quota is exhausted",
        "session_exhausted": "the current session is exhausted",
    }
    return labels.get(reason, reason.replace("_", " "))


def _format_plan_source(source: str) -> str:
    labels = {
        "planned_early_anchor": "Pre-anchor",
        "expected_reset_reuse": "Reset-boundary reuse",
        "planned_fresh_session": "Fresh session",
        "active_session": "Active session",
        "natural_reset_reuse": "Natural reset reuse",
        "no coverage": "No coverage",
    }
    return labels.get(source, source.replace("_", " ").title())


def _format_plan_kick_usage(kick: PlannedKick, usage_sources: dict[str, str]) -> str:
    usage = _format_plan_usage_minutes(kick.usable_session_minutes)
    if _specialist_reset_boundary(kick) is not None:
        specialist_usage = f"{usage} + reset + {usage}"
        if usage_sources.get(kick.account_key) == "plan_override":
            return f"{specialist_usage} override"
        return specialist_usage
    if usage_sources.get(kick.account_key) == "plan_override":
        return f"{usage} override"
    return usage


def _format_plan_kick_covers(kick: PlannedKick, *, reference: datetime | None = None) -> str:
    covers = _format_plan_time_range(
        kick.segment_start,
        kick.segment_end,
        reference=reference,
    )
    reset_at = _specialist_reset_boundary(kick)
    if reset_at is None:
        return covers
    return f"{covers} (reset {_format_plan_timestamp(reset_at, reference=kick.segment_start)})"


def _specialist_reset_boundary(kick: PlannedKick) -> datetime | None:
    if kick.purpose != PENDING_KICK_PURPOSE_SPECIALIST_READINESS:
        return None
    reset_at = kick.segment_start + timedelta(minutes=kick.usable_session_minutes)
    if kick.segment_start < reset_at < kick.segment_end:
        return reset_at
    return None


def _format_plan_usage_minutes(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes > 60:
        hours, remainder = divmod(minutes, 60)
        return f"{hours}h{remainder:02d}m"
    return f"{minutes}m"


def _format_kick_purpose(purpose: str) -> str:
    if purpose == PENDING_KICK_PURPOSE_SPECIALIST_READINESS:
        return "specialist readiness"
    if purpose == PENDING_KICK_PURPOSE_COVERAGE:
        return "coverage"
    return purpose.replace("_", " ")


def _format_plan_time_range(
    start: datetime,
    end: datetime,
    *,
    reference: datetime | None = None,
) -> str:
    local_start = start.astimezone()
    local_end = end.astimezone()
    if local_start.date() == local_end.date():
        suffix = _format_plan_day_offset(local_start, reference)
        return f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}{suffix}"
    if reference is not None:
        suffix = _format_plan_day_offset(local_end, reference)
        if suffix:
            return f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}{suffix}"
    if (local_end.date() - local_start.date()).days == 1:
        return f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')} (+1 day)"
    return f"{local_start.strftime('%Y-%m-%d %H:%M')}-{local_end.strftime('%Y-%m-%d %H:%M')}"


def _format_plan_day_offset(value: datetime, reference: datetime | None) -> str:
    if reference is None:
        return ""
    day_offset = (value.date() - reference.astimezone().date()).days
    if day_offset == 0:
        return ""
    if day_offset > 0:
        return f" (+{day_offset} day{'s' if day_offset != 1 else ''})"
    return f" ({day_offset} day{'s' if day_offset != -1 else ''})"


def _format_plan_timestamp(value: datetime, *, reference: datetime | None = None) -> str:
    local_value = value.astimezone()
    if reference is not None and local_value.date() != reference.astimezone().date():
        return local_value.strftime("%Y-%m-%d %H:%M")
    return local_value.strftime("%H:%M")


def _reservation_advisories_for_pairs(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    config: Config | None = None,
    *,
    now: datetime | None = None,
) -> list[ReservationAdvisory]:
    current = now or datetime.now(timezone.utc)
    statuses_by_key = _cache_statuses_by_key_from_pairs(accounts, statuses)
    return build_reservation_advisories(
        accounts,
        statuses_by_key,
        load_pending_kicks(current),
        now=current,
    )


def _render_reservation_advisories(advisories: Sequence[ReservationAdvisory]) -> None:
    warning_advisories = [
        advisory
        for advisory in advisories
        if advisory.risk_state in ACTIONABLE_RISK_STATES
    ]
    if not warning_advisories:
        return
    console.print("\n[yellow bold]Reserved account warnings:[/yellow bold]")
    for advisory in warning_advisories:
        line = Text("  ! ", style="yellow")
        line.append(format_reservation_advisory_message(advisory), style="yellow")
        console.print(line)


def _render_reservation_advisories_for_pairs(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    config: Config | None = None,
) -> None:
    _render_reservation_advisories(_reservation_advisories_for_pairs(accounts, statuses, config))


def _render_current_reservation_advisories() -> None:
    config = Config.load()
    cached = _load_status_cache(config)
    if cached is None:
        return
    accounts, statuses, _cache_entries = cached
    _render_reservation_advisories_for_pairs(accounts, statuses, config)


def _send_reservation_advisory_notifications(
    accounts: list[AccountConfig],
    statuses_by_key: dict[str, AccountStatus],
    config: Config,
    *,
    now: datetime,
    daemon_log: bool,
) -> int:
    advisories = build_reservation_advisories(
        accounts,
        statuses_by_key,
        load_pending_kicks(now),
        now=now,
    )
    state = load_reservation_advisory_state()
    accounts_by_key = {account_key_string(account): account for account in accounts}
    sent = 0
    for advisory in advisories:
        if advisory.risk_state not in ACTIONABLE_RISK_STATES:
            continue
        if advisory.notification_key in state:
            continue
        account = accounts_by_key.get(advisory.account_key)
        if account is None:
            continue
        delivered, acknowledged = _send_account_notifications(
            account,
            config.notifications,
            lambda notifications, advisory=advisory: notify_reservation_advisory(
                format_reservation_advisory_message(advisory),
                notifications,
            ),
            daemon_log=daemon_log,
            context="reservation_advisory",
        )
        if acknowledged:
            mark_reservation_advisory_notified(advisory.notification_key, now=now)
            sent += 1
            if daemon_log:
                _daemon_log(
                    "reservation_advisory_sent",
                    account=advisory.account_label,
                    risk_state=advisory.risk_state,
                    delivered=bool(delivered),
                )
    return sent


@cli.command("run")
@click.option("--dry-run", is_flag=True, help="Show what would be kicked without acting")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--codex", "codex_only", is_flag=True, help="Run only Codex provider accounts")
def run_cmd(dry_run: bool, as_json: bool, codex_only: bool):
    """Refresh status, kick eligible windows, and summarize."""
    config, accounts, statuses, duration_ms, refresh_error = _run_refresh(codex_only=codex_only)
    kicked: list[dict] = []
    skipped: list[dict] = []
    reservation_advisories: list[ReservationAdvisory] = []
    failed = refresh_error is not None

    if not refresh_error:
        now = datetime.now(timezone.utc)
        statuses_by_key = _cache_statuses_by_key_from_pairs(accounts, statuses)
        reservation_advisories = build_reservation_advisories(
            accounts,
            statuses_by_key,
            load_pending_kicks(now),
            now=now,
        )
        if not dry_run:
            _send_reservation_advisory_notifications(
                accounts,
                statuses_by_key,
                config,
                now=now,
                daemon_log=False,
            )
        stagger_state = KickStaggerState()
        due_results: list[dict] = []
        if not dry_run:
            # Same shared due-pending execution the daemon runs, so a due
            # orchestrated kick executes instead of falling through to
            # smart-schedule evaluation.
            _execute_due_pending_kicks(
                accounts,
                config,
                statuses_by_key=statuses_by_key,
                stagger_state=stagger_state,
                results_sink=due_results,
            )
        for item in due_results:
            entry, item_failed = _run_due_pending_entry(item)
            if item_failed:
                skipped.append(entry)
            else:
                kicked.append(entry)
            failed = failed or item_failed
        # Reload after due execution so evaluation sees removed pendings and
        # the kick history written by the executed kicks.
        history = load_kick_history(limit=200)
        pending = load_pending_kicks(now)
        for account, status in zip(accounts, statuses, strict=False):
            bucket, item, item_failed = _run_evaluate_account(
                account,
                status,
                config,
                dry_run=dry_run,
                history=history,
                pending=pending,
                now=now,
                stagger_state=stagger_state,
            )
            if bucket == "kicked":
                kicked.append(item)
            else:
                skipped.append(item)
            failed = failed or item_failed

    payload = _run_payload(
        refreshed_count=len(accounts),
        refresh_duration_ms=duration_ms,
        refresh_error=refresh_error,
        dry_run=dry_run,
        kicked=kicked,
        skipped=skipped,
        reservation_advisories=reservation_advisories,
    )
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        if not accounts and refresh_error is None:
            console.print("[dim]No saved accounts. Run [bold]tk setup[/bold] after logging in.[/dim]")
        _render_run_summary(
            refreshed_count=len(accounts),
            refresh_duration_ms=duration_ms,
            refresh_error=refresh_error,
            dry_run=dry_run,
            kicked=kicked,
            skipped=skipped,
            reservation_advisories=reservation_advisories,
        )
        if dry_run:
            _print_report_timestamp("Dry run evaluated at")
    sys.exit(1 if failed else 0)


@cli.command("doctor")
@click.argument("label", required=False)
@click.option("--json-output", is_flag=True, help="Output as JSON")
@click.option("--repair", is_flag=True, help="Clean dead refresh locks before reporting")
def doctor_cmd(label: str | None, json_output: bool, repair: bool):
    """Diagnose TokenKick config, cache, daemon, and provider wiring."""
    try:
        report = build_doctor_report(label=label, repair=repair)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    if json_output:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        render_doctor_report(report, console)
        _print_report_timestamp("Doctor run at")
    sys.exit(0 if report.summary.fail == 0 else 1)


# ---------------------------------------------------------------------------
# tk accounts
# ---------------------------------------------------------------------------

@cli.group(invoke_without_command=True)
@click.pass_context
def accounts(ctx):
    """Manage account visibility."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(accounts_list)


@accounts.command("list")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def accounts_list(as_json: bool):
    """Show all discovered accounts and visibility state."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    if _app_json_requested(as_json):
        emit_app_success(
            {"accounts": _accounts_list_payload(config, loaded_accounts)},
            message=None if loaded_accounts else "No saved accounts. Run tk setup after logging in.",
        )
        return
    if not loaded_accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return
    _render_accounts_table(config, loaded_accounts)


@accounts.command("notifications")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def accounts_notifications(as_json: bool):
    """Show per-account notification delivery state."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    if _app_json_requested(as_json):
        emit_app_success(
            _account_notifications_payload(config, loaded_accounts),
            message=None if loaded_accounts else "No saved accounts. Run tk setup after logging in.",
        )
        return
    if not loaded_accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return
    _render_account_notifications_table(config, loaded_accounts)


@accounts.command("planning")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def accounts_planning(as_json: bool):
    """Show account planning defaults, orchestration roles, and reserve thresholds."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    if _app_json_requested(as_json):
        emit_app_success(
            {"accounts": _account_planning_payload(config, loaded_accounts)},
            message=None if loaded_accounts else "No saved accounts. Run tk setup after logging in.",
        )
        return
    if not loaded_accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return
    _render_account_planning_table(config, loaded_accounts)


@accounts.command("detail")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def accounts_detail(label: str, as_json: bool):
    """Show read-only details for one account."""
    config = Config.load()
    account = _account_by_label_or_exit(label, _load_accounts(config))
    if as_json:
        click.echo(json.dumps(_account_detail_payload(account), indent=2))
        return
    _render_account_detail(account)


def _run_account_mutation(
    as_json: bool,
    mutator: Callable[[], AccountConfig | None],
) -> None:
    if _app_json_requested(as_json):
        _account_mutation_json(mutator)
        return
    mutator()


@accounts.command("hide")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_hide(label: str, as_json: bool):
    """Hide an account from tk status and bulk kicking."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_account_visibility(label, visible=False, config=config, accounts=loaded_accounts),
    )


@accounts.command("show")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_show(label: str, as_json: bool):
    """Show an account in tk status."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_account_visibility(label, visible=True, config=config, accounts=loaded_accounts),
    )


@accounts.command("enable-notifications")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_enable_notifications(label: str, as_json: bool):
    """Enable notifications for one account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_account_notifications(label, enabled=True, config=config, accounts=loaded_accounts),
    )


@accounts.command("disable-notifications")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_disable_notifications(label: str, as_json: bool):
    """Disable notifications for one account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_account_notifications(label, enabled=False, config=config, accounts=loaded_accounts),
    )


@accounts.command("set-notifications")
@click.argument("label")
@click.option("--ntfy", "ntfy", is_flag=True, help="Route this account to ntfy")
@click.option("--telegram", "telegram", is_flag=True, help="Route this account to Telegram")
@click.option("--global-default", "global_default", is_flag=True, help="Use globally enabled destinations")
@click.option("--none", "none", is_flag=True, help="Disable notifications for this account")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_notifications(
    label: str,
    ntfy: bool,
    telegram: bool,
    global_default: bool,
    none: bool,
    as_json: bool,
):
    """Set account-specific notification routes."""
    exclusive_count = int(global_default) + int(none)
    usage_error: str | None = None
    if exclusive_count > 1 or (exclusive_count and (ntfy or telegram)):
        usage_error = "Choose route backends, --global-default, or --none."
    elif not global_default and not none and not (ntfy or telegram):
        usage_error = "Choose --ntfy, --telegram, --global-default, or --none."
    if usage_error is not None:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, usage_error)
            sys.exit(2)
        console.print(f"[red]{usage_error}[/red]")
        raise click.exceptions.Exit(2)
    backends = None if global_default else [] if none else [backend for backend, enabled in (("ntfy", ntfy), ("telegram", telegram)) if enabled]
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_account_notification_backends(
            label,
            backends,
            config=config,
            accounts=loaded_accounts,
        ),
    )


@accounts.command("set-usable")
@click.argument("label")
@click.argument("minutes", type=click.IntRange(1, 1440))
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_usable(label: str, minutes: int, as_json: bool):
    """Set measured usable planning minutes for one account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_usable_session_minutes(label, minutes, config=config, accounts=loaded_accounts),
    )


@accounts.command("set-role")
@click.argument("label")
@click.argument("role")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_role(label: str, role: str, as_json: bool):
    """Set orchestration role for one account."""
    try:
        normalized = normalize_orchestration_role(role)
    except ValueError as exc:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, str(exc))
            sys.exit(2)
        console.print(f"[red]{exc}[/red]")
        raise click.exceptions.Exit(2) from exc
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_orchestration_role(label, normalized, config=config, accounts=loaded_accounts),
    )


@accounts.command("set-weekly-reserve")
@click.argument("label")
@click.argument("threshold", type=click.IntRange(1, 99))
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_weekly_reserve(label: str, threshold: int, as_json: bool):
    """Demote an account to backup after a weekly usage percent threshold."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_weekly_reserve_threshold(
            label,
            threshold,
            config=config,
            accounts=loaded_accounts,
        ),
    )


@accounts.command("clear-weekly-reserve")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_clear_weekly_reserve(label: str, as_json: bool):
    """Clear weekly reserve threshold for one account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_weekly_reserve_threshold(
            label,
            None,
            config=config,
            accounts=loaded_accounts,
        ),
    )


@accounts.command("enable-probe")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_enable_probe(label: str, as_json: bool):
    """Enable explicit quota-consuming status probe for a Claude account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_status_probe(label, enabled=True, config=config, accounts=loaded_accounts),
    )


@accounts.command("disable-probe")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_disable_probe(label: str, as_json: bool):
    """Disable explicit quota-consuming status probe for a Claude account."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_status_probe(label, enabled=False, config=config, accounts=loaded_accounts),
    )


@accounts.command("set-direct-usage")
@click.argument("label")
@click.option("--enable", "enable", is_flag=True, help="Enable Claude direct /usage for this account")
@click.option("--disable", "disable", is_flag=True, help="Disable Claude direct /usage for this account")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_direct_usage(label: str, enable: bool, disable: bool, as_json: bool):
    """Enable or disable Claude direct /usage for one account."""
    if enable == disable:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, "Choose exactly one: --enable or --disable.")
            sys.exit(2)
        console.print("[red]Choose exactly one: --enable or --disable.[/red]")
        raise click.exceptions.Exit(2)
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_direct_usage(label, enabled=enable, config=config, accounts=loaded_accounts),
    )


@accounts.command("set-kick-model")
@click.argument("label")
@click.argument("model")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_set_kick_model(label: str, model: str, as_json: bool):
    """Override the model used for tiny kick prompts."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_kick_model(label, model=model, config=config, accounts=loaded_accounts),
    )


@accounts.command("clear-kick-model")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def accounts_clear_kick_model(label: str, as_json: bool):
    """Reset an account to its provider default kick model."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_kick_model(label, model=None, config=config, accounts=loaded_accounts),
    )


# ---------------------------------------------------------------------------
# tk model
# ---------------------------------------------------------------------------

@cli.group("model")
def model_group():
    """Manage the model used for tiny kick prompts."""


@model_group.command("set")
@click.argument("label")
@click.argument("model")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def model_set(label: str, model: str, as_json: bool):
    """Override the model used for tiny kick prompts."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_kick_model(label, model=model, config=config, accounts=loaded_accounts),
    )


@model_group.command("clear")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def model_clear(label: str, as_json: bool):
    """Reset an account to its provider default kick model."""
    config = Config.load()
    loaded_accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_kick_model(label, model=None, config=config, accounts=loaded_accounts),
    )


# ---------------------------------------------------------------------------
# tk kick
# ---------------------------------------------------------------------------

@dataclass
class _SingleKickTarget:
    account: AccountConfig
    status: AccountStatus
    clear_pending: bool


class _SingleKickStopped(Exception):
    """Single-label kick resolution ended without a kick target."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        cli_markup: str | None = None,
        failure: bool = False,
        exit_code: int = 0,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.cli_markup = cli_markup if cli_markup is not None else message
        self.failure = failure
        self.exit_code = exit_code


class _SingleKickConfirmationRequired(Exception):
    """JSON kick hit a prompt the CLI would ask interactively; needs --yes."""

    def __init__(self, kind: str, question: str):
        super().__init__(question)
        self.kind = kind
        self.question = question


def _resolve_single_kick_target(
    label: str,
    config: Config,
    accounts: list[AccountConfig],
    *,
    force: bool,
    dry_run: bool,
    confirm: Callable[[str, str, str | None], bool],
) -> _SingleKickTarget:
    """Shared single-label kick eligibility for the CLI and JSON paths.

    Mirrors the historical `tk kick <label>` flow exactly: every skip keeps
    its original wording (markup preserved for the CLI), and the two
    interactive prompts go through `confirm(kind, question, preamble)`.
    """
    account = next((a for a in accounts if a.label == label), None)
    if not account:
        raise _SingleKickStopped(
            "account_not_found",
            f'Account "{label}" not found.',
            cli_markup=f'[red]Account "{label}" not found.[/red]',
            failure=True,
        )
    if _is_monitor_only_provider(account.provider):
        message = _monitor_only_message(account.provider)
        raise _SingleKickStopped(
            "monitor_only",
            message,
            cli_markup=f"[red]{message}[/red]",
            failure=True,
            exit_code=1,
        )
    if account.provider not in KICKABLE_PROVIDERS:
        raise _SingleKickStopped(
            "not_kickable",
            f'Skipping "{label}": only Codex and Claude accounts can be kicked.',
            cli_markup=(
                f'[yellow]Skipping "{label}": only Codex and Claude accounts can be kicked.[/yellow]'
            ),
        )

    clear_pending = False
    key = account_key_string(account)
    now = datetime.now(timezone.utc)
    pending_kick = load_pending_kicks(now).get(key)
    if pending_kick_blocks_auto_kick(pending_kick, now):
        if not dry_run and not force:
            if not confirm(
                "pending_kick",
                f'"{label}" has a planned kick {_format_run_scheduled_reason(pending_kick)}. '
                "Kick now and clear that pending kick?",
                None,
            ):
                raise _SingleKickStopped(
                    "cancelled",
                    "Kick cancelled.",
                    cli_markup="[dim]Kick cancelled.[/dim]",
                )
        if not dry_run:
            clear_pending = True

    status = _fetch_status(account, config)
    weekly_reset_ready = _weekly_reset_ready(status)
    long_kick = _long_kick_eligible(status)
    phantom_session = (
        not weekly_reset_ready
        and _is_phantom_session_candidate(status)
        and _phantom_session_ready(account, status, record_observation=False)
    )
    session_kick = (
        not weekly_reset_ready
        and (_session_kick_eligible(account, status, allow_stale=True) or phantom_session)
    )
    if not long_kick and status.state != AccountState.FRESH and not session_kick and not force:
        cooldown_remaining = _session_cooldown_remaining(account, status)
        if cooldown_remaining is not None:
            message = (
                f'Waiting to kick "{label}": session resets in '
                f"{_format_duration(cooldown_remaining)}."
            )
            raise _SingleKickStopped(
                "session_cooldown",
                message,
                cli_markup=f"[dim]{message}[/dim]",
            )
        message = f'Skipping "{label}": state is {status.state.value}, not fresh.'
        raise _SingleKickStopped("not_fresh", message, cli_markup=f"[dim]{message}[/dim]")

    stale_confirmed = False
    if status.stale:
        if not force:
            if not confirm(
                "stale_status",
                f'Kick "{label}" anyway?',
                f"Warning: {_stale_status_reason(status)}",
            ):
                raise _SingleKickStopped(
                    "cancelled",
                    "Kick cancelled.",
                    cli_markup="[dim]Kick cancelled.[/dim]",
                )
        stale_confirmed = True

    cooldown_remaining = (
        None
        if force or stale_confirmed or long_kick or phantom_session
        else _session_cooldown_remaining(account, status)
    )
    if cooldown_remaining is not None:
        message = (
            f'Waiting to kick "{label}": session resets in '
            f"{_format_duration(cooldown_remaining)}."
        )
        raise _SingleKickStopped(
            "session_cooldown",
            message,
            cli_markup=f"[dim]{message}[/dim]",
        )

    history = load_kick_history(limit=200)
    provider_unchanged_retry_ready = False
    if phantom_session and not force and not stale_confirmed:
        provider_unchanged_attempts = _provider_unchanged_phantom_kick_attempt_count(
            account,
            status,
            history,
        )
        if provider_unchanged_attempts >= PROVIDER_UNCHANGED_PHANTOM_KICK_MAX_ATTEMPTS:
            message = (
                f'Skipping "{label}": phantom session unresolved after '
                f"{provider_unchanged_attempts} attempts."
            )
            raise _SingleKickStopped(
                "phantom_attempts_exhausted",
                message,
                cli_markup=f"[dim]{message}[/dim]",
            )
        provider_unchanged_backoff_until = _provider_unchanged_phantom_kick_backoff_until(
            account,
            status,
            history,
        )
        if provider_unchanged_backoff_until is not None:
            retry_at = datetime.fromtimestamp(
                provider_unchanged_backoff_until,
                timezone.utc,
            ).astimezone()
            message = (
                f'Skipping "{label}": provider unchanged after recent phantom '
                f"kick; retry after {retry_at.strftime('%H:%M %Z')}."
            )
            raise _SingleKickStopped(
                "phantom_backoff",
                message,
                cli_markup=f"[dim]{message}[/dim]",
            )
        provider_unchanged_retry_ready = provider_unchanged_attempts > 0
    if (
        not force
        and
        not stale_confirmed
        and not session_kick
        and _was_kicked_in_current_window(account, status, history)
    ):
        message = f'Skipping "{label}": already kicked in this window.'
        raise _SingleKickStopped(
            "already_kicked_window",
            message,
            cli_markup=f"[dim]{message}[/dim]",
        )
    if (
        not force
        and
        not stale_confirmed
        and session_kick
        and not provider_unchanged_retry_ready
        and _was_kicked_in_current_session_window(account, status, history)
    ):
        message = f'Skipping "{label}": already kicked in this session window.'
        raise _SingleKickStopped(
            "already_kicked_session_window",
            message,
            cli_markup=f"[dim]{message}[/dim]",
        )

    return _SingleKickTarget(account=account, status=status, clear_pending=clear_pending)


def _cli_kick_confirm(kind: str, question: str, preamble: str | None) -> bool:
    del kind
    if preamble:
        click.echo(preamble)
    return _confirm_prompt(question, default=False)


_KICK_EVENT_EXCLUDED_FIELDS = ("prompt_text", "response_text", "provider_output_excerpt")


def _kick_event_payload(event: KickEvent) -> dict:
    data = event.to_dict()
    for field_name in _KICK_EVENT_EXCLUDED_FIELDS:
        data.pop(field_name, None)
    return data


def _kick_label_json(
    label: str,
    config: Config,
    accounts: list[AccountConfig],
    *,
    force: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """`tk kick LABEL --json-output`: app envelope outcomes, no prompts."""
    confirmations: list[str] = []

    def confirm(kind: str, question: str, preamble: str | None) -> bool:
        del preamble
        confirmations.append(kind)
        if dry_run or assume_yes or force:
            return True
        raise _SingleKickConfirmationRequired(kind, question)

    base_payload = {"action": "kick", "account": label, "dry_run": dry_run}
    try:
        target = _resolve_single_kick_target(
            label,
            config,
            accounts,
            force=force,
            dry_run=dry_run,
            confirm=confirm,
        )
    except _SingleKickConfirmationRequired as needed:
        emit_app_error(
            "confirmation_required",
            f"{needed.question} Re-run with --yes to confirm.",
            payload={
                **base_payload,
                "decision": "confirmation_required",
                "confirmations": [needed.kind],
            },
        )
        sys.exit(1)
    except _SingleKickStopped as stop:
        if stop.failure:
            emit_app_error(stop.code, stop.message, payload={**base_payload, "decision": "stopped"})
            sys.exit(stop.exit_code or 1)
        emit_app_success(
            {
                **base_payload,
                "decision": "skipped",
                "reason_code": stop.code,
                "kicked": False,
                "event": None,
            },
            message=stop.message,
        )
        return

    kick_type = _kick_type_for_status(target.status)
    if dry_run:
        emit_app_success(
            {
                **base_payload,
                "decision": "would_kick",
                "kicked": False,
                "kick_type": kick_type,
                "clears_pending_kick": target.clear_pending,
                "confirmations": confirmations,
                "event": None,
            }
        )
        return

    event = _kick_and_notify(target.account, config, target.status, kick_type=kick_type)
    if event.success and target.clear_pending:
        remove_pending_kick(target.account)
    if event.success and event.confirmed:
        result = "confirmed"
    elif event.success:
        result = "unconfirmed"
    else:
        result = "failed"
    payload = {
        **base_payload,
        "decision": "attempted",
        "kicked": event.success,
        "result": result,
        "kick_type": kick_type,
        "event": _kick_event_payload(event),
    }
    if event.success:
        emit_app_success(payload, message=event.error)
        return
    emit_app_error("kick_failed", event.error or "The kick attempt failed.", payload=payload)
    sys.exit(1)


@cli.command()
@click.argument("label", required=False)
@click.option("--all", "kick_all", is_flag=True, help="Kick all fresh enabled accounts")
@click.option("--auto", "auto_mode", is_flag=True, help="Kick or schedule all fresh enabled accounts")
@click.option("--force", is_flag=True, help="Bypass smart scheduling and kick immediately")
@click.option("--dry-run", is_flag=True, help="Show what would be kicked without acting")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON (single label only)")
@click.option("--yes", "assume_yes", is_flag=True, help="Confirm prompts when used with --json-output")
@click.option("--enable", "enable_label", metavar="LABEL", hidden=True)
@click.option("--disable", "disable_label", metavar="LABEL", hidden=True)
def kick(
    label: str | None,
    kick_all: bool,
    auto_mode: bool,
    force: bool,
    dry_run: bool,
    as_json: bool,
    assume_yes: bool,
    enable_label: str | None,
    disable_label: str | None,
):
    """Kick a quota window to anchor it."""
    config = _repair_codex_home_identity_drift_if_needed(
        _migrate_codex_home_keys_if_needed(Config.load())
    )
    accounts = _load_accounts(config)

    if _app_json_requested(as_json):
        if kick_all or auto_mode or enable_label or disable_label:
            emit_app_error(
                ERROR_USAGE,
                "--json-output supports kicking a single account label only.",
            )
            sys.exit(2)
        if not label:
            emit_app_error(ERROR_USAGE, "Specify an account label to kick.")
            sys.exit(2)
        if not accounts:
            emit_app_error("no_accounts", "No saved accounts. Run setup after logging in.")
            sys.exit(1)
        with _console_redirected_to_stderr():
            _kick_label_json(
                label,
                config,
                accounts,
                force=force,
                dry_run=dry_run,
                assume_yes=assume_yes,
            )
        return

    if not accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return

    toggle_labels = [value for value in (enable_label, disable_label) if value]
    if toggle_labels:
        if len(toggle_labels) > 1 or kick_all or auto_mode or label or dry_run or force:
            console.print(
                "[red]Use exactly one kick mode: --all, auto enable <label>, "
                "auto disable <label>, or <label>.[/red]"
            )
            return
        _set_auto_kick(toggle_labels[0], enabled=bool(enable_label), config=config, accounts=accounts)
        return

    targets: list[tuple[AccountConfig, AccountStatus]] = []
    clear_pending_after_manual_success: set[str] = set()

    if kick_all or auto_mode:
        stagger_state = KickStaggerState()
        if not dry_run:
            _execute_due_pending_kicks(accounts, config, stagger_state=stagger_state)
        _kick_all_enabled_accounts(
            accounts,
            config,
            dry_run=dry_run,
            force=force,
            stagger_state=stagger_state,
            suppress_pending=not force,
        )
        return

    elif label:
        try:
            target = _resolve_single_kick_target(
                label,
                config,
                accounts,
                force=force,
                dry_run=dry_run,
                confirm=_cli_kick_confirm,
            )
        except _SingleKickStopped as stop:
            console.print(stop.cli_markup)
            if stop.exit_code:
                raise click.exceptions.Exit(stop.exit_code) from stop
            return
        if target.clear_pending:
            clear_pending_after_manual_success.add(account_key_string(target.account))
        targets.append((target.account, target.status))

    else:
        console.print("[yellow]Specify an account label or use --all.[/yellow]")
        console.print("[dim]Usage: tk kick <label> | tk kick --all[/dim]")
        return

    for account, status in targets:
        if dry_run:
            console.print(f'[dim]Would kick:[/dim] {account.label}')
            continue

        event = _kick_and_notify(account, config, status, kick_type=_kick_type_for_status(status))
        if event.success and account_key_string(account) in clear_pending_after_manual_success:
            remove_pending_kick(account)


@cli.command()
@click.argument("label")
def wake(label: str):
    """Bootstrap a dormant account with a one-time explicit kick."""
    config = _repair_codex_home_identity_drift_if_needed(
        _migrate_codex_home_keys_if_needed(Config.load())
    )
    accounts = _load_accounts(config)
    account = next((candidate for candidate in accounts if candidate.label == label), None)
    if account is None:
        console.print(f'[red]Account "{label}" not found.[/red]')
        return
    if _is_monitor_only_provider(account.provider):
        console.print(f"[red]{_monitor_only_message(account.provider)}[/red]")
        raise click.exceptions.Exit(1)
    if account.provider not in KICKABLE_PROVIDERS:
        console.print(
            f'[yellow]Skipping "{label}": only Codex and Claude accounts can be woken.[/yellow]'
        )
        return
    status = _fetch_status(account, config)
    if status.state != AccountState.FRESH:
        console.print(f'[dim]Skipping "{label}": state is {status.state.value}, not fresh.[/dim]')
        return
    _kick_and_notify(account, config, status, kick_type="wake")


# ---------------------------------------------------------------------------
# tk auto
# ---------------------------------------------------------------------------

@cli.group(invoke_without_command=True)
@click.pass_context
def auto(ctx):
    """Manage auto-kick settings."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(auto_status)


@auto.command("status")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def auto_status(as_json: bool):
    """Show auto-kick state for all accounts."""
    config = Config.load()
    accounts = _load_accounts(config)
    if _app_json_requested(as_json):
        emit_app_success(
            {"accounts": _auto_status_payload(accounts)},
            message=None if accounts else "No saved accounts. Run tk setup after logging in.",
        )
        return
    if not accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return
    _render_auto_status_table(accounts)
    _print_report_timestamp("Auto-kick status printed at")


@auto.command("enable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.option("--accept-risk", "consent_token", metavar="ENABLE", hidden=True)
def auto_enable(label: str, as_json: bool, consent_token: str | None):
    """Enable auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_auto_kick(
            label,
            enabled=True,
            config=config,
            accounts=accounts,
            allow_consent_prompt=not _app_json_requested(as_json),
            consent_token=consent_token,
        ),
    )


@auto.command("disable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def auto_disable(label: str, as_json: bool):
    """Disable auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_auto_kick(label, enabled=False, config=config, accounts=accounts),
    )


@auto.group("session")
def auto_session():
    """Manage 5h/session window auto-kicks."""


@auto_session.command("enable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.option("--accept-risk", "consent_token", metavar="ENABLE", hidden=True)
def auto_session_enable(label: str, as_json: bool, consent_token: str | None):
    """Enable session auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_session_auto_kick(
            label,
            enabled=True,
            config=config,
            accounts=accounts,
            allow_consent_prompt=not _app_json_requested(as_json),
            consent_token=consent_token,
        ),
    )


@auto_session.command("disable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def auto_session_disable(label: str, as_json: bool):
    """Disable session auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_session_auto_kick(label, enabled=False, config=config, accounts=accounts),
    )


@auto.group("weekly")
def auto_weekly():
    """Manage weekly/primary window auto-kicks."""


@auto_weekly.command("enable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.option("--accept-risk", "consent_token", metavar="ENABLE", hidden=True)
def auto_weekly_enable(label: str, as_json: bool, consent_token: str | None):
    """Enable weekly auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_weekly_auto_kick(
            label,
            enabled=True,
            config=config,
            accounts=accounts,
            allow_consent_prompt=not _app_json_requested(as_json),
            consent_token=consent_token,
        ),
    )


@auto_weekly.command("disable")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def auto_weekly_disable(label: str, as_json: bool):
    """Disable weekly auto-kick for an account."""
    config = Config.load()
    accounts = _load_accounts(config)
    _run_account_mutation(
        as_json,
        lambda: _set_weekly_auto_kick(label, enabled=False, config=config, accounts=accounts),
    )


# ---------------------------------------------------------------------------
# tk codex-strategy / tk codex-fire-all compatibility alias
# ---------------------------------------------------------------------------

@cli.group("codex-strategy", invoke_without_command=True)
@click.pass_context
def codex_strategy(ctx):
    """Manage Codex surface strategy."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(codex_strategy_status)


@codex_strategy.command("status")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def codex_strategy_status(as_json: bool):
    """Show Codex burst ladder mode, order, and gap."""
    config = Config.load()
    payload = _codex_burst_ladder_status_payload(config)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    _render_codex_burst_ladder_status(payload)


@codex_strategy.command("enable")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def codex_strategy_enable(as_json: bool):
    """Enable Codex burst ladder dispatch for auto/scheduled kicks."""
    if _app_json_requested(as_json):
        _run_codex_strategy_mutation_json(
            "enable",
            lambda: _save_config_like(Config.load(), codex_burst_ladder_enabled=True) or Config.load(),
        )
        return
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_enabled=True)
    console.print("[green]Codex burst ladder enabled for auto/scheduled kicks.[/green]")
    _render_codex_burst_ladder_status(_codex_burst_ladder_status_payload(Config.load()))


@codex_strategy.command("disable")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def codex_strategy_disable(as_json: bool):
    """Disable Codex burst ladder dispatch and use the patient adaptive ladder."""
    if _app_json_requested(as_json):
        _run_codex_strategy_mutation_json(
            "disable",
            lambda: _save_config_like(Config.load(), codex_burst_ladder_enabled=False) or Config.load(),
        )
        return
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_enabled=False)
    console.print("[green]Codex burst ladder disabled; patient adaptive ladder is active.[/green]")
    _render_codex_burst_ladder_status(_codex_burst_ladder_status_payload(Config.load()))


@codex_strategy.command("order")
@click.argument("surfaces", nargs=-1)
@click.option("--reset", "reset_order", is_flag=True, help="Reset to the default burst order")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def codex_strategy_order(surfaces: tuple[str, ...], reset_order: bool, as_json: bool):
    """Set burst ladder surface order/subset, e.g. tk codex-strategy order repo legacy."""
    if _app_json_requested(as_json):
        if reset_order and surfaces:
            emit_app_error(ERROR_USAGE, "Use either surfaces or --reset, not both.")
            sys.exit(2)
        if reset_order:
            _run_codex_strategy_mutation_json(
                "order_reset",
                lambda: _save_config_like(Config.load(), codex_burst_ladder_surface_order=[]) or Config.load(),
            )
            return
        if surfaces:
            try:
                order = _parse_codex_fire_all_surface_order(list(surfaces))
            except click.ClickException as exc:
                emit_app_error(ERROR_USAGE, exc.message)
                sys.exit(2)
            if not order:
                emit_app_error(ERROR_USAGE, "Provide at least one surface or use --reset.")
                sys.exit(2)
            _run_codex_strategy_mutation_json(
                "order_set",
                lambda: _save_config_like(Config.load(), codex_burst_ladder_surface_order=order) or Config.load(),
            )
            return
        emit_app_success(
            {
                "action": "status",
                "codex_strategy": _codex_burst_ladder_status_payload(Config.load()),
            }
        )
        return
    config = Config.load()
    if reset_order and surfaces:
        raise click.ClickException("Use either surfaces or --reset, not both.")
    if reset_order:
        _save_config_like(config, codex_burst_ladder_surface_order=[])
        console.print("[green]Codex burst ladder surface order reset to default.[/green]")
    elif surfaces:
        order = _parse_codex_fire_all_surface_order(list(surfaces))
        if not order:
            raise click.ClickException("Provide at least one surface or use --reset.")
        _save_config_like(config, codex_burst_ladder_surface_order=order)
        console.print(f"[green]Codex burst ladder surface order set: {', '.join(order)}.[/green]")
    else:
        _render_codex_burst_ladder_status(_codex_burst_ladder_status_payload(config))
        return
    _render_codex_burst_ladder_status(_codex_burst_ladder_status_payload(Config.load()))


@codex_strategy.command("gap")
@click.argument("seconds", type=float)
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def codex_strategy_gap(seconds: float, as_json: bool):
    """Set seconds to wait between serialized burst ladder surfaces."""
    if seconds < 0:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, "Gap seconds must be >= 0.")
            sys.exit(2)
        raise click.ClickException("Gap seconds must be >= 0.")
    if _app_json_requested(as_json):
        _run_codex_strategy_mutation_json(
            "gap_set",
            lambda: _save_config_like(Config.load(), codex_burst_ladder_gap_seconds=int(seconds)) or Config.load(),
        )
        return
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_gap_seconds=int(seconds))
    console.print(f"[green]Codex burst ladder surface gap set to {int(seconds)}s.[/green]")
    _render_codex_burst_ladder_status(_codex_burst_ladder_status_payload(Config.load()))


@codex_strategy.group("demotion")
@click.pass_context
def codex_strategy_demotion(ctx: click.Context):
    """Configure per-account surface demotion for Codex strategy."""
    ctx.ensure_object(dict)


@codex_strategy_demotion.command("enable")
@click.option("--all", "all_accounts", is_flag=True, help="Apply to all Codex accounts.")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.argument("label", required=False)
@click.pass_context
def codex_strategy_demotion_enable(ctx: click.Context, all_accounts: bool, as_json: bool, label: str | None):
    if _app_json_requested(as_json):
        try:
            _set_codex_strategy_demotion_scope(ctx, label=label, all_accounts=all_accounts, command_name="enable")
        except click.ClickException as exc:
            emit_app_error(ERROR_USAGE, exc.message)
            sys.exit(2)
        if all_accounts:
            _run_codex_strategy_mutation_json(
                "demotion_enable_all",
                lambda: _set_all_codex_surface_demotion_config(True),
            )
            return
        _run_codex_surface_mutation_json(
            "demotion_enable",
            label or "",
            lambda _account: _set_codex_surface_demotion_config(label or "", codex_surface_auto_demote=True),
        )
        return
    _set_codex_strategy_demotion_scope(ctx, label=label, all_accounts=all_accounts, command_name="enable")
    if all_accounts:
        count = _set_all_codex_surface_demotion_config(True)
        console.print(f"[green]Codex surface auto-demotion enabled for {count} Codex accounts.[/green]")
        return
    codex_surfaces_demotion_enable(ctx)


@codex_strategy_demotion.command("disable")
@click.option("--all", "all_accounts", is_flag=True, help="Apply to all Codex accounts.")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.argument("label", required=False)
@click.pass_context
def codex_strategy_demotion_disable(ctx: click.Context, all_accounts: bool, as_json: bool, label: str | None):
    if _app_json_requested(as_json):
        try:
            _set_codex_strategy_demotion_scope(ctx, label=label, all_accounts=all_accounts, command_name="disable")
        except click.ClickException as exc:
            emit_app_error(ERROR_USAGE, exc.message)
            sys.exit(2)
        if all_accounts:
            _run_codex_strategy_mutation_json(
                "demotion_disable_all",
                lambda: _set_all_codex_surface_demotion_config(False),
            )
            return
        _run_codex_surface_mutation_json(
            "demotion_disable",
            label or "",
            lambda _account: _set_codex_surface_demotion_config(label or "", codex_surface_auto_demote=False),
        )
        return
    _set_codex_strategy_demotion_scope(ctx, label=label, all_accounts=all_accounts, command_name="disable")
    if all_accounts:
        count = _set_all_codex_surface_demotion_config(False)
        console.print(f"[green]Codex surface auto-demotion disabled for {count} Codex accounts.[/green]")
        return
    codex_surfaces_demotion_disable(ctx)


@codex_strategy_demotion.command("set")
@click.argument("label")
@click.option("--after-strong-clusters", type=click.IntRange(1, 100), default=None)
@click.option("--min-active-surfaces", type=click.IntRange(1, len(DEFAULT_CODEX_SURFACE_ORDER)), default=None)
@click.option("--min-kept-anchor-rate", type=click.FloatRange(0.0, 1.0), default=None)
@click.option("--measurement-clusters", type=click.IntRange(1, 200), default=None)
@click.option("--rescue-cooldown-strong-clusters", type=click.IntRange(0, 200), default=None)
@click.pass_context
def codex_strategy_demotion_set(
    ctx: click.Context,
    label: str,
    after_strong_clusters: int | None,
    min_active_surfaces: int | None,
    min_kept_anchor_rate: float | None,
    measurement_clusters: int | None,
    rescue_cooldown_strong_clusters: int | None,
):
    ctx.obj["codex_surfaces_label"] = label
    codex_surfaces_demotion_set(
        ctx,
        after_strong_clusters,
        min_active_surfaces,
        min_kept_anchor_rate,
        measurement_clusters,
        rescue_cooldown_strong_clusters,
    )


@codex_strategy_demotion.command("force-keep")
@click.argument("label")
@click.argument("surfaces", nargs=-1, required=True)
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_strategy_demotion_force_keep(
    ctx: click.Context,
    label: str,
    surfaces: tuple[str, ...],
    as_json: bool,
):
    if _app_json_requested(as_json):
        try:
            order = _parse_codex_fire_all_surface_order(list(surfaces))
        except click.ClickException as exc:
            emit_app_error(ERROR_USAGE, exc.message)
            sys.exit(2)
        _run_codex_surface_mutation_json(
            "force_keep",
            label,
            lambda _account: _set_codex_surface_demotion_config(label, codex_surface_force_keep=order),
        )
        return
    ctx.obj["codex_surfaces_label"] = label
    codex_surfaces_demotion_force_keep(ctx, surfaces)


@codex_strategy_demotion.command("force-prune")
@click.argument("label")
@click.argument("surfaces", nargs=-1, required=True)
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_strategy_demotion_force_prune(
    ctx: click.Context,
    label: str,
    surfaces: tuple[str, ...],
    as_json: bool,
):
    if _app_json_requested(as_json):
        try:
            order = _parse_codex_fire_all_surface_order(list(surfaces))
        except click.ClickException as exc:
            emit_app_error(ERROR_USAGE, exc.message)
            sys.exit(2)
        if len(order) >= len(DEFAULT_CODEX_SURFACE_ORDER):
            emit_app_error(ERROR_USAGE, "Cannot force-prune every Codex surface.")
            sys.exit(2)
        _run_codex_surface_mutation_json(
            "force_prune",
            label,
            lambda _account: _set_codex_surface_demotion_config(label, codex_surface_force_prune=order),
        )
        return
    ctx.obj["codex_surfaces_label"] = label
    codex_surfaces_demotion_force_prune(ctx, surfaces)


@codex_strategy_demotion.command("clear-overrides")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_strategy_demotion_clear_overrides(ctx: click.Context, label: str, as_json: bool):
    if _app_json_requested(as_json):
        _run_codex_surface_mutation_json(
            "clear_overrides",
            label,
            lambda _account: _set_codex_surface_demotion_config(
                label,
                codex_surface_force_keep=[],
                codex_surface_force_prune=[],
            ),
        )
        return
    ctx.obj["codex_surfaces_label"] = label
    codex_surfaces_demotion_clear_overrides(ctx)


@codex_strategy_demotion.command("evidence")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def codex_strategy_demotion_evidence(ctx: click.Context, label: str, as_json: bool):
    ctx.obj["codex_surfaces_label"] = label
    ctx.invoke(codex_surfaces_demotion_evidence, as_json=as_json)


@codex_strategy_demotion.command("reset-evidence")
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_strategy_demotion_reset_evidence(ctx: click.Context, label: str, as_json: bool):
    if _app_json_requested(as_json):
        _run_codex_surface_mutation_json(
            "reset_evidence",
            label,
            lambda account: reset_codex_surface_demotion_evidence(_codex_surface_stats_file(), account),
        )
        return
    ctx.obj["codex_surfaces_label"] = label
    codex_surfaces_demotion_reset_evidence(ctx)


def _set_codex_strategy_demotion_scope(
    ctx: click.Context,
    *,
    label: str | None,
    all_accounts: bool,
    command_name: str,
) -> None:
    if all_accounts and label:
        raise click.ClickException(f"Use either --all or LABEL with demotion {command_name}, not both.")
    if not all_accounts and not label:
        raise click.ClickException(f"Provide a Codex account LABEL, or use --all with demotion {command_name}.")
    if label is not None:
        ctx.obj["codex_surfaces_label"] = label


def _codex_strategy_mutation_payload(action: str) -> dict:
    return {
        "action": action,
        "codex_strategy": _codex_burst_ladder_status_payload(Config.load()),
    }


def _codex_surfaces_mutation_payload(action: str, account: AccountConfig) -> dict:
    report = codex_surface_stats_for_account(account, _codex_surface_stats_file())
    report["provider_home"] = account.provider_home
    return {
        "action": action,
        "account": account.label,
        "codex_surfaces": report,
        "codex_strategy": _codex_burst_ladder_status_payload(Config.load()),
    }


def _run_codex_strategy_mutation_json(action: str, mutator: Callable[[], object | None]) -> None:
    _run_mutation_json(mutator, lambda _updated: _codex_strategy_mutation_payload(action))


def _run_codex_surface_mutation_json(
    action: str,
    label: str,
    mutator: Callable[[AccountConfig], object | None],
) -> None:
    def run() -> AccountConfig | None:
        config = Config.load()
        account = _codex_direct_account_or_exit(label, _load_accounts(config))
        mutator(account)
        return _codex_direct_account_or_exit(label, _load_accounts(Config.load()))

    _run_mutation_json(run, lambda updated: _codex_surfaces_mutation_payload(action, updated))


@cli.group("codex-fire-all", invoke_without_command=True)
@click.pass_context
def codex_fire_all(ctx):
    """Deprecated alias for Codex burst ladder strategy."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(codex_fire_all_status)


@codex_fire_all.command("status")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def codex_fire_all_status(as_json: bool):
    """Show deprecated fire-all alias status."""
    config = Config.load()
    payload = _codex_fire_all_status_payload(config)
    payload["deprecated_alias"] = True
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    console.print("[yellow]Deprecated: use tk codex-strategy status.[/yellow]")
    _render_codex_fire_all_status(payload)


@codex_fire_all.command("enable")
def codex_fire_all_enable():
    """Deprecated alias: enable Codex burst ladder dispatch."""
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_enabled=True)
    console.print("[yellow]Deprecated: use tk codex-strategy enable.[/yellow]")
    console.print("[green]Codex burst ladder enabled for auto/scheduled kicks.[/green]")
    _render_codex_fire_all_status(_codex_fire_all_status_payload(Config.load()))


@codex_fire_all.command("disable")
def codex_fire_all_disable():
    """Deprecated alias: disable Codex burst ladder dispatch."""
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_enabled=False)
    console.print("[yellow]Deprecated: use tk codex-strategy disable.[/yellow]")
    console.print("[green]Codex burst ladder disabled; patient adaptive ladder is active.[/green]")
    _render_codex_fire_all_status(_codex_fire_all_status_payload(Config.load()))


@codex_fire_all.command("order")
@click.argument("surfaces", nargs=-1)
@click.option("--reset", "reset_order", is_flag=True, help="Reset to the default burst ladder order")
def codex_fire_all_order(surfaces: tuple[str, ...], reset_order: bool):
    """Deprecated alias: set burst ladder surface order/subset."""
    config = Config.load()
    if reset_order and surfaces:
        raise click.ClickException("Use either surfaces or --reset, not both.")
    if reset_order:
        _save_config_like(config, codex_burst_ladder_surface_order=[])
        console.print("[yellow]Deprecated: use tk codex-strategy order.[/yellow]")
        console.print("[green]Codex burst ladder surface order reset to default.[/green]")
    elif surfaces:
        order = _parse_codex_fire_all_surface_order(list(surfaces))
        if not order:
            raise click.ClickException("Provide at least one surface or use --reset.")
        _save_config_like(config, codex_burst_ladder_surface_order=order)
        console.print("[yellow]Deprecated: use tk codex-strategy order.[/yellow]")
        console.print(f"[green]Codex burst ladder surface order set: {', '.join(order)}.[/green]")
    else:
        _render_codex_fire_all_status(_codex_fire_all_status_payload(config))
        return
    _render_codex_fire_all_status(_codex_fire_all_status_payload(Config.load()))


@codex_fire_all.command("gap")
@click.argument("seconds", type=float)
def codex_fire_all_gap(seconds: float):
    """Deprecated alias: set burst ladder inter-surface gap."""
    if seconds < 0:
        raise click.ClickException("Gap seconds must be >= 0.")
    config = Config.load()
    _save_config_like(config, codex_burst_ladder_gap_seconds=int(seconds))
    console.print("[yellow]Deprecated: use tk codex-strategy gap.[/yellow]")
    console.print(f"[green]Codex burst ladder surface gap set to {int(seconds)}s.[/green]")
    _render_codex_fire_all_status(_codex_fire_all_status_payload(Config.load()))


def _codex_burst_ladder_status_payload(config: Config) -> dict:
    accounts = _load_accounts(config)
    active_order = _codex_burst_ladder_surface_order(config)
    demotion = _codex_surface_demotion_status_payload(accounts)
    effective = _codex_effective_kicking_order_status_payload(accounts, config)
    return {
        "schema_version": 1,
        "strategy": "burst_ladder" if _codex_burst_ladder_enabled(config) else "patient_adaptive_ladder",
        "enabled": _codex_burst_ladder_enabled(config),
        "config_enabled": config.codex_burst_ladder_enabled,
        "active_order": list(active_order),
        "effective_kicking_order": effective.get("order", []),
        "effective_kicking_order_summary": effective.get("summary", "unknown"),
        "effective_kicking_order_by_account": effective.get("by_account", {}),
        "effective_kicking_order_errors": effective.get("errors", {}),
        "configured_order": list(config.codex_burst_ladder_surface_order),
        "default_order": list(CODEX_FIRE_ALL_DEFAULT_SURFACES),
        "active_gap_seconds": _codex_burst_ladder_gap_seconds(config),
        "configured_gap_seconds": config.codex_burst_ladder_gap_seconds,
        "auto_demotion": demotion,
        "applies_to": "auto/scheduled Codex kicks only",
        "enabled_behavior": "Burst ladder fires the configured set at the gap with no early-stop.",
        "disabled_behavior": "Patient adaptive ladder uses verified retries and the 900s retry backoff.",
    }


def _codex_effective_kicking_order_status_payload(accounts: list[AccountConfig], config: Config) -> dict:
    codex_accounts = [account for account in accounts if account.provider == "codex"]
    if not codex_accounts:
        return {
            "state": "none",
            "summary": "no Codex accounts",
            "order": [],
            "by_account": {},
            "errors": {},
        }
    by_account: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for account in codex_accounts:
        try:
            by_account[account.label] = list(_effective_codex_burst_ladder_surfaces(account, config))
        except click.ClickException as exc:
            errors[account.label] = exc.message
    unique_orders = {tuple(order) for order in by_account.values()}
    if errors and not unique_orders:
        return {
            "state": "error",
            "summary": "unavailable: no account has an active surface set",
            "order": [],
            "by_account": by_account,
            "errors": errors,
        }
    if len(unique_orders) == 1 and not errors:
        order = list(next(iter(unique_orders)))
        return {
            "state": "common",
            "summary": ", ".join(order),
            "order": order,
            "by_account": by_account,
            "errors": {},
        }
    summary_parts = [f"{label}: {', '.join(order)}" for label, order in by_account.items()]
    summary_parts.extend(f"{label}: unavailable" for label in errors)
    return {
        "state": "varies",
        "summary": "varies by account; " + "; ".join(summary_parts),
        "order": [],
        "by_account": by_account,
        "errors": errors,
    }


def _codex_surface_demotion_status_payload(accounts: list[AccountConfig]) -> dict:
    codex_accounts = [account for account in accounts if account.provider == "codex"]
    enabled = [account for account in codex_accounts if account.codex_surface_auto_demote]
    disabled = [account for account in codex_accounts if not account.codex_surface_auto_demote]
    if not codex_accounts:
        state = "none"
        summary = "no Codex accounts"
    elif enabled and disabled:
        state = "mixed"
        summary = f"mixed ({len(enabled)} on, {len(disabled)} off)"
    elif enabled:
        state = "all_on"
        summary = f"all on ({len(enabled)}/{len(codex_accounts)} Codex accounts)"
    else:
        state = "all_off"
        summary = f"all off (0/{len(codex_accounts)} Codex accounts)"
    return {
        "state": state,
        "summary": summary,
        "enabled_count": len(enabled),
        "disabled_count": len(disabled),
        "total_codex_accounts": len(codex_accounts),
        "enabled_labels": [account.label for account in enabled],
        "disabled_labels": [account.label for account in disabled],
    }


def _codex_fire_all_status_payload(config: Config) -> dict:
    return _codex_burst_ladder_status_payload(config)


def _render_codex_burst_ladder_status(payload: dict) -> None:
    table = Table(title="Codex Surface Strategy", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Strategy", str(payload.get("strategy")))
    table.add_row("Burst ladder enabled", "yes" if payload.get("enabled") else "no")
    demotion = payload.get("auto_demotion") if isinstance(payload.get("auto_demotion"), dict) else {}
    table.add_row("Auto-demotion", str(demotion.get("summary") or "unknown"))
    table.add_row("Effective kicking order", str(payload.get("effective_kicking_order_summary") or "unknown"))
    configured_order = payload.get("configured_order") or []
    table.add_row("Configured order", ", ".join(configured_order) if configured_order else "default")
    table.add_row("Default order", ", ".join(payload.get("default_order") or []))
    table.add_row("Burst gap", f"{float(payload.get('active_gap_seconds') or 0.0):.0f}s")
    table.add_row("Applies to", str(payload.get("applies_to")))
    table.add_row(
        "Behavior",
        str(payload.get("enabled_behavior") if payload.get("enabled") else payload.get("disabled_behavior")),
    )
    console.print(table)


def _render_codex_fire_all_status(payload: dict) -> None:
    _render_codex_burst_ladder_status(payload)


# ---------------------------------------------------------------------------
# tk schedule
# ---------------------------------------------------------------------------

@cli.group(invoke_without_command=True)
@click.pass_context
def schedule(ctx):
    """Manage smart kick scheduling."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(schedule_show)


def _validate_schedule_target(account: str | None, use_default: bool, *, as_json: bool = False) -> bool:
    if bool(account) == bool(use_default):
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, "Choose exactly one: --account <label> or --default.")
            sys.exit(2)
        console.print("[red]Choose exactly one: --account <label> or --default.[/red]")
        return False
    return True


def _validate_work_window(
    value: str | None,
    timezone_name: str | None,
    *,
    as_json: bool = False,
) -> bool:
    if value is None:
        return True
    try:
        tz = ZoneInfo(timezone_name) if timezone_name else datetime.now().astimezone().tzinfo
        parse_work_window(value, datetime.now().date(), tz)
        return True
    except ZoneInfoNotFoundError:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, f'Invalid work window "{value}". Unknown timezone "{timezone_name}".')
            sys.exit(2)
        console.print(f'[red]Invalid work window "{value}". Unknown timezone "{timezone_name}".[/red]')
        return False
    except ValueError as exc:
        if _app_json_requested(as_json):
            emit_app_error(ERROR_USAGE, f'Invalid work window "{value}": {exc}.')
            sys.exit(2)
        console.print(f'[red]Invalid work window "{value}": {exc}.[/red]')
        return False


def _schedule_config_with_tz(
    config: Config,
    timezone_name: str | None,
    *,
    as_json: bool = False,
) -> ScheduleConfig | None:
    schedule_config = config.schedule
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if _app_json_requested(as_json):
                emit_app_error(ERROR_USAGE, f'Unknown timezone "{timezone_name}".')
                sys.exit(2)
            console.print(f'[red]Unknown timezone "{timezone_name}".[/red]')
            raise click.Abort()
        schedule_config.timezone = timezone_name
    return schedule_config


def _save_schedule_config(config: Config, schedule_config: ScheduleConfig) -> None:
    _save_config_like(config, schedule=schedule_config)


@schedule.command("set")
@click.option("--account", metavar="LABEL")
@click.option("--default", "set_default", is_flag=True, help="Set the global default schedule")
@click.option("--weekdays", metavar="HH:MM-HH:MM")
@click.option("--weekends", metavar="HH:MM-HH:MM")
@click.option("--timezone", "timezone_name", metavar="IANA_TZ")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def schedule_set(
    account: str | None,
    set_default: bool,
    weekdays: str | None,
    weekends: str | None,
    timezone_name: str | None,
    as_json: bool,
):
    """Set smart kick scheduling windows."""
    if not _validate_schedule_target(account, set_default, as_json=as_json):
        return
    config = Config.load()
    schedule_config = _schedule_config_with_tz(config, timezone_name, as_json=as_json)
    if schedule_config is None:
        return
    if not _validate_work_window(weekdays, timezone_name or config.schedule.timezone, as_json=as_json):
        return
    if not _validate_work_window(weekends, timezone_name or config.schedule.timezone, as_json=as_json):
        return
    schedule_config.enabled = True

    target = schedule_config.default if set_default else schedule_config.accounts.get(account, WorkSchedule())
    target.enabled = True
    if weekdays is not None:
        target.weekdays = weekdays
    if weekends is not None:
        target.weekends = weekends
    if account:
        schedule_config.accounts[account] = target
        removed = _invalidate_smart_schedule_pending_kicks(account_label=account, quiet=as_json)
    else:
        schedule_config.default = target
        removed = _invalidate_smart_schedule_pending_kicks(quiet=as_json)
    _save_schedule_config(config, schedule_config)
    if _app_json_requested(as_json):
        pending = load_pending_kicks(datetime.now(timezone.utc))
        emit_app_success(
            {
                "action": "set",
                "scope": account or "default",
                "removed_pending_kicks": [item.to_dict() for item in removed],
                "schedule": _schedule_show_payload(config, pending, account),
            },
            message=(
                f'Smart scheduling enabled for "{account}".'
                if account
                else "Default smart scheduling enabled."
            ),
        )
        return
    if account:
        console.print(f'[green]Smart scheduling enabled for "{account}".[/green]')
    else:
        console.print("[green]Default smart scheduling enabled.[/green]")


def _invalidate_smart_schedule_pending_kicks(
    account_label: str | None = None,
    *,
    quiet: bool = False,
) -> list[PendingKick]:
    """Invalidate non-orchestrated pending kicks for a schedule change and report it.

    Orchestrated pending kicks belong to applied plans; `tk plan cancel` is the
    path for removing those.
    """
    removed = invalidate_pending_kicks(
        account_label=account_label,
        exclude_orchestrated=True,
    )
    if removed and not quiet:
        details = ", ".join(
            f"{item.account_label} ({_format_pending_kick_cell(item)})"
            for item in sorted(removed, key=lambda item: (item.kick_at, item.account_label))
        )
        noun = "kick" if len(removed) == 1 else "kicks"
        console.print(
            f"[yellow]Removed {len(removed)} smart-schedule pending {noun}: {details}.[/yellow]"
        )
    kept_orchestrated = [
        item
        for item in load_pending_kicks(datetime.now(timezone.utc)).values()
        if item.reason == ScheduleReason.ORCHESTRATED.value
        and (account_label is None or item.account_label == account_label)
    ]
    if kept_orchestrated and not quiet:
        labels = ", ".join(sorted({item.account_label for item in kept_orchestrated}))
        noun = "kick" if len(kept_orchestrated) == 1 else "kicks"
        console.print(
            f"[dim]Kept {len(kept_orchestrated)} orchestrated pending {noun} for {labels}; "
            "use tk plan cancel to remove them.[/dim]"
        )
    return removed


@schedule.command("show")
@click.option("--account", metavar="LABEL")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def schedule_show(account: str | None = None, as_json: bool = False):
    """Show smart scheduling configuration and pending kicks."""
    config = Config.load()
    pending = load_pending_kicks(datetime.now(timezone.utc))
    if _app_json_requested(as_json):
        emit_app_success(_schedule_show_payload(config, pending, account))
        return
    table = Table(title="TokenKick Smart Schedule", show_header=True)
    table.add_column("Scope", style="bold")
    table.add_column("Enabled")
    table.add_column("Weekdays")
    table.add_column("Weekends")
    table.add_column("Next kick", style="dim")
    table.add_column("Status")

    rows: list[tuple[str, WorkSchedule]] = []
    if account:
        schedule_value = config.schedule.accounts.get(account)
        if schedule_value is None:
            console.print(f'[dim]No account schedule for "{account}".[/dim]')
            _render_pending_kicks_section(pending, account_label=account)
            _print_report_timestamp("Schedule printed at")
            return
        rows.append((account, schedule_value))
    else:
        rows.append(("default", config.schedule.default))
        rows.extend(sorted(config.schedule.accounts.items()))

    cached = _load_status_cache(config)
    statuses_by_label = {
        status.label: status
        for status in (cached[1] if cached is not None else [])
    }

    for scope, schedule_value in rows:
        next_kick = "—"
        status_text = "—"
        for pending_kick in pending.values():
            if pending_kick.account_label == scope:
                next_kick = _format_pending_kick_cell(pending_kick)
                cached_status = statuses_by_label.get(scope)
                if pending_kick.gave_up_at:
                    status_text = f"gave up after {pending_kick.attempt_count} attempts"
                elif pending_kick.next_retry_at:
                    status_text = f"retrying after failure {pending_kick.attempt_count}"
                elif cached_status is not None and cached_status.stale:
                    status_text = f"blocked: {_stale_status_reason(cached_status)}"
                elif pending_kick.purpose != PENDING_KICK_PURPOSE_COVERAGE:
                    status_text = _format_kick_purpose(pending_kick.purpose)
                break
        table.add_row(
            scope,
            "yes" if config.schedule.enabled and schedule_value.enabled else "no",
            schedule_value.weekdays or "—",
            schedule_value.weekends or "—",
            next_kick,
            status_text,
        )
    console.print(table)
    _render_pending_kicks_section(pending, account_label=account)
    _print_report_timestamp("Schedule printed at")


def _render_pending_kicks_section(
    pending: dict[str, PendingKick],
    *,
    account_label: str | None = None,
) -> None:
    items = [
        item
        for item in pending.values()
        if account_label is None or item.account_label == account_label
    ]
    if not items:
        console.print("[dim]No pending kicks.[/dim]")
        return
    items.sort(key=lambda item: (item.kick_at, item.account_label, item.purpose))
    table = Table(title="Pending kicks", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Reason")
    table.add_column("Purpose")
    table.add_column("Kick at", no_wrap=True)
    table.add_column("Status")
    for item in items:
        table.add_row(
            item.account_label,
            item.reason.replace("_", " "),
            _format_kick_purpose(item.purpose),
            _format_pending_kick_cell(item),
            _format_pending_kick_section_status(item),
        )
    console.print(table)


def _format_pending_kick_section_status(pending_kick: PendingKick) -> str:
    if pending_kick.gave_up_at:
        return f"gave up after {pending_kick.attempt_count} attempts"
    if pending_kick.next_retry_at:
        status = f"retrying after failure {pending_kick.attempt_count}"
        try:
            retry_at = from_utc_iso(pending_kick.next_retry_at).astimezone()
        except ValueError:
            return status
        return f"{status}; next attempt {retry_at.strftime('%H:%M %Z')}"
    return "scheduled"


@schedule.command("clear")
@click.option("--account", metavar="LABEL")
@click.option("--default", "clear_default", is_flag=True, help="Clear the global default schedule")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def schedule_clear(account: str | None, clear_default: bool, as_json: bool):
    """Clear smart scheduling for an account or the default."""
    if not _validate_schedule_target(account, clear_default, as_json=as_json):
        return
    config = Config.load()
    if account:
        config.schedule.accounts.pop(account, None)
        removed = _invalidate_smart_schedule_pending_kicks(account_label=account, quiet=as_json)
    else:
        config.schedule.default = WorkSchedule()
        removed = _invalidate_smart_schedule_pending_kicks(quiet=as_json)
    _save_schedule_config(config, config.schedule)
    if _app_json_requested(as_json):
        pending = load_pending_kicks(datetime.now(timezone.utc))
        emit_app_success(
            {
                "action": "clear",
                "scope": account or "default",
                "removed_pending_kicks": [item.to_dict() for item in removed],
                "schedule": _schedule_show_payload(config, pending, account),
            },
            message=(
                f'Cleared smart scheduling for "{account}".'
                if account
                else "Cleared default smart scheduling."
            ),
        )
        return
    if account:
        console.print(f'[green]Cleared smart scheduling for "{account}".[/green]')
    else:
        console.print("[green]Cleared default smart scheduling.[/green]")


@schedule.command("disable")
@click.option("--account", metavar="LABEL")
@click.option("--default", "disable_default", is_flag=True, help="Disable the global default schedule")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def schedule_disable(account: str | None, disable_default: bool, as_json: bool):
    """Disable smart scheduling while keeping configured windows."""
    if not _validate_schedule_target(account, disable_default, as_json=as_json):
        return
    config = Config.load()
    if account:
        schedule_value = config.schedule.accounts.get(account)
        if schedule_value is None:
            schedule_value = WorkSchedule()
        schedule_value.enabled = False
        config.schedule.accounts[account] = schedule_value
        removed = _invalidate_smart_schedule_pending_kicks(account_label=account, quiet=as_json)
    else:
        config.schedule.default.enabled = False
        removed = _invalidate_smart_schedule_pending_kicks(quiet=as_json)
    _save_schedule_config(config, config.schedule)
    if _app_json_requested(as_json):
        pending = load_pending_kicks(datetime.now(timezone.utc))
        emit_app_success(
            {
                "action": "disable",
                "scope": account or "default",
                "removed_pending_kicks": [item.to_dict() for item in removed],
                "schedule": _schedule_show_payload(config, pending, account),
            },
            message=(
                f'Smart scheduling disabled for "{account}".'
                if account
                else "Default smart scheduling disabled."
            ),
        )
        return
    if account:
        console.print(f'[green]Smart scheduling disabled for "{account}".[/green]')
    else:
        console.print("[green]Default smart scheduling disabled.[/green]")


# ---------------------------------------------------------------------------
# tk history
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--limit", default=20, help="Number of events to show")
@click.option("--account", "account_label", help="Only show history for this account label")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--verbose", "verbose", is_flag=True, help="Show detailed kick evidence columns")
@click.option("--include-probes", is_flag=True, help="Include background provider status probes")
@click.option("--kind", "kind_filter", help="Only show one event kind, such as session or status_probe")
@click.option("--anchored", "anchored_only", is_flag=True, help="Only show confirmed moved anchors")
def history(
    limit: int,
    account_label: str | None,
    as_json: bool,
    verbose: bool,
    include_probes: bool,
    kind_filter: str | None,
    anchored_only: bool,
):
    """Show recent kick history."""
    events = load_kick_history(limit=_history_load_limit(limit, include_probes, kind_filter, anchored_only))
    if account_label:
        labels = _history_filter_labels(account_label, Config.load().accounts)
        events = [event for event in events if event.label in labels]
    events = _filter_history_events(
        events,
        limit,
        include_probes=include_probes,
        kind_filter=kind_filter,
        anchored_only=anchored_only,
    )

    if as_json:
        # Always valid JSON on stdout, including the empty case, so the app
        # can decode every answer.
        click.echo(json.dumps([e.to_dict() for e in events], indent=2))
        return

    if not events:
        if account_label:
            console.print(f'[dim]No kick history for "{account_label}".[/dim]')
        else:
            console.print("[dim]No kick history yet.[/dim]")
        return

    if verbose:
        _render_history_verbose(events)
        _print_report_timestamp("History printed at")
        return

    table = Table(title=_history_table_title(), show_header=True, expand=True)
    table.add_column("Time", no_wrap=True)
    table.add_column("Account", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("Result", no_wrap=True)
    table.add_column("Details", no_wrap=True, overflow="ellipsis", ratio=5)

    for event in reversed(events):
        result = _history_event_result(event)
        table.add_row(
            _history_event_compact_time(event),
            event.label,
            result,
            _history_event_compact_details(event)[:160],
        )

    _history_console().print(table)
    _print_report_timestamp("History printed at")


def _history_load_limit(
    limit: int,
    include_probes: bool,
    kind_filter: str | None,
    anchored_only: bool = False,
) -> int:
    if ((include_probes or kind_filter) and not anchored_only) or limit <= 0:
        return limit
    return max(limit * 10, 200)


def _filter_history_events(
    events: list[KickEvent],
    limit: int,
    *,
    include_probes: bool,
    kind_filter: str | None,
    anchored_only: bool = False,
) -> list[KickEvent]:
    if kind_filter:
        events = [event for event in events if _history_event_kind(event) == kind_filter]
    elif not include_probes:
        events = [event for event in events if not _history_event_is_status_probe(event)]
    if anchored_only:
        events = [event for event in events if _history_event_is_anchored(event)]
    if limit <= 0:
        return []
    return events[-limit:]


def _history_event_kind(event: KickEvent) -> str:
    return event.kick_type or event.kind


def _history_event_is_status_probe(event: KickEvent) -> bool:
    return _history_event_kind(event) == "status_probe"


def _history_event_is_anchored(event: KickEvent) -> bool:
    return event.success and event.confirmed and event.post_kick_status == "moved"


def _history_table_title() -> Text:
    return Text.assemble(("Kick", "bold green"), (" History", "bold white"))


def _history_console() -> Console:
    width = max(160, shutil.get_terminal_size((160, 24)).columns)
    return Console(width=width, stderr=app_mode_enabled())


def _render_history_verbose(events: list[KickEvent]) -> None:
    table = Table(title=_history_table_title(), show_header=True, expand=True)
    table.add_column("Time", no_wrap=True, width=16)
    table.add_column("Account", no_wrap=True, width=20, overflow="ellipsis")
    table.add_column("Result", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Surface", no_wrap=True, min_width=7)
    table.add_column("Attempt", no_wrap=True)
    table.add_column("Evidence", ratio=5, overflow="fold")
    table.add_column("Details", min_width=14, ratio=2, overflow="fold")

    for event in reversed(events):
        table.add_row(
            _history_event_display_time(event),
            event.label,
            _history_event_result(event),
            _history_event_kind(event),
            event.codex_surface or "—",
            _kick_event_attempt_summary(event) or "—",
            _kick_event_evidence_summary(event) or "—",
            _history_event_base_details(event)[:180],
        )

    _history_console().print(table)


def _history_event_result(event: KickEvent) -> str:
    if not event.success:
        return "[red]✗[/red]"
    if not event.confirmed:
        return "[yellow]~[/yellow]"
    return "[green]✓[/green]"


def _history_event_details(event: KickEvent) -> str:
    base_details = _history_event_base_details(event)
    context = _history_event_codex_context(event)
    if context:
        return f"{context}; {base_details}"
    return base_details


def _history_event_compact_time(event: KickEvent) -> str:
    return _history_event_display_time(event)


def _history_event_display_time(event: KickEvent) -> str:
    return _history_timestamp_without_timezone(event.to_dict()["timestamp_local"])


def _history_timestamp_without_timezone(timestamp: str) -> str:
    parts = timestamp.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isalpha():
        return parts[0]
    return timestamp


def _history_event_compact_details(event: KickEvent) -> str:
    parts = []
    if event.codex_surface:
        parts.append(event.codex_surface)
    attempt = _kick_event_attempt_summary(event)
    if attempt:
        parts.append(attempt)
    evidence = _kick_event_compact_evidence(event)
    if evidence:
        parts.append(evidence)
    method = _compact_codex_method(event.codex_confirmation_method)
    if method:
        parts.append(method)
    if event.codex_attribution and event.codex_attribution != CODEX_ATTRIBUTION_STRONG:
        parts.append(_compact_codex_attribution(event.codex_attribution))
    if event.codex_provider_stale:
        parts.append("stale")
    if event.post_kick_status and event.post_kick_status != "moved":
        parts.append(event.post_kick_status)

    base_details = _history_event_base_details(event)
    if _compact_history_should_include_base(event, base_details):
        parts.append(base_details)
    return "; ".join(parts) if parts else base_details


def _kick_event_compact_evidence(event: KickEvent) -> str:
    parts = []
    if event.evidence_response is not None:
        parts.append(f"r{_bool_icon(event.evidence_response)}")
    if event.evidence_tokens is not None:
        parts.append(f"t{_bool_icon(event.evidence_tokens)}")
    if event.evidence_provider_moved is not None:
        parts.append(f"m{_bool_icon(event.evidence_provider_moved)}")
    return " ".join(parts)


def _compact_codex_method(method: str | None) -> str:
    if not method:
        return ""
    return {
        "pending_reset_clock": "pending",
        "late_reset_clock": "late",
        "reset_clock": "reset",
        "provider_moved": "moved",
    }.get(method, method)


def _compact_codex_attribution(attribution: str) -> str:
    return {
        CODEX_ATTRIBUTION_TIMING_MATCH: "timing",
        CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE: "external",
    }.get(attribution, attribution)


def _compact_history_should_include_base(event: KickEvent, base_details: str) -> bool:
    if not (event.codex_surface or event.codex_attempt is not None):
        return True
    if base_details in {
        "OK",
        "TokenKick anchor probe completed.",
        "Superseded by reset-clock match",
    }:
        return False
    if event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR:
        return False
    return True


def _history_event_base_details(event: KickEvent) -> str:
    if event.post_kick_status == "superseded":
        return "Superseded by reset-clock match"
    if event.response_text:
        if event.error:
            return f"{event.response_text} ({event.error})"
        return event.response_text
    if event.provider_output_excerpt and event.error:
        return f"Provider output saved in JSON; {event.error}"
    if event.error:
        return event.error
    details = "OK"
    model = event.reported_model or event.kick_model
    if model:
        details = f"{details} - {model}"
    if event.total_tokens is not None:
        details = f"{details} - {event.total_tokens} tokens"
    return details


def _history_event_codex_context(event: KickEvent) -> str:
    parts = []
    surface = _kick_event_surface_summary(event)
    if surface:
        parts.append(surface)
    attempt = _kick_event_attempt_summary(event)
    if attempt:
        parts.append(f"attempt={attempt}")
    evidence = _kick_event_evidence_summary(event)
    if evidence:
        parts.append(evidence)
    return "; ".join(parts)


def _kick_event_surface_summary(event: KickEvent) -> str:
    if not event.codex_surface:
        return ""
    return f"surface={event.codex_surface}"


def _kick_event_attempt_summary(event: KickEvent) -> str:
    if event.codex_attempt is None:
        return ""
    if event.codex_max_attempts is not None:
        return f"{event.codex_attempt}/{event.codex_max_attempts}"
    return str(event.codex_attempt)


def _kick_event_evidence_summary(event: KickEvent) -> str:
    parts = []
    if event.evidence_response is not None:
        parts.append(f"response={_yes_no(event.evidence_response)}")
    if event.evidence_tokens is not None:
        parts.append(f"tokens={_yes_no(event.evidence_tokens)}")
    if event.evidence_provider_moved is not None:
        parts.append(f"provider_moved={_yes_no(event.evidence_provider_moved)}")
    if event.codex_confirmation_method:
        parts.append(f"method={event.codex_confirmation_method}")
    if event.codex_attribution:
        parts.append(f"attribution={event.codex_attribution}")
    if (
        event.codex_confirmation_method in {"reset_clock", "late_reset_clock"}
        and event.codex_anchor_match_delta_seconds is not None
    ):
        parts.append(f"delta={round(event.codex_anchor_match_delta_seconds, 1)}s")
    if event.codex_provider_stale is not None:
        parts.append(f"provider_stale={_yes_no(event.codex_provider_stale)}")
    if event.post_kick_status:
        parts.append(f"post={event.post_kick_status}")
    if not parts:
        return ""
    return "evidence: " + " ".join(parts)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _bool_icon(value: bool) -> str:
    return "✓" if value else "✗"


def _history_filter_labels(account_label: str, accounts: list[AccountConfig]) -> set[str]:
    labels = {account_label}
    account = next((candidate for candidate in accounts if candidate.label == account_label), None)
    if account is None:
        return labels
    component = _provider_first_account_component(account)
    if component:
        labels.add(component)
        labels.add(f"{component} ({account.provider})")
        labels.add(f"{account.provider} ({component})")
    return labels


# ---------------------------------------------------------------------------
# tk reset-log
# ---------------------------------------------------------------------------

@cli.command("reset-log")
@click.option("--since", help="Only show reset events since a relative time like 7d or 24h")
@click.option("--provider", help="Only show events for one provider")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--csv", "as_csv", is_flag=True, help="Output as CSV")
@click.option("--detail", "detail_id", help="Show full detail for one event id")
@click.option("--unacknowledged", is_flag=True, help="Only show unacknowledged reset events")
@click.option("--latest", "ack_latest", is_flag=True, help="With ack: acknowledge latest event")
@click.option("--all", "ack_all", is_flag=True, help="With ack: acknowledge all matching events")
@click.argument("action", nargs=-1)
def reset_log(
    since: str | None,
    provider: str | None,
    as_json: bool,
    as_csv: bool,
    detail_id: str | None,
    unacknowledged: bool,
    ack_latest: bool,
    ack_all: bool,
    action: tuple[str, ...],
):
    """Show detected provider global reset events."""
    if as_json and as_csv:
        console.print("[red]Use either --json-output or --csv, not both.[/red]")
        return
    cutoff = parse_since(since) if since else None
    if since and cutoff is None:
        console.print("[red]Invalid --since value. Use forms like 7d, 24h, or an ISO timestamp.[/red]")
        return
    if action:
        if action[0] != "ack":
            console.print(f'[red]Unknown reset-log action "{action[0]}".[/red]')
            return
        _reset_log_ack(
            event_ids=action[1:],
            latest=ack_latest,
            all_events=ack_all,
            as_json=as_json,
        )
        return
    events = filter_reset_events(
        load_reset_events(),
        since=cutoff,
        provider=provider,
        unacknowledged=unacknowledged,
    )
    if detail_id:
        event = next((candidate for candidate in events if candidate.id == detail_id), None)
        if event is None:
            console.print(f'[red]Reset event "{detail_id}" not found.[/red]')
            return
        if as_json:
            click.echo(json.dumps(event.to_dict(), indent=2))
            return
        _render_reset_event_detail(event)
        _print_report_timestamp("Reset log printed at")
        return
    if as_json:
        click.echo(json.dumps({"events": [event.to_dict() for event in events]}, indent=2))
        return
    if as_csv:
        click.echo(reset_events_csv(events), nl=False)
        return
    if not events:
        console.print("[dim]No reset events logged.[/dim]")
        _print_report_timestamp("Reset log printed at")
        return
    table = Table(title="TokenKick — Reset Event Log", show_header=True)
    table.add_column("Time")
    table.add_column("Type")
    table.add_column("Provider")
    table.add_column("Confidence")
    table.add_column("Accounts")
    table.add_column("Impact")
    table.add_column("Ack")
    for event in reversed(events):
        table.add_row(
            _format_reset_event_time(event),
            _format_reset_event_type(event),
            event.provider.title(),
            event.confidence,
            ", ".join(event.affected_accounts),
            _format_reset_event_impact(event),
            "yes" if event.acknowledged_at else "no",
        )
    console.print(table)
    _print_report_timestamp("Reset log printed at")


def _format_reset_event_type(event: ResetEvent) -> str:
    return "provider observation" if is_provider_reset_observation(event) else "global reset"


def _reset_log_ack(
    *,
    event_ids: tuple[str, ...],
    latest: bool,
    all_events: bool,
    as_json: bool = False,
) -> None:
    usage_message = "Use one of: tk reset-log ack EVENT_ID, ack --latest, or ack --all."
    if sum(bool(value) for value in (event_ids, latest, all_events)) != 1:
        if _app_json_requested(as_json):
            emit_app_error("reset_log_ack_invalid", usage_message)
            return
        console.print(f"[red]{usage_message}[/red]")
        return
    updated = acknowledge_reset_events(
        event_ids=event_ids or None,
        latest=latest,
        all_events=all_events,
        acknowledged_by="app" if app_mode_enabled() else "cli",
    )
    if _app_json_requested(as_json):
        emit_app_success(
            {"acknowledged": [event.to_dict() for event in updated]},
            message=(
                f"Acknowledged {len(updated)} reset event(s)."
                if updated
                else "No matching unacknowledged reset events."
            ),
        )
        return
    if not updated:
        console.print("[yellow]No matching unacknowledged reset events.[/yellow]")
        return
    labels = ", ".join(event.id for event in updated)
    console.print(f"[green]Acknowledged reset event(s): {labels}[/green]")


def record_reset_event_recovery_action(event_id: str, action: str) -> ResetEvent | None:
    return _record_reset_event_recovery_action(event_id, action)


def _render_reset_event_detail(event: ResetEvent) -> None:
    table = Table(title=f"Reset Event: {event.id}", show_header=False)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("Detected", _format_reset_event_time(event))
    table.add_row(
        "Type",
        "provider reset observation" if is_provider_reset_observation(event) else "global reset",
    )
    table.add_row("Provider", event.provider.title())
    table.add_row("Confidence", event.confidence)
    table.add_row("Trigger", event.trigger)
    table.add_row("Impact", _format_reset_event_impact(event))
    notification = "sent" if event.notification_sent else "not sent"
    if event.notification_skip_reason:
        notification = f"{notification} ({event.notification_skip_reason})"
    table.add_row("Notification", notification)
    table.add_row(
        "Acknowledged",
        f"{event.acknowledged_at} by {event.acknowledged_by or 'unknown'}"
        if event.acknowledged_at
        else "no",
    )
    if event.recovery_action:
        table.add_row("Recovery", f"{event.recovery_action} at {event.recovery_action_at or '—'}")
    if event.pending_kicks_invalidated:
        table.add_row("Invalidated", ", ".join(event.pending_kicks_invalidated))
    if event.failover_guidance:
        table.add_row("Use next", event.failover_guidance)
    console.print(table)

    accounts_table = Table(title="Affected Accounts", show_header=True)
    accounts_table.add_column("Account")
    accounts_table.add_column("Before")
    accounts_table.add_column("After")
    accounts_table.add_column("Lost")
    impacts = {impact["account"]: impact for impact in account_impacts(event)}
    for snapshot in event.account_snapshots:
        impact = impacts.get(snapshot.account, {})
        before = (
            f"{snapshot.before_state}, w {_format_optional_pct(snapshot.before_weekly_used_pct)}, "
            f"resets {impact.get('previous_reset_prediction') or '—'}"
        )
        after = (
            f"{snapshot.after_state}, w {_format_optional_pct(snapshot.after_weekly_used_pct)}, "
            f"resets {impact.get('new_reset_prediction') or '—'}"
        )
        lost = impact.get("quota_hours_lost")
        accounts_table.add_row(
            snapshot.account,
            before,
            after,
            "—" if lost is None else f"~{lost:g}h",
        )
    console.print(accounts_table)
    console.print(event.detail)


def _format_reset_event_time(event: ResetEvent) -> str:
    detected = parse_utc(event.detected_at)
    if detected is None:
        return event.detected_at
    return detected.strftime("%Y-%m-%d %H:%M UTC")


def _format_reset_event_impact(event: ResetEvent) -> str:
    if is_provider_reset_observation(event):
        return "status changed"
    if event.total_quota_hours_lost is None:
        return "—"
    return f"~{event.total_quota_hours_lost:g}h quota lost"


def _format_optional_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}%"


@cli.command("codex-usage", hidden=True)
@click.argument("label", required=False)
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def codex_usage(label: str | None, as_json: bool):
    """Show sanitized Codex provider rate-limit buckets for debugging."""
    config = Config.load()
    accounts = [
        account
        for account in _load_accounts(config)
        if account.provider == "codex"
        and account.source == DataSource.CODEX_DIRECT
        and account.provider_home
        and (label is None or account.label == label)
    ]
    if label is not None and not accounts:
        console.print(f'[red]Codex direct account "{label}" not found.[/red]')
        raise click.exceptions.Exit(1)
    if not accounts:
        console.print("[yellow]No Codex direct accounts with provider_home are configured.[/yellow]")
        return

    account_bucket_map = _codex_usage_account_bucket_map(accounts)
    reports = [_codex_usage_debug_report(account, account_bucket_map=account_bucket_map) for account in accounts]
    if as_json:
        click.echo(json.dumps(reports, indent=2))
        return

    table = Table(title="Codex Usage Buckets", show_header=True)
    table.add_column("Account")
    table.add_column("Selected")
    table.add_column("Meaning")
    table.add_column("Weekly")
    table.add_column("Session")
    table.add_column("Buckets")
    for report in reports:
        if report.get("error"):
            table.add_row(str(report["label"]), "[red]error[/red]", "-", "-", str(report["error"]))
            continue
        selected = report.get("selected_bucket") or {}
        selected_name = selected.get("limit_id") or selected.get("key") or "-"
        status = report.get("selected_status") or {}
        mapped_account = selected.get("mapped_account_label") or "-"
        table.add_row(
            str(report["label"]),
            f"{selected_name} → {mapped_account}",
            _codex_usage_bucket_display_name(selected),
            _debug_percent(status.get("weekly_used_percent")),
            _debug_percent(status.get("session_used_percent")),
            str(report.get("bucket_count", 0)),
        )
    console.print(table)


def _codex_usage_bucket_map_key(provider_home: str | None, bucket: str | None) -> tuple[str, str]:
    home = (provider_home or "").strip()
    bucket_id = (bucket or CODEX_DEFAULT_RATE_LIMIT_ID).strip() or CODEX_DEFAULT_RATE_LIMIT_ID
    return home, bucket_id


def _codex_usage_account_bucket_map(accounts: list[AccountConfig]) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    for account in accounts:
        bucket = (account.codex_rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip() or CODEX_DEFAULT_RATE_LIMIT_ID
        mapping[_codex_usage_bucket_map_key(account.provider_home, bucket)] = account.label
    return mapping


def _codex_usage_debug_report(
    account: AccountConfig,
    *,
    account_bucket_map: dict[tuple[str, str], str] | None = None,
) -> dict:
    try:
        usage = read_codex_provider_usage(Path(account.provider_home))
    except CodexProviderUsageError as exc:
        return {
            "label": account.label,
            "provider_home": account.provider_home,
            "error": str(exc),
        }
    report = _codex_usage_debug_payload(account, usage.response, account_bucket_map=account_bucket_map)
    report["elapsed_ms"] = usage.elapsed_ms
    return report


def _codex_usage_debug_payload(
    account: AccountConfig,
    response: dict,
    *,
    account_bucket_map: dict[tuple[str, str], str] | None = None,
) -> dict:
    result = response.get("result") if isinstance(response, dict) else None
    by_limit = result.get("rateLimitsByLimitId") if isinstance(result, dict) else None
    bucket_items: list[tuple[str, dict]] = []
    buckets: list[dict] = []
    if isinstance(by_limit, dict):
        for key, value in by_limit.items():
            if not isinstance(value, dict):
                buckets.append({"key": str(key), "valid": False, "issue": "bucket is not an object"})
                continue
            issue = _codex_appserver_rate_limit_issue(value)
            bucket = _codex_usage_bucket_debug(str(key), value, issue)
            mapped_label = (account_bucket_map or {}).get(
                _codex_usage_bucket_map_key(account.provider_home, bucket["limit_id"] or bucket["key"])
            )
            if mapped_label:
                bucket["mapped_account_label"] = mapped_label
            buckets.append(bucket)
            if issue is None:
                bucket_items.append((bucket["limit_id"] or bucket["key"], value))

    selected_bucket: dict | None = None
    if bucket_items:
        selected_limit_id = (account.codex_rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip()
        selected_limit_ids = {limit_id for limit_id, _value in bucket_items}
        if selected_limit_id not in selected_limit_ids and selected_limit_id == CODEX_DEFAULT_RATE_LIMIT_ID:
            selected_limit_id = bucket_items[0][0]
        if selected_limit_id in selected_limit_ids:
            selected_bucket = next(
                (
                    bucket
                    for bucket in buckets
                    if bucket.get("limit_id") == selected_limit_id or bucket.get("key") == selected_limit_id
                ),
                None,
            )

    selected_status = _parse_codex_appserver_ratelimits(
        account.label,
        response,
        rate_limit_id=account.codex_rate_limit_id,
        rate_limit_name=account.codex_rate_limit_name,
    )
    return {
        "label": account.label,
        "provider_home": account.provider_home,
        "response_id": response.get("id") if isinstance(response, dict) else None,
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "bucket_count": len(buckets),
        "selected_bucket": selected_bucket,
        "selected_status": {
            "state": selected_status.state.value,
            "weekly_used_percent": selected_status.used_percent,
            "weekly_resets_in_seconds": selected_status.resets_in_seconds,
            "weekly_resets_at": selected_status.resets_at,
            "weekly_window_minutes": selected_status.window_minutes,
            "session_used_percent": selected_status.session_used_percent,
            "session_resets_in_seconds": selected_status.session_resets_in_seconds,
            "session_resets_at": selected_status.session_resets_at,
            "session_window_minutes": selected_status.session_window_minutes,
            "window_anchor_state": selected_status.window_anchor_state,
            "error": selected_status.error,
        },
        "buckets": buckets,
    }


class _CodexSurfacesGroup(click.Group):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if "--json-output" in args and args.index("--json-output") > 0:
            args = ["--json-output", *[arg for arg in args if arg != "--json-output"]]
        if args and args[0] == "reset-stats":
            args = ["__all__", *args]
        return super().parse_args(ctx, args)


@cli.group(
    "codex-surfaces",
    cls=_CodexSurfacesGroup,
    invoke_without_command=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("label")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def codex_surfaces(ctx: click.Context, label: str, as_json: bool):
    """Show learned Codex surface order and stats without kicking."""
    ctx.ensure_object(dict)
    ctx.obj["codex_surfaces_label"] = label
    if ctx.invoked_subcommand is not None:
        return
    if "--json-output" in ctx.args:
        as_json = True
    config = Config.load()
    account = _codex_direct_account_or_exit(label, _load_accounts(config))
    report = codex_surface_stats_for_account(account, _codex_surface_stats_file())
    report["provider_home"] = account.provider_home
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    _render_codex_surfaces_report(report)


@codex_surfaces.command("reset-stats")
@click.option("--all", "all_accounts", is_flag=True, help="Reset learned surface stats for all Codex accounts.")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_surfaces_reset_stats(ctx: click.Context, all_accounts: bool, as_json: bool):
    """Reset learned Codex surface scores/order without deleting history."""
    label = _codex_surfaces_label_from_ctx(ctx)
    accounts = _load_accounts(Config.load())
    if _app_json_requested(as_json):
        if all_accounts:
            codex_accounts = [account for account in accounts if account.provider == "codex"]
            for account in codex_accounts:
                reset_codex_surface_learning_stats(_codex_surface_stats_file(), account)
            emit_app_success(
                {
                    "action": "reset_stats_all",
                    "count": len(codex_accounts),
                    "codex_strategy": _codex_burst_ladder_status_payload(Config.load()),
                },
                message=f"Learned Codex surface stats reset for {len(codex_accounts)} Codex account(s).",
            )
            return
        if label == "__all__":
            emit_app_error(ERROR_USAGE, 'Provide a Codex account label, or use "tk codex-surfaces reset-stats --all".')
            sys.exit(2)
        _run_codex_surface_mutation_json(
            "reset_stats",
            label,
            lambda account: reset_codex_surface_learning_stats(_codex_surface_stats_file(), account),
        )
        return
    if all_accounts:
        codex_accounts = [account for account in accounts if account.provider == "codex"]
        for account in codex_accounts:
            reset_codex_surface_learning_stats(_codex_surface_stats_file(), account)
        console.print(
            "[green]Learned Codex surface stats reset for "
            f"{len(codex_accounts)} Codex account(s).[/green]"
        )
        console.print(
            "[dim]Kick history, demotion settings, force overrides, and demotion evidence were not changed.[/dim]"
        )
        return
    if label == "__all__":
        raise click.ClickException('Provide a Codex account label, or use "tk codex-surfaces reset-stats --all".')
    account = _codex_direct_account_or_exit(label, accounts)
    reset_codex_surface_learning_stats(_codex_surface_stats_file(), account)
    console.print(f'[green]Learned Codex surface stats reset for "{label}".[/green]')
    console.print("[dim]Kick history, demotion settings, force overrides, and demotion evidence were not changed.[/dim]")


@codex_surfaces.command("reset-all")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.pass_context
def codex_surfaces_reset_all(ctx: click.Context, as_json: bool):
    """Reset learned stats and demotion evidence for one Codex account."""
    label = _codex_surfaces_label_from_ctx(ctx)
    if _app_json_requested(as_json):
        _run_codex_surface_mutation_json(
            "reset_stats_and_evidence",
            label,
            lambda account: (
                reset_codex_surface_learning_stats(_codex_surface_stats_file(), account),
                reset_codex_surface_demotion_evidence(_codex_surface_stats_file(), account),
            ),
        )
        return
    account = _codex_direct_account_or_exit(label, _load_accounts(Config.load()))
    reset_codex_surface_learning_stats(_codex_surface_stats_file(), account)
    reset_codex_surface_demotion_evidence(_codex_surface_stats_file(), account)
    console.print(f'[green]Codex surface stats and demotion evidence reset for "{label}".[/green]')
    console.print("[dim]Kick history, demotion settings, and force overrides were not changed.[/dim]")


@codex_surfaces.group("demotion")
def codex_surfaces_demotion():
    """Configure per-account Codex surface auto-demotion."""


@codex_surfaces_demotion.command("enable")
@click.pass_context
def codex_surfaces_demotion_enable(ctx: click.Context):
    label = _codex_surfaces_label_from_ctx(ctx)
    _set_codex_surface_demotion_config(label, codex_surface_auto_demote=True)
    console.print(f'[green]Codex surface auto-demotion enabled for "{label}".[/green]')


@codex_surfaces_demotion.command("disable")
@click.pass_context
def codex_surfaces_demotion_disable(ctx: click.Context):
    label = _codex_surfaces_label_from_ctx(ctx)
    _set_codex_surface_demotion_config(label, codex_surface_auto_demote=False)
    console.print(f'[green]Codex surface auto-demotion disabled for "{label}".[/green]')


@codex_surfaces_demotion.command("evidence")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def codex_surfaces_demotion_evidence(ctx: click.Context, as_json: bool):
    """Show the stored strong-cluster evidence behind surface demotions."""
    label = _codex_surfaces_label_from_ctx(ctx)
    account = _codex_direct_account_or_exit(label, _load_accounts(Config.load()))
    report = codex_surface_stats_for_account(account, _codex_surface_stats_file())
    report["provider_home"] = account.provider_home
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    _render_codex_surface_demotion_evidence_report(report)


@codex_surfaces_demotion.command("set")
@click.option("--after-strong-clusters", type=click.IntRange(1, 100), default=None)
@click.option("--min-active-surfaces", type=click.IntRange(1, len(DEFAULT_CODEX_SURFACE_ORDER)), default=None)
@click.option("--min-kept-anchor-rate", type=click.FloatRange(0.0, 1.0), default=None)
@click.option("--measurement-clusters", type=click.IntRange(1, 200), default=None)
@click.option("--rescue-cooldown-strong-clusters", type=click.IntRange(0, 200), default=None)
@click.pass_context
def codex_surfaces_demotion_set(
    ctx: click.Context,
    after_strong_clusters: int | None,
    min_active_surfaces: int | None,
    min_kept_anchor_rate: float | None,
    measurement_clusters: int | None,
    rescue_cooldown_strong_clusters: int | None,
):
    label = _codex_surfaces_label_from_ctx(ctx)
    overrides = {
        key: value
        for key, value in {
            "codex_surface_demote_after_strong_clusters": after_strong_clusters,
            "codex_surface_demote_min_active_surfaces": min_active_surfaces,
            "codex_surface_demote_min_kept_anchor_rate": min_kept_anchor_rate,
            "codex_surface_demote_measurement_clusters": measurement_clusters,
            "codex_surface_rescue_cooldown_strong_clusters": rescue_cooldown_strong_clusters,
        }.items()
        if value is not None
    }
    if not overrides:
        raise click.ClickException("Provide at least one demotion setting to change.")
    _set_codex_surface_demotion_config(label, **overrides)
    console.print(f'[green]Codex surface demotion settings updated for "{label}".[/green]')


@codex_surfaces_demotion.command("force-keep")
@click.argument("surfaces", nargs=-1, required=True)
@click.pass_context
def codex_surfaces_demotion_force_keep(ctx: click.Context, surfaces: tuple[str, ...]):
    label = _codex_surfaces_label_from_ctx(ctx)
    order = _parse_codex_fire_all_surface_order(list(surfaces))
    _set_codex_surface_demotion_config(label, codex_surface_force_keep=order)
    console.print(f'[green]Force-keep set for "{label}": {", ".join(order)}.[/green]')


@codex_surfaces_demotion.command("force-prune")
@click.argument("surfaces", nargs=-1, required=True)
@click.pass_context
def codex_surfaces_demotion_force_prune(ctx: click.Context, surfaces: tuple[str, ...]):
    label = _codex_surfaces_label_from_ctx(ctx)
    order = _parse_codex_fire_all_surface_order(list(surfaces))
    if len(order) >= len(DEFAULT_CODEX_SURFACE_ORDER):
        raise click.ClickException("Cannot force-prune every Codex surface.")
    active_count = len(DEFAULT_CODEX_SURFACE_ORDER) - len(order)
    console.print(
        "[yellow]Warning: force-pruned surfaces are manual overrides and are not "
        "auto-reintroduced on a miss.[/yellow]"
    )
    if active_count < 2:
        console.print(
            "[red]Warning: this leaves fewer than 2 active surfaces; a force-pruned "
            "miss has no automatic rescue path.[/red]"
        )
    _set_codex_surface_demotion_config(label, codex_surface_force_prune=order)
    console.print(f'[green]Force-prune set for "{label}": {", ".join(order)}.[/green]')


@codex_surfaces_demotion.command("clear-overrides")
@click.pass_context
def codex_surfaces_demotion_clear_overrides(ctx: click.Context):
    label = _codex_surfaces_label_from_ctx(ctx)
    _set_codex_surface_demotion_config(
        label,
        codex_surface_force_keep=[],
        codex_surface_force_prune=[],
    )
    console.print(f'[green]Codex surface force overrides cleared for "{label}".[/green]')


@codex_surfaces_demotion.command("reset-evidence")
@click.pass_context
def codex_surfaces_demotion_reset_evidence(ctx: click.Context):
    label = _codex_surfaces_label_from_ctx(ctx)
    account = _codex_direct_account_or_exit(label, _load_accounts(Config.load()))
    reset_codex_surface_demotion_evidence(_codex_surface_stats_file(), account)
    console.print(f'[green]Codex surface demotion evidence reset for "{label}".[/green]')


def _codex_surfaces_label_from_ctx(ctx: click.Context) -> str:
    value = (ctx.obj or {}).get("codex_surfaces_label")
    if not isinstance(value, str):
        raise click.ClickException("Missing Codex account label.")
    return value


def _set_codex_surface_demotion_config(label: str, **overrides: object) -> None:
    config = Config.load()
    accounts = _load_accounts(config)
    account = _account_by_label_or_exit(label, accounts)
    if account.provider != "codex":
        raise click.ClickException(f'Account "{label}" is not a Codex account.')
    force_keep = set(overrides.get("codex_surface_force_keep", account.codex_surface_force_keep))
    force_prune = set(overrides.get("codex_surface_force_prune", account.codex_surface_force_prune))
    overlap = force_keep & force_prune
    if overlap:
        raise click.ClickException(
            f"Surface cannot be both force-kept and force-pruned: {', '.join(sorted(overlap))}"
        )
    updated = [
        replace(candidate, **overrides) if candidate.label == account.label else candidate
        for candidate in accounts
    ]
    _save_config_like(config, accounts=updated)


def _set_all_codex_surface_demotion_config(enabled: bool) -> int:
    config = Config.load()
    accounts = _load_accounts(config)
    updated = [
        replace(candidate, codex_surface_auto_demote=enabled)
        if candidate.provider == "codex"
        else candidate
        for candidate in accounts
    ]
    count = sum(1 for candidate in accounts if candidate.provider == "codex")
    _save_config_like(config, accounts=updated)
    return count


@cli.command("codex-surface-test", hidden=True)
@click.argument("label")
@click.option(
    "--mode",
    type=click.Choice(CODEX_KICK_SURFACES),
    default=CODEX_KICK_SURFACE_REPO,
    show_default=True,
    help="Codex exec surface to test: repo, legacy, repo-skip, or interactive-like.",
)
@click.option(
    "--wait",
    "wait_seconds",
    type=float,
    default=0.0,
    show_default=True,
    help="Seconds to wait before the post-kick usage read.",
)
@click.option(
    "--poll-timeout",
    "poll_timeout_seconds",
    type=float,
    default=1200.0,
    show_default=True,
    help="Seconds to poll for the first provider reset-clock move after the kick.",
)
@click.option(
    "--poll-interval",
    "poll_interval_seconds",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds between provider reset-clock polls.",
)
@click.option("--yes", is_flag=True, help="Confirm this quota-consuming diagnostic")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def codex_surface_test(
    label: str,
    mode: str,
    wait_seconds: float,
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
    yes: bool,
    as_json: bool,
):
    """Compare Codex usage before/after one controlled exec surface."""
    config = Config.load()
    account = _codex_direct_account_or_exit(label, _load_accounts(config))
    if not yes:
        console.print(
            "[yellow]This is an active Codex diagnostic. It runs codex exec and can "
            "consume quota or anchor a window.[/yellow]"
        )
        if not click.confirm(f'Run active Codex surface test for "{label}"?', default=False):
            console.print("[dim]Codex surface test cancelled.[/dim]")
            return
    report = _codex_surface_debug_report(
        account,
        mode=mode,
        wait_seconds=wait_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    _render_codex_surface_debug_report(report)


def _codex_direct_account_or_exit(label: str, accounts: list[AccountConfig]) -> AccountConfig:
    account = _account_by_label_or_exit(label, accounts)
    if account.provider != "codex" or not account.provider_home:
        console.print(f'[red]Account "{label}" is not a Codex direct account with provider_home.[/red]')
        raise click.exceptions.Exit(1)
    return account


def _render_codex_surfaces_report(report: dict) -> None:
    table = Table(title=f"Current Codex Surface Order — {report.get('label')}", show_header=True)
    table.add_column("Rank")
    table.add_column("Surface")
    table.add_column("State")
    table.add_column("Learning score", justify="right")
    table.add_column("Strong wins / tries")
    table.add_column("Issues")
    table.add_column("Last strong win")
    for surface in report.get("surfaces") or []:
        table.add_row(
            str(surface.get("rank") or "—"),
            _surface_title(surface),
            _surface_state_text(surface),
            f"{float(surface.get('score') or 0.0):.2f}",
            f"{int(surface.get('confirmed') or 0)}/{int(surface.get('attempts') or 0)}",
            _surface_issue_summary(surface),
            _format_optional_timestamp(surface.get("last_confirmed_at")),
        )
    console.print(table)
    order = [str(surface) for surface in report.get("order") or []]
    if order:
        console.print(f"[dim]Next adaptive session attempt order: {' -> '.join(order)}[/dim]")
    skipped = _skipped_surface_summaries(report)
    if skipped:
        console.print("[dim]Not attempted by adaptive order: " + "; ".join(skipped) + ".[/dim]")
    demotion = report.get("demotion") or {}
    if demotion:
        enabled = "enabled" if demotion.get("enabled") else "disabled"
        console.print(
            "[dim]Auto-demotion "
            f"{enabled}; force-keep={', '.join(demotion.get('force_keep') or []) or '—'}; "
            f"force-prune={', '.join(demotion.get('force_prune') or []) or '—'}.[/dim]"
        )
        if demotion.get("force_prune"):
            console.print(
                "[yellow]Force-pruned surfaces are manual overrides and are not "
                "auto-reintroduced on a miss.[/yellow]"
            )
        for evidence in report.get("demotion_evidence") or []:
            surface = evidence.get("surface") or "unknown"
            eligible = int(evidence.get("eligible_clusters") or 0)
            kept = int(evidence.get("kept_ahead_wins") or 0)
            wins = int(evidence.get("surface_strong_wins") or 0)
            console.print(
                "[dim]"
                f"{surface}: {wins} strong wins in the last {eligible} eligible clusters; "
                f"kept-ahead surfaces won {kept}/{eligible}."
                "[/dim]"
            )
            console.print(
                "[dim]Use Surface demotion -> Show demotion evidence for cluster details.[/dim]"
            )
    console.print(
        "[dim]Notes: learning score is a capped preference score, not a probability. "
        "Strong wins are reset-clock-confirmed anchors. No output/tokens means Codex "
        "returned neither assistant text nor token usage evidence.[/dim]"
    )
    console.print(
        "[dim]Burst ladder keeps its configured order, then filters demoted and "
        "force-pruned surfaces; use `tk codex-strategy status` for its effective order.[/dim]"
    )
    console.print("[dim]Read-only: this command does not run Codex or kick accounts.[/dim]")


def _render_codex_surface_demotion_evidence_report(report: dict) -> None:
    console.print(f"[bold]Demotion Evidence — {report.get('label')}[/bold]")
    demotion = report.get("demotion") or {}
    console.print(
        "[dim]Policy: demote after "
        f"{int(demotion.get('after_strong_clusters') or 0)} eligible strong clusters; "
        f"keep at least {int(demotion.get('min_active_surfaces') or 0)} active surfaces; "
        "require kept-ahead win rate >= "
        f"{float(demotion.get('min_kept_anchor_rate') or 0.0):.0%}; "
        f"look back up to {int(demotion.get('measurement_clusters') or 0)} clusters.[/dim]"
    )
    evidence_items = report.get("demotion_evidence") or []
    if not evidence_items:
        console.print("[green]No surfaces are currently auto-demoted for this account.[/green]")
        console.print("[dim]Read-only: this command does not run Codex or kick accounts.[/dim]")
        return
    for evidence in evidence_items:
        surface = str(evidence.get("surface") or "unknown")
        console.print()
        console.print(f"[bold]{surface}[/bold] [dim]{evidence.get('surface_label') or ''}[/dim]")
        console.print("[dim]Decision: skip this surface for now.[/dim]")
        reason = evidence.get("reason")
        if reason:
            console.print(f"[dim]Reason: {reason}.[/dim]")
        eligible = int(evidence.get("eligible_clusters") or 0)
        wins = int(evidence.get("surface_strong_wins") or 0)
        kept = int(evidence.get("kept_ahead_wins") or 0)
        active_count = _count_evidence_cluster_flag(evidence, "surface_active")
        attempted_count = _count_evidence_cluster_flag(evidence, "surface_attempted")
        if eligible:
            console.print(
                "[dim]"
                f"Conclusion: {surface} was eligible in {active_count}/{eligible} clusters "
                f"and tried in {attempted_count}/{eligible}, but never produced the strong winner."
                "[/dim]"
            )
        console.print(
            "[dim]"
            f"Summary: {surface} won {wins}/{eligible}; "
            f"surfaces ahead won {kept}/{eligible} "
            f"({float(evidence.get('kept_ahead_rate') or 0.0):.0%})."
            "[/dim]"
        )
        console.print(f"[dim]This decision used {eligible} eligible clusters.[/dim]")
        clusters = evidence.get("recent_clusters") or []
        table = Table(title=f"Recent eligible clusters for {surface}", show_header=True)
        table.add_column("#", justify="right")
        table.add_column("Time")
        table.add_column("Winner")
        table.add_column(f"{surface} eligible")
        table.add_column("Tried in cluster")
        table.add_column(f"Ahead of {surface} then")
        for cluster in clusters:
            table.add_row(
                str(cluster.get("index") or "—"),
                _format_optional_timestamp(cluster.get("timestamp")),
                str(cluster.get("winner") or "—"),
                "yes" if cluster.get("surface_active") else "no",
                _surface_list(cluster.get("attempted_surfaces")),
                _surface_list(cluster.get("kept_ahead_surfaces")),
            )
        console.print(table)
    console.print("[dim]Read-only: this command does not run Codex or kick accounts.[/dim]")


def _surface_title(surface: dict) -> Text:
    name = str(surface.get("surface") or "unknown")
    label = str(surface.get("surface_label") or "")
    if label and label != name:
        return Text.assemble((name, "bold"), "\n", (label, "dim"))
    return Text(name)


def _count_evidence_cluster_flag(evidence: dict, flag: str) -> int:
    clusters = evidence.get("recent_clusters")
    if not isinstance(clusters, list):
        return 0
    return sum(1 for cluster in clusters if isinstance(cluster, dict) and cluster.get(flag))


def _surface_state_text(surface: dict) -> str:
    state = str(surface.get("state") or "active")
    if state == "demoted":
        return "skipped (auto-demoted)"
    if state == "force-pruned":
        return "skipped (force-pruned)"
    if state == "force-kept":
        return "active (force-kept)"
    if state == "active_rescue_cooldown":
        cooldown = surface.get("rescue_cooldown_remaining_strong_clusters")
        if cooldown is not None:
            return f"active (rescued, {cooldown} clusters left)"
        return "active (rescued)"
    return state


def _surface_issue_summary(surface: dict) -> str:
    parts = []
    no_generation = int(surface.get("no_generation") or 0)
    failures = int(surface.get("failures") or 0)
    timing_matches = int(surface.get("timing_matches") or 0)
    external_possible = int(surface.get("external_possible") or 0)
    if no_generation:
        parts.append(f"no output/tokens {no_generation}")
    if failures:
        parts.append(f"failed {failures}")
    if timing_matches:
        parts.append(f"timing {timing_matches}")
    if external_possible:
        parts.append(f"external possible {external_possible}")
    return " · ".join(parts) if parts else "—"


def _skipped_surface_summaries(report: dict) -> list[str]:
    summaries = []
    for surface in report.get("surfaces") or []:
        if surface.get("rank") is not None:
            continue
        state = str(surface.get("state") or "")
        name = str(surface.get("surface") or "unknown")
        if state == "demoted":
            summaries.append(f"{name} auto-demoted")
        elif state == "force-pruned":
            summaries.append(f"{name} force-pruned")
    return summaries


def _surface_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "—"
    return ", ".join(str(value) for value in values)


@cli.command("codex-surface-patterns")
@click.argument("label", required=False)
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def codex_surface_patterns(label: str | None, as_json: bool):
    """Analyze Codex surface history patterns without changing live behavior."""
    events = load_kick_history(limit=100_000)
    scope_label = label
    if label:
        labels, scope_label = _codex_surface_pattern_filter_labels(label, Config.load().accounts)
        events = [event for event in events if event.label in labels]
    report = build_codex_surface_patterns_report(events)
    report["scope_label"] = scope_label
    if as_json:
        click.echo(json.dumps(report, indent=2))
        return
    _render_codex_surface_patterns_report(report)


def _codex_surface_pattern_filter_labels(
    label: str,
    accounts: list[AccountConfig],
) -> tuple[set[str], str]:
    for account in accounts:
        component = _provider_first_account_component(account)
        aliases = {account.label}
        provider_wrapped = _account_label_provider_wrapped_component(account)
        if provider_wrapped:
            aliases.add(provider_wrapped)
        if component:
            aliases.update(
                {
                    component,
                    f"{component} ({account.provider})",
                    f"{account.provider} ({component})",
                }
            )
        if label in aliases:
            return aliases, account.label
    return {label}, label


def _account_label_provider_wrapped_component(account: AccountConfig) -> str | None:
    prefix = f"{account.provider} ("
    if not account.label.startswith(prefix) or not account.label.endswith(")"):
        return None
    component = account.label[len(prefix):-1].strip()
    return component or None


def _render_codex_surface_patterns_report(report: dict) -> None:
    scope = report.get("scope_label") or "all Codex accounts"
    ignored = report.get("ignored") or {}
    verdict = report.get("verdict") or {}
    baseline = report.get("baseline") or {}
    candidates = report.get("candidates") or {}
    console.print("[bold]Surface Pattern Check — experimental/read-only[/bold]")
    console.print(f"[yellow]{_surface_pattern_verdict_summary(verdict)}[/yellow]")
    console.print(f"[dim]Scope: {scope}[/dim]")
    console.print(
        "[dim]Strong clusters checked: "
        f"{int(report.get('eligible_clusters') or 0)}; backtest samples: "
        f"{int(report.get('evaluated_samples') or 0)}[/dim]"
    )

    table = Table(title="Backtested prediction rules", show_header=True)
    table.add_column("Rule")
    table.add_column("N", justify="right")
    table.add_column("Winner first")
    table.add_column("Winner in first 2")
    table.add_column("Top-1 lift")
    table.add_column("Top-2 lift")

    table.add_row(
        _surface_pattern_predictor_label("baseline_per_account_score"),
        str(int(baseline.get("samples") or 0)),
        _surface_pattern_rate(baseline, "top1"),
        _surface_pattern_rate(baseline, "top2"),
        "—",
        "—",
    )
    for name, candidate in candidates.items():
        table.add_row(
            _surface_pattern_predictor_label(str(name)),
            str(int(candidate.get("samples") or 0)),
            _surface_pattern_rate(candidate, "top1"),
            _surface_pattern_rate(candidate, "top2"),
            _surface_pattern_lift(candidate, "top1"),
            _surface_pattern_lift(candidate, "top2"),
        )

    console.print(table)
    console.print(
        "[dim]Excluded from backtest: "
        + _surface_pattern_ignored_summary(ignored)
        + "[/dim]"
    )
    hints = report.get("sequence_hints") or []
    if hints:
        console.print("[dim]Sequence hints:[/dim]")
        for hint in hints:
            console.print(
                "[dim]- "
                f"{hint.get('feature')} -> {hint.get('surface')} "
                f"(support={hint.get('support')})[/dim]"
            )
    else:
        console.print("[dim]No stable sequence pattern detected.[/dim]")
    console.print("[dim]Read-only: this command does not change live surface ranking.[/dim]")


def _surface_pattern_verdict_summary(verdict: dict) -> str:
    status = verdict.get("status")
    if status == "candidate_lift_observed":
        winner = verdict.get("winner") or "a candidate rule"
        return (
            f"Preliminary signal: {_surface_pattern_predictor_label(str(winner))} beat "
            "the baseline, but live ranking is unchanged."
        )
    if status == "insufficient_data":
        return "Not enough strong clusters to compare rules yet. Keep collecting data."
    if status == "no_significant_lift":
        return (
            "No better rule found. Keep the current per-account learning score; "
            "no live ranking change is recommended."
        )
    return str(verdict.get("message") or "No verdict available.")


def _surface_pattern_predictor_label(name: str) -> str:
    labels = {
        "baseline_per_account_score": "Current per-account learning score",
        "per_account_majority": "Most wins for this account",
        "global_recency": "Last winner anywhere",
        "sequence_features": "Sequence-pattern guess",
    }
    return labels.get(name, name)


def _surface_pattern_ignored_summary(ignored: dict) -> str:
    labels = {
        "timing_match": "timing-only matches",
        "external_possible": "external activity possible",
        "superseded": "superseded attempts",
        "generated_unconfirmed": "generated but unconfirmed",
        "failed_or_no_generation": "failed/no output",
        "no_strong_winner": "no single strong winner",
    }
    parts = [
        f"{label} {int(ignored.get(key) or 0)}"
        for key, label in labels.items()
        if int(ignored.get(key) or 0) > 0
    ]
    return ", ".join(parts) if parts else "none"


def _surface_pattern_rate(metrics: dict, prefix: str) -> str:
    hits = int(metrics.get(f"{prefix}_hits") or 0)
    samples = int(metrics.get("samples") or 0)
    rate = float(metrics.get(f"{prefix}_rate") or 0.0) * 100
    return f"{hits}/{samples} ({rate:.1f}%)"


def _surface_pattern_lift(metrics: dict, prefix: str) -> str:
    hits = int(metrics.get(f"{prefix}_lift_hits") or 0)
    rate = float(metrics.get(f"{prefix}_lift_rate") or 0.0) * 100
    return f"{hits:+d} ({rate:+.1f}pp)"


def _format_optional_timestamp(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _codex_surface_debug_report(
    account: AccountConfig,
    *,
    mode: str,
    wait_seconds: float = 0.0,
    poll_timeout_seconds: float = 0.0,
    poll_interval_seconds: float = 60.0,
) -> dict:
    before = _codex_usage_debug_report(account)
    invocation = kick_invocation_for_account(account, codex_surface=mode)
    kick_started_at = time.time()
    event = kick_account(account, record=False, codex_surface=mode)
    kick_finished_at = time.time()
    if event.timestamp is None:
        event.timestamp = kick_finished_at
    event.codex_surface = mode
    event.codex_attempt_started_at = kick_started_at
    event.codex_attempt_finished_at = kick_finished_at
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    poll = _codex_surface_poll_for_reset_move(
        account,
        event,
        before,
        kick_started_at=kick_started_at,
        kick_finished_at=kick_finished_at,
        timeout_seconds=poll_timeout_seconds,
        interval_seconds=poll_interval_seconds,
    )
    after = poll["final_report"]
    return {
        "label": account.label,
        "provider_home": account.provider_home,
        "mode": mode,
        "cwd": str(invocation.cwd) if invocation.cwd else None,
        "workspace_git_present": invocation.workspace_git_present,
        "command_preview": _codex_surface_command_preview(invocation.command),
        "kick": event.to_dict(),
        "kick_started_at": kick_started_at,
        "kick_finished_at": kick_finished_at,
        "before": _codex_surface_usage_snapshot(before),
        "after": _codex_surface_usage_snapshot(after),
        "delta": _codex_surface_usage_delta(before, after),
        "inferred_anchor": _codex_surface_inferred_anchor(event, after),
        "poll": _codex_surface_poll_payload(poll),
    }


def _codex_surface_poll_for_reset_move(
    account: AccountConfig,
    kick: KickEvent,
    before: dict,
    *,
    kick_started_at: float,
    kick_finished_at: float,
    timeout_seconds: float,
    interval_seconds: float,
) -> dict:
    timeout_seconds = max(0.0, timeout_seconds)
    interval_seconds = max(1.0, interval_seconds)
    deadline = time.time() + timeout_seconds
    observations: list[dict] = []
    first_move: dict | None = None
    final_report: dict | None = None
    while True:
        observed_at = time.time()
        report = _codex_usage_debug_report(account)
        final_report = report
        observation = _codex_surface_poll_observation(
            before,
            report,
            observed_at=observed_at,
            kick=kick,
            kick_started_at=kick_started_at,
            kick_finished_at=kick_finished_at,
        )
        observations.append(observation)
        if observation["moved"]:
            first_move = observation
            break
        now = time.time()
        if now >= deadline:
            break
        time.sleep(min(interval_seconds, deadline - now))
    if final_report is None:
        final_report = _codex_usage_debug_report(account)
    return {
        "timeout_seconds": timeout_seconds,
        "interval_seconds": interval_seconds,
        "observations": observations,
        "first_move": first_move,
        "final_report": final_report,
    }


def _codex_surface_poll_observation(
    before: dict,
    current: dict,
    *,
    observed_at: float,
    kick: KickEvent,
    kick_started_at: float,
    kick_finished_at: float,
) -> dict:
    snapshot = _codex_surface_usage_snapshot(current)
    moved = _codex_surface_reset_clock_moved(
        _codex_surface_usage_snapshot(before),
        snapshot,
    )
    inferred_anchor = _codex_surface_inferred_anchor(kick, current)
    return {
        "observed_at": observed_at,
        "elapsed_from_kick_start_seconds": observed_at - kick_started_at,
        "elapsed_from_kick_finish_seconds": observed_at - kick_finished_at,
        "session_resets_at": snapshot.get("session_resets_at"),
        "session_resets_in_seconds": snapshot.get("session_resets_in_seconds"),
        "session_used_percent": snapshot.get("session_used_percent"),
        "window_anchor_state": snapshot.get("window_anchor_state"),
        "moved": moved,
        "delta": _codex_surface_usage_delta(before, current),
        "inferred_anchor": inferred_anchor,
    }


def _codex_surface_poll_payload(poll: dict) -> dict:
    first_move = poll.get("first_move")
    return {
        "timeout_seconds": poll.get("timeout_seconds"),
        "interval_seconds": poll.get("interval_seconds"),
        "observations": poll.get("observations") or [],
        "moved": first_move is not None,
        "first_move_observed_at": first_move.get("observed_at") if first_move else None,
        "first_move_delay_seconds": (
            first_move.get("elapsed_from_kick_start_seconds") if first_move else None
        ),
        "first_move_delay_from_finish_seconds": (
            first_move.get("elapsed_from_kick_finish_seconds") if first_move else None
        ),
        "first_move": first_move,
    }


def _codex_surface_reset_clock_moved(before: dict, after: dict) -> bool:
    before_reset = before.get("session_resets_at")
    after_reset = after.get("session_resets_at")
    if isinstance(before_reset, (int, float)) and isinstance(after_reset, (int, float)):
        return abs(after_reset - before_reset) > SESSION_KICK_WINDOW_START_GRACE_SECONDS
    before_remaining = before.get("session_resets_in_seconds")
    after_remaining = after.get("session_resets_in_seconds")
    if isinstance(before_remaining, (int, float)) and isinstance(after_remaining, (int, float)):
        return after_remaining > before_remaining + CLAUDE_RECONCILIATION_SESSION_JUMP_SECONDS
    return False


def _codex_surface_command_preview(command: list[str]) -> list[str]:
    if not command:
        return []
    return [*command[:-1], "<prompt>"]


def _codex_surface_usage_snapshot(report: dict) -> dict:
    if report.get("error"):
        return {"error": report.get("error")}
    selected = report.get("selected_bucket") or {}
    status = report.get("selected_status") or {}
    return {
        "primary_used_percent": selected.get("primary_used_percent"),
        "primary_window_minutes": selected.get("primary_window_minutes"),
        "primary_resets_at": selected.get("primary_resets_at"),
        "session_used_percent": status.get("session_used_percent"),
        "session_resets_in_seconds": status.get("session_resets_in_seconds"),
        "session_resets_at": status.get("session_resets_at"),
        "session_window_minutes": status.get("session_window_minutes"),
        "weekly_used_percent": status.get("weekly_used_percent"),
        "weekly_resets_at": status.get("weekly_resets_at"),
        "window_anchor_state": status.get("window_anchor_state"),
        "elapsed_ms": report.get("elapsed_ms"),
    }


def _codex_surface_usage_delta(before: dict, after: dict) -> dict:
    before_snapshot = _codex_surface_usage_snapshot(before)
    after_snapshot = _codex_surface_usage_snapshot(after)
    return {
        "primary_used_percent": _numeric_delta(
            before_snapshot.get("primary_used_percent"),
            after_snapshot.get("primary_used_percent"),
        ),
        "primary_resets_at": _numeric_delta(
            before_snapshot.get("primary_resets_at"),
            after_snapshot.get("primary_resets_at"),
        ),
        "session_used_percent": _numeric_delta(
            before_snapshot.get("session_used_percent"),
            after_snapshot.get("session_used_percent"),
        ),
        "session_resets_at": _numeric_delta(
            before_snapshot.get("session_resets_at"),
            after_snapshot.get("session_resets_at"),
        ),
    }


def _numeric_delta(before: object, after: object) -> float | int | None:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    return after - before


def _codex_surface_inferred_anchor(kick: KickEvent, after: dict) -> dict:
    snapshot = _codex_surface_usage_snapshot(after)
    reset_at = snapshot.get("session_resets_at")
    window_minutes = snapshot.get("session_window_minutes")
    if not isinstance(reset_at, (int, float)) or not isinstance(window_minutes, (int, float)):
        return {"inferred_anchor_at": None, "match_delta_seconds": None}
    inferred_anchor_at = reset_at - (window_minutes * 60)
    return {
        "inferred_anchor_at": inferred_anchor_at,
        "match_delta_seconds": _codex_anchor_match_delta_seconds(kick, inferred_anchor_at),
    }


def _render_codex_surface_debug_report(report: dict) -> None:
    table = Table(title="Codex Exec Surface Diagnostic", show_header=True)
    table.add_column("Field")
    table.add_column("Value")
    kick = report.get("kick") or {}
    before = report.get("before") or {}
    after = report.get("after") or {}
    delta = report.get("delta") or {}
    inferred_anchor = report.get("inferred_anchor") or {}
    poll = report.get("poll") or {}
    table.add_row("Account", str(report.get("label")))
    table.add_row("Mode", str(report.get("mode")))
    table.add_row("CWD", str(report.get("cwd") or "inherited"))
    table.add_row("Workspace git", str(report.get("workspace_git_present")))
    table.add_row("Kick success", str(kick.get("success")))
    table.add_row("Kick error", str(kick.get("error") or "-"))
    table.add_row("Response", str(kick.get("response_text") or "-"))
    table.add_row("Tokens", _codex_surface_token_summary(kick))
    table.add_row("Before session", _codex_surface_session_summary(before))
    table.add_row("After session", _codex_surface_session_summary(after))
    table.add_row("Delta", json.dumps(delta, sort_keys=True))
    table.add_row("Poll result", _codex_surface_poll_summary(poll))
    table.add_row("Inferred anchor", json.dumps(inferred_anchor, sort_keys=True))
    console.print(table)


def _codex_surface_token_summary(kick: dict) -> str:
    parts = []
    for key, label in (
        ("input_tokens", "in"),
        ("output_tokens", "out"),
        ("total_tokens", "total"),
    ):
        value = kick.get(key)
        if value is not None:
            parts.append(f"{label}={value}")
    return ", ".join(parts) if parts else "-"


def _codex_surface_poll_summary(poll: dict) -> str:
    if not poll:
        return "not run"
    observations = poll.get("observations") or []
    delay = poll.get("first_move_delay_seconds")
    delay_text = f"{delay:.1f}s" if isinstance(delay, (int, float)) else "—"
    return (
        f"moved={bool(poll.get('moved'))}; "
        f"first_move_delay={delay_text}; "
        f"observations={len(observations)}; "
        f"timeout={float(poll.get('timeout_seconds') or 0.0):.1f}s; "
        f"interval={float(poll.get('interval_seconds') or 0.0):.1f}s"
    )


def _codex_surface_session_summary(snapshot: dict) -> str:
    if snapshot.get("error"):
        return str(snapshot["error"])
    return (
        f"used={snapshot.get('session_used_percent')}%, "
        f"seconds={snapshot.get('session_resets_in_seconds')}, "
        f"resets_at={snapshot.get('session_resets_at')}, "
        f"anchor={snapshot.get('window_anchor_state')}"
    )


def _codex_usage_bucket_debug(key: str, value: dict, issue: str | None) -> dict:
    primary = value.get("primary") if isinstance(value.get("primary"), dict) else {}
    secondary = value.get("secondary") if isinstance(value.get("secondary"), dict) else {}
    limit_id = value.get("limitId") if isinstance(value.get("limitId"), str) else None
    limit_name = value.get("limitName") if isinstance(value.get("limitName"), str) else None
    return {
        "key": key,
        "limit_id": limit_id,
        "limit_name": limit_name,
        "display_name": _codex_usage_bucket_display_name(
            {"key": key, "limit_id": limit_id, "limit_name": limit_name}
        ),
        "plan_type": value.get("planType") if isinstance(value.get("planType"), str) else None,
        "rate_limit_reached_type": value.get("rateLimitReachedType"),
        "valid": issue is None,
        "issue": issue,
        "primary_used_percent": primary.get("usedPercent"),
        "primary_window_minutes": primary.get("windowDurationMins"),
        "primary_resets_at": primary.get("resetsAt"),
        "secondary_used_percent": secondary.get("usedPercent"),
        "secondary_window_minutes": secondary.get("windowDurationMins"),
        "secondary_resets_at": secondary.get("resetsAt"),
    }


def _codex_usage_bucket_display_name(bucket: dict | None) -> str:
    if not isinstance(bucket, dict):
        return "-"
    return _codex_appserver_bucket_display_name(bucket)


def _debug_percent(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:g}%"
    return "-"


# ---------------------------------------------------------------------------
# tk setup / init
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--rename-label",
    "rename_labels",
    multiple=True,
    metavar="LABEL",
    help="Opt saved account labels into provider-first format.",
)
@click.option("--dry-run", is_flag=True, help="Show discovered config changes without writing")
@click.option("--no-daemon-prompt", is_flag=True, hidden=True)
def setup(rename_labels: tuple[str, ...], dry_run: bool, no_daemon_prompt: bool):
    """Auto-discover local accounts and save config."""
    progress_active = _setup_progress("Reading saved TokenKick config")
    if not progress_active:
        console.print("[bold]TokenKick Setup[/bold]\n")
    if dry_run and rename_labels:
        _setup_progress(None)
        console.print("[red]Use either --dry-run or --rename-label, not both.[/red]")
        raise click.exceptions.Exit(1)

    existing = Config.load()
    if not dry_run:
        _setup_progress("Checking saved account migrations")
        existing = _repair_codex_home_identity_drift_if_needed(
            _migrate_codex_home_keys_if_needed(existing)
        )
    if rename_labels:
        _setup_progress(None)
        if progress_active:
            console.print("[bold]TokenKick Setup[/bold]\n")
        _setup_rename_labels(existing, rename_labels)
        return

    _setup_progress("Discovering accounts and reading status")
    accounts, statuses, _discovered, summary, _new_accounts = _load_account_status_pairs(
        existing,
        prepare_claude_setup=not dry_run,
    )
    if not accounts:
        _setup_progress(None)
        if progress_active:
            console.print("[bold]TokenKick Setup[/bold]\n")
        console.print(f"[yellow]{summary}[/yellow]")
        console.print("[dim]Log in with Codex/CodexBar, then run [bold]tk setup[/bold] again.[/dim]")
        return

    _setup_progress("Checking duplicate and unhealthy homes")
    setup_accounts = _with_setup_auto_kick_defaults(accounts, existing)
    setup_accounts, hidden_duplicate_labels = _hide_unusable_duplicate_codex_homes(
        setup_accounts,
        statuses,
        existing,
    )
    _setup_progress("Preparing setup summary")
    _setup_progress(None)
    if progress_active:
        console.print("[bold]TokenKick Setup[/bold]\n")
    console.print(f"[green]{summary}[/green]")
    for account in accounts:
        console.print(f"[dim]  - {account.label} ({account.provider}, {account.source.value})[/dim]")
    console.print("[dim]Hide accounts from status with [bold]tk accounts hide <label>[/bold].[/dim]")
    if not dry_run:
        _apply_claude_direct_usage_setup_default(existing, accounts)

    config = replace(existing, accounts=setup_accounts)

    if dry_run:
        console.print("\n[bold]Dry run: config would not be saved.[/bold]")
        for line in _setup_config_diff(existing.accounts, setup_accounts):
            console.print(f"[dim]{line}[/dim]")
        visible_accounts, visible_statuses = _filter_status_pairs_by_visibility(
            setup_accounts,
            statuses,
            show_all=False,
        )
        _render_status_table(visible_statuses, visible_accounts, config)
        return

    _migrate_pending_kick_keys(existing.accounts, setup_accounts)
    config.save()
    _save_status_cache(
        setup_accounts,
        _cache_statuses_by_key_from_pairs(setup_accounts, statuses),
    )
    console.print(f"\n[green bold]Config saved to {CONFIG_FILE}[/green bold]\n")
    _print_setup_auto_kick_risk_note()
    _print_setup_codex_home_warnings(setup_accounts, statuses, hidden_duplicate_labels)
    _print_setup_macos_codex_permission_note(setup_accounts)
    visible_accounts, visible_statuses = _filter_status_pairs_by_visibility(
        setup_accounts,
        statuses,
        show_all=False,
    )
    _render_status_table(visible_statuses, visible_accounts, config)
    if not config.notifications.enabled:
        console.print("\n[dim]Enable notifications with tk notify --ntfy <topic>.[/dim]")
    if not no_daemon_prompt:
        _maybe_prompt_start_daemon_after_setup()


def _print_setup_auto_kick_risk_note() -> None:
    console.print(
        "[dim]Auto-kick is off by default. If you enable it later, TokenKick can "
        "send minimal provider requests automatically on your accounts; provider "
        "terms and any consequences are your responsibility. Use at your own risk.[/dim]"
    )


def _print_setup_codex_home_warnings(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    hidden_duplicate_labels: list[str],
) -> None:
    duplicate_groups = _duplicate_codex_home_groups(accounts)
    if not duplicate_groups:
        return
    statuses_by_label = {status.label: status for status in statuses}
    for identity, group in duplicate_groups:
        usable = [
            account.label
            for account in group
            if _setup_duplicate_status_usable(statuses_by_label.get(account.label))
        ]
        unusable = [
            account.label
            for account in group
            if not _setup_duplicate_status_usable(statuses_by_label.get(account.label))
        ]
        console.print(f"\n[yellow]Multiple Codex homes found for {identity}.[/yellow]")
        console.print("[dim]Only enable auto-kick for homes that are currently usable.[/dim]")
        if usable:
            console.print(f"[dim]Using healthy home(s): {', '.join(usable)}.[/dim]")
        if unusable:
            console.print(f"[dim]Unusable duplicate home(s): {', '.join(unusable)}.[/dim]")
    if hidden_duplicate_labels:
        console.print(
            "[dim]Hidden from normal status: "
            f"{', '.join(hidden_duplicate_labels)}. They remain saved.[/dim]"
        )
    console.print(
        '[dim]Inspect details with tk accounts detail "<label>"; restore after re-auth with '
        'tk accounts show "<label>".[/dim]'
    )


def _hide_unusable_duplicate_codex_homes(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    existing: Config,
) -> tuple[list[AccountConfig], list[str]]:
    duplicate_groups = _duplicate_codex_home_groups(accounts)
    if not duplicate_groups:
        return accounts, []
    statuses_by_label = {status.label: status for status in statuses}
    existing_keys = {account_key_string(account) for account in existing.accounts}
    hidden_labels: list[str] = []
    updates_by_label: dict[str, AccountConfig] = {}
    for _identity, group in duplicate_groups:
        has_usable_home = any(
            _setup_duplicate_status_usable(statuses_by_label.get(account.label))
            for account in group
        )
        if not has_usable_home:
            continue
        for account in group:
            if account_key_string(account) in existing_keys:
                continue
            status = statuses_by_label.get(account.label)
            if _setup_duplicate_status_unusable(status):
                updates_by_label[account.label] = replace(account, visible=False)
                hidden_labels.append(account.label)
    if not updates_by_label:
        return accounts, []
    return [updates_by_label.get(account.label, account) for account in accounts], hidden_labels


def _setup_duplicate_status_usable(status: AccountStatus | None) -> bool:
    return (
        status is not None
        and not status.stale
        and status.error is None
        and status.state != AccountState.UNKNOWN
    )


def _setup_duplicate_status_unusable(status: AccountStatus | None) -> bool:
    return not _setup_duplicate_status_usable(status)


def _duplicate_codex_home_groups(
    accounts: list[AccountConfig],
) -> list[tuple[str, list[AccountConfig]]]:
    grouped: dict[str, list[AccountConfig]] = {}
    for account in accounts:
        if account.provider != "codex":
            continue
        identity = (
            account.identity_email
            or account.codexbar_account
            or account.identity_provider_id
            or ""
        )
        identity = identity.strip().lower()
        if not identity:
            continue
        grouped.setdefault(identity, []).append(account)
    return [
        (identity, group)
        for identity, group in sorted(grouped.items())
        if len({account.provider_home or account.session_path or account.label for account in group}) > 1
    ]


def _print_setup_macos_codex_permission_note(accounts: list[AccountConfig]) -> None:
    if sys.platform != "darwin":
        return
    if not any(account.provider == "codex" and account.provider_home for account in accounts):
        return
    console.print(
        "\n[cyan]macOS note:[/cyan] [dim]Codex may ask whether your terminal can control "
        "Codex Computer Use.app. Allow = full status/kicks; Don't Allow = status may stay stale.[/dim]"
    )


def _setup_should_prompt_start_daemon() -> bool:
    if os.environ.get("TK_NO_INTERACTIVE"):
        return False
    if not sys.stdin.isatty():
        return False
    pid = _read_daemon_pid()
    return not (pid is not None and _pid_is_running(pid))


def _maybe_prompt_start_daemon_after_setup() -> None:
    if not _setup_should_prompt_start_daemon():
        return
    console.print("\n[dim]TokenKick needs the background daemon to kick windows automatically.[/dim]")
    if _confirm_prompt("Start the TokenKick daemon now?", default=True):
        _start_daemon_background()
    else:
        console.print("[dim]Start it later with tk daemon --background.[/dim]")


def _setup_config_diff(
    existing_accounts: list[AccountConfig],
    proposed_accounts: list[AccountConfig],
) -> list[str]:
    existing_by_label = {account.label: account for account in existing_accounts}
    proposed_by_label = {account.label: account for account in proposed_accounts}
    lines: list[str] = []
    for account in proposed_accounts:
        previous = existing_by_label.get(account.label)
        if previous is None:
            lines.append(f"+ {account.label} ({account.provider}, {account.source.value})")
        elif previous.to_dict() != account.to_dict():
            lines.append(f"~ {account.label} ({account.provider}, {account.source.value})")
        else:
            lines.append(f"= {account.label} ({account.provider}, {account.source.value})")
    for account in existing_accounts:
        if account.label not in proposed_by_label:
            lines.append(f"- {account.label} ({account.provider}, {account.source.value})")
    if not lines:
        lines.append("= no account changes")
    return lines


def _setup_rename_labels(config: Config, labels: tuple[str, ...]) -> None:
    if not config.accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return
    _updated_config, renamed, not_renamed = _rename_saved_labels(config, labels)
    if renamed:
        for old_label, new_label in renamed.items():
            console.print(f'[green]Renamed "{old_label}" -> "{new_label}".[/green]')
        console.print(f"[dim]Backup: {LABEL_FORMAT_BACKUP_FILE}[/dim]")
    if not_renamed:
        for label in not_renamed:
            console.print(
                f'[yellow]Could not rename "{label}": no exact saved label with provider identity found.[/yellow]'
            )
    if renamed:
        console.print(f"\n[green bold]Config saved to {CONFIG_FILE}[/green bold]")


def _apply_claude_direct_usage_setup_default(
    config: Config,
    accounts: list[AccountConfig],
) -> None:
    if not any(account.provider == "claude" for account in accounts):
        return
    if not config.claude.direct_usage_enabled and not config.claude.direct_usage_explicit:
        config.claude.direct_usage_enabled = True
        console.print("[dim]Claude direct usage enabled for status reads.[/dim]")


@cli.command("refresh-cache", hidden=True)
def refresh_cache():
    """Refresh daemon status cache in a detached helper process."""
    try:
        config = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)
        config = _migrate_provider_first_labels_if_needed(config, emit_notice=False)
        config = _migrate_codex_home_keys_if_needed(config, emit_notice=False)
        config = _repair_codex_home_identity_drift_if_needed(config, emit_notice=False)
        _refresh_status_cache(config, daemon_log=True)
    finally:
        _release_status_refresh_lock()


@cli.command()
@click.pass_context
def init(ctx):
    """Deprecated alias for `tk setup`."""
    console.print("[yellow]tk init is deprecated; use tk setup.[/yellow]\n")
    ctx.invoke(setup, rename_labels=(), dry_run=False, no_daemon_prompt=False)


# ---------------------------------------------------------------------------
# tk antigravity
# ---------------------------------------------------------------------------

@cli.group("antigravity", hidden=True)
def antigravity_group():
    """Hidden Antigravity diagnostics."""


@antigravity_group.command("probe-kick", hidden=True)
@click.option(
    "--family",
    type=click.Choice(("gemini", "claude-gpt")),
    required=True,
    help="Antigravity quota family to test.",
)
@click.option("--account", "account_label", help="Antigravity account label when more than one exists.")
@click.option("--model", help="Override the agy model label used for the probe request.")
@click.option(
    "--timeout",
    "timeout_seconds",
    type=click.IntRange(10, 600),
    default=120,
    show_default=True,
    help="Seconds to allow the agy print request.",
)
@click.option("--yes", is_flag=True, help="Confirm this quota-consuming diagnostic.")
@click.option("--json-output", "as_json", is_flag=True, help="Output sanitized JSON.")
def antigravity_probe_kick(
    family: str,
    account_label: str | None,
    model: str | None,
    timeout_seconds: int,
    yes: bool,
    as_json: bool,
):
    """Run one evidence-only Antigravity probe request and compare quota buckets."""
    if as_json and not yes:
        raise click.ClickException(
            "Antigravity probe-kick --json-output requires --yes because it can spend quota."
        )
    config = Config.load()
    account = _antigravity_probe_account_or_exit(account_label, _load_accounts(config))
    normalized_family = _normalize_antigravity_probe_family(family)
    selected_model = model or ANTIGRAVITY_PROBE_DEFAULT_MODELS[normalized_family]
    if not yes:
        console.print(
            "[yellow]This is an active Antigravity diagnostic. It runs one agy "
            "non-interactive prompt and can consume quota or anchor a 5-hour window.[/yellow]"
        )
        console.print("[dim]Antigravity remains monitor-only; this does not enable tk kick or auto-kick.[/dim]")
        if not click.confirm(
            f'Run Antigravity probe for "{account.label}" using {selected_model}?',
            default=False,
        ):
            console.print("[dim]Antigravity probe-kick cancelled.[/dim]")
            return

    report = _antigravity_probe_kick_report(
        account,
        family=normalized_family,
        model=selected_model,
        timeout_seconds=timeout_seconds,
    )
    _append_antigravity_probe_evidence(report)
    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        _render_antigravity_probe_report(report)
    if report["verdict"] == "failed":
        raise click.exceptions.Exit(1)


def _antigravity_probe_account_or_exit(
    label: str | None,
    accounts: list[AccountConfig],
) -> AccountConfig:
    antigravity_accounts = [account for account in accounts if account.provider == "antigravity"]
    if label is not None:
        account = next((candidate for candidate in antigravity_accounts if candidate.label == label), None)
        if account is None:
            raise click.ClickException(f'Antigravity account "{label}" not found.')
        return account
    if not antigravity_accounts:
        raise click.ClickException("No Antigravity account configured. Run tk setup after logging in.")
    if len(antigravity_accounts) > 1:
        labels = ", ".join(account.label for account in antigravity_accounts)
        raise click.ClickException(f"Multiple Antigravity accounts configured; pass --account. Found: {labels}")
    return antigravity_accounts[0]


def _normalize_antigravity_probe_family(family: str) -> str:
    return family.replace("-", "_")


def _antigravity_probe_evidence_file() -> Path:
    return CONFIG_DIR / "antigravity-probe-evidence.jsonl"


def _antigravity_probe_kick_report(
    account: AccountConfig,
    *,
    family: str,
    model: str,
    timeout_seconds: int,
) -> dict:
    identity_email = _verified_antigravity_probe_identity(account)
    probe_account = replace(
        account,
        source=DataSource.ANTIGRAVITY_CLI,
        identity_email=identity_email,
        codexbar_account=account.codexbar_account or identity_email,
    )
    before_status = _read_antigravity_probe_status(probe_account, phase="before")
    before_windows = _antigravity_probe_family_windows(before_status, family)

    request = _run_antigravity_probe_request(
        family=family,
        model=model,
        timeout_seconds=timeout_seconds,
    )

    try:
        after_status = _read_antigravity_probe_status(probe_account, phase="after")
        after_windows = _antigravity_probe_family_windows(after_status, family)
        comparison = _antigravity_probe_compare(before_windows, after_windows)
    except click.ClickException as exc:
        after_windows = {"session": None, "weekly": None}
        comparison = {
            "session_changed": False,
            "weekly_changed": False,
            "session_used_delta": None,
            "weekly_used_delta": None,
            "session_reset_delta_seconds": None,
            "weekly_reset_delta_seconds": None,
            "after_read_error": str(exc),
        }

    verdict = _antigravity_probe_verdict(request, comparison)
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account": {
            "label": account.label,
            "account_key": account_key_string(account),
            "identity_email": identity_email,
        },
        "family": family.replace("_", "-"),
        "model": model,
        "prompt_id": ANTIGRAVITY_PROBE_PROMPT_ID,
        "before": _antigravity_probe_window_payload(before_windows),
        "after": _antigravity_probe_window_payload(after_windows),
        "request": request,
        "comparison": comparison,
        "bucket_changed": bool(comparison.get("session_changed")),
        "weekly_bucket_changed": bool(comparison.get("weekly_changed")),
        "verdict": verdict,
    }


def _verified_antigravity_probe_identity(account: AccountConfig) -> str:
    cli_identity = read_antigravity_cli_identity()
    if not cli_identity:
        raise click.ClickException(
            "Antigravity CLI identity could not be verified. Log in with agy and run setup again."
        )
    expected = (account.identity_email or account.codexbar_account or "").strip().lower()
    if expected and expected != cli_identity.strip().lower():
        raise click.ClickException(
            "Antigravity CLI identity mismatch: "
            f'configured "{expected}", agy CLI "{cli_identity}".'
        )
    return cli_identity


def _read_antigravity_probe_status(account: AccountConfig, *, phase: str) -> AccountStatus:
    status = fetch_status(account)
    if not has_complete_antigravity_quota_windows(status):
        reason = status.error or "Antigravity did not return all four quota buckets."
        raise click.ClickException(f"Cannot read complete Antigravity quota buckets {phase}: {reason}")
    return status


def _antigravity_probe_family_windows(status: AccountStatus, family: str) -> dict[str, dict]:
    windows = status.quota_windows
    if not isinstance(windows, list):
        raise click.ClickException("Antigravity status has no quota_windows.")
    matched: dict[str, dict] = {}
    for window in windows:
        if not isinstance(window, dict) or window.get("family") != family:
            continue
        kind = window.get("window_kind")
        if kind in {"session", "weekly"}:
            matched[str(kind)] = window
    if set(matched) != {"session", "weekly"}:
        raise click.ClickException(f"Antigravity quota family {family} is incomplete.")
    return matched


def _run_antigravity_probe_request(
    *,
    family: str,
    model: str,
    timeout_seconds: int,
) -> dict:
    binary = antigravity_cli_binary()
    started_at = time.monotonic()
    if not binary:
        return {
            "success": False,
            "error": "agy CLI executable not found.",
            "duration_seconds": 0.0,
            "returncode": None,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
    workspace = CONFIG_DIR / "antigravity-probe-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    command = [
        binary,
        "--log-file",
        os.devnull,
        "--model",
        model,
        "--print-timeout",
        f"{timeout_seconds}s",
        "--print",
        ANTIGRAVITY_PROBE_PROMPT,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 30,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "agy probe request timed out.",
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "returncode": None,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
    except OSError as exc:
        return {
            "success": False,
            "error": _sanitize_antigravity_probe_error(f"agy probe request failed: {exc}"),
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "returncode": None,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
    return {
        "success": result.returncode == 0,
        "error": None
        if result.returncode == 0
        else f"agy probe request exited with code {result.returncode}.",
        "duration_seconds": round(time.monotonic() - started_at, 3),
        "returncode": result.returncode,
        "stdout_bytes": len(result.stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(result.stderr.encode("utf-8", errors="replace")),
        "family": family.replace("_", "-"),
    }


def _antigravity_probe_compare(before: dict[str, dict], after: dict[str, dict]) -> dict:
    session = _antigravity_probe_window_compare(before["session"], after["session"])
    weekly = _antigravity_probe_window_compare(before["weekly"], after["weekly"])
    return {
        "session_changed": session["changed"],
        "weekly_changed": weekly["changed"],
        "session_used_delta": session["used_delta"],
        "weekly_used_delta": weekly["used_delta"],
        "session_reset_delta_seconds": session["reset_delta_seconds"],
        "weekly_reset_delta_seconds": weekly["reset_delta_seconds"],
    }


def _antigravity_probe_window_compare(before: dict, after: dict) -> dict:
    before_used = _antigravity_probe_float(before.get("used_percent"))
    after_used = _antigravity_probe_float(after.get("used_percent"))
    before_reset = _antigravity_probe_float(before.get("resets_at"))
    after_reset = _antigravity_probe_float(after.get("resets_at"))
    used_delta = (
        None if before_used is None or after_used is None else round(after_used - before_used, 6)
    )
    reset_delta = (
        None if before_reset is None or after_reset is None else round(after_reset - before_reset, 3)
    )
    changed = False
    if used_delta is not None and abs(used_delta) > 0.0001:
        changed = True
    if (
        reset_delta is not None
        and abs(reset_delta) > ANTIGRAVITY_PROBE_RESET_CHANGE_TOLERANCE_SECONDS
    ):
        changed = True
    return {
        "changed": changed,
        "used_delta": used_delta,
        "reset_delta_seconds": reset_delta,
    }


def _antigravity_probe_verdict(request: dict, comparison: dict) -> str:
    if not request.get("success"):
        return "failed"
    if comparison.get("after_read_error"):
        return "failed"
    if comparison.get("session_changed"):
        return "proved"
    return "inconclusive"


def _antigravity_probe_window_payload(windows: dict[str, dict | None]) -> dict:
    return {
        "session": _antigravity_probe_single_window_payload(windows.get("session")),
        "weekly": _antigravity_probe_single_window_payload(windows.get("weekly")),
    }


def _antigravity_probe_single_window_payload(window: dict | None) -> dict | None:
    if not isinstance(window, dict):
        return None
    resets_at = _antigravity_probe_float(window.get("resets_at"))
    return {
        "id": window.get("id"),
        "title": window.get("title"),
        "used_percent": _antigravity_probe_float(window.get("used_percent")),
        "resets_at": resets_at,
        "resets_at_utc": _antigravity_probe_utc(resets_at),
        "resets_in_seconds": _antigravity_probe_int(window.get("resets_in_seconds")),
        "window_minutes": _antigravity_probe_int(window.get("window_minutes")),
    }


def _antigravity_probe_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _antigravity_probe_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _antigravity_probe_utc(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_antigravity_probe_evidence(report: dict) -> None:
    record = _antigravity_probe_evidence_record(report)

    def append_line(current: str) -> str:
        prefix = current if not current or current.endswith("\n") else f"{current}\n"
        return prefix + json.dumps(record, sort_keys=True) + "\n"

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    locked_update_text(_antigravity_probe_evidence_file(), append_line)


def _antigravity_probe_evidence_record(report: dict) -> dict:
    return {
        "schema_version": report["schema_version"],
        "timestamp": report["timestamp"],
        "account": report["account"],
        "family": report["family"],
        "model": report["model"],
        "prompt_id": report["prompt_id"],
        "before": report["before"],
        "after": report["after"],
        "request": {
            "success": report["request"].get("success"),
            "returncode": report["request"].get("returncode"),
            "duration_seconds": report["request"].get("duration_seconds"),
            "stdout_bytes": report["request"].get("stdout_bytes"),
            "stderr_bytes": report["request"].get("stderr_bytes"),
            "error": _sanitize_antigravity_probe_error(report["request"].get("error")),
        },
        "comparison": report["comparison"],
        "bucket_changed": report["bucket_changed"],
        "weekly_bucket_changed": report["weekly_bucket_changed"],
        "verdict": report["verdict"],
    }


def _sanitize_antigravity_probe_error(value) -> str | None:
    if value is None:
        return None
    sanitized = str(value)
    sanitized = re.sub(
        r"(?i)\b(csrf[_-]?token|access[_-]?token|refresh[_-]?token|token)=\S+",
        r"\1=<redacted>",
        sanitized,
    )
    sanitized = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", sanitized)
    return sanitized[:240]


def _render_antigravity_probe_report(report: dict) -> None:
    account = report["account"]
    console.print("[bold]Antigravity Probe Kick Evidence[/bold]")
    console.print(f"[dim]Account:[/dim] {account['label']} ({account['identity_email']})")
    console.print(f"[dim]Family:[/dim] {report['family']}")
    console.print(f"[dim]Model:[/dim] {report['model']}")
    table = Table(show_header=True)
    table.add_column("Bucket")
    table.add_column("Before used", justify="right")
    table.add_column("After used", justify="right")
    table.add_column("Before reset")
    table.add_column("After reset")
    table.add_column("Changed")
    comparison = report["comparison"]
    for kind, changed_key in (("session", "session_changed"), ("weekly", "weekly_changed")):
        before = report["before"].get(kind) or {}
        after = report["after"].get(kind) or {}
        table.add_row(
            "5h" if kind == "session" else "weekly",
            _antigravity_probe_percent(before.get("used_percent")),
            _antigravity_probe_percent(after.get("used_percent")),
            before.get("resets_at_utc") or "—",
            after.get("resets_at_utc") or "—",
            "yes" if comparison.get(changed_key) else "no",
        )
    console.print(table)
    request = report["request"]
    if not request.get("success"):
        console.print(f"[red]Request failed:[/red] {request.get('error') or 'unknown error'}")
    after_read_error = comparison.get("after_read_error")
    if after_read_error:
        console.print(f"[red]After-read failed:[/red] {after_read_error}")
    console.print(f"[bold]Verdict:[/bold] {report['verdict']}")
    console.print(
        "[dim]Evidence saved to "
        f"{_antigravity_probe_evidence_file()}; no prompt text or raw provider output was saved.[/dim]"
    )


def _antigravity_probe_percent(value) -> str:
    numeric = _antigravity_probe_float(value)
    if numeric is None:
        return "—"
    return f"{numeric:.3f}%"


# ---------------------------------------------------------------------------
# tk claude
# ---------------------------------------------------------------------------

@cli.group()
def claude():
    """Configure Claude provider behavior."""


@claude.group("direct-usage")
def claude_direct_usage():
    """Configure Claude direct /usage probing."""


def _claude_direct_usage_payload(config: Config) -> dict:
    return {
        "claude": {
            "direct_usage_enabled": config.claude.direct_usage_enabled,
            "direct_usage_explicit": config.claude.direct_usage_explicit,
        }
    }


@claude_direct_usage.command("enable")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def claude_direct_usage_enable(as_json: bool):
    """Enable Claude direct /usage globally."""
    if _app_json_requested(as_json):
        _run_mutation_json(lambda: _set_global_claude_direct_usage(True), _claude_direct_usage_payload)
        return
    _set_global_claude_direct_usage(True)


@claude_direct_usage.command("disable")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def claude_direct_usage_disable(as_json: bool):
    """Disable Claude direct /usage globally."""
    if _app_json_requested(as_json):
        _run_mutation_json(lambda: _set_global_claude_direct_usage(False), _claude_direct_usage_payload)
        return
    _set_global_claude_direct_usage(False)


# ---------------------------------------------------------------------------
# tk notify
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--ntfy", "ntfy_topic", metavar="TOPIC", help="Enable ntfy.sh notifications")
@click.option(
    "--telegram",
    nargs=2,
    metavar="TOKEN CHAT_ID",
    help="Enable Telegram push notifications",
)
@click.option(
    "--telegram-remote",
    nargs=2,
    metavar="TOKEN CHAT_ID",
    help="Save Telegram remote credentials without enabling Telegram push notifications",
)
@click.option(
    "--disable-backend",
    type=click.Choice(NOTIFICATION_BACKENDS),
    metavar="BACKEND",
    help="Disable one notification backend but keep its credentials",
)
@click.option(
    "--backend",
    "test_backend",
    type=click.Choice(("all", *NOTIFICATION_BACKENDS)),
    default="all",
    show_default=True,
    help="For tk notify test, send only through one backend.",
)
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
@click.argument("action", required=False)
def notify(
    ntfy_topic: str | None,
    telegram: tuple[str, str] | None,
    telegram_remote: tuple[str, str] | None,
    disable_backend: str | None,
    test_backend: str,
    as_json: bool,
    action: str | None,
):
    """Configure notification settings without running setup."""
    as_json = _app_json_requested(as_json)
    if action is not None:
        if action != "test":
            message = f'Unknown notify action "{action}". Use: tk notify test'
            if as_json:
                emit_app_error(ERROR_USAGE, message)
                sys.exit(2)
            console.print(f"[red]{message}[/red]")
            return
        if ntfy_topic or telegram or telegram_remote or disable_backend:
            message = (
                "Use either tk notify test [--backend all|ntfy|telegram] or "
                "configure/change a backend, not both."
            )
            if as_json:
                emit_app_error(ERROR_USAGE, message)
                sys.exit(2)
            console.print(f"[red]{message}[/red]")
            return
        with _console_redirected_to_stderr() if as_json else nullcontext():
            delivered, failure_reason = _send_test_notification(
                Config.load().notifications,
                test_backend,
                notify_test,
            )
        backend_label = "Telegram" if test_backend == "telegram" else test_backend
        target_message = (
            "Test notification" if test_backend == "all" else f"{backend_label} test notification"
        )
        if as_json:
            if delivered:
                payload = {"action": "test", "delivered": True}
                if test_backend != "all":
                    payload["backend"] = test_backend
                emit_app_success(
                    payload,
                    message=f"{target_message} sent.",
                )
            else:
                payload = {"action": "test", "delivered": False}
                if test_backend != "all":
                    payload.update({"backend": test_backend, "reason": failure_reason})
                emit_app_error(
                    "notification_test_failed",
                    f"{target_message} failed.",
                    payload=payload,
                )
                sys.exit(1)
            return
        if delivered:
            console.print(f"[green]{target_message} sent.[/green]")
        else:
            console.print(f"[red]{target_message} failed.[/red]")
        return

    if test_backend != "all":
        message = "--backend is only valid with tk notify test."
        if as_json:
            emit_app_error(ERROR_USAGE, message)
            sys.exit(2)
        console.print(f"[red]{message}[/red]")
        return

    action_count = sum(bool(value) for value in (ntfy_topic, telegram, telegram_remote, disable_backend))
    if action_count != 1:
        message = (
            "Choose exactly one: --ntfy <topic>, --telegram <token> <chat_id>, "
            "--telegram-remote <token> <chat_id>, or --disable-backend <backend>."
        )
        if as_json:
            emit_app_error(ERROR_USAGE, message)
            sys.exit(2)
        console.print(f"[red]{message}[/red]")
        return

    config = Config.load()
    if ntfy_topic:
        config.notifications = replace(
            config.notifications,
            enabled=True,
            backend="ntfy",
            ntfy_topic=ntfy_topic,
            enabled_backends=_with_enabled_notification_backend(config.notifications, "ntfy"),
        )
        success_message = "ntfy notifications enabled."
    elif telegram:
        token, chat_id = telegram
        config.notifications = replace(
            config.notifications,
            enabled=True,
            backend="telegram",
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            enabled_backends=_with_enabled_notification_backend(config.notifications, "telegram"),
        )
        success_message = "Telegram notifications enabled."
    elif telegram_remote:
        token, chat_id = telegram_remote
        backends = _without_enabled_notification_backend(config.notifications, "telegram")
        config.notifications = replace(
            config.notifications,
            enabled=bool(backends),
            backend=_primary_notification_backend(backends, config.notifications.backend),
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            enabled_backends=backends,
        )
        success_message = "Telegram remote credentials saved; Telegram notifications disabled."
    else:
        assert disable_backend is not None
        backends = _without_enabled_notification_backend(config.notifications, disable_backend)
        config.notifications = replace(
            config.notifications,
            enabled=bool(backends),
            backend=_primary_notification_backend(backends, config.notifications.backend),
            enabled_backends=backends,
        )
        backend_label = "Telegram" if disable_backend == "telegram" else disable_backend
        success_message = f"{backend_label} notifications disabled."

    if not as_json:
        console.print(f"[green]{success_message}[/green]")

    config.save()

    if as_json:
        emit_app_success(
            _account_notifications_payload(config, _load_accounts(config)),
            message=success_message,
        )


# ---------------------------------------------------------------------------
# tk remote telegram
# ---------------------------------------------------------------------------

def _telegram_remote_log(event: str, **fields) -> None:
    console.print(_format_log_line(event, **fields), markup=False)


def _telegram_remote_executable_path() -> str:
    return _daemon_executable_path() or sys.argv[0]


def _read_telegram_remote_pid() -> int | None:
    if not TELEGRAM_REMOTE_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE)
    return info.pid if info is not None else None


def _read_telegram_remote_version() -> str | None:
    if not TELEGRAM_REMOTE_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE)
    return info.version if info is not None else None


def _prepare_telegram_remote_pidfile_for_start() -> int | None:
    if not TELEGRAM_REMOTE_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE)
    if info is None:
        try:
            TELEGRAM_REMOTE_PID_FILE.unlink()
        except OSError:
            pass
        return None
    if _pid_is_running(info.pid):
        return info.pid
    try:
        TELEGRAM_REMOTE_PID_FILE.unlink()
    except OSError:
        pass
    return None


def _write_owned_telegram_remote_pidfile(pid: int) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TELEGRAM_REMOTE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_daemon_pidfile(
        TELEGRAM_REMOTE_PID_FILE,
        pid,
        executable=_telegram_remote_executable_path(),
    )


def _remove_telegram_remote_pidfile_if_owned(pid: int) -> None:
    info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE)
    if info is None or info.pid != pid:
        return
    try:
        TELEGRAM_REMOTE_PID_FILE.unlink()
    except OSError:
        pass


def _telegram_remote_uptime_human() -> str:
    try:
        started_at = TELEGRAM_REMOTE_PID_FILE.stat().st_mtime
    except OSError:
        return "unknown"
    return _format_duration(int(time.time() - started_at))


def _telegram_remote_status_payload(config: Config | None = None) -> dict:
    pidfile_exists = TELEGRAM_REMOTE_PID_FILE.exists()
    info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE) if pidfile_exists else None
    pid = info.pid if info is not None else None
    version = info.version if info is not None else None
    executable = info.executable if info is not None else None
    running = bool(pid and _pid_is_running(pid))
    installed = installed_version()
    current_executable = _telegram_remote_executable_path()
    executable_match = (
        executable is not None
        and Path(executable).expanduser() == Path(current_executable).expanduser()
    )
    uptime_seconds: int | None = None
    if running:
        try:
            uptime_seconds = int(time.time() - TELEGRAM_REMOTE_PID_FILE.stat().st_mtime)
        except OSError:
            pass
    if config is None:
        config = Config.load()
    configured = True
    chat_id: str | None = None
    config_error: str | None = None
    try:
        credentials = telegram_remote_credentials(config)
        chat_id = credentials.chat_id
    except TelegramRemoteConfigError as exc:
        configured = False
        config_error = str(exc)
    return {
        "enabled": bool(config.telegram_remote_enabled),
        "configured": configured,
        "chat_id": chat_id,
        "config_error": config_error,
        "running": running,
        "pid": pid if running else None,
        "version": version if running else None,
        "executable": executable if running else None,
        "installed_version": installed,
        "version_match": (version == installed) if running else None,
        "executable_match": executable_match if running and executable is not None else None,
        "pidfile_exists": pidfile_exists,
        "stale_pidfile": pidfile_exists and not running,
        "uptime_seconds": uptime_seconds,
        "pidfile_path": str(TELEGRAM_REMOTE_PID_FILE),
        "log_path": str(TELEGRAM_REMOTE_LOG_FILE),
        "state_path": str(TELEGRAM_REMOTE_STATE_FILE),
    }


def _set_telegram_remote_enabled(enabled: bool) -> Config:
    config = Config.load()
    if config.telegram_remote_enabled == enabled:
        return config
    config.telegram_remote_enabled = enabled
    config.save()
    return config


def _require_telegram_remote_config(as_json: bool) -> None:
    payload = _telegram_remote_status_payload()
    if payload["configured"]:
        return
    message = str(payload["config_error"])
    if as_json:
        emit_app_error(
            "telegram_remote_not_configured",
            message,
            payload={"telegram_remote": payload},
        )
    else:
        console.print(f"[red]{message}[/red]")
    sys.exit(1)


def _start_telegram_remote_background(*, quiet: bool = False) -> int | None:
    telegram_remote_credentials(Config.load())
    pid = _prepare_telegram_remote_pidfile_for_start()
    if pid is not None:
        _set_telegram_remote_enabled(True)
        if not quiet:
            console.print(f"[green]TokenKick Telegram remote already running[/green] (pid {pid}).")
        return pid

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with TELEGRAM_REMOTE_LOG_FILE.open("a") as log:
        process = subprocess.Popen(
            [sys.argv[0], "remote", "telegram"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _write_owned_telegram_remote_pidfile(process.pid)
    _set_telegram_remote_enabled(True)
    if not quiet:
        console.print(
            f"[green]TokenKick Telegram remote started in background[/green] "
            f"(pid {process.pid}, log {TELEGRAM_REMOTE_LOG_FILE})."
        )
    return process.pid


def _stop_telegram_remote(*, quiet: bool = False, disable_intent: bool = True) -> bool:
    if not TELEGRAM_REMOTE_PID_FILE.exists():
        if disable_intent:
            _set_telegram_remote_enabled(False)
        if not quiet:
            console.print("[yellow]TokenKick Telegram remote is not running.[/yellow]")
        return False

    pid_info = read_daemon_pidfile(TELEGRAM_REMOTE_PID_FILE)
    if pid_info is None:
        try:
            TELEGRAM_REMOTE_PID_FILE.unlink()
        except OSError:
            pass
        if disable_intent:
            _set_telegram_remote_enabled(False)
        if not quiet:
            console.print("[yellow]Removed stale Telegram remote pidfile.[/yellow]")
        return False
    pid = pid_info.pid

    if not _pid_is_running(pid):
        try:
            TELEGRAM_REMOTE_PID_FILE.unlink()
        except OSError:
            pass
        if disable_intent:
            _set_telegram_remote_enabled(False)
        if not quiet:
            console.print(
                f"[yellow]TokenKick Telegram remote was not running[/yellow] "
                f"(stale pid {pid})."
            )
        return False

    exited = False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        exited = True
        if not quiet:
            console.print(
                f"[yellow]TokenKick Telegram remote was not running[/yellow] "
                f"(stale pid {pid})."
            )
    except PermissionError:
        if not quiet:
            console.print(
                f"[red]Cannot stop TokenKick Telegram remote[/red] "
                f"(permission denied for pid {pid})."
            )
        return False
    except OSError as exc:
        if not quiet:
            console.print(f"[red]Cannot stop TokenKick Telegram remote[/red] (pid {pid}: {exc}).")
        return False

    if not exited:
        exited = _wait_for_pid_exit(pid)
    if not exited:
        if not quiet:
            console.print(
                f"[red]TokenKick Telegram remote did not stop within "
                f"{DAEMON_STOP_TIMEOUT_SECONDS:g}s[/red] (pid {pid}); pidfile kept."
            )
        return False

    try:
        TELEGRAM_REMOTE_PID_FILE.unlink()
    except OSError:
        pass
    if disable_intent:
        _set_telegram_remote_enabled(False)
    if not quiet:
        console.print(f"[green]TokenKick Telegram remote stopped[/green] (pid {pid}).")
    return True


def _telegram_remote_status() -> None:
    _require_telegram_remote_config(as_json=False)
    payload = _telegram_remote_status_payload()
    pid = _read_telegram_remote_pid()
    if pid is None:
        enabled_note = (
            " (enabled; daemon will restart it on the next poll)"
            if payload["enabled"]
            else ""
        )
        if TELEGRAM_REMOTE_PID_FILE.exists():
            console.print(
                "[yellow]TokenKick Telegram remote not running[/yellow] "
                f"(stale pidfile, run tk remote telegram --background to restart){enabled_note}"
            )
        else:
            console.print(f"[yellow]TokenKick Telegram remote not running[/yellow]{enabled_note}")
        _print_report_timestamp("Telegram remote status printed at")
        return

    if not _pid_is_running(pid):
        enabled_note = (
            " (enabled; daemon will restart it on the next poll)"
            if payload["enabled"]
            else ""
        )
        console.print(
            "[yellow]TokenKick Telegram remote not running[/yellow] "
            f"(stale pidfile, run tk remote telegram --background to restart){enabled_note}"
        )
        _print_report_timestamp("Telegram remote status printed at")
        return

    console.print(
        f"[green]TokenKick Telegram remote running[/green] "
        f"(pid {pid}, uptime {_telegram_remote_uptime_human()}, "
        f"chat {payload['chat_id']})"
    )
    _print_report_timestamp("Telegram remote status printed at")


def _telegram_remote_start_json() -> None:
    _require_telegram_remote_config(as_json=True)
    before = _telegram_remote_status_payload()
    if before["running"]:
        emit_app_success(
            {"action": "start", "started": False, "already_running": True, "telegram_remote": before},
            message=f"TokenKick Telegram remote already running (pid {before['pid']}).",
        )
        return
    pid = _start_telegram_remote_background(quiet=True)
    after = _telegram_remote_status_payload()
    if pid is None or not after["running"]:
        emit_app_error(
            "telegram_remote_start_failed",
            "TokenKick Telegram remote could not be started.",
            payload={
                "action": "start",
                "started": False,
                "already_running": False,
                "telegram_remote": after,
            },
        )
        sys.exit(1)
    emit_app_success(
        {"action": "start", "started": True, "already_running": False, "telegram_remote": after},
        message=f"TokenKick Telegram remote started in background (pid {pid}).",
    )


def _telegram_remote_stop_json() -> None:
    before = _telegram_remote_status_payload()
    stopped = _stop_telegram_remote(quiet=True)
    after = _telegram_remote_status_payload()
    payload = {
        "action": "stop",
        "stopped": stopped,
        "was_running": before["running"],
        "telegram_remote": after,
    }
    if after["running"]:
        emit_app_error(
            "telegram_remote_stop_failed",
            f"TokenKick Telegram remote could not be stopped (pid {after['pid']}).",
            payload=payload,
        )
        sys.exit(1)
    message = (
        f"TokenKick Telegram remote stopped (pid {before['pid']})."
        if stopped
        else "TokenKick Telegram remote was not running."
    )
    emit_app_success(payload, message=message)


def _telegram_remote_restart_json() -> None:
    _require_telegram_remote_config(as_json=True)
    before = _telegram_remote_status_payload()
    if before["running"]:
        stopped = _stop_telegram_remote(quiet=True, disable_intent=False)
        if not stopped and TELEGRAM_REMOTE_PID_FILE.exists():
            emit_app_error(
                "telegram_remote_stop_failed",
                "TokenKick Telegram remote could not be stopped; restart aborted.",
                payload={
                    "action": "restart",
                    "restarted": False,
                    "telegram_remote": _telegram_remote_status_payload(),
                },
            )
            sys.exit(1)
    elif TELEGRAM_REMOTE_PID_FILE.exists():
        try:
            TELEGRAM_REMOTE_PID_FILE.unlink()
        except OSError:
            pass
    pid = _start_telegram_remote_background(quiet=True)
    after = _telegram_remote_status_payload()
    if pid is None or not after["running"]:
        emit_app_error(
            "telegram_remote_start_failed",
            "TokenKick Telegram remote could not be started.",
            payload={"action": "restart", "restarted": False, "telegram_remote": after},
        )
        sys.exit(1)
    emit_app_success(
        {
            "action": "restart",
            "restarted": True,
            "was_running": before["running"],
            "telegram_remote": after,
        },
        message=(
            f"TokenKick Telegram remote {'restarted' if before['running'] else 'started'} "
            f"(pid {pid})."
        ),
    )


def _run_telegram_remote_foreground() -> None:
    try:
        credentials = telegram_remote_credentials(Config.load())
    except TelegramRemoteConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    current_pid = os.getpid()
    existing_pid = _prepare_telegram_remote_pidfile_for_start()
    if existing_pid is not None and existing_pid != current_pid:
        console.print(f"[red]TokenKick Telegram remote already running[/red] (pid {existing_pid}).")
        sys.exit(1)

    _write_owned_telegram_remote_pidfile(current_pid)
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    try:
        signal.signal(
            signal.SIGTERM,
            lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        listener = TelegramRemoteListener(
            client=TelegramRemoteClient(credentials.token),
            allowed_chat_id=credentials.chat_id,
            state_file=TELEGRAM_REMOTE_STATE_FILE,
            status_runner=lambda: run_status_cached_command(_telegram_remote_executable_path()),
            refresh_runner=lambda: run_status_refresh_command(_telegram_remote_executable_path()),
            logger=lambda event, fields: _telegram_remote_log(event, **fields),
        )
        _telegram_remote_log("telegram_remote_start", chat_id=credentials.chat_id)
        listener.run_forever()
    except TelegramRemoteConfigError as exc:
        _telegram_remote_log("telegram_remote_config_error", error=str(exc))
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        _telegram_remote_log("telegram_remote_stop")
    finally:
        try:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
        except (OSError, ValueError):
            pass
        _remove_telegram_remote_pidfile_if_owned(current_pid)


@cli.group("remote")
def remote_group():
    """Remote read-only status channels."""


@remote_group.command("telegram")
@click.option("--background", is_flag=True, help="Start Telegram remote listener in the background")
@click.option("--stop", "stop_remote", is_flag=True, help="Stop the Telegram remote listener")
@click.option("--status", "remote_status", is_flag=True, help="Show Telegram remote listener status")
@click.option("--restart", "restart_remote", is_flag=True, help="Restart Telegram remote listener")
@click.option(
    "--json-output",
    "as_json",
    is_flag=True,
    help="Output result as JSON (with --status, --background, --stop, or --restart)",
)
def remote_telegram(
    background: bool,
    stop_remote: bool,
    remote_status: bool,
    restart_remote: bool,
    as_json: bool,
):
    """Run a read-only Telegram status listener."""
    selected_modes = [background, stop_remote, remote_status, restart_remote]
    selected_count = sum(1 for selected in selected_modes if selected)
    as_json = as_json or (app_mode_enabled() and selected_count > 0)
    if selected_count > 1:
        message = "Use only one Telegram remote mode: --background, --stop, --status, or --restart."
        if as_json:
            emit_app_error(ERROR_USAGE, message)
            sys.exit(2)
        console.print(f"[red]{message}[/red]")
        return
    if as_json and selected_count == 0:
        emit_app_error(
            ERROR_USAGE,
            "--json-output requires --status, --background, --stop, or --restart.",
        )
        sys.exit(2)
    if stop_remote:
        if as_json:
            _telegram_remote_stop_json()
        else:
            _stop_telegram_remote()
        return
    if remote_status:
        if as_json:
            _require_telegram_remote_config(as_json=True)
            emit_app_success({"telegram_remote": _telegram_remote_status_payload()})
        else:
            _telegram_remote_status()
        return
    if restart_remote:
        if as_json:
            _telegram_remote_restart_json()
        else:
            _require_telegram_remote_config(as_json=False)
            pid = _read_telegram_remote_pid()
            if (
                pid
                and not _stop_telegram_remote(quiet=True, disable_intent=False)
                and TELEGRAM_REMOTE_PID_FILE.exists()
            ):
                console.print("[red]TokenKick Telegram remote could not be stopped; restart aborted.[/red]")
                return
            if pid is None and TELEGRAM_REMOTE_PID_FILE.exists():
                try:
                    TELEGRAM_REMOTE_PID_FILE.unlink()
                except OSError:
                    pass
            _start_telegram_remote_background()
        return
    if background:
        if as_json:
            _telegram_remote_start_json()
        else:
            try:
                _start_telegram_remote_background()
            except TelegramRemoteConfigError as exc:
                console.print(f"[red]{exc}[/red]")
                sys.exit(1)
        return

    _run_telegram_remote_foreground()


# ---------------------------------------------------------------------------
# tk poll
# ---------------------------------------------------------------------------

@cli.command("poll")
@click.argument("minutes", type=int, required=False)
def poll_interval(minutes: int | None):
    """Show or set the daemon poll interval in minutes."""
    config = Config.load()
    if minutes is None:
        console.print(
            f"[green]Daemon poll interval: {config.poll_interval_minutes}m[/green]\n"
            "[dim]Claude /usage is treated as low-cost but nonzero-cost; "
            "the 5m default avoids unnecessary provider probes.[/dim]"
        )
        return
    if minutes < 1:
        console.print("[red]Poll interval must be at least 1 minute.[/red]")
        return

    config.poll_interval_minutes = minutes
    config.save()
    console.print(f"[green]Daemon poll interval set to {minutes}m.[/green]")


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_daemon_pid() -> int | None:
    if not DAEMON_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(DAEMON_PID_FILE)
    return info.pid if info is not None else None


def _read_daemon_version() -> str | None:
    if not DAEMON_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(DAEMON_PID_FILE)
    return info.version if info is not None else None


def _prepare_daemon_pidfile_for_start() -> int | None:
    """Return a live daemon pid, or clear stale pidfile state before starting."""
    if not DAEMON_PID_FILE.exists():
        return None
    info = read_daemon_pidfile(DAEMON_PID_FILE)
    if info is None:
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass
        return None
    if _pid_is_running(info.pid):
        return info.pid
    try:
        DAEMON_PID_FILE.unlink()
    except OSError:
        pass
    return None


def _write_owned_daemon_pidfile(pid: int) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_daemon_pidfile(DAEMON_PID_FILE, pid, executable=_daemon_executable_path())


def _daemon_executable_path() -> str | None:
    if not sys.argv or not sys.argv[0]:
        return None
    try:
        return str(Path(sys.argv[0]).resolve())
    except OSError:
        return sys.argv[0]


def _remove_daemon_pidfile_if_owned(pid: int) -> None:
    info = read_daemon_pidfile(DAEMON_PID_FILE)
    if info is None or info.pid != pid:
        return
    try:
        DAEMON_PID_FILE.unlink()
    except OSError:
        pass


def _daemon_uptime_human() -> str:
    try:
        started_at = DAEMON_PID_FILE.stat().st_mtime
    except OSError:
        return "unknown"
    return _format_duration(int(time.time() - started_at))


def _daemon_status_payload(config: Config | None = None) -> dict:
    """Structured daemon state shared by `tk daemon --json-output` and `tk app snapshot`."""
    pidfile_exists = DAEMON_PID_FILE.exists()
    info = read_daemon_pidfile(DAEMON_PID_FILE) if pidfile_exists else None
    pid = info.pid if info is not None else None
    version = info.version if info is not None else None
    executable = info.executable if info is not None else None
    running = bool(pid and _pid_is_running(pid))
    installed = installed_version()
    current_executable = _daemon_executable_path()
    executable_match = (
        executable is not None
        and current_executable is not None
        and Path(executable).expanduser() == Path(current_executable).expanduser()
    )
    uptime_seconds: int | None = None
    if running:
        try:
            uptime_seconds = int(time.time() - DAEMON_PID_FILE.stat().st_mtime)
        except OSError:
            pass
    if config is None:
        config = Config.load()
    return {
        "running": running,
        "pid": pid if running else None,
        "version": version if running else None,
        "executable": executable if running else None,
        "installed_version": installed,
        "version_match": (version == installed) if running else None,
        "executable_match": executable_match if running and executable is not None else None,
        "pidfile_exists": pidfile_exists,
        "stale_pidfile": pidfile_exists and not running,
        "uptime_seconds": uptime_seconds,
        "poll_interval_minutes": config.poll_interval_minutes,
        "pidfile_path": str(DAEMON_PID_FILE),
        "log_path": str(DAEMON_LOG_FILE),
    }


def _daemon_start_json() -> None:
    before = _daemon_status_payload()
    if before["running"]:
        emit_app_success(
            {"action": "start", "started": False, "already_running": True, "daemon": before},
            message=f"TokenKick daemon already running (pid {before['pid']}).",
        )
        return
    pid = _start_daemon_background(quiet=True)
    after = _daemon_status_payload()
    if pid is None or not after["running"]:
        emit_app_error(
            "daemon_start_failed",
            "TokenKick daemon could not be started.",
            payload={"action": "start", "started": False, "already_running": False, "daemon": after},
        )
        sys.exit(1)
    emit_app_success(
        {"action": "start", "started": True, "already_running": False, "daemon": after},
        message=f"TokenKick daemon started in background (pid {pid}).",
    )


def _daemon_stop_json() -> None:
    before = _daemon_status_payload()
    stopped = _stop_daemon(quiet=True)
    after = _daemon_status_payload()
    payload = {
        "action": "stop",
        "stopped": stopped,
        "was_running": before["running"],
        "daemon": after,
    }
    if after["running"]:
        emit_app_error(
            "daemon_stop_failed",
            f"TokenKick daemon could not be stopped (pid {after['pid']}).",
            payload=payload,
        )
        sys.exit(1)
    message = (
        f"TokenKick daemon stopped (pid {before['pid']})."
        if stopped
        else "TokenKick daemon was not running."
    )
    emit_app_success(payload, message=message)


def _daemon_restart_json() -> None:
    before = _daemon_status_payload()
    if before["running"]:
        stopped = _stop_daemon(quiet=True)
        if not stopped and DAEMON_PID_FILE.exists():
            emit_app_error(
                "daemon_stop_failed",
                "TokenKick daemon could not be stopped; restart aborted.",
                payload={"action": "restart", "restarted": False, "daemon": _daemon_status_payload()},
            )
            sys.exit(1)
    elif DAEMON_PID_FILE.exists():
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass
    pid = _start_daemon_background(quiet=True)
    after = _daemon_status_payload()
    if pid is None or not after["running"]:
        emit_app_error(
            "daemon_start_failed",
            "TokenKick daemon could not be started.",
            payload={"action": "restart", "restarted": False, "daemon": after},
        )
        sys.exit(1)
    emit_app_success(
        {
            "action": "restart",
            "restarted": True,
            "was_running": before["running"],
            "daemon": after,
        },
        message=f"TokenKick daemon {'restarted' if before['running'] else 'started'} (pid {pid}).",
    )


def _start_daemon_background(*, quiet: bool = False) -> int | None:
    pid = _prepare_daemon_pidfile_for_start()
    if pid is not None:
        if not quiet:
            console.print(f"[green]TokenKick daemon already running[/green] (pid {pid}).")
        return pid

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with DAEMON_LOG_FILE.open("a") as log:
        process = subprocess.Popen(
            [sys.argv[0], "daemon"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _write_owned_daemon_pidfile(process.pid)
    if not quiet:
        console.print(
            f"[green]TokenKick daemon started in background[/green] "
            f"(pid {process.pid}, log {DAEMON_LOG_FILE})."
        )
    return process.pid


def _stop_daemon(*, quiet: bool = False) -> bool:
    if not DAEMON_PID_FILE.exists():
        if not quiet:
            console.print("[yellow]TokenKick daemon is not running.[/yellow]")
        return False

    pid_info = read_daemon_pidfile(DAEMON_PID_FILE)
    if pid_info is None:
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass
        if not quiet:
            console.print("[yellow]Removed stale daemon pidfile.[/yellow]")
        return False
    pid = pid_info.pid

    if not _pid_is_running(pid):
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass
        if not quiet:
            console.print(f"[yellow]TokenKick daemon was not running[/yellow] (stale pid {pid}).")
        return False

    exited = False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        exited = True
        if not quiet:
            console.print(f"[yellow]TokenKick daemon was not running[/yellow] (stale pid {pid}).")
    except PermissionError:
        if not quiet:
            console.print(f"[red]Cannot stop TokenKick daemon[/red] (permission denied for pid {pid}).")
        return False
    except OSError as exc:
        if not quiet:
            console.print(f"[red]Cannot stop TokenKick daemon[/red] (pid {pid}: {exc}).")
        return False

    if not exited:
        exited = _wait_for_pid_exit(pid)
    if not exited:
        if not quiet:
            console.print(
                f"[red]TokenKick daemon did not stop within {DAEMON_STOP_TIMEOUT_SECONDS:g}s[/red] "
                f"(pid {pid}); pidfile kept."
            )
        return False

    try:
        DAEMON_PID_FILE.unlink()
    except OSError:
        pass
    if not quiet:
        console.print(f"[green]TokenKick daemon stopped[/green] (pid {pid}).")
    return True


def _wait_for_pid_exit(
    pid: int,
    *,
    timeout_seconds: float | None = None,
    poll_seconds: float | None = None,
) -> bool:
    timeout = DAEMON_STOP_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    poll = DAEMON_STOP_POLL_SECONDS if poll_seconds is None else poll_seconds
    deadline = time.monotonic() + max(0.0, timeout)
    while _pid_is_running(pid):
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(max(0.0, poll), deadline - now))
    return True


def _daemon_status() -> None:
    config = Config.load()
    daemon_payload = _daemon_status_payload(config)
    telegram_payload = _telegram_remote_status_payload(config)
    installed = daemon_payload["installed_version"]
    console.print(f"TokenKick installed version: v{installed}")

    if daemon_payload["running"]:
        version = daemon_payload["version"] or "unknown"
        version_note = (
            ""
            if daemon_payload["version_match"]
            else f" [yellow](installed v{installed}; run tk update)[/yellow]"
        )
        console.print(
            f"[green]TokenKick daemon running[/green] v{version} "
            f"(pid {daemon_payload['pid']}, uptime {_daemon_uptime_human()}, "
            f"poll interval {config.poll_interval_minutes}m){version_note}"
        )
    elif daemon_payload["stale_pidfile"]:
        console.print(
            "[yellow]TokenKick daemon not running[/yellow] "
            "(stale pidfile, run tk daemon --restart to repair)"
        )
    else:
        console.print("[yellow]TokenKick daemon not running[/yellow]")

    if telegram_payload["running"]:
        version = telegram_payload["version"] or "unknown"
        version_note = (
            ""
            if telegram_payload["version_match"]
            else f" [yellow](installed v{installed}; restart Telegram remote)[/yellow]"
        )
        console.print(
            f"[green]TokenKick Telegram remote running[/green] v{version} "
            f"(pid {telegram_payload['pid']}, chat {telegram_payload['chat_id']}){version_note}"
        )
    elif telegram_payload["stale_pidfile"]:
        console.print(
            "[yellow]TokenKick Telegram remote not running[/yellow] "
            "(stale pidfile, run tk remote telegram --restart to repair)"
        )
    elif not telegram_payload["configured"]:
        console.print("[yellow]TokenKick Telegram remote not configured[/yellow]")
    else:
        console.print("[yellow]TokenKick Telegram remote not running[/yellow]")

    update_needed = (
        daemon_payload["running"] and daemon_payload["version_match"] is False
    ) or (
        telegram_payload["running"] and telegram_payload["version_match"] is False
    )
    console.print(f"Update needed: {'yes' if update_needed else 'no'}")
    console.print(
        "[dim]Upgrade flow: pipx upgrade tokenkick; tk update[/dim]"
    )
    _print_report_timestamp("Daemon status printed at")


def _handle_global_reset_event(
    event: ResetEvent | None,
    config: Config,
    *,
    daemon_log: bool = False,
) -> ResetEvent | None:
    if event is None:
        return None
    observation = is_provider_reset_observation(event)
    log_name = (
        "provider_reset_observation"
        if observation
        else ("global_reset_possible" if event.confidence == "possible" else "global_reset_detected")
    )
    if has_duplicate_reset_event(event):
        if daemon_log:
            _daemon_log(
                "provider_reset_observation_duplicate" if observation else "global_reset_duplicate",
                provider=event.provider,
                confidence=event.confidence,
                trigger=event.trigger,
                accounts=",".join(event.affected_accounts),
            )
        return None
    if event.confidence == "confirmed" and not observation:
        removed = invalidate_event_pending_kicks(event)
        if daemon_log and removed:
            _daemon_log(
                "global_reset_pending_invalidated",
                provider=event.provider,
                accounts=",".join(event.pending_kicks_invalidated),
            )
    if _global_reset_should_notify(event, config):
        event.notification_sent = _send_reset_event_notification(
            event,
            config,
            daemon_log=daemon_log,
        )
        if not event.notification_sent and not event.notification_skip_reason:
            event.notification_skip_reason = "delivery failed"
    else:
        event.notification_sent = False
        event.notification_skip_reason = (
            f"below notification threshold ({config.global_reset_notify_min_confidence})"
        )
    if daemon_log:
        _daemon_log(
            ("provider_reset_observation_notification_sent" if observation else "global_reset_notification_sent")
            if event.notification_sent
            else (
                "provider_reset_observation_notification_skipped"
                if observation
                else "global_reset_notification_skipped"
            ),
            provider=event.provider,
            confidence=event.confidence,
            reason=event.notification_skip_reason,
        )
    appended = append_reset_event(event)
    if daemon_log and appended:
        _daemon_log(
            log_name,
            provider=event.provider,
            confidence=event.confidence,
            trigger=event.trigger,
            accounts=",".join(event.affected_accounts),
            quota_hours_lost=event.total_quota_hours_lost,
        )
    return event if appended else None


def _send_reset_event_notification(
    event: ResetEvent,
    config: Config,
    *,
    daemon_log: bool,
) -> bool:
    if not is_provider_reset_observation(event):
        delivered = _send_global_notifications(
            config.notifications,
            lambda notifications: notify_reset_event(event, notifications),
        )
        if not delivered:
            event.notification_skip_reason = (
                "notifications disabled" if not config.notifications.enabled else "delivery failed"
            )
        return delivered
    account = next(
        (candidate for candidate in config.accounts if candidate.label in event.affected_accounts),
        None,
    )
    if account is None:
        event.notification_skip_reason = "account not found"
        return False
    delivered, acknowledged = _send_account_notifications(
        account,
        config.notifications,
        lambda notifications: notify_reset_event(event, notifications),
        daemon_log=daemon_log,
        context="provider_reset_observation",
    )
    if not delivered:
        event.notification_skip_reason = "notifications disabled" if acknowledged else "delivery failed"
    return delivered


def _global_reset_should_notify(event: ResetEvent, config: Config) -> bool:
    if is_provider_reset_observation(event):
        return True
    ranks = {"possible": 0, "likely": 1, "confirmed": 2}
    return ranks.get(event.confidence, 0) >= ranks.get(
        config.global_reset_notify_min_confidence,
        1,
    )


def _restart_daemon() -> None:
    was_running = False
    pid = _read_daemon_pid()
    if pid:
        was_running = _stop_daemon(quiet=True)
        if not was_running and DAEMON_PID_FILE.exists():
            console.print("[red]TokenKick daemon could not be stopped; restart aborted.[/red]")
            return
    elif DAEMON_PID_FILE.exists():
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass

    new_pid = _start_daemon_background(quiet=True)
    if new_pid is None:
        console.print("[red]TokenKick daemon could not be started.[/red]")
        return
    if was_running:
        console.print(f"[green]TokenKick daemon restarted[/green] (pid {new_pid})")
    else:
        console.print(f"[green]TokenKick daemon started[/green] (pid {new_pid})")


def _restart_daemon_for_update() -> bool:
    was_running = False
    pid = _read_daemon_pid()
    if pid:
        was_running = _stop_daemon(quiet=True)
        if not was_running and DAEMON_PID_FILE.exists():
            return False
    elif DAEMON_PID_FILE.exists():
        return False

    new_pid = _start_daemon_background(quiet=True)
    if new_pid is None:
        return False
    if was_running:
        console.print(f"[green]TokenKick daemon restarted[/green] (pid {new_pid})")
    else:
        console.print(f"[green]TokenKick daemon started[/green] (pid {new_pid})")
    return True


def _restart_stale_daemon_for_update() -> bool:
    try:
        DAEMON_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    new_pid = _start_daemon_background(quiet=True)
    if new_pid is None:
        return False
    console.print(f"[green]TokenKick daemon restarted[/green] (pid {new_pid})")
    return True


def _restart_telegram_remote_for_update() -> bool:
    was_running = False
    pid = _read_telegram_remote_pid()
    if pid:
        was_running = _stop_telegram_remote(quiet=True, disable_intent=False)
        if not was_running and TELEGRAM_REMOTE_PID_FILE.exists():
            return False
    elif TELEGRAM_REMOTE_PID_FILE.exists():
        return False

    if not was_running:
        return True
    try:
        new_pid = _start_telegram_remote_background(quiet=True)
    except TelegramRemoteConfigError:
        return False
    if new_pid is None:
        return False
    console.print(f"[green]TokenKick Telegram remote restarted[/green] (pid {new_pid})")
    return True


def _restart_stale_telegram_remote_for_update() -> bool:
    try:
        TELEGRAM_REMOTE_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        new_pid = _start_telegram_remote_background(quiet=True)
    except TelegramRemoteConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return False
    if new_pid is None:
        return False
    console.print(f"[green]TokenKick Telegram remote restarted[/green] (pid {new_pid})")
    return True


def _background_process_upgrade_state_payload() -> dict[str, bool]:
    return {
        "daemon": bool(_daemon_status_payload().get("running")),
        "telegram_remote": bool(_telegram_remote_status_payload().get("running")),
    }


def _write_background_process_upgrade_state(
    payload: dict[str, bool] | None = None,
) -> dict[str, bool]:
    state = payload if payload is not None else _background_process_upgrade_state_payload()
    normalized = {
        "daemon": bool(state.get("daemon")),
        "telegram_remote": bool(state.get("telegram_remote")),
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    UPGRADE_BACKGROUND_STATE_FILE.write_text(json.dumps(normalized, indent=2) + "\n")
    return normalized


def _read_background_process_upgrade_state() -> dict[str, bool]:
    try:
        raw = UPGRADE_BACKGROUND_STATE_FILE.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "daemon": bool(data.get("daemon")),
        "telegram_remote": bool(data.get("telegram_remote")),
    }


def _clear_background_process_upgrade_state() -> None:
    try:
        UPGRADE_BACKGROUND_STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _upgrade_state_restore_needed(state: dict[str, bool], payload: dict) -> bool:
    return (
        bool(state.get("daemon")) and not payload["daemon_running"]
    ) or (
        bool(state.get("telegram_remote")) and not payload["telegram_remote_running"]
    )


def _restore_background_processes_from_upgrade_state(
    state: dict[str, bool],
    payload: dict,
) -> bool:
    ok = True
    if state.get("daemon") and not payload["daemon_running"]:
        new_pid = _start_daemon_background(quiet=True)
        if new_pid is None:
            ok = False
        else:
            console.print(f"[green]TokenKick daemon restored[/green] (pid {new_pid})")
    if state.get("telegram_remote") and not payload["telegram_remote_running"]:
        try:
            new_pid = _start_telegram_remote_background(quiet=True)
        except TelegramRemoteConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            ok = False
        else:
            if new_pid is None:
                ok = False
            else:
                console.print(
                    f"[green]TokenKick Telegram remote restored[/green] (pid {new_pid})"
                )
    return ok


def _update_status_payload() -> dict:
    installed = installed_version()
    daemon_pid = _read_daemon_pid()
    daemon_version = _read_daemon_version()
    daemon_running = bool(daemon_pid and _pid_is_running(daemon_pid))
    daemon_pidfile_exists = DAEMON_PID_FILE.exists()
    daemon_has_pidfile = daemon_pid is not None
    daemon_match = (
        (daemon_version == installed)
        if daemon_running
        else not daemon_pidfile_exists
    )

    telegram_pid = _read_telegram_remote_pid()
    telegram_version = _read_telegram_remote_version()
    telegram_running = bool(telegram_pid and _pid_is_running(telegram_pid))
    telegram_pidfile_exists = TELEGRAM_REMOTE_PID_FILE.exists()
    telegram_has_pidfile = telegram_pid is not None
    telegram_match = (
        (telegram_version == installed)
        if telegram_running
        else not telegram_pidfile_exists
    )
    return {
        "schema_version": 1,
        "installed_version": installed,
        "daemon_version": daemon_version if daemon_has_pidfile else None,
        "daemon_running": daemon_running,
        "daemon_match": daemon_match,
        "daemon_pid": daemon_pid if daemon_has_pidfile else None,
        "telegram_remote_version": telegram_version if telegram_has_pidfile else None,
        "telegram_remote_running": telegram_running,
        "telegram_remote_match": telegram_match,
        "telegram_remote_pid": telegram_pid if telegram_has_pidfile else None,
        "match": daemon_match and telegram_match,
    }


def _render_update_status(payload: dict) -> None:
    installed = payload["installed_version"]
    if not payload["daemon_running"] and not payload["telegram_remote_running"]:
        console.print(
            f"TokenKick installed version: v{installed}; daemon not running; "
            "Telegram remote not running."
        )
        return
    if payload["daemon_running"]:
        if payload["daemon_match"]:
            console.print(f"TokenKick daemon up to date (v{installed}).")
        else:
            console.print(
                f"TokenKick daemon version mismatch: running v{payload['daemon_version']}, "
                f"installed v{installed}."
            )
    else:
        console.print("TokenKick daemon not running.")
    if payload["telegram_remote_running"]:
        if payload["telegram_remote_match"]:
            console.print(f"TokenKick Telegram remote up to date (v{installed}).")
        else:
            console.print(
                "TokenKick Telegram remote version mismatch: "
                f"running v{payload['telegram_remote_version']}, installed v{installed}."
            )
    else:
        console.print("TokenKick Telegram remote not running.")


def _restart_background_processes_for_update(payload: dict) -> bool:
    ok = True
    if payload["daemon_running"] and not payload["daemon_match"]:
        ok = _restart_daemon_for_update() and ok
    if payload["telegram_remote_running"] and not payload["telegram_remote_match"]:
        ok = _restart_telegram_remote_for_update() and ok
    return ok


@cli.command("update")
@click.option("--check", is_flag=True, help="Check background process versions without restarting")
@click.option("--yes", is_flag=True, help="Restart stale background processes without prompting")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def update_cmd(check: bool, yes: bool, as_json: bool):
    """Check whether background processes need a restart after updating TokenKick."""
    payload = _update_status_payload()
    upgrade_state = _read_background_process_upgrade_state()
    pid = _read_daemon_pid()
    telegram_pid = _read_telegram_remote_pid()
    stale_pidfile = DAEMON_PID_FILE.exists() and (pid is None or not _pid_is_running(pid))
    stale_telegram_pidfile = TELEGRAM_REMOTE_PID_FILE.exists() and (
        telegram_pid is None or not _pid_is_running(telegram_pid)
    )
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        if stale_pidfile or stale_telegram_pidfile:
            sys.exit(1)
        sys.exit(0 if payload["match"] else 1)

    if yes and (stale_pidfile or stale_telegram_pidfile):
        repaired = True
        if stale_pidfile:
            console.print("[yellow]Daemon pidfile is stale; restarting daemon.[/yellow]")
            repaired = _restart_stale_daemon_for_update() and repaired
        if stale_telegram_pidfile:
            console.print(
                "[yellow]Telegram remote pidfile is stale; restarting Telegram remote.[/yellow]"
            )
            repaired = _restart_stale_telegram_remote_for_update() and repaired
        if not repaired:
            console.print("[red]TokenKick background processes could not be restarted.[/red]")
            sys.exit(1)
        payload = _update_status_payload()
        stale_pidfile = False
        stale_telegram_pidfile = False

    restore_needed = _upgrade_state_restore_needed(upgrade_state, payload)
    if upgrade_state and restore_needed:
        if check:
            _render_update_status(payload)
            console.print(
                "[yellow]Background processes that were running before upgrade "
                "are not running now.[/yellow]"
            )
            sys.exit(1)
        if yes or click.confirm(
            "Restart background processes that were running before upgrade?",
            default=True,
        ):
            if not _restore_background_processes_from_upgrade_state(upgrade_state, payload):
                console.print("[red]TokenKick background processes could not be restored.[/red]")
                sys.exit(1)
            _clear_background_process_upgrade_state()
            payload = _update_status_payload()
        else:
            _clear_background_process_upgrade_state()
            console.print("[yellow]Background process restore declined.[/yellow]")
            sys.exit(1)
    elif upgrade_state:
        _clear_background_process_upgrade_state()

    if stale_pidfile:
        console.print(
            "[yellow]Daemon pidfile is stale; run [bold]tk daemon --restart[/bold] "
            "to cleanly restart.[/yellow]"
        )
        sys.exit(1)
    if stale_telegram_pidfile:
        console.print(
            "[yellow]Telegram remote pidfile is stale; run "
            "[bold]tk remote telegram --restart[/bold] to cleanly restart.[/yellow]"
        )
        sys.exit(1)
    _render_update_status(payload)
    if payload["match"] or (
        not payload["daemon_running"] and not payload["telegram_remote_running"]
    ):
        sys.exit(0)
    if check:
        sys.exit(1)
    if not yes and not click.confirm("Restart stale background processes now?", default=True):
        console.print("[yellow]Background process restart declined.[/yellow]")
        sys.exit(1)
    if _restart_background_processes_for_update(payload):
        _clear_background_process_upgrade_state()
        sys.exit(0)
    console.print("[red]TokenKick background processes could not be restarted.[/red]")
    sys.exit(1)


# ---------------------------------------------------------------------------
# tk daemon
# ---------------------------------------------------------------------------

def _daemon_sleep_seconds(
    interval: int | float,
    deferred: list[tuple[AccountConfig, AccountStatus, int]],
    phantom_recovery_deferred: list[tuple[AccountConfig, int]],
) -> int | float:
    sleep_seconds = interval
    if deferred:
        next_deferred = min(cooldown for _account, _status, cooldown in deferred)
        sleep_seconds = min(interval, max(60, next_deferred + 60))
    if phantom_recovery_deferred:
        next_recovery = min(cooldown for _account, cooldown in phantom_recovery_deferred)
        sleep_seconds = min(
            sleep_seconds,
            max(PHANTOM_RECOVERY_DAEMON_SLEEP_FLOOR_SECONDS, next_recovery),
        )
    return sleep_seconds


@cli.command()
@click.option("--background", is_flag=True, help="Start daemon in the background and exit")
@click.option("--stop", "stop_daemon", is_flag=True, help="Stop the daemon")
@click.option("--status", "daemon_status", is_flag=True, help="Show daemon status")
@click.option("--restart", "restart_daemon", is_flag=True, help="Restart the daemon in the background")
@click.option(
    "--json-output",
    "as_json",
    is_flag=True,
    help="Output result as JSON (with --status, --background, --stop, or --restart)",
)
def daemon(background: bool, stop_daemon: bool, daemon_status: bool, restart_daemon: bool, as_json: bool):
    """Run as a poller — checks and kicks on interval."""
    selected_modes = [background, stop_daemon, daemon_status, restart_daemon]
    selected_count = sum(1 for selected in selected_modes if selected)
    as_json = as_json or (app_mode_enabled() and selected_count > 0)
    if selected_count > 1:
        message = "Use only one daemon mode: --background, --stop, --status, or --restart."
        if as_json:
            emit_app_error(ERROR_USAGE, message)
            sys.exit(2)
        console.print(f"[red]{message}[/red]")
        return
    if as_json and selected_count == 0:
        emit_app_error(
            ERROR_USAGE,
            "--json-output requires --status, --background, --stop, or --restart.",
        )
        sys.exit(2)
    if stop_daemon:
        if as_json:
            _daemon_stop_json()
        else:
            _stop_daemon()
        return
    if daemon_status:
        if as_json:
            emit_app_success({"daemon": _daemon_status_payload()})
        else:
            _daemon_status()
        return
    if restart_daemon:
        if as_json:
            _daemon_restart_json()
        else:
            _restart_daemon()
        return
    if background:
        if as_json:
            _daemon_start_json()
        else:
            _start_daemon_background()
        return

    current_pid = os.getpid()
    existing_pid = _prepare_daemon_pidfile_for_start()
    if existing_pid is not None and existing_pid != current_pid:
        console.print(f"[red]TokenKick daemon already running[/red] (pid {existing_pid}).")
        sys.exit(1)

    _reap_dead_status_refresh_lock(STATUS_CACHE_REFRESH_LOCK_FILE)
    config = Config.load()
    accounts = _load_accounts(config)

    if not accounts:
        console.print("[red]No saved accounts. Run tk setup after logging in.[/red]")
        return

    _write_owned_daemon_pidfile(current_pid)
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    try:
        signal.signal(
            signal.SIGTERM,
            lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

        _daemon_log("daemon_start", poll_interval=f"{config.poll_interval_minutes}m")
        if config.schedule.is_default():
            _daemon_log(
                "feature_available",
                feature="smart_scheduling",
                command="tk schedule set --default --weekdays HH:MM-HH:MM",
            )

        previous_reset_entries = _load_status_cache_entries()
        reset_detection_from_disk = bool(previous_reset_entries)
        fallback_sleep_seconds = config.poll_interval_minutes * 60
        try:
            while True:
                try:
                    previous_reset_entries, reset_detection_from_disk, sleep_seconds = _daemon_poll_once(
                        previous_reset_entries,
                        reset_detection_from_disk,
                    )
                    fallback_sleep_seconds = max(60, sleep_seconds)
                except Exception as exc:
                    _daemon_log(
                        "poll_error",
                        error=_daemon_poll_error_text(exc),
                        retry_in_seconds=fallback_sleep_seconds,
                    )
                    sleep_seconds = fallback_sleep_seconds
                time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            _daemon_log("daemon_stop")
    finally:
        try:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
        except (OSError, ValueError):
            pass
        _remove_daemon_pidfile_if_owned(current_pid)


def _daemon_poll_error_text(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if len(detail) > 500:
        detail = detail[:499].rstrip() + "…"
    return f"{exc.__class__.__name__}: {detail}" if detail else exc.__class__.__name__


def _supervise_telegram_remote(config: Config) -> None:
    if not config.telegram_remote_enabled:
        return
    try:
        telegram_remote_credentials(config)
    except TelegramRemoteConfigError as exc:
        _daemon_log(
            "telegram_remote_supervise_skipped",
            reason="not_configured",
            error=str(exc),
        )
        return

    payload = _telegram_remote_status_payload(config)
    if payload["running"]:
        return

    try:
        pid = _start_telegram_remote_background(quiet=True)
    except Exception as exc:
        _daemon_log(
            "telegram_remote_supervise_failed",
            reason="start_error",
            error=_daemon_poll_error_text(exc),
        )
        return
    if pid is None:
        _daemon_log("telegram_remote_supervise_failed", reason="start_returned_no_pid")
        return
    _daemon_log("telegram_remote_supervise_started", pid=pid)


def _daemon_poll_once(
    previous_reset_entries: dict[str, dict],
    reset_detection_from_disk: bool,
) -> tuple[dict[str, dict], bool, int | float]:
    """Run one daemon poll iteration and return reset-tracking state plus sleep."""
    loop_config = _migrate_codex_home_keys_if_needed(
        Config.load(),
        emit_notice=False,
    )
    loop_config = _repair_codex_home_identity_drift_if_needed(
        loop_config,
        emit_notice=False,
    )
    _supervise_telegram_remote(loop_config)
    interval = loop_config.poll_interval_minutes * 60
    accounts = _load_accounts(loop_config)
    previous_entries_for_reset = previous_reset_entries
    refresh_accounts, refresh_statuses = _refresh_status_cache(loop_config, daemon_log=True)
    statuses_by_key = _cache_statuses_by_key_from_pairs(refresh_accounts, refresh_statuses)
    cache_entries_after_refresh = _load_status_cache_entries()
    late_attributions = _apply_codex_late_attribution(
        refresh_accounts,
        statuses_by_key,
        daemon_log=True,
    )
    _apply_claude_predicted_session_due_statuses(
        accounts,
        statuses_by_key,
        cache_entries_after_refresh,
        daemon_log=True,
    )
    _apply_codex_predicted_session_due_statuses(
        accounts,
        statuses_by_key,
        cache_entries_after_refresh,
        daemon_log=True,
    )
    claude_reconciliation_executed = _execute_claude_reconciliation_probes(
        accounts,
        statuses_by_key,
        loop_config,
        cache_entries_after_refresh,
        daemon_log=True,
    )
    reset_events = detect_reset_events(
        previous_entries=previous_entries_for_reset,
        accounts=refresh_accounts,
        statuses_by_key=statuses_by_key,
        kick_history=load_kick_history(limit=200),
        now=datetime.now(timezone.utc),
        restarted_from_disk=reset_detection_from_disk,
    )
    for reset_event in reset_events:
        _handle_global_reset_event(reset_event, loop_config, daemon_log=True)
    previous_reset_entries = _load_status_cache_entries()
    reset_detection_from_disk = False
    stagger_state = KickStaggerState()
    phantom_recovery_executed, phantom_recovery_deferred = _execute_verified_phantom_recoveries(
        accounts,
        statuses_by_key,
        loop_config,
        daemon_log=True,
        stagger_state=stagger_state,
    )
    surface_reintroductions = _execute_codex_surface_reintroductions(
        accounts,
        statuses_by_key,
        loop_config,
        daemon_log=True,
        stagger_state=stagger_state,
    )
    codex_pending_confirmation_followups = _execute_codex_pending_confirmation_followups(
        accounts,
        statuses_by_key,
        loop_config,
        surface_reintroductions,
        daemon_log=True,
    )
    due_executed = _execute_due_pending_kicks(
        accounts,
        loop_config,
        daemon_log=True,
        statuses_by_key=statuses_by_key,
        stagger_state=stagger_state,
    )
    reservation_advisory_notifications = _send_reservation_advisory_notifications(
        accounts,
        statuses_by_key,
        loop_config,
        now=datetime.now(timezone.utc),
        daemon_log=True,
    )
    targets, deferred = _kickable_window_targets(
        accounts,
        statuses_by_key,
        loop_config,
        manage_phantom_recovery=True,
    )
    for account in _auto_kick_kickable_accounts(accounts):
        status = statuses_by_key.get(account_key_string(account))
        if status is not None and status.stale and status.state == AccountState.FRESH:
            _daemon_log(
                "auto_kick_skipped",
                account=account.label,
                reason="stale_status",
                error=_stale_status_reason(status),
            )
    _daemon_log(
        "poll",
        auto_kick_accounts=len(_auto_kick_kickable_accounts(accounts)),
        fresh_targets=len(targets),
        deferred=len(deferred),
        scheduled_due=due_executed,
        phantom_recovery=phantom_recovery_executed,
        codex_surface_reintroductions=surface_reintroductions.count,
        codex_pending_confirmation_followups=codex_pending_confirmation_followups,
        claude_reconcile=claude_reconciliation_executed,
        codex_late_attributions=late_attributions,
        reservation_advisories=reservation_advisory_notifications,
    )
    if (
        not targets
        and not deferred
        and due_executed == 0
        and phantom_recovery_executed == 0
        and surface_reintroductions.count == 0
        and codex_pending_confirmation_followups == 0
        and claude_reconciliation_executed == 0
        and late_attributions == 0
        and reservation_advisory_notifications == 0
    ):
        _daemon_log_target_scan(
            accounts,
            statuses_by_key,
            manage_phantom_recovery=True,
        )
    _kick_all_enabled_accounts(
        accounts,
        loop_config,
        targets=targets,
        deferred=deferred,
        daemon_log=True,
        stagger_state=stagger_state,
    )

    sleep_seconds = _daemon_sleep_seconds(interval, deferred, phantom_recovery_deferred)
    return previous_reset_entries, reset_detection_from_disk, sleep_seconds


# ---------------------------------------------------------------------------
# tk app — JSON-first commands for the native macOS app
# ---------------------------------------------------------------------------

from .app_commands import app_group as _app_group  # noqa: E402 — needs the CLI group defined first

cli.add_command(_app_group)
