"""Regression boundaries from the Opus review; no live provider calls."""

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tokenkick import action_state, cli, kicker, sources
from tokenkick.antigravity import _parse_antigravity_reset_prose_seconds
from tokenkick.codexbar_source import _select_codexbar_entry
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    KickEvent,
)


def account(provider="claude"):
    return AccountConfig(
        label="fixture",
        provider=provider,
        identity_email="fixture@example.test",
        source=DataSource.CLAUDE_DIRECT if provider == "claude" else DataSource.CODEX_DIRECT,
        provider_home="/unused-fixture",
    )


def test_claude_identity_mismatch_blocks_every_fallback(monkeypatch):
    monkeypatch.setattr(
        sources, "read_claude_identity", lambda: SimpleNamespace(email="other@example.test")
    )
    fallback = Mock(side_effect=AssertionError("must not read another account"))
    monkeypatch.setattr(sources, "_fetch_codexbar_cli", fallback)
    monkeypatch.setattr(sources, "_fetch_claude_probe", fallback)
    result = sources._fetch_claude_direct(replace(account(), status_probe_enabled=True))
    assert result.state == AccountState.UNKNOWN
    assert "identity" in result.error
    fallback.assert_not_called()


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("identity", [None, SimpleNamespace(email="other@example.test")])
def test_dispatch_rejects_unverified_identity(monkeypatch, provider, identity):
    monkeypatch.setattr(
        kicker, "_kick_invocation", lambda *a, **kw: kicker.KickInvocation(["fixture", "-p", "OK"])
    )
    monkeypatch.setattr(kicker, "read_claude_identity", lambda: identity)
    monkeypatch.setattr(kicker, "read_codex_identity", lambda home: identity)
    send = Mock(side_effect=AssertionError("must not generate"))
    monkeypatch.setattr(kicker.subprocess, "run", send)
    result = kicker.kick_account(account(provider), record=False)
    assert not result.success and not result.confirmed and result.dispatch_state == "not_sent"
    send.assert_not_called()


def test_codexbar_account_selection_requires_matching_identity():
    entries = [{"provider": "claude", "usage": {"primary": {"usedPercent": 10}}}]
    assert _select_codexbar_entry(entries, "claude", "fixture@example.test") == {}


def test_preparation_exception_does_not_poison_action(tmp_path):
    with pytest.raises(ValueError), action_state.account_action(tmp_path, "fixture"):
        raise ValueError("before dispatch")
    with action_state.account_action(tmp_path, "fixture"):
        pass


def test_dispatch_exception_retains_guard(tmp_path):
    with pytest.raises(ValueError), action_state.account_action(tmp_path, "fixture"):
        action_state.mark_action_dispatched()
        raise ValueError("after dispatch")
    with (
        pytest.raises(action_state.UncertainActionError),
        action_state.account_action(tmp_path, "fixture"),
    ):
        pass


def test_uncertain_result_retains_guard_even_on_normal_return(tmp_path):
    with action_state.account_action(tmp_path, "fixture"):
        action_state.mark_action_dispatched()
        action_state.mark_action_uncertain()
    with (
        pytest.raises(action_state.UncertainActionError),
        action_state.account_action(tmp_path, "fixture"),
    ):
        pass


@pytest.mark.parametrize(
    "text,expected",
    [("Reset time unknown", None), ("Resets in 3d (known issue)", 259200), ("Resets now", 0)],
)
def test_antigravity_reset_prose_requires_explicit_ready(text, expected):
    assert _parse_antigravity_reset_prose_seconds(text) == expected


def test_expired_weekly_clock_guards_recent_pending_request(monkeypatch):
    now = time.time()
    status = AccountStatus(
        label="fixture", state=AccountState.FRESH, resets_at=now - 3600, window_minutes=10080
    )
    event = KickEvent(
        label="fixture",
        timestamp=now - 120,
        success=True,
        confirmed=False,
        codex_surface="repo",
        codex_confirmation_method="pending_reset_clock",
        post_kick_status="pending",
    )
    assert cli._was_pending_codex_fire_all_in_current_weekly_window(
        account("codex"), status, [event]
    )
    assert not cli._was_pending_codex_fire_all_in_current_weekly_window(
        account("codex"), status, [replace(event, timestamp=now - 604800)]
    )


def test_claude_weekly_response_is_not_anchor_confirmation(monkeypatch):
    monkeypatch.setattr(
        cli, "kick_account", lambda *a, **kw: KickEvent(label="fixture", response_text="OK")
    )
    monkeypatch.setattr(
        cli,
        "_run_claude_usage_touch",
        lambda *a, **kw: (KickEvent(label="fixture", success=False), None),
    )
    event = cli._kick_claude_usage_session(account(), Config(), kick_type="kick")
    assert event.success and not event.confirmed
    assert event.post_kick_status == "provider_accepted"


def test_claude_weekly_moved_reset_is_verified(monkeypatch):
    now = time.time()
    before = AccountStatus(label="fixture", state=AccountState.FRESH, resets_at=now - 60)
    after = AccountStatus(label="fixture", state=AccountState.ACTIVE, resets_at=now + 604800)
    monkeypatch.setattr(
        cli, "kick_account", lambda *a, **kw: KickEvent(label="fixture", response_text="OK")
    )
    monkeypatch.setattr(
        cli, "_run_claude_usage_touch", lambda *a, **kw: (KickEvent(label="fixture"), after)
    )
    event = cli._kick_claude_usage_session(account(), Config(), pre_status=before, kick_type="kick")
    assert event.confirmed and event.evidence_provider_moved


def test_run_changed_approval_cannot_reach_execution(monkeypatch):
    import json
    from click.testing import CliRunner

    a = account()
    st = AccountStatus(label=a.label, state=AccountState.FRESH)
    monkeypatch.setattr(
        cli, "_run_refresh", lambda **kw: (Config(accounts=[a]), [a], [st], 0, None)
    )
    monkeypatch.setattr(cli, "load_kick_history", lambda **kw: [])
    monkeypatch.setattr(cli, "load_pending_kicks", lambda *a: {})
    monkeypatch.setattr(cli, "_run_evaluate_account", lambda *a, **kw: ("kicked", {}, False))
    dispatch = Mock(side_effect=AssertionError("changed preview must not execute"))
    monkeypatch.setattr(cli, "_execute_due_pending_kicks", dispatch)
    result = CliRunner().invoke(cli.cli, ["run", "--json-output", "--expected-targets", "[]"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "targets_changed"
    dispatch.assert_not_called()


def test_run_scope_binds_trigger_and_pending_action():
    from tokenkick.request_scope import run_scope, approve_targets, target_allowed, consume_target

    with run_scope():
        approve_targets([{"account_key": "A", "kick_type": "session", "pending": {"id": "old"}}])
        assert target_allowed("A", "session", {"id": "old"})
        assert not target_allowed("A", "kick", {"id": "old"})
        assert not target_allowed("B", "session", {"id": "old"})
        assert not target_allowed("A", "session", {"id": "new"})
        consume_target("A")
        assert not target_allowed("A", "session", {"id": "old"})
    assert target_allowed("A", "session", None)


def test_mcp_kick_preview_declares_live_read(monkeypatch):
    from tokenkick.mcp_core import TokenKickMCPCore, LIVE_PROVIDER_TIMEOUT_SECONDS

    core = TokenKickMCPCore(tk_path="/unused")
    run = Mock(return_value={"ok": False})
    monkeypatch.setattr(core, "_run", run)
    core.tokenkick_kick_preview(label="fixture")
    assert run.call_args.kwargs["provider_refresh"] is True
    assert run.call_args.kwargs["timeout"] == LIVE_PROVIDER_TIMEOUT_SECONDS


def test_mcp_timeout_reports_uncertain_quota_outcome(tmp_path):
    import sys
    from tokenkick.mcp_core import TokenKickMCPCore

    # No provider: only a sleeping Python process under the owned process group.
    core = TokenKickMCPCore(tk_path=sys.executable)
    result = core._run(["-c", "import time; time.sleep(10)"], risk="quota_consuming", timeout=0.02)
    assert not result["ok"] and result["error"]["code"] == "outcome_unknown"
    assert "retry is not safe" in result["error"]["message"]
