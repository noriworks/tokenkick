"""Version helpers shared by CLI and diagnostics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import tokenkick

UNKNOWN_VERSION = "unknown"


@dataclass(frozen=True)
class DaemonPidfileInfo:
    pid: int
    version: str
    executable: str | None = None


def installed_version() -> str:
    package_attr = getattr(tokenkick, "__version__", None)
    if isinstance(package_attr, str) and package_attr:
        return package_attr
    try:
        return package_version("tokenkick")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def read_daemon_pidfile(path: Path) -> DaemonPidfileInfo | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    lines = raw.splitlines()
    parts = lines[0].split() if lines else []
    if not parts:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    version = parts[1] if len(parts) > 1 and parts[1] else UNKNOWN_VERSION
    executable = None
    for line in lines[1:]:
        if not line.startswith("executable="):
            continue
        raw_executable = line.removeprefix("executable=")
        try:
            decoded = json.loads(raw_executable)
        except json.JSONDecodeError:
            decoded = raw_executable
        if isinstance(decoded, str) and decoded:
            executable = decoded
        break
    return DaemonPidfileInfo(pid=pid, version=version, executable=executable)


def write_daemon_pidfile(
    path: Path,
    pid: int,
    version: str | None = None,
    executable: str | None = None,
) -> None:
    text = f"{pid} {version or installed_version()}\n"
    if executable:
        text += f"executable={json.dumps(executable)}\n"
    path.write_text(text)
