"""Claude monitoring failures, cooldown persistence, and truthful observations."""

import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from tokenkick import cli, sources, status_cache, status_rendering
from tokenkick.claude_monitoring import cooling_down, diagnostic_text, diagnostics, retry_at
from tokenkick.models import (
    AccountConfig, AccountState, AccountStatus, ClaudeProbeContext, ClaudeProbeError,
    ClaudeProbeErrorCategory as Category, Config, DataSource, account_key_string,
)

NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
STAMP = "2026-10-13T10:00:00Z"


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz else cls.current.replace(tzinfo=None)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    Clock.current = NOW
    monkeypatch.setattr(sources, "datetime", Clock)
    monkeypatch.setattr(sources, "_status_observed_at", lambda: Clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr(cli, "_status_cache_now", lambda: Clock.current)
    monkeypatch.setattr(cli, "_status_cache_observed_at", lambda: Clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr(cli, "STATUS_CACHE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_claude_identity_probe_error", lambda _: None)
    monkeypatch.setattr(sources.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(sources, "claude_auth_status", lambda _: None)
    monkeypatch.setattr(sources, "_capture_claude_usage", lambda _: pytest.fail("unexpected live probe"))
    monkeypatch.setattr(sources, "_fetch_claude_probe", lambda _: pytest.fail("unexpected generation"))
    monkeypatch.setattr(sources, "_fetch_codexbar_cli", lambda account, **_: AccountStatus(
        label=account.label, state=AccountState.UNKNOWN, error="fallback unavailable"))
    sources._set_claude_cli_usage_cache(None)
    yield
    sources._set_claude_cli_usage_cache(None)


def account():
    return AccountConfig(label="claude (test)", provider="claude", source=DataSource.CLAUDE_DIRECT)


def direct_status():
    return AccountStatus(label=account().label, state=AccountState.ACTIVE,
                         source_detail="claude-cli-usage", observed_at=STAMP,
                         used_percent=22, resets_in_seconds=3600, window_minutes=10080)


def limited(count=1):
    return ClaudeProbeContext(
        last_direct_probe_at=STAMP, direct_rate_limit_failures=count,
        last_direct_probe_error=ClaudeProbeError(Category.RATE_LIMITED, "rate limited"),
    )


@pytest.mark.parametrize("count,minutes", [(0, 5), (1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (10000, 60)])
def test_backoff_boundaries(count, minutes):
    entry = vars(limited(count))
    assert retry_at(entry) == NOW + timedelta(minutes=minutes)
    assert cooling_down(entry, NOW)
    assert cooling_down(entry, NOW + timedelta(minutes=minutes, microseconds=-1))
    assert not cooling_down(entry, NOW + timedelta(minutes=minutes))
    assert not cooling_down(entry, NOW - timedelta(seconds=1))


@pytest.mark.parametrize("bad", [None, "huge", -5, True, {}, 3.5])
def test_malformed_count_uses_legacy_minimum(bad):
    assert retry_at({**vars(limited()), "direct_rate_limit_failures": bad}) == NOW + timedelta(minutes=5)


@pytest.mark.parametrize("bad", [None, "bad", 43, "9999-12-31T23:59:59Z"])
def test_bad_timestamp_does_not_lock_out(bad):
    assert not cooling_down({**vars(limited()), "last_direct_probe_at": bad}, NOW)


def test_real_failures_grow_and_success_resets(isolated):
    context = ClaudeProbeContext()
    for minutes in [5, 10, 20, 40, 60, 60]:
        sources._record_claude_probe_error(context, Category.RATE_LIMITED, "rate limited")
        assert retry_at(vars(context)) == Clock.current + timedelta(minutes=minutes)
        Clock.current += timedelta(minutes=minutes)
    sources._record_claude_probe_success(context, direct_status())
    assert context.direct_rate_limit_failures == 0
    assert context.last_direct_probe_error is None
    assert retry_at(vars(context)) is None
    sources._record_claude_probe_error(context, Category.RATE_LIMITED, "rate limited")
    assert context.direct_rate_limit_failures == 1


def test_legacy_rate_limit_counts_previous_attempt(isolated):
    context = limited(0)
    sources._record_claude_probe_error(context, Category.RATE_LIMITED, "rate limited")
    assert context.direct_rate_limit_failures == 2


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("recent_success", [False, True])
def test_cooldown_blocks_native_and_generation_even_forced(isolated, force, recent_success):
    context = limited(3)
    if recent_success:
        context.last_direct_success_at = STAMP
        context.last_direct_success_status = direct_status()
    candidate = replace(account(), status_probe_enabled=True)
    Clock.current += timedelta(minutes=6)  # Beyond old fixed interval.
    with sources.claude_cli_usage_refresh_allowed(force=force):
        status = sources._fetch_claude_direct(candidate, claude_probe_context=context)
    assert not context.direct_probe_performed
    assert context.direct_rate_limit_failures == 3
    assert context.last_direct_probe_at == STAMP
    if recent_success:
        assert status.observed_at == STAMP
        assert status.stale
    else:
        assert status.state == AccountState.UNKNOWN


def test_deadline_allows_real_probe_and_clears_error(isolated, monkeypatch):
    context = limited(2)
    Clock.current += timedelta(minutes=10)
    monkeypatch.setattr(sources, "_capture_claude_usage", lambda _: "fixture")
    monkeypatch.setattr(sources, "_parse_claude_usage_output", lambda *_: direct_status())
    with sources.claude_cli_usage_refresh_allowed():
        result = sources._fetch_claude_direct(account(), claude_probe_context=context)
    assert result.state == AccountState.ACTIVE
    assert context.direct_probe_performed
    assert context.direct_rate_limit_failures == 0
    assert context.last_direct_probe_error is None


@pytest.mark.parametrize("failure", [False, True])
def test_pass_cache_does_not_recount_or_renew(isolated, monkeypatch, failure):
    calls = []

    def capture(_):
        calls.append(True)
        if failure:
            raise RuntimeError("rate limited")
        return "fixture"

    monkeypatch.setattr(sources, "_capture_claude_usage", capture)
    monkeypatch.setattr(sources, "_parse_claude_usage_output", lambda *_: direct_status())
    first, second = ClaudeProbeContext(), ClaudeProbeContext()
    with sources.claude_cli_usage_refresh_allowed():
        a = sources._fetch_claude_cli_usage(account(), first)
        Clock.current += timedelta(seconds=20)
        b = sources._fetch_claude_cli_usage(account(), second)
    assert len(calls) == 1
    assert first.direct_probe_performed and not second.direct_probe_performed
    assert second.last_direct_probe_at == first.last_direct_probe_at
    assert second.last_direct_success_at == first.last_direct_success_at
    assert second.direct_rate_limit_failures == first.direct_rate_limit_failures
    assert a.observed_at == b.observed_at


def save(status, context):
    status._claude_probe_context = context
    cli._save_status_cache([account()], {account_key_string(account()): status})
    return cli._load_status_cache_entries()[account_key_string(account())]


def test_restart_persists_cooldown_and_original_observation(isolated):
    save(direct_status(), ClaudeProbeContext(last_direct_success_status=direct_status(),
                                            last_direct_success_at=STAMP))
    context = limited(4)
    Clock.current += timedelta(minutes=10)
    entry = save(AccountStatus(label=account().label, state=AccountState.UNKNOWN, error="rate limited"), context)
    loaded = status_cache._claude_probe_context_for_account(account(), Config())
    assert loaded.direct_rate_limit_failures == 4
    assert loaded.last_direct_success_at == STAMP  # A failure never erases the observation.
    assert entry["provider_observed_at"] == STAMP
    with sources.claude_cli_usage_refresh_allowed(force=True):
        sources._fetch_claude_direct(account(), claude_probe_context=loaded)
    assert not loaded.direct_probe_performed


def test_older_writer_cannot_clear_newer_backoff(isolated):
    context = limited(3)
    context.last_direct_probe_at = "2026-10-13T10:10:00Z"
    save(direct_status(), context)
    older = ClaudeProbeContext(last_direct_probe_at=STAMP, last_direct_success_at=STAMP,
                               last_direct_success_status=direct_status())
    entry = save(direct_status(), older)
    assert entry["direct_rate_limit_failures"] == 3
    assert entry["last_direct_probe_error"].category == Category.RATE_LIMITED


def test_background_refresh_waits_but_other_accounts_do_not(isolated):
    candidate = account()
    entry = {**vars(limited(3)), "account": candidate, "status": direct_status(),
             "cached_at": STAMP, "provider_observed_at": STAMP, "refresh_error": "ProviderError"}
    Clock.current += timedelta(minutes=10)
    assert not status_cache._status_cache_needs_refresh({"claude": entry}, Config())
    other = {"account": AccountConfig(label="codex", provider="codex"), "needs_refresh": True}
    assert status_cache._status_cache_needs_refresh({"claude": entry, "codex": other}, Config())
    Clock.current += timedelta(minutes=10)
    assert status_cache._status_cache_needs_refresh({"claude": entry}, Config())


@pytest.mark.parametrize("source", ["claude-cli-usage", "claude-codexbar-fallback"])
def test_usage_touch_rejects_reused_data_and_saves_failure(isolated, monkeypatch, source):
    context = limited(3)
    status = replace(direct_status(), source_detail=source)
    status._claude_probe_context = context
    monkeypatch.setattr(cli, "_fetch_status", lambda *_: status)
    event, _ = cli._run_claude_usage_touch(account(), Config(), kind="session", kick_type="session",
                                         success_response="ok", failure_prefix="failed", force=True)
    assert not event.success and not event.confirmed
    entry = cli._load_status_cache_entries()[account_key_string(account())]
    assert entry["direct_rate_limit_failures"] == 3
    assert entry["last_direct_probe_error"].category == Category.RATE_LIMITED
    assert entry["provider_observed_at"] == STAMP


def test_usage_touch_requires_proof_of_fresh_probe(isolated, monkeypatch):
    monkeypatch.setattr(cli, "_fetch_status", lambda *_: direct_status())
    event, _ = cli._run_claude_usage_touch(account(), Config(), kind="session", kick_type="session",
                                         success_response="ok", failure_prefix="failed")
    assert not event.success and not event.confirmed


@pytest.mark.parametrize("category,text", [
    (Category.RATE_LIMITED, "refresh rate-limited"),
    (Category.NOT_AUTHENTICATED, "authentication failed"),
    (Category.TIMEOUT, "refresh timed out"),
    (Category.PARSE_FAILED, "could not be parsed"),
    (Category.IDENTITY_MISMATCH, "identity mismatch"),
    (Category.DISABLED, "monitoring disabled"),
])
@pytest.mark.parametrize("source,description", [
    ("claude-cli-usage", "cached direct data"),
    ("claude-codexbar-fallback", "CodexBar fallback data"),
])
def test_diagnostics_distinguish_failure_and_source_without_raw_secrets(category, text, source, description):
    entry = {**vars(limited()), "status": replace(direct_status(), source_detail=source)}
    entry["last_direct_probe_error"] = ClaudeProbeError(category, "secret-do-not-print", raw="secret-raw")
    message = diagnostic_text(entry)
    assert text in message and description in message
    assert "secret" not in message
    assert "secret" not in str(diagnostics(entry))
    if category == Category.RATE_LIMITED:
        assert "2026-10-13T10:05:00Z" in message
        assert "authentication" not in message


@pytest.mark.parametrize("message", ["HTTP 429", "Too many requests", "rate_limit_error", "rate limited; retry login later"])
def test_rate_limit_classification(message):
    assert sources._claude_probe_error_category_from_message(message) == Category.RATE_LIMITED
    assert "rate limited" in sources._extract_claude_usage_error(message)


def test_table_footer_and_json_keep_same_diagnostics(isolated, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=220, color_system=None))
    monkeypatch.setattr(cli, "load_pending_kicks", lambda *_: {})
    monkeypatch.setattr(cli, "load_kick_history", lambda **_: [])
    status = replace(direct_status(), stale=True)
    entry = {**vars(limited(2)), "account": account(), "status": status,
             "cached_at": STAMP, "provider_observed_at": STAMP, "refresh_error": "ProviderError"}
    entries = {account_key_string(account()): entry}
    status_rendering._render_status_table([status], [account()], cache_entries=entries)
    text = output.getvalue()
    assert "Stale CodexBar" not in text
    assert "Claude usage refresh rate-limited; showing cached direct data" in text
    assert "2026-10-13T10:10:00Z" in text
    footer = status_cache._format_status_cache_footer(entries, Config())
    assert "Claude usage refresh rate-limited" in footer
    rows = status_rendering._status_rows_as_dict([status], [account()], config=Config(), cache_entries=entries)
    assert rows[0]["usage_monitoring"]["error_category"] == "rate_limited"
    assert rows[0]["usage_monitoring"]["observation_source"] == "claude-cli-usage"


def test_reconciliation_does_not_record_skipped_cooldown_as_failed_kick(isolated, monkeypatch):
    candidate = replace(account(), auto_kick=True, session_auto_kick=True)
    status = replace(direct_status(), session_used_percent=20, session_resets_in_seconds=7200,
                     session_window_minutes=300)
    entries = {account_key_string(candidate): {
        **vars(limited(3)), "account": candidate, "status": status,
        "last_direct_success_at": "2026-10-13T07:00:00Z",
    }}
    Clock.current += timedelta(minutes=10)
    monkeypatch.setattr(cli, "_run_claude_usage_touch", lambda *_args, **_kwargs: pytest.fail("skipped cooldown"))
    assert cli._execute_claude_reconciliation_probes([candidate], {}, Config(), entries) == 0
    Clock.current += timedelta(minutes=10)
    assert cli._claude_reconciliation_due(candidate, entries, Config()) is not None


def test_native_rate_limit_with_usable_fallback_persists_for_next_refresh(isolated, monkeypatch):
    def capture(_):
        raise RuntimeError("HTTP 429 Too many requests")

    monkeypatch.setattr(sources, "_capture_claude_usage", capture)
    monkeypatch.setattr(sources, "_fetch_codexbar_cli", lambda *_args, **_kwargs: replace(
        direct_status(), source_detail="codexbar-cli"))
    with sources.claude_cli_usage_refresh_allowed():
        result = status_cache._fetch_status(account(), Config())
    assert result.source_detail == "claude-cli-usage"
    cli._save_status_cache([account()], {account_key_string(account()): result})
    loaded = status_cache._claude_probe_context_for_account(account(), Config())
    assert loaded.last_direct_probe_error.category == Category.RATE_LIMITED
    assert loaded.direct_rate_limit_failures == 1
    monkeypatch.setattr(sources, "_capture_claude_usage", lambda _: pytest.fail("cooldown bypass"))
    Clock.current += timedelta(minutes=1)
    with sources.claude_cli_usage_refresh_allowed(force=True):
        status_cache._fetch_status(account(), Config())


def test_non_rate_limit_error_ends_consecutive_streak(isolated):
    context = limited(4)
    sources._record_claude_probe_error(context, Category.NOT_AUTHENTICATED, "auth failed")
    assert context.direct_rate_limit_failures == 0
    assert retry_at(vars(context)) is None
    assert diagnostics(vars(context))["error_category"] == "not_authenticated"


def test_fresh_fallback_footer_still_reports_direct_monitor_failure(isolated):
    status = replace(direct_status(), source_detail="claude-codexbar-fallback")
    entry = {**vars(limited()), "account": account(), "status": status,
             "cached_at": STAMP, "provider_observed_at": STAMP}
    footer = status_cache._format_status_cache_footer({"claude": entry}, Config())
    assert "rate-limited; showing CodexBar fallback data" in footer
    assert "2026-10-13T10:05:00Z" in footer
    assert not status_cache._status_cache_entry_is_stale(entry, Config())


def test_unrelated_numeric_output_is_not_http_rate_limit():
    assert sources._extract_claude_usage_error("Tokens: 429\nCurrent session 5% used") is None


def test_repeated_refreshes_reload_backoff_then_recover(isolated, monkeypatch):
    attempts = []

    def capture(_):
        attempts.append(Clock.current)
        if len(attempts) <= 2:
            raise RuntimeError("HTTP 429 Too many requests")
        return "ok"

    monkeypatch.setattr(sources, "_capture_claude_usage", capture)
    monkeypatch.setattr(sources, "_parse_claude_usage_output", lambda *_: replace(
        direct_status(), observed_at=Clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")))

    def refresh():
        # Each call reloads persisted state, as a new CLI process would.
        with sources.claude_cli_usage_refresh_allowed(force=True):
            result = status_cache._fetch_status(account(), Config())
        cli._save_status_cache([account()], {account_key_string(account()): result})
        return cli._load_status_cache_entries()[account_key_string(account())]

    assert refresh()["direct_rate_limit_failures"] == 1
    Clock.current = NOW + timedelta(minutes=4)
    assert refresh()["direct_rate_limit_failures"] == 1
    assert len(attempts) == 1
    Clock.current = NOW + timedelta(minutes=5)
    assert refresh()["direct_rate_limit_failures"] == 2
    Clock.current = NOW + timedelta(minutes=14)
    assert refresh()["direct_rate_limit_failures"] == 2
    assert len(attempts) == 2
    Clock.current = NOW + timedelta(minutes=15)
    final = refresh()
    assert len(attempts) == 3
    assert final["direct_rate_limit_failures"] == 0
    assert final["last_direct_probe_error"] is None
    assert final["refresh_error"] is None
    assert final["provider_observed_at"] == "2026-10-13T10:15:00Z"


@pytest.fixture
def discovery_refresh(isolated, monkeypatch):
    """Use the actual discovery/merge/cache pipeline, mocking only providers."""
    from tokenkick import discovery
    from tokenkick.direct import DirectIdentity

    candidate = replace(account(), identity_email='test@example.test', identity_provider_id='claude-test')
    config = Config(accounts=[candidate])
    monkeypatch.setattr(cli, 'read_claude_identity', lambda: DirectIdentity(
        provider='claude', email=candidate.identity_email, provider_account_id=candidate.identity_provider_id))
    monkeypatch.setattr(discovery, '_append_codex_direct_accounts', lambda *_: None)
    monkeypatch.setattr(discovery, '_append_antigravity_cli_account', lambda *_: None)
    monkeypatch.setattr(discovery.shutil, 'which', lambda name: '/fake/claude' if name == 'claude' else None)
    monkeypatch.setattr(cli, '_discover_codex_session_accounts', lambda: ([], []))
    monkeypatch.setattr(cli, 'Config', type('ConfigLoader', (), {'load': staticmethod(lambda: config)}))
    for name in ('_migrate_v04_direct_sources_if_needed', '_migrate_provider_first_labels_if_needed',
                 '_migrate_codex_home_keys_if_needed', '_repair_codex_home_identity_drift_if_needed'):
        monkeypatch.setattr(cli, name, lambda config, **_: config)
    monkeypatch.setattr(cli, '_supervise_telegram_remote', lambda _: None)
    monkeypatch.setattr('tokenkick.stagger.daemon_step', lambda *_, **__: None)
    return candidate, config


def test_background_refreshes_refresh_claude_and_keep_failures_through_discovery(discovery_refresh, monkeypatch):
    from click.testing import CliRunner

    candidate, config = discovery_refresh
    key = account_key_string(candidate)
    attempts = []

    def capture(_):
        attempts.append(Clock.current)
        if len(attempts) in (2, 3):
            raise RuntimeError('HTTP 429 Too many requests')
        return 'usage fixture'

    monkeypatch.setattr(sources, '_capture_claude_usage', capture)
    monkeypatch.setattr(sources, '_parse_claude_usage_output', lambda label, _: replace(
        direct_status(), label=label, observed_at=Clock.current.strftime('%Y-%m-%dT%H:%M:%SZ')))

    def refresh():
        result = CliRunner().invoke(cli.cli, ['refresh-cache'])
        assert result.exit_code == 0, result.output
        return cli._load_status_cache_entries()[key]

    first = refresh()
    assert first['last_direct_success_at'] == STAMP
    Clock.current += timedelta(minutes=10)
    failure = refresh()
    assert len(attempts) == 2
    assert failure['last_direct_probe_error'].category == Category.RATE_LIMITED
    assert failure['direct_rate_limit_failures'] == 1
    assert failure['provider_observed_at'] == STAMP
    assert failure['refresh_error'] is not None
    assert 'rate-limited' in diagnostic_text(failure)
    Clock.current += timedelta(minutes=1)
    assert refresh()['direct_rate_limit_failures'] == 1
    assert len(attempts) == 2
    Clock.current += timedelta(minutes=4)
    assert refresh()['direct_rate_limit_failures'] == 2
    Clock.current += timedelta(minutes=10)
    recovered = refresh()
    assert len(attempts) == 4
    assert recovered['last_direct_probe_error'] is None
    assert recovered['direct_rate_limit_failures'] == 0
    assert recovered['refresh_error'] is None
    assert recovered['provider_observed_at'] == '2026-10-13T10:25:00Z'


def test_daemon_poll_obtains_claude_read_before_automation(discovery_refresh, monkeypatch):
    candidate, config = discovery_refresh
    calls = []
    monkeypatch.setattr(sources, '_capture_claude_usage', lambda _: calls.append('usage') or 'fixture')
    monkeypatch.setattr(sources, '_parse_claude_usage_output', lambda label, _: replace(direct_status(), label=label))

    def stop_after_refresh(*_, **__):
        entry = cli._load_status_cache_entries()[account_key_string(candidate)]
        assert entry['last_direct_success_at'] == STAMP
        assert calls == ['usage']
        raise RuntimeError('verified refreshed cache before automation')

    monkeypatch.setattr('tokenkick.status_rendering.notify_dormant_accounts', stop_after_refresh)
    with pytest.raises(RuntimeError, match='verified refreshed cache before automation'):
        cli._daemon_poll_once({}, False)


@pytest.mark.parametrize('scope', ['global', 'account'])
def test_background_discovery_honors_claude_monitoring_opt_out(discovery_refresh, monkeypatch, scope):
    from click.testing import CliRunner

    candidate, config = discovery_refresh
    if scope == 'global':
        config.claude.direct_usage_enabled = False
    else:
        config.accounts = [replace(candidate, direct_usage_enabled=False)]
    result = CliRunner().invoke(cli.cli, ['refresh-cache'])
    assert result.exit_code == 0, result.output
    entry = cli._load_status_cache_entries()[account_key_string(candidate)]
    assert entry['last_direct_probe_error'].category == Category.DISABLED
    assert entry['last_direct_probe_at'] is None
