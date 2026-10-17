"""Cross-process provider action leases and crash evidence."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import time
from pathlib import Path

from .state_io import atomic_write_text, state_file_lock


class UncertainActionError(RuntimeError):
    """An earlier request may have reached the provider before its process exited."""


_current_action: ContextVar[tuple[Path, dict] | None] = ContextVar("tokenkick_action", default=None)


def action_path(directory: Path, key: str) -> Path:
    return directory / "actions" / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def read_action(directory: Path, key: str) -> dict:
    try:
        record = json.loads(action_path(directory, key).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"state": "invalid"}
    return (record if isinstance(record, dict) and isinstance(record.get("state"), str)
            else {"state": "invalid"})


def action_blocker(directory: Path, key: str, *, now: float | None = None) -> str | None:
    record = read_action(directory, key)
    state = record.get("state")
    if state == "awaiting_anchor":
        return "request_awaiting_anchor"
    if state in {"in_flight", "uncertain", "invalid"} or state not in {
        None, "preparing", "not_sent", "completed",
    }:
        return "request_outcome_uncertain"
    until = record.get("recovery_not_before")
    if until is not None:
        if type(until) not in {float, int} or not math.isfinite(until):
            return "request_outcome_uncertain"
        if until > (time.time() if now is None else now):
            return "request_recovery_cooldown"
    return None


def mark_action_awaiting_anchor(event) -> None:
    """Keep an accepted generation blocked without calling its transport uncertain."""
    action = _current_action.get()
    if action is not None:
        path, record = action
        record.update(state="awaiting_anchor", provider="claude",
                      request_kind=event.kick_type or event.kind,
                      accepted_at=event.timestamp,
                      accepted_evidence=bool(event.evidence_response or event.evidence_tokens))
        atomic_write_text(path, json.dumps(record))


def mark_action_verified() -> None:
    """Keep the lease crash-protected until the caller finishes recording history."""
    action = _current_action.get()
    if action is not None:
        path, record = action
        record.update(state="in_flight", quota_verified=True)
        atomic_write_text(path, json.dumps(record))


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
    path = action_path(directory, key)
    with state_file_lock(path, timeout=0):
        try:
            prior = json.loads(path.read_text())
        except FileNotFoundError:
            prior = {}
        except (OSError, ValueError):
            prior = {"state": "uncertain"}
        blocker = action_blocker(directory, key)
        if blocker:
            if not recover:
                message = {
                    "request_awaiting_anchor": "An earlier request was accepted; quota evidence is pending. Waiting for safe native-window recovery.",
                    "request_recovery_cooldown": "The old request guard was recovered; waiting for the next polling interval before dispatch.",
                }.get(blocker, "An earlier request has an uncertain outcome. Refresh provider status and "
                      "inspect request recovery status before sending another request.")
                raise UncertainActionError(message)
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
            if record["state"] not in {"uncertain", "awaiting_anchor"}:
                record.update(state="completed", completed_at=time.time())
                atomic_write_text(path, json.dumps(record))
        finally:
            _current_action.reset(token)
