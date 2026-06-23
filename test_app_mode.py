"""Tests for TK_APP_MODE and the tk app JSON contract."""

import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from tokenkick import app_commands
from tokenkick.app_mode import (
    APP_SCHEMA_VERSION,
    app_envelope,
    app_mode_enabled,
)
from tokenkick.cli import (
    _confirm_prompt,
    _should_open_interactive_menu,
    cli,
)
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    NotifyConfig,
    WorkSchedule,
)

ENVELOPE_KEYS = {"schema_version", "ok", "error_code", "message", "warnings", "payload"}


@pytest.fixture(autouse=True)
def isolate_state(monkeypatch, tmp_path):
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
        "tokenkick.cli.CODEX_PENDING_CONFIRMATIONS_FILE",
        config_dir / "codex-pending-confirmations.json",
    )
    monkeypatch.setattr(
        "tokenkick.cli._codex_surface_stats_file",
        lambda: config_dir / "codex-surface-stats.json",
    )
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
    return config_dir


def _codex_account(label="codex (dev)", **kwargs):
    return AccountConfig(
        label=label,
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home="/tmp/codex-home",
        **kwargs,
    )


def _seed_config(accounts):
    config = Config(accounts=accounts)
    config.save()
    return config


def _parse_envelope(output: str) -> dict:
    data = json.loads(output)
    assert set(data) == ENVELOPE_KEYS
    assert data["schema_version"] == APP_SCHEMA_VERSION
    return data


# ---------------------------------------------------------------------------
# app_mode module
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
    ],
)
def test_app_mode_enabled_parses_env(monkeypatch, value, expected):
    monkeypatch.setenv("TK_APP_MODE", value)
    assert app_mode_enabled() is expected


def test_app_mode_disabled_without_env(monkeypatch):
    monkeypatch.delenv("TK_APP_MODE", raising=False)
    assert app_mode_enabled() is False


def test_app_envelope_shape():
    envelope = app_envelope(ok=False, error_code="x", message="m", warnings=["w"], payload={"a": 1})
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["ok"] is False
    assert envelope["error_code"] == "x"
    assert envelope["warnings"] == ["w"]
    assert envelope["payload"] == {"a": 1}


def test_app_mode_disables_interactive_menu(monkeypatch):
    monkeypatch.setenv("TK_APP_MODE", "1")
    assert _should_open_interactive_menu() is False


def test_app_mode_confirm_prompt_returns_default(monkeypatch):
    monkeypatch.setenv("TK_APP_MODE", "1")
    assert _confirm_prompt("Proceed?", default=False) is False
    assert _confirm_prompt("Proceed?", default=True) is True


# ---------------------------------------------------------------------------
# JSON-safe errors under TK_APP_MODE
# ---------------------------------------------------------------------------

def test_app_mode_unknown_command_emits_json_error(monkeypatch):
    monkeypatch.setenv("TK_APP_MODE", "1")
    runner = CliRunner()
    result = runner.invoke(cli, ["frobnicate"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_app_mode_internal_error_emits_json_error(monkeypatch):
    monkeypatch.setenv("TK_APP_MODE", "1")
    monkeypatch.setattr(
        "tokenkick.cli._daemon_status_payload",
        lambda config=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "internal_error"
    assert "boom" in envelope["message"]
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# tk daemon --json-output
# ---------------------------------------------------------------------------

def test_daemon_status_json_not_running():
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    daemon = envelope["payload"]["daemon"]
    assert daemon["running"] is False
    assert daemon["pidfile_exists"] is False
    assert daemon["stale_pidfile"] is False
    assert result.exit_code == 0


def test_daemon_status_json_stale_pidfile(isolate_state):
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "daemon.pid").write_text("999999999 9.9.9\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    daemon = envelope["payload"]["daemon"]
    assert daemon["running"] is False
    assert daemon["pidfile_exists"] is True
    assert daemon["stale_pidfile"] is True


def test_daemon_status_json_reports_executable_mismatch(isolate_state):
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "daemon.pid").write_text(
        f'{os.getpid()} 9.9.9\nexecutable="/opt/pipx/bin/tk"\n'
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    daemon = envelope["payload"]["daemon"]
    assert daemon["running"] is True
    assert daemon["executable"] == "/opt/pipx/bin/tk"
    assert daemon["executable_match"] is False


def test_daemon_json_requires_a_mode():
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_daemon_json_rejects_multiple_modes():
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--status", "--stop", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_telegram_remote_status_json_requires_config():
    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "telegram_remote_not_configured"
    assert "tk notify --telegram" in envelope["message"]
    assert result.exit_code == 1


def test_telegram_remote_background_json_requires_config():
    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--background", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "telegram_remote_not_configured"
    assert result.exit_code == 1


def test_telegram_remote_status_json_not_running():
    Config(
        accounts=[_codex_account("codex (dev)")],
        notifications=NotifyConfig(
            enabled=True,
            backend="telegram",
            telegram_bot_token="tok123",
            telegram_chat_id="chat456",
        ),
    ).save()
    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--status", "--json-output"])
    envelope = _parse_envelope(result.output)
    remote = envelope["payload"]["telegram_remote"]
    assert envelope["ok"] is True
    assert remote["configured"] is True
    assert remote["chat_id"] == "chat456"
    assert remote["enabled"] is False
    assert remote["running"] is False
    assert remote["pidfile_exists"] is False


def test_telegram_remote_background_starts_process_and_writes_pid(monkeypatch, isolate_state):
    Config(
        accounts=[_codex_account("codex (dev)")],
        notifications=NotifyConfig(
            enabled=True,
            backend="telegram",
            telegram_bot_token="tok123",
            telegram_chat_id="chat456",
        ),
    ).save()
    calls = []

    class FakeProcess:
        pid = 4242

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tokenkick.cli.sys.argv", ["/tmp/tk"])
    monkeypatch.setattr("tokenkick.versioning.installed_version", lambda: "1.16.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--background"])

    assert result.exit_code == 0
    assert "Telegram remote started in background" in result.output
    assert (isolate_state / "telegram-remote.pid").read_text().startswith("4242 1.16.0")
    assert Config.load().telegram_remote_enabled is True
    assert calls[0][0][0] == ["/tmp/tk", "remote", "telegram"]
    assert calls[0][1]["start_new_session"] is True


def test_telegram_remote_background_does_not_start_duplicate(monkeypatch, isolate_state):
    Config(
        accounts=[_codex_account("codex (dev)")],
        notifications=NotifyConfig(
            enabled=True,
            backend="telegram",
            telegram_bot_token="tok123",
            telegram_chat_id="chat456",
        ),
    ).save()
    (isolate_state / "telegram-remote.pid").write_text("4242\n")
    calls = []
    monkeypatch.setattr("tokenkick.cli._pid_is_running", lambda pid: True)
    monkeypatch.setattr("tokenkick.cli.subprocess.Popen", lambda *args, **kwargs: calls.append(args))

    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--background"])

    assert result.exit_code == 0
    assert "already running" in result.output
    assert Config.load().telegram_remote_enabled is True
    assert calls == []


def test_telegram_remote_stop_removes_stale_pidfile(isolate_state):
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "telegram-remote.pid").write_text("4242\n")
    Config(telegram_remote_enabled=True).save()

    runner = CliRunner()
    result = runner.invoke(cli, ["remote", "telegram", "--stop"])

    assert result.exit_code == 0
    assert "was not running" in result.output
    assert not (isolate_state / "telegram-remote.pid").exists()
    assert Config.load().telegram_remote_enabled is False


def test_daemon_stop_json_when_not_running():
    runner = CliRunner()
    result = runner.invoke(cli, ["daemon", "--stop", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["stopped"] is False
    assert envelope["payload"]["was_running"] is False
    assert envelope["payload"]["daemon"]["running"] is False


# ---------------------------------------------------------------------------
# read-only JSON outputs
# ---------------------------------------------------------------------------

def test_accounts_list_json_empty():
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "list", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"] == {"accounts": []}
    assert "No saved accounts" in envelope["message"]


def test_accounts_list_json_fields():
    _seed_config([_codex_account("codex (dev)", auto_kick=True)])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "list", "--json-output"])
    envelope = _parse_envelope(result.output)
    (account,) = envelope["payload"]["accounts"]
    assert account["label"] == "codex (dev)"
    assert account["provider"] == "codex"
    assert account["kickable"] is True
    assert account["auto_kick"] is True
    assert account["visible"] is True
    assert "notifications_route" in account
    assert "kick_model" in account


def test_accounts_notifications_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "notifications", "--json-output"])
    envelope = _parse_envelope(result.output)
    payload = envelope["payload"]
    assert payload["global_enabled"] is False
    (account,) = payload["accounts"]
    assert account["label"] == "codex (dev)"
    assert "route" in account


def test_accounts_planning_json():
    _seed_config([_codex_account("codex (dev)", usable_session_minutes=120)])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "planning", "--json-output"])
    envelope = _parse_envelope(result.output)
    (account,) = envelope["payload"]["accounts"]
    assert account["usable_session_minutes"] == 120
    assert account["orchestration_role"] == "normal"
    assert "effective_orchestration_role" in account
    assert account["weekly_reserve_threshold_percent"] is None


def test_auto_status_json():
    _seed_config([_codex_account("codex (dev)", auto_kick=True)])
    runner = CliRunner()
    result = runner.invoke(cli, ["auto", "status", "--json-output"])
    envelope = _parse_envelope(result.output)
    (account,) = envelope["payload"]["accounts"]
    assert account["auto_kick"] is True
    assert account["weekly_auto_kick"] is True
    assert account["session_auto_kick"] is True
    assert account["monitor_only"] is False


def test_schedule_show_json_defaults():
    runner = CliRunner()
    result = runner.invoke(cli, ["schedule", "show", "--json-output"])
    envelope = _parse_envelope(result.output)
    payload = envelope["payload"]
    assert payload["enabled"] is False
    assert payload["default"] == {"enabled": False}
    assert payload["accounts"] == {}
    assert payload["pending_kicks"] == []


# ---------------------------------------------------------------------------
# mutation JSON results
# ---------------------------------------------------------------------------

def test_schedule_set_default_json_success():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["schedule", "set", "--default", "--weekdays", "09:00-17:00", "--json-output"],
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "set"
    assert envelope["payload"]["scope"] == "default"
    assert envelope["payload"]["schedule"]["default"]["weekdays"] == "09:00-17:00"
    assert Config.load().schedule.default.weekdays == "09:00-17:00"
    assert result.exit_code == 0


def test_schedule_disable_account_json_success():
    _seed_config([_codex_account("codex (dev)")])
    config = Config.load()
    config.schedule.enabled = True
    config.schedule.accounts["codex (dev)"] = WorkSchedule(
        enabled=True,
        weekdays="09:00-17:00",
    )
    config.save()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["schedule", "disable", "--account", "codex (dev)", "--json-output"],
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "disable"
    assert envelope["payload"]["scope"] == "codex (dev)"
    assert envelope["payload"]["schedule"]["accounts"]["codex (dev)"]["enabled"] is False
    assert Config.load().schedule.accounts["codex (dev)"].enabled is False
    assert result.exit_code == 0


def test_schedule_clear_default_json_success():
    config = Config.load()
    config.schedule.enabled = True
    config.schedule.default.enabled = True
    config.schedule.default.weekdays = "09:00-17:00"
    config.save()

    runner = CliRunner()
    result = runner.invoke(cli, ["schedule", "clear", "--default", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "clear"
    assert envelope["payload"]["scope"] == "default"
    assert envelope["payload"]["schedule"]["default"] == {"enabled": False}
    assert Config.load().schedule.default.is_default()
    assert result.exit_code == 0


def test_schedule_set_json_usage_error_is_nonzero_and_pure_json():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["schedule", "set", "--weekdays", "09:00-17:00", "--json-output"],
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert "Choose exactly one" in envelope["message"]
    assert result.exit_code == 2

def test_accounts_hide_json_success():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "hide", "codex (dev)", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["account"]["visible"] is False
    assert "hidden" in envelope["message"]
    assert Config.load().accounts[0].visible is False
    assert result.exit_code == 0


def test_accounts_hide_json_not_found():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "hide", "nope", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "mutation_failed"
    assert "not found" in envelope["message"]
    assert result.exit_code == 1


def test_auto_enable_json_success():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "auto",
            "enable",
            "codex (dev)",
            "--accept-risk",
            "ENABLE",
            "--json-output",
        ],
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["account"]["auto_kick"] is True
    assert Config.load().accounts[0].auto_kick is True
    assert Config.load().auto_kick_consents == {"codex": 1}


def test_auto_enable_json_requires_consent():
    _seed_config([_codex_account("codex (dev)")])
    result = CliRunner().invoke(
        cli,
        ["auto", "enable", "codex (dev)", "--json-output"],
    )

    envelope = _parse_envelope(result.output)
    consent = envelope["payload"]["consent"]
    assert envelope["ok"] is False
    assert envelope["error_code"] == "auto_kick_consent_required"
    assert consent["provider"] == "codex"
    assert consent["version"] == 1
    assert consent["confirmation"] == "ENABLE"
    assert "Whether scheduled kicking falls under that is unsettled" in consent["text"]
    assert Config.load().accounts[0].auto_kick is False
    assert result.exit_code == 1


def test_auto_enable_json_rejected_for_gemini():
    _seed_config(
        [AccountConfig(label="gemini (dev)", provider="gemini", source=DataSource.MANUAL)]
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["auto", "enable", "gemini (dev)", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "mutation_rejected"
    assert result.exit_code == 1


def test_accounts_set_role_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "set-role", "codex (dev)", "backup", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["account"]["orchestration_role"] == "backup"


def test_accounts_set_notifications_json_usage_error():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["accounts", "set-notifications", "codex (dev)", "--json-output"],
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_claude_direct_usage_enable_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["claude", "direct-usage", "enable", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["claude"]["direct_usage_enabled"] is True
    assert Config.load().claude.direct_usage_enabled is True


def test_codex_strategy_enable_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["codex-strategy", "enable", "--json-output"])
    envelope = _parse_envelope(result.output)

    assert result.exit_code == 0
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "enable"
    assert envelope["payload"]["codex_strategy"]["enabled"] is True
    assert Config.load().codex_burst_ladder_enabled is True


def test_codex_strategy_gap_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["codex-strategy", "gap", "45", "--json-output"])
    envelope = _parse_envelope(result.output)

    assert result.exit_code == 0
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "gap_set"
    assert envelope["payload"]["codex_strategy"]["configured_gap_seconds"] == 45
    assert Config.load().codex_burst_ladder_gap_seconds == 45


def test_codex_strategy_demotion_force_prune_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "codex-strategy",
            "demotion",
            "force-prune",
            "codex (dev)",
            "interactive-like",
            "--json-output",
        ],
    )
    envelope = _parse_envelope(result.output)

    assert result.exit_code == 0
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "force_prune"
    assert envelope["payload"]["account"] == "codex (dev)"
    assert envelope["payload"]["codex_surfaces"]["demotion"]["force_prune"] == ["interactive-like"]
    assert Config.load().accounts[0].codex_surface_force_prune == ["interactive-like"]


def test_codex_surfaces_reset_stats_app_mode_json():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["codex-surfaces", "codex (dev)", "reset-stats"],
        env={"TK_APP_MODE": "1"},
    )
    envelope = _parse_envelope(result.output)

    assert result.exit_code == 0
    assert envelope["ok"] is True
    assert envelope["payload"]["action"] == "reset_stats"
    assert envelope["payload"]["codex_surfaces"]["label"] == "codex (dev)"


def test_mutation_json_keeps_stdout_pure():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["accounts", "hide", "codex (dev)", "--json-output"])
    json.loads(result.output)  # raises if anything but the envelope reached stdout


# ---------------------------------------------------------------------------
# tk app snapshot
# ---------------------------------------------------------------------------

def test_app_snapshot_envelope_and_sections(monkeypatch):
    monkeypatch.setattr(app_commands, "_external_tk_info", lambda **_: None)
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "snapshot"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    payload = envelope["payload"]
    for section in (
        "generated_at",
        "core",
        "runtime",
        "paths",
        "daemon",
        "status",
        "pending_kicks",
        "schedule",
        "advisories",
        "reset_observations",
        "notifications",
        "codex_strategy",
        "update",
    ):
        assert section in payload, f"missing snapshot section {section}"
    assert payload["daemon"]["running"] is False
    assert payload["update"]["installed_version"] == payload["core"]["version"]
    assert payload["notifications"]["accounts"][0]["label"] == "codex (dev)"
    assert result.exit_code == 0


def test_app_snapshot_warns_on_daemon_version_mismatch(monkeypatch, isolate_state):
    monkeypatch.setattr(app_commands, "_external_tk_info", lambda **_: None)
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "daemon.pid").write_text(f"{os.getpid()} 0.0.1\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "snapshot"])
    envelope = _parse_envelope(result.output)
    daemon = envelope["payload"]["daemon"]
    assert daemon["running"] is True
    assert daemon["version"] == "0.0.1"
    assert daemon["version_match"] is False
    assert any("0.0.1" in warning for warning in envelope["warnings"])


def test_app_snapshot_includes_daemon_executable_mismatch(monkeypatch, isolate_state):
    monkeypatch.setattr(app_commands, "_external_tk_info", lambda **_: None)
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "daemon.pid").write_text(
        f'{os.getpid()} 9.9.9\nexecutable="/opt/pipx/bin/tk"\n'
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["app", "snapshot"])
    envelope = _parse_envelope(result.output)
    daemon = envelope["payload"]["daemon"]

    assert daemon["running"] is True
    assert daemon["executable"] == "/opt/pipx/bin/tk"
    assert daemon["executable_match"] is False
    assert any("different TokenKick executable" in warning for warning in envelope["warnings"])


def test_app_snapshot_warns_on_external_tk_mismatch(monkeypatch):
    monkeypatch.setattr(
        app_commands,
        "_external_tk_info",
        lambda **_: {"path": "/usr/local/bin/tk", "is_current_runtime": False, "version": "0.0.9"},
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "snapshot"])
    envelope = _parse_envelope(result.output)
    assert envelope["payload"]["runtime"]["external_tk"]["version"] == "0.0.9"
    assert any("External tk" in warning for warning in envelope["warnings"])


def test_app_snapshot_warns_without_status_cache(monkeypatch):
    monkeypatch.setattr(app_commands, "_external_tk_info", lambda **_: None)
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "snapshot"])
    envelope = _parse_envelope(result.output)
    assert envelope["payload"]["status"]["cached"] is False
    assert any("status cache" in warning.lower() for warning in envelope["warnings"])


# ---------------------------------------------------------------------------
# tk app setup
# ---------------------------------------------------------------------------

def _setup_pairs(accounts, statuses, summary="Found 1 account via auto-discovery: codex."):
    def fake_load(config, *, prepare_claude_setup=False):
        return accounts, statuses, True, summary, []

    return fake_load


def test_app_setup_json_lines_success(monkeypatch):
    account = _codex_account("codex (dev)")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE)
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        _setup_pairs([account], [status]),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "setup"])
    lines = [line for line in result.output.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    events = [record["event"] for record in records]
    assert events[0] == "setup_started"
    assert "discovery_completed" in events
    assert "config_saved" in events
    final = records[-1]
    assert final["event"] == "setup_completed"
    assert final["ok"] is True
    assert final["payload"]["config_saved"] is True
    assert final["payload"]["accounts"][0]["label"] == "codex (dev)"
    saved = Config.load()
    assert [a.label for a in saved.accounts] == ["codex (dev)"]
    assert result.exit_code == 0


def test_app_setup_json_lines_no_accounts(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        _setup_pairs([], [], summary="No accounts found."),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "setup"])
    records = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    final = records[-1]
    assert final["event"] == "setup_completed"
    assert final["ok"] is True
    assert final["payload"]["config_saved"] is False
    assert final["payload"]["accounts"] == []
    assert final["warnings"]
    assert result.exit_code == 0


def test_app_setup_json_lines_cancelled(monkeypatch):
    def raise_interrupt(config, *, prepare_claude_setup=False):
        raise KeyboardInterrupt

    monkeypatch.setattr("tokenkick.cli._load_account_status_pairs", raise_interrupt)
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "setup"])
    records = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    final = records[-1]
    assert final["event"] == "setup_cancelled"
    assert final["ok"] is False
    assert final["error_code"] == "cancelled"
    assert result.exit_code == 130


def test_app_setup_json_lines_failure(monkeypatch):
    def raise_error(config, *, prepare_claude_setup=False):
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr("tokenkick.cli._load_account_status_pairs", raise_error)
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "setup"])
    records = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    final = records[-1]
    assert final["event"] == "setup_failed"
    assert final["ok"] is False
    assert final["error_code"] == "setup_failed"
    assert "discovery exploded" in final["message"]
    assert result.exit_code == 1


def test_app_setup_never_prompts_or_starts_daemon(monkeypatch):
    account = _codex_account("codex (dev)")
    status = AccountStatus(label=account.label, state=AccountState.ACTIVE)
    monkeypatch.setattr(
        "tokenkick.cli._load_account_status_pairs",
        _setup_pairs([account], [status]),
    )

    def fail_prompt(*args, **kwargs):
        raise AssertionError("app setup must not prompt")

    def fail_daemon(*args, **kwargs):
        raise AssertionError("app setup must not start the daemon")

    monkeypatch.setattr("tokenkick.cli._confirm_prompt", fail_prompt)
    monkeypatch.setattr("tokenkick.cli._start_daemon_background", fail_daemon)
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "setup"])
    records = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert records[-1]["event"] == "setup_completed"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# tk app doctor
# ---------------------------------------------------------------------------

def test_app_doctor_envelope_and_sections():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "doctor"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    payload = envelope["payload"]
    for section in ("environment", "provider_clis", "state", "daemon", "doctor"):
        assert section in payload, f"missing doctor section {section}"
    assert payload["state"]["config_dir_writable"] is True
    assert payload["state"]["config_loadable"] is True
    assert payload["doctor"] is not None
    assert "summary" in payload["doctor"]
    for name in ("codex", "claude", "gemini"):
        assert "found" in payload["provider_clis"][name]
    assert result.exit_code == 0


def test_app_doctor_reports_unreadable_config(isolate_state):
    isolate_state.mkdir(parents=True, exist_ok=True)
    (isolate_state / "config.json").write_text("{not json")
    runner = CliRunner()
    result = runner.invoke(cli, ["app", "doctor"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["state"]["config_loadable"] is False
    assert envelope["payload"]["state"]["config_error"]
    assert envelope["warnings"]


# ---------------------------------------------------------------------------
# external tk detection
# ---------------------------------------------------------------------------

def test_external_tk_info_absent(monkeypatch):
    monkeypatch.setattr(app_commands.shutil, "which", lambda name: None)
    assert app_commands._external_tk_info() is None


def test_external_tk_info_current_runtime(monkeypatch, tmp_path):
    fake_tk = tmp_path / "tk"
    fake_tk.write_text("#!/bin/sh\n")
    monkeypatch.setattr(app_commands.shutil, "which", lambda name: str(fake_tk))
    monkeypatch.setattr(app_commands.sys, "argv", [str(fake_tk)])
    info = app_commands._external_tk_info()
    assert info["is_current_runtime"] is True
    assert info["version"] is not None


def test_external_tk_info_other_runtime_skips_probe(monkeypatch, tmp_path):
    fake_tk = tmp_path / "tk"
    fake_tk.write_text("#!/bin/sh\n")
    monkeypatch.setattr(app_commands.shutil, "which", lambda name: str(fake_tk))
    monkeypatch.setattr(app_commands.sys, "argv", ["/somewhere/else"])
    info = app_commands._external_tk_info(probe_version=False)
    assert info["is_current_runtime"] is False
    assert info["version"] is None


# ---------------------------------------------------------------------------
# stdout reservation end-to-end (subprocess, real TK_APP_MODE)
# ---------------------------------------------------------------------------

def _run_tk_subprocess(args, home, extra_env=None):
    env = {
        **os.environ,
        "HOME": str(home),
        "TK_APP_MODE": "1",
        "PATH": "/usr/bin:/bin",
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", "from tokenkick.cli import cli; cli()", *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
    )


def test_app_mode_status_keeps_stdout_empty(tmp_path):
    result = _run_tk_subprocess(["status"], home=tmp_path)
    assert result.stdout == ""


def test_app_mode_accounts_list_stdout_is_json(tmp_path):
    result = _run_tk_subprocess(["accounts", "list"], home=tmp_path)
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["payload"] == {"accounts": []}


# ---------------------------------------------------------------------------
# tk kick --json-output (single label)
# ---------------------------------------------------------------------------

def _fresh_status(label, *, stale=False):
    return AccountStatus(label=label, state=AccountState.FRESH, stale=stale)


def test_kick_json_rejects_bulk_modes():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "--all", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_kick_json_requires_label():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_kick_json_account_not_found():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "nope", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "account_not_found"
    assert result.exit_code == 1


def test_kick_json_gemini_is_monitor_only():
    _seed_config(
        [AccountConfig(label="gemini (dev)", provider="gemini", source=DataSource.MANUAL)]
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "gemini (dev)", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "monitor_only"
    assert result.exit_code == 1


def test_kick_json_skips_not_fresh_account(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: AccountStatus(
            label=account.label, state=AccountState.ACTIVE
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["decision"] == "skipped"
    assert envelope["payload"]["reason_code"] == "not_fresh"
    assert envelope["payload"]["kicked"] is False
    assert "not fresh" in envelope["message"]
    assert result.exit_code == 0


def test_kick_json_stale_status_requires_yes(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label, stale=True),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "confirmation_required"
    assert envelope["payload"]["confirmations"] == ["stale_status"]
    assert result.exit_code == 1


def test_kick_json_dry_run_reports_would_kick(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--dry-run", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    payload = envelope["payload"]
    assert payload["decision"] == "would_kick"
    assert payload["kicked"] is False
    assert payload["dry_run"] is True
    assert payload["kick_type"]
    assert payload["confirmations"] == []
    assert result.exit_code == 0


def test_kick_json_attempted_confirmed(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label),
    )
    from tokenkick.models import KickEvent

    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, status, kick_type=None: KickEvent(
            label=account.label,
            success=True,
            confirmed=True,
            kick_type=kick_type,
            prompt_text="secret prompt",
            response_text="long response",
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output", "--yes"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    payload = envelope["payload"]
    assert payload["decision"] == "attempted"
    assert payload["kicked"] is True
    assert payload["result"] == "confirmed"
    assert payload["event"]["confirmed"] is True
    assert "prompt_text" not in payload["event"]
    assert "response_text" not in payload["event"]
    assert result.exit_code == 0


def test_kick_json_attempted_unconfirmed(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label),
    )
    from tokenkick.cli import PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR
    from tokenkick.models import KickEvent

    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, status, kick_type=None: KickEvent(
            label=account.label,
            success=True,
            confirmed=False,
            error=PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR,
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output", "--yes"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    payload = envelope["payload"]
    assert payload["result"] == "unconfirmed"
    assert envelope["message"] == PROVIDER_ACCEPTED_PHANTOM_KICK_ERROR
    assert result.exit_code == 0


def test_kick_json_attempted_failed(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label),
    )
    from tokenkick.models import KickEvent

    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, status, kick_type=None: KickEvent(
            label=account.label,
            success=False,
            confirmed=False,
            error="codex exec failed: rate limited",
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output", "--yes"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "kick_failed"
    assert envelope["payload"]["result"] == "failed"
    assert "rate limited" in envelope["message"]
    assert result.exit_code == 1


def test_kick_json_stale_with_yes_proceeds(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._fetch_status",
        lambda account, config=None: _fresh_status(account.label, stale=True),
    )
    from tokenkick.models import KickEvent

    monkeypatch.setattr(
        "tokenkick.cli._kick_and_notify",
        lambda account, config, status, kick_type=None: KickEvent(
            label=account.label, success=True, confirmed=True
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kick", "codex (dev)", "--json-output", "--yes"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["result"] == "confirmed"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# tk history --json-output / tk notify --json-output (Phase 5C)
# ---------------------------------------------------------------------------

def test_history_json_emits_empty_array_when_no_events():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["history", "--json-output"])
    assert json.loads(result.output) == []
    assert result.exit_code == 0


def test_notify_json_enables_ntfy():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--ntfy", "fixture-topic", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == "ntfy notifications enabled."
    payload = envelope["payload"]
    assert payload["global_enabled"] is True
    assert "ntfy:fixture-topic" in payload["destination"]
    assert Config.load().notifications.ntfy_topic == "fixture-topic"
    assert result.exit_code == 0


def test_notify_json_enables_telegram():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli, ["notify", "--telegram", "tok123", "chat456", "--json-output"]
    )
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == "Telegram notifications enabled."
    assert "telegram:chat456" in envelope["payload"]["destination"]
    saved = Config.load().notifications
    assert saved.telegram_bot_token == "tok123"
    assert saved.telegram_chat_id == "chat456"


def test_notify_json_configures_telegram_remote_only():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(
        cli, ["notify", "--telegram-remote", "tok123", "chat456", "--json-output"]
    )

    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == (
        "Telegram remote credentials saved; Telegram notifications disabled."
    )
    assert envelope["payload"]["global_enabled"] is False
    saved = Config.load().notifications
    assert saved.enabled is False
    assert saved.enabled_backends == []
    assert saved.telegram_bot_token == "tok123"
    assert saved.telegram_chat_id == "chat456"


def test_notify_json_disable_telegram_backend_keeps_remote_credentials():
    config = _seed_config([_codex_account("codex (dev)")])
    config.notifications = NotifyConfig(
        enabled=True,
        backend="telegram",
        ntfy_topic="topic",
        telegram_bot_token="tok123",
        telegram_chat_id="chat456",
        enabled_backends=["ntfy", "telegram"],
    )
    config.save()

    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--disable-backend", "telegram", "--json-output"])

    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == "Telegram notifications disabled."
    assert "ntfy:topic" in envelope["payload"]["destination"]
    assert "telegram:chat456" not in envelope["payload"]["destination"]
    saved = Config.load().notifications
    assert saved.enabled is True
    assert saved.backend == "ntfy"
    assert saved.enabled_backends == ["ntfy"]
    assert saved.telegram_bot_token == "tok123"
    assert saved.telegram_chat_id == "chat456"


def test_notify_json_usage_error_without_backend():
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_notify_json_unknown_action():
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "frobnicate", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_notify_test_json_failure_when_disabled():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "test", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "notification_test_failed"
    assert envelope["payload"] == {"action": "test", "delivered": False}
    assert result.exit_code == 1


def test_notify_test_json_selected_backend_only(monkeypatch):
    config = _seed_config([_codex_account("codex (dev)")])
    config.notifications = NotifyConfig(
        enabled=True,
        backend="ntfy",
        ntfy_topic="topic",
        telegram_bot_token="tok123",
        telegram_chat_id="chat456",
        enabled_backends=["ntfy", "telegram"],
    )
    config.save()
    calls = []
    monkeypatch.setattr(
        "tokenkick.cli.notify_test",
        lambda notifications: calls.append(notifications) or True,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "test", "--backend", "telegram", "--json-output"])

    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["message"] == "Telegram test notification sent."
    assert envelope["payload"] == {
        "action": "test",
        "backend": "telegram",
        "delivered": True,
    }
    assert [call.backend for call in calls] == ["telegram"]
    assert calls[0].telegram_chat_id == "chat456"


def test_notify_test_json_selected_backend_reports_missing_credentials():
    config = _seed_config([_codex_account("codex (dev)")])
    config.notifications = NotifyConfig(
        enabled=True,
        backend="telegram",
        enabled_backends=["telegram"],
    )
    config.save()

    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "test", "--backend", "telegram", "--json-output"])

    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "notification_test_failed"
    assert envelope["message"] == "Telegram test notification failed."
    assert envelope["payload"] == {
        "action": "test",
        "backend": "telegram",
        "delivered": False,
        "reason": "missing_telegram_credentials",
    }
    assert result.exit_code == 1


def test_notify_json_backend_option_requires_test_action():
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--backend", "telegram", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is False
    assert envelope["error_code"] == "usage_error"
    assert result.exit_code == 2


def test_notify_test_json_success(monkeypatch):
    _seed_config([_codex_account("codex (dev)")])
    monkeypatch.setattr(
        "tokenkick.cli._send_global_notifications",
        lambda notifications, sender: True,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "test", "--json-output"])
    envelope = _parse_envelope(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["delivered"] is True


def test_notify_cli_behavior_unchanged():
    _seed_config([_codex_account("codex (dev)")])
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--ntfy", "topic-x"])
    assert "ntfy notifications enabled." in result.output
    assert Config.load().notifications.ntfy_topic == "topic-x"
    assert result.exit_code == 0
