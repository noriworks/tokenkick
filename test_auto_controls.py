"""Persisted switch semantics and agreement between MCP preview and CLI apply."""

import hashlib
import io
import itertools
import json

from click.testing import CliRunner
import pytest
from rich.console import Console

from tokenkick import auto_controls, cli, interactive
from tokenkick.mcp_core import TokenKickMCPCore
from tokenkick.models import AccountConfig, Config


@pytest.fixture(autouse=True)
def no_provider_requests(monkeypatch):
    monkeypatch.setattr(cli, "_load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(cli, "_read_daemon_pid", lambda: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Control changes must not generate or refresh provider requests")

    monkeypatch.setattr(cli, "kick_account", forbidden)
    monkeypatch.setattr(cli, "_fetch_status", forbidden)


def save(master, weekly, session):
    config = Config(accounts=[AccountConfig(
        label="codex", provider="codex", auto_kick=master,
        weekly_auto_kick=weekly, session_auto_kick=session,
    )])
    config.save()
    return config


def invoke(scope, enabled, *extra):
    return CliRunner().invoke(cli.cli, [
        "auto", *([] if scope == "all" else [scope]),
        "enable" if enabled else "disable", "codex",
        *(["--accept-risk", "ENABLE"] if enabled else []), *extra,
    ])


@pytest.mark.parametrize("master,weekly,session", list(itertools.product([False, True], repeat=3)))
@pytest.mark.parametrize("scope", ["all", "weekly", "session"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cli_persisted_switch_matrix(master, weekly, session, scope, enabled):
    save(master, weekly, session)
    result = invoke(scope, enabled)
    assert result.exit_code == 0, result.output
    account = Config.load().accounts[0]
    # Expectations independent of the policy helper used by the implementation.
    assert account.auto_kick == (enabled if scope == "all" else master or enabled)
    assert account.weekly_auto_kick == (enabled if scope == "weekly" else weekly)
    assert account.session_auto_kick == (enabled if scope == "session" else session)


def test_weekly_only_pause_resume_survives_reloads_and_leaves_other_account_alone():
    config = save(False, False, False)
    other = AccountConfig(label="untouched", provider="claude", auto_kick=True)
    config.accounts.append(other)
    config.save()
    assert invoke("weekly", True).exit_code == 0
    for _ in range(3):
        assert invoke("all", False).exit_code == 0
        account = Config.load().accounts[0]
        assert auto_controls.controls(account) == dict(auto_kick=False, weekly_auto_kick=True, session_auto_kick=False)
        assert invoke("all", True).exit_code == 0
        loaded = Config.load()
        assert auto_controls.controls(loaded.accounts[0]) == dict(auto_kick=True, weekly_auto_kick=True, session_auto_kick=False)
        assert loaded.accounts[1] == other


def test_empty_master_resume_is_explicit():
    save(False, False, False)
    result = invoke("all", True)
    assert result.exit_code == 0
    assert "No windows selected" in result.output and "Nothing will run" in result.output
    result = CliRunner().invoke(cli.cli, ["auto", "status"])
    assert "no windows selected" in result.output


def test_paused_preferences_render_as_paused_in_every_terminal_view(monkeypatch):
    account = save(False, True, True).accounts[0]
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=180))
    cli._render_auto_status_table([account])
    assert "paused" in output.getvalue() and "enabled" not in output.getvalue()
    output.seek(0)
    output.truncate()
    monkeypatch.setattr(cli, "_codex_account_limit_metadata", lambda _: {
        "detected_plan_type": "pro", "plan_stale": False,
        "session_limit_effective": "absent", "session_limit_override": "auto",
    })
    cli._render_accounts_table(Config(accounts=[account]), [account])
    assert "Weekly: paused" in output.getvalue()
    assert "Session: paused" in output.getvalue()
    label = interactive._auto_account_menu_choices([account])[0].name
    assert "Weekly paused" in label and "Session paused" in label


@pytest.fixture
def mcp(monkeypatch):
    core = TokenKickMCPCore()

    def run(argv, **kwargs):
        result = CliRunner().invoke(cli.cli, argv, env={"TK_APP_MODE": "1"})
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    monkeypatch.setattr(core, "_run", run)
    return core


@pytest.mark.parametrize("scope,enabled", list(itertools.product(["all", "weekly", "session"], [False, True])))
def test_mcp_preview_matches_persisted_cli_apply(mcp, scope, enabled):
    config = save(False, True, False)
    preview = mcp.tokenkick_auto_set_preview(account="codex", enabled=enabled, scope=scope)
    assert preview["payload"]["before"] == auto_controls.controls(config.accounts[0])
    result = mcp.tokenkick_auto_set(
        account="codex", enabled=enabled, scope=scope,
        preview_token=preview["preview_token"]["token"], confirm=True,
        quota_ack=True, accept_risk=True,
    )
    assert result["ok"]
    assert preview["payload"]["after"] == auto_controls.controls(Config.load().accounts[0])


def test_old_policy_previews_rejected_by_mcp_and_cli(mcp):
    config = save(False, True, False)
    target = cli._auto_status_payload(config.accounts)[0]
    old_fingerprint = hashlib.sha256(json.dumps(target, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    token = mcp.token_store.create(
        tool_name="tokenkick_auto_set", argv=mcp._auto_argv("codex", True, "weekly"),
        risk="dangerous_quota_operational", expected_plan=old_fingerprint,
    )
    with pytest.raises(ValueError, match="changed"):
        mcp.tokenkick_auto_set(account="codex", enabled=True, scope="weekly",
                              preview_token=token.token, confirm=True, quota_ack=True, accept_risk=True)
    result = invoke("weekly", True, "--expected-auto-fingerprint", old_fingerprint, "--json-output")
    assert result.exit_code != 0 and "stale_preview" in result.output
    assert Config.load().accounts == config.accounts


@pytest.mark.parametrize("scope", ["all", "weekly", "session"])
def test_window_enable_cannot_bypass_consent(scope):
    config = save(False, True, False)
    result = CliRunner().invoke(cli.cli, ["auto", *([] if scope == "all" else [scope]), "enable", "codex", "--json-output"])
    assert result.exit_code != 0
    assert Config.load().accounts == config.accounts
