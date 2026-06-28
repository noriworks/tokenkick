"""Tests for CLI auto-discovery behavior."""

import json
import copy
import io
import os
import signal
import subprocess
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from tokenkick.cli import (
    CODEX_HOME_KEY_MIGRATION_KEY,
    CODEX_KICK_STAGGER_SECONDS,
    CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
    CODEX_ATTRIBUTION_STRONG,
    CODEX_ATTRIBUTION_TIMING_MATCH,
    CODEX_FIRE_ALL_SURFACES,
    CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR,
    CODEX_SESSION_ANCHOR_MISALIGNED_ERROR,
    CODEX_SESSION_ANCHOR_PENDING_ERROR,
    DIRECT_SOURCE_MIGRATION_KEY,
    LABEL_FORMAT_MIGRATION_KEY,
    KickStaggerState,
    PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
    _codex_accounts,
    _codex_surface_debug_report,
    _codex_surface_missed_kept_cluster,
    _codex_fire_all_surface_gap_seconds,
    _codex_fire_all_surface_order,
    _codex_fire_all_surfaces_enabled,
    _codex_surface_retry_backoff_seconds,
    _codex_usage_account_bucket_map,
    _codex_usage_debug_payload,
    _discover_accounts_and_statuses,
    _discover_codexbar_accounts,
    _discover_codex_session_accounts,
    _discover_direct_accounts,
    _daemon_log_target_scan,
    _apply_codex_late_attribution,
    _apply_codex_predicted_session_due_statuses,
    _execute_codex_pending_confirmation_followups,
    CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
    _daemon_sleep_seconds,
    _execute_due_pending_kicks,
    _execute_codex_surface_reintroductions,
    _execute_verified_phantom_recoveries,
    _apply_claude_predicted_session_due_statuses,
    _codex_attribution_for_anchor_delta,
    _execute_claude_reconciliation_probes,
    _format_session_reset,
    _format_used_cell,
    _format_used_labeled_cell,
    _format_used_percent,
    _format_weekly_reset,
    _format_status_cache_footer,
    _format_status_footer_timestamp,
    _confirm_prompt,
    _format_plan_source,
    _format_plan_timestamp,
    _format_plan_time_range,
    _render_plan,
    _parse_plan_usage_duration_minutes,
    _parse_plan_usage_overrides,
    _filter_status_pairs_by_provider,
    _format_log_line,
    _history_event_details,
    _history_event_result,
    _history_timestamp_without_timezone,
    _handle_global_reset_event,
    _kick_eligibility,
    _kick_type_for_status,
    _kick_all_enabled_accounts,
    _kick_and_notify,
    _kickable_window_targets,
    _load_accounts,
    _load_account_status_pairs,
    _load_saved_account_status_snapshot,
    _load_status_cache,
    _load_status_cache_entries,
    _migrate_v04_direct_sources_if_needed,
    _migrate_codex_home_keys_if_needed,
    _migrate_pending_kick_keys,
    _migrate_provider_first_labels_if_needed,
    _merge_discovered_accounts,
    _observe_phantom_session_state,
    _record_codex_pending_confirmation_notification,
    _repair_codex_home_identity_drift_if_needed,
    _prune_phantom_session_observations_for_accounts,
    _phantom_recovery_model_for_attempt,
    _run_evaluate_account,
    _run_kick_attempt,
    _run_claude_usage_touch,
    _send_reservation_advisory_notifications,
    _save_status_cache,
    _save_config_like,
    _sort_statuses,
    _start_background_status_refresh,
    _render_status_table,
    _refresh_status_cache,
    _report_timestamp_text,
    _status_action,
    _status_actionable_now,
    _status_cache_entry_is_stale,
    _status_refresh_lock_info,
    _status_state_display,
    _verify_claude_session_anchor,
    _verify_codex_session_anchor,
    _verify_phantom_kick,
    _was_kicked_in_current_session_window,
    _was_kicked_in_current_window,
    _with_setup_auto_kick_defaults,
    cli,
)
from tokenkick.direct import CODEX_PROVIDER_USAGE_SOURCE_DETAIL, DirectIdentity, email_from_id_token
from tokenkick.codex_surface_stats import (
    codex_surface_order_for_account,
    codex_surface_stats_for_account,
    update_codex_surface_stats,
)
from tokenkick.kicker import (
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_NO_GENERATION_EVIDENCE_ERROR,
    kick_account,
    kick_invocation_for_account,
)
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeConfig,
    ClaudeProbeContext,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    Config,
    DataSource,
    KickEvent,
    NotifyConfig,
    ScheduleConfig,
    WorkSchedule,
    account_key_string,
    synthetic_status_reason,
)
from tokenkick.reset_defense import AccountSnapshot, ResetEvent, append_reset_event, load_reset_events
from tokenkick.orchestration import (
    AccountPlanInput,
    OrchestrationPlan,
    PendingKickDiff,
    PlannedKick,
    PlannedSegment,
    SkippedAccount,
    build_orchestration_plan,
    build_pending_kick_diff,
)
from tokenkick.scheduling import (
    PENDING_KICK_PURPOSE_COVERAGE,
    PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
    PendingKick,
    PendingKickStateError,
    ScheduleReason,
    SchedulingWindowBasis,
    load_pending_kicks,
    save_pending_kicks,
    to_utc_iso,
)
from tokenkick.reservation_advisories import (
    RISK_PLAN_MAY_BE_COMPROMISED,
    RISK_QUIET_PERIOD_ACTIVE,
    RISK_QUIET_PERIOD_SOON,
    RISK_SAFE,
    build_reservation_advisories,
)


def test_codex_usage_debug_payload_sanitizes_and_selects_bucket(monkeypatch):
    now = 1_779_696_762
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: now)
    account = AccountConfig(
        label="codex (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-debug",
    )
    response = {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 1,
                        "windowDurationMins": 300,
                        "resetsAt": now + 300 * 60,
                    },
                    "secondary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 10080 * 60,
                    },
                },
                "gpt-5.4-mini": {
                    "limitId": "gpt-5.4-mini",
                    "limitName": "GPT-5.4 mini",
                    "primary": {
                        "usedPercent": 8,
                        "windowDurationMins": 300,
                        "resetsAt": now + 60 * 60,
                    },
                    "secondary": {
                        "usedPercent": 2,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 86400,
                    },
                },
            }
        },
    }

    payload = _codex_usage_debug_payload(account, response)

    assert payload["provider_home"] == "/tmp/codex-debug"
    assert payload["bucket_count"] == 2
    buckets = {bucket["limit_id"]: bucket for bucket in payload["buckets"]}
    assert buckets["codex"]["display_name"] == "main/default Codex quota"
    assert buckets["gpt-5.4-mini"]["display_name"] == "GPT-5.4 mini"
    assert payload["selected_bucket"]["limit_id"] == "codex"
    assert payload["selected_bucket"]["display_name"] == "main/default Codex quota"
    assert payload["selected_status"]["weekly_used_percent"] == 0.0
    assert payload["selected_status"]["weekly_resets_at"] == now + 10080 * 60
    assert payload["selected_status"]["session_used_percent"] == 1.0
    assert payload["selected_status"]["session_resets_at"] == now + 300 * 60
    assert "auth" not in json.dumps(payload).lower()


def test_codex_usage_debug_payload_selects_saved_spark_bucket(monkeypatch):
    now = 1_779_696_762
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: now)
    account = AccountConfig(
        label="codex-spark (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-debug",
        codex_rate_limit_id="codex_bengalfox",
        codex_rate_limit_name="GPT-5.3-Codex-Spark",
    )
    response = {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 1,
                        "windowDurationMins": 300,
                        "resetsAt": now + 300 * 60,
                    },
                    "secondary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 10080 * 60,
                    },
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 8,
                        "windowDurationMins": 300,
                        "resetsAt": now + 60 * 60,
                    },
                    "secondary": {
                        "usedPercent": 2,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 86400,
                    },
                },
            }
        },
    }

    payload = _codex_usage_debug_payload(
        account,
        response,
        account_bucket_map={
            ("/tmp/codex-debug", "codex"): "codex (debug)",
            ("/tmp/codex-debug", "codex_bengalfox"): account.label,
        },
    )

    buckets = {bucket["limit_id"]: bucket for bucket in payload["buckets"]}
    assert buckets["codex_bengalfox"]["mapped_account_label"] == account.label
    assert payload["selected_bucket"]["limit_id"] == "codex_bengalfox"
    assert payload["selected_status"]["weekly_used_percent"] == 2.0
    assert payload["selected_status"]["session_used_percent"] == 8.0


def test_codex_usage_bucket_mapping_is_scoped_to_provider_home(monkeypatch):
    now = 1_779_696_762
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: now)
    accounts = [
        AccountConfig(
            label="codex (alpha)",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-alpha",
        ),
        AccountConfig(
            label="codex (beta)",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-beta",
        ),
        AccountConfig(
            label="codex-spark (alpha)",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-alpha",
            codex_rate_limit_id="codex_bengalfox",
        ),
        AccountConfig(
            label="codex-spark (beta)",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-beta",
            codex_rate_limit_id="codex_bengalfox",
        ),
    ]
    response = {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 1,
                        "windowDurationMins": 300,
                        "resetsAt": now + 300 * 60,
                    },
                    "secondary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 10080 * 60,
                    },
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 8,
                        "windowDurationMins": 300,
                        "resetsAt": now + 60 * 60,
                    },
                    "secondary": {
                        "usedPercent": 2,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 86400,
                    },
                },
            }
        },
    }

    account_bucket_map = _codex_usage_account_bucket_map(accounts)

    alpha_main = _codex_usage_debug_payload(accounts[0], response, account_bucket_map=account_bucket_map)
    beta_main = _codex_usage_debug_payload(accounts[1], response, account_bucket_map=account_bucket_map)
    alpha_spark = _codex_usage_debug_payload(accounts[2], response, account_bucket_map=account_bucket_map)
    beta_spark = _codex_usage_debug_payload(accounts[3], response, account_bucket_map=account_bucket_map)

    assert alpha_main["selected_bucket"]["mapped_account_label"] == "codex (alpha)"
    assert beta_main["selected_bucket"]["mapped_account_label"] == "codex (beta)"
    assert alpha_spark["selected_bucket"]["mapped_account_label"] == "codex-spark (alpha)"
    assert beta_spark["selected_bucket"]["mapped_account_label"] == "codex-spark (beta)"


def test_codex_usage_debug_payload_labels_codex_spark_bucket(monkeypatch):
    now = 1_779_696_762
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: now)
    account = AccountConfig(
        label="codex (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-debug",
    )
    response = {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 1,
                        "windowDurationMins": 300,
                        "resetsAt": now + 300 * 60,
                    },
                    "secondary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": now + 10080 * 60,
                    },
                }
            }
        },
    }

    payload = _codex_usage_debug_payload(account, response)

    assert payload["selected_bucket"]["limit_id"] == "codex_bengalfox"
    assert payload["selected_bucket"]["display_name"] == "GPT-5.3-Codex-Spark quota"


def test_codex_direct_discovery_adds_spark_sibling_when_bucket_exists(monkeypatch, tmp_path):
    from tokenkick.discovery import _append_codex_direct_account

    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    monkeypatch.setattr(
        "tokenkick.cli.read_codex_identity",
        lambda _home: DirectIdentity("codex", provider_account_id="acct-dev", email="dev@example.test"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.codex_appserver_bucket_metadata",
        lambda _home: [
            {
                "key": "codex",
                "limit_id": "codex",
                "limit_name": None,
                "display_name": "main/default Codex quota",
            },
            {
                "key": "codex_bengalfox",
                "limit_id": "codex_bengalfox",
                "limit_name": "GPT-5.3-Codex-Spark",
                "display_name": "GPT-5.3-Codex-Spark quota",
            },
        ],
    )
    monkeypatch.setattr(
        "tokenkick.discovery._read_codex_appserver_ratelimits_for_account",
        lambda account, _home: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            codex_rate_limit_id=account.codex_rate_limit_id,
            codex_rate_limit_name=account.codex_rate_limit_name,
        ),
    )

    _append_codex_direct_account(accounts, statuses, tmp_path / ".codex")

    assert [account.codex_rate_limit_id for account in accounts] == [None, "codex_bengalfox"]
    spark = accounts[1]
    assert spark.label.startswith("codex-spark")
    assert spark.auto_kick is False
    assert spark.weekly_auto_kick is False
    assert spark.session_auto_kick is False
    assert spark.kick_model == "gpt-5.3-codex-spark"
    assert spark.plan_tier == "spark"
    assert statuses[1].codex_rate_limit_id == "codex_bengalfox"


def test_codex_direct_discovery_skips_spark_sibling_when_bucket_absent(monkeypatch, tmp_path):
    from tokenkick.discovery import _append_codex_direct_account

    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    monkeypatch.setattr(
        "tokenkick.cli.read_codex_identity",
        lambda _home: DirectIdentity("codex", provider_account_id="acct-dev", email="dev@example.test"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.codex_appserver_bucket_metadata",
        lambda _home: [
            {
                "key": "codex",
                "limit_id": "codex",
                "limit_name": None,
                "display_name": "main/default Codex quota",
            }
        ],
    )

    _append_codex_direct_account(accounts, statuses, tmp_path / ".codex")

    assert len(accounts) == 1
    assert accounts[0].codex_rate_limit_id is None


def test_setup_preserves_existing_spark_kick_model_override():
    spark = AccountConfig(
        label="codex-spark (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_rate_limit_id="codex_bengalfox",
        kick_model="gpt-5.3-codex-spark",
        auto_kick=False,
    )
    existing = replace(spark, kick_model="custom-spark-model", auto_kick=True)

    updated = _with_setup_auto_kick_defaults([spark], Config(accounts=[existing]))

    assert updated[0].kick_model == "custom-spark-model"
    assert updated[0].auto_kick is True


def test_codex_surface_debug_report_compares_before_after_usage(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-debug",
    )
    reports = iter(
        [
            {
                "selected_bucket": {
                    "primary_used_percent": 1,
                    "primary_window_minutes": 300,
                    "primary_resets_at": 1000,
                },
                "selected_status": {
                    "session_used_percent": 1.0,
                    "session_resets_in_seconds": 17999,
                    "session_resets_at": 1000,
                    "weekly_used_percent": 0.0,
                    "weekly_resets_at": 2000,
                    "window_anchor_state": "anchored",
                },
                "elapsed_ms": 100,
            },
            {
                "selected_bucket": {
                    "primary_used_percent": 3,
                    "primary_window_minutes": 300,
                    "primary_resets_at": 1300,
                },
                "selected_status": {
                    "session_used_percent": 3.0,
                    "session_resets_in_seconds": 17699,
                    "session_resets_at": 1300,
                    "weekly_used_percent": 0.0,
                    "weekly_resets_at": 2000,
                    "window_anchor_state": "anchored",
                },
                "elapsed_ms": 100,
            },
        ]
    )
    monkeypatch.setattr("tokenkick.cli._codex_usage_debug_report", lambda _account: next(reports))
    monkeypatch.setattr(
        "tokenkick.cli.kick_invocation_for_account",
        lambda _account, **_kwargs: SimpleNamespace(
            command=["codex", "exec", "--json", "<prompt>"],
            cwd=tmp_path,
            workspace_git_present=True,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda _account, **_kwargs: KickEvent(
            label=account.label,
            success=True,
            input_tokens=10,
            output_tokens=2,
            response_text="ok",
        ),
    )

    report = _codex_surface_debug_report(account, mode="repo", wait_seconds=0)

    assert report["mode"] == "repo"
    assert report["cwd"] == str(tmp_path)
    assert report["delta"]["session_used_percent"] == 2.0
    assert report["delta"]["session_resets_at"] == 300
    assert report["kick"]["response_text"] == "ok"
    assert report["poll"]["moved"] is True


def test_codex_surface_debug_report_polls_until_reset_clock_moves(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-debug",
    )
    reports = iter(
        [
            {
                "selected_bucket": {
                    "primary_used_percent": 1,
                    "primary_window_minutes": 300,
                    "primary_resets_at": 1000,
                },
                "selected_status": {
                    "session_used_percent": 1.0,
                    "session_resets_in_seconds": 100,
                    "session_resets_at": 1000,
                    "session_window_minutes": 300,
                    "window_anchor_state": "available_unanchored",
                },
                "elapsed_ms": 100,
            },
            {
                "selected_bucket": {
                    "primary_used_percent": 1,
                    "primary_window_minutes": 300,
                    "primary_resets_at": 1000,
                },
                "selected_status": {
                    "session_used_percent": 1.0,
                    "session_resets_in_seconds": 90,
                    "session_resets_at": 1000,
                    "session_window_minutes": 300,
                    "window_anchor_state": "available_unanchored",
                },
                "elapsed_ms": 100,
            },
            {
                "selected_bucket": {
                    "primary_used_percent": 3,
                    "primary_window_minutes": 300,
                    "primary_resets_at": 3000,
                },
                "selected_status": {
                    "session_used_percent": 3.0,
                    "session_resets_in_seconds": 17800,
                    "session_resets_at": 3000,
                    "session_window_minutes": 300,
                    "window_anchor_state": "anchored",
                },
                "elapsed_ms": 100,
            },
        ]
    )
    times = iter([1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1070.0])
    sleeps = []
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: next(times))
    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("tokenkick.cli._codex_usage_debug_report", lambda _account: next(reports))
    monkeypatch.setattr(
        "tokenkick.cli.kick_invocation_for_account",
        lambda _account, **_kwargs: SimpleNamespace(
            command=["codex", "exec", "--json", "<prompt>"],
            cwd=tmp_path,
            workspace_git_present=True,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda _account, **_kwargs: KickEvent(
            label=account.label,
            timestamp=1000.0,
            success=True,
            input_tokens=10,
            output_tokens=2,
            response_text="ok",
        ),
    )

    report = _codex_surface_debug_report(
        account,
        mode="repo-skip",
        wait_seconds=0,
        poll_timeout_seconds=120,
        poll_interval_seconds=30,
    )

    poll = report["poll"]
    assert poll["moved"] is True
    assert poll["first_move_delay_seconds"] == 70.0
    assert poll["first_move_delay_from_finish_seconds"] == 60.0
    assert len(poll["observations"]) == 2
    assert poll["observations"][0]["moved"] is False
    assert poll["observations"][1]["moved"] is True
    assert sleeps == [30]


def test_status_account_filter_outputs_one_account(monkeypatch):
    accounts = [
        AccountConfig(label="codex (alpha)", provider="codex"),
        AccountConfig(label="codex (beta)", provider="codex", visible=False),
    ]
    statuses = [
        AccountStatus(label="codex (alpha)", state=AccountState.ACTIVE),
        AccountStatus(label="codex (beta)", state=AccountState.FRESH),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: (accounts, statuses, {}),
    )

    result = CliRunner().invoke(
        cli,
        ["status", "--account", "codex (beta)", "--json-output"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [row["label"] for row in payload["accounts"]] == ["codex (beta)"]


def test_status_verbose_shows_codex_fire_all_surface_mode(monkeypatch):
    account = AccountConfig(label="codex (alpha)", provider="codex")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE)
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            accounts=[account],
            codex_fire_all_surfaces=True,
            codex_fire_all_surface_order=[CODEX_KICK_SURFACE_REPO, CODEX_KICK_SURFACE_LEGACY],
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: ([account], [status], {}),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["status", "--verbose"])

    assert result.exit_code == 0
    assert "Codex surface strategy: burst ladder enabled" in result.output
    assert "order repo, legacy" in result.output


def test_save_config_like_preserves_unmodified_config_fields():
    account = AccountConfig(label="codex (alpha)", provider="codex")
    config = Config(
        accounts=[account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        poll_interval_minutes=5,
        schedule=ScheduleConfig(
            accounts={"codex (alpha)": WorkSchedule(enabled=True, weekdays="09:00-17:00")}
        ),
        codex_surface_retry_backoff_seconds=42,
        codex_burst_ladder_enabled=True,
        codex_burst_ladder_gap_seconds=90,
        codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_REPO],
        global_reset_notify_min_confidence="confirmed",
        migrations={"future-field-preservation": True},
    )

    _save_config_like(config, poll_interval_minutes=7)

    loaded = Config.load()
    assert loaded.poll_interval_minutes == 7
    assert loaded.accounts == [account]
    assert loaded.notifications.ntfy_topic == "topic"
    assert loaded.schedule.accounts["codex (alpha)"].weekdays == "09:00-17:00"
    assert loaded.codex_surface_retry_backoff_seconds == 42
    assert loaded.codex_burst_ladder_enabled is True
    assert loaded.codex_burst_ladder_gap_seconds == 90
    assert loaded.codex_burst_ladder_surface_order == [CODEX_KICK_SURFACE_REPO]
    assert loaded.codex_fire_all_surfaces is True
    assert loaded.codex_fire_all_surface_gap_seconds == 90
    assert loaded.codex_fire_all_surface_order == [CODEX_KICK_SURFACE_REPO]
    assert loaded.global_reset_notify_min_confidence == "confirmed"
    assert loaded.migrations == {"future-field-preservation": True}


def test_status_refresh_cache_reload_preserves_unmodified_config_fields(monkeypatch):
    account = AccountConfig(label="codex (alpha)", provider="codex")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE)
    original_config = Config(
        accounts=[account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        poll_interval_minutes=3,
        schedule=ScheduleConfig(
            accounts={account.label: WorkSchedule(enabled=True, weekdays="10:00-18:00")}
        ),
        codex_surface_retry_backoff_seconds=77,
        codex_burst_ladder_enabled=True,
        codex_burst_ladder_gap_seconds=90,
        codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_REPO],
        global_reset_notify_min_confidence="confirmed",
        migrations={"future-field-preservation": True},
    )
    captured: list[Config] = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: original_config)
    monkeypatch.setattr("tokenkick.cli.claude_cli_usage_refresh_allowed", lambda: nullcontext())
    monkeypatch.setattr("tokenkick.cli._migrate_v04_direct_sources_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._migrate_provider_first_labels_if_needed", lambda config: config)
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache_fast",
        lambda _config: ([account], [status], False, "Refreshed.", []),
    )

    def fake_load_status_cache(config):
        captured.append(config)
        return [account], [status], {}

    monkeypatch.setattr("tokenkick.cli._load_status_cache", fake_load_status_cache)

    result = CliRunner().invoke(cli, ["status", "--refresh", "--json-output"])

    assert result.exit_code == 0
    assert len(captured) == 1
    refreshed_config = captured[0]
    assert refreshed_config.accounts == [account]
    assert refreshed_config.notifications.ntfy_topic == "topic"
    assert refreshed_config.poll_interval_minutes == 3
    assert refreshed_config.schedule.accounts[account.label].weekdays == "10:00-18:00"
    assert refreshed_config.codex_surface_retry_backoff_seconds == 77
    assert refreshed_config.codex_burst_ladder_enabled is True
    assert refreshed_config.codex_burst_ladder_gap_seconds == 90
    assert refreshed_config.codex_burst_ladder_surface_order == [CODEX_KICK_SURFACE_REPO]
    assert refreshed_config.global_reset_notify_min_confidence == "confirmed"
    assert refreshed_config.migrations == {"future-field-preservation": True}


def test_status_prints_timestamp_below_table(monkeypatch):
    account = AccountConfig(label="codex (alpha)", provider="codex")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE)
    now = datetime(2026, 5, 22, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: ([account], [status], {}),
    )
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: now)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    expected_time = now.astimezone().strftime("%H:%M")
    assert f"Status printed at {expected_time}." in result.output
    assert "CEST" not in result.output
    assert "CET" not in result.output


def test_accounts_detail_outputs_one_account(monkeypatch):
    account = AccountConfig(
        label="codex (alpha)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/tokenkick-alpha",
        auto_kick=True,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))

    result = CliRunner().invoke(cli, ["accounts", "detail", "codex (alpha)", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["label"] == "codex (alpha)"
    assert payload["kickable"] is True
    assert payload["provider_home"] == "/tmp/tokenkick-alpha"


def test_codex_attribution_classifier_uses_strong_delta_threshold():
    assert _codex_attribution_for_anchor_delta(0.0) == CODEX_ATTRIBUTION_STRONG
    assert _codex_attribution_for_anchor_delta(30.0) == CODEX_ATTRIBUTION_STRONG
    assert _codex_attribution_for_anchor_delta(120.6) == CODEX_ATTRIBUTION_TIMING_MATCH


def test_codex_surfaces_is_read_only(monkeypatch):
    account = AccountConfig(
        label="codex (alpha)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/tokenkick-alpha",
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not kick")),
    )

    result = CliRunner().invoke(cli, ["codex-surfaces", "codex (alpha)", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert payload["order"] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]


def test_codex_surfaces_text_explains_scores_and_surface_labels(monkeypatch, tmp_path):
    import tokenkick.cli as cli_module

    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    Config(accounts=[account]).save()
    update_codex_surface_stats(
        cli_module._codex_surface_stats_file(),
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_LEGACY,
                codex_attribution=CODEX_ATTRIBUTION_STRONG,
                response_text="ok",
            )
        ],
    )

    result = CliRunner().invoke(cli, ["codex-surfaces", "codex"])

    assert result.exit_code == 0, result.output
    assert "Current Codex Surface Order" in result.output
    assert "Strong wins / tries" in result.output
    assert "Plain Codex exec" in result.output
    assert "Next adaptive session attempt order" in result.output
    assert "learning score is a capped preference score" in result.output
    assert "output/tokens means Codex returned neither assistant text" in result.output


def test_codex_surface_demotion_evidence_renders_clusters(monkeypatch, tmp_path):
    import tokenkick.cli as cli_module

    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        codex_surface_auto_demote=True,
    )
    Config(accounts=[account]).save()
    for index in range(5):
        update_codex_surface_stats(
            cli_module._codex_surface_stats_file(),
            account,
            [
                KickEvent(
                    label=account.label,
                    timestamp=1000.0 + index,
                    success=True,
                    confirmed=True,
                    codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
                    codex_cluster_id=f"cluster-{index}",
                    codex_attempt_started_at=1000.0 + index,
                    codex_attempt_finished_at=1001.0 + index,
                    codex_attribution=CODEX_ATTRIBUTION_STRONG,
                    response_text="ok",
                )
            ],
        )

    result = CliRunner().invoke(cli, ["codex-surfaces", "codex", "demotion", "evidence"])

    assert result.exit_code == 0, result.output
    assert "Demotion Evidence" in result.output
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE in result.output
    assert "Recent eligible clusters" in result.output
    assert "Conclusion: interactive-like was eligible in 5/5 clusters" in result.output
    assert "surfaces ahead won 5/5" in result.output
    assert "look back up to 20 clusters" in result.output
    assert "interactive-like eligible" in result.output
    assert "Tried in cluster" in result.output
    assert "Ahead of interactive-like then" in result.output
    assert "repo-skip" in result.output

    strategy_result = CliRunner().invoke(cli, ["codex-strategy", "demotion", "evidence", "codex"])

    assert strategy_result.exit_code == 0, strategy_result.output
    assert "Demotion Evidence" in strategy_result.output


def test_codex_surface_patterns_is_read_only_json(monkeypatch):
    events = [
        KickEvent(
            label="codex (alpha)",
            timestamp=1000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            codex_cluster_id="cluster-one",
            codex_attribution=CODEX_ATTRIBUTION_STRONG,
            response_text="ok",
        ),
        KickEvent(
            label="codex (alpha)",
            timestamp=1100.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            codex_surface=CODEX_KICK_SURFACE_LEGACY,
            codex_cluster_id="cluster-two",
            codex_attribution=CODEX_ATTRIBUTION_STRONG,
            response_text="ok",
        ),
    ]
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=50: events)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not kick")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.update_codex_surface_stats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not score")),
    )

    result = CliRunner().invoke(cli, ["codex-surface-patterns", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert payload["experimental"] is True
    assert payload["eligible_clusters"] == 2
    assert payload["evaluated_samples"] == 1
    assert payload["verdict"]["status"] == "insufficient_data"


def test_codex_surface_patterns_resolves_provider_first_component(monkeypatch):
    account = AccountConfig(label="codex (alpha)", provider="codex")
    events = [
        KickEvent(
            label="codex (alpha)",
            timestamp=1000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            codex_cluster_id="cluster-one",
            codex_attribution=CODEX_ATTRIBUTION_STRONG,
            response_text="ok",
        ),
        KickEvent(
            label="codex (alpha)",
            timestamp=1100.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            codex_surface=CODEX_KICK_SURFACE_LEGACY,
            codex_cluster_id="cluster-two",
            codex_attribution=CODEX_ATTRIBUTION_STRONG,
            response_text="ok",
        ),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=50: events)

    result = CliRunner().invoke(cli, ["codex-surface-patterns", "alpha", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["scope_label"] == "codex (alpha)"
    assert payload["eligible_clusters"] == 2
    assert payload["evaluated_samples"] == 1


def test_codex_surface_patterns_text_labels_experimental_read_only(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=50: [])

    result = CliRunner().invoke(cli, ["codex-surface-patterns"])

    assert result.exit_code == 0
    assert "Surface Pattern Check" in result.output
    assert "experimental/read-only" in result.output
    assert "Not enough strong clusters" in result.output
    assert "Current per-account learning score" in result.output
    assert "Most wins for this account" in result.output
    assert "Last winner anywhere" in result.output
    assert "Sequence-pattern guess" in result.output
    assert "Winner first" in result.output
    assert "Excluded from backtest: none" in result.output
    assert "Read-only: this command does not change live surface ranking" in result.output


def test_codex_surface_patterns_text_summarizes_verdict_and_exclusions(monkeypatch):
    events = []
    for index in range(60):
        events.append(
            KickEvent(
                label="codex",
                timestamp=1000.0 + index,
                success=True,
                confirmed=True,
                kind="session",
                kick_type="session",
                codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
                codex_cluster_id=f"strong-{index}",
                codex_attribution=CODEX_ATTRIBUTION_STRONG,
                response_text="ok",
            )
        )
    events.extend(
        [
            KickEvent(
                label="codex",
                timestamp=2000.0,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                codex_surface=CODEX_KICK_SURFACE_LEGACY,
                codex_cluster_id="unconfirmed",
                response_text="ok",
            ),
            KickEvent(
                label="codex",
                timestamp=2010.0,
                success=False,
                confirmed=False,
                kind="session",
                kick_type="session",
                codex_surface=CODEX_KICK_SURFACE_REPO,
                codex_cluster_id="failed",
            ),
        ]
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=50: events)

    result = CliRunner().invoke(cli, ["codex-surface-patterns"])

    assert result.exit_code == 0
    assert "No better rule found" in result.output
    assert "generated but unconfirmed 1" in result.output
    assert "failed/no output 1" in result.output
    assert "no single strong winner 2" in result.output


def test_codex_surface_test_requires_confirmation(monkeypatch):
    account = AccountConfig(
        label="codex (alpha)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/tokenkick-alpha",
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not kick")),
    )

    result = CliRunner().invoke(cli, ["codex-surface-test", "codex (alpha)"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output


def test_interactive_like_codex_surface_uses_account_home_with_skip_flag(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    account = AccountConfig(
        label="codex (debug)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(codex_home),
    )

    invocation = kick_invocation_for_account(
        account,
        codex_surface=CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    )

    assert invocation.cwd == codex_home
    assert invocation.env["CODEX_HOME"] == str(codex_home)
    assert "--skip-git-repo-check" in invocation.command
    assert invocation.command[:3] == ["codex", "exec", "--json"]


@pytest.fixture(autouse=True)
def isolate_status_cache(monkeypatch, tmp_path):
    config_dir = tmp_path / "tokenkick-state"
    monkeypatch.setattr("tokenkick.models.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.models.CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr("tokenkick.models.HISTORY_FILE", config_dir / "history.jsonl")
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.cli.CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", config_dir / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", config_dir / "phantom-recovery.json")
    monkeypatch.setattr("tokenkick.cli.DORMANT_HINTS_FILE", config_dir / "dormant-hints.json")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", config_dir / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", config_dir / "daemon.log")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", config_dir / "telegram-remote.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_LOG_FILE", config_dir / "telegram-remote.log")
    monkeypatch.setattr(
        "tokenkick.cli.TELEGRAM_REMOTE_STATE_FILE",
        config_dir / "telegram-remote-state.json",
    )
    monkeypatch.setattr(
        "tokenkick.cli.UPGRADE_BACKGROUND_STATE_FILE",
        config_dir / "upgrade-background-processes.json",
    )
    monkeypatch.setattr(
        "tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE",
        config_dir / "codex-pending-confirmations.json",
    )
    monkeypatch.setattr(
        "tokenkick.cli._codex_surface_stats_file",
        lambda: config_dir / "codex-surface-stats.json",
    )
    monkeypatch.setattr("tokenkick.discovery.antigravity_cli_detected", lambda: False)
    monkeypatch.setattr("tokenkick.discovery.read_antigravity_cli_identity", lambda: None)
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        "tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE",
        tmp_path / "status-cache-refresh.pid",
    )
    monkeypatch.setattr(
        "tokenkick.scheduling.PENDING_KICKS_FILE",
        config_dir / "pending-kicks.json",
    )
    monkeypatch.setattr("tokenkick.migrations.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.migrations.CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(
        "tokenkick.migrations.DIRECT_SOURCE_BACKUP_FILE",
        config_dir / "config.json.pre-v0.4-backup",
    )
    monkeypatch.setattr(
        "tokenkick.migrations.DIRECT_SOURCE_APPSERVER_BACKUP_FILE",
        config_dir / "config.json.pre-v0.4x-appserver-backup",
    )
    monkeypatch.setattr(
        "tokenkick.migrations.LABEL_FORMAT_BACKUP_FILE",
        config_dir / "config.json.pre-label-format-backup",
    )
    monkeypatch.setattr(
        "tokenkick.migrations.CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE",
        config_dir / "config.json.pre-codex-home-identity-repair-backup",
    )
    monkeypatch.setattr(
        "tokenkick.reset_defense.RESET_EVENTS_FILE",
        config_dir / "reset-events.jsonl",
    )
    monkeypatch.setattr(
        "tokenkick.reservation_advisories.RESERVATION_ADVISORY_STATE_FILE",
        config_dir / "reserved-account-advisories.json",
    )


def _codexbar_payload():
    return [
        {
            "provider": "codex",
            "source": "openai-web",
            "usage": {
                "accountEmail": "dev@example.test",
                "identity": {
                    "accountEmail": "dev@example.test",
                    "loginMethod": "Pro 5x",
                    "providerID": "codex",
                },
                "primary": {"usedPercent": 0, "windowMinutes": 300},
                "secondary": {
                    "resetsAt": "2026-05-23T21:18:02Z",
                    "usedPercent": 15,
                    "windowMinutes": 10080,
                },
            },
        },
        {
            "provider": "claude",
            "source": "oauth",
            "usage": {
                "identity": {"loginMethod": "Claude Pro", "providerID": "claude"},
                "primary": {
                    "resetsAt": "2026-05-18T00:30:00Z",
                    "usedPercent": 8,
                    "windowMinutes": 300,
                },
                "secondary": {
                    "resetsAt": "2026-05-22T12:00:00Z",
                    "usedPercent": 4,
                    "windowMinutes": 10080,
                },
            },
        },
        {
            "provider": "antigravity",
            "source": "auto",
            "error": {"message": "Antigravity Google auth not found."},
        },
    ]


def _isolate_config_files(monkeypatch, tmp_path):
    config_dir = tmp_path / ".tokenkick"
    config_file = config_dir / "config.json"
    backup_file = config_dir / "config.json.pre-v0.4-backup"
    appserver_backup_file = config_dir / "config.json.pre-v0.4x-appserver-backup"
    label_backup_file = config_dir / "config.json.pre-label-format-backup"
    status_cache_file = config_dir / "status-cache.json"
    dormant_hints_file = config_dir / "dormant-hints.json"
    monkeypatch.setattr("tokenkick.models.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.models.CONFIG_FILE", config_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.cli.CONFIG_FILE", config_file)
    monkeypatch.setattr("tokenkick.migrations.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.migrations.CONFIG_FILE", config_file)
    monkeypatch.setattr("tokenkick.migrations.DIRECT_SOURCE_BACKUP_FILE", backup_file)
    monkeypatch.setattr("tokenkick.migrations.DIRECT_SOURCE_APPSERVER_BACKUP_FILE", appserver_backup_file)
    monkeypatch.setattr("tokenkick.migrations.LABEL_FORMAT_BACKUP_FILE", label_backup_file)
    monkeypatch.setattr("tokenkick.cli.DIRECT_SOURCE_BACKUP_FILE", backup_file)
    monkeypatch.setattr("tokenkick.cli.DIRECT_SOURCE_APPSERVER_BACKUP_FILE", appserver_backup_file)
    monkeypatch.setattr("tokenkick.cli.LABEL_FORMAT_BACKUP_FILE", label_backup_file)
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", status_cache_file)
    monkeypatch.setattr("tokenkick.cli.DORMANT_HINTS_FILE", dormant_hints_file)
    return config_file, backup_file, status_cache_file


def _status_json_payload(output: str) -> dict:
    return json.loads(output[output.index("{"):])


def _status_json_accounts(output: str) -> list[dict]:
    return _status_json_payload(output)["accounts"]


def _reset_event(
    event_id: str = "reset-1",
    confidence: str = "likely",
    detected_at: str = "2026-06-04T14:32:00Z",
) -> ResetEvent:
    return ResetEvent(
        id=event_id,
        detected_at=detected_at,
        provider="codex",
        confidence=confidence,
        affected_accounts=["secondary", "reserve"],
        trigger="usage_drop",
        account_snapshots=[
            AccountSnapshot(
                account="secondary",
                before_state="active",
                before_weekly_used_pct=50,
                before_weekly_resets_at="2026-06-06T14:32:00Z",
                after_state="fresh",
                after_weekly_used_pct=0,
                after_weekly_resets_at=None,
            )
        ],
        total_quota_hours_lost=24,
        previous_reset_predictions={"secondary": "2026-06-06T14:32:00Z"},
        new_reset_predictions={"secondary": None},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="Codex global reset detected. 2 accounts affected, ~24h saved quota lost.",
        detail="Trigger: usage_drop\nConfidence: likely\nAffected: secondary, reserve.",
        failover_guidance="Failover: claude has 80% weekly remaining.",
    )


def _provider_observation_event(
    event_id: str = "observation-1",
    detected_at: str = "2026-06-04T14:32:00Z",
) -> ResetEvent:
    return ResetEvent(
        id=event_id,
        detected_at=detected_at,
        provider="claude",
        confidence="possible",
        affected_accounts=["claude (work)"],
        trigger="single_account_usage_drop",
        account_snapshots=[
            AccountSnapshot(
                account="claude (work)",
                before_state="active",
                before_weekly_used_pct=16,
                before_weekly_resets_at="2026-06-07T14:32:00Z",
                after_state="active",
                after_weekly_used_pct=1,
                after_weekly_resets_at="2026-06-07T14:32:00Z",
            )
        ],
        total_quota_hours_lost=None,
        previous_reset_predictions={"claude (work)": "2026-06-07T14:32:00Z"},
        new_reset_predictions={"claude (work)": "2026-06-07T14:32:00Z"},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="Claude provider reset observation: claude (work) weekly usage changed 16% -> 1%.",
        detail=(
            "Trigger: single_account_usage_drop\n"
            "Confidence: possible\n"
            "Affected: claude (work).\n"
            "Weekly usage: 16% -> 1%."
        ),
    )


def _fake_id_token(email: str) -> str:
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_status_reports_malformed_config_without_traceback(monkeypatch, tmp_path):
    config_file, _backup_file, _status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    config_file.parent.mkdir(parents=True)
    config_file.write_text('{"accounts": [\n')

    result = CliRunner().invoke(cli, ["status", "--refresh"])

    assert result.exit_code == 1
    assert "TokenKick config is not valid JSON." in result.output
    assert str(config_file) in result.output
    assert "Repair the JSON" in result.output
    assert "Traceback" not in result.output


def test_status_reports_unknown_config_source_without_traceback(monkeypatch, tmp_path):
    config_file, _backup_file, _status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        '{"accounts": [{"label": "personal", "source": "future-source"}]}\n'
    )

    result = CliRunner().invoke(cli, ["status", "--refresh"])

    assert result.exit_code == 1
    assert "TokenKick config has an invalid account entry." in result.output
    assert "accounts[0]" in result.output
    assert "future-source" in result.output
    assert "Traceback" not in result.output


def test_history_reports_malformed_history_without_traceback(monkeypatch, tmp_path):
    history_file = tmp_path / "history.jsonl"
    history_file.write_text('{"label": "ok", "timestamp": 1000.0, "success": true}\n{"label":')
    monkeypatch.setattr("tokenkick.models.HISTORY_FILE", history_file)

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 1
    assert "TokenKick history contains malformed JSON." in result.output
    assert f"{history_file}:2" in result.output
    assert "Repair or remove the malformed history line" in result.output
    assert "Traceback" not in result.output


def test_history_event_result_marks_unconfirmed_attempts():
    assert _history_event_result(KickEvent(label="ok", success=True)) == "[green]✓[/green]"
    assert (
        _history_event_result(
            KickEvent(label="attempted", success=True, confirmed=False, kind="probe")
        )
        == "[yellow]~[/yellow]"
    )
    assert _history_event_result(KickEvent(label="failed", success=False)) == "[red]✗[/red]"


def test_history_table_shows_unconfirmed_attempt_marker(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="probe",
        error="Provider still reports a tiny phantom session after the kick attempt",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 0
    assert "~" in result.output
    assert "✓" not in result.output
    assert "Provider still reports" in result.output


def test_history_table_marks_provider_accepted_phantom_as_attempted(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="phantom_recovery",
        kick_type="session",
        error=PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
        evidence_response=True,
        evidence_tokens=True,
        evidence_provider_moved=False,
        post_kick_status="phantom",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 0
    assert "~" in result.output
    assert "m✗" in result.output


def test_history_table_shows_compact_codex_evidence(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
        codex_surface=CODEX_KICK_SURFACE_REPO,
        codex_attempt=2,
        codex_max_attempts=3,
        evidence_response=False,
        evidence_tokens=False,
        post_kick_status="not_checked",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 0
    assert "repo" in result.output
    assert "surface=r…" not in result.output
    assert "2/3" in result.output
    assert "r✗" in result.output


def test_history_compact_shows_wider_details(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        error=(
            "Provider still reports the session pending after the anchor probe; "
            "final diagnostic marker"
        ),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 0
    assert "Provider still reports the session pending after the anchor" in result.output
    assert "CET" not in result.output


def test_history_verbose_shows_codex_evidence_columns(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        codex_surface=CODEX_KICK_SURFACE_REPO,
        codex_attempt=2,
        codex_max_attempts=3,
        evidence_response=True,
        evidence_tokens=False,
        evidence_provider_moved=True,
        post_kick_status="moved",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history", "--verbose"], terminal_width=140)

    assert result.exit_code == 0
    assert "Surface" in result.output
    assert "Attempt" in result.output
    assert "Evidence" in result.output
    assert "repo" in result.output
    assert "surface=r…" not in result.output
    assert "2/3" in result.output
    assert "provider_moved=yes" in result.output


def test_history_timestamp_without_timezone_drops_local_abbreviation():
    assert _history_timestamp_without_timezone("2026-06-05 20:16 CEST") == "2026-06-05 20:16"
    assert _history_timestamp_without_timezone("2026-06-05 20:16") == "2026-06-05 20:16"


def test_history_verbose_omits_timezone_and_keeps_account_label(monkeypatch):
    event = KickEvent(
        label="codex (personal)",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        codex_surface=CODEX_KICK_SURFACE_REPO,
        codex_attempt=4,
        codex_max_attempts=4,
        evidence_response=True,
        evidence_tokens=True,
        codex_confirmation_method="pending_reset_clock",
        post_kick_status="pending",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])
    monkeypatch.setattr(
        "tokenkick.cli._history_event_display_time",
        lambda _event: "2026-06-05 20:16",
    )

    result = CliRunner().invoke(cli, ["history", "--verbose"], terminal_width=140)

    assert result.exit_code == 0
    assert "2026-06-05 20:16 CEST" not in result.output
    assert "2026-06-05 20:16" in result.output
    assert "codex (personal)" in result.output


def test_history_hides_status_probes_by_default(monkeypatch):
    probe = KickEvent(
        label="claude",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="status_probe",
        error="Claude /usage reconciliation completed.",
    )
    kick = KickEvent(
        label="claude",
        timestamp=1100.0,
        success=True,
        kind="session",
        response_text="Claude /usage session anchor completed.",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [probe, kick])

    result = CliRunner().invoke(cli, ["history", "--verbose"])

    assert result.exit_code == 0
    assert "session" in result.output
    assert "status_probe" not in result.output
    assert "reconciliation" not in result.output
    assert "History printed at" in result.output


def test_history_compact_prints_timestamp(monkeypatch):
    event = KickEvent(label="claude", timestamp=1100.0, success=True, kind="session")
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [event])

    result = CliRunner().invoke(cli, ["history"])

    assert result.exit_code == 0
    assert "Kick History" in result.output
    assert "History printed at" in result.output


def test_history_can_include_status_probes(monkeypatch):
    probe = KickEvent(
        label="claude",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="status_probe",
        error="Claude /usage reconciliation completed.",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [probe])

    result = CliRunner().invoke(cli, ["history", "--include-probes", "--verbose"], terminal_width=140)

    assert result.exit_code == 0
    assert "status_probe" in result.output
    assert "reconciliation" in result.output


def test_history_kind_filter_shows_status_probes(monkeypatch):
    probe = KickEvent(
        label="claude",
        timestamp=1000.0,
        success=True,
        confirmed=False,
        kind="status_probe",
        error="Claude /usage reconciliation completed.",
    )
    kick = KickEvent(label="claude", timestamp=1100.0, success=True, kind="session")
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [probe, kick])

    result = CliRunner().invoke(cli, ["history", "--kind", "status_probe", "--verbose"], terminal_width=140)

    assert result.exit_code == 0
    assert "status_probe" in result.output
    assert "session" not in result.output


def test_history_json_output_contains_codex_evidence(monkeypatch):
    event = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_attempt=1,
        codex_max_attempts=3,
        evidence_response=True,
        evidence_tokens=False,
        provider_output_excerpt='{"type":"agent_message"}',
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: [event])

    result = CliRunner().invoke(cli, ["history", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["codex_surface"] == CODEX_KICK_SURFACE_REPO_SKIP
    assert payload[0]["codex_attempt"] == 1
    assert payload[0]["evidence_response"] is True
    assert payload[0]["provider_output_excerpt"] == '{"type":"agent_message"}'
    assert "History printed at" not in result.output


def test_history_anchored_json_output_drops_non_moved_rows(monkeypatch):
    moved = KickEvent(
        label="codex",
        timestamp=1000.0,
        success=True,
        confirmed=True,
        post_kick_status="moved",
        response_text="anchored",
    )
    superseded = KickEvent(
        label="codex",
        timestamp=1001.0,
        success=True,
        confirmed=True,
        post_kick_status="superseded",
        response_text="superseded",
    )
    pending = KickEvent(
        label="codex",
        timestamp=1002.0,
        success=True,
        confirmed=False,
        post_kick_status="pending",
        response_text="pending",
    )
    unchanged = KickEvent(
        label="codex",
        timestamp=1003.0,
        success=True,
        confirmed=False,
        post_kick_status="unchanged",
        response_text="unchanged",
    )
    failed = KickEvent(
        label="codex",
        timestamp=1004.0,
        success=False,
        confirmed=False,
        error="failed",
    )
    monkeypatch.setattr(
        "tokenkick.cli.load_kick_history",
        lambda limit=200: [moved, superseded, pending, unchanged, failed],
    )

    result = CliRunner().invoke(cli, ["history", "--anchored", "--json-output"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [event["response_text"] for event in payload] == ["anchored"]


def test_reset_log_empty_state(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", tmp_path / "reset-events.jsonl")

    result = CliRunner().invoke(cli, ["reset-log"])

    assert result.exit_code == 0
    assert "No reset events logged." in result.output
    assert "Reset log printed at" in result.output


def test_reset_log_table_detail_json_and_csv(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event()
    assert append_reset_event(event)

    table_result = CliRunner().invoke(cli, ["reset-log", "--provider", "codex", "--since", "30d"])
    detail_result = CliRunner().invoke(cli, ["reset-log", "--detail", event.id])
    json_result = CliRunner().invoke(cli, ["reset-log", "--json-output"])
    csv_result = CliRunner().invoke(cli, ["reset-log", "--csv"])

    assert table_result.exit_code == 0
    assert "Reset Event Log" in table_result.output
    assert "secondary, reserve" in table_result.output
    assert "Reset log printed at" in table_result.output
    assert detail_result.exit_code == 0
    assert "Affected Accounts" in detail_result.output
    assert "Use next" in detail_result.output
    assert "~24h" in detail_result.output
    assert "Reset log printed at" in detail_result.output
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["events"][0]["id"] == event.id
    assert payload["events"][0]["account_impacts"][0]["quota_hours_lost"] == 24.0
    assert "Reset log printed at" not in json_result.output
    assert csv_result.exit_code == 0
    assert "affected_accounts" in csv_result.output
    assert "Reset log printed at" not in csv_result.output


def test_reset_log_ack_json_output(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event()
    assert append_reset_event(event)

    ack_result = CliRunner().invoke(cli, ["reset-log", "ack", event.id, "--json-output"])
    assert ack_result.exit_code == 0
    envelope = json.loads(ack_result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == "Acknowledged 1 reset event(s)."
    assert envelope["payload"]["acknowledged"][0]["id"] == event.id
    assert envelope["payload"]["acknowledged"][0]["acknowledged_at"] is not None

    latest_result = CliRunner().invoke(cli, ["reset-log", "ack", "--latest", "--json-output"])
    assert latest_result.exit_code == 0
    latest = json.loads(latest_result.output)
    assert latest["ok"] is True
    assert latest["payload"]["acknowledged"] == []
    assert latest["message"] == "No matching unacknowledged reset events."

    invalid_result = CliRunner().invoke(cli, ["reset-log", "ack", "--json-output"])
    assert invalid_result.exit_code == 0
    invalid = json.loads(invalid_result.output)
    assert invalid["ok"] is False
    assert invalid["error_code"] == "reset_log_ack_invalid"


def test_reset_log_renders_provider_observation(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _provider_observation_event()
    assert append_reset_event(event)

    table_result = CliRunner().invoke(cli, ["reset-log"])
    detail_result = CliRunner().invoke(cli, ["reset-log", "--detail", event.id])
    json_result = CliRunner().invoke(cli, ["reset-log", "--json-output"])

    assert table_result.exit_code == 0
    assert "provider observation" in table_result.output
    assert "status changed" in table_result.output
    assert detail_result.exit_code == 0
    assert "provider reset observation" in detail_result.output
    assert "quota lost" not in detail_result.output
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["events"][0]["trigger"] == "single_account_usage_drop"
    assert payload["events"][0]["account_impacts"][0]["quota_hours_lost"] is None


def test_reset_log_ack_latest_and_unacknowledged_filter(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    first = _reset_event(event_id="first", detected_at="2026-06-04T14:32:00Z")
    second = _reset_event(event_id="second", detected_at="2026-06-05T14:32:00Z")
    assert append_reset_event(first)
    assert append_reset_event(second)

    ack_result = CliRunner().invoke(cli, ["reset-log", "ack", "--latest"])
    json_result = CliRunner().invoke(cli, ["reset-log", "--unacknowledged", "--json-output"])

    assert ack_result.exit_code == 0
    assert "second" in ack_result.output
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert [event["id"] for event in payload["events"]] == ["first"]
    loaded = load_reset_events()
    assert loaded[0].acknowledged_at is None
    assert loaded[1].acknowledged_by == "cli"


def test_status_shows_recent_reset_banner(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    assert append_reset_event(_reset_event(detected_at=_utc_iso(datetime.now(timezone.utc))))
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE, used_percent=10)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: ([account], [status], {}))
    monkeypatch.setattr("tokenkick.cli._status_cache_needs_refresh", lambda _entries, _config: False)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Global reset detected on Codex" in result.output
    assert "tk reset-log" in result.output


def test_status_hides_acknowledged_reset_banner(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event(detected_at=_utc_iso(datetime.now(timezone.utc)))
    event.acknowledged_at = _utc_iso(datetime.now(timezone.utc))
    event.acknowledged_by = "cli"
    assert append_reset_event(event)
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE, used_percent=10)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: ([account], [status], {}))
    monkeypatch.setattr("tokenkick.cli._status_cache_needs_refresh", lambda _entries, _config: False)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Global reset detected on Codex" not in result.output


def test_status_shows_possible_reset_as_subtle_banner(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    assert append_reset_event(
        _reset_event(confidence="possible", detected_at=_utc_iso(datetime.now(timezone.utc)))
    )
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE, used_percent=10)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: ([account], [status], {}))
    monkeypatch.setattr("tokenkick.cli._status_cache_needs_refresh", lambda _entries, _config: False)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Possible global reset on Codex" in result.output


def test_status_shows_provider_observation_banner(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _provider_observation_event(detected_at=_utc_iso(datetime.now(timezone.utc)))
    assert append_reset_event(event)
    account = AccountConfig(label="claude (work)", provider="claude")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE, used_percent=1)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: ([account], [status], {}))
    monkeypatch.setattr("tokenkick.cli._status_cache_needs_refresh", lambda _entries, _config: False)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Provider reset observation on Claude" in result.output
    assert f"tk reset-log --detail {event.id}" in result.output


def test_global_reset_response_notifies_likely_without_invalidating_pending(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    reset_file = tmp_path / "reset-events.jsonl"
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    future_kick_at = datetime.now(timezone.utc) + timedelta(hours=1)
    future_work_start = datetime.now(timezone.utc)
    future_work_end = future_work_start + timedelta(hours=8)
    pending = PendingKick(
        account_key="codex-direct|codex|secondary",
        account_label="secondary",
        provider="codex",
        kick_at=to_utc_iso(future_kick_at),
        created_at=to_utc_iso(future_work_start),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(future_work_start),
        work_end=to_utc_iso(future_work_end),
    )
    scheduling_mod.save_pending_kicks({pending.account_key: pending})
    notified = []
    monkeypatch.setattr("tokenkick.cli.notify_reset_event", lambda event, config: notified.append(event.id) or True)
    event = _reset_event()

    handled = _handle_global_reset_event(
        event,
        Config(notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic")),
    )

    assert handled is event
    assert event.pending_kicks_invalidated == []
    assert event.notification_sent is True
    assert notified == [event.id]
    assert len(load_reset_events()) == 1
    assert pending_file.exists()


def test_global_reset_response_skips_possible_notification_by_default(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    notified = []
    monkeypatch.setattr("tokenkick.cli.notify_reset_event", lambda event, config: notified.append(event.id) or True)
    event = _reset_event(confidence="possible")

    handled = _handle_global_reset_event(
        event,
        Config(notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic")),
    )

    assert handled is event
    assert event.pending_kicks_invalidated == []
    assert event.notification_sent is False
    assert event.notification_skip_reason == "below notification threshold (likely)"
    assert notified == []
    assert len(load_reset_events()) == 1


def test_provider_observation_notifies_through_account_route(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_reset_event",
        lambda event, notifications: sent.append((event.id, notifications.backend)) or True,
    )
    event = _provider_observation_event()
    account = AccountConfig(
        label="claude (work)",
        provider="claude",
        notification_backends=["telegram"],
    )

    handled = _handle_global_reset_event(
        event,
        Config(
            accounts=[account],
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat",
                enabled_backends=["ntfy", "telegram"],
            ),
        ),
    )

    assert handled is event
    assert event.notification_sent is True
    assert event.notification_skip_reason is None
    assert sent == [(event.id, "telegram")]
    assert len(load_reset_events()) == 1


def test_provider_observation_respects_disabled_account_notifications(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    sent = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_reset_event",
        lambda event, notifications: (
            sent.append((event.id, notifications.backend)) or True
            if notifications.enabled
            else False
        ),
    )
    event = _provider_observation_event()
    account = AccountConfig(
        label="claude (work)",
        provider="claude",
        notifications_enabled=False,
    )

    handled = _handle_global_reset_event(
        event,
        Config(
            accounts=[account],
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        ),
    )

    assert handled is event
    assert event.notification_sent is False
    assert event.notification_skip_reason == "notifications disabled"
    assert sent == []


def test_global_reset_notification_threshold_can_include_possible(monkeypatch, tmp_path):
    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    notified = []
    monkeypatch.setattr("tokenkick.cli.notify_reset_event", lambda event, config: notified.append(event.id) or True)
    event = _reset_event(confidence="possible")

    handled = _handle_global_reset_event(
        event,
        Config(
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
            global_reset_notify_min_confidence="possible",
        ),
    )

    assert handled is event
    assert event.notification_sent is True
    assert event.notification_skip_reason is None
    assert notified == [event.id]


def test_global_reset_response_invalidates_confirmed_event(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    reset_file = tmp_path / "reset-events.jsonl"
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    future_kick_at = datetime.now(timezone.utc) + timedelta(hours=1)
    future_work_start = datetime.now(timezone.utc)
    pending = PendingKick(
        account_key="codex-direct|codex|secondary",
        account_label="secondary",
        provider="codex",
        kick_at=to_utc_iso(future_kick_at),
        created_at=to_utc_iso(future_work_start),
        reason="optimal",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(future_work_start),
        work_end=to_utc_iso(future_work_start + timedelta(hours=8)),
    )
    scheduling_mod.save_pending_kicks({pending.account_key: pending})
    monkeypatch.setattr("tokenkick.cli.notify_reset_event", lambda _event, _config: True)
    event = _reset_event(confidence="confirmed")

    handled = _handle_global_reset_event(
        event,
        Config(notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic")),
    )

    assert handled is event
    assert event.pending_kicks_invalidated == ["secondary"]
    assert event.notification_sent is True
    assert load_pending_kicks() == {}


def test_history_details_prefers_kick_response_text():
    event = KickEvent(
        label="codex",
        success=True,
        response_text="TokenKick anchor probe completed.",
        reported_model="gpt-5-codex",
        total_tokens=13,
    )

    assert _history_event_details(event) == "TokenKick anchor probe completed."


def test_history_details_keeps_response_visible_for_ambiguous_kick():
    event = KickEvent(
        label="codex",
        success=True,
        error="Codex accepted usage, but session status is still ambiguous",
        response_text="TokenKick anchor probe completed.",
    )

    assert _history_event_details(event).startswith("TokenKick anchor probe completed.")


def test_history_details_points_to_json_when_provider_excerpt_saved():
    event = KickEvent(
        label="codex",
        success=True,
        error="Codex accepted usage, but session status is still ambiguous",
        provider_output_excerpt='{"event":"done"}',
    )

    assert _history_event_details(event).startswith("Provider output saved in JSON")


def test_history_details_marks_codex_reset_clock_confirmation():
    event = KickEvent(
        label="codex",
        success=True,
        confirmed=True,
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_attempt=1,
        codex_max_attempts=3,
        evidence_response=True,
        evidence_provider_moved=True,
        codex_confirmation_method="reset_clock",
        codex_anchor_match_delta_seconds=12.2,
        post_kick_status="moved",
    )

    details = _history_event_details(event)

    assert "surface=repo-skip" in details
    assert "method=reset_clock" in details
    assert "delta=12.2s" in details


def test_history_details_marks_codex_provider_moved_confirmation():
    event = KickEvent(
        label="codex",
        success=True,
        confirmed=True,
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_attempt=1,
        codex_max_attempts=4,
        evidence_response=True,
        evidence_provider_moved=True,
        codex_confirmation_method="provider_moved",
        post_kick_status="moved",
    )

    details = _history_event_details(event)

    assert _history_event_result(event) == "[green]✓[/green]"
    assert "provider_moved=yes" in details
    assert "method=provider_moved" in details
    assert "post=moved" in details


def test_history_details_marks_codex_command_only_as_attempted():
    event = KickEvent(
        label="codex",
        success=True,
        confirmed=False,
        error=CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR,
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_attempt=1,
        codex_max_attempts=4,
        evidence_response=True,
        codex_confirmation_method="none",
        post_kick_status="not_checked",
    )

    details = _history_event_details(event)

    assert _history_event_result(event) == "[yellow]~[/yellow]"
    assert "method=none" in details
    assert "post=not_checked" in details


def test_discover_accounts_from_codexbar(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.shutil.which", lambda name: "/opt/homebrew/bin/codexbar")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_000)
    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", lambda: ([], []))
    monkeypatch.setattr("tokenkick.cli._discover_codex_session_accounts", lambda: ([], []))
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_codexbar_payload()),
            stderr="",
        ),
    )

    accounts, statuses, summary = _discover_accounts_and_statuses()

    assert summary == "Found 2 accounts via auto-discovery: claude, codex."
    assert [account.label for account in accounts] == ["codex (dev)", "claude"]
    assert [account.source for account in accounts] == [
        DataSource.CODEXBAR_CLI,
        DataSource.CODEXBAR_CLI,
    ]
    assert accounts[0].codexbar_account == "dev@example.test"
    assert [account.auto_kick for account in accounts] == [True, True]
    assert [status.state for status in statuses] == [AccountState.ACTIVE, AccountState.ACTIVE]


def test_discover_uses_codexbar_all_accounts_and_skips_sessions(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.shutil.which", lambda name: "/opt/homebrew/bin/codexbar")
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: 1_779_000_000)
    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", lambda: ([], []))

    all_accounts_payload = [
        {
            "account": "personal@example.test",
            "provider": "codex",
            "source": "auto",
            "usage": {
                "accountEmail": "personal@example.test",
                "secondary": {"usedPercent": 17, "windowMinutes": 10080},
            },
        },
        {
            "account": "work@example.test",
            "provider": "codex",
            "source": "auto",
            "usage": {
                "accountEmail": "work@example.test",
                "secondary": {"usedPercent": 0, "windowMinutes": 10080},
            },
        },
    ]
    legacy_payload = [
        {
            "provider": "codex",
            "usage": {
                "accountEmail": "selected@example.test",
                "secondary": {"usedPercent": 99, "windowMinutes": 10080},
            },
        },
        {
            "provider": "claude",
            "usage": {"secondary": {"usedPercent": 4, "windowMinutes": 10080}},
        },
    ]

    def fake_run(cmd, *args, **kwargs):
        if cmd[:4] == ["codexbar", "usage", "--provider", "codex"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(all_accounts_payload), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(legacy_payload), stderr="")

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)
    monkeypatch.setattr(
        "tokenkick.cli._discover_codex_session_accounts",
        lambda: (_ for _ in ()).throw(AssertionError("session fallback should not run")),
    )

    accounts, statuses, summary = _discover_accounts_and_statuses()

    assert summary == "Found 3 accounts via auto-discovery: claude, codex."
    assert [account.label for account in accounts] == [
        "codex (personal)",
        "codex (work)",
        "claude",
    ]
    assert [account.codexbar_account for account in accounts[:2]] == [
        "personal@example.test",
        "work@example.test",
    ]
    assert [account.source for account in accounts] == [
        DataSource.CODEXBAR_CLI,
        DataSource.CODEXBAR_CLI,
        DataSource.CODEXBAR_CLI,
    ]
    assert [status.state for status in statuses] == [
        AccountState.ACTIVE,
        AccountState.FRESH,
        AccountState.ACTIVE,
    ]


def test_discover_codexbar_account_errors_do_not_mark_all_accounts_available(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: 1_779_000_000)

    all_accounts_payload = [
        {
            "account": "personal@example.test",
            "provider": "codex",
            "source": "auto",
            "error": {"message": "Codex returned invalid data"},
        }
    ]

    def fake_run(cmd, *args, **kwargs):
        if cmd[:4] == ["codexbar", "usage", "--provider", "codex"]:
            return SimpleNamespace(returncode=1, stdout=json.dumps(all_accounts_payload), stderr="")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

    accounts, statuses, codex_all_accounts_available = _discover_codexbar_accounts()

    assert codex_all_accounts_available is False
    assert accounts == []
    assert statuses == []


def test_email_from_id_token_decodes_email_claim():
    assert email_from_id_token(_fake_id_token("primary@example.test")) == "primary@example.test"


def test_discover_codex_session_accounts_reads_primary_and_managed_homes(tmp_path, monkeypatch):
    home = tmp_path
    primary_home = home / ".codex"
    primary_sessions = primary_home / "sessions"
    managed_home = home / "managed-home"
    managed_sessions = managed_home / "sessions"
    managed_file = (
        home
        / "Library"
        / "Application Support"
        / "CodexBar"
        / "managed-codex-accounts.json"
    )

    primary_sessions.mkdir(parents=True)
    managed_sessions.mkdir(parents=True)
    managed_file.parent.mkdir(parents=True)
    (primary_home / "auth.json").write_text(
        json.dumps({"tokens": {"id_token": _fake_id_token("primary@example.test")}})
    )
    managed_file.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "email": "managed@example.test",
                        "managedHomePath": str(managed_home),
                    },
                    {
                        "email": "nosessions@example.test",
                        "managedHomePath": str(home / "missing-home"),
                    },
                ]
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: home)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            used_percent=12.0,
        ),
    )

    accounts, statuses = _discover_codex_session_accounts()

    assert [account.label for account in accounts] == ["primary", "managed", "nosessions"]
    assert [account.source for account in accounts] == [
        DataSource.CODEX_SESSION_FILE,
        DataSource.CODEX_SESSION_FILE,
        DataSource.CODEX_SESSION_FILE,
    ]
    assert accounts[0].session_path == str(primary_sessions)
    assert accounts[1].session_path == str(managed_sessions)
    assert accounts[2].session_path == str(home / "missing-home" / "sessions")
    assert [status.state for status in statuses] == [
        AccountState.ACTIVE,
        AccountState.ACTIVE,
        AccountState.UNKNOWN,
    ]
    assert statuses[2].error == "No session data — use this account to start tracking."


def test_discover_direct_accounts_reads_codex_and_claude_identity(tmp_path, monkeypatch):
    home = tmp_path
    codex_home = home / ".codex"
    codex_sessions = codex_home / "sessions"
    codex_sessions.mkdir(parents=True)
    (codex_sessions / "session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-23T04:18:33Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "secondary": {"used_percent": 12, "window_minutes": 10080}
                    },
                },
            }
        )
        + "\n"
    )
    (codex_home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "account_id": "acct_codex",
                    "id_token": _fake_id_token("codex@example.test"),
                }
            }
        )
    )
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "userID": "user-uuid",
                "oauthAccount": {
                    "accountUuid": "claude-account",
                    "organizationUuid": "claude-org",
                    "emailAddress": "claude@example.test",
                },
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: home)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )

    accounts, statuses = _discover_direct_accounts()

    assert [account.provider for account in accounts] == ["codex", "claude"]
    assert [account.source for account in accounts] == [
        DataSource.CODEX_DIRECT,
        DataSource.CLAUDE_DIRECT,
    ]
    assert accounts[0].identity_provider_id == "acct_codex"
    assert accounts[0].identity_email == "codex@example.test"
    assert accounts[1].identity_provider_id == "claude-account"
    assert accounts[1].identity_org_id == "claude-org"
    assert [status.state for status in statuses] == [AccountState.ACTIVE, AccountState.ACTIVE]


def test_discover_direct_accounts_reads_antigravity_cli_identity(tmp_path, monkeypatch):
    app_dir = tmp_path / ".gemini" / "antigravity-cli"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("tokenkick.cli.read_codex_identity", lambda _home: None)
    monkeypatch.setattr("tokenkick.cli.read_claude_identity", lambda: None)
    monkeypatch.setattr("tokenkick.discovery.antigravity_cli_detected", lambda: True)
    monkeypatch.setattr(
        "tokenkick.discovery.read_antigravity_cli_identity",
        lambda: "dev@example.test",
    )
    monkeypatch.setattr("tokenkick.discovery.antigravity_cli_app_dir", lambda: app_dir)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.UNKNOWN),
    )

    accounts, statuses = _discover_direct_accounts()

    assert [account.provider for account in accounts] == ["antigravity"]
    assert accounts[0].source == DataSource.ANTIGRAVITY_CLI
    assert accounts[0].identity_email == "dev@example.test"
    assert accounts[0].provider_home == str(app_dir)
    assert accounts[0].auto_kick is False
    assert accounts[0].session_auto_kick is False
    assert statuses[0].state == AccountState.UNKNOWN


def test_discover_direct_accounts_adds_antigravity_cli_without_identity(tmp_path, monkeypatch):
    app_dir = tmp_path / ".gemini" / "antigravity-cli"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("tokenkick.cli.read_codex_identity", lambda _home: None)
    monkeypatch.setattr("tokenkick.cli.read_claude_identity", lambda: None)
    monkeypatch.setattr("tokenkick.discovery.antigravity_cli_detected", lambda: True)
    monkeypatch.setattr("tokenkick.discovery.read_antigravity_cli_identity", lambda: None)
    monkeypatch.setattr("tokenkick.discovery.antigravity_cli_app_dir", lambda: app_dir)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.UNKNOWN),
    )

    accounts, statuses = _discover_direct_accounts()

    assert [account.provider for account in accounts] == ["antigravity"]
    assert accounts[0].label == "antigravity"
    assert accounts[0].source == DataSource.ANTIGRAVITY_CLI
    assert accounts[0].identity_email is None
    assert accounts[0].codexbar_account is None
    assert accounts[0].provider_home == str(app_dir)
    assert accounts[0].auto_kick is False
    assert accounts[0].session_auto_kick is False
    assert statuses[0].state == AccountState.UNKNOWN


def test_merge_discovered_accounts_aliases_antigravity_cli_to_codexbar():
    cli_account = AccountConfig(
        label="dev",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
        identity_email="dev@example.test",
        codexbar_account="dev@example.test",
        label_origin="auto",
    )
    codexbar_account = AccountConfig(
        label="dev",
        provider="antigravity",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="antigravity",
        codexbar_account="dev@example.test",
        label_origin="auto",
    )
    cli_status = AccountStatus(label="dev", state=AccountState.UNKNOWN)
    codexbar_status = AccountStatus(
        label="dev",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        quota_windows=[{"id": "antigravity-quota-summary-gemini-5h"}],
    )

    accounts, statuses = _merge_discovered_accounts(
        [(cli_account, cli_status), (codexbar_account, codexbar_status)]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEXBAR_CLI
    assert statuses[0].used_percent == 12.0


def test_discover_direct_accounts_reads_tokenkick_managed_codex_homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = tmp_path / "tokenkick"
    managed_home = config_dir / "codex-homes" / "personal"
    managed_home.mkdir(parents=True)
    (managed_home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "account_id": "acct_nori",
                    "id_token": _fake_id_token("personal@example.test"),
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: home)
    monkeypatch.setattr("tokenkick.discovery.Path.home", lambda: home)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.cli.read_claude_identity", lambda: None)
    monkeypatch.setattr("tokenkick.discovery.codex_appserver_bucket_metadata", lambda _home: [])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )

    accounts, statuses = _discover_direct_accounts()

    assert [account.label for account in accounts] == ["personal"]
    assert accounts[0].provider_home == str(managed_home)
    assert accounts[0].identity_provider_id == "acct_nori"
    assert accounts[0].identity_email == "personal@example.test"
    assert [status.state for status in statuses] == [AccountState.ACTIVE]


def test_discover_direct_accounts_prepares_claude_setup(tmp_path, monkeypatch):
    calls = []
    home = tmp_path / "home"
    config_dir = tmp_path / "tokenkick"
    home.mkdir()
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("tokenkick.discovery.Path.home", lambda: home)
    monkeypatch.setattr("tokenkick.cli.read_codex_identity", lambda _path: None)
    monkeypatch.setattr(
        "tokenkick.cli.read_claude_identity",
        lambda: DirectIdentity(
            provider="claude",
            provider_account_id="claude-account",
            email="claude@example.test",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.ensure_claude_probe_ready",
        lambda path: calls.append(("probe", path)),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.ensure_claude_cli_settings",
        lambda path: calls.append(("settings", path)),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )

    accounts, _statuses = _discover_direct_accounts(prepare_claude_setup=True)

    assert [account.provider for account in accounts] == ["claude"]
    assert calls == [("probe", config_dir), ("settings", home)]


def test_codex_direct_account_key_is_home_scoped_when_home_is_known():
    account = AccountConfig(
        label="codex (shared)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-shared",
        identity_email="shared@example.test",
        provider_home="/tmp/codex-home",
        session_path="/tmp/codex-home/sessions",
    )

    assert account_key_string(account) == "codex-home|codex|/tmp/codex-home"


def test_merge_discovered_keeps_primary_and_managed_codex_homes_separate():
    primary = AccountConfig(
        label="shared",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-shared",
        identity_email="shared@example.test",
        provider_home="/tmp/primary",
        session_path="/tmp/primary/sessions",
    )
    managed = AccountConfig(
        label="shared",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-shared",
        identity_email="shared@example.test",
        provider_home="/tmp/managed",
        session_path="/tmp/managed/sessions",
    )
    primary_status = AccountStatus(label="shared", state=AccountState.ACTIVE, used_percent=91.0)
    managed_status = AccountStatus(label="shared", state=AccountState.ACTIVE, used_percent=16.0)

    accounts, statuses = _merge_discovered_accounts(
        [(primary, primary_status), (managed, managed_status)]
    )

    assert [account.provider_home for account in accounts] == ["/tmp/primary", "/tmp/managed"]
    assert [status.used_percent for status in statuses] == [91.0, 16.0]
    assert len({account.label for account in accounts}) == 2


def test_configured_managed_codex_home_keeps_exact_status_when_primary_same_email(monkeypatch):
    configured = AccountConfig(
        label="codex (shared)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-shared",
        identity_email="shared@example.test",
        provider_home="/tmp/managed",
        session_path="/tmp/managed/sessions",
    )
    primary = replace(
        configured,
        label="shared",
        provider_home="/tmp/primary",
        session_path="/tmp/primary/sessions",
    )
    managed = replace(configured, label="shared")
    primary_status = AccountStatus(label="shared", state=AccountState.ACTIVE, used_percent=91.0)
    managed_status = AccountStatus(label="shared", state=AccountState.ACTIVE, used_percent=16.0)

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [primary, managed],
            [primary_status, managed_status],
            "Found 2 accounts via auto-discovery: codex.",
        ),
    )

    accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[configured])
    )

    assert accounts[0].provider_home == "/tmp/managed"
    assert statuses[0].used_percent == 16.0
    assert [account.provider_home for account in new_accounts] == ["/tmp/primary"]


def test_codex_home_key_migration_rekeys_cache_and_pending(tmp_path, monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tokenkick.migrations.CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE",
        tmp_path / ".tokenkick" / "config.json.pre-codex-home-identity-repair-backup",
    )
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    account = AccountConfig(
        label="codex (shared)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-shared",
        identity_email="shared@example.test",
        provider_home="/tmp/managed",
        session_path="/tmp/managed/sessions",
    )
    Config(accounts=[account]).save()
    old_key = "identity|codex|acct-shared"
    new_key = account_key_string(account)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    old_key: {
                        "account": account.to_dict(),
                        "status": {
                            "label": "codex (shared)",
                            "state": "active",
                            "used_percent": 16.0,
                            "source_detail": "codex-session-jsonl",
                        },
                        "cached_at": "2026-05-23T15:20:00Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )
    now = datetime.now(timezone.utc)
    pending = scheduling_mod.PendingKick(
        account_key=old_key,
        account_label=account.label,
        provider=account.provider,
        kick_at=_utc_iso(now + timedelta(hours=2)),
        created_at=_utc_iso(now - timedelta(hours=1)),
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=_utc_iso(now + timedelta(hours=2)),
        work_end=_utc_iso(now + timedelta(hours=6)),
    )
    scheduling_mod.save_pending_kicks({old_key: pending})

    migrated = _migrate_codex_home_keys_if_needed(Config.load(), emit_notice=False)

    assert migrated.migrations[CODEX_HOME_KEY_MIGRATION_KEY] is True
    cache = json.loads(cache_file.read_text())
    assert old_key not in cache["accounts"]
    assert new_key in cache["accounts"]
    pending_after = scheduling_mod.load_pending_kicks(now)
    assert old_key not in pending_after
    assert pending_after[new_key].account_key == new_key
    assert json.loads(config_file.read_text())["migrations"][CODEX_HOME_KEY_MIGRATION_KEY] is True


def test_codex_home_identity_repair_rebinds_primary_to_matching_managed_home(
    tmp_path,
    monkeypatch,
):
    config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tokenkick.migrations.CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE",
        tmp_path / ".tokenkick" / "config.json.pre-codex-home-identity-repair-backup",
    )
    home = tmp_path / "home"
    primary_home = home / ".codex"
    managed_home = home / "managed-secondary"
    managed_file = home / "Library" / "Application Support" / "CodexBar" / "managed-codex-accounts.json"
    managed_file.parent.mkdir(parents=True)
    managed_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {
                        "email": "secondary@example.test",
                        "providerAccountID": "acct-secondary",
                        "managedHomePath": str(managed_home),
                    }
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: home)

    def fake_identity(path):
        if path == primary_home:
            return DirectIdentity("codex", provider_account_id="acct-user", email="user@example.test")
        if path == managed_home:
            return DirectIdentity("codex", provider_account_id="acct-secondary", email="secondary@example.test")
        return None

    monkeypatch.setattr("tokenkick.cli.read_codex_identity", fake_identity)
    account = AccountConfig(
        label="codex (secondary)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-secondary",
        identity_email="secondary@example.test",
        provider_home=str(primary_home),
        session_path=str(primary_home / "sessions"),
        codexbar_account="secondary@example.test",
    )
    Config(accounts=[account]).save()
    old_key = account_key_string(account)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    old_key: {
                        "account": replace(
                            account,
                            identity_provider_id="acct-user",
                            identity_email="user@example.test",
                        ).to_dict(),
                        "status": {
                            "label": "codex (secondary)",
                            "state": "active",
                            "used_percent": 24.0,
                            "source_detail": "codex-session-jsonl",
                        },
                        "cached_at": "2026-05-23T15:20:00Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )

    repaired = _repair_codex_home_identity_drift_if_needed(Config.load(), emit_notice=False)

    assert repaired.accounts[0].provider_home == str(managed_home)
    assert repaired.accounts[0].session_path == str(managed_home / "sessions")
    assert repaired.accounts[0].label == "codex (secondary)"
    assert old_key not in json.loads(cache_file.read_text())["accounts"]
    assert json.loads(config_file.read_text())["accounts"][0]["provider_home"] == str(managed_home)


def test_status_cache_rejects_codex_identity_mismatch(tmp_path, monkeypatch):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex (secondary)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-secondary",
        identity_email="secondary@example.test",
        provider_home="/tmp/primary",
        session_path="/tmp/primary/sessions",
    )
    cached_account = replace(
        account,
        identity_provider_id="acct-user",
        identity_email="user@example.test",
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    account_key_string(account): {
                        "account": cached_account.to_dict(),
                        "status": {
                            "label": "codex (secondary)",
                            "state": "active",
                            "used_percent": 24.0,
                        },
                        "cached_at": "2026-05-23T15:20:00Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )

    assert _load_status_cache(Config(accounts=[account])) is None


def test_v04_direct_source_migration_runs_once_and_writes_backup(tmp_path, monkeypatch):
    config_file, backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy = Config(
        accounts=[
            AccountConfig(
                label="dev",
                provider="codex",
                source=DataSource.CODEXBAR_CLI,
                auto_kick=True,
                visible=False,
                codexbar_provider="codex",
                codexbar_account="dev@example.test",
            )
        ]
    )
    legacy.save()
    original = config_file.read_text()
    direct = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-dev",
        identity_email="dev@example.test",
        session_path="/tmp/dev/sessions",
    )
    calls = 0

    def discover_direct():
        nonlocal calls
        calls += 1
        duplicate = replace(direct, label="dev-duplicate", session_path="/tmp/dev/other-sessions")
        return (
            [direct, duplicate],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=12.0,
                    source_detail="codex-session-jsonl",
                ),
                AccountStatus(label="dev-duplicate", state=AccountState.UNKNOWN),
            ],
        )

    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", discover_direct)

    migrated = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)
    migrated_again = _migrate_v04_direct_sources_if_needed(migrated, emit_notice=False)

    assert calls == 1
    assert backup_file.read_text() == original
    assert migrated_again.accounts[0].source == DataSource.CODEX_DIRECT
    assert migrated_again.accounts[0].identity_provider_id == "acct-dev"
    assert migrated_again.accounts[0].auto_kick is True
    assert migrated_again.accounts[0].visible is False
    assert migrated_again.migrations[DIRECT_SOURCE_MIGRATION_KEY] is True
    saved = Config.load()
    assert saved.accounts[0].source == DataSource.CODEX_DIRECT
    assert saved.migrations[DIRECT_SOURCE_MIGRATION_KEY] is True


def test_v04_direct_source_migration_prunes_old_cache_entries(tmp_path, monkeypatch):
    config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    Config(accounts=[legacy_account]).save()
    direct_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-dev",
        identity_email="dev@example.test",
    )
    old_key = "account|codex|dev@example.test"
    _save_status_cache(
        [legacy_account],
        {
            old_key: AccountStatus(
                label="dev",
                state=AccountState.ACTIVE,
                source_detail="codexbar-cli",
            )
        },
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [direct_account],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    source_detail="codex-session-jsonl",
                )
            ],
        ),
    )

    _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)

    cache = json.loads((config_file.parent / "status-cache.json").read_text())
    assert old_key not in cache["accounts"]


def test_provider_first_label_migration_runs_once_and_rekeys_state(tmp_path, monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    config_file, _backup_file, status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    label_backup_file = config_file.parent / "config.json.pre-label-format-backup"
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    now = datetime.now(timezone.utc)
    account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-secondary",
        identity_email="personal@example.test",
    )
    Config(
        accounts=[account],
        schedule=ScheduleConfig(accounts={"personal": WorkSchedule(enabled=True)}),
    ).save()
    original = config_file.read_text()
    _save_status_cache(
        [account],
        {
            "identity|codex|acct-secondary": AccountStatus(
                label="personal",
                state=AccountState.ACTIVE,
                source_detail="codex-session-jsonl",
            )
        },
    )
    pending_file.write_text(
        json.dumps(
            {
                "identity|codex|acct-secondary": {
                    "account_key": "identity|codex|acct-secondary",
                    "account_label": "personal",
                    "provider": "codex",
                    "kick_at": _utc_iso(now + timedelta(hours=2)),
                    "created_at": _utc_iso(now - timedelta(hours=1)),
                    "reason": "optimal",
                    "windows_needed": 2,
                    "expected_waste_minutes": 0,
                    "waste_location": "none",
                    "work_start": _utc_iso(now + timedelta(hours=2)),
                    "work_end": _utc_iso(now + timedelta(hours=10)),
                    "notified": True,
                }
            }
        )
        + "\n"
    )

    migrated = _migrate_provider_first_labels_if_needed(Config.load(), emit_notice=False)
    migrated_again = _migrate_provider_first_labels_if_needed(migrated, emit_notice=False)

    assert label_backup_file.read_text() == original
    assert migrated_again.accounts[0].label == "codex (personal)"
    assert migrated_again.accounts[0].label_origin == "auto"
    assert migrated_again.migrations[LABEL_FORMAT_MIGRATION_KEY] is True
    assert "personal" not in migrated_again.schedule.accounts
    assert "codex (personal)" in migrated_again.schedule.accounts
    cache = json.loads(status_cache_file.read_text())
    entry = cache["accounts"]["identity|codex|acct-secondary"]
    assert entry["account"]["label"] == "codex (personal)"
    assert entry["status"]["label"] == "codex (personal)"
    pending = scheduling_mod.load_pending_kicks(now)
    assert pending["identity|codex|acct-secondary"].account_label == "codex (personal)"


def test_provider_first_label_migration_renames_account_provider_format(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="personal (codex)",
                provider="codex",
                source=DataSource.CODEX_DIRECT,
                identity_provider_id="acct-secondary",
                identity_email="personal@example.test",
            )
        ]
    ).save()

    migrated = _migrate_provider_first_labels_if_needed(Config.load(), emit_notice=False)

    assert migrated.accounts[0].label == "codex (personal)"


def test_provider_first_label_migration_renames_codexbar_default(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="work",
                provider="codex",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="codex",
                codexbar_account="work@example.test",
            )
        ]
    ).save()

    migrated = _migrate_provider_first_labels_if_needed(Config.load(), emit_notice=False)

    assert migrated.accounts[0].label == "codex (work)"


def test_provider_first_label_migration_skips_bare_provider_labels(
    tmp_path,
    monkeypatch,
    capsys,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CLAUDE_DIRECT,
                identity_provider_id="claude-account",
                identity_email="work@example.test",
            ),
            AccountConfig(
                label="codex",
                provider="codex",
                source=DataSource.CODEX_DIRECT,
                identity_provider_id="acct-codex",
                identity_email="codex@example.test",
            ),
        ]
    ).save()

    migrated = _migrate_provider_first_labels_if_needed(Config.load())
    captured = capsys.readouterr()

    assert [account.label for account in migrated.accounts] == ["claude", "codex"]
    assert "did not rename labels" in captured.err
    assert "claude" in captured.err
    assert "codex" in captured.err
    assert 'tk setup --rename-label "claude"' in captured.err


def test_setup_rename_label_opts_bare_provider_into_provider_first(
    tmp_path,
    monkeypatch,
):
    config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CLAUDE_DIRECT,
                identity_provider_id="claude-account",
                identity_email="work@example.test",
            )
        ]
    ).save()

    result = CliRunner().invoke(cli, ["setup", "--rename-label", "claude"])

    assert result.exit_code == 0
    assert 'Renamed "claude" -> "claude (work)"' in result.output
    saved = Config.load()
    assert saved.accounts[0].label == "claude (work)"
    assert saved.accounts[0].label_origin == "user"
    assert (config_file.parent / "config.json.pre-label-format-backup").exists()


def test_status_json_after_provider_first_migration_uses_new_label(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-secondary",
        identity_email="personal@example.test",
    )
    Config(accounts=[account]).save()
    _save_status_cache(
        [account],
        {
            "identity|codex|acct-secondary": AccountStatus(
                label="personal",
                state=AccountState.ACTIVE,
                source_detail="codex-session-jsonl",
            )
        },
    )
    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", lambda: ([], []))

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["label"] == "codex (personal)"
    assert rows[0]["source_detail"] == "codex-session-jsonl"


def test_status_cache_uses_saved_config_label_for_display(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    cached_account = AccountConfig(
        label="work (claude)",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )
    saved_account = replace(cached_account, label="claude")
    _save_status_cache(
        [cached_account],
        {
            "identity|claude|claude-org:claude-account": AccountStatus(
                label="work (claude)",
                state=AccountState.ACTIVE,
                source_detail="claude-codexbar-fallback",
            )
        },
    )

    cached = _load_status_cache(Config(accounts=[saved_account]))

    assert cached is not None
    accounts, statuses, entries = cached
    assert accounts[0].label == "claude"
    assert statuses[0].label == "claude"
    assert entries["identity|claude|claude-org:claude-account"]["status"].label == "claude"


def test_status_cache_misaligned_config_keys_force_live_refresh(tmp_path, monkeypatch):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label=f"account-{index}", provider="codex", source=DataSource.CODEX_DIRECT)
        for index in range(6)
    ]
    accounts.append(
        AccountConfig(
            label="codex (work)",
            provider="codex",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="codex",
            codexbar_account="work@example.test",
        )
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    **{
                        f"codex-direct|codex|account-{index}": {
                            "account": accounts[index].to_dict(),
                            "status": {
                                "label": accounts[index].label,
                                "state": "active",
                                "source_detail": "codex-session-jsonl",
                            },
                            "cached_at": "2026-05-23T04:18:33Z",
                            "refresh_error": None,
                        }
                        for index in range(6)
                    },
                    "identity|codex|acct-work": {
                        "account": AccountConfig(
                            label="codex (work)",
                            provider="codex",
                            source=DataSource.CODEX_DIRECT,
                            identity_provider_id="acct-work",
                            identity_email="work@example.test",
                        ).to_dict(),
                        "status": {
                            "label": "codex (work)",
                            "state": "fresh",
                            "source_detail": CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                            "window_anchor_state": "available_unanchored",
                        },
                        "cached_at": "2026-05-23T04:18:33Z",
                        "refresh_error": None,
                    },
                },
            }
        )
    )

    cached = _load_status_cache(Config(accounts=accounts))

    assert cached is None


def test_status_cache_normalizes_legacy_codex_provider_source_detail(tmp_path, monkeypatch):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-codex",
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    account_key_string(account): {
                        "account": account.to_dict(),
                        "status": {
                            "label": "codex",
                            "state": "active",
                            "source_detail": "codex-appserver-ratelimits",
                        },
                        "cached_at": "2026-05-23T04:18:33Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )

    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, statuses, entries = cached
    assert statuses[0].source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert entries[account_key_string(account)]["status"].source_detail == (
        CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    )


def test_status_outputs_use_live_source_when_cache_key_is_misaligned(tmp_path, monkeypatch):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label=f"account-{index}", provider="codex", source=DataSource.CODEX_DIRECT)
        for index in range(6)
    ]
    sky_account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    accounts.append(sky_account)
    Config(
        accounts=accounts,
        migrations={
            DIRECT_SOURCE_MIGRATION_KEY: True,
            LABEL_FORMAT_MIGRATION_KEY: True,
        },
    ).save()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    "identity|codex|acct-work": {
                        "account": AccountConfig(
                            label="codex (work)",
                            provider="codex",
                            source=DataSource.CODEX_DIRECT,
                            identity_provider_id="acct-work",
                            identity_email="work@example.test",
                        ).to_dict(),
                        "status": {
                            "label": "codex (work)",
                            "state": "fresh",
                            "source_detail": CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                            "window_anchor_state": "available_unanchored",
                        },
                        "cached_at": "2026-05-23T04:18:33Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )
    live_statuses = [
        AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            source_detail="codex-session-jsonl",
        )
        for account in accounts[:6]
    ] + [
        AccountStatus(
            label="codex (work)",
            state=AccountState.FRESH,
            source_detail="codexbar-history",
        )
    ]
    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", lambda: ([], []))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (accounts, live_statuses, "Found 7 accounts via test discovery: codex."),
    )

    captured: dict[str, str | None] = {}

    def capture_table(statuses, _accounts, *_args, **_kwargs):
        captured.update({status.label: status.source_detail for status in statuses})

    monkeypatch.setattr("tokenkick.cli._render_status_table", capture_table)

    table_result = CliRunner().invoke(cli, ["status"])
    json_result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert table_result.exit_code == 0
    assert json_result.exit_code == 0
    rows = _status_json_accounts(json_result.output)
    by_label = {row["label"]: row for row in rows}
    assert len(rows) == 7
    assert captured["codex (work)"] == "codexbar-history"
    assert by_label["codex (work)"]["source_detail"] == "codexbar-history"


def test_status_cache_miss_rechecks_and_migrates_working_appserver_direct(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(
        accounts=[legacy_account],
        migrations={
            DIRECT_SOURCE_MIGRATION_KEY: True,
            LABEL_FORMAT_MIGRATION_KEY: True,
        },
    ).save()
    direct_account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(tmp_path / "missing-sessions"),
        provider_home=str(tmp_path / "managed-home"),
        codexbar_account="work@example.test",
    )
    direct_status = AccountStatus(
        label="codex (work)",
        state=AccountState.FRESH,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="available_unanchored",
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    "identity|codex|acct-work": {
                        "account": direct_account.to_dict(),
                        "status": {
                            "label": "codex (work)",
                            "state": "fresh",
                            "source_detail": CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                            "window_anchor_state": "available_unanchored",
                        },
                        "cached_at": "2026-05-23T04:18:33Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: ([direct_account], [direct_status]),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: pytest.fail("status should reuse cache after v0.4.x recheck migration"),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["source_detail"] == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    saved = Config.load()
    assert saved.accounts[0].source == DataSource.CODEX_DIRECT
    assert saved.accounts[0].provider_home == str(tmp_path / "managed-home")


def test_v04_direct_source_migration_leaves_unmatched_accounts(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    Config(accounts=[legacy_account]).save()
    direct_account = AccountConfig(
        label="other",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-other",
        identity_email="other@example.test",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: ([direct_account], [AccountStatus(label="other", state=AccountState.ACTIVE)]),
    )

    migrated = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)

    assert migrated.accounts[0].source == DataSource.CODEXBAR_CLI
    assert migrated.accounts[0].identity_provider_id is None
    assert migrated.migrations[DIRECT_SOURCE_MIGRATION_KEY] is True


def test_v04_direct_source_migration_skips_unreadable_codex_direct(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(accounts=[legacy_account]).save()
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(tmp_path / "missing-sessions"),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [direct_account],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.UNKNOWN,
                    source_detail="codex-session-jsonl",
                )
            ],
        ),
    )

    migrated = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)

    assert migrated.accounts[0].source == DataSource.CODEXBAR_CLI
    assert migrated.accounts[0].identity_provider_id is None
    assert migrated.migrations[DIRECT_SOURCE_MIGRATION_KEY] is True


def test_unreadable_codex_direct_duplicate_is_not_surfaced_as_new_account(monkeypatch):
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path="/tmp/missing-sessions",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [legacy_account, direct_account],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.FRESH,
                    source_detail="codexbar-cli",
                ),
                AccountStatus(
                    label="work",
                    state=AccountState.UNKNOWN,
                    source_detail="codex-session-jsonl",
                ),
            ],
            "Found 2 accounts via auto-discovery: codex.",
        ),
    )

    accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[legacy_account])
    )

    assert new_accounts == []
    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEXBAR_CLI
    assert statuses[0].source_detail == "codexbar-cli"


def test_v04_direct_source_migration_rechecks_skipped_codex_on_refresh(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    sessions_dir = tmp_path / "codex" / "sessions"
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(accounts=[legacy_account]).save()
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(sessions_dir),
    )
    status = AccountStatus(
        label="work",
        state=AccountState.UNKNOWN,
        source_detail="codex-session-jsonl",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: ([direct_account], [status]),
    )

    first_pass = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)
    sessions_dir.mkdir(parents=True)
    second_pass = _migrate_v04_direct_sources_if_needed(
        first_pass,
        emit_notice=False,
        recheck_skipped=True,
    )

    assert first_pass.accounts[0].source == DataSource.CODEXBAR_CLI
    assert second_pass.accounts[0].source == DataSource.CODEX_DIRECT
    assert second_pass.accounts[0].identity_provider_id == "acct-work"


def test_v04_direct_source_migration_rechecks_skipped_codex_with_working_appserver(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(
        accounts=[legacy_account],
        migrations={DIRECT_SOURCE_MIGRATION_KEY: True},
    ).save()
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(tmp_path / "missing-sessions"),
        provider_home=str(tmp_path / "managed-home"),
        codexbar_account="work@example.test",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [direct_account],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.FRESH,
                    source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                    window_anchor_state="available_unanchored",
                )
            ],
        ),
    )

    migrated = _migrate_v04_direct_sources_if_needed(
        Config.load(),
        emit_notice=False,
        recheck_skipped=True,
    )

    assert migrated.accounts[0].source == DataSource.CODEX_DIRECT
    assert migrated.accounts[0].provider_home == str(tmp_path / "managed-home")


def test_v04_direct_source_migration_does_not_count_codexbar_fallback_as_direct(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(
        accounts=[legacy_account],
        migrations={DIRECT_SOURCE_MIGRATION_KEY: True},
    ).save()
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(tmp_path / "missing-sessions"),
        provider_home=str(tmp_path / "managed-home"),
        codexbar_account="work@example.test",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [direct_account],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.FRESH,
                    source_detail="codexbar-history",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda _account: AccountStatus(
            label="work",
            state=AccountState.FRESH,
            source_detail="codexbar-history",
        ),
    )

    migrated = _migrate_v04_direct_sources_if_needed(
        Config.load(),
        emit_notice=False,
        recheck_skipped=True,
    )

    assert migrated.accounts[0].source == DataSource.CODEXBAR_CLI
    assert migrated.accounts[0].provider_home is None


def test_v04x_provider_usage_recheck_upgrades_skipped_codex_with_distinct_notice(
    tmp_path,
    monkeypatch,
    capsys,
):
    _config_file, _backup_file, cache_file = _isolate_config_files(monkeypatch, tmp_path)
    appserver_backup_file = tmp_path / ".tokenkick" / "config.json.pre-v0.4x-appserver-backup"
    legacy_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    Config(
        accounts=[legacy_account],
        migrations={DIRECT_SOURCE_MIGRATION_KEY: True},
    ).save()
    old_key = "account|codex|work@example.test"
    cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    old_key: {
                        "account": legacy_account.to_dict(),
                        "status": {
                            "label": "work",
                            "state": "fresh",
                            "source_detail": "codexbar-history",
                        },
                        "cached_at": "2026-05-23T04:18:33Z",
                        "refresh_error": None,
                    }
                },
            }
        )
    )
    direct_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
        identity_email="work@example.test",
        session_path=str(tmp_path / "missing-sessions"),
        provider_home=str(tmp_path / "managed-home"),
        codexbar_account="work@example.test",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [direct_account],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.FRESH,
                    source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                    window_anchor_state="available_unanchored",
                )
            ],
        ),
    )

    migrated = _migrate_v04_direct_sources_if_needed(
        Config.load(),
        emit_notice=True,
        recheck_skipped=True,
    )
    notice = capsys.readouterr().err
    cache_data = json.loads(cache_file.read_text())

    assert migrated.accounts[0].source == DataSource.CODEX_DIRECT
    assert migrated.accounts[0].provider_home == str(tmp_path / "managed-home")
    assert appserver_backup_file.exists()
    assert "Upgrading direct Codex provider usage reads for work (codex)" in notice
    assert "Previous CodexBar fallback is no longer needed" in notice
    assert old_key not in cache_data["accounts"]


def test_status_json_preserves_migrated_claude_saved_label(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="claude",
            )
        ]
    ).save()
    claude_direct = AccountConfig(
        label="work",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
        identity_email="work@example.test",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [claude_direct],
            [
                AccountStatus(
                    label="work",
                    state=AccountState.ACTIVE,
                    source_detail="claude-codexbar-fallback",
                )
            ],
        ),
    )
    monkeypatch.setattr("tokenkick.cli.shutil.which", lambda _name: None)
    monkeypatch.setattr("tokenkick.cli._discover_codex_session_accounts", lambda: ([], []))

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["label"] == "claude"
    assert rows[0]["source_detail"] == "claude-codexbar-fallback"


def test_status_json_auto_migration_uses_direct_source_details(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="dev",
                provider="codex",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="codex",
                codexbar_account="dev@example.test",
            ),
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="claude",
            ),
        ]
    ).save()
    codex_direct = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-dev",
        identity_email="dev@example.test",
    )
    claude_direct = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )

    def discover_direct():
        return (
            [codex_direct, claude_direct],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    source_detail="codex-session-jsonl",
                ),
                AccountStatus(
                    label="claude",
                    state=AccountState.ACTIVE,
                    source_detail="claude-cli-usage",
                ),
            ],
        )

    monkeypatch.setattr("tokenkick.cli._discover_direct_accounts", discover_direct)
    monkeypatch.setattr("tokenkick.cli.shutil.which", lambda _name: None)

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    assert "TokenKick migrated direct provider sources" in result.output
    rows = _status_json_accounts(result.output)
    by_label = {row["label"]: row for row in rows}
    assert by_label["codex (dev)"]["source_detail"] == "codex-session-jsonl"
    assert by_label["claude"]["source_detail"] == "claude-cli-usage"
    saved = Config.load()
    assert [account.source for account in saved.accounts] == [
        DataSource.CODEX_DIRECT,
        DataSource.CLAUDE_DIRECT,
    ]


def test_claude_direct_migration_requires_cli_usage(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="claude",
            )
        ]
    ).save()
    claude_direct = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_direct_accounts",
        lambda: (
            [claude_direct],
            [
                AccountStatus(
                    label="claude",
                    state=AccountState.ACTIVE,
                    source_detail="claude-codexbar-fallback",
                )
            ],
        ),
    )

    migrated = _migrate_v04_direct_sources_if_needed(Config.load(), emit_notice=False)

    assert migrated.accounts[0].source == DataSource.CODEXBAR_CLI
    assert Config.load().accounts[0].source == DataSource.CODEXBAR_CLI


def test_status_json_claude_direct_without_codexbar_shows_unknown_explanation(
    tmp_path,
    monkeypatch,
):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": "claude-account",
                    "organizationUuid": "claude-org",
                    "emailAddress": "claude@example.test",
                }
            }
        )
    )
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CODEXBAR_CLI,
                codexbar_provider="claude",
            )
        ]
    ).save()
    monkeypatch.setattr("tokenkick.cli.Path.home", lambda: home)
    monkeypatch.setattr("tokenkick.direct.Path.home", lambda: home)
    monkeypatch.setattr("tokenkick.cli.shutil.which", lambda _name: None)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert len(rows) == 1
    assert rows[0]["label"] == "claude"
    assert rows[0]["state"] == "unknown"
    assert rows[0]["source_detail"] == "claude-config-json"
    assert "CodexBar fallback is unavailable" in rows[0]["error"]
    assert "enable the explicit Claude probe" in rows[0]["error"]
    assert "tk status --refresh" in rows[0]["error"]


def test_status_json_routine_does_not_run_claude_usage_without_cache(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CLAUDE_DIRECT,
                identity_provider_id="claude-account",
                identity_org_id="claude-org",
            )
        ]
    ).save()
    monkeypatch.setattr("tokenkick.cli._discover_accounts_and_statuses", lambda: ([], [], "No accounts found."))
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: pytest.fail("routine status must not run Claude /usage"),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="CodexBar unavailable",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["source_detail"] == "claude-config-json"


def test_status_refresh_can_run_claude_usage(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="claude",
                provider="claude",
                source=DataSource.CLAUDE_DIRECT,
                identity_provider_id="claude-account",
                identity_org_id="claude-org",
            )
        ]
    ).save()
    calls = []
    monkeypatch.setattr("tokenkick.cli._discover_accounts_and_statuses", lambda: ([], [], "No accounts found."))
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        "tokenkick.sources.read_claude_identity",
        lambda: DirectIdentity("claude", provider_account_id="claude-account", organization_id="claude-org"),
    )

    def fake_capture(_binary):
        calls.append(_binary)
        return """
        Current session
        7% used
        Resets in 1h
        Current week
        11% used
        Resets in 5h
        """

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage", fake_capture)

    result = CliRunner().invoke(cli, ["status", "--refresh", "--json-output"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["source_detail"] == "claude-cli-usage"
    assert rows[0]["used_percent"] == 11.0
    assert calls == ["/usr/bin/claude"]


def test_background_status_refresh_does_not_run_claude_usage(tmp_path, monkeypatch):
    _config_file, _backup_file, _cache_file = _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )
    config = Config(accounts=[account])
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda *_args, **_kwargs: ([], [], "No accounts found."),
    )
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: pytest.fail("background refresh must not run Claude /usage"),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="CodexBar unavailable",
        ),
    )

    accounts, statuses = _refresh_status_cache(config, daemon_log=True)

    assert accounts == [account]
    assert statuses[0].source_detail == "claude-config-json"
    assert statuses[0].state == AccountState.UNKNOWN


def test_discovered_claude_direct_status_keeps_probe_context(tmp_path, monkeypatch):
    cache_file = tmp_path / "status-cache.json"
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.discovery.Path.home", lambda: tmp_path)
    monkeypatch.setattr("tokenkick.discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("tokenkick.cli.read_codex_identity", lambda _home: None)
    monkeypatch.setattr(
        "tokenkick.cli.read_claude_identity",
        lambda: DirectIdentity(
            "claude",
            provider_account_id="claude-account",
            organization_id="claude-org",
        ),
    )
    captured = {}

    def fake_fetch_status(account, **kwargs):
        captured["context"] = kwargs.get("claude_probe_context")
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Claude CLI /usage timed out.",
            source_detail="claude-cli-usage",
        )

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch_status)

    accounts, statuses = _discover_direct_accounts(Config())

    assert [account.provider for account in accounts] == ["claude"]
    assert isinstance(captured["context"], ClaudeProbeContext)
    assert getattr(statuses[0], "_claude_probe_context") is captured["context"]


def test_configured_claude_discovery_relabel_preserves_probe_context(tmp_path, monkeypatch):
    cache_file = tmp_path / "status-cache.json"
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.discovery.Path.home", lambda: tmp_path)
    monkeypatch.setattr("tokenkick.discovery.shutil.which", lambda _name: None)
    monkeypatch.setattr("tokenkick.cli.read_codex_identity", lambda _home: None)
    monkeypatch.setattr(
        "tokenkick.cli.read_claude_identity",
        lambda: DirectIdentity(
            "claude",
            provider_account_id="claude-account",
            organization_id="claude-org",
        ),
    )

    def fake_fetch_status(account, **_kwargs):
        return AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Claude CLI /usage timed out.",
            source_detail="claude-cli-usage",
        )

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch_status)
    saved = AccountConfig(
        label="claude (work)",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )

    accounts, statuses, _discovered, _summary, _new_accounts = _load_account_status_pairs(
        Config(accounts=[saved])
    )

    assert [account.label for account in accounts] == ["claude (work)"]
    assert statuses[0].label == "claude (work)"
    assert isinstance(getattr(statuses[0], "_claude_probe_context"), ClaudeProbeContext)


def test_merge_discovered_accounts_dedupes_codex_email_and_keeps_more_detail():
    codexbar_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    session_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/dev/sessions",
        codexbar_account="dev@example.test",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (codexbar_account, AccountStatus(label="dev", state=AccountState.ACTIVE)),
            (
                session_account,
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=20.0,
                    window_minutes=10080,
                ),
            ),
        ]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEX_SESSION_FILE
    assert statuses[0].used_percent == 20.0


def test_merge_discovered_accounts_prefers_codex_session_for_same_email():
    codexbar_account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="personal@example.test",
    )
    session_account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/personal/sessions",
        codexbar_account="personal@example.test",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (
                codexbar_account,
                AccountStatus(
                    label="personal",
                    state=AccountState.FRESH,
                    used_percent=0.0,
                    window_minutes=10080,
                    resets_in_seconds=604800,
                ),
            ),
            (
                session_account,
                AccountStatus(
                    label="personal",
                    state=AccountState.ACTIVE,
                    used_percent=17.0,
                    window_minutes=10080,
                    resets_in_seconds=500000,
                ),
            ),
        ]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEX_SESSION_FILE
    assert statuses[0].state == AccountState.ACTIVE
    assert statuses[0].used_percent == 17.0


def test_merge_discovered_accounts_prefers_codexbar_over_empty_session_for_same_email():
    codexbar_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    session_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/work/sessions",
        codexbar_account="work@example.test",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (
                codexbar_account,
                AccountStatus(
                    label="work",
                    state=AccountState.FRESH,
                    used_percent=0.0,
                    window_minutes=10080,
                    resets_in_seconds=604800,
                ),
            ),
            (
                session_account,
                AccountStatus(
                    label="work",
                    state=AccountState.UNKNOWN,
                    error="No session data — use this account to start tracking.",
                ),
            ),
        ]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEXBAR_CLI
    assert statuses[0].state == AccountState.FRESH
    assert statuses[0].used_percent == 0.0


def test_merge_discovered_accounts_prefers_codex_direct_identity_over_codexbar():
    codexbar_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    direct_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct_dev",
        identity_email="dev@example.test",
        codexbar_account="dev@example.test",
        session_path="/tmp/dev/sessions",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (
                codexbar_account,
                AccountStatus(label="dev", state=AccountState.FRESH, used_percent=0.0),
            ),
            (
                direct_account,
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=18.0,
                    source_detail="codex-session-jsonl",
                ),
            ),
        ]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CODEX_DIRECT
    assert accounts[0].identity_provider_id == "acct_dev"
    assert statuses[0].used_percent == 18.0


def test_merge_discovered_accounts_prefers_claude_direct_over_codexbar_singleton():
    codexbar_account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="claude",
    )
    direct_account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (
                codexbar_account,
                AccountStatus(label="claude", state=AccountState.FRESH, used_percent=0.0),
            ),
            (
                direct_account,
                AccountStatus(label="claude", state=AccountState.UNKNOWN),
            ),
        ]
    )

    assert len(accounts) == 1
    assert accounts[0].source == DataSource.CLAUDE_DIRECT
    assert statuses[0].state == AccountState.UNKNOWN


def test_merge_discovered_accounts_disambiguates_duplicate_labels_by_provider():
    codex_account = AccountConfig(
        label="shared",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        codexbar_account="shared@example.test",
    )
    gemini_account = AccountConfig(
        label="shared",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="shared@example.test",
    )

    accounts, statuses = _merge_discovered_accounts(
        [
            (codex_account, AccountStatus(label="shared", state=AccountState.ACTIVE)),
            (gemini_account, AccountStatus(label="shared", state=AccountState.FRESH)),
        ]
    )

    assert [account.label for account in accounts] == ["codex (shared)", "gemini (shared)"]
    assert [status.label for status in statuses] == ["codex (shared)", "gemini (shared)"]


def test_status_action_shows_kick_now_for_kickable_fresh_accounts():
    assert (
        _status_action(AccountStatus(label="shared (codex)", state=AccountState.FRESH))
        == "Kick now"
    )
    assert (
        _status_action(
            AccountStatus(label="claude", state=AccountState.FRESH),
            {"claude": "claude"},
        )
        == "Kick now"
    )
    assert (
        _status_action(AccountStatus(label="shared (gemini)", state=AccountState.FRESH))
        == "Monitor only"
    )
    assert (
        _status_action(
            AccountStatus(label="gemini", state=AccountState.FRESH),
            {"gemini": "gemini"},
        )
        == "Monitor only"
    )
    assert (
        _status_action(
            AccountStatus(label="antigravity", state=AccountState.ACTIVE),
            {"antigravity": "antigravity"},
        )
        == "Monitor only"
    )
    assert (
        _status_action(
            AccountStatus(label="gemini", state=AccountState.ACTIVE),
            {"gemini": "gemini"},
        )
        == "Monitor only"
    )


def test_status_action_shortens_no_session_data_unknown_message():
    status = AccountStatus(
        label="shared (codex)",
        state=AccountState.UNKNOWN,
        error="No session data — use this account to start tracking.",
    )

    assert _status_action(status) == "No session data"


def test_status_window_display_helpers_use_relative_time_and_usage_colors():
    assert _format_weekly_reset(5 * 86400 + 23 * 3600 + 59 * 60) == "[dim]weekly[/dim]  in 5d 23h"
    assert _format_weekly_reset(59 * 60) == "[dim]weekly[/dim]  in 59m"
    assert _format_session_reset(4 * 3600 + 12 * 60) == "[cyan]session in 4h 12m[/cyan]"
    assert _format_session_reset(22 * 60) == "[cyan]session in 0h 22m[/cyan]"
    assert _format_session_reset(None) == "[cyan]session —[/cyan]"

    assert _format_used_percent(0) == "[green]  0%[/green]"
    assert _format_used_percent(31) == "[yellow] 31%[/yellow]"
    assert _format_used_percent(65) == "[dark_orange] 65%[/dark_orange]"
    assert _format_used_percent(91) == "[red] 91%[/red]"
    assert _format_used_percent(None) == "—"


def test_status_used_cell_shows_openrouter_balance_with_spent_color():
    status = AccountStatus(
        label="openrouter",
        state=AccountState.ACTIVE,
        used_percent=85.4,
        balance_remaining=1.45589982,
        balance_limit=10.0,
        balance_spent_percent=85.4,
        session_used_percent=12.0,
    )

    assert _format_used_cell(status, "openrouter") == "[red]$1.46/$10.00 left[/red]"
    assert _format_used_cell(status, "openrouter", session=True) == "—"


def test_status_used_labeled_cell_distinguishes_weekly_and_session():
    status = AccountStatus(
        label="dev",
        state=AccountState.ACTIVE,
        used_percent=37.0,
        session_used_percent=52.0,
    )

    assert _format_used_labeled_cell(status, "codex") == "[dim]w[/dim] [yellow] 37%[/yellow]"
    assert (
        _format_used_labeled_cell(status, "codex", session=True)
        == "[dim]s[/dim] [dark_orange] 52%[/dark_orange]"
    )


def test_status_summary_lists_only_fresh_kickable_accounts(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [],
            [
                AccountStatus(label="shared (codex)", state=AccountState.FRESH),
                AccountStatus(label="claude", state=AccountState.FRESH),
                AccountStatus(label="shared (gemini)", state=AccountState.FRESH),
            ],
            "Found 3 accounts via auto-discovery: claude, codex, gemini.",
        ),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "claude" in result.output
    assert "shared (codex)" in result.output
    assert "shared (gemini)" in result.output
    assert "Weekly ready windows: claude, shared (codex)" in result.output
    assert "shared (gemini)" in result.output


def test_status_summary_excludes_session_cooldown_accounts(monkeypatch):
    observed = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: observed.timestamp())
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [
                AccountConfig(label="deferred", provider="codex"),
                AccountConfig(label="ready", provider="codex"),
            ],
            [
                AccountStatus(
                    label="deferred",
                    state=AccountState.FRESH,
                    session_used_percent=1.0,
                    session_resets_in_seconds=8040,
                ),
                AccountStatus(label="ready", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Session cooling down" not in result.output
    assert "session in 2h 14m" in result.output
    assert "Weekly ready windows: deferred, ready" in result.output


def test_status_summary_lists_session_ready_as_kick_ready(monkeypatch):
    account = AccountConfig(label="active", provider="codex", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(
        label="active",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: ([account], [status], "Found 1 account via auto-discovery: codex."),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Kick-ready windows: active" in result.output
    assert "tk kick --all" in result.output
    assert "kick them now" in result.output
    assert "anchor them now" not in result.output


def test_status_summary_suggests_session_auto_enable_when_manual_session_ready(monkeypatch):
    account = AccountConfig(label="active", provider="codex")
    status = AccountStatus(
        label="active",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: ([account], [status], "Found 1 account via auto-discovery: codex."),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Kick-ready windows: active" in result.output
    assert 'tk auto enable "active"' in result.output


def test_status_table_keeps_used_column_readable_with_session_actions(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=100, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    accounts = [
        AccountConfig(label="codex (work)", provider="codex"),
        AccountConfig(label="codex (primaryaccount)", provider="codex"),
    ]
    statuses = [
        AccountStatus(
            label="codex (work)",
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=6 * 24 * 60 * 60,
            session_used_percent=1.0,
            session_resets_in_seconds=3 * 60 * 60 + 39 * 60,
            session_window_minutes=300,
        ),
        AccountStatus(
            label="codex (primaryaccount)",
            state=AccountState.ACTIVE,
            used_percent=1.0,
            resets_in_seconds=15 * 60 * 60,
            session_used_percent=10.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        ),
    ]

    _render_status_table(statuses, accounts)

    rendered = output.getvalue()
    assert "w   0%" in rendered
    assert "s   1%" in rendered
    assert "w   1%" in rendered
    assert "s  10%" in rendered
    assert "w …" not in rendered
    assert "s …" not in rendered


def test_status_table_marks_codexbar_fallback_and_blocks_auto_prompt(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    account = AccountConfig(
        label="codex (reserve)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
    )
    status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        source_detail="codexbar-history",
    )

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Weekly ready*" in rendered
    assert "CodexBar fallback" in rendered
    assert "indirect Codex data via CodexBar fallback" in rendered
    assert "automatic kicks are blocked" in rendered
    assert "Weekly ready windows" not in rendered


def test_status_state_display_marks_stale_fresh_without_wide_emoji():
    stale = _status_state_display(AccountStatus(label="stale", state=AccountState.FRESH), stale=True)
    fresh = _status_state_display(AccountStatus(label="fresh", state=AccountState.FRESH))
    available = _status_state_display(
        AccountStatus(
            label="available",
            state=AccountState.FRESH,
            window_anchor_state="available_unanchored",
        )
    )
    anchored = _status_state_display(
        AccountStatus(label="anchored", state=AccountState.FRESH, window_anchor_state="anchored")
    )
    session_active = _status_state_display(
        AccountStatus(
            label="session-active",
            state=AccountState.FRESH,
            used_percent=0.0,
            session_used_percent=1.0,
            window_anchor_state="anchored",
        )
    )
    phantom_session = _status_state_display(
        AccountStatus(
            label="phantom-session",
            state=AccountState.FRESH,
            used_percent=0.0,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            window_anchor_state="anchored",
        ),
        phantom_session=True,
    )
    active_phantom_session = _status_state_display(
        AccountStatus(
            label="active-phantom-session",
            state=AccountState.ACTIVE,
            used_percent=0.0,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            window_anchor_state="anchored",
        ),
        phantom_session=True,
    )
    active_session_ready = _status_state_display(
        AccountStatus(
            label="active-session-ready",
            state=AccountState.ACTIVE,
            used_percent=65.0,
            session_used_percent=0.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
        )
    )
    claude_active_session_ready = _status_state_display(
        AccountStatus(
            label="claude-active-session-ready",
            state=AccountState.ACTIVE,
            used_percent=27.0,
            session_used_percent=0.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
            source_detail="claude-cli-usage",
        ),
        provider="claude",
    )
    antigravity_active_session_ready = _status_state_display(
        AccountStatus(
            label="antigravity-active-session-ready",
            state=AccountState.ACTIVE,
            used_percent=51.0,
            session_used_percent=0.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
        ),
        provider="antigravity",
    )
    session_exhausted = _status_state_display(
        AccountStatus(
            label="session-exhausted",
            state=AccountState.ACTIVE,
            used_percent=65.0,
            session_used_percent=100.0,
            session_resets_in_seconds=2400,
            session_window_minutes=300,
        )
    )

    assert stale == "🟡 Weekly ready (stale)"
    assert fresh == "🟢 Weekly ready"
    assert available == "🟢 Weekly ready"
    assert anchored == "🟢 Weekly ready"
    assert session_active == "🟢 Weekly ready"
    assert phantom_session == "🟡 Phantom session"
    assert active_phantom_session == "🟡 Phantom session"
    assert active_session_ready == "🟢 Session ready"
    assert claude_active_session_ready == "🔵 Active"
    assert antigravity_active_session_ready == "🔵 Active"
    assert session_exhausted == "🟠 Session exhausted"


def test_status_table_antigravity_refresh_failure_is_monitor_only_not_blocking(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=51.0,
        resets_in_seconds=25 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=4 * 60 * 60 + 59 * 60,
        session_window_minutes=300,
        source_detail="antigravity-cli",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "account": account,
                "status": status,
                "refresh_error": "ProviderError",
            }
        },
    )

    rendered = output.getvalue()
    assert "antigravity" in rendered
    assert "🔵 Active" in rendered
    assert "Session ready" not in rendered
    assert "Monitor stale" in rendered
    assert "Refresh failed" not in rendered
    assert "automatic kicks are blocked" not in rendered


def _antigravity_quota_windows() -> list[dict]:
    return [
        {
            "id": "antigravity-quota-summary-gemini-5h",
            "title": "Gemini Models Five Hour Limit",
            "family": "gemini",
            "window_kind": "session",
            "used_percent": 66.0,
            "resets_at": 1_780_007_920.0,
            "resets_in_seconds": 2 * 3600 + 12 * 60,
            "window_minutes": 300,
            "source": "antigravity-cli",
        },
        {
            "id": "antigravity-quota-summary-gemini-weekly",
            "title": "Gemini Models Weekly Limit",
            "family": "gemini",
            "window_kind": "weekly",
            "used_percent": 51.0,
            "resets_at": 1_780_104_400.0,
            "resets_in_seconds": 24 * 3600 + 5 * 3600,
            "window_minutes": 10080,
            "source": "antigravity-cli",
        },
        {
            "id": "antigravity-quota-summary-3p-5h",
            "title": "Claude and GPT models Five Hour Limit",
            "family": "claude_gpt",
            "window_kind": "session",
            "used_percent": 0.0,
            "resets_at": 1_780_017_940.0,
            "resets_in_seconds": 4 * 3600 + 58 * 60,
            "window_minutes": 300,
            "source": "antigravity-cli",
        },
        {
            "id": "antigravity-quota-summary-3p-weekly",
            "title": "Claude and GPT models Weekly Limit",
            "family": "claude_gpt",
            "window_kind": "weekly",
            "used_percent": 4.0,
            "resets_at": 1_780_104_400.0,
            "resets_in_seconds": 24 * 3600 + 5 * 3600,
            "window_minutes": 10080,
            "source": "antigravity-cli",
        },
    ]


def _antigravity_status_with_windows(
    *,
    label: str = "antigravity",
    windows: list[dict] | None = None,
    state: AccountState = AccountState.ACTIVE,
    error: str | None = None,
) -> AccountStatus:
    return AccountStatus(
        label=label,
        state=state,
        quota_windows=windows if windows is not None else _antigravity_quota_windows(),
        source_detail="antigravity-cli",
        error=error,
    )


def _antigravity_probe_after_windows(
    *,
    family: str = "gemini",
    session_used_percent: float = 67.0,
    session_resets_at: float | None = 1_780_010_000.0,
    weekly_used_percent: float | None = None,
) -> list[dict]:
    windows = copy.deepcopy(_antigravity_quota_windows())
    for window in windows:
        if window["family"] != family:
            continue
        if window["window_kind"] == "session":
            window["used_percent"] = session_used_percent
            if session_resets_at is not None:
                window["resets_at"] = session_resets_at
        elif window["window_kind"] == "weekly" and weekly_used_percent is not None:
            window["used_percent"] = weekly_used_percent
    return windows


def _antigravity_probe_account() -> AccountConfig:
    return AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
        identity_email="dev@example.test",
    )


def test_antigravity_probe_kick_requires_confirmation(monkeypatch):
    account = _antigravity_probe_account()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: "dev@example.test")
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: pytest.fail("probe request must not run without confirmation"),
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "active Antigravity diagnostic" in result.output
    assert "cancelled" in result.output


def test_antigravity_probe_kick_proved_and_stores_sanitized_evidence(tmp_path, monkeypatch):
    account = _antigravity_probe_account()
    statuses = [
        _antigravity_status_with_windows(label=account.label),
        _antigravity_status_with_windows(
            label=account.label,
            windows=_antigravity_probe_after_windows(),
        ),
    ]
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: "dev@example.test")
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda _account: statuses.pop(0))
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: {
            "success": True,
            "error": None,
            "duration_seconds": 0.25,
            "returncode": 0,
            "stdout_bytes": 3,
            "stderr_bytes": 0,
            "family": "gemini",
        },
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini", "--yes"],
    )

    assert result.exit_code == 0
    assert "dev@example.test" in result.output
    assert "gemini" in result.output
    assert "Verdict:" in result.output
    assert "proved" in result.output

    evidence_file = tmp_path / "antigravity-probe-evidence.jsonl"
    evidence_text = evidence_file.read_text()
    assert "Reply with exactly: OK" not in evidence_text
    assert "\nOK\n" not in evidence_text
    records = [json.loads(line) for line in evidence_text.splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["verdict"] == "proved"
    assert record["account"]["identity_email"] == "dev@example.test"
    assert record["family"] == "gemini"
    assert record["before"]["session"]["used_percent"] == 66.0
    assert record["after"]["session"]["used_percent"] == 67.0
    assert record["bucket_changed"] is True
    assert record["weekly_bucket_changed"] is False
    assert record["request"] == {
        "success": True,
        "returncode": 0,
        "duration_seconds": 0.25,
        "stdout_bytes": 3,
        "stderr_bytes": 0,
        "error": None,
    }


def test_antigravity_probe_kick_claude_gpt_json_output(monkeypatch):
    account = _antigravity_probe_account()
    statuses = [
        _antigravity_status_with_windows(label=account.label),
        _antigravity_status_with_windows(
            label=account.label,
            windows=_antigravity_probe_after_windows(
                family="claude_gpt",
                session_used_percent=1.0,
                session_resets_at=1_780_019_000.0,
            ),
        ),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: "dev@example.test")
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda _account: statuses.pop(0))
    monkeypatch.setattr("tokenkick.cli._append_antigravity_probe_evidence", lambda _report: None)
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: {
            "success": True,
            "error": None,
            "duration_seconds": 0.25,
            "returncode": 0,
            "stdout_bytes": 3,
            "stderr_bytes": 0,
            "family": "claude-gpt",
        },
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "claude-gpt", "--json-output", "--yes"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["family"] == "claude-gpt"
    assert payload["model"] == "GPT-OSS 120B (Medium)"
    assert payload["before"]["session"]["id"] == "antigravity-quota-summary-3p-5h"
    assert payload["after"]["session"]["used_percent"] == 1.0
    assert payload["bucket_changed"] is True
    assert payload["verdict"] == "proved"


def test_antigravity_probe_kick_json_requires_yes(monkeypatch):
    account = _antigravity_probe_account()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini", "--json-output"],
    )

    assert result.exit_code == 1
    assert "--json-output requires --yes" in result.output


def test_antigravity_probe_kick_fails_without_verified_identity(monkeypatch):
    account = _antigravity_probe_account()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: None)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: pytest.fail("quota read must not run without identity"),
    )
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: pytest.fail("probe request must not run without identity"),
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini", "--yes"],
    )

    assert result.exit_code == 1
    assert "identity could not be verified" in result.output


def test_antigravity_probe_kick_fails_closed_on_incomplete_buckets(monkeypatch):
    account = _antigravity_probe_account()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: "dev@example.test")
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: _antigravity_status_with_windows(windows=_antigravity_quota_windows()[:-1]),
    )
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: pytest.fail("probe request must not run without complete buckets"),
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini", "--yes"],
    )

    assert result.exit_code == 1
    assert "complete Antigravity quota buckets before" in result.output


def test_antigravity_probe_kick_failed_request_saves_failed_evidence(tmp_path, monkeypatch):
    account = _antigravity_probe_account()
    statuses = [
        _antigravity_status_with_windows(label=account.label),
        _antigravity_status_with_windows(label=account.label),
    ]
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli.read_antigravity_cli_identity", lambda: "dev@example.test")
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda _account: statuses.pop(0))
    monkeypatch.setattr(
        "tokenkick.cli._run_antigravity_probe_request",
        lambda **_kwargs: {
            "success": False,
            "error": "Bearer abc.def token=secret csrf_token=abc",
            "duration_seconds": 0.1,
            "returncode": 7,
            "stdout_bytes": 0,
            "stderr_bytes": 42,
            "family": "gemini",
        },
    )

    result = CliRunner().invoke(
        cli,
        ["antigravity", "probe-kick", "--family", "gemini", "--yes"],
    )

    assert result.exit_code == 1
    assert "Verdict:" in result.output
    assert "failed" in result.output

    evidence_text = (tmp_path / "antigravity-probe-evidence.jsonl").read_text()
    assert "abc.def" not in evidence_text
    assert "secret" not in evidence_text
    record = json.loads(evidence_text)
    assert record["verdict"] == "failed"
    assert record["request"]["error"] == "Bearer <redacted> token=<redacted> csrf_token=<redacted>"


def test_status_table_expands_antigravity_quota_families(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=66.0,
        resets_in_seconds=2 * 3600 + 12 * 60,
        window_minutes=300,
        session_used_percent=66.0,
        session_resets_in_seconds=2 * 3600 + 12 * 60,
        session_window_minutes=300,
        quota_windows=_antigravity_quota_windows(),
        source_detail="antigravity-cli",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=160, color_system=None),
    )
    monkeypatch.setattr("tokenkick.status_rendering.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "antigravity" in rendered
    assert "quota buckets" in rendered
    assert "Gemini" in rendered
    assert "Claude/GPT" in rendered
    assert "5h in 2h 12m" in rendered
    assert "5h in 4h 58m" in rendered
    assert "5h  66%" in rendered
    assert "5h   0%" in rendered
    assert "weekly  51% in 1d 5h" in rendered
    assert "weekly   4% in 1d 5h" in rendered
    assert "Monitor only" in rendered
    assert "session in 2h 12m" not in rendered


def test_status_table_keeps_antigravity_partial_windows_on_summary_row(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=66.0,
        resets_in_seconds=2 * 3600 + 12 * 60,
        window_minutes=300,
        session_used_percent=66.0,
        session_resets_in_seconds=2 * 3600 + 12 * 60,
        session_window_minutes=300,
        quota_windows=_antigravity_quota_windows()[:-1],
        source_detail="antigravity-cli",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=160, color_system=None),
    )
    monkeypatch.setattr("tokenkick.status_rendering.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "quota buckets" not in rendered
    assert "Gemini" not in rendered
    assert "Claude/GPT" not in rendered
    assert "session in 2h 12m" in rendered


def test_status_json_includes_antigravity_quota_targets(monkeypatch):
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=66.0,
        resets_in_seconds=2 * 3600 + 12 * 60,
        window_minutes=300,
        session_used_percent=66.0,
        session_resets_in_seconds=2 * 3600 + 12 * 60,
        session_window_minutes=300,
        quota_windows=_antigravity_quota_windows(),
        source_detail="antigravity-cli",
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache_fast",
        lambda _config: ([account], [status], False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: None)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-06-28T12:00:00Z")
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.status_rendering.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])

    assert result.exit_code == 0
    row = _status_json_accounts(result.output)[0]
    assert row["provider"] == "antigravity"
    assert row["monitor_only"] is True
    assert row["kickable"] is False
    assert len(row["quota_windows"]) == 4
    assert [target["family"] for target in row["quota_targets"]] == ["gemini", "claude_gpt"]

    gemini, claude_gpt = row["quota_targets"]
    assert gemini["id"] == "antigravity:gemini"
    assert gemini["title"] == "Gemini"
    assert gemini["session"]["id"] == "antigravity-quota-summary-gemini-5h"
    assert gemini["weekly"]["id"] == "antigravity-quota-summary-gemini-weekly"
    assert gemini["session_used_percent"] == 66.0
    assert gemini["session_resets_in_seconds"] == 2 * 3600 + 12 * 60
    assert gemini["weekly_used_percent"] == 51.0
    assert gemini["weekly_resets_in_seconds"] == 24 * 3600 + 5 * 3600

    assert claude_gpt["id"] == "antigravity:claude_gpt"
    assert claude_gpt["title"] == "Claude/GPT"
    assert claude_gpt["session"]["id"] == "antigravity-quota-summary-3p-5h"
    assert claude_gpt["weekly"]["id"] == "antigravity-quota-summary-3p-weekly"
    assert claude_gpt["session_used_percent"] == 0.0
    assert claude_gpt["weekly_used_percent"] == 4.0


def test_status_table_prioritizes_weekly_exhausted_over_refresh_failure(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=100.0,
        resets_in_seconds=17 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        stale=True,
        stale_seconds=40 * 60 * 60,
        error="Codex provider refresh failed.",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "refresh_error": "ProviderError",
            }
        },
    )

    rendered = output.getvalue()
    assert "Weekly exhausted" in rendered
    assert "session blocked" in rendered
    assert "Refresh failed" not in rendered
    assert "session reset ready" not in rendered
    assert "Active" not in rendered


def test_status_table_does_not_show_stale_reset_ready_as_weekly_exhausted(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=100.0,
        resets_in_seconds=0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        stale=True,
        stale_seconds=63 * 60 * 60,
        error="Codex provider refresh failed.",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=180, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "refresh_error": "ProviderError",
            }
        },
    )

    rendered = output.getvalue()
    assert "Weekly exhausted" not in rendered
    assert "Refresh failed" in rendered
    assert "reset rea" in rendered


def test_status_summary_excludes_refresh_failed_weekly_ready(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex home",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=5 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=100.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=180, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "refresh_error": "ProviderError",
                "provider_observed_at": "2000-01-01T00:00:00Z",
            }
        },
    )

    rendered = output.getvalue()
    assert "Refresh failed" in rendered
    assert "Refresh failed; automatic kicks are blocked" in rendered
    assert "Recovery hint" in rendered
    assert "CODEX_HOME='/tmp/codex home' codex" in rendered
    assert "tk kick 'codex (work)' --force" in rendered
    assert "recovery kick consumes a small amount of Codex usage" in rendered
    assert "Weekly ready windows" not in rendered
    assert "tk kick --all" not in rendered


def test_status_table_prioritizes_session_exhausted_over_refresh_failure(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=49.0,
        resets_in_seconds=34 * 60 * 60,
        window_minutes=10080,
        session_used_percent=100.0,
        session_resets_in_seconds=41 * 60,
        session_window_minutes=300,
        stale=True,
        stale_seconds=40 * 60 * 60,
        error="Codex provider refresh failed.",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "refresh_error": "ProviderError",
            }
        },
    )

    rendered = output.getvalue()
    assert "Session exhausted" in rendered
    assert "Wait for session" in rendered
    assert "session in 0h 41m" in rendered
    assert "Refresh failed" not in rendered
    assert "Use if needed" not in rendered


def test_status_table_labels_claude_passive_refresh_failure_as_cached(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(
        label="claude (work)",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=4 * 60 * 60 + 33 * 60,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table(
        [status],
        [account],
        cache_entries={
            account_key_string(account): {
                "account": account,
                "status": status,
                "refresh_error": "ProviderError",
            }
        },
    )

    rendered = output.getvalue()
    assert "🔵 Active ⏱" in rendered
    assert "Cached Claude status" in rendered
    assert "⚠️" not in rendered
    assert "Refresh failed" not in rendered


def test_status_table_expands_for_long_actions(monkeypatch):
    output = io.StringIO()
    account = AccountConfig(label="codex (primaryaccount)", provider="codex")
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=51.0,
        resets_in_seconds=24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=4 * 60 * 60,
        session_window_minutes=300,
    )
    long_action = "Provider accepted; recheck after 10:23 CEST"

    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=170, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.status_rendering._status_table_action", lambda *_args, **_kwargs: long_action)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "codex (primaryaccount)" in rendered
    assert "session in 4h" in rendered
    assert "w  51%" in rendered
    assert long_action in rendered
    assert "Queued" not in rendered


def test_status_table_shows_queued_column_only_when_needed(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    output = io.StringIO()
    account = AccountConfig(label="scheduled", provider="codex", auto_kick=True)
    status = AccountStatus(
        label=account.label,
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_window_minutes=300,
    )
    now = datetime.now(timezone.utc)
    pending_file = tmp_path / "pending-kicks.json"
    pending_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "account_key": account_key_string(account),
                    "account_label": account.label,
                    "provider": account.provider,
                    "kick_at": _utc_iso(now + timedelta(hours=2)),
                    "created_at": _utc_iso(now - timedelta(hours=1)),
                    "reason": "optimal",
                    "windows_needed": 2,
                    "expected_waste_minutes": 0,
                    "waste_location": "none",
                    "work_start": _utc_iso(now + timedelta(hours=2)),
                    "work_end": _utc_iso(now + timedelta(hours=10)),
                    "window_basis": "session",
                    "notified": True,
                }
            }
        )
    )

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Queued" in rendered
    assert "scheduled" in rendered
    assert "Kick now" in rendered


def test_status_state_display_marks_indirect_fallback():
    assert _status_state_display(
        AccountStatus(label="active", state=AccountState.ACTIVE),
        indirect=True,
    ) == "🔵 Active*"


def test_status_table_labels_kickable_phantom_session(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1200.0)
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="work@example.test",
    )
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 2,
                    "session_resets_in_seconds": 17940,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )
    history = [
        KickEvent(
            label="codex (primaryaccount)",
            timestamp=120.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=100,
        )
    ]
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Fresh (session active)" not in rendered
    assert "Kick now" in rendered


def test_status_table_labels_phantom_session_backoff(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1000.0)
    account = AccountConfig(
        label="codex (reserve)",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="user@example.test",
    )
    status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    history = [
        KickEvent(
            label="codex (reserve)",
            timestamp=900.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        )
    ]
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Fresh (session active)" not in rendered
    assert "Retry after" in rendered


def test_status_table_labels_partial_weekly_usage_phantom_session(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1200.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "session_resets_in_seconds": 17940,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 16.0,
                }
            }
        )
    )

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Active" not in rendered
    assert "Kick session" in rendered


def test_status_table_labels_near_full_phantom_when_session_window_missing(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1200.0)
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.ACTIVE,
        used_percent=26.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=16440,
        session_window_minutes=None,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "session_resets_in_seconds": 16440,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 26.0,
                }
            }
        )
    )

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Use if needed" not in rendered


def test_status_action_shows_provider_accepted_recovery_backoff(monkeypatch, tmp_path):
    account = AccountConfig(label="codex (work)", provider="codex")
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.ACTIVE,
        used_percent=26.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=16440,
        session_window_minutes=None,
    )
    key = account_key_string(account)
    recovery_file = tmp_path / "recovery.json"
    recovery_file.write_text(
        json.dumps(
            {
                key: {
                    "first_started_at": 10_000.0,
                    "last_seen_at": 10_000.0,
                    "last_attempt_at": 10_000.0,
                    "attempts": 1,
                    "status": "provider_accepted",
                    "cooldown_until": 12_700.0,
                }
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    action = _status_action(status, {"codex (work)": "codex"}, account)

    assert action.startswith("Provider accepted; recheck after")


def test_status_table_hides_provider_reset_for_confirmed_sliding_phantom(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_180.0)
    account = AccountConfig(
        label="codex (reserve)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17998,
        session_resets_at=1_780_018_360.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    history = [
        KickEvent(
            label="codex (reserve)",
            timestamp=10_000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 10_000.0,
                    "last_seen_at": 10_120.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "first_session_resets_at": 1_780_018_000.0,
                    "session_resets_in_seconds": 17998,
                    "session_resets_at": 1_780_018_360.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "session phantom" in rendered
    assert "session in 4h 59m" not in rendered
    assert "session in 4h 57m" not in rendered


def test_status_table_marks_confirmed_sliding_phantom_after_stuck_observation(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (reserve)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17998,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    history = [
        KickEvent(
            label="codex (reserve)",
            timestamp=100.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=11008,
            output_tokens=18,
        )
    ]
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 300.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "session_resets_in_seconds": 17998,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Provider unchanged" in rendered
    assert _kick_eligibility(account, status, "codex", history=history).reason == (
        "provider_unchanged"
    )


def test_status_table_hides_midwindow_sliding_phantom_countdown(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=52.0,
        resets_in_seconds=43 * 60,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=3 * 3600 + 29 * 60,
        session_resets_at=1_780_002_900.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 240.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 3 * 3600 + 30 * 60,
                    "first_session_resets_at": 1_780_002_600.0,
                    "session_resets_in_seconds": 3 * 3600 + 29 * 60,
                    "session_resets_at": 1_780_002_900.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 52.0,
                }
            }
        )
    )
    history = [
        KickEvent(
            label="codex (primaryaccount)",
            timestamp=120.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=100,
        )
    ]
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" in rendered
    assert "Active" not in rendered
    assert "session phantom" in rendered
    assert "session in 3h 29m" not in rendered
    assert "Kick session" in rendered


def test_status_table_keeps_midwindow_real_countdown_while_observing(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (real)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (real)",
        state=AccountState.ACTIVE,
        used_percent=52.0,
        resets_in_seconds=43 * 60,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=3 * 3600 + 25 * 60,
        session_resets_at=1_780_001_500.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 240.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 3 * 3600 + 30 * 60,
                    "first_session_resets_at": 1_780_001_500.0,
                    "session_resets_in_seconds": 3 * 3600 + 25 * 60,
                    "session_resets_at": 1_780_001_500.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 52.0,
                }
            }
        )
    )

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" not in rendered
    assert "Active" in rendered
    assert "session in 3h 25m" in rendered
    assert "session phantom" not in rendered
    assert "Use if needed" in rendered


def test_status_table_keeps_new_tiny_anchored_session_active(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=4 * 3600 + 54 * 60,
        session_resets_at=1_780_018_000.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Phantom session" not in rendered
    assert "Active" in rendered
    assert "session in 4h 54m" in rendered
    assert "Kick session" not in rendered
    assert "Kick-ready windows" not in rendered
    assert "Use if needed" in rendered
    assert _kick_eligibility(account, status, "codex", history=[]).kickable is False


def test_observe_phantom_session_clears_stable_anchored_reset(monkeypatch, tmp_path):
    phantom_file = tmp_path / "phantom.json"
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=4 * 3600 + 54 * 60,
        session_resets_at=1_780_018_000.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    phantom_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 200.0,
                    "observations": 51,
                    "first_session_resets_in_seconds": 8633,
                    "first_session_resets_at": 1_780_000_000.0,
                    "session_resets_in_seconds": 4 * 3600 + 55 * 60,
                    "session_resets_at": 1_780_018_000.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )

    _observe_phantom_session_state(account, status)

    assert json.loads(phantom_file.read_text()) == {}


def test_weekly_reset_ready_overrides_phantom_session_display(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=120, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 300.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        weekly_auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=52.0,
        resets_in_seconds=0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=3 * 3600 + 2 * 60,
        session_resets_at=1_780_001_100.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "phantom.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 240.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 3 * 3600 + 30 * 60,
                    "first_session_resets_at": 1_780_000_600.0,
                    "session_resets_in_seconds": 3 * 3600 + 2 * 60,
                    "session_resets_at": 1_780_001_100.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 52.0,
                }
            }
        )
    )
    history = [
        KickEvent(
            label="codex (primaryaccount)",
            timestamp=120.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=100,
        )
    ]
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    _render_status_table([status], [account])

    rendered = output.getvalue()
    assert "Weekly ready" in rendered
    assert "Phantom session" not in rendered
    assert "Kick now" in rendered
    assert "Kick session" not in rendered
    assert "Weekly ready windows" in rendered
    assert "anchor them now" in rendered
    assert _kick_eligibility(account, status, "codex", history=history).kick_type == "kick"
    assert _kick_type_for_status(status) == "kick"
    targets, deferred = _kickable_window_targets(
        [account],
        statuses_by_key={account_key_string(account): status},
    )
    assert targets == [(account, status)]
    assert deferred == []


def test_provider_accepted_phantom_backoff_is_not_actionable(monkeypatch, tmp_path):
    output = io.StringIO()
    monkeypatch.setattr(
        "tokenkick.cli.console",
        Console(file=output, width=140, color_system=None),
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", tmp_path / "recovery.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_000.0)
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        weekly_auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=6 * 24 * 3600 + 23 * 3600,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=4 * 3600 + 51 * 60,
        session_resets_at=1_000.0 + 4 * 3600 + 51 * 60,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    (tmp_path / "recovery.json").write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_started_at": 900.0,
                    "last_seen_at": 900.0,
                    "last_attempt_at": 900.0,
                    "attempts": 1,
                    "status": "provider_accepted",
                    "cooldown_until": 1_900.0,
                }
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _render_status_table([status], [account])

    rendered = output.getvalue()
    eligibility = _kick_eligibility(account, status, "codex", history=[])
    assert eligibility.kickable is False
    assert eligibility.reason == "phantom_recovery_backoff"
    assert "Phantom session" in rendered
    assert "session phantom" in rendered
    assert "session in 4h 51m" not in rendered
    assert "Provider accepted; recheck after" in rendered
    assert "Kick-ready windows" not in rendered


def test_sliding_session_reset_at_marks_provider_unchanged_before_stuck_wait(monkeypatch, tmp_path):
    account = AccountConfig(label="codex (reserve)", provider="codex")
    status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17999,
        session_resets_at=1_780_018_360.0,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    history = [
        KickEvent(
            label="codex (reserve)",
            timestamp=100.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=11008,
            output_tokens=18,
        )
    ]
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 120.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "first_session_resets_at": 1_780_018_000.0,
                    "session_resets_in_seconds": 17999,
                    "session_resets_at": 1_780_018_360.0,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 120.0)

    eligibility = _kick_eligibility(account, status, "codex", history=history)

    assert eligibility.reason == "provider_unchanged"


def test_available_unanchored_status_is_kickable_despite_session_artifact(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: status)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets([account])

    assert targets == [(account, status)]
    assert deferred == []


def test_status_surfaces_dormant_wake_hint_once_until_state_changes(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-work",
    )
    stale_status = AccountStatus(
        label="codex (work)",
        state=AccountState.FRESH,
        stale=True,
        stale_seconds=9_960,
        source_detail="codexbar-history",
    )
    active_status = AccountStatus(label="codex (work)", state=AccountState.ACTIVE)
    state = {"status": stale_status}
    notifications = []

    monkeypatch.setattr("tokenkick.cli.DORMANT_HINTS_FILE", tmp_path / "dormant-hints.json")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [state["status"]], False, "Loaded test status.", []),
    )
    monkeypatch.setattr(
        "tokenkick.cli.notify_dormant_account",
        lambda label, config: notifications.append(label),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    first = CliRunner().invoke(cli, ["status"])
    second = CliRunner().invoke(cli, ["status"])
    state["status"] = active_status
    cleared = CliRunner().invoke(cli, ["status", "--refresh"])
    state["status"] = stale_status
    third = CliRunner().invoke(cli, ["status", "--refresh"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert cleared.exit_code == 0
    assert third.exit_code == 0
    assert 'Run `tk wake "codex (work)"` to bootstrap it.' in first.output
    assert 'Run `tk wake "codex (work)"` to bootstrap it.' not in second.output
    assert 'Run `tk wake "codex (work)"` to bootstrap it.' in third.output
    assert notifications == ["codex (work)", "codex (work)"]


def test_status_summary_excludes_phantom_confirmed_session_cooldown(monkeypatch, tmp_path):
    account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_provider="codex",
        codexbar_account="work@example.test",
    )
    status = AccountStatus(
        label="work",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                "account|codex|work@example.test": {
                    "first_seen_at": 10_000.0,
                    "last_seen_at": 11_300.0,
                    "observations": 6,
                    "session_resets_in_seconds": 17940,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 11_300.0)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status", "--refresh"])

    assert result.exit_code == 0
    assert "Kick now" in result.output
    assert "Weekly ready windows: work" in result.output


def test_status_marks_codex_active_session_with_matching_unconfirmed_attempt(monkeypatch):
    now = 100_000.0
    anchor_at = now - 20 * 60
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=28.0,
        resets_in_seconds=4 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_at=anchor_at + 300 * 60,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        timestamp=anchor_at,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_cluster_id="cluster-reserve",
        codex_attempt_started_at=anchor_at,
        codex_attempt_finished_at=anchor_at + 10,
        codex_confirmation_method="none",
    )

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [event])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Codex unconfirmed" in result.output
    assert "session unconfirm" in result.output
    assert "Confirming session" in result.output


def test_status_refresh_records_phantom_session_observation(monkeypatch, tmp_path):
    account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="work@example.test",
    )
    status = AccountStatus(
        label="work",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._migrate_v04_direct_sources_if_needed",
        lambda config, **_kwargs: config,
    )
    monkeypatch.setattr(
        "tokenkick.cli._migrate_provider_first_labels_if_needed",
        lambda config: config,
    )
    monkeypatch.setattr(
        "tokenkick.cli._migrate_codex_home_keys_if_needed",
        lambda config: config,
    )
    monkeypatch.setattr(
        "tokenkick.cli._repair_codex_home_identity_drift_if_needed",
        lambda config: config,
    )
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache_fast",
        lambda config: ([account], [status], False, "Loaded test status.", [], False),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status", "--refresh", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(phantom_file.read_text())
    assert data[account_key_string(account)]["first_seen_at"] == 10_000.0
    assert data[account_key_string(account)]["first_session_resets_in_seconds"] == 17940


def test_status_summary_includes_zero_session_fresh_account(monkeypatch):
    account = AccountConfig(label="ready", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="ready",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Kick now" in result.output
    assert "Weekly ready windows: ready" in result.output


def test_status_summary_excludes_scheduled_pending_fresh_account(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    account = AccountConfig(label="scheduled", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="scheduled",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
        session_used_percent=0.0,
    )
    now = datetime.now(timezone.utc)
    pending_file = tmp_path / "pending-kicks.json"
    pending_file.write_text(
        json.dumps(
            {
                "manual|codex|scheduled": {
                    "account_key": "manual|codex|scheduled",
                    "account_label": "scheduled",
                    "provider": "codex",
                    "kick_at": _utc_iso(now + timedelta(hours=2)),
                    "created_at": _utc_iso(now - timedelta(hours=1)),
                    "reason": "optimal",
                    "windows_needed": 2,
                    "expected_waste_minutes": 0,
                    "waste_location": "none",
                    "work_start": _utc_iso(now + timedelta(hours=2)),
                    "work_end": _utc_iso(now + timedelta(hours=10)),
                    "notified": True,
                }
            }
        )
    )

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Weekly ready windows" not in result.output


def test_status_verbose_shows_pending_kick_window_basis(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    account = AccountConfig(label="scheduled", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="scheduled",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_window_minutes=300,
    )
    now = datetime.now(timezone.utc)
    pending_file = tmp_path / "pending-kicks.json"
    pending_file.write_text(
        json.dumps(
            {
                "manual|codex|scheduled": {
                    "account_key": "manual|codex|scheduled",
                    "account_label": "scheduled",
                    "provider": "codex",
                    "kick_at": _utc_iso(now + timedelta(hours=2)),
                    "created_at": _utc_iso(now - timedelta(hours=1)),
                    "reason": "optimal",
                    "windows_needed": 2,
                    "expected_waste_minutes": 0,
                    "waste_location": "none",
                    "work_start": _utc_iso(now + timedelta(hours=2)),
                    "work_end": _utc_iso(now + timedelta(hours=10)),
                    "window_basis": "session",
                    "notified": True,
                }
            }
        )
    )

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    default = CliRunner().invoke(cli, ["status"])
    verbose = CliRunner().invoke(cli, ["status", "--verbose"])

    assert default.exit_code == 0
    assert verbose.exit_code == 0
    assert "(session)" not in default.output
    assert "(session)" in verbose.output


def test_pending_kick_migration_rekeys_due_kick_before_execution(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    old_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    new_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct_dev",
        identity_email="dev@example.test",
    )
    now = datetime.now(timezone.utc)
    due_at = _utc_iso(now - timedelta(hours=1))
    pending_file.write_text(
        json.dumps(
            {
                "account|codex|dev@example.test": {
                    "account_key": "account|codex|dev@example.test",
                    "account_label": "dev",
                    "provider": "codex",
                    "kick_at": due_at,
                    "created_at": _utc_iso(now - timedelta(hours=2)),
                    "reason": "optimal",
                    "windows_needed": 2,
                    "expected_waste_minutes": 180,
                    "waste_location": "pre_work",
                    "work_start": _utc_iso(now + timedelta(hours=4)),
                    "work_end": _utc_iso(now + timedelta(hours=11)),
                    "window_basis": "session",
                    "notified": True,
                }
            }
        )
        + "\n"
    )

    _migrate_pending_kick_keys([old_account], [new_account])

    migrated = scheduling_mod.load_pending_kicks(now)
    assert list(migrated) == ["identity|codex|acct_dev"]
    assert migrated["identity|codex|acct_dev"].kick_at == due_at
    assert migrated["identity|codex|acct_dev"].window_basis == "session"


def test_status_summary_excludes_true_phantom_waiting_state(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_provider="codex",
        codexbar_account="phantom@example.test",
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_300.0)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda config: ([account], [status], False, "Loaded test status.", []),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["status", "--refresh"])

    assert result.exit_code == 0
    assert "Weekly ready windows: phantom" in result.output


def test_sort_statuses_orders_by_actionability():
    accounts = [
        AccountConfig(label="unknown", provider="codex"),
        AccountConfig(label="active-claude", provider="claude"),
        AccountConfig(label="active-codex-late", provider="codex"),
        AccountConfig(label="active-codex-soon", provider="codex"),
        AccountConfig(
            label="codex-spark (dev)",
            provider="codex",
            codex_rate_limit_id="codex_bengalfox",
        ),
        AccountConfig(label="fresh-monitor", provider="gemini"),
        AccountConfig(label="fresh-claude", provider="claude"),
        AccountConfig(label="waiting", provider="codex"),
        AccountConfig(label="fresh-kick", provider="codex"),
    ]
    statuses = [
        AccountStatus(label="unknown", state=AccountState.UNKNOWN),
        AccountStatus(
            label="active-claude",
            state=AccountState.ACTIVE,
            used_percent=12,
            session_resets_in_seconds=7200,
        ),
        AccountStatus(
            label="active-codex-late",
            state=AccountState.ACTIVE,
            used_percent=89,
            session_resets_in_seconds=7200,
        ),
        AccountStatus(
            label="active-codex-soon",
            state=AccountState.ACTIVE,
            used_percent=12,
            session_resets_in_seconds=600,
        ),
        AccountStatus(
            label="codex-spark (dev)",
            state=AccountState.ACTIVE,
            used_percent=0,
            session_resets_in_seconds=300,
        ),
        AccountStatus(label="fresh-monitor", state=AccountState.FRESH, used_percent=0),
        AccountStatus(label="fresh-claude", state=AccountState.FRESH, used_percent=0),
        AccountStatus(label="waiting", state=AccountState.WAITING),
        AccountStatus(label="fresh-kick", state=AccountState.FRESH, used_percent=0),
    ]

    sorted_statuses = _sort_statuses(statuses, accounts)

    assert [status.label for status in sorted_statuses] == [
        "fresh-claude",
        "fresh-kick",
        "active-claude",
        "active-codex-soon",
        "active-codex-late",
        "waiting",
        "codex-spark (dev)",
        "fresh-monitor",
        "unknown",
    ]


def test_status_json_output_is_sorted_by_actionability(monkeypatch):
    accounts = [
        AccountConfig(label="unknown", provider="codex"),
        AccountConfig(label="fresh-monitor", provider="gemini"),
        AccountConfig(label="fresh-kick", provider="codex"),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            accounts,
            [
                AccountStatus(label="unknown", state=AccountState.UNKNOWN),
                AccountStatus(label="fresh-monitor", state=AccountState.FRESH),
                AccountStatus(label="fresh-kick", state=AccountState.FRESH),
            ],
            "Found 3 accounts via auto-discovery: codex, gemini.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    assert [status["label"] for status in _status_json_accounts(result.output)] == [
        "fresh-kick",
        "fresh-monitor",
        "unknown",
    ]


def test_status_hides_hidden_accounts_by_default_and_all_shows_them(monkeypatch):
    accounts = [
        AccountConfig(label="visible", provider="codex", visible=True),
        AccountConfig(label="hidden", provider="codex", visible=False),
    ]
    statuses = [
        AccountStatus(label="visible", state=AccountState.ACTIVE),
        AccountStatus(label="hidden", state=AccountState.FRESH),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (accounts, statuses, "Found 2 accounts via auto-discovery: codex."),
    )

    default = CliRunner().invoke(cli, ["status", "--json-output"])
    show_all = CliRunner().invoke(cli, ["status", "--json-output", "--all"])

    assert default.exit_code == 0
    assert show_all.exit_code == 0
    assert [status["label"] for status in _status_json_accounts(default.output)] == ["visible"]
    assert [status["label"] for status in _status_json_accounts(show_all.output)] == ["hidden", "visible"]


def test_filter_status_pairs_by_provider_keeps_matching_accounts_and_statuses():
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="gemini", provider="gemini"),
    ]
    statuses = [
        AccountStatus(label="codex", state=AccountState.ACTIVE),
        AccountStatus(label="gemini", state=AccountState.FRESH),
    ]

    filtered_accounts, filtered_statuses = _filter_status_pairs_by_provider(
        accounts,
        statuses,
        "codex",
    )

    assert [account.label for account in filtered_accounts] == ["codex"]
    assert [status.label for status in filtered_statuses] == ["codex"]


def test_status_codex_filter_hides_non_codex_table_rows(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="gemini", provider="gemini"),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            accounts,
            [
                AccountStatus(label="codex", state=AccountState.ACTIVE),
                AccountStatus(label="gemini", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: codex, gemini.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--codex"])

    assert result.exit_code == 0
    assert "codex" in result.output
    assert "gemini" not in result.output
    assert "Monitor only" not in result.output


def test_status_codex_filter_applies_to_json_output(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="gemini", provider="gemini"),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            accounts,
            [
                AccountStatus(label="codex", state=AccountState.ACTIVE),
                AccountStatus(label="gemini", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: codex, gemini.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output", "--codex"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert [status["label"] for status in data["accounts"]] == ["codex"]


def test_status_auto_discovers_when_config_has_no_accounts(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=15.0,
                    resets_in_seconds=3600,
                    window_minutes=10080,
                )
            ],
            "Found 1 account via CodexBar: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert data["accounts"][0]["label"] == "dev"
    assert data["accounts"][0]["state"] == "active"
    assert data["accounts"][0]["used_percent"] == 15.0


def test_status_json_wrapper_present_with_zero_accounts(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: ([], [], False, "loaded", []),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert data == {
        "cached": False,
        "cached_at": None,
        "refresh_error": None,
        "refresh_in_progress": False,
        "schema_version": 1,
        "accounts": [],
    }


def test_status_json_wrapper_cached_at_uses_oldest_observed_account(monkeypatch):
    accounts = [
        AccountConfig(label="newer", provider="codex"),
        AccountConfig(label="older", provider="codex"),
    ]
    statuses = [
        AccountStatus(label="newer", state=AccountState.ACTIVE, observed_at="2026-05-24T14:32:00Z"),
        AccountStatus(label="older", state=AccountState.ACTIVE, observed_at="2026-05-24T14:20:00Z"),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: (accounts, statuses, False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert data["cached_at"] == "2026-05-24T14:20:00Z"
    assert data["schema_version"] == 1


def test_status_json_wrapper_refresh_error_when_background_refresh_fails(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex")
    key = account_key_string(account)
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-24T14:00:00Z")
    _save_status_cache([account], {key: AccountStatus(label="cached", state=AccountState.ACTIVE)})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: datetime(2026, 5, 24, 14, 10, tzinfo=timezone.utc))
    monkeypatch.setattr("tokenkick.cli._start_background_status_refresh", lambda: False)
    monkeypatch.setattr("tokenkick.cli._status_refresh_lock_active", lambda: False)

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert data["cached"] is True
    assert data["refresh_error"] == "Background refresh could not start."


def test_status_json_marks_account_refresh_error_stale(monkeypatch, tmp_path):
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    key = account_key_string(account)
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-24T14:00:00Z")
    _save_status_cache(
        [account],
        {key: AccountStatus(label="claude", state=AccountState.ACTIVE)},
        {key: "TimeoutExpired"},
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[account], poll_interval_minutes=5),
    )
    monkeypatch.setattr("tokenkick.cli._status_cache_needs_refresh", lambda _entries, _config: False)

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert data["accounts"][0]["label"] == "claude"
    assert data["accounts"][0]["stale"] is True
    assert data["accounts"][0]["refresh_error"] == "TimeoutExpired"


def test_incomplete_claude_weekly_cache_is_refresh_failed(monkeypatch, tmp_path):
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    key = account_key_string(account)
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-24T14:00:00Z")
    _save_status_cache(
        [account],
        {
            key: AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=63.0,
                window_minutes=10080,
                source_detail="claude-cli-usage",
            )
        },
    )

    cached = _load_status_cache(Config(accounts=[account], poll_interval_minutes=5))

    assert cached is not None
    _accounts, _statuses, entries = cached
    assert entries[key]["refresh_error"] == "IncompleteClaudeWeeklyReset"
    assert _status_cache_entry_is_stale(entries[key], Config(accounts=[account], poll_interval_minutes=5))


def test_claude_probe_context_ignores_incomplete_cached_success(monkeypatch, tmp_path):
    from tokenkick.status_cache import _claude_probe_context_for_account

    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    key = account_key_string(account)
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-24T14:00:00Z")
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=63.0,
        window_minutes=10080,
        source_detail="claude-cli-usage",
    )
    setattr(
        status,
        "_claude_probe_context",
        ClaudeProbeContext(
            last_direct_success_at="2026-05-24T14:00:00Z",
            last_direct_success_status=status,
        ),
    )
    _save_status_cache([account], {key: status})

    context = _claude_probe_context_for_account(account, Config(accounts=[account]))

    assert context.last_direct_success_at is None
    assert context.last_direct_success_status is None


def test_status_json_wrapper_refresh_in_progress(monkeypatch, tmp_path):
    lock_file = tmp_path / "status-cache-refresh.pid"
    lock_file.write_text("12345")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: lock_file.stat().st_mtime)
    monkeypatch.setattr("tokenkick.status_cache._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: ([], [], False, "loaded", []),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])

    assert result.exit_code == 0
    assert _status_json_payload(result.output)["refresh_in_progress"] is True


def test_status_refresh_codex_direct_failure_flows_through_codexbar_fallback(
    monkeypatch,
    tmp_path,
):
    observed = "2026-05-23T04:18:33Z"
    observed_epoch = datetime.fromisoformat(observed.replace("Z", "+00:00")).timestamp()
    managed_home = tmp_path / "managed-codex"
    managed_home.mkdir()
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    managed_accounts = tmp_path / "managed-codex-accounts.json"
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(managed_home / "missing-sessions"),
        provider_home=str(managed_home),
        codexbar_account="codex@example.test",
        identity_provider_id="acct_123",
        identity_email="codex@example.test",
    )
    history_dir.joinpath("codex.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "codex:v1:provider-account:acct_123": [
                        {
                            "name": "weekly",
                            "windowMinutes": 10080,
                            "entries": [
                                {
                                    "capturedAt": observed,
                                    "usedPercent": 9,
                                    "resetsAt": "2026-05-30T04:18:33Z",
                                }
                            ],
                        }
                    ]
                },
            }
        )
    )
    managed_accounts.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {"email": "codex@example.test", "providerAccountID": "acct_123"}
                ],
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._discover_accounts_and_statuses", lambda: ([], [], "none"))
    monkeypatch.setattr("tokenkick.cli._prune_phantom_session_observations_for_accounts", lambda _accounts: None)
    monkeypatch.setattr(
        "tokenkick.sources.read_codex_identity",
        lambda _home: DirectIdentity(
            provider="codex",
            provider_account_id="acct_123",
            email="codex@example.test",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="provider usage unavailable",
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_MANAGED_ACCOUNTS_FILE", managed_accounts)
    monkeypatch.setattr("tokenkick.codexbar_source.time.time", lambda: observed_epoch)

    result = CliRunner().invoke(cli, ["status", "--refresh", "--json-output"])

    assert result.exit_code == 0
    payload = _status_json_payload(result.output)
    assert payload["accounts"][0]["label"] == "codex"
    assert payload["accounts"][0]["state"] == "active"
    assert payload["accounts"][0]["used_percent"] == 9.0
    assert payload["accounts"][0]["source_detail"] == "codexbar-history"
    assert payload["accounts"][0]["stale"] is False


def test_status_json_account_row_includes_agent_planning_fields(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=True,
        weekly_auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(label="codex", state=AccountState.ACTIVE, used_percent=1)
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at="2026-05-24T18:00:00Z",
        created_at="2026-05-24T14:00:00Z",
        reason="align with work window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-24T17:00:00Z",
        work_end="2026-05-24T22:00:00Z",
        window_basis="session",
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            accounts=[account],
            schedule=ScheduleConfig(
                enabled=True,
                accounts={"codex": WorkSchedule(enabled=True, weekdays="17:00-22:00")},
            ),
        ),
    )
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-24T14:32:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: ([account], [status], False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.status_rendering.load_pending_kicks", lambda *_args, **_kwargs: {account_key_string(account): pending})

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])

    assert result.exit_code == 0
    row = _status_json_accounts(result.output)[0]
    assert row["provider"] == "codex"
    assert row["account_key"] == account_key_string(account)
    assert row["auto_kick"] is True
    assert row["weekly_auto_kick"] is True
    assert row["session_auto_kick"] is True
    assert row["weekly_used_percent"] == 1
    assert row["weekly_headroom_percent"] == 99.0
    assert row["schedule_enabled"] is True
    assert row["schedule_weekdays"] == "17:00-22:00"
    assert row["kickable"] is False
    assert row["kick_blocked_reason"] == "pending_kick"
    assert row["next_kick_at"] == "2026-05-24T18:00:00Z"
    assert row["pending_kick"]["window_basis"] == "session"
    assert row["pending_kick"]["next_action_at"] == "2026-05-24T18:00:00Z"


def test_status_json_codex_filter_scopes_accounts_not_metadata(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="gemini", provider="gemini"),
    ]
    statuses = [
        AccountStatus(label="codex", state=AccountState.ACTIVE, observed_at="2026-05-24T14:32:00Z"),
        AccountStatus(label="gemini", state=AccountState.ACTIVE, observed_at="2026-05-24T14:00:00Z"),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: (accounts, statuses, False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli, ["status", "--json-output", "--codex", "--refresh"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert [row["label"] for row in data["accounts"]] == ["codex"]
    assert data["cached_at"] == "2026-05-24T14:32:00Z"


def _mock_run_refresh(monkeypatch, accounts, statuses, config: Config | None = None):
    config = config or Config(accounts=list(accounts))
    monkeypatch.setattr(
        "tokenkick.cli._run_refresh",
        lambda *, codex_only: (config, list(accounts), list(statuses), 2300, None),
    )


def _run_json_payload(output: str) -> dict:
    return json.loads(output[output.index("{"):])


def test_run_refreshes_and_kicks_fresh_auto_account(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    events = []
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, pre_status=None, **kwargs: events.append((account.label, kwargs))
        or KickEvent(label=account.label, success=True),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert len(events) == 1
    assert events[0][0] == "codex"
    stagger_state = events[0][1].pop("stagger_state")
    assert isinstance(stagger_state, KickStaggerState)
    assert events[0][1] == {"send_notification": False, "kick_type": "kick"}
    assert "Refreshed 1 accounts in 2.3s" in result.output
    assert "Kicked 1 accounts" in result.output
    assert "codex — weekly window anchored" in result.output
    assert "Dry run evaluated at" not in result.output


def test_run_dry_run_does_not_call_kick(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not kick"),
    )

    result = CliRunner().invoke(cli, ["run", "--dry-run"])

    assert result.exit_code == 0
    assert "Would kick 1 accounts" in result.output
    assert "codex — weekly window would be anchored" in result.output
    assert "Dry run evaluated at" in result.output


def test_run_skips_account_in_phantom_backoff(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr(
        "tokenkick.cli.load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=datetime.now(timezone.utc).timestamp(),
                success=True,
                confirmed=False,
                kind="probe",
                error="Provider still reports a tiny phantom session after the kick attempt",
            )
        ],
    )
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "phantom-session backoff" in result.output


def test_run_skips_when_smart_schedule_defers(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    pending_at = datetime.now(timezone.utc) + timedelta(hours=2, minutes=13)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._run_schedule_decision",
        lambda *_args, **_kwargs: PendingKick(
            account_key=account_key_string(account),
            account_label=account.label,
            provider=account.provider,
            kick_at=pending_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason="optimal",
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=pending_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            work_end=(pending_at + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "scheduled for" in result.output
    assert "Kicked 0 accounts" in result.output


def _due_orchestrated_run_account():
    return AccountConfig(
        label="personal",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )


def _due_orchestrated_run_status():
    return AccountStatus(
        label="personal",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_window_minutes=300,
        session_resets_in_seconds=0,
    )


def _save_due_orchestrated_pending(scheduling_mod, account, now):
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=_utc_iso(now - timedelta(minutes=5)),
        created_at=_utc_iso(now - timedelta(hours=1)),
        reason="orchestrated",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=_utc_iso(now - timedelta(minutes=5)),
        work_end=_utc_iso(now + timedelta(hours=3)),
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({pending.account_key: pending})
    return pending


def _smart_schedule_config_for(account):
    return Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            accounts={
                account.label: WorkSchedule(
                    enabled=True,
                    weekdays="00:00-23:30",
                    weekends="00:00-23:30",
                )
            },
        ),
    )


def test_run_executes_due_orchestrated_pending_instead_of_rescheduling(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    from tokenkick.models import append_kick_event

    account = _due_orchestrated_run_account()
    status = _due_orchestrated_run_status()
    now = datetime.now(timezone.utc)
    _save_due_orchestrated_pending(scheduling_mod, account, now)
    _mock_run_refresh(monkeypatch, [account], [status], config=_smart_schedule_config_for(account))
    kick_types = []

    def fake_kick(account_arg, _config, pre_status=None, **kwargs):
        event = KickEvent(
            label=account_arg.label,
            success=True,
            kind=kwargs.get("kick_type") or "kick",
            kick_type=kwargs.get("kick_type"),
        )
        kick_types.append(kwargs.get("kick_type"))
        append_kick_event(event)
        return event

    monkeypatch.setattr("tokenkick.cli._kick_and_notify", fake_kick)

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    payload = _run_json_payload(result.output)
    assert result.exit_code == 0
    assert kick_types == ["session"]
    kicked_reasons = [item["reason"] for item in payload["kicked"]]
    assert "orchestrated pending kick executed" in kicked_reasons
    # The due orchestrated kick executed and was removed; it must not have been
    # converted into a smart-schedule pending by the configured work window.
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def test_run_records_retry_backoff_when_due_orchestrated_kick_fails(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    account = _due_orchestrated_run_account()
    status = _due_orchestrated_run_status()
    now = datetime.now(timezone.utc)
    pending = _save_due_orchestrated_pending(scheduling_mod, account, now)
    _mock_run_refresh(monkeypatch, [account], [status], config=_smart_schedule_config_for(account))
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account_arg, _config, pre_status=None, **kwargs: KickEvent(
            label=account_arg.label,
            success=False,
            error="provider exploded",
            kind=kwargs.get("kick_type") or "kick",
            kick_type=kwargs.get("kick_type"),
        ),
    )

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    payload = _run_json_payload(result.output)
    assert result.exit_code == 1
    skipped_reasons = [item["reason"] for item in payload["skipped"]]
    assert any(
        reason.startswith("orchestrated pending kick failed: provider exploded")
        for reason in skipped_reasons
    )
    stored = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    remaining = stored[pending.account_key]
    assert remaining.reason == "orchestrated"
    assert remaining.attempt_count == 1
    assert remaining.next_retry_at is not None


def test_run_does_not_opportunistically_kick_deferred_orchestrated_pending(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    account = _due_orchestrated_run_account()
    status = _due_orchestrated_run_status()
    now = datetime.now(timezone.utc)
    pending = _save_due_orchestrated_pending(scheduling_mod, account, now)
    _mock_run_refresh(monkeypatch, [account], [status])
    # Simulate any due-pending guard deferral (stale status, boundary grace,
    # cooldown, ...) by leaving the due orchestrated pending in place.
    monkeypatch.setattr(
        "tokenkick.cli._execute_due_pending_kicks",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail(
            "generic evaluation must not kick an account owned by an orchestrated pending kick"
        ),
    )

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    payload = _run_json_payload(result.output)
    assert result.exit_code == 0
    assert payload["kicked"] == []
    owned = [
        item
        for item in payload["skipped"]
        if item.get("reason_code") == "orchestrated_pending_owns_account"
    ]
    assert len(owned) == 1
    assert owned[0]["label"] == "personal"
    assert owned[0]["reason"] == "orchestrated pending kick owns this account"
    stored = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert stored[pending.account_key].reason == "orchestrated"
    assert stored[pending.account_key].kick_at == pending.kick_at


def test_run_dry_run_previews_due_orchestrated_execution(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    account = _due_orchestrated_run_account()
    status = _due_orchestrated_run_status()
    now = datetime.now(timezone.utc)
    pending = _save_due_orchestrated_pending(scheduling_mod, account, now)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not kick"),
    )

    result = CliRunner().invoke(cli, ["run", "--dry-run", "--json-output"])

    payload = _run_json_payload(result.output)
    assert result.exit_code == 0
    assert payload["dry_run"] is True
    assert [item["reason"] for item in payload["kicked"]] == [
        "orchestrated pending kick would be executed"
    ]
    assert payload["kicked"][0]["dry_run"] is True
    stored = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert stored[pending.account_key].kick_at == pending.kick_at


def test_run_evaluate_does_not_convert_due_orchestrated_to_smart_schedule(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    account = _due_orchestrated_run_account()
    now = datetime.now(timezone.utc)
    pending = _save_due_orchestrated_pending(scheduling_mod, account, now)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    local_now = datetime.now().astimezone()
    window = (
        f"{(local_now + timedelta(hours=2)).strftime('%H:%M')}"
        f"-{(local_now + timedelta(hours=5)).strftime('%H:%M')}"
    )
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            accounts={
                account.label: WorkSchedule(enabled=True, weekdays=window, weekends=window)
            },
        ),
    )

    bucket, payload, failed = _run_evaluate_account(
        account,
        status,
        config,
        dry_run=False,
        history=[],
        pending=scheduling_mod.load_pending_kicks(now),
        now=now,
    )

    assert bucket == "skipped"
    assert failed is False
    assert payload["reason"] == "orchestrated pending kick owns this account"
    assert payload["reason_code"] == "orchestrated_pending_owns_account"
    stored = scheduling_mod.load_pending_kicks(now)
    assert stored[pending.account_key].reason == "orchestrated"
    assert stored[pending.account_key].kick_at == pending.kick_at


def test_run_skips_monitor_only_provider(monkeypatch):
    account = AccountConfig(label="antigravity", provider="antigravity", auto_kick=True)
    status = AccountStatus(label="antigravity", state=AccountState.FRESH)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "antigravity — not kickable (monitor-only)" in result.output


def test_run_skips_gemini_daily_rpd_monitor_only(monkeypatch):
    account = AccountConfig(label="gemini", provider="gemini", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(label="gemini", state=AccountState.FRESH)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail("Gemini must not be kicked"),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "gemini — monitor-only (daily RPD reset)" in result.output


def test_run_json_marks_gemini_monitor_only_reason_code(monkeypatch):
    account = AccountConfig(label="gemini", provider="gemini")
    status = AccountStatus(label="gemini", state=AccountState.FRESH)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    assert result.exit_code == 0
    data = _run_json_payload(result.output)
    assert data["skipped"] == [
        {
            "label": "gemini",
            "provider": "gemini",
            "reason": "monitor-only (daily RPD reset)",
            "reason_code": "monitor_only_daily_rpd",
        }
    ]


def test_run_session_auto_kick_requires_session_auto_enabled(monkeypatch):
    enabled = AccountConfig(label="enabled", provider="codex", auto_kick=True, session_auto_kick=True)
    disabled = AccountConfig(label="disabled", provider="codex", auto_kick=True, session_auto_kick=False)
    statuses = [
        AccountStatus(
            label="enabled",
            state=AccountState.ACTIVE,
            session_used_percent=0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        ),
        AccountStatus(
            label="disabled",
            state=AccountState.ACTIVE,
            session_used_percent=0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        ),
    ]
    calls = []
    _mock_run_refresh(monkeypatch, [enabled, disabled], statuses)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, pre_status=None, **kwargs: calls.append((account.label, kwargs))
        or KickEvent(label=account.label, success=True),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == "enabled"
    stagger_state = calls[0][1].pop("stagger_state")
    assert isinstance(stagger_state, KickStaggerState)
    assert calls[0][1] == {"send_notification": False, "kick_type": "session"}
    assert "enabled — session window anchored" in result.output
    assert "disabled — session auto-kick disabled" in result.output


def test_claude_session_auto_kick_requires_session_reset_anchor(monkeypatch):
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=63.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=None,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )

    assert _kick_eligibility(account, status, history=[]).kickable is False


def test_claude_session_auto_kick_uses_predicted_due_session(monkeypatch):
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=99.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )

    eligibility = _kick_eligibility(account, status, history=[])

    assert eligibility.kickable is True
    assert eligibility.kick_type == "session"


def test_claude_predicted_due_status_replaces_failed_passive_refresh():
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="Claude direct /usage disabled during passive refresh.",
        )
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=73.0,
                window_minutes=10080,
                session_used_percent=99.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            )
        }
    }

    _apply_claude_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.ACTIVE
    assert statuses_by_key[key].session_resets_in_seconds == 0
    assert synthetic_status_reason(statuses_by_key[key]) == "claude_session_due_from_cache"


def test_claude_predicted_due_status_replaces_stale_passive_refresh():
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=73.0,
            window_minutes=10080,
            session_used_percent=99.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
            source_detail="claude-cli-usage",
            stale=True,
            stale_seconds=900,
            error="passive refresh unavailable",
        )
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=73.0,
                window_minutes=10080,
                session_used_percent=99.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            )
        }
    }

    _apply_claude_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.ACTIVE
    assert statuses_by_key[key].session_resets_in_seconds == 0
    assert statuses_by_key[key].stale is False
    assert synthetic_status_reason(statuses_by_key[key]) == "claude_session_due_from_cache"


def test_claude_predicted_due_status_waits_until_cached_reset_due():
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(label="claude", state=AccountState.UNKNOWN, error="refresh failed")
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=73.0,
                window_minutes=10080,
                session_used_percent=99.0,
                session_resets_in_seconds=300,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            )
        }
    }

    _apply_claude_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.UNKNOWN


def test_claude_predicted_due_status_stops_after_newer_direct_probe_failure():
    account = AccountConfig(label="claude", provider="claude", auto_kick=True, session_auto_kick=True)
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(label="claude", state=AccountState.UNKNOWN, error="refresh failed")
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=73.0,
                window_minutes=10080,
                session_used_percent=99.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            ),
            "last_direct_success_at": "2026-05-28T10:00:00Z",
            "last_direct_probe_at": "2026-05-28T12:00:00Z",
            "last_direct_probe_error": ClaudeProbeError(
                ClaudeProbeErrorCategory.NOT_AUTHENTICATED,
                "Claude CLI is not logged in. Run `claude auth login --claudeai`.",
            ),
        }
    }

    _apply_claude_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.UNKNOWN
    assert synthetic_status_reason(statuses_by_key[key]) is None


def test_codex_predicted_due_status_replaces_failed_direct_refresh():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(
            label="codex",
            state=AccountState.UNKNOWN,
            stale=True,
            stale_seconds=2400,
            error="Codex provider usage failed.",
        )
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="codex",
                state=AccountState.ACTIVE,
                used_percent=49.0,
                window_minutes=10080,
                session_used_percent=1.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                stale=True,
                stale_seconds=2400,
                error="Codex provider usage failed.",
            )
        }
    }

    _apply_codex_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.ACTIVE
    assert statuses_by_key[key].session_resets_in_seconds == 0
    assert statuses_by_key[key].stale is False
    assert statuses_by_key[key].error is None
    assert synthetic_status_reason(statuses_by_key[key]) == "codex_session_due_from_cache"


def test_codex_predicted_due_status_waits_until_cached_reset_due():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(label="codex", state=AccountState.UNKNOWN, error="refresh failed")
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="codex",
                state=AccountState.ACTIVE,
                used_percent=49.0,
                window_minutes=10080,
                session_used_percent=1.0,
                session_resets_in_seconds=300,
                session_window_minutes=300,
                source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
            )
        }
    }

    _apply_codex_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.UNKNOWN


def test_codex_predicted_due_status_skips_weekly_exhausted_account():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(label="codex", state=AccountState.UNKNOWN, error="refresh failed")
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="codex",
                state=AccountState.ACTIVE,
                used_percent=100.0,
                window_minutes=10080,
                session_used_percent=0.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
            )
        }
    }

    _apply_codex_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.UNKNOWN


def test_codex_predicted_due_status_allows_stale_weekly_reset_ready():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    statuses_by_key = {
        key: AccountStatus(label="codex", state=AccountState.UNKNOWN, error="refresh failed")
    }
    entries = {
        key: {
            "status": AccountStatus(
                label="codex",
                state=AccountState.ACTIVE,
                used_percent=100.0,
                resets_in_seconds=0,
                window_minutes=10080,
                session_used_percent=0.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
                stale=True,
                stale_seconds=63 * 60 * 60,
                error="Codex provider usage failed.",
            )
        }
    }

    _apply_codex_predicted_session_due_statuses([account], statuses_by_key, entries)

    assert statuses_by_key[key].state == AccountState.ACTIVE
    assert statuses_by_key[key].used_percent == 100.0
    assert statuses_by_key[key].session_resets_in_seconds == 0
    assert statuses_by_key[key].stale is False
    assert synthetic_status_reason(statuses_by_key[key]) == "codex_session_due_from_cache"


def test_daemon_sleep_uses_sixty_second_floor_for_phantom_recovery():
    account = AccountConfig(label="phantom", provider="codex")
    status = AccountStatus(label="phantom", state=AccountState.ACTIVE)

    assert _daemon_sleep_seconds(300, [], [(account, 15)]) == 60
    assert _daemon_sleep_seconds(300, [], [(account, 75)]) == 75
    assert _daemon_sleep_seconds(300, [(account, status, 180)], [(account, 15)]) == 60


def test_codex_session_kick_skips_weekly_exhausted_account():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=100.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is False
    assert eligibility.reason == "weekly_exhausted"
    assert _status_action(status, {"codex": "codex"}, account) == "Weekly exhausted"


def test_codex_session_kick_skips_session_exhausted_account():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=51.0,
        window_minutes=10080,
        session_used_percent=100.0,
        session_resets_in_seconds=2400,
        session_window_minutes=300,
    )

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is False
    assert eligibility.reason == "session_exhausted"
    assert _status_action(status, {"codex": "codex"}, account) == "Wait for session"


def test_claude_session_kick_uses_usage_flow(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=99.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    post_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17_880,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    recorded = []
    saved = []
    notified = []
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr("tokenkick.cli.kick_account", lambda *_args, **_kwargs: pytest.fail("should use /usage"))
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: post_status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, _config: notified.append(event) or True)

    event = _kick_and_notify(
        account,
        Config(accounts=[account], notifications=NotifyConfig(enabled=True, backend="ntfy")),
        pre_status,
        kick_type="session",
    )

    assert event.success is True
    assert event.confirmed is True
    assert event.evidence_provider_moved is True
    assert event.post_kick_status == "moved"
    assert event.kind == "session"
    assert event.kick_type == "session"
    assert event.prompt_text == "/usage"
    assert recorded == [event]
    assert notified == [event]
    assert saved


def test_kick_and_notify_uses_disabled_account_notification_config(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", notifications_enabled=False)
    observed_configs: list[NotifyConfig] = []
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: KickEvent(label="codex", success=True, confirmed=True),
    )
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda _event, notifications: observed_configs.append(notifications) or notifications.enabled,
    )

    event = _kick_and_notify(
        account,
        Config(
            accounts=[account],
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        ),
    )

    assert event.success is True
    assert len(observed_configs) == 1
    assert observed_configs[0].enabled is False
    assert observed_configs[0].backend == "ntfy"
    assert observed_configs[0].ntfy_topic == "topic"


def test_kick_and_notify_fans_out_to_account_notification_backends(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        notification_backends=["ntfy", "telegram"],
    )
    observed_backends: list[str] = []
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: KickEvent(label="codex", success=True, confirmed=True),
    )
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda _event, notifications: observed_backends.append(notifications.backend) or True,
    )

    event = _kick_and_notify(
        account,
        Config(
            accounts=[account],
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=["ntfy", "telegram"],
            ),
        ),
    )

    assert event.success is True
    assert observed_backends == ["ntfy", "telegram"]


def test_kick_and_notify_does_not_send_account_telegram_when_backend_disabled(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        notification_backends=["telegram"],
    )
    observed_configs: list[NotifyConfig] = []
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: KickEvent(label="codex", success=True, confirmed=True),
    )
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda _event, notifications: observed_configs.append(notifications) or notifications.enabled,
    )

    event = _kick_and_notify(
        account,
        Config(
            accounts=[account],
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=["ntfy"],
            ),
        ),
    )

    assert event.success is True
    assert len(observed_configs) == 1
    assert observed_configs[0].enabled is False


def test_claude_session_kick_requires_observed_anchor(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=99.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    post_status = replace(pre_status, session_used_percent=99.0, session_resets_in_seconds=0)
    recorded = []
    notified = []
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr("tokenkick.cli.kick_account", lambda *_args, **_kwargs: pytest.fail("should use /usage"))
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: post_status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, _config: notified.append(event) or True)

    event = _kick_and_notify(
        account,
        Config(accounts=[account], notifications=NotifyConfig(enabled=True, backend="ntfy")),
        pre_status,
        kick_type="session",
    )

    assert event.success is True
    assert event.confirmed is False
    assert event.evidence_provider_moved is False
    assert event.post_kick_status == "unchanged"
    assert event.error == "Claude /usage completed, but session anchor was not observed"
    assert event.kind == "session"
    assert event.kick_type == "session"
    assert recorded == [event]
    assert notified == [event]


def test_claude_session_kick_rejects_non_direct_fallback(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=99.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    fallback_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        source_detail="claude-codexbar-fallback",
    )
    recorded = []
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: fallback_status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda _event, _config: True)

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status,
        kick_type="session",
        send_notification=False,
    )

    assert event.success is False
    assert event.confirmed is False
    assert "non-direct status" in event.error
    assert recorded == [event]


def test_claude_reconciliation_probe_records_tracked_status_touch(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    cached_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=20.0,
        session_resets_in_seconds=7_200,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    fresh_status = replace(cached_status, session_resets_in_seconds=7_000)
    entries = {
        key: {
            "status": cached_status,
            "last_direct_success_at": "2026-05-28T10:00:00Z",
        }
    }
    statuses_by_key = {
        key: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="passive refresh failed",
        )
    }
    recorded = []
    notified = []
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: datetime(2026, 5, 28, 12, 1, tzinfo=timezone.utc))
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: fresh_status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, _config: notified.append(event) or True)

    executed = _execute_claude_reconciliation_probes(
        [account],
        statuses_by_key,
        Config(accounts=[account]),
        entries,
    )

    assert executed == 1
    assert recorded[0].kind == "reconcile"
    assert recorded[0].kick_type == "status_probe"
    assert recorded[0].confirmed is False
    assert recorded[0].evidence_provider_moved is False
    assert recorded[0].post_kick_status == "unchanged"
    assert recorded[0].error == "Claude /usage completed, but session anchor was not observed"
    assert recorded[0].prompt_text == "/usage"
    assert "reconciliation completed" in recorded[0].response_text
    assert notified == []
    assert statuses_by_key[key].session_resets_in_seconds == 7_000


def test_claude_reconciliation_probe_notifies_when_session_jumps(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    cached_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        window_minutes=10080,
        session_used_percent=20.0,
        session_resets_in_seconds=7_200,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    fresh_status = replace(cached_status, session_used_percent=0.0, session_resets_in_seconds=17_880)
    entries = {
        key: {
            "status": cached_status,
            "last_direct_success_at": "2026-05-28T10:00:00Z",
        }
    }
    statuses_by_key = {key: AccountStatus(label="claude", state=AccountState.ACTIVE)}
    recorded = []
    notified = []
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: datetime(2026, 5, 28, 12, 1, tzinfo=timezone.utc))
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: fresh_status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, _config: notified.append(event) or True)

    executed = _execute_claude_reconciliation_probes(
        [account],
        statuses_by_key,
        Config(accounts=[account], notifications=NotifyConfig(enabled=True, backend="ntfy")),
        entries,
    )

    assert executed == 1
    assert recorded[0].kind == "session"
    assert recorded[0].kick_type == "session"
    assert recorded[0].confirmed is True
    assert recorded[0].evidence_provider_moved is True
    assert recorded[0].post_kick_status == "moved"
    assert "anchored session" in recorded[0].response_text
    assert notified == [recorded[0]]


def test_claude_reconciliation_probe_waits_for_interval(monkeypatch):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    key = account_key_string(account)
    entries = {
        key: {
            "status": AccountStatus(
                label="claude",
                state=AccountState.ACTIVE,
                used_percent=73.0,
                window_minutes=10080,
                session_used_percent=20.0,
                session_resets_in_seconds=7_200,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            ),
            "last_direct_success_at": "2026-05-28T10:00:00Z",
        }
    }
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: datetime(2026, 5, 28, 11, 0, tzinfo=timezone.utc))
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda *_args, **_kwargs: pytest.fail("too early"))

    executed = _execute_claude_reconciliation_probes(
        [account],
        {},
        Config(accounts=[account]),
        entries,
    )

    assert executed == 0


def test_claude_usage_touch_persists_direct_success_timestamp(monkeypatch, tmp_path):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=73.0,
        resets_in_seconds=3600,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17_880,
        session_window_minutes=300,
        source_detail="claude-cli-usage",
    )
    observed_at = "2026-05-30T10:00:00Z"

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: observed_at)
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: status)

    event, _status = _run_claude_usage_touch(
        account,
        Config(accounts=[account]),
        kind="reconcile",
        kick_type="status_probe",
        success_response="ok",
        failure_prefix="failed",
    )

    entries = _load_status_cache_entries()
    entry = entries[account_key_string(account)]
    assert event.success is True
    assert entry["last_direct_probe_at"] == observed_at
    assert entry["last_direct_success_at"] == observed_at
    assert entry["last_direct_success_status"].source_detail == "claude-cli-usage"


def test_claude_usage_touch_persists_direct_failure_context(monkeypatch, tmp_path):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    observed_at = "2026-05-30T10:00:00Z"
    status = AccountStatus(
        label="claude",
        state=AccountState.UNKNOWN,
        error=(
            "Claude CLI is not logged in. Run `claude auth login --claudeai` as the "
            "same user that runs TokenKick, then run `tk status --refresh`."
        ),
        source_detail="claude-cli-usage",
    )
    setattr(
        status,
        "_claude_probe_context",
        ClaudeProbeContext(
            last_direct_probe_at=observed_at,
            last_direct_probe_error=ClaudeProbeError(
                ClaudeProbeErrorCategory.NOT_AUTHENTICATED,
                status.error,
            ),
        ),
    )

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: observed_at)
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: status)

    event, _status = _run_claude_usage_touch(
        account,
        Config(accounts=[account]),
        kind="session",
        kick_type="session",
        success_response="ok",
        failure_prefix="failed",
    )

    entries = _load_status_cache_entries()
    entry = entries[account_key_string(account)]
    assert event.success is False
    assert "claude auth login --claudeai" in event.error
    assert entry["status"].state == AccountState.UNKNOWN
    assert entry["last_direct_probe_at"] == observed_at
    assert entry["last_direct_probe_error"].category == ClaudeProbeErrorCategory.NOT_AUTHENTICATED


def test_run_session_auto_kick_respects_existing_schedule(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_used_percent=0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            default=WorkSchedule(enabled=True, weekdays=None, weekends=None),
        ),
    )
    _mock_run_refresh(monkeypatch, [account], [status], config)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail("scheduled session auto-kick must not kick"),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "codex — no scheduled session auto-kick window today" in result.output


def test_run_json_output_shape(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=False)
    status = AccountStatus(label="codex", state=AccountState.ACTIVE)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    assert result.exit_code == 0
    data = _run_json_payload(result.output)
    assert data["schema_version"] == 1
    assert data["refreshed_count"] == 1
    assert data["refresh_duration_ms"] == 2300
    assert data["refresh_error"] is None
    assert data["dry_run"] is False
    assert data["kicked"] == []
    assert data["skipped"] == [{"label": "codex", "provider": "codex", "reason": "already active"}]


def test_run_codex_passes_filter_to_refresh(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "tokenkick.cli._run_refresh",
        lambda *, codex_only: captured.append(codex_only) or (Config(), [], [], 10, None),
    )

    result = CliRunner().invoke(cli, ["run", "--codex"])

    assert result.exit_code == 0
    assert captured == [True]


def test_run_exits_one_on_kick_failure(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, pre_status=None, **kwargs: KickEvent(
            label=account.label,
            success=False,
            error="boom",
        ),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 1
    assert "codex — kick failed: boom" in result.output


def test_run_empty_config_is_clean(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._run_refresh",
        lambda *, codex_only: (Config(), [], [], 4, None),
    )

    result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0
    assert "No saved accounts" in result.output
    assert "Refreshed 0 accounts" in result.output


def test_bare_command_auto_discovers_and_prints_setup_hint(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=15.0,
                    resets_in_seconds=3600,
                    window_minutes=10080,
                )
            ],
            "Found 1 account via CodexBar: codex.",
        ),
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert "dev" in result.output
    assert "Run tk setup to save this config and enable notifications." in result.output


def test_bare_command_opens_interactive_menu_when_tty(monkeypatch):
    opened = []
    monkeypatch.setattr("tokenkick.cli._should_open_interactive_menu", lambda: True)
    monkeypatch.setattr("tokenkick.cli._open_interactive_menu", lambda _ctx: opened.append(True))

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert opened == [True]


def test_no_interactive_env_keeps_bare_status(monkeypatch):
    monkeypatch.setenv("TK_NO_INTERACTIVE", "1")
    monkeypatch.setattr(
        "tokenkick.cli.sys.stdin",
        SimpleNamespace(isatty=lambda: True),
    )
    monkeypatch.setattr(
        "tokenkick.cli.sys.stdout",
        SimpleNamespace(isatty=lambda: True),
    )

    module = __import__("tokenkick.cli", fromlist=["_should_open_interactive_menu"])
    assert not module._should_open_interactive_menu()


def test_menu_command_opens_interactive_menu(monkeypatch):
    import tokenkick.interactive as interactive

    calls = []
    monkeypatch.setattr(
        interactive,
        "run_command_center",
        lambda _ctx, **kwargs: calls.append(kwargs),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert calls == [{"first_run_setup": False}]


def test_load_accounts_reads_saved_config_without_live_discovery(monkeypatch):
    account = AccountConfig(label="saved", provider="codex")

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )

    assert _load_accounts(Config(accounts=[account])) == [account]


def test_account_only_commands_do_not_discover_or_fetch_status(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=True),
        AccountConfig(label="claude", provider="claude", auto_kick=False),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )

    listed = CliRunner().invoke(cli, ["accounts", "list"])
    auto_result = CliRunner().invoke(cli, ["auto", "status"])

    assert listed.exit_code == 0
    assert auto_result.exit_code == 0
    assert "codex" in listed.output
    assert "claude" in auto_result.output


def test_account_only_mutations_do_not_discover_or_fetch_status(monkeypatch):
    saved = []
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=False, visible=True),
        AccountConfig(label="claude", provider="claude", auto_kick=False, visible=True),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )

    enabled = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="ENABLE\n")
    hidden = CliRunner().invoke(cli, ["accounts", "hide", "claude"])

    assert enabled.exit_code == 0
    assert hidden.exit_code == 0
    assert saved[0].accounts[0].auto_kick is True
    assert saved[1].accounts[1].visible is False


def test_account_only_commands_with_no_saved_accounts_stay_fast(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )

    listed = CliRunner().invoke(cli, ["accounts", "list"])
    auto_result = CliRunner().invoke(cli, ["auto", "status"])

    assert listed.exit_code == 0
    assert auto_result.exit_code == 0
    assert "No saved accounts" in listed.output
    assert "No saved accounts" in auto_result.output


def test_status_cache_round_trips_statuses_for_saved_accounts(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    status = AccountStatus(
        label="cached",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        resets_in_seconds=3600,
        window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")

    _save_status_cache([account], {"manual|codex|cached": status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    accounts, statuses, entries = cached
    assert [loaded.label for loaded in accounts] == ["cached"]
    assert entries["manual|codex|cached"]["cached_at"] == "2026-05-22T08:00:00Z"
    assert entries["manual|codex|cached"]["refresh_error"] is None
    assert statuses[0].state == AccountState.ACTIVE
    assert statuses[0].used_percent == 12.0


def test_claude_direct_probe_metadata_round_trips_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    key = account_key_string(account)
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=20.0,
        source_detail="claude-cli-usage",
    )
    setattr(
        status,
        "_claude_probe_context",
        ClaudeProbeContext(
            last_direct_probe_at="2026-05-24T10:00:00Z",
            last_direct_probe_error=None,
            last_direct_success_at="2026-05-24T10:00:00Z",
            last_direct_success_status=status,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)

    _save_status_cache([account], {key: status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    entry = cached[2][key]
    assert entry["last_direct_probe_at"] == "2026-05-24T10:00:00Z"
    assert entry["last_direct_success_at"] == "2026-05-24T10:00:00Z"
    assert entry["last_direct_probe_error"] is None
    assert entry["last_direct_success_status"].source_detail == "claude-cli-usage"


def test_claude_direct_probe_error_round_trips_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    key = account_key_string(account)
    status = AccountStatus(label="claude", state=AccountState.ACTIVE, source_detail="claude-codexbar-fallback")
    setattr(
        status,
        "_claude_probe_context",
        ClaudeProbeContext(
            last_direct_probe_at="2026-05-24T10:00:00Z",
            last_direct_probe_error=ClaudeProbeError(ClaudeProbeErrorCategory.TIMEOUT, "timeout"),
        ),
    )
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)

    _save_status_cache([account], {key: status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    error = cached[2][key]["last_direct_probe_error"]
    assert error.category == ClaudeProbeErrorCategory.TIMEOUT


def test_status_cache_preserves_useful_cache_when_full_refresh_times_out(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    key = "manual|codex|cached"
    active_status = AccountStatus(
        label="cached",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        resets_in_seconds=3600,
        window_minutes=300,
    )
    timeout_status = AccountStatus(
        label="cached",
        state=AccountState.UNKNOWN,
        error="codexbar timed out after 8s",
    )
    observed_times = iter(["2026-05-22T08:00:00Z", "2026-05-22T08:05:00Z", "2026-05-22T08:05:00Z"])

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: next(observed_times))

    _save_status_cache([account], {key: active_status})
    _save_status_cache([account], {key: timeout_status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, statuses, entries = cached
    assert entries[key]["cached_at"] == "2026-05-22T08:05:00Z"
    assert entries[key]["refresh_error"] == "TimeoutExpired"
    assert statuses[0].state == AccountState.ACTIVE
    assert statuses[0].used_percent == 12.0


def test_status_cache_failure_keeps_provider_observed_age_separate_from_touch(
    monkeypatch,
    tmp_path,
):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    key = "manual|codex|cached"
    active_status = AccountStatus(
        label="cached",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        resets_in_seconds=3600,
        window_minutes=300,
    )
    timeout_status = AccountStatus(
        label="cached",
        state=AccountState.UNKNOWN,
        error="codex provider usage read timed out",
    )
    observed_times = iter(["2026-05-22T08:00:00Z", "2026-05-22T08:09:00Z"])

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: next(observed_times))
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 10, tzinfo=timezone.utc),
    )

    _save_status_cache([account], {key: active_status})
    _save_status_cache([account], {key: timeout_status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, _statuses, entries = cached
    assert entries[key]["cached_at"] == "2026-05-22T08:09:00Z"
    assert entries[key]["provider_observed_at"] == "2026-05-22T08:00:00Z"
    footer = _format_status_cache_footer(entries, Config(poll_interval_minutes=5))
    assert "oldest 10m ago" in footer
    assert "oldest 1m ago" not in footer


@pytest.mark.parametrize(
    ("provider_observed_at", "expected_footer"),
    [
        ("2026-05-22T08:08:00Z", "2m ago"),
        ("2026-05-22T07:50:00Z", "oldest 20m ago"),
    ],
)
def test_status_cache_uses_codexbar_fallback_observed_at_for_freshness(
    monkeypatch,
    tmp_path,
    provider_observed_at,
    expected_footer,
):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct-codex",
    )
    key = account_key_string(account)
    is_stale = provider_observed_at == "2026-05-22T07:50:00Z"
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        observed_at=provider_observed_at,
        source_detail="codexbar-history",
        stale_seconds=1_200 if is_stale else 120,
        stale=is_stale,
    )

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:09:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 10, tzinfo=timezone.utc),
    )

    _save_status_cache([account], {key: status})
    cached = _load_status_cache(Config(accounts=[account], poll_interval_minutes=5))

    assert cached is not None
    _accounts, statuses, entries = cached
    assert statuses[0].observed_at == provider_observed_at
    assert entries[key]["cached_at"] == "2026-05-22T08:09:00Z"
    assert entries[key]["provider_observed_at"] == provider_observed_at
    footer = _format_status_cache_footer(entries, Config(poll_interval_minutes=5))
    assert expected_footer in footer


def test_status_cache_recomputes_countdown_from_absolute_reset_anchor(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    key = "manual|codex|cached"
    observed = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)
    loaded_at = datetime(2026, 5, 22, 8, 10, tzinfo=timezone.utc)
    status = AccountStatus(
        label="cached",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        resets_in_seconds=3600,
        window_minutes=300,
        session_used_percent=1.0,
        session_resets_in_seconds=1800,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: loaded_at.timestamp())

    _save_status_cache([account], {key: status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, statuses, entries = cached
    loaded_status = statuses[0]
    assert entries[key]["status"].resets_at == observed.timestamp() + 3600
    assert entries[key]["status"].session_resets_at == observed.timestamp() + 1800
    assert loaded_status.resets_in_seconds == 3000
    assert loaded_status.session_resets_in_seconds == 1200


def test_status_cache_preserves_useful_cache_when_provider_returns_error_status(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    key = "manual|codex|cached"
    active_status = AccountStatus(label="cached", state=AccountState.ACTIVE, used_percent=12.0)
    error_status = AccountStatus(
        label="cached",
        state=AccountState.UNKNOWN,
        error="Codex returned invalid data: codex app-server closed stdout",
    )
    observed_times = iter(["2026-05-22T08:00:00Z", "2026-05-22T08:05:00Z"])

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: next(observed_times))

    _save_status_cache([account], {key: active_status})
    _save_status_cache([account], {key: error_status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, statuses, entries = cached
    assert entries[key]["cached_at"] == "2026-05-22T08:05:00Z"
    assert entries[key]["refresh_error"] == "ParseError"
    assert statuses[0].state == AccountState.ACTIVE
    assert statuses[0].used_percent == 12.0


def test_status_cache_merges_per_account_when_one_refresh_fails(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    accounts = [
        AccountConfig(label="one", provider="codex"),
        AccountConfig(label="two", provider="codex"),
        AccountConfig(label="three", provider="codex"),
    ]
    keys = [f"manual|codex|{account.label}" for account in accounts]
    observed_times = iter(["2026-05-22T08:00:00Z", "2026-05-22T08:05:00Z"])

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: next(observed_times))

    _save_status_cache(
        accounts,
        {
            keys[0]: AccountStatus(label="one", state=AccountState.ACTIVE, used_percent=10.0),
            keys[1]: AccountStatus(label="two", state=AccountState.ACTIVE, used_percent=20.0),
            keys[2]: AccountStatus(label="three", state=AccountState.ACTIVE, used_percent=30.0),
        },
    )
    _save_status_cache(
        accounts,
        {
            keys[0]: AccountStatus(label="one", state=AccountState.ACTIVE, used_percent=11.0),
            keys[1]: AccountStatus(label="two", state=AccountState.UNKNOWN, error="boom"),
            keys[2]: AccountStatus(label="three", state=AccountState.ACTIVE, used_percent=31.0),
        },
        {keys[1]: "RuntimeError"},
    )
    cached = _load_status_cache(Config(accounts=accounts))

    assert cached is not None
    _accounts, statuses, entries = cached
    by_label = {status.label: status for status in statuses}
    assert by_label["one"].used_percent == 11.0
    assert by_label["two"].used_percent == 20.0
    assert by_label["three"].used_percent == 31.0
    assert entries[keys[0]]["refresh_error"] is None
    assert entries[keys[1]]["refresh_error"] == "RuntimeError"
    assert entries[keys[1]]["cached_at"] == "2026-05-22T08:05:00Z"
    assert entries[keys[2]]["refresh_error"] is None


def test_status_cache_logs_refresh_failure_for_daemon(monkeypatch, tmp_path, capsys):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex")
    key = "manual|codex|cached"

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")

    _save_status_cache(
        [account],
        {key: AccountStatus(label="cached", state=AccountState.UNKNOWN, error="boom")},
        {key: "RuntimeError"},
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert "[cache_refresh_failed]" in output
    assert 'account="cached"' in output
    assert 'error_class="RuntimeError"' in output


def test_status_cache_writes_timeout_status_when_no_useful_cache_exists(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    account = AccountConfig(label="cached", provider="codex", visible=True)
    timeout_status = AccountStatus(
        label="cached",
        state=AccountState.UNKNOWN,
        error="codexbar timed out after 8s",
    )

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")

    _save_status_cache([account], {"manual|codex|cached": timeout_status})
    cached = _load_status_cache(Config(accounts=[account]))

    assert cached is not None
    _accounts, statuses, entries = cached
    assert entries["manual|codex|cached"]["cached_at"] == "2026-05-22T08:00:00Z"
    assert entries["manual|codex|cached"]["refresh_error"] == "TimeoutExpired"
    assert statuses[0].state == AccountState.UNKNOWN
    assert statuses[0].error == "codexbar timed out after 8s"


def test_status_cache_write_is_atomic_when_tmp_write_fails(monkeypatch, tmp_path):
    cache_file = tmp_path / "status-cache.json"
    original_content = '{"version": 1, "observed_at": "old", "accounts": [], "statuses": []}\n'
    cache_file.write_text(original_content)
    account = AccountConfig(label="cached", provider="codex", visible=True)
    status = AccountStatus(label="cached", state=AccountState.ACTIVE)

    def fail_replace(_source, _target):
        raise OSError("interrupted replace")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr("tokenkick.state_io.os.replace", fail_replace)

    _save_status_cache([account], {"manual|codex|cached": status})

    assert cache_file.read_text() == original_content
    assert not list(tmp_path.glob(".status-cache.json.*.tmp"))


def test_status_cache_footer_renders_fresh_cache_in_local_time(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 5, tzinfo=timezone.utc),
    )

    footer = _format_status_cache_footer(
        {
            "manual|codex|cached": {
                "cached_at": "2026-05-22T08:00:00Z",
                "refresh_error": None,
            }
        },
        Config(poll_interval_minutes=5),
    )

    expected_time = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc).astimezone().strftime("%H:%M")
    assert expected_time in footer
    assert "(5m ago)" in footer
    assert "daemon may not be running" not in footer
    assert "tk status --refresh" in footer
    assert "CEST" not in footer
    assert "CET" not in footer


def test_status_footer_timestamp_uses_date_for_other_days():
    same_day = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)
    later_same_day = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    previous_day = datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc)

    assert _format_status_footer_timestamp(
        same_day,
        now=later_same_day,
    ) == same_day.astimezone().strftime("%H:%M")
    assert _format_status_footer_timestamp(
        previous_day,
        now=later_same_day,
    ) == previous_day.astimezone().strftime(
        "%Y-%m-%d %H:%M",
    )


def test_report_timestamp_text_uses_local_footer_format():
    now = datetime(2026, 5, 22, 8, 5, tzinfo=timezone.utc)

    text = _report_timestamp_text("History printed at", now=now)

    assert text == f"History printed at {now.astimezone().strftime('%H:%M')}."
    assert "CEST" not in text
    assert "CET" not in text


def test_status_cache_footer_renders_mildly_stale_cache_without_warning(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 10, tzinfo=timezone.utc),
    )

    footer = _format_status_cache_footer(
        {
            "manual|codex|cached": {
                "cached_at": "2026-05-22T08:00:00Z",
                "refresh_error": None,
            }
        },
        Config(poll_interval_minutes=5),
    )

    assert "(10m ago)" in footer
    assert "Cached provider data from" in footer
    assert "stale" not in footer


def test_status_cache_footer_warns_when_cache_is_stale(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 45, tzinfo=timezone.utc),
    )

    footer = _format_status_cache_footer(
        {
            "manual|codex|cached": {
                "cached_at": "2026-05-22T08:00:00Z",
                "refresh_error": None,
            }
        },
        Config(poll_interval_minutes=5),
    )

    assert "0 current" in footer
    assert "1 old (oldest 45m ago)" in footer
    assert "Run [bold]tk status --refresh[/bold]" in footer


def test_status_cache_footer_summarizes_mixed_freshness(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 5, tzinfo=timezone.utc),
    )

    footer = _format_status_cache_footer(
        {
            "manual|codex|fresh": {
                "cached_at": "2026-05-22T08:04:00Z",
                "refresh_error": None,
            },
            "manual|codex|stale": {
                "cached_at": "2026-05-22T07:20:00Z",
                "refresh_error": "TimeoutExpired",
            },
        },
        Config(poll_interval_minutes=5),
    )

    assert "Cache: 1 current (1m ago), 1 old (oldest 45m ago)" in footer
    assert (
        "manual|codex|stale (last refresh failed: TimeoutExpired; last provider read 45m ago)"
        in footer
    )
    assert "Run [bold]tk status --refresh[/bold]" in footer


def test_status_cache_footer_labels_claude_passive_refresh_failure(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 22, tzinfo=timezone.utc),
    )
    account = AccountConfig(label="claude (work)", provider="claude")
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        source_detail="claude-cli-usage",
    )

    footer = _format_status_cache_footer(
        {
            account_key_string(account): {
                "account": account,
                "status": status,
                "cached_at": "2026-05-22T08:13:00Z",
                "provider_observed_at": "2026-05-22T08:00:00Z",
                "refresh_error": "ProviderError",
            },
        },
        Config(poll_interval_minutes=10),
    )

    assert "claude (work)" in footer
    assert "passive refresh unavailable; last provider read 22m ago" in footer
    assert "last refresh failed" not in footer


def test_status_cache_footer_names_weekly_exhausted_stale_account(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
    )
    account = AccountConfig(label="codex (personal)", provider="codex")
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=100.0,
        resets_in_seconds=17 * 60 * 60,
        window_minutes=10080,
    )

    footer = _format_status_cache_footer(
        {
            "manual|codex|secondary": {
                "account": account,
                "status": status,
                "cached_at": "2026-05-22T08:00:00Z",
                "provider_observed_at": "2026-05-20T16:00:00Z",
                "refresh_error": "ProviderError",
            }
        },
        Config(poll_interval_minutes=5),
    )

    assert "0 current" in footer
    assert "1 old (oldest 40h ago)" in footer
    assert "codex (personal) (weekly exhausted; last provider read 40h ago)" in footer
    assert "ProviderError" not in footer


def test_calendar_json_uses_cache_without_provider_refresh(monkeypatch):
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_at=(now + timedelta(days=2)).timestamp(),
        window_minutes=10080,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: now)
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda *_args, **_kwargs: pytest.fail("calendar must not refresh providers"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.load_pending_kicks",
        lambda *_args, **_kwargs: {
            account_key_string(account): PendingKick(
                account_key=account_key_string(account),
                account_label=account.label,
                provider=account.provider,
                kick_at="2026-05-27T14:00:00Z",
                created_at="2026-05-27T12:00:00Z",
                reason="align with work window",
                windows_needed=1,
                expected_waste_minutes=0,
                waste_location="none",
                work_start="2026-05-27T13:00:00Z",
                work_end="2026-05-27T18:00:00Z",
                window_basis="session",
            )
        },
    )
    _save_status_cache([account], {account_key_string(account): status})

    result = CliRunner().invoke(cli, ["calendar", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == 1
    assert data["generated_at"] == "2026-05-27T12:00:00Z"
    assert data["days_ahead"] == 7
    assert data["pending_kicks"][0]["account_label"] == "codex"
    assert data["pending_kicks"][0]["next_action_at"] == "2026-05-27T14:00:00Z"
    assert data["events"][0]["account"] == "codex"
    assert data["events"][0]["type"] == "weekly_reset"
    assert data["events"][0]["predicted_at"] == "2026-05-29T12:00:00Z"


def test_calendar_filters_days_account_codex_and_hidden(monkeypatch):
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="hidden", provider="codex", visible=False),
        AccountConfig(label="claude", provider="claude"),
    ]
    statuses = [
        AccountStatus(
            label="codex",
            state=AccountState.ACTIVE,
            resets_at=(now + timedelta(days=2)).timestamp(),
        ),
        AccountStatus(
            label="hidden",
            state=AccountState.ACTIVE,
            resets_at=(now + timedelta(days=1)).timestamp(),
        ),
        AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            resets_at=(now + timedelta(days=8)).timestamp(),
        ),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: now)
    _save_status_cache(
        accounts,
        {
            account_key_string(account): status
            for account, status in zip(accounts, statuses, strict=False)
        },
    )

    default = CliRunner().invoke(cli, ["calendar"])
    codex = CliRunner().invoke(cli, ["calendar", "--codex"])
    hidden = CliRunner().invoke(cli, ["calendar", "--account", "hidden", "--all"])
    short = CliRunner().invoke(cli, ["calendar", "--days", "1", "--json-output"])

    assert default.exit_code == 0
    assert "codex" in default.output
    assert "hidden" not in default.output
    assert "claude" not in default.output
    assert "Calendar generated at" in default.output
    assert "codex" in codex.output
    assert "claude" not in codex.output
    assert "hidden" in hidden.output
    assert json.loads(short.output)["events"] == []
    assert "Calendar generated at" not in short.output


def _setup_plan_cli_state(monkeypatch, tmp_path):
    _config_file, _backup_file, _status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / ".tokenkick" / "pending-kicks.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    account = AccountConfig(
        label="codex (alpha)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
        provider_home="/tmp/codex-alpha",
        usable_session_minutes=90,
    )
    config = Config(accounts=[account], schedule=ScheduleConfig(timezone="UTC"))
    config.save()
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: now)
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=1.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_at=now.timestamp(),
        session_window_minutes=300,
        observed_at=to_utc_iso(now),
    )
    _save_status_cache([account], {account_key_string(account): status})
    return account, pending_file


def test_plan_json_output_is_machine_readable_and_read_only(monkeypatch, tmp_path):
    _account, pending_file = _setup_plan_cli_state(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "10:00-13:00",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--json-output",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["read_only"] is True
    assert payload["applied"] is False
    assert payload["planned_kicks"][0]["reason"] == "orchestrated"
    assert not pending_file.exists()


def test_plan_apply_requires_yes_in_non_interactive_mode(monkeypatch, tmp_path):
    _account, pending_file = _setup_plan_cli_state(monkeypatch, tmp_path)
    monkeypatch.setenv("TK_NO_INTERACTIVE", "1")

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "10:00-13:00",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--apply",
            "--json-output",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert "--apply requires --yes" in payload["message"]
    assert not pending_file.exists()


def test_plan_apply_yes_writes_only_orchestrated_pending_kicks(monkeypatch, tmp_path):
    account, _pending_file = _setup_plan_cli_state(monkeypatch, tmp_path)
    monkeypatch.setenv("TK_NO_INTERACTIVE", "1")

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "10:00-13:00",
            "--date",
            "2026-06-05",
            "--timezone",
            "UTC",
            "--apply",
            "--yes",
            "--json-output",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["read_only"] is False
    assert payload["applied"] is True
    pending = load_pending_kicks(datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc))
    stored = pending[account_key_string(account)]
    assert stored.reason == ScheduleReason.ORCHESTRATED.value
    assert stored.window_basis == SchedulingWindowBasis.SESSION.value


def _pending_kick_for_test(
    account: AccountConfig,
    *,
    now: datetime,
    reason: str = ScheduleReason.ORCHESTRATED.value,
    purpose: str = PENDING_KICK_PURPOSE_COVERAGE,
) -> PendingKick:
    return PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now + timedelta(hours=1)),
        created_at=to_utc_iso(now),
        reason=reason,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now),
        work_end=to_utc_iso(now + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
        purpose=purpose,
    )


def _active_session_status(
    label: str,
    *,
    reset_at: datetime,
    state: AccountState = AccountState.ACTIVE,
    session_used_percent: float = 25.0,
    used_percent: float = 10.0,
    stale: bool = False,
) -> AccountStatus:
    return AccountStatus(
        label=label,
        state=state,
        used_percent=used_percent,
        session_used_percent=session_used_percent,
        session_resets_at=reset_at.timestamp(),
        session_window_minutes=300,
        stale=stale,
    )


def test_reservation_advisory_detects_quiet_period_soon_and_active():
    account = AccountConfig(label="codex (personal)", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {account_key_string(account): _pending_kick_for_test(account, now=now)}

    soon = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now + timedelta(minutes=20),
            )
        },
        pending,
        now=now,
    )
    active = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now - timedelta(minutes=5),
            )
        },
        pending,
        now=now,
    )

    assert [item.risk_state for item in soon] == [RISK_QUIET_PERIOD_SOON]
    assert [item.risk_state for item in active] == [RISK_QUIET_PERIOD_ACTIVE]


def test_reservation_advisory_reports_safe_for_non_actionable_known_status():
    account = AccountConfig(label="codex", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {account_key_string(account): _pending_kick_for_test(account, now=now)}

    reset_after_kick = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now + timedelta(hours=2),
                session_used_percent=0.0,
            )
        },
        pending,
        now=now,
    )
    stale = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now + timedelta(minutes=20),
                stale=True,
            )
        },
        pending,
        now=now,
    )
    unknown = build_reservation_advisories(
        [account],
        {
            account_key_string(account): AccountStatus(
                label=account.label,
                state=AccountState.UNKNOWN,
            )
        },
        pending,
        now=now,
    )

    assert [item.risk_state for item in reset_after_kick] == [RISK_SAFE]
    assert stale == []
    assert unknown == []


def test_reservation_advisory_includes_coverage_and_specialist_pending_kicks():
    codex = AccountConfig(label="codex", provider="codex")
    claude = AccountConfig(label="claude", provider="claude")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {
        account_key_string(codex): _pending_kick_for_test(codex, now=now),
        account_key_string(claude): _pending_kick_for_test(
            claude,
            now=now,
            purpose=PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
        ),
    }
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in (codex, claude)
    }

    advisories = build_reservation_advisories([codex, claude], statuses, pending, now=now)

    assert {item.account_label for item in advisories} == {"codex", "claude"}
    assert {item.pending_purpose for item in advisories} == {
        PENDING_KICK_PURPOSE_COVERAGE,
        PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
    }


def test_reservation_advisory_ignores_due_past_and_given_up_pending_kicks():
    account = AccountConfig(label="codex", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    status = _active_session_status(account.label, reset_at=now - timedelta(minutes=10))
    past = _pending_kick_for_test(account, now=now)
    past.kick_at = to_utc_iso(now - timedelta(minutes=1))
    given_up = _pending_kick_for_test(account, now=now)
    given_up.gave_up_at = to_utc_iso(now - timedelta(minutes=1))

    assert build_reservation_advisories(
        [account],
        {account_key_string(account): status},
        {account_key_string(account): past},
        now=now,
    ) == []
    assert build_reservation_advisories(
        [account],
        {account_key_string(account): status},
        {account_key_string(account): given_up},
        now=now,
    ) == []


def test_reservation_advisory_plan_compromised_requires_concrete_active_conflict():
    account = AccountConfig(label="codex", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {account_key_string(account): _pending_kick_for_test(account, now=now)}

    compromised = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now + timedelta(hours=2),
                session_used_percent=15.0,
            )
        },
        pending,
        now=now,
    )
    stale = build_reservation_advisories(
        [account],
        {
            account_key_string(account): _active_session_status(
                account.label,
                reset_at=now + timedelta(hours=2),
                session_used_percent=15.0,
                stale=True,
            )
        },
        pending,
        now=now,
    )

    assert [item.risk_state for item in compromised] == [RISK_PLAN_MAY_BE_COMPROMISED]
    assert stale == []


def test_reservation_advisory_suggests_unreserved_account_first():
    reserved = AccountConfig(label="codex (personal)", provider="codex")
    unreserved = AccountConfig(label="codex (work)", provider="codex")
    later_reserved = AccountConfig(label="codex (reserve)", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {
        account_key_string(reserved): _pending_kick_for_test(reserved, now=now),
        account_key_string(later_reserved): _pending_kick_for_test(
            later_reserved,
            now=now + timedelta(hours=2),
        ),
    }
    accounts = [reserved, unreserved, later_reserved]
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in accounts
    }

    [advisory] = build_reservation_advisories(accounts, statuses, pending, now=now)[:1]

    assert advisory.suggestion_label == "codex (work)"
    assert advisory.suggestion_reason == "not reserved for this plan"


def test_reservation_advisory_treats_far_smart_schedule_pending_as_unreserved():
    reserved = AccountConfig(label="codex (personal)", provider="codex")
    scheduled = AccountConfig(label="codex (work)", provider="codex")
    orchestrated = AccountConfig(label="codex (reserve)", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    scheduled_pending = _pending_kick_for_test(
        scheduled,
        now=now + timedelta(hours=2),
        reason=ScheduleReason.OPTIMAL.value,
    )
    pending = {
        account_key_string(reserved): _pending_kick_for_test(reserved, now=now),
        account_key_string(scheduled): scheduled_pending,
        account_key_string(orchestrated): _pending_kick_for_test(
            orchestrated,
            now=now + timedelta(hours=3),
        ),
    }
    accounts = [reserved, scheduled, orchestrated]
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in accounts
    }

    [advisory] = build_reservation_advisories(accounts, statuses, pending, now=now)[:1]

    assert advisory.suggestion_label == "codex (work)"
    assert advisory.suggestion_reason == "not reserved for this plan"


def test_reservation_advisory_suggests_latest_reserved_if_all_viable_reserved():
    reserved = AccountConfig(label="codex (personal)", provider="codex")
    earlier = AccountConfig(label="codex (reserve)", provider="codex")
    latest = AccountConfig(label="codex (work)", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    pending = {
        account_key_string(reserved): _pending_kick_for_test(reserved, now=now),
        account_key_string(earlier): _pending_kick_for_test(earlier, now=now + timedelta(hours=1)),
        account_key_string(latest): _pending_kick_for_test(latest, now=now + timedelta(hours=3)),
    }
    accounts = [reserved, earlier, latest]
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in accounts
    }

    [advisory] = build_reservation_advisories(accounts, statuses, pending, now=now)[:1]

    assert advisory.suggestion_label == "codex (work)"
    assert advisory.suggestion_reason is not None
    assert advisory.suggestion_reason.startswith("not needed until ")


def test_reservation_advisory_excludes_hidden_stale_exhausted_monitor_only_and_same_account():
    reserved = AccountConfig(label="codex (personal)", provider="codex")
    hidden = AccountConfig(label="codex hidden", provider="codex", visible=False)
    stale = AccountConfig(label="codex stale", provider="codex")
    exhausted = AccountConfig(label="codex exhausted", provider="codex")
    monitor = AccountConfig(label="gemini", provider="gemini")
    viable = AccountConfig(label="codex viable", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    accounts = [reserved, hidden, stale, exhausted, monitor, viable]
    pending = {account_key_string(reserved): _pending_kick_for_test(reserved, now=now)}
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in accounts
    }
    statuses[account_key_string(stale)] = replace(statuses[account_key_string(stale)], stale=True)
    statuses[account_key_string(exhausted)] = replace(statuses[account_key_string(exhausted)], used_percent=100.0)

    [advisory] = build_reservation_advisories(accounts, statuses, pending, now=now)

    assert advisory.suggestion_label == "codex viable"


def test_reservation_advisory_excludes_excluded_role_and_unusable_accounts():
    reserved = AccountConfig(label="codex reserved", provider="codex")
    excluded = AccountConfig(label="codex excluded", provider="codex", orchestration_role="excluded")
    waiting = AccountConfig(label="codex waiting", provider="codex")
    no_headroom = AccountConfig(label="codex no headroom", provider="codex")
    viable = AccountConfig(label="codex viable", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    accounts = [reserved, excluded, waiting, no_headroom, viable]
    pending = {account_key_string(reserved): _pending_kick_for_test(reserved, now=now)}
    statuses = {
        account_key_string(account): _active_session_status(
            account.label,
            reset_at=now + timedelta(minutes=20),
        )
        for account in accounts
    }
    statuses[account_key_string(waiting)] = replace(
        statuses[account_key_string(waiting)],
        state=AccountState.WAITING,
    )
    statuses[account_key_string(no_headroom)] = replace(
        statuses[account_key_string(no_headroom)],
        session_used_percent=100.0,
    )

    [advisory] = build_reservation_advisories(accounts, statuses, pending, now=now)

    assert advisory.suggestion_label == "codex viable"


def test_status_displays_reserved_account_warning(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    _config_file, _backup_file, _status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    account = AccountConfig(label="codex (personal)", provider="codex")
    now = datetime.now(timezone.utc)
    status = _active_session_status(account.label, reset_at=now + timedelta(minutes=20))
    Config(accounts=[account]).save()
    _save_status_cache([account], {account_key_string(account): status})
    save_pending_kicks({account_key_string(account): _pending_kick_for_test(account, now=now)})

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Reserved account warnings" in result.output
    assert '"codex (personal)" is reserved for an orchestration kick' in result.output
    assert "Avoid using it from" in result.output


def test_run_json_includes_reservation_advisories(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=False)
    now = datetime.now(timezone.utc)
    status = _active_session_status(account.label, reset_at=now + timedelta(minutes=20))
    pending = {account_key_string(account): _pending_kick_for_test(account, now=now)}
    _mock_run_refresh(monkeypatch, [account], [status])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda _now=None: pending)
    monkeypatch.setattr("tokenkick.cli._send_reservation_advisory_notifications", lambda *_args, **_kwargs: 0)

    result = CliRunner().invoke(cli, ["run", "--json-output"])

    assert result.exit_code == 0
    payload = _run_json_payload(result.output)
    assert payload["reservation_advisories"][0]["account_label"] == "codex"
    assert payload["reservation_advisories"][0]["risk_state"] == RISK_QUIET_PERIOD_SOON


def test_reservation_advisory_notifications_send_once_per_risk_state(monkeypatch, tmp_path):
    import tokenkick.reservation_advisories as advisories_mod
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    advisory_file = tmp_path / "reserved-account-advisories.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(advisories_mod, "RESERVATION_ADVISORY_STATE_FILE", advisory_file)
    account = AccountConfig(label="codex", provider="codex")
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    status = _active_session_status(account.label, reset_at=now + timedelta(minutes=20))
    config = Config(
        accounts=[account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="tokenkick"),
    )
    save_pending_kicks({account_key_string(account): _pending_kick_for_test(account, now=now)})
    sent = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_reservation_advisory",
        lambda message, notifications: sent.append((message, notifications.backend)) or True,
    )

    first = _send_reservation_advisory_notifications(
        [account],
        {account_key_string(account): status},
        config,
        now=now,
        daemon_log=False,
    )
    second = _send_reservation_advisory_notifications(
        [account],
        {account_key_string(account): status},
        config,
        now=now,
        daemon_log=False,
    )

    assert first == 1
    assert second == 0
    assert len(sent) == 1
    assert "reserved for an orchestration kick" in sent[0][0]


def test_reservation_advisory_notification_state_prunes_old_entries(monkeypatch, tmp_path):
    import tokenkick.reservation_advisories as advisories_mod

    advisory_file = tmp_path / "reserved-account-advisories.json"
    monkeypatch.setattr(advisories_mod, "RESERVATION_ADVISORY_STATE_FILE", advisory_file)
    now = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    old_key = f"old::{to_utc_iso(now - timedelta(days=10))}::{RISK_QUIET_PERIOD_SOON}"
    current_key = f"current::{to_utc_iso(now - timedelta(days=1))}::{RISK_QUIET_PERIOD_SOON}"
    new_key = f"new::{to_utc_iso(now + timedelta(hours=1))}::{RISK_QUIET_PERIOD_ACTIVE}"
    advisory_file.write_text(
        json.dumps(
            {
                old_key: {"notified_at": to_utc_iso(now - timedelta(days=10))},
                current_key: {"notified_at": to_utc_iso(now - timedelta(days=1))},
            }
        )
    )

    advisories_mod.mark_reservation_advisory_notified(new_key, now=now)

    state = json.loads(advisory_file.read_text())
    assert old_key not in state
    assert current_key in state
    assert new_key in state


def test_plan_cancel_yes_removes_only_orchestrated_pending_kicks(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    codex = AccountConfig(label="codex", provider="codex")
    claude = AccountConfig(label="claude", provider="claude")
    manual = AccountConfig(label="manual", provider="codex")
    now = datetime.now(timezone.utc)
    save_pending_kicks(
        {
            account_key_string(codex): _pending_kick_for_test(codex, now=now),
            account_key_string(claude): _pending_kick_for_test(
                claude,
                now=now,
                purpose=PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
            ),
            account_key_string(manual): _pending_kick_for_test(
                manual,
                now=now,
                reason=ScheduleReason.SINGLE_WINDOW.value,
            ),
        }
    )

    result = CliRunner().invoke(cli, ["plan", "cancel", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cancelled 2 orchestration pending kick(s)" in result.output
    pending = load_pending_kicks(now)
    assert list(pending) == [account_key_string(manual)]


def test_plan_cancel_account_removes_only_requested_orchestrated_pending(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    codex = AccountConfig(label="codex", provider="codex")
    claude = AccountConfig(label="claude", provider="claude")
    now = datetime.now(timezone.utc)
    save_pending_kicks(
        {
            account_key_string(codex): _pending_kick_for_test(codex, now=now),
            account_key_string(claude): _pending_kick_for_test(claude, now=now),
        }
    )

    result = CliRunner().invoke(cli, ["plan", "cancel", "--account", "codex", "--yes"])

    assert result.exit_code == 0, result.output
    pending = load_pending_kicks(now)
    assert list(pending) == [account_key_string(claude)]


def test_plan_cancel_json_without_yes_is_read_only(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    account = AccountConfig(label="codex", provider="codex")
    now = datetime.now(timezone.utc)
    save_pending_kicks({account_key_string(account): _pending_kick_for_test(account, now=now)})

    result = CliRunner().invoke(cli, ["plan", "cancel", "--json-output"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert payload["applied"] is False
    assert "--json-output requires --yes" in payload["message"]
    assert account_key_string(account) in load_pending_kicks(now)


def test_plan_cancel_no_matching_pending_kicks_is_success(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)

    result = CliRunner().invoke(cli, ["plan", "cancel", "--yes"])

    assert result.exit_code == 0
    assert "No applied orchestration pending kicks found." in result.output


def test_orchestrated_pending_executes_through_due_pending_kicks(monkeypatch, tmp_path):
    account, _pending_file = _setup_plan_cli_state(monkeypatch, tmp_path)
    due_pending_file = tmp_path / ".tokenkick" / "pending-due.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", due_pending_file)
    now = datetime.now(timezone.utc)
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now),
        work_end=to_utc_iso(now + timedelta(hours=2)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    save_pending_kicks({pending.account_key: pending})
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        used_percent=1.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    events = []

    def fake_kick_and_notify(*args, **kwargs):
        events.append((args, kwargs))
        return KickEvent(label=account.label, success=True, confirmed=False, kind="session")

    monkeypatch.setattr("tokenkick.cli._kick_and_notify", fake_kick_and_notify)
    monkeypatch.setattr("tokenkick.cli.notify_scheduled_kick", lambda *_args, **_kwargs: True)

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        statuses_by_key={account_key_string(account): status},
    )

    assert executed == 1
    assert events
    assert events[0][1]["kick_type"] == "session"
    assert events[0][1]["allow_codex_fire_all"] is True


def test_calendar_ics_output(monkeypatch):
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        resets_at=datetime(2026, 5, 29, 12, 32, tzinfo=timezone.utc).timestamp(),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._status_cache_now", lambda: now)
    _save_status_cache([account], {account_key_string(account): status})

    result = CliRunner().invoke(cli, ["calendar", "--ics"])

    assert result.exit_code == 0
    assert result.output.startswith("BEGIN:VCALENDAR")
    assert "DTSTART:20260529T123200Z" in result.output
    assert "VALARM" in result.output
    assert "TokenKick — Reset Calendar" not in result.output
    assert "Calendar generated at" not in result.output


def test_calendar_empty_and_stale_cache_warning(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.UNKNOWN,
        observed_at="2026-05-27T10:00:00Z",
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[account], poll_interval_minutes=5),
    )
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    )
    _save_status_cache([account], {account_key_string(account): status})

    result = CliRunner().invoke(cli, ["calendar"])

    assert result.exit_code == 0
    assert "No predicted resets" in result.output
    assert "Status cache is 2h old" in result.output
    assert "tk status --refresh" in result.output
    assert "Calendar generated at" in result.output


def test_calendar_help_shows_options():
    result = CliRunner().invoke(cli, ["calendar", "--help"])

    assert result.exit_code == 0
    assert "--days" in result.output
    assert "--account" in result.output
    assert "--json-output" in result.output
    assert "--ics" in result.output


def test_status_uses_daemon_cache_without_live_discovery(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex", visible=True)
    status = AccountStatus(label="cached", state=AccountState.ACTIVE, used_percent=12.0)

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    _save_status_cache([account], {"manual|codex|cached": status})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "cached" in result.output
    assert "Cache" in result.output
    assert "ago" in result.output
    assert "tk status --refresh" in result.output


def test_status_reports_refresh_failure_while_showing_preserved_cache(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex", visible=True)
    key = "manual|codex|cached"
    active_status = AccountStatus(label="cached", state=AccountState.ACTIVE, used_percent=12.0)
    timeout_status = AccountStatus(
        label="cached",
        state=AccountState.UNKNOWN,
        error="codexbar timed out after 8s",
    )
    observed_times = iter(["2026-05-22T08:00:00Z", "2026-05-22T08:05:00Z", "2026-05-22T08:05:00Z"])

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: next(observed_times))
    _save_status_cache([account], {key: active_status})
    _save_status_cache([account], {key: timeout_status})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "cached" in result.output
    assert "Active" in result.output
    assert "⚠️" in result.output
    assert "Refresh failed" in result.output
    assert "1 old" in result.output


def test_status_refresh_bypasses_daemon_cache(monkeypatch, tmp_path):
    cached_account = AccountConfig(label="cached", provider="codex")
    live_account = AccountConfig(label="live", provider="codex")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    _save_status_cache(
        [cached_account],
        {"manual|codex|cached": AccountStatus(label="cached", state=AccountState.ACTIVE)},
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[live_account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [live_account],
            [AccountStatus(label="live", state=AccountState.FRESH)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])

    assert result.exit_code == 0
    data = _status_json_payload(result.output)
    assert [row["label"] for row in data["accounts"]] == ["live"]


def test_status_refresh_updates_cache_on_success(monkeypatch, tmp_path):
    account = AccountConfig(label="live", provider="codex")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:05:00Z")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="live", state=AccountState.ACTIVE, used_percent=42.0)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])
    cached = _load_status_cache(Config(accounts=[account]))

    assert result.exit_code == 0
    assert cached is not None
    _accounts, statuses, entries = cached
    assert statuses[0].used_percent == 42.0
    assert entries["manual|codex|live"]["cached_at"] == "2026-05-22T08:05:00Z"
    assert entries["manual|codex|live"]["refresh_error"] is None


def test_status_refresh_fetches_saved_accounts_without_discovery(monkeypatch, tmp_path):
    codex = AccountConfig(label="codex", provider="codex")
    antigravity = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="antigravity",
        codexbar_account="ag@example.test",
    )
    fetched: list[str] = []

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr(
        "tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE",
        tmp_path / "status-cache-refresh.pid",
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[codex, antigravity]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run for saved refresh")),
    )

    def fake_fetch(account, _config=None):
        fetched.append(account.label)
        used = 7.0 if account.provider == "antigravity" else 0.0
        return AccountStatus(label=account.label, state=AccountState.FRESH, used_percent=used)

    monkeypatch.setattr("tokenkick.cli._fetch_status", fake_fetch)

    result = CliRunner().invoke(cli, ["status", "--refresh", "--json-output"])

    assert result.exit_code == 0
    assert fetched == ["codex", "antigravity"]
    rows = {row["label"]: row for row in _status_json_accounts(result.output)}
    assert rows["codex"]["used_percent"] == 0.0
    assert rows["antigravity"]["used_percent"] == 7.0


def test_status_without_cache_writes_cache_from_live_fetch(monkeypatch, tmp_path):
    account = AccountConfig(label="live", provider="codex")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:05:00Z")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="live", state=AccountState.ACTIVE, used_percent=42.0)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status", "--json-output"])
    cached = _load_status_cache(Config(accounts=[account]))

    assert result.exit_code == 0
    assert cached is not None
    _accounts, statuses, entries = cached
    assert statuses[0].used_percent == 42.0
    assert entries["manual|codex|live"]["cached_at"] == "2026-05-22T08:05:00Z"


def test_status_stale_cache_starts_background_refresh(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex")
    calls = []

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr(
        "tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE",
        tmp_path / "status-cache-refresh.pid",
    )
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", tmp_path / "daemon.log")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 45, tzinfo=timezone.utc),
    )
    _save_status_cache(
        [account],
        {"manual|codex|cached": AccountStatus(label="cached", state=AccountState.ACTIVE)},
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("live discovery should not block cached status")),
    )

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 12345
            calls.append((args, kwargs))

    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", FakePopen)

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "cached" in result.output
    assert "Background refresh started" in result.output
    assert "30-60s" in result.output
    assert "few seconds" not in result.output
    assert calls
    assert calls[0][0][0][-1] == "refresh-cache"


def test_status_recent_failed_cache_does_not_spawn_background_refresh(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr(
        "tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE",
        tmp_path / "status-cache-refresh.pid",
    )
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:44:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 45, tzinfo=timezone.utc),
    )
    _save_status_cache(
        [account],
        {"manual|codex|cached": AccountStatus(label="cached", state=AccountState.UNKNOWN, error="boom")},
        {"manual|codex|cached": "RuntimeError"},
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("refresh should be throttled")),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "cached" in result.output
    assert "Background refresh started" not in result.output
    assert "Background refresh still running" not in result.output


def test_status_background_refresh_lock_prevents_duplicate_process(monkeypatch, tmp_path):
    account = AccountConfig(label="cached", provider="codex")
    lock_file = tmp_path / "status-cache-refresh.pid"

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 45, tzinfo=timezone.utc),
    )
    _save_status_cache(
        [account],
        {"manual|codex|cached": AccountStatus(label="cached", state=AccountState.ACTIVE)},
    )
    lock_file.write_text("starting")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate refresh should not start")),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "cached" in result.output
    assert "Background refresh started" not in result.output
    assert "Background refresh still running" in result.output
    assert "30-60s" in result.output


def test_status_refresh_lock_releases_dead_pid_immediately(monkeypatch, tmp_path):
    lock_file = tmp_path / "status-cache-refresh.pid"
    lock_file.write_text("4242")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.status_cache._pid_is_running", lambda pid: False)

    assert _status_refresh_lock_info() is None
    assert not lock_file.exists()


def test_status_background_refresh_lock_oserror_fails_closed_with_diagnostic(
    monkeypatch,
    tmp_path,
):
    account = AccountConfig(label="cached", provider="codex")
    key = account_key_string(account)
    lock_file = tmp_path / "status-cache-refresh.pid"
    calls = []

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", tmp_path / "daemon.log")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._status_cache_now",
        lambda: datetime(2026, 5, 22, 8, 45, tzinfo=timezone.utc),
    )
    _save_status_cache(
        [account],
        {key: AccountStatus(label="cached", state=AccountState.ACTIVE)},
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr(
        "tokenkick.status_cache.os.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert calls == []
    assert "Background refresh lock could not be acquired" in result.output
    assert "read-only filesystem" in result.output


def test_background_status_refresh_invokes_cli_module(monkeypatch, tmp_path):
    calls = []

    class FakeProcess:
        pid = 4242

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    lock_file = tmp_path / "status-cache-refresh.pid"
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", tmp_path / "daemon.log")
    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.status_cache.sys.argv", ["/tmp/not-executable-tk"])
    monkeypatch.setattr("tokenkick.status_cache.sys.executable", "/opt/tokenkick/python")
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", fake_popen)

    assert _start_background_status_refresh() is True
    assert calls[0][0][0] == [
        "/opt/tokenkick/python",
        "-m",
        "tokenkick.cli",
        "refresh-cache",
    ]
    assert lock_file.read_text() == "4242"


def test_refresh_cache_command_updates_cache(monkeypatch, tmp_path):
    account = AccountConfig(label="live", provider="codex")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:05:00Z")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="live", state=AccountState.ACTIVE, used_percent=55.0)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["refresh-cache"])
    cached = _load_status_cache(Config(accounts=[account]))

    assert result.exit_code == 0
    assert cached is not None
    _accounts, statuses, _entries = cached
    assert statuses[0].used_percent == 55.0


def test_status_cache_uses_current_saved_visibility(monkeypatch, tmp_path):
    cached_account = AccountConfig(label="hidden", provider="codex", visible=True)
    saved_account = AccountConfig(label="hidden", provider="codex", visible=False)
    status = AccountStatus(label="hidden", state=AccountState.ACTIVE)

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_FILE", tmp_path / "status-cache.json")
    _save_status_cache([cached_account], {"manual|codex|hidden": status})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[saved_account]))

    default = CliRunner().invoke(cli, ["status", "--json-output"])
    show_all = CliRunner().invoke(cli, ["status", "--json-output", "--all"])

    assert default.exit_code == 0
    assert show_all.exit_code == 0
    assert _status_json_payload(default.output)["accounts"] == []
    assert [row["label"] for row in _status_json_accounts(show_all.output)] == ["hidden"]


def test_status_setup_hint_does_not_offer_notifications_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [],
            [
                AccountStatus(
                    label="dev",
                    state=AccountState.ACTIVE,
                    used_percent=15.0,
                )
            ],
            "Found 1 account via CodexBar: codex.",
        ),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "Run tk setup to save this config." in result.output
    assert "enable notifications" not in result.output


def test_load_account_status_pairs_merges_newly_discovered_accounts(monkeypatch):
    saved_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    gemini_account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="gemini@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            used_percent=10.0,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [saved_account, gemini_account],
            [
                AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=10.0),
                AccountStatus(label="gemini", state=AccountState.FRESH, used_percent=0.0),
            ],
            "Found 2 accounts via CodexBar: codex, gemini.",
        ),
    )

    _accounts, statuses, discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert discovered is False
    assert [status.label for status in statuses] == ["dev", "gemini (gemini)"]
    assert [account.label for account in new_accounts] == ["gemini (gemini)"]


def test_load_account_status_pairs_adds_spark_for_configured_codex_home(monkeypatch):
    saved_account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/home/example/.tokenkick/codex-homes/personal",
        identity_email="personal@example.test",
        auto_kick=True,
        session_auto_kick=True,
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda *_args, **_kwargs: ([], [], "No direct provider identity found."),
    )
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, _config=None: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            used_percent=26.0,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.codex_appserver_bucket_metadata",
        lambda _home: [
            {
                "key": "codex_bengalfox",
                "limit_id": "codex_bengalfox",
                "limit_name": "GPT-5.3-Codex-Spark",
                "display_name": "GPT-5.3-Codex-Spark quota",
            },
            {
                "key": "codex",
                "limit_id": "codex",
                "limit_name": None,
                "display_name": "main/default Codex quota",
            },
        ],
    )
    monkeypatch.setattr(
        "tokenkick.discovery._read_codex_appserver_ratelimits_for_account",
        lambda account, _home: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            codex_rate_limit_id=account.codex_rate_limit_id,
            codex_rate_limit_name=account.codex_rate_limit_name,
        ),
    )

    accounts, statuses, discovered, summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert discovered is False
    assert summary == "Configured 2 accounts after discovery: codex=1, codex-spark=1."
    assert [account.label for account in accounts] == [
        "codex (personal)",
        "codex-spark (personal)",
    ]
    spark = new_accounts[0]
    assert spark.codex_rate_limit_id == "codex_bengalfox"
    assert spark.auto_kick is False
    assert spark.weekly_auto_kick is False
    assert spark.session_auto_kick is False
    assert statuses[1].label == "codex-spark (personal)"


def test_load_account_status_pairs_does_not_duplicate_existing_spark_account(monkeypatch):
    main = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/home/example/.tokenkick/codex-homes/personal",
        identity_email="personal@example.test",
    )
    spark = replace(
        main,
        label="codex-spark (personal)",
        codex_rate_limit_id="codex_bengalfox",
        codex_rate_limit_name="GPT-5.3-Codex-Spark",
        kick_model="custom-spark",
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda *_args, **_kwargs: ([], [], "No direct provider identity found."),
    )
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, _config=None: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.discovery.codex_appserver_bucket_metadata",
        lambda _home: [
            {
                "key": "codex_bengalfox",
                "limit_id": "codex_bengalfox",
                "limit_name": "GPT-5.3-Codex-Spark",
                "display_name": "GPT-5.3-Codex-Spark quota",
            }
        ],
    )

    accounts, _statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[main, spark])
    )

    assert [account.label for account in accounts] == [
        "codex (personal)",
        "codex-spark (personal)",
    ]
    assert accounts[1].kick_model == "custom-spark"
    assert new_accounts == []


def test_load_account_status_pairs_does_not_downgrade_saved_claude_to_identity_only_status(
    monkeypatch,
):
    saved_account = AccountConfig(
        label="claude (work)",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
        identity_email="work@example.test",
        auto_kick=True,
        session_auto_kick=True,
    )
    discovered_account = replace(saved_account, label="work")
    identity_only_status = AccountStatus(
        label="work",
        state=AccountState.UNKNOWN,
        error=(
            "Claude identity was read from ~/.claude.json, but TokenKick could not read "
            "Claude CLI /usage directly."
        ),
        source_detail="claude-cli-usage",
    )
    cached_status = AccountStatus(
        label="claude (work)",
        state=AccountState.ACTIVE,
        used_percent=15.0,
        resets_in_seconds=6 * 24 * 3600,
        session_used_percent=12.0,
        session_resets_in_seconds=3600,
        source_detail="claude-cli-usage",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda *_args, **_kwargs: (
            [discovered_account],
            [identity_only_status],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache_entries",
        lambda: {
            account_key_string(saved_account): {
                "account": saved_account,
                "status": cached_status,
                "cached_at": "2026-06-06T10:00:00Z",
            }
        },
    )
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("identity-only discovery should use the cached saved status")
        ),
    )

    accounts, statuses, discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert discovered is False
    assert new_accounts == []
    assert accounts[0].label == "claude (work)"
    assert accounts[0].auto_kick is True
    assert statuses[0].label == "claude (work)"
    assert statuses[0].state == AccountState.ACTIVE
    assert statuses[0].error is None


def test_load_account_status_pairs_reuses_discovered_status_for_saved_codexbar_account(monkeypatch):
    saved_account = AccountConfig(
        label="custom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    discovered_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )

    def fail_fetch(_account):
        raise AssertionError("fetch_status should not run for matched CodexBar account")

    monkeypatch.setattr("tokenkick.cli.fetch_status", fail_fetch)
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered_account],
            [AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=15.0)],
            "Found 1 account via CodexBar: codex.",
        ),
    )

    _accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert new_accounts == []
    assert statuses[0].label == "custom"
    assert statuses[0].used_percent == 15.0


def test_load_account_status_pairs_uses_configured_fallback_when_discovery_is_unknown(monkeypatch):
    saved_account = AccountConfig(
        label="custom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    discovered_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            used_percent=15.0,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered_account],
            [AccountStatus(label="dev", state=AccountState.UNKNOWN, error="Provider failed")],
            "Found 1 account via CodexBar: codex.",
        ),
    )

    _accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert new_accounts == []
    assert statuses[0].label == "custom"
    assert statuses[0].used_percent == 15.0


def test_load_account_status_pairs_reuses_codexbar_result_for_saved_fallbacks(monkeypatch, tmp_path):
    saved_accounts = [
        AccountConfig(
            label="claude",
            provider="claude",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="claude",
        ),
        AccountConfig(
            label="work",
            provider="gemini",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="gemini",
            codexbar_account="work@example.test",
        ),
    ]
    calls = []
    payload = [
        {
            "provider": "claude",
            "usage": {
                "primary": {
                    "usedPercent": 72,
                    "windowMinutes": 300,
                    "resetsAt": "2026-05-22T10:30:00Z",
                }
            },
        },
        {
            "provider": "gemini",
            "usage": {
                "accountEmail": "work@example.test",
                "primary": {
                    "usedPercent": 1,
                    "windowMinutes": 1440,
                    "resetsAt": "2026-05-23T10:30:00Z",
                },
            },
        },
    ]

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: ([], [], "No accounts found."),
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

    accounts, statuses, _discovered, _summary, _new_accounts = _load_account_status_pairs(
        Config(accounts=saved_accounts)
    )

    assert [account.label for account in accounts] == ["claude", "work"]
    assert [status.used_percent for status in statuses] == [72.0, 1.0]
    assert calls == [["codexbar", "--format", "json", "--pretty"]]


def test_status_prints_new_account_note_for_existing_config(monkeypatch):
    saved_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )
    gemini_account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="gemini@example.test",
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[saved_account]))
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [saved_account, gemini_account],
            [
                AccountStatus(label="dev", state=AccountState.ACTIVE),
                AccountStatus(label="gemini", state=AccountState.FRESH),
            ],
            "Found 2 accounts via CodexBar: codex, gemini.",
        ),
    )

    result = CliRunner().invoke(cli, ["status"])

    assert result.exit_code == 0
    assert "gemini" in result.output
    assert "1 new account discovered: gemini (gemini). Run tk setup to save." in result.output


def test_load_account_status_pairs_disambiguates_new_provider_label_against_saved_config(monkeypatch):
    saved_account = AccountConfig(
        label="shared",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="shared@example.test",
    )
    new_account = AccountConfig(
        label="shared",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/shared/sessions",
        codexbar_account="shared@example.test",
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[saved_account]))
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [saved_account, new_account],
            [
                AccountStatus(label="shared", state=AccountState.ACTIVE),
                AccountStatus(label="shared", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: codex, gemini.",
        ),
    )

    _accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[saved_account])
    )

    assert [status.label for status in statuses] == ["shared", "codex (shared)"]
    assert [account.label for account in new_accounts] == ["codex (shared)"]


def test_load_account_status_pairs_normalizes_stale_suffixed_saved_provider_label(monkeypatch):
    codex_account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/work/sessions",
        codexbar_account="work@example.test",
    )
    gemini_account = AccountConfig(
        label="work-2",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="work@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(label=account.label, state=AccountState.ACTIVE),
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [codex_account, gemini_account],
            [
                AccountStatus(label="work", state=AccountState.ACTIVE),
                AccountStatus(label="work-2", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: codex, gemini.",
        ),
    )

    _accounts, statuses, _discovered, _summary, new_accounts = _load_account_status_pairs(
        Config(accounts=[codex_account, gemini_account])
    )

    assert new_accounts == []
    assert [status.label for status in statuses] == [
        "work",
        "work-2",
    ]


def test_codex_accounts_filters_non_codex_providers():
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="claude", provider="claude"),
        AccountConfig(label="gemini", provider="gemini"),
    ]

    assert [account.label for account in _codex_accounts(accounts)] == ["codex"]


def test_kickable_window_targets_refetches_and_filters_state(monkeypatch):
    accounts = [
        AccountConfig(label="fresh", provider="codex", auto_kick=True),
        AccountConfig(label="fresh-claude", provider="claude", auto_kick=True),
        AccountConfig(label="active", provider="codex", auto_kick=True),
        AccountConfig(label="gemini", provider="gemini"),
    ]

    def fake_fetch(account):
        if account.label in {"fresh", "fresh-claude"}:
            return AccountStatus(
                label=account.label,
                state=AccountState.FRESH,
                used_percent=0.0,
                resets_in_seconds=3600,
                window_minutes=10080,
            )
        return AccountStatus(label=account.label, state=AccountState.ACTIVE, used_percent=10.0)

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, _deferred = _kickable_window_targets(accounts)

    assert [account.label for account, _status in targets] == ["fresh", "fresh-claude"]


def test_gemini_never_selected_as_auto_kick_candidate(monkeypatch):
    accounts = [AccountConfig(label="gemini", provider="gemini", auto_kick=True, session_auto_kick=True)]
    statuses = {
        account_key_string(accounts[0]): AccountStatus(
            label="gemini",
            state=AccountState.FRESH,
            session_used_percent=0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        )
    }
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(accounts, statuses)

    assert targets == []
    assert deferred == []


def test_codexbar_fallback_not_selected_as_auto_kick_candidate(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        source_detail="codexbar-history",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_run_auto_blocks_codexbar_fallback_auto_kick():
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        source_detail="codexbar-history",
    )

    state, payload, kicked = _run_evaluate_account(
        account,
        status,
        Config(accounts=[account]),
        dry_run=False,
        history=[],
        pending={},
        now=datetime.now(timezone.utc),
    )

    assert state == "skipped"
    assert not kicked
    assert payload["reason_code"] == "codexbar_fallback_auto_kick_blocked"


def test_kickable_window_targets_defers_until_session_cooldown_expires(monkeypatch):
    accounts = [
        AccountConfig(label="deferred", provider="codex", auto_kick=True),
        AccountConfig(label="ready", provider="codex", auto_kick=True),
        AccountConfig(label="unknown-session", provider="claude", auto_kick=True),
    ]

    def fake_fetch(account):
        if account.label == "deferred":
            return AccountStatus(
                label=account.label,
                state=AccountState.FRESH,
                used_percent=0.0,
                window_minutes=10080,
                session_used_percent=1.0,
                session_resets_in_seconds=8040,
                session_window_minutes=300,
            )
        if account.label == "ready":
            return AccountStatus(
                label=account.label,
                state=AccountState.FRESH,
                used_percent=0.0,
                window_minutes=10080,
                session_used_percent=0.0,
                session_resets_in_seconds=8040,
                session_window_minutes=300,
            )
        return AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            window_minutes=10080,
        )

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets(accounts)

    assert [account.label for account, _status in targets] == [
        "deferred",
        "ready",
        "unknown-session",
    ]
    assert deferred == []


def test_saved_account_status_snapshot_fetches_each_account_and_reuses_for_targets(monkeypatch):
    accounts = [
        AccountConfig(label="fresh", provider="codex", auto_kick=True),
        AccountConfig(label="active", provider="claude", auto_kick=True),
    ]
    fetched = []

    def fake_fetch(account):
        fetched.append(account.label)
        if account.label == "fresh":
            return AccountStatus(
                label=account.label,
                state=AccountState.FRESH,
                used_percent=0.0,
                resets_in_seconds=3600,
                window_minutes=300,
            )
        return AccountStatus(label=account.label, state=AccountState.ACTIVE)

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    statuses_by_key, failures_by_key = _load_saved_account_status_snapshot(accounts)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: (_ for _ in ()).throw(AssertionError("target scan should reuse snapshot")),
    )

    targets, deferred = _kickable_window_targets(accounts, statuses_by_key)

    assert fetched == ["fresh", "active"]
    assert failures_by_key == {}
    assert [account.label for account, _status in targets] == ["fresh"]
    assert deferred == []


def test_saved_account_status_snapshot_records_fetch_exceptions(monkeypatch):
    account = AccountConfig(label="boom", provider="codex")

    def fail_fetch(_account):
        raise TimeoutError("provider hung")

    monkeypatch.setattr("tokenkick.cli.fetch_status", fail_fetch)

    statuses_by_key, failures_by_key = _load_saved_account_status_snapshot([account])

    assert statuses_by_key["manual|codex|boom"].state == AccountState.UNKNOWN
    assert statuses_by_key["manual|codex|boom"].error == "provider hung"
    assert failures_by_key == {"manual|codex|boom": "TimeoutError"}


def test_kickable_targets_allows_persistent_phantom_session(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="phantom@example.test",
    )
    current_time = [0.0]

    def fake_fetch(_account):
        return AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
        )

    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    for timestamp in (0, 299):
        current_time[0] = timestamp
        targets, deferred = _kickable_window_targets([account])
        assert [target.label for target, _status in targets] == ["phantom"]
        assert deferred == []

    current_time[0] = 300
    targets, deferred = _kickable_window_targets([account])

    assert [target.label for target, _status in targets] == ["phantom"]
    assert deferred == []


def test_daemon_target_selection_holds_phantom_for_verified_recovery(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 100.0)

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
        manage_phantom_recovery=True,
    )

    assert targets == []
    assert deferred == []


def test_daemon_target_scan_reports_phantom_observing_when_recovery_holds_target(
    monkeypatch,
    tmp_path,
):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    logged = []

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 100.0)
    monkeypatch.setattr(
        "tokenkick.cli._daemon_log",
        lambda event, **fields: logged.append((event, fields)),
    )

    _daemon_log_target_scan(
        [account],
        {account_key_string(account): status},
        manage_phantom_recovery=True,
    )

    assert logged == [
        (
            "target_scan",
            {
                "account": "phantom",
                "state": "active",
                "reason": "phantom_observing",
                "kick_type": None,
                "cooldown_remaining": None,
                "session_resets_in": 17940,
                "session_used": 1.0,
                "window_anchor_state": None,
            },
        )
    ]


def test_daemon_target_selection_defers_active_phantom_recovery(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    recovery_file = tmp_path / "recovery.json"
    recovery_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "attempts": 1,
                    "last_attempt_at": 100.0,
                    "last_seen_at": 100.0,
                    "status": "recovering",
                    "updated_at": 100.0,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 120.0)

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
        manage_phantom_recovery=True,
    )

    assert targets == []
    assert len(deferred) == 1
    assert deferred[0][0] == account
    assert deferred[0][1] == status
    assert deferred[0][2] == 25


def test_kick_all_dry_run_does_not_record_phantom_session(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="phantom@example.test",
    )
    phantom_file = tmp_path / "phantom.json"

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
        ),
    )

    result = CliRunner().invoke(cli, ["kick", "--all", "--dry-run"])

    assert result.exit_code == 0
    assert not phantom_file.exists()


@pytest.mark.parametrize(
    ("first_seen_at", "session_resets", "expected_ready"),
    [
        (None, 17940, True),
        (100.0, 17940, True),
        (0.0, 17940, True),
        (0.0, 16800, True),
    ],
)
def test_phantom_status_actionability_matches_kick_eligibility(
    monkeypatch,
    tmp_path,
    first_seen_at,
    session_resets,
    expected_ready,
):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="phantom@example.test",
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=session_resets,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"
    if first_seen_at is not None:
        phantom_file.write_text(
            json.dumps(
                {
                    account_key_string(account): {
                        "first_seen_at": first_seen_at,
                        "last_seen_at": first_seen_at,
                        "observations": 1,
                        "session_resets_in_seconds": session_resets,
                        "session_used_percent": 1.0,
                        "weekly_used_percent": 0.0,
                    }
                }
            )
        )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1200.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    eligibility = _kick_eligibility(account, status, "codex", history=[])
    actionable = _status_actionable_now(
        account,
        status,
        "codex",
        history=[],
        pending_kick=None,
    )
    action = _status_action(status, {"phantom": "codex"}, account)

    assert eligibility.kickable is expected_ready
    assert actionable is expected_ready
    assert action == ("Kick now" if expected_ready else "Session cooling down")


def test_phantom_session_requires_basically_full_session_reset(monkeypatch, tmp_path):
    account = AccountConfig(
        label="real-usage",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="real@example.test",
    )
    current_time = [0.0]

    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", tmp_path / "phantom.json")
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=16800,
            session_window_minutes=300,
        ),
    )

    for timestamp in (0, 400, 800, 1200):
        current_time[0] = timestamp
        targets, deferred = _kickable_window_targets([account])

    assert [target.label for target, _status in targets] == ["real-usage"]
    assert deferred == []


def test_phantom_session_allows_rounded_one_percent_values(monkeypatch, tmp_path):
    account = AccountConfig(
        label="rounded-phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="rounded@example.test",
    )
    status = AccountStatus(
        label="rounded-phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.6,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 1,
                    "session_resets_in_seconds": 17940,
                    "session_used_percent": 1.6,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1200.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is True


def test_phantom_session_detects_stuck_countdown_before_full_wait(monkeypatch, tmp_path):
    account = AccountConfig(
        label="stuck-phantom",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="stuck@example.test",
    )
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 18000,
                    "session_resets_in_seconds": 18000,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )
    status = AccountStatus(
        label="stuck-phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17980,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 5 * 60.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is True


def test_phantom_session_keeps_real_countdown_cooling(monkeypatch, tmp_path):
    account = AccountConfig(
        label="real-countdown",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="real-countdown@example.test",
    )
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "first_seen_at": 0.0,
                    "last_seen_at": 0.0,
                    "observations": 1,
                    "first_session_resets_in_seconds": 18000,
                    "session_resets_in_seconds": 18000,
                    "session_used_percent": 1.0,
                    "weekly_used_percent": 0.0,
                }
            }
        )
    )
    status = AccountStatus(
        label="real-countdown",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17700,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 5 * 60.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is True
    assert eligibility.cooldown_remaining is None


def test_phantom_session_observations_prune_stale_entries(monkeypatch, tmp_path):
    account = AccountConfig(
        label="fresh-entry",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=True,
        codexbar_account="fresh@example.test",
    )
    stale_key = "account|codex|stale@example.test"
    fresh_key = "account|codex|fresh@example.test"
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                    stale_key: {"first_seen_at": 0, "last_seen_at": 1000},
                    fresh_key: {"first_seen_at": 200_000, "last_seen_at": 200_000},
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 200_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda _account: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    _kickable_window_targets([account])

    data = json.loads(phantom_file.read_text())
    assert stale_key not in data
    assert fresh_key in data


def test_phantom_session_observations_prune_removed_accounts(monkeypatch, tmp_path):
    active = AccountConfig(
        label="active",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_account="active@example.test",
    )
    removed_key = "account|codex|removed@example.test"
    active_key = "account|codex|active@example.test"
    phantom_file = tmp_path / "phantom.json"
    phantom_file.write_text(
        json.dumps(
            {
                removed_key: {"first_seen_at": 10_000, "last_seen_at": 10_000},
                active_key: {"first_seen_at": 10_000, "last_seen_at": 10_000},
            }
        )
    )
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)

    _prune_phantom_session_observations_for_accounts([active])

    data = json.loads(phantom_file.read_text())
    assert removed_key not in data
    assert active_key in data


def test_kickable_window_targets_skips_auto_kick_disabled_accounts(monkeypatch):
    accounts = [
        AccountConfig(label="enabled", provider="codex", auto_kick=True),
        AccountConfig(label="enabled-claude", provider="claude", auto_kick=True),
        AccountConfig(label="disabled", provider="codex", auto_kick=False),
        AccountConfig(label="disabled-claude", provider="claude", auto_kick=False),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=3600,
            window_minutes=10080,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, _deferred = _kickable_window_targets(accounts)

    assert [account.label for account, _status in targets] == ["enabled", "enabled-claude"]


def test_kickable_window_targets_skips_hidden_accounts(monkeypatch):
    accounts = [
        AccountConfig(label="visible", provider="codex", auto_kick=True, visible=True),
        AccountConfig(label="hidden", provider="codex", auto_kick=True, visible=False),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=3600,
            window_minutes=10080,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, _deferred = _kickable_window_targets(accounts)

    assert [account.label for account, _status in targets] == ["visible"]


def test_status_action_shows_session_cooldown_for_fresh_kickable_accounts():
    status = AccountStatus(
        label="work",
        state=AccountState.FRESH,
        used_percent=0.0,
        session_used_percent=1.0,
        session_resets_in_seconds=8040,
        session_window_minutes=300,
    )

    assert _status_action(status, {"work": "codex"}) == "Kick now"


def test_status_action_shows_ambiguous_phantom_backoff(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account).startswith("Retry after ")


def test_status_action_shows_phantom_unresolved_after_repeated_attempts(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        ),
        KickEvent(
            label="work",
            timestamp=1_779_003_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        ),
    ]

    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account) == "Phantom unresolved"
    assert _kick_eligibility(account, status, "codex", history=history).reason == "phantom_unresolved"


def test_status_action_applies_old_phantom_history_to_active_zero_usage_status(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        ),
        KickEvent(
            label="work",
            timestamp=1_779_003_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        ),
    ]

    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account) == "Phantom unresolved"
    assert _kick_eligibility(account, status, "codex", history=history).reason == "phantom_unresolved"


def test_status_action_counts_unconfirmed_wake_as_phantom_backoff(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=False,
            kind="wake",
            error="Provider still reports a tiny phantom session after the kick attempt",
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account).startswith("Retry after ")
    assert _kick_eligibility(account, status, "codex", history=history).reason == "phantom_backoff"


def test_status_action_observes_unconfirmed_token_bearing_phantom_attempt(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=False,
            kind="wake",
            error="Provider still reports a tiny phantom session after the kick attempt",
            input_tokens=23236,
            output_tokens=87,
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account) == "Use if needed"
    assert _kick_eligibility(account, status, "codex", history=history).kickable is False


def test_status_action_retries_confirmed_provider_unchanged_phantom(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=23236,
            output_tokens=87,
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account) == "Session anchored"
    assert _kick_eligibility(account, status, "codex", history=history).reason == (
        "already_session_kicked"
    )


def test_confirmed_provider_unchanged_phantom_retries_after_short_backoff(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=23236,
            output_tokens=87,
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_000_700.0)

    eligibility = _kick_eligibility(account, status, "codex", history=history)

    assert eligibility.kickable is False
    assert eligibility.reason == "already_session_kicked"


def test_confirmed_provider_unchanged_phantom_stops_after_capped_retries(monkeypatch):
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="work",
            timestamp=1_779_000_000.0 + offset,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
            input_tokens=23236,
            output_tokens=87,
        )
        for offset in (0, 700, 1400)
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1_779_002_100.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)

    assert _status_action(status, {"work": "codex"}, account) == "Session anchored"
    assert _kick_eligibility(account, status, "codex", history=history).reason == (
        "already_session_kicked"
    )


def test_active_phantom_without_history_observes_before_session_kick():
    account = AccountConfig(label="work", provider="codex")
    status = AccountStatus(
        label="work",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is False
    assert eligibility.reason == "active"


def test_active_phantom_with_partial_weekly_usage_observes_before_session_kick():
    account = AccountConfig(label="codex (primaryaccount)", provider="codex")
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is False
    assert eligibility.reason == "active"


def test_active_codex_zero_session_full_countdown_is_session_kickable(monkeypatch):
    account = AccountConfig(label="secondary", provider="codex")
    status = AccountStatus(
        label="secondary",
        state=AccountState.ACTIVE,
        used_percent=65.0,
        window_minutes=10080,
        window_anchor_state="anchored",
        session_used_percent=0.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    eligibility = _kick_eligibility(account, status, "codex", history=[])

    assert eligibility.kickable is True
    assert eligibility.kick_type == "session"
    assert _status_action(status, {"secondary": "codex"}, account) == "Kick session"


def test_was_kicked_in_current_window_detects_prior_success(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(label="fresh", provider="codex")
    status = AccountStatus(
        label="fresh",
        state=AccountState.FRESH,
        resets_in_seconds=3600,
        window_minutes=300,
    )
    history = [KickEvent(label="fresh", timestamp=9_000.0, success=True)]

    assert _was_kicked_in_current_window(account, status, history) is True


def test_was_kicked_in_current_window_ignores_unconfirmed_attempts(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(label="fresh", provider="codex")
    status = AccountStatus(
        label="fresh",
        state=AccountState.FRESH,
        resets_in_seconds=3600,
        window_minutes=300,
    )
    history = [
        KickEvent(label="fresh", timestamp=9_000.0, success=True, confirmed=False, kind="probe")
    ]

    assert _was_kicked_in_current_window(account, status, history) is False


def test_was_kicked_in_current_window_ignores_session_kicks(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(label="fresh", provider="codex")
    status = AccountStatus(
        label="fresh",
        state=AccountState.FRESH,
        resets_in_seconds=3600,
        window_minutes=300,
    )
    history = [
        KickEvent(
            label="fresh",
            timestamp=9_000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]

    assert _was_kicked_in_current_window(account, status, history) is False


def test_recent_session_kick_counts_when_codex_session_reset_slides(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(label="phantom", provider="codex")
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=17_995,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="phantom",
            timestamp=9_940.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]

    assert _was_kicked_in_current_session_window(account, status, history) is True


def test_unconfirmed_phantom_session_event_does_not_dedupe_real_session_kick(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    history = [
        KickEvent(
            label="codex",
            timestamp=9_940.0,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="phantom",
        )
    ]

    eligibility = _kick_eligibility(account, status, "codex", history=history)

    assert eligibility.kickable is True
    assert eligibility.kick_type == "session"


def test_previous_session_kick_does_not_block_new_near_full_session(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 20_000.0)
    account = AccountConfig(label="codex (work)", provider="codex")
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=17_995,
        session_resets_at=37_995.0,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="codex (work)",
            timestamp=3_200.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]

    assert _was_kicked_in_current_session_window(account, status, history) is False


def test_kick_auto_only_kicks_fresh_codex_accounts(monkeypatch):
    kicked = []
    notified = []
    accounts = [
        AccountConfig(label="fresh", provider="codex"),
        AccountConfig(label="active", provider="codex"),
        AccountConfig(label="gemini", provider="gemini"),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: (
            [
                (
                    accounts[0],
                    AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0),
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, notifications: notified.append(event.label),
    )

    result = CliRunner().invoke(cli, ["kick", "--auto"])

    assert result.exit_code == 0
    assert kicked == ["fresh"]
    assert notified == ["fresh"]


def test_multi_codex_auto_kick_kicks_all_eligible_accounts_with_each_codex_home(
    monkeypatch,
    tmp_path,
):
    homes = [tmp_path / "codex-a", tmp_path / "codex-b"]
    accounts = [
        AccountConfig(
            label="codex-a",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(homes[0]),
        ),
        AccountConfig(
            label="codex-b",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(homes[1]),
        ),
    ]
    statuses_by_key = {
        account_key_string(account): AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=0.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )
        for account in accounts
    }
    observed_homes = []
    observed_stdin = []
    events = []

    def fake_run(command, **kwargs):
        if command == ["git", "init"]:
            (kwargs["cwd"] / ".git").mkdir()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        observed_homes.append(kwargs["env"]["CODEX_HOME"])
        observed_stdin.append(kwargs["stdin"])
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"agent_message","message":"TokenKick anchor probe completed."}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: events.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        accounts,
        statuses_by_key,
        Config(accounts=accounts),
    )
    kicked, deferred_count = _kick_all_enabled_accounts(
        accounts,
        Config(accounts=accounts),
        targets=targets,
        deferred=deferred,
    )

    assert kicked == 2
    assert deferred_count == 0
    assert observed_homes == [str(homes[0]), str(homes[1])]
    assert observed_stdin == [subprocess.DEVNULL, subprocess.DEVNULL]
    assert [event.label for event in events] == ["codex-a", "codex-b"]
    assert all(event.success for event in events)


def test_successful_weekly_kick_marks_status_cache_entry_stale(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    key = account_key_string(account)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=25.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
    )
    _save_status_cache([account], {key: status})
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    cleared = []
    monkeypatch.setattr(
        "tokenkick.cli._clear_phantom_session_observation",
        lambda candidate: cleared.append(("observation", candidate.label)),
    )
    monkeypatch.setattr(
        "tokenkick.cli._clear_phantom_recovery_state",
        lambda candidate: cleared.append(("recovery", candidate.label)),
    )

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        status,
        send_notification=False,
        kick_type="kick",
    )
    cached = _load_status_cache(Config(accounts=[account]))

    assert event.success is True
    assert cached is not None
    _accounts, statuses, entries = cached
    assert statuses[0].stale is True
    assert statuses[0].stale_seconds == 0
    assert entries[key]["needs_refresh"] is True
    assert cleared == [("observation", "codex"), ("recovery", "codex")]


def test_kick_all_staggers_codex_targets_when_batch_state_passed(monkeypatch):
    accounts = [
        AccountConfig(label="codex-a", provider="codex", auto_kick=True),
        AccountConfig(label="codex-b", provider="codex", auto_kick=True),
    ]
    statuses = [
        AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=0.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        )
        for account in accounts
    ]
    current_time = [100.0]
    sleeps = []
    kicked = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr("tokenkick.cli.time.monotonic", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.remove_pending_kick", lambda _account: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )

    kicked_count, deferred_count = _kick_all_enabled_accounts(
        accounts,
        Config(accounts=accounts),
        targets=list(zip(accounts, statuses, strict=False)),
        deferred=[],
        stagger_state=KickStaggerState(),
    )

    assert kicked_count == 2
    assert deferred_count == 0
    assert kicked == ["codex-a", "codex-b"]
    assert sleeps == [CODEX_KICK_STAGGER_SECONDS]


def test_codex_kick_surfaces_json_error_from_stdout(monkeypatch):
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps(
                {
                    "type": "error",
                    "message": json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "type": "invalid_request_error",
                                "message": "The model is not supported.",
                            },
                        }
                    ),
                }
            ),
        ]
    )

    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=stdout,
            stderr="Reading additional input from stdin...\nwarning noise",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(
        AccountConfig(label="codex", provider="codex", kick_model="bad-model"),
        record=False,
    )

    assert not event.success
    assert event.error == "codex exited 1: The model is not supported."


def test_active_codex_account_does_not_block_fresh_codex_auto_kick(monkeypatch, tmp_path):
    active = AccountConfig(
        label="active",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        provider_home=str(tmp_path / "active"),
    )
    fresh = AccountConfig(
        label="fresh",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        provider_home=str(tmp_path / "fresh"),
    )
    statuses_by_key = {
        account_key_string(active): AccountStatus(
            label="active",
            state=AccountState.ACTIVE,
            used_percent=5.0,
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        ),
        account_key_string(fresh): AccountStatus(
            label="fresh",
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=0.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        ),
    }
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [active, fresh],
        statuses_by_key,
        Config(accounts=[active, fresh]),
    )

    assert [account.label for account, _status in targets] == ["fresh"]
    assert deferred == []


def test_multi_codex_kick_exception_on_one_account_does_not_abort_others(
    monkeypatch,
    tmp_path,
):
    accounts = [
        AccountConfig(
            label="codex-a",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(tmp_path / "codex-a"),
        ),
        AccountConfig(
            label="codex-b",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(tmp_path / "codex-b"),
        ),
    ]
    status = AccountStatus(
        label="ready",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
    )
    kicked = []

    def fake_kick(account, **_kwargs):
        if account.label == "codex-a":
            raise RuntimeError("transport failed")
        kicked.append(account.label)
        return KickEvent(label=account.label, success=True)

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    attempted, deferred = _kick_all_enabled_accounts(
        accounts,
        Config(accounts=accounts),
        targets=[
            (accounts[0], replace(status, label="codex-a")),
            (accounts[1], replace(status, label="codex-b")),
        ],
        deferred=[],
        daemon_log=True,
    )

    assert attempted == 2
    assert deferred == 0
    assert kicked == ["codex-b"]


def test_multi_codex_phantom_observations_are_keyed_per_codex_home(monkeypatch, tmp_path):
    accounts = [
        AccountConfig(
            label="codex",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(tmp_path / "home-a"),
        ),
        AccountConfig(
            label="codex",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            provider_home=str(tmp_path / "home-b"),
        ),
    ]
    statuses_by_key = {
        account_key_string(account): AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=17940,
            session_window_minutes=300,
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )
        for account in accounts
    }
    phantom_file = tmp_path / "phantom.json"
    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 1000.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        accounts,
        statuses_by_key,
        Config(accounts=accounts),
    )

    data = json.loads(phantom_file.read_text())
    assert [account.label for account, _status in targets] == ["codex", "codex"]
    assert deferred == []
    assert sorted(data) == sorted(account_key_string(account) for account in accounts)


def test_kick_label_rejects_gemini_monitor_only(monkeypatch):
    accounts = [AccountConfig(label="gemini", provider="gemini")]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda *_args, **_kwargs: pytest.fail("Gemini kick must not fetch or run"),
    )

    result = CliRunner().invoke(cli, ["kick", "gemini"])

    assert result.exit_code == 1
    assert "Gemini is monitor-only" in result.output
    assert "daily reset at midnight Pacific" in result.output


def test_kick_label_rejects_antigravity_monitor_only(monkeypatch):
    accounts = [AccountConfig(label="antigravity", provider="antigravity")]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda *_args, **_kwargs: pytest.fail("Antigravity kick must not fetch or run"),
    )

    result = CliRunner().invoke(cli, ["kick", "antigravity"])

    assert result.exit_code == 1
    assert "Antigravity is monitor-only" in result.output
    assert "Kicking is disabled" in result.output


def test_manual_kick_allows_active_weekly_when_session_ready(monkeypatch):
    account = AccountConfig(label="codex (reserve)", provider="codex")
    moved_status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=32.0,
        session_resets_in_seconds=17_940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    recorded = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.ACTIVE,
            used_percent=1.0,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: KickEvent(label=loaded.label, success=True),
    )
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: moved_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "codex (reserve)"])

    assert result.exit_code == 0
    assert len(recorded) == 1
    assert recorded[0].label == "codex (reserve)"
    assert recorded[0].kind == "session"
    assert recorded[0].kick_type == "session"


def test_manual_kick_waits_when_active_weekly_session_cooling_down(monkeypatch):
    account = AccountConfig(label="codex (reserve)", provider="codex")

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.ACTIVE,
            used_percent=1.0,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=7200,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("session cooldown should block manual kick"),
    )

    result = CliRunner().invoke(cli, ["kick", "codex (reserve)"])

    assert result.exit_code == 0
    assert "session resets in 2h" in result.output


def test_manual_kick_force_bypasses_active_session_cooldown(monkeypatch):
    account = AccountConfig(label="codex (reserve)", provider="codex")
    moved_status = AccountStatus(
        label="codex (reserve)",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        window_minutes=10080,
        session_used_percent=68.0,
        session_resets_in_seconds=17_940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    recorded = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.ACTIVE,
            used_percent=11.0,
            window_minutes=10080,
            session_used_percent=68.0,
            session_resets_in_seconds=16_895,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: KickEvent(label=loaded.label, success=True),
    )
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: moved_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    result = CliRunner().invoke(cli, ["kick", "codex (reserve)", "--force"])

    assert result.exit_code == 0
    assert "Waiting to kick" not in result.output
    assert len(recorded) == 1
    assert recorded[0].label == "codex (reserve)"
    assert recorded[0].kind == "session"
    assert recorded[0].kick_type == "session"


def test_manual_session_kick_preserves_stale_confirmation(monkeypatch):
    account = AccountConfig(label="codex (reserve)", provider="codex")
    recorded = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli._confirm_prompt", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.ACTIVE,
            used_percent=1.0,
            window_minutes=10080,
            session_used_percent=1.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
            stale=True,
            stale_seconds=1800,
            source_detail="codexbar-history",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: KickEvent(label=loaded.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "codex (reserve)"])

    assert result.exit_code == 0
    assert recorded[0].kind == "session"


def test_session_auto_kick_opt_out_skips_session_kick(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True, session_auto_kick=False)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_session_auto_kick_targets_active_weekly_session_ready(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True, session_auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == [(account, status)]
    assert deferred == []


def test_weekly_fresh_wins_over_session_auto_kick(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == [(account, status)]
    assert deferred == []


def test_session_auto_kick_does_not_authorize_fresh_weekly_kick(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=False,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_weekly_auto_kick_opt_out_skips_fresh_weekly_kick(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=True,
        weekly_auto_kick=False,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_weekly_auto_kick_opt_out_still_allows_session_kick(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=True,
        weekly_auto_kick=False,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == [(account, status)]
    assert deferred == []


def test_master_auto_kick_disabled_skips_weekly_and_session_kicks(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=False,
        weekly_auto_kick=True,
        session_auto_kick=True,
    )
    fresh = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    session_ready = replace(
        fresh,
        state=AccountState.ACTIVE,
        used_percent=1.0,
        session_used_percent=1.0,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    weekly_targets, weekly_deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): fresh},
    )
    session_targets, session_deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): session_ready},
    )

    assert weekly_targets == []
    assert weekly_deferred == []
    assert session_targets == []
    assert session_deferred == []


def test_fresh_weekly_kick_ignores_active_session_cooldown(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=25.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )
    bucket, payload, failed = _run_evaluate_account(
        account,
        status,
        Config(accounts=[account]),
        dry_run=True,
        history=[],
        pending={},
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    assert targets == [(account, status)]
    assert deferred == []
    assert bucket == "kicked"
    assert payload["reason"] == "weekly window would be anchored"
    assert failed is False


def test_future_orchestrated_pending_suppresses_weekly_auto_kick(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        created_at=to_utc_iso(datetime.now(timezone.utc)),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        work_end=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {account_key_string(account): pending})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_future_orchestrated_pending_suppresses_session_auto_kick(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", session_auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        created_at=to_utc_iso(datetime.now(timezone.utc)),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        work_end=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {account_key_string(account): pending})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_future_smart_schedule_pending_suppresses_auto_kick(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        created_at=to_utc_iso(datetime.now(timezone.utc)),
        reason=ScheduleReason.SINGLE_WINDOW.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=1)),
        work_end=to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {account_key_string(account): pending})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_due_orchestrated_pending_does_not_suppress_auto_candidate(monkeypatch):
    now = datetime.now(timezone.utc)
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now - timedelta(minutes=1)),
        created_at=to_utc_iso(now - timedelta(hours=2)),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now - timedelta(minutes=1)),
        work_end=to_utc_iso(now + timedelta(hours=2)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {account_key_string(account): pending})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == [(account, status)]
    assert deferred == []


def test_given_up_pending_does_not_suppress_auto_candidate(monkeypatch):
    now = datetime.now(timezone.utc)
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now + timedelta(hours=1)),
        created_at=to_utc_iso(now - timedelta(hours=2)),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now + timedelta(hours=1)),
        work_end=to_utc_iso(now + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
        attempt_count=4,
        gave_up_at=to_utc_iso(now),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {account_key_string(account): pending})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == [(account, status)]
    assert deferred == []


def test_pending_retry_backoff_suppresses_run_auto_kick():
    now = datetime(2026, 5, 22, 17, 3, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis=SchedulingWindowBasis.SESSION.value,
        attempt_count=1,
        next_retry_at="2026-05-22T17:05:00Z",
    )

    bucket, payload, failed = _run_evaluate_account(
        account,
        status,
        Config(accounts=[account]),
        dry_run=False,
        history=[],
        pending={account_key_string(account): pending},
        now=now,
    )

    assert bucket == "skipped"
    assert "scheduled for" in payload["reason"]
    assert failed is False


def test_pending_long_kick_blocks_duplicate_session_target(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    account = AccountConfig(label="codex", provider="codex", session_auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at="2099-05-22T19:00:00Z",
        created_at="2099-05-22T17:00:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2099-05-22T19:00:00Z",
        work_end="2099-05-22T23:00:00Z",
        window_basis="primary",
    )
    scheduling_mod.save_pending_kicks({account_key_string(account): pending})
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_session_auto_kick_skips_already_kicked_session_window(monkeypatch):
    account = AccountConfig(label="codex", provider="codex", session_auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    monkeypatch.setattr(
        "tokenkick.cli.load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=datetime.now(timezone.utc).timestamp() - 60,
                success=True,
                confirmed=True,
                kind="session",
                kick_type="session",
            )
        ],
    )

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_session_auto_kick_suppresses_recent_boundary_duplicate(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    account = AccountConfig(label="codex", provider="codex", session_auto_kick=True)
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17990,
        session_resets_at=now + 17990,
        session_window_minutes=300,
    )
    monkeypatch.setattr(
        "tokenkick.cli.load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=now - 600,
                success=True,
                confirmed=True,
                kind="session",
                kick_type="session",
            )
        ],
    )

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
    )

    assert targets == []
    assert deferred == []


def test_status_action_shows_session_ready_for_active_weekly():
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )

    assert _status_action(status, {"codex": "codex"}, account) == "Kick session"


def test_kick_all_only_kicks_fresh_enabled_accounts(monkeypatch):
    kicked = []
    accounts = [
        AccountConfig(label="fresh", provider="codex"),
        AccountConfig(label="active", provider="codex"),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: (
            [
                (
                    accounts[0],
                    AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0),
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)

    result = CliRunner().invoke(cli, ["kick", "--all"])

    assert result.exit_code == 0
    assert kicked == ["fresh"]


def test_phantom_kick_records_unconfirmed_attempt_when_status_stays_ambiguous(monkeypatch):
    recorded = []
    notified = []
    kick_calls = []
    accounts = [AccountConfig(label="phantom", provider="codex")]
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: ([(accounts[0], phantom_status)], []),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **kwargs: kick_calls.append(kwargs)
        or KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda account: phantom_status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, notifications: notified.append(event),
    )

    result = CliRunner().invoke(cli, ["kick", "--all"])

    assert result.exit_code == 0
    assert recorded[0].success is True
    assert recorded[0].confirmed is False
    assert recorded[0].kind == "phantom_recovery"
    assert recorded[0].kick_type == "session"
    assert kick_calls == [
        {
            "record": False,
            "phantom_recovery": True,
            "model_override": None,
            "codex_surface": CODEX_KICK_SURFACE_REPO_SKIP,
        }
    ]
    assert notified == recorded
    assert "Attempted" in result.output


def test_kick_label_force_bypasses_already_session_kicked(monkeypatch):
    kicked = []
    account = AccountConfig(label="phantom", provider="codex")
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="phantom",
            timestamp=10_000.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_300.0)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: kicked.append(candidate.label)
        or KickEvent(label=candidate.label, success=True, input_tokens=1),
    )
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda candidate: status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "phantom", "--force"])

    assert result.exit_code == 0
    assert kicked == ["phantom", "phantom", "phantom", "phantom"]


def test_codex_direct_weekly_kick_marks_command_only_when_pre_status_missing(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            success=True,
            evidence_response=True,
            response_text="TokenKick anchor probe completed.",
        ),
    )

    event = _run_kick_attempt(
        account,
        Config(),
        phantom_recovery=False,
        daemon_log=False,
        kick_type="kick",
        model_override=None,
        pre_status=None,
        record_event=False,
        log_result=False,
    )

    assert event.success is True
    assert event.confirmed is False
    assert event.evidence_provider_moved is None
    assert event.codex_confirmation_method == "none"
    assert event.post_kick_status == "not_checked"
    assert event.error == CODEX_PROVIDER_MOVEMENT_NOT_CONFIRMED_ERROR


def test_codex_direct_weekly_kick_confirms_when_provider_moves(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_at=10_000.0,
        resets_in_seconds=0,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=2.0,
        resets_at=20_000.0,
        resets_in_seconds=10_000,
        window_anchor_state="anchored",
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            success=True,
            evidence_response=True,
            response_text="TokenKick anchor probe completed.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._fetch_codex_provider_movement_status",
        lambda *_args: post_status,
    )

    event = _run_kick_attempt(
        account,
        Config(),
        phantom_recovery=False,
        daemon_log=False,
        kick_type="kick",
        model_override=None,
        pre_status=pre_status,
        record_event=False,
        log_result=False,
    )

    assert event.success is True
    assert event.confirmed is True
    assert event.evidence_provider_moved is True
    assert event.codex_confirmation_method == "provider_moved"
    assert event.post_kick_status == "moved"
    assert event.error is None


def test_daemon_codex_weekly_kick_rechecks_live_status_and_skips_active(monkeypatch, capsys):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    stale_target = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=0,
    )
    live_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        resets_in_seconds=3600,
        source_detail="codex-direct-appserver",
    )
    kicked = []

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda *_args, **_kwargs: live_status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: kicked.append(candidate.label)
        or KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._utc_log_timestamp", lambda: "2026-06-03T08:25:00Z")

    executed, deferred = _kick_all_enabled_accounts(
        [account],
        Config(accounts=[account]),
        targets=[(account, stale_target)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert kicked == []
    assert executed == 1
    assert deferred == 0
    assert 'reason="live_status_changed"' in output


def test_verify_codex_session_anchor_rejects_phantom_post_status(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=True)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "phantom"
    assert verified.error == CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR


def test_verify_codex_session_anchor_rejects_unanchored_zero_movement(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = replace(pre_status)
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=True)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "unchanged"
    assert verified.error == CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR


def test_verify_codex_session_anchor_accepts_real_anchor_post_status(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=32.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=True)

    assert verified.confirmed is True
    assert verified.evidence_provider_moved is True
    assert verified.post_kick_status == "moved"
    assert verified.error is None


def test_daemon_verify_codex_session_anchor_defers_zero_usage_near_full_anchor(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17_999,
        session_resets_at=10_000.0 + (5 * 60 * 60),
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label=account.label,
        timestamp=10_000.0,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        evidence_tokens=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args: pytest.fail("daemon session verification must not wait for delayed check"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda *_args: pytest.fail("daemon session verification must not sleep inline"),
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=True)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "pending"
    assert verified.codex_confirmation_method == "pending_reset_clock"
    assert verified.error == CODEX_SESSION_ANCHOR_PENDING_ERROR


def test_verify_codex_session_anchor_rejects_misaligned_anchor_time(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=5.0,
        window_minutes=10080,
        session_used_percent=5.0,
        session_resets_in_seconds=17_940,
        session_resets_at=10_000.0 + (6 * 60 * 60),
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label=account.label,
        timestamp=10_000.0,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )

    verified = _verify_codex_session_anchor(account, event, pre_status)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "misaligned"
    assert verified.error == CODEX_SESSION_ANCHOR_MISALIGNED_ERROR


def test_verify_codex_session_anchor_marks_stale_provider_read_pending(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=5.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17_900,
        session_window_minutes=300,
        observed_at="2026-06-03T05:00:00Z",
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        codex_attempt_started_at=1_780_471_000.0,
        codex_attempt_finished_at=1_780_471_010.0,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: post_status,
    )

    verified = _verify_codex_session_anchor(account, event, pre_status)

    assert verified.confirmed is False
    assert verified.codex_confirmation_method == "pending_reset_clock"
    assert verified.codex_provider_stale is True
    assert verified.post_kick_status == "pending"
    assert verified.error == CODEX_SESSION_ANCHOR_PENDING_ERROR


def test_verify_codex_session_anchor_non_daemon_defers_delayed_check(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        window_minutes=10080,
        session_used_percent=10.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    post_status = replace(pre_status)
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
    )
    monkeypatch.setattr("tokenkick.cli._poll_codex_session_anchor_status", lambda *_args: post_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args, **_kwargs: pytest.fail("non-daemon kick must not wait for delayed verification"),
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=False)

    assert verified.confirmed is False
    assert verified.post_kick_status == "pending"
    assert verified.codex_confirmation_method == "pending_reset_clock"
    assert verified.error == CODEX_SESSION_ANCHOR_PENDING_ERROR


def test_kick_and_notify_non_daemon_codex_pending_prints_confirmation_later(
    monkeypatch,
    capsys,
):
    account = AccountConfig(label="codex", provider="codex")
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        window_minutes=10080,
        session_used_percent=10.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    calls = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr("tokenkick.cli._poll_codex_session_anchor_status", lambda *_args: pre_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args, **_kwargs: pytest.fail("non-daemon kick must not wait for delayed verification"),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=pre_status,
        send_notification=False,
        kick_type="session",
    )

    output = capsys.readouterr().out
    assert event.confirmed is False
    assert event.post_kick_status == "pending"
    assert event.codex_confirmation_method == "pending_reset_clock"
    assert len(calls) == 1
    assert "provider confirmation will be checked on the next status refresh or daemon poll" in output


def test_verify_claude_session_anchor_rejects_phantom_like_post_status():
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(label="claude", success=True, confirmed=True)

    verified = _verify_claude_session_anchor(event, pre_status, post_status)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "unchanged"
    assert verified.error == CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR


def test_verify_claude_session_anchor_accepts_real_anchor_post_status():
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    post_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        window_minutes=10080,
        session_used_percent=35.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label="claude",
        success=True,
        confirmed=False,
        error=CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
    )

    verified = _verify_claude_session_anchor(event, pre_status, post_status)

    assert verified.confirmed is True
    assert verified.evidence_provider_moved is True
    assert verified.post_kick_status == "moved"
    assert verified.error is None


def test_verify_claude_session_anchor_accepts_already_active_post_status():
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=20.0,
        window_minutes=10080,
        session_used_percent=80.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    post_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=20.0,
        window_minutes=10080,
        session_used_percent=80.0,
        session_resets_in_seconds=3500,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label="claude",
        success=True,
        confirmed=False,
        response_text="Claude /usage session anchor completed.",
        error=CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
    )

    verified = _verify_claude_session_anchor(event, pre_status, post_status)

    assert verified.confirmed is True
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "already_active"
    assert verified.response_text == "Claude /usage confirmed the session window is already active."
    assert verified.error is None


def test_verify_claude_session_anchor_prefers_already_active_over_near_full_moved():
    pre_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17_940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    post_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17_900,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    event = KickEvent(
        label="claude",
        success=True,
        confirmed=False,
        response_text="Claude /usage session anchor completed.",
        error=CLAUDE_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
    )

    verified = _verify_claude_session_anchor(event, pre_status, post_status)

    assert verified.confirmed is True
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "already_active"
    assert verified.response_text == "Claude /usage confirmed the session window is already active."
    assert verified.error is None


def test_codex_phantom_kick_stays_unconfirmed_when_exec_reports_tokens(monkeypatch):
    recorded = []
    calls = []
    accounts = [AccountConfig(label="phantom", provider="codex")]
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: ([(accounts[0], phantom_status)], []),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **kwargs: calls.append(kwargs)
        or KickEvent(
            label=account.label,
            success=True,
            input_tokens=123,
            output_tokens=4,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda account: phantom_status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "--all"])

    assert result.exit_code == 0
    assert recorded[0].success is True
    assert recorded[0].confirmed is False
    assert recorded[0].kind == "phantom_recovery"
    assert recorded[0].kick_type == "session"
    assert recorded[0].error == "Codex accepted usage, but session status is still ambiguous"
    assert recorded[0].post_kick_status == "phantom"
    assert recorded[0].evidence_provider_moved is False
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert "Attempted" in result.output


def test_verify_phantom_kick_keeps_no_evidence_codex_attempt_unconfirmed(monkeypatch):
    account = AccountConfig(label="phantom", provider="codex")
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=False,
        error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
    )

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: phantom_status)

    verified = _verify_phantom_kick(account, event, daemon_log=True)

    assert verified.success is True
    assert verified.confirmed is False
    assert verified.kind == "probe"
    assert verified.error == CODEX_NO_GENERATION_EVIDENCE_ERROR
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "phantom"


def test_verify_phantom_kick_keeps_token_bearing_phantom_unconfirmed(monkeypatch):
    account = AccountConfig(label="phantom", provider="codex")
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        input_tokens=123,
        output_tokens=4,
    )

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: phantom_status)

    verified = _verify_phantom_kick(account, event, daemon_log=True)

    assert verified.success is True
    assert verified.confirmed is False
    assert verified.error == PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "phantom"


def test_daemon_verify_phantom_kick_defers_delayed_codex_anchor(monkeypatch):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        input_tokens=123,
        output_tokens=4,
    )

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: phantom_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_phantom_status",
        lambda *_args: pytest.fail("daemon phantom verification must not wait for delayed check"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda *_args: pytest.fail("daemon phantom verification must not sleep inline"),
    )

    verified = _verify_phantom_kick(account, event, daemon_log=True)

    assert verified.confirmed is False
    assert verified.error == CODEX_SESSION_ANCHOR_PENDING_ERROR
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "pending"
    assert verified.codex_confirmation_method == "pending_reset_clock"


def test_verify_phantom_kick_non_daemon_skips_delayed_codex_anchor(monkeypatch):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        input_tokens=123,
        output_tokens=4,
    )

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: phantom_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_phantom_status",
        lambda *_args, **_kwargs: pytest.fail("non-daemon phantom check must not wait"),
    )

    verified = _verify_phantom_kick(account, event, daemon_log=False)

    assert verified.confirmed is False
    assert verified.post_kick_status == "phantom"
    assert verified.error == PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR


def test_verify_phantom_kick_confirms_no_evidence_codex_when_status_moves(monkeypatch):
    account = AccountConfig(label="phantom", provider="codex")
    moved_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=3.0,
        session_resets_in_seconds=16500,
        session_window_minutes=300,
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=False,
        error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
    )
    cleared = []

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: moved_status)
    monkeypatch.setattr(
        "tokenkick.cli._clear_phantom_session_observation",
        lambda candidate: cleared.append(("observation", candidate.label)),
    )
    monkeypatch.setattr(
        "tokenkick.cli._clear_phantom_recovery_state",
        lambda candidate: cleared.append(("recovery", candidate.label)),
    )

    verified = _verify_phantom_kick(account, event)

    assert verified.success is True
    assert verified.confirmed is True
    assert verified.error is None
    assert verified.evidence_provider_moved is True
    assert verified.post_kick_status == "moved"
    assert cleared == [("observation", "phantom"), ("recovery", "phantom")]


def test_verify_phantom_kick_records_unknown_post_status(monkeypatch):
    account = AccountConfig(label="phantom", provider="codex")
    unknown_status = AccountStatus(
        label="phantom",
        state=AccountState.UNKNOWN,
        error="refresh failed",
    )
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
    )

    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: unknown_status)

    verified = _verify_phantom_kick(account, event)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "unknown"
    assert "could not verify" in verified.error


def test_kick_and_notify_retries_codex_surface_after_no_evidence(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    unchanged_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    moved_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=32.0,
        session_resets_in_seconds=17_940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    logs = []
    recorded = []
    calls = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        if kwargs["codex_surface"] == CODEX_KICK_SURFACE_REPO_SKIP:
            return KickEvent(
                label=candidate.label,
                success=True,
                confirmed=False,
                error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
                evidence_response=False,
                evidence_tokens=False,
                post_kick_status="not_checked",
            )
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            evidence_tokens=False,
            post_kick_status="not_checked",
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: (
            unchanged_status
            if calls[-1]["codex_surface"] == CODEX_KICK_SURFACE_REPO_SKIP
            else moved_status
        ),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli._daemon_log",
        lambda event_name, **fields: logs.append((event_name, fields)),
    )

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.confirmed is True
    assert event.response_text == "TokenKick anchor probe completed."
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
    ]
    assert recorded[0].confirmed is False
    assert recorded[0].error == CODEX_NO_GENERATION_EVIDENCE_ERROR
    assert recorded[0].codex_surface == CODEX_KICK_SURFACE_REPO_SKIP
    assert recorded[0].codex_attempt == 1
    assert recorded[0].codex_max_attempts == 4
    assert recorded[1] == event
    assert recorded[1].codex_surface == CODEX_KICK_SURFACE_LEGACY
    assert recorded[1].codex_attempt == 2
    assert recorded[1].codex_max_attempts == 4
    assert logs == [
        (
            "kick_start",
            {
                "account": "codex",
                "surface": CODEX_KICK_SURFACE_REPO_SKIP,
                "attempt": 1,
                "max_attempts": 4,
            },
        ),
        (
            "kick_start",
            {
                "account": "codex",
                "surface": CODEX_KICK_SURFACE_LEGACY,
                "attempt": 2,
                "max_attempts": 4,
            },
        ),
        (
            "kick_attempted",
            {
                "account": "codex",
                "surface": CODEX_KICK_SURFACE_REPO_SKIP,
                "attempt": 1,
                "max_attempts": 4,
                "response_evidence": False,
                "token_evidence": False,
                "provider_moved": False,
                "confirmation_method": "none",
                "post_status": "unchanged",
                "reason": CODEX_NO_GENERATION_EVIDENCE_ERROR,
            },
        ),
        (
            "kick_confirmed",
            {
                "account": "codex",
                "surface": CODEX_KICK_SURFACE_LEGACY,
                "attempt": 2,
                "max_attempts": 4,
                "response_evidence": True,
                "token_evidence": False,
                "provider_moved": True,
                "confirmation_method": "reset_clock",
                "anchor_delta_seconds": 60.0,
                "post_status": "moved",
            },
        ),
    ]


def test_kick_and_notify_notifies_only_final_codex_retry_result(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE)
    calls = []
    recorded = []
    notified = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=False,
            error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, _notifications: notified.append(event) or True,
    )

    event = _kick_and_notify(
        account,
        Config(accounts=[account], notifications=NotifyConfig(enabled=True, backend="ntfy")),
        pre_status=status,
        send_notification=True,
        kick_type="session",
    )

    assert event.confirmed is False
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert recorded[-1] == event
    assert len(recorded) == 4
    assert notified == [event]


def test_kick_and_notify_does_not_retry_codex_hard_failure(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE)
    calls = []
    recorded = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(label=candidate.label, success=False, error="codex exited 1")

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.success is False
    assert event.error == "codex exited 1"
    assert [call["codex_surface"] for call in calls] == [CODEX_KICK_SURFACE_REPO_SKIP]
    assert recorded == [event]


def test_kick_and_notify_does_not_retry_confirmed_codex_first_surface(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    moved_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=8.0,
        window_minutes=10080,
        session_used_percent=32.0,
        session_resets_in_seconds=17_940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    calls = []
    recorded = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: moved_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.confirmed is True
    assert [call["codex_surface"] for call in calls] == [CODEX_KICK_SURFACE_REPO_SKIP]
    assert recorded == [event]


def test_kick_and_notify_retries_codex_surfaces_after_delayed_anchor_not_observed(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    unchanged_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    calls = []
    recorded = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            evidence_tokens=True,
            post_kick_status="not_checked",
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: unchanged_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.success is True
    assert event.confirmed is False
    assert event.error == CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR
    assert event.evidence_response is True
    assert event.evidence_tokens is True
    assert event.evidence_provider_moved is False
    assert event.post_kick_status == "unchanged"
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert len(recorded) == 4
    assert all(item.error == CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR for item in recorded)


def test_kick_and_notify_pauses_direct_codex_after_four_generated_unconfirmed_surfaces(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    unchanged_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    calls = []
    recorded = []
    sleeps = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            evidence_tokens=True,
            post_kick_status="not_checked",
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: unchanged_status,
    )
    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.success is True
    assert event.confirmed is False
    assert event.error == CODEX_SESSION_ANCHOR_PENDING_ERROR
    assert event.codex_confirmation_method == "pending_reset_clock"
    assert event.post_kick_status == "pending"
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert sleeps == []
    assert len(recorded) == 4
    assert recorded[-1] == event
    assert recorded[-1].codex_surface == CODEX_KICK_SURFACE_INTERACTIVE_LIKE


def test_kick_and_notify_retries_codex_surfaces_after_delayed_generation_phantom_post(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    phantom_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    calls = []
    recorded = []

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            evidence_tokens=True,
            post_kick_status="not_checked",
        )

    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: phantom_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=status,
        daemon_log=True,
        send_notification=False,
        kick_type="session",
    )

    assert event.success is True
    assert event.confirmed is False
    assert event.error == CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR
    assert event.post_kick_status == "phantom"
    assert event.evidence_provider_moved is False
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert len(recorded) == 4
    assert all(item.confirmed is False for item in recorded)
    assert all(item.post_kick_status == "phantom" for item in recorded)
    assert all(item.evidence_provider_moved is False for item in recorded)


def test_kick_and_notify_fire_all_disabled_uses_ladder_and_scorer(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    moved_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=3.0,
        session_resets_in_seconds=17_880,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    calls = []
    recorded = []
    stats_updates = []

    monkeypatch.setattr(
        "tokenkick.cli._codex_retry_surfaces_for_account",
        lambda _account: (CODEX_KICK_SURFACE_REPO_SKIP,),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: moved_status,
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli.update_codex_surface_stats",
        lambda _path, _account, events: stats_updates.append(list(events)),
    )

    event = _kick_and_notify(
        account,
        Config(
            accounts=[account],
            codex_fire_all_surfaces=False,
            codex_fire_all_surface_order=[CODEX_KICK_SURFACE_LEGACY],
        ),
        pre_status=status,
        send_notification=False,
        kick_type="session",
        allow_codex_fire_all=True,
    )

    assert event.confirmed is True
    assert [call["codex_surface"] for call in calls] == [CODEX_KICK_SURFACE_REPO_SKIP]
    assert len(recorded) == 1
    assert stats_updates == [[event]]


def test_kick_and_notify_fire_all_surfaces_records_four_attempts_one_notification_no_scorer(
    monkeypatch,
):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    calls = []
    sleeps = []
    recorded = []
    notified = []

    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            post_kick_status="not_checked",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, _notifications: notified.append(event) or True,
    )
    monkeypatch.setattr(
        "tokenkick.cli.update_codex_surface_stats",
        lambda *_args: pytest.fail("fire-all must not update the surface scorer"),
    )

    event = _kick_and_notify(
        account,
        Config(
            accounts=[account],
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
            codex_fire_all_surfaces=True,
            codex_burst_ladder_gap_seconds=30,
        ),
        pre_status=status,
        kick_type="session",
        allow_codex_fire_all=True,
    )

    assert [call["codex_surface"] for call in calls] == list(CODEX_FIRE_ALL_SURFACES)
    assert sleeps == [30.0, 30.0, 30.0]
    assert len(recorded) == 4
    assert len({item.codex_cluster_id for item in recorded}) == 1
    assert {item.codex_cluster_origin for item in recorded} == {"burst"}
    assert [item.codex_attempt for item in recorded] == [1, 2, 3, 4]
    assert all(item.codex_max_attempts == 4 for item in recorded)
    assert all(item.confirmed is False for item in recorded)
    assert all(item.post_kick_status == "pending" for item in recorded)
    assert all(item.codex_confirmation_method == "pending_reset_clock" for item in recorded)
    assert all(item.error == CODEX_SESSION_ANCHOR_PENDING_ERROR for item in recorded)
    assert notified == [event]
    assert event == recorded[-1]


def test_burst_ladder_filters_demoted_and_force_pruned_surfaces(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_auto_demote=True,
        codex_surface_force_keep=[CODEX_KICK_SURFACE_INTERACTIVE_LIKE],
        codex_surface_force_prune=[CODEX_KICK_SURFACE_REPO],
    )
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    calls = []

    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.codex_surface_stats_for_account",
        lambda *_args: {"demotion": {"demoted": {CODEX_KICK_SURFACE_REPO_SKIP: {}, CODEX_KICK_SURFACE_INTERACTIVE_LIKE: {}}}},
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs["codex_surface"])
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )

    _kick_and_notify(
        account,
        Config(
            accounts=[account],
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_gap_seconds=0,
            codex_burst_ladder_surface_order=[
                CODEX_KICK_SURFACE_REPO_SKIP,
                CODEX_KICK_SURFACE_REPO,
                CODEX_KICK_SURFACE_LEGACY,
            ],
        ),
        pre_status=status,
        send_notification=False,
        kick_type="kick",
        allow_codex_fire_all=True,
    )

    assert calls == [CODEX_KICK_SURFACE_LEGACY, CODEX_KICK_SURFACE_INTERACTIVE_LIKE]


def test_burst_ladder_rejects_empty_effective_surface_set(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_force_prune=[CODEX_KICK_SURFACE_LEGACY],
    )
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("empty burst set must not kick"),
    )

    with pytest.raises(click.ClickException, match="no active surfaces"):
        _kick_and_notify(
            account,
            Config(
                accounts=[account],
                codex_burst_ladder_enabled=True,
                codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_LEGACY],
            ),
            pre_status=status,
            send_notification=False,
            kick_type="kick",
            allow_codex_fire_all=True,
        )


def test_fire_all_custom_full_order_is_respected(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    calls = []

    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs["codex_surface"])
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )

    _kick_and_notify(
        account,
        Config(
            accounts=[account],
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_gap_seconds=0,
            codex_burst_ladder_surface_order=[
                CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
                CODEX_KICK_SURFACE_REPO,
                CODEX_KICK_SURFACE_REPO_SKIP,
                CODEX_KICK_SURFACE_LEGACY,
            ],
        ),
        pre_status=status,
        send_notification=False,
        kick_type="kick",
        allow_codex_fire_all=True,
    )

    assert calls == [
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
    ]


def test_fire_all_subset_fires_only_configured_surfaces(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    calls = []
    recorded = []

    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs["codex_surface"])
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )

    _kick_and_notify(
        account,
        Config(
            accounts=[account],
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_gap_seconds=0,
            codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_REPO, CODEX_KICK_SURFACE_LEGACY],
        ),
        pre_status=status,
        send_notification=False,
        kick_type="kick",
        allow_codex_fire_all=True,
    )

    assert calls == [CODEX_KICK_SURFACE_REPO, CODEX_KICK_SURFACE_LEGACY]
    assert [event.codex_attempt for event in recorded] == [1, 2]
    assert all(event.codex_max_attempts == 2 for event in recorded)


def test_fire_all_single_surface_subset_uses_fire_all_path(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.FRESH)
    calls = []
    recorded = []

    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.update_codex_surface_stats",
        lambda *_args: pytest.fail("single-surface fire-all must not use scorer"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs["codex_surface"])
        or KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )

    _kick_and_notify(
        account,
        Config(
            accounts=[account],
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_LEGACY],
        ),
        pre_status=status,
        send_notification=False,
        kick_type="kick",
        allow_codex_fire_all=True,
    )

    assert calls == [CODEX_KICK_SURFACE_LEGACY]
    assert len(recorded) == 1
    assert recorded[0].codex_attempt == 1
    assert recorded[0].codex_max_attempts == 1
    assert recorded[0].codex_confirmation_method == "pending_reset_clock"


def test_fire_all_pending_attempt_blocks_next_session_poll(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    recorded = []

    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            timestamp=10_000.0,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)

    _kick_and_notify(
        account,
        Config(accounts=[account], codex_burst_ladder_enabled=True, codex_burst_ladder_gap_seconds=0),
        pre_status=status,
        send_notification=False,
        kick_type="session",
        allow_codex_fire_all=True,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: list(recorded))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account], codex_burst_ladder_enabled=True),
    )

    assert targets == []
    assert deferred == []
    assert _kick_eligibility(account, status, "codex", history=recorded).reason == (
        "codex_awaiting_confirmation"
    )


def test_fire_all_preserves_inter_account_stagger(monkeypatch):
    accounts = [
        AccountConfig(label="codex-a", provider="codex", auto_kick=True),
        AccountConfig(label="codex-b", provider="codex", auto_kick=True),
    ]
    statuses = [
        AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            resets_at=604900.0,
            window_minutes=10080,
        )
        for account in accounts
    ]
    current_time = [100.0]
    sleeps = []
    calls = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr("tokenkick.cli.time.monotonic", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda now=None: {})
    monkeypatch.setattr("tokenkick.cli.remove_pending_kick", lambda _account: None)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **kwargs: calls.append((account.label, kwargs["codex_surface"]))
        or KickEvent(
            label=account.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
        ),
    )

    kicked_count, deferred_count = _kick_all_enabled_accounts(
        accounts,
        Config(accounts=accounts, codex_fire_all_surfaces=True, codex_fire_all_surface_gap_seconds=0),
        targets=list(zip(accounts, statuses, strict=False)),
        deferred=[],
        stagger_state=KickStaggerState(),
    )

    assert kicked_count == 2
    assert deferred_count == 0
    assert sleeps == [CODEX_KICK_STAGGER_SECONDS]
    assert calls == [
        (accounts[0].label, surface) for surface in CODEX_FIRE_ALL_SURFACES
    ] + [
        (accounts[1].label, surface) for surface in CODEX_FIRE_ALL_SURFACES
    ]


def test_daemon_verify_codex_session_anchor_defers_instead_of_delayed_check(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    immediate_status = replace(pre_status)
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        evidence_tokens=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: immediate_status,
    )
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args: pytest.fail("daemon session verification must not call delayed helper"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda *_args: pytest.fail("daemon session verification must not sleep inline"),
    )

    verified = _verify_codex_session_anchor(account, event, pre_status, daemon_log=True)

    assert verified.confirmed is False
    assert verified.evidence_provider_moved is False
    assert verified.post_kick_status == "pending"
    assert verified.codex_confirmation_method == "pending_reset_clock"
    assert verified.error == CODEX_SESSION_ANCHOR_PENDING_ERROR


def test_daemon_verify_codex_session_anchor_logs_configured_deferred_backoff(
    monkeypatch,
    capsys,
):
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    immediate_status = replace(pre_status)
    event = KickEvent(
        label=account.label,
        success=True,
        confirmed=True,
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        evidence_tokens=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._poll_codex_session_anchor_status",
        lambda *_args: immediate_status,
    )
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args: pytest.fail("daemon session verification must not call delayed helper"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda *_args: pytest.fail("daemon session verification must not sleep inline"),
    )

    verified = _verify_codex_session_anchor(
        account,
        event,
        pre_status,
        Config(codex_surface_retry_backoff_seconds=123),
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert "codex_delayed_verification_deferred" in output
    assert "backoff_seconds=123.0" in output
    assert verified.confirmed is False
    assert verified.codex_confirmation_method == "pending_reset_clock"


def test_codex_surface_retry_backoff_env_overrides_config(monkeypatch):
    monkeypatch.setenv("TK_CODEX_SURFACE_RETRY_BACKOFF_SECONDS", "7.5")

    delay = _codex_surface_retry_backoff_seconds(
        Config(codex_surface_retry_backoff_seconds=123)
    )

    assert delay == 7.5


def test_codex_fire_all_env_overrides_config(monkeypatch):
    monkeypatch.setenv("TK_CODEX_FIRE_ALL_SURFACES", "true")
    monkeypatch.setenv("TK_CODEX_FIRE_ALL_SURFACE_GAP_SECONDS", "4.5")
    monkeypatch.setenv("TK_CODEX_FIRE_ALL_SURFACE_ORDER", "repo,legacy")

    assert _codex_fire_all_surfaces_enabled(Config(codex_fire_all_surfaces=False)) is True
    assert _codex_fire_all_surface_gap_seconds(
        Config(codex_fire_all_surface_gap_seconds=30)
    ) == 4.5
    assert _codex_fire_all_surface_order(
        Config(codex_fire_all_surface_order=[CODEX_KICK_SURFACE_INTERACTIVE_LIKE])
    ) == (CODEX_KICK_SURFACE_REPO, CODEX_KICK_SURFACE_LEGACY)


def test_codex_fire_all_empty_order_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("TK_CODEX_FIRE_ALL_SURFACE_ORDER", raising=False)

    assert _codex_fire_all_surface_order(Config(codex_fire_all_surface_order=[])) == (
        CODEX_FIRE_ALL_SURFACES
    )
    monkeypatch.setenv("TK_CODEX_FIRE_ALL_SURFACE_ORDER", "")

    assert _codex_fire_all_surface_order(Config(codex_fire_all_surface_order=[])) == (
        CODEX_FIRE_ALL_SURFACES
    )


def test_codex_fire_all_commands_manage_mode_order_and_gap(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    Config(codex_fire_all_surfaces=False).save()

    status_result = CliRunner().invoke(cli, ["codex-fire-all", "status", "--json-output"])

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.output)
    assert status_payload["enabled"] is False
    assert status_payload["active_order"] == list(CODEX_FIRE_ALL_SURFACES)

    enable_result = CliRunner().invoke(cli, ["codex-fire-all", "enable"])
    order_result = CliRunner().invoke(cli, ["codex-fire-all", "order", "repo", "legacy"])
    gap_result = CliRunner().invoke(cli, ["codex-fire-all", "gap", "7"])

    assert enable_result.exit_code == 0
    assert order_result.exit_code == 0
    assert gap_result.exit_code == 0
    config = Config.load()
    assert config.codex_fire_all_surfaces is True
    assert config.codex_fire_all_surface_order == ["repo", "legacy"]
    assert config.codex_fire_all_surface_gap_seconds == 7

    disable_result = CliRunner().invoke(cli, ["codex-fire-all", "disable"])
    reset_result = CliRunner().invoke(cli, ["codex-fire-all", "order", "--reset"])

    assert disable_result.exit_code == 0
    assert reset_result.exit_code == 0
    config = Config.load()
    assert config.codex_fire_all_surfaces is False
    assert config.codex_fire_all_surface_order == []


def test_codex_fire_all_order_command_rejects_invalid_surface(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    Config().save()

    result = CliRunner().invoke(cli, ["codex-fire-all", "order", "repo", "bad-surface"])

    assert result.exit_code != 0
    assert "Unknown Codex surface" in result.output


def test_codex_surface_demotion_force_prune_warns_and_saves(monkeypatch, tmp_path):
    config_file, _backup_file, _status_cache_file = _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    Config(accounts=[account]).save()

    result = CliRunner().invoke(
        cli,
        [
            "codex-surfaces",
            "codex",
            "demotion",
            "force-prune",
            CODEX_KICK_SURFACE_LEGACY,
            CODEX_KICK_SURFACE_REPO,
            CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "not auto-reintroduced on a miss" in result.output
    assert "fewer than 2 active surfaces" in result.output
    loaded = Config.load()
    assert loaded.accounts[0].codex_surface_force_prune == [
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert config_file.exists()


def test_codex_surfaces_reset_stats_resets_learning_only(monkeypatch, tmp_path):
    import tokenkick.cli as cli_module
    from tokenkick.codex_surface_stats import (
        codex_surface_stats_for_account,
        reset_codex_surface_demotion_evidence,
        update_codex_surface_stats,
    )

    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_surface_auto_demote=True,
    )
    Config(accounts=[account]).save()
    stats_file = cli_module._codex_surface_stats_file()
    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_REPO,
                codex_attribution=CODEX_ATTRIBUTION_STRONG,
                codex_cluster_id="cluster-1",
                response_text="ok",
            )
        ],
    )

    result = CliRunner().invoke(cli, ["codex-surfaces", "codex", "reset-stats"])

    assert result.exit_code == 0, result.output
    assert "demotion evidence were not changed" in result.output
    report = codex_surface_stats_for_account(account, stats_file)
    repo = next(surface for surface in report["surfaces"] if surface["surface"] == CODEX_KICK_SURFACE_REPO)
    assert repo["attempts"] == 0
    assert repo["confirmed"] == 0
    assert report["demotion"]["strong_cluster_count"] == 1
    reset_codex_surface_demotion_evidence(stats_file, account)


def test_codex_surfaces_reset_all_resets_learning_and_demotion(monkeypatch, tmp_path):
    import tokenkick.cli as cli_module
    from tokenkick.codex_surface_stats import codex_surface_stats_for_account, update_codex_surface_stats

    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_surface_auto_demote=True,
    )
    Config(accounts=[account]).save()
    stats_file = cli_module._codex_surface_stats_file()
    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_REPO,
                codex_attribution=CODEX_ATTRIBUTION_STRONG,
                codex_cluster_id="cluster-1",
                response_text="ok",
            )
        ],
    )

    result = CliRunner().invoke(cli, ["codex-surfaces", "codex", "reset-all"])

    assert result.exit_code == 0, result.output
    report = codex_surface_stats_for_account(account, stats_file)
    repo = next(surface for surface in report["surfaces"] if surface["surface"] == CODEX_KICK_SURFACE_REPO)
    assert repo["attempts"] == 0
    assert report["demotion"]["strong_cluster_count"] == 0


def test_codex_surfaces_reset_stats_all_without_label(monkeypatch, tmp_path):
    import tokenkick.cli as cli_module
    from tokenkick.codex_surface_stats import codex_surface_stats_for_account, update_codex_surface_stats

    _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(
            label="codex-a",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-a",
        ),
        AccountConfig(
            label="codex-b",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex-b",
        ),
        AccountConfig(label="claude", provider="claude"),
    ]
    Config(accounts=accounts).save()
    stats_file = cli_module._codex_surface_stats_file()
    for index, account in enumerate(accounts[:2], start=1):
        update_codex_surface_stats(
            stats_file,
            account,
            [
                KickEvent(
                    label=account.label,
                    success=True,
                    confirmed=True,
                    codex_surface=CODEX_KICK_SURFACE_REPO,
                    response_text=f"ok-{index}",
                )
            ],
        )

    result = CliRunner().invoke(cli, ["codex-surfaces", "reset-stats", "--all"])

    assert result.exit_code == 0, result.output
    assert "2 Codex account" in result.output
    for account in accounts[:2]:
        report = codex_surface_stats_for_account(account, stats_file)
        repo = next(surface for surface in report["surfaces"] if surface["surface"] == CODEX_KICK_SURFACE_REPO)
        assert repo["attempts"] == 0


def test_codex_strategy_demotion_all_enable_disable(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label="codex-a", provider="codex", codex_surface_auto_demote=False),
        AccountConfig(label="codex-b", provider="codex", codex_surface_auto_demote=False),
        AccountConfig(label="claude", provider="claude", codex_surface_auto_demote=False),
    ]
    Config(accounts=accounts).save()

    enable_result = CliRunner().invoke(cli, ["codex-strategy", "demotion", "enable", "--all"])

    assert enable_result.exit_code == 0, enable_result.output
    loaded = Config.load()
    assert [account.codex_surface_auto_demote for account in loaded.accounts] == [True, True, False]
    assert "enabled for 2 Codex accounts" in enable_result.output

    disable_result = CliRunner().invoke(cli, ["codex-strategy", "demotion", "disable", "--all"])

    assert disable_result.exit_code == 0, disable_result.output
    loaded = Config.load()
    assert [account.codex_surface_auto_demote for account in loaded.accounts] == [False, False, False]
    assert "disabled for 2 Codex accounts" in disable_result.output


def test_codex_strategy_status_shows_auto_demotion_summary(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label="codex-a", provider="codex", codex_surface_auto_demote=True),
        AccountConfig(label="codex-b", provider="codex", codex_surface_auto_demote=False),
        AccountConfig(label="claude", provider="claude", codex_surface_auto_demote=True),
    ]
    Config(accounts=accounts).save()

    result = CliRunner().invoke(cli, ["codex-strategy", "status"])

    assert result.exit_code == 0, result.output
    assert "Auto-demotion" in result.output
    assert "mixed (1 on, 1 off)" in result.output
    assert "Effective kicking order" in result.output
    assert "Active order" not in result.output


def test_codex_strategy_status_json_includes_auto_demotion(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label="codex-a", provider="codex", codex_surface_auto_demote=True),
        AccountConfig(label="codex-b", provider="codex", codex_surface_auto_demote=True),
    ]
    Config(accounts=accounts).save()

    result = CliRunner().invoke(cli, ["codex-strategy", "status", "--json-output"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["auto_demotion"]["state"] == "all_on"
    assert payload["auto_demotion"]["summary"] == "all on (2/2 Codex accounts)"
    assert payload["auto_demotion"]["enabled_labels"] == ["codex-a", "codex-b"]
    assert payload["effective_kicking_order"] == list(CODEX_FIRE_ALL_SURFACES)
    assert payload["effective_kicking_order_summary"] == ", ".join(CODEX_FIRE_ALL_SURFACES)


def test_codex_strategy_status_shows_per_account_effective_kicking_order(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    accounts = [
        AccountConfig(label="codex-a", provider="codex"),
        AccountConfig(
            label="codex-b",
            provider="codex",
            codex_surface_force_prune=[CODEX_KICK_SURFACE_REPO_SKIP],
        ),
    ]
    Config(
        accounts=accounts,
        codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_REPO_SKIP, CODEX_KICK_SURFACE_LEGACY],
    ).save()

    result = CliRunner().invoke(cli, ["codex-strategy", "status", "--json-output"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["effective_kicking_order"] == []
    assert payload["effective_kicking_order_summary"] == (
        "varies by account; codex-a: repo-skip, legacy; codex-b: legacy"
    )
    assert payload["effective_kicking_order_by_account"] == {
        "codex-a": [CODEX_KICK_SURFACE_REPO_SKIP, CODEX_KICK_SURFACE_LEGACY],
        "codex-b": [CODEX_KICK_SURFACE_LEGACY],
    }


def test_codex_strategy_demotion_all_rejects_non_bulk_commands(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    Config(accounts=[AccountConfig(label="codex", provider="codex")]).save()

    result = CliRunner().invoke(cli, ["codex-strategy", "demotion", "enable"])

    assert result.exit_code != 0
    assert "Provide a Codex account LABEL, or use --all" in result.output


def test_codex_surface_reintroduction_on_fresh_all_unchanged_read(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    import tokenkick.cli as cli_module

    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_surface_auto_demote=True,
    )
    config = Config(accounts=[account], codex_surface_retry_backoff_seconds=1)
    Config(accounts=[account]).save()
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now.timestamp())
    stats_file = cli_module._codex_surface_stats_file()
    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            account,
            [
                KickEvent(
                    label=account.label,
                    success=True,
                    confirmed=True,
                    codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
                    codex_cluster_id=f"strong-{index}",
                    codex_attempt=1,
                    codex_attempt_finished_at=1000.0 + index,
                    codex_attribution=CODEX_ATTRIBUTION_STRONG,
                    response_text="ok",
                )
            ],
        )
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE not in codex_surface_order_for_account(
        account,
        stats_file,
    )
    last_attempt = now.timestamp() - 1000
    kept = codex_surface_order_for_account(account, stats_file)
    assert set(cli_module._codex_retry_surfaces_for_account(account)) == set(kept)
    assert _codex_surface_retry_backoff_seconds(config) == 1
    history = [
        KickEvent(
            label=account.label,
            success=True,
            confirmed=False,
            error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
            post_kick_status="unchanged",
            codex_surface=surface,
            codex_cluster_id="miss",
            codex_attempt=index,
            codex_max_attempts=len(kept),
            codex_attempt_finished_at=last_attempt + index,
        )
        for index, surface in enumerate(kept, start=1)
    ]
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        observed_at=to_utc_iso(now),
        session_used_percent=0.0,
        session_window_minutes=300,
    )
    kicked = []
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=300: history)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *args, **kwargs: kicked.append(kwargs) or KickEvent(label=account.label, success=True),
    )
    assert _codex_surface_missed_kept_cluster(account, status, history, config) is not None

    executed = _execute_codex_surface_reintroductions(
        [account],
        {account_key_string(account): status},
        config,
    )

    assert executed == 1
    assert kicked[0]["kick_type"] == "session"
    assert kicked[0]["allow_codex_fire_all"] is False
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE in codex_surface_order_for_account(
        account,
        stats_file,
    )


def test_codex_surface_reintroduction_ignores_stale_read(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    import tokenkick.cli as cli_module

    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_surface_auto_demote=True,
    )
    stats_file = cli_module._codex_surface_stats_file()
    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            account,
            [
                KickEvent(
                    label=account.label,
                    success=True,
                    confirmed=True,
                    codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
                    codex_cluster_id=f"strong-{index}",
                    codex_attribution=CODEX_ATTRIBUTION_STRONG,
                    response_text="ok",
                )
            ],
        )
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        stale=True,
        observed_at=to_utc_iso(datetime.now(timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=300: [])
    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda *_args, **_kwargs: pytest.fail("stale reads must not reintroduce"),
    )

    executed = _execute_codex_surface_reintroductions(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account], codex_surface_retry_backoff_seconds=1),
    )

    assert executed == 0
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE not in codex_surface_order_for_account(
        account,
        stats_file,
    )


def _pending_codex_confirmation_event(
    account: AccountConfig,
    *,
    cluster_id: str = "pending-cluster",
    finished_at: float = 1000.0,
) -> KickEvent:
    return KickEvent(
        label=account.label,
        timestamp=finished_at,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        post_kick_status="pending",
        codex_confirmation_method="pending_reset_clock",
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_cluster_id=cluster_id,
        codex_attempt=1,
        codex_max_attempts=2,
        codex_attempt_finished_at=finished_at,
    )


def _pending_confirmation_status(account: AccountConfig, observed_at: float, *, stale: bool = False):
    return AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        observed_at=to_utc_iso(datetime.fromtimestamp(observed_at, tz=timezone.utc)),
        stale=stale,
        session_used_percent=0.0,
        session_window_minutes=300,
    )


def test_codex_pending_confirmation_records_only_delivered_daemon_notifications(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account)

    _record_codex_pending_confirmation_notification(
        account,
        event,
        delivered=False,
        daemon_log=True,
    )
    assert not pending_file.exists()

    _record_codex_pending_confirmation_notification(
        account,
        event,
        delivered=True,
        daemon_log=False,
    )
    assert not pending_file.exists()

    _record_codex_pending_confirmation_notification(
        account,
        event,
        delivered=True,
        daemon_log=True,
    )

    data = json.loads(pending_file.read_text())
    key = f"{account_key_string(account)}::pending-cluster"
    assert data[key]["account_label"] == "codex"
    assert data[key]["last_attempt_finished_at"] == 1000.0
    assert data[key]["recovery_in_flight"] is False


def test_daemon_deferred_codex_session_records_pending_confirmation_state(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    pre_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=10.0,
        session_used_percent=10.0,
        session_resets_in_seconds=3600,
        session_window_minutes=300,
        window_anchor_state="anchored",
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            timestamp=2000.0,
            success=True,
            evidence_response=True,
            response_text="TokenKick anchor probe completed.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli._poll_codex_session_anchor_status", lambda *_args: pre_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_session_anchor_status",
        lambda *_args: pytest.fail("daemon session deferral must not call delayed helper"),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr(
        "tokenkick.cli._send_account_notifications",
        lambda *_args, **_kwargs: (True, True),
    )

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=pre_status,
        daemon_log=True,
        kick_type="session",
    )

    assert event.confirmed is False
    assert event.codex_confirmation_method == "pending_reset_clock"
    data = json.loads(pending_file.read_text())
    assert len(data) == 1
    state = next(iter(data.values()))
    assert state["account_key"] == account_key_string(account)
    assert state["account_label"] == "codex"
    assert state["cluster_id"] == event.codex_cluster_id
    assert state["last_attempt_finished_at"] == 2000.0


def test_daemon_deferred_codex_phantom_records_pending_confirmation_state(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="phantom",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            timestamp=2000.0,
            success=True,
            evidence_response=True,
            input_tokens=123,
            output_tokens=4,
        ),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: phantom_status)
    monkeypatch.setattr(
        "tokenkick.cli._delayed_codex_phantom_status",
        lambda *_args: pytest.fail("daemon phantom deferral must not call delayed helper"),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr(
        "tokenkick.cli._send_account_notifications",
        lambda *_args, **_kwargs: (True, True),
    )

    event = _kick_and_notify(
        account,
        Config(accounts=[account]),
        pre_status=phantom_status,
        daemon_log=True,
        kick_type="session",
    )

    assert event.confirmed is False
    assert event.codex_confirmation_method == "pending_reset_clock"
    assert event.kind == "phantom_recovery"
    assert event.kick_type == "session"
    data = json.loads(pending_file.read_text())
    assert len(data) == 1
    state = next(iter(data.values()))
    assert state["account_key"] == account_key_string(account)
    assert state["account_label"] == "phantom"
    assert state["cluster_id"] == event.codex_cluster_id
    assert state["last_attempt_finished_at"] == 2000.0


def test_codex_pending_confirmation_followup_sends_after_fresh_grace_without_demote(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account)
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event])
    notified = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda label, notifications: notified.append(label) or True,
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 1500.0)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=1),
    )

    assert sent == 1
    assert notified == ["codex"]
    assert json.loads(pending_file.read_text()) == {}


def test_codex_pending_confirmation_followup_skips_stale_read(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account)
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event])
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda *_args: pytest.fail("stale reads must not alert"),
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 1500.0, stale=True)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=1),
    )

    assert sent == 0
    assert json.loads(pending_file.read_text())


def test_codex_pending_confirmation_followup_clears_when_late_anchor_appears(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account)
    anchored = replace(
        event,
        confirmed=True,
        post_kick_status="moved",
        evidence_provider_moved=True,
        codex_confirmation_method="late_reset_clock",
    )
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event, anchored])
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda *_args: pytest.fail("confirmed anchors must clear without alerting"),
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 1500.0)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=1),
    )

    assert sent == 0
    assert json.loads(pending_file.read_text()) == {}


def test_codex_pending_confirmation_followup_defers_when_reintroduction_fires(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 2000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account, cluster_id="miss", finished_at=1000.0)
    recovery_event = _pending_codex_confirmation_event(
        account,
        cluster_id="recovery",
        finished_at=1950.0,
    )
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    _record_codex_pending_confirmation_notification(
        account,
        recovery_event,
        delivered=True,
        daemon_log=True,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event, recovery_event])
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda *_args: pytest.fail("recovery-in-flight must defer user alerts"),
    )
    recovery = SimpleNamespace(
        records=[
            SimpleNamespace(
                account_key=account_key_string(account),
                account_label=account.label,
                missed_cluster_id="miss",
                recovery_cluster_id="recovery",
                recovery_attempt_finished_at=1950.0,
            )
        ]
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 1980.0)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=900),
        recovery,
    )

    assert sent == 0
    states = json.loads(pending_file.read_text())
    assert len(states) == 1
    state = next(iter(states.values()))
    assert state["recovery_in_flight"] is True
    assert state["recovery_cluster_id"] == "recovery"


def test_codex_pending_confirmation_followup_clears_after_reintroduction_success(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 3000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account, cluster_id="miss", finished_at=1000.0)
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    state = json.loads(pending_file.read_text())
    key = f"{account_key_string(account)}::miss"
    state[key]["recovery_in_flight"] = True
    state[key]["recovery_cluster_id"] = "recovery"
    state[key]["recovery_attempt_finished_at"] = 2000.0
    pending_file.write_text(json.dumps(state) + "\n")
    anchored = replace(
        event,
        codex_cluster_id="recovery",
        confirmed=True,
        post_kick_status="moved",
        evidence_provider_moved=True,
        codex_confirmation_method="late_reset_clock",
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event, anchored])
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda *_args: pytest.fail("successful recovery must clear without alerting"),
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 2500.0)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=1),
    )

    assert sent == 0
    assert json.loads(pending_file.read_text()) == {}


def test_codex_pending_confirmation_followup_alerts_after_reintroduction_miss(
    monkeypatch,
    tmp_path,
):
    _isolate_config_files(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 3000.0)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
    )
    event = _pending_codex_confirmation_event(account, cluster_id="miss", finished_at=1000.0)
    _record_codex_pending_confirmation_notification(account, event, delivered=True, daemon_log=True)
    state = json.loads(pending_file.read_text())
    key = f"{account_key_string(account)}::miss"
    state[key]["recovery_in_flight"] = True
    state[key]["recovery_cluster_id"] = "recovery"
    state[key]["recovery_attempt_finished_at"] = 2000.0
    pending_file.write_text(json.dumps(state) + "\n")
    recovery_event = _pending_codex_confirmation_event(
        account,
        cluster_id="recovery",
        finished_at=2000.0,
    )
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=500: [event, recovery_event])
    notified = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_codex_pending_confirmation_missing",
        lambda label, notifications: notified.append(label) or True,
    )

    sent = _execute_codex_pending_confirmation_followups(
        [account],
        {account_key_string(account): _pending_confirmation_status(account, 2500.0)},
        Config(accounts=[account], notifications=NotifyConfig(enabled=True), codex_surface_retry_backoff_seconds=1),
    )

    assert sent == 1
    assert notified == ["codex"]
    assert json.loads(pending_file.read_text()) == {}


def test_codex_late_attribution_marks_closest_generated_attempt(monkeypatch, tmp_path):
    import tokenkick.models as models_mod

    now = 100_000.0
    base = now - 2_000.0
    history_file = tmp_path / "history.jsonl"
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
    )
    cluster_id = "cluster-secondary"
    events = [
        KickEvent(
            label=account.label,
            timestamp=base,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base,
            codex_attempt_finished_at=base + 10,
            codex_confirmation_method="none",
        ),
        KickEvent(
            label=account.label,
            timestamp=base + 300,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_LEGACY,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base + 300,
            codex_attempt_finished_at=base + 310,
            codex_confirmation_method="none",
        ),
        KickEvent(
            label=account.label,
            timestamp=base + 600,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_REPO,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base + 600,
            codex_attempt_finished_at=base + 610,
            codex_confirmation_method="none",
        ),
    ]
    history_file.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in events))
    update_codex_surface_stats(stats_file, account, events)
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        session_used_percent=100.0,
        session_resets_at=base + 300 + 300 * 60,
        session_window_minutes=300,
    )

    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: stats_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)

    changed = _apply_codex_late_attribution(
        [account],
        {account_key_string(account): status},
    )

    assert changed == 1
    updated = models_mod.load_kick_history(limit=10)
    by_surface = {event.codex_surface: event for event in updated}
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].confirmed is True
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].error is None
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].codex_confirmation_method == "late_reset_clock"
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].codex_attribution == CODEX_ATTRIBUTION_STRONG
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].codex_anchor_match_delta_seconds == 0.0
    assert by_surface[CODEX_KICK_SURFACE_REPO_SKIP].confirmed is False
    assert by_surface[CODEX_KICK_SURFACE_REPO_SKIP].post_kick_status == "unchanged"
    assert by_surface[CODEX_KICK_SURFACE_REPO].confirmed is False
    assert by_surface[CODEX_KICK_SURFACE_REPO].post_kick_status == "superseded"
    assert "post=superseded" in _history_event_details(by_surface[CODEX_KICK_SURFACE_REPO])
    assert "attribution=strong" in _history_event_details(by_surface[CODEX_KICK_SURFACE_LEGACY])
    assert "Superseded by reset-clock match" in _history_event_details(
        by_surface[CODEX_KICK_SURFACE_REPO]
    )
    assert codex_surface_order_for_account(account, stats_file)[0] == CODEX_KICK_SURFACE_LEGACY
    assert (
        _apply_codex_late_attribution(
            [account],
            {account_key_string(account): status},
        )
        == 0
    )


def test_codex_late_attribution_timing_match_does_not_teach_surface_order(
    monkeypatch,
    tmp_path,
):
    import tokenkick.models as models_mod

    now = 100_000.0
    base = now - 2_000.0
    history_file = tmp_path / "history.jsonl"
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
    )
    cluster_id = "cluster-timing"
    events = [
        KickEvent(
            label=account.label,
            timestamp=base,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base,
            codex_attempt_finished_at=base + 10,
            codex_confirmation_method="none",
        ),
        KickEvent(
            label=account.label,
            timestamp=base + 300,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_LEGACY,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base + 300,
            codex_attempt_finished_at=base + 310,
            codex_confirmation_method="none",
        ),
        KickEvent(
            label=account.label,
            timestamp=base + 600,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            error=CODEX_SESSION_ANCHOR_NOT_OBSERVED_ERROR,
            post_kick_status="unchanged",
            codex_surface=CODEX_KICK_SURFACE_REPO,
            codex_cluster_id=cluster_id,
            codex_attempt_started_at=base + 600,
            codex_attempt_finished_at=base + 610,
            codex_confirmation_method="none",
        ),
    ]
    history_file.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in events))
    update_codex_surface_stats(stats_file, account, events)
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        session_used_percent=100.0,
        session_resets_at=base + 430.6 + 300 * 60,
        session_window_minutes=300,
    )

    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: stats_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)

    changed = _apply_codex_late_attribution(
        [account],
        {account_key_string(account): status},
    )

    assert changed == 1
    updated = models_mod.load_kick_history(limit=10)
    by_surface = {event.codex_surface: event for event in updated}
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].confirmed is True
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].codex_attribution == CODEX_ATTRIBUTION_TIMING_MATCH
    assert "attribution=timing_match" in _history_event_details(
        by_surface[CODEX_KICK_SURFACE_LEGACY]
    )
    assert by_surface[CODEX_KICK_SURFACE_LEGACY].codex_anchor_match_delta_seconds == pytest.approx(
        120.6
    )
    assert codex_surface_order_for_account(account, stats_file)[0] == CODEX_KICK_SURFACE_REPO_SKIP


def test_burst_late_attribution_trains_scorer_from_persisted_origin(monkeypatch, tmp_path):
    import tokenkick.models as models_mod

    now = 100_000.0
    base = now - 2_000.0
    history_file = tmp_path / "history.jsonl"
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
        codex_surface_auto_demote=True,
    )
    cluster_id = "burst-cluster"
    events = [
        KickEvent(
            label=account.label,
            timestamp=base,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            post_kick_status="pending",
            codex_confirmation_method="pending_reset_clock",
            codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            codex_cluster_id=cluster_id,
            codex_cluster_origin="burst",
            codex_attempt=1,
            codex_attempt_started_at=base,
            codex_attempt_finished_at=base + 10,
        ),
        KickEvent(
            label=account.label,
            timestamp=base + 90,
            success=True,
            confirmed=False,
            kind="session",
            kick_type="session",
            response_text="TokenKick anchor probe completed.",
            evidence_response=True,
            post_kick_status="pending",
            codex_confirmation_method="pending_reset_clock",
            codex_surface=CODEX_KICK_SURFACE_LEGACY,
            codex_cluster_id=cluster_id,
            codex_cluster_origin="burst",
            codex_attempt=2,
            codex_attempt_started_at=base + 90,
            codex_attempt_finished_at=base + 100,
        ),
    ]
    history_file.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in events))
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        session_used_percent=100.0,
        session_resets_at=base + 300 * 60,
        session_window_minutes=300,
    )

    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: stats_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)

    changed = _apply_codex_late_attribution(
        [account],
        {account_key_string(account): status},
    )

    assert changed == 1
    updated = models_mod.load_kick_history(limit=10)
    assert {event.codex_cluster_origin for event in updated} == {"burst"}
    report = codex_surface_stats_for_account(account, stats_file)
    by_surface = {surface["surface"]: surface for surface in report["surfaces"]}
    assert by_surface[CODEX_KICK_SURFACE_REPO_SKIP]["attempts"] == 1
    assert by_surface[CODEX_KICK_SURFACE_REPO_SKIP]["confirmed"] == 1
    assert by_surface[CODEX_KICK_SURFACE_LEGACY]["attempts"] == 1


def test_adaptive_late_attribution_does_not_use_burst_training_hook(monkeypatch, tmp_path):
    import tokenkick.models as models_mod

    now = 100_000.0
    base = now - 2_000.0
    history_file = tmp_path / "history.jsonl"
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
    )
    event = KickEvent(
        label=account.label,
        timestamp=base,
        success=True,
        confirmed=False,
        kind="session",
        kick_type="session",
        response_text="TokenKick anchor probe completed.",
        evidence_response=True,
        post_kick_status="pending",
        codex_confirmation_method="pending_reset_clock",
        codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
        codex_cluster_id="adaptive-cluster",
        codex_cluster_origin="adaptive",
        codex_attempt_started_at=base,
        codex_attempt_finished_at=base + 10,
    )
    history_file.write_text(json.dumps(event.to_dict()) + "\n")
    status = AccountStatus(
        label=account.label,
        state=AccountState.ACTIVE,
        session_used_percent=100.0,
        session_resets_at=base + 300 * 60,
        session_window_minutes=300,
    )
    late_confirmations = []
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: stats_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)
    monkeypatch.setattr(
        "tokenkick.cli.update_codex_surface_stats",
        lambda *_args: pytest.fail("adaptive clusters must not use burst training hook"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.apply_codex_surface_late_confirmation",
        lambda _path, _account, confirmed_event: late_confirmations.append(confirmed_event.codex_cluster_origin),
    )

    changed = _apply_codex_late_attribution(
        [account],
        {account_key_string(account): status},
    )

    assert changed == 1
    assert late_confirmations == ["adaptive"]


def test_burst_late_attribution_accumulates_demotion_evidence(monkeypatch, tmp_path):
    import tokenkick.models as models_mod

    now = 100_000.0
    history_file = tmp_path / "history.jsonl"
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
        codex_surface_auto_demote=True,
        codex_surface_demote_after_strong_clusters=5,
        codex_surface_demote_measurement_clusters=20,
    )
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: stats_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: now)
    history_file.write_text("")

    for index in range(5):
        base = 50_000.0 + index * 1_000
        cluster_id = f"burst-{index}"
        events = []
        for attempt, surface in enumerate(CODEX_FIRE_ALL_SURFACES, start=1):
            events.append(
                KickEvent(
                    label=account.label,
                    timestamp=base + attempt,
                    success=True,
                    confirmed=False,
                    kind="session",
                    kick_type="session",
                    response_text="TokenKick anchor probe completed.",
                    evidence_response=True,
                    post_kick_status="pending",
                    codex_confirmation_method="pending_reset_clock",
                    codex_surface=surface,
                    codex_cluster_id=cluster_id,
                    codex_cluster_origin="burst",
                    codex_attempt=attempt,
                    codex_attempt_started_at=base + attempt,
                    codex_attempt_finished_at=base + attempt + 0.5,
                )
            )
        with history_file.open("a") as handle:
            handle.write("".join(json.dumps(event.to_dict()) + "\n" for event in events))
        status = AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            session_used_percent=100.0,
            session_resets_at=(base + 1.25) + 300 * 60,
            session_window_minutes=300,
        )

        changed = _apply_codex_late_attribution(
            [account],
            {account_key_string(account): status},
        )

        assert changed == 1

    report = codex_surface_stats_for_account(account, stats_file)

    assert report["demotion"]["strong_cluster_count"] == 5
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE in report["demotion"]["demoted"]


def test_verified_phantom_recovery_attempts_and_records_state(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    calls = []
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    notified = []
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, _notifications: notified.append(event),
    )

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert deferred == []
    assert calls == [
        {
            "record": False,
            "phantom_recovery": True,
            "model_override": None,
            "codex_surface": CODEX_KICK_SURFACE_REPO_SKIP,
        }
    ]
    assert len(notified) == 1
    assert notified[0].kind == "phantom_recovery"
    assert notified[0].confirmed is False
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1
    assert state["status"] == "recovering"


def test_verified_phantom_recovery_retries_surfaces_within_one_attempt(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    calls = []
    recorded = []
    notified = []
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )

    def fake_kick(candidate, **kwargs):
        calls.append(kwargs)
        if kwargs["codex_surface"] == CODEX_KICK_SURFACE_REPO_SKIP:
            return KickEvent(
                label=candidate.label,
                success=True,
                confirmed=False,
                error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
            )
        return KickEvent(
            label=candidate.label,
            success=True,
            confirmed=True,
            response_text="TokenKick anchor probe completed.",
        )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr("tokenkick.cli.kick_account", fake_kick)
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, _notifications: notified.append(event),
    )

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert deferred == []
    assert [call["codex_surface"] for call in calls] == [
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    ]
    assert len(recorded) == 4
    assert recorded[0].confirmed is False
    assert recorded[0].error == CODEX_NO_GENERATION_EVIDENCE_ERROR
    assert recorded[0].codex_surface == CODEX_KICK_SURFACE_REPO_SKIP
    assert recorded[1].confirmed is False
    assert recorded[1].error == "Codex accepted usage, but session status is still ambiguous"
    assert recorded[1].post_kick_status == "phantom"
    assert recorded[1].evidence_provider_moved is False
    assert recorded[1].codex_surface == CODEX_KICK_SURFACE_LEGACY
    assert recorded[2].confirmed is False
    assert recorded[2].error == "Codex accepted usage, but session status is still ambiguous"
    assert recorded[2].post_kick_status == "phantom"
    assert recorded[2].evidence_provider_moved is False
    assert recorded[2].codex_surface == CODEX_KICK_SURFACE_REPO
    assert recorded[3].confirmed is False
    assert recorded[3].error == "Codex accepted usage, but session status is still ambiguous"
    assert recorded[3].post_kick_status == "phantom"
    assert recorded[3].evidence_provider_moved is False
    assert recorded[3].codex_surface == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    assert notified == [recorded[3]]
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1
    assert state["status"] == "provider_accepted"


def test_verified_phantom_recovery_retries_after_current_window_kick(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.ACTIVE,
        used_percent=26.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    calls = []
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )
    history = [
        KickEvent(
            label=account.label,
            timestamp=9_900.0,
            success=True,
            confirmed=True,
            kind="session",
            kick_type="session",
        )
    ]

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args, **_kwargs: None)

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert deferred == []
    assert calls == [
        {
            "record": False,
            "phantom_recovery": True,
            "model_override": None,
            "codex_surface": CODEX_KICK_SURFACE_REPO_SKIP,
        }
    ]
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1


def test_verified_phantom_recovery_skips_weekly_exhausted_account(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (personal)",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (personal)",
        state=AccountState.ACTIVE,
        used_percent=100.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("weekly-exhausted account should not kick"),
    )

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 0
    assert deferred == []


def test_verified_phantom_recovery_handles_partial_weekly_usage(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (primaryaccount)",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (primaryaccount)",
        state=AccountState.ACTIVE,
        used_percent=16.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    calls = []
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args, **_kwargs: None)

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert deferred == []
    assert calls == [
        {
            "record": False,
            "phantom_recovery": True,
            "model_override": None,
            "codex_surface": CODEX_KICK_SURFACE_REPO_SKIP,
        }
    ]
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1


def test_verified_phantom_recovery_keeps_state_when_provider_accepts_usage(monkeypatch, tmp_path):
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="codex (work)",
        state=AccountState.ACTIVE,
        used_percent=26.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(
            label=candidate.label,
            success=True,
            input_tokens=123,
            output_tokens=4,
        ),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args, **_kwargs: None)

    executed, deferred = _execute_verified_phantom_recoveries(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert deferred == []
    assert phantom_file.exists()
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1
    assert state["status"] == "provider_accepted"
    assert state["cooldown_until"] == 12_700.0
    assert "Codex accepted usage" in state["last_error"]


def test_verified_phantom_recovery_uses_default_model_without_override(monkeypatch, tmp_path):
    account = AccountConfig(label="phantom", provider="codex")
    assert _phantom_recovery_model_for_attempt(account, 1) is None
    assert _phantom_recovery_model_for_attempt(account, 2) is None
    assert _phantom_recovery_model_for_attempt(account, 3) is None


def test_verified_phantom_recovery_defers_between_attempts(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    recovery_file = tmp_path / "recovery.json"
    key = account_key_string(account)
    recovery_file.write_text(
        json.dumps(
            {
                key: {
                    "first_started_at": 10_000.0,
                    "last_seen_at": 10_000.0,
                    "last_attempt_at": 10_000.0,
                    "attempts": 1,
                    "status": "recovering",
                }
            }
        )
    )

    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_010.0)

    targets, deferred = _kickable_window_targets(
        [account],
        {account_key_string(account): status},
        Config(accounts=[account]),
        manage_phantom_recovery=True,
    )

    assert targets == []
    assert deferred
    assert deferred[0][2] == 35
    assert _status_action(status, {"phantom": "codex"}, account) == "Phantom recovery 1/5"


def test_verified_phantom_recovery_retries_after_cooldown(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    key = account_key_string(account)
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 12_800.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )
    recovery_file.write_text(
        json.dumps(
            {
                key: {
                    "first_started_at": 10_000.0,
                    "last_seen_at": 10_000.0,
                    "last_attempt_at": 10_000.0,
                    "attempts": 5,
                    "status": "cooldown",
                    "cooldown_until": 12_700.0,
                }
            }
        )
    )
    calls = []

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 12_800.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **kwargs: calls.append(kwargs)
        or KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args, **_kwargs: None)

    executed, _deferred = _execute_verified_phantom_recoveries(
        [account],
        {key: status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert calls[0]["model_override"] is None
    state = json.loads(recovery_file.read_text())[key]
    assert state["attempts"] == 1
    assert state["status"] == "recovering"


def test_verified_phantom_recovery_final_failure_notifies_once(monkeypatch, tmp_path):
    account = AccountConfig(
        label="phantom",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    key = account_key_string(account)
    phantom_file = tmp_path / "phantom.json"
    recovery_file = tmp_path / "recovery.json"
    phantom_file.write_text(
        json.dumps(
            {
                key: {
                    "first_seen_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "observations": 2,
                    "first_session_resets_in_seconds": 17940,
                }
            }
        )
    )
    recovery_file.write_text(
        json.dumps(
            {
                key: {
                    "first_started_at": 9_000.0,
                    "last_seen_at": 10_000.0,
                    "last_attempt_at": 9_000.0,
                    "attempts": 4,
                    "status": "recovering",
                }
            }
        )
    )
    notified = []

    monkeypatch.setattr("tokenkick.cli.PHANTOM_SESSION_FILE", phantom_file)
    monkeypatch.setattr("tokenkick.cli.PHANTOM_RECOVERY_FILE", recovery_file)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_000.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda candidate, config=None: status)
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._mark_status_cache_entry_stale", lambda _account: True)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_kick",
        lambda event, _notifications: notified.append(event),
    )

    executed, _deferred = _execute_verified_phantom_recoveries(
        [account],
        {key: status},
        Config(accounts=[account]),
        daemon_log=False,
        stagger_state=None,
    )

    assert executed == 1
    assert len(notified) == 1
    assert notified[0].success is False
    assert notified[0].kind == "phantom_recovery"
    assert "after 5 attempts" in (notified[0].error or "")


def test_recent_ambiguous_phantom_attempt_backs_off_auto_kick(monkeypatch, capsys):
    account = AccountConfig(label="phantom", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="phantom",
            timestamp=10_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        )
    ]

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 10_300.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: (_ for _ in ()).throw(AssertionError("kick should back off")),
    )

    _kick_all_enabled_accounts(
        [account],
        Config(accounts=[account]),
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert "[kick_backoff]" in output
    assert 'account="phantom"' in output
    assert 'reason="ambiguous_phantom_after_kick"' in output


def test_expired_ambiguous_phantom_backoff_allows_retry(monkeypatch):
    account = AccountConfig(label="phantom", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    history = [
        KickEvent(
            label="phantom",
            timestamp=10_000.0,
            success=True,
            confirmed=False,
            kind="probe",
            error="Provider still reports a tiny phantom session after the kick attempt",
        )
    ]
    kicked = []

    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 14_000.0)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: history)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda account: status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    _kick_all_enabled_accounts(
        [account],
        Config(accounts=[account]),
        targets=[(account, status)],
        deferred=[],
    )

    assert kicked == ["phantom"]


def test_phantom_kick_records_confirmed_when_status_changes(monkeypatch):
    recorded = []
    accounts = [AccountConfig(label="phantom", provider="codex")]
    phantom_status = AccountStatus(
        label="phantom",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=17940,
        session_window_minutes=300,
    )
    active_status = AccountStatus(
        label="phantom",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=2.0,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: ([(accounts[0], phantom_status)], []),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda account: active_status)
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "--all"])

    assert result.exit_code == 0
    assert recorded[0].success is True
    assert recorded[0].confirmed is True
    assert recorded[0].kind == "phantom_recovery"
    assert recorded[0].kick_type == "session"


def test_kick_all_dry_run_previews_without_kicking(monkeypatch):
    kicked = []
    accounts = [AccountConfig(label="fresh", provider="codex")]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr(
        "tokenkick.cli._kickable_window_targets",
        lambda loaded, **_kwargs: (
            [
                (
                    accounts[0],
                    AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0),
                )
            ],
            [],
        ),
    )
    monkeypatch.setattr("tokenkick.cli.kick_account", lambda account: kicked.append(account.label))

    result = CliRunner().invoke(cli, ["kick", "--all", "--dry-run"])

    assert result.exit_code == 0
    assert "Would kick:" in result.output
    assert "fresh" in result.output
    assert kicked == []


def test_kick_enable_and_disable_toggle_codex_auto_kick(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=False),
        AccountConfig(label="claude", provider="claude", auto_kick=False),
        AccountConfig(label="gemini", provider="gemini", auto_kick=False),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            accounts=accounts,
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
            poll_interval_minutes=13,
        ),
    )
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    enabled = CliRunner().invoke(cli, ["kick", "--enable", "codex"], input="ENABLE\n")
    enabled_claude = CliRunner().invoke(
        cli, ["kick", "--enable", "claude"], input="ENABLE\n"
    )
    disabled = CliRunner().invoke(cli, ["kick", "--disable", "codex"])

    assert enabled.exit_code == 0
    assert enabled_claude.exit_code == 0
    assert disabled.exit_code == 0
    assert saved[0].accounts[0].auto_kick is True
    assert saved[1].accounts[1].auto_kick is True
    assert saved[2].accounts[0].auto_kick is False
    assert saved[0].notifications.ntfy_topic == "topic"
    assert saved[0].poll_interval_minutes == 13


def test_auto_enable_and_disable_toggle_auto_kick(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=False),
        AccountConfig(label="claude", provider="claude", auto_kick=False),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=accounts, poll_interval_minutes=13),
    )
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    enabled = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="ENABLE\n")
    disabled = CliRunner().invoke(cli, ["auto", "disable", "claude"])

    assert enabled.exit_code == 0
    assert disabled.exit_code == 0
    assert saved[0].accounts[0].auto_kick is True
    assert saved[0].accounts[0].weekly_auto_kick is True
    assert saved[0].accounts[0].session_auto_kick is True
    assert saved[1].accounts[1].auto_kick is False
    assert saved[1].accounts[1].weekly_auto_kick is False
    assert saved[1].accounts[1].session_auto_kick is False
    assert saved[0].poll_interval_minutes == 13


def test_auto_enable_requires_exact_provider_consent():
    Config(accounts=[AccountConfig(label="codex", provider="codex")]).save()

    cancelled = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="\n")
    assert Config.load().accounts[0].auto_kick is False
    rejected = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="enable\n")
    assert Config.load().accounts[0].auto_kick is False
    accepted = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="ENABLE\n")

    assert cancelled.exit_code == 0
    assert rejected.exit_code == 0
    assert Config.load().accounts[0].auto_kick is True
    assert "Auto-kick remains off for Codex" in cancelled.output
    assert "Auto-kick remains off for Codex" in rejected.output
    assert "Whether scheduled kicking falls under that is unsettled" in accepted.output
    assert Config.load().has_auto_kick_consent("codex") is True


def test_provider_consent_is_requested_once_and_kept_separate():
    Config(
        accounts=[
            AccountConfig(label="codex", provider="codex"),
            AccountConfig(label="claude", provider="claude"),
        ]
    ).save()

    codex = CliRunner().invoke(cli, ["auto", "enable", "codex"], input="ENABLE\n")
    disabled = CliRunner().invoke(cli, ["auto", "disable", "codex"])
    codex_again = CliRunner().invoke(cli, ["auto", "session", "enable", "codex"])
    claude = CliRunner().invoke(cli, ["auto", "weekly", "enable", "claude"], input="ENABLE\n")

    assert codex.exit_code == 0
    assert disabled.exit_code == 0
    assert codex_again.exit_code == 0
    assert "Enabling auto-kick" not in codex_again.output
    assert "Anthropic's Consumer Terms restrict automated or scripted access" in claude.output
    assert Config.load().auto_kick_consents == {"codex": 1, "claude": 1}


def test_accounts_notification_commands_toggle_account_notifications(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", notifications_enabled=True),
        AccountConfig(label="claude", provider="claude", notifications_enabled=False),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=accounts, poll_interval_minutes=13),
    )
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    disabled = CliRunner().invoke(cli, ["accounts", "disable-notifications", "codex"])
    enabled = CliRunner().invoke(cli, ["accounts", "enable-notifications", "claude"])

    assert disabled.exit_code == 0
    assert enabled.exit_code == 0
    assert saved[0].accounts[0].notifications_enabled is False
    assert saved[1].accounts[1].notifications_enabled is True
    assert saved[0].poll_interval_minutes == 13


def test_accounts_set_notifications_sets_backend_routes(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="claude", provider="claude"),
    ]

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=accounts, poll_interval_minutes=13),
    )
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    telegram = CliRunner().invoke(cli, ["accounts", "set-notifications", "codex", "--telegram"])
    both = CliRunner().invoke(
        cli,
        ["accounts", "set-notifications", "claude", "--ntfy", "--telegram"],
    )
    disabled = CliRunner().invoke(cli, ["accounts", "set-notifications", "codex", "--none"])

    assert telegram.exit_code == 0
    assert both.exit_code == 0
    assert disabled.exit_code == 0
    assert saved[0].accounts[0].notifications_enabled is True
    assert saved[0].accounts[0].notification_backends == ["telegram"]
    assert saved[1].accounts[1].notification_backends == ["ntfy", "telegram"]
    assert saved[2].accounts[0].notifications_enabled is False
    assert saved[2].accounts[0].notification_backends == []
    assert saved[0].poll_interval_minutes == 13


def test_accounts_notifications_status_reflects_disabled_account(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex", notifications_enabled=False),
        AccountConfig(label="claude", provider="claude", notification_backends=["telegram"]),
    ]
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            accounts=accounts,
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=["ntfy", "telegram"],
            ),
        ),
    )
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)

    result = CliRunner().invoke(cli, ["accounts", "notifications"])

    assert result.exit_code == 0
    assert "TokenKick Account Notifications" in result.output
    assert "codex" in result.output
    assert "❌ disabled" in result.output
    assert "claude" in result.output
    assert "✅ telegram" in result.output
    assert "ntfy:topic" in result.output
    assert "telegram:chat-id" in result.output


def test_auto_session_enable_and_disable_toggle_session_auto_kick(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", session_auto_kick=False),
        AccountConfig(label="claude", provider="claude", session_auto_kick=True),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    enabled = CliRunner().invoke(
        cli, ["auto", "session", "enable", "codex"], input="ENABLE\n"
    )
    disabled = CliRunner().invoke(cli, ["auto", "session", "disable", "claude"])

    assert enabled.exit_code == 0
    assert disabled.exit_code == 0
    assert saved[0].accounts[0].session_auto_kick is True
    assert saved[1].accounts[1].session_auto_kick is False
    assert saved[0].accounts[0].auto_kick is False
    assert saved[1].accounts[1].auto_kick is False


def test_auto_weekly_enable_and_disable_toggle_weekly_auto_kick(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=False, weekly_auto_kick=False),
        AccountConfig(label="claude", provider="claude", auto_kick=True, weekly_auto_kick=True),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    enabled = CliRunner().invoke(
        cli, ["auto", "weekly", "enable", "codex"], input="ENABLE\n"
    )
    disabled = CliRunner().invoke(cli, ["auto", "weekly", "disable", "claude"])

    assert enabled.exit_code == 0
    assert disabled.exit_code == 0
    assert saved[0].accounts[0].weekly_auto_kick is True
    assert saved[1].accounts[1].weekly_auto_kick is False
    assert saved[0].accounts[0].auto_kick is False
    assert saved[1].accounts[1].auto_kick is True


def test_auto_toggles_reject_gemini(monkeypatch):
    saved: list[Config] = []
    accounts = [AccountConfig(label="gemini", provider="gemini")]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    auto_result = CliRunner().invoke(cli, ["auto", "enable", "gemini"])
    session_result = CliRunner().invoke(cli, ["auto", "session", "enable", "gemini"])
    weekly_result = CliRunner().invoke(cli, ["auto", "weekly", "enable", "gemini"])

    assert auto_result.exit_code == 1
    assert session_result.exit_code == 1
    assert weekly_result.exit_code == 1
    assert "Gemini is monitor-only; auto-kick cannot be enabled." in auto_result.output
    assert "Gemini is monitor-only; auto-kick cannot be enabled." in session_result.output
    assert "Gemini is monitor-only; auto-kick cannot be enabled." in weekly_result.output
    assert saved == []


def test_auto_toggles_reject_antigravity(monkeypatch):
    saved: list[Config] = []
    accounts = [AccountConfig(label="antigravity", provider="antigravity")]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    auto_result = CliRunner().invoke(cli, ["auto", "enable", "antigravity"])
    session_result = CliRunner().invoke(cli, ["auto", "session", "enable", "antigravity"])
    weekly_result = CliRunner().invoke(cli, ["auto", "weekly", "enable", "antigravity"])

    assert auto_result.exit_code == 1
    assert session_result.exit_code == 1
    assert weekly_result.exit_code == 1
    assert "Antigravity is monitor-only; auto-kick cannot be enabled." in auto_result.output
    assert "Antigravity is monitor-only; auto-kick cannot be enabled." in session_result.output
    assert "Antigravity is monitor-only; auto-kick cannot be enabled." in weekly_result.output
    assert saved == []


def test_account_config_auto_kick_defaults_and_json_overrides():
    account = AccountConfig.from_dict({"label": "codex"})
    migrated = AccountConfig.from_dict({"label": "codex", "auto_kick": True})
    session_opt_out = AccountConfig.from_dict(
        {"label": "codex", "auto_kick": True, "session_auto_kick": False}
    )

    assert account.session_auto_kick is False
    assert account.weekly_auto_kick is False
    assert migrated.session_auto_kick is True
    assert migrated.weekly_auto_kick is True
    assert session_opt_out.session_auto_kick is False
    assert account.kick_model is None
    assert "session_auto_kick" not in account.to_dict()
    assert session_opt_out.to_dict()["session_auto_kick"] is False
    assert (
        AccountConfig(label="codex", auto_kick=True, weekly_auto_kick=False).to_dict()[
            "weekly_auto_kick"
        ]
        is False
    )
    assert AccountConfig(label="codex", weekly_auto_kick=True).to_dict()["weekly_auto_kick"] is True
    assert AccountConfig(label="codex", session_auto_kick=True).to_dict()["session_auto_kick"] is True
    assert AccountConfig(label="codex", kick_model="custom").to_dict()["kick_model"] == "custom"


def test_config_load_migrates_gemini_to_monitor_only(tmp_path, monkeypatch, capsys):
    _isolate_config_files(monkeypatch, tmp_path)
    Config(
        accounts=[
            AccountConfig(
                label="gemini",
                provider="gemini",
                auto_kick=True,
                session_auto_kick=True,
            )
        ]
    ).save()

    loaded = Config.load()
    first_notice = capsys.readouterr().err
    loaded_again = Config.load()
    second_notice = capsys.readouterr().err

    assert loaded.accounts[0].auto_kick is False
    assert loaded.accounts[0].weekly_auto_kick is False
    assert loaded.accounts[0].session_auto_kick is False
    assert loaded_again.accounts[0].auto_kick is False
    assert "Gemini auto-kick has been disabled" in first_notice
    assert second_notice == ""


def test_auto_status_shows_enabled_disabled_and_non_kickable(monkeypatch):
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=True, session_auto_kick=True),
        AccountConfig(label="claude", provider="claude", auto_kick=False, visible=False),
        AccountConfig(label="gemini", provider="gemini", auto_kick=True),
        AccountConfig(label="openrouter", provider="openrouter", auto_kick=True),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)

    result = CliRunner().invoke(cli, ["auto"])

    assert result.exit_code == 0
    assert "Auto-kick" in result.output
    assert "Weekly" in result.output
    assert "Session" in result.output
    assert "codex" in result.output
    assert "✅ enabled" in result.output
    assert "claude" in result.output
    assert "❌ disabled" in result.output
    assert "hidden" in result.output
    assert "gemini" in result.output
    assert "openrouter" in result.output
    assert "kickable)" in result.output
    assert "Auto-kick status printed at" in result.output


def test_auto_help_includes_weekly_and_session_commands():
    result = CliRunner().invoke(cli, ["auto", "--help"])

    assert result.exit_code == 0
    assert "weekly" in result.output
    assert "session" in result.output
    assert "enable" in result.output
    assert "disable" in result.output


def test_accounts_set_and_clear_kick_model(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="openrouter", provider="openrouter"),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    set_result = CliRunner().invoke(cli, ["accounts", "set-kick-model", "codex", "custom-mini"])
    clear_result = CliRunner().invoke(cli, ["accounts", "clear-kick-model", "codex"])
    unsupported_result = CliRunner().invoke(
        cli, ["accounts", "set-kick-model", "openrouter", "cheap"]
    )

    assert set_result.exit_code == 0
    assert clear_result.exit_code == 0
    assert unsupported_result.exit_code == 0
    assert saved[0].accounts[0].kick_model == "custom-mini"
    assert saved[1].accounts[0].kick_model is None
    assert "support kick models" in unsupported_result.output


def test_model_alias_set_and_clear_kick_model(monkeypatch):
    saved: list[Config] = []
    accounts = [AccountConfig(label="codex", provider="codex")]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    set_result = CliRunner().invoke(cli, ["model", "set", "codex", "gpt-5.5"])
    clear_result = CliRunner().invoke(cli, ["model", "clear", "codex"])

    assert set_result.exit_code == 0
    assert clear_result.exit_code == 0
    assert saved[0].accounts[0].kick_model == "gpt-5.5"
    assert saved[1].accounts[0].kick_model is None


def test_accounts_list_hide_and_show_manage_visibility(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", visible=True),
        AccountConfig(label="gemini", provider="gemini", visible=False),
    ]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    listed = CliRunner().invoke(cli, ["accounts"])
    hidden = CliRunner().invoke(cli, ["accounts", "hide", "codex"])
    shown = CliRunner().invoke(cli, ["accounts", "show", "gemini"])

    assert listed.exit_code == 0
    assert "Kick model" in listed.output
    assert "default" in listed.output
    assert "codex" in listed.output
    assert "✅ visible" in listed.output
    assert "gemini" in listed.output
    assert "❌ hidden" in listed.output
    assert hidden.exit_code == 0
    assert shown.exit_code == 0
    assert saved[0].accounts[0].visible is False
    assert saved[1].accounts[1].visible is True


def test_kick_enable_rejects_non_kickable_accounts(monkeypatch):
    saved: list[Config] = []
    accounts = [AccountConfig(label="openrouter", provider="openrouter", auto_kick=False)]

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: accounts)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["kick", "--enable", "openrouter"])

    assert result.exit_code == 0
    assert "only Codex and Claude accounts support" in result.output
    assert "auto-kick" in result.output
    assert saved == []


def test_kick_specific_skips_non_fresh_account(monkeypatch):
    account = AccountConfig(label="active", provider="codex")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded: AccountStatus(label="active", state=AccountState.ACTIVE),
    )

    result = CliRunner().invoke(cli, ["kick", "active"])

    assert result.exit_code == 0
    assert "not fresh" in result.output


def test_kick_specific_uses_saved_label_then_fetches_live_status(monkeypatch):
    account = AccountConfig(label="active", provider="codex")
    fetched = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    def fake_fetch(loaded):
        fetched.append(loaded.label)
        return AccountStatus(label=loaded.label, state=AccountState.ACTIVE)

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)

    result = CliRunner().invoke(cli, ["kick", "active"])

    assert result.exit_code == 0
    assert fetched == ["active"]
    assert "not fresh" in result.output


def test_kick_specific_warns_and_confirms_stale_fresh_status(monkeypatch):
    account = AccountConfig(label="fresh", provider="codex")
    kicked = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.FRESH,
            stale=True,
            stale_seconds=9_960,
            source_detail="codexbar-history",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: kicked.append(loaded.label)
        or KickEvent(label=loaded.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)

    result = CliRunner().invoke(cli, ["kick", "fresh"], input="y\n")

    assert result.exit_code == 0
    assert (
        "Warning: codexbar-history is stale (2h 46m old); automatic kick is blocked "
        "until fresh data is available."
    ) in result.output
    assert "(\n2h 46m old\n)" not in result.output
    assert "Kick \"fresh\" anyway?" in result.output
    assert kicked == ["fresh"]


@pytest.mark.parametrize("success", [True, False])
def test_kick_specific_stale_confirmation_executes_and_logs_regardless_outcome(
    monkeypatch,
    success,
):
    account = AccountConfig(label="codex (work)", provider="codex")
    recorded = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.FRESH,
            stale=True,
            stale_seconds=9_960,
            source_detail="codexbar-history",
            session_used_percent=1.0,
            session_resets_in_seconds=9_960,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: KickEvent(
            label=loaded.label,
            success=success,
            error=None if success else "codex exited 1",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["kick", "codex (work)"], input="y\n")

    assert result.exit_code == 0
    assert len(recorded) == 1
    assert recorded[0].label == "codex (work)"
    assert recorded[0].success is success
    assert "Waiting to kick" not in result.output


def test_confirm_prompt_foregrounds_tty_process_group(monkeypatch, capsys):
    writes = []
    reads = iter([b"y", b"\n"])
    tcset_calls = []
    signal_calls = []

    monkeypatch.setattr("tokenkick.cli.os.open", lambda path, flags: 99)
    monkeypatch.setattr("tokenkick.cli.os.close", lambda fd: None)
    monkeypatch.setattr("tokenkick.cli.os.getpid", lambda: 1234)
    monkeypatch.setattr("tokenkick.cli.os.getpgrp", lambda: 200)
    monkeypatch.setattr("tokenkick.cli.os.tcgetpgrp", lambda fd: 100)
    monkeypatch.setattr(
        "tokenkick.cli.os.tcsetpgrp",
        lambda fd, pgrp: tcset_calls.append((fd, pgrp)),
    )
    monkeypatch.setattr("tokenkick.cli.os.write", lambda fd, data: writes.append(data))
    monkeypatch.setattr("tokenkick.cli.os.read", lambda fd, size: next(reads))
    monkeypatch.setattr("tokenkick.cli.signal.getsignal", lambda sig: "old-handler")
    monkeypatch.setattr(
        "tokenkick.cli.signal.signal",
        lambda sig, handler: signal_calls.append((sig, handler)),
    )

    assert _confirm_prompt("Kick?", default=False) is True

    err = capsys.readouterr().err
    assert "Prompt process groups: pid=1234 pgrp=200" in err
    assert "stdin_tty_pgrp=None dev_tty_pgrp=100" in err
    assert "Using prompt method: dev-tty" in err
    assert writes == [b"Kick? [y/N]: "]
    assert tcset_calls == [(99, 200), (99, 100)]
    assert signal_calls[0] == (signal.SIGTTOU, signal.SIG_IGN)
    assert signal_calls[-1] == (signal.SIGTTOU, "old-handler")


def test_wake_kicks_stale_fresh_account_without_prompt_and_logs_wake_type(monkeypatch):
    account = AccountConfig(label="codex (work)", provider="codex")
    recorded = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: [account])
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda loaded, **_kwargs: AccountStatus(
            label=loaded.label,
            state=AccountState.FRESH,
            stale=True,
            stale_seconds=9_960,
            source_detail="codexbar-history",
            session_used_percent=1.0,
            session_resets_in_seconds=9_960,
            session_window_minutes=300,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda loaded, **_kwargs: KickEvent(label=loaded.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    result = CliRunner().invoke(cli, ["wake", "codex (work)"])

    assert result.exit_code == 0
    assert "Kick \"codex (work)\" anyway?" not in result.output
    assert len(recorded) == 1
    assert recorded[0].label == "codex (work)"
    assert recorded[0].kind == "wake"
    assert recorded[0].to_dict()["kick_type"] == "wake"


def test_history_account_filter_resolves_pre_migration_label(monkeypatch):
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_email="work@example.test",
    )
    events = [
        KickEvent(label="work (codex)", timestamp=1_779_000_000.0, success=True),
        KickEvent(label="other", timestamp=1_779_000_100.0, success=True),
    ]
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=20: events)

    result = CliRunner().invoke(
        cli,
        ["history", "--account", "codex (work)", "--json-output"],
    )

    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert [row["label"] for row in rows] == ["work (codex)"]


def test_kick_specific_honors_configured_codexbar_thresholds(monkeypatch):
    account = AccountConfig(label="active", provider="codex")
    captured = {}
    config = Config(
        accounts=[account],
        codexbar_staleness_threshold_seconds=42,
        codexbar_rejection_threshold_seconds=84,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)

    def fake_fetch(
        loaded,
        *,
        codexbar_staleness_threshold_seconds,
        codexbar_rejection_threshold_seconds,
    ):
        captured["stale"] = codexbar_staleness_threshold_seconds
        captured["reject"] = codexbar_rejection_threshold_seconds
        return AccountStatus(label=loaded.label, state=AccountState.ACTIVE)

    monkeypatch.setattr("tokenkick.cli.fetch_status", fake_fetch)

    result = CliRunner().invoke(cli, ["kick", "active"])

    assert result.exit_code == 0
    assert captured == {"stale": 42, "reject": 84}


def test_status_json_always_includes_freshness_fields(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    status = AccountStatus(label="codex", state=AccountState.ACTIVE, used_percent=1)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._status_cache_observed_at", lambda: "2026-05-22T08:00:00Z")
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: ([account], [status], False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli, ["status", "--json-output", "--refresh"])

    assert result.exit_code == 0
    rows = _status_json_accounts(result.output)
    assert rows[0]["observed_at"] == "2026-05-22T08:00:00Z"
    assert rows[0]["source_detail"] == "manual"
    assert rows[0]["stale"] is False
    assert rows[0]["stale_seconds"] is None


def test_status_surfaces_exact_codexbar_failure_next_step(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    message = (
        "CodexBar data schema version mismatch: expected 1, got 9. "
        "Update TokenKick, then run tk status --refresh."
    )
    status = AccountStatus(label="codex", state=AccountState.UNKNOWN, error=message)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        lambda _config: ([account], [status], False, "loaded", []),
    )
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli, ["status", "--refresh"])

    assert result.exit_code == 0
    assert message in result.output


def test_setup_saves_discovered_accounts_without_prompt(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=15.0)],
            "Found 1 account via CodexBar: codex.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="old"),
            poll_interval_minutes=17,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert "Enable push notifications?" not in result.output
    assert len(saved) == 1
    assert saved[0].accounts[0] == replace(
        account,
        auto_kick=False,
        weekly_auto_kick=False,
        session_auto_kick=False,
    )
    assert saved[0].notifications.ntfy_topic == "old"
    assert saved[0].poll_interval_minutes == 17
    assert "Enable notifications with tk notify" not in result.output
    assert "Auto-kick is off by default." in result.output
    assert "provider terms and any consequences are your responsibility" in result.output


def test_setup_dry_run_prints_config_diff_without_writing(monkeypatch):
    saved: list[Config] = []
    migrated_pending = []
    existing_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
        auto_kick=True,
        visible=False,
    )
    new_account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [existing_account, new_account],
            [
                AccountStatus(label="dev", state=AccountState.ACTIVE),
                AccountStatus(label="claude", state=AccountState.FRESH),
            ],
            "Found 2 accounts via auto-discovery: claude, codex.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[existing_account], notifications=NotifyConfig(enabled=True)),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))
    monkeypatch.setattr("tokenkick.cli._migrate_pending_kick_keys", lambda *_args: migrated_pending.append(True))

    result = CliRunner().invoke(cli, ["setup", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run: config would not be saved." in result.output
    assert "= dev (codex, codexbar-cli)" in result.output
    assert "+ claude (claude, claude-direct)" in result.output
    assert "Config saved" not in result.output
    assert saved == []
    assert migrated_pending == []


def test_setup_shows_notification_hint_when_notifications_disabled(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=15.0)],
            "Found 1 account via CodexBar: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert "Enable notifications with tk notify --ntfy <topic>." in result.output
    assert saved[0].accounts[0].auto_kick is False
    assert saved[0].accounts[0].weekly_auto_kick is False
    assert saved[0].accounts[0].session_auto_kick is False


def test_setup_saves_discovery_status_cache_for_onboarding(monkeypatch, tmp_path):
    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "codex-home"),
        identity_email="dev@example.test",
    )
    status = AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=15.0)

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: ([account], [status], "Found 1 account via auto-discovery: codex."),
    )

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    config = Config.load()
    cached = _load_status_cache(config)
    assert cached is not None
    _accounts, statuses, _entries = cached
    assert [(row.label, row.state) for row in statuses] == [("dev", AccountState.ACTIVE)]


def test_setup_preserves_existing_auto_kick_settings_on_rediscovery(monkeypatch):
    saved: list[Config] = []
    existing = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
        auto_kick=True,
        weekly_auto_kick=False,
        session_auto_kick=True,
    )
    discovered = replace(existing, auto_kick=False, weekly_auto_kick=False, session_auto_kick=False)

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered],
            [AccountStatus(label="codex (dev)", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[existing]))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].auto_kick is True
    assert saved[0].accounts[0].weekly_auto_kick is False
    assert saved[0].accounts[0].session_auto_kick is True


def test_setup_preserves_unmodified_top_level_config_fields(monkeypatch):
    saved: list[Config] = []
    existing_account = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
    )
    discovered = replace(existing_account)
    existing_config = Config(
        accounts=[existing_account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        poll_interval_minutes=2,
        schedule=ScheduleConfig(
            accounts={existing_account.label: WorkSchedule(enabled=True, weekdays="11:00-19:00")}
        ),
        codex_surface_retry_backoff_seconds=66,
        codex_burst_ladder_enabled=True,
        codex_burst_ladder_gap_seconds=90,
        codex_burst_ladder_surface_order=[CODEX_KICK_SURFACE_REPO],
        global_reset_notify_min_confidence="confirmed",
        migrations={"future-field-preservation": True},
    )
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered],
            [AccountStatus(label=discovered.label, state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: existing_config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))
    monkeypatch.setattr("tokenkick.cli._save_status_cache", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    assert len(saved) == 1
    updated = saved[0]
    assert updated.notifications.ntfy_topic == "topic"
    assert updated.poll_interval_minutes == 2
    assert updated.schedule.accounts[existing_account.label].weekdays == "11:00-19:00"
    assert updated.codex_surface_retry_backoff_seconds == 66
    assert updated.codex_burst_ladder_enabled is True
    assert updated.codex_burst_ladder_gap_seconds == 90
    assert updated.codex_burst_ladder_surface_order == [CODEX_KICK_SURFACE_REPO]
    assert updated.codex_fire_all_surfaces is True
    assert updated.codex_fire_all_surface_gap_seconds == 90
    assert updated.codex_fire_all_surface_order == [CODEX_KICK_SURFACE_REPO]
    assert updated.global_reset_notify_min_confidence == "confirmed"
    assert updated.migrations == {"future-field-preservation": True}


def _rediscovery_setup(monkeypatch, existing: AccountConfig, discovered: AccountConfig):
    saved: list[Config] = []
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered],
            [AccountStatus(label=discovered.label, state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[existing]))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))
    return saved


def test_setup_rediscovery_preserves_notification_routes(monkeypatch):
    existing = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
        notifications_enabled=False,
        notification_backends=["telegram"],
    )
    discovered = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
    )
    saved = _rediscovery_setup(monkeypatch, existing, discovered)

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    account = saved[0].accounts[0]
    assert account.notifications_enabled is False
    assert account.notification_backends == ["telegram"]


def test_setup_rediscovery_preserves_codex_demotion_settings(monkeypatch):
    existing = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
        codex_surface_auto_demote=True,
        codex_surface_demote_after_strong_clusters=8,
        codex_surface_demote_min_active_surfaces=3,
        codex_surface_demote_min_kept_anchor_rate=0.9,
        codex_surface_demote_measurement_clusters=25,
        codex_surface_rescue_cooldown_strong_clusters=30,
    )
    discovered = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
    )
    saved = _rediscovery_setup(monkeypatch, existing, discovered)

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    account = saved[0].accounts[0]
    assert account.codex_surface_auto_demote is True
    assert account.codex_surface_demote_after_strong_clusters == 8
    assert account.codex_surface_demote_min_active_surfaces == 3
    assert account.codex_surface_demote_min_kept_anchor_rate == 0.9
    assert account.codex_surface_demote_measurement_clusters == 25
    assert account.codex_surface_rescue_cooldown_strong_clusters == 30


def test_setup_rediscovery_preserves_codex_force_overrides(monkeypatch):
    existing = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
        codex_surface_force_keep=["legacy", "repo-skip"],
        codex_surface_force_prune=["interactive-like"],
    )
    discovered = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_email="dev@example.test",
    )
    saved = _rediscovery_setup(monkeypatch, existing, discovered)

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    account = saved[0].accounts[0]
    assert account.codex_surface_force_keep == ["legacy", "repo-skip"]
    assert account.codex_surface_force_prune == ["interactive-like"]


def test_setup_rediscovery_updates_discovery_owned_identity_fields(monkeypatch):
    existing = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        identity_provider_id="acct-old",
        identity_email="old@example.test",
        notification_backends=["ntfy"],
    )
    discovered = AccountConfig(
        label="codex (dev)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        session_path="/tmp/codex-home/sessions",
        identity_provider_id="acct-new",
        identity_email="renamed@example.test",
        codex_rate_limit_name="GPT-5.5",
    )
    saved = _rediscovery_setup(monkeypatch, existing, discovered)

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    account = saved[0].accounts[0]
    assert account.identity_provider_id == "acct-new"
    assert account.identity_email == "renamed@example.test"
    assert account.session_path == "/tmp/codex-home/sessions"
    assert account.codex_rate_limit_name == "GPT-5.5"
    # User-owned settings survive the identity refresh.
    assert account.notification_backends == ["ntfy"]
    assert account.auto_kick is False


def test_setup_warns_about_duplicate_codex_homes(monkeypatch, tmp_path):
    saved: list[Config] = []
    primary_home = tmp_path / "homes" / ".codex"
    duplicate_home = tmp_path / "homes" / ".codex-managed"
    primary = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(primary_home),
        identity_email="personal@example.test",
    )
    duplicate = AccountConfig(
        label="personal-old",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(duplicate_home),
        identity_email="personal@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [primary, duplicate],
            [
                AccountStatus(label="personal", state=AccountState.ACTIVE, source_detail="codex-direct"),
                AccountStatus(
                    label="personal-old",
                    state=AccountState.UNKNOWN,
                    source_detail="codex-direct",
                    error="auth expired",
                ),
            ],
            "Found 2 accounts via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    assert "Multiple Codex homes found for personal@example.test." in result.output
    assert "personal@example.test" in result.output
    assert "Using healthy home(s): personal." in result.output
    assert "Unusable duplicate home(s): personal-old." in result.output
    assert str(primary_home) not in result.output
    assert str(duplicate_home) not in result.output
    assert "auth expired" not in result.output
    assert 'tk accounts detail "<label>"' in result.output
    assert 'tk accounts show "<label>"' in result.output
    assert "Hidden from normal status: personal-old. They remain saved." in result.output
    assert len(saved[0].accounts) == 2
    assert [account.auto_kick for account in saved[0].accounts] == [False, False]
    assert [account.visible for account in saved[0].accounts] == [True, False]


def test_setup_does_not_auto_hide_existing_duplicate_codex_home(monkeypatch, tmp_path):
    saved: list[Config] = []
    primary = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "homes" / ".codex"),
        identity_email="personal@example.test",
    )
    duplicate = AccountConfig(
        label="personal-old",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "homes" / ".codex-managed"),
        identity_email="personal@example.test",
        visible=True,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [primary, duplicate],
            [
                AccountStatus(label="personal", state=AccountState.ACTIVE, source_detail="codex-direct"),
                AccountStatus(
                    label="personal-old",
                    state=AccountState.UNKNOWN,
                    source_detail="codex-direct",
                    error="auth expired",
                ),
            ],
            "Found 2 accounts via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[primary, duplicate]))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    assert [account.visible for account in saved[0].accounts] == [True, True]
    assert "Hidden unusable duplicate home" not in result.output


def test_setup_prints_macos_codex_permission_note(monkeypatch, tmp_path):
    saved: list[Config] = []
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(tmp_path / "homes" / ".codex"),
        identity_email="codex@example.test",
    )

    monkeypatch.setattr("tokenkick.cli.sys.platform", "darwin")
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="codex", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup", "--no-daemon-prompt"])

    assert result.exit_code == 0
    assert "Codex Computer Use.app" in result.output
    assert "Allow = full status/kicks" in result.output
    assert "status may stay stale" in result.output
    assert "TokenKick does not use AppleScript" not in result.output


def test_setup_prompts_to_start_daemon_when_interactive(monkeypatch):
    saved: list[Config] = []
    started = []
    account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="dev", state=AccountState.ACTIVE, used_percent=15.0)],
            "Found 1 account via CodexBar: codex.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(notifications=NotifyConfig(enabled=True)))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))
    monkeypatch.setattr("tokenkick.cli._setup_should_prompt_start_daemon", lambda: True)
    monkeypatch.setattr("tokenkick.cli._confirm_prompt", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("tokenkick.cli._start_daemon_background", lambda: started.append(True))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert saved
    assert started == [True]
    assert "needs the background daemon" in result.output


def test_setup_no_interactive_suppresses_daemon_prompt(monkeypatch):
    import tokenkick.cli as cli_module

    monkeypatch.setenv("TK_NO_INTERACTIVE", "1")

    assert not cli_module._setup_should_prompt_start_daemon()


def test_setup_auto_enables_claude_direct_usage_when_not_explicit(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="claude", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(claude=ClaudeConfig(direct_usage_enabled=False)),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert "Claude direct usage enabled for status reads." in result.output
    assert saved[0].claude.direct_usage_enabled is True
    assert saved[0].claude.direct_usage_explicit is False


def test_setup_preserves_explicit_claude_direct_usage_disable(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="claude", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            claude=ClaudeConfig(direct_usage_enabled=False, direct_usage_explicit=True),
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert "Claude direct usage enabled for status reads." not in result.output
    assert saved[0].claude.direct_usage_enabled is False
    assert saved[0].claude.direct_usage_explicit is True


def test_setup_migrates_legacy_claude_direct_usage_disable(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="claude", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            claude=ClaudeConfig.from_dict({"direct_usage_enabled": False}),
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert "Claude direct usage enabled for status reads." in result.output
    assert saved[0].claude.direct_usage_enabled is True
    assert saved[0].claude.direct_usage_explicit is False


def test_setup_dry_run_does_not_auto_enable_claude_direct_usage(monkeypatch):
    saved: list[Config] = []
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [account],
            [AccountStatus(label="claude", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(claude=ClaudeConfig(direct_usage_enabled=False)),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup", "--dry-run"])

    assert result.exit_code == 0
    assert "Claude direct usage enabled for status reads." not in result.output
    assert saved == []


def test_setup_preserves_claude_account_direct_usage_opt_out(monkeypatch):
    saved: list[Config] = []
    existing = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        direct_usage_enabled=False,
    )
    discovered = replace(existing, direct_usage_enabled=True)

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [discovered],
            [AccountStatus(label="claude", state=AccountState.ACTIVE)],
            "Found 1 account via auto-discovery: claude.",
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[existing]))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].direct_usage_enabled is False


def test_setup_merges_discovered_accounts_without_touching_notifications(monkeypatch):
    saved: list[Config] = []
    existing_account = AccountConfig(
        label="dev",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        codexbar_account="dev@example.test",
        visible=False,
    )
    new_account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
        codexbar_account="gemini@example.test",
    )
    claude_account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="claude",
    )
    notifications = NotifyConfig(
        enabled=True,
        backend="telegram",
        telegram_bot_token="token",
        telegram_chat_id="chat-id",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [existing_account, new_account, claude_account],
            [
                AccountStatus(label="dev", state=AccountState.ACTIVE),
                AccountStatus(label="gemini", state=AccountState.FRESH),
                AccountStatus(label="claude", state=AccountState.FRESH),
            ],
            "Found 3 accounts via auto-discovery: claude, codex, gemini.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            accounts=[existing_account],
            notifications=notifications,
            poll_interval_minutes=23,
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert [account.label for account in saved[0].accounts] == ["dev", "gemini (gemini)", "claude"]
    assert [account.auto_kick for account in saved[0].accounts] == [False, False, False]
    assert [account.visible for account in saved[0].accounts] == [False, True, True]
    assert saved[0].notifications is notifications
    assert saved[0].notifications.backend == "telegram"
    assert saved[0].notifications.telegram_bot_token == "token"
    assert saved[0].notifications.telegram_chat_id == "chat-id"
    assert saved[0].poll_interval_minutes == 23


def test_setup_preserves_explicit_codex_auto_kick_opt_outs(monkeypatch):
    saved: list[Config] = []
    enabled_account = AccountConfig(
        label="enabled",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        auto_kick=True,
        session_path="/tmp/enabled/sessions",
    )
    disabled_account = AccountConfig(
        label="disabled",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        auto_kick=False,
        session_path="/tmp/disabled/sessions",
    )
    claude_account = AccountConfig(
        label="disabled-claude",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=False,
        codexbar_provider="claude",
    )

    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [enabled_account, disabled_account, claude_account],
            [
                AccountStatus(label="enabled", state=AccountState.ACTIVE),
                AccountStatus(label="disabled", state=AccountState.ACTIVE),
                AccountStatus(label="disabled-claude", state=AccountState.ACTIVE),
            ],
            "Found 3 accounts via auto-discovery: claude, codex.",
        ),
    )
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[enabled_account, disabled_account, claude_account]),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["setup"])

    assert result.exit_code == 0
    assert [account.auto_kick for account in saved[0].accounts] == [True, False, False]


def test_notify_ntfy_saves_notification_config(monkeypatch):
    saved: list[Config] = []
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["notify", "--ntfy", "topic-name"])

    assert result.exit_code == 0
    assert saved[0].notifications.enabled is True
    assert saved[0].notifications.backend == "ntfy"
    assert saved[0].notifications.ntfy_topic == "topic-name"


def test_notify_telegram_saves_notification_config(monkeypatch):
    saved: list[Config] = []
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["notify", "--telegram", "token", "chat-id"])

    assert result.exit_code == 0
    assert saved[0].notifications.enabled is True
    assert saved[0].notifications.backend == "telegram"
    assert saved[0].notifications.telegram_bot_token == "token"
    assert saved[0].notifications.telegram_chat_id == "chat-id"


def test_notify_telegram_remote_saves_credentials_without_push(monkeypatch):
    saved: list[Config] = []
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["notify", "--telegram-remote", "token", "chat-id"])

    assert result.exit_code == 0
    assert "Telegram remote credentials saved" in result.output
    assert saved[0].notifications.enabled is False
    assert saved[0].notifications.enabled_backends == []
    assert saved[0].notifications.telegram_bot_token == "token"
    assert saved[0].notifications.telegram_chat_id == "chat-id"


def test_notify_backends_are_additive(monkeypatch):
    saved: list[Config] = []
    existing = NotifyConfig(
        enabled=True,
        backend="ntfy",
        ntfy_topic="topic",
        enabled_backends=["ntfy"],
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(notifications=existing))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["notify", "--telegram", "token", "chat-id"])

    assert result.exit_code == 0
    assert saved[0].notifications.ntfy_topic == "topic"
    assert saved[0].notifications.telegram_bot_token == "token"
    assert saved[0].notifications.telegram_chat_id == "chat-id"
    assert saved[0].notifications.enabled_backends == ["ntfy", "telegram"]


def test_notify_disable_backend_keeps_telegram_remote_credentials(monkeypatch):
    saved: list[Config] = []
    existing = NotifyConfig(
        enabled=True,
        backend="telegram",
        ntfy_topic="topic",
        telegram_bot_token="token",
        telegram_chat_id="chat-id",
        enabled_backends=["ntfy", "telegram"],
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(notifications=existing))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["notify", "--disable-backend", "telegram"])

    assert result.exit_code == 0
    assert "Telegram notifications disabled" in result.output
    assert saved[0].notifications.enabled is True
    assert saved[0].notifications.backend == "ntfy"
    assert saved[0].notifications.enabled_backends == ["ntfy"]
    assert saved[0].notifications.telegram_bot_token == "token"
    assert saved[0].notifications.telegram_chat_id == "chat-id"


def test_notify_test_sends_configured_test_notification(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic")
        ),
    )
    monkeypatch.setattr("tokenkick.cli.notify_test", lambda notifications: calls.append(notifications) or True)

    result = CliRunner().invoke(cli, ["notify", "test"])

    assert result.exit_code == 0
    assert "Test notification sent" in result.output
    assert len(calls) == 1
    assert calls[0].ntfy_topic == "topic"


def test_notify_test_sends_all_enabled_backends(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=["ntfy", "telegram"],
            )
        ),
    )
    monkeypatch.setattr("tokenkick.cli.notify_test", lambda notifications: calls.append(notifications) or True)

    result = CliRunner().invoke(cli, ["notify", "test"])

    assert result.exit_code == 0
    assert "Test notification sent" in result.output
    assert [config.backend for config in calls] == ["ntfy", "telegram"]


def test_notify_test_can_target_telegram_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(
                enabled=True,
                backend="ntfy",
                ntfy_topic="topic",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=["ntfy", "telegram"],
            )
        ),
    )
    monkeypatch.setattr("tokenkick.cli.notify_test", lambda notifications: calls.append(notifications) or True)

    result = CliRunner().invoke(cli, ["notify", "test", "--backend", "telegram"])

    assert result.exit_code == 0
    assert "Telegram test notification sent" in result.output
    assert [config.backend for config in calls] == ["telegram"]


def test_notify_test_can_target_telegram_remote_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(
            notifications=NotifyConfig(
                enabled=False,
                backend="ntfy",
                telegram_bot_token="token",
                telegram_chat_id="chat-id",
                enabled_backends=[],
            )
        ),
    )
    monkeypatch.setattr("tokenkick.cli.notify_test", lambda notifications: calls.append(notifications) or True)

    result = CliRunner().invoke(cli, ["notify", "test", "--backend", "telegram"])

    assert result.exit_code == 0
    assert "Telegram test notification sent" in result.output
    assert [config.backend for config in calls] == ["telegram"]
    assert calls[0].enabled is True
    assert calls[0].telegram_chat_id == "chat-id"


def test_interactive_banner_includes_tokenkick_wordmark(capsys):
    import tokenkick.interactive as interactive

    interactive._print_banner()

    output = capsys.readouterr().out
    assert "→ TokenKick" in output
    assert "|  _ \\ ___" in output
    assert "\\____|\\___/ (_)" in output


def test_interactive_first_run_opens_setup_before_main_menu(monkeypatch):
    import tokenkick.interactive as interactive

    setup_calls = []
    selections = iter(["exit"])
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(interactive, "_setup_menu", lambda _ctx: setup_calls.append(True))
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=[]))

    interactive.run_command_center(click.Context(cli), first_run_setup=True)

    assert setup_calls == [True]


def test_interactive_first_run_skips_setup_when_accounts_saved(monkeypatch):
    import tokenkick.interactive as interactive

    setup_calls = []
    selections = iter(["exit"])
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(interactive, "_setup_menu", lambda _ctx: setup_calls.append(True))
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        "tokenkick.interactive.Config.load",
        lambda: Config(accounts=[AccountConfig(label="codex", provider="codex")]),
    )

    interactive.run_command_center(click.Context(cli), first_run_setup=True)

    assert setup_calls == []


def test_interactive_setup_runs_discovery_without_second_confirmation(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["run", "exit", "exit"])
    invoked = []
    next_step_choices: list[list[str]] = []
    next_step_names: list[list[str]] = []

    def select(message, choices, **_kwargs):
        if message == "Next setup step":
            next_step_choices.append([choice.value for choice in choices])
            next_step_names.append([choice.name for choice in choices])
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: pytest.fail("setup run must not confirm twice"))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))

    interactive._setup_menu(click.Context(cli))

    assert invoked == [("setup", {"rename_labels": (), "dry_run": False, "no_daemon_prompt": True})]
    assert next_step_choices == [["auto", "notifications", "daemon", "schedule_info", "codex_info", "exit"]]
    assert next_step_names == [
        [
            "Review & enable auto-kick",
            "Configure notifications",
            "Start background daemon",
            "Schedule & orchestration info",
            "Codex strategy info",
            "Back",
        ]
    ]


def test_interactive_setup_info_blocks_render_as_panels(capsys):
    import tokenkick.interactive as interactive

    interactive._print_setup_schedule_info()
    interactive._print_setup_codex_strategy_info()

    output = capsys.readouterr().out
    assert "Schedule & Orchestration" in output
    assert "Smart schedule" in output
    assert "Orchestration plan" in output
    assert "Main menu -> Schedule" in output
    assert "Codex Surface Strategy" in output
    assert "Burst ladder" in output
    assert "Surface order" in output
    assert "Safe default" in output


def test_interactive_setup_returns_to_next_step_menu_after_subflow(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["run", "auto", "exit", "exit"])
    messages = []
    dispatched = []

    def select(message, _choices, **_kwargs):
        messages.append(message)
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_run_setup_discovery", lambda *_args: None)
    monkeypatch.setattr(interactive, "_dispatch_action", lambda _ctx, action: dispatched.append(action))

    interactive._setup_menu(click.Context(cli))

    assert dispatched == ["auto"]
    assert messages == ["Setup", "Next setup step", "Next setup step", "Setup"]


def test_setup_progress_context_installs_and_restores_callback(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    calls = []
    original = cli_module._SETUP_PROGRESS_CALLBACK

    class FakeProgress:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args):
            calls.append("exit")

        def __call__(self, message):
            calls.append(message)

        def finish(self):
            calls.append("finish")

    monkeypatch.setattr(interactive, "_setup_progress_live_enabled", lambda: True)
    monkeypatch.setattr(interactive, "_PhasedSetupProgress", FakeProgress)

    with interactive._setup_progress_context(cli_module):
        assert cli_module._setup_progress("Reading saved TokenKick config") is True

    assert calls == ["enter", "Reading saved TokenKick config", "exit", "finish"]
    assert cli_module._SETUP_PROGRESS_CALLBACK is original


def test_interactive_prompt_wrappers_map_escape_skip_to_back(monkeypatch):
    from InquirerPy import inquirer
    import tokenkick.interactive as interactive

    calls = []

    class Prompt:
        def execute(self):
            return None

    def fake_select(**kwargs):
        calls.append(kwargs)
        return Prompt()

    monkeypatch.setattr(inquirer, "select", fake_select)
    monkeypatch.setattr(inquirer, "checkbox", fake_select)

    assert interactive._select("Menu", ["One"]) == interactive.MENU_EXIT
    assert interactive._checkbox("Accounts", ["One"]) == [interactive.MENU_EXIT]
    assert all(call["mandatory"] is False for call in calls)
    assert all(call["keybindings"]["skip"] == [{"key": "escape"}] for call in calls)


def test_interactive_select_retries_interrupted_terminal_raw_mode(monkeypatch):
    from InquirerPy import inquirer
    import tokenkick.interactive as interactive

    calls = {"execute": 0}

    class Prompt:
        def execute(self):
            calls["execute"] += 1
            if calls["execute"] == 1:
                raise interactive.termios.error(interactive.errno.EINTR, "Interrupted system call")
            return "ok"

    monkeypatch.setattr(inquirer, "select", lambda **_kwargs: Prompt())
    monkeypatch.setattr(interactive, "_claim_foreground_terminal", lambda: (None, None))
    monkeypatch.setattr(interactive, "_restore_foreground_terminal", lambda *_args: None)

    assert interactive._select("Menu", ["ok"]) == "ok"
    assert calls["execute"] == 2


def test_interactive_confirm_uses_visible_back_choice(monkeypatch):
    import tokenkick.interactive as interactive

    captured = []

    def fake_select(message, choices, **kwargs):
        captured.append((message, choices, kwargs))
        return "yes"

    monkeypatch.setattr(interactive, "_select", fake_select)

    assert interactive._confirm("Proceed?", default=True) is True
    assert [choice.value for choice in captured[0][1]] == ["yes", "no", "exit"]
    assert captured[0][2]["default"] == "yes"


def test_interactive_text_action_can_go_back(monkeypatch):
    import tokenkick.interactive as interactive

    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: "exit")
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: pytest.fail("text field should not open"))

    assert interactive._text_action("Gap", default="30") is None


def test_interactive_status_menu_invokes_refresh_status(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["status", "refresh", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "status",
            {
                "as_json": False,
                "codex_only": False,
                "show_all": False,
                "account_label": None,
                "refresh": True,
                "verbose": False,
            },
        )
    ]


def test_interactive_diagnostics_menu_invokes_surface_patterns(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["diagnostics", "surface_patterns", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "codex-surface-patterns",
            {
                "label": None,
                "as_json": False,
            },
        )
    ]


def test_interactive_diagnostics_menu_invokes_status_details(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["diagnostics", "status_details", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "status",
            {
                "as_json": False,
                "codex_only": False,
                "show_all": False,
                "account_label": None,
                "refresh": False,
                "verbose": True,
            },
        )
    ]


def test_interactive_global_reset_recovery_acknowledges_event(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event()
    assert append_reset_event(event)
    selections = iter(["diagnostics", "reset_recovery", event.id, "ack", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    loaded = load_reset_events()
    assert loaded[0].acknowledged_by == "tui"
    assert "Acknowledged reset event" in result.output


def test_interactive_provider_observation_recovery_has_no_plan_action(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _provider_observation_event()
    assert append_reset_event(event)
    selections = iter(["diagnostics", "reset_recovery", event.id, "exit", "exit", "exit", "exit"])
    action_choices = []

    def fake_select(message, choices, **_kwargs):
        if message == "Reset recovery action":
            action_choices.extend(choice.value for choice in choices)
        return next(selections)

    monkeypatch.setattr(interactive, "_select", fake_select)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(
        interactive,
        "_orchestration_plan_flow",
        lambda *_args, **_kwargs: pytest.fail("provider observations must not plan orchestration"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert action_choices == ["ack", "detail", "exit"]
    assert load_reset_events()[0].acknowledged_at is None


def test_interactive_global_reset_recovery_can_apply_orchestration_plan(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event()
    assert append_reset_event(event)
    selections = iter([
        "diagnostics",
        "reset_recovery",
        event.id,
        "plan",
        "today",
        "no",
        "exit",
        "exit",
        "exit",
        "exit",
    ])
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    applied_plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        applied=True,
    )
    applied = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda candidate, *, now, current_time=None: applied.append((candidate, now)) or applied_plan,
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert applied == [(plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc))]
    loaded = load_reset_events()
    assert loaded[0].recovery_action == "orchestration_applied"


def test_interactive_recovery_does_not_record_applied_when_apply_fails(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    reset_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_file)
    event = _reset_event()
    assert append_reset_event(event)
    selections = iter([
        "diagnostics",
        "reset_recovery",
        event.id,
        "plan",
        "today",
        "no",
        "exit",
        "exit",
        "exit",
        "exit",
    ])
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    failed_plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        applied=False,
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda candidate, *, now, current_time=None: failed_plan,
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    loaded = load_reset_events()
    assert loaded[0].recovery_action == "orchestration_previewed"


def test_interactive_history_menu_invokes_compact_recent(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["history", "all_recent_compact", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "history",
            {
                "limit": 20,
                "account_label": None,
                "as_json": False,
                "verbose": False,
                "anchored_only": False,
            },
        )
    ]


def test_interactive_history_menu_invokes_account_verbose_recent(monkeypatch):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex")]
    selections = iter(["history", "account_recent_verbose", "codex", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "history",
            {
                "limit": 20,
                "account_label": "codex",
                "as_json": False,
                "verbose": True,
                "anchored_only": False,
            },
        )
    ]


def test_interactive_history_menu_invokes_custom_limit_account_compact(monkeypatch):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex")]
    selections = iter(["history", "custom_limit_compact", "account", "codex", "50", "all", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [
        (
            "history",
            {
                "limit": 50,
                "account_label": "codex",
                "as_json": False,
                "verbose": False,
                "anchored_only": False,
            },
        )
    ]


def test_interactive_auto_menu_saves_auto_kick(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    accounts = [AccountConfig(label="codex", provider="codex", auto_kick=False)]
    selections = iter(["configure", "auto", "enable_selected", "all", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: ["codex"])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: (
            accounts,
            [
                AccountStatus(label="codex", state=AccountState.ACTIVE),
                AccountStatus(label="claude", state=AccountState.FRESH),
            ],
            {},
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"], input="ENABLE\n")

    assert result.exit_code == 0
    assert saved[0].accounts[0].auto_kick is True
    assert saved[0].accounts[0].weekly_auto_kick is True
    assert saved[0].accounts[0].session_auto_kick is True


def test_interactive_configure_menu_splits_accounts_and_auto_kick(monkeypatch):
    import tokenkick.interactive as interactive

    seen_configure_choices: list[list[str]] = []
    seen_configure_labels: list[list[str]] = []
    selections = iter(["configure", "exit", "exit"])

    def select(message, choices, **_kwargs):
        if message == "Configure":
            seen_configure_choices.append([choice.value for choice in choices])
            seen_configure_labels.append([choice.name for choice in choices])
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert seen_configure_choices == [
        [
            "setup",
            "accounts",
            "auto",
            "codex_settings",
            "notifications",
            "remote_telegram",
            "mcp",
            "exit",
        ]
    ]
    assert "Accounts" in seen_configure_labels[0]
    assert "Auto-kick" in seen_configure_labels[0]
    assert "Remote status (Telegram)" in seen_configure_labels[0]
    assert "Agent tools (MCP)" in seen_configure_labels[0]
    assert "Accounts & auto-kick" not in seen_configure_labels[0]


def test_interactive_daemon_menu_shows_manual_upgrade_when_not_pipx(monkeypatch):
    import tokenkick.interactive as interactive

    seen_choices: list[list[str]] = []

    def select(message, choices, **_kwargs):
        if message == "Daemon":
            seen_choices.append([choice.value for choice in choices])
        return interactive.MENU_EXIT

    monkeypatch.setattr(interactive, "_pipx_upgrade_command", lambda: None)
    monkeypatch.setattr(interactive, "_select", select)

    interactive._daemon_menu(click.Context(cli))

    assert seen_choices == [
        [
            "status",
            "start",
            "stop",
            "restart",
            "update",
            "upgrade_help",
            "exit",
        ]
    ]


def test_interactive_daemon_menu_runs_fresh_tk_update(monkeypatch):
    import tokenkick.interactive as interactive

    calls = []
    selections = iter(["update", interactive.MENU_EXIT])

    monkeypatch.setattr(interactive, "_pipx_upgrade_command", lambda: None)
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_tk_subprocess_command", lambda: "/tmp/tk")
    monkeypatch.setattr(interactive, "_run_visible_command", lambda args: calls.append(list(args)) or 0)

    interactive._daemon_menu(click.Context(cli))

    assert calls == [["/tmp/tk", "update"]]


def test_interactive_daemon_menu_runs_pipx_upgrade_update(monkeypatch):
    import tokenkick.interactive as interactive

    calls = []
    selections = iter(["pipx_upgrade_update", interactive.MENU_EXIT])

    monkeypatch.setattr(
        interactive,
        "_pipx_upgrade_command",
        lambda: ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        interactive,
        "_run_pipx_upgrade",
        lambda *, update_after: calls.append(update_after),
    )

    interactive._daemon_menu(click.Context(cli))

    assert calls == [True]


def test_interactive_pipx_upgrade_update_restores_previously_running_remote(monkeypatch):
    import tokenkick.interactive as interactive

    calls = []
    monkeypatch.setattr(
        interactive,
        "_pipx_upgrade_command",
        lambda: ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
    )
    monkeypatch.setattr(
        interactive,
        "_background_process_status_before_upgrade",
        lambda: {"daemon": False, "telegram_remote": True},
    )
    monkeypatch.setattr(interactive, "_tk_subprocess_command", lambda: "/usr/local/bin/tk")
    monkeypatch.setattr(interactive, "_run_visible_command", lambda args: calls.append(list(args)) or 0)

    interactive._run_pipx_upgrade(update_after=True)

    assert calls == [
        ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
        ["/usr/local/bin/tk", "update", "--yes"],
        ["/usr/local/bin/tk", "remote", "telegram", "--background"],
    ]


def test_interactive_pipx_upgrade_update_restores_previously_running_daemon(monkeypatch):
    import tokenkick.interactive as interactive

    calls = []
    monkeypatch.setattr(
        interactive,
        "_pipx_upgrade_command",
        lambda: ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
    )
    monkeypatch.setattr(
        interactive,
        "_background_process_status_before_upgrade",
        lambda: {"daemon": True, "telegram_remote": False},
    )
    monkeypatch.setattr(interactive, "_tk_subprocess_command", lambda: "/usr/local/bin/tk")
    monkeypatch.setattr(interactive, "_run_visible_command", lambda args: calls.append(list(args)) or 0)

    interactive._run_pipx_upgrade(update_after=True)

    assert calls == [
        ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
        ["/usr/local/bin/tk", "update", "--yes"],
        ["/usr/local/bin/tk", "daemon", "--background"],
    ]


def test_interactive_pipx_upgrade_records_background_state_for_later_update(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    state_file = tmp_path / "upgrade-background-processes.json"
    monkeypatch.setattr("tokenkick.cli.UPGRADE_BACKGROUND_STATE_FILE", state_file)
    monkeypatch.setattr(
        "tokenkick.cli._daemon_status_payload",
        lambda: {"running": False},
    )
    monkeypatch.setattr(
        "tokenkick.cli._telegram_remote_status_payload",
        lambda: {"running": True},
    )
    monkeypatch.setattr(
        interactive,
        "_pipx_upgrade_command",
        lambda: ["/usr/local/bin/pipx", "upgrade", "tokenkick"],
    )
    monkeypatch.setattr(interactive, "_run_visible_command", lambda _args: 0)

    interactive._run_pipx_upgrade(update_after=False)

    assert json.loads(state_file.read_text()) == {
        "daemon": False,
        "telegram_remote": True,
    }


def test_interactive_pipx_upgrade_command_requires_pipx_runtime(monkeypatch):
    import tokenkick.interactive as interactive

    monkeypatch.setattr(interactive.shutil, "which", lambda name: f"/bin/{name}" if name == "pipx" else None)
    monkeypatch.setattr(interactive.sys, "prefix", "/Users/me/.local/share/pipx/venvs/tokenkick")
    monkeypatch.setattr(interactive.sys, "executable", "/Users/me/.local/share/pipx/venvs/tokenkick/bin/python")
    monkeypatch.setattr(interactive.sys, "argv", ["/Users/me/.local/bin/tk"])

    assert interactive._pipx_upgrade_command() == ["/bin/pipx", "upgrade", "tokenkick"]

    monkeypatch.setattr(interactive.sys, "prefix", "/Users/me/dev/TokenKick/.venv")
    monkeypatch.setattr(interactive.sys, "executable", "/Users/me/dev/TokenKick/.venv/bin/python")
    monkeypatch.setattr(interactive.sys, "argv", ["/Users/me/dev/TokenKick/.venv/bin/tk"])

    assert interactive._pipx_upgrade_command() is None


def test_interactive_remote_telegram_test_targets_telegram_only(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    calls = []
    selections = iter(["test", interactive.MENU_EXIT])

    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        interactive,
        "_invoke",
        lambda _ctx, command, **kwargs: calls.append((command, kwargs)),
    )

    interactive._remote_telegram_menu(click.Context(cli))

    assert calls == [
        (
            cli_module.notify,
            {
                "ntfy_topic": None,
                "telegram": None,
                "telegram_remote": None,
                "disable_backend": None,
                "test_backend": "telegram",
                "action": "test",
            },
        )
    ]


def test_interactive_remote_telegram_configure_is_remote_only(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    calls = []
    selections = iter(["configure", interactive.MENU_EXIT])

    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_secret_action", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(interactive, "_text_action", lambda *_args, **_kwargs: "chat-id")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        interactive,
        "_invoke",
        lambda _ctx, command, **kwargs: calls.append((command, kwargs)),
    )

    interactive._remote_telegram_menu(click.Context(cli))

    assert calls == [
        (
            cli_module.notify,
            {
                "ntfy_topic": None,
                "telegram": None,
                "telegram_remote": ("token", "chat-id"),
                "disable_backend": None,
                "test_backend": "all",
                "action": None,
            },
        )
    ]


def test_interactive_remote_telegram_can_disable_push_notifications(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    calls = []
    selections = iter(["remote_only", interactive.MENU_EXIT])

    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        interactive,
        "_invoke",
        lambda _ctx, command, **kwargs: calls.append((command, kwargs)),
    )

    interactive._remote_telegram_menu(click.Context(cli))

    assert calls == [
        (
            cli_module.notify,
            {
                "ntfy_topic": None,
                "telegram": None,
                "telegram_remote": None,
                "disable_backend": "telegram",
                "test_backend": "all",
                "action": None,
            },
        )
    ]


def test_interactive_mcp_menu_show_status_is_non_mutating(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.mcp_setup as mcp_setup

    status_calls: list[str] = []
    rendered_payloads: list[dict] = []
    mutation_calls: list[str] = []
    selections = iter(["status", interactive.MENU_EXIT, interactive.MENU_EXIT])

    class FakeMCPSetupManager:
        def status(self, *, client: str = "all") -> dict:
            status_calls.append(client)
            return {"clients": [{"client": "codex", "state": "configured"}]}

        def install(self, **_kwargs):
            mutation_calls.append("install")
            return {"clients": []}

        def remove(self, **_kwargs):
            mutation_calls.append("remove")
            return {"clients": []}

    monkeypatch.setattr(mcp_setup, "MCPSetupManager", FakeMCPSetupManager)
    monkeypatch.setattr(interactive, "_print_mcp_status", lambda payload: rendered_payloads.append(payload))
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))

    interactive._mcp_menu(click.Context(cli))

    assert status_calls == ["all", "all", "all"]
    assert len(rendered_payloads) == 3
    assert mutation_calls == []


def test_interactive_accounts_menu_sets_planning_default(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(
        [
            "configure",
            "accounts",
            "planning_defaults",
            "set",
            "codex",
            "180",
            "exit",
            "exit",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].usable_session_minutes == 180
    assert 'Usable session minutes for "codex" set to 180.' in result.output


def test_interactive_accounts_menu_hides_selected_accounts(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirms: list[str] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(["configure", "accounts", "hide_selected", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: ["codex"])
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda message, **_kwargs: confirms.append(message) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].visible is False
    assert confirms == ["Hide 1 account(s): codex?"]
    assert 'Account "codex" is now hidden.' in result.output


def test_interactive_accounts_menu_shows_selected_accounts(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirms: list[str] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex", visible=False)])
    selections = iter(["configure", "accounts", "show_selected", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: ["codex"])
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda message, **_kwargs: confirms.append(message) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].visible is True
    assert confirms == ["Show 1 account(s): codex?"]
    assert 'Account "codex" is now shown.' in result.output


def test_interactive_accounts_menu_empty_visibility_selection_explains_space(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(["configure", "accounts", "hide_selected", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: pytest.fail("empty selection must not confirm"))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved == []
    assert "No accounts selected; press Space to select account(s), then Enter." in result.output


def test_interactive_accounts_menu_visibility_back_does_not_save(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(["configure", "accounts", "hide_selected", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: [interactive.MENU_EXIT])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: pytest.fail("Back must not confirm"))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved == []


def test_interactive_accounts_menu_saves_orchestration_role(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(
        [
            "configure",
            "accounts",
            "orchestration_roles",
            "role",
            "codex",
            "backup",
            "yes",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].orchestration_role == "backup"


def test_interactive_accounts_menu_warns_when_use_first_demotes_existing(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirm_messages: list[str] = []
    config = Config(
        accounts=[
            AccountConfig(label="personal", provider="codex"),
            AccountConfig(label="work", provider="codex", orchestration_role="use_first"),
        ]
    )
    selections = iter(
        [
            "configure",
            "accounts",
            "orchestration_roles",
            "role",
            "personal",
            "use_first",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda message, **_kwargs: confirm_messages.append(message) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].orchestration_role == "use_first"
    assert saved[0].accounts[1].orchestration_role == "normal"
    assert 'This will demote "work" to Normal.' in confirm_messages[0]


def test_interactive_accounts_menu_threshold_back_does_not_save(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    selections = iter(
        [
            "configure",
            "accounts",
            "orchestration_roles",
            "threshold",
            "codex",
            "exit",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: None)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved == []


def test_interactive_auto_menu_can_enable_all_accounts(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirm_defaults = []
    accounts = [
        AccountConfig(label="codex", provider="codex", auto_kick=False),
        AccountConfig(label="claude", provider="claude", auto_kick=False),
    ]
    selections = iter(["configure", "auto", "enable_all", "session", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda *_args, **kwargs: confirm_defaults.append(kwargs.get("default")) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: (
            accounts,
            [
                AccountStatus(label="codex", state=AccountState.ACTIVE),
                AccountStatus(label="claude", state=AccountState.FRESH),
            ],
            {},
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"], input="ENABLE\nENABLE\n")

    assert result.exit_code == 0
    assert confirm_defaults == [True]
    assert [config.accounts[0].session_auto_kick for config in saved] == [True, False]
    assert saved[-1].accounts[1].session_auto_kick is True


def test_interactive_auto_menu_enable_all_skips_unusable_accounts(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex-good", provider="codex", auto_kick=False),
        AccountConfig(label="codex-stale", provider="codex", auto_kick=False),
        AccountConfig(label="claude-expired", provider="claude", auto_kick=False),
    ]
    selections = iter(["configure", "auto", "enable_all", "all", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(
        "tokenkick.cli._load_status_cache",
        lambda _config: (
            accounts,
            [
                AccountStatus(label="codex-good", state=AccountState.ACTIVE),
                AccountStatus(
                    label="codex-stale",
                    state=AccountState.ACTIVE,
                    stale=True,
                    source_detail="cached",
                ),
                AccountStatus(
                    label="claude-expired",
                    state=AccountState.UNKNOWN,
                    error="auth expired",
                ),
            ],
            {},
        ),
    )
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"], input="ENABLE\n")

    assert result.exit_code == 0
    assert [account.auto_kick for account in saved[-1].accounts] == [True, False, False]
    assert [account.weekly_auto_kick for account in saved[-1].accounts] == [True, False, False]
    assert [account.session_auto_kick for account in saved[-1].accounts] == [True, False, False]
    assert "Skipped auto-kick enable for accounts that are not currently usable" in result.output
    assert "codex-stale" in result.output
    assert "claude-expired: auth expired" in result.output
    assert 'tk accounts hide "<label>"' in result.output


def test_interactive_auto_menu_selected_back_returns_to_accounts_menu(monkeypatch):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex", auto_kick=False)]
    selections = iter(["configure", "auto", "enable_selected", "status", "exit", "exit", "exit"])
    checkbox_choices = iter([["exit"]])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: next(checkbox_choices))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [("status", {})]


def test_interactive_codex_fire_all_menu_sets_order(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(codex_fire_all_surfaces=True)
    selections = iter(["configure", "codex_settings", "order", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "pick_codex_surface_order",
        lambda *_args, **_kwargs: ("repo", "legacy"),
    )
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].codex_fire_all_surface_order == ["repo", "legacy"]


def test_interactive_codex_fire_all_menu_resets_order_from_picker(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(
        codex_fire_all_surfaces=True,
        codex_fire_all_surface_order=["repo", "legacy"],
    )
    selections = iter(["configure", "codex_settings", "order", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "pick_codex_surface_order",
        lambda *_args, **_kwargs: interactive.CODEX_SURFACE_ORDER_RESET,
    )
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].codex_fire_all_surface_order == []


def test_interactive_codex_fire_all_settings_menu_hides_outer_reset(monkeypatch):
    import tokenkick.interactive as interactive

    seen_codex_settings: list[list[str]] = []
    selections = iter(["configure", "codex_settings", "exit", "exit", "exit"])

    def select(message, choices, **_kwargs):
        if message == "Codex surface strategy":
            seen_codex_settings.append([choice.value for choice in choices])
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config())

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert seen_codex_settings
    assert "reset_order" not in seen_codex_settings[0]


def test_interactive_codex_surface_demotion_disable_all(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(
        accounts=[
            AccountConfig(label="codex-a", provider="codex", codex_surface_auto_demote=True),
            AccountConfig(label="codex-b", provider="codex", codex_surface_auto_demote=True),
            AccountConfig(label="claude", provider="claude", codex_surface_auto_demote=True),
        ]
    )
    selections = iter(["configure", "codex_settings", "demotion", "disable_all", "exit", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved
    assert [account.codex_surface_auto_demote for account in saved[-1].accounts] == [False, False, True]


def test_interactive_codex_surface_demotion_shows_evidence(monkeypatch):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex")]
    selections = iter(["configure", "codex_settings", "demotion", "show", "codex", "exit", "exit", "exit", "exit"])
    invoked = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_invoke", lambda _ctx, command, **kwargs: invoked.append((command.name, kwargs)))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert invoked == [("evidence", {"as_json": False})]


def test_interactive_codex_surface_stats_menu_resets_one_account(monkeypatch, tmp_path):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex")]
    selections = iter(["configure", "codex_settings", "surface_stats", "reset_one", "codex", "exit", "exit", "exit", "exit"])
    reset_labels = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli._codex_surface_stats_file", lambda: tmp_path / "stats.json")
    monkeypatch.setattr(
        "tokenkick.cli.reset_codex_surface_learning_stats",
        lambda _path, account: reset_labels.append(account.label),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert reset_labels == ["codex"]


def test_codex_surface_order_draft_fragments_highlight_reordered_surfaces():
    import tokenkick.interactive as interactive

    fragments = interactive._surface_order_draft_fragments(
        ["legacy", "repo-skip", "interactive-like", "repo"],
        ["repo-skip", "legacy", "interactive-like", "repo"],
    )

    assert fragments == [
        ("bold fg:ansiyellow", "legacy"),
        ("", ", "),
        ("bold fg:ansiyellow", "repo-skip"),
        ("", ", "),
        ("", "interactive-like"),
        ("", ", "),
        ("", "repo"),
    ]


def test_interactive_codex_fire_all_order_back_does_not_save(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config(codex_fire_all_surfaces=True)
    selections = iter(["configure", "codex_settings", "order", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_codex_surface_order", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved == []
    assert "Burst ladder surface order unchanged." in result.output


def test_interactive_codex_fire_all_order_same_as_saved_does_not_prompt_or_save(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirms: list[str] = []
    config = Config(codex_fire_all_surfaces=True)
    selections = iter(["configure", "codex_settings", "order", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "pick_codex_surface_order",
        lambda *_args, **_kwargs: tuple(CODEX_FIRE_ALL_SURFACES),
    )
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda message, **_kwargs: confirms.append(message) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved == []
    assert confirms == []
    assert "already matches saved order" in result.output


def test_interactive_schedule_menu_saves_default_weekday_window(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    config = Config()
    selections = iter(["schedule", "smart", "__default__", "weekdays", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "09:00-17:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.invalidate_pending_kicks", lambda **_kwargs: [])

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].schedule.enabled is True
    assert saved[0].schedule.default.enabled is True
    assert saved[0].schedule.default.weekdays == "09:00-17:00"


def test_interactive_schedule_menu_shows_orchestration_and_smart_schedule(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.cli as cli_module

    seen_schedule_choices: list[list[str]] = []
    selections = iter(["schedule", "exit", "exit"])

    def select(message, choices, **_kwargs):
        if message == "Schedule":
            seen_schedule_choices.append([choice.value for choice in choices])
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(cli_module, "_orchestrated_pending_kicks", lambda **_kwargs: [])

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert seen_schedule_choices == [["orchestration", "smart", "status", "exit"]]


def test_interactive_schedule_menu_shows_cancel_only_with_orchestrated_pending(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.cli as cli_module

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex")
    pending = [_pending_kick_for_test(account, now=now)]
    seen_schedule_choices: list[list[str]] = []
    selections = iter(["schedule", "exit", "exit"])

    def select(message, choices, **_kwargs):
        if message == "Schedule":
            seen_schedule_choices.append([choice.value for choice in choices])
        return next(selections)

    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(cli_module, "_orchestrated_pending_kicks", lambda **_kwargs: pending)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert seen_schedule_choices == [["orchestration", "cancel_orchestration", "smart", "status", "exit"]]


def test_interactive_orchestration_cancel_back_does_not_mutate(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.cli as cli_module

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex")
    pending = [_pending_kick_for_test(account, now=now)]
    monkeypatch.setattr(cli_module, "_orchestrated_pending_kicks", lambda **_kwargs: pending)
    monkeypatch.setattr(cli_module, "_render_orchestrated_pending_kicks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: interactive.MENU_EXIT)
    monkeypatch.setattr(
        interactive,
        "cancel_orchestrated_pending_kicks",
        lambda *_args, **_kwargs: pytest.fail("Back must not cancel pending kicks"),
    )

    interactive._orchestration_cancel_menu()


def test_interactive_orchestration_cancel_all_confirmation_defaults_no(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.cli as cli_module

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    account = AccountConfig(label="codex", provider="codex")
    pending = [_pending_kick_for_test(account, now=now)]
    selections = iter(["all", interactive.MENU_EXIT])
    confirm_defaults = []
    monkeypatch.setattr(cli_module, "_orchestrated_pending_kicks", lambda **_kwargs: pending)
    monkeypatch.setattr(cli_module, "_render_orchestrated_pending_kicks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda _message, **kwargs: confirm_defaults.append(kwargs.get("default")) or False,
    )
    monkeypatch.setattr(
        interactive,
        "cancel_orchestrated_pending_kicks",
        lambda *_args, **_kwargs: pytest.fail("declined cancellation must not mutate"),
    )

    interactive._orchestration_cancel_menu()

    assert confirm_defaults == [False]


def test_interactive_orchestration_cancel_selected_accounts(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.cli as cli_module

    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    codex = AccountConfig(label="codex", provider="codex")
    claude = AccountConfig(label="claude", provider="claude")
    pending = [
        _pending_kick_for_test(codex, now=now),
        _pending_kick_for_test(claude, now=now),
    ]
    pending_calls = iter([pending, []])
    cancelled = []
    monkeypatch.setattr(cli_module, "_orchestrated_pending_kicks", lambda **_kwargs: next(pending_calls))
    monkeypatch.setattr(cli_module, "_render_orchestrated_pending_kicks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: "selected")
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: ["codex"])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)

    def cancel(**kwargs):
        cancelled.append(kwargs)
        return SimpleNamespace(removed=[pending[0]])

    monkeypatch.setattr(interactive, "cancel_orchestrated_pending_kicks", cancel)

    interactive._orchestration_cancel_menu()

    assert len(cancelled) == 1
    assert cancelled[0]["account_labels"] == {"codex"}
    assert isinstance(cancelled[0]["now"], datetime)


def test_interactive_work_window_summary_marks_overnight_end():
    import tokenkick.interactive as interactive

    assert (
        interactive._work_window_picker_summary("22:30", "02:00")
        == "Start: 22:30 End: 02:00 (+1 day)"
    )
    assert interactive._work_window_picker_summary("09:00", "17:00") == "Start: 09:00 End: 17:00"


def test_work_window_end_slot_validity_splits_today_and_next_day():
    import tokenkick.interactive as interactive

    assert interactive._work_window_end_slot_enabled("21:00", "02:00", day_offset=0) is False
    assert interactive._work_window_end_slot_enabled("21:00", "02:00", day_offset=1) is True
    assert interactive._work_window_end_slot_enabled("21:00", "22:00", day_offset=1) is False
    assert interactive._work_window_end_slot_enabled("21:00", "21:00", day_offset=1) is False
    assert interactive._work_window_end_slot_enabled("21:00", "20:30", day_offset=1) is True
    assert interactive._work_window_end_slot_enabled("21:00", "21:30", day_offset=0) is True


def test_work_window_picker_value_preserves_overnight_string_format():
    import tokenkick.interactive as interactive

    assert interactive._work_window_picker_value("21:00", "02:00") == "21:00-02:00"


def test_initial_work_window_end_position_moves_forward_or_next_day():
    import tokenkick.interactive as interactive

    assert interactive._initial_work_window_end_position("21:00") == (0, 3, 7)
    assert interactive._initial_work_window_end_position("23:30") == (1, 0, 0)


def test_work_window_day_labels_use_date_or_recurring_names():
    import tokenkick.interactive as interactive

    assert interactive._work_window_day_labels(date(2026, 6, 9)) == (
        "Today, 2026-06-09",
        "Tomorrow, 2026-06-10",
    )
    assert interactive._work_window_day_labels(None) == ("Start day", "Next day")


def test_parse_work_window_still_supports_overnight_string():
    from tokenkick.scheduling import parse_work_window

    start, end = parse_work_window("21:00-02:00", date(2026, 6, 9), timezone.utc)

    assert start == datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 10, 2, 0, tzinfo=timezone.utc)


def test_parse_work_window_rejects_equal_start_and_end():
    from tokenkick.scheduling import parse_work_window

    with pytest.raises(ValueError, match="start and end must differ"):
        parse_work_window("09:00-09:00", date(2026, 6, 9), timezone.utc)


def test_schedule_set_rejects_equal_work_window(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())

    result = CliRunner().invoke(
        cli,
        ["schedule", "set", "--default", "--weekdays", "09:00-09:00"],
    )

    assert "start and end must differ" in result.output
    assert "Default smart scheduling enabled" not in result.output


def test_plan_rejects_work_window_already_in_the_past(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())

    result = CliRunner().invoke(
        cli,
        ["plan", "--work-window", "08:00-10:00", "--date", "2020-01-01"],
    )

    assert result.exit_code == 1
    assert "already ended at" in result.output
    assert "Choose a later window or pass --date" in result.output


def test_accounts_set_usable_saves_account_planning_minutes(monkeypatch):
    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex-pro", provider="codex")])
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["accounts", "set-usable", "codex-pro", "150"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].usable_session_minutes == 150
    assert 'Usable session minutes for "codex-pro" set to 150.' in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["accounts", "set-usable", "codex", "150"],
        ["accounts", "set-role", "codex", "backup"],
        ["accounts", "set-weekly-reserve", "codex", "70"],
    ],
)
def test_account_planning_setting_changes_report_removed_orchestrated_pending(
    monkeypatch,
    command,
):
    saved: list[Config] = []
    account = AccountConfig(label="codex", provider="codex")
    config = Config(accounts=[account])
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    pending = {account_key_string(account): _pending_kick_for_test(account, now=now)}

    def save_pending(value):
        pending.clear()
        pending.update(value)

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr("tokenkick.cli.save_pending_kicks", save_pending)

    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 0
    assert pending == {}
    assert "Removed 1 orchestrated pending kick" in result.output
    assert "because account planning settings changed" in result.output


def test_account_orchestration_role_defaults_and_validation():
    account = AccountConfig.from_dict({"label": "codex", "provider": "codex"})

    assert account.orchestration_role == "normal"
    assert "orchestration_role" not in account.to_dict()

    configured = AccountConfig(
        label="claude",
        provider="claude",
        orchestration_role="use-first",
        weekly_reserve_threshold_percent="70",
    )
    payload = configured.to_dict()

    assert configured.orchestration_role == "use_first"
    assert configured.weekly_reserve_threshold_percent == 70
    assert payload["orchestration_role"] == "use_first"
    assert payload["weekly_reserve_threshold_percent"] == 70
    with pytest.raises(ValueError):
        AccountConfig(label="bad", orchestration_role="primary")
    with pytest.raises(ValueError):
        AccountConfig(label="bad", weekly_reserve_threshold_percent=100)


def test_setup_preserves_orchestration_role_and_reserve_threshold():
    discovered = [
        AccountConfig(label="codex", provider="codex"),
        AccountConfig(label="new", provider="codex", orchestration_role="backup"),
    ]
    existing = Config(
        accounts=[
            AccountConfig(
                label="codex",
                provider="codex",
                orchestration_role="specialist",
                weekly_reserve_threshold_percent=70,
            )
        ]
    )

    merged = _with_setup_auto_kick_defaults(discovered, existing)

    assert merged[0].orchestration_role == "specialist"
    assert merged[0].weekly_reserve_threshold_percent == 70
    assert merged[1].orchestration_role == "backup"


def test_accounts_set_role_accepts_aliases_and_rejects_unknown(monkeypatch):
    saved: list[Config] = []
    config = Config(accounts=[AccountConfig(label="codex", provider="codex")])
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})

    result = CliRunner().invoke(cli, ["accounts", "set-role", "codex", "backup-only"])
    invalid = CliRunner().invoke(cli, ["accounts", "set-role", "codex", "primary"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].orchestration_role == "backup"
    assert "Backup" in result.output
    assert invalid.exit_code == 2
    assert "orchestration_role must be one of" in invalid.output


def test_accounts_set_role_use_first_demotes_existing_primary(monkeypatch):
    saved: list[Config] = []
    accounts = [
        AccountConfig(label="personal", provider="codex"),
        AccountConfig(label="work", provider="codex", orchestration_role="use_first"),
        AccountConfig(label="reserve", provider="codex", orchestration_role="backup"),
    ]
    config = Config(accounts=accounts)
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    pending = {
        account_key_string(accounts[0]): PendingKick(
            account_key=account_key_string(accounts[0]),
            account_label=accounts[0].label,
            provider=accounts[0].provider,
            kick_at=to_utc_iso(now + timedelta(hours=1)),
            created_at=to_utc_iso(now),
            reason=ScheduleReason.ORCHESTRATED.value,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=to_utc_iso(now),
            work_end=to_utc_iso(now + timedelta(hours=2)),
            window_basis=SchedulingWindowBasis.SESSION.value,
        ),
        account_key_string(accounts[1]): PendingKick(
            account_key=account_key_string(accounts[1]),
            account_label=accounts[1].label,
            provider=accounts[1].provider,
            kick_at=to_utc_iso(now + timedelta(hours=1)),
            created_at=to_utc_iso(now),
            reason=ScheduleReason.ORCHESTRATED.value,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=to_utc_iso(now),
            work_end=to_utc_iso(now + timedelta(hours=2)),
            window_basis=SchedulingWindowBasis.SESSION.value,
        ),
    }

    def save_pending(value):
        pending.clear()
        pending.update(value)

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr("tokenkick.cli.save_pending_kicks", save_pending)

    result = CliRunner().invoke(cli, ["accounts", "set-role", "personal", "use-first"])

    assert result.exit_code == 0
    assert [account.orchestration_role for account in saved[0].accounts] == [
        "use_first",
        "normal",
        "backup",
    ]
    assert list(pending) == []
    assert "Only one account can be Use first" in result.output
    assert '"work"' in result.output


def test_accounts_weekly_reserve_set_and_clear(monkeypatch):
    saved: list[Config] = []
    current = Config(accounts=[AccountConfig(label="codex", provider="codex")])

    def save_config(config):
        nonlocal current
        current = copy.deepcopy(config)
        saved.append(copy.deepcopy(config))

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: current)
    monkeypatch.setattr("tokenkick.cli.Config.save", save_config)
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})

    set_result = CliRunner().invoke(cli, ["accounts", "set-weekly-reserve", "codex", "70"])
    clear_result = CliRunner().invoke(cli, ["accounts", "clear-weekly-reserve", "codex"])
    invalid_result = CliRunner().invoke(cli, ["accounts", "set-weekly-reserve", "codex", "100"])

    assert set_result.exit_code == 0
    assert clear_result.exit_code == 0
    assert invalid_result.exit_code == 2
    assert saved[0].accounts[0].weekly_reserve_threshold_percent == 70
    assert saved[1].accounts[0].weekly_reserve_threshold_percent is None


def test_accounts_planning_shows_roles_usage_and_threshold(monkeypatch):
    account = AccountConfig(
        label="codex",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=150,
        orchestration_role="use_first",
        weekly_reserve_threshold_percent=70,
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._load_status_cache", lambda _config: None)

    result = CliRunner().invoke(cli, ["accounts", "planning"])

    assert result.exit_code == 0
    assert "TokenKick Account Planning" in result.output
    assert "Use first" in result.output
    assert "150m" in result.output
    assert "70%" in result.output


def test_planning_setting_change_invalidates_only_orchestrated_pending(monkeypatch):
    account = AccountConfig(label="codex", provider="codex")
    other = AccountConfig(label="other", provider="codex")
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    pending = {
        account_key_string(account): PendingKick(
            account_key=account_key_string(account),
            account_label=account.label,
            provider=account.provider,
            kick_at=to_utc_iso(now + timedelta(hours=1)),
            created_at=to_utc_iso(now),
            reason=ScheduleReason.ORCHESTRATED.value,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=to_utc_iso(now),
            work_end=to_utc_iso(now + timedelta(hours=2)),
            window_basis=SchedulingWindowBasis.SESSION.value,
        ),
        account_key_string(other): PendingKick(
            account_key=account_key_string(other),
            account_label=other.label,
            provider=other.provider,
            kick_at=to_utc_iso(now + timedelta(hours=1)),
            created_at=to_utc_iso(now),
            reason=ScheduleReason.ORCHESTRATED.value,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=to_utc_iso(now),
            work_end=to_utc_iso(now + timedelta(hours=2)),
            window_basis=SchedulingWindowBasis.SESSION.value,
        ),
    }
    saved_pending = []
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account, other]))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda _config: None)
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr("tokenkick.cli.save_pending_kicks", lambda value: saved_pending.append(value))

    result = CliRunner().invoke(cli, ["accounts", "set-role", "codex", "excluded"])

    assert result.exit_code == 0
    assert list(saved_pending[0]) == [account_key_string(other)]


def test_pending_kick_purpose_defaults_and_diff_replaces_changed_purpose():
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    existing = PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at=to_utc_iso(now + timedelta(hours=1)),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now),
        work_end=to_utc_iso(now + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    legacy_payload = existing.to_dict()
    legacy_payload.pop("purpose")
    planned = PlannedKick(
        account_key=existing.account_key,
        account_label=existing.account_label,
        provider=existing.provider,
        kick_at=now + timedelta(hours=1),
        work_start=now,
        work_end=now + timedelta(hours=3),
        segment_start=now,
        segment_end=now + timedelta(hours=3),
        usable_session_minutes=90,
        purpose=PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
    )

    loaded = PendingKick.from_dict(legacy_payload)
    diff = build_pending_kick_diff([planned], {existing.account_key: existing})

    assert loaded is not None
    assert loaded.purpose == PENDING_KICK_PURPOSE_COVERAGE
    assert diff.replaces_orchestrated[0]["planned"]["purpose"] == PENDING_KICK_PURPOSE_SPECIALIST_READINESS


@pytest.mark.parametrize(
    ("raw", "minutes"),
    [
        ("180", 180),
        ("180m", 180),
        ("3h", 180),
        ("2.5h", 150),
        ("1h30m", 90),
    ],
)
def test_plan_usage_duration_parser_accepts_supported_forms(raw: str, minutes: int):
    assert _parse_plan_usage_duration_minutes(raw) == minutes


@pytest.mark.parametrize("raw", ["0", "-1", "1441", "abc", "1d"])
def test_plan_usage_duration_parser_rejects_invalid_values(raw: str):
    with pytest.raises(click.ClickException):
        _parse_plan_usage_duration_minutes(raw)


def test_plan_usage_overrides_parse_exact_account_labels():
    account = AccountConfig(label="codex (home)", provider="codex")

    parsed = _parse_plan_usage_overrides([account], ["codex (home)=3h"])

    assert parsed == {"manual|codex|codex (home)": 180}


@pytest.mark.parametrize(
    "values",
    [
        ["missing=3h"],
        ["codex=3h", "codex=2h"],
        ["codex"],
        ["codex="],
    ],
)
def test_plan_usage_overrides_reject_invalid_entries(values: list[str]):
    account = AccountConfig(label="codex", provider="codex")

    with pytest.raises(click.ClickException):
        _parse_plan_usage_overrides([account], values)


def _fresh_orchestration_status(
    account: AccountConfig,
    now: datetime,
    *,
    used_percent: float = 0.0,
) -> AccountStatus:
    return AccountStatus(
        label=account.label,
        state=AccountState.FRESH,
        used_percent=used_percent,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
        observed_at=to_utc_iso(now),
    )


def _build_direct_orchestration_plan(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    *,
    now: datetime,
    work_start: datetime,
    work_end: datetime,
):
    return build_orchestration_plan(
        config=Config(accounts=accounts, schedule=ScheduleConfig(timezone="UTC")),
        inputs=[
            AccountPlanInput(account=account, status=status, cache_stale=False)
            for account, status in zip(accounts, statuses, strict=False)
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
        cache_age_seconds=0,
    )


def test_orchestration_excluded_spark_skips_before_unmeasured_check():
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    account = AccountConfig(
        label="codex-spark",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        codex_rate_limit_id="codex_bengalfox",
        auto_kick=True,
        session_auto_kick=True,
        orchestration_role="excluded",
    )

    plan = _build_direct_orchestration_plan(
        [account],
        [_fresh_orchestration_status(account, now)],
        now=now,
        work_start=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc),
    )

    assert plan.skipped_accounts[0].reason == "orchestration_excluded"
    assert plan.accounts_considered[0]["reason"] == "orchestration_excluded"


def test_orchestration_preserves_use_first_when_equal_coverage_is_available():
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    normal = AccountConfig(
        label="normal",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=120,
    )
    preferred = AccountConfig(
        label="preferred",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
        orchestration_role="use_first",
    )

    plan = _build_direct_orchestration_plan(
        [normal, preferred],
        [_fresh_orchestration_status(normal, now), _fresh_orchestration_status(preferred, now)],
        now=now,
        work_start=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc),
    )

    assert plan.segments[0].account_label == "normal"
    assert not plan.coverage_gaps


def test_orchestration_backup_used_when_it_avoids_worse_coverage():
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    short_primary = AccountConfig(
        label="short-primary",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=30,
    )
    backup = AccountConfig(
        label="backup",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
        orchestration_role="backup",
    )

    plan = _build_direct_orchestration_plan(
        [short_primary, backup],
        [_fresh_orchestration_status(short_primary, now), _fresh_orchestration_status(backup, now)],
        now=now,
        work_start=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc),
    )

    assert plan.segments[0].account_label == "backup"
    assert plan.coverage_gaps == []


def test_orchestration_weekly_reserve_demotes_to_backup():
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    reserved = AccountConfig(
        label="reserved",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
        orchestration_role="use_first",
        weekly_reserve_threshold_percent=70,
    )
    normal = AccountConfig(
        label="normal",
        provider="codex",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
    )

    plan = _build_direct_orchestration_plan(
        [reserved, normal],
        [
            _fresh_orchestration_status(reserved, now, used_percent=70.0),
            _fresh_orchestration_status(normal, now, used_percent=0.0),
        ],
        now=now,
        work_start=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc),
    )

    reserved_row = next(row for row in plan.accounts_considered if row["account_label"] == "reserved")
    assert reserved_row["effective_orchestration_role"] == "backup"
    assert plan.segments[0].account_label == "normal"


def test_specialist_readiness_plans_separate_purpose_without_main_coverage():
    now = datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc)
    specialist = AccountConfig(
        label="claude",
        provider="claude",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
        orchestration_role="specialist",
    )

    plan = _build_direct_orchestration_plan(
        [specialist],
        [_fresh_orchestration_status(specialist, now)],
        now=now,
        work_start=datetime(2026, 6, 5, 18, 30, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 23, 30, tzinfo=timezone.utc),
    )

    assert plan.segments == []
    assert plan.planned_kicks[0].account_label == "claude"
    assert plan.planned_kicks[0].purpose == PENDING_KICK_PURPOSE_SPECIALIST_READINESS
    assert plan.planned_kicks[0].kick_at == datetime(2026, 6, 5, 15, 0, tzinfo=timezone.utc)
    assert plan.accounts_considered[0]["specialist_readiness_planned"] is True


def test_specialist_readiness_skips_when_early_kick_window_missed():
    now = datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)
    specialist = AccountConfig(
        label="claude",
        provider="claude",
        auto_kick=True,
        session_auto_kick=True,
        usable_session_minutes=90,
        orchestration_role="specialist",
    )

    plan = _build_direct_orchestration_plan(
        [specialist],
        [_fresh_orchestration_status(specialist, now)],
        now=now,
        work_start=datetime(2026, 6, 5, 18, 30, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 23, 30, tzinfo=timezone.utc),
    )

    assert plan.planned_kicks == []
    assert plan.skipped_accounts[0].reason == "specialist_early_kick_window_missed"
    assert plan.accounts_considered[0]["specialist_readiness_planned"] is False


def test_interactive_orchestration_custom_usage_passes_overrides(monkeypatch):
    import tokenkick.interactive as interactive

    config = Config(
        schedule=ScheduleConfig(timezone="UTC"),
        accounts=[
            AccountConfig(
                label="codex",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            )
        ],
    )
    selections = iter(["schedule", "orchestration", "today", "custom", "custom", "exit", "exit", "exit"])
    built = []
    plan = SimpleNamespace(
        planned_kicks=[],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: "3h")
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: config)
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **kwargs: built.append(kwargs) or (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert built[0]["usage_overrides"] == ("codex=180m",)


def test_interactive_orchestration_blank_usage_keeps_default(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    config = Config(
        accounts=[
            AccountConfig(
                label="codex",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            )
        ]
    )
    selections = iter(["custom", "custom"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: "")

    assert interactive._orchestration_usage_overrides(config, cli_module) == ()


def test_interactive_orchestration_usage_prompt_labels_default_custom(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    prompts = []
    config = Config(
        accounts=[
            AccountConfig(
                label="codex (personal)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=150,
            ),
            AccountConfig(
                label="codex (work)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
            AccountConfig(
                label="codex (reserve)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
            AccountConfig(
                label="claude (work)",
                provider="claude",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=90,
            ),
        ]
    )

    def fake_select(message, choices, **kwargs):
        prompts.append((message, [choice.name for choice in choices], kwargs.get("default")))
        return "default"

    monkeypatch.setattr(interactive, "_select", fake_select)

    assert interactive._orchestration_usage_overrides(config, cli_module) == ()
    assert prompts == [
        (
            "Usage assumptions:",
            ["Default (Personal 2h30m, Work 2h, Reserve 2h, Claude 1h30m)", "Custom", "Back"],
            "default",
        )
    ]


def test_interactive_orchestration_usage_prompt_truncates_long_defaults():
    import tokenkick.interactive as interactive

    config = Config(
        accounts=[
            AccountConfig(
                label="codex (personal)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=150,
            ),
            AccountConfig(
                label="codex (work)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
            AccountConfig(
                label="codex (reserve)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
            AccountConfig(
                label="claude (work)",
                provider="claude",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=90,
            ),
            AccountConfig(
                label="codex (backup-one)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
            AccountConfig(
                label="codex (backup-two)",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            ),
        ]
    )

    assert (
        interactive._usage_defaults_menu_label(interactive._orchestration_usage_accounts(config), config)
        == "Default (Personal 2h30m, Work 2h, Reserve 2h, Claude 1h30m, +2 more)"
    )


def test_interactive_orchestration_usage_presets_avoid_text_entry(monkeypatch):
    import tokenkick.cli as cli_module
    import tokenkick.interactive as interactive

    config = Config(
        accounts=[
            AccountConfig(
                label="codex",
                provider="codex",
                auto_kick=True,
                session_auto_kick=True,
                usable_session_minutes=120,
            )
        ]
    )
    selections = iter(["custom", "180"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: pytest.fail("preset must not ask for text"))

    assert interactive._orchestration_usage_overrides(config, cli_module) == ("codex=180m",)


def test_usage_choice_options_are_presets_not_set_value():
    import tokenkick.interactive as interactive

    labels = [choice.name for choice in interactive._usage_choice_options(120)]

    assert labels[0] == "Keep default (2h)"
    assert "3h" in labels
    assert not any("Set value" in label for label in labels)


def test_time_picker_initial_slot_uses_next_half_hour():
    import tokenkick.interactive as interactive

    assert interactive._next_half_hour_slot(datetime(2026, 6, 9, 13, 33)) == "14:00"
    assert interactive._next_half_hour_slot(datetime(2026, 6, 9, 13, 30)) == "13:30"
    assert interactive._next_half_hour_slot(datetime(2026, 6, 9, 23, 59)) == "00:00"


def test_time_picker_initial_slot_uses_morning_for_future_orchestration_dates():
    import tokenkick.interactive as interactive

    now = datetime(2026, 6, 9, 13, 33)

    assert interactive._initial_work_window_start_slot(date(2026, 6, 9), now) == "14:00"
    assert interactive._initial_work_window_start_slot(date(2026, 6, 10), now) == "07:00"
    assert interactive._initial_work_window_start_slot(date(2026, 6, 20), now) == "07:00"
    assert interactive._initial_work_window_start_slot(None, now) == "14:00"


def test_plan_time_range_includes_dates_when_crossing_midnight():
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime(2026, 6, 9, 22, 30, tzinfo=local_tz)
    end = datetime(2026, 6, 10, 2, 0, tzinfo=local_tz)

    assert _format_plan_time_range(start, end) == "22:30-02:00 (+1 day)"


def test_plan_time_range_marks_next_day_relative_to_work_start():
    local_tz = datetime.now().astimezone().tzinfo
    reference = datetime(2026, 6, 9, 21, 0, tzinfo=local_tz)
    start = datetime(2026, 6, 10, 0, 30, tzinfo=local_tz)
    end = datetime(2026, 6, 10, 2, 0, tzinfo=local_tz)

    assert _format_plan_time_range(start, end, reference=reference) == "00:30-02:00 (+1 day)"


def test_plan_source_labels_are_human_readable():
    assert _format_plan_source("planned_early_anchor") == "Pre-anchor"
    assert _format_plan_source("expected_reset_reuse") == "Reset-boundary reuse"
    assert _format_plan_source("planned_fresh_session") == "Fresh session"
    assert _format_plan_source("natural_reset_reuse") == "Natural reset reuse"


def test_render_plan_uses_compact_human_output(capsys):
    local_tz = datetime.now().astimezone().tzinfo
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=local_tz)
    work_end = datetime(2026, 6, 10, 2, 0, tzinfo=local_tz)
    plan = OrchestrationPlan(
        read_only=True,
        applied=False,
        work_start=work_start,
        work_end=work_end,
        timezone="UTC",
        accounts_considered=[
            {
                "account_key": "manual|codex|personal",
                "usage_source": "plan_override",
            }
        ],
        segments=[
            PlannedSegment(
                account_key="manual|codex|personal",
                account_label="codex (personal)",
                provider="codex",
                start=work_start,
                end=datetime(2026, 6, 9, 23, 30, tzinfo=local_tz),
                source="planned_early_anchor",
                kick_at=datetime(2026, 6, 9, 18, 30, tzinfo=local_tz),
            ),
            PlannedSegment(
                account_key="manual|codex|personal",
                account_label="codex (personal)",
                provider="codex",
                start=datetime(2026, 6, 9, 23, 30, tzinfo=local_tz),
                end=work_end,
                source="expected_reset_reuse",
            ),
        ],
        planned_kicks=[
            PlannedKick(
                account_key="manual|codex|personal",
                account_label="codex (personal)",
                provider="codex",
                kick_at=datetime(2026, 6, 9, 18, 30, tzinfo=local_tz),
                work_start=work_start,
                work_end=work_end,
                segment_start=work_start,
                segment_end=work_end,
                usable_session_minutes=150,
            ),
            PlannedKick(
                account_key="manual|claude|work",
                account_label="claude (work)",
                provider="claude",
                kick_at=datetime(2026, 6, 9, 17, 30, tzinfo=local_tz),
                work_start=work_start,
                work_end=datetime(2026, 6, 10, 0, 0, tzinfo=local_tz),
                segment_start=work_start,
                segment_end=datetime(2026, 6, 10, 0, 0, tzinfo=local_tz),
                usable_session_minutes=90,
                purpose=PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
            ),
        ],
        skipped_accounts=[],
        coverage_gaps=[],
        diff=PendingKickDiff(),
        limitations=[],
        message="read-only plan; no pending kicks were changed",
    )

    _render_plan(plan)

    output = capsys.readouterr().out
    assert "TokenKick" in output
    assert "Pre-anchor" in output
    assert "Reset-boundary reuse" in output
    assert "planned_early_anchor" not in output
    assert "expected_reset_reuse" not in output
    assert "23:30-02:00 (+1 day)" in output
    assert "Specialist readiness" not in output
    assert "specialist readiness" in output
    assert "2h30m override" in output
    assert "reset 22:30" in output
    assert "1h30m + reset + 1h30m" in output


def test_render_plan_explains_skipped_specialist_auto_kick_fallback(capsys):
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc)
    work_end = datetime(2026, 6, 10, 2, 0, tzinfo=timezone.utc)
    plan = OrchestrationPlan(
        read_only=True,
        applied=False,
        work_start=work_start,
        work_end=work_end,
        timezone="UTC",
        accounts_considered=[
            {
                "account_key": "manual|claude|work",
                "account_label": "claude (work)",
                "provider": "claude",
                "effective_orchestration_role": "specialist",
                "included": False,
                "reason": "specialist_not_available_for_early_kick",
            }
        ],
        segments=[],
        planned_kicks=[],
        skipped_accounts=[
            SkippedAccount(
                account_key="manual|claude|work",
                account_label="claude (work)",
                provider="claude",
                reason="specialist_not_available_for_early_kick",
            )
        ],
        coverage_gaps=[],
        diff=PendingKickDiff(),
        limitations=[],
        message="read-only plan; no pending kicks were changed",
    )

    _render_plan(plan)

    output = capsys.readouterr().out
    assert "Skipped specialist" in output
    assert "claude (work): could not be prepared for this plan" in output
    assert "current session timing does not make the early" in output
    assert "kick" in output
    assert "available" in output
    assert "normal auto-kick can still run" in output


def test_render_plan_hints_refresh_for_stale_skipped_specialist(capsys):
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc)
    plan = OrchestrationPlan(
        read_only=True,
        applied=False,
        work_start=work_start,
        work_end=work_start + timedelta(hours=3),
        timezone="UTC",
        accounts_considered=[
            {
                "account_key": "manual|claude|work",
                "account_label": "claude (work)",
                "provider": "claude",
                "effective_orchestration_role": "specialist",
                "included": False,
                "reason": "stale_status",
            }
        ],
        segments=[],
        planned_kicks=[],
        skipped_accounts=[
            SkippedAccount(
                account_key="manual|claude|work",
                account_label="claude (work)",
                provider="claude",
                reason="stale_status",
            )
        ],
        coverage_gaps=[],
        diff=PendingKickDiff(),
        limitations=[],
        message="read-only plan; no pending kicks were changed",
    )

    _render_plan(plan)

    output = capsys.readouterr().out
    assert "cached provider status is stale" in output
    assert "Run tk status --refresh, then rebuild the plan" in output


def test_plan_timestamp_includes_date_when_after_reference_day():
    local_tz = datetime.now().astimezone().tzinfo
    reference = datetime(2026, 6, 9, 22, 30, tzinfo=local_tz)
    value = datetime(2026, 6, 10, 0, 15, tzinfo=local_tz)

    assert _format_plan_timestamp(value, reference=reference) == "2026-06-10 00:15"


def test_interactive_orchestration_preview_uses_today_without_apply(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "today", "no", "exit", "exit", "exit"])
    rendered = []
    built = []
    plan = SimpleNamespace(
        planned_kicks=[],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **kwargs: built.append(kwargs) or (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda candidate: rendered.append(candidate))
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("preview-only empty plan must not apply"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert built == [
        {
            "work_window": "10:00-13:00",
            "date_text": datetime.now(timezone.utc).date().isoformat(),
            "timezone_text": None,
            "usage_overrides": (),
        }
    ]
    assert rendered == [plan]


def test_interactive_orchestration_custom_date_and_apply(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "custom", "set", "no", "exit", "exit", "exit"])
    confirms = []
    built = []
    applied = []
    warning_renders = []
    picker_kwargs = []
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    applied_plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        applied=True,
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: "2026-06-07")
    monkeypatch.setattr(interactive, "pick_work_window", lambda **kwargs: picker_kwargs.append(kwargs) or "18:30-23:30")
    monkeypatch.setattr(interactive, "_confirm", lambda message, **_kwargs: confirms.append(message) or True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **kwargs: built.append(kwargs) or (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli._render_current_reservation_advisories",
        lambda: warning_renders.append("rendered"),
    )

    def apply_plan(candidate, *, now, current_time=None):
        applied.append((candidate, now))
        return applied_plan

    monkeypatch.setattr("tokenkick.cli.apply_orchestration_plan", apply_plan)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert picker_kwargs == [{"title": "Orchestration work window", "base_date": date(2026, 6, 7)}]
    assert built == [
        {
            "work_window": "18:30-23:30",
            "date_text": "2026-06-07",
            "timezone_text": None,
            "usage_overrides": (),
        }
    ]
    assert confirms == ["Apply this orchestration plan?"]
    assert applied == [(plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc))]
    assert warning_renders == ["rendered"]


def test_interactive_orchestration_refreshes_stale_specialist_and_rebuilds(monkeypatch):
    import tokenkick.interactive as interactive

    stale_specialist = SkippedAccount(
        account_key="manual|claude|work",
        account_label="claude (work)",
        provider="claude",
        reason="stale_status",
    )
    stale_plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        accounts_considered=[
            {
                "account_key": "manual|claude|work",
                "effective_orchestration_role": "specialist",
            }
        ],
        skipped_accounts=[stale_specialist],
    )
    refreshed_plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        accounts_considered=[],
        skipped_accounts=[],
    )
    selections = iter(["schedule", "orchestration", "today", "default", "refresh", "exit", "exit", "exit"])
    built = []
    rendered = []
    refreshed = []
    applied = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))

    def build_plan(**kwargs):
        built.append(kwargs)
        plan = stale_plan if len(built) == 1 else refreshed_plan
        return plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("tokenkick.cli._build_plan_from_options", build_plan)
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda plan: rendered.append(plan))
    monkeypatch.setattr("tokenkick.cli._refresh_status_cache_fast", lambda config: refreshed.append(config))
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda plan, *, now, current_time=None: applied.append((plan, now)) or plan,
    )
    monkeypatch.setattr("tokenkick.cli._render_current_reservation_advisories", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert len(built) == 2
    assert rendered == [stale_plan, refreshed_plan, refreshed_plan]
    assert len(refreshed) == 1
    assert applied == [(refreshed_plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc))]


def test_interactive_orchestration_can_continue_without_stale_specialist_refresh(monkeypatch):
    import tokenkick.interactive as interactive

    stale_specialist = SkippedAccount(
        account_key="manual|claude|work",
        account_label="claude (work)",
        provider="claude",
        reason="stale_status",
    )
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        accounts_considered=[
            {
                "account_key": "manual|claude|work",
                "effective_orchestration_role": "specialist",
            }
        ],
        skipped_accounts=[stale_specialist],
    )
    selections = iter(["schedule", "orchestration", "today", "default", "continue", "exit", "exit", "exit"])
    applied = []
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache_fast",
        lambda *_args, **_kwargs: pytest.fail("continue must not refresh status"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda candidate, *, now, current_time=None: applied.append((candidate, now)) or candidate,
    )
    monkeypatch.setattr("tokenkick.cli._render_current_reservation_advisories", lambda: None)

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert applied == [(plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc))]


def test_interactive_orchestration_stale_specialist_back_does_not_apply(monkeypatch):
    import tokenkick.interactive as interactive

    stale_specialist = SkippedAccount(
        account_key="manual|claude|work",
        account_label="claude (work)",
        provider="claude",
        reason="stale_status",
    )
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
        accounts_considered=[
            {
                "account_key": "manual|claude|work",
                "effective_orchestration_role": "specialist",
            }
        ],
        skipped_accounts=[stale_specialist],
    )
    selections = iter(["schedule", "orchestration", "today", "default", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache_fast",
        lambda *_args, **_kwargs: pytest.fail("back must not refresh status"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("back must not apply orchestration"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0


def test_interactive_orchestration_apply_declined_does_not_mutate(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "tomorrow", "no", "exit", "exit", "exit"])
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[]),
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("declined orchestration apply must not mutate"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0


def test_interactive_orchestration_unmanaged_conflict_blocks_apply(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "today", "no", "exit", "exit", "exit"])
    plan = SimpleNamespace(
        planned_kicks=[object()],
        diff=SimpleNamespace(conflicts_unmanaged=[{"reason": "conflict_unmanaged_pending"}]),
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "10:00-13:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: pytest.fail("conflict must not prompt apply"))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr("tokenkick.cli._render_plan", lambda _plan: None)
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("conflicted orchestration plan must not apply"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert "not applied; resolve unmanaged pending-kick conflicts first" in result.output


def test_interactive_orchestration_invalid_custom_date_returns_to_date_step(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "custom", "set", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text", lambda *_args, **_kwargs: "2026-99-99")
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: pytest.fail("invalid date must not open window picker"))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="UTC")))
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("invalid date must not apply"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert "Invalid date: use YYYY-MM-DD." in result.output


def test_interactive_orchestration_engine_rejection_returns_to_date_step(monkeypatch):
    import tokenkick.interactive as interactive

    selections = iter(["schedule", "orchestration", "today", "no", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "02:00-03:00")
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(schedule=ScheduleConfig(timezone="Europe/Berlin")))
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (_ for _ in ()).throw(click.ClickException("nonexistent local time")),
    )
    monkeypatch.setattr(
        "tokenkick.cli.apply_orchestration_plan",
        lambda *_args, **_kwargs: pytest.fail("rejected plan must not apply"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert "nonexistent local time" in result.output


def test_interactive_notifications_menu_saves_ntfy(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    selections = iter(["configure", "notifications", "ntfy", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_text_action", lambda *_args, **_kwargs: "topic-name")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].notifications.enabled is True
    assert saved[0].notifications.backend == "ntfy"
    assert saved[0].notifications.ntfy_topic == "topic-name"


def test_interactive_notifications_menu_toggles_account_notifications(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    confirm_defaults: list[bool] = []
    accounts = [
        AccountConfig(label="codex", provider="codex", notifications_enabled=True),
        AccountConfig(label="claude", provider="claude", notifications_enabled=True),
    ]
    selections = iter(
        [
            "configure",
            "notifications",
            "accounts",
            "disable_selected",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: ["codex"])
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda *_args, **kwargs: confirm_defaults.append(kwargs.get("default")) or True,
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].notifications_enabled is False
    assert saved[0].accounts[1].notifications_enabled is True
    assert confirm_defaults == [True]


def test_interactive_notifications_menu_toggles_one_account_without_confirm(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex-spark (personal)", provider="codex", notifications_enabled=True),
        AccountConfig(label="claude", provider="claude", notifications_enabled=True),
    ]
    selections = iter(
        [
            "configure",
            "notifications",
            "accounts",
            "disable_one",
            "codex-spark (personal)",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda *_args, **_kwargs: pytest.fail("single-account notification toggles should not confirm"),
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].notifications_enabled is False
    assert saved[0].accounts[1].notifications_enabled is True


def test_interactive_notifications_menu_sets_one_account_route(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    accounts = [
        AccountConfig(label="codex-spark (personal)", provider="codex"),
        AccountConfig(label="claude", provider="claude"),
    ]
    selections = iter(
        [
            "configure",
            "notifications",
            "accounts",
            "set_route",
            "codex-spark (personal)",
            "telegram",
            "exit",
            "exit",
            "exit",
            "exit",
        ]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert saved[0].accounts[0].notifications_enabled is True
    assert saved[0].accounts[0].notification_backends == ["telegram"]
    assert saved[0].accounts[1].notification_backends is None


def test_interactive_notifications_menu_empty_bulk_selection_warns(monkeypatch):
    import tokenkick.interactive as interactive

    saved: list[Config] = []
    accounts = [AccountConfig(label="codex", provider="codex", notifications_enabled=True)]
    selections = iter(["configure", "notifications", "accounts", "disable_selected", "exit", "exit", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_checkbox", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        interactive,
        "_confirm",
        lambda *_args, **_kwargs: pytest.fail("empty notification selections should not confirm"),
    )
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.interactive.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert "No accounts selected; press Space to select account(s), then Enter." in result.output
    assert saved == []


def test_interactive_kick_menu_decline_does_not_kick(monkeypatch):
    import tokenkick.interactive as interactive

    accounts = [AccountConfig(label="codex", provider="codex")]
    selections = iter(["kick", "codex", "exit", "exit"])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=accounts))
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("declined interactive kick must not run"),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0


def test_poll_shows_current_interval(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(poll_interval_minutes=13),
    )

    result = CliRunner().invoke(cli, ["poll"])

    assert result.exit_code == 0
    assert "Daemon poll interval: 13m" in result.output


def test_poll_sets_interval(monkeypatch):
    saved: list[Config] = []
    config = Config(poll_interval_minutes=10)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["poll", "5"])

    assert result.exit_code == 0
    assert "Daemon poll interval set to 5m" in result.output
    assert saved[0].poll_interval_minutes == 5


def test_poll_rejects_non_positive_interval(monkeypatch):
    saved: list[Config] = []
    config = Config(poll_interval_minutes=10)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(self))

    result = CliRunner().invoke(cli, ["poll", "0"])

    assert result.exit_code == 0
    assert "at least 1 minute" in result.output
    assert saved == []


def test_claude_direct_usage_global_commands_round_trip(tmp_path, monkeypatch):
    _isolate_config_files(monkeypatch, tmp_path)
    Config(claude=ClaudeConfig(direct_usage_enabled=True)).save()

    disable = CliRunner().invoke(cli, ["claude", "direct-usage", "disable"])
    disabled = Config.load()
    enable = CliRunner().invoke(cli, ["claude", "direct-usage", "enable"])
    enabled = Config.load()

    assert disable.exit_code == 0
    assert disabled.claude.direct_usage_enabled is False
    assert disabled.claude.direct_usage_explicit is True
    assert enable.exit_code == 0
    assert enabled.claude.direct_usage_enabled is True
    assert enabled.claude.direct_usage_explicit is True


def test_accounts_set_direct_usage_round_trips(tmp_path, monkeypatch):
    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    Config(accounts=[account]).save()

    disable = CliRunner().invoke(cli, ["accounts", "set-direct-usage", "claude", "--disable"])
    disabled = Config.load().accounts[0]
    enable = CliRunner().invoke(cli, ["accounts", "set-direct-usage", "claude", "--enable"])
    enabled = Config.load().accounts[0]

    assert disable.exit_code == 0
    assert disabled.direct_usage_enabled is False
    assert enable.exit_code == 0
    assert enabled.direct_usage_enabled is True


def test_accounts_set_direct_usage_rejects_gemini(tmp_path, monkeypatch):
    _isolate_config_files(monkeypatch, tmp_path)
    account = AccountConfig(label="gemini", provider="gemini")
    Config(accounts=[account]).save()

    result = CliRunner().invoke(cli, ["accounts", "set-direct-usage", "gemini", "--enable"])

    assert result.exit_code == 1
    assert "Gemini is monitor-only; auto-kick cannot be enabled." in result.output
    assert Config.load().accounts[0].direct_usage_enabled is True


def test_schedule_set_show_clear_and_disable_persist(monkeypatch):
    saved: list[Config] = []
    config = Config()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    monkeypatch.setattr("tokenkick.cli.invalidate_pending_kicks", lambda **_kwargs: [])

    set_result = CliRunner().invoke(
        cli,
        [
            "schedule",
            "set",
            "--account",
            "personal",
            "--weekdays",
            "14:00-21:00",
            "--weekends",
            "10:00-16:00",
            "--timezone",
            "Europe/Berlin",
        ],
    )
    show_result = CliRunner().invoke(cli, ["schedule", "show", "--account", "personal"])
    disable_result = CliRunner().invoke(cli, ["schedule", "disable", "--account", "personal"])
    clear_result = CliRunner().invoke(cli, ["schedule", "clear", "--account", "personal"])

    assert set_result.exit_code == 0
    assert saved[0].schedule.enabled is True
    assert saved[0].schedule.timezone == "Europe/Berlin"
    assert saved[0].schedule.accounts["personal"].weekdays == "14:00-21:00"
    assert "14:00-21:00" in show_result.output
    assert disable_result.exit_code == 0
    assert saved[1].schedule.accounts["personal"].enabled is False
    assert clear_result.exit_code == 0
    assert "personal" not in saved[2].schedule.accounts


def test_schedule_set_rejects_nonexistent_dst_boundary(monkeypatch):
    saved: list[Config] = []
    config = Config()

    class DstTransitionDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 29, 12, tzinfo=tz)

    monkeypatch.setattr("tokenkick.cli.datetime", DstTransitionDateTime)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))

    result = CliRunner().invoke(
        cli,
        [
            "schedule",
            "set",
            "--default",
            "--weekdays",
            "02:30-04:00",
            "--timezone",
            "Europe/Berlin",
        ],
    )

    assert result.exit_code == 0
    assert "does not exist" in result.output
    assert "DST" in result.output
    assert "transition" in result.output
    assert saved == []


def test_schedule_show_surfaces_stale_blocked_pending_kick(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        ),
    )
    pending = scheduling_mod.PendingKick(
        account_key="manual|codex|personal",
        account_label="personal",
        provider="codex",
        kick_at="2099-05-22T17:00:00Z",
        created_at="2099-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2099-05-22T17:00:00Z",
        work_end="2099-05-22T21:00:00Z",
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({"manual|codex|personal": pending})
    _save_status_cache(
        [account],
        {
            "manual|codex|personal": AccountStatus(
                label="personal",
                state=AccountState.FRESH,
                stale=True,
                stale_seconds=1_800,
                source_detail="CodexBar widget snapshot",
            )
        },
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)

    result = CliRunner().invoke(cli, ["schedule", "show"])

    assert result.exit_code == 0
    assert "blocked:" in result.output
    assert "CodexBar widget snapshot" in result.output
    assert "automatic kick is blocked" in result.output
    assert "Schedule printed at" in result.output


def test_schedule_show_missing_account_prints_timestamp(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})

    result = CliRunner().invoke(cli, ["schedule", "show", "--account", "missing"])

    assert result.exit_code == 0
    assert 'No account schedule for "missing".' in result.output
    assert "Schedule printed at" in result.output


def test_schedule_show_surfaces_failed_pending_kick(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        ),
    )
    pending = scheduling_mod.PendingKick(
        account_key="manual|codex|personal",
        account_label="personal",
        provider="codex",
        kick_at="2099-05-22T17:00:00Z",
        created_at="2099-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2099-05-22T17:00:00Z",
        work_end="2099-05-22T21:00:00Z",
        window_basis="session",
        attempt_count=4,
        last_error="provider failed",
        gave_up_at="2099-05-22T18:00:00Z",
    )
    scheduling_mod.save_pending_kicks({"manual|codex|personal": pending})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)

    result = CliRunner().invoke(cli, ["schedule", "show"])

    assert result.exit_code == 0
    assert "failed (4 attempts)" in result.output
    assert "gave up after 4 attempts" in result.output


def _pending_kick_for_section(
    scheduling_mod,
    label: str,
    *,
    reason: str = "optimal",
    purpose: str = "coverage",
    **overrides,
):
    fields = {
        "account_key": f"manual|codex|{label}",
        "account_label": label,
        "provider": "codex",
        "kick_at": "2099-05-22T17:00:00Z",
        "created_at": "2099-05-22T15:31:00Z",
        "reason": reason,
        "windows_needed": 1,
        "expected_waste_minutes": 0,
        "waste_location": "none",
        "work_start": "2099-05-22T17:00:00Z",
        "work_end": "2099-05-22T21:00:00Z",
        "window_basis": "session",
        "purpose": purpose,
    }
    fields.update(overrides)
    return scheduling_mod.PendingKick(**fields)


def test_schedule_show_lists_pending_kicks_without_schedule_rows(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending-kicks.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    config = Config(
        accounts=[
            AccountConfig(label="personal", provider="codex", auto_kick=True),
            AccountConfig(label="research", provider="codex", auto_kick=True),
        ],
        schedule=ScheduleConfig(
            enabled=True,
            default=WorkSchedule(enabled=True, weekdays="09:00-17:00"),
        ),
    )
    scheduling_mod.save_pending_kicks(
        {
            "manual|codex|personal": _pending_kick_for_section(scheduling_mod, "personal"),
            "manual|codex|research": _pending_kick_for_section(
                scheduling_mod,
                "research",
                reason="orchestrated",
                purpose="specialist_readiness",
            ),
        }
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)

    result = CliRunner().invoke(cli, ["schedule", "show"])

    assert result.exit_code == 0
    assert "Pending kicks" in result.output
    assert "personal" in result.output
    assert "research" in result.output
    assert "orchestrated" in result.output
    assert "scheduled" in result.output


def test_schedule_show_account_filter_shows_pending_without_schedule_override(
    monkeypatch,
    tmp_path,
):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending-kicks.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    config = Config(
        accounts=[AccountConfig(label="research", provider="codex", auto_kick=True)],
        schedule=ScheduleConfig(
            enabled=True,
            default=WorkSchedule(enabled=True, weekdays="09:00-17:00"),
        ),
    )
    scheduling_mod.save_pending_kicks(
        {
            "manual|codex|research": _pending_kick_for_section(
                scheduling_mod,
                "research",
                reason="orchestrated",
            ),
        }
    )
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)

    result = CliRunner().invoke(cli, ["schedule", "show", "--account", "research"])

    assert result.exit_code == 0
    assert 'No account schedule for "research".' in result.output
    assert "Pending kicks" in result.output
    assert "research" in result.output
    assert "orchestrated" in result.output
    assert "Schedule printed at" in result.output


def test_schedule_show_reports_no_pending_kicks(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr("tokenkick.cli.load_pending_kicks", lambda *_args, **_kwargs: {})

    result = CliRunner().invoke(cli, ["schedule", "show"])

    assert result.exit_code == 0
    assert "No pending kicks." in result.output


def test_format_pending_kick_section_status_variants(tmp_path, monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    from tokenkick.cli import _format_pending_kick_section_status

    scheduled = _pending_kick_for_section(scheduling_mod, "personal")
    assert _format_pending_kick_section_status(scheduled) == "scheduled"

    retrying = _pending_kick_for_section(
        scheduling_mod,
        "personal",
        attempt_count=2,
        last_error="provider failed",
        next_retry_at="2099-05-22T18:30:00Z",
    )
    rendered = _format_pending_kick_section_status(retrying)
    assert rendered.startswith("retrying after failure 2; next attempt ")

    gave_up = _pending_kick_for_section(
        scheduling_mod,
        "personal",
        attempt_count=4,
        last_error="provider failed",
        gave_up_at="2099-05-22T18:00:00Z",
    )
    assert _format_pending_kick_section_status(gave_up) == "gave up after 4 attempts"


def test_schedule_show_warns_and_quarantines_corrupt_pending_kicks(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    pending_file = scheduling_mod.PENDING_KICKS_FILE
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text("{not valid json")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())

    result = CliRunner().invoke(cli, ["schedule", "show"])

    assert result.exit_code == 0
    assert "was corrupt" in result.output
    assert "moved it aside" in result.output
    assert "No pending kicks." in result.output
    assert not pending_file.exists()
    quarantined = list(pending_file.parent.glob("pending-kicks.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{not valid json"


def test_plan_apply_reports_not_applied_when_pending_save_fails(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())

    def refuse_save(_data):
        raise PendingKickStateError("disk full")

    monkeypatch.setattr("tokenkick.orchestration.save_pending_kicks", refuse_save)
    future_day = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "09:00-12:00",
            "--date",
            future_day,
            "--apply",
            "--yes",
            "--json-output",
        ],
    )

    payload = _status_json_payload(result.output)
    assert payload["applied"] is False
    assert payload["read_only"] is True
    assert payload["message"].startswith("not applied;")
    assert "disk full" in payload["message"]


def _stale_orchestrated_pending_for_plan_window(scheduling_mod):
    plan_day = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    window_start = f"{plan_day.isoformat()}T09:00:00Z"
    window_end = f"{plan_day.isoformat()}T12:00:00Z"
    stale = _pending_kick_for_section(
        scheduling_mod,
        "research",
        reason="orchestrated",
        kick_at=window_start,
        work_start=window_start,
        work_end=window_end,
    )
    scheduling_mod.save_pending_kicks({stale.account_key: stale})
    return plan_day, stale


def test_plan_apply_json_reports_removed_stale_orchestrated(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    plan_day, stale = _stale_orchestrated_pending_for_plan_window(scheduling_mod)

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "09:00-12:00",
            "--date",
            plan_day.isoformat(),
            "--timezone",
            "UTC",
            "--apply",
            "--yes",
            "--json-output",
        ],
    )

    payload = _status_json_payload(result.output)
    assert payload["applied"] is True
    removals = payload["diff"]["removes_orchestrated"]
    assert len(removals) == 1
    assert removals[0]["reason"] == "stale_orchestrated_not_in_plan"
    assert removals[0]["existing"]["account_label"] == "research"
    assert "removed 1 stale orchestrated pending kick" in payload["message"]
    assert stale.account_key not in scheduling_mod.load_pending_kicks(
        datetime.now(timezone.utc)
    )


def test_plan_preview_shows_stale_orchestrated_removals_without_mutating(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    plan_day, stale = _stale_orchestrated_pending_for_plan_window(scheduling_mod)

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "09:00-12:00",
            "--date",
            plan_day.isoformat(),
            "--timezone",
            "UTC",
        ],
    )

    assert result.exit_code == 0
    assert "Applying removes 1 stale orchestrated pending kick" in result.output
    assert "research" in result.output
    assert stale.account_key in scheduling_mod.load_pending_kicks(
        datetime.now(timezone.utc)
    )


def test_plan_apply_human_output_mentions_removed_stale_orchestrated(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    plan_day, stale = _stale_orchestrated_pending_for_plan_window(scheduling_mod)

    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "--work-window",
            "09:00-12:00",
            "--date",
            plan_day.isoformat(),
            "--timezone",
            "UTC",
            "--apply",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "Removed 1 stale orchestrated pending kick" in result.output
    assert "research" in result.output
    assert stale.account_key not in scheduling_mod.load_pending_kicks(
        datetime.now(timezone.utc)
    )


def test_plan_cancel_still_removes_orchestrated_pending_kicks(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    _plan_day, stale = _stale_orchestrated_pending_for_plan_window(scheduling_mod)

    result = CliRunner().invoke(cli, ["plan", "cancel", "--yes"])

    assert result.exit_code == 0
    assert "cancelled 1 orchestration pending kick(s)" in result.output
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def _stale_orchestration_plan_fixture():
    built_now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex",
        auto_kick=True,
        session_auto_kick=True,
    )
    plan = _build_direct_orchestration_plan(
        [account],
        [_fresh_orchestration_status(account, built_now)],
        now=built_now,
        work_start=datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc),
        work_end=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
    )
    assert plan.planned_kicks
    return plan, built_now


def test_plan_apply_json_refuses_stale_planned_kick(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    plan, built_now = _stale_orchestration_plan_fixture()
    # Fresh plan age, stale kick times: exercises the kick-overdue refusal.
    plan.built_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, built_now),
    )

    result = CliRunner().invoke(
        cli,
        ["plan", "--work-window", "09:00-12:00", "--apply", "--yes", "--json-output"],
    )

    payload = _status_json_payload(result.output)
    assert payload["applied"] is False
    assert payload["read_only"] is True
    assert payload["message"].startswith("not applied; plan is stale, rebuild the plan")
    assert "has already passed" in payload["message"]
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def test_plan_apply_human_output_refuses_stale_plan(monkeypatch):
    import tokenkick.scheduling as scheduling_mod

    plan, built_now = _stale_orchestration_plan_fixture()
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, built_now),
    )

    result = CliRunner().invoke(
        cli,
        ["plan", "--work-window", "09:00-12:00", "--apply", "--yes"],
    )

    assert result.exit_code == 0
    assert "not applied; plan is stale, rebuild the plan" in result.output
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def test_interactive_orchestration_apply_refuses_stale_plan(monkeypatch):
    import tokenkick.interactive as interactive
    import tokenkick.scheduling as scheduling_mod

    plan, built_now = _stale_orchestration_plan_fixture()
    selections = iter(
        ["schedule", "orchestration", "today", "default", "exit", "exit", "exit"]
    )
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(interactive, "pick_work_window", lambda **_kwargs: "09:00-12:00")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(interactive, "_print_banner", lambda: None)
    monkeypatch.setattr(
        "tokenkick.interactive.Config.load",
        lambda: Config(schedule=ScheduleConfig(timezone="UTC")),
    )
    monkeypatch.setattr(
        "tokenkick.cli._build_plan_from_options",
        lambda **_kwargs: (plan, built_now),
    )

    result = CliRunner().invoke(cli, ["menu"])

    assert result.exit_code == 0
    assert "not applied; plan is stale, rebuild the plan" in result.output
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def _isolate_schedule_command_state(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending-kicks.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)
    saved: list[Config] = []
    config = Config()
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: config)
    monkeypatch.setattr("tokenkick.cli.Config.save", lambda self: saved.append(copy.deepcopy(self)))
    return scheduling_mod, saved


def test_schedule_set_default_preserves_orchestrated_pending_kicks(monkeypatch, tmp_path):
    scheduling_mod, saved = _isolate_schedule_command_state(monkeypatch, tmp_path)
    smart = _pending_kick_for_section(scheduling_mod, "personal")
    orchestrated = _pending_kick_for_section(scheduling_mod, "research", reason="orchestrated")
    scheduling_mod.save_pending_kicks(
        {smart.account_key: smart, orchestrated.account_key: orchestrated}
    )

    result = CliRunner().invoke(
        cli,
        ["schedule", "set", "--default", "--weekdays", "09:00-17:00"],
    )

    assert result.exit_code == 0
    remaining = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert list(remaining) == [orchestrated.account_key]
    assert "Removed 1 smart-schedule pending kick: personal" in result.output
    assert "Kept 1 orchestrated pending kick for research" in result.output
    assert "tk plan cancel" in result.output
    assert saved[0].schedule.default.weekdays == "09:00-17:00"


def test_schedule_set_account_scopes_invalidation_and_preserves_orchestrated(
    monkeypatch,
    tmp_path,
):
    scheduling_mod, _saved = _isolate_schedule_command_state(monkeypatch, tmp_path)
    smart_personal = _pending_kick_for_section(scheduling_mod, "personal")
    orchestrated_personal = _pending_kick_for_section(
        scheduling_mod,
        "personal",
        reason="orchestrated",
        account_key="manual|codex|personal-orchestrated",
    )
    smart_other = _pending_kick_for_section(scheduling_mod, "research")
    scheduling_mod.save_pending_kicks(
        {
            smart_personal.account_key: smart_personal,
            orchestrated_personal.account_key: orchestrated_personal,
            smart_other.account_key: smart_other,
        }
    )

    result = CliRunner().invoke(
        cli,
        ["schedule", "set", "--account", "personal", "--weekdays", "14:00-21:00"],
    )

    assert result.exit_code == 0
    remaining = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert sorted(remaining) == [
        orchestrated_personal.account_key,
        smart_other.account_key,
    ]
    assert "Removed 1 smart-schedule pending kick: personal" in result.output
    assert "Kept 1 orchestrated pending kick for personal" in result.output


def test_schedule_clear_preserves_orchestrated_and_reports_removed_smart(
    monkeypatch,
    tmp_path,
):
    scheduling_mod, _saved = _isolate_schedule_command_state(monkeypatch, tmp_path)
    smart = _pending_kick_for_section(scheduling_mod, "personal")
    orchestrated = _pending_kick_for_section(
        scheduling_mod,
        "personal",
        reason="orchestrated",
        account_key="manual|codex|personal-orchestrated",
    )
    scheduling_mod.save_pending_kicks(
        {smart.account_key: smart, orchestrated.account_key: orchestrated}
    )

    result = CliRunner().invoke(cli, ["schedule", "clear", "--account", "personal"])

    assert result.exit_code == 0
    remaining = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert list(remaining) == [orchestrated.account_key]
    assert "Removed 1 smart-schedule pending kick: personal" in result.output
    assert "Kept 1 orchestrated pending kick for personal" in result.output


def test_schedule_disable_preserves_orchestrated_without_removal_note(
    monkeypatch,
    tmp_path,
):
    scheduling_mod, _saved = _isolate_schedule_command_state(monkeypatch, tmp_path)
    orchestrated = _pending_kick_for_section(scheduling_mod, "research", reason="orchestrated")
    scheduling_mod.save_pending_kicks({orchestrated.account_key: orchestrated})

    result = CliRunner().invoke(cli, ["schedule", "disable", "--default"])

    assert result.exit_code == 0
    remaining = scheduling_mod.load_pending_kicks(datetime.now(timezone.utc))
    assert list(remaining) == [orchestrated.account_key]
    assert "Removed" not in result.output
    assert "Kept 1 orchestrated pending kick for research" in result.output


def test_invalidate_pending_kicks_without_exclusion_still_removes_orchestrated(
    monkeypatch,
    tmp_path,
):
    scheduling_mod, _saved = _isolate_schedule_command_state(monkeypatch, tmp_path)
    orchestrated = _pending_kick_for_section(scheduling_mod, "research", reason="orchestrated")
    scheduling_mod.save_pending_kicks({orchestrated.account_key: orchestrated})

    removed = scheduling_mod.invalidate_pending_kicks(account_label="research")

    assert [item.account_key for item in removed] == [orchestrated.account_key]
    assert scheduling_mod.load_pending_kicks(datetime.now(timezone.utc)) == {}


def test_smart_schedule_persists_pending_without_kicking(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    kicked = []
    notified = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.kick_account", lambda account, **_kwargs: kicked.append(account))
    monkeypatch.setattr(
        "tokenkick.cli.notify_schedule_decision",
        lambda label, decision, notifications: notified.append((label, decision.kick_at)),
    )

    _kick_all_enabled_accounts(config.accounts or [account], config, targets=[(account, status)], deferred=[])
    _kick_all_enabled_accounts(config.accounts or [account], config, targets=[(account, status)], deferred=[])

    pending = scheduling_mod.load_pending_kicks(datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc))
    assert kicked == []
    assert len(pending) == 1
    assert next(iter(pending.values())).notified is True
    assert notified == [("personal", "2026-05-22T09:00:00Z")]


def test_smart_schedule_retries_configured_ntfy_schedule_notification_failure(
    monkeypatch,
    tmp_path,
    capsys,
):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    config = Config(
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        ),
    )
    notified = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.kick_account", lambda account, **_kwargs: pytest.fail("should schedule"))
    monkeypatch.setattr(
        "tokenkick.cli.notify_schedule_decision",
        lambda label, decision, notifications: notified.append((label, decision.kick_at)) or False,
    )

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )
    first_output = capsys.readouterr().out
    pending = scheduling_mod.load_pending_kicks(datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc))
    assert next(iter(pending.values())).notified is False

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )
    second_output = capsys.readouterr().out

    assert notified == [
        ("personal", "2026-05-22T09:00:00Z"),
        ("personal", "2026-05-22T09:00:00Z"),
    ]
    assert 'context="schedule_decision"' in first_output
    assert 'reason="delivery_failed"' in first_output
    assert 'context="schedule_decision"' in second_output
    assert 'reason="delivery_failed"' in second_output


def test_smart_schedule_skips_long_window_without_session_and_kicks_immediately(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    schedule_notifications = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)
    monkeypatch.setattr(
        "tokenkick.cli.notify_schedule_decision",
        lambda label, decision, notifications: schedule_notifications.append(label),
    )

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert "[schedule_skipped]" in output
    assert 'reason="no_suitable_window"' in output
    assert "primary_window_minutes=10080" in output
    assert [event.label for event in recorded] == ["personal"]
    assert scheduling_mod.load_pending_kicks() == {}
    assert schedule_notifications == []


def test_smart_schedule_uses_codex_session_window_for_long_primary(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.notify_schedule_decision", lambda *_args: None)

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )
    first_output = capsys.readouterr().out
    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )
    second_output = capsys.readouterr().out
    config.schedule.accounts["personal"] = WorkSchedule(enabled=True, weekdays="15:00-22:00")
    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )
    changed_output = capsys.readouterr().out

    pending = scheduling_mod.load_pending_kicks(datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc))
    assert len(pending) == 1
    pending_kick = next(iter(pending.values()))
    assert pending_kick.window_basis == "session"
    assert pending_kick.kick_at == "2026-05-22T10:00:00Z"
    assert "[schedule_session_window]" in first_output
    assert "session_window_minutes=300" in first_output
    assert "[schedule_session_window]" not in second_output
    assert "[schedule_session_window]" in changed_output


def test_session_auto_kick_with_schedule_kicks_inside_work_window(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", session_auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.ACTIVE,
        used_percent=1.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_scheduled_kick", lambda *_args: None)

    _kick_all_enabled_accounts([account], config, targets=[(account, status)], deferred=[])

    assert [event.kind for event in recorded] == ["session"]
    assert scheduling_mod.load_pending_kicks() == {}


def test_session_schedule_pending_executes_when_due(monkeypatch, tmp_path, capsys):
    import tokenkick.models as models_mod
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    history_file = tmp_path / "history.jsonl"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)

    class FixedDateTime(datetime):
        current = datetime(2026, 5, 22, 15, 31, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    status = AccountStatus(
        label="claude",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=0,
        session_window_minutes=300,
    )
    config = Config(
        accounts=[account],
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"claude": WorkSchedule(enabled=True, weekdays="19:00-23:00")},
        ),
    )
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.fetch_status", lambda _account: status)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.notify_schedule_decision", lambda *_args: None)
    monkeypatch.setattr("tokenkick.cli.notify_scheduled_kick", lambda *_args: None)

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    pending = scheduling_mod.load_pending_kicks(FixedDateTime.current)
    assert pending_file.exists()
    assert list(pending) == ["manual|claude|claude"]
    pending_kick = pending["manual|claude|claude"]
    assert pending_kick.window_basis == "session"
    assert pending_kick.kick_at == "2026-05-22T17:00:00Z"

    FixedDateTime.current = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
    executed = _execute_due_pending_kicks(
        [account],
        config,
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": status},
    )

    output = capsys.readouterr().out
    history = [json.loads(line) for line in history_file.read_text().splitlines()]
    assert executed == 1
    assert scheduling_mod.load_pending_kicks(FixedDateTime.current) == {}
    assert history[0]["label"] == "claude"
    assert "[scheduled_kick_executed]" in output
    assert "[scheduled_kick_confirmed]" in output


def test_due_pending_codex_kicks_share_batch_stagger(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    accounts = [
        AccountConfig(label="codex-a", provider="codex", auto_kick=True),
        AccountConfig(label="codex-b", provider="codex", auto_kick=True),
    ]
    statuses_by_key = {
        account_key_string(account): AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=0.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        )
        for account in accounts
    }
    pending = {}
    for account in accounts:
        pending[account_key_string(account)] = PendingKick(
            account_key=account_key_string(account),
            account_label=account.label,
            provider=account.provider,
            kick_at="2026-05-22T17:00:00Z",
            created_at="2026-05-22T15:31:00Z",
            reason="single_window",
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start="2026-05-22T17:00:00Z",
            work_end="2026-05-22T21:00:00Z",
            window_basis="session",
        )
    scheduling_mod.save_pending_kicks(pending)

    current_time = [100.0]
    sleeps = []
    kicked = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.time.monotonic", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_scheduled_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )

    executed = _execute_due_pending_kicks(
        accounts,
        Config(accounts=accounts),
        statuses_by_key=statuses_by_key,
        stagger_state=KickStaggerState(),
    )

    assert executed == 2
    assert kicked == ["codex-a", "codex-b"]
    assert sleeps == [CODEX_KICK_STAGGER_SECONDS]
    assert scheduling_mod.load_pending_kicks(FixedDateTime.now(timezone.utc)) == {}


def test_same_provider_home_main_and_spark_due_kicks_are_serialized(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    provider_home = str(tmp_path / "codex-home")
    accounts = [
        AccountConfig(
            label="codex-main",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home=provider_home,
            auto_kick=True,
            session_auto_kick=True,
        ),
        AccountConfig(
            label="codex-spark",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home=provider_home,
            codex_rate_limit_id="codex_bengalfox",
            auto_kick=True,
            session_auto_kick=True,
            usable_session_minutes=30,
        ),
    ]
    statuses_by_key = {
        account_key_string(account): AccountStatus(
            label=account.label,
            state=AccountState.FRESH,
            used_percent=0.0,
            resets_in_seconds=604800,
            window_minutes=10080,
            session_used_percent=0.0,
            session_resets_in_seconds=0,
            session_window_minutes=300,
        )
        for account in accounts
    }
    scheduling_mod.save_pending_kicks(
        {
            account_key_string(account): PendingKick(
                account_key=account_key_string(account),
                account_label=account.label,
                provider=account.provider,
                kick_at="2026-05-22T17:00:00Z",
                created_at="2026-05-22T15:31:00Z",
                reason="orchestrated",
                windows_needed=1,
                expected_waste_minutes=0,
                waste_location="none",
                work_start="2026-05-22T17:00:00Z",
                work_end="2026-05-22T21:00:00Z",
                window_basis="session",
            )
            for account in accounts
        }
    )

    current_time = [100.0]
    sleeps = []
    kicked = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.time.monotonic", lambda: current_time[0])
    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda _event: None)
    monkeypatch.setattr("tokenkick.cli.notify_scheduled_kick", lambda *_args: None)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: kicked.append(account.label)
        or KickEvent(label=account.label, success=True),
    )

    executed = _execute_due_pending_kicks(
        accounts,
        Config(accounts=accounts),
        statuses_by_key=statuses_by_key,
        stagger_state=KickStaggerState(),
    )

    assert executed == 2
    assert kicked == ["codex-main", "codex-spark"]
    assert sleeps == [CODEX_KICK_STAGGER_SECONDS]


def test_due_pending_kick_survives_transient_unknown_status(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 3, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    unknown_status = AccountStatus(
        label="claude",
        state=AccountState.UNKNOWN,
        error="codexbar timed out after 20s",
    )

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("due kick should wait instead of kicking"),
    )

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": unknown_status},
    )

    output = capsys.readouterr().out
    assert executed == 0
    assert scheduling_mod.load_pending_kicks(FixedDateTime.now(timezone.utc)) == {
        "manual|claude|claude": pending
    }
    assert "[scheduled_kick_waiting]" in output
    assert pending_file.read_text().strip() != "{}"


def test_due_pending_kick_skips_stale_status_without_deleting_pending(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 3, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    stale_status = AccountStatus(
        label="claude",
        state=AccountState.FRESH,
        stale=True,
        stale_seconds=1_800,
        source_detail="CodexBar history",
    )

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("stale due kick should not execute"),
    )

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": stale_status},
    )

    output = capsys.readouterr().out
    assert executed == 0
    assert scheduling_mod.load_pending_kicks(FixedDateTime.now(timezone.utc)) == {
        "manual|claude|claude": pending
    }
    assert "[scheduled_kick_waiting]" in output
    assert 'reason="stale_status"' in output
    assert pending_file.read_text().strip() != "{}"


def test_due_session_pending_waits_inside_reset_boundary_grace(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    active_status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        session_used_percent=99.0,
        session_resets_in_seconds=60,
        session_window_minutes=300,
    )

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("boundary grace should wait instead of kicking"),
    )

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": active_status},
    )

    output = capsys.readouterr().out
    assert executed == 0
    assert scheduling_mod.load_pending_kicks(FixedDateTime.now(timezone.utc)) == {
        "manual|claude|claude": pending
    }
    assert "[scheduled_kick_waiting]" in output
    assert 'reason="session_boundary_grace"' in output
    assert "session_resets_in=60" in output
    assert pending_file.read_text().strip() != "{}"


def test_due_pending_kick_failure_is_retained_with_retry_metadata(monkeypatch, tmp_path, capsys):
    import tokenkick.models as models_mod
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", tmp_path / "history.jsonl")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 17, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    status = AccountStatus(label="claude", state=AccountState.FRESH, used_percent=0.0)

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: KickEvent(label="claude", success=False, error="cli failed"),
    )
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": status},
    )

    retained = scheduling_mod.load_pending_kicks(FixedDateTime.now(timezone.utc))[
        "manual|claude|claude"
    ]
    output = capsys.readouterr().out
    assert executed == 1
    assert retained.attempt_count == 1
    assert retained.last_error == "cli failed"
    assert retained.last_attempt_at == "2026-05-22T17:00:00Z"
    assert retained.next_retry_at == "2026-05-22T17:05:00Z"
    assert retained.gave_up_at is None
    assert "[scheduled_kick_failed]" in output
    assert 'next_retry_at="2026-05-22T17:05:00Z"' in output


def test_due_pending_kick_failure_backoff_blocks_until_retry(monkeypatch, tmp_path):
    import tokenkick.models as models_mod
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", tmp_path / "history.jsonl")

    class FixedDateTime(datetime):
        current = datetime(2026, 5, 22, 17, 3, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
        attempt_count=1,
        last_attempt_at="2026-05-22T17:00:00Z",
        last_error="cli failed",
        next_retry_at="2026-05-22T17:05:00Z",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    status = AccountStatus(label="claude", state=AccountState.FRESH, used_percent=0.0)
    calls = []

    def fail_kick(*_args, **_kwargs):
        calls.append(FixedDateTime.current)
        return KickEvent(label="claude", success=False, error="still failing")

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.kick_account", fail_kick)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)

    early = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        statuses_by_key={"manual|claude|claude": status},
    )
    FixedDateTime.current = datetime(2026, 5, 22, 17, 5, tzinfo=timezone.utc)
    retry = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        statuses_by_key={"manual|claude|claude": status},
    )

    retained = scheduling_mod.load_pending_kicks(FixedDateTime.current)["manual|claude|claude"]
    assert early == 0
    assert retry == 1
    assert len(calls) == 1
    assert retained.attempt_count == 2
    assert retained.next_retry_at == "2026-05-22T17:20:00Z"


def test_due_pending_kick_gives_up_after_max_attempts(monkeypatch, tmp_path, capsys):
    import tokenkick.models as models_mod
    import tokenkick.scheduling as scheduling_mod

    pending_file = tmp_path / "pending-kicks.json"
    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(models_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models_mod, "HISTORY_FILE", tmp_path / "history.jsonl")

    class FixedDateTime(datetime):
        current = datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    account = AccountConfig(label="claude", provider="claude", auto_kick=True)
    pending = scheduling_mod.PendingKick(
        account_key="manual|claude|claude",
        account_label="claude",
        provider="claude",
        kick_at="2026-05-22T17:00:00Z",
        created_at="2026-05-22T15:31:00Z",
        reason="single_window",
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start="2026-05-22T17:00:00Z",
        work_end="2026-05-22T21:00:00Z",
        window_basis="session",
        attempt_count=3,
        last_attempt_at="2026-05-22T17:15:00Z",
        last_error="still failing",
        next_retry_at="2026-05-22T18:00:00Z",
    )
    scheduling_mod.save_pending_kicks({"manual|claude|claude": pending})
    status = AccountStatus(label="claude", state=AccountState.FRESH, used_percent=0.0)
    calls = []

    def fail_kick(*_args, **_kwargs):
        calls.append(FixedDateTime.current)
        return KickEvent(label="claude", success=False, error="permanent failure")

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr("tokenkick.cli.kick_account", fail_kick)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda *_args: None)

    executed = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": status},
    )
    FixedDateTime.current = datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc)
    after_give_up = _execute_due_pending_kicks(
        [account],
        Config(accounts=[account]),
        daemon_log=True,
        statuses_by_key={"manual|claude|claude": status},
    )

    retained = scheduling_mod.load_pending_kicks(FixedDateTime.current)["manual|claude|claude"]
    output = capsys.readouterr().out
    assert executed == 1
    assert after_give_up == 0
    assert len(calls) == 1
    assert retained.attempt_count == 4
    assert retained.next_retry_at is None
    assert retained.gave_up_at == "2026-05-22T18:00:00Z"
    assert "[scheduled_kick_gave_up]" in output


def test_smart_schedule_skips_1440_minute_boundary(monkeypatch, tmp_path, capsys):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="daily", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="daily",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=1440,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"daily": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    assert "[schedule_skipped]" in capsys.readouterr().out
    assert [event.label for event in recorded] == ["daily"]
    assert scheduling_mod.load_pending_kicks() == {}


def test_smart_schedule_allows_1439_minute_boundary(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="short", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="short",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=1439,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"short": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    pending = scheduling_mod.load_pending_kicks(datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc))
    assert recorded == []
    assert len(pending) == 1
    assert next(iter(pending.values())).account_label == "short"


def test_schedule_fallback_logs_and_kicks_when_no_work_window_today(monkeypatch, capsys):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert "[schedule_fallback]" in output
    assert 'reason="no_work_window_today"' in output
    assert recorded[0].label == "personal"


def test_kick_all_force_bypasses_smart_schedule(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)
    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=300,
    )
    config = Config(
        schedule=ScheduleConfig(
            enabled=True,
            timezone="Europe/Berlin",
            accounts={"personal": WorkSchedule(enabled=True, weekdays="14:00-21:00")},
        )
    )
    recorded = []
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        force=True,
    )

    assert [event.label for event in recorded] == ["personal"]
    assert scheduling_mod.load_pending_kicks() == {}


def test_kick_all_force_bypasses_future_pending_suppression(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    status = AccountStatus(
        label="personal",
        state=AccountState.FRESH,
        used_percent=0.0,
        resets_in_seconds=604800,
        window_minutes=10080,
    )
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now + timedelta(hours=1)),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now + timedelta(hours=1)),
        work_end=to_utc_iso(now + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    scheduling_mod.save_pending_kicks({account_key_string(account): pending})
    recorded = []
    monkeypatch.setattr("tokenkick.cli._fetch_status", lambda _account, _config=None: status)
    monkeypatch.setattr("tokenkick.cli.load_kick_history", lambda limit=200: [])
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: recorded.append(event))
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)

    _kick_all_enabled_accounts(
        [account],
        Config(accounts=[account]),
        force=True,
        suppress_pending=False,
    )

    assert [event.label for event in recorded] == ["personal"]
    assert scheduling_mod.load_pending_kicks() == {}


def test_manual_kick_decline_future_pending_override_does_not_mutate(monkeypatch, tmp_path):
    import tokenkick.scheduling as scheduling_mod

    monkeypatch.setattr(scheduling_mod, "PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(scheduling_mod, "CONFIG_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    account = AccountConfig(label="personal", provider="codex", auto_kick=True)
    pending = PendingKick(
        account_key=account_key_string(account),
        account_label=account.label,
        provider=account.provider,
        kick_at=to_utc_iso(now + timedelta(hours=1)),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(now + timedelta(hours=1)),
        work_end=to_utc_iso(now + timedelta(hours=3)),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    scheduling_mod.save_pending_kicks({account_key_string(account): pending})
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account]))
    monkeypatch.setattr("tokenkick.cli._confirm_prompt", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda *_args, **_kwargs: pytest.fail("declined planned-kick override should not fetch status"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda *_args, **_kwargs: pytest.fail("declined planned-kick override should not kick"),
    )

    result = CliRunner().invoke(cli, ["kick", "personal"])

    assert result.exit_code == 0
    assert "Kick cancelled." in result.output
    assert scheduling_mod.load_pending_kicks() == {account_key_string(account): pending}


def test_daemon_reloads_poll_interval_each_loop(monkeypatch):
    account = AccountConfig(label="personal", provider="codex")
    status = AccountStatus(label="personal", state=AccountState.WAITING)
    configs = iter(
        [
            Config(accounts=[account], poll_interval_minutes=5),
            Config(accounts=[account], poll_interval_minutes=1),
        ]
    )
    sleeps = []

    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: next(configs))
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._refresh_status_cache", lambda *_args, **_kwargs: ([account], [status]))
    monkeypatch.setattr("tokenkick.cli._execute_due_pending_kicks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("tokenkick.cli._kickable_window_targets", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr("tokenkick.cli._kick_all_enabled_accounts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._daemon_log", lambda *_args, **_kwargs: None)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert sleeps == [60]


def test_daemon_supervises_enabled_telegram_remote(monkeypatch):
    import tokenkick.cli as cli_module

    config = Config(
        telegram_remote_enabled=True,
        notifications=NotifyConfig(
            telegram_bot_token="token",
            telegram_chat_id="chat-id",
        ),
    )
    starts: list[bool] = []
    logs: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        cli_module,
        "_telegram_remote_status_payload",
        lambda _config=None: {"running": False},
    )
    monkeypatch.setattr(
        cli_module,
        "_start_telegram_remote_background",
        lambda *, quiet=False: starts.append(quiet) or 4242,
    )
    monkeypatch.setattr(cli_module, "_daemon_log", lambda event, **fields: logs.append((event, fields)))

    cli_module._supervise_telegram_remote(config)

    assert starts == [True]
    assert logs == [("telegram_remote_supervise_started", {"pid": 4242})]


def test_daemon_supervision_skips_when_telegram_remote_disabled(monkeypatch):
    import tokenkick.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_start_telegram_remote_background",
        lambda *, quiet=False: pytest.fail("disabled Telegram remote must not start"),
    )

    cli_module._supervise_telegram_remote(Config(telegram_remote_enabled=False))


def test_daemon_poll_invokes_telegram_remote_supervision(monkeypatch):
    import tokenkick.cli as cli_module

    account = AccountConfig(label="personal", provider="codex")
    calls: list[bool] = []

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[account], telegram_remote_enabled=True),
    )
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr(
        "tokenkick.cli._supervise_telegram_remote",
        lambda config: calls.append(config.telegram_remote_enabled),
    )
    monkeypatch.setattr(
        "tokenkick.cli._refresh_status_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop after supervision")),
    )

    with pytest.raises(RuntimeError, match="stop after supervision"):
        cli_module._daemon_poll_once({}, False)

    assert calls == [True]


def test_daemon_poll_error_logs_and_continues(monkeypatch):
    account = AccountConfig(label="personal", provider="codex")
    status = AccountStatus(label="personal", state=AccountState.WAITING)
    events: list[tuple[str, dict]] = []
    sleeps: list[float] = []
    refresh_calls = {"count": 0}

    def fake_refresh(*_args, **_kwargs):
        refresh_calls["count"] += 1
        if refresh_calls["count"] == 1:
            raise RuntimeError("status refresh exploded")
        return [account], [status]

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[account], poll_interval_minutes=1),
    )
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._refresh_status_cache", fake_refresh)
    monkeypatch.setattr("tokenkick.cli._execute_due_pending_kicks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("tokenkick.cli._kickable_window_targets", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr("tokenkick.cli._kick_all_enabled_accounts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "tokenkick.cli._daemon_log",
        lambda event, **fields: events.append((event, fields)),
    )

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli.time.sleep", fake_sleep)

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    poll_errors = [fields for event, fields in events if event == "poll_error"]
    assert len(poll_errors) == 1
    assert poll_errors[0]["error"] == "RuntimeError: status refresh exploded"
    assert poll_errors[0]["retry_in_seconds"] == 60
    assert sleeps == [60, 60]
    assert refresh_calls["count"] == 2
    assert any(event == "poll" for event, _fields in events)
    assert events[-1][0] == "daemon_stop"


def test_daemon_poll_error_guard_does_not_swallow_keyboard_interrupt(monkeypatch):
    account = AccountConfig(label="personal", provider="codex")
    events: list[tuple[str, dict]] = []
    sleeps: list[float] = []

    def fake_refresh(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: Config(accounts=[account], poll_interval_minutes=1),
    )
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._refresh_status_cache", fake_refresh)
    monkeypatch.setattr(
        "tokenkick.cli._daemon_log",
        lambda event, **fields: events.append((event, fields)),
    )
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert sleeps == []
    assert all(event != "poll_error" for event, _fields in events)
    assert events[-1][0] == "daemon_stop"


def test_daemon_start_reaps_dead_refresh_lock(monkeypatch, tmp_path):
    account = AccountConfig(label="personal", provider="codex")
    status = AccountStatus(label="personal", state=AccountState.WAITING)
    lock_file = tmp_path / "status-cache-refresh.pid"
    lock_file.write_text("4242")

    monkeypatch.setattr("tokenkick.cli.STATUS_CACHE_REFRESH_LOCK_FILE", lock_file)
    monkeypatch.setattr("tokenkick.status_cache._pid_is_running", lambda pid: False)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr("tokenkick.cli._migrate_codex_home_keys_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._repair_codex_home_identity_drift_if_needed", lambda config, **_kwargs: config)
    monkeypatch.setattr("tokenkick.cli._refresh_status_cache", lambda *_args, **_kwargs: ([account], [status]))
    monkeypatch.setattr("tokenkick.cli._execute_due_pending_kicks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("tokenkick.cli._kickable_window_targets", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr("tokenkick.cli._kick_all_enabled_accounts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("tokenkick.cli._daemon_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "tokenkick.cli.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert not lock_file.exists()


def test_daemon_foreground_writes_and_removes_owned_pidfile(monkeypatch, tmp_path):
    account = AccountConfig(label="personal", provider="codex")
    pid_file = tmp_path / "daemon.pid"
    observed_pidfile = []

    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.os.getpid", lambda: 5555)
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr("tokenkick.cli._daemon_log", lambda *_args, **_kwargs: None)

    def fake_poll(*_args, **_kwargs):
        observed_pidfile.append(pid_file.read_text())
        raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli._daemon_poll_once", fake_poll)

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert len(observed_pidfile) == 1
    assert observed_pidfile[0].startswith("5555 0.5.2\nexecutable=")
    assert not pid_file.exists()


def test_daemon_foreground_refuses_second_live_daemon(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.os.getpid", lambda: 5555)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: pid == 4242)
    monkeypatch.setattr(
        "tokenkick.cli.Config.load",
        lambda: pytest.fail("duplicate foreground daemon must stop before loading config"),
    )

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 1
    assert "already running" in result.output
    assert "pid 4242" in result.output
    assert pid_file.read_text() == "4242\n"


def test_daemon_foreground_recovers_stale_pidfile(monkeypatch, tmp_path):
    account = AccountConfig(label="personal", provider="codex")
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    observed_pidfile = []

    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.os.getpid", lambda: 5555)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr("tokenkick.cli._daemon_log", lambda *_args, **_kwargs: None)

    def fake_poll(*_args, **_kwargs):
        observed_pidfile.append(pid_file.read_text())
        raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli._daemon_poll_once", fake_poll)

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert len(observed_pidfile) == 1
    assert observed_pidfile[0].startswith("5555 0.5.2\nexecutable=")
    assert not pid_file.exists()


def test_daemon_foreground_cleanup_does_not_remove_replaced_pidfile(monkeypatch, tmp_path):
    account = AccountConfig(label="personal", provider="codex")
    pid_file = tmp_path / "daemon.pid"

    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.os.getpid", lambda: 5555)
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(accounts=[account], poll_interval_minutes=1))
    monkeypatch.setattr("tokenkick.cli._daemon_log", lambda *_args, **_kwargs: None)

    def fake_poll(*_args, **_kwargs):
        pid_file.write_text("7777 0.5.2\n")
        raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli._daemon_poll_once", fake_poll)

    result = CliRunner().invoke(cli, ["daemon"])

    assert result.exit_code == 0
    assert pid_file.read_text() == "7777 0.5.2\n"


def test_daemon_background_starts_process_and_writes_pid(monkeypatch, tmp_path):
    calls = []

    class FakeProcess:
        pid = 4242

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", log_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tokenkick.cli.sys.argv", ["/tmp/tk"])
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["daemon", "--background"])

    assert result.exit_code == 0
    assert "started in background" in result.output
    assert pid_file.read_text().startswith("4242 0.5.2\nexecutable=")
    assert calls[0][0][0] == ["/tmp/tk", "daemon"]
    assert calls[0][1]["start_new_session"] is True


def test_daemon_background_does_not_start_duplicate(monkeypatch, tmp_path):
    calls = []
    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", log_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", lambda *args, **kwargs: calls.append(args))

    result = CliRunner().invoke(cli, ["daemon", "--background"])

    assert result.exit_code == 0
    assert "already running" in result.output
    assert calls == []


def test_daemon_stop_kills_pid_and_removes_pidfile(monkeypatch, tmp_path):
    calls = []
    running = True
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)

    def fake_kill(pid, sig):
        nonlocal running
        calls.append((pid, sig))
        if sig == signal.SIGTERM:
            running = False

    monkeypatch.setattr("tokenkick.cli.os.kill", fake_kill)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: running)

    result = CliRunner().invoke(cli, ["daemon", "--stop"])

    assert result.exit_code == 0
    assert "stopped" in result.output
    assert calls == [(4242, signal.SIGTERM)]
    assert not pid_file.exists()


def test_daemon_stop_keeps_pidfile_when_process_does_not_exit(monkeypatch, tmp_path):
    calls = []
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_STOP_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.os.kill", lambda pid, sig: calls.append((pid, sig)))

    result = CliRunner().invoke(cli, ["daemon", "--stop"])

    assert result.exit_code == 0
    assert "did not stop within 0s" in result.output
    assert "pidfile kept" in result.output
    assert calls == [(4242, signal.SIGTERM)]
    assert pid_file.exists()


def test_daemon_stop_handles_missing_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)

    result = CliRunner().invoke(cli, ["daemon", "--stop"])

    assert result.exit_code == 0
    assert "not running" in result.output


def test_daemon_stop_removes_stale_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)

    result = CliRunner().invoke(cli, ["daemon", "--stop"])

    assert result.exit_code == 0
    assert "stale pid 4242" in result.output
    assert not pid_file.exists()


def test_daemon_status_shows_running_pid_uptime_and_poll_interval(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    os.utime(pid_file, (1000, 1000))
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.time.time", lambda: 4720)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(poll_interval_minutes=5))

    result = CliRunner().invoke(cli, ["daemon", "--status"])

    assert result.exit_code == 0
    assert "daemon running" in result.output
    assert "pid 4242" in result.output
    assert "uptime 1h 2m" in result.output
    assert "poll interval 5m" in result.output
    assert "Daemon status printed at" in result.output


def test_daemon_status_detects_foreground_daemon_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("5555 0.5.2\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: pid == 5555)
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config(poll_interval_minutes=1))

    result = CliRunner().invoke(cli, ["daemon", "--status"])

    assert result.exit_code == 0
    assert "daemon running" in result.output
    assert "pid 5555" in result.output
    assert "poll interval 1m" in result.output


def test_daemon_status_reports_stale_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)

    result = CliRunner().invoke(cli, ["daemon", "--status"])

    assert result.exit_code == 0
    assert "not running" in result.output
    assert "stale pidfile" in result.output
    assert pid_file.exists()
    assert "Daemon status printed at" in result.output


def test_daemon_status_reports_missing_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)

    result = CliRunner().invoke(cli, ["daemon", "--status"])

    assert result.exit_code == 0
    assert "daemon not running" in result.output
    assert "Daemon status printed at" in result.output


def test_daemon_restart_stops_running_and_starts_new_background(monkeypatch, tmp_path):
    killed = []
    calls = []
    running = True

    class FakeProcess:
        pid = 7777

    def fake_kill(pid, sig):
        nonlocal running
        killed.append((pid, sig))
        if sig == signal.SIGTERM:
            running = False

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", log_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.os.kill", fake_kill)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: running)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tokenkick.cli.sys.argv", ["/tmp/tk"])
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["daemon", "--restart"])

    assert result.exit_code == 0
    assert "daemon restarted" in result.output
    assert "pid 7777" in result.output
    assert killed == [(4242, signal.SIGTERM)]
    assert pid_file.read_text().startswith("7777 0.5.2\nexecutable=")
    assert calls[0][0][0] == ["/tmp/tk", "daemon"]


def test_daemon_restart_aborts_when_existing_daemon_does_not_stop(monkeypatch, tmp_path):
    calls = []
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_STOP_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.os.kill", lambda *_args: None)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = CliRunner().invoke(cli, ["daemon", "--restart"])

    assert result.exit_code == 0
    assert "restart aborted" in result.output
    assert calls == []
    assert pid_file.read_text() == "4242\n"


def test_daemon_restart_starts_when_not_running(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 7777

    pid_file = tmp_path / "daemon.pid"
    log_file = tmp_path / "daemon.log"
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.DAEMON_LOG_FILE", log_file)
    monkeypatch.setattr("tokenkick.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr("tokenkick.cli.sys.argv", ["/tmp/tk"])
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["daemon", "--restart"])

    assert result.exit_code == 0
    assert "daemon started" in result.output
    assert "pid 7777" in result.output
    assert pid_file.read_text().startswith("7777 0.5.2\nexecutable=")


def test_daemon_rejects_multiple_modes():
    result = CliRunner().invoke(cli, ["daemon", "--background", "--stop"])

    assert result.exit_code == 0
    assert "Use only one daemon mode" in result.output


def test_root_version_option_reports_installed_version(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "tk, version 0.5.2"


def test_update_reports_installed_version_when_no_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert "installed version: v0.5.2" in result.output
    assert "daemon not running" in result.output


def test_update_reports_daemon_up_to_date(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.2\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert "daemon up to date (v0.5.2)" in result.output


def test_update_restarts_on_confirmed_mismatch(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli._restart_daemon_for_update", lambda: restarted.append(True) or True)

    result = CliRunner().invoke(cli, ["update"], input="y\n")

    assert result.exit_code == 0
    assert "version mismatch" in result.output
    assert restarted == [True]


def test_update_decline_mismatch_exits_one(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "tokenkick.cli._restart_daemon_for_update",
        lambda: pytest.fail("declined update must not restart"),
    )

    result = CliRunner().invoke(cli, ["update"], input="n\n")

    assert result.exit_code == 1
    assert "Background process restart declined" in result.output


def test_update_check_never_restarts_and_exits_for_mismatch(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "tokenkick.cli._restart_daemon_for_update",
        lambda: pytest.fail("--check must not restart"),
    )

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert result.exit_code == 1
    assert "version mismatch" in result.output


def test_update_check_exits_zero_for_match(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.2\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert result.exit_code == 0


def test_update_json_output_shape(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)

    result = CliRunner().invoke(cli, ["update", "--json-output"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": 1,
        "installed_version": "0.5.2",
        "daemon_version": "0.5.0",
        "daemon_running": True,
        "daemon_match": False,
        "daemon_pid": 4242,
        "telegram_remote_version": None,
        "telegram_remote_running": False,
        "telegram_remote_match": True,
        "telegram_remote_pid": None,
        "match": False,
    }


def test_update_old_pidfile_version_unknown_is_mismatch(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli._restart_daemon_for_update", lambda: True)

    result = CliRunner().invoke(cli, ["update"], input="y\n")

    assert result.exit_code == 0
    assert "running vunknown, installed v0.5.2" in result.output


def test_update_stale_pidfile_defers_to_daemon_restart(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr(
        "tokenkick.cli._restart_daemon_for_update",
        lambda: pytest.fail("stale pidfile must not restart through tk update"),
    )

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 1
    assert "pidfile is stale" in result.output
    assert "tk daemon --restart" in result.output


def test_update_yes_repairs_stale_daemon_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr(
        "tokenkick.cli._start_daemon_background",
        lambda *, quiet=False: restarted.append(quiet) or 5555,
    )

    result = CliRunner().invoke(cli, ["update", "--yes"])

    assert result.exit_code == 0
    assert "Daemon pidfile is stale; restarting daemon" in result.output
    assert restarted == [True]
    assert not pid_file.exists()


def test_update_invalid_pidfile_defers_to_daemon_restart(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("not-a-pid\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 1
    assert "pidfile is stale" in result.output
    assert "tk daemon --restart" in result.output


def test_update_json_stale_pidfile_reports_mismatch(monkeypatch, tmp_path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)

    result = CliRunner().invoke(cli, ["update", "--json-output"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": 1,
        "installed_version": "0.5.2",
        "daemon_version": "0.5.0",
        "daemon_running": False,
        "daemon_match": False,
        "daemon_pid": 4242,
        "telegram_remote_version": None,
        "telegram_remote_running": False,
        "telegram_remote_match": True,
        "telegram_remote_pid": None,
        "match": False,
    }


def test_update_reports_and_restarts_telegram_remote_mismatch(monkeypatch, tmp_path):
    daemon_pid_file = tmp_path / "daemon.pid"
    remote_pid_file = tmp_path / "telegram-remote.pid"
    remote_pid_file.write_text("4444 0.5.0\n")
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", daemon_pid_file)
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", remote_pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: pid == 4444)
    monkeypatch.setattr("tokenkick.cli._restart_telegram_remote_for_update", lambda: restarted.append(True) or True)
    monkeypatch.setattr(
        "tokenkick.cli._restart_daemon_for_update",
        lambda: pytest.fail("daemon is not stale and must not restart"),
    )

    result = CliRunner().invoke(cli, ["update", "--yes"])

    assert result.exit_code == 0
    assert "Telegram remote version mismatch" in result.output
    assert restarted == [True]


def test_update_yes_repairs_stale_telegram_remote_pidfile(monkeypatch, tmp_path):
    remote_pid_file = tmp_path / "telegram-remote.pid"
    remote_pid_file.write_text("4444 0.5.0\n")
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", remote_pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr(
        "tokenkick.cli._start_telegram_remote_background",
        lambda *, quiet=False: restarted.append(quiet) or 5555,
    )

    result = CliRunner().invoke(cli, ["update", "--yes"])

    assert result.exit_code == 0
    assert "Telegram remote pidfile is stale; restarting Telegram remote" in result.output
    assert restarted == [True]
    assert not remote_pid_file.exists()


def test_update_yes_restores_telegram_remote_from_upgrade_state(monkeypatch, tmp_path):
    state_file = tmp_path / "upgrade-background-processes.json"
    state_file.write_text('{"daemon": false, "telegram_remote": true}\n')
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", tmp_path / "telegram-remote.pid")
    monkeypatch.setattr("tokenkick.cli.UPGRADE_BACKGROUND_STATE_FILE", state_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr(
        "tokenkick.cli._start_telegram_remote_background",
        lambda *, quiet=False: restarted.append(quiet) or 5555,
    )

    result = CliRunner().invoke(cli, ["update", "--yes"])

    assert result.exit_code == 0
    assert "Telegram remote restored" in result.output
    assert restarted == [True]
    assert not state_file.exists()


def test_update_prompts_to_restore_telegram_remote_from_upgrade_state(monkeypatch, tmp_path):
    state_file = tmp_path / "upgrade-background-processes.json"
    state_file.write_text('{"daemon": false, "telegram_remote": true}\n')
    restarted = []
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", tmp_path / "telegram-remote.pid")
    monkeypatch.setattr("tokenkick.cli.UPGRADE_BACKGROUND_STATE_FILE", state_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)
    monkeypatch.setattr(
        "tokenkick.cli._start_telegram_remote_background",
        lambda *, quiet=False: restarted.append(quiet) or 5555,
    )

    result = CliRunner().invoke(cli, ["update"], input="y\n")

    assert result.exit_code == 0
    assert "Restart background processes that were running before upgrade?" in result.output
    assert "Telegram remote restored" in result.output
    assert restarted == [True]
    assert not state_file.exists()


def test_update_check_fails_when_upgrade_state_needs_restore(monkeypatch, tmp_path):
    state_file = tmp_path / "upgrade-background-processes.json"
    state_file.write_text('{"daemon": false, "telegram_remote": true}\n')
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", tmp_path / "telegram-remote.pid")
    monkeypatch.setattr("tokenkick.cli.UPGRADE_BACKGROUND_STATE_FILE", state_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: False)

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert result.exit_code == 1
    assert "were running before upgrade" in result.output
    assert state_file.exists()


def test_update_json_reports_telegram_remote_mismatch(monkeypatch, tmp_path):
    remote_pid_file = tmp_path / "telegram-remote.pid"
    remote_pid_file.write_text("4444 0.5.0\n")
    monkeypatch.setattr("tokenkick.cli.DAEMON_PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr("tokenkick.cli.TELEGRAM_REMOTE_PID_FILE", remote_pid_file)
    monkeypatch.setattr("tokenkick.cli.installed_version", lambda: "0.5.2")
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: pid == 4444)

    result = CliRunner().invoke(cli, ["update", "--json-output"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": 1,
        "installed_version": "0.5.2",
        "daemon_version": None,
        "daemon_running": False,
        "daemon_match": True,
        "daemon_pid": None,
        "telegram_remote_version": "0.5.0",
        "telegram_remote_running": True,
        "telegram_remote_match": False,
        "telegram_remote_pid": 4444,
        "match": False,
    }


def test_format_log_line_uses_utc_iso_and_key_values(monkeypatch):
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            from datetime import datetime

            return datetime(2026, 5, 22, 6, 40, 52, tzinfo=tz)

    monkeypatch.setattr("tokenkick.cli.datetime", FixedDateTime)

    line = _format_log_line(
        "poll",
        auto_kick_accounts=5,
        fresh_targets=0,
        account="work solar",
        confirmed=True,
    )

    assert line == (
        '2026-05-22T06:40:52Z [poll] auto_kick_accounts=5 '
        'fresh_targets=0 account="work solar" confirmed=true'
    )


def test_kick_all_daemon_log_uses_structured_events(monkeypatch, capsys):
    accounts = [AccountConfig(label="fresh", provider="codex")]
    status = AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0)
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda account, **_kwargs: KickEvent(label=account.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: None)
    monkeypatch.setattr("tokenkick.cli._utc_log_timestamp", lambda: "2026-05-22T06:40:52Z")

    _kick_all_enabled_accounts(
        accounts,
        Config(),
        targets=[(accounts[0], status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert '2026-05-22T06:40:52Z [kick_start] account="fresh"' in output
    assert '2026-05-22T06:40:52Z [kick_confirmed] account="fresh"' in output


def test_kick_all_daemon_log_records_notification_delivery(monkeypatch, capsys):
    account = AccountConfig(label="fresh", provider="codex")
    status = AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0)
    config = Config(
        accounts=[account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: True)
    monkeypatch.setattr("tokenkick.cli._utc_log_timestamp", lambda: "2026-05-22T06:40:52Z")

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert (
        '2026-05-22T06:40:52Z [notification_sent] account="fresh" '
        'backend="ntfy" context="kick"'
    ) in output


def test_kick_all_daemon_log_records_notification_failure(monkeypatch, capsys):
    account = AccountConfig(label="fresh", provider="codex")
    status = AccountStatus(label="fresh", state=AccountState.FRESH, used_percent=0.0)
    config = Config(
        accounts=[account],
        notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="topic"),
    )
    monkeypatch.setattr(
        "tokenkick.cli.kick_account",
        lambda candidate, **_kwargs: KickEvent(label=candidate.label, success=True),
    )
    monkeypatch.setattr("tokenkick.cli.append_kick_event", lambda event: None)
    monkeypatch.setattr("tokenkick.cli.notify_kick", lambda event, notifications: False)
    monkeypatch.setattr("tokenkick.cli._utc_log_timestamp", lambda: "2026-05-22T06:40:52Z")

    _kick_all_enabled_accounts(
        [account],
        config,
        targets=[(account, status)],
        deferred=[],
        daemon_log=True,
    )

    output = capsys.readouterr().out
    assert (
        '2026-05-22T06:40:52Z [notification_failed] account="fresh" '
        'backend="ntfy" context="kick" reason="delivery_failed"'
    ) in output


def test_init_invokes_setup_with_deprecation_message(monkeypatch):
    monkeypatch.setattr("tokenkick.cli.Config.load", lambda: Config())
    monkeypatch.setattr(
        "tokenkick.cli._discover_accounts_and_statuses",
        lambda: (
            [],
            [],
            "No CodexBar CLI or Codex session files found.",
        ),
    )

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0
    assert "tk init is deprecated; use tk setup." in result.output
