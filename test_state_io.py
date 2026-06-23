"""Concurrency tests for TokenKick state-file persistence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from tokenkick.models import AccountConfig, Config, DataSource
from tokenkick.state_io import state_file_lock


def _run_processes(script: str, args: list[list[str]], *, cwd: str) -> None:
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, *process_args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for process_args in args
    ]
    failures: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        if process.returncode != 0:
            failures.append(f"exit={process.returncode}\nstdout={stdout}\nstderr={stderr}")
    assert not failures, "\n".join(failures)


def test_history_appends_from_multiple_processes(tmp_path):
    history_file = tmp_path / "history.jsonl"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        import tokenkick.models as models
        from tokenkick.models import KickEvent, append_kick_event

        models.CONFIG_DIR = Path(sys.argv[1]).parent
        models.HISTORY_FILE = Path(sys.argv[1])
        label = sys.argv[2]
        append_kick_event(KickEvent(label=label, timestamp=float(sys.argv[3]), success=True))
        """
    )

    labels = [f"account-{index}" for index in range(12)]
    _run_processes(
        script,
        [[str(history_file), label, str(index)] for index, label in enumerate(labels)],
        cwd=os.getcwd(),
    )

    events = [json.loads(line) for line in history_file.read_text().splitlines()]
    assert sorted(event["label"] for event in events) == sorted(labels)


def test_config_saves_from_multiple_processes_produce_complete_json(tmp_path):
    config_file = tmp_path / "config.json"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        import tokenkick.models as models
        from tokenkick.models import AccountConfig, Config, DataSource

        models.CONFIG_DIR = Path(sys.argv[1]).parent
        models.CONFIG_FILE = Path(sys.argv[1])
        label = sys.argv[2]
        Config(accounts=[AccountConfig(label=label, source=DataSource.MANUAL)]).save()
        """
    )

    labels = [f"config-{index}" for index in range(12)]
    _run_processes(script, [[str(config_file), label] for label in labels], cwd=os.getcwd())

    data = json.loads(config_file.read_text())
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["label"] in labels


def test_pending_kicks_upsert_from_multiple_processes(tmp_path):
    pending_file = tmp_path / "pending-kicks.json"
    script = textwrap.dedent(
        """
        import sys
        from datetime import datetime, timezone
        from pathlib import Path

        import tokenkick.scheduling as scheduling
        from tokenkick.models import AccountConfig, DataSource
        from tokenkick.scheduling import (
            ScheduleDecision,
            ScheduleReason,
            WasteLocation,
            upsert_pending_kick,
        )

        scheduling.PENDING_KICKS_FILE = Path(sys.argv[1])
        label = sys.argv[2]
        base = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
        decision = ScheduleDecision(
            kick_at=base,
            reason=ScheduleReason.OPTIMAL,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location=WasteLocation.NONE,
            work_start=base,
            work_end=base,
            optimal_kick_at=base,
        )
        account = AccountConfig(label=label, provider="codex", source=DataSource.MANUAL)
        upsert_pending_kick(account, decision, now=base)
        """
    )

    labels = [f"pending-{index}" for index in range(12)]
    _run_processes(script, [[str(pending_file), label] for label in labels], cwd=os.getcwd())

    data = json.loads(pending_file.read_text())
    assert sorted(value["account_label"] for value in data.values()) == sorted(labels)


def test_status_cache_saves_from_multiple_processes(tmp_path):
    cache_file = tmp_path / "status-cache.json"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        from tokenkick import cli
        from tokenkick.models import AccountConfig, AccountState, AccountStatus, DataSource, account_key_string
        from tokenkick.status_cache import _save_status_cache

        cli.CONFIG_DIR = Path(sys.argv[1]).parent
        cli.STATUS_CACHE_FILE = Path(sys.argv[1])
        label = sys.argv[2]
        account = AccountConfig(
            label=label,
            provider="codex",
            source=DataSource.CODEX_DIRECT,
            identity_provider_id=label,
        )
        status = AccountStatus(
            label=label,
            state=AccountState.FRESH,
            used_percent=0.0,
            observed_at="2026-05-25T12:00:00Z",
        )
        _save_status_cache([account], {account_key_string(account): status})
        """
    )

    labels = [f"cache-{index}" for index in range(12)]
    _run_processes(script, [[str(cache_file), label] for label in labels], cwd=os.getcwd())

    data = json.loads(cache_file.read_text())
    assert data["version"] == 2
    assert sorted(entry["account"]["label"] for entry in data["accounts"].values()) == sorted(labels)


def test_config_save_keeps_previous_file_when_atomic_replace_fails(tmp_path, monkeypatch):
    import tokenkick.models as models
    import tokenkick.state_io as state_io

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(models, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models, "CONFIG_FILE", config_file)
    Config(accounts=[AccountConfig(label="old", source=DataSource.MANUAL)]).save()
    original = config_file.read_text()

    def fail_replace(_source, _target):
        raise OSError("simulated interrupted replace")

    monkeypatch.setattr(state_io.os, "replace", fail_replace)

    with pytest.raises(OSError):
        Config(accounts=[AccountConfig(label="new", source=DataSource.MANUAL)]).save()

    assert config_file.read_text() == original
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_state_file_lock_times_out_under_contention(tmp_path):
    path = tmp_path / "state.json"
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from tokenkick.state_io import StateFileLockTimeout, state_file_lock

        try:
            with state_file_lock(Path(sys.argv[1]), timeout=0.1, poll_interval=0.01):
                print("acquired")
        except StateFileLockTimeout:
            print("timeout")
        """
    )

    with state_file_lock(path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=os.getcwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=True,
        )

    assert result.stdout.strip() == "timeout"
