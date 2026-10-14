"""Terminal selection conventions, real key handling and review boundaries."""

import asyncio
from types import SimpleNamespace

import click
import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tokenkick import cli, interactive as ui
from tokenkick.models import AccountConfig, Config


class RecordingOutput(DummyOutput):
    def __init__(self):
        self.fragments = []

    def write(self, text):
        self.fragments.append(text)

    def write_raw(self, text):
        self.fragments.append(text)


def run_keyboard_prompt(monkeypatch, keys, *, checkbox=True):
    output = RecordingOutput()
    counts = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        steps = iter(keys)

        def execute(prompt):
            def rendered(app):
                screen = app.renderer.last_rendered_screen
                if screen is not None:
                    output.fragments.extend(
                        "".join(cell.char for _, cell in sorted(row.items())) + "\n"
                        for _, row in sorted(screen.data_buffer.items())
                    )
                if checkbox:
                    counts.append(len(prompt.selected_choices))
                key = next(steps, None)
                if key is not None:
                    pipe.send_text(key)

            prompt.application.after_render += rendered
            return asyncio.run(asyncio.wait_for(prompt.execute_async(), timeout=4))

        monkeypatch.setattr(ui, "_execute_inquirer_prompt", execute)
        choices = [
            ui.MenuChoice("Alpha", "a"),
            ui.MenuChoice("Beta", "b"),
            ui.MenuChoice("Back", ui.MENU_EXIT),
        ]
        result = (
            ui._checkbox("Choose accounts", choices)
            if checkbox
            else ui._select("Choose account", choices)
        )
    return result, counts, "".join(output.fragments)


def test_real_space_toggles_and_enter_submits_all_marked_accounts(monkeypatch):
    result, counts, output = run_keyboard_prompt(monkeypatch, [" ", "\x1b[B", " ", "\r"])
    assert result == ["a", "b"]
    assert 0 in counts and 1 in counts and 2 in counts
    assert "☐" in output and "☑" in output
    assert "0 accounts selected" in output
    assert "1 account selected" in output
    assert "2 accounts selected" in output
    assert (
        "Space Toggle" in output and "Enter Review selection" in output and "Esc Cancel" in output
    )
    assert "Back" not in output


def test_real_enter_without_space_does_not_select_highlighted_account(monkeypatch):
    result, _, _ = run_keyboard_prompt(monkeypatch, ["\r"])
    assert result == []


def test_real_space_can_uncheck_and_count_returns_to_zero(monkeypatch):
    result, counts, _ = run_keyboard_prompt(monkeypatch, [" ", " ", "\r"])
    assert result == []
    assert counts[:3] == [0, 1, 0]


def test_real_escape_discards_even_a_nonempty_selection(monkeypatch):
    result, counts, _ = run_keyboard_prompt(monkeypatch, [" ", "\x1b"])
    assert result == [ui.MENU_EXIT]
    assert 1 in counts


def test_real_single_choice_keeps_enter_to_choose(monkeypatch):
    result, _, output = run_keyboard_prompt(monkeypatch, ["\x1b[B", "\r"], checkbox=False)
    assert result == "b"
    assert "Enter Choose" in output and "Esc Back" in output


@pytest.mark.parametrize(
    "choices",
    [
        [],
        [ui.MenuChoice("Back", ui.MENU_EXIT)],
        [{"name": "Back", "value": ui.MENU_EXIT}],
        [ui.MENU_EXIT],
    ],
)
def test_empty_choices_or_navigation_only_do_not_open_a_checkbox(monkeypatch, choices, capsys):
    monkeypatch.setattr(
        ui, "_execute_inquirer_prompt", lambda *_: pytest.fail("No selectable accounts")
    )
    assert ui._checkbox("Choose accounts", choices) == []
    assert "No matching accounts" in capsys.readouterr().out


def test_account_labels_are_concise_keep_identity_and_explain_families():
    accounts = [
        AccountConfig(
            label="codex (work)",
            provider="codex",
            provider_home="/private/home",
            visible=False,
            auto_kick=True,
        ),
        AccountConfig(
            label="antigravity", provider="antigravity", antigravity_family="gemini", auto_kick=True
        ),
        AccountConfig(
            label="antigravity (Claude)", provider="antigravity", antigravity_family="claude_gpt"
        ),
        AccountConfig(label="Personal", provider="gemini"),
    ]
    before = [a.to_dict() for a in accounts]
    choices = ui._account_menu_choices(accounts, only_kickable=False)
    assert [c.value for c in choices] == [a.label for a in accounts]
    assert "[hidden]" in choices[0].name
    assert "Gemini" in choices[1].name and "Claude/GPT" in choices[2].name
    assert "monitor-only" in choices[3].name
    assert all(
        "direct home" not in c.name and "kickable" not in c.name and "/private" not in c.name
        for c in choices
    )
    auto = ui._auto_account_menu_choices(accounts)
    assert any(
        "Auto-kick on" in c.name and "Weekly enabled" in c.name and "Session enabled" in c.name for c in auto
    )
    assert [a.to_dict() for a in accounts] == before


@pytest.mark.parametrize("scope", ["all", "weekly", "session"])
@pytest.mark.parametrize("enabled", [False, True])
def test_auto_review_describes_exact_switch_transitions(scope, enabled):
    a = AccountConfig(
        label="Test",
        provider="claude",
        auto_kick=False,
        weekly_auto_kick=True,
        session_auto_kick=False,
    )
    changes = ui._auto_selection_changes([a], [a.label], scope, enabled)
    row = changes[0]
    assert "Test" in row
    assert f"Master off → {'on' if enabled else 'off'}" in row
    assert f"Weekly on → {'off' if scope == 'weekly' and not enabled else 'on'}" in row
    assert f"Session off → {'on' if enabled and scope == 'session' else 'off'}" in row
    assert ("automation paused" in row) == (not enabled)


@pytest.fixture
def flow(monkeypatch):
    accounts = [
        AccountConfig(label="Alpha", provider="claude", auto_kick=True),
        AccountConfig(label="Beta", provider="codex", auto_kick=True),
    ]
    config = Config(accounts=accounts)
    monkeypatch.setattr(ui.Config, "load", lambda: config)
    monkeypatch.setattr(cli, "_load_status_cache", lambda *_: None)
    monkeypatch.setattr(cli, "_render_account_notifications_table", lambda *_: None)
    pending = [
        SimpleNamespace(account_label=a.label, kick_at="2026-10-13T14:00:00Z") for a in accounts
    ]
    monkeypatch.setattr(cli, "_orchestrated_pending_kicks", lambda **_: pending)
    monkeypatch.setattr(cli, "_render_orchestrated_pending_kicks", lambda *_a, **_kw: None)
    return config


def run_flow(monkeypatch, kind, selected, confirm):
    choices = {
        "hide": ["hide_selected", "exit"],
        "show": ["show_selected", "exit"],
        "auto": [
            "disable_selected",
            *(["session"] if selected and ui.MENU_EXIT not in selected else []),
            "exit",
        ],
        "notifications": ["disable_selected", "exit"],
        "orchestration": ["selected", "exit"],
    }
    picks = iter(choices[kind])
    monkeypatch.setattr(ui, "_select", lambda *_a, **_kw: next(picks))
    checkbox_choices = []

    def checkbox(message, options):
        checkbox_choices.extend(options)
        return selected

    monkeypatch.setattr(ui, "_checkbox", checkbox)
    confirmations = []
    applied = []

    def review(message, **kwargs):
        assert not applied
        assert kwargs["default"] is False
        confirmations.append(message)
        return confirm

    def apply(*args, **kwargs):
        assert confirmations and confirm
        applied.append((args, kwargs))
        return SimpleNamespace(removed=["Alpha"])

    monkeypatch.setattr(ui, "_confirm", review)
    monkeypatch.setattr(ui, "_invoke", apply)
    monkeypatch.setattr(ui, "cancel_orchestrated_pending_kicks", apply)
    ctx = click.Context(cli.cli)
    if kind in {"hide", "show"}:
        ui._accounts_menu(ctx)
    elif kind == "auto":
        ui._auto_menu(ctx)
    elif kind == "notifications":
        ui._notification_accounts_menu(ctx)
    else:
        ui._orchestration_cancel_menu()
    assert all(c.value != ui.MENU_EXIT for c in checkbox_choices)
    return confirmations, applied


@pytest.mark.parametrize("kind", ["hide", "show", "auto", "notifications", "orchestration"])
@pytest.mark.parametrize("confirm", [False, True])
def test_every_bulk_flow_reviews_named_changes_before_applying(
    monkeypatch, flow, kind, confirm, capsys
):
    if kind == "show":
        for a in flow.accounts:
            a.visible = False
    confirmations, applied = run_flow(monkeypatch, kind, ["Alpha"], confirm)
    assert len(confirmations) == 1
    assert len(applied) == (1 if confirm else 0)
    output = capsys.readouterr().out
    assert "Review changes:" in output and "Alpha" in output
    assert "Beta:" not in output
    expected = {
        "hide": "visible → hidden",
        "show": "hidden → visible",
        "auto": "Session on → off",
        "notifications": "notifications on → off",
        "orchestration": "cancel pending kick at 2026-10-13T14:00:00Z",
    }
    assert expected[kind] in output


@pytest.mark.parametrize("kind", ["hide", "show", "auto", "notifications", "orchestration"])
@pytest.mark.parametrize("selected", [[], [ui.MENU_EXIT]])
def test_every_bulk_flow_empty_or_escape_leaves_state_alone(monkeypatch, flow, kind, selected):
    confirmations, applied = run_flow(monkeypatch, kind, selected, True)
    assert not confirmations and not applied


@pytest.mark.parametrize(
    "action,confirm", [("disable_one", True), ("enable_one", True), ("disable_one", False)]
)
def test_single_account_auto_action_uses_enter_selection_without_checkbox(
    monkeypatch, flow, action, confirm, capsys
):
    picks = iter([action, "Beta", "all", "exit"])
    monkeypatch.setattr(ui, "_select", lambda *_a, **_kw: next(picks))
    monkeypatch.setattr(
        ui, "_checkbox", lambda *_: pytest.fail("Single account selection uses Enter")
    )
    calls = []

    def review(message, **kwargs):
        assert kwargs["default"] is False
        assert "Beta" in capsys.readouterr().out
        return confirm

    monkeypatch.setattr(ui, "_confirm", review)
    monkeypatch.setattr(ui, "_invoke", lambda *args, **kwargs: calls.append((args, kwargs)))
    ui._auto_menu(click.Context(cli.cli))
    assert len(calls) == int(confirm)
    if calls:
        assert calls[0][1]["label"] == "Beta"
        assert calls[0][0][1] is (cli.auto_enable if action == "enable_one" else cli.auto_disable)


def test_single_account_auto_escape_does_not_review_or_apply(monkeypatch, flow):
    picks = iter(["disable_one", "exit", "exit"])
    monkeypatch.setattr(ui, "_select", lambda *_a, **_kw: next(picks))
    monkeypatch.setattr(ui, "_confirm", lambda *_a, **_kw: pytest.fail("Nothing selected"))
    monkeypatch.setattr(ui, "_invoke", lambda *_a, **_kw: pytest.fail("Esc must not apply"))
    ui._auto_menu(click.Context(cli.cli))
