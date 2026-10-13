"""Codex plan detection, duration mapping and manual capability fallback regressions."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import time

import click
from click.testing import CliRunner
import pytest

from tokenkick import cli as cli_mod, interactive, sources
from tokenkick.codex_limits import display_status, session_actions_allowed, session_limit_info
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    KickEvent,
    merge_discovered_account,
)
from tokenkick.orchestration import _skip_reason
from tokenkick.status_cache import _account_status_cache_dict, _account_status_from_cache
from tokenkick.status_rendering import _format_session_reset_for_status, _status_rows_as_dict


def account(**kwargs):
    return AccountConfig(
        label="Codex test",
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        identity_email="test@example.test",
        **kwargs,
    )


def window(minutes, *, used=10, due=3600, native=True):
    if native:
        return {
            "usedPercent": used,
            "windowDurationMins": minutes,
            "resetsAt": int(time.time()) + due,
        }
    return {"used_percent": used, "window_minutes": minutes, "resets_at": int(time.time()) + due}


def parse(primary, secondary=None, *, plan="pro", omit_secondary=False):
    bucket = {"primary": primary, "planType": plan}
    if not omit_secondary:
        bucket["secondary"] = secondary
    value = sources._parse_codex_appserver_ratelimits(
        "Codex test", {"id": 2, "result": {"rateLimitsByLimitId": {"codex": bucket}}}
    )
    value.observed_at = datetime.now(timezone.utc).isoformat()
    return value


@pytest.mark.parametrize("plan", ["pro", "prolite", "plus", "future_enterprise"])
@pytest.mark.parametrize("swapped", [False, True])
def test_live_weekly_only_maps_by_duration_not_plan_or_position(plan, swapped):
    weekly = window(10080)
    s = parse(None if swapped else weekly, weekly if swapped else None, plan=plan)
    assert s.state == AccountState.ACTIVE
    assert s.window_minutes == 10080 and s.used_percent == 10
    assert s.session_window_minutes is None and s.session_used_percent is None
    assert s.detected_plan_type == plan
    assert s.codex_session_limit == "absent"
    assert session_limit_info(account(), s)["session_limit_effective"] == "absent"
    assert not session_actions_allowed(account(), s)


@pytest.mark.parametrize("swapped", [False, True])
def test_plus_and_future_plan_windows_work_in_either_order(swapped):
    pair = [window(300, used=12), window(10080, used=34)]
    if swapped:
        pair.reverse()
    s = parse(*pair, plan="future-plan")
    assert (s.window_minutes, s.used_percent) == (10080, 34)
    assert (s.session_window_minutes, s.session_used_percent) == (300, 12)
    assert s.codex_session_limit == "present"
    assert s.detected_plan_type == "future-plan"


@pytest.mark.parametrize(
    "other", [{}, "bad", window(60), window(1440), window(10080), {"usedPercent": float("nan")}]
)
def test_ambiguous_companion_does_not_mean_unlimited(other):
    s = parse(window(10080), other)
    assert s.codex_session_limit == "unknown"
    assert session_limit_info(account(), s)["session_limit_effective"] == "unknown"
    assert not session_actions_allowed(account(), s)
    assert s.source_diagnostics["codex_windows"]


def test_missing_is_not_explicit_null_and_empty_response_is_unknown():
    assert parse(window(10080), omit_secondary=True).codex_session_limit == "unknown"
    assert parse(window(10080), None).codex_session_limit == "absent"
    empty = parse(None, None)
    assert empty.state == AccountState.UNKNOWN
    assert session_limit_info(account(), empty)["session_limit_detected"] == "unknown"


@pytest.mark.parametrize(
    "primary,secondary",
    [(window(300), window(300)), (window(60), None), (window(10080), window(10080))],
)
def test_duplicate_or_novel_windows_cannot_authorize_session(primary, secondary):
    s = parse(primary, secondary)
    assert s.session_window_minutes is None
    assert not session_actions_allowed(account(), s)


@pytest.mark.parametrize("bad", [None, {}, "bad", window(60)])
def test_valid_session_survives_invalid_or_unsupported_weekly_companion(bad):
    s = parse(window(300), bad, plan="plus")
    assert s.session_window_minutes == 300 and s.session_used_percent == 10
    assert s.window_minutes is None
    assert s.codex_session_limit == "present"


@pytest.mark.parametrize("plan", [None, 123, "bad\nplan", "x" * 65])
def test_invalid_or_missing_plan_is_not_invented_from_windows(plan):
    s = parse(window(10080), plan=plan)
    assert s.detected_plan_type is None
    assert s.codex_session_limit == "absent"


@pytest.mark.parametrize("swapped", [False, True])
def test_session_logs_use_same_duration_mapping_but_cannot_prove_current_absence(swapped):
    weekly = window(10080, native=False)
    s = sources._parse_session_rate_limit(
        "Codex test",
        {
            "rate_limits": {
                "primary": None if swapped else weekly,
                "secondary": weekly if swapped else None,
                "plan_type": "prolite",
            }
        },
    )
    s.source_detail = "codex-session-jsonl"
    assert s.window_minutes == 10080 and s.session_window_minutes is None
    assert s.detected_plan_type == "prolite"
    assert session_limit_info(account(), s)["session_limit_effective"] == "unknown"


def test_fresh_absence_and_unknown_capability_do_not_lose_to_old_session_logs():
    older = parse(window(300, used=50), window(10080, used=60), plan="plus")
    older.source_detail = "codex-session-jsonl"
    for current in (parse(window(10080, used=0)), parse(window(10080, used=0), window(60))):
        assert not sources._codex_session_status_should_override_appserver(current, older)


def test_plan_changes_are_reobserved_without_mutating_manual_planning_tier():
    a = account(plan_tier="custom", codex_session_limit_override="auto")
    for s, plan, cap in [
        (parse(window(300), window(10080), plan="plus"), "plus", "present"),
        (parse(window(10080), plan="pro"), "pro", "absent"),
        (parse(window(300), window(10080), plan="plus"), "plus", "present"),
    ]:
        info = session_limit_info(a, s)
        assert info["detected_plan_type"] == plan
        assert info["session_limit_effective"] == cap
        assert a.plan_tier == "custom"


@pytest.mark.parametrize(
    "change", [{"stale": True}, {"error": "refresh failed"}, {"state": AccountState.UNKNOWN}]
)
def test_failed_or_stale_status_never_claims_no_session_limit(change):
    s = replace(parse(window(10080)), **change)
    info = session_limit_info(account(), s)
    assert info["session_limit_effective"] == "unknown"
    assert info["detected_plan_type"] == "pro"


def test_legacy_seven_day_session_cache_requires_refresh():
    old = AccountStatus(
        label="Codex test",
        state=AccountState.ACTIVE,
        session_window_minutes=10080,
        session_used_percent=10,
        session_resets_at=time.time() + 3600,
        source_detail="codex-provider-usage",
    )
    read = _account_status_from_cache(_account_status_cache_dict(old))
    assert read.stale and read.state == AccountState.UNKNOWN and read.error
    assert read.session_window_minutes is None
    assert not session_actions_allowed(account(), read)


def test_override_roundtrip_does_not_destroy_raw_observation_or_toggle_automation():
    a = account(auto_kick=True)
    s = parse(window(300), window(10080), plan="plus")
    raw = _account_status_cache_dict(s)
    disabled = replace(a, codex_session_limit_override="absent")
    view = display_status(disabled, s)
    assert view.session_window_minutes is None and view.window_minutes == 10080
    assert session_limit_info(disabled, s)["session_limit_warning"]
    assert _account_status_cache_dict(s) == raw
    restored = _account_status_from_cache(raw)
    assert display_status(a, restored).session_window_minutes == 300
    assert a.auto_kick and a.session_auto_kick and a.weekly_auto_kick


def test_present_override_does_not_invent_missing_or_ambiguous_window():
    a = account(codex_session_limit_override="present", auto_kick=True)
    for s in (parse(window(10080)), parse(window(10080), window(60))):
        info = session_limit_info(a, s)
        assert info["session_limit_effective"] == "present" and info["session_limit_warning"]
        assert not session_actions_allowed(a, s)
        assert not cli_mod._session_kick_eligible(a, s)
        assert _skip_reason(a, s, False) == "no_session_window"


def test_absent_override_blocks_phantoms_and_session_plans_but_allows_weekly():
    a = account(auto_kick=True, codex_session_limit_override="absent")
    active = parse(window(300, used=1, due=17980), window(10080, used=1, due=604000), plan="plus")
    assert not cli_mod._session_kick_eligible(a, active)
    assert not cli_mod._is_phantom_session_observable(a, active)
    assert not cli_mod._phantom_recovery_should_manage(a, active)
    assert _skip_reason(a, active, False) == "no_session_window"
    ready = parse(window(300, used=10), window(10080, used=0, due=-1), plan="plus")
    assert cli_mod._kick_eligibility(a, ready).kick_type == "kick"


@pytest.fixture
def local_config(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.models.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tokenkick.models.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cli_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli_mod, "_load_status_cache", lambda *_: None)
    return tmp_path


def test_override_cli_roundtrip_preserves_other_accounts_and_user_flags(local_config):
    a = account(auto_kick=True, weekly_auto_kick=False, session_auto_kick=True, plan_tier="custom")
    other = AccountConfig(label="Claude", provider="claude", auto_kick=True)
    Config(accounts=[a, other]).save()
    for mode in ("absent", "present", "auto"):
        result = CliRunner().invoke(
            cli_mod.cli, ["accounts", "set-session-limit", a.label, mode, "--json-output"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)["payload"]["account"]
        assert payload["codex_session_limit_override"] == mode
        saved = Config.load()
        assert saved.accounts[1] == other
        assert saved.accounts[0] == replace(a, codex_session_limit_override=mode)
    rejected = CliRunner().invoke(
        cli_mod.cli, ["accounts", "set-session-limit", "Claude", "absent", "--json-output"]
    )
    assert rejected.exit_code == 2
    assert Config.load().accounts[1] == other


def test_override_validated_and_preserved_on_discovery():
    a = account(codex_session_limit_override="absent")
    merged = merge_discovered_account(a, account())
    assert merged.codex_session_limit_override == "absent"
    with pytest.raises(ValueError):
        account(codex_session_limit_override="unlimited")


@pytest.mark.parametrize("confirm", [False, True])
def test_override_menu_selects_one_account_and_requires_confirmation(
    monkeypatch, local_config, confirm
):
    a = account(auto_kick=True)
    other = replace(a, label="Other account")
    Config(accounts=[a, other]).save()
    selections = iter(["session_limit", a.label, "absent", interactive.MENU_EXIT])
    monkeypatch.setattr(interactive, "_select", lambda *_args, **_kw: next(selections))
    prompts = []

    def ask(message, **kw):
        prompts.append(message)
        assert kw["default"] is False
        return confirm

    monkeypatch.setattr(interactive, "_confirm", ask)
    interactive._accounts_menu(click.Context(cli_mod.cli))
    saved = Config.load()
    assert saved.accounts[0].codex_session_limit_override == ("absent" if confirm else "auto")
    assert saved.accounts[1] == other
    assert a.label in prompts[0]


def test_cached_account_metadata_and_status_json_expose_evidence(monkeypatch, local_config):
    a = account()
    s = parse(window(10080), plan="prolite")
    Config(accounts=[a]).save()
    monkeypatch.setattr(cli_mod, "_load_status_cache", lambda *_: ([a], [s], {}))
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "list", "--json-output"])
    row = json.loads(result.output)["payload"]["accounts"][0]
    assert row["detected_plan_type"] == "prolite"
    assert row["session_limit_detected"] == "absent"
    assert row["session_limit_override"] == "auto"
    monkeypatch.setattr(cli_mod, "load_kick_history", lambda **_: [])
    monkeypatch.setattr("tokenkick.status_rendering._pending_by_label", lambda: {})
    row = _status_rows_as_dict([s], [a])[0]
    assert row["session_limit_effective"] == "absent"
    assert row["window_minutes"] == 10080
    assert row.get("session_window_minutes") is None
    assert "No five-hour limit" in _format_session_reset_for_status(s, codex_session_limit="absent")
    assert "unknown" in _format_session_reset_for_status(s, codex_session_limit="unknown")
    result = CliRunner().invoke(cli_mod.cli, ["accounts", "detail", a.label])
    assert "prolite" in result.output and "Detected 5h limit" in result.output


@pytest.mark.parametrize("mode", ["auto", "present", "absent"])
def test_queued_session_preflight_cannot_kick_after_upgrade_to_weekly_only(
    monkeypatch, local_config, mode
):
    a = account(auto_kick=True, codex_session_limit_override=mode)
    old = parse(window(300, due=-1), window(10080), plan="plus")
    new = parse(window(10080, used=0, due=-1), plan="pro")
    calls = []
    monkeypatch.setattr(cli_mod, "_fetch_status", lambda *_: calls.append("read") or new)
    monkeypatch.setattr(
        cli_mod, "_kick_and_notify_owned", lambda *_a, **_k: pytest.fail("Must not generate")
    )
    event = cli_mod._kick_and_notify(
        a, Config(accounts=[a]), old, automatic=True, kick_type="session"
    )
    assert calls == ["read"]
    assert not event.success and "session kick blocked" in event.error


def test_final_preflight_honors_override_changed_after_planning(monkeypatch, local_config):
    a = account(auto_kick=True)
    Config(accounts=[a]).save()
    old_config = Config.load()
    Config(accounts=[replace(a, codex_session_limit_override="absent")]).save()
    s = parse(window(300, due=-1), window(10080), plan="plus")
    monkeypatch.setattr(cli_mod, "_fetch_status", lambda *_: s)
    monkeypatch.setattr(
        cli_mod, "_kick_and_notify_owned", lambda *_a, **_k: pytest.fail("Must not generate")
    )
    event = cli_mod._kick_and_notify(a, old_config, s, automatic=True, kick_type="session")
    assert not event.success


def test_native_weekly_only_still_allows_weekly_preflight(monkeypatch, local_config):
    a = account(auto_kick=True)
    s = parse(window(10080, used=0, due=-1))
    monkeypatch.setattr(cli_mod, "_fetch_status", lambda *_: s)
    monkeypatch.setattr(cli_mod, "load_kick_history", lambda **_: [])
    calls = []

    def send(*_a, **_k):
        calls.append("weekly")
        return KickEvent(label=a.label, success=True)

    monkeypatch.setattr(cli_mod, "_kick_and_notify_owned", send)
    assert cli_mod._kick_and_notify(
        a, Config(accounts=[a]), s, automatic=True, kick_type="kick"
    ).success
    assert calls == ["weekly"]


@pytest.mark.parametrize("capability", ["absent", "unknown"])
def test_due_session_queue_cannot_become_a_weekly_kick(monkeypatch, capability):
    from datetime import timedelta
    from tokenkick.models import account_key_string
    from tokenkick.scheduling import PendingKick, to_utc_iso

    a = account(auto_kick=True)
    now = datetime.now(timezone.utc)
    key = account_key_string(a)
    pending = PendingKick(
        key,
        a.label,
        a.provider,
        to_utc_iso(now - timedelta(minutes=1)),
        to_utc_iso(now - timedelta(hours=1)),
        "optimal",
        1,
        0,
        "none",
        to_utc_iso(now - timedelta(minutes=1)),
        to_utc_iso(now + timedelta(hours=1)),
        window_basis="session",
    )
    s = parse(window(10080, used=0, due=-1), None if capability == "absent" else window(60))
    removed = []
    monkeypatch.setattr(cli_mod, "load_pending_kicks", lambda *_: {key: pending})
    monkeypatch.setattr(cli_mod, "remove_pending_kick", lambda a: removed.append(a.label))
    monkeypatch.setattr(
        cli_mod, "_kick_and_notify", lambda *_a, **_kw: pytest.fail("No kick should be sent")
    )
    assert (
        cli_mod._execute_due_pending_kicks([a], Config(accounts=[a]), statuses_by_key={key: s}) == 0
    )
    assert removed == ([a.label] if capability == "absent" else [])


def test_send_time_check_bypasses_polling_cache(monkeypatch, local_config):
    a = account(auto_kick=True)
    ready = parse(window(10080, used=0, due=-1))
    outer = {"old": object()}
    monkeypatch.setattr(sources, "_CODEX_APPSERVER_CACHE", outer)
    monkeypatch.setattr(sources, "_CODEX_APPSERVER_USAGE_CACHE", outer)

    def read(*_args):
        assert sources._CODEX_APPSERVER_CACHE == {}
        assert sources._CODEX_APPSERVER_USAGE_CACHE == {}
        return ready

    monkeypatch.setattr(cli_mod, "_fetch_status", read)
    monkeypatch.setattr(cli_mod, "load_kick_history", lambda **_: [])
    monkeypatch.setattr(
        cli_mod, "_kick_and_notify_owned", lambda *_a, **_kw: KickEvent(label=a.label, success=True)
    )
    assert cli_mod._kick_and_notify(
        a, Config(accounts=[a]), ready, automatic=True, kick_type="kick"
    ).success
    assert sources._CODEX_APPSERVER_CACHE is outer
    assert sources._CODEX_APPSERVER_USAGE_CACHE is outer


def test_override_cannot_strip_synthetic_prediction_guard():
    from tokenkick.models import mark_synthetic_status, is_synthetic_status

    a = account(codex_session_limit_override="absent")
    s = parse(window(10080, used=0, due=-1))
    predicted = mark_synthetic_status(s, "test prediction")
    assert is_synthetic_status(display_status(a, predicted))
    eligibility = cli_mod._kick_eligibility(a, predicted)
    assert not eligibility.kickable and eligibility.reason == "predicted_status_requires_refresh"


@pytest.mark.parametrize(
    "change", [{"stale": True}, {"error": "unavailable"}, {"source_detail": "codex-session-jsonl"}]
)
def test_automatic_native_preflight_blocks_stale_failed_or_fallback_reads(
    monkeypatch, local_config, change
):
    a = account(auto_kick=True)
    fresh = parse(window(10080, used=0, due=-1))
    monkeypatch.setattr(cli_mod, "_fetch_status", lambda *_: replace(fresh, **change))
    monkeypatch.setattr(
        cli_mod, "_kick_and_notify_owned", lambda *_a, **_kw: pytest.fail("Must not generate")
    )
    event = cli_mod._kick_and_notify(
        a, Config(accounts=[a]), fresh, automatic=True, force=True, kick_type="kick"
    )
    assert not event.success and "Fresh native" in event.error
