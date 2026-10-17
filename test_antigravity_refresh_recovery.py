"""Native Argon schema, cache diagnostics and receipt-only stagger recovery."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tokenkick import antigravity as agy, cli, sources, stagger_recovery, status_cache
from tokenkick.action_state import action_path
from tokenkick.antigravity_automation import _attempt_path, usable_status
from tokenkick.models import AccountConfig, AccountState, Config, DataSource, KickEvent, account_key_string

T = 1800000000


@pytest.fixture
def quota(monkeypatch):
    monkeypatch.setattr(agy.time, "time", lambda: T)
    return json.loads((Path(__file__).parent / "test_fixtures/antigravity_quota_argon.json").read_text())


def account(family="gemini", **kwargs):
    return AccountConfig(label=family, provider="antigravity", antigravity_family=family,
                         source=DataSource.ANTIGRAVITY_CLI, identity_email="fixture@example.test", **kwargs)


def parse(quota, family="gemini"):
    status = sources._parse_antigravity_quota_summary(family, quota)
    status.observed_at = datetime.fromtimestamp(T, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return agy.antigravity_family_status(status, family)


@pytest.mark.parametrize("family", ["gemini", "claude_gpt"])
def test_live_argon_schema_keeps_required_family_windows_separate(quota, family):
    quota['response']['groups'][-1]['buckets'][0]['remainingFraction'] = 0
    status = parse(quota, family)
    assert len(status.quota_windows) == 5
    assert agy.has_complete_antigravity_quota_windows(status)
    assert usable_status(account(family), status)
    assert status.used_percent < 100  # Exhausted Argon does not exhaust either family.
    assert status.session_window_minutes == 300
    extra = next(w for w in status.quota_windows if w['family'] == 'argon')
    assert extra['window_kind'] == 'weekly' and extra['used_percent'] == 100
    assert extra['reset_evidence'] == 'provider_timestamp'
    assert not any(w['family'] == 'argon' and w['window_kind'] == 'session' for w in status.quota_windows)


@pytest.mark.parametrize("change", ["missing_core", "duplicate_core", "duplicate_argon", "unknown_bucket",
                                    "wrong_window", "bad_fraction", "nan", "bad_reset", "unknown_argon",
                                    "boolean_fraction", "infinite_reset", "boolean_reset", "bad_nanos"])
def test_extra_quota_does_not_relax_required_window_validation(quota, change):
    groups = quota['response']['groups']
    argon = groups[-1]['buckets'][0]
    if change == 'missing_core':
        groups[0]['buckets'].pop()
    elif change == 'duplicate_core':
        groups[0]['buckets'].append(deepcopy(groups[0]['buckets'][0]))
    elif change == 'duplicate_argon':
        groups.append(deepcopy(groups[-1]))
    elif change == 'unknown_bucket':
        groups.append({'displayName': 'Unknown', 'buckets': [{'bucketId': 'unknown'}]})
    elif change == 'wrong_window':
        argon['window'] = '5h'
    elif change == 'bad_fraction':
        argon['remainingFraction'] = 2
    elif change == 'nan':
        argon['remainingFraction'] = float('nan')
    elif change == 'bad_reset':
        argon['resetTime'] = 'invalid'
    elif change == 'boolean_fraction':
        argon['remainingFraction'] = True
    elif change == 'infinite_reset':
        argon['resetTime'] = float('inf')
    elif change == 'boolean_reset':
        argon['resetTime'] = True
    elif change == 'bad_nanos':
        argon['resetTime'] = {'seconds': T+1000, 'nanos': float('inf')}
    else:
        argon['bucketId'] = 'gemini-argon-unknown-weekly'
    with pytest.raises(sources._AntigravityQuotaSummaryParseError):
        parse(quota)


def test_four_bucket_accounts_still_parse_and_optional_export_round_trips(quota):
    status = parse(quota)
    extra = [{'id': w['id'], 'window': {'usedPercent': w['used_percent'],
             'windowMinutes': w['window_minutes'], 'resetsAt': w['resets_at']}}
             for w in status.quota_windows]
    copied = agy.antigravity_status_from_extra_rate_windows(
        'gemini', {'extraRateWindows': extra}, window_source='antigravity-cli', source_detail='antigravity-cli')
    assert len(copied.quota_windows) == 5
    assert agy.antigravity_family_status(copied, 'gemini').used_percent == status.used_percent
    quota['response']['groups'].pop()
    assert len(parse(quota).quota_windows) == 4


def test_argon_separate_quota_override_cannot_send_gemini_kick(quota, monkeypatch):
    a = account(kick_model='Gemini 4 Argon (Low)')
    monkeypatch.setattr(cli, 'kick_account', lambda *_a, **_k: pytest.fail('must not generate'))
    event = cli._run_kick_attempt(a, Config(), phantom_recovery=False, daemon_log=False,
                                  kick_type='session', model_override=None, pre_status=parse(quota))
    assert event.dispatch_state == 'not_sent'
    assert 'separate weekly quota' in event.error
    assert cli._kick_eligibility(a, parse(quota)).reason == 'antigravity_separate_model_quota'


def test_cache_preserves_safe_schema_diagnostic_and_old_age_until_success(quota, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, '_status_cache_now', lambda: datetime.fromtimestamp(T, timezone.utc))
    monkeypatch.setattr(status_cache, '_status_cache_now', lambda: datetime.fromtimestamp(T, timezone.utc))
    monkeypatch.setattr(cli, 'STATUS_CACHE_FILE', tmp_path / 'status-cache.json')
    a, good = account(), parse(quota)
    key = account_key_string(a)
    status_cache._save_status_cache([a], {key: good})
    failed = replace(good, state=AccountState.UNKNOWN, error=agy.ANTIGRAVITY_QUOTA_SUMMARY_PARSE_ERROR,
                     source_diagnostics={'error': 'Bearer private-token'})
    status_cache._save_status_cache([a], {key: failed})
    entry = status_cache._load_status_cache_entries()[key]
    assert entry['status'].observed_at == good.observed_at
    assert entry['refresh_diagnostic'] == 'quota_schema'
    assert 'parser rejected' in status_cache._status_cache_stale_reason(entry)
    effective = status_cache.effective_cached_status(entry['status'], entry, Config())
    assert effective.stale and 'parser rejected' in effective.error
    assert 'private-token' not in cli.STATUS_CACHE_FILE.read_text()
    status_cache._save_status_cache([a], {key: good})
    entry = status_cache._load_status_cache_entries()[key]
    assert entry['refresh_error'] is None and entry['refresh_diagnostic'] is None
    assert status_cache.effective_cached_status(entry['status'], entry, Config()).error is None


def test_first_native_refresh_failure_never_persists_unfiltered_provider_text(quota, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'STATUS_CACHE_FILE', tmp_path / 'status-cache.json')
    a = account()
    failed = replace(parse(quota), state=AccountState.UNKNOWN, error='endpoint error: private-token',
                     source_diagnostics={'error': 'Bearer private-token'})
    status_cache._save_status_cache([a], {account_key_string(a): failed})
    saved = cli.STATUS_CACHE_FILE.read_text()
    assert 'private-token' not in saved
    entry = status_cache._load_status_cache_entries()[account_key_string(a)]
    assert entry['refresh_diagnostic'] == 'transport'
    assert 'endpoint is unavailable' in entry['status'].error


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    a = account()
    attempt = {'family': 'gemini', 'at': T, 'outcome': 'unconfirmed'}
    action = {'state': 'completed', 'started_at': T+0.1, 'dispatched_at': T+2, 'completed_at': T+45}
    guard = {'attempted_at': T+1, 'retry_after': T+18002, 'session_reset': T+17990}
    event = KickEvent(label=a.label, timestamp=T+9, kind='session', kick_type='session',
                      success=True, confirmed=True, dispatch_state='sent', evidence_response=True,
                      evidence_provider_moved=True, post_kick_status='confirmed')
    history = [event]
    monkeypatch.setattr(stagger_recovery, 'load_kick_history', lambda **_k: history)
    def write():
        for path, data in [(action_path(tmp_path, account_key_string(a)), action), (_attempt_path(tmp_path, a), guard)]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
    write()
    return a, attempt, action, guard, history, write


def test_legacy_confirmed_receipt_reconciles_without_changing_evidence(receipt, tmp_path):
    a, attempt, *_ = receipt
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    proof = stagger_recovery.confirmed_receipt(tmp_path, a, attempt, T+600)
    assert proof['confirmed_event_at'] == T+9
    assert proof['retry_after'] == T+18002
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*.json')}


@pytest.mark.parametrize("change", ["unconfirmed", "failed", "wrong_account", "old_event", "duplicate", "missing",
                                    "no_provider_evidence", "probe", "in_flight", "other_dispatch", "no_cooldown",
                                    "nan", "too_short", "too_long", "future", "active_context", "locked"])
def test_uncertain_or_mismatched_receipts_never_recover(receipt, tmp_path, monkeypatch, change):
    a, attempt, action, guard, history, write = receipt
    event = history[0]
    if change == 'unconfirmed':
        event.confirmed = False
    elif change == 'failed':
        event.success = False
    elif change == 'wrong_account':
        event.label = 'claude_gpt'
    elif change == 'old_event':
        event.timestamp = T-10
    elif change == 'duplicate':
        history.append(deepcopy(event))
    elif change == 'missing':
        history.clear()
    elif change == 'no_provider_evidence':
        event.evidence_provider_moved = False
    elif change == 'probe':
        event.kind = 'probe'
    elif change == 'in_flight':
        action['state'] = 'in_flight'
    elif change == 'other_dispatch':
        action['started_at'] = T+500
    elif change == 'no_cooldown':
        guard['retry_after'] = None
    elif change == 'nan':
        guard['attempted_at'] = float('nan')
    elif change == 'too_short':
        guard['retry_after'] = T+100
    elif change == 'too_long':
        guard['retry_after'] = T+604800
    elif change == 'future':
        action['completed_at'] = T+601
    elif change == 'active_context':
        monkeypatch.setattr(stagger_recovery, 'action_in_progress', lambda: True)
    elif change == 'locked':
        def busy(*_a, **_k):
            raise stagger_recovery.StateFileLockTimeout('busy')
        monkeypatch.setattr(stagger_recovery, 'state_file_lock', busy)
    write()
    assert stagger_recovery.confirmed_receipt(tmp_path, a, attempt, T+600) is None
