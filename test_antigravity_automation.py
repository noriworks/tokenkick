"""Family isolation, native requests, durable suppression and MCP consent regressions."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import subprocess
import time
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from tokenkick import antigravity_automation as automation
from tokenkick import cli as cli_mod
from tokenkick.antigravity import ANTIGRAVITY_QUOTA_WINDOW_SPECS, antigravity_family_status
from tokenkick.kicker import kick_account, kick_invocation_for_account
from tokenkick.mcp_core import TokenKickMCPCore
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    account_key_string,
)


def account(family="gemini", **kwargs):
    return AccountConfig(
        label=f"agy {family}",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
        antigravity_family=family,
        identity_email="test@example.test",
        **kwargs,
    )


def status(family="gemini", *, session=0, weekly=0, used=0):
    now = time.time()
    windows = []
    for key, spec in ANTIGRAVITY_QUOTA_WINDOW_SPECS.items():
        seconds = (
            (session if spec["window_kind"] == "session" else weekly)
            if spec["family"] == family
            else 12345
        )
        windows.append(
            {
                "id": key,
                **spec,
                "used_percent": used if spec["family"] == family else 99,
                "resets_at": now + seconds - (1 if seconds == 0 else 0),
                "resets_in_seconds": seconds,
            }
        )
    return antigravity_family_status(
        AccountStatus(
            label=f"agy {family}",
            state=AccountState.ACTIVE,
            quota_windows=windows,
            source_detail="antigravity-cli",
            observed_at=datetime.now(timezone.utc).isoformat(),
        ),
        family,
    )


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    for module in ("models", "cli", "kicker"):
        monkeypatch.setattr(f"tokenkick.{module}.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.models.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(
        "tokenkick.kicker.read_antigravity_cli_identity", lambda: "test@example.test"
    )
    monkeypatch.setattr("tokenkick.kicker.antigravity_cli_binary", lambda: "/bin/agy")
    monkeypatch.setattr(
        "tokenkick.kicker.read_antigravity_models",
        lambda _: ["Gemini 3.8 Flash (Low)", "Claude Sonnet 5.5 (Low)"],
    )


def test_migration_is_idempotent_preserves_other_accounts_and_disables_new_automation(tmp_path):
    legacy = replace(account(None, auto_kick=True), label="custom agy", visible=False)
    other = AccountConfig(label="claude", provider="claude", auto_kick=True)
    Config(accounts=[legacy, other]).save()
    migrated = Config.load()
    assert [(a.label, a.antigravity_family) for a in migrated.accounts] == [
        ("custom agy", "gemini"),
        ("custom agy (Claude)", "claude_gpt"),
        ("claude", None),
    ]
    assert all(
        not a.auto_kick and not a.session_auto_kick and not a.weekly_auto_kick and not a.visible
        for a in migrated.accounts[:2]
    )
    assert migrated.accounts[-1] == other
    assert Config.load().accounts == migrated.accounts
    assert len({account_key_string(a) for a in migrated.accounts}) == 3
    first = replace(migrated.accounts[0], auto_kick=True)
    migrated.accounts[0] = first
    migrated.save()
    assert Config.load().accounts[0].auto_kick


def test_migration_avoids_label_collisions_and_preserves_schedules():
    from tokenkick.models import ScheduleConfig, WorkSchedule

    legacy = replace(account(None), label="agy")
    collision = AccountConfig(label="agy (Claude)", provider="claude")
    Config(
        accounts=[legacy, collision],
        schedule=ScheduleConfig(accounts={"agy": WorkSchedule(weekdays="09:00-17:00")}),
    ).save()
    config = Config.load()
    assert [a.label for a in config.accounts] == ["agy", "agy (Claude) 2", "agy (Claude)"]
    assert config.schedule.accounts["agy (Claude) 2"] == config.schedule.accounts["agy"]


@pytest.mark.parametrize("family", ["gemini", "claude_gpt"])
def test_projection_uses_only_matching_weekly_and_session_metrics(family):
    s = status(family, session=0, weekly=1000, used=3)
    assert s.used_percent == s.session_used_percent == 3
    assert s.resets_in_seconds == 1000
    assert s.session_resets_in_seconds == 0
    assert s.window_minutes == 10080
    assert s.session_window_minutes == 300
    assert len(s.quota_windows) == 4
    assert automation.usable_status(account(family), s)
    assert not automation.usable_status(
        account("claude_gpt" if family == "gemini" else "gemini"), s
    )


@pytest.mark.parametrize(
    "bad", ["missing", "duplicate", "nan", "boolean", "family", "window", "nonobject"]
)
def test_projection_rejects_malformed_or_incomplete_buckets(bad):
    s = status()
    if bad == "missing":
        s.quota_windows.pop()
    elif bad == "duplicate":
        s.quota_windows.append(s.quota_windows[0])
    elif bad == "nan":
        s.quota_windows[0]["used_percent"] = float("nan")
    elif bad == "boolean":
        s.quota_windows[0]["resets_at"] = True
    elif bad == "family":
        s.quota_windows[0]["family"] = "claude_gpt"
    elif bad == "window":
        s.quota_windows[0]["window_minutes"] = 5
    elif bad == "nonobject":
        s.quota_windows[0] = None
    assert antigravity_family_status(s, "gemini").state == AccountState.UNKNOWN


@pytest.mark.parametrize(
    "family,model",
    [("gemini", "Gemini 3.8 Flash (Low)"), ("claude_gpt", "Claude Sonnet 5.5 (Low)")],
)
def test_native_invocation_is_economical_bounded_and_isolated(family, model, tmp_path):
    invocation = kick_invocation_for_account(account(family))
    assert invocation.model == model
    assert "--effort" not in invocation.command
    assert "--sandbox" in invocation.command
    assert "--dangerously-skip-permissions" not in invocation.command
    assert "--mode" not in invocation.command  # ignored alongside disabled slash commands
    assert invocation.command[invocation.command.index("--print-timeout") + 1] == "45s"
    assert invocation.cwd == tmp_path / "antigravity-kick-workspace"


def test_identity_mismatch_sends_no_request(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.kicker.read_antigravity_cli_identity", lambda: "other@example.test"
    )
    monkeypatch.setattr(
        "tokenkick.kicker.subprocess.run", lambda *a, **k: pytest.fail("must not generate")
    )
    event = kick_account(account(), record=False)
    assert not event.success
    assert "identity" in event.error


@pytest.mark.parametrize(
    "output,returncode,evidence",
    [("OK\n", 0, True),
        ("OK.\n", 0, True),
        ("ok!", 0, True), ("", 0, False), ("secret unexpected response", 0, False), ("", 2, False)],
)
def test_native_exit_does_not_confirm_anchor_or_store_arbitrary_output(
    monkeypatch, output, returncode, evidence
):
    def run(cmd, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 60
        return SimpleNamespace(returncode=returncode, stdout=output, stderr="Bearer secret")

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", run)
    event = kick_account(account(), record=False)
    assert not event.confirmed
    assert event.evidence_response == evidence
    assert "secret" not in json.dumps(event.to_dict())


@pytest.mark.parametrize(
    "change", ["stale", "error", "source", "synthetic", "wrong_family", "missing_family"]
)
def test_eligibility_fails_closed_without_live_family_data(change):
    a, s = account(), status()
    if change == "stale":
        s.stale = True
    elif change == "error":
        s.error = "read failed"
    elif change == "source":
        s.source_detail = "codexbar"
    elif change == "synthetic":
        from tokenkick.models import mark_synthetic_status

        mark_synthetic_status(s, "predicted")
    elif change == "wrong_family":
        a = account("claude_gpt")
    else:
        a = account(None)
    assert not cli_mod._kick_eligibility(a, s).kickable


def test_eligibility_and_exhaustion_are_family_scoped():
    a = account()
    assert cli_mod._kick_eligibility(a, status()).kick_type == "kick"
    assert cli_mod._kick_eligibility(a, status(weekly=1000)).kick_type == "session"
    assert not cli_mod._kick_eligibility(a, status(session=1000, weekly=1000)).kickable
    assert not cli_mod._kick_eligibility(a, status(weekly=1000, used=100)).kickable


def test_attempt_guard_survives_rename_history_rotation_and_corrupt_state(tmp_path, monkeypatch):
    a, s = account(), status()
    automation.record_attempt(tmp_path, a, s)
    assert not cli_mod._kick_eligibility(
        replace(a, label="renamed"), replace(s, label="renamed"), history=[]
    ).kickable
    assert cli_mod._kick_eligibility(account("claude_gpt"), status("claude_gpt")).kickable
    now = time.time()
    monkeypatch.setattr(automation.time, "time", lambda: now + 18001)
    assert automation.attempt_blocked(tmp_path, a, s)
    assert automation.attempt_blocked(tmp_path, a, replace(s, session_resets_at=now + 18000))
    automation.record_confirmation(tmp_path, a, replace(s, session_resets_at=now + 18000))
    assert not automation.attempt_blocked(tmp_path, a, s)
    automation._attempt_path(tmp_path, a).write_text("bad json")
    assert automation.attempt_blocked(tmp_path, a, s)


@pytest.mark.parametrize(
    "movement,confirmed",
    [
        ("usage", False),
        ("anchor", True),
        ("stale", False),
        ("other_family", False),
        ("error", False),
    ],
)
def test_post_request_verification_requires_correct_family_reset_clock(
    monkeypatch, movement, confirmed, tmp_path
):
    a, before = account(), status(weekly=1000)
    after = status(weekly=1000, used=1, session=18000 if movement in {"anchor", "stale"} else 0)
    if movement == "stale":
        after.stale = True
    if movement == "other_family":
        after = status("claude_gpt", session=18000)

    def fetch(*args):
        if movement == "error":
            raise OSError("private error")
        return after

    monkeypatch.setattr(cli_mod, "_fetch_status", fetch)
    monkeypatch.setattr(
        "tokenkick.kicker.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="OK\n", stderr=""),
    )
    events = []
    monkeypatch.setattr(cli_mod, "append_kick_event", events.append)
    event = cli_mod._run_kick_attempt(
        a,
        Config(),
        phantom_recovery=False,
        daemon_log=False,
        kick_type="session",
        model_override=None,
        pre_status=before,
    )
    assert event.confirmed is confirmed
    assert events == [event]
    assert automation.attempt_blocked(tmp_path, a, before)
    assert "private" not in json.dumps(event.to_dict())


def test_timeout_persists_attempt_and_suppresses_next_automatic_request(monkeypatch, tmp_path):
    a, before = account(), status()

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("agy", 60)

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", run)
    monkeypatch.setattr(cli_mod, "append_kick_event", lambda _: None)
    event = cli_mod._run_kick_attempt(
        a,
        Config(),
        phantom_recovery=False,
        daemon_log=False,
        kick_type="kick",
        model_override=None,
        pre_status=before,
    )
    assert not event.success
    assert automation.attempt_blocked(tmp_path, a, before)


def test_cli_consent_and_family_controls_do_not_generate(monkeypatch):
    Config(accounts=[account(), account("claude_gpt")]).save()
    monkeypatch.setattr(cli_mod, "_load_accounts", lambda config: config.accounts)
    monkeypatch.setattr(cli_mod, "kick_account", lambda *a, **k: pytest.fail("must not generate"))
    runner = CliRunner()
    refused = runner.invoke(cli_mod.cli, ["auto", "enable", "agy gemini", "--json-output"])
    assert refused.exit_code == 1
    assert json.loads(refused.output)["error_code"] == "auto_kick_consent_required"
    allowed = runner.invoke(
        cli_mod.cli, ["auto", "enable", "agy gemini", "--json-output", "--accept-risk", "ENABLE"]
    )
    assert allowed.exit_code == 0, allowed.output
    assert Config.load().accounts[0].auto_kick
    assert not Config.load().accounts[1].auto_kick


@pytest.fixture
def mcp_core(monkeypatch):
    core = TokenKickMCPCore(tk_path="/fake/tk")
    target = {
        "label": "agy gemini",
        "provider": "antigravity",
        "kickable": True,
        "account_fingerprint": "stable-family-id",
        "auto_kick": False,
        "weekly_auto_kick": False,
        "session_auto_kick": False,
    }
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return {"ok": True, "payload": {"accounts": [target.copy()]}}

    monkeypatch.setattr(core, "_run", run)
    return core, target, calls


@pytest.mark.parametrize("scope", ["all", "weekly", "session"])
def test_mcp_enable_binds_scope_and_requires_consent(mcp_core, scope):
    core, target, calls = mcp_core
    preview = core.tokenkick_auto_set_preview(account=target["label"], enabled=True, scope=scope)
    assert len(calls) == 1 and calls[0][:2] == ["auto", "status"]
    assert "consume" in preview["payload"]["consent_text"]
    token = preview["preview_token"]["token"]
    args = dict(
        account=target["label"], enabled=True, scope=scope, preview_token=token, confirm=True
    )
    with pytest.raises(ValueError, match="quota_ack"):
        core.tokenkick_auto_set(**args)
    with pytest.raises(ValueError, match="accept_risk"):
        core.tokenkick_auto_set(**args, quota_ack=True)
    result = core.tokenkick_auto_set(**args, quota_ack=True, accept_risk=True)
    assert result["ok"]
    assert calls[-1][:-2] == [
        "auto",
        *([] if scope == "all" else [scope]),
        "enable",
        target["label"],
        "--json-output",
        "--accept-risk",
        "ENABLE",
    ]
    with pytest.raises(ValueError, match="already used"):
        core.tokenkick_auto_set(**args, quota_ack=True, accept_risk=True)


def test_mcp_disable_needs_no_risk_consent_and_rejects_target_change(mcp_core):
    core, target, calls = mcp_core
    preview = core.tokenkick_auto_set_preview(account=target["label"], enabled=False)
    target["account_fingerprint"] = "replaced-identity"
    with pytest.raises(ValueError, match="changed"):
        core.tokenkick_auto_set(
            account=target["label"],
            enabled=False,
            confirm=True,
            preview_token=preview["preview_token"]["token"],
        )
    preview = core.tokenkick_auto_set_preview(account=target["label"], enabled=False)
    assert core.tokenkick_auto_set(
        account=target["label"],
        enabled=False,
        confirm=True,
        preview_token=preview["preview_token"]["token"],
    )["ok"]
    assert calls[-1][:-2] == ["auto", "disable", target["label"], "--json-output"]


def test_mcp_cannot_change_scope_or_enable_state_after_preview(mcp_core):
    core, target, calls = mcp_core
    preview = core.tokenkick_auto_set_preview(account=target["label"], enabled=True, scope="weekly")
    with pytest.raises(ValueError, match="arguments"):
        core.tokenkick_auto_set(
            account=target["label"],
            enabled=True,
            scope="session",
            confirm=True,
            quota_ack=True,
            accept_risk=True,
            preview_token=preview["preview_token"]["token"],
        )
    assert len(calls) == 1


def test_mcp_transport_exposes_controls():
    import asyncio
    from tokenkick.mcp_server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"tokenkick_auto_status", "tokenkick_auto_set_preview", "tokenkick_auto_set"} <= names


@pytest.mark.parametrize(
    "observed", [None, "invalid", "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"]
)
def test_quota_age_must_be_known_recent_and_plausible(observed):
    assert not automation.usable_status(account(), replace(status(), observed_at=observed))


def test_pending_schedule_blocks_immediate_family_kick():
    assert not cli_mod._kick_eligibility(account(), status(), pending_kick=object()).kickable


def test_cli_rechecks_mcp_fingerprint_before_config_write(monkeypatch):
    Config(accounts=[account()]).save()
    monkeypatch.setattr(cli_mod, "_load_accounts", lambda config: config.accounts)
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "auto",
            "enable",
            "agy gemini",
            "--json-output",
            "--accept-risk",
            "ENABLE",
            "--expected-auto-fingerprint",
            "stale",
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error_code"] == "stale_preview"
    assert not Config.load().accounts[0].auto_kick


def test_discovery_keeps_families_distinct_and_preserves_configured_flags(monkeypatch):
    from tokenkick import discovery

    monkeypatch.setattr(discovery, "antigravity_cli_detected", lambda: True)
    monkeypatch.setattr(discovery, "read_antigravity_cli_identity", lambda: "test@example.test")
    reads = []
    monkeypatch.setattr(cli_mod, "fetch_status", lambda a: reads.append(a) or status())
    accounts, statuses = [], []
    discovery._append_antigravity_cli_account(accounts, statuses)
    assert len(reads) == 1
    assert len({account_key_string(a) for a in accounts}) == 2
    labeled, statuses = discovery._apply_display_labels(list(zip(accounts, statuses)))
    assert labeled[0].label != labeled[1].label
    configured = replace(labeled[0], label="custom", auto_kick=True, weekly_auto_kick=False)
    merged, s = discovery._pair_for_configured_account(
        configured, {discovery._account_key(labeled[0]): (labeled[0], statuses[0])}
    )
    assert merged.label == "custom" and merged.auto_kick and not merged.weekly_auto_kick
    assert s.label == "custom"


def test_force_still_requires_fresh_native_status_before_sending(monkeypatch):
    monkeypatch.setattr(cli_mod, "_fetch_status", lambda *a: replace(status(), stale=True))
    monkeypatch.setattr(cli_mod, "kick_account", lambda *a, **k: pytest.fail("must not generate"))
    event = cli_mod._kick_and_notify(account(), Config(), status(), force=True)
    assert not event.success
    assert "Fresh complete" in event.error


def test_mcp_stdio_roundtrip_uses_real_cli_and_rejects_replay(tmp_path):
    import asyncio
    import os
    from pathlib import Path
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    home = tmp_path / "stdio-home"
    (home / ".tokenkick").mkdir(parents=True)
    (home / ".tokenkick/config.json").write_text(json.dumps({"accounts": [account().to_dict()]}))
    launcher = tmp_path / "tk-cli"
    launcher.write_text(f"#!{sys.executable}\nfrom tokenkick.cli import cli\ncli()\n")
    launcher.chmod(0o700)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tokenkick.mcp_server"],
        cwd=str(Path(__file__).parent),
        env={
            "HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "TOKENKICK_TK_PATH": str(launcher),
            "PYTHONPATH": str(Path(__file__).parent),
        },
    )

    async def run():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert "tokenkick_auto_set" in {t.name for t in tools.tools}

                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.isError
                    value = json.loads(result.content[0].text)
                    assert value["ok"], value
                    return value

                for enabled in (True, False):
                    preview = await call(
                        "tokenkick_auto_set_preview", {"account": "agy gemini", "enabled": enabled}
                    )
                    args = {
                        "account": "agy gemini",
                        "enabled": enabled,
                        "preview_token": preview["preview_token"]["token"],
                        "confirm": True,
                        "quota_ack": enabled,
                        "accept_risk": enabled,
                    }
                    await call("tokenkick_auto_set", args)
                    state = await call("tokenkick_auto_status", {})
                    assert state["payload"]["accounts"][0]["auto_kick"] is enabled
                    assert (await client.call_tool("tokenkick_auto_set", args)).isError

    asyncio.run(run())


def test_zero_rounded_weekly_usage_does_not_invent_a_weekly_reset():
    s = status(weekly=1000, session=0, used=0)
    assert s.state == AccountState.ACTIVE
    assert cli_mod._kick_type_for_status(s) == "session"
    assert cli_mod._kick_eligibility(account(), s).kick_type == "session"


def test_catalog_failure_does_not_create_uncertain_sent_marker(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.kicker.read_antigravity_models", lambda _: [])
    monkeypatch.setattr(
        "tokenkick.kicker.subprocess.run", lambda *a, **k: pytest.fail("must not send")
    )
    monkeypatch.setattr(cli_mod, "append_kick_event", lambda _: None)
    a, before = account(), status()
    event = cli_mod._run_kick_attempt(
        a,
        Config(),
        phantom_recovery=False,
        daemon_log=False,
        kick_type="kick",
        model_override=None,
        pre_status=before,
    )
    assert not event.success
    assert not automation.attempt_blocked(tmp_path, a, before)


def test_attempt_persistence_failure_cannot_send(monkeypatch):
    def fail():
        raise OSError("private path")

    monkeypatch.setattr(
        "tokenkick.kicker.subprocess.run", lambda *a, **k: pytest.fail("must not send")
    )
    event = kick_account(account(), record=False, before_send=fail)
    assert not event.success
    assert "private" not in event.error


def test_disabling_legacy_unsupported_account_is_always_available(monkeypatch):
    legacy = AccountConfig(label="legacy agy", provider="antigravity", auto_kick=True)
    Config(accounts=[legacy]).save()
    monkeypatch.setattr(cli_mod, "_load_accounts", lambda config: config.accounts)
    result = CliRunner().invoke(cli_mod.cli, ["auto", "disable", legacy.label, "--json-output"])
    assert result.exit_code == 0, result.output
    assert not Config.load().accounts[0].auto_kick


def test_native_agy_identity_precedes_unrelated_gemini_login(tmp_path):
    import base64
    from tokenkick.antigravity import read_antigravity_cli_identity

    directory = tmp_path / ".gemini/antigravity-cli"
    directory.mkdir(parents=True)
    claim = (
        base64.urlsafe_b64encode(json.dumps({"email": "native@example.test"}).encode())
        .decode()
        .rstrip("=")
    )
    native = directory / "antigravity-oauth-token"
    native.write_text(
        json.dumps({"id_token": f"header.{claim}.signature", "token": {"access_token": "secret"}})
    )
    (directory.parent / "google_accounts.json").write_text(
        json.dumps({"active": "other@example.test"})
    )
    assert read_antigravity_cli_identity(tmp_path) == "native@example.test"
    native.write_text("invalid")
    assert read_antigravity_cli_identity(tmp_path) is None


def test_migration_pins_previously_anonymous_native_identity(monkeypatch):
    monkeypatch.setattr(
        "tokenkick.antigravity.read_antigravity_cli_identity", lambda: "native@example.test"
    )
    Config(accounts=[replace(account(None), identity_email=None)]).save()
    migrated = Config.load()
    assert len(migrated.accounts) == 2
    assert all(
        a.identity_email == "native@example.test" and not a.auto_kick for a in migrated.accounts
    )


@pytest.mark.parametrize('retry_after', [float('nan'), float('inf'), -1, True])
def test_malformed_retry_deadline_cannot_reenable_generation(tmp_path, retry_after):
    path = automation._attempt_path(tmp_path, account())
    path.parent.mkdir()
    path.write_text(json.dumps({'attempted_at': time.time(), 'retry_after': retry_after}))
    assert automation.attempt_blocked(tmp_path, account(), status())
