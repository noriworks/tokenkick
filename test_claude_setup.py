"""Tests for Claude CLI onboarding helpers."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from tokenkick.claude_setup import (
    claude_probe_git_present,
    claude_settings_path,
    ensure_claude_cli_settings,
    ensure_claude_probe_ready,
)


def test_ensure_claude_probe_ready_runs_git_init_when_missing(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        (tmp_path / "claude-probe" / ".git").mkdir()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("tokenkick.claude_setup.subprocess.run", fake_run)

    path = ensure_claude_probe_ready(tmp_path)

    assert path == tmp_path / "claude-probe"
    assert claude_probe_git_present(tmp_path)
    assert calls[0][0] == ["git", "init"]
    assert calls[0][1]["cwd"] == str(path)


def test_ensure_claude_probe_ready_is_idempotent(monkeypatch, tmp_path):
    (tmp_path / "claude-probe" / ".git").mkdir(parents=True)
    calls = []
    monkeypatch.setattr("tokenkick.claude_setup.subprocess.run", lambda *args, **kwargs: calls.append(args))

    path = ensure_claude_probe_ready(tmp_path)

    assert path == tmp_path / "claude-probe"
    assert calls == []


def test_ensure_claude_probe_ready_ignores_git_failure(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git", "init"], timeout=10)

    monkeypatch.setattr("tokenkick.claude_setup.subprocess.run", fail)

    path = ensure_claude_probe_ready(tmp_path)

    assert path == tmp_path / "claude-probe"
    assert path.exists()


def test_ensure_claude_cli_settings_writes_defaults_when_missing(tmp_path):
    path = ensure_claude_cli_settings(tmp_path)

    assert path == claude_settings_path(tmp_path)
    assert path.read_text() == '{\n  "theme": "dark"\n}\n'


def test_ensure_claude_cli_settings_does_not_overwrite_existing_settings(tmp_path):
    path = claude_settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"theme":"light","custom":true}\n')

    returned = ensure_claude_cli_settings(tmp_path)

    assert returned == path
    assert path.read_text() == '{"theme":"light","custom":true}\n'
