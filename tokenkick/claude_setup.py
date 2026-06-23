"""Claude CLI setup helpers for TokenKick onboarding and diagnostics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def claude_probe_dir(config_dir: Path) -> Path:
    return config_dir / "claude-probe"


def claude_probe_git_present(config_dir: Path) -> bool:
    return (claude_probe_dir(config_dir) / ".git").exists()


def claude_settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def claude_settings_present(home: Path) -> bool:
    return claude_settings_path(home).exists()


def ensure_claude_probe_ready(config_dir: Path) -> Path:
    """Prepare the Claude probe directory for non-interactive Claude CLI use."""
    path = claude_probe_dir(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    if claude_probe_git_present(config_dir):
        return path
    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return path


def ensure_claude_cli_settings(home: Path) -> Path:
    """Write minimal Claude CLI settings when the user has no settings file yet."""
    path = claude_settings_path(home)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n")
    return path
