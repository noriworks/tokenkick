"""Persistent staggering: multi-cycle behavior, durable ownership and shared MCP API."""
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from types import SimpleNamespace

from click.testing import CliRunner
import pytest
from rich.console import Console

from tokenkick import cli, models, stagger
from tokenkick.antigravity import ANTIGRAVITY_QUOTA_WINDOW_SPECS, antigravity_family_status
from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, DataSource
from tokenkick.mcp_core import TokenKickMCPCore

T = 1800000000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(stagger, "load_pending_kicks", lambda: {})
    monkeypatch.setattr(cli, "_read_daemon_pid", lambda: None)
    accounts = [AccountConfig(label=f, provider="antigravity", source=DataSource.ANTIGRAVITY_CLI,
                              identity_email="fixture@example.test", antigravity_family=f)
                for f in stagger.FAMILIES]
    Config(accounts=accounts).save()
    return SimpleNamespace(directory=tmp_path, accounts=accounts, monkeypatch=monkeypatch)


def activate():
    plan = stagger.preview("enable", "gemini", "claude_gpt")
    return stagger.apply("enable", "gemini", "claude_gpt", expected=plan["fingerprint"], accept_risk=True)


@pytest.mark.parametrize("scope", ["all", "weekly", "session"])
def test_account_window_enable_does_not_resume_paused_stagger(env, scope):
    activate()
    plan = stagger.preview("pause")
    stagger.apply("pause", expected=plan["fingerprint"])
    result = CliRunner().invoke(cli.cli, [
        "auto", *([] if scope == "all" else [scope]), "enable", "gemini",
        "--accept-risk", "ENABLE", "--json-output",
    ])
    assert result.exit_code == 0, result.output
    assert Config.load().accounts[0].auto_kick
    assert stagger.load()["group"]["mode"] == "paused"
    provider = Provider()
    assert provider.tick()["mode"] == "paused"
    assert provider.sent == []
    assert stagger.ownership(Config.load().accounts[0]) == "stagger paused"
    row = cli._auto_status_payload(Config.load().accounts)[0]
    assert not row["weekly_effective"] and not row["session_effective"]
    row = cli._accounts_list_payload(Config.load(), Config.load().accounts)[0]
    assert not row["weekly_effective"] and not row["session_effective"]


class Provider:
    def __init__(self, at=T, anchors=None):
        self.now = at
        self.anchors = anchors or {}
        self.sent = []
        self.weekly = {}
        self.fail = False

    def sleep(self, seconds):
        self.now += seconds

    def fetch(self, a, _config):
        windows = []
        for key, spec in ANTIGRAVITY_QUOTA_WINDOW_SPECS.items():
            f = spec["family"]
            session = spec["window_kind"] == "session"
            active = f in self.anchors and self.anchors[f] + stagger.PERIOD > self.now
            reset = (self.anchors[f] + stagger.PERIOD if active else self.now + stagger.PERIOD) if session else self.now + 500000
            windows.append({"id": key, **spec, "used_percent": (1 if active else 0) if session else self.weekly.get(f, 10),
                            "resets_at": reset, "resets_in_seconds": int(reset - self.now), "source": "antigravity-cli", "reset_evidence": "provider_timestamp"})
        return antigravity_family_status(AccountStatus(
            label=a.label, state=AccountState.ACTIVE, source_detail="antigravity-cli",
            observed_at=datetime.fromtimestamp(self.now, timezone.utc).isoformat(), quota_windows=windows,
        ), a.antigravity_family)

    def send(self, a, config, before, **kwargs):
        assert kwargs["automatic"] and kwargs["stagger_controller"] and not kwargs["force"]
        assert config.has_auto_kick_consent("antigravity")
        assert a.auto_kick and a.session_auto_kick
        f = a.antigravity_family
        assert self.anchors.get(f, self.now - stagger.PERIOD) + stagger.PERIOD <= self.now
        self.sent.append((f, self.now))
        if not self.fail:
            self.anchors[f] = self.now
        return SimpleNamespace(dispatch_state="sent", success=not self.fail, confirmed=not self.fail)

    def tick(self):
        return stagger.tick(fetch=self.fetch, send=self.send, now=lambda: self.now, sleep=self.sleep)


class UncertainClockProvider(Provider):
    """Native timestamps are complete but outside the verified idle horizon."""

    uncertain = False

    def fetch(self, account, config):
        status = super().fetch(account, config)
        if self.uncertain and account.antigravity_family == "gemini":
            for window in status.quota_windows:
                if window["family"] == "gemini" and window["window_kind"] == "session":
                    window["resets_at"] = self.now + stagger.PERIOD + 60
        return status


def pending_reset_recovery():
    activate()
    p = UncertainClockProvider(T + 10000, {"gemini": T, "claude_gpt": T + 9000})
    result = p.tick()
    due = result["next_at"]
    p.now = due + 19
    p.uncertain = True
    result = p.tick()
    assert result["state"] == "rechecking"
    assert result["next_at"] is None  # A read deadline is not a generation deadline.
    assert result["next_check_at"] == p.now + 30
    assert not p.sent
    return p, due, result


def test_uncertain_reset_recovers_persisted_slot_and_realigns_following_cycles(env):
    p, due, result = pending_reset_recovery()
    for delay in (60, 120):
        p.now = result["next_check_at"]
        result = p.tick()
        assert result["next_check_at"] == p.now + delay
        assert stagger.load()["runtime"]["verification_retry"]["expires_at"] == due + 300
    # All evaluations reload the persisted state; this also covers process restart.
    p.now = result["next_check_at"]
    assert due + 120 < p.now < due + 300
    p.uncertain = False
    result = p.tick()
    assert [f for f, _ in p.sent] == ["gemini"]
    anchor = p.sent[0][1]
    assert result["last_recovery"]["outcome"] == "recovered"
    assert result["last_recovery"]["anchor"] == anchor
    assert result["verification_retry"] is None
    assert result["next_at"] == anchor + stagger.OFFSET
    for _ in range(4):
        p.now = result["next_at"]
        result = p.tick()
    assert [f for f, _ in p.sent] == ["gemini", "claude_gpt", "gemini", "claude_gpt", "gemini"]
    for i in range(1, len(p.sent)):
        assert abs(p.sent[i][1] - p.sent[i - 1][1] - stagger.OFFSET) <= stagger.TOLERANCE
        if i >= 2:
            assert p.sent[i][1] - p.sent[i - 2][1] >= stagger.PERIOD


def test_reset_recheck_budget_is_persistent_and_never_extended(env):
    p, due, result = pending_reset_recovery()
    for count in (1, 2, 3):
        assert stagger.load()["runtime"]["verification_retry"]["retry_count"] == count
        p.now = result["next_check_at"]
        result = p.tick()
    assert result["state"] == "blocked"
    assert result["verification_retry"] is None
    assert result["next_check_at"] is None
    assert result["last_recovery"]["reason"] == "verification_budget_exhausted"
    assert result["last_recovery"]["expires_at"] == due + 300
    p.now += 600
    p.uncertain = False
    assert p.tick()["next_at"] > p.now
    assert not p.sent


@pytest.mark.parametrize("delta", [-1000, 301])
def test_unknown_clock_outside_known_slot_grace_has_no_rapid_retries(env, delta):
    activate()
    p = UncertainClockProvider(T + 10000, {"gemini": T, "claude_gpt": T + 9000})
    due = p.tick()["next_at"]
    p.now = due + delta
    p.uncertain = True
    result = p.tick()
    assert result["state"] == "blocked"
    assert result["verification_retry"] is None
    assert not p.sent


def test_native_cooldown_inside_grace_delays_recovered_request(env):
    from tokenkick.antigravity_automation import _attempt_path
    p, due, result = pending_reset_recovery()
    file = _attempt_path(env.directory, env.accounts[0])
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"attempted_at": T + 200, "retry_after": due + 200}))
    p.now = result["next_check_at"]
    p.uncertain = False
    result = p.tick()
    assert result["next_at"] == due + 200
    assert result["next_check_at"] is None
    assert not p.sent
    assert stagger.wake_seconds(600, now=p.now) == 60
    p.now = result["next_at"]
    result = p.tick()
    assert len(p.sent) == 1
    assert p.sent[0][1] >= due + 200
    assert result["last_recovery"]["outcome"] == "recovered"


def test_preflight_refusal_of_recovered_slot_does_not_schedule_generation_retry(env):
    p, due, _ = pending_reset_recovery()
    p.now = due + 150
    p.uncertain = False
    p.send = lambda *a, **kw: SimpleNamespace(dispatch_state="not_sent", error="permissions changed")
    result = p.tick()
    assert result["state"] == "blocked"
    assert result["next_check_at"] is None
    assert result["last_recovery"]["reason"] == "request_not_sent"
    assert stagger.load()["runtime"].get("attempt") is None
    assert not p.sent


def test_slow_dispatch_preflight_cannot_send_past_recovery_deadline(env, monkeypatch):
    p, due, _ = pending_reset_recovery()
    p.now = due + 280
    p.uncertain = False
    monkeypatch.setattr(stagger.time, "time", lambda: p.now)
    def preflight(account, config, status, **kwargs):
        p.now = due + 301
        reason = stagger.send_guard(account, automatic=True, controller=True, status=status)
        assert reason == "Recovery deadline passed during preflight; no stagger request sent."
        return SimpleNamespace(dispatch_state="not_sent", error=reason)
    p.send = preflight
    result = p.tick()
    assert result["state"] == "blocked"
    assert result["last_recovery"]["outcome"] == "skipped"
    assert not p.sent


@pytest.mark.parametrize("elapsed", [301, 4 * stagger.PERIOD + 1000, -500])
def test_pending_reset_recovery_does_not_replay_after_expiry_or_clock_jump(env, elapsed):
    p, due, _ = pending_reset_recovery()
    p.now = due + elapsed
    p.uncertain = False
    result = p.tick()
    assert not p.sent
    assert result["verification_retry"] is None
    assert result["last_recovery"]["outcome"] == "skipped"
    assert result["next_at"] > p.now


def test_slow_native_read_consumes_recovery_grace(env):
    p, due, _ = pending_reset_recovery()
    p.now = due + 295
    p.uncertain = False
    result = p.tick()  # The independent read pair finishes after the cutoff.
    assert result["last_recovery"]["reason"] == "grace_expired"
    assert not p.sent


def test_expired_recovery_does_not_block_a_genuinely_due_future_slot(env):
    p, due, _ = pending_reset_recovery()
    p.now = due + 4 * stagger.PERIOD
    p.uncertain = False
    result = p.tick()
    assert p.sent == [("gemini", due + 4 * stagger.PERIOD + 6)]
    assert result["last_recovery"]["outcome"] == "skipped"
    assert result["last_recovery"]["reason"] == "grace_expired"
    assert result["next_at"] > p.now


@pytest.mark.parametrize("family", stagger.FAMILIES)
def test_intervening_activity_cancels_retained_reset_slot(env, family):
    p, due, _ = pending_reset_recovery()
    p.now = due + 150
    p.uncertain = False
    p.anchors[family] = p.now
    result = p.tick()
    assert not p.sent
    assert result["verification_retry"] is None
    assert result["last_recovery"]["reason"] in {"activity_changed_phase", "family_already_active"}
    assert result["next_at"] > p.now


@pytest.mark.parametrize("blocker", ["reserve", "native_cooldown", "uncertain_native_attempt"])
def test_recovery_keeps_quota_and_native_dispatch_guards(env, blocker):
    from tokenkick.antigravity_automation import _attempt_path
    p, due, _ = pending_reset_recovery()
    p.now = due + 150
    p.uncertain = False
    if blocker == "reserve":
        config = Config.load()
        config.accounts[0].weekly_reserve_threshold_percent = 60
        config.save()
        p.weekly["gemini"] = 70
    else:
        file = _attempt_path(env.directory, env.accounts[0])
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"attempted_at": due, "retry_after":
                                   None if blocker == "uncertain_native_attempt" else due + stagger.PERIOD}))
    result = p.tick()
    assert not p.sent
    assert result["verification_retry"] is None
    assert result["last_recovery"]["outcome"] == "skipped"


@pytest.mark.parametrize("action", ["pause", "disable"])
def test_control_edits_cancel_pending_reset_recovery(env, action):
    p, _, _ = pending_reset_recovery()
    plan = stagger.preview(action)
    stagger.apply(action, expected=plan["fingerprint"])
    assert stagger.load()["runtime"].get("verification_retry") is None
    p.now += 30
    assert p.tick()["mode"] in {"paused", "disabled"}
    assert not p.sent


@pytest.mark.parametrize("failure", ["auth", "stale", "identity"])
def test_unknown_gemini_clock_cannot_hide_invalid_other_family(env, failure):
    p, _, result = pending_reset_recovery()
    p.now = result["next_check_at"]
    fetch = p.fetch
    def bad(account, config):
        status = fetch(account, config)
        if account.antigravity_family == "claude_gpt":
            return replace(status, **({"error": "authentication failed"} if failure == "auth" else
                                      {"stale": True} if failure == "stale" else {"label": "wrong"}))
        return status
    p.fetch = bad
    result = p.tick()
    assert result["state"] == "blocked"
    assert result["next_check_at"] is None
    assert result["verification_retry"] is None
    assert not p.sent


@pytest.mark.parametrize("failure", ["send_failed", "post_send_read_failed", "post_send_unknown_clock", "no_anchor"])
def test_recovered_slot_preserves_request_outcome_across_monitoring_failure(env, failure):
    p, due, _ = pending_reset_recovery()
    p.now = due + 150
    p.uncertain = False
    send = p.send
    fetch = p.fetch
    def native(account, config):
        if failure == "post_send_read_failed" and p.sent:
            raise OSError("fixture native read failure")
        return fetch(account, config)
    p.fetch = native
    def uncertain(*args, **kwargs):
        event = send(*args, **kwargs)
        if failure == "post_send_unknown_clock":
            p.anchors.pop("gemini")
            p.uncertain = True
        if failure == "no_anchor":
            p.anchors.pop("gemini")
        return event
    p.fail = failure == "send_failed"
    p.send = uncertain
    result = p.tick()
    assert result["state"] == "blocked"
    assert result["verification_retry"] is None
    confirmed = failure != "send_failed"
    assert result["last_recovery"]["outcome"] == ("recovered" if confirmed else "unconfirmed")
    assert stagger.load()["runtime"]["attempt"]["outcome"] == ("confirmed" if confirmed else "unconfirmed")
    # Recovery of monitoring must never resend the already-confirmed request.
    p.uncertain = False
    p.fetch = fetch
    immediate = p.tick()
    assert len(p.sent) == 1
    assert (immediate["state"] == "blocked") is not confirmed
    p.now += stagger.PERIOD * 2
    later = p.tick()
    if not confirmed:
        assert later["state"] == "blocked"
        assert len(p.sent) == 1
    else:
        assert later["state"] != "blocked"
        for family in stagger.FAMILIES:
            times = [at for f, at in p.sent if f == family]
            assert all(b - a >= stagger.PERIOD for a, b in zip(times, times[1:]))


def test_reset_recheck_daemon_deadline_and_terminal_mcp_diagnostics(env, monkeypatch):
    p, due, result = pending_reset_recovery()
    monkeypatch.setattr(stagger.time, "time", lambda: p.now)
    calls = []
    def check(**kwargs):
        calls.append(p.now)
        return {"state": "rechecking"}
    monkeypatch.setattr(stagger, "tick", check)
    monkeypatch.setattr(cli, "_daemon_log", lambda *a, **kw: None)
    assert stagger.wake_seconds(600, now=p.now) == 30
    stagger.daemon_step(Config.load())
    assert not calls
    p.now = result["next_check_at"]
    stagger.daemon_step(Config.load())
    assert calls == [p.now]
    core = TokenKickMCPCore()
    runner = CliRunner()
    def run(argv, **kwargs):
        response = runner.invoke(cli.cli, argv)
        assert response.exit_code == 0, response.output
        return json.loads(response.output)
    monkeypatch.setattr(core, "_run", run)
    payload = core.tokenkick_stagger_status()["payload"]
    assert payload["next_check_at"] == p.now
    assert payload["next_at"] is None
    assert payload["verification_retry"]["expires_at"] == due + 300
    output = io.StringIO()
    stagger.render(Console(file=output, width=110), payload)
    assert "rechecking" in output.getvalue()
    assert "Recheck gemini reset:" in output.getvalue()
    assert "Delayed slot: gemini" in output.getvalue()
    assert "(1/3)" in output.getvalue()


@pytest.mark.parametrize("field,value", [("retry_count", 4), ("expires_at", T), ("retry_at", float("inf"))])
def test_corrupt_persisted_retry_blocks_automation(env, field, value):
    pending_reset_recovery()
    data = stagger.load()
    data["runtime"]["verification_retry"][field] = value
    stagger.path().write_text(json.dumps(data))
    assert stagger.status()["state"] == "blocked"
    with pytest.raises(stagger.StaggerError):
        Provider().tick()


def test_corrupt_recovery_audit_fails_closed_instead_of_crashing_render(env):
    pending_reset_recovery()
    data = stagger.load()
    data["runtime"]["last_recovery"] = {"family": "gemini", "outcome": "claimed",
                                        "reason": "evidence_verified", "finished_at": T}
    stagger.save(data)
    payload = stagger.status()
    assert payload["state"] == "blocked"
    output = io.StringIO()
    stagger.render(Console(file=output, width=110), payload)
    assert "Invalid/unsupported stagger state" in output.getvalue()


def test_three_cycles_persist_across_restarts_with_no_duplicate_or_early_requests(env):
    activate()
    provider = Provider()
    result = provider.tick()
    for _ in range(6):
        assert result["next_at"] > provider.now
        # Every call reloads persisted policy/state, as after a process restart.
        provider.now = result["next_at"]
        result = provider.tick()
    assert [f for f, _ in provider.sent] == ["gemini", "claude_gpt", "gemini", "claude_gpt", "gemini", "claude_gpt", "gemini"]
    for i, (_, at) in enumerate(provider.sent):
        if i:
            assert abs(at - provider.sent[i-1][1] - stagger.OFFSET) <= stagger.TOLERANCE
        if i >= 2:
            assert at - provider.sent[i-2][1] >= stagger.PERIOD
    assert result["state"] == "aligned"
    assert stagger.load()["runtime"]["attempt"]["outcome"] == "confirmed"


def test_adopt_active_pair_without_generation(env):
    activate()
    provider = Provider(T + 10000, {"gemini": T, "claude_gpt": T + 9000})
    result = provider.tick()
    assert result["state"] == "aligned"
    assert result["observed_offset_seconds"] == 9000
    assert result["next_at"] == T + 18000
    assert not provider.sent


def test_uncertain_generation_never_retries_even_after_restart_and_history_loss(env):
    activate()
    provider = Provider()
    provider.fail = True
    assert provider.tick()["state"] == "blocked"
    provider.now += 90000
    assert provider.tick()["state"] == "blocked"
    assert len(provider.sent) == 1
    assert "unconfirmed" in stagger.status()["reason"]


@pytest.mark.parametrize("action", ["pause", "disable"])
def test_stop_never_restores_independent_automation(env, action):
    activate()
    plan = stagger.preview(action)
    stagger.apply(action, expected=plan["fingerprint"])
    assert all(not a.auto_kick and not a.session_auto_kick for a in Config.load().accounts)
    p = Provider()
    p.tick()
    assert not p.sent
    assert stagger.status()["enabled"] is False


def test_resume_is_persistent_and_requires_consent_ack(env):
    activate()
    plan = stagger.preview("pause")
    stagger.apply("pause", expected=plan["fingerprint"])
    plan = stagger.preview("resume")
    with pytest.raises(stagger.StaggerError, match="acknowledgement"):
        stagger.apply("resume", expected=plan["fingerprint"])
    stagger.apply("resume", expected=plan["fingerprint"], accept_risk=True)
    assert Provider().tick()["next_family"] == "claude_gpt"


def test_stale_preview_rejects_changed_member_settings(env):
    plan = stagger.preview("enable", "gemini", "claude_gpt")
    config = Config.load()
    config.accounts[0].weekly_auto_kick = not config.accounts[0].weekly_auto_kick
    config.save()
    with pytest.raises(stagger.StaggerError, match="Settings changed"):
        stagger.apply("enable", "gemini", "claude_gpt", expected=plan["fingerprint"], accept_risk=True)
    assert stagger.load()["group"] is None


@pytest.mark.parametrize("field,value", [("identity_email", "other@example.test"), ("antigravity_family", "gemini")])
def test_reject_identity_and_family_mismatch(env, field, value):
    config = Config.load()
    setattr(config.accounts[1], field, value)
    config.save()
    with pytest.raises(stagger.StaggerError, match="identity|ambiguous"):
        stagger.preview("enable", "gemini", "claude_gpt")


def test_pending_schedule_conflict_blocks_activation_and_execution(env):
    env.monkeypatch.setattr(stagger, "load_pending_kicks", lambda: {models.account_key_string(env.accounts[0]): object()})
    with pytest.raises(stagger.StaggerError, match="conflicting"):
        stagger.preview("enable", "gemini", "claude_gpt")
    env.monkeypatch.setattr(stagger, "load_pending_kicks", lambda: {})
    activate()
    env.monkeypatch.setattr(stagger, "load_pending_kicks", lambda: {models.account_key_string(env.accounts[0]): object()})
    p = Provider()
    assert p.tick()["state"] == "blocked"
    assert not p.sent


def test_master_stop_blocks_group_and_independent_targets(env):
    activate()
    config = Config.load()
    assert cli._auto_kick_kickable_accounts(config.accounts) == []
    assert "stagger-managed" in stagger.send_guard(config.accounts[0], automatic=True)
    config.accounts[0].auto_kick = False
    config.save()
    p = Provider()
    assert p.tick()["state"] == "blocked"
    assert not p.sent
    assert "blocked" in stagger.ownership(config.accounts[0])


def test_weekly_exhaustion_does_not_disable_healthy_family(env):
    activate()
    p = Provider()
    p.weekly["claude_gpt"] = 100
    result = p.tick()
    assert result["state"] == "degraded"
    assert result["next_family"] == "gemini"
    p.now = result["next_at"]
    p.tick()
    assert [f for f, _ in p.sent] == ["gemini", "gemini"]


def test_late_restart_does_not_burst_catch_up(env):
    activate()
    p = Provider()
    p.tick()
    p.now += 4 * stagger.PERIOD + 2000
    count = len(p.sent)
    result = p.tick()
    assert len(p.sent) == count
    assert result["next_at"] > p.now


def test_external_usage_preserves_active_window_and_realigns(env):
    activate()
    p = Provider(T + 10000, {"gemini": T, "claude_gpt": T + 9000})
    p.tick()
    p.now = T + 20000
    p.anchors["gemini"] = T + 19000  # user started Gemini late
    result = p.tick()
    assert result["state"] == "realigning"
    assert not p.sent
    p.now = result["next_at"]
    result = p.tick()
    assert p.sent[0][0] == "claude_gpt"
    assert result["state"] == "aligned"


@pytest.mark.parametrize("bad", ["not-json", '{"version": 2}', '{"version":1,"revision":0,"group":null,"runtime":{"phase":"tomorrow"}}'])
def test_corrupt_state_fails_closed_for_independent_automation(env, bad):
    stagger.path().write_text(bad)
    assert stagger.status()["state"] == "blocked"
    assert stagger.owns(env.accounts[0])
    assert cli._auto_kick_kickable_accounts([replace(env.accounts[0], auto_kick=True)]) == []


def test_wake_uses_deadline_without_ten_minute_slippage(env):
    activate()
    p = Provider()
    result = p.tick()
    assert stagger.wake_seconds(600, now=result["next_at"] - 7) == 7
    assert stagger.wake_seconds(600, now=p.now) == 60


def test_cli_preview_apply_status_are_same_persisted_policy(env):
    runner = CliRunner()
    args = ["--action", "enable", "--gemini", "gemini", "--claude", "claude_gpt"]
    result = runner.invoke(cli.cli, ["stagger", "preview", *args, "--json-output"])
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)["payload"]
    result = runner.invoke(cli.cli, ["stagger", "apply", *args, "--expected", preview["fingerprint"], "--yes", "--accept-risk", "ENABLE", "--json-output"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["payload"]["recurring"] is True
    assert stagger.status()["enabled"] is True
    status = runner.invoke(cli.cli, ["stagger", "status", "--json-output"])
    assert json.loads(status.output)["payload"]["daemon_running"] is False


def test_mcp_real_cli_preview_apply_then_daemon_continues_without_agent(env, monkeypatch):
    core = TokenKickMCPCore()
    runner = CliRunner()
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        result = runner.invoke(cli.cli, argv)
        assert result.exit_code == 0, result.output
        return json.loads(result.output)
    monkeypatch.setattr(core, "_run", run)
    args = dict(action="enable", gemini="gemini", claude="claude_gpt")
    preview = core.tokenkick_stagger_preview(**args)
    token = preview["preview_token"]["token"]
    with pytest.raises(ValueError, match="quota_ack"):
        core.tokenkick_stagger_apply(**args, preview_token=token, confirm=True)
    applied = core.tokenkick_stagger_apply(**args, preview_token=token, confirm=True, quota_ack=True, accept_risk=True)
    assert applied["payload"]["recurring"] is True
    with pytest.raises(ValueError, match="already used"):
        core.tokenkick_stagger_apply(**args, preview_token=token, confirm=True, quota_ack=True, accept_risk=True)
    p = Provider()
    result = p.tick()
    for _ in range(4):
        p.now = result["next_at"]
        result = p.tick()
    assert len(p.sent) == 5
    assert len(calls) == 2  # No MCP follow-up required for subsequent cycles.


def test_narrow_terminal_separates_target_observation_and_runtime(env):
    activate()
    p = Provider(T + 10000, {"gemini": T, "claude_gpt": T + 9000})
    p.tick()
    output = io.StringIO()
    stagger.render(Console(file=output, width=55), stagger.status(daemon_running=False))
    text = output.getvalue()
    assert "Target offset: 2h30m" in text
    assert "Observed offset: 150.0m" in text
    assert "daemon stopped" in text


def test_pause_during_native_preflight_wins(env):
    activate()
    p = Provider()
    first = True
    def fetch(a, config):
        nonlocal first
        if first:
            first = False
            plan = stagger.preview("pause")
            stagger.apply("pause", expected=plan["fingerprint"])
        return p.fetch(a, config)
    result = stagger.tick(fetch=fetch, send=p.send, now=lambda: p.now, sleep=p.sleep)
    assert result["state"] == "paused"
    assert not p.sent


def test_permission_change_during_native_preflight_blocks_send(env):
    activate()
    p = Provider()
    first = True
    def fetch(a, config):
        nonlocal first
        if first:
            first = False
            current = Config.load()
            current.accounts[1].session_auto_kick = False
            current.save()
        return p.fetch(a, config)
    result = stagger.tick(fetch=fetch, send=p.send, now=lambda: p.now, sleep=p.sleep)
    assert result["state"] == "blocked"
    assert not p.sent


def test_normal_executor_enforces_group_slot_and_allows_only_claimed_due_family(env):
    activate()
    p = Provider()
    env.monkeypatch.setattr(stagger.time, "time", lambda: p.now)
    env.monkeypatch.setattr(cli, "load_kick_history", lambda **_: [])
    env.monkeypatch.setattr(cli, "_fetch_status", p.fetch)
    def owned(a, config, before, **kwargs):
        return p.send(a, config, before, automatic=True, stagger_controller=True, force=False)
    env.monkeypatch.setattr(cli, "_kick_and_notify_owned", owned)
    # Actual normal executor, account action lease, configuration reload and guard.
    result = stagger.tick(fetch=p.fetch, now=lambda: p.now, sleep=p.sleep)
    assert result["next_family"] == "claude_gpt"
    assert len(p.sent) == 1
    # Even a caller setting the internal controller flag cannot claim another slot.
    a = Config.load().accounts[1]
    statuses = stagger.read_pair({x.antigravity_family: x for x in Config.load().accounts}, Config.load(), p.fetch, p.sleep)
    event = cli._kick_and_notify(a, Config.load(), statuses["claude_gpt"], automatic=True,
                                stagger_controller=True, kick_type="session")
    assert not event.success
    assert "claimed" in event.error
    assert len(p.sent) == 1


def test_durable_native_cooldown_defers_without_failed_generation(env):
    from tokenkick.antigravity_automation import _attempt_path
    activate()
    p = Provider(T + 18000, {"gemini": T, "claude_gpt": T + 9000})
    data = stagger.load()
    data["runtime"] = {"anchors": {"gemini": T, "claude_gpt": T + 9000}, "phase": T}
    stagger.save(data)
    _attempt_path(env.directory, env.accounts[0]).parent.mkdir(parents=True)
    _attempt_path(env.directory, env.accounts[0]).write_text(json.dumps({"attempted_at": T+40, "retry_after": T+18040}))
    result = p.tick()
    assert result["next_at"] == T+18040
    assert not p.sent
    p.now = result["next_at"]
    assert p.tick()["state"] == "aligned"
    assert len(p.sent) == 1


def test_explicit_recovery_requires_cooldown_and_stays_paused_without_generation(env):
    activate()
    p = Provider()
    p.fail = True
    p.tick()
    env.monkeypatch.setattr(stagger.time, "time", lambda: p.now)
    read_pair = stagger.read_pair
    env.monkeypatch.setattr(stagger, "read_pair", lambda accounts, config, fetch: read_pair(accounts, config, p.fetch, p.sleep))
    plan = stagger.preview("recover")
    with pytest.raises(stagger.StaggerError, match="cooldown"):
        stagger.apply("recover", expected=plan["fingerprint"])
    p.now += stagger.PERIOD + 10
    plan = stagger.preview("recover")
    result = stagger.apply("recover", expected=plan["fingerprint"])
    assert result["state"] == "paused"
    assert len(p.sent) == 1
    assert stagger.load()["runtime"]["attempt"]["recovery"].startswith("explicit")
    assert all(not a.auto_kick for a in Config.load().accounts)


def test_two_daemon_evaluations_do_not_duplicate_requests(env):
    from concurrent.futures import ThreadPoolExecutor
    activate()
    p = Provider()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: p.tick(), range(2)))
    assert len(p.sent) == 1
    assert all(result["next_family"] == "claude_gpt" for result in results)


@pytest.mark.parametrize("kind", ["stale", "identity", "duration", "authentication", "relative"])
def test_bad_native_evidence_never_generates(env, kind):
    activate()
    p = Provider()
    def fetch(a, config):
        status = p.fetch(a, config)
        if kind == "stale":
            status.observed_at = datetime.fromtimestamp(p.now - 500, timezone.utc).isoformat()
        elif kind == "identity":
            status.label = "another identity"
        elif kind == "duration":
            status.quota_windows[0]["window_minutes"] = 600
        elif kind == "authentication":
            status.error = "Authentication unavailable"
        elif kind == "relative":
            status.quota_windows[0]["reset_evidence"] = "relative_estimate"
        return status
    result = stagger.tick(fetch=fetch, send=p.send, now=lambda: p.now, sleep=p.sleep)
    assert result["state"] == "blocked"
    assert not p.sent


def test_reserve_stops_only_affected_family(env):
    config = Config.load()
    config.accounts[0].weekly_reserve_threshold_percent = 60
    config.save()
    activate()
    p = Provider()
    p.weekly["gemini"] = 60
    result = p.tick()
    assert result["state"] == "degraded"
    assert result["next_family"] == "claude_gpt"
    assert not p.sent


def test_stale_mcp_preview_does_not_change_controls(env, monkeypatch):
    core = TokenKickMCPCore()
    runner = CliRunner()
    monkeypatch.setattr(core, "_run", lambda args, **_: json.loads(runner.invoke(cli.cli, args).output))
    args = dict(action="enable", gemini="gemini", claude="claude_gpt")
    token = core.tokenkick_stagger_preview(**args)["preview_token"]["token"]
    config = Config.load()
    config.accounts[0].weekly_reserve_threshold_percent = 70
    config.save()
    result = core.tokenkick_stagger_apply(**args, preview_token=token, confirm=True, quota_ack=True, accept_risk=True)
    assert result["ok"] is False
    assert stagger.load()["group"] is None


def test_mcp_transport_advertises_and_calls_persistent_controls(env, monkeypatch):
    import asyncio
    from tokenkick import mcp_server
    core = TokenKickMCPCore()
    runner = CliRunner()
    monkeypatch.setattr(core, "_run", lambda args, **_: json.loads(runner.invoke(cli.cli, args).output))
    monkeypatch.setattr(mcp_server, "_core", core)
    async def check():
        tools = {t.name: t for t in await mcp_server.mcp.list_tools()}
        assert {"tokenkick_stagger_status", "tokenkick_stagger_preview", "tokenkick_stagger_apply"} <= tools.keys()
        result = await mcp_server.mcp.call_tool("tokenkick_stagger_preview", {"action": "enable", "gemini": "gemini", "claude": "claude_gpt"})
        # Exercise the actual registered transport, including serialized JSON.
        structured = json.loads(result[0].text)
        assert structured["payload"]["recurring"] is True
        token = structured["preview_token"]["token"]
        result = await mcp_server.mcp.call_tool("tokenkick_stagger_apply", {
            "action": "enable", "gemini": "gemini", "claude": "claude_gpt",
            "preview_token": token, "confirm": True, "quota_ack": True, "accept_risk": True,
        })
        assert json.loads(result[0].text)["payload"]["enabled"] is True
    asyncio.run(check())


def test_targeted_wakeup_does_not_run_full_provider_poll(env, monkeypatch):
    activate()
    clock = [0.0]
    evaluations = []
    monkeypatch.setattr(stagger.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stagger.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(stagger, "daemon_step", lambda *_args, **_: evaluations.append(clock[0]))
    monkeypatch.setattr(cli, "_refresh_status_cache", lambda *_args, **_: pytest.fail("must not poll unrelated providers"))
    stagger.wait_between_polls(600)
    assert clock[0] == 600
    assert evaluations == list(range(60, 601, 60))


def test_notification_routes_deduplicate_healthy_cycles_and_repeat_on_recovery(env, monkeypatch):
    from tokenkick import notifier
    activate()
    results = iter([
        {"enabled": True, "state": "aligned", "reason": None},
        {"enabled": True, "state": "aligned", "reason": None},
        {"enabled": True, "state": "blocked", "reason": "Authentication unavailable"},
        {"enabled": True, "state": "aligned", "reason": None},
    ])
    monkeypatch.setattr(stagger, "tick", lambda **_: next(results))
    monkeypatch.setattr(cli, "_daemon_log", lambda *args, **kwargs: None)
    routes = [models.NotifyConfig(backend="ntfy"), models.NotifyConfig(backend="telegram")]
    monkeypatch.setattr(cli, "_notification_configs_for_account", lambda *_: routes)
    messages = []
    monkeypatch.setattr(notifier, "notify_stagger", lambda message, config: messages.append((message, config.backend)))
    for _ in range(4):
        stagger.daemon_step(Config.load())
    assert [backend for _, backend in messages] == ["ntfy", "telegram"] * 3


def test_lost_policy_does_not_enable_independent_kicks_and_can_be_disabled(env):
    activate()
    stagger.path().unlink()
    config = Config.load()
    assert all(stagger.owns(a) for a in config.accounts)
    assert cli._auto_kick_kickable_accounts(config.accounts) == []
    assert stagger.status()["state"] == "blocked"
    assert "missing policy" in stagger.ownership(config.accounts[0])
    plan = stagger.preview("disable")
    stagger.apply("disable", expected=plan["fingerprint"])
    assert all(not a.auto_kick and not a.stagger_group_id for a in Config.load().accounts)


def test_disabled_after_member_removal_leaves_remaining_account_off(env):
    activate()
    config = Config.load()
    config.accounts = config.accounts[:1]
    config.save()
    assert stagger.status()["state"] == "blocked"
    plan = stagger.preview("disable")
    stagger.apply("disable", expected=plan["fingerprint"])
    assert Config.load().accounts[0].auto_kick is False


def test_interrupted_permission_save_keeps_paused_owner(env, monkeypatch):
    plan = stagger.preview("enable", "gemini", "claude_gpt")
    monkeypatch.setattr(Config, "save", lambda _: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        stagger.apply("enable", "gemini", "claude_gpt", expected=plan["fingerprint"], accept_risk=True)
    assert stagger.load()["group"]["mode"] == "paused"
    assert all(stagger.owns(a) for a in env.accounts)


def test_status_and_account_views_share_ownership(env):
    activate()
    config = Config.load()
    rows = cli._accounts_list_payload(config, config.accounts)
    assert all(row["automation_owner"] == "stagger-managed" for row in rows)
    assert all(cli._account_detail_payload(a)["automation_owner"] == "stagger-managed" for a in config.accounts)
    assert all(row["automation_owner"] == "stagger-managed" for row in cli._auto_status_payload(config.accounts))


def test_existing_work_window_planner_cannot_claim_stagger_member(env):
    from tokenkick.orchestration import _skip_reason
    activate()
    assert _skip_reason(Config.load().accounts[0], None, False) == "stagger_managed"


def test_utc_slots_do_not_depend_on_local_dst(env):
    # Europe/Berlin DST ends between these observations. Policy uses epoch time.
    start = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()
    runtime = {"phase": start, "anchors": {"gemini": start}}
    observed = {"gemini": {"anchor": start, "blocker": None},
                "claude_gpt": {"anchor": None, "blocker": None}}
    decision = stagger.decide(runtime, observed, start + 60)
    assert decision["next_at"] == start + 9000


def test_clock_jump_skips_missed_slots_and_keeps_active_windows(env):
    observed = {"gemini": {"anchor": None, "blocker": None},
                "claude_gpt": {"anchor": None, "blocker": None}}
    runtime = {"phase": T, "anchors": {"gemini": T, "claude_gpt": T+9000}}
    decision = stagger.decide(runtime, observed, T + 2*stagger.PERIOD + 1000)
    assert decision["next_at"] > T + 2*stagger.PERIOD + 1000
    backward = stagger.decide(runtime, observed, T - 500)
    assert backward["next_at"] >= T + stagger.PERIOD


def test_crash_after_claim_never_replays_on_restart(env):
    activate()
    p = Provider()
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        stagger.tick(fetch=p.fetch, send=crash, now=lambda: p.now, sleep=p.sleep)
    assert stagger.load()["runtime"]["attempt"]["outcome"] == "attempting"
    p.now += stagger.PERIOD * 2
    assert p.tick()["state"] == "blocked"
    assert not p.sent


def test_recovery_clears_only_affected_native_guard_after_latest_possible_attempt(env, monkeypatch):
    from tokenkick.antigravity_automation import _attempt_path, attempt_retry_at
    import hashlib
    activate()
    p = Provider()
    p.fail = True
    p.tick()
    attempted = p.now + 20
    guarded = env.accounts[0]
    native = _attempt_path(env.directory, guarded)
    native.parent.mkdir(parents=True)
    native.write_text(json.dumps({"attempted_at": attempted, "retry_after": None}))
    other = _attempt_path(env.directory, env.accounts[1])
    other.write_text(json.dumps({"attempted_at": T, "retry_after": T+18000}))
    other_before = other.read_text()
    action = env.directory / "actions" / (hashlib.sha256(models.account_key_string(guarded).encode()).hexdigest() + ".json")
    action.parent.mkdir(parents=True)
    action.write_text(json.dumps({"state": "in_flight", "started_at": attempted}))
    monkeypatch.setattr(stagger.time, "time", lambda: p.now)
    read_pair = stagger.read_pair
    monkeypatch.setattr(stagger, "read_pair", lambda accounts, config, fetch: read_pair(accounts, config, p.fetch, p.sleep))
    p.now = attempted + stagger.PERIOD - 1
    plan = stagger.preview("recover")
    with pytest.raises(stagger.StaggerError, match="cooldown"):
        stagger.apply("recover", expected=plan["fingerprint"])
    p.now += 2
    stagger.apply("recover", expected=plan["fingerprint"])
    assert attempt_retry_at(env.directory, guarded) >= attempted + stagger.PERIOD
    assert json.loads(action.read_text())["state"] == "completed"
    assert other.read_text() == other_before
    assert len(p.sent) == 1


def test_lost_policy_with_one_remaining_member_can_be_disabled(env):
    activate()
    config = Config.load()
    config.accounts = config.accounts[:1]
    config.save()
    stagger.path().unlink()
    plan = stagger.preview("disable")
    stagger.apply("disable", expected=plan["fingerprint"])
    assert not Config.load().accounts[0].auto_kick
    assert stagger.load()["group"]["mode"] == "disabled"


def test_persisted_validation_remains_active_with_python_optimization(env):
    import subprocess
    import sys
    stagger.path().write_text(json.dumps({"version": 2, "revision": 0, "group": None, "runtime": {}}))
    result = subprocess.run([sys.executable, "-O", "-c", """
from pathlib import Path
import sys
from tokenkick.stagger import load, StaggerError
try:
    load(Path(sys.argv[1]))
except StaggerError:
    sys.exit(0)
sys.exit(1)
""", str(env.directory)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_disable_clears_ownership_after_identity_key_drift(env):
    activate()
    config = Config.load()
    config.accounts[0] = replace(config.accounts[0], identity_email="changed@example.test")
    config.save()
    plan = stagger.preview("disable")
    stagger.apply("disable", expected=plan["fingerprint"])
    assert all(a.stagger_group_id is None and not a.auto_kick for a in Config.load().accounts)


def test_missing_member_does_not_break_daemon_notification_routing(env, monkeypatch):
    activate()
    config = Config.load()
    config.accounts.pop()
    config.save()
    monkeypatch.setattr(cli, "_send_account_notifications", lambda *a, **kw: None)
    result = stagger.daemon_step(config)
    assert result["state"] == "blocked"


def test_safe_refusal_recovers_on_next_poll_without_explicit_recovery(env):
    activate()
    provider = Provider()
    result = stagger.tick(fetch=provider.fetch, now=lambda: provider.now, sleep=provider.sleep,
        send=lambda *a, **kw: SimpleNamespace(dispatch_state="not_sent", success=False,
                                               confirmed=False, error="freshness changed"))
    assert result["state"] == "blocked"
    assert not stagger.load()["runtime"].get("attempt")
    provider.now += 600
    result = provider.tick()
    assert result["state"] != "blocked"
    assert result["next_at"] > provider.now  # Do not replay the missed slot.
    provider.now = result["next_at"]
    provider.tick()
    assert len(provider.sent) == 1


def adopted_then_unused(family="gemini"):
    """A fixed-looking rollover is adopted without a TokenKick generation."""
    activate()
    peer = next(f for f in stagger.FAMILIES if f != family)
    p = UncertainClockProvider(T + 18020, {family: T + 18000, peer: T + 9052})
    assert p.tick()["state"] == "aligned"
    assert not p.sent
    p.anchors.pop(family)
    p.now = T + 20700
    return p, peer


@pytest.mark.parametrize("family", stagger.FAMILIES)
def test_disproven_adopted_anchor_restarts_recurring_phase(env, family):
    p, peer = adopted_then_unused(family)
    result = p.tick()
    assert [f for f, _ in p.sent] == [family]
    assert result["last_anchor_correction"]["outcome"] == "reanchored"
    assert result["last_anchor_correction"]["previous_anchor"] == T + 18000
    assert result["anchor_corrections"] == {}
    anchor = p.sent[0][1]
    assert result["next_family"] == peer
    assert result["next_at"] == anchor + stagger.OFFSET
    assert result["state"] == "realigning"  # Current peer still has its old window.
    for _ in range(5):
        p.now = result["next_at"]
        result = p.tick()  # Reloads the persisted state every time.
    assert [f for f, _ in p.sent] == [family, peer, family, peer, family, peer]
    for previous, current in zip(p.sent, p.sent[1:]):
        assert stagger.OFFSET <= current[1] - previous[1] <= stagger.OFFSET + 20
    assert result["state"] == "aligned"


def test_unknown_then_unanchored_corrects_without_forcing_unknown_clock(env):
    p, _ = adopted_then_unused()
    p.uncertain = True
    assert p.tick()["state"] == "blocked"
    assert not p.sent
    assert stagger.load()["runtime"]["anchors"]["gemini"] == T + 18000
    p.uncertain = False
    p.now += 600
    assert p.tick()["last_anchor_correction"]["outcome"] == "reanchored"
    assert len(p.sent) == 1


def test_correction_waits_for_peer_spacing_and_survives_restart(env):
    p, peer = adopted_then_unused()
    p.anchors[peer] = p.now - 60  # Recent external peer activity must be preserved.
    result = p.tick()
    assert not p.sent
    assert result["next_family"] == "gemini"
    assert result["next_at"] == p.anchors[peer] + stagger.OFFSET
    assert "gemini" not in stagger.load()["runtime"]["anchors"]
    assert "gemini" in result["anchor_corrections"]
    result = p.tick()
    assert not p.sent
    p.now = result["next_at"]
    assert p.tick()["last_anchor_correction"]["outcome"] == "reanchored"
    assert [f for f, _ in p.sent] == ["gemini"]


def test_correction_retains_durable_request_cooldown(env, monkeypatch):
    from tokenkick import antigravity_automation
    p, _ = adopted_then_unused()
    allowed = p.now + 1200
    monkeypatch.setattr(antigravity_automation, "attempt_retry_at",
                        lambda directory, account: allowed if account.antigravity_family == "gemini" else None)
    result = p.tick()
    assert not p.sent
    assert result["next_at"] == allowed
    p.now = allowed
    p.tick()
    assert len(p.sent) == 1 and p.sent[0][1] >= allowed


@pytest.mark.parametrize("blocker", ["quota", "reserve", "uncertain_guard", "unconfirmed", "pause"])
def test_anchor_correction_does_not_bypass_safety_controls(env, monkeypatch, blocker):
    from tokenkick import antigravity_automation
    p, _ = adopted_then_unused()
    if blocker == "quota":
        p.weekly["gemini"] = 100
    elif blocker == "reserve":
        config = Config.load()
        config.accounts[0].weekly_reserve_threshold_percent = 5
        config.save()
    elif blocker == "uncertain_guard":
        def guard(*args):
            raise ValueError("Previous request unconfirmed")
        monkeypatch.setattr(antigravity_automation, "attempt_retry_at", guard)
    elif blocker == "unconfirmed":
        data = stagger.load()
        data["runtime"]["attempt"] = {"family": "gemini", "at": p.now - 1, "outcome": "unconfirmed"}
        stagger.save(data)
    else:
        plan = stagger.preview("pause")
        stagger.apply("pause", expected=plan["fingerprint"])
    result = p.tick()
    assert not p.sent
    assert result["state"] in {"degraded", "blocked", "paused"}


def test_both_disproven_anchors_never_send_simultaneously(env):
    p, peer = adopted_then_unused()
    # The peer's saved anchor should still be active too, but now both are idle.
    p.anchors.pop(peer)
    result = p.tick()
    assert len(p.sent) == 1
    assert result["next_at"] == p.sent[0][1] + stagger.OFFSET
    assert result["anchor_corrections"]
    p.now = result["next_at"]
    result = p.tick()
    assert len(p.sent) == 2 and p.sent[0][0] != p.sent[1][0]
    assert result["anchor_corrections"] == {}
    assert result["state"] == "aligned"


@pytest.mark.parametrize("failure", ["failed", "post_read", "not_sent"])
def test_corrective_dispatch_failure_never_replays_immediately(env, failure):
    p, _ = adopted_then_unused()
    original = p.send
    fetch = p.fetch
    def read(*args):
        if failure == "post_read" and p.sent:
            raise ValueError("Native read unavailable")
        return fetch(*args)
    p.fetch = read
    def send(*args, **kwargs):
        if failure == "not_sent":
            return SimpleNamespace(dispatch_state="not_sent", error="Preflight refused")
        p.fail = failure == "failed"
        event = original(*args, **kwargs)
        return event
    p.send = send
    assert p.tick()["state"] == "blocked"
    count = len(p.sent)
    p.now += 600
    result = p.tick()
    assert len(p.sent) == count
    if failure == "not_sent":
        assert result["last_anchor_correction"]["outcome"] == "not_sent"
        assert result["next_at"] > p.now
    else:
        assert result["state"] == "blocked"


def test_external_window_supersedes_pending_correction(env):
    p, peer = adopted_then_unused()
    p.anchors[peer] = p.now - 30
    assert p.tick()["anchor_corrections"]
    p.now += 100
    p.anchors["gemini"] = p.now
    result = p.tick()
    assert not p.sent
    assert result["anchor_corrections"] == {}
    assert result["last_anchor_correction"]["outcome"] == "native_window_observed"
    assert stagger.load()["runtime"]["phase"] == p.anchors["gemini"]
    assert result["next_at"] >= p.anchors[result["next_family"]] + stagger.PERIOD


@pytest.mark.parametrize("value", [[], {"wrong": {"anchor": T, "detected_at": T+1}},
    {"gemini": {"anchor": float("nan"), "detected_at": T+1}},
    {"gemini": {"anchor": T, "detected_at": T-1}},
    {"gemini": {"anchor": T, "detected_at": T+stagger.PERIOD}}, {"gemini": {}}])
def test_malformed_anchor_correction_fails_closed(env, value):
    activate()
    data = stagger.load()
    data["runtime"]["anchor_corrections"] = value
    stagger.path().write_text(json.dumps(data))
    assert stagger.status()["state"] == "blocked"


def test_pending_anchor_correction_is_visible_in_terminal_and_mcp_status(env):
    p, peer = adopted_then_unused()
    p.anchors[peer] = p.now - 30
    result = p.tick()
    out = io.StringIO()
    stagger.render(Console(file=out, width=160), result)
    assert "previous anchor disproven" in out.getvalue()
    assert result["state"] == "realigning"
    def run(argv, **kwargs):
        response = CliRunner().invoke(cli.cli, argv)
        assert response.exit_code == 0, response.output
        return json.loads(response.output)
    core = TokenKickMCPCore()
    env.monkeypatch.setattr(core, "_run", run)
    assert core.tokenkick_stagger_status()["payload"]["anchor_corrections"] == result["anchor_corrections"]


@pytest.mark.parametrize("fault", ["stale", "synthetic", "error"])
def test_bad_native_data_cannot_retract_an_adopted_anchor(env, fault):
    p, _ = adopted_then_unused()
    fetch = p.fetch
    def read(*args):
        status = fetch(*args)
        if fault == "synthetic":
            return models.mark_synthetic_status(status, "fixture")
        return replace(status, **({"stale": True} if fault == "stale" else {"error": "unavailable"}))
    p.fetch = read
    assert p.tick()["state"] == "blocked"
    assert not p.sent
    assert stagger.load()["runtime"]["anchors"]["gemini"] == T + 18000
    assert not stagger.load()["runtime"]["anchor_corrections"]


def test_pending_correction_never_dispatches_before_clock_rollback(env):
    p, peer = adopted_then_unused()
    p.anchors[peer] = p.now - 30
    result = p.tick()
    detected = result["anchor_corrections"]["gemini"]["detected_at"]
    p.now = detected - 100
    result = p.tick()
    assert not p.sent
    assert result["next_at"] >= detected


def test_pending_correction_retains_confirmed_runtime_attempt_cooldown(env):
    p, _ = adopted_then_unused()
    data = stagger.load()
    attempt_at = T + 18000
    data["runtime"]["attempt"] = {"family": "gemini", "at": attempt_at, "outcome": "confirmed"}
    stagger.save(data)
    result = p.tick()
    assert not p.sent
    assert "gemini" in result["anchor_corrections"]
    assert result["next_at"] >= min(attempt_at + stagger.PERIOD, p.anchors["claude_gpt"] + stagger.PERIOD)
    # When the peer goes first, its new anchor imposes another spacing constraint.
    for _ in range(3):
        p.now = result["next_at"]
        result = p.tick()
    gemini = next(at for f, at in p.sent if f == "gemini")
    assert gemini >= attempt_at + stagger.PERIOD


class RolloverProvider(Provider):
    """A native zero-use rollover can stay fixed across a six-second pair."""
    provisional = None

    def fetch(self, account, config):
        status = super().fetch(account, config)
        for window in status.quota_windows:
            if window['family'] == self.provisional and window['window_kind'] == 'session':
                window['used_percent'] = 0
        return antigravity_family_status(status, account.antigravity_family)

    def send(self, *args, **kwargs):
        self.provisional = None
        return super().send(*args, **kwargs)


def start_provisional_rollover(family='gemini'):
    activate()
    peer = next(f for f in stagger.FAMILIES if f != family)
    p = RolloverProvider(T + 17000, {family: T, peer: T + 9000})
    p.tick()
    original = stagger.load()['runtime']
    p.now = T + 18010
    p.anchors[family] = T + 18000
    p.provisional = family
    return p, peer, original


@pytest.mark.parametrize('family', stagger.FAMILIES)
def test_zero_use_rollover_does_not_adopt_anchor_or_claim_alignment(env, family):
    p, _, original = start_provisional_rollover(family)
    result = p.tick()
    assert result['state'] == 'verifying_rollover'
    assert not result['operational']
    assert result['observed_offset_seconds'] is None
    runtime = stagger.load()['runtime']
    assert runtime['anchors'] == original['anchors']
    assert runtime['phase'] == original['phase']
    assert result['next_check_at'] == p.now + 60
    assert not p.sent


def test_provisional_reads_survive_restart_and_budget_does_not_renew(env):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    candidate = result['provisional_rollovers']['gemini'].copy()
    checks = 0
    while result['next_check_at'] is not None:
        checks += 1
        assert checks <= 26
        p.now = result['next_check_at']
        result = p.tick()
        assert result['provisional_rollovers']['gemini'] == candidate
    assert result['next_check_at'] is None
    assert result['state'] == 'verifying_rollover'
    assert not p.sent
    p.now += 600
    assert p.tick()['provisional_rollovers']['gemini'] == candidate
    assert not p.sent


@pytest.mark.parametrize('family', stagger.FAMILIES)
def test_provisional_to_verified_idle_recovers_and_recurs(env, family):
    p, peer, _ = start_provisional_rollover(family)
    result = p.tick()
    p.anchors.pop(family)
    p.now = result['next_check_at']
    result = p.tick()
    assert len(p.sent) == 1
    assert result['last_anchor_correction']['outcome'] == 'reanchored'
    assert not result['provisional_rollovers']
    assert result['next_family'] == peer
    assert result['next_at'] == p.sent[0][1] + stagger.OFFSET
    for _ in range(5):
        p.now = result['next_at']
        result = p.tick()
    assert [f for f, _ in p.sent] == [family, peer] * 3
    for earlier, later in zip(p.sent, p.sent[1:]):
        assert stagger.OFFSET <= later[1] - earlier[1] <= stagger.OFFSET + 20
    for earlier, later in zip(p.sent, p.sent[2:]):
        assert later[1] - earlier[1] >= stagger.PERIOD


def test_provisional_correction_keeps_real_dispatch_cooldown(env, monkeypatch):
    from tokenkick import antigravity_automation
    p, _, _ = start_provisional_rollover()
    p.tick()
    p.anchors.pop('gemini')
    allowed = p.now + 300
    monkeypatch.setattr(antigravity_automation, 'attempt_retry_at',
                        lambda directory, a: allowed if a.antigravity_family == 'gemini' else None)
    result = p.tick()
    assert not p.sent
    assert result['next_at'] == allowed
    p.now = allowed
    p.tick()
    assert p.sent[0][1] >= allowed


@pytest.mark.parametrize('activity', ['usage', 'different_anchor'])
def test_external_activity_supersedes_provisional_rollover(env, activity):
    p, _, _ = start_provisional_rollover()
    p.tick()
    if activity == 'usage':
        p.provisional = None
    else:
        p.now += 100
        p.anchors['gemini'] = p.now - 40
    result = p.tick()
    assert not result['provisional_rollovers']
    assert stagger.load()['runtime']['anchors']['gemini'] == p.anchors['gemini']
    assert not p.sent


@pytest.mark.parametrize('blocker', ['paused', 'permission', 'unknown_dispatch', 'quota'])
def test_provisional_correction_does_not_bypass_controls(env, monkeypatch, blocker):
    from tokenkick import antigravity_automation
    p, _, _ = start_provisional_rollover()
    p.tick()
    p.anchors.pop('gemini')
    if blocker == 'paused':
        data = stagger.load()
        data['group']['mode'] = 'paused'
        stagger.save(data)
    elif blocker == 'permission':
        config = Config.load()
        config.accounts[0].session_auto_kick = False
        config.save()
    elif blocker == 'unknown_dispatch':
        monkeypatch.setattr(antigravity_automation, 'attempt_retry_at',
                            lambda *_: (_ for _ in ()).throw(ValueError('unconfirmed')))
    else:
        p.weekly['gemini'] = 100
    p.tick()
    assert not p.sent


def test_unknown_evidence_cannot_clear_provisional_guard(env):
    p, _, _ = start_provisional_rollover()
    p.tick()
    candidate = stagger.load()['runtime']['provisional_rollovers'].copy()
    fetch = p.fetch
    def unknown(a, c):
        result = fetch(a, c)
        result.stale = True
        return result
    result = stagger.tick(fetch=unknown, send=p.send, now=lambda: p.now, sleep=p.sleep)
    assert result['state'] == 'blocked'
    assert result['provisional_rollovers'] == candidate
    assert not p.sent


def test_provisional_clock_rollback_cannot_dispatch_or_renew_budget(env):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    candidate = result['provisional_rollovers'].copy()
    p.anchors.pop('gemini')
    p.now -= 600
    result = p.tick()
    assert result['provisional_rollovers'] == candidate
    assert not p.sent


@pytest.mark.parametrize('corrupt', ['family', 'nan', 'expiry', 'check', 'empty'])
def test_provisional_persisted_state_validation(env, corrupt):
    p, _, _ = start_provisional_rollover()
    p.tick()
    data = stagger.load()
    r = data['runtime']
    c = r['provisional_rollovers']['gemini']
    if corrupt == 'family':
        r['provisional_rollovers']['other'] = c
    elif corrupt == 'nan':
        c['anchor'] = 'nan'
    elif corrupt == 'expiry':
        c['expires_at'] += 1
    elif corrupt == 'check':
        r['rollover_check_at'] = c['expires_at'] + 1
    else:
        r['provisional_rollovers'] = {}
    stagger.save(data)
    with pytest.raises(stagger.StaggerError):
        stagger.load()


def test_provisional_status_is_visible_through_terminal_and_mcp(env):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    stream = io.StringIO()
    stagger.render(Console(file=stream, width=150), result)
    assert 'zero use; no new request confirmed' in stream.getvalue()
    assert 'Read-only rollover check' in stream.getvalue()
    def run(argv, **kwargs):
        response = CliRunner().invoke(cli.cli, argv)
        assert response.exit_code == 0, response.output
        return json.loads(response.output)
    core = TokenKickMCPCore()
    env.monkeypatch.setattr(core, '_run', run)
    assert core.tokenkick_stagger_status()['payload']['provisional_rollovers'] == result['provisional_rollovers']


def test_rollover_checks_wake_daemon_before_normal_poll(env, monkeypatch):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    monkeypatch.setattr(stagger.time, 'time', lambda: p.now)
    assert stagger.wake_seconds(600, now=p.now) == 60
    calls = []
    monkeypatch.setattr(stagger, 'tick', lambda **_: calls.append(p.now) or result)
    stagger.daemon_step(Config.load())
    assert not calls
    p.now = result['next_check_at']
    stagger.daemon_step(Config.load())
    assert calls == [p.now]


@pytest.mark.parametrize('plateau_seconds', [34 * 60, 42 * 60])
def test_production_length_plateau_is_never_aligned_and_idle_is_detected_promptly(env, plateau_seconds):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    began = p.now
    evidence_available = began + plateau_seconds
    checks = 0
    while p.now < evidence_available:
        assert result['state'] == 'verifying_rollover'
        assert not p.sent
        next_check = result['next_check_at']
        assert next_check is not None
        p.now = next_check
        if p.now >= evidence_available:
            p.anchors.pop('gemini')
        result = p.tick()
        checks += 1
        assert checks <= 26
    assert len(p.sent) == 1
    assert p.sent[0][1] - evidence_available <= 126
    assert result['last_anchor_correction']['outcome'] == 'reanchored'


def test_both_zero_use_rollovers_never_dispatch_together(env):
    activate()
    p = RolloverProvider(T + 17000, {'gemini': T, 'claude_gpt': T})
    p.tick()
    p.now = T + 18010
    p.anchors = {'gemini': T + 18000, 'claude_gpt': T + 18000}
    fetch = p.fetch
    def zero(a, c):
        s = fetch(a, c)
        for w in s.quota_windows:
            if w['window_kind'] == 'session':
                w['used_percent'] = 0
        return antigravity_family_status(s, a.antigravity_family)
    result = stagger.tick(fetch=zero, send=p.send, now=lambda: p.now, sleep=p.sleep)
    assert set(result['provisional_rollovers']) == set(stagger.FAMILIES)
    p.anchors = {}
    result = p.tick()
    assert len(p.sent) == 1
    assert result['next_at'] >= p.sent[0][1] + stagger.OFFSET


def test_changed_periodic_candidate_does_not_extend_read_budget(env):
    p, _, _ = start_provisional_rollover()
    result = p.tick()
    expires = result['provisional_rollovers']['gemini']['expires_at']
    p.now += stagger.PERIOD
    p.anchors['gemini'] += stagger.PERIOD
    result = p.tick()
    assert result['provisional_rollovers']['gemini']['expires_at'] == expires
    assert result['next_check_at'] is None
    assert not p.sent


def test_legacy_confirmed_stagger_recovery_resumes_alternation_without_duplicate(env):
    from tokenkick import stagger_recovery
    from tokenkick.action_state import action_path
    from tokenkick.antigravity_automation import _attempt_path
    from tokenkick.models import KickEvent, account_key_string

    activate()
    p = Provider()
    p.tick()
    original_send = list(p.sent)
    data = stagger.load()
    attempt = data['runtime']['attempt']
    at = attempt['at']
    attempt['outcome'] = 'unconfirmed'  # 1.23.5 downgraded an already-confirmed send.
    data['runtime'].update(state='blocked', reason='Previous request unconfirmed')
    stagger.save(data)
    a = Config.load().accounts[0]
    action = {'state': 'completed', 'started_at': at+0.1, 'dispatched_at': at+2, 'completed_at': at+5}
    cooldown = {'attempted_at': at+1, 'retry_after': at+18002}
    for path, value in [(action_path(env.directory, account_key_string(a)), action),
                        (_attempt_path(env.directory, a), cooldown)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    history = [KickEvent(label=a.label, timestamp=at+3, kind='session', kick_type='session',
                         success=True, confirmed=True, dispatch_state='sent', evidence_response=True,
                         evidence_provider_moved=True, post_kick_status='confirmed')]
    env.monkeypatch.setattr(stagger_recovery, 'load_kick_history', lambda **_: history)
    p.now += 100
    recovered = p.tick()
    assert recovered['state'] == 'awaiting_evidence'
    assert recovered['request_outcome'] == 'confirmed'
    assert recovered['last_request_recovery']['confirmed_event_at'] == at+3
    assert p.sent == original_send  # The reconciliation turn cannot send.
    assert json.loads(_attempt_path(env.directory, a).read_text()) == cooldown
    # Restart and history pruning do not lose the persisted reconciliation.
    history.clear()
    restarted = Provider(p.now, dict(p.anchors))
    env.monkeypatch.setattr(stagger.time, 'time', lambda: restarted.now)
    for _ in range(6):
        result = restarted.tick()
        assert result['state'] != 'blocked'
        assert result['next_at'] is not None
        restarted.now = result['next_at']
    sent = original_send + restarted.sent
    assert len(sent) >= 5
    assert all(a[0] != b[0] for a, b in zip(sent, sent[1:]))
    assert all(abs((b[1]-a[1])-stagger.OFFSET) <= 30 for a, b in zip(sent, sent[1:]))
    for family in stagger.FAMILIES:
        times = [at for f, at in sent if f == family]
        assert all(b-a >= stagger.PERIOD for a, b in zip(times, times[1:]))


def test_failed_refresh_is_not_reported_as_fresh_stagger_evidence(env):
    activate()
    p = Provider()
    env.monkeypatch.setattr(stagger.time, 'time', lambda: p.now)
    assert p.tick()['evidence_fresh']
    def failed(*_a):
        raise OSError('quota endpoint unavailable')
    p.fetch = failed
    p.now += 30
    result = p.tick()
    assert result['state'] == 'blocked'
    assert not result['evidence_fresh']
    assert result['request_outcome'] == 'confirmed'
    assert len(p.sent) == 1
