"""Evidence-based request retirement and consistent duplicate protection."""
import json
import multiprocessing
import time
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from tokenkick import action_state, claude_action_recovery as recovery, cli, status_rendering
from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, DataSource, KickEvent, account_key_string
from tokenkick.state_io import state_file_lock


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'CONFIG_DIR', tmp_path)
    now = float(int(time.time()))
    a = AccountConfig(label='guard-test', provider='claude', source=DataSource.CLAUDE_DIRECT,
                      auto_kick=True, identity_email='fixture@example.test')
    dispatched = now - 3 * 86400
    record = {'state': 'uncertain', 'started_at': dispatched - 1, 'dispatched_at': dispatched}
    path = action_state.action_path(tmp_path, account_key_string(a))
    path.parent.mkdir()
    path.write_text(json.dumps(record))
    e = KickEvent(label=a.label, timestamp=dispatched + 5, kind='session', kick_type='session',
                  success=True, confirmed=False, dispatch_state='sent', evidence_tokens=True)
    s = AccountStatus(label=a.label, state=AccountState.FRESH, used_percent=0,
                      resets_at=now + 6 * 86400, resets_in_seconds=6 * 86400,
                      window_minutes=10080, source_detail='claude-cli-usage', observed_at=iso(now),
                      session_used_percent=0, session_resets_at=None)
    return a, s, [e], now, path


def test_guard_blocks_eligibility_terminal_json_and_dispatch(case, tmp_path, monkeypatch):
    a, s, h, now, path = case
    monkeypatch.setattr(cli, 'load_kick_history', lambda **kw: h)
    monkeypatch.setattr(cli, 'load_pending_kicks', lambda *args: {})
    send = Mock(side_effect=AssertionError('duplicate generation'))
    monkeypatch.setattr(cli, '_kick_and_notify_owned', send)
    assert cli._kick_eligibility(a, s, history=h).reason == 'request_outcome_uncertain'
    assert status_rendering._status_action(s, {a.label: 'claude'}, a) == 'Accepted request; checking later window'
    row = status_rendering._status_rows_as_dict([s], [a], Config())[0]
    assert not row['kickable'] and row['kick_blocked_reason'] == 'request_outcome_uncertain'
    assert 'blocked' in status_rendering._status_state_display(s, request_blocked=True)
    log = Mock()
    monkeypatch.setattr(cli, '_daemon_log', log)
    event = cli._kick_and_notify(a, Config(), s, automatic=True, daemon_log=True)
    assert event.dispatch_state == 'not_sent'
    assert log.call_args.kwargs['reason'] == 'request_guard_blocked'
    send.assert_not_called()


def test_recovery_requires_distinct_reads_survives_restart_and_does_not_send(case, tmp_path):
    a, s, history, now, path = case
    original = path.read_text()
    assert recovery.reconcile(tmp_path, a, s, history, now=now) is None
    assert path.read_text() != original
    assert recovery.reconcile(tmp_path, a, s, history, now=now + 60) is None  # same cached read
    later = replace(s, observed_at=iso(now + 60))
    audit = recovery.reconcile(tmp_path, a, later, history, now=now + 60, cooldown_seconds=600)
    assert audit['reason'] == 'later_native_window'
    assert audit['previous_state'] == 'uncertain'
    assert not history[0].confirmed  # retiring the guard does not invent confirmation
    record = json.loads(path.read_text())
    assert record['dispatched_at'] == history[0].timestamp - 5
    assert action_state.action_blocker(tmp_path, account_key_string(a), now=now + 60) == 'request_recovery_cooldown'
    assert action_state.action_blocker(tmp_path, account_key_string(a), now=now + 661) is None
    with pytest.raises(action_state.UncertainActionError), action_state.account_action(tmp_path, account_key_string(a)):
        pytest.fail('same-pass dispatch')
    assert recovery.reconcile(tmp_path, a, later, history, now=now + 70) is None
    assert json.loads(path.read_text()) == record


@pytest.mark.parametrize('change', [
    {'stale': True}, {'error': 'rate limited'}, {'source_detail': 'codexbar'},
    {'observed_at': None}, {'observed_at': 'bad'}, {'window_minutes': 300},
    {'resets_at': float('nan')}, {'resets_at': float('inf')}, {'label': 'different'},
    {'state': AccountState.UNKNOWN},
])
def test_bad_evidence_cannot_retire_guard(case, tmp_path, change):
    a, s, h, now, path = case
    for offset in [0, 120]:
        assert recovery.reconcile(tmp_path, a, replace(s, **change), h, now=now + offset) is None
    assert json.loads(path.read_text())['state'] == 'uncertain'


@pytest.mark.parametrize('offset', [-901, 1])
def test_old_or_future_observations_block_recovery(case, tmp_path, offset):
    a, s, h, now, path = case
    assert recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now + offset)), h, now=now) is None
    assert 'recovery_candidate' not in json.loads(path.read_text())


@pytest.mark.parametrize('variant', ['missing', 'wrong_account', 'unaccepted', 'duplicate', 'wrong_time', 'no_receipt'])
def test_recovery_requires_matching_accepted_request(case, tmp_path, variant):
    a, s, h, now, path = case
    event = h[0]
    history = {'missing': [], 'wrong_account': [replace(event, label='other')],
               'unaccepted': [replace(event, success=False)], 'duplicate': h + h,
               'wrong_time': [replace(event, timestamp=now)],
               'no_receipt': [replace(event, evidence_tokens=False)]}[variant]
    assert recovery.reconcile(tmp_path, a, s, history, now=now) is None
    assert 'recovery_candidate' not in json.loads(path.read_text())


def test_same_window_elapsed_time_and_moving_deadlines_are_not_recovery(case, tmp_path):
    a, s, h, now, path = case
    # A seven-day window that started before the request remains protected.
    same = replace(s, resets_at=h[0].timestamp - 3600 + 604800)
    assert recovery.reconcile(tmp_path, a, same, h, now=now) is None
    recovery.reconcile(tmp_path, a, s, h, now=now)
    moving = replace(s, observed_at=iso(now + 120), resets_at=s.resets_at + 120)
    assert recovery.reconcile(tmp_path, a, moving, h, now=now + 120) is None
    assert json.loads(path.read_text())['recovery_candidate']['first_observed_at'] == now + 120


def test_session_cannot_retire_weekly_request(case, tmp_path):
    a, s, h, now, path = case
    s = replace(s, resets_at=None, session_resets_at=now + 3600, session_window_minutes=300)
    weekly = [replace(h[0], kind='kick', kick_type='kick')]
    assert recovery.reconcile(tmp_path, a, s, weekly, now=now) is None
    assert 'recovery_candidate' not in json.loads(path.read_text())
    recovery.reconcile(tmp_path, a, s, h, now=now)
    audit = recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now + 60)), h, now=now + 60)
    assert audit['window'] == 'session'


def _hold_guard(path, ready, release):
    with state_file_lock(path):
        ready.set()
        release.wait(10)


def test_live_owner_prevents_reconciliation(case, tmp_path):
    a, s, h, now, path = case
    before = path.read_bytes()
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    owner = ctx.Process(target=_hold_guard, args=(path, ready, release))
    owner.start()
    try:
        assert ready.wait(10)
        assert recovery.reconcile(tmp_path, a, s, h, now=now) is None
    finally:
        release.set()
        owner.join(10)
        if owner.is_alive():
            owner.terminate()
            owner.join(5)
    assert path.read_bytes() == before


def test_accepted_claude_request_retains_distinct_anchor_guard(case, tmp_path, monkeypatch):
    a, s, h, now, path = case
    monkeypatch.setattr(cli, 'kick_account', lambda *args, **kw: replace(h[0]))
    monkeypatch.setattr(cli, '_run_claude_usage_touch', Mock(side_effect=RuntimeError('monitor offline')))
    with action_state.account_action(tmp_path, account_key_string(a), recover=True):
        action_state.mark_action_dispatched()
        event = cli._kick_claude_usage_session(a, Config(), pre_status=s, kick_type='session')
    assert event.success and not event.confirmed
    assert json.loads(path.read_text())['state'] == 'awaiting_anchor'
    assert cli._kick_eligibility(a, s).reason == 'request_awaiting_anchor'


@pytest.mark.parametrize('raw', ['{broken', 'null', '[]', '{}', '{"state":[]}', '{"state":"unrecognized"}'])
def test_corrupt_guard_is_visible_and_not_auto_recovered(case, tmp_path, raw):
    a, s, h, now, path = case
    path.write_text(raw)
    assert cli._kick_eligibility(a, s).reason == 'request_outcome_uncertain'
    assert recovery.reconcile(tmp_path, a, s, h, now=now) is None
    assert path.read_text() == raw


def test_durable_receipt_recovers_after_history_pruning(case, tmp_path):
    a, s, h, now, path = case
    record = json.loads(path.read_text())
    record.update(state='awaiting_anchor', provider='claude', request_kind='session',
                  accepted_at=h[0].timestamp, accepted_evidence=True)
    path.write_text(json.dumps(record))
    recovery.reconcile(tmp_path, a, s, [], now=now)
    assert recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(now + 60)), [], now=now + 60)


@pytest.mark.parametrize('changes', [
    {'provider': 'codex'}, {'accepted_at': float('nan')}, {'request_kind': 'probe'},
    {'accepted_evidence': False}, {'accepted_at': 0}, {'request_kind': []},
])
def test_invalid_durable_receipt_stays_blocked(case, tmp_path, changes):
    a, s, h, now, path = case
    record = json.loads(path.read_text())
    record.update(state='awaiting_anchor', provider='claude', request_kind='session',
                  accepted_at=h[0].timestamp, accepted_evidence=True)
    record.update(changes)
    path.write_text(json.dumps(record))
    assert recovery.reconcile(tmp_path, a, s, h, now=now) is None
    assert 'recovery_candidate' not in json.loads(path.read_text())


def test_preview_reports_guard_and_no_action(case, tmp_path):
    a, s, h, now, path = case
    outcome, result, dispatched = cli._run_evaluate_account(
        a, s, Config(), dry_run=True, history=h, pending={}, now=datetime.now(timezone.utc))
    assert outcome == 'skipped' and not dispatched
    assert result['reason_code'] == 'request_outcome_uncertain'


def test_evidence_gap_restarts_pair_and_account_identity_is_required(case, tmp_path):
    a, s, h, now, path = case
    assert recovery.reconcile(tmp_path, replace(a, identity_email=None), s, h, now=now) is None
    recovery.reconcile(tmp_path, a, s, h, now=now)
    later = now + 2000
    assert recovery.reconcile(tmp_path, a, replace(s, observed_at=iso(later)), h, now=later) is None
    assert json.loads(path.read_text())['recovery_candidate']['first_observed_at'] == later


def test_recovery_never_reenters_its_own_active_request(case, tmp_path):
    a, s, h, now, path = case
    with action_state.account_action(tmp_path, account_key_string(a), recover=True):
        action_state.mark_action_dispatched()
        action_state.mark_action_uncertain()
        before = path.read_bytes()
        assert recovery.reconcile(tmp_path, a, s, h, now=now) is None
        assert path.read_bytes() == before


@pytest.mark.parametrize("existing_week", [False, True])
def test_daemon_reconciles_before_dispatch_and_next_poll_cannot_duplicate(case, tmp_path, monkeypatch, existing_week):
    a, s, h, now, path = case
    a = replace(a, session_auto_kick=True)
    if existing_week:
        s = replace(s, used_percent=2, resets_at=now + 3 * 86400, resets_in_seconds=3 * 86400)
        h[0] = replace(h[0], kind='kick', kick_type='kick')
    separation = 120 if existing_week else 60
    config = Config(accounts=[a], poll_interval_minutes=10)
    monkeypatch.setattr(cli.Config, 'load', lambda: config)
    monkeypatch.setattr(cli, '_migrate_codex_home_keys_if_needed', lambda c, **kw: c)
    monkeypatch.setattr(cli, '_repair_codex_home_identity_drift_if_needed', lambda c, **kw: c)
    monkeypatch.setattr(cli, '_load_accounts', lambda c: c.accounts)
    monkeypatch.setattr(cli, '_supervise_telegram_remote', lambda c: None)
    monkeypatch.setattr(cli.stagger, 'daemon_step', lambda *args, **kw: {})
    monkeypatch.setattr(cli, 'load_kick_history', lambda **kw: h)
    monkeypatch.setattr(cli, 'load_pending_kicks', lambda *args: {})
    log = Mock()
    monkeypatch.setattr(cli, '_daemon_log', log)
    # Stop after the real recovery phase to isolate unrelated poll side effects.
    monkeypatch.setattr(status_rendering, 'notify_dormant_accounts', Mock(side_effect=RuntimeError('after recovery')))
    for tick in [now, now + separation]:
        monkeypatch.setattr(time, 'time', lambda: tick)
        current = replace(s, observed_at=iso(tick))
        monkeypatch.setattr(cli, '_refresh_status_cache', lambda *args, **kw: ([a], [current]))
        with pytest.raises(RuntimeError, match='after recovery'):
            cli._daemon_poll_once({}, False)
    assert any(c.args[0] == 'claude_request_guard_recovered' for c in log.call_args_list)
    send = Mock()
    monkeypatch.setattr(cli, '_kick_and_notify_owned', send)
    result = cli._kick_and_notify(a, config, current, automatic=True, kick_type='kick')
    assert result.dispatch_state == 'not_sent'
    send.assert_not_called()
    monkeypatch.setattr(time, 'time', lambda: now + separation + 601)
    def dispatch(*args, **kwargs):
        action_state.mark_action_dispatched()
        event = KickEvent(label=a.label, timestamp=now + separation + 601, kind='session', kick_type='session')
        h.append(event)
        return event
    send.side_effect = dispatch
    # The active week is no longer a reason to send; a future due session is.
    assert cli._kick_and_notify(a, config, current, automatic=True, kick_type='kick').dispatch_state == 'not_sent'
    send.assert_not_called()
    current = replace(current, state=AccountState.ACTIVE, observed_at=iso(now + separation + 601),
                      session_resets_at=now + separation + 600, session_resets_in_seconds=-1, session_window_minutes=300)
    assert cli._kick_and_notify(a, config, current, automatic=True, kick_type='session').confirmed
    assert cli._kick_and_notify(a, config, current, automatic=True, kick_type='session').dispatch_state == 'not_sent'
    assert send.call_count == 1


def test_interrupted_post_request_probe_preserves_receipt_without_history(case, tmp_path, monkeypatch):
    a, s, h, now, path = case
    monkeypatch.setattr(time, 'time', lambda: now)
    event = KickEvent(label=a.label, timestamp=now, response_text='OK', kind='session', kick_type='session')
    monkeypatch.setattr(cli, 'kick_account', lambda *args, **kw: event)
    monkeypatch.setattr(cli, '_run_claude_usage_touch', Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt), action_state.account_action(tmp_path, account_key_string(a), recover=True):
        action_state.mark_action_dispatched()
        cli._kick_claude_usage_session(a, Config(), pre_status=s, kick_type='session')
    record = json.loads(path.read_text())
    assert record['state'] == 'awaiting_anchor'
    assert record['accepted_evidence'] is True
    assert record['accepted_at'] == now
    assert recovery._receipt(record, a, []) == ('session', now)


def test_confirmed_result_finishes_lease_only_after_history_scope(case, tmp_path):
    a, s, h, now, path = case
    with action_state.account_action(tmp_path, account_key_string(a), recover=True):
        action_state.mark_action_dispatched()
        action_state.mark_action_awaiting_anchor(h[0])
        action_state.mark_action_verified()
        assert json.loads(path.read_text())['state'] == 'in_flight'
    assert json.loads(path.read_text())['state'] == 'completed'
