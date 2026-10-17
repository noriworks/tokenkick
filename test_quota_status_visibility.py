"""Default terminal and JSON visibility for separate quotas and managed windows."""
from dataclasses import replace
from datetime import datetime, timezone
import io
import time

import pytest
from rich.console import Console

from tokenkick import cli, status_rendering as render
from tokenkick.models import AccountConfig, AccountStatus, AccountState, Config, DataSource


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(cli, 'load_kick_history', lambda **kw: [])
    monkeypatch.setattr(cli, 'load_pending_kicks', lambda *args: {})
    monkeypatch.setattr(cli, '_stagger_status_payload', lambda: {'mode':'disabled','members':[]})
    out = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=out, width=200, color_system=None))
    now = time.time()
    accounts = [AccountConfig(label=f, provider='antigravity', source=DataSource.ANTIGRAVITY_CLI,
                              identity_email='fixture@example.test', antigravity_family=f)
                for f in ('gemini','claude_gpt')]
    quota = dict(id='antigravity-quota-summary-argon-weekly', family='argon', window_kind='weekly',
                 window_minutes=10080, used_percent=17, resets_in_seconds=600000, resets_at=now+600000)
    statuses = [AccountStatus(label=a.label, state=AccountState.ACTIVE, used_percent=10,
                 resets_at=now+500000, resets_in_seconds=500000, window_minutes=10080,
                 session_resets_at=now+1000, session_resets_in_seconds=1000, session_used_percent=0,
                 session_window_minutes=300, source_detail='antigravity-cli',
                 observed_at=datetime.fromtimestamp(now,timezone.utc).isoformat(), quota_windows=[quota]) for a in accounts]
    return out, accounts, statuses, now


@pytest.mark.parametrize('verbose', [False, True])
def test_argon_visible_once_in_default_and_verbose_views(display, verbose):
    out, accounts, statuses, now = display
    render._render_status_table(statuses, accounts, Config(), verbose=verbose)
    text = out.getvalue()
    assert text.count('Argon 4') == 1
    assert '17%' in text and 'Weekly quota' in text
    assert 'Monitor only; separate allowance' in text
    rows = render._status_rows_as_dict(statuses, accounts, Config())
    assert all(r['additional_quotas'][0]['monitor_only'] for r in rows)
    assert all(r['additional_quotas'][0]['window_kind'] == 'weekly' for r in rows)


def test_argon_chooses_freshest_shared_copy_and_marks_stale(display):
    out, accounts, statuses, now = display
    statuses[0] = replace(statuses[0], stale=True, observed_at='2020-01-01T00:00:00Z')
    render._render_status_table(statuses, accounts, Config())
    assert 'Weekly (cached)' not in out.getvalue()
    out.seek(0)
    out.truncate()
    statuses[1] = replace(statuses[1], observed_at='2020-01-02T00:00:00Z')
    render._render_status_table(statuses, accounts, Config())
    assert 'Weekly (cached)' in out.getvalue()


def test_separate_identities_and_missing_or_malformed_argon(display):
    out, accounts, statuses, now = display
    accounts[1] = replace(accounts[1], identity_email='other@example.test')
    render._render_status_table(statuses, accounts, Config())
    assert out.getvalue().count('Argon 4') == 2
    out.seek(0)
    out.truncate()
    statuses[0] = replace(statuses[0], quota_windows=[])
    statuses[1] = replace(statuses[1], quota_windows=[dict(statuses[1].quota_windows[0], used_percent=float('nan'))])
    render._render_status_table(statuses, accounts, Config())
    assert 'Argon 4' not in out.getvalue()


@pytest.mark.parametrize('state,running', [('scheduled', True), ('paused', True), ('blocked', True), ('scheduled', False)])
def test_managed_members_show_controller_and_never_kick_now_banner(display, monkeypatch, state, running):
    out, accounts, statuses, now = display
    accounts = [replace(a, stagger_group_id='antigravity-default') for a in accounts]
    payload = dict(enabled=True, state=state, daemon_running=running, next_family='claude_gpt', next_at=now+9000)
    monkeypatch.setattr(cli, '_stagger_status_payload', lambda: payload)
    # Even an individually kickable quota belongs to the stagger controller.
    monkeypatch.setattr(cli, '_status_actionable_now', lambda *args, **kw: True)
    render._render_status_table(statuses, accounts, Config())
    text = out.getvalue()
    assert 'Kick-ready windows' not in text and 'tk kick --all' not in text
    if running and state == 'scheduled':
        expected = datetime.fromtimestamp(now+9000).astimezone().strftime('%H:%M %Z')
        assert render._status_action(statuses[1], {}, accounts[1]) == 'Stagger: ' + expected
        assert render._status_action(statuses[0], {}, accounts[0]) == 'Stagger: waiting for peer'
    else:
        assert render._status_action(statuses[1], {}, accounts[1]) == 'Stagger ' + (state if running else 'daemon stopped')
    rows = render._status_rows_as_dict(statuses, accounts, Config())
    assert all(r['automation_action'].startswith('Stagger') for r in rows)


@pytest.mark.parametrize("field,value", [("resets_at", float("inf")), ("resets_in_seconds", float("nan")),
                                       ("resets_in_seconds", "invalid")])
def test_argon_invalid_reset_never_crashes_display(display, field, value):
    out, accounts, statuses, _ = display
    statuses = [replace(s, quota_windows=[dict(s.quota_windows[0], **{field:value})]) for s in statuses]
    render._render_status_table(statuses, accounts, Config())
    assert "Argon 4" not in out.getvalue()
