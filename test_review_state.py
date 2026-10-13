"""State and interface regressions for the Opus repair batch; isolated state only."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import time

import click
from click.testing import CliRunner
import pytest

from tokenkick import cli, models, sources, status_cache
from tokenkick.models import AccountStatus, AccountState, Config


@pytest.fixture
def state(tmp_path, monkeypatch):
    for module in (models, cli):
        monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(module, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "STATUS_CACHE_REFRESH_LOCK_FILE", tmp_path / "refresh")
    monkeypatch.setattr(cli, "DAEMON_LOG_FILE", tmp_path / "daemon.log")
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", tmp_path / "pending.json")
    return tmp_path


@pytest.mark.parametrize(
    "payload",
    [
        {"migrations": None},
        {"poll_interval_minutes": 0},
        {"telegram_remote_enabled": "false"},
        {"accounts": [{"label": "A", "auto_kick": "false"}]},
        {"schedule": {"default": {"enabled": "false"}}},
        {"schedule": {"scheduling_target": "future"}},
        {"schema_version": 2},
        {"accounts": [{"label": "A", "weekly_auto_kick": "false"}]},
        {"accounts": [{"label": "A", "session_auto_kick": "false"}]},
        {"accounts": [{"label": "A", "codex_surface_demote_min_kept_anchor_rate": float("nan")}]},
        {"accounts": [{"label": None}]},
    ],
)
def test_malformed_controls_fail_before_mutation(state, payload):
    p = state / "config.json"
    original = json.dumps(payload)
    p.write_text(original)
    with pytest.raises(models.StateFileError):
        Config.load()
    assert p.read_text() == original


def test_unknown_config_fields_survive_unrelated_save(state):
    data = {
        "future": {"version": 7},
        "accounts": [{"label": "A", "future_account": "keep"}],
        "notifications": {"future_channel": "keep"},
        "schedule": {
            "default": {"future_window": 42},
            "accounts": {"A": {"enabled": True, "future_rule": 5}},
        },
    }
    (state / "config.json").write_text(json.dumps(data))
    config = Config.load()
    config.poll_interval_minutes = 10
    config.save()
    saved = json.loads((state / "config.json").read_text())
    assert saved["future"] == data["future"]
    assert saved["accounts"][0]["future_account"] == "keep"
    assert saved["schedule"]["default"]["future_window"] == 42
    assert saved["schedule"]["accounts"]["A"]["future_rule"] == 5


def test_slow_native_read_is_diagnostic_not_failure():
    result = sources._parse_codex_appserver_ratelimits(
        "A",
        {
            "id": 2,
            "result": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 10,
                        "windowDurationMins": 300,
                        "resetsAt": time.time() + 18000,
                    },
                    "secondary": None,
                }
            },
        },
        elapsed_ms=10500,
    )
    assert result.error is None
    assert result.source_diagnostics["elapsed_ms"] == 10500


def test_expired_jsonl_cannot_replace_fresh_native():
    native = AccountStatus(
        "A",
        AccountState.ACTIVE,
        source_detail="codex-app-server",
        session_window_minutes=300,
        session_resets_in_seconds=18000,
        session_used_percent=0.01,
    )
    old = replace(
        native,
        used_percent=60,
        session_used_percent=50,
        session_resets_at=time.time() - 60,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    assert not sources._codex_session_status_should_override_appserver(native, old)


def test_abandoned_empty_refresh_lock_recovers_but_live_owner_stays(state):
    p = cli.STATUS_CACHE_REFRESH_LOCK_FILE
    p.write_text("")
    os.utime(p, (time.time() - 7200,) * 2)
    assert not status_cache._status_refresh_lock_active()
    p.write_text(str(os.getpid()))
    os.utime(p, (time.time() - 7200,) * 2)
    assert status_cache._status_refresh_lock_active()


def test_refresh_lock_truncated_after_stat_is_treated_as_empty():
    path = SimpleNamespace(
        exists=lambda: True,
        stat=lambda: SimpleNamespace(st_mtime=time.time(), st_size=12),
        read_text=lambda: "",
    )
    pid, age = status_cache._read_refresh_lock(path)
    assert pid is None
    assert age == 0


def test_frozen_background_refresh_uses_executable_command(state, monkeypatch):
    monkeypatch.setattr(status_cache.sys, "frozen", True, raising=False)
    child = Mock(return_value=SimpleNamespace(pid=os.getpid()))
    monkeypatch.setattr(status_cache.subprocess, "Popen", child)
    assert status_cache._start_background_status_refresh()
    assert child.call_args.args[0] == [status_cache.sys.executable, "refresh-cache"]


def test_internal_discovery_type_error_does_not_retry(monkeypatch):
    from tokenkick.discovery import _load_account_status_pairs_cached

    discovery = Mock(side_effect=TypeError("internal defect"))
    monkeypatch.setattr(cli, "_discover_accounts_and_statuses", discovery)
    with pytest.raises(TypeError, match="internal defect"):
        _load_account_status_pairs_cached(Config())
    assert discovery.call_count == 1


def test_menu_contains_expected_validation_error(capsys):
    from tokenkick.interactive import _invoke

    @click.command()
    def invalid():
        raise click.ClickException("Select at least one surface")

    _invoke(click.Context(invalid), invalid)
    assert "Select at least one surface" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0.5"])
def test_burst_gap_rejects_invalid_values_without_writing(state, value):
    result = CliRunner().invoke(cli.cli, ["codex-strategy", "gap", "--", value])
    assert result.exit_code != 0
    assert not (state / "config.json").exists()


def test_history_renders_untrusted_markup_literally(capsys):
    cli._render_history_verbose(
        [models.KickEvent(label="[/bad]", response_text="[/bad]", error="[/other]")]
    )
    assert "[/bad]" in capsys.readouterr().out


def test_telegram_nonfinite_usage_and_warning_priority():
    from tokenkick.telegram_remote import _percent_text, format_status_reply, StatusCommandResult

    for v in [float("nan"), float("inf")]:
        assert _percent_text(v) == "?"
    rows = [{"label": str(i), "state": "active"} for i in range(12)]
    rows.append(
        {"label": "Warning account", "state": "unknown", "error": "Authentication required"}
    )
    reply = format_status_reply(StatusCommandResult(0, json.dumps({"accounts": rows}), ""))
    assert "Warning account" in reply


def test_doctor_masks_ntfy_destination():
    from tokenkick.doctor import _doctor_notifications

    config = Config(
        notifications=models.NotifyConfig(enabled=True, ntfy_topic="private-topic-secret")
    )
    info, checks = _doctor_notifications(config)
    assert "private-topic-secret" not in str(info) + str(checks)


def test_subsecond_reset_rounding_does_not_compromise_plan():
    from tokenkick.reservation_advisories import _reservation_plan_may_be_compromised

    at = datetime.now(timezone.utc)
    status = AccountStatus(
        "A", AccountState.ACTIVE, session_used_percent=3, session_resets_at=at.timestamp() + 0.4
    )
    assert not _reservation_plan_may_be_compromised(status, at)
    assert _reservation_plan_may_be_compromised(
        replace(status, session_resets_at=at.timestamp() + 30), at
    )


def test_plan_display_uses_selected_zone_even_on_utc_host():
    from zoneinfo import ZoneInfo

    token = cli._plan_display_zone.set(ZoneInfo("Europe/Berlin"))
    try:
        assert (
            cli._format_plan_timestamp(datetime(2026, 10, 13, 12, tzinfo=timezone.utc)) == "14:00"
        )
    finally:
        cli._plan_display_zone.reset(token)


def test_shutdown_during_action_drains_then_stops(state):
    from tokenkick.action_state import account_action

    try:
        with account_action(state, "A"):
            cli._daemon_shutdown(None, None)
            assert cli._shutdown_requested
        with pytest.raises(KeyboardInterrupt):
            cli._daemon_shutdown(None, None)
    finally:
        cli._shutdown_requested = False
