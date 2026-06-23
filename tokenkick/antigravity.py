"""Shared Antigravity process detection helpers."""

from __future__ import annotations

import re
import shutil
import subprocess


def parse_process_line(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def is_language_server_command(command: str) -> bool:
    return bool(re.search(r"(?:^|[/\\])language_server(?:_macos)?(?:\s|$)", command.lower()))


def is_antigravity_language_server(command: str) -> bool:
    lower = command.lower()
    return is_language_server_command(lower) and (
        "--app_data_dir" in lower or "/antigravity/" in lower or "\\antigravity\\" in lower
    )


def lsof_binary() -> str | None:
    return next(
        (candidate for candidate in ["/usr/sbin/lsof", "/usr/bin/lsof", shutil.which("lsof")] if candidate),
        None,
    )


def parse_lsof_listening_ports(output: str) -> list[int]:
    ports: set[int] = set()
    for match in re.finditer(r":(\d+)\s+\(LISTEN\)", output):
        ports.add(int(match.group(1)))
    return sorted(ports)


def listening_ports_for_pid(pid: int, *, timeout_seconds: float = 2.0) -> list[int]:
    lsof = lsof_binary()
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_lsof_listening_ports(result.stdout)
