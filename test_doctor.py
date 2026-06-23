"""Tests for tk doctor diagnostics."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from click.testing import CliRunner

from tokenkick import doctor as doctor_mod
from tokenkick import models, scheduling
from tokenkick.cli import cli
from tokenkick.direct import DirectIdentity
from tokenkick.doctor import DoctorCheck, build_doctor_report
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeConfig,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    Config,
    DataSource,
    KickEvent,
    NotifyConfig,
    ScheduleConfig,
    WorkSchedule,
    account_key_string,
)
from tokenkick.reset_defense import ResetEvent, append_reset_event


def _isolate_doctor(monkeypatch, tmp_path, config: Config | None = None):
    config_file = tmp_path / "config.json"
    cache_file = tmp_path / "status-cache.json"
    daemon_pid = tmp_path / "daemon.pid"
    refresh_lock = tmp_path / "status-cache-refresh.pid"
    pending_file = tmp_path / "pending-kicks.json"
    reset_events_file = tmp_path / "reset-events.jsonl"
    monkeypatch.setattr(models, "CONFIG_FILE", config_file)
    monkeypatch.setattr(models, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(scheduling, "PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr(doctor_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(doctor_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(doctor_mod, "STATUS_CACHE_FILE", cache_file)
    monkeypatch.setattr(doctor_mod, "DAEMON_PID_FILE", daemon_pid)
    monkeypatch.setattr(doctor_mod, "STATUS_CACHE_REFRESH_LOCK_FILE", refresh_lock)
    monkeypatch.setattr(doctor_mod, "CLAUDE_HOME", tmp_path)
    monkeypatch.setattr(doctor_mod, "CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr(doctor_mod, "CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.reset_defense.RESET_EVENTS_FILE", reset_events_file)
    if config is not None:
        monkeypatch.setattr(doctor_mod.Config, "load", classmethod(lambda cls: config))
    return SimpleNamespace(
        config_file=config_file,
        cache_file=cache_file,
        daemon_pid=daemon_pid,
        refresh_lock=refresh_lock,
        pending_file=pending_file,
        reset_events_file=reset_events_file,
    )


def test_doctor_reports_codex_fire_all_surface_mode(monkeypatch, tmp_path):
    _isolate_doctor(
        monkeypatch,
        tmp_path,
        Config(
            codex_fire_all_surfaces=True,
            codex_fire_all_surface_order=["repo", "legacy"],
        ),
    )

    report = build_doctor_report()

    payload = report.to_dict()
    assert payload["config"]["codex_fire_all_surfaces"] is True
    assert payload["config"]["codex_fire_all_surfaces_active"] is True
    assert payload["config"]["codex_fire_all_surface_order_active"] == ["repo", "legacy"]
    assert any(
        check["code"] == "codex_surface_dispatch_mode"
        and "fire-all enabled" in check["message"]
        and "repo, legacy" in check["message"]
        for check in payload["checks"]
    )


def test_doctor_reports_invalid_codex_fire_all_surface_order_env(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config(codex_fire_all_surfaces=True))
    monkeypatch.setenv("TK_CODEX_FIRE_ALL_SURFACE_ORDER", "repo,repo")

    report = build_doctor_report()

    payload = report.to_dict()
    assert payload["config"]["codex_fire_all_surface_order_error"] is not None
    assert any(
        check["level"] == "FAIL"
        and check["code"] == "codex_surface_dispatch_mode"
        and "Duplicate Codex fire-all surface" in check["message"]
        for check in payload["checks"]
    )


def _write_cache(path, accounts: list[AccountConfig], statuses: list[AccountStatus], *, refresh_error: str | None = None):
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    account_key_string(account): {
                        "account": account.to_dict(),
                        "status": status.to_dict(),
                        "cached_at": "2026-05-23T18:00:00Z",
                        "refresh_error": refresh_error,
                    }
                    for account, status in zip(accounts, statuses, strict=False)
                },
            }
        )
    )


def test_doctor_zero_accounts_clean_report(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config())

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "0 accounts" in result.output
    assert "Run `tk setup`" in result.output
    assert "Doctor run at" in result.output


def test_doctor_summary_counts_aggregate_global_and_account(monkeypatch, tmp_path):
    account = AccountConfig(label="codex", provider="codex")
    paths = _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    _write_cache(paths.cache_file, [account], [AccountStatus(label="codex", state=AccountState.ACTIVE)])

    report = build_doctor_report()

    assert report.summary.accounts == 1
    assert report.summary.ok >= 1
    assert report.summary.warn >= 1
    assert report.summary.fail == 0


def test_doctor_json_shape_summary_first(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config())

    result = CliRunner().invoke(cli, ["doctor", "--json-output"])

    assert result.exit_code == 0
    assert result.output.lstrip().startswith('{\n  "summary"')
    assert "Doctor run at" not in result.output
    data = json.loads(result.output)
    assert list(data.keys()) == [
        "summary",
        "config",
        "cache",
        "daemon",
        "schedule",
        "notifications",
        "accounts",
        "checks",
    ]
    assert all(set(check) == {"level", "code", "message", "fix"} for check in data["checks"])


def test_doctor_label_scopes_only_accounts(monkeypatch, tmp_path):
    one = AccountConfig(label="one", provider="codex")
    two = AccountConfig(label="two", provider="gemini")
    paths = _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[one, two]))
    _write_cache(
        paths.cache_file,
        [one, two],
        [
            AccountStatus(label="one", state=AccountState.ACTIVE),
            AccountStatus(label="two", state=AccountState.ACTIVE),
        ],
    )

    result = CliRunner().invoke(cli, ["doctor", "two", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["config"]["accounts"] == 2
    assert [account["label"] for account in data["accounts"]] == ["two"]


def test_doctor_gemini_reports_monitor_only(monkeypatch, tmp_path):
    account = AccountConfig(label="gemini", provider="gemini")
    paths = _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    _write_cache(paths.cache_file, [account], [AccountStatus(label="gemini", state=AccountState.ACTIVE)])

    result = CliRunner().invoke(cli, ["doctor", "gemini", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    checks = data["accounts"][0]["checks"]
    assert any(
        check["level"] == "INFO"
        and check["code"] == "gemini_monitor_only"
        and "daily RPD reset at midnight PT" in check["message"]
        for check in checks
    )
    assert "cache" in data and "daemon" in data and "notifications" in data


def test_doctor_unknown_label_exits_two(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[AccountConfig(label="one")]))

    result = CliRunner().invoke(cli, ["doctor", "missing"])

    assert result.exit_code == 2
    assert 'Account "missing" not found.' in result.output


def test_doctor_daemon_states(monkeypatch, tmp_path):
    paths = _isolate_doctor(monkeypatch, tmp_path, Config())
    monkeypatch.setattr(doctor_mod, "installed_version", lambda: "0.5.2")
    no_daemon = build_doctor_report()
    assert no_daemon.daemon["status"] == "not_running"

    paths.daemon_pid.write_text("12345 0.5.2")
    monkeypatch.setattr(doctor_mod.os, "kill", lambda _pid, _sig: None)
    running = build_doctor_report()
    assert running.daemon["status"] == "running"
    assert any(check.code == "daemon_running" for check in running.checks)

    def dead(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(doctor_mod.os, "kill", dead)
    stale = build_doctor_report()
    assert stale.daemon["status"] == "stale"
    assert any(check.level == "FAIL" and check.fix == "run `tk daemon --restart`" for check in stale.checks)


def test_doctor_daemon_version_mismatch_warns(monkeypatch, tmp_path):
    paths = _isolate_doctor(monkeypatch, tmp_path, Config())
    paths.daemon_pid.write_text("12345 0.5.0")
    monkeypatch.setattr(doctor_mod, "installed_version", lambda: "0.5.2")
    monkeypatch.setattr(doctor_mod.os, "kill", lambda _pid, _sig: None)

    report = build_doctor_report()

    assert report.daemon["daemon_version"] == "0.5.0"
    assert any(
        check.level == "WARN"
        and check.code == "daemon_version_mismatch"
        and check.fix == "run `tk update` to restart with the new version"
        for check in report.checks
    )


def test_doctor_refresh_lock_running_and_stale(monkeypatch, tmp_path):
    paths = _isolate_doctor(monkeypatch, tmp_path, Config())
    paths.refresh_lock.write_text("123")
    monkeypatch.setattr(doctor_mod.time, "time", lambda: 1_000)
    os.utime(paths.refresh_lock, (950, 950))
    monkeypatch.setattr(doctor_mod.os, "kill", lambda _pid, _sig: None)

    running = build_doctor_report()
    assert running.cache["refresh_lock"]["status"] == "running"

    os.utime(paths.refresh_lock, (1, 1))
    monkeypatch.setattr(doctor_mod.os, "kill", lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()))
    stale = build_doctor_report()
    assert stale.cache["refresh_lock"]["status"] == "not_running"
    assert not paths.refresh_lock.exists()
    assert any(check.code == "refresh_lock_reaped" for check in stale.checks)


def test_doctor_repair_option_reaps_dead_refresh_lock(monkeypatch, tmp_path):
    paths = _isolate_doctor(monkeypatch, tmp_path, Config())
    paths.refresh_lock.write_text("4242")
    monkeypatch.setattr(doctor_mod.os, "kill", lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()))

    result = CliRunner().invoke(cli, ["doctor", "--repair"])

    assert result.exit_code == 0
    assert "Removed stale refresh lock for dead PID 4242." in result.output
    assert not paths.refresh_lock.exists()


def test_doctor_notifications_masked_and_no_network(monkeypatch, tmp_path):
    config = Config(
        notifications=NotifyConfig(
            enabled=True,
            backend="telegram",
            telegram_bot_token="secret-token",
            telegram_chat_id="123456789",
        )
    )
    _isolate_doctor(monkeypatch, tmp_path, config)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "***6789" in result.output
    assert "secret-token" not in result.output


def test_doctor_schedule_pending_and_stale_warning(monkeypatch, tmp_path):
    account = AccountConfig(label="codex", provider="codex")
    paths = _isolate_doctor(
        monkeypatch,
        tmp_path,
        Config(
            accounts=[account],
            schedule=ScheduleConfig(enabled=True, accounts={"codex": WorkSchedule(enabled=True)}),
        ),
    )
    _write_cache(
        paths.cache_file,
        [account],
        [AccountStatus(label="codex", state=AccountState.FRESH, stale=True)],
    )
    paths.pending_file.write_text(
        json.dumps(
            {
                account_key_string(account): {
                    "account_key": account_key_string(account),
                    "account_label": "codex",
                    "provider": "codex",
                    "kick_at": "2099-05-23T18:00:00Z",
                    "created_at": "2099-05-23T17:00:00Z",
                    "reason": "single_window",
                    "windows_needed": 1,
                    "expected_waste_minutes": 0,
                    "waste_location": "none",
                    "work_start": "2099-05-23T18:00:00Z",
                    "work_end": "2099-05-23T22:00:00Z",
                    "window_basis": "session",
                }
            }
        )
    )

    report = build_doctor_report()

    assert report.schedule["pending_kicks"] == 1
    assert report.schedule["pending"][0]["window_basis"] == "session"
    assert any(check.code == "schedule_pending_blocked_stale" for check in report.checks)


def test_doctor_antigravity_metadata_no_http_and_masks_csrf(monkeypatch, tmp_path):
    account = AccountConfig(label="ag", provider="antigravity")
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    token = "token-should-not-render"

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["ps", "-ax", "-o"]:
            return SimpleNamespace(
                stdout=(
                    f"123 /Applications/Antigravity.app/Contents/Resources/bin/language_server "
                    f"--app_data_dir antigravity --csrf_token {token}\n"
                )
            )
        if "-p" in cmd:
            return SimpleNamespace(stdout="language 123 reserve TCP 127.0.0.1:51487 (LISTEN)\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(doctor_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "tokenkick.sources._antigravity_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP probe should not run")),
    )

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "language server found (language_server)" in result.output
    assert "CSRF flag present" in result.output
    assert "listening ports observed" in result.output
    assert token not in result.output


def test_doctor_antigravity_absent(monkeypatch, tmp_path):
    account = AccountConfig(label="ag", provider="antigravity")
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(doctor_mod.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "Antigravity app process not running" in result.output
    assert "language server not running" in result.output


def test_doctor_codex_identity_mismatch_fix_hint(monkeypatch, tmp_path):
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(home),
        session_path=str(sessions),
        identity_provider_id="expected",
        identity_email="expected@example.test",
    )
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(
        doctor_mod,
        "read_codex_identity",
        lambda _home: DirectIdentity("codex", provider_account_id="actual", email="actual@example.test"),
    )

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "configured home identity does not match saved account" in result.output
    assert doctor_mod.CODEX_IDENTITY_MISMATCH_FIX in result.output


def test_doctor_warns_on_repeated_codex_no_evidence_attempts(monkeypatch, tmp_path):
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(home),
        session_path=str(sessions),
        identity_provider_id="acct",
        identity_email="codex@example.test",
    )
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(
        doctor_mod,
        "read_codex_identity",
        lambda _home: DirectIdentity(
            "codex",
            provider_account_id="acct",
            email="codex@example.test",
        ),
    )
    now = 10_000.0
    monkeypatch.setattr(doctor_mod.time, "time", lambda: now)
    monkeypatch.setattr(
        doctor_mod,
        "load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=now - 60,
                success=True,
                confirmed=False,
                error=doctor_mod.CODEX_NO_GENERATION_EVIDENCE_ERROR,
                codex_surface="repo-skip",
            ),
            KickEvent(
                label="codex",
                timestamp=now - 30,
                success=True,
                confirmed=False,
                error=doctor_mod.CODEX_NO_GENERATION_EVIDENCE_ERROR,
                codex_surface="repo",
            ),
        ],
    )

    report = build_doctor_report()

    checks = [check for account_report in report.accounts for check in account_report.checks]
    warning = next(check for check in checks if check.code == "codex_repeated_no_evidence_kicks")
    assert warning.level == "WARN"
    assert "2 recent Codex kicks" in warning.message
    assert 'tk codex-surfaces "codex"' in warning.fix
    assert "tk status --refresh --codex" in warning.fix
    assert "codex-surface-test" not in warning.fix


def test_doctor_unconfirmed_codex_cluster_recommends_late_attribution_first(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(home),
        session_path=str(sessions),
        identity_provider_id="acct",
        identity_email="codex@example.test",
    )
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(
        doctor_mod,
        "read_codex_identity",
        lambda _home: DirectIdentity(
            "codex",
            provider_account_id="acct",
            email="codex@example.test",
        ),
    )
    now = 10_000.0
    monkeypatch.setattr(doctor_mod.time, "time", lambda: now)
    monkeypatch.setattr(
        doctor_mod,
        "load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=now - 900,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="repo-skip",
                codex_cluster_id="cluster",
            ),
            KickEvent(
                label="codex",
                timestamp=now - 600,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="legacy",
                codex_cluster_id="cluster",
            ),
            KickEvent(
                label="codex",
                timestamp=now - 300,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="repo",
                codex_cluster_id="cluster",
            ),
        ],
    )

    report = build_doctor_report()

    checks = [check for account_report in report.accounts for check in account_report.checks]
    warning = next(check for check in checks if check.code == "codex_unconfirmed_generation_cluster")
    assert warning.level == "WARN"
    assert "late attribution" in warning.message
    assert "`tk status --refresh --codex`" in warning.fix
    assert 'tk codex-surfaces "codex"' in warning.fix
    assert "codex-surface-test" not in warning.fix


def test_doctor_reports_ambiguous_codex_late_attribution_without_unconfirmed_cluster(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(home),
        session_path=str(sessions),
        identity_provider_id="acct",
        identity_email="codex@example.test",
    )
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(
        doctor_mod,
        "read_codex_identity",
        lambda _home: DirectIdentity(
            "codex",
            provider_account_id="acct",
            email="codex@example.test",
        ),
    )
    now = 10_000.0
    monkeypatch.setattr(doctor_mod.time, "time", lambda: now)
    monkeypatch.setattr(
        doctor_mod,
        "load_kick_history",
        lambda limit=200: [
            KickEvent(
                label="codex",
                timestamp=now - 900,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="repo-skip",
                codex_cluster_id="cluster",
            ),
            KickEvent(
                label="codex",
                timestamp=now - 600,
                success=True,
                confirmed=True,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="legacy",
                codex_cluster_id="cluster",
                codex_confirmation_method="late_reset_clock",
                codex_attribution="timing_match",
                codex_anchor_match_delta_seconds=120.6,
            ),
            KickEvent(
                label="codex",
                timestamp=now - 300,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="repo",
                codex_cluster_id="cluster",
            ),
            KickEvent(
                label="codex",
                timestamp=now - 60,
                success=True,
                confirmed=False,
                kind="session",
                kick_type="session",
                response_text="TokenKick anchor probe completed.",
                codex_surface="interactive-like",
                codex_cluster_id="cluster",
            ),
        ],
    )

    report = build_doctor_report()

    checks = [check for account_report in report.accounts for check in account_report.checks]
    assert all(check.code != "codex_unconfirmed_generation_cluster" for check in checks)
    warning = next(check for check in checks if check.code == "codex_late_attribution_ambiguous")
    assert warning.level == "WARN"
    assert "Timing match" in warning.message
    assert "causality ambiguous" in warning.message


def test_doctor_claude_direct_probe_time_and_error(monkeypatch, tmp_path):
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    paths = _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    paths.cache_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": {
                    account_key_string(account): {
                        "account": account.to_dict(),
                        "status": AccountStatus(
                            label="claude",
                            state=AccountState.ACTIVE,
                            source_detail="claude-codexbar-fallback",
                        ).to_dict(),
                        "cached_at": "2026-05-23T18:00:00Z",
                        "refresh_error": None,
                        "last_direct_probe_at": "2026-05-23T17:50:00Z",
                        "last_direct_probe_error": ClaudeProbeError(
                            ClaudeProbeErrorCategory.TIMEOUT,
                            "timeout",
                        ).to_dict(),
                    }
                },
            }
        )
    )
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(doctor_mod, "read_claude_identity", lambda _path: DirectIdentity("claude"))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "last direct /usage probe failed (timeout" in result.output
    assert "run `tk setup`" in result.output
    assert "retry with `tk status --refresh`" in result.output


def test_doctor_reports_missing_claude_setup_files(monkeypatch, tmp_path):
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(doctor_mod, "read_claude_identity", lambda _path: DirectIdentity("claude"))

    result = CliRunner().invoke(cli, ["doctor", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    checks = data["accounts"][0]["checks"]
    by_code = {check["code"]: check for check in checks}
    assert by_code["claude_probe_git_missing"]["fix"] == "run `tk setup` to fix"
    assert by_code["claude_settings_missing"]["fix"] == "run `tk setup` to fix"


def test_doctor_reports_present_claude_setup_files(monkeypatch, tmp_path):
    account = AccountConfig(label="claude", provider="claude", source=DataSource.CLAUDE_DIRECT)
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    (tmp_path / "claude-probe" / ".git").mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"theme":"dark"}\n')
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(doctor_mod, "read_claude_identity", lambda _path: DirectIdentity("claude"))

    result = CliRunner().invoke(cli, ["doctor", "--json-output"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    checks = data["accounts"][0]["checks"]
    codes = {check["code"] for check in checks}
    assert "claude_probe_git_present" in codes
    assert "claude_settings_present" in codes


def test_doctor_claude_direct_opt_out_states(monkeypatch, tmp_path):
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        direct_usage_enabled=False,
    )
    _isolate_doctor(
        monkeypatch,
        tmp_path,
        Config(accounts=[account], claude=ClaudeConfig(direct_usage_enabled=False)),
    )
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(doctor_mod, "read_claude_identity", lambda _path: DirectIdentity("claude"))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "direct /usage disabled at account level" in result.output
    assert "direct /usage disabled globally" in result.output


def test_doctor_mentions_recent_reset_events_and_correlation_limits(monkeypatch, tmp_path):
    account = AccountConfig(label="solo", provider="claude", source=DataSource.CLAUDE_DIRECT)
    _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    detected_at = datetime.now(timezone.utc).replace(microsecond=0)
    event = ResetEvent(
        id="reset-1",
        detected_at=detected_at.isoformat().replace("+00:00", "Z"),
        provider="codex",
        confidence="likely",
        affected_accounts=["secondary", "reserve"],
        trigger="usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=24,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=["secondary"],
        notification_sent=True,
        summary="summary",
        detail="detail",
    )
    observation = ResetEvent(
        id="observation-1",
        detected_at=(detected_at + timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
        provider="claude",
        confidence="possible",
        affected_accounts=["solo"],
        trigger="single_account_usage_drop",
        account_snapshots=[],
        total_quota_hours_lost=None,
        previous_reset_predictions={},
        new_reset_predictions={},
        pending_kicks_invalidated=[],
        notification_sent=False,
        summary="summary",
        detail="detail",
    )
    assert append_reset_event(event)
    assert append_reset_event(observation)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "global reset likely on codex" in result.output
    assert "provider reset observation on claude" in result.output
    assert "global reset correlation needs 2+ visible accounts" in result.output


def test_doctor_check_fail_requires_fix():
    try:
        DoctorCheck("FAIL", "bad", "missing fix")
    except ValueError as exc:
        assert "requires a fix hint" in str(exc)
    else:
        raise AssertionError("FAIL without fix should raise")


def test_doctor_does_not_refresh_or_start_background(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config())
    monkeypatch.setattr(
        "tokenkick.cli.fetch_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fetch_status should not run")),
    )
    monkeypatch.setattr(
        "tokenkick.cli._start_background_status_refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("background refresh should not start")),
    )

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0


def test_doctor_has_no_reserved_refresh_stub(monkeypatch, tmp_path):
    _isolate_doctor(monkeypatch, tmp_path, Config())

    result = CliRunner().invoke(cli, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "--refresh" not in result.output
    assert "--repair" in result.output
    assert "not implemented" not in result.output


def test_doctor_codex_refresh_error_shows_recovery_kick_hint(monkeypatch, tmp_path):
    provider_home = tmp_path / "codex home"
    account = AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(provider_home),
        identity_email="work@example.com",
    )
    (provider_home / "sessions").mkdir(parents=True)
    paths = _isolate_doctor(monkeypatch, tmp_path, Config(accounts=[account]))
    monkeypatch.setattr(
        doctor_mod,
        "read_codex_identity",
        lambda _home: DirectIdentity(provider="codex", email="work@example.com"),
    )
    _write_cache(
        paths.cache_file,
        [account],
        [
            AccountStatus(
                label=account.label,
                state=AccountState.FRESH,
                used_percent=0.0,
                session_used_percent=100.0,
                session_resets_in_seconds=0,
            )
        ],
        refresh_error="ProviderError",
    )

    report = build_doctor_report("codex (work)")
    refresh_check = next(
        check
        for account_report in report.accounts
        for check in account_report.checks
        if check.code == "account_refresh_error"
    )

    assert "last refresh failed (ProviderError); last provider read" in refresh_check.message
    assert refresh_check.fix is not None
    assert "CODEX_HOME=" in refresh_check.fix
    assert "Codex opens but refresh still fails" in refresh_check.fix
    assert "tk kick 'codex (work)' --force" in refresh_check.fix
    assert "tk status --refresh --account 'codex (work)' --verbose" in refresh_check.fix


def test_doctor_report_to_dict_shape():
    check = DoctorCheck("OK", "ok", "fine")
    report = doctor_mod.DoctorReport(
        summary=doctor_mod.DoctorSummary(ok=1, warn=0, fail=0, accounts=0, cache_status="current"),
        config={},
        cache={},
        daemon={},
        schedule={},
        notifications={},
        accounts=[],
        checks=[check],
    )

    assert list(report.to_dict().keys())[0] == "summary"
