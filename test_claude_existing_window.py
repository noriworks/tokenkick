"""Regression for the accepted 12:20 weekly request against an existing week."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from tokenkick import cli, claude_action_recovery as recovery, action_state, status_rendering
from tokenkick.models import AccountConfig, AccountStatus, AccountState, Config, DataSource, KickEvent, account_key_string


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


@pytest.fixture
def case(tmp_path, monkeypatch):
    now = 1792236000.0
    monkeypatch.setattr(recovery.time, 'time', lambda: now)
    monkeypatch.setattr(cli, 'CONFIG_DIR', tmp_path)
    a = AccountConfig(label='claude', provider='claude', source=DataSource.CLAUDE_DIRECT,
                      identity_email='fixture@example.test', auto_kick=True, session_auto_kick=True)
    dispatched = 1792232419.443589
    record = dict(state='uncertain', started_at=dispatched-0.1, dispatched_at=dispatched)
    path = action_state.action_path(tmp_path, account_key_string(a))
    path.parent.mkdir()
    path.write_text(json.dumps(record))
    s = AccountStatus(label=a.label, state=AccountState.ACTIVE, used_percent=2,
                      window_minutes=10080, resets_at=1792756739.0, resets_in_seconds=520739,
                      session_window_minutes=300, session_used_percent=12,
                      session_resets_at=1792250339.0, session_resets_in_seconds=14339,
                      source_detail='claude-cli-usage', observed_at=iso(now))
    h = [KickEvent(label=a.label, timestamp=dispatched+3, kind='kick', kick_type='kick',
                   success=True, confirmed=False, dispatch_state='sent', evidence_tokens=True)]
    return a, s, h, now, path


def test_existing_week_retires_receipt_without_confirming_or_resending(case, tmp_path, monkeypatch):
    a, s, h, now, path = case
    original_history = [e.to_dict() for e in h]
    send = Mock(side_effect=AssertionError('generation during recovery'))
    monkeypatch.setattr(cli, 'kick_account', send)
    assert recovery.reconcile(tmp_path, a, s, h, now=now, cooldown_seconds=600) is None
    # Re-read persisted state, then accept native minute-level rounding after 2 minutes.
    audit = recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now+120), resets_at=s.resets_at+60),
                               h, now=now+120, cooldown_seconds=600)
    assert audit['reason'] == 'existing_weekly_window'
    assert audit['reset_at'] == s.resets_at
    assert json.loads(path.read_text())['state'] == 'completed'
    assert [e.to_dict() for e in h] == original_history
    assert not h[0].confirmed
    assert action_state.action_blocker(tmp_path, account_key_string(a), now=now+120) == 'request_recovery_cooldown'
    assert cli._kick_claude_usage_session(a, Config(), pre_status=s, kick_type='kick').dispatch_state == 'not_sent'
    send.assert_not_called()


@pytest.mark.parametrize('change', [dict(used_percent=0), dict(used_percent=float('nan')),
    dict(used_percent=True), dict(used_percent=101), dict(stale=True), dict(error='offline'),
    dict(source_detail='codexbar'), dict(window_minutes=300), dict(resets_at=float('inf')),
    dict(state=AccountState.UNKNOWN), dict(label='other')])
def test_invalid_existing_window_cannot_retire_receipt(case, tmp_path, change):
    a, s, h, now, path = case
    for offset in (0, 600):
        assert recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now+offset), **change),
                                  h, now=now+offset) is None
    assert json.loads(path.read_text())['state'] == 'uncertain'


@pytest.mark.parametrize('variant', ['missing', 'ambiguous', 'contradictory', 'future', 'wrong_identity'])
def test_existing_window_receipt_and_identity_are_mandatory(case, tmp_path, variant):
    a, s, h, now, path = case
    if variant == 'missing':
        h = []
    elif variant == 'ambiguous':
        h *= 2
    elif variant == 'contradictory':
        h.append(replace(h[0], success=False))
    elif variant == 'future':
        h = [replace(h[0], timestamp=now+600)]
    else:
        a = replace(a, identity_email=None)
    for offset in (0, 600):
        assert recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now+offset)), h, now=now+offset) is None
    assert json.loads(path.read_text())['state'] == 'uncertain'


def test_rounding_tolerance_never_walks_or_accepts_cached_reads(case, tmp_path):
    a, s, h, now, path = case
    recovery.reconcile(tmp_path, a, s, h, now=now)
    assert recovery.reconcile(tmp_path, a, s, h, now=now+600) is None
    for offset in (60, 120, 180, 240):
        assert recovery.reconcile(tmp_path, a, replace(s, resets_at=s.resets_at+offset,
                                  observed_at=iso(now+offset)), h, now=now+offset) is None
    assert json.loads(path.read_text())['state'] == 'uncertain'


def test_active_week_with_rounded_zero_suppresses_weekly_but_allows_due_session(case, monkeypatch):
    a, s, h, now, path = case
    path.unlink()
    s = replace(s, state=AccountState.FRESH, used_percent=0)
    assert not cli._long_kick_eligible(s)
    assert not cli._kick_eligibility(a, s, history=[]).kickable
    assert 'Weekly ready' not in status_rendering._status_state_display(s, provider='claude')
    due = replace(s, session_resets_at=now-1, session_resets_in_seconds=-1, session_used_percent=0)
    eligible = cli._kick_eligibility(a, due, history=[])
    assert eligible.kickable and eligible.kick_type == 'session'
    # No inference for fallback, stale, or a not-yet-started week.
    for other in (replace(s, source_detail='codexbar'), replace(s, stale=True),
                  replace(s, resets_at=now+604800)):
        assert not recovery.weekly_window_active(other)


def test_legacy_accepted_guard_explains_requirement_in_terminal_and_json(case, monkeypatch):
    a, s, h, now, path = case
    monkeypatch.setattr(cli, 'load_kick_history', lambda **kw: h)
    monkeypatch.setattr(cli, 'load_pending_kicks', lambda *args: {})
    assert status_rendering._status_action(s, {a.label:'claude'}, a) == 'Accepted request; checking weekly evidence'
    row = status_rendering._status_rows_as_dict([s], [a], Config())[0]
    assert row['request_recovery']['accepted']
    assert 'Two fresh' in row['request_recovery']['requirement']
    assert not row['kickable']


def test_normalization_preserves_monitoring_diagnostics(case):
    _, s, _, _, _ = case
    s = replace(s, state=AccountState.FRESH, used_percent=0)
    s._claude_probe_context = {"category": "rate_limit"}
    normalized = recovery.normalize_status(s)
    assert normalized.state == AccountState.ACTIVE
    assert s.state == AccountState.FRESH
    assert normalized._claude_probe_context == s._claude_probe_context
