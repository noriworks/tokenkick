"""Cross-component regressions for the repository reliability audit."""

import importlib.util
import multiprocessing
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

import tokenkick.cli as cli
import tokenkick.models as models
import tokenkick.sources as sources
import tokenkick.status_cache as cache
import tokenkick.status_rendering as rendering
from tokenkick.action_state import account_action, UncertainActionError
from tokenkick.models import AccountConfig, AccountStatus, AccountState, Config, DataSource
from tokenkick.scheduling import PendingKick, resolve_today_work_window, to_utc_iso
from tokenkick.state_io import StateFileLockTimeout
from tokenkick.mcp_setup import _remove_toml_tokenkick_block, tomllib, MCPSetupError
from tokenkick.telegram_remote import TelegramRemoteClient, TelegramRemoteListener, truncate_telegram_reply
from tokenkick.versioning import read_daemon_pidfile, write_daemon_pidfile, pidfile_process_matches


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for module in (models, cli):
        monkeypatch.setattr(module, 'CONFIG_DIR', tmp_path)
        monkeypatch.setattr(module, 'CONFIG_FILE', tmp_path / 'config.json')
    monkeypatch.setattr(models, 'HISTORY_FILE', tmp_path / 'history.jsonl')
    monkeypatch.setattr(cli, 'DAEMON_PID_FILE', tmp_path / 'daemon.pid')
    monkeypatch.setattr('tokenkick.scheduling.PENDING_KICKS_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(cli, 'load_kick_history', lambda **_: [])
    monkeypatch.setattr(rendering, '_pending_by_label', lambda: {})


def status(label='A', **kwargs):
    return AccountStatus(label, AccountState.ACTIVE, used_percent=5, resets_in_seconds=86400,
                         window_minutes=10080, session_window_minutes=300,
                         session_resets_in_seconds=0, session_used_percent=0, **kwargs)


@pytest.mark.parametrize('missing', [0, 1, 2])
def test_partial_cache_keeps_pairs_and_never_borrows_quota(missing):
    accounts = [AccountConfig(label=name, provider='claude', auto_kick=True) for name in 'ABC']
    entries = {models.account_key_string(a): {'account': a, 'status': status(a.label),
               'provider_observed_at': to_utc_iso(datetime.now(timezone.utc))}
               for i, a in enumerate(accounts) if i != missing}
    paired, statuses, _ = cache._load_status_cache_for_accounts(Config(accounts=accounts), entries, require_all=False)
    assert [(a.label, s.label) for a, s in zip(paired, statuses, strict=True)] == [
        (a.label, a.label) for i, a in enumerate(accounts) if i != missing]


def test_config_disjoint_saves_preserve_disable_and_other_fields():
    Config(accounts=[AccountConfig(label='A', auto_kick=True)]).save()
    disable, unrelated = Config.load(), Config.load()
    disable.accounts[0].auto_kick = False
    disable.save()
    unrelated.poll_interval_minutes = 20
    unrelated.accounts[0].visible = False
    unrelated.save()
    saved = Config.load()
    assert not saved.accounts[0].auto_kick
    assert not saved.accounts[0].visible
    assert saved.poll_interval_minutes == 20


def test_config_conflicting_edits_fail_without_overwriting():
    Config().save()
    first, second = Config.load(), Config.load()
    first.poll_interval_minutes = 10
    second.poll_interval_minutes = 20
    first.save()
    with pytest.raises(models.StateFileError, match='concurrently'):
        second.save()
    assert Config.load().poll_interval_minutes == 10


@pytest.mark.parametrize('error,age', [('AuthError', 0), ('IdentityError', 0), (None, 3600)])
def test_failed_or_old_predictions_cannot_authorize_requests(error, age):
    a = AccountConfig(label='A', provider='codex', source=DataSource.CODEX_DIRECT, auto_kick=True)
    entry = {'status': status(error=error), 'refresh_error': error,
             'provider_observed_at': to_utc_iso(datetime.now(timezone.utc) - timedelta(seconds=age))}
    assert cli._codex_predicted_session_due_status(a, {models.account_key_string(a): entry}) is None


@pytest.mark.parametrize('key', ['primary', 'secondary'])
@pytest.mark.parametrize('missing', [None, {}, 'invalid', {'usedPercent': float('nan')}])
def test_codex_optional_native_window_preserves_valid_other_window(key, missing):
    good = {'usedPercent': 10, 'windowDurationMins': 300 if key == 'secondary' else 10080,
            'resetsAt': 2_000_000_000}
    other = 'secondary' if key == 'primary' else 'primary'
    parsed = sources._parse_codex_appserver_ratelimits('A', {'id': 2, 'result': {
        'rateLimits': {key: missing, other: good}}})
    assert parsed.state == AccountState.ACTIVE
    assert (parsed.session_used_percent if other == 'primary' else parsed.used_percent) == 10
    assert (parsed.session_window_minutes if key == 'primary' else parsed.window_minutes) is None


def test_broken_session_fallback_cannot_hide_native_usage(monkeypatch):
    a = AccountConfig(label='A', source=DataSource.CODEX_DIRECT, identity_email='a@example.test')
    expected = status()
    monkeypatch.setattr(sources, '_codex_direct_identity_mismatch_status', lambda _: None)
    monkeypatch.setattr(sources, '_fetch_codex_appserver_ratelimits', lambda _: expected)
    monkeypatch.setattr(sources, '_fetch_codex_session_jsonl_status', Mock(side_effect=AttributeError('bad record')))
    assert sources._fetch_codex_direct(a) is expected
    assert sources._parse_session_rate_limit('A', {'rate_limits': {'secondary': None}}).state == AccountState.UNKNOWN


@pytest.mark.parametrize('header', ['[mcp_servers."other"]', '[[tools]]', '["mcp_servers.tokenkick"]'])
def test_toml_remove_preserves_quoted_and_array_siblings(header):
    original = '[mcp_servers.tokenkick]\ncommand="tk"\n' + header + '\ncommand="other"\n'
    actual = tomllib.loads(_remove_toml_tokenkick_block(original))
    expected = tomllib.loads(header + '\ncommand="other"\n')
    assert actual == expected


def test_toml_multiline_header_like_content_preserved_or_refused():
    source = 'text = """hello\n[mcp_servers.tokenkick]\nworld\n"""\n'
    try:
        assert tomllib.loads(_remove_toml_tokenkick_block(source)) == tomllib.loads(source)
    except MCPSetupError:
        pass  # A conservative refusal is preferable to changing unrelated content.


def test_export_rejects_source_alias_ancestor_and_existing_destination(tmp_path):
    spec = importlib.util.spec_from_file_location('exporter', Path(__file__).parent / 'scripts/create-public-export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    marker = root / 'marker'
    marker.write_text('preserve')
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    for destination in [root / '..' / 'repo', alias, tmp_path, root / 'subdir']:
        with pytest.raises(ValueError):
            exporter.export(root, destination)
        assert marker.read_text() == 'preserve'
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        exporter.export(root, existing)


@pytest.mark.parametrize('pid', [0, -1, -123])
def test_nonpositive_pidfiles_are_never_signal_targets(tmp_path, pid):
    path = tmp_path / 'pid'
    path.write_text(str(pid))
    assert read_daemon_pidfile(path) is None
    assert not cli._pid_is_running(pid)


def test_pid_identity_reuse_and_legacy_refused(monkeypatch, tmp_path):
    path = tmp_path / 'daemon.pid'
    monkeypatch.setattr('tokenkick.versioning.process_identity', lambda _: 'first')
    write_daemon_pidfile(path, 12345)
    assert pidfile_process_matches(read_daemon_pidfile(path))
    monkeypatch.setattr('tokenkick.versioning.process_identity', lambda _: 'reused')
    kill = Mock()
    monkeypatch.setattr(cli, '_pid_is_running', lambda _: True)
    monkeypatch.setattr(cli.os, 'kill', kill)
    assert not cli._stop_daemon(quiet=True)
    kill.assert_not_called()
    path.write_text('12345\n')
    assert not cli._stop_daemon(quiet=True)
    kill.assert_not_called()


def pending(a, end):
    now = datetime.now(timezone.utc)
    return PendingKick(models.account_key_string(a), a.label, a.provider,
                       to_utc_iso(now-timedelta(minutes=1)), to_utc_iso(now-timedelta(hours=3)),
                       'optimal', 1, 0, 'none', to_utc_iso(now-timedelta(hours=2)),
                       to_utc_iso(end), window_basis='session')


@pytest.mark.parametrize('expired', [False, True])
def test_due_kick_respects_common_guard_and_work_end(monkeypatch, expired):
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    st = replace(status(), used_percent=5 if expired else 100)
    p = pending(a, datetime.now(timezone.utc)+timedelta(hours=-1 if expired else 1))
    monkeypatch.setattr(cli, 'load_pending_kicks', lambda *_: {p.account_key: p})
    monkeypatch.setattr(cli, '_session_cooldown_remaining', lambda *_: None)
    monkeypatch.setattr(cli, '_was_kicked_in_current_session_window', lambda *_: False)
    action = Mock()
    monkeypatch.setattr(cli, '_kick_and_notify', action)
    cli._execute_due_pending_kicks([a], Config(accounts=[a]), statuses_by_key={p.account_key: st})
    action.assert_not_called()


def _hold_action(directory, ready):
    with account_action(Path(directory), 'test'):
        ready.set()
        import time
        time.sleep(30)


def test_action_lease_serializes_processes_and_preserves_crash_evidence(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    ready = ctx.Event()
    child = ctx.Process(target=_hold_action, args=(str(tmp_path), ready))
    child.start()
    try:
        assert ready.wait(10)
        with pytest.raises(StateFileLockTimeout):
            with account_action(tmp_path, 'test'):
                pytest.fail('concurrent action entered')
    finally:
        child.terminate()
        child.join(10)
    with pytest.raises(UncertainActionError):
        with account_action(tmp_path, 'test'):
            pytest.fail('uncertain request repeated')
    with account_action(tmp_path, 'test', recover=True):
        pass


def test_old_cache_metadata_and_eligibility_agree():
    a = AccountConfig(label='A', provider='claude')
    st = replace(status(), state=AccountState.FRESH)
    entry = {'account': a, 'status': st, 'provider_observed_at': '2020-01-01T00:00:00Z'}
    row = rendering._status_rows_as_dict([st], [a], Config(), {models.account_key_string(a): entry})[0]
    assert row['stale'] and not row['kickable']
    assert row['kick_blocked_reason'] == 'stale_status'


def test_telegram_network_retry_and_rate_limit_backoff(monkeypatch, tmp_path):
    response = Mock(status_code=429, text='rate limited')
    response.json.return_value = {'parameters': {'retry_after': 60}}
    monkeypatch.setattr(httpx, 'get', Mock(side_effect=[httpx.ReadTimeout('secret URL'), response, KeyboardInterrupt()]))
    client = TelegramRemoteClient('test-token')
    monkeypatch.setattr(client, 'get_webhook_info', lambda: {})
    sleeps = []
    logs = []
    monkeypatch.setattr('tokenkick.telegram_remote.time.sleep', sleeps.append)
    monkeypatch.setattr('tokenkick.telegram_remote.random.uniform', lambda *_: 0)
    listener = TelegramRemoteListener(client=client, allowed_chat_id='test', state_file=tmp_path/'state',
                                      status_runner=Mock(), logger=lambda *args: logs.append(args))
    with pytest.raises(KeyboardInterrupt):
        listener.run_forever()
    assert sleeps == [2, 60]
    assert 'secret URL' not in str(logs)


def test_telegram_html_truncation_does_not_split_tags_or_entities():
    text = truncate_telegram_reply('<b>' + '&amp;' * 3000 + '</b>', limit=80, html=True)
    assert len(text) <= 80
    assert '<b>' not in text
    assert text.count('&') == text.count('&amp;')


@pytest.mark.parametrize('day,weekdays,weekends', [(13, '22:00-06:00', None), (17, '22:00-06:00', None),
                                                   (19, None, '22:00-06:00')])
def test_overnight_window_uses_start_days_schedule(day, weekdays, weekends):
    now = datetime(2026, 10, day, 1, tzinfo=timezone.utc)
    window = resolve_today_work_window(models.WorkSchedule(enabled=True, weekdays=weekdays, weekends=weekends), now, timezone.utc)
    assert window is not None and window[0] < now < window[1]
    assert window[0].date() == (now-timedelta(days=1)).date()


def _stats_writer(path, start):
    from tokenkick.codex_surface_stats import update_codex_surface_stats
    from tokenkick.kicker import CODEX_KICK_SURFACE_REPO
    start.wait(10)
    a = AccountConfig(label='A', provider='codex')
    for _ in range(12):
        update_codex_surface_stats(Path(path), a, [models.KickEvent(
            label='A', success=True, confirmed=True, codex_surface=CODEX_KICK_SURFACE_REPO)])


def test_statistics_transactions_preserve_concurrent_updates(tmp_path):
    from tokenkick.codex_surface_stats import load_codex_surface_stats
    from tokenkick.kicker import CODEX_KICK_SURFACE_REPO
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Event()
    path = tmp_path / 'stats.json'
    children = [ctx.Process(target=_stats_writer, args=(str(path), start)) for _ in range(3)]
    for child in children:
        child.start()
    start.set()
    try:
        for child in children:
            child.join(15)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
    data = load_codex_surface_stats(path)
    account = data['accounts'][models.account_key_string(AccountConfig(label='A', provider='codex'))]
    assert account[CODEX_KICK_SURFACE_REPO]['attempts'] == 36


def _hold_daemon_owner(directory, ready):
    import time
    @cli._process_lock(lambda: Path(directory) / 'daemon.owner')
    def foreground():
        ready.set()
        time.sleep(30)
    foreground()


def test_foreground_daemon_ownership_is_exclusive(tmp_path):
    import click
    ctx = multiprocessing.get_context('spawn')
    ready = ctx.Event()
    child = ctx.Process(target=_hold_daemon_owner, args=(str(tmp_path), ready))
    child.start()
    try:
        assert ready.wait(10)
        @cli._process_lock(lambda: tmp_path / 'daemon.owner')
        def other():
            pytest.fail('second daemon entered')
        with pytest.raises(click.ClickException, match='owns'):
            other()
    finally:
        child.terminate()
        child.join(10)
    # Kernel releases the lease after a crash.
    with cli.state_file_lock(tmp_path / 'daemon.owner', timeout=0):
        pass


def test_startup_never_overwrites_another_owners_pidfile(tmp_path):
    path = tmp_path / 'daemon.pid'
    path.write_text('1234\nprocess_identity=other-owner\n')
    process = Mock(pid=4567)
    process.poll.return_value = 1
    assert not cli._wait_for_owned_start(process, path)
    assert path.read_text() == '1234\nprocess_identity=other-owner\n'
    process.terminate.assert_not_called()


@pytest.mark.parametrize('change,reason', [
    ({'used_percent': 100}, 'weekly_exhausted'),
    ({'session_used_percent': 100, 'session_resets_in_seconds': 1000}, 'session_exhausted'),
    ({'stale': True}, 'stale_status'),
])
def test_action_boundary_rejects_unsafe_status(monkeypatch, change, reason):
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    action = Mock()
    monkeypatch.setattr(cli, '_kick_and_notify_owned', action)
    event = cli._kick_and_notify(a, Config(accounts=[a]), replace(status(), **change))
    assert not event.success and not event.confirmed
    assert event.error == reason
    action.assert_not_called()


def test_auto_action_reloads_disable_before_provider_request(monkeypatch):
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    Config(accounts=[a]).save()
    old = Config.load()
    current = Config.load()
    current.accounts[0].auto_kick = False
    current.save()
    action = Mock()
    monkeypatch.setattr(cli, '_kick_and_notify_owned', action)
    event = cli._kick_and_notify(a, old, status(), automatic=True, kick_type='session')
    assert not event.success and 'disabled' in event.error
    action.assert_not_called()


def test_repeated_action_rechecks_history_under_lease(monkeypatch):
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    history = []
    monkeypatch.setattr(cli, 'load_kick_history', lambda **_: history.copy())
    def action(*args, **kwargs):
        event = models.KickEvent(label='A', success=True, confirmed=True, kick_type='session')
        history.append(event)
        return event
    execute = Mock(side_effect=action)
    monkeypatch.setattr(cli, '_kick_and_notify_owned', execute)
    assert cli._kick_and_notify(a, Config(accounts=[a]), status(), kick_type='session').success
    assert not cli._kick_and_notify(a, Config(accounts=[a]), status(), kick_type='session').success
    assert execute.call_count == 1


def test_failed_live_read_never_replaced_by_healthy_cache():
    a = AccountConfig(label='A', provider='codex', source=DataSource.CODEX_DIRECT, auto_kick=True)
    key = models.account_key_string(a)
    failed = AccountStatus('A', AccountState.UNKNOWN, stale=True, error='IdentityError')
    live = {key: failed}
    entries = {key: {'status': status(), 'provider_observed_at': to_utc_iso(datetime.now(timezone.utc))}}
    cli._apply_codex_predicted_session_due_statuses([a], live, entries)
    assert live[key] is failed


def test_plan_fingerprint_binds_config_and_pending_changes(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from tokenkick.orchestration import build_orchestration_plan, AccountPlanInput
    now = datetime.now(timezone.utc)
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    plan = build_orchestration_plan(config=Config(accounts=[a]), inputs=[AccountPlanInput(a, replace(status(), state=AccountState.FRESH))],
        work_start=now+timedelta(hours=1), work_end=now+timedelta(hours=3), now=now,
        timezone_name='UTC', pending={})
    fingerprint = plan.fingerprint()
    assert plan.planned_kicks
    assert replace(plan, cache_age_seconds=40, built_at=now+timedelta(seconds=1)).fingerprint() == fingerprint
    assert replace(plan, config_revision='changed').fingerprint() != fingerprint
    # Simulate a configuration edit between preview and apply.
    changed = replace(plan, config_revision='changed')
    monkeypatch.setattr(cli, '_build_plan_from_options', lambda **_: (changed, now))
    apply = Mock()
    monkeypatch.setattr(cli, 'apply_orchestration_plan', apply)
    result = CliRunner().invoke(cli.cli, ['plan', '--work-window', '09:00-17:00',
        '--apply', '--yes', '--json-output', '--expected-plan', fingerprint])
    assert result.exit_code == 1
    assert 'changed since preview' in result.output
    apply.assert_not_called()


def test_export_copies_tracked_files_into_new_destination(tmp_path):
    spec = importlib.util.spec_from_file_location('exporter', Path(__file__).parent / 'scripts/create-public-export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'tracked').write_text('hello')
    (root / 'untracked').write_text('private')
    subprocess.run(['git', '-C', str(root), 'add', 'tracked'], check=True)
    destination = tmp_path / 'export'
    exporter.export(root, destination)
    assert (destination / 'tracked').read_text() == 'hello'
    assert not (destination / 'untracked').exists()


def test_plan_apply_uses_reviewed_clock_but_rechecks_current_state(monkeypatch):
    import json
    from click.testing import CliRunner
    clock = [datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)]
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    Config(accounts=[a]).save()
    entry = {'account': a, 'status': replace(status(), state=AccountState.FRESH),
             'provider_observed_at': to_utc_iso(clock[0])}
    monkeypatch.setattr(cli, '_status_cache_now', lambda: clock[0])
    monkeypatch.setattr(cli, '_load_status_cache_entries', lambda: {models.account_key_string(a): entry})
    for name in ('_migrate_provider_first_labels_if_needed', '_migrate_codex_home_keys_if_needed',
                 '_repair_codex_home_identity_drift_if_needed'):
        monkeypatch.setattr(cli, name, lambda config: config)
    args = ['plan', '--work-window', '10:00-13:00', '--date', '2026-10-13', '--timezone', 'UTC', '--json-output']
    runner = CliRunner()
    preview = runner.invoke(cli.cli, args)
    assert preview.exit_code == 0, preview.output
    payload = json.loads(preview.output)
    assert payload['planned_kicks']
    clock[0] += timedelta(seconds=45)
    applied = runner.invoke(cli.cli, args + ['--apply', '--yes', '--expected-plan',
        payload['plan_fingerprint'], '--preview-at', payload['built_at']])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)['applied'] is True


def test_recovery_does_not_repeat_just_completed_action(monkeypatch):
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    execute = Mock(return_value=models.KickEvent(label='A', success=True))
    monkeypatch.setattr(cli, '_kick_and_notify_owned', execute)
    assert cli._kick_and_notify(a, Config(accounts=[a]), status()).success
    event = cli._kick_and_notify(a, Config(accounts=[a]), status(), recovery_history=[])
    assert not event.success and 'Recent action' in event.error
    assert execute.call_count == 1


def test_plan_transaction_does_not_erase_concurrent_pending_write(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import tokenkick.orchestration as orchestration
    import tokenkick.scheduling as scheduling
    now = datetime.now(timezone.utc)
    a = AccountConfig(label='A', provider='claude', auto_kick=True)
    b = AccountConfig(label='B', provider='claude', auto_kick=True)
    plan = orchestration.build_orchestration_plan(config=Config(accounts=[a]),
        inputs=[orchestration.AccountPlanInput(a, replace(status(), state=AccountState.FRESH))],
        work_start=now+timedelta(hours=1), work_end=now+timedelta(hours=3), now=now,
        timezone_name='UTC', pending={})
    read, release, other_done = threading.Event(), threading.Event(), threading.Event()
    real_load = orchestration.load_pending_kicks
    def held_read(*args, **kwargs):
        data = real_load(*args, **kwargs)
        read.set()
        assert release.wait(5)
        return data
    monkeypatch.setattr(orchestration, 'load_pending_kicks', held_read)
    extra = pending(b, now+timedelta(hours=2))
    def add_other():
        with cli.state_file_lock(scheduling.PENDING_KICKS_FILE):
            items = scheduling.load_pending_kicks(now)
            items[extra.account_key] = extra
            scheduling.save_pending_kicks(items)
        other_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestration.apply_orchestration_plan, plan, now=now, current_time=now)
        assert read.wait(5)
        second = pool.submit(add_other)
        try:
            assert not other_done.wait(0.1)
        finally:
            release.set()
        assert first.result(timeout=5).applied
        second.result(timeout=5)
    assert set(scheduling.load_pending_kicks(now)) == {models.account_key_string(a), extra.account_key}


def test_status_error_html_truncation_remains_valid():
    from tokenkick.telegram_remote import format_status_reply, StatusCommandResult
    output = format_status_reply(StatusCommandResult(1, '', '<' * 6000))
    assert len(output) <= 3900
    assert output.count('<i>') == output.count('</i>')
    assert not output.endswith('&l')
