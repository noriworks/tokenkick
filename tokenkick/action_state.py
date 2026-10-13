"""Cross-process provider action leases and crash evidence."""

from contextlib import contextmanager
import hashlib
import json
import time
from pathlib import Path

from .state_io import atomic_write_text, state_file_lock


class UncertainActionError(RuntimeError):
    """An earlier request may have reached the provider before its process exited."""


@contextmanager
def account_action(directory: Path, key: str, *, recover: bool = False):
    path = directory / "actions" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
    with state_file_lock(path, timeout=0):
        try:
            prior = json.loads(path.read_text())
        except FileNotFoundError:
            prior = {}
        except (OSError, ValueError):
            prior = {"state": "uncertain"}
        if not isinstance(prior, dict) or prior.get("state") in {"in_flight", "uncertain"}:
            if not recover:
                raise UncertainActionError(
                    "An earlier request has an uncertain outcome. Refresh provider status and "
                    "inspect history before explicitly recovering with --force."
                )
        record = {"state": "in_flight", "started_at": time.time()}
        atomic_write_text(path, json.dumps(record))
        yield prior
        record.update(state="completed", completed_at=time.time())
        atomic_write_text(path, json.dumps(record))
