"""Cross-process provider action leases and crash evidence."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import time
from pathlib import Path

from .state_io import atomic_write_text, state_file_lock


class UncertainActionError(RuntimeError):
    """An earlier request may have reached the provider before its process exited."""


_current_action: ContextVar[tuple[Path, dict] | None] = ContextVar("tokenkick_action", default=None)


def mark_action_dispatched() -> None:
    """Persist intent immediately before crossing the generation boundary."""
    action = _current_action.get()
    if action is not None:
        path, record = action
        record.update(state="in_flight", dispatched_at=time.time())
        atomic_write_text(path, json.dumps(record))


def mark_action_uncertain() -> None:
    action = _current_action.get()
    if action is not None:
        path, record = action
        record.update(state="uncertain")
        atomic_write_text(path, json.dumps(record))


def action_in_progress() -> bool:
    return _current_action.get() is not None


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
        record = {"state": "preparing", "started_at": time.time()}
        atomic_write_text(path, json.dumps(record))
        token = _current_action.set((path, record))
        try:
            yield prior if isinstance(prior, dict) else {}
        except BaseException:
            # Preparation cannot have sent a request. A dispatch exception must
            # retain the durable guard, including cancellation and process death.
            if record["state"] == "preparing":
                record.update(state="not_sent", completed_at=time.time())
                atomic_write_text(path, json.dumps(record))
            raise
        else:
            if record["state"] != "uncertain":
                record.update(state="completed", completed_at=time.time())
                atomic_write_text(path, json.dumps(record))
        finally:
            _current_action.reset(token)
