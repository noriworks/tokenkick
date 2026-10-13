"""Version helpers shared by CLI and diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import tokenkick
from .state_io import atomic_write_text

UNKNOWN_VERSION = "unknown"


@dataclass(frozen=True)
class DaemonPidfileInfo:
    pid: int
    version: str
    executable: str | None = None
    process_identity: str | None = None


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
    if pid <= 0:
        return None
    version = parts[1] if len(parts) > 1 and parts[1] else UNKNOWN_VERSION
    executable = None
    identity = next((line.removeprefix("process_identity=") for line in lines
                     if line.startswith("process_identity=")), None)
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
    return DaemonPidfileInfo(pid=pid, version=version, executable=executable, process_identity=identity)


def write_daemon_pidfile(
    path: Path,
    pid: int,
    version: str | None = None,
    executable: str | None = None,
) -> None:
    if pid <= 0:
        raise ValueError("PID must be positive")
    text = f"{pid} {version or installed_version()}\n"
    identity = process_identity(pid)
    if identity:
        text += f"process_identity={identity}\n"
    if executable:
        text += f"executable={json.dumps(executable)}\n"
    atomic_write_text(path, text)


def process_identity(pid: int) -> str | None:
    """OS-owned creation identity; legacy PID files are readable but cannot signal."""
    if pid <= 0:
        return None
    try:
        if sys.platform.startswith("linux"):
            stat = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot}:{stat[19]}"
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=2, check=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def pidfile_process_matches(info: DaemonPidfileInfo) -> bool:
    return bool(info.pid > 0 and info.process_identity
                and process_identity(info.pid) == info.process_identity)
