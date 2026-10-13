"""Native idle horizons, normal kick eligibility, and real anchor evidence."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tokenkick import antigravity_automation as automation, cli, sources, status_rendering
from tokenkick.antigravity import (
    ANTIGRAVITY_QUOTA_WINDOW_SPECS,
    antigravity_family_status,
    antigravity_status_from_quota_summary,
)
from tokenkick.antigravity_windows import (
    classify_session_clocks,
    needs_idle_observation,
    verified_unanchored,
)
from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    account_key_string,
)

NOW = 1791890000.0


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def account(family="gemini"):
    return AccountConfig(
        label="antigravity" if family == "gemini" else "antigravity (Claude)",
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
        antigravity_family=family,
        identity_email="fixture@example.test",
    )


def reading(observed, *, fixed=None, family="gemini", used=0, evidence="provider_timestamp"):
    windows = []
    for key, spec in ANTIGRAVITY_QUOTA_WINDOW_SPECS.items():
        is_session = spec["window_kind"] == "session"
        reset = (
            (fixed if fixed is not None and spec["family"] == family else observed + 18000)
            if is_session
            else NOW + 400000
        )
        windows.append(
            {
                "id": key,
                **spec,
                "used_percent": used if is_session else 20,
                "resets_at": reset,
                "resets_in_seconds": max(0, int(reset - observed)),
                "source": "antigravity-cli",
                "reset_evidence": evidence,
            }
        )
    return antigravity_family_status(
        AccountStatus(
            label=account(family).label,
            state=AccountState.ACTIVE,
            source_detail="antigravity-cli",
            observed_at=stamp(observed),
            quota_windows=windows,
        ),
        family,
    )


def idle(family="gemini"):
    return antigravity_family_status(
        classify_session_clocks(reading(NOW, family=family), reading(NOW + 6, family=family)),
        family,
    )


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(automation.time, "time", lambda: NOW + 6)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli, "STATUS_CACHE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(cli, "load_pending_kicks", lambda *_: {})
    monkeypatch.setattr(cli, "load_kick_history", lambda **_: [])


@pytest.mark.parametrize("family", ["gemini", "claude_gpt"])
def test_two_sliding_native_reads_make_normal_session_kick_eligible(family, monkeypatch):
    status = idle(family)
    assert status.window_anchor_state == "available_unanchored"
    assert verified_unanchored(status, family)
    assert cli._kick_eligibility(account(family), status).kick_type == "session"
    monkeypatch.setattr(cli, "_fetch_status", lambda *_: status)
    target = cli._resolve_single_kick_target(
        account(family).label,
        Config(),
        [account(family)],
        force=False,
        dry_run=True,
        confirm=lambda *_: False,
    )
    assert target.account == account(family)


@pytest.mark.parametrize("elapsed", [0, 1, 4, -1, 121])
def test_too_close_reused_reversed_or_old_observations_cannot_open_window(elapsed):
    status = antigravity_family_status(
        classify_session_clocks(reading(NOW), reading(NOW + elapsed)), "gemini"
    )
    assert not verified_unanchored(status, "gemini")
    assert not cli._kick_eligibility(account(), status).kickable


@pytest.mark.parametrize("elapsed", [5, 6, 60, 120])
def test_independent_sliding_reads_within_bounds(elapsed):
    result = antigravity_family_status(
        classify_session_clocks(reading(NOW), reading(NOW + elapsed)), "gemini"
    )
    assert verified_unanchored(result, "gemini")


@pytest.mark.parametrize(
    "change", ["used", "relative", "stale", "error", "source", "no_observed", "partial_shift"]
)
def test_ambiguous_or_untrusted_horizons_stay_ineligible(change):
    first, second = reading(NOW), reading(NOW + 6)
    if change == "used":
        second = reading(NOW + 6, used=0.001)
    elif change == "relative":
        second = reading(NOW + 6, evidence="relative_estimate")
    elif change == "stale":
        second.stale = True
    elif change == "error":
        second.error = "quota read failed"
    elif change == "source":
        second.source_detail = "codexbar"
    elif change == "no_observed":
        second.observed_at = None
    elif change == "partial_shift":
        second = reading(NOW + 6, fixed=NOW + 18003)
    result = antigravity_family_status(classify_session_clocks(first, second), "gemini")
    assert not verified_unanchored(result, "gemini")
    assert not cli._kick_eligibility(account(), result).kickable


def test_stable_deadline_is_active_even_at_zero_usage():
    status = antigravity_family_status(
        classify_session_clocks(
            reading(NOW, fixed=NOW + 18000), reading(NOW + 6, fixed=NOW + 18000)
        ),
        "gemini",
    )
    assert status.window_anchor_state == "anchored"
    assert not verified_unanchored(status, "gemini")
    assert not cli._kick_eligibility(account(), status).kickable


def test_one_sliding_read_is_not_enough():
    single = reading(NOW + 6)
    assert needs_idle_observation(single)
    assert not verified_unanchored(single, "gemini")
    assert not cli._kick_eligibility(account(), single).kickable


def test_family_projection_cannot_borrow_other_familys_idle_evidence():
    first, second = reading(NOW), reading(NOW + 6, fixed=NOW + 18000, family="claude_gpt")
    result = classify_session_clocks(first, second)
    assert verified_unanchored(antigravity_family_status(result, "gemini"), "gemini")
    assert not verified_unanchored(antigravity_family_status(result, "claude_gpt"), "claude_gpt")


@pytest.mark.parametrize("blocker", ["weekly_exhausted", "attempt", "stale", "identity_projection"])
def test_unstarted_window_does_not_bypass_existing_guards(blocker, tmp_path):
    a, s = account(), idle()
    if blocker == "weekly_exhausted":
        for window in s.quota_windows:
            if window["family"] == "gemini" and window["window_kind"] == "weekly":
                window["used_percent"] = 100
        s = antigravity_family_status(s, "gemini")
    elif blocker == "attempt":
        automation.record_attempt(tmp_path, a, s)
    elif blocker == "stale":
        s.stale = True
    else:
        a = account("claude_gpt")
    assert not cli._kick_eligibility(a, s).kickable


@pytest.mark.parametrize("tamper", ["no_proof", "wrong_time", "nan_reset", "unearned_flag"])
def test_serialized_unanchored_flag_alone_cannot_enable_kicks(tamper):
    status = idle()
    window = next(
        w for w in status.quota_windows if w["family"] == "gemini" and w["window_kind"] == "session"
    )
    if tamper == "no_proof":
        window.pop("anchor_evidence")
    elif tamper == "wrong_time":
        window["anchor_evidence"]["observed_at"] = stamp(NOW)
    elif tamper == "nan_reset":
        window["anchor_evidence"]["previous_reset"] = float("nan")
    else:
        window["anchor_evidence"]["previous_reset"] = NOW + 17900
    assert not cli._kick_eligibility(account(), status).kickable


@pytest.mark.parametrize("family", ["gemini", "claude_gpt"])
def test_unstarted_to_fixed_reset_confirms_without_sixty_second_jump(family, monkeypatch):
    before = idle(family)
    after = reading(NOW + 8, fixed=NOW + 18007, family=family, used=0.001)
    verification = reading(NOW + 14, fixed=NOW + 18007, family=family, used=0.001)
    monkeypatch.setattr(automation.time, "time", lambda: NOW + 14)
    assert automation.anchor_confirmed(
        before, after, session=True, verification=verification, family=family
    )
    assert not automation.anchor_confirmed(before, after, session=True, family=family)


@pytest.mark.parametrize(
    "scenario", ["sliding", "relative", "reused", "old_anchor", "wrong_family", "stale"]
)
def test_anchor_confirmation_rejects_false_evidence(scenario, monkeypatch):
    before = idle()
    after = reading(NOW + 80, fixed=NOW + 18007, used=0.001)
    final = reading(NOW + 86, fixed=NOW + 18007, used=0.001)
    if scenario == "sliding":
        after, final = reading(NOW + 80), reading(NOW + 86)
    elif scenario == "relative":
        final = reading(NOW + 86, fixed=NOW + 18007, evidence="relative_estimate")
    elif scenario == "reused":
        final = after
    elif scenario == "old_anchor":
        after, final = reading(NOW + 80, fixed=NOW + 17000), reading(NOW + 86, fixed=NOW + 17000)
    elif scenario == "wrong_family":
        after, final = (
            reading(NOW + 80, fixed=NOW + 18007, family="claude_gpt"),
            reading(NOW + 86, fixed=NOW + 18007, family="claude_gpt"),
        )
    else:
        final.stale = True
    monkeypatch.setattr(automation.time, "time", lambda: NOW + 86)
    assert not automation.anchor_confirmed(
        before, after, session=True, verification=final, family="gemini"
    )


def test_relative_prose_and_native_timestamps_keep_distinct_provenance():
    groups = []
    for family, title in [("gemini", "Gemini Models"), ("3p", "Claude and GPT models")]:
        groups.append(
            {
                "displayName": title,
                "buckets": [
                    {
                        "bucketId": family + "-weekly",
                        "displayName": "Weekly limit",
                        "remainingFraction": 0.8,
                        "description": "Resets in 5d",
                    },
                    {
                        "bucketId": family + "-five-hour",
                        "displayName": "Five hour limit",
                        "remainingFraction": 1,
                        "resetTime": stamp(NOW + 18000),
                    },
                ],
            }
        )
    result = antigravity_status_from_quota_summary(
        "agy", {"groups": groups}, window_source="antigravity-cli", source_detail="antigravity-cli"
    )
    assert {w["reset_evidence"] for w in result.quota_windows if w["window_kind"] == "session"} == {
        "provider_timestamp"
    }
    assert {w["reset_evidence"] for w in result.quota_windows if w["window_kind"] == "weekly"} == {
        "relative_estimate"
    }


def test_native_fetch_observes_again_without_generation_and_keeps_process_cleanup(monkeypatch):
    calls = []
    first_session, second_session = SimpleNamespace(id=1), SimpleNamespace(id=2)
    sessions = iter([first_session, second_session])
    statuses = iter([reading(NOW), reading(NOW + 6)])
    monkeypatch.setattr(sources, "antigravity_cli_binary", lambda: "/fixture/agy")
    from contextlib import nullcontext

    monkeypatch.setattr(sources, "_antigravity_cli_refresh_lock", nullcontext)
    monkeypatch.setattr(sources, "_start_antigravity_cli_session", lambda _: next(sessions))
    monkeypatch.setattr(
        sources,
        "_open_antigravity_cli_session",
        lambda *_: pytest.fail("must not reuse cached CLI"),
    )

    def fetch(_, session):
        calls.append(("read", session))
        return next(statuses)

    monkeypatch.setattr(sources, "_fetch_antigravity_cli_https_status", fetch)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: calls.append(("wait", seconds)))
    monkeypatch.setattr(
        sources, "_stop_antigravity_cli_session", lambda obj: calls.append(("stop", obj))
    )
    result = antigravity_family_status(sources._fetch_antigravity_cli_https(account()), "gemini")
    assert verified_unanchored(result, "gemini")
    assert calls == [
        ("read", first_session),
        ("stop", first_session),
        ("wait", 6),
        ("read", second_session),
        ("stop", second_session),
    ]


def test_status_and_cache_preserve_honest_unstarted_presentation(tmp_path):
    status = idle()
    assert "not started" in status_rendering._format_session_reset_for_status(status)
    assert "Session ready" in status_rendering._status_state_display(status, provider="antigravity")
    cli._save_status_cache([account()], {account_key_string(account()): status})
    restored = cli._load_status_cache_entries()[account_key_string(account())]["status"]
    assert verified_unanchored(restored, "gemini")
    assert restored.session_resets_at == status.session_resets_at


@pytest.mark.parametrize(
    "reason", ["active", "antigravity_attempt_already_sent", "antigravity_live_quota_required"]
)
def test_normal_preview_explains_actual_blocker(reason, monkeypatch, tmp_path):
    s = idle() if reason != "active" else reading(NOW + 6, fixed=NOW + 17000)
    if reason == "antigravity_attempt_already_sent":
        automation.record_attempt(tmp_path, account(), s)
    elif reason == "antigravity_live_quota_required":
        s.stale = True
    monkeypatch.setattr(cli, "_fetch_status", lambda *_: s)
    with pytest.raises(cli._SingleKickStopped) as stopped:
        cli._resolve_single_kick_target(
            account().label,
            Config(),
            [account()],
            force=False,
            dry_run=True,
            confirm=lambda *_: False,
        )
    assert stopped.value.code == reason
    assert "not fresh" not in stopped.value.message


def test_malformed_window_entries_do_not_crash_or_gain_evidence():
    first, second = reading(NOW), reading(NOW + 6)
    second.quota_windows = [None, 3, "invalid"]
    result = classify_session_clocks(first, second)
    assert not verified_unanchored(result, "gemini")


def test_native_observation_precedes_slow_identity_lookup(monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(sources, "_antigravity_request_json", lambda *_: {})
    monkeypatch.setattr(sources, "_parse_antigravity_quota_summary", lambda *_: reading(NOW))
    monkeypatch.setattr(sources, "_status_observed_at", lambda: stamp(clock[0]))

    def identity():
        clock[0] += 15
        return "fixture@example.test"

    monkeypatch.setattr(sources, "read_antigravity_cli_identity", identity)
    result = sources._fetch_antigravity_cli_quota_from_port("antigravity", 1)
    assert result.observed_at == stamp(NOW)
    assert clock[0] == NOW + 15


@pytest.mark.parametrize(
    "seconds, expected", [(-1, "not_due"), (0, None), (600, None), (601, "missed_window")]
)
def test_one_time_stagger_timing_bounds(seconds, expected):
    from tokenkick.antigravity_stagger import timing_blocker

    assert timing_blocker(NOW + 9000, NOW + seconds) == expected


@pytest.mark.parametrize(
    "blocker",
    [
        None,
        "auto",
        "pending",
        "schedule",
        "missing",
        "duplicate",
        "stale",
        "gemini_changed",
        "claude_started",
        "attempt",
    ],
)
def test_one_time_stagger_preserves_user_activity_and_controls(blocker):
    from dataclasses import replace
    from tokenkick.antigravity_stagger import prepare
    from tokenkick.models import WorkSchedule

    accounts = [
        replace(account(f), auto_kick=False, weekly_auto_kick=False, session_auto_kick=False)
        for f in ("gemini", "claude_gpt")
    ]
    config = Config(accounts=accounts)
    statuses = {
        accounts[0].label: reading(NOW + 6, fixed=NOW + 9006, used=1),
        accounts[1].label: idle("claude_gpt"),
    }
    pending = {}
    if blocker == "auto":
        accounts[1].auto_kick = True
    elif blocker == "pending":
        pending[account_key_string(accounts[1])] = object()
    elif blocker == "schedule":
        config.schedule.enabled = True
        config.schedule.accounts[accounts[1].label] = WorkSchedule(enabled=True)
    elif blocker == "missing":
        config.accounts = accounts[:1]
    elif blocker == "duplicate":
        config.accounts = accounts + [accounts[1]]
    elif blocker == "stale":
        statuses[accounts[0].label].stale = True
    elif blocker == "gemini_changed":
        statuses[accounts[0].label] = reading(NOW + 6, fixed=NOW + 9106, used=1)
    elif blocker == "claude_started":
        statuses[accounts[1].label] = reading(
            NOW + 6, fixed=NOW + 17000, family="claude_gpt", used=1
        )
    target, reason = prepare(
        config,
        NOW + 9006,
        now=NOW + 6,
        fetch=lambda a, _: statuses[a.label],
        pending=pending,
        eligibility=lambda *_: SimpleNamespace(
            kickable=blocker != "attempt", reason="attempt_guard"
        ),
    )
    assert bool(target) == (blocker is None)
    assert bool(reason) == (blocker is not None)


@pytest.mark.parametrize("execute", [False, True])
def test_one_time_stagger_never_repeats_or_forces(monkeypatch, execute):
    from tokenkick import antigravity_stagger as stagger
    from tokenkick.models import KickEvent

    a, s = account("claude_gpt"), idle("claude_gpt")
    monkeypatch.setattr(stagger.Config, "load", lambda: Config())
    monkeypatch.setattr(stagger, "prepare", lambda *_, **__: ((a, s), None))
    sends = []

    def send(*args, **kwargs):
        sends.append(kwargs)
        return KickEvent(label=a.label, success=True, confirmed=False, error="no fixed anchor")

    monkeypatch.setattr(cli, "_kick_and_notify", send)
    result = stagger.run_once(NOW + 9006, execute=execute)
    assert len(sends) == int(execute)
    if execute:
        assert sends[0]["force"] is False
        assert sends[0]["send_notification"] is False
        assert result["result"] == "unconfirmed_or_failed"
        assert result["followup_scheduled"] is False
    else:
        assert result["result"] == "would_kick"


def test_one_time_stagger_missed_deadline_never_reads_or_sends(monkeypatch):
    from tokenkick import antigravity_stagger as stagger

    monkeypatch.setattr(
        stagger.Config, "load", lambda: pytest.fail("must stop before reading config")
    )
    assert stagger.run_once(NOW, execute=True)["reason"] == "missed_window"


def test_one_time_stagger_failed_event_cannot_report_confirmation(monkeypatch):
    from tokenkick import antigravity_stagger as stagger
    from tokenkick.models import KickEvent

    monkeypatch.setattr(stagger.Config, "load", lambda: Config())
    monkeypatch.setattr(
        stagger, "prepare", lambda *_, **__: ((account("claude_gpt"), idle("claude_gpt")), None)
    )
    monkeypatch.setattr(
        cli, "_kick_and_notify", lambda *_, **__: KickEvent(label="agy", success=False)
    )
    assert stagger.run_once(NOW + 9006, execute=True)["result"] == "unconfirmed_or_failed"
