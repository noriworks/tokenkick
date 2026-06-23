"""Atomic state-file IO helpers."""

from __future__ import annotations

import errno
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


class StateFileLockTimeout(TimeoutError):
    """Raised when a state-file lock cannot be acquired in time."""


def state_lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


@contextmanager
def state_file_lock(
    path: Path,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    """Hold an exclusive advisory lock for a persisted state file."""
    import fcntl

    lock_path = state_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if timeout is not None and time.monotonic() - start >= timeout:
                    raise StateFileLockTimeout(f"Timed out waiting for {lock_path}") from exc
                time.sleep(poll_interval)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a text file using a unique temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp_name = handle.name
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        _fsync_directory(path.parent)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def locked_atomic_write_text(path: Path, text: str) -> None:
    with state_file_lock(path):
        atomic_write_text(path, text)


def locked_update_text(path: Path, update: Callable[[str], str]) -> None:
    with state_file_lock(path):
        try:
            current = path.read_text()
        except FileNotFoundError:
            current = ""
        replacement = update(current)
        atomic_write_text(path, replacement)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            unsupported = {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)}
            if exc.errno not in unsupported:
                raise
    finally:
        os.close(fd)
