from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from tokenkick.cli import cli
from tokenkick.mcp_setup import MCPSetupError, MCPSetupManager


def _fake_executable(path: Path, body: str = "") -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _manager(tmp_path: Path, *, platform: str = "darwin") -> MCPSetupManager:
    tk = _fake_executable(tmp_path / "tk", 'printf "%s\\n" "$@" >> "$HOME/tk.log"\n')
    return MCPSetupManager(home=tmp_path, tk_path=str(tk), claude_path=None, platform=platform)


def test_codex_install_preserves_unrelated_servers_and_creates_backup(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            """\
            # Keep this comment
            [mcp_servers.other]
            command = "other"
            args = ["serve"]
            """
        ),
        encoding="utf-8",
    )

    result = manager.install(client="codex")

    assert result["clients"][0]["changed"] is True
    backup = result["clients"][0]["backup_path"]
    assert backup and Path(backup).exists()
    text = path.read_text(encoding="utf-8")
    assert "# Keep this comment" in text
    assert "[mcp_servers.other]" in text
    assert "[mcp_servers.tokenkick]" in text
    assert 'args = ["mcp", "serve"]' in text
    assert "TOKENKICK_TK_PATH" in text
    assert manager.status(client="codex")["clients"][0]["state"] == "configured"


def test_codex_repair_updates_stale_runtime_path(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            """\
            [mcp_servers.tokenkick]
            command = "/old/tk"
            args = ["mcp", "serve"]

            [mcp_servers.tokenkick.env]
            TOKENKICK_TK_PATH = "/old/tk"
            """
        ),
        encoding="utf-8",
    )

    assert manager.status(client="codex")["clients"][0]["state"] == "needs_repair"

    result = manager.install(client="codex", repair_only=True)

    assert result["clients"][0]["changed"] is True
    assert str(tmp_path / "tk") in path.read_text(encoding="utf-8")
    assert manager.status(client="codex")["clients"][0]["state"] == "configured"


def test_codex_remove_only_removes_tokenkick_block(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            """\
            [mcp_servers.other]
            command = "other"

            [mcp_servers.tokenkick]
            command = "/old/tk"
            args = ["mcp", "serve"]
            """
        ),
        encoding="utf-8",
    )

    result = manager.remove(client="codex")

    assert result["clients"][0]["changed"] is True
    text = path.read_text(encoding="utf-8")
    assert "[mcp_servers.other]" in text
    assert "tokenkick" not in text


def test_codex_malformed_config_is_refused_and_project_local_config_is_ignored(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("not = [valid\n", encoding="utf-8")
    local = tmp_path / "project" / ".codex" / "config.toml"
    local.parent.mkdir(parents=True)
    local.write_text("[mcp_servers.local]\ncommand = \"local\"\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "project")

    with pytest.raises(MCPSetupError, match="not valid TOML"):
        manager.install(client="codex")

    assert path.read_text(encoding="utf-8") == "not = [valid\n"
    assert "local" in local.read_text(encoding="utf-8")


def test_claude_desktop_json_preserves_unknown_keys_and_refuses_invalid_json(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "globalShortcut": "x",
                "mcpServers": {"other": {"command": "other", "args": []}},
            }
        ),
        encoding="utf-8",
    )

    result = manager.install(client="claude-desktop")

    assert result["clients"][0]["changed"] is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["globalShortcut"] == "x"
    assert "other" in data["mcpServers"]
    assert data["mcpServers"]["tokenkick"]["args"] == ["mcp", "serve"]

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MCPSetupError, match="not valid JSON"):
        manager.install(client="claude-desktop")


def test_claude_code_uses_cli_and_never_edits_claude_json(tmp_path):
    log = tmp_path / "claude-argv.jsonl"
    claude = _fake_executable(
        tmp_path / "claude",
        "python3 -c "
        + repr(
            f"import json, sys; open({str(log)!r}, 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')"
        )
        + ' "$@"\n'
        "exit 0\n",
    )
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text('{"keep": true}\n', encoding="utf-8")
    manager = MCPSetupManager(home=tmp_path, tk_path=str(_fake_executable(tmp_path / "tk")), claude_path=str(claude))

    result = manager.install(client="claude-code")

    assert result["clients"][0]["changed"] is True
    assert claude_json.read_text(encoding="utf-8") == '{"keep": true}\n'
    argv = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert argv[:5] == ["mcp", "add-json", "--scope", "user", "tokenkick"]
    config = json.loads(argv[5])
    assert config["command"] == str(tmp_path / "tk")
    assert config["args"] == ["mcp", "serve"]


def test_config_snippet_writes_nothing(tmp_path):
    manager = _manager(tmp_path)

    snippet = manager.config_snippet(client="codex")

    assert snippet["writes_file"] is False
    assert "[mcp_servers.tokenkick]" in snippet["snippet"]
    assert not (tmp_path / ".codex").exists()


def test_helper_reads_runtime_path_and_executes_tk_mcp_serve(tmp_path):
    log = tmp_path / "tk.log"
    tk = _fake_executable(tmp_path / "tk", f'printf "%s\\n" "$@" >> {log!s}\n')
    manager = MCPSetupManager(home=tmp_path, tk_path=str(tk))

    helper = manager.ensure_helper()

    assert helper["changed"] is True
    assert manager.helper_runtime_path.read_text(encoding="utf-8").strip() == str(tk)
    completed = subprocess.run(
        [str(manager.helper_path)],
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == ["mcp", "serve"]

    moved = _fake_executable(tmp_path / "tk-new")
    repaired = MCPSetupManager(home=tmp_path, tk_path=str(moved)).ensure_helper()
    assert repaired["configured_runtime"] == str(moved)


def test_cli_mutations_require_yes_and_json_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(cli, ["mcp", "install", "--client", "codex", "--json-output"])

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_cli_install_codex_with_yes_writes_global_home_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(cli, ["mcp", "install", "--client", "codex", "--yes", "--json-output"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert not (tmp_path / ".claude.json").exists()


def test_app_mcp_status_uses_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(cli, ["app", "mcp-status"], env={"TK_APP_MODE": "1"})

    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["payload"]["server_name"] == "tokenkick"
    assert {client["client"] for client in envelope["payload"]["clients"]} >= {"codex", "claude-desktop"}


def test_status_uses_clear_claude_display_names(tmp_path):
    payload = MCPSetupManager(
        home=tmp_path,
        tk_path=str(_fake_executable(tmp_path / "tk")),
        claude_path=None,
        platform="linux",
    ).status(client="all")

    by_client = {client["client"]: client for client in payload["clients"]}
    assert by_client["claude-desktop"]["client_display"] == "Claude Desktop (Mac app)"
    assert by_client["claude-desktop"]["state"] == "unsupported"
    assert "Mac app" in by_client["claude-desktop"]["message"]
    assert by_client["claude-code"]["client_display"] == "Claude Code (CLI)"
    assert "Claude Code CLI" in by_client["claude-code"]["config_method"]


def test_app_mcp_install_requires_yes_and_stays_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["app", "mcp-install", "--client", "codex"],
        env={"TK_APP_MODE": "1"},
    )

    assert result.exit_code == 2
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["payload"]["read_only"] is True
    assert not (tmp_path / ".codex" / "config.toml").exists()
