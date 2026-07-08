"""Tests for TokenKick models and state determination."""

import json
import time

import pytest

from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeConfig,
    Config,
    DataSource,
    KickEvent,
    NotifyConfig,
    ScheduleConfig,
    StateFileError,
    WorkSchedule,
    account_key_string,
    format_notification_timestamp,
    format_local_timestamp,
    load_kick_history,
)
from tokenkick.versioning import read_daemon_pidfile, write_daemon_pidfile


class TestAccountState:
    def test_fresh_emoji(self):
        assert AccountState.FRESH.emoji == "🟢"

    def test_fresh_action(self):
        assert AccountState.FRESH.action == "Kick now"

    def test_all_states_have_emoji(self):
        for state in AccountState:
            assert state.emoji is not None

    def test_all_states_have_action(self):
        for state in AccountState:
            assert state.action is not None


class TestDaemonPidfile:
    def test_old_pidfile_without_executable_still_loads(self, tmp_path):
        pidfile = tmp_path / "daemon.pid"
        pidfile.write_text("1234 1.2.3\n")

        info = read_daemon_pidfile(pidfile)

        assert info is not None
        assert info.pid == 1234
        assert info.version == "1.2.3"
        assert info.executable is None

    def test_pidfile_executable_roundtrips_paths_with_spaces(self, tmp_path):
        pidfile = tmp_path / "daemon.pid"
        executable = "/Applications/TokenKick Dev.app/Contents/Resources/tokenkick/tk"

        write_daemon_pidfile(pidfile, 1234, version="1.2.3", executable=executable)
        info = read_daemon_pidfile(pidfile)

        assert info is not None
        assert info.pid == 1234
        assert info.version == "1.2.3"
        assert info.executable == executable


class TestAccountConfig:
    def test_roundtrip_dict(self):
        config = AccountConfig(
            label="personal",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            auto_kick=True,
            visible=False,
            codexbar_provider="codex",
            identity_provider_id="acct_123",
            identity_email="Personal@Example.Test",
            provider_home="/tmp/codex",
            label_origin="auto",
        )
        d = config.to_dict()
        restored = AccountConfig.from_dict(d)
        assert restored.label == "personal"
        assert restored.source == DataSource.CODEX_DIRECT
        assert restored.auto_kick is True
        assert restored.visible is False
        assert restored.codexbar_provider == "codex"
        assert restored.identity_provider_id == "acct_123"
        assert restored.identity_email == "Personal@Example.Test"
        assert restored.provider_home == "/tmp/codex"
        assert restored.label_origin == "auto"

    def test_visible_defaults_to_true_for_existing_configs(self):
        restored = AccountConfig.from_dict({"label": "personal"})
        assert restored.visible is True

    def test_notifications_enabled_defaults_to_true_for_existing_configs(self):
        restored = AccountConfig.from_dict({"label": "personal"})
        assert restored.notifications_enabled is True
        assert "notifications_enabled" not in restored.to_dict()

    def test_notifications_enabled_false_roundtrips(self):
        restored = AccountConfig.from_dict(
            {"label": "personal", "notifications_enabled": False}
        )
        assert restored.notifications_enabled is False
        assert restored.to_dict()["notifications_enabled"] is False

    def test_notification_backends_roundtrip(self):
        restored = AccountConfig.from_dict(
            {"label": "personal", "notification_backends": ["telegram", "ntfy", "telegram"]}
        )

        assert restored.notification_backends == ["telegram", "ntfy"]
        assert restored.to_dict()["notification_backends"] == ["telegram", "ntfy"]

    def test_label_origin_defaults_to_user_for_existing_configs(self):
        restored = AccountConfig.from_dict({"label": "personal"})
        assert restored.label_origin == "user"

    def test_legacy_codex_ratelimit_source_loads_as_session_file(self):
        restored = AccountConfig.from_dict(
            {
                "label": "personal",
                "provider": "codex",
                "source": "codex-ratelimit",
                "session_path": "/tmp/codex-sessions",
            }
        )

        assert restored.source == DataSource.CODEX_SESSION_FILE
        assert restored.to_dict()["source"] == "codex-session-file"

    def test_codex_spark_bucket_gets_bucket_aware_key_without_changing_main_key(self):
        main = AccountConfig(
            label="codex",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex",
        )
        spark = AccountConfig(
            label="codex-spark",
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            provider_home="/tmp/codex",
            codex_rate_limit_id="codex_bengalfox",
        )

        assert account_key_string(main) == "codex-home|codex|/tmp/codex"
        assert account_key_string(spark) == "codex-home|codex|/tmp/codex#codex_bengalfox"

    def test_unknown_fields_are_ignored_for_forward_compatibility(self):
        restored = AccountConfig.from_dict({"label": "personal", "future_field": "future"})

        assert restored.label == "personal"
        assert not hasattr(restored, "future_field")

    def test_label_origin_null_saves_back_as_user(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        monkeypatch.setattr("tokenkick.models.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("tokenkick.models.CONFIG_FILE", config_file)
        config_file.write_text(
            '{"accounts": [{"label": "openrouter", "label_origin": null}]}\n'
        )

        config = Config.load()
        config.save()

        saved = config_file.read_text()
        assert '"label_origin": "user"' in saved
        assert '"label_origin": null' not in saved

    def test_none_values_excluded_from_dict(self):
        config = AccountConfig(label="test")
        d = config.to_dict()
        assert "codexbar_url" not in d
        assert "session_path" not in d

    def test_planning_fields_roundtrip(self):
        config = AccountConfig(
            label="test",
            plan_tier="pro_5x",
            usable_session_minutes=240,
        )

        restored = AccountConfig.from_dict(config.to_dict())

        assert restored.plan_tier == "pro_5x"
        assert restored.usable_session_minutes == 240

    def test_codex_surface_demotion_fields_roundtrip(self):
        config = AccountConfig(
            label="test",
            codex_surface_auto_demote=True,
            codex_surface_demote_after_strong_clusters=7,
            codex_surface_demote_min_active_surfaces=3,
            codex_surface_demote_min_kept_anchor_rate=0.98,
            codex_surface_demote_measurement_clusters=30,
            codex_surface_rescue_cooldown_strong_clusters=25,
            codex_surface_force_keep=["repo-skip"],
            codex_surface_force_prune=["interactive-like"],
        )

        restored = AccountConfig.from_dict(config.to_dict())

        assert restored.codex_surface_auto_demote is True
        assert restored.codex_surface_demote_after_strong_clusters == 7
        assert restored.codex_surface_demote_min_active_surfaces == 3
        assert restored.codex_surface_demote_min_kept_anchor_rate == 0.98
        assert restored.codex_surface_demote_measurement_clusters == 30
        assert restored.codex_surface_rescue_cooldown_strong_clusters == 25
        assert restored.codex_surface_force_keep == ["repo-skip"]
        assert restored.codex_surface_force_prune == ["interactive-like"]


class TestClaudeConfig:
    def test_false_without_explicit_marker_is_not_explicit_opt_out(self):
        config = ClaudeConfig.from_dict({"direct_usage_enabled": False})

        assert config.direct_usage_enabled is False
        assert config.direct_usage_explicit is False

    def test_explicit_marker_round_trips(self):
        config = ClaudeConfig.from_dict(
            {"direct_usage_enabled": False, "direct_usage_explicit": True}
        )

        assert config.to_dict() == {
            "direct_usage_enabled": False,
            "direct_usage_explicit": True,
        }


class TestAccountStatus:
    def test_resets_in_human_hours(self):
        status = AccountStatus(
            label="test",
            state=AccountState.WAITING,
            resets_in_seconds=7200,
        )
        assert status.resets_in_human == "2h 0m"

    def test_resets_in_human_minutes(self):
        status = AccountStatus(
            label="test",
            state=AccountState.ACTIVE,
            resets_in_seconds=1800,
        )
        assert status.resets_in_human == "30m"

    def test_resets_in_human_none(self):
        status = AccountStatus(
            label="test",
            state=AccountState.UNKNOWN,
        )
        assert status.resets_in_human == "—"

    def test_resets_in_human_reset_ready(self):
        status = AccountStatus(
            label="test",
            state=AccountState.FRESH,
            resets_in_seconds=0,
        )
        assert status.resets_in_human == "reset ready"
        assert status.resets_at_local == "reset ready"

    def test_resets_at_local_uses_local_timestamp(self, monkeypatch):
        monkeypatch.setattr("tokenkick.models.time.time", lambda: 1000.0)
        status = AccountStatus(
            label="test",
            state=AccountState.WAITING,
            resets_in_seconds=3600,
        )
        assert status.resets_at_local == format_local_timestamp(4600.0)

    def test_to_dict(self):
        status = AccountStatus(
            label="test",
            state=AccountState.FRESH,
            used_percent=0.0,
        )
        d = status.to_dict()
        assert d["state"] == "fresh"
        assert d["resets_in_human"] == "—"
        assert d["resets_at_local"] == "—"
        assert "error" not in d  # None values excluded


class TestConfig:
    def test_save_and_load(self, tmp_path, monkeypatch):
        # Redirect config to temp directory
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        config = Config(
            accounts=[
                AccountConfig(label="personal", auto_kick=True),
                AccountConfig(label="work", source=DataSource.CODEXBAR_HTTP),
            ],
            notifications=NotifyConfig(enabled=True, backend="ntfy", ntfy_topic="test-topic"),
            poll_interval_minutes=5,
        )
        config.save()

        assert config_file.exists()

        loaded = Config.load()
        assert len(loaded.accounts) == 2
        assert loaded.accounts[0].label == "personal"
        assert loaded.accounts[1].source == DataSource.CODEXBAR_HTTP
        assert loaded.notifications.ntfy_topic == "test-topic"
        assert loaded.poll_interval_minutes == 5

    def test_notification_enabled_backends_roundtrip(self):
        restored = NotifyConfig.from_dict(
            {"enabled": True, "enabled_backends": ["telegram", "ntfy", "telegram"]}
        )

        assert restored.enabled_backends == ["telegram", "ntfy"]
        assert restored.to_dict()["enabled_backends"] == ["telegram", "ntfy"]

    def test_notification_policy_roundtrip(self):
        restored = NotifyConfig.from_dict({"enabled": True, "policy": "errors"})

        assert restored.policy == "errors"
        assert restored.to_dict()["policy"] == "errors"
        assert NotifyConfig.from_dict({"policy": "noisy"}).policy == "all"

    def test_telegram_remote_enabled_roundtrips(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(telegram_remote_enabled=True).save()

        loaded = Config.load()

        assert loaded.telegram_remote_enabled is True
        assert json.loads(config_file.read_text())["telegram_remote_enabled"] is True

    def test_codex_surface_retry_backoff_roundtrips(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(codex_surface_retry_backoff_seconds=42).save()

        loaded = Config.load()

        assert loaded.codex_surface_retry_backoff_seconds == 42

    def test_codex_burst_ladder_config_roundtrips(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_gap_seconds=12,
        ).save()

        loaded = Config.load()

        assert loaded.codex_burst_ladder_enabled is True
        assert loaded.codex_burst_ladder_gap_seconds == 12
        assert loaded.codex_burst_ladder_surface_order == []
        assert loaded.codex_fire_all_surfaces is True
        assert loaded.codex_fire_all_surface_gap_seconds == 12

    def test_codex_burst_ladder_surface_order_roundtrips(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(codex_burst_ladder_surface_order=["repo", "legacy"]).save()

        loaded = Config.load()

        assert loaded.codex_burst_ladder_surface_order == ["repo", "legacy"]
        assert loaded.codex_fire_all_surface_order == ["repo", "legacy"]

    def test_legacy_codex_fire_all_config_migrates_to_burst_ladder(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "codex_fire_all_surfaces": True,
                    "codex_fire_all_surface_gap_seconds": 30,
                    "codex_fire_all_surface_order": ["repo", "legacy"],
                }
            )
            + "\n"
        )

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        loaded = Config.load()

        assert loaded.codex_burst_ladder_enabled is True
        assert loaded.codex_burst_ladder_gap_seconds == 90
        assert loaded.codex_burst_ladder_surface_order == ["repo", "legacy"]
        assert loaded.codex_fire_all_surfaces is True
        assert loaded.codex_fire_all_surface_gap_seconds == 90

    def test_legacy_fire_all_gap_30_loads_as_burst_ladder_default(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "codex_fire_all_surfaces": True,
                    "codex_fire_all_surface_gap_seconds": 30,
                }
            )
            + "\n"
        )

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        loaded = Config.load()

        assert loaded.codex_burst_ladder_enabled is True
        assert loaded.codex_burst_ladder_gap_seconds == 90
        assert loaded.codex_fire_all_surfaces is True
        assert loaded.codex_fire_all_surface_gap_seconds == 90

    def test_legacy_fire_all_gap_90_loads_as_explicit_burst_ladder_gap(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "codex_fire_all_surfaces": True,
                    "codex_fire_all_surface_gap_seconds": 90,
                }
            )
            + "\n"
        )

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        loaded = Config.load()

        assert loaded.codex_burst_ladder_enabled is True
        assert loaded.codex_burst_ladder_gap_seconds == 90
        assert loaded.codex_fire_all_surfaces is True
        assert loaded.codex_fire_all_surface_gap_seconds == 90

    def test_modern_burst_ladder_gap_30_roundtrips_exactly(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(
            codex_burst_ladder_enabled=True,
            codex_burst_ladder_gap_seconds=30,
        ).save()

        loaded = Config.load()

        assert loaded.codex_burst_ladder_enabled is True
        assert loaded.codex_burst_ladder_gap_seconds == 30
        assert loaded.codex_fire_all_surfaces is True
        assert loaded.codex_fire_all_surface_gap_seconds == 30

    def test_codex_fire_all_surface_order_rejects_unknown_names(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"codex_burst_ladder_surface_order": ["repo", "bad-surface"]}\n')

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        with pytest.raises(StateFileError) as exc:
            Config.load()

        assert "unknown Codex surface" in str(exc.value)

    def test_codex_fire_all_surface_order_rejects_duplicates(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"codex_burst_ladder_surface_order": ["repo", "repo"]}\n')

        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        with pytest.raises(StateFileError) as exc:
            Config.load()

        assert "duplicate Codex surface" in str(exc.value)

    def test_load_missing_file(self, tmp_path, monkeypatch):
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", tmp_path / "nope.json")

        config = Config.load()
        assert config.accounts == []

    def test_old_config_defaults_schedule_disabled(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"poll_interval_minutes": 5}\n')
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        config = Config.load()

        assert config.schedule.enabled is False
        assert config.schedule.accounts == {}
        assert config.schedule.scheduling_target == "auto"

    def test_schedule_config_roundtrips(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".tokenkick"
        config_file = config_dir / "config.json"
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        Config(
            schedule=ScheduleConfig(
                enabled=True,
                timezone="Europe/Berlin",
                scheduling_target="session",
                default=WorkSchedule(enabled=True, weekdays="09:00-17:00"),
                accounts={
                    "personal": WorkSchedule(
                        enabled=True,
                        weekdays="14:00-21:00",
                        weekends="10:00-16:00",
                    )
                },
                usable_session_tier_defaults={"plus": 90, "pro_5x": 240},
            )
        ).save()

        loaded = Config.load()

        assert loaded.schedule.enabled is True
        assert loaded.schedule.timezone == "Europe/Berlin"
        assert loaded.schedule.scheduling_target == "session"
        assert loaded.schedule.default.weekdays == "09:00-17:00"
        assert loaded.schedule.accounts["personal"].weekdays == "14:00-21:00"
        assert loaded.schedule.accounts["personal"].weekends == "10:00-16:00"
        assert loaded.schedule.usable_session_tier_defaults == {"plus": 90, "pro_5x": 240}

    def test_invalid_schedule_target_defaults_to_auto(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"poll_interval_minutes": 5, "schedule": {"enabled": true, "scheduling_target": "weird"}}\n'
        )
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        config = Config.load()

        assert config.schedule.scheduling_target == "auto"

    def test_malformed_json_reports_state_file_error(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"accounts": [\n')
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        with pytest.raises(StateFileError) as exc_info:
            Config.load()

        message = str(exc_info.value)
        assert "TokenKick config is not valid JSON." in message
        assert str(config_file) in message
        assert "Repair the JSON" in message

    def test_unknown_account_source_reports_state_file_error(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"accounts": [{"label": "personal", "source": "future-source"}]}\n'
        )
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        with pytest.raises(StateFileError) as exc_info:
            Config.load()

        message = str(exc_info.value)
        assert "invalid account entry" in message
        assert "accounts[0]" in message
        assert "future-source" in message

    def test_unknown_account_fields_do_not_break_config_load(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"accounts": [{"label": "personal", "source": "manual", "future_field": true}]}\n'
        )
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        config = Config.load()

        assert config.accounts[0].label == "personal"

    def test_codex_home_alias_loads_as_provider_home(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"accounts": [{"label": "work", "provider": "codex", '
            '"source": "codex-direct", "codex_home": "/tmp/work-codex"}]}\n'
        )
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "CONFIG_FILE", config_file)

        config = Config.load()

        assert config.accounts[0].provider_home == "/tmp/work-codex"


def test_account_key_string_matches_phantom_identity():
    account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEXBAR_CLI,
        codexbar_account="Personal@Example.Test",
    )

    assert account_key_string(account) == "account|codex|personal@example.test"


def test_config_roundtrip_preserves_migration_flags(tmp_path, monkeypatch):
    import tokenkick.models as models

    monkeypatch.setattr(models, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models, "CONFIG_FILE", tmp_path / "config.json")
    Config(migrations={"v0.4-direct-sources": True}).save()

    restored = Config.load()

    assert restored.migrations == {"v0.4-direct-sources": True}


def test_account_key_string_prefers_direct_identity():
    account = AccountConfig(
        label="personal",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_provider_id="acct_123",
        identity_email="personal@example.test",
        codexbar_account="personal@example.test",
    )

    assert account_key_string(account) == "identity|codex|acct_123"


def test_claude_account_key_includes_org_identity():
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="account-uuid",
        identity_org_id="org-uuid",
    )

    assert account_key_string(account) == "identity|claude|org-uuid:account-uuid"


class TestKickEvent:
    def test_roundtrip(self):
        event = KickEvent(label="test", timestamp=1000.0, success=True)
        d = event.to_dict()
        restored = KickEvent.from_dict(d)
        assert restored.label == "test"
        assert restored.timestamp == 1000.0
        assert restored.success is True
        assert restored.confirmed is True
        assert restored.kind == "kick"

    def test_to_dict_includes_local_timestamp(self):
        event = KickEvent(label="test", timestamp=1000.0, success=True)
        assert event.to_dict()["timestamp_local"] == format_local_timestamp(1000.0)

    def test_from_dict_defaults_new_history_fields(self):
        restored = KickEvent.from_dict({"label": "old", "timestamp": 1000.0, "success": True})

        assert restored.confirmed is True
        assert restored.kind == "kick"
        assert restored.codex_surface is None
        assert restored.evidence_response is None

    def test_roundtrip_codex_evidence_fields(self):
        event = KickEvent(
            label="codex",
            timestamp=1000.0,
            success=True,
            codex_surface="repo",
            codex_attempt=2,
            codex_max_attempts=3,
            evidence_response=True,
            evidence_tokens=False,
            evidence_provider_moved=True,
            post_kick_status="moved",
        )

        restored = KickEvent.from_dict(event.to_dict())

        assert restored.codex_surface == "repo"
        assert restored.codex_attempt == 2
        assert restored.codex_max_attempts == 3
        assert restored.evidence_response is True
        assert restored.evidence_tokens is False
        assert restored.evidence_provider_moved is True
        assert restored.post_kick_status == "moved"

    def test_history_partial_line_reports_state_file_error(self, tmp_path, monkeypatch):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text('{"label": "ok", "timestamp": 1000.0, "success": true}\n{"label":')
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)

        with pytest.raises(StateFileError) as exc_info:
            load_kick_history(limit=20)

        message = str(exc_info.value)
        assert "TokenKick history contains malformed JSON." in message
        assert f"{history_file}:2" in message
        assert "Repair or remove the malformed history line" in message

    def test_history_unknown_fields_are_ignored_for_forward_compatibility(
        self, tmp_path, monkeypatch
    ):
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"label": "ok", "timestamp": 1000.0, "success": true, "future_field": "future"}\n'
        )
        import tokenkick.models as models_mod
        monkeypatch.setattr(models_mod, "HISTORY_FILE", history_file)

        events = load_kick_history(limit=20)

        assert events[0].label == "ok"


def test_format_notification_timestamp_is_shorter_than_history_timestamp():
    ts = format_notification_timestamp(time.time())

    assert len(ts) < len(format_local_timestamp(time.time()))


def _rediscovery_classification_groups():
    from tokenkick.models import (
        ACCOUNT_DISCOVERY_OWNED_FIELDS,
        ACCOUNT_LABEL_FIELDS,
        ACCOUNT_USER_OWNED_DISCOVERY_FALLBACK_FIELDS,
        ACCOUNT_USER_OWNED_FIELDS,
    )

    return {
        "discovery_owned": ACCOUNT_DISCOVERY_OWNED_FIELDS,
        "user_owned": ACCOUNT_USER_OWNED_FIELDS,
        "user_owned_discovery_fallback": ACCOUNT_USER_OWNED_DISCOVERY_FALLBACK_FIELDS,
        "label": ACCOUNT_LABEL_FIELDS,
    }


def test_account_config_rediscovery_field_inventory_is_complete():
    from dataclasses import fields as dataclass_fields
    from itertools import combinations

    groups = _rediscovery_classification_groups()
    all_fields = {field.name for field in dataclass_fields(AccountConfig)}
    union = set().union(*groups.values())

    assert all_fields - union == set(), (
        "Unclassified AccountConfig fields: "
        f"{sorted(all_fields - union)}. Every field must be classified as "
        "discovery-owned, user-owned, user-owned-with-discovery-fallback, or "
        "label in tokenkick/models.py so rediscovery cannot silently reset it."
    )
    assert union - all_fields == set(), (
        f"Stale classification entries: {sorted(union - all_fields)}."
    )
    for (name_a, group_a), (name_b, group_b) in combinations(groups.items(), 2):
        assert not (group_a & group_b), (
            f"Fields classified in both {name_a} and {name_b}: "
            f"{sorted(group_a & group_b)}"
        )


def _merge_existing_account() -> AccountConfig:
    return AccountConfig(
        label="codex (work)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        weekly_auto_kick=False,
        session_auto_kick=True,
        visible=False,
        notifications_enabled=False,
        notification_backends=["ntfy"],
        codexbar_provider="codex",
        codexbar_url="http://old.example.test",
        codexbar_account="old@example.test",
        session_path="/old/home/sessions",
        provider_home="/old/home",
        identity_provider_id="acct-old",
        identity_email="old@example.test",
        identity_org_id="org-old",
        label_origin="user",
        status_probe_enabled=True,
        direct_usage_enabled=False,
        codex_rate_limit_id="codex",
        codex_rate_limit_name="old-name",
        kick_model="model-user",
        plan_tier="pro",
        usable_session_minutes=150,
        orchestration_role="backup",
        weekly_reserve_threshold_percent=70,
        codex_surface_auto_demote=True,
        codex_surface_demote_after_strong_clusters=7,
        codex_surface_demote_min_active_surfaces=3,
        codex_surface_demote_min_kept_anchor_rate=0.9,
        codex_surface_demote_measurement_clusters=25,
        codex_surface_rescue_cooldown_strong_clusters=30,
        codex_surface_force_keep=["legacy"],
        codex_surface_force_prune=["repo"],
    )


def _merge_discovered_account_fixture() -> AccountConfig:
    return AccountConfig(
        label="codex (work-renamed)",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        auto_kick=False,
        weekly_auto_kick=True,
        session_auto_kick=False,
        visible=True,
        notifications_enabled=True,
        notification_backends=None,
        codexbar_provider="codex-new",
        codexbar_url="http://new.example.test",
        codexbar_account="new@example.test",
        session_path="/new/home/sessions",
        provider_home="/new/home",
        identity_provider_id="acct-new",
        identity_email="new@example.test",
        identity_org_id="org-new",
        label_origin="auto",
        status_probe_enabled=False,
        direct_usage_enabled=True,
        codex_rate_limit_id="codex_bengalfox",
        codex_rate_limit_name="new-name",
        kick_model="model-discovered",
        plan_tier="spark",
        usable_session_minutes=120,
        orchestration_role="normal",
        weekly_reserve_threshold_percent=40,
        codex_surface_auto_demote=False,
        codex_surface_demote_after_strong_clusters=5,
        codex_surface_demote_min_active_surfaces=2,
        codex_surface_demote_min_kept_anchor_rate=0.95,
        codex_surface_demote_measurement_clusters=20,
        codex_surface_rescue_cooldown_strong_clusters=20,
        codex_surface_force_keep=[],
        codex_surface_force_prune=[],
    )


def test_merge_discovered_account_follows_field_classification():
    from tokenkick.models import merge_discovered_account

    groups = _rediscovery_classification_groups()
    existing = _merge_existing_account()
    discovered = _merge_discovered_account_fixture()
    # Guard against vacuous assertions: every classified field must differ
    # between the two fixture accounts.
    for name in set().union(*groups.values()):
        assert getattr(existing, name) != getattr(discovered, name), (
            f"fixture accounts must differ in field {name!r}"
        )

    merged = merge_discovered_account(existing, discovered)

    for name in groups["discovery_owned"]:
        assert getattr(merged, name) == getattr(discovered, name), name
    for name in groups["user_owned"] | groups["label"]:
        assert getattr(merged, name) == getattr(existing, name), name
    for name in groups["user_owned_discovery_fallback"]:
        assert getattr(merged, name) == getattr(existing, name), name


def test_merge_discovered_account_fallback_fields_use_discovery_when_unset():
    from dataclasses import replace

    from tokenkick.models import merge_discovered_account

    groups = _rediscovery_classification_groups()
    existing = replace(
        _merge_existing_account(),
        kick_model=None,
        plan_tier=None,
        usable_session_minutes=None,
        weekly_reserve_threshold_percent=None,
    )
    discovered = _merge_discovered_account_fixture()

    merged = merge_discovered_account(existing, discovered)

    for name in groups["user_owned_discovery_fallback"]:
        assert getattr(merged, name) == getattr(discovered, name), name


def test_merge_discovered_account_can_take_discovered_label():
    from tokenkick.models import merge_discovered_account

    existing = _merge_existing_account()
    discovered = _merge_discovered_account_fixture()

    merged = merge_discovered_account(existing, discovered, preserve_label=False)

    assert merged.label == discovered.label
    assert merged.label_origin == discovered.label_origin
