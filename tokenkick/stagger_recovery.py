"""Reconcile a downgraded stagger receipt without ever sending a request."""

import json
import math
from pathlib import Path

from .action_state import action_in_progress, action_path, read_action
from .antigravity_automation import _attempt_path, attempt_retry_at
from .models import account_key_string, load_kick_history
from .state_io import StateFileLockTimeout, state_file_lock


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def confirmed_receipt(directory: Path, account, attempt: dict, now: float) -> dict | None:
    """Require agreeing controller, dispatch, native cooldown and history records.

    Caller owns the stagger lock. Completed action ownership is checked under
    the same account lock used for dispatch; nothing here clears an uncertain
    transport guard or infers a request from elapsed time/current usage alone.
    """
    if action_in_progress() or attempt.get("family") != account.antigravity_family:
        return None
    at = attempt.get("at")
    if not _number(at) or at > now:
        return None
    key = account_key_string(account)
    try:
        with state_file_lock(action_path(directory, key), timeout=0):
            action = read_action(directory, key)
            guard = json.loads(_attempt_path(directory, account).read_text())
            retry_at = attempt_retry_at(directory, account)
            started, dispatched, completed = (action.get(k) for k in (
                "started_at", "dispatched_at", "completed_at"))
            attempted = guard.get("attempted_at")
            if (action.get("state") != "completed"
                    or not all(_number(v) for v in (started, dispatched, completed, attempted, retry_at))
                    or not at <= started <= attempted <= dispatched <= at + 120
                    or not dispatched <= completed <= min(now, at + 600)
                    or not attempted + 18000 <= retry_at <= attempted + 18300):
                return None
            # A unique sent event must identify this dispatch, not another poll,
            # a probe, or the peer account. Contradictory/duplicate records block.
            candidates = [e for e in load_kick_history(limit=1000)
                          if e.label == account.label and _number(e.timestamp)
                          and at <= e.timestamp <= completed and e.dispatch_state != "not_sent"]
            if len(candidates) != 1:
                return None
            event = candidates[0]
            if (event.kind != "session" or event.kick_type != "session"
                    or event.success is not True or event.confirmed is not True
                    or event.dispatch_state != "sent" or event.evidence_response is not True
                    or event.evidence_provider_moved is not True or event.post_kick_status != "confirmed"
                    or not dispatched <= event.timestamp <= dispatched + 120):
                return None
            return {"family": account.antigravity_family, "attempt_at": at,
                    "confirmed_event_at": event.timestamp, "retry_after": retry_at,
                    "reconciled_at": now, "method": "confirmed_history_and_dispatch_guards"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StateFileLockTimeout):
        return None
