"""Reconcile Claude request guards from later native windows; never send requests."""

import json
import math
import time

from .action_state import action_in_progress, action_path, read_action
from .claude_monitoring import parse_time
from .models import AccountState, DataSource, account_key_string, is_synthetic_status
from .state_io import StateFileLockTimeout, atomic_write_text, state_file_lock

MAX_AGE = 15 * 60
MIN_SEPARATION = 60
REQUEST_MARGIN = 5 * 60


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def _receipt(record, account, history):
    dispatched = record.get("dispatched_at")
    started = record.get("started_at")
    if not (_number(dispatched) and _number(started) and 0 < started <= dispatched):
        return None
    # New guards retain their own receipt so history pruning cannot strand them.
    if record.get("state") == "awaiting_anchor" or "accepted_at" in record:
        accepted = record.get("accepted_at")
        kind = record.get("request_kind")
        if (record.get("provider") == "claude" and isinstance(kind, str) and kind in {"kick", "session"}
                and record.get("accepted_evidence") is True and _number(accepted)
                and dispatched <= accepted <= dispatched + REQUEST_MARGIN):
            return kind, dispatched
        return None
    # Legacy uncertain/transport guards need a separately recorded accepted receipt.
    matches = [e for e in history if e.label == account.label and e.success
               and e.dispatch_state == "sent" and e.kind not in {"probe", "reconcile", "status_probe"}
               and (e.kick_type or e.kind) in {"kick", "session"}
               and _number(e.timestamp) and dispatched <= e.timestamp <= dispatched + REQUEST_MARGIN
               and (e.evidence_response or e.evidence_tokens)]
    if len(matches) != 1:
        return None
    event = matches[0]
    kind = event.kick_type or event.kind
    return kind, dispatched


def reconcile(directory, account, status, history, *, now=None, cooldown_seconds=60):
    """Return an audit result after two fresh reads of a later window, under ownership.

    This retires an old guard, not the request's historical uncertainty. A later
    session can retire only a session request; a later week can retire either.
    """
    if (account.provider != "claude" or status.label != account.label
            or account.source != DataSource.CLAUDE_DIRECT or not account.identity_email
            or action_in_progress()):
        return None
    now = time.time() if now is None else now
    if not _number(now) or not _number(cooldown_seconds) or cooldown_seconds < 60:
        return None
    key = account_key_string(account)
    path = action_path(directory, key)
    if read_action(directory, key).get("state") not in {"uncertain", "in_flight", "awaiting_anchor"}:
        return None
    try:
        with state_file_lock(path, timeout=0):
            record = read_action(directory, key)
            if record.get("state") not in {"uncertain", "in_flight", "awaiting_anchor"}:
                return None
            receipt = _receipt(record, account, history)
            observed = parse_time(status.observed_at)
            valid = (receipt is not None and observed is not None
                     and 0 <= now - observed.timestamp() <= MAX_AGE
                     and status.source_detail == "claude-cli-usage"
                     and not status.stale and not status.error and not is_synthetic_status(status)
                     and status.state != AccountState.UNKNOWN)
            candidate = None
            if valid:
                kind, dispatched = receipt
                observed_at = observed.timestamp()
                windows = [("weekly", status.resets_at, status.window_minutes, 10080)]
                if kind == "session":
                    windows.append(("session", status.session_resets_at, status.session_window_minutes, 300))
                for window, reset, minutes, expected in windows:
                    if not _number(reset) or minutes != expected:
                        continue
                    anchor = reset - expected * 60
                    if dispatched + REQUEST_MARGIN < anchor <= observed_at <= now < reset:
                        candidate = {"window": window, "reset_at": reset, "anchor_at": anchor,
                                     "first_observed_at": observed_at, "last_observed_at": observed_at}
                        break
            if candidate is None:
                if record.pop("recovery_candidate", None) is not None:
                    atomic_write_text(path, json.dumps(record))
                return None
            previous = record.get("recovery_candidate")
            same = (isinstance(previous, dict)
                    and all(previous.get(k) == candidate[k] for k in ["window", "reset_at", "anchor_at"])
                    and _number(previous.get("first_observed_at"))
                    and _number(previous.get("last_observed_at"))
                    and 0 <= now - previous["first_observed_at"] <= 2 * MAX_AGE
                    and previous["first_observed_at"] <= previous["last_observed_at"] <= observed_at)
            if same:
                candidate["first_observed_at"] = previous["first_observed_at"]
            if (same and observed_at > previous["last_observed_at"]
                    and observed_at - candidate["first_observed_at"] >= MIN_SEPARATION):
                audit = {"reason": "later_native_window", "previous_state": record["state"],
                         "dispatched_at": dispatched, "request_kind": kind, **candidate,
                         "recovered_at": now}
                record.update(state="completed", completed_at=now, recovery=audit,
                              recovery_not_before=now + cooldown_seconds)
                record.pop("recovery_candidate", None)
                atomic_write_text(path, json.dumps(record))
                return audit
            record["recovery_candidate"] = candidate
            atomic_write_text(path, json.dumps(record))
            return None
    except StateFileLockTimeout:
        # An active owner may still be sending or verifying. Never race it.
        return None
