"""MCP client setup helpers.

This module configures external MCP clients to launch TokenKick's first-party
stdio server.  It deliberately does not import provider, daemon, schedule, kick,
or account mutation code: setup only reads/writes MCP client config files or
uses the Claude Code CLI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .state_io import atomic_write_text

try:  # pragma: no cover - exercised implicitly depending on Python version
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


MCP_SERVER_NAME = "tokenkick"
MCP_ARGS = ["mcp", "serve"]
MCP_TOOL_TIMEOUT_SECONDS = 180
MCP_STARTUP_TIMEOUT_SECONDS = 20
APP_SUPPORT_RELATIVE = Path("Library") / "Application Support" / "TokenKick"
MCP_HELPER_NAME = "tokenkick-mcp-helper.sh"
MCP_RUNTIME_PATH_NAME = "tokenkick-mcp-runtime-path"
CLIENTS = ("codex", "claude-desktop", "claude-code")
ClientName = Literal["codex", "claude-desktop", "claude-code"]
CLIENT_DISPLAY_NAMES = {
    "codex": "Codex",
    "claude-desktop": "Claude Desktop (Mac app)",
    "claude-code": "Claude Code (CLI)",
}


class MCPSetupError(RuntimeError):
    """Raised when MCP setup cannot safely inspect or mutate client config."""


@dataclass(frozen=True)
class MCPWriteResult:
    changed: bool
    backup_path: str | None = None


def normalize_mcp_client(value: str) -> ClientName:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "codex": "codex",
        "claude": "claude-desktop",
        "claude-desktop": "claude-desktop",
        "claude-code": "claude-code",
        "claude-cli": "claude-code",
    }
    try:
        return aliases[normalized]  # type: ignore[return-value]
    except KeyError as exc:
        raise MCPSetupError(f"Unknown MCP client: {value}") from exc


def normalize_mcp_clients(value: str | None) -> list[ClientName]:
    if value is None or value == "all":
        return list(CLIENTS)  # type: ignore[list-item]
    if value == "auto":
        return ["codex", "claude-desktop", "claude-code"]
    return [normalize_mcp_client(value)]


class MCPSetupManager:
    def __init__(
        self,
        *,
        home: Path | None = None,
        tk_path: str | None = None,
        claude_path: str | None = None,
        now: float | None = None,
        platform: str | None = None,
    ) -> None:
        self.home = home or Path.home()
        self.tk_path = _resolve_tk_path(tk_path)
        self.claude_path = claude_path if claude_path is not None else shutil.which("claude")
        self.now = now
        self.platform = platform or sys.platform

    @property
    def app_support_dir(self) -> Path:
        return self.home / APP_SUPPORT_RELATIVE

    @property
    def helper_path(self) -> Path:
        return self.app_support_dir / MCP_HELPER_NAME

    @property
    def helper_runtime_path(self) -> Path:
        return self.app_support_dir / MCP_RUNTIME_PATH_NAME

    def status(self, *, client: str | None = "all") -> dict[str, Any]:
        clients = [self._client_status(name) for name in normalize_mcp_clients(client)]
        return {
            "schema_version": 1,
            "server_name": MCP_SERVER_NAME,
            "canonical_command": {
                "command": self.tk_path,
                "args": MCP_ARGS,
                "env": {"TOKENKICK_TK_PATH": self.tk_path},
            },
            "helper": self.helper_status(),
            "clients": clients,
            "summary": _summary_for_clients(clients),
        }

    def doctor(self) -> dict[str, Any]:
        payload = self.status(client="all")
        checks = []
        for client in payload["clients"]:
            state = client["state"]
            level = "INFO"
            if state in {"malformed", "needs_repair"}:
                level = "WARN"
            elif state == "unsupported":
                level = "INFO"
            checks.append(
                {
                    "client": client["client"],
                    "level": level,
                    "state": state,
                    "message": client["message"],
                    "fix": client.get("recommended_action"),
                }
            )
        payload["checks"] = checks
        return payload

    def config_snippet(self, *, client: str, use_helper: bool = False) -> dict[str, Any]:
        name = normalize_mcp_client(client)
        config = self._server_config(use_helper=use_helper)
        if name == "codex":
            return {
                "client": name,
                "config_path": str(self.codex_config_path()),
                "format": "toml",
                "snippet": _codex_toml_block(config),
                "writes_file": False,
            }
        if name == "claude-desktop":
            return {
                "client": name,
                "config_path": str(self.claude_desktop_config_path()),
                "format": "json",
                "snippet": json.dumps({"mcpServers": {MCP_SERVER_NAME: config}}, indent=2),
                "writes_file": False,
            }
        return {
            "client": name,
            "config_path": None,
            "format": "argv",
            "snippet": " ".join(_claude_code_add_json_argv("claude", config)),
            "argv": _claude_code_add_json_argv(self.claude_path or "claude", config),
            "writes_file": False,
        }

    def install(
        self,
        *,
        client: str | None,
        use_helper: bool = False,
        repair_only: bool = False,
    ) -> dict[str, Any]:
        results = []
        helper_result = None
        if use_helper:
            helper_result = self.ensure_helper()
        for name in normalize_mcp_clients(client):
            results.append(
                self._install_client(
                    name,
                    use_helper=use_helper,
                    repair_only=repair_only,
                )
            )
        return {
            "schema_version": 1,
            "operation": "repair" if repair_only else "install",
            "helper": helper_result or self.helper_status(),
            "clients": results,
            "summary": _summary_for_clients(results),
        }

    def remove(self, *, client: str | None) -> dict[str, Any]:
        results = [self._remove_client(name) for name in normalize_mcp_clients(client)]
        return {
            "schema_version": 1,
            "operation": "remove",
            "clients": results,
            "summary": _summary_for_clients(results),
        }

    def ensure_helper(self) -> dict[str, Any]:
        self.app_support_dir.mkdir(parents=True, exist_ok=True)
        runtime_changed = _write_if_changed(self.helper_runtime_path, self.tk_path + "\n").changed
        script = _helper_script()
        script_result = _write_if_changed(self.helper_path, script)
        mode = self.helper_path.stat().st_mode
        self.helper_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        status_payload = self.helper_status()
        status_payload.update(
            {
                "changed": runtime_changed or script_result.changed,
                "script_backup_path": script_result.backup_path,
            }
        )
        return status_payload

    def helper_status(self) -> dict[str, Any]:
        configured_runtime = None
        try:
            configured_runtime = self.helper_runtime_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        return {
            "path": str(self.helper_path),
            "runtime_path_file": str(self.helper_runtime_path),
            "exists": self.helper_path.exists(),
            "runtime_path_exists": self.helper_runtime_path.exists(),
            "configured_runtime": configured_runtime,
            "expected_runtime": self.tk_path,
            "needs_repair": configured_runtime != self.tk_path or not self.helper_path.exists(),
        }

    def codex_config_path(self) -> Path:
        return self.home / ".codex" / "config.toml"

    def claude_desktop_config_path(self) -> Path:
        return self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"

    def _client_status(self, client: ClientName) -> dict[str, Any]:
        if client == "codex":
            return self._codex_status()
        if client == "claude-desktop":
            return self._claude_desktop_status()
        return self._claude_code_status()

    def _server_config(self, *, use_helper: bool) -> dict[str, Any]:
        if use_helper:
            return {
                "type": "stdio",
                "command": str(self.helper_path),
                "args": [],
            }
        return {
            "type": "stdio",
            "command": self.tk_path,
            "args": list(MCP_ARGS),
            "env": {"TOKENKICK_TK_PATH": self.tk_path},
        }

    def _install_client(
        self,
        client: ClientName,
        *,
        use_helper: bool,
        repair_only: bool,
    ) -> dict[str, Any]:
        if client == "codex":
            return self._install_codex(use_helper=use_helper, repair_only=repair_only)
        if client == "claude-desktop":
            return self._install_claude_desktop(use_helper=use_helper, repair_only=repair_only)
        return self._install_claude_code(use_helper=use_helper, repair_only=repair_only)

    def _remove_client(self, client: ClientName) -> dict[str, Any]:
        if client == "codex":
            return self._remove_codex()
        if client == "claude-desktop":
            return self._remove_claude_desktop()
        return self._remove_claude_code()

    def _codex_status(self) -> dict[str, Any]:
        path = self.codex_config_path()
        base = _base_client_status("codex", path=path)
        try:
            parsed, text = _load_toml_config(path)
        except MCPSetupError as exc:
            return _malformed_status(base, str(exc))
        entry = _codex_entry(parsed)
        block_present = _toml_tokenkick_block_present(text)
        return _status_from_entry(base, entry, block_present=block_present, manager=self)

    def _claude_desktop_status(self) -> dict[str, Any]:
        path = self.claude_desktop_config_path()
        base = _base_client_status("claude-desktop", path=path)
        if self.platform != "darwin":
            base.update(
                {
                    "supported": False,
                    "state": "unsupported",
                    "message": "Claude Desktop (Mac app) config is only supported on macOS.",
                    "recommended_action": "Use Claude Code (CLI) on this machine, or configure Claude Desktop on a Mac.",
                }
            )
            return base
        try:
            data = _load_json_config(path)
        except MCPSetupError as exc:
            return _malformed_status(base, str(exc))
        entry = _json_mcp_entry(data)
        return _status_from_entry(base, entry, block_present=True, manager=self)

    def _claude_code_status(self) -> dict[str, Any]:
        base = _base_client_status("claude-code", path=None)
        base["config_method"] = "Claude Code CLI (`claude mcp`)"
        base["claude_path"] = self.claude_path
        if not self.claude_path:
            base.update(
                {
                    "supported": False,
                    "state": "unsupported",
                    "message": "Claude Code CLI was not found on PATH.",
                    "recommended_action": "Install Claude Code, then run `tk mcp install --client claude-code --yes`.",
                }
            )
            return base
        check = _run_claude_cli([self.claude_path, "mcp", "list"], timeout=5)
        configured = "tokenkick" in (check.get("stdout") or "")
        base.update(
            {
                "state": "configured" if configured else "missing",
                "configured": configured,
                "message": (
                    "TokenKick MCP is listed by Claude Code."
                    if configured
                    else "TokenKick MCP is not listed by Claude Code."
                ),
                "recommended_action": None
                if configured
                else "Run `tk mcp install --client claude-code --yes`.",
                "probe": check,
            }
        )
        if check["returncode"] != 0:
            base.update(
                {
                    "state": "unknown",
                    "message": "Claude Code MCP status could not be read.",
                    "recommended_action": "Run `claude mcp list` for details.",
                }
            )
        return base

    def _install_codex(self, *, use_helper: bool, repair_only: bool) -> dict[str, Any]:
        path = self.codex_config_path()
        current = self._codex_status()
        if repair_only and current["state"] == "missing":
            return _skipped_result("codex", "No existing TokenKick MCP config to repair.", path=path)
        config = self._server_config(use_helper=use_helper)
        parsed, text = _load_toml_config(path)
        if _codex_entry(parsed) is not None and not _toml_tokenkick_block_present(text):
            raise MCPSetupError(
                "Codex config contains tokenkick in an unsupported inline TOML shape; "
                "use `tk mcp config-snippet --client codex` and edit it manually."
            )
        replacement = _replace_toml_tokenkick_block(text, _codex_toml_block(config))
        _validate_toml(replacement)
        write = _write_if_changed(path, replacement)
        return _write_result("codex", path=path, write=write, state=self._codex_status())

    def _install_claude_desktop(self, *, use_helper: bool, repair_only: bool) -> dict[str, Any]:
        path = self.claude_desktop_config_path()
        current = self._claude_desktop_status()
        if current.get("supported") is False:
            return current
        if repair_only and current["state"] == "missing":
            return _skipped_result("claude-desktop", "No existing TokenKick MCP config to repair.", path=path)
        data = _load_json_config(path)
        if not data:
            data = {}
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise MCPSetupError("Claude Desktop config field mcpServers must be an object.")
        servers[MCP_SERVER_NAME] = self._server_config(use_helper=use_helper)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        write = _write_if_changed(path, text)
        return _write_result("claude-desktop", path=path, write=write, state=self._claude_desktop_status())

    def _install_claude_code(self, *, use_helper: bool, repair_only: bool) -> dict[str, Any]:
        del repair_only
        if not self.claude_path:
            return self._claude_code_status()
        config = self._server_config(use_helper=use_helper)
        argv = _claude_code_add_json_argv(self.claude_path, config)
        result = _run_claude_cli(argv, timeout=30)
        return {
            "client": "claude-code",
            "state": "configured" if result["returncode"] == 0 else "failed",
            "changed": result["returncode"] == 0,
            "config_path": None,
            "command": argv,
            "message": (
                "Claude Code MCP config updated through `claude mcp add-json`."
                if result["returncode"] == 0
                else "Claude Code MCP config could not be updated."
            ),
            "probe": result,
        }

    def _remove_codex(self) -> dict[str, Any]:
        path = self.codex_config_path()
        if not path.exists():
            return _skipped_result("codex", "Codex config file does not exist.", path=path)
        parsed, text = _load_toml_config(path)
        if _codex_entry(parsed) is not None and not _toml_tokenkick_block_present(text):
            raise MCPSetupError(
                "Codex config contains tokenkick in an unsupported inline TOML shape; remove it manually."
            )
        replacement = _remove_toml_tokenkick_block(text)
        _validate_toml(replacement)
        write = _write_if_changed(path, replacement)
        return _write_result("codex", path=path, write=write, state=self._codex_status())

    def _remove_claude_desktop(self) -> dict[str, Any]:
        path = self.claude_desktop_config_path()
        if not path.exists():
            return _skipped_result(
                "claude-desktop",
                "Claude Desktop config file does not exist.",
                path=path,
            )
        data = _load_json_config(path)
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            servers.pop(MCP_SERVER_NAME, None)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        write = _write_if_changed(path, text)
        return _write_result("claude-desktop", path=path, write=write, state=self._claude_desktop_status())

    def _remove_claude_code(self) -> dict[str, Any]:
        if not self.claude_path:
            return self._claude_code_status()
        argv = [self.claude_path, "mcp", "remove", MCP_SERVER_NAME, "--scope", "user"]
        result = _run_claude_cli(argv, timeout=30)
        return {
            "client": "claude-code",
            "state": "removed" if result["returncode"] == 0 else "failed",
            "changed": result["returncode"] == 0,
            "config_path": None,
            "command": argv,
            "message": (
                "Claude Code MCP config removed through `claude mcp remove`."
                if result["returncode"] == 0
                else "Claude Code MCP config could not be removed."
            ),
            "probe": result,
        }


def _resolve_tk_path(value: str | None) -> str:
    candidate = value or os.environ.get("TOKENKICK_TK_PATH")
    if candidate:
        return str(Path(candidate).expanduser().resolve())
    if sys.argv and sys.argv[0]:
        try:
            argv_path = Path(sys.argv[0]).expanduser()
            if argv_path.exists():
                return str(argv_path.resolve())
        except OSError:
            pass
    found = shutil.which("tk")
    return str(Path(found).resolve()) if found else "tk"


def _base_client_status(client: ClientName, *, path: Path | None) -> dict[str, Any]:
    return {
        "client": client,
        "client_display": CLIENT_DISPLAY_NAMES[client],
        "server_name": MCP_SERVER_NAME,
        "supported": True,
        "configured": False,
        "state": "missing",
        "config_path": str(path) if path is not None else None,
        "config_exists": path.exists() if path is not None else None,
        "message": "TokenKick MCP is not configured.",
        "recommended_action": f"Run `tk mcp install --client {client} --yes`.",
        "entry": None,
        "issues": [],
    }


def _malformed_status(base: dict[str, Any], message: str) -> dict[str, Any]:
    base.update(
        {
            "state": "malformed",
            "message": message,
            "recommended_action": "Fix the malformed config file, then run `tk mcp repair`.",
            "issues": [message],
        }
    )
    return base


def _status_from_entry(
    base: dict[str, Any],
    entry: dict[str, Any] | None,
    *,
    block_present: bool,
    manager: MCPSetupManager,
) -> dict[str, Any]:
    if entry is None:
        return base
    issues = _entry_issues(entry, manager=manager)
    state = "configured" if not issues else "needs_repair"
    if not block_present:
        state = "needs_repair"
        issues.append("TokenKick config exists in an unsupported inline shape.")
    base.update(
        {
            "configured": True,
            "state": state,
            "entry": entry,
            "issues": issues,
            "message": "TokenKick MCP is configured." if state == "configured" else "TokenKick MCP needs repair.",
            "recommended_action": None if state == "configured" else f"Run `tk mcp repair --client {base['client']} --yes`.",
        }
    )
    return base


def _entry_issues(entry: dict[str, Any], *, manager: MCPSetupManager) -> list[str]:
    issues: list[str] = []
    command = entry.get("command")
    args = entry.get("args")
    env = entry.get("env")
    helper_path = str(manager.helper_path)
    if command == helper_path:
        if args not in (None, []):
            issues.append("Helper-based config should not pass extra args.")
        helper = manager.helper_status()
        if helper["needs_repair"]:
            issues.append("Stable helper runtime path is stale or missing.")
        return issues
    if command != manager.tk_path:
        issues.append("Configured command does not match the current TokenKick runtime.")
    if args != MCP_ARGS:
        issues.append("Configured args should be ['mcp', 'serve'].")
    if not isinstance(env, dict) or env.get("TOKENKICK_TK_PATH") != manager.tk_path:
        issues.append("TOKENKICK_TK_PATH should point to the same runtime.")
    if isinstance(command, str) and command.startswith("/") and not Path(command).exists():
        issues.append("Configured command path does not exist.")
    return issues


def _summary_for_clients(clients: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"configured": 0, "missing": 0, "needs_repair": 0, "malformed": 0, "unsupported": 0, "other": 0}
    for client in clients:
        state = str(client.get("state") or "other")
        counts[state if state in counts else "other"] += 1
    return counts


def _load_toml_config(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        raise MCPSetupError(f"Could not read Codex config: {exc}") from exc
    try:
        parsed = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as exc:
        raise MCPSetupError(f"Codex config is not valid TOML: {exc}") from exc
    return parsed, text


def _validate_toml(text: str) -> None:
    try:
        tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as exc:
        raise MCPSetupError(f"Generated Codex config would be invalid TOML: {exc}") from exc


def _codex_entry(parsed: dict[str, Any]) -> dict[str, Any] | None:
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(MCP_SERVER_NAME)
    return entry if isinstance(entry, dict) else None


def _toml_tokenkick_block_present(text: str) -> bool:
    return any(_toml_section_name(line) == f"mcp_servers.{MCP_SERVER_NAME}" for line in text.splitlines())


def _replace_toml_tokenkick_block(text: str, block: str) -> str:
    stripped = _remove_toml_tokenkick_block(text).rstrip()
    if stripped:
        return stripped + "\n\n" + block
    return block


def _remove_toml_tokenkick_block(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        section = _toml_section_name(line)
        if section is not None:
            skipping = section == f"mcp_servers.{MCP_SERVER_NAME}" or section.startswith(
                f"mcp_servers.{MCP_SERVER_NAME}."
            )
        if not skipping:
            output.append(line)
    return "\n".join(output).rstrip() + ("\n" if output else "")


def _toml_section_name(line: str) -> str | None:
    match = re.match(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$", line)
    return match.group(1) if match else None


def _codex_toml_block(config: dict[str, Any]) -> str:
    lines = [
        f"[mcp_servers.{MCP_SERVER_NAME}]",
        f"command = {json.dumps(config['command'])}",
        "args = [" + ", ".join(json.dumps(item) for item in config.get("args", [])) + "]",
        f"startup_timeout_sec = {MCP_STARTUP_TIMEOUT_SECONDS}",
        f"tool_timeout_sec = {MCP_TOOL_TIMEOUT_SECONDS}",
    ]
    env = config.get("env")
    if isinstance(env, dict) and env:
        lines.extend(["", f"[mcp_servers.{MCP_SERVER_NAME}.env]"])
        for key, value in sorted(env.items()):
            lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _load_json_config(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise MCPSetupError(f"Could not read Claude Desktop config: {exc}") from exc
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise MCPSetupError(f"Claude Desktop config is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MCPSetupError("Claude Desktop config must be a JSON object.")
    return data


def _json_mcp_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(MCP_SERVER_NAME)
    return entry if isinstance(entry, dict) else None


def _write_if_changed(path: Path, text: str) -> MCPWriteResult:
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if current == text:
        return MCPWriteResult(changed=False)
    backup_path = None
    if current is not None:
        backup = path.with_name(f"{path.name}.tokenkick-backup-{_timestamp()}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(current, encoding="utf-8")
        backup_path = str(backup)
    atomic_write_text(path, text)
    return MCPWriteResult(changed=True, backup_path=backup_path)


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-{time.time_ns() % 1_000_000_000:09d}"


def _write_result(
    client: ClientName,
    *,
    path: Path,
    write: MCPWriteResult,
    state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "client": client,
        "config_path": str(path),
        "changed": write.changed,
        "backup_path": write.backup_path,
        "state": state["state"],
        "message": (
            f"{client} MCP config updated."
            if write.changed
            else f"{client} MCP config already matched the requested state."
        ),
        "status": state,
    }


def _skipped_result(client: ClientName, message: str, *, path: Path) -> dict[str, Any]:
    return {
        "client": client,
        "config_path": str(path),
        "changed": False,
        "state": "skipped",
        "message": message,
    }


def _helper_script() -> str:
    return """#!/bin/sh
set -eu
RUNTIME_PATH_FILE="${HOME}/Library/Application Support/TokenKick/tokenkick-mcp-runtime-path"
if [ ! -r "$RUNTIME_PATH_FILE" ]; then
  echo "TokenKick MCP runtime path is missing: $RUNTIME_PATH_FILE" >&2
  exit 127
fi
RUNTIME="$(cat "$RUNTIME_PATH_FILE")"
if [ ! -x "$RUNTIME" ]; then
  echo "TokenKick MCP runtime is not executable: $RUNTIME" >&2
  exit 127
fi
export TOKENKICK_TK_PATH="$RUNTIME"
exec "$RUNTIME" mcp serve
"""


def _claude_code_add_json_argv(claude_path: str, config: dict[str, Any]) -> list[str]:
    return [
        claude_path,
        "mcp",
        "add-json",
        "--scope",
        "user",
        MCP_SERVER_NAME,
        json.dumps(config, separators=(",", ":")),
    ]


def _run_claude_cli(argv: list[str], *, timeout: float = 30) -> dict[str, Any]:
    env = dict(os.environ)
    env["TK_NO_INTERACTIVE"] = "1"
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except OSError as exc:
        return {
            "argv": argv,
            "returncode": 127,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "returncode": 124,
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": "Claude Code command timed out.",
        }
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
