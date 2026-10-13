"""Notification menu, credential reuse and per-destination delivery regressions."""

import copy
import json

import click
from click.testing import CliRunner
import pytest

from tokenkick import cli as app, interactive
from tokenkick.models import AccountConfig, Config, NotifyConfig

TOKEN = "123456:secret-never-display-this"


@pytest.fixture(autouse=True)
def local_settings(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.models.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.models.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(app, "_load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(
        app,
        "_telegram_remote_status_payload",
        lambda *_: {
            "running": True,
            "enabled": True,
            "configured": True,
        },
    )
    monkeypatch.setattr(
        app, "notify_test", lambda *_: pytest.fail("Unexpected notification delivery")
    )
    config = Config(
        accounts=[AccountConfig(label="Claude", provider="claude")],
        notifications=NotifyConfig(
            enabled=True,
            ntfy_topic="saved-topic",
            telegram_bot_token=TOKEN,
            telegram_chat_id="-12345",
            enabled_backends=["ntfy"],
            policy="errors",
        ),
        telegram_remote_enabled=True,
        poll_interval_minutes=13,
    )
    config.save()
    return config


def invoke(*args):
    return CliRunner().invoke(app.cli, list(args))


def menu(monkeypatch, selections, *, confirm=True):
    answers = iter(selections)
    seen = []

    def choose(title, choices, **kwargs):
        seen.append((title, choices))
        result = next(answers)
        choice = next(c for c in choices if c.value == result)
        assert choice.enabled, f"Selected disabled item: {choice.name}"
        return result

    monkeypatch.setattr(interactive, "_select", choose)
    monkeypatch.setattr(interactive, "_confirm", lambda *_a, **_kw: confirm)
    interactive._notifications_menu(click.Context(app.cli))
    return seen


@pytest.mark.parametrize("as_json", [False, True])
def test_overview_shows_saved_destinations_and_remote_without_exposing_token(
    local_settings, as_json
):
    before = Config.load()._data()
    result = invoke("notify", *(["--json-output"] if as_json else []))
    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output and "secret-never" not in result.output
    assert "saved-topic" in result.output and "-12345" in result.output
    if as_json:
        row = json.loads(result.output)["payload"]
        assert row["telegram_token_saved"] is True
        assert row["enabled_backends"] == ["ntfy"]
        assert row["telegram_remote_running"] is True
    else:
        assert "saved ••••••" in result.output
        assert "errors/checks only" in result.output and "routine events" in result.output
        assert "running" in result.output and "independent" in result.output
    assert Config.load()._data() == before


@pytest.mark.parametrize("backend", ["ntfy", "telegram"])
def test_enable_and_disable_reuse_and_retain_credentials(local_settings, backend):
    before = Config.load()
    for action in ("--disable-backend", "--enable-backend"):
        result = invoke("notify", action, backend, "--json-output")
        assert result.exit_code == 0, result.output
        saved = Config.load()
        assert saved.notifications.telegram_bot_token == TOKEN
        assert saved.notifications.ntfy_topic == "saved-topic"
        assert saved.notifications.telegram_chat_id == "-12345"
        assert saved.accounts == before.accounts
        assert saved.telegram_remote_enabled == before.telegram_remote_enabled
        assert saved.poll_interval_minutes == before.poll_interval_minutes
        assert saved.notifications.policy == "errors"
        assert (backend in saved.notifications.enabled_backends) == (action == "--enable-backend")


@pytest.mark.parametrize("backend", ["ntfy", "telegram"])
def test_enable_requires_complete_credentials_without_saving(local_settings, backend):
    local_settings.notifications.ntfy_topic = None
    local_settings.notifications.telegram_bot_token = None
    local_settings.save()
    before = Config.load()._data()
    result = invoke("notify", "--enable-backend", backend, "--json-output")
    assert result.exit_code == 2
    assert "Save complete" in result.output
    assert Config.load()._data() == before


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("backend", ["ntfy", "telegram"])
def test_editing_credentials_preserves_push_routes_and_remote_settings(
    local_settings, backend, enabled
):
    local_settings.notifications.enabled = enabled
    local_settings.notifications.enabled_backends = ["ntfy", "telegram"] if enabled else []
    local_settings.save()
    expected = copy.deepcopy(Config.load())
    if backend == "ntfy":
        args = ["--save-ntfy", "updated-topic"]
        expected.notifications.ntfy_topic = "updated-topic"
    else:
        args = ["--save-telegram", "new-secret", "new-chat"]
        expected.notifications.telegram_bot_token = "new-secret"
        expected.notifications.telegram_chat_id = "new-chat"
    result = invoke("notify", *args, "--json-output")
    assert result.exit_code == 0, result.output
    assert "new-secret" not in result.output and TOKEN not in result.output
    assert Config.load()._data() == expected._data()


@pytest.mark.parametrize(
    "args",
    [
        ["--save-ntfy", " "],
        ["--save-telegram", "", "chat"],
        ["--enable-backend", "telegram", "test"],
        ["--save-ntfy", "new", "--disable-backend", "ntfy"],
    ],
)
def test_invalid_or_conflicting_actions_never_save(args):
    before = Config.load()._data()
    result = invoke("notify", *args, "--json-output")
    assert result.exit_code == 2
    assert Config.load()._data() == before


@pytest.mark.parametrize(
    "ntfy_ok,telegram_ok", [(True, True), (True, False), (False, True), (False, False)]
)
def test_combined_test_requires_every_enabled_destination(
    monkeypatch, local_settings, ntfy_ok, telegram_ok
):
    local_settings.notifications.enabled_backends = ["ntfy", "telegram"]
    local_settings.save()
    calls = []

    def send(config):
        calls.append(config.backend)
        return {"ntfy": ntfy_ok, "telegram": telegram_ok}[config.backend]

    monkeypatch.setattr(app, "notify_test", send)
    before = Config.load()._data()
    result = invoke("notify", "test", "--json-output")
    body = json.loads(result.output)
    assert result.exit_code == (0 if ntfy_ok and telegram_ok else 1)
    assert body["ok"] == (ntfy_ok and telegram_ok)
    assert [r["status"] for r in body["payload"]["results"]] == [
        "sent" if ntfy_ok else "failed",
        "sent" if telegram_ok else "failed",
    ]
    assert calls == ["ntfy", "telegram"]
    assert Config.load()._data() == before


def test_transport_exception_is_redacted_and_other_destination_still_tested(
    monkeypatch, local_settings
):
    local_settings.notifications.enabled_backends = ["ntfy", "telegram"]
    local_settings.save()
    calls = []

    def send(config):
        calls.append(config.backend)
        if config.backend == "ntfy":
            raise RuntimeError("https://secret.example/" + TOKEN)
        return True

    monkeypatch.setattr(app, "notify_test", send)
    result = invoke("notify", "test")
    assert result.exit_code == 1
    assert "ntfy: failed (delivery_failed)" in result.output
    assert "telegram: sent" in result.output
    assert TOKEN not in result.output
    assert calls == ["ntfy", "telegram"]


def test_combined_test_reports_disabled_telegram_without_sending(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "notify_test", lambda n: calls.append(n.backend) or True)
    result = invoke("notify", "test", "--json-output")
    assert result.exit_code == 0
    assert calls == ["ntfy"]
    assert json.loads(result.output)["payload"]["results"][1] == {
        "backend": "telegram",
        "status": "skipped",
        "reason": "push_disabled",
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_all_disabled_reports_no_delivery_and_never_sends(local_settings, as_json):
    local_settings.notifications.enabled = False
    local_settings.save()
    result = invoke("notify", "test", *(["--json-output"] if as_json else []))
    assert result.exit_code == 1
    assert "push_disabled" in result.output


def test_enabled_backend_missing_credentials_is_a_failure_not_hidden(monkeypatch, local_settings):
    local_settings.notifications.enabled_backends = ["ntfy", "telegram"]
    local_settings.notifications.telegram_bot_token = None
    local_settings.save()
    calls = []
    monkeypatch.setattr(app, "notify_test", lambda n: calls.append(n.backend) or True)
    result = invoke("notify", "test", "--json-output")
    assert result.exit_code == 1
    assert calls == ["ntfy"]
    assert (
        json.loads(result.output)["payload"]["results"][1]["reason"]
        == "missing_telegram_credentials"
    )


def test_legacy_single_backend_still_only_tests_that_backend(monkeypatch, local_settings):
    local_settings.notifications.enabled_backends = None
    local_settings.notifications.backend = "telegram"
    local_settings.save()
    calls = []
    monkeypatch.setattr(app, "notify_test", lambda n: calls.append(n.backend) or True)
    assert invoke("notify", "test").exit_code == 0
    assert calls == ["telegram"]


def test_menu_shows_current_state_and_actual_test_targets(monkeypatch, capsys):
    seen = menu(monkeypatch, ["exit"])
    names = [c.name for c in seen[0][1]]
    assert "Test enabled destinations: ntfy" in names
    assert "Telegram settings (push off)" in names
    output = capsys.readouterr().out
    assert "saved-topic" in output and "saved ••••••" in output
    assert TOKEN not in output


@pytest.mark.parametrize("confirm", [False, True])
def test_individual_test_explains_disabled_push_and_never_enables_it(monkeypatch, capsys, confirm):
    before = Config.load()._data()
    calls = []
    monkeypatch.setattr(app, "notify_test", lambda n: calls.append(n.backend) or True)
    menu(monkeypatch, ["test_telegram", "exit"], confirm=confirm)
    assert calls == (["telegram"] if confirm else [])
    assert Config.load()._data() == before
    assert "push is off" in capsys.readouterr().out


def test_no_enabled_targets_menu_explains_individual_tests(monkeypatch, local_settings, capsys):
    local_settings.notifications.enabled = False
    local_settings.save()
    menu(monkeypatch, ["test", "exit"])
    assert "No push destinations enabled" in capsys.readouterr().out


@pytest.mark.parametrize("confirm", [False, True])
def test_backend_menu_enables_saved_telegram_without_prompting_for_credentials(
    monkeypatch, confirm
):
    monkeypatch.setattr(
        interactive, "_secret_action", lambda *_a, **_kw: pytest.fail("Must reuse token")
    )
    monkeypatch.setattr(
        interactive, "_text_action", lambda *_a, **_kw: pytest.fail("Must reuse chat")
    )
    menu(monkeypatch, ["telegram", "enable", "exit", "exit"], confirm=confirm)
    assert Config.load().notifications.enabled_backends == (
        ["ntfy", "telegram"] if confirm else ["ntfy"]
    )


@pytest.mark.parametrize("action", ["chat", "token", "topic"])
@pytest.mark.parametrize("confirm", [False, True])
def test_edit_submenus_prefill_nonsecret_fields_preserve_other_credentials_and_cancel(
    monkeypatch, action, confirm
):
    before = Config.load()._data()
    defaults = []
    secret_calls = []

    def text(*args, **kwargs):
        defaults.append(kwargs.get("default"))
        return "updated-topic" if action == "topic" else "new-chat"

    monkeypatch.setattr(interactive, "_text_action", text)
    monkeypatch.setattr(
        interactive, "_secret_action", lambda *_a, **_kw: secret_calls.append(True) or "new-secret"
    )
    menu(
        monkeypatch,
        ["ntfy" if action == "topic" else "telegram", action, "exit", "exit"],
        confirm=confirm,
    )
    expected = copy.deepcopy(before)
    if confirm:
        key, value = {
            "chat": ("telegram_chat_id", "new-chat"),
            "token": ("telegram_bot_token", "new-secret"),
            "topic": ("ntfy_topic", "updated-topic"),
        }[action]
        expected["notifications"][key] = value
    assert Config.load()._data() == expected
    assert secret_calls == ([True] if action == "token" else [])
    assert defaults == (
        [] if action == "token" else ["saved-topic" if action == "topic" else "-12345"]
    )


def test_back_during_credential_entry_never_partially_saves(monkeypatch):
    before = Config.load()._data()
    monkeypatch.setattr(interactive, "_secret_action", lambda *_a, **_kw: "new-token")
    monkeypatch.setattr(interactive, "_text_action", lambda *_a, **_kw: None)
    menu(monkeypatch, ["telegram", "configure", "exit", "exit"])
    assert Config.load()._data() == before


def test_routes_distinguish_inheritance_custom_disabled_and_blocked(local_settings):
    local_settings.accounts = [
        AccountConfig(label="Inherited", provider="claude"),
        AccountConfig(label="Custom", provider="claude", notification_backends=["telegram"]),
        AccountConfig(label="Off", provider="codex", notifications_enabled=False),
    ]
    local_settings.save()
    result = invoke("accounts", "notifications", "--json-output")
    assert result.exit_code == 0
    rows = json.loads(result.output)["payload"]["accounts"]
    assert [r["route_mode"] for r in rows] == ["inherit", "custom", "disabled"]
    assert rows[0]["effective_backends"] == ["ntfy"]
    assert rows[1]["effective_backends"] == []
    assert rows[1]["blocked_backends"] == ["telegram"]
    assert "push disabled" in rows[1]["route_description"]
    assert rows[2]["effective_backends"] == []


def test_account_routes_are_visible_on_entry_and_editable_alongside_summary(monkeypatch, capsys):
    menu(monkeypatch, ["accounts", "set_route", "Claude", "telegram", "exit", "exit"])
    assert Config.load().accounts[0].notification_backends == ["telegram"]
    output = capsys.readouterr().out
    assert "Inherit global" in output and "Custom" in output and "push disabled" in output
    assert "do not enable a backend" in output
