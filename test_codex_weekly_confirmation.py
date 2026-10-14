"""Weekly reset-clock proof, including the observed Skysolaranlagen burst."""

from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from tokenkick import cli, codex_weekly_confirmation as weekly, models
from tokenkick.models import AccountConfig, AccountState, AccountStatus, DataSource, KickEvent

T = 1791973842.0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


@pytest.fixture
def env(monkeypatch):
    account = AccountConfig(label="codex (skysolaranlagen)", provider="codex",
                            source=DataSource.CODEX_DIRECT, provider_home="/fixture/skysolar",
                            identity_email="fixture@example.test")
    clock = [T + 600]
    monkeypatch.setattr(weekly.time, "time", lambda: clock[0])

    def forbidden(*args, **kwargs):
        pytest.fail("Weekly reconciliation must neither send/read providers nor train session scoring")

    for name in ("kick_account", "_fetch_status", "update_codex_surface_stats",
                 "apply_codex_surface_late_confirmation"):
        monkeypatch.setattr(cli, name, forbidden)
    for name in ("_migrate_v04_direct_sources_if_needed",
                 "_migrate_provider_first_labels_if_needed",
                 "_migrate_codex_home_keys_if_needed",
                 "_repair_codex_home_identity_drift_if_needed"):
        monkeypatch.setattr(cli, name, lambda config, *args, **kwargs: config)
    return account, clock


def event(account, at=T, **changes):
    return replace(KickEvent(
        label=account.label, timestamp=at + 0.32, confirmed=False, kind="kick",
        kick_type="kick", codex_surface="repo-skip", codex_cluster_id="weekly-burst",
        codex_cluster_origin="burst", codex_attempt_started_at=at - 4,
        codex_attempt_finished_at=at + 0.32, evidence_response=True, evidence_tokens=True,
        codex_confirmation_method="pending_reset_clock", post_kick_status="pending",
        error=cli.CODEX_SESSION_ANCHOR_PENDING_ERROR,
    ), **changes)


def status(account, observed=T + 600, **changes):
    return replace(AccountStatus(
        label=account.label, state=AccountState.ACTIVE, source_detail="codex-provider-usage",
        observed_at=iso(observed), resets_at=T + weekly.WEEK_SECONDS, window_minutes=10080,
        used_percent=0, codex_rate_limit_id="codex", codex_session_limit="absent",
    ), **changes)


def refresh(account, clock, observed, **changes):
    clock[0] = observed
    return cli._apply_codex_late_attribution(
        [account], {models.account_key_string(account): status(account, observed, **changes)})


def test_legacy_skysolar_weekly_only_confirms_after_stable_read_and_reload(env):
    account, clock = env
    models.append_kick_event(event(account))
    models.append_kick_event(event(account, T + 93, codex_surface="interactive-like"))
    assert refresh(account, clock, T + 600) == 0
    persisted = models.load_kick_history()
    first = min(persisted, key=lambda e: e.timestamp)
    assert first.codex_weekly_evidence_observed_at == iso(T + 600)
    assert not first.confirmed
    assert refresh(account, clock, T + 1200) == 1
    by_surface = {e.codex_surface: e for e in models.load_kick_history()}
    winner = by_surface["repo-skip"]
    assert winner.confirmed and winner.error is None
    assert winner.codex_confirmation_method == "weekly_reset_clock"
    assert winner.codex_provider_weekly_resets_at == T + 604800
    assert winner.codex_weekly_inferred_anchor_at == T
    assert winner.codex_weekly_anchor_match_delta_seconds == 0
    assert winner.codex_provider_session_resets_at is None
    assert by_surface["interactive-like"].post_kick_status == "superseded"
    assert not by_surface["interactive-like"].confirmed
    before = models.HISTORY_FILE.read_bytes()
    assert refresh(account, clock, T + 1800) == 0
    assert models.HISTORY_FILE.read_bytes() == before
    assert "weekly confirmed" in cli._history_event_compact_details(winner)
    assert "weekly_reset_at=" in cli._history_event_details(winner)
    assert not cli._codex_pending_session_confirmation_event(winner)


@pytest.mark.parametrize("changes", [
    {"stale": True}, {"error": "rate limited"}, {"state": AccountState.UNKNOWN},
    {"source_detail": "codexbar"}, {"source_detail": "codex-session-jsonl"},
    {"window_minutes": None}, {"window_minutes": 300},
    {"codex_rate_limit_id": "other"}, {"label": "another account"},
    {"observed_at": None}, {"observed_at": "invalid"},
    {"observed_at": iso(T - 10)}, {"observed_at": iso(T + 601)},
    {"resets_at": None}, {"resets_at": float("nan")}, {"resets_at": True},
    {"resets_at": T - 1}, {"resets_at": T + 604800 + 600},
    {"resets_at": T + 18000},
])
def test_invalid_observations_never_confirm_or_seed_evidence(env, changes):
    account, clock = env
    models.append_kick_event(event(account))
    before = models.HISTORY_FILE.read_bytes()
    assert refresh(account, clock, T + 600, **changes) == 0
    assert models.HISTORY_FILE.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"success": False}, {"dispatch_state": "uncertain"}, {"dispatch_state": "not_sent"},
    {"evidence_response": False, "evidence_tokens": False},
    {"label": "another account"}, {"codex_surface": None},
    {"codex_attempt_started_at": None}, {"codex_attempt_finished_at": float("inf")},
    {"codex_attempt_started_at": T - 1000},
    {"codex_account_fingerprint": "different-identity"},
    {"codex_pre_weekly_resets_at": T + 604800},
    {"post_kick_status": "superseded"},
])
def test_ineligible_attempts_never_confirm(env, changes):
    account, clock = env
    models.append_kick_event(event(account, **changes))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 1200) == 0
    assert not models.load_kick_history()[0].confirmed


def test_same_cached_observation_and_too_short_interval_are_not_confirmation(env):
    account, clock = env
    models.append_kick_event(event(account))
    assert refresh(account, clock, T + 600) == 0
    clock[0] = T + 610
    assert weekly.reconcile(account, status(account)) == []
    assert refresh(account, clock, T + 620) == 0
    assert refresh(account, clock, T + 631) == 1


def test_moving_deadline_restarts_stability_evidence(env):
    account, clock = env
    models.append_kick_event(event(account))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 660, resets_at=T + 604820) == 0
    assert refresh(account, clock, T + 720) == 0
    assert refresh(account, clock, T + 780) == 1


def test_deadline_moving_outside_match_range_invalidates_previous_evidence(env):
    account, clock = env
    models.append_kick_event(event(account))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 660, resets_at=T + 604800 + 600) == 0
    assert models.load_kick_history()[0].codex_weekly_evidence_observed_at is None
    assert refresh(account, clock, T + 720) == 0
    assert refresh(account, clock, T + 780) == 1


def test_duplicate_saved_labels_cannot_bind_legacy_history(env):
    account, clock = env
    models.append_kick_event(event(account))
    other = replace(account, provider_home="/fixture/other")
    for observed in [T + 600, T + 1200]:
        clock[0] = observed
        assert cli._apply_codex_late_attribution(
            [account, other], {models.account_key_string(account): status(account, observed)}) == 0
    assert models.load_kick_history()[0].codex_account_fingerprint is None


def test_no_cluster_metadata_needed_for_single_weekly_request(env):
    account, clock = env
    models.append_kick_event(event(account, codex_cluster_id=None, codex_cluster_origin=None))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 1200) == 1


def test_weekly_history_survives_more_than_session_lookback_but_not_past_reset(env):
    account, clock = env
    models.append_kick_event(event(account))
    assert refresh(account, clock, T + 2 * 86400) == 0
    assert refresh(account, clock, T + 2 * 86400 + 600) == 1
    account2 = replace(account, label="expired")
    models.append_kick_event(event(account2))
    assert refresh(account2, clock, T + 604801) == 0
    assert not next(e for e in models.load_kick_history() if e.label == "expired").confirmed


def test_ambiguous_overlapping_requests_remain_unconfirmed(env):
    account, clock = env
    models.append_kick_event(event(account))
    models.append_kick_event(event(account, codex_surface="legacy", codex_cluster_id="other-burst"))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 1200) == 0
    assert all(not e.confirmed for e in models.load_kick_history())


def test_new_attempt_binds_identity_and_preexisting_weekly_window(env):
    account, clock = env
    attempt = event(account)
    weekly.prepare_attempt(account, attempt, status(account))
    assert attempt.codex_account_fingerprint == weekly.fingerprint(account)
    assert attempt.codex_pre_weekly_resets_at == T + 604800
    models.append_kick_event(attempt)
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 1200) == 0


def test_identity_change_after_first_read_does_not_confirm(env):
    account, clock = env
    models.append_kick_event(event(account))
    assert refresh(account, clock, T + 600) == 0
    changed = replace(account, identity_email="replacement@example.test")
    assert refresh(changed, clock, T + 1200) == 0
    assert not models.load_kick_history()[0].confirmed


def test_weekly_only_deadline_does_not_confirm_session_event(env):
    account, clock = env
    models.append_kick_event(event(account, kind="session", kick_type="session"))
    assert refresh(account, clock, T + 600) == 0
    assert refresh(account, clock, T + 1200) == 0
    assert not models.load_kick_history()[0].confirmed


def test_session_deadline_does_not_confirm_weekly_event(env):
    account, clock = env
    models.append_kick_event(event(account))
    for observed in [T + 600, T + 1200]:
        assert refresh(account, clock, observed, resets_at=T + 604800 - 3600,
                       session_resets_at=T + 18000, session_window_minutes=300,
                       codex_session_limit="present") == 0
    assert not models.load_kick_history()[0].confirmed


def test_new_fields_roundtrip_and_legacy_history_loads(env):
    account, _ = env
    legacy = {"label": account.label, "timestamp": T, "success": True, "confirmed": False}
    assert KickEvent.from_dict(legacy).codex_weekly_evidence_observed_at is None
    prepared = event(account)
    weekly.prepare_attempt(account, prepared, status(account))
    assert KickEvent.from_dict(json.loads(json.dumps(prepared.to_dict()))) == prepared


def test_actual_weekly_attempt_records_first_observation_then_refresh_confirms(env, monkeypatch):
    account, clock = env
    clock[0] = T - 4
    before = status(account, T - 5, resets_at=T - 1)

    def generate(*args, **kwargs):
        clock[0] = T + 0.32
        return KickEvent(label=account.label, timestamp=T + 0.32, success=True,
                         confirmed=False, evidence_tokens=True)

    def read(*args, **kwargs):
        clock[0] = T + 5
        return status(account, T + 5)

    monkeypatch.setattr(cli, "kick_account", generate)
    monkeypatch.setattr(cli, "_fetch_codex_provider_movement_status", read)
    attempt = cli._run_kick_attempt(account, models.Config(accounts=[account]),
        phantom_recovery=False, daemon_log=False, kick_type="kick", model_override=None,
        pre_status=before, log_result=False)
    assert not attempt.confirmed
    assert attempt.codex_weekly_evidence_observed_at == iso(T + 5)
    assert attempt.codex_account_fingerprint == weekly.fingerprint(account)
    assert refresh(account, clock, T + 600) == 1


def test_live_status_refresh_reconciles_weekly_history(env, monkeypatch):
    from click.testing import CliRunner

    account, clock = env
    models.Config(accounts=[account]).save()
    models.append_kick_event(event(account))
    monkeypatch.setattr(cli, "_refresh_status_cache_fast", lambda config:
                        ([account], [status(account, clock[0])], False, "fixture", [], False))
    monkeypatch.setattr(cli, "_load_status_cache", lambda config: None)
    for observed in [T + 600, T + 1200]:
        clock[0] = observed
        result = CliRunner().invoke(cli.cli, ["status", "--refresh", "--json-output"])
        assert result.exit_code == 0, result.output
    assert models.load_kick_history()[0].confirmed


def test_cached_status_never_seeds_weekly_confirmation(env, monkeypatch):
    from click.testing import CliRunner

    account, clock = env
    models.Config(accounts=[account]).save()
    models.append_kick_event(event(account))
    monkeypatch.setattr(cli, "_load_status_cache", lambda config: ([account], [status(account)], {}))
    monkeypatch.setattr(cli, "_start_background_status_refresh", lambda *args, **kwargs: False)
    result = CliRunner().invoke(cli.cli, ["status", "--json-output"])
    assert result.exit_code == 0, result.output
    assert models.load_kick_history()[0].codex_weekly_evidence_observed_at is None
