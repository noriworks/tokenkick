"""Tests for data source parsing and state determination."""

import json
import signal
import subprocess
import threading

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenkick.antigravity import (
    antigravity_cli_binary,
    antigravity_cli_detected,
    is_antigravity_language_server,
    parse_lsof_listening_ports,
    parse_process_line,
    read_antigravity_cli_identity,
)
from tokenkick.codexbar_source import (
    CODEXBAR_FUTURE_SKEW_TOLERANCE_SECONDS,
    CODEXBAR_SNAPSHOT_FUTURE_MESSAGE,
    CODEXBAR_TIMEOUT_SECONDS,
    CODEXBAR_NOT_INSTALLED_MESSAGE,
    CODEXBAR_SNAPSHOT_STALE_MESSAGE,
    _fetch_codexbar_cli,
    _fetch_codexbar_http,
    _parse_codexbar_json,
    _run_codexbar_json,
)
from tokenkick.direct import CODEX_PROVIDER_USAGE_SOURCE_DETAIL, ClaudeAuthStatus, DirectIdentity
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    ClaudeProbeContext,
    ClaudeProbeError,
    ClaudeProbeErrorCategory,
    DataSource,
)
from tokenkick.sources import (
    CLAUDE_CLI_USAGE_SOURCE_DETAIL,
    ANTIGRAVITY_SOURCE_DETAIL,
    _determine_state,
    _fetch_antigravity_cli,
    _fetch_antigravity_direct,
    _parse_codex_appserver_ratelimits,
    _fetch_claude_direct,
    _fetch_claude_cli_usage,
    _fetch_codex_direct,
    _fetch_codex_session_file,
    _ClaudeUsageCaptureTimeout,
    _capture_claude_usage_pty,
    _find_latest_rate_limit,
    _nested_get,
    _parse_antigravity_user_status,
    _parse_claude_usage_output,
    _parse_session_rate_limit,
    _claude_cached_direct_status_usable,
    _claude_usage_next_pty_input,
    _claude_usage_capture_has_complete_window_data,
    _claude_usage_output_looks_relevant,
    _redact_claude_usage_raw,
    _terminate_process_group,
    claude_cli_usage_refresh_allowed,
    fetch_status,
    polling_pass_cache,
)


NOW = 1_779_000_000


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 5, 24, 10, 3, tzinfo=tz)


def _mock_codex_identity(monkeypatch, *, account_id: str = "acct_123", email: str | None = None):
    monkeypatch.setattr(
        "tokenkick.sources.read_codex_identity",
        lambda _home: DirectIdentity(
            provider="codex",
            provider_account_id=account_id,
            email=email,
        ),
    )


def _claude_account(status_probe_enabled: bool = False) -> AccountConfig:
    return AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="claude-account",
        identity_org_id="claude-org",
        identity_email="claude@example.test",
        status_probe_enabled=status_probe_enabled,
    )


def _active_claude_identity():
    return DirectIdentity(
        "claude",
        provider_account_id="claude-account",
        organization_id="claude-org",
        email="claude@example.test",
    )


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _fake_id_token(email: str) -> str:
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def _antigravity_codexbar_entry(*, missing_id: str | None = None) -> dict:
    windows = [
        {
            "id": "antigravity-quota-summary-gemini-5h",
            "title": "Gemini Models Five Hour Limit",
            "window": {
                "usedPercent": 66.22157,
                "resetsAt": "2026-05-23T06:18:33Z",
                "windowMinutes": 300,
            },
        },
        {
            "id": "antigravity-quota-summary-gemini-weekly",
            "title": "Gemini Models Weekly Limit",
            "window": {
                "usedPercent": 51.460007,
                "resetsAt": "2026-05-24T09:18:33Z",
                "windowMinutes": 10080,
            },
        },
        {
            "id": "antigravity-quota-summary-3p-5h",
            "title": "Claude and GPT models Five Hour Limit",
            "window": {
                "usedPercent": 0,
                "resetsAt": "2026-05-23T09:18:33Z",
                "windowMinutes": 300,
            },
        },
        {
            "id": "antigravity-quota-summary-3p-weekly",
            "title": "Claude and GPT models Weekly Limit",
            "window": {
                "usedPercent": 4.16723,
                "resetsAt": "2026-05-24T08:18:33Z",
                "windowMinutes": 10080,
            },
        },
    ]
    if missing_id is not None:
        windows = [window for window in windows if window["id"] != missing_id]
    return {
        "provider": "antigravity",
        "account": "dev@example.test",
            "usage": {
                "updatedAt": "2026-05-23T04:18:33Z",
                "extraRateWindows": windows,
                "primary": windows[0]["window"],
                "secondary": windows[-1]["window"],
            },
        }


def _codexbar_error_run(*_args, **_kwargs):
    return SimpleNamespace(
        returncode=1,
        stdout=json.dumps([{"provider": "codex", "error": {"message": "provider failed"}}]),
        stderr="",
    )


def test_manual_source_points_to_real_setup_path():
    status = fetch_status(AccountConfig(label="manual", source=DataSource.MANUAL))

    assert status.state == AccountState.UNKNOWN
    assert "tk setup" in status.error
    assert "tk touch" not in status.error


def test_read_antigravity_cli_identity_reads_active_email_only(tmp_path):
    login_file = tmp_path / ".gemini" / "google_accounts.json"
    login_file.parent.mkdir()
    login_file.write_text(
        json.dumps(
            {
                "active": "dev@example.test",
                "old": [{"email": "old@example.test", "refresh_token": "secret"}],
            }
        )
    )

    assert read_antigravity_cli_identity(tmp_path) == "dev@example.test"


def test_read_antigravity_cli_identity_decodes_oauth_id_token_fallback(tmp_path):
    creds_file = tmp_path / ".gemini" / "oauth_creds.json"
    creds_file.parent.mkdir()
    creds_file.write_text(
        json.dumps(
            {
                "id_token": _fake_id_token("oauth@example.test"),
                "access_token": "secret",
                "refresh_token": "secret",
            }
        )
    )

    assert read_antigravity_cli_identity(tmp_path) == "oauth@example.test"


def test_antigravity_cli_binary_finds_user_local_bin_when_path_missing(monkeypatch, tmp_path):
    binary = tmp_path / ".local" / "bin" / "agy"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr("tokenkick.antigravity.shutil.which", lambda _name: None)

    assert antigravity_cli_binary(tmp_path) == str(binary)


def test_antigravity_cli_detected_accepts_local_marker_when_binary_missing(monkeypatch, tmp_path):
    marker = tmp_path / ".gemini" / "antigravity-cli"
    marker.mkdir(parents=True)
    monkeypatch.setattr("tokenkick.antigravity.shutil.which", lambda _name: None)

    assert antigravity_cli_binary(tmp_path) is None
    assert antigravity_cli_detected(tmp_path) is True


def test_fetch_antigravity_cli_returns_complete_local_windows(monkeypatch):
    quota_status = AccountStatus(
        label="antigravity",
        state=AccountState.ACTIVE,
        quota_windows=[
            {
                "id": window["id"],
                "title": window["title"],
                "family": "gemini" if "gemini" in window["id"] else "claude_gpt",
                "window_kind": "weekly" if "weekly" in window["id"] else "session",
                "used_percent": window["window"]["usedPercent"],
                "resets_at": _epoch(window["window"]["resetsAt"]),
                "resets_in_seconds": 3600,
                "window_minutes": window["window"]["windowMinutes"],
                "source": ANTIGRAVITY_SOURCE_DETAIL,
            }
            for window in _antigravity_codexbar_entry()["usage"]["extraRateWindows"]
        ],
    )
    monkeypatch.setattr("tokenkick.sources._fetch_antigravity_direct", lambda _account: quota_status)

    status = _fetch_antigravity_cli(
        AccountConfig(
            label="antigravity",
            provider="antigravity",
            source=DataSource.ANTIGRAVITY_CLI,
        )
    )

    assert status.source_detail == "antigravity-cli"
    assert status.quota_windows is not None
    assert len(status.quota_windows) == 4


def test_fetch_antigravity_cli_rejects_local_api_identity_mismatch(monkeypatch):
    quota_status = AccountStatus(
        label="antigravity",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        source_detail=ANTIGRAVITY_SOURCE_DETAIL,
    )
    setattr(quota_status, "_antigravity_identity_email", "desktop@example.test")
    monkeypatch.setattr("tokenkick.sources._fetch_antigravity_direct", lambda _account: quota_status)

    status = _fetch_antigravity_cli(
        AccountConfig(
            label="antigravity",
            provider="antigravity",
            source=DataSource.ANTIGRAVITY_CLI,
            identity_email="cli@example.test",
        )
    )

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == "antigravity-cli"
    assert "identity mismatch" in status.error
    assert "cli@example.test" in status.error
    assert "desktop@example.test" in status.error


def test_fetch_antigravity_cli_requires_verified_local_api_identity(monkeypatch):
    quota_status = AccountStatus(
        label="antigravity",
        state=AccountState.ACTIVE,
        used_percent=12.0,
        source_detail=ANTIGRAVITY_SOURCE_DETAIL,
    )
    monkeypatch.setattr("tokenkick.sources._fetch_antigravity_direct", lambda _account: quota_status)

    status = _fetch_antigravity_cli(
        AccountConfig(
            label="antigravity",
            provider="antigravity",
            source=DataSource.ANTIGRAVITY_CLI,
            identity_email="cli@example.test",
        )
    )

    assert status.state == AccountState.UNKNOWN
    assert "could not verify" in status.error


def test_fetch_antigravity_cli_fails_closed_without_local_quota_api(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.sources._fetch_antigravity_direct",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.UNKNOWN,
            error="Antigravity language server not detected.",
            source_detail=ANTIGRAVITY_SOURCE_DETAIL,
        ),
    )

    status = fetch_status(
        AccountConfig(
            label="antigravity",
            provider="antigravity",
            source=DataSource.ANTIGRAVITY_CLI,
        )
    )

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == "antigravity-cli"
    assert "does not expose a non-interactive quota command" in status.error


def test_codexbar_json_uses_refresh_timeout(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

    data, error = _run_codexbar_json(["codexbar", "--format", "json"])

    assert data == {}
    assert error is None
    assert calls[0][1]["timeout"] == CODEXBAR_TIMEOUT_SECONDS
    assert CODEXBAR_TIMEOUT_SECONDS == 20


def test_codexbar_missing_binary_uses_user_facing_message(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

    data, error = _run_codexbar_json(["codexbar", "--format", "json"])

    assert data is None
    assert error == CODEXBAR_NOT_INSTALLED_MESSAGE


def test_fetch_status_populates_metadata_for_live_codexbar_cli(monkeypatch, tmp_path):
    observed = "2026-05-23T04:18:33Z"
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "provider": "gemini",
                        "usage": {
                            "secondary": {
                                "usedPercent": 1,
                                "windowMinutes": 1440,
                                "resetsAt": "2026-05-24T04:18:33Z",
                            }
                        },
                    }
                ]
            ),
            stderr="",
        ),
    )
    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )

    status = fetch_status(account)

    assert status.observed_at == observed
    assert status.source_detail == "codexbar-cli"
    assert status.state == AccountState.ACTIVE


def test_parse_antigravity_user_status_selects_codexbar_model_order(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))

    status = _parse_antigravity_user_status(
        "antigravity",
        {
            "code": "OK",
            "userStatus": {
                "email": "dev@example.test",
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Gemini Flash",
                            "modelOrAlias": {"model": "gemini-flash"},
                            "quotaInfo": {
                                "remainingFraction": 0.75,
                                "resetTime": "2026-05-23T06:18:33Z",
                            },
                        },
                        {
                            "label": "Claude Sonnet",
                            "modelOrAlias": {"model": "claude-sonnet"},
                            "quotaInfo": {
                                "remainingFraction": 0.6,
                                "resetTime": "2026-05-23T05:18:33Z",
                            },
                        },
                        {
                            "label": "Gemini Pro Low",
                            "modelOrAlias": {"model": "gemini-pro-low"},
                            "quotaInfo": {
                                "remainingFraction": 0.9,
                                "resetTime": "2026-05-23T07:18:33Z",
                            },
                        },
                    ]
                },
            },
        },
    )

    assert status.source_detail == ANTIGRAVITY_SOURCE_DETAIL
    assert status.state == AccountState.ACTIVE
    assert status.used_percent == 40.0
    assert status.resets_in_seconds == 3600
    assert status.session_used_percent == 10.0
    assert status.session_resets_in_seconds == 10800
    assert getattr(status, "_antigravity_identity_email") == "dev@example.test"


def test_parse_antigravity_user_status_accepts_named_quota_windows(monkeypatch):
    monkeypatch.setattr("tokenkick.source_utils.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))

    status = _parse_antigravity_user_status(
        "antigravity",
        {
            "code": "OK",
            "userStatus": {
                "extraRateWindows": _antigravity_codexbar_entry()["usage"]["extraRateWindows"],
                "cascadeModelConfigData": {"clientModelConfigs": []},
            },
        },
    )

    assert status.source_detail == ANTIGRAVITY_SOURCE_DETAIL
    assert status.used_percent == 66.22157
    assert status.quota_windows is not None
    assert {window["source"] for window in status.quota_windows} == {ANTIGRAVITY_SOURCE_DETAIL}


def test_parse_antigravity_lsof_ports_deduplicates_and_sorts():
    output = """
COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
lang    42  reserve 10u IPv4 0      0t0 TCP 127.0.0.1:61234 (LISTEN)
lang    42  reserve 11u IPv4 0      0t0 TCP 127.0.0.1:61233 (LISTEN)
lang    42  reserve 12u IPv6 0      0t0 TCP [::1]:61234 (LISTEN)
"""

    assert parse_lsof_listening_ports(output) == [61233, 61234]


def test_antigravity_process_helpers_identify_scoped_language_server():
    line = "123 /Applications/Antigravity.app/language_server_macos --app_data_dir /tmp/Antigravity"

    assert parse_process_line(line) == (
        123,
        "/Applications/Antigravity.app/language_server_macos --app_data_dir /tmp/Antigravity",
    )
    assert is_antigravity_language_server(line)
    assert not is_antigravity_language_server("123 /tmp/language_server --other flag")


def test_antigravity_direct_failure_falls_back_to_codexbar(monkeypatch, tmp_path):
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="antigravity",
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_antigravity_direct",
        lambda _account: AccountStatus(
            label="antigravity",
            state=AccountState.UNKNOWN,
            error="Antigravity language server not detected.",
            source_detail=ANTIGRAVITY_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr(
        "tokenkick.codexbar_source._load_codexbar_provider_json",
        lambda _provider: (None, "CodexBar provider command unavailable"),
    )
    monkeypatch.setattr(
        "tokenkick.codexbar_source._load_codexbar_legacy_json",
        lambda: (
            [
                {
                    "provider": "antigravity",
                    "usage": {
                        "primary": {
                            "usedPercent": 12,
                            "resetsAt": "2026-05-23T06:18:33Z",
                        }
                    },
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))

    status = _fetch_codexbar_cli(account)

    assert status.source_detail == "codexbar-cli"
    assert status.used_percent == 12.0


def test_antigravity_codexbar_complete_buckets_beat_direct_summary(monkeypatch, tmp_path):
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="antigravity",
    )
    monkeypatch.setattr("tokenkick.source_utils.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr(
        "tokenkick.codexbar_source._load_codexbar_provider_json",
        lambda _provider: ([_antigravity_codexbar_entry()], None),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_antigravity_direct",
        lambda account: AccountStatus(
            label=account.label,
            state=AccountState.ACTIVE,
            used_percent=12.0,
            resets_in_seconds=3600,
            source_detail=ANTIGRAVITY_SOURCE_DETAIL,
        ),
    )

    status = _fetch_codexbar_cli(account)

    assert status.source_detail == "codexbar-cli"
    assert status.used_percent == 66.22157
    assert status.quota_windows is not None
    assert len(status.quota_windows) == 4


def test_antigravity_direct_complete_buckets_beat_codexbar(monkeypatch, tmp_path):
    account = AccountConfig(
        label="antigravity",
        provider="antigravity",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="antigravity",
    )
    direct_status = _parse_antigravity_user_status(
        "antigravity",
        {
            "code": "OK",
            "userStatus": {
                "extraRateWindows": _antigravity_codexbar_entry()["usage"]["extraRateWindows"],
                "cascadeModelConfigData": {"clientModelConfigs": []},
            },
        },
    )
    monkeypatch.setattr("tokenkick.sources._fetch_antigravity_direct", lambda _account: direct_status)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr(
        "tokenkick.codexbar_source._load_codexbar_provider_json",
        lambda _provider: pytest.fail("CodexBar should not run when direct buckets are complete"),
    )

    status = _fetch_codexbar_cli(account)

    assert status.source_detail == ANTIGRAVITY_SOURCE_DETAIL
    assert status.quota_windows is not None
    assert len(status.quota_windows) == 4


def test_antigravity_direct_probe_uses_csrf_lsof_and_unleash(monkeypatch):
    account = AccountConfig(label="antigravity", provider="antigravity")
    calls: list[tuple] = []

    def fake_run(cmd, **_kwargs):
        calls.append(tuple(cmd))
        if cmd[:3] == ["ps", "-ax", "-o"]:
            return SimpleNamespace(
                stdout=(
                    "123 /Applications/Antigravity.app/Contents/Resources/bin/language_server "
                    "--standalone --override_ide_name antigravity --https_server_port 0 "
                    "--csrf_token token-123 --app_data_dir antigravity\n"
                )
            )
        if "-p" in cmd:
            return SimpleNamespace(stdout="lang 123 reserve TCP 127.0.0.1:3456 (LISTEN)\n")
        raise AssertionError(cmd)

    responses = [
        {},
        {
            "userStatus": {
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Claude",
                            "modelOrAlias": {"model": "claude"},
                            "quotaInfo": {"remainingFraction": 0.8},
                        }
                    ]
                }
            }
        },
    ]

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)
    monkeypatch.setattr(
        "tokenkick.sources._antigravity_request_json",
        lambda scheme, port, path, body, csrf_token: responses.pop(0),
    )

    status = _fetch_antigravity_direct(account)

    assert ("ps", "-ax", "-o", "pid=,command=") in calls
    assert any("lsof" in call[0] and "-p" in call for call in calls)
    assert status.source_detail == ANTIGRAVITY_SOURCE_DETAIL
    assert status.used_percent == 20.0


def test_antigravity_direct_permission_error_is_unknown(monkeypatch):
    account = AccountConfig(label="antigravity", provider="antigravity")

    def blocked_ps(*_args, **_kwargs):
        raise PermissionError("blocked")

    monkeypatch.setattr("tokenkick.sources.subprocess.run", blocked_ps)

    status = _fetch_antigravity_direct(account)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == ANTIGRAVITY_SOURCE_DETAIL
    assert "blocked by system permissions" in status.error


def test_fetch_status_populates_metadata_for_session_file(monkeypatch, tmp_path):
    observed = "2026-05-23T04:18:33Z"
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": observed,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 0,
                            "window_minutes": 300,
                            "resets_at": "2026-05-23T09:18:33Z",
                        },
                        "secondary": {
                            "used_percent": 2,
                            "window_minutes": 10080,
                            "resets_at": "2026-05-30T04:18:33Z",
                        },
                    },
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed))
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path=str(sessions_dir),
    )

    status = fetch_status(account)

    assert status.observed_at == observed
    assert status.source_detail == "codex-session-file"
    assert status.state == AccountState.ACTIVE


def test_parse_claude_usage_output_supports_used_and_resets():
    status = _parse_claude_usage_output(
        "claude",
        """
        Settings: Usage
        Current session
        37% used
        Resets in 2h 15m
        Current week
        12% used
        Resets in 4d
        """,
        now=NOW,
    )

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.used_percent == 12.0
    assert status.window_minutes == 10080
    assert status.session_used_percent == 37.0
    assert status.session_resets_in_seconds == 8100


def test_parse_claude_usage_output_converts_left_to_used():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        80% left
        Resets in 1h 0m
        Current week (all models)
        65% remaining
        Resets in 6h 30m
        """,
        now=NOW,
    )

    assert status.used_percent == 35.0
    assert status.session_used_percent == 20.0
    assert status.resets_in_seconds == 23400


def test_parse_claude_usage_output_supports_compact_reset_times():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        2% used
        Resets12:20pm(Europe/Berlin)
        Current week (all models)
        57% used
        ResetsMay29at2pm(Europe/Berlin)
        """,
        now=NOW,
    )

    assert status.session_resets_in_seconds == 13200
    assert status.resets_in_seconds == 1056000


def test_parse_claude_usage_output_marks_weekly_fresh_active_when_session_running():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        0% used
        Resets in 1h 14m
        Current week
        0% used
        Resets in 6d 4h
        """,
        now=NOW,
    )

    assert status.state == AccountState.ACTIVE
    assert status.used_percent == 0.0
    assert status.session_used_percent == 0.0
    assert status.session_resets_in_seconds == 4440


def test_parse_claude_usage_output_keeps_weekly_fresh_when_session_ready():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        0% used
        Resets in 0m
        Current week
        0% used
        Resets in 6d 4h
        """,
        now=NOW,
    )

    assert status.state == AccountState.FRESH


def test_parse_claude_usage_output_allows_enterprise_session_only():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        42% used
        Resets in 30m
        """,
        now=NOW,
    )

    assert status.used_percent == 42.0
    assert status.window_minutes == 300
    assert status.session_window_minutes == 300


def test_parse_claude_usage_output_fails_closed_on_malformed_output():
    status = _parse_claude_usage_output("claude", "Total cost: $0.0000", now=NOW)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert "missing Current session" in status.error


def test_parse_claude_usage_output_fails_closed_on_incomplete_weekly_window():
    status = _parse_claude_usage_output(
        "claude",
        """
        Current session
        0% used
        Resets in 1h 30m
        Current week
        63% used
        """,
        now=NOW,
    )

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert "missing Current week reset" in status.error


def test_parse_claude_usage_output_handles_loading_tui_capture_fixture():
    fixture = Path("test_fixtures/claude_usage_loading_screen.txt").read_text()
    status = _parse_claude_usage_output("claude", fixture, now=NOW)

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.session_used_percent == 0.0
    assert status.used_percent == 24.0


def test_claude_usage_relevant_detection_handles_loading_and_mangled_header():
    assert _claude_usage_output_looks_relevant("Loading usage data...")
    assert _claude_usage_output_looks_relevant("Curret session\n0% used")
    assert not _claude_usage_output_looks_relevant("\x1b7\x1b[r\x1b8")


def test_claude_usage_pty_resends_usage_command_on_welcome_screen():
    welcome_screen = 'ClaudeCode v2.1.152\nWelcome back\nTry "refactor <filepath>"\n? for shortcuts'

    assert (
        _claude_usage_next_pty_input(
            welcome_screen,
            now=2.1,
            start=0.0,
            sent_usage_count=0,
            last_usage_send=0.0,
            welcome_dismissed=False,
            last_welcome_dismiss=0.0,
            last_enter=0.0,
        )
        == b"\r"
    )
    assert (
        _claude_usage_next_pty_input(
            welcome_screen,
            now=3.0,
            start=0.0,
            sent_usage_count=0,
            last_usage_send=0.0,
            welcome_dismissed=True,
            last_welcome_dismiss=2.1,
            last_enter=2.1,
        )
        == b"/usage\r"
    )
    assert (
        _claude_usage_next_pty_input(
            welcome_screen,
            now=4.6,
            start=0.0,
            sent_usage_count=1,
            last_usage_send=3.0,
            welcome_dismissed=True,
            last_welcome_dismiss=2.1,
            last_enter=3.0,
        )
        == b"/usage\r"
    )
    assert (
        _claude_usage_next_pty_input(
            "Loading usage data...",
            now=5.5,
            start=0.0,
            sent_usage_count=1,
            last_usage_send=4.6,
            welcome_dismissed=True,
            last_welcome_dismiss=2.1,
            last_enter=4.6,
        )
        == b"\r"
    )


def test_claude_usage_capture_waits_for_reset_and_weekly_data():
    session_only = """
    Settings: Usage
    Current session
    0% used
    """
    session_with_reset = """
    Settings: Usage
    Current session
    0% used
    Resets in 1h 39m
    """
    complete = """
    Settings: Usage
    Current session
    0% used
    Resets in 1h 39m
    Current week
    63% used
    Resets in 3d 4h
    """

    assert not _claude_usage_capture_has_complete_window_data(session_only)
    assert not _claude_usage_capture_has_complete_window_data(session_with_reset)
    assert _claude_usage_capture_has_complete_window_data(complete)


def test_claude_cli_usage_beats_codexbar_fallback(monkeypatch):
    account = _claude_account()
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: """
        Current session
        10% used
        Resets in 1h
        Current week
        20% used
        Resets in 5h
        """,
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=99.0,
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account)

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.used_percent == 20.0


def test_claude_cli_usage_refresh_allowance_is_thread_local(monkeypatch):
    account = _claude_account()
    usage_calls = []
    statuses = {}

    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr(
        "tokenkick.sources._fetch_claude_cli_usage",
        lambda candidate, context=None: usage_calls.append(candidate.label)
        or AccountStatus(
            label=candidate.label,
            state=AccountState.ACTIVE,
            used_percent=20.0,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=64.0,
            source_detail="codexbar-cli",
        ),
    )

    def fetch_in_background():
        statuses["background"] = _fetch_claude_direct(account)

    with claude_cli_usage_refresh_allowed():
        statuses["foreground"] = _fetch_claude_direct(account)
        thread = threading.Thread(target=fetch_in_background)
        thread.start()
        thread.join()

    assert usage_calls == ["claude"]
    assert statuses["foreground"].source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert statuses["background"].source_detail == "claude-codexbar-fallback"
    assert statuses["background"].used_percent == 64.0


def test_claude_cli_usage_cache_is_thread_local(monkeypatch):
    account = _claude_account()
    captures = []
    statuses = {}

    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")

    def fake_capture(_binary):
        captures.append(threading.current_thread().name)
        weekly_used = 20 if len(captures) == 1 else 33
        return f"""
        Current session
        10% used
        Resets in 1h
        Current week
        {weekly_used}% used
        Resets in 5h
        """

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage", fake_capture)

    with claude_cli_usage_refresh_allowed():
        first = _fetch_claude_cli_usage(account)
        second = _fetch_claude_cli_usage(account)

        def fetch_in_background():
            with claude_cli_usage_refresh_allowed():
                statuses["background"] = _fetch_claude_cli_usage(account)

        thread = threading.Thread(target=fetch_in_background)
        thread.start()
        thread.join()

    assert first.used_percent == 20.0
    assert second.used_percent == 20.0
    assert statuses["background"].used_percent == 33.0
    assert len(captures) == 2


def test_claude_cli_usage_failure_falls_back_to_codexbar(monkeypatch):
    account = _claude_account()
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("tokenkick.sources._capture_claude_usage", lambda _binary: "bad output")
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=64.0,
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account)

    assert status.source_detail == "claude-codexbar-fallback"
    assert status.used_percent == 64.0


def test_claude_cli_usage_failure_survives_codexbar_fallback_timeout(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext()
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: (_ for _ in ()).throw(TimeoutError("Claude CLI /usage timed out.")),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("codexbar", 20)
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert "Claude CLI /usage timed out" in status.error
    assert context.last_direct_probe_error is not None
    assert context.last_direct_probe_error.category == ClaudeProbeErrorCategory.TIMEOUT


def test_claude_cli_usage_fails_fast_when_auth_status_is_logged_out(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext()
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        "tokenkick.sources.claude_auth_status",
        lambda _binary: ClaudeAuthStatus(
            logged_in=False,
            auth_method="none",
            api_provider="firstParty",
            message=(
                "Claude CLI is not logged in. Run `claude auth login --claudeai` "
                "as the same user that runs TokenKick, then run `tk status --refresh`."
            ),
        ),
    )
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: pytest.fail("logged-out Claude should not open /usage"),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_cli_usage(account, context)

    assert status.state == AccountState.UNKNOWN
    assert "claude auth login --claudeai" in status.error
    assert context.last_direct_probe_error is not None
    assert context.last_direct_probe_error.category == ClaudeProbeErrorCategory.NOT_AUTHENTICATED


def test_claude_cli_usage_timeout_records_redacted_raw_capture(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext()
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr("tokenkick.sources.shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: (_ for _ in ()).throw(
            _ClaudeUsageCaptureTimeout("Current session\nuser@example.test\n0% used\n")
        ),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="fallback unavailable",
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.state == AccountState.UNKNOWN
    assert context.last_direct_probe_error is not None
    assert context.last_direct_probe_error.raw is not None
    assert "[email]" in context.last_direct_probe_error.raw
    assert "user@example.test" not in context.last_direct_probe_error.raw


def test_claude_direct_recent_success_reuses_cache_without_probe(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext(
        last_direct_probe_at="2026-05-24T10:00:00Z",
        last_direct_success_at="2026-05-24T10:00:00Z",
        last_direct_success_status=AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=12.0,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.sources.datetime", _FixedDateTime)
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr(
        "tokenkick.sources._capture_claude_usage",
        lambda _binary: (_ for _ in ()).throw(AssertionError("should not probe")),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.used_percent == 12.0


def test_claude_direct_recent_failure_reuses_recent_success(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext(
        last_direct_probe_at="2026-05-24T10:00:00Z",
        last_direct_probe_error=ClaudeProbeError(ClaudeProbeErrorCategory.TIMEOUT, "timeout"),
        last_direct_success_at="2026-05-24T09:45:00Z",
        last_direct_success_status=AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=18.0,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.sources.datetime", _FixedDateTime)
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.used_percent == 18.0


def test_claude_direct_recent_failure_rejects_incomplete_weekly_success(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext(
        last_direct_probe_at="2026-05-24T10:00:00Z",
        last_direct_probe_error=ClaudeProbeError(ClaudeProbeErrorCategory.TIMEOUT, "timeout"),
        last_direct_success_at="2026-05-24T09:45:00Z",
        last_direct_success_status=AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=63.0,
            window_minutes=10080,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.sources.datetime", _FixedDateTime)
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=64.0,
            resets_in_seconds=12_000,
            window_minutes=10080,
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.source_detail == "claude-codexbar-fallback"
    assert status.used_percent == 64.0


def test_claude_cached_direct_status_requires_weekly_reset_anchor():
    status = AccountStatus(
        label="claude",
        state=AccountState.ACTIVE,
        used_percent=63.0,
        window_minutes=10080,
        source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
    )

    assert _claude_cached_direct_status_usable(status) is False


def test_claude_direct_recent_failure_without_recent_success_falls_back(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext(
        last_direct_probe_at="2026-05-24T10:00:00Z",
        last_direct_probe_error=ClaudeProbeError(ClaudeProbeErrorCategory.TIMEOUT, "timeout"),
        last_direct_success_at="2026-05-24T09:00:00Z",
        last_direct_success_status=AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=18.0,
            source_detail=CLAUDE_CLI_USAGE_SOURCE_DETAIL,
        ),
    )
    monkeypatch.setattr("tokenkick.sources.datetime", _FixedDateTime)
    monkeypatch.setattr("tokenkick.sources.read_claude_identity", lambda: _active_claude_identity())
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=64.0,
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.source_detail == "claude-codexbar-fallback"
    assert status.used_percent == 64.0


def test_claude_direct_identity_mismatch_falls_back(monkeypatch):
    account = _claude_account()
    context = ClaudeProbeContext()
    monkeypatch.setattr(
        "tokenkick.sources.read_claude_identity",
        lambda: DirectIdentity("claude", provider_account_id="other", organization_id="claude-org"),
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.ACTIVE,
            used_percent=64.0,
            source_detail="codexbar-cli",
        ),
    )

    with claude_cli_usage_refresh_allowed():
        status = _fetch_claude_direct(account, claude_probe_context=context)

    assert status.source_detail == "claude-codexbar-fallback"
    assert context.last_direct_probe_error.category == ClaudeProbeErrorCategory.IDENTITY_MISMATCH


def test_parse_claude_usage_fixture():
    fixture = Path("test_fixtures/claude_usage_mid_use.txt").read_text()
    status = _parse_claude_usage_output("claude", fixture, now=NOW)

    assert status.source_detail == CLAUDE_CLI_USAGE_SOURCE_DETAIL
    assert status.used_percent == 20.0
    assert status.session_used_percent == 9.0


def test_claude_usage_pty_retries_loading_only_capture(monkeypatch):
    calls = []

    def fake_capture(_binary: str, *, timeout_seconds: float) -> str:
        calls.append(timeout_seconds)
        if len(calls) == 1:
            raise _ClaudeUsageCaptureTimeout("Settings: Usage\nLoading usage data...\nEsc to cancel")
        return "Current session\n3% used\nResets in 1h\nCurrent week\n20% used\nResets in 5h\n"

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage_pty_once", fake_capture)

    raw = _capture_claude_usage_pty("/usr/bin/claude")

    assert "Current session" in raw
    assert calls == [5.0, 10.0]


def test_claude_usage_pty_reuses_valid_panel_from_timeout(monkeypatch):
    def fake_capture(_binary: str, *, timeout_seconds: float) -> str:
        raise _ClaudeUsageCaptureTimeout(
            "Curret session\n3% used\nResets in 1h\nCurrent week\n20% used\nResets in 5h\n"
        )

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage_pty_once", fake_capture)

    raw = _capture_claude_usage_pty("/usr/bin/claude")

    assert "Curret session" in raw


def test_claude_usage_pty_rejects_incomplete_weekly_timeout(monkeypatch):
    calls = []

    def fake_capture(_binary: str, *, timeout_seconds: float) -> str:
        calls.append(timeout_seconds)
        raise _ClaudeUsageCaptureTimeout(
            "Current session\n0% used\nResets in 1h\nCurrent week\n63% used\n"
        )

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage_pty_once", fake_capture)

    try:
        _capture_claude_usage_pty("/usr/bin/claude")
    except TimeoutError as exc:
        assert "waiting for the usage panel" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expected TimeoutError")

    assert calls == [5.0, 10.0]


def test_claude_usage_pty_loading_retry_timeout(monkeypatch):
    calls = []

    def fake_capture(_binary: str, *, timeout_seconds: float) -> str:
        calls.append(timeout_seconds)
        raise _ClaudeUsageCaptureTimeout("Settings: Usage\nLoading usage data...\nEsc to cancel")

    monkeypatch.setattr("tokenkick.sources._capture_claude_usage_pty_once", fake_capture)

    try:
        _capture_claude_usage_pty("/usr/bin/claude")
    except TimeoutError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expected TimeoutError")

    assert calls == [5.0, 10.0]


def test_terminate_process_group_waits_after_sigkill(monkeypatch):
    events = []
    wait_calls = 0

    class FakeProcess:
        pid = 123

        def wait(self, timeout=None):
            nonlocal wait_calls
            events.append(("wait", timeout))
            wait_calls += 1
            if wait_calls == 2:
                return 0
            raise subprocess.TimeoutExpired("claude", timeout)

    def fake_killpg(pid, sig):
        events.append(("killpg", pid, sig))

    monkeypatch.setattr("tokenkick.sources.os.name", "posix")
    monkeypatch.setattr("tokenkick.sources.os.killpg", fake_killpg)

    _terminate_process_group(FakeProcess())

    assert events == [
        ("killpg", 123, signal.SIGTERM),
        ("wait", 0.5),
        ("killpg", 123, signal.SIGKILL),
        ("wait", 0.5),
    ]


def test_claude_probe_raw_redaction_removes_email_and_plan():
    redacted = _redact_claude_usage_raw(
        "Signed in as user@example.com\nPlan: Pro\nOrganization org-abcdef123456\nCurrent session 1% used"
    )

    assert "user@example.com" not in redacted
    assert "Pro" not in redacted
    assert "[email]" in redacted
    assert "[plan]" in redacted


def test_claude_probe_is_last_resort(monkeypatch):
    account = _claude_account(status_probe_enabled=True)
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="CodexBar unavailable",
        ),
    )
    monkeypatch.setattr("tokenkick.sources.probe_claude_status", lambda: (True, None))

    status = _fetch_claude_direct(account)

    assert status.source_detail == "claude-probe"


def _codex_appserver_response(
    *,
    weekly_used: float = 0,
    weekly_resets_at: float | None = None,
    session_used: float = 1,
    session_resets_at: float | None = None,
) -> dict:
    return {
        "id": 2,
        "result": {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": session_used,
                        "windowDurationMins": 300,
                        "resetsAt": session_resets_at or NOW + 300 * 60,
                    },
                    "secondary": {
                        "usedPercent": weekly_used,
                        "windowDurationMins": 10080,
                        "resetsAt": weekly_resets_at or NOW + 10080 * 60,
                    },
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    "planType": "plus",
                    "rateLimitReachedType": None,
                }
            }
        },
    }


def test_parse_codex_appserver_marks_offer_as_available_unanchored(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)

    status = _parse_codex_appserver_ratelimits("codex", _codex_appserver_response())

    assert status.state == AccountState.FRESH
    assert status.used_percent == 0.0
    assert status.window_minutes == 10080
    assert status.session_used_percent == 1.0
    assert status.session_resets_in_seconds == 300 * 60
    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert status.window_anchor_state == "available_unanchored"


def test_parse_codex_appserver_marks_stable_nonzero_window_as_anchored(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)

    status = _parse_codex_appserver_ratelimits(
        "codex",
        _codex_appserver_response(weekly_used=5, weekly_resets_at=NOW + 4 * 86400),
    )

    assert status.state == AccountState.ACTIVE
    assert status.window_anchor_state == "anchored"


def test_parse_codex_appserver_marks_zero_percent_anchored_window_active(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)

    status = _parse_codex_appserver_ratelimits(
        "codex",
        _codex_appserver_response(weekly_used=0, weekly_resets_at=NOW + 4 * 86400),
    )

    assert status.state == AccountState.ACTIVE
    assert status.used_percent == 0.0
    assert status.window_anchor_state == "anchored"


def test_parse_codex_provider_usage_real_fixture_maps_session_and_weekly(monkeypatch):
    fixture = json.loads(Path("test_fixtures/codex_provider_usage_baseline.json").read_text())
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: 1_779_696_762)

    status = _parse_codex_appserver_ratelimits("personal", {"id": 2, "result": fixture})

    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert status.state == AccountState.ACTIVE
    assert status.session_used_percent == 4.0
    assert status.session_window_minutes == 300
    assert status.session_resets_in_seconds == 10896
    assert status.session_resets_at == 1779707658.0
    assert status.used_percent == 27.0
    assert status.window_minutes == 10080
    assert status.resets_at == 1780189765.0
    assert status.window_anchor_state == "anchored"


def test_parse_codex_provider_usage_preserves_real_one_percent_countdown(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)

    status = _parse_codex_appserver_ratelimits(
        "codex",
        _codex_appserver_response(session_used=1, session_resets_at=NOW + 2 * 60 * 60),
    )

    assert status.session_used_percent == 1.0
    assert status.session_resets_in_seconds == 2 * 60 * 60


def test_parse_codex_provider_usage_main_selects_codex_bucket(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    response = _codex_appserver_response()
    response["result"]["rateLimitsByLimitId"]["codex_model"] = {
        "limitId": "codex_model",
        "limitName": "Codex Model",
        "primary": {
            "usedPercent": 7,
            "windowDurationMins": 300,
            "resetsAt": NOW + 2 * 60 * 60,
        },
        "secondary": {
            "usedPercent": 3,
            "windowDurationMins": 10080,
            "resetsAt": NOW + 5 * 86400,
        },
    }

    status = _parse_codex_appserver_ratelimits("codex", response)

    assert status.state == AccountState.FRESH
    assert status.codex_rate_limit_id == "codex"
    assert status.used_percent == 0.0
    assert status.session_used_percent == 1.0
    assert status.resets_in_seconds == 10080 * 60
    assert status.session_resets_in_seconds == 300 * 60


def test_parse_codex_provider_usage_selects_requested_spark_bucket(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    response = _codex_appserver_response()
    response["result"]["rateLimitsByLimitId"]["codex_bengalfox"] = {
        "limitId": "codex_bengalfox",
        "limitName": "GPT-5.3-Codex-Spark",
        "primary": {
            "usedPercent": 9,
            "windowDurationMins": 300,
            "resetsAt": NOW + 90 * 60,
        },
        "secondary": {
            "usedPercent": 4,
            "windowDurationMins": 10080,
            "resetsAt": NOW + 3 * 86400,
        },
    }

    status = _parse_codex_appserver_ratelimits(
        "codex-spark",
        response,
        rate_limit_id="codex_bengalfox",
    )

    assert status.state == AccountState.ACTIVE
    assert status.codex_rate_limit_id == "codex_bengalfox"
    assert status.codex_rate_limit_name == "GPT-5.3-Codex-Spark"
    assert status.used_percent == 4.0
    assert status.session_used_percent == 9.0
    assert status.resets_in_seconds == 3 * 86400
    assert status.session_resets_in_seconds == 90 * 60


def test_parse_codex_provider_usage_missing_spark_bucket_is_unknown(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)

    status = _parse_codex_appserver_ratelimits(
        "codex-spark",
        _codex_appserver_response(),
        rate_limit_id="codex_bengalfox",
    )

    assert status.state == AccountState.UNKNOWN
    assert status.codex_rate_limit_id == "codex_bengalfox"
    assert "missing Codex provider bucket" in str(status.error)


def test_parse_codex_provider_usage_prefers_generic_when_all_buckets_unused(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    response = _codex_appserver_response(session_used=0)
    response["result"]["rateLimitsByLimitId"]["codex_unused_model"] = {
        "limitId": "codex_unused_model",
        "limitName": "Unused Model",
        "primary": {
            "usedPercent": 0,
            "windowDurationMins": 300,
            "resetsAt": NOW + 60,
        },
        "secondary": {
            "usedPercent": 0,
            "windowDurationMins": 10080,
            "resetsAt": NOW + 60,
        },
    }

    status = _parse_codex_appserver_ratelimits("codex", response)

    assert status.state == AccountState.FRESH
    assert status.session_resets_in_seconds == 300 * 60
    assert status.resets_in_seconds == 10080 * 60


def test_parse_codex_appserver_schema_mismatch_is_loud():
    status = _parse_codex_appserver_ratelimits("codex", {"id": 2, "result": {}})

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert "schema mismatch" in status.error
    assert "expected v1" in status.error
    assert "missing valid result.rateLimitsByLimitId entry" in status.error


def test_codex_direct_session_jsonl_overrides_appserver_phantom(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-23T04:18:33Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "secondary": {
                            "used_percent": 12,
                            "window_minutes": 10080,
                            "resets_at": NOW + 100,
                        }
                    },
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    _mock_codex_identity(monkeypatch)
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codex_appserver_ratelimits",
        lambda _account: appserver_status,
    )
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(sessions_dir),
        provider_home=str(tmp_path),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codex-session-jsonl"
    assert status.used_percent == 12.0


def test_codex_direct_uses_session_jsonl_when_appserver_and_codexbar_unavailable(
    monkeypatch,
    tmp_path,
):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-23T04:18:33Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "secondary": {
                            "used_percent": 12,
                            "window_minutes": 10080,
                            "resets_at": NOW + 100,
                        }
                    },
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    _mock_codex_identity(monkeypatch)
    monkeypatch.setattr("tokenkick.sources._fetch_codex_appserver_ratelimits", lambda _account: None)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(sessions_dir),
        provider_home=str(tmp_path),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codex-session-jsonl"


def test_codex_direct_blocks_home_identity_mismatch(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-23T04:18:33Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "secondary": {
                            "used_percent": 12,
                            "window_minutes": 10080,
                            "resets_at": NOW + 100,
                        }
                    },
                },
            }
        )
        + "\n"
    )
    _mock_codex_identity(monkeypatch, account_id="acct-user", email="user@example.test")
    account = AccountConfig(
        label="codex (secondary)",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(sessions_dir),
        provider_home=str(tmp_path),
        identity_provider_id="acct-secondary",
        identity_email="secondary@example.test",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == "codex-auth-json"
    assert "identity mismatch" in status.error


def test_codex_direct_missing_auth_identity_falls_back_to_codexbar_history(
    monkeypatch,
    tmp_path,
):
    observed = "2026-05-23T04:18:33Z"
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed))
    monkeypatch.setattr("tokenkick.sources.read_codex_identity", lambda _home: None)
    managed_home = tmp_path / "managed"
    managed_home.mkdir()
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "codex.json").write_text(
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
                                    "usedPercent": 11,
                                    "resetsAt": "2026-05-30T04:18:33Z",
                                }
                            ],
                        }
                    ]
                },
            }
        )
    )
    managed_accounts = tmp_path / "managed-codex-accounts.json"
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
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_MANAGED_ACCOUNTS_FILE", managed_accounts)
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

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codexbar-history"
    assert status.used_percent == 11.0


def test_codex_direct_uses_appserver_when_sessions_missing(monkeypatch, tmp_path):
    managed_home = tmp_path / "managed"
    managed_home.mkdir()
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: appserver_status,
    )
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(managed_home / "missing-sessions"),
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.FRESH
    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert status.window_anchor_state == "available_unanchored"


def test_codex_direct_prefers_session_jsonl_when_appserver_is_phantom(monkeypatch, tmp_path):
    managed_home = tmp_path / "managed"
    sessions_dir = managed_home / "sessions" / "2026" / "05" / "26"
    sessions_dir.mkdir(parents=True)
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    (sessions_dir / "probe.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-26T09:20:38Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 8.0,
                            "window_minutes": 300,
                            "resets_at": NOW + 60 * 60,
                        },
                        "secondary": {
                            "used_percent": 3.0,
                            "window_minutes": 10080,
                            "resets_at": NOW + 4 * 86400,
                        },
                    },
                },
            }
        )
        + "\n"
    )
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        session_used_percent=1.0,
        session_resets_in_seconds=300 * 60,
        session_window_minutes=300,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: appserver_status,
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codex-session-jsonl"
    assert status.used_percent == 3.0
    assert status.session_used_percent == 8.0


def test_codex_direct_prefers_clean_appserver_reset_over_stale_session_jsonl(
    monkeypatch,
    tmp_path,
):
    managed_home = tmp_path / "managed"
    sessions_dir = managed_home / "sessions" / "2026" / "05" / "26"
    sessions_dir.mkdir(parents=True)
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    (sessions_dir / "old-session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-26T09:20:38Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 8.0,
                            "window_minutes": 300,
                            "resets_at": NOW + 60 * 60,
                        },
                        "secondary": {
                            "used_percent": 3.0,
                            "window_minutes": 10080,
                            "resets_at": NOW + 4 * 86400,
                        },
                    },
                },
            }
        )
        + "\n"
    )
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.FRESH,
        used_percent=0.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=300 * 60,
        session_window_minutes=300,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="available_unanchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: appserver_status,
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.FRESH
    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert status.used_percent == 0.0
    assert status.session_used_percent == 0.0
    assert status.window_anchor_state == "available_unanchored"


def test_codex_direct_prefers_clean_appserver_session_reset_with_active_weekly(
    monkeypatch,
    tmp_path,
):
    managed_home = tmp_path / "managed"
    sessions_dir = managed_home / "sessions" / "2026" / "05" / "30"
    sessions_dir.mkdir(parents=True)
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    (sessions_dir / "old-session.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-30T09:25:23Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 13.0,
                            "window_minutes": 300,
                            "resets_at": NOW + 2 * 60 * 60,
                        },
                        "secondary": {
                            "used_percent": 16.0,
                            "window_minutes": 10080,
                            "resets_at": NOW + 4 * 86400,
                        },
                    },
                },
            }
        )
        + "\n"
    )
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=14.0,
        window_minutes=10080,
        session_used_percent=0.0,
        session_resets_in_seconds=300 * 60,
        session_window_minutes=300,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="anchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: appserver_status,
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert status.used_percent == 14.0
    assert status.session_used_percent == 0.0
    assert status.session_resets_in_seconds == 300 * 60


def test_codex_direct_prefers_session_jsonl_when_appserver_is_active_weekly_phantom(
    monkeypatch,
    tmp_path,
):
    managed_home = tmp_path / "managed"
    sessions_dir = managed_home / "sessions" / "2026" / "05" / "30"
    sessions_dir.mkdir(parents=True)
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    (sessions_dir / "probe.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-05-30T09:25:23Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "used_percent": 8.0,
                            "window_minutes": 300,
                            "resets_at": NOW + 2 * 60 * 60,
                        },
                        "secondary": {
                            "used_percent": 51.0,
                            "window_minutes": 10080,
                            "resets_at": NOW + 4 * 86400,
                        },
                    },
                },
            }
        )
        + "\n"
    )
    appserver_status = AccountStatus(
        label="codex",
        state=AccountState.ACTIVE,
        used_percent=51.0,
        window_minutes=10080,
        session_used_percent=1.0,
        session_resets_in_seconds=int(300 * 60 * 0.96),
        session_window_minutes=300,
        source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        window_anchor_state="anchored",
    )
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: appserver_status,
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codex-session-jsonl"
    assert status.used_percent == 51.0
    assert status.session_used_percent == 8.0


def test_codex_appserver_reader_is_cached_within_polling_pass(monkeypatch, tmp_path):
    managed_home = tmp_path / "managed"
    managed_home.mkdir()
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch)
    calls = []

    def fake_read(label, home):
        calls.append((label, home))
        return AccountStatus(
            label=label,
            state=AccountState.FRESH,
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        )

    monkeypatch.setattr("tokenkick.sources._read_codex_appserver_ratelimits", fake_read)
    account = AccountConfig(
        label="codex",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(managed_home / "missing-sessions"),
        provider_home=str(managed_home),
        identity_provider_id="acct_123",
    )

    with polling_pass_cache():
        first = _fetch_codex_direct(account)
        second = _fetch_codex_direct(account)

    assert first.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert second.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    assert len(calls) == 1


def test_codex_direct_appserver_failure_falls_back_to_codexbar_history(
    monkeypatch,
    tmp_path,
):
    observed = "2026-05-23T04:18:33Z"
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed))
    managed_home = tmp_path / "managed"
    managed_home.mkdir()
    (managed_home / "auth.json").write_text("{}")
    _mock_codex_identity(monkeypatch, email="codex@example.test")
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "codex.json").write_text(
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
    managed_accounts = tmp_path / "managed-codex-accounts.json"
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
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_MANAGED_ACCOUNTS_FILE", managed_accounts)
    monkeypatch.setattr(
        "tokenkick.sources._read_codex_appserver_ratelimits",
        lambda label, home: AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="boom",
            source_detail=CODEX_PROVIDER_USAGE_SOURCE_DETAIL,
        ),
    )
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

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "codexbar-history"
    assert status.used_percent == 9.0


def test_polling_pass_cache_reuses_codexbar_result_within_context(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

    with polling_pass_cache():
        first, first_error = _run_codexbar_json(["codexbar", "--format", "json"])
        second, second_error = _run_codexbar_json(["codexbar", "--format", "json"])

    assert first == {"ok": True}
    assert second == {"ok": True}
    assert first_error is None
    assert second_error is None
    assert len(calls) == 1


class TestDetermineState:
    def test_zero_percent_is_fresh(self):
        assert _determine_state(0.0, 600000) == AccountState.FRESH

    def test_nonzero_percent_is_active(self):
        assert _determine_state(22.0, 351406) == AccountState.ACTIVE

    def test_only_resets_in_is_waiting(self):
        assert _determine_state(None, 32400) == AccountState.WAITING

    def test_no_data_is_unknown(self):
        assert _determine_state(None, None) == AccountState.UNKNOWN

    def test_hundred_percent_is_active(self):
        assert _determine_state(100.0, 0) == AccountState.ACTIVE


class TestParseSessionRateLimit:
    def test_parse_real_payload_uses_secondary_window(self, monkeypatch):
        """Test with a payload matching actual Codex session JSONL structure."""
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        payload = {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 5200,
                    "cached_input_tokens": 2048,
                    "output_tokens": 14,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 5214,
                },
            },
            "rate_limits": {
                "limit_id": "codex",
                "limit_name": None,
                "primary": {
                    "used_percent": 0.0,
                    "window_minutes": 300,
                    "resets_at": NOW + 500,
                },
                "secondary": {
                    "used_percent": 15.0,
                    "window_minutes": 10080,
                    "resets_at": NOW + 604800,
                },
                "credits": None,
                "plan_type": "prolite",
                "rate_limit_reached_type": None,
            },
        }
        status = _parse_session_rate_limit("test", payload)
        assert status.label == "test"
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 15.0
        assert status.resets_in_seconds == 604800
        assert status.window_minutes == 10080
        assert status.session_used_percent == 0.0
        assert status.session_resets_in_seconds == 500
        assert status.session_window_minutes == 300

    def test_parse_fresh_payload_with_resets_at(self, monkeypatch):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        payload = {
            "rate_limits": {
                "primary": {"used_percent": 0.0, "window_minutes": 300, "resets_at": NOW + 300},
                "secondary": {
                    "used_percent": 0.0,
                    "window_minutes": 10080,
                    "resets_at": NOW + 10080 * 60,
                },
            },
        }
        status = _parse_session_rate_limit("personal", payload)
        assert status.state == AccountState.FRESH
        assert status.used_percent == 0.0
        assert status.resets_in_seconds == 10080 * 60
        assert status.window_anchor_state == "available_unanchored"

    def test_zero_percent_anchored_session_payload_is_active(self, monkeypatch):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        payload = {
            "rate_limits": {
                "primary": {
                    "used_percent": 1.0,
                    "window_minutes": 300,
                    "resets_at": NOW + 2 * 60 * 60,
                },
                "secondary": {
                    "used_percent": 0.0,
                    "window_minutes": 10080,
                    "resets_at": NOW + 4 * 24 * 60 * 60,
                },
            },
        }

        status = _parse_session_rate_limit("personal", payload)

        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 0.0
        assert status.window_anchor_state == "anchored"
        assert status.session_used_percent == 1.0
        assert status.session_resets_in_seconds == 2 * 60 * 60

    def test_tiny_codex_session_preserves_local_countdown(self, monkeypatch):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        payload = {
            "rate_limits": {
                "primary": {
                    "used_percent": 1.0,
                    "window_minutes": 300,
                    "resets_at": NOW + 2 * 60 * 60,
                },
                "secondary": {
                    "used_percent": 0.0,
                    "window_minutes": 10080,
                    "resets_at": NOW + 7 * 24 * 60 * 60,
                },
            },
        }

        status = _parse_session_rate_limit("personal", payload)

        assert status.session_used_percent == 1.0
        assert status.session_resets_in_seconds == 2 * 60 * 60

    def test_parse_legacy_resets_in_seconds_payload(self):
        payload = {
            "rate_limits": {
                "secondary": {
                    "used_percent": 22.0,
                    "window_minutes": 10079,
                    "resets_in_seconds": 351406,
                },
            },
        }
        status = _parse_session_rate_limit("test", payload)
        assert status.state == AccountState.ACTIVE
        assert status.resets_in_seconds == 351406


class TestFindLatestRateLimit:
    def test_finds_latest_real_session_event(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        day_dir = sessions_dir / "2026" / "05" / "17"
        day_dir.mkdir(parents=True)

        older_payload = {
            "type": "token_count",
            "rate_limits": {"secondary": {"used_percent": 3.0, "resets_at": NOW + 100}},
        }
        latest_payload = {
            "type": "token_count",
            "rate_limits": {"secondary": {"used_percent": 15.0, "resets_at": NOW + 200}},
        }

        (day_dir / "older.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-05-17T14:22:50.101Z",
                    "type": "event_msg",
                    "payload": older_payload,
                }
            )
            + "\n"
        )
        (day_dir / "latest.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-05-17T19:38:37.361Z",
                    "type": "event_msg",
                    "payload": latest_payload,
                }
            )
            + "\n"
        )

        assert _find_latest_rate_limit(sessions_dir) == latest_payload


class TestParseCodexBarJson:
    def test_parse_codexbar_usage_entry(self, monkeypatch):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        data = [
            {
                "provider": "codex",
                "source": "openai-web",
                "usage": {
                    "primary": {"usedPercent": 0, "windowMinutes": 300},
                    "secondary": {
                        "resetDescription": "Resets May 23, 2026 11:18 PM",
                        "resetsAt": "2026-05-23T21:18:02Z",
                        "usedPercent": 15,
                        "windowMinutes": 10080,
                    },
                    "updatedAt": "2026-05-17T19:36:02Z",
                },
            }
        ]

        status = _parse_codexbar_json("personal", data, provider="codex")
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 15.0
        assert status.window_minutes == 10080
        assert status.resets_in_seconds == 571082
        assert status.session_used_percent == 0.0
        assert status.session_window_minutes == 300

    def test_codexbar_prefers_weekly_window_when_windows_are_reordered(self):
        data = {
            "provider": "codex",
            "usage": {
                "primary": {
                    "usedPercent": 17,
                    "windowMinutes": 10080,
                },
                "secondary": {
                    "usedPercent": 0,
                    "windowMinutes": 300,
                },
            },
        }

        status = _parse_codexbar_json("personal", data, provider="codex")
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 17.0
        assert status.window_minutes == 10080
        assert status.session_used_percent == 0.0
        assert status.session_window_minutes == 300

    def test_codexbar_tracks_session_cooldown_separately(self, monkeypatch):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        data = {
            "provider": "codex",
            "usage": {
                "primary": {
                    "resetsAt": "2026-05-17T08:40:00Z",
                    "usedPercent": 1,
                    "windowMinutes": 300,
                },
                "secondary": {
                    "resetsAt": "2026-05-24T20:52:35Z",
                    "usedPercent": 0,
                    "windowMinutes": 10080,
                },
            },
        }

        status = _parse_codexbar_json("work", data, provider="codex")
        assert status.state == AccountState.FRESH
        assert status.used_percent == 0.0
        assert status.window_minutes == 10080
        assert status.session_used_percent == 1.0
        assert status.session_resets_in_seconds == 300 * 60
        assert status.session_window_minutes == 300

    def test_codexbar_provider_error_is_unknown(self):
        data = [
            {
                "provider": "codex",
                "source": "auto",
                "error": {
                    "code": 1,
                    "kind": "provider",
                    "message": "Codex returned invalid data",
                },
            }
        ]

        status = _parse_codexbar_json("personal", data, provider="codex")
        assert status.state == AccountState.UNKNOWN
        assert status.error == "Codex returned invalid data"

    def test_codexbar_falls_back_to_primary_when_secondary_is_missing(self):
        data = {
            "provider": "openrouter",
            "usage": {
                "primary": {"usedPercent": 85.4},
                "secondary": None,
            },
        }

        status = _parse_codexbar_json("openrouter", data, provider="openrouter")
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 85.4

    def test_codexbar_parses_openrouter_spending_cap_balance(self):
        data = {
            "provider": "openrouter",
            "source": "api",
            "usage": {
                "openRouterUsage": {
                    "balance": 13.56,
                    "keyLimit": 10,
                    "keyUsage": 8.54410018,
                    "usedPercent": 61.26,
                },
                "primary": {"usedPercent": 85.4410018},
                "secondary": None,
            },
        }

        status = _parse_codexbar_json("openrouter", data, provider="openrouter")
        assert status.used_percent == 85.4410018
        assert status.balance_remaining == 10 - 8.54410018
        assert status.balance_limit == 10.0
        assert status.balance_spent_percent == 85.4410018

    def test_codexbar_selects_matching_account_email(self):
        data = [
            {
                "provider": "codex",
                "usage": {
                    "accountEmail": "one@example.test",
                    "secondary": {"usedPercent": 10},
                },
            },
            {
                "provider": "codex",
                "usage": {
                    "accountEmail": "two@example.test",
                    "secondary": {"usedPercent": 20},
                },
            },
        ]

        status = _parse_codexbar_json(
            "two",
            data,
            provider="codex",
            account="two@example.test",
        )
        assert status.used_percent == 20.0

    def test_codexbar_selects_matching_top_level_account_email(self):
        data = [
            {
                "account": "one@example.test",
                "provider": "codex",
                "usage": {"secondary": {"usedPercent": 10}},
            },
            {
                "account": "two@example.test",
                "provider": "codex",
                "usage": {"secondary": {"usedPercent": 20}},
            },
        ]

        status = _parse_codexbar_json(
            "two",
            data,
            provider="codex",
            account="two@example.test",
        )
        assert status.used_percent == 20.0

    def test_codexbar_parses_antigravity_extra_rate_windows(self, monkeypatch):
        monkeypatch.setattr("tokenkick.source_utils.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))

        status = _parse_codexbar_json(
            "antigravity",
            [_antigravity_codexbar_entry()],
            provider="antigravity",
            account="dev@example.test",
        )

        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 66.22157
        assert status.window_minutes == 300
        assert status.session_used_percent == 66.22157
        assert status.session_window_minutes == 300
        assert status.quota_windows is not None
        assert [window["id"] for window in status.quota_windows] == [
            "antigravity-quota-summary-gemini-5h",
            "antigravity-quota-summary-gemini-weekly",
            "antigravity-quota-summary-3p-5h",
            "antigravity-quota-summary-3p-weekly",
        ]
        assert status.quota_windows[0]["family"] == "gemini"
        assert status.quota_windows[0]["window_kind"] == "session"
        assert status.quota_windows[1]["family"] == "gemini"
        assert status.quota_windows[1]["window_kind"] == "weekly"
        assert status.quota_windows[2]["family"] == "claude_gpt"
        assert status.quota_windows[2]["window_kind"] == "session"
        assert status.quota_windows[3]["family"] == "claude_gpt"
        assert status.quota_windows[3]["window_kind"] == "weekly"
        assert status.quota_windows[0]["resets_at"] == _epoch("2026-05-23T06:18:33Z")
        assert status.quota_windows[0]["resets_in_seconds"] == 7200
        assert status.quota_windows[0]["source"] == "codexbar"

    def test_codexbar_antigravity_extra_rate_windows_fail_closed_when_incomplete(self):
        status = _parse_codexbar_json(
            "antigravity",
            [_antigravity_codexbar_entry(missing_id="antigravity-quota-summary-3p-weekly")],
            provider="antigravity",
        )

        assert status.state == AccountState.UNKNOWN
        assert "Antigravity quota windows" in status.error
        assert status.quota_windows is None

    def test_codexbar_antigravity_extra_rate_windows_fail_closed_on_unknown_id(self):
        entry = _antigravity_codexbar_entry()
        entry["usage"]["extraRateWindows"][0]["id"] = "antigravity-quota-summary-new-window"

        status = _parse_codexbar_json("antigravity", [entry], provider="antigravity")

        assert status.state == AccountState.UNKNOWN
        assert "Antigravity quota windows" in status.error
        assert status.quota_windows is None

    def test_codexbar_cli_uses_all_accounts_for_codex(self, monkeypatch):
        commands = []
        all_accounts_data = [
            {
                "account": "one@example.test",
                "provider": "codex",
                "usage": {"secondary": {"usedPercent": 10, "windowMinutes": 10080}},
            },
            {
                "account": "two@example.test",
                "provider": "codex",
                "usage": {"secondary": {"usedPercent": 20, "windowMinutes": 10080}},
            },
        ]

        def fake_run(cmd, *args, **kwargs):
            commands.append(cmd)
            if cmd[:4] == ["codexbar", "usage", "--provider", "codex"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(all_accounts_data), stderr="")
            raise AssertionError("legacy CodexBar command should not run")

        monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)

        account = AccountConfig(
            label="two",
            provider="codex",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="codex",
            codexbar_account="two@example.test",
        )

        status = _fetch_codexbar_cli(account)
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 20.0
        assert commands == [
            ["codexbar", "usage", "--provider", "codex", "--all-accounts", "--format", "json"]
        ]

    def test_codexbar_cli_falls_back_to_legacy_when_all_accounts_unavailable(
        self,
        monkeypatch,
        tmp_path,
    ):
        commands = []
        legacy_data = [
            {
                "provider": "codex",
                "usage": {
                    "accountEmail": "dev@example.test",
                    "secondary": {"usedPercent": 15, "windowMinutes": 10080},
                },
            }
        ]

        def fake_run(cmd, *args, **kwargs):
            commands.append(cmd)
            if cmd[:4] == ["codexbar", "usage", "--provider", "codex"]:
                return SimpleNamespace(returncode=64, stdout="unsupported", stderr="unknown option")
            return SimpleNamespace(returncode=0, stdout=json.dumps(legacy_data), stderr="")

        monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", fake_run)
        monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
        monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")

        account = AccountConfig(
            label="dev",
            provider="codex",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="codex",
            codexbar_account="dev@example.test",
        )

        status = _fetch_codexbar_cli(account)
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 15.0
        assert commands == [
            ["codexbar", "usage", "--provider", "codex", "--all-accounts", "--format", "json"],
            ["codexbar", "--format", "json", "--pretty"],
        ]

    def test_codexbar_cli_parses_valid_json_from_nonzero_exit(self, monkeypatch):
        data = [
            {
                "provider": "codex",
                "usage": {
                    "accountEmail": "dev@example.test",
                    "secondary": {"usedPercent": 15, "windowMinutes": 10080},
                },
            }
        ]
        monkeypatch.setattr(
            "tokenkick.codexbar_source.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=1,
                stdout=json.dumps(data),
                stderr="",
            ),
        )

        account = AccountConfig(
            label="dev",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="codex",
            codexbar_account="dev@example.test",
        )

        status = _fetch_codexbar_cli(account)
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 15.0

    def test_codexbar_cli_falls_back_to_widget_snapshot(self, monkeypatch, tmp_path):
        snapshot_file = tmp_path / "widget-snapshot.json"
        snapshot_file.write_text(
            json.dumps(
                {
                    "generatedAt": "2026-05-23T04:18:33Z",
                    "entries": [
                        {
                            "provider": "gemini",
                            "updatedAt": "2026-05-23T04:18:32Z",
                            "primary": {
                                "usedPercent": 0,
                                "windowMinutes": 1440,
                                "resetsAt": "2026-05-24T04:18:32Z",
                            },
                            "secondary": {
                                "usedPercent": 0.1,
                                "windowMinutes": 1440,
                                "resetsAt": "2026-05-23T06:25:48Z",
                            },
                        }
                    ],
                }
            )
        )
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: NOW)
        monkeypatch.setattr("tokenkick.codexbar_source.time.time", lambda: _epoch("2026-05-23T04:18:40Z"))
        monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
        monkeypatch.setattr(
            "tokenkick.codexbar_source.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    [
                        {
                            "provider": "gemini",
                            "error": {"message": "A server with the specified hostname could not be found."},
                        }
                    ]
                ),
                stderr="",
            ),
        )

        account = AccountConfig(
            label="work",
            provider="gemini",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="gemini",
            codexbar_account="work@example.test",
        )

        status = _fetch_codexbar_cli(account)
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 0.1
        assert status.window_minutes == 1440

    def test_codexbar_cli_falls_back_to_claude_history(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))
        history_dir = tmp_path / "history"
        history_dir.mkdir()
        (history_dir / "claude.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "accounts": {},
                    "unscoped": [
                        {
                            "name": "session",
                            "windowMinutes": 300,
                            "entries": [{"capturedAt": "2026-05-23T04:18:33Z", "usedPercent": 0}],
                        },
                        {
                            "name": "weekly",
                            "windowMinutes": 10080,
                            "entries": [
                                {
                                    "capturedAt": "2026-05-23T04:18:33Z",
                                    "resetsAt": "2026-05-29T12:00:00Z",
                                    "usedPercent": 4,
                                }
                            ],
                        },
                    ],
                }
            )
        )
        monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
        monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
        monkeypatch.setattr(
            "tokenkick.codexbar_source.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=3,
                stdout=json.dumps(
                    [
                        {
                            "provider": "claude",
                            "error": {"message": "Claude OAuth credentials not found."},
                        }
                    ]
                ),
                stderr="",
            ),
        )

        account = AccountConfig(
            label="claude",
            provider="claude",
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider="claude",
        )

        status = _fetch_codexbar_cli(account)
        assert status.state == AccountState.ACTIVE
        assert status.used_percent == 4.0
        assert status.session_used_percent == 0.0
        assert status.window_minutes == 10080


def test_codex_ratelimit_missing_sessions_falls_back_to_codexbar_history(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    managed_file = tmp_path / "managed-codex-accounts.json"
    managed_file.write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {
                        "email": "work@example.test",
                        "providerAccountID": "provider-work",
                    }
                ]
            }
        )
    )
    (history_dir / "codex.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "codex:v1:provider-account:provider-work": [
                        {
                            "name": "session",
                            "windowMinutes": 300,
                            "entries": [
                                {
                                    "capturedAt": "2026-05-23T04:18:32Z",
                                    "resetsAt": "2026-05-23T09:18:32Z",
                                    "usedPercent": 1,
                                }
                            ],
                        },
                        {
                            "name": "weekly",
                            "windowMinutes": 10080,
                            "entries": [
                                {
                                    "capturedAt": "2026-05-23T04:18:32Z",
                                    "resetsAt": "2026-05-30T04:18:32Z",
                                    "usedPercent": 0,
                                }
                            ],
                        },
                    ]
                }
            }
        )
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_MANAGED_ACCOUNTS_FILE", managed_file)

    account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path=str(tmp_path / "missing-sessions"),
        codexbar_account="work@example.test",
    )

    status = _fetch_codex_session_file(account)
    assert status.state == AccountState.FRESH
    assert status.used_percent == 0.0
    assert status.window_minutes == 10080


def test_codex_direct_missing_sessions_does_not_fall_back_to_codexbar(monkeypatch, tmp_path):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "codex.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {},
                "unscoped": [
                    {
                        "name": "weekly",
                        "windowMinutes": 10080,
                        "entries": [{"capturedAt": "2026-05-23T04:18:33Z", "usedPercent": 0}],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)

    account = AccountConfig(
        label="direct",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        session_path=str(tmp_path / "missing-sessions"),
        identity_provider_id="acct_123",
        identity_email="direct@example.test",
    )

    status = _fetch_codex_direct(account)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == "codex-session-jsonl"
    assert "No Codex session data" in status.error


def test_claude_direct_uses_codexbar_fallback_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch("2026-05-23T04:18:33Z"))
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "claude.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {},
                "unscoped": [
                    {
                        "name": "session",
                        "windowMinutes": 300,
                        "entries": [{"capturedAt": "2026-05-23T04:18:33Z", "usedPercent": 0}],
                    },
                    {
                        "name": "weekly",
                        "windowMinutes": 10080,
                        "entries": [
                            {
                                "capturedAt": "2026-05-23T04:18:33Z",
                                "resetsAt": "2026-05-29T12:00:00Z",
                                "usedPercent": 4,
                            }
                        ],
                    },
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=3,
            stdout=json.dumps(
                [{"provider": "claude", "error": {"message": "Claude OAuth credentials not found."}}]
            ),
            stderr="",
        ),
    )
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="account-uuid",
        identity_org_id="org-uuid",
    )

    status = _fetch_claude_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.used_percent == 4.0
    assert status.source_detail == "claude-codexbar-fallback"


def test_claude_direct_without_codexbar_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="account-uuid",
        identity_org_id="org-uuid",
    )

    status = _fetch_claude_direct(account)

    assert status.state == AccountState.UNKNOWN
    assert status.source_detail == "claude-config-json"
    assert "CodexBar fallback is unavailable" in status.error
    assert "enable the explicit Claude probe" in status.error
    assert "tk status --refresh" in status.error


def test_claude_direct_probe_is_explicit(monkeypatch):
    monkeypatch.setattr("tokenkick.sources.probe_claude_status", lambda: (True, None))
    monkeypatch.setattr(
        "tokenkick.sources._fetch_codexbar_cli",
        lambda *_args, **_kwargs: AccountStatus(
            label="claude",
            state=AccountState.UNKNOWN,
            error="CodexBar unavailable",
        ),
    )
    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        identity_provider_id="account-uuid",
        status_probe_enabled=True,
    )

    status = _fetch_claude_direct(account)

    assert status.state == AccountState.ACTIVE
    assert status.source_detail == "claude-probe"
    assert "consuming probe" in status.error
    assert status.session_used_percent == 1.0
    assert status.session_window_minutes == 300


def test_codexbar_widget_snapshot_parses_supported_providers(monkeypatch, tmp_path):
    observed_at = "2026-05-23T04:18:33Z"
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "generatedAt": observed_at,
                "entries": [
                    {
                        "provider": provider,
                        "updatedAt": observed_at,
                        "secondary": {
                            "usedPercent": used,
                            "windowMinutes": 10080 if provider in {"codex", "claude"} else 1440,
                            "resetsAt": "2026-05-24T04:18:33Z",
                        },
                    }
                    for provider, used in [
                        ("claude", 0),
                        ("gemini", 2),
                        ("antigravity", 3),
                        ("openrouter", 4),
                    ]
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed_at))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)

    for provider, expected_used in [
        ("claude", 0.0),
        ("gemini", 2.0),
        ("antigravity", 3.0),
        ("openrouter", 4.0),
    ]:
        account = AccountConfig(
            label=provider,
            provider=provider,
            source=DataSource.CODEXBAR_CLI,
            codexbar_provider=provider,
        )
        status = _fetch_codexbar_cli(account)
        assert status.used_percent == expected_used
        assert status.observed_at == observed_at
        assert status.source_detail == "codexbar-snapshot"
        assert status.stale is False


def test_codexbar_cli_prefers_local_snapshot_for_non_codex_without_cli_wait(
    monkeypatch,
    tmp_path,
):
    observed_at = "2026-05-23T04:18:33Z"
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "generatedAt": observed_at,
                "entries": [
                    {
                        "provider": "gemini",
                        "updatedAt": observed_at,
                        "secondary": {
                            "usedPercent": 2,
                            "windowMinutes": 1440,
                            "resetsAt": "2026-05-24T04:18:33Z",
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed_at))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr(
        "tokenkick.codexbar_source.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CodexBar CLI should not run")),
    )

    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )
    status = _fetch_codexbar_cli(account)

    assert status.used_percent == 2.0
    assert status.source_detail == "codexbar-snapshot"


def test_codexbar_history_schema_mismatch_names_expected_detected_and_update_side(
    monkeypatch,
    tmp_path,
):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "claude.json").write_text(json.dumps({"version": 9, "unscoped": []}))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)

    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="claude",
    )

    status = _fetch_codexbar_cli(account)
    assert status.state == AccountState.UNKNOWN
    assert "expected 1, got 9" in status.error
    assert "Update TokenKick" in status.error


def test_codexbar_history_schema_mismatch_lower_version_tells_user_to_update_codexbar(
    monkeypatch,
    tmp_path,
):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "claude.json").write_text(json.dumps({"version": 0, "unscoped": []}))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)

    account = AccountConfig(
        label="claude",
        provider="claude",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="claude",
    )

    status = _fetch_codexbar_cli(account)
    assert status.state == AccountState.UNKNOWN
    assert "expected 1, got 0" in status.error
    assert "Update CodexBar" in status.error


def test_codexbar_managed_accounts_schema_mismatch_surfaces_for_codex_history(
    monkeypatch,
    tmp_path,
):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "codex.json").write_text(json.dumps({"version": 1, "accounts": {}}))
    managed_file = tmp_path / "managed-codex-accounts.json"
    managed_file.write_text(json.dumps({"version": 1, "accounts": []}))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", history_dir)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_MANAGED_ACCOUNTS_FILE", managed_file)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [])

    account = AccountConfig(
        label="work",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path=str(tmp_path / "missing-sessions"),
        codexbar_account="work@example.test",
    )

    status = _fetch_codex_session_file(account)
    assert status.state == AccountState.UNKNOWN
    assert "expected 2, got 1" in status.error
    assert "Update CodexBar" in status.error


def test_codexbar_widget_snapshot_rejects_malformed_required_shape(monkeypatch, tmp_path):
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(json.dumps({"generatedAt": "2026-05-23T04:18:33Z"}))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)

    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )

    status = _fetch_codexbar_cli(account)
    assert status.state == AccountState.UNKNOWN
    assert status.error == "CodexBar widget snapshot is missing required fields."


def test_codexbar_snapshot_applies_configurable_staleness_and_rejection_thresholds(
    monkeypatch,
    tmp_path,
):
    observed_at = "2026-05-23T04:00:00Z"
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "generatedAt": observed_at,
                "entries": [
                    {
                        "provider": "gemini",
                        "updatedAt": observed_at,
                        "secondary": {
                            "usedPercent": 0,
                            "windowMinutes": 1440,
                            "resetsAt": "2026-05-24T04:00:00Z",
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed_at) + 1_000)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)
    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )

    fresh_enough = _fetch_codexbar_cli(
        account,
        codexbar_staleness_threshold_seconds=1_200,
        codexbar_rejection_threshold_seconds=2_000,
    )
    stale = _fetch_codexbar_cli(
        account,
        codexbar_staleness_threshold_seconds=900,
        codexbar_rejection_threshold_seconds=2_000,
    )
    rejected = _fetch_codexbar_cli(
        account,
        codexbar_staleness_threshold_seconds=900,
        codexbar_rejection_threshold_seconds=999,
    )

    assert fresh_enough.stale is False
    assert stale.stale is True
    assert stale.state == AccountState.FRESH
    assert stale.stale_seconds == 1_000
    assert rejected.state == AccountState.UNKNOWN
    assert rejected.error == CODEXBAR_SNAPSHOT_STALE_MESSAGE
    assert rejected.observed_at == observed_at


def test_codexbar_snapshot_preserves_source_observed_at_for_fresh_and_stale(
    monkeypatch,
    tmp_path,
):
    observed_at = "2026-05-23T04:00:00Z"
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "generatedAt": observed_at,
                "entries": [
                    {
                        "provider": "gemini",
                        "updatedAt": observed_at,
                        "secondary": {
                            "usedPercent": 0,
                            "windowMinutes": 1440,
                            "resetsAt": "2026-05-24T04:00:00Z",
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr("tokenkick.codexbar_source.time.time", lambda: _epoch(observed_at) + 1_000)
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)
    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )

    fresh_enough = _fetch_codexbar_cli(
        account,
        codexbar_staleness_threshold_seconds=1_200,
        codexbar_rejection_threshold_seconds=2_000,
    )
    stale = _fetch_codexbar_cli(
        account,
        codexbar_staleness_threshold_seconds=900,
        codexbar_rejection_threshold_seconds=2_000,
    )

    assert fresh_enough.observed_at == observed_at
    assert fresh_enough.stale is False
    assert fresh_enough.stale_seconds == 1_000
    assert stale.observed_at == observed_at
    assert stale.stale is True
    assert stale.stale_seconds == 1_000


def test_codexbar_snapshot_rejects_large_future_timestamps(monkeypatch, tmp_path):
    now = "2026-05-23T04:00:00Z"
    allowed_future = "2026-05-23T04:04:59Z"
    suspect_future = "2026-05-23T04:05:01Z"
    assert _epoch(suspect_future) - _epoch(now) > CODEXBAR_FUTURE_SKEW_TOLERANCE_SECONDS
    snapshot_file = tmp_path / "widget-snapshot.json"

    def write_snapshot(observed_at: str) -> None:
        snapshot_file.write_text(
            json.dumps(
                {
                    "generatedAt": observed_at,
                    "entries": [
                        {
                            "provider": "gemini",
                            "updatedAt": observed_at,
                            "secondary": {
                                "usedPercent": 0,
                                "windowMinutes": 1440,
                                "resetsAt": "2026-05-24T04:00:00Z",
                            },
                        }
                    ],
                }
            )
        )

    monkeypatch.setattr("tokenkick.codexbar_source.time.time", lambda: _epoch(now))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")
    monkeypatch.setattr("tokenkick.codexbar_source.subprocess.run", _codexbar_error_run)
    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="gemini",
    )

    write_snapshot(allowed_future)
    allowed = _fetch_codexbar_cli(account)
    write_snapshot(suspect_future)
    suspect = _fetch_codexbar_cli(account)

    assert allowed.state == AccountState.FRESH
    assert allowed.stale is False
    assert allowed.stale_seconds == 0
    assert suspect.state == AccountState.UNKNOWN
    assert suspect.error == CODEXBAR_SNAPSHOT_FUTURE_MESSAGE
    assert suspect.observed_at == suspect_future
    assert suspect.stale is True
    assert suspect.stale_seconds is None


def test_codexbar_http_provider_error_falls_back_to_valid_local_snapshot(monkeypatch, tmp_path):
    import httpx

    observed_at = "2026-05-23T04:18:33Z"
    snapshot_file = tmp_path / "widget-snapshot.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "generatedAt": observed_at,
                "entries": [
                    {
                        "provider": "gemini",
                        "updatedAt": observed_at,
                        "secondary": {
                            "usedPercent": 0,
                            "windowMinutes": 1440,
                            "resetsAt": "2026-05-24T04:18:33Z",
                        },
                    }
                ],
            }
        )
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"provider": "gemini", "error": {"message": "provider failed"}}]

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed_at))
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_WIDGET_SNAPSHOT_FILES", [snapshot_file])
    monkeypatch.setattr("tokenkick.codexbar_source.CODEXBAR_HISTORY_DIR", tmp_path / "missing-history")

    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_HTTP,
        codexbar_provider="gemini",
    )

    status = _fetch_codexbar_http(account)

    assert status.state == AccountState.FRESH
    assert status.source_detail == "codexbar-snapshot"


def test_fetch_status_populates_metadata_for_live_codexbar_http(monkeypatch):
    import httpx

    observed = "2026-05-23T04:18:33Z"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "provider": "gemini",
                    "usage": {
                        "secondary": {
                            "usedPercent": 1,
                            "windowMinutes": 1440,
                            "resetsAt": "2026-05-24T04:18:33Z",
                        }
                    },
                }
            ]

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("tokenkick.sources.time.time", lambda: _epoch(observed))
    account = AccountConfig(
        label="gemini",
        provider="gemini",
        source=DataSource.CODEXBAR_HTTP,
        codexbar_provider="gemini",
    )

    status = fetch_status(account)

    assert status.observed_at == observed
    assert status.source_detail == "codexbar-http"
    assert status.state == AccountState.ACTIVE


class TestNestedGet:
    def test_simple(self):
        assert _nested_get({"a": {"b": 42}}, ("a", "b")) == 42

    def test_missing(self):
        assert _nested_get({"a": {"b": 42}}, ("a", "c")) is None

    def test_not_dict(self):
        assert _nested_get({"a": "string"}, ("a", "b")) is None

    def test_empty(self):
        assert _nested_get({}, ("a",)) is None
