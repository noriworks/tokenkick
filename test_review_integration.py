"""Boundary regressions: real processes/CLI, synthetic state, no provider calls."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from tokenkick import cli, models, scheduling, sources, stagger, status_cache
from tokenkick.models import AccountConfig, AccountStatus, AccountState
from tokenkick.mcp_core import TokenKickMCPCore
from test_review_state import state as state


def pending(account, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = scheduling.to_utc_iso
    return scheduling.PendingKick(
        models.account_key_string(account),
        account.label,
        account.provider,
        stamp(now),
        stamp(now),
        "orchestrated",
        1,
        0,
        "none",
        stamp(now),
        stamp(now + timedelta(hours=4)),
    )


def test_old_completion_cannot_remove_or_fail_replacement(state):
    a = AccountConfig("fixture", "claude")
    old = pending(a)
    new = replace(
        old, kick_at=scheduling.to_utc_iso(datetime.now(timezone.utc) + timedelta(minutes=15))
    )
    scheduling.save_pending_kicks({new.account_key: new})
    assert scheduling.remove_pending_kick(a, expected=old) is None
    assert scheduling.record_pending_kick_failure(a, "old request failed", expected=old) is None
    assert scheduling.load_pending_kicks()[new.account_key] == new
    assert scheduling.remove_pending_kick(a, expected=new) == new


def test_old_refresh_owner_cannot_release_new_owner(state, monkeypatch):
    assert status_cache._acquire_status_refresh_lock()
    path = cli.STATUS_CACHE_REFRESH_LOCK_FILE
    path.write_text(f"{os.getpid()}\nreplacement-token\n")
    status_cache._release_status_refresh_lock()
    assert path.exists()
    monkeypatch.setenv("TK_REFRESH_LOCK_TOKEN", "replacement-token")
    status_cache._release_status_refresh_lock()
    assert not path.exists()


def test_newer_native_read_wins_over_recent_jsonl():
    now = datetime.now(timezone.utc)
    native = AccountStatus(
        "fixture",
        AccountState.ACTIVE,
        source_detail="codex-app-server",
        session_window_minutes=300,
        session_used_percent=0.01,
        session_resets_at=now.timestamp() + 18000,
        observed_at=now.isoformat(),
    )
    jsonl = replace(
        native, session_used_percent=40, observed_at=(now - timedelta(seconds=1)).isoformat()
    )
    assert not sources._codex_session_status_should_override_appserver(native, jsonl)


def test_stagger_refuses_older_running_daemon(state):
    (state / "daemon.pid").write_text(f"{os.getpid()} 1.22.0\n")
    with pytest.raises(stagger.StaggerError, match="capabilities"):
        stagger.require_compatible_daemon(state)
    from tokenkick.versioning import write_daemon_pidfile

    write_daemon_pidfile(state / "daemon.pid", os.getpid())
    stagger.require_compatible_daemon(state)


def test_real_cli_mcp_propagates_diagnostic_and_pending_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tokenkick").mkdir()
    (tmp_path / ".tokenkick/config.json").write_text("{bad")
    # CI installs into its active interpreter, without a repository-local .venv.
    core = TokenKickMCPCore(tk_path=sys.executable)
    result = core._run(["-m", "tokenkick", "app", "doctor"], risk="read_only", app_mode=True)
    assert result["ok"]  # Doctor successfully reports diagnostic failures.
    assert "not valid json" in json.dumps(result).lower()
    result = core._run(["-m", "tokenkick", "app", "snapshot"], risk="read_only", app_mode=True)
    assert not result["ok"]


def test_python_background_refresh_writes_cache_and_releases_lock(tmp_path, monkeypatch):
    directory = tmp_path / ".tokenkick"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps({
        "accounts": [{"label": "fixture", "provider": "gemini", "source": "manual"}],
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "CONFIG_DIR", directory)
    monkeypatch.setattr(cli, "DAEMON_LOG_FILE", directory / "daemon.log")
    lock = directory / "status-cache-refresh.pid"
    monkeypatch.setattr(cli, "STATUS_CACHE_REFRESH_LOCK_FILE", lock)
    assert status_cache._start_background_status_refresh()
    deadline = time.monotonic() + 15
    cache = directory / "status-cache.json"
    while time.monotonic() < deadline:
        if cache.exists() and not lock.exists():
            assert json.loads(cache.read_text())
            return
        time.sleep(0.05)
    pytest.fail("Python background refresh did not write its cache and release the lock")


def test_mcp_timeout_terminates_owned_descendant(tmp_path):
    pidfile = tmp_path / "child.pid"
    heartbeat = tmp_path / "heartbeat"
    # The grandchild ignores TERM, exercising the process-group KILL fallback.
    child_code = (
        "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        + f"p=Path({str(heartbeat)!r}); "
        + "exec('while True:\\n p.write_text(str(time.time_ns()))\\n time.sleep(.02)')"
    )
    code = (
        f"import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"open({str(pidfile)!r},'w').write(str(p.pid)); time.sleep(30)"
    )
    result = TokenKickMCPCore(tk_path=sys.executable)._run(
        ["-c", code], risk="quota_consuming", timeout=0.5
    )
    assert result["error"]["code"] == "outcome_unknown"
    assert int(pidfile.read_text()) > 0
    before = heartbeat.read_text()
    time.sleep(0.1)
    assert heartbeat.read_text() == before


@pytest.mark.parametrize(
    "args",
    [
        ["wake", "missing"],
        ["reset-log", "nonsense"],
        ["reset-log", "ack"],
        ["schedule", "set", "--weekdays", "09:00-17:00"],
    ],
)
def test_human_cli_validation_is_nonzero(state, args):
    result = CliRunner().invoke(cli.cli, args)
    assert result.exit_code != 0


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path("scripts") / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_systemd_renderer_uses_actual_install_paths():
    render = load_script("render-systemd-unit").render
    unit = render("fixture", "/srv/fixture", "/srv/fixture/.local/bin/tk")
    assert "HOME=/srv/fixture" in unit and 'ExecStart="/srv/fixture/.local/bin/tk" daemon' in unit
    assert "TimeoutStopSec=360" in unit
    with pytest.raises(ValueError):
        render("fixture", "/srv/%i", "/bin/tk")


def test_dirty_export_is_refused_before_destination_creation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "private").write_text("untracked")
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="clean"):
        load_script("create-public-export").export(root, destination)
    assert not destination.exists()


def test_dormant_notification_is_claimed_before_send(state, monkeypatch):
    from tokenkick.status_rendering import notify_dormant_accounts
    from tokenkick.models import Config

    a = AccountConfig("fixture", "codex")
    observed = AccountStatus(a.label, AccountState.FRESH, stale=True)
    sent = []
    monkeypatch.setattr(cli, "notify_dormant_account_for_account", lambda *args: sent.append(args))
    notify_dormant_accounts([observed], {a.label: a}, Config())
    notify_dormant_accounts([observed], {a.label: a}, Config())
    assert len(sent) == 1


def test_poison_update_is_acknowledged_and_old_authorization_revoked(tmp_path):
    from tokenkick.telegram_remote import TelegramRemoteListener, load_last_update_id
    from test_telegram_remote import FakeClient, StopLoop

    update = {"update_id": 42, "message": {"chat": {"id": 7}, "text": "/refresh"}}
    client = FakeClient([update])
    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="7",
        state_file=tmp_path / "telegram",
        status_runner=lambda: None,
    )
    listener.handle_update = lambda _: (_ for _ in ()).throw(ValueError("bad payload"))
    with pytest.raises(StopLoop):
        listener.run_forever()
    assert load_last_update_id(tmp_path / "telegram") == 42
    restarted = TelegramRemoteListener(
        client=FakeClient(),
        allowed_chat_id="7",
        state_file=tmp_path / "telegram",
        status_runner=lambda: pytest.fail("revoked chat must not run"),
        authorization_current=lambda: False,
    )
    assert restarted.next_offset() == 43
    restarted.run_forever()
    assert not restarted.client.get_updates_calls


def test_burst_stops_after_ambiguous_dispatch_and_does_not_record_refusal(state, monkeypatch):
    from tokenkick.models import Config, KickEvent

    a = AccountConfig("fixture", "codex")
    monkeypatch.setattr(
        cli, "_effective_codex_burst_ladder_surfaces", lambda *a: ("repo", "legacy")
    )
    monkeypatch.setattr(cli, "_codex_burst_ladder_gap_seconds", lambda *a: 0)
    sent = []
    recorded = []

    def attempt(*args, **kwargs):
        sent.append(kwargs["codex_surface"])
        return KickEvent("fixture", success=False, confirmed=False, dispatch_state="uncertain")

    monkeypatch.setattr(cli, "_run_kick_attempt", attempt)
    monkeypatch.setattr(cli, "append_kick_event", recorded.append)
    cli._kick_codex_fire_all_surfaces(
        a, Config(), pre_status=None, daemon_log=False, kick_type="kick", model_override=None
    )
    assert sent == ["repo"] and len(recorded) == 1
    cli._record_codex_fire_all_cluster(
        [KickEvent("fixture", dispatch_state="not_sent")], daemon_log=False
    )
    assert len(recorded) == 1


def test_release_verifier_rejects_tag_or_artifact_version_mismatch(tmp_path):
    import zipfile

    module = load_script("verify-release-artifacts")
    (tmp_path / "tokenkick").mkdir()
    (tmp_path / "tokenkick/__init__.py").write_text('__version__="1.2.3"')
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.2.3"')
    with pytest.raises(ValueError, match="Tag"):
        module.verify("1.2.4", [], tmp_path)
    wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tokenkick-1.2.3.dist-info/METADATA", "Name: tokenkick\nVersion: 1.2.2\n")
    with pytest.raises(ValueError, match="metadata mismatch"):
        module.verify("1.2.3", [wheel], tmp_path)


def test_dmg_layout_failure_detaches_owned_mount_and_preserves_other_files(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for name, body in {
        "hdiutil": 'echo "$@" >> "$FIXTURE_LOG"\nexit 0',
        "osascript": "exit 1",
    }.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    app = tmp_path / "Fixture.app"
    (app / "Contents").mkdir(parents=True)
    unrelated = tmp_path / "tokenkick-dmg-1.22.0"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    result = subprocess.run(
        ["sh", "scripts/create-local-dmg.sh", str(app)],
        env={
            **os.environ,
            "PATH": str(bin_dir) + ":" + os.environ["PATH"],
            "PYTHON": sys.executable,
            "TMPDIR": str(tmp_path),
            "OUTPUT_DIR": str(tmp_path / "output"),
            "FIXTURE_LOG": str(log),
            "DMG_FINDER_LAYOUT": "1",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert any(call.startswith("detach ") for call in calls)
    assert (unrelated / "keep").read_text() == "keep"
    assert not list(tmp_path.glob("tokenkick-dmg.*"))


def test_concurrent_cancellation_is_not_resurrected_by_invalidation(state):
    a, b = AccountConfig("A", "claude"), AccountConfig("B", "claude")
    scheduling.save_pending_kicks({models.account_key_string(x): pending(x) for x in (a, b)})
    ready = state / "read-ready"
    code = """import sys,time
from pathlib import Path
from tokenkick import cli,scheduling
from tokenkick.models import AccountConfig
scheduling.PENDING_KICKS_FILE=Path(sys.argv[1])
original=cli.load_pending_kicks
def slow_read(*args,**kwargs):
    data=original(*args,**kwargs)
    Path(sys.argv[2]).write_text('ready')
    time.sleep(.4)
    return data
cli.load_pending_kicks=slow_read
cli._invalidate_orchestrated_pending_for_account(AccountConfig('A','claude'))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(scheduling.PENDING_KICKS_FILE), str(ready)],
        env={**os.environ, "HOME": str(state)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        scheduling.remove_pending_kick(b)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err.decode()
        assert scheduling.load_pending_kicks() == {}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_independent_work_windows_are_conflicts():
    from tokenkick.orchestration import build_pending_kick_diff
    from tokenkick.orchestration import PlannedKick

    a = AccountConfig("fixture", "claude")
    old = pending(a)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    kick = PlannedKick(
        account_key=old.account_key,
        account_label=a.label,
        provider="claude",
        kick_at=start,
        work_start=start,
        work_end=start + timedelta(hours=3),
        segment_start=start,
        segment_end=start + timedelta(hours=2),
        usable_session_minutes=120,
    )
    diff = build_pending_kick_diff([kick], {old.account_key: old})
    assert diff.conflicts_unmanaged and not diff.replaces_orchestrated
