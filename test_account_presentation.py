"""Regression coverage for account settings and live-status presentation."""

from dataclasses import replace
import io
import json

import click
from click.testing import CliRunner
import pytest
from rich.console import Console

from tokenkick import cli as cli_mod
from tokenkick import interactive
from tokenkick.models import AccountConfig, Config, DataSource


def agy(family, **kwargs):
    return AccountConfig(
        label="custom saved label", provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI, antigravity_family=family, **kwargs,
    )


@pytest.fixture
def saved_accounts(monkeypatch):
    def install(accounts):
        config = Config(accounts=accounts)
        monkeypatch.setattr(cli_mod.Config, "load", lambda: config)
        monkeypatch.setattr(cli_mod, "_load_accounts", lambda _: accounts)
        monkeypatch.setattr(cli_mod.Config, "save", lambda _: pytest.fail("Settings view must not save"))
        monkeypatch.setattr(cli_mod, "console", Console(width=180, force_terminal=False))
        return config
    return install


@pytest.mark.parametrize("account,expected", [
    (agy("gemini"), "auto (Gemini Flash, Low)"),
    (agy("claude_gpt"), "auto (Claude Haiku → Sonnet, Low)"),
    (agy("gemini", kick_model="Gemini 3.8 Flash (Low)"), "Gemini 3.8 Flash (Low)"),
    (agy("claude_gpt", kick_model="Claude Sonnet 5.5 (Low)"), "Claude Sonnet 5.5 (Low)"),
    (AccountConfig(label="Claude", provider="claude"), "haiku (default; low effort, thinking off)"),
    (AccountConfig(label="Claude", provider="claude", kick_model="sonnet"), "sonnet (low effort, thinking off)"),
    (AccountConfig(label="Codex", provider="codex"), "auto (Luna, lowest effort)"),
    (AccountConfig(label="Codex", provider="codex", kick_model="custom-model"), "custom-model"),
    (agy(None), "—"),
    (AccountConfig(label="Gemini", provider="gemini"), "—"),
])
def test_accounts_json_describes_provider_model_policy_without_catalog_reads(
    monkeypatch, saved_accounts, account, expected,
):
    saved_accounts([account])
    # A settings read must neither resolve a live catalog nor prepare a request.
    monkeypatch.setattr("tokenkick.kicker.read_antigravity_models", lambda *_: pytest.fail("Unexpected catalog read"))
    monkeypatch.setattr("tokenkick.kicker.read_codex_model_catalog", lambda *_: pytest.fail("Unexpected catalog read"))
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "list", "--json-output"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)["payload"]["accounts"][0]
    assert row["kick_model"] == expected
    assert row["label"] == account.label
    assert row["antigravity_family"] == account.antigravity_family


@pytest.mark.parametrize("family,family_text,model_text", [
    ("gemini", "Gemini family", "Gemini Flash"),
    ("claude_gpt", "Claude/GPT family", "Claude Haiku → Sonnet"),
])
def test_antigravity_list_and_detail_identify_family_without_renaming(
    saved_accounts, family, family_text, model_text,
):
    account = agy(family)
    config = saved_accounts([account])
    before = account.to_dict()
    for args in (["accounts", "list"], ["accounts", "detail", account.label]):
        result = CliRunner().invoke(cli_mod.cli, args)
        assert result.exit_code == 0, result.output
        assert account.label in result.output
        assert family_text in result.output
        assert model_text in result.output
        assert "Luna" not in result.output
    assert config.accounts[0].to_dict() == before


@pytest.mark.parametrize("master,weekly,session", [
    (True, False, True), (False, True, False), (True, True, False),
])
def test_settings_table_and_json_distinguish_all_auto_switches(
    saved_accounts, master, weekly, session,
):
    account = agy("gemini", auto_kick=master, weekly_auto_kick=weekly, session_auto_kick=session)
    saved_accounts([account])
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "list"])
    assert result.exit_code == 0, result.output
    assert "TokenKick Account Settings" in result.output
    for label, value in [("Master", master), ("Weekly", weekly), ("Session", session)]:
        assert f"{label}: {'on' if value else 'off'}" in result.output
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "list", "--json-output"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)["payload"]["accounts"][0]
    assert (row["auto_kick"], row["weekly_auto_kick"], row["session_auto_kick"]) == (master, weekly, session)


def test_monitor_only_settings_do_not_advertise_enabled_automation(saved_accounts):
    saved_accounts([agy(None, auto_kick=True, weekly_auto_kick=True, session_auto_kick=True)])
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "list"])
    assert result.exit_code == 0, result.output
    assert "monitor-only" in result.output
    assert "Combined families" in result.output
    assert "Master: on" not in result.output
    assert "Luna" not in result.output


def test_accounts_menu_separates_settings_from_live_provider_status(monkeypatch, saved_accounts):
    saved_accounts([agy("gemini")])
    selections = iter(["settings", "status", interactive.MENU_EXIT])
    calls = []
    def select(_title, choices):
        assert {c.value: c.name for c in choices}.items() >= {
            "settings": "Show account settings", "status": "Show live usage and resets",
        }.items()
        return next(selections)
    monkeypatch.setattr(interactive, "_select", select)
    monkeypatch.setattr(cli_mod.accounts_list, "callback", lambda **kw: calls.append(("settings", kw)))
    monkeypatch.setattr(cli_mod.status, "callback", lambda **kw: calls.append(("status", kw)))
    interactive._accounts_menu(click.Context(cli_mod.cli))
    assert calls == [
        ("settings", {"as_json": False}),
        ("status", {"as_json": False, "codex_only": False, "show_all": False,
                    "account_label": None, "refresh": True, "verbose": False}),
    ]


def test_screenshot_accounts_render_at_normal_terminal_width(monkeypatch):
    accounts = [
        AccountConfig(label="codex (exampleaccount)", provider="codex"),
        AccountConfig(label="claude (exampleaccount)", provider="claude", auto_kick=True),
        replace(agy("gemini", auto_kick=True), label="antigravity"),
        replace(agy("claude_gpt", auto_kick=True), label="antigravity (Claude)"),
    ]
    output = io.StringIO()
    monkeypatch.setattr(cli_mod, "console", Console(width=120, file=output, force_terminal=False))
    cli_mod._render_accounts_table(Config(accounts=accounts), accounts)
    text = output.getvalue()
    assert "…" not in text
    assert all(len(line) <= 120 for line in text.splitlines())
    for word in ("Master:", "Weekly:", "Session:", "Gemini", "Claude/GPT", "thinking", "off"):
        assert word in text
