"""Reconcile Claude request guards from stable native windows; never send requests."""

import json
import math
import time
from copy import copy

from .action_state import action_in_progress, action_path, read_action
from .claude_monitoring import parse_time
from .models import AccountState, DataSource, account_key_string, is_synthetic_status
from .state_io import StateFileLockTimeout, atomic_write_text, state_file_lock

MAX_AGE = 15 * 60
MIN_SEPARATION = 60
REQUEST_MARGIN = 5 * 60
RESET_PRECISION = 60
EXISTING_WINDOW_SEPARATION = 120


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def weekly_window_active(status, *, now=None):
    """Suppress redundant weekly sends; this alone never clears a request guard."""
    now = time.time() if now is None else now
    observed = parse_time(status.observed_at)
    reset = status.resets_at
    return bool(
        _number(now) and observed is not None and 0 <= now - observed.timestamp() <= MAX_AGE
        and status.source_detail == "claude-cli-usage" and not status.stale and not status.error
        and status.state != AccountState.UNKNOWN and not is_synthetic_status(status)
        and status.window_minutes == 10080 and _number(reset)
        and reset > now and reset - 604800 < observed.timestamp() - REQUEST_MARGIN
    )


def normalize_status(status):
    if status.state == AccountState.FRESH and weekly_window_active(status):
        status = copy(status)
        status.state = AccountState.ACTIVE
        return status
    return status


def guard_diagnostic(directory, account, history):
    record = read_action(directory, account_key_string(account))
    if account.provider != "claude" or record.get("state") not in {"uncertain", "in_flight", "awaiting_anchor"}:
        return None
    receipt = _receipt(record, account, history)
    if receipt is None:
        return {"accepted": False, "requirement": "Inspect request evidence; automatic recovery lacks a matching accepted receipt."}
    kind, _ = receipt
    return {"accepted": True, "request_kind": kind,
            "requirement": ("Two fresh stable weekly observations with usage, or a verified later weekly window."
                            if kind == "kick" else "Two fresh observations of a stable later session or weekly window.")}


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
    matches = [e for e in history if e.label == account.label
               and e.dispatch_state != "not_sent" and e.kind not in {"probe", "reconcile", "status_probe"}
               and (e.kick_type or e.kind) in {"kick", "session"}
               and _number(e.timestamp) and dispatched <= e.timestamp <= dispatched + REQUEST_MARGIN]
    if len(matches) != 1:
        return None
    event = matches[0]
    if not (event.success is True and event.dispatch_state == "sent"
            and (event.evidence_response is True or event.evidence_tokens is True)):
        return None
    kind = event.kick_type or event.kind
    return kind, dispatched


def reconcile(directory, account, status, history, *, now=None, cooldown_seconds=60):
    """Return an evidence-only audit after two fresh window reads, under ownership.

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
                        candidate = {"reason": "later_native_window", "window": window, "reset_at": reset, "anchor_at": anchor,
                                     "first_observed_at": observed_at, "last_observed_at": observed_at}
                        break
                if (candidate is None and kind == "kick" and weekly_window_active(status, now=now)
                        and _number(status.used_percent) and 0 < status.used_percent <= 100
                        and status.resets_at - 604800 < dispatched - REQUEST_MARGIN
                        and observed_at > dispatched + REQUEST_MARGIN):
                    candidate = {"reason": "existing_weekly_window", "window": "weekly",
                                 "reset_at": status.resets_at, "anchor_at": status.resets_at - 604800,
                                 "first_observed_at": observed_at, "last_observed_at": observed_at}
            if candidate is None:
                if record.pop("recovery_candidate", None) is not None:
                    atomic_write_text(path, json.dumps(record))
                return None
            previous = record.get("recovery_candidate")
            existing = candidate["reason"] == "existing_weekly_window"
            same = (isinstance(previous, dict)
                    and previous.get("reason", "later_native_window") == candidate["reason"]
                    and previous.get("window") == candidate["window"]
                    and _number(previous.get("reset_at")) and _number(previous.get("anchor_at"))
                    and abs(previous["reset_at"] - candidate["reset_at"]) <= (RESET_PRECISION if existing else 0)
                    and abs(previous["anchor_at"] - candidate["anchor_at"]) <= (RESET_PRECISION if existing else 0)
                    and _number(previous.get("first_observed_at"))
                    and _number(previous.get("last_observed_at"))
                    and 0 <= now - previous["first_observed_at"] <= 2 * MAX_AGE
                    and previous["first_observed_at"] <= previous["last_observed_at"] <= observed_at)
            if same:
                candidate["first_observed_at"] = previous["first_observed_at"]
                # Keep the original deadline: repeated rounding must not walk it forward.
                candidate["reset_at"] = previous["reset_at"]
                candidate["anchor_at"] = previous["anchor_at"]
            if (same and observed_at > previous["last_observed_at"]
                    and observed_at - candidate["first_observed_at"] >= (EXISTING_WINDOW_SEPARATION if existing else MIN_SEPARATION)):
                audit = {"previous_state": record["state"],
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
