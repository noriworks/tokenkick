"""Persistent Antigravity orchestration: policy, durable ownership and daemon work.

The daemon owns recurrence. CLI and MCP only change this same persisted policy.
No scheduler outside TokenKick is required.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from . import models
from .antigravity_windows import (
    classify_session_clocks, native_window, observed_epoch, verified_unanchored,
)
from .models import Config, DataSource, account_key_string
from .scheduling import load_pending_kicks, schedule_for_account
from .state_io import atomic_write_text, state_file_lock

FAMILIES = ("gemini", "claude_gpt")
PERIOD = 18000
OFFSET = 9000
TOLERANCE = 120
RECOVERY_GRACE = 300
EVIDENCE_RETRY_DELAYS = (30, 60, 120)
ROLLOVER_CHECK_INTERVAL = 60
ROLLOVER_CHECK_BUDGET = 2700


class StaggerError(ValueError):
    pass


class WindowEvidencePending(StaggerError):
    """Complete native data exists, but the session clock is inconclusive."""

    def __init__(self, family, observed, reset):
        self.family = family
        self.observed = observed
        self.reset = reset
        super().__init__(f"{family}: session anchor is unverified; recheck native observations.")


def path(directory: Path | None = None) -> Path:
    return (directory if directory is not None else models.CONFIG_DIR) / "stagger.json"


def lock(directory: Path | None = None):
    return state_file_lock(path(directory))


def empty():
    return {"version": 1, "revision": 0, "group": None, "runtime": {}}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(condition):
    if not condition:
        raise ValueError("Invalid persisted stagger field")


def load(directory: Path | None = None):
    try:
        data = json.loads(path(directory).read_text())
    except FileNotFoundError:
        return empty()
    except (OSError, ValueError) as exc:
        raise StaggerError("Stagger state cannot be read; automatic Antigravity requests are blocked.") from exc
    try:
        _validate(type(data["version"]) is int and data["version"] == 1)
        _validate(type(data["revision"]) is int and data["revision"] >= 0)
        _validate(isinstance(data["runtime"], dict))
        group = data["group"]
        if group is not None:
            _validate(group["mode"] in {"enabled", "paused", "disabled"})
            _validate(set(group["members"]) == set(FAMILIES))
            _validate(all(isinstance(v, str) and v for v in group["members"].values()))
            _validate(len(set(group["members"].values())) == 2)
            _validate(isinstance(group["identity"], str) and group["identity"])
        runtime = data["runtime"]
        rollovers = runtime.get("provisional_rollovers", {})
        _validate(isinstance(rollovers, dict))
        for family, candidate in rollovers.items():
            _validate(family in FAMILIES and isinstance(candidate, dict))
            _validate(all(_number(candidate[k]) for k in ("anchor", "detected_at", "expires_at")))
            _validate(candidate["anchor"] <= candidate["detected_at"] < candidate["anchor"] + PERIOD)
            _validate(candidate["expires_at"] == candidate["detected_at"] + ROLLOVER_CHECK_BUDGET)
        _validate(runtime.get("rollover_check_at") is None or (
            _number(runtime["rollover_check_at"]) and bool(rollovers)
            and runtime["rollover_check_at"] <= min(c["expires_at"] for c in rollovers.values())))
        _validate(runtime.get("state") != "verifying_rollover" or bool(rollovers))
        for key in ("phase", "evaluated_at", "next_at", "evidence_observed_at"):
            _validate(runtime.get(key) is None or _number(runtime[key]))
        _validate(isinstance(runtime.get("anchors", {}), dict))
        _validate(all(k in FAMILIES and _number(v) for k, v in runtime.get("anchors", {}).items()))
        corrections = runtime.get("anchor_corrections", {})
        _validate(isinstance(corrections, dict))
        for family, correction in corrections.items():
            _validate(family in FAMILIES and isinstance(correction, dict))
            _validate(_number(correction["anchor"]) and _number(correction["detected_at"]))
            _validate(correction["anchor"] <= correction["detected_at"] < correction["anchor"] + PERIOD)
        last_correction = runtime.get("last_anchor_correction")
        if last_correction is not None:
            _validate(isinstance(last_correction, dict) and last_correction["family"] in FAMILIES)
            _validate(last_correction["outcome"] in {"reanchored", "native_window_observed", "not_sent"})
            _validate(all(_number(last_correction[k]) for k in ("previous_anchor", "finished_at")))
        _validate(runtime.get("next_family") is None or runtime["next_family"] in FAMILIES)
        _validate(runtime.get("observed_offset_seconds") is None or _number(runtime["observed_offset_seconds"]))
        retry = runtime.get("verification_retry")
        if retry is not None:
            _validate(isinstance(retry, dict))
            _validate(retry["family"] in FAMILIES and retry["blocked_family"] in FAMILIES)
            _validate(all(_number(retry[k]) for k in (
                "slot_at", "expires_at", "retry_at", "phase", "observed_at", "reset_at")))
            _validate(retry["expires_at"] == retry["slot_at"] + RECOVERY_GRACE)
            _validate(retry["slot_at"] - TOLERANCE <= retry["retry_at"] <= retry["expires_at"])
            _validate(type(retry["retry_count"]) is int and 1 <= retry["retry_count"] <= len(EVIDENCE_RETRY_DELAYS))
        _validate(runtime.get("state") != "rechecking" or retry is not None)
        recovery = runtime.get("last_recovery")
        if recovery is not None:
            _validate(isinstance(recovery, dict) and recovery["family"] in FAMILIES)
            _validate(recovery["outcome"] in {"claimed", "recovered", "skipped", "unconfirmed"})
            _validate(isinstance(recovery["reason"], str) and _number(recovery["finished_at"]))
            _validate(_number(recovery["slot_at"]) and _number(recovery["expires_at"]))
            _validate(recovery["expires_at"] == recovery["slot_at"] + RECOVERY_GRACE)
        attempt = runtime.get("attempt")
        if attempt is not None:
            _validate(attempt["family"] in FAMILIES and _number(attempt["at"]))
            _validate(attempt["outcome"] in {"attempting", "unconfirmed", "confirmed"})
        receipt = runtime.get("last_request_recovery")
        if receipt is not None:
            _validate(receipt["family"] in FAMILIES
                      and receipt["method"] == "confirmed_history_and_dispatch_guards")
            _validate(all(_number(receipt[k]) for k in (
                "attempt_at", "confirmed_event_at", "retry_after", "reconciled_at")))
            _validate(receipt["attempt_at"] <= receipt["confirmed_event_at"] <= receipt["reconciled_at"])
            _validate(receipt["retry_after"] >= receipt["attempt_at"] + PERIOD)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise StaggerError("Invalid/unsupported stagger state; automatic Antigravity requests are blocked.") from exc
    return data


def save(data, directory=None):
    atomic_write_text(path(directory), json.dumps(data, indent=2, allow_nan=False) + "\n")


def owns(account, directory=None):
    if account.provider != "antigravity":
        return False
    if account.stagger_group_id:
        return True
    try:
        group = load(directory)["group"]
    except StaggerError:
        return True  # Invalid policy must never fall through to independent automation.
    return bool(group and group["mode"] != "disabled"
                and account_key_string(account) in group["members"].values())


def ownership(account, directory=None):
    if not owns(account, directory):
        return "independent" if account.auto_kick else "off"
    try:
        data = load(directory)
    except StaggerError:
        return "blocked (invalid stagger state)"
    if not data["group"] or data["group"]["mode"] == "disabled":
        return "stagger blocked (missing policy; reconfigure)"
    if data["group"]["mode"] == "paused":
        return "stagger paused"
    if not account.auto_kick or not account.session_auto_kick:
        return "stagger blocked (auto/session off)"
    return "stagger-managed"


def members(config, group):
    result = {}
    for family, key in group["members"].items():
        matches = [a for a in config.accounts if account_key_string(a) == key]
        if len(matches) != 1:
            raise StaggerError("Member missing or ambiguous; reconfigure the stagger.")
        a = matches[0]
        if (a.provider != "antigravity" or a.source != DataSource.ANTIGRAVITY_CLI
                or a.antigravity_family != family
                or (a.identity_email or "").strip().lower() != group["identity"]):
            raise StaggerError("Member identity changed; reconfigure the stagger.")
        result[family] = a
    ids = {a.identity_provider_id for a in result.values() if a.identity_provider_id}
    if len(ids) > 1:
        raise StaggerError("Members do not belong to the same Antigravity identity.")
    return result


def conflicts(config, accounts, pending=None):
    pending = load_pending_kicks() if pending is None else pending
    return [a.label for a in accounts.values()
            if schedule_for_account(config.schedule, a.label) or account_key_string(a) in pending]


def preview(action, gemini=None, claude=None, *, directory=None):
    if action not in {"enable", "resume", "pause", "disable", "recover"}:
        raise StaggerError("Unknown stagger action.")
    if action != "enable" and (gemini is not None or claude is not None):
        raise StaggerError("Account labels are only accepted for enable.")
    data, config = load(directory), Config.load()
    group = data["group"]
    if action == "enable":
        selected = {}
        for family, label in zip(FAMILIES, (gemini, claude), strict=True):
            found = [a for a in config.accounts if a.label == label]
            if len(found) != 1:
                raise StaggerError("Select exactly one saved account per family.")
            selected[family] = found[0]
        group = {"mode": "enabled", "identity": (selected["gemini"].identity_email or "").strip().lower(),
                 "members": {f: account_key_string(a) for f, a in selected.items()}}
        if not group["identity"]:
            raise StaggerError("Verified account email is required for both families.")
        if data["group"] and data["group"]["mode"] != "disabled" and data["group"]["members"] != group["members"]:
            raise StaggerError("Disable the existing group before selecting different members.")
    if not group and action in {"disable", "pause"}:
        marked = [a for a in config.accounts if a.stagger_group_id and a.provider == "antigravity"]
        if marked:
            group = {"mode": "paused", "identity": (marked[0].identity_email or "unknown").lower(),
                     "members": {f: next((account_key_string(a) for a in marked if a.antigravity_family == f),
                                          "missing:" + f) for f in FAMILIES}}
    if not group:
        raise StaggerError("No stagger is configured.")
    if action in {"pause", "disable"}:
        # Stopping must still work when a saved member was removed/replaced.
        selected = {f: a for f, key in group["members"].items() for a in config.accounts
                    if account_key_string(a) == key or a.stagger_group_id == "antigravity-default" and a.antigravity_family == f}
    else:
        selected = members(config, group)
    competing = conflicts(config, selected)
    if action in {"enable", "resume"} and competing:
        raise StaggerError("Cancel conflicting schedules first: " + ", ".join(competing))
    payload = {"action": action, "revision": data["revision"], "group": group,
               "members": {f: a.to_dict() for f, a in selected.items()}, "conflicts": competing,
               "consent": config.has_auto_kick_consent("antigravity"),
               "recovery_attempt": data["runtime"].get("attempt") if action == "recover" else None}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"action": action, "fingerprint": fingerprint, "group": group,
            "target_offset_minutes": 150, "recurring": True,
            "changes": [{"label": a.label, "family": f,
                         "master_before": a.auto_kick, "session_before": a.session_auto_kick,
                         "master_after": action in {"enable", "resume"},
                         "session_after": action in {"enable", "resume"},
                         "weekly_preserved": a.weekly_auto_kick}
                        for f, a in selected.items()],
            "note": ("Recovery performs native reads, requires a full five-hour cooldown, and leaves orchestration paused. " if action == "recover" else "") + "Runs persistently in the TokenKick daemon. Weekly requests share stagger slots. Pause/disable never restores independent automation."}


def require_compatible_daemon(directory=None):
    """An older running daemon must not ignore newly granted group ownership."""
    import os
    from .versioning import read_daemon_pidfile
    info = read_daemon_pidfile((directory or models.CONFIG_DIR) / "daemon.pid")
    if info is None:
        return
    try:
        os.kill(info.pid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    if "stagger-v1" not in info.capabilities or "dispatch-lifecycle-v1" not in info.capabilities:
        raise StaggerError("Running daemon lacks required orchestration capabilities; stop it and restart with the updated runtime before enabling.")


def apply(action, gemini=None, claude=None, *, expected, accept_risk=False, directory=None):
    with lock(directory), state_file_lock(models.CONFIG_FILE):
        plan = preview(action, gemini, claude, directory=directory)
        if plan["fingerprint"] != expected:
            raise StaggerError("Settings changed; create a new stagger preview.")
        config = Config.load()
        if action in {"enable", "resume"}:
            require_compatible_daemon(directory)
            if not accept_risk:
                raise StaggerError("Explicit acknowledgement of automatic quota use is required.")
            config.record_auto_kick_consent("antigravity")
        data = load(directory)
        if action == "recover":
            _recover_guard(data, config, directory)
        old = data["group"]
        group = plan["group"]
        changed_members = not old or old["members"] != group["members"]
        if changed_members:
            data["runtime"] = {}
        # A paused owner is persisted before permission edits. Any interrupted
        # two-file transition remains stopped and cannot enable independent sends.
        data["group"] = {**group, "mode": "paused"}
        data["revision"] += 1
        save(data, directory)
        enabled = action in {"enable", "resume"}
        keys = set(group["members"].values())
        config.accounts = [replace(a, auto_kick=enabled, session_auto_kick=enabled,
                                  stagger_group_id=None if action == "disable" else "antigravity-default")
                           if account_key_string(a) in keys or action in {"pause", "disable"} and a.stagger_group_id == "antigravity-default" else a for a in config.accounts]
        config.save()
        data["group"]["mode"] = "enabled" if enabled else {"pause": "paused", "disable": "disabled", "recover": "paused"}[action]
        data["runtime"].update(state="awaiting_evidence" if enabled else data["group"]["mode"],
                               reason=None, next_at=None, next_family=None)
        _finish_verification_retry(data["runtime"], "skipped", "control_changed", time.time())
        save(data, directory)
        return status(directory=directory)


def observation(account, status, now):
    family = account.antigravity_family
    observed = observed_epoch(status)
    window = native_window(status, family)
    weekly = native_window(status, family, "weekly")
    if status.error:
        from .antigravity import ANTIGRAVITY_REFRESH_ERRORS, antigravity_refresh_error_code
        code = antigravity_refresh_error_code(status)
        if code:
            raise StaggerError(f"{family}: {ANTIGRAVITY_REFRESH_ERRORS[code]}")
    if (status.label != account.label or models.is_synthetic_status(status)
            or observed is None or not -30 <= now - observed <= 120
            or window is None or weekly is None):
        raise StaggerError("Fresh complete native quota evidence required.")
    if verified_unanchored(status, family):
        anchor = None
    elif window.get("anchor_state") == "anchored" and window["resets_at"] > now:
        anchor = window["resets_at"] - PERIOD
    else:
        raise WindowEvidencePending(family, observed, window["resets_at"])
    reserve = account.weekly_reserve_threshold_percent
    reason = None
    if weekly["used_percent"] >= 100 and weekly["resets_at"] > now:
        reason = "weekly_exhausted"
    elif reserve is not None and weekly["used_percent"] >= reserve and weekly["resets_at"] > now:
        reason = "weekly_reserve_reached"
    return {"anchor": anchor, "blocker": reason}


def _finish_verification_retry(runtime, outcome, reason, now):
    retry = runtime.pop("verification_retry", None)
    if retry is not None:
        runtime["last_recovery"] = {
            **retry, "outcome": outcome, "reason": reason, "finished_at": now,
        }


def _observations(accounts, statuses, now):
    """Do not let one ambiguous clock hide another member's invalid evidence."""
    observations, pending = {}, None
    for family, account in accounts.items():
        try:
            observations[family] = observation(account, statuses[family], now)
        except WindowEvidencePending as exc:
            pending = pending or exc
    if pending is not None:
        raise pending
    return observations


def _schedule_verification_retry(runtime, error, now):
    """Retain one known slot; never renew its absolute deadline or a sent request."""
    attempt = runtime.get("attempt")
    if (runtime.get("state") in {"attempting", "verifying"}
            or attempt and attempt["outcome"] != "confirmed"):
        return False
    retry = runtime.get("verification_retry")
    if retry is None:
        due, family, phase = (runtime.get(k) for k in ("next_at", "next_family", "phase"))
        if (family not in FAMILIES or not _number(due) or not _number(phase)
                or not due - TOLERANCE <= now <= due + RECOVERY_GRACE
                or attempt and attempt["family"] == family and attempt["at"] >= due):
            return False
        retry = {"family": family, "slot_at": due, "expires_at": due + RECOVERY_GRACE,
                 "phase": phase, "retry_count": 0}
    retry = {**retry, "blocked_family": error.family,
             "observed_at": error.observed, "reset_at": error.reset}
    count = retry["retry_count"]
    if (now < retry["slot_at"] - TOLERANCE or count >= len(EVIDENCE_RETRY_DELAYS)
            or now + EVIDENCE_RETRY_DELAYS[count] > retry["expires_at"]):
        # Only persist a validated pending record; the finished audit may have
        # no retry_at if the initial read already consumed the whole grace.
        runtime["verification_retry"] = retry
        _finish_verification_retry(runtime, "skipped", "verification_budget_exhausted", now)
        return False
    runtime["verification_retry"] = {**retry, "retry_count": count + 1,
                                      "retry_at": now + EVIDENCE_RETRY_DELAYS[count]}
    runtime.update(state="rechecking", reason=str(error), next_at=None, next_family=None,
                   evaluated_at=now, recovery="bounded_native_recheck")
    return True


def decide(runtime, observations, now):
    """Pure recurring policy. Never replay a missed slot or consume an active one."""
    runtime = dict(runtime)
    anchors = dict(runtime.get("anchors", {}))
    corrections = dict(runtime.get("anchor_corrections", {}))
    active = {f: v["anchor"] for f, v in observations.items() if v["anchor"] is not None}
    # None reaches this function only after paired native reads prove an unused
    # sliding horizon. Ordinary expiration and clock rollback are not corrections.
    for f, anchor in list(anchors.items()):
        if f not in active and anchor <= now < anchor + PERIOD:
            corrections[f] = {"anchor": anchor, "detected_at": now}
            del anchors[f]
    restored = set(active) & set(corrections)
    for f in sorted(restored):
        correction = corrections.pop(f)
        runtime["last_anchor_correction"] = {
            "family": f, "previous_anchor": correction["anchor"],
            "finished_at": now, "outcome": "native_window_observed",
        }
    anchors.update(active)
    phase = runtime.get("phase")
    if phase is None and active:
        phase = active.get("gemini", active.get("claude_gpt", 0) - OFFSET)
    if phase is None:
        phase = now
    offset = ((active["claude_gpt"] - active["gemini"]) % PERIOD) if len(active) == 2 else None
    aligned = offset is not None and abs(offset - OFFSET) <= TOLERANCE
    # An external request that changed a window becomes the surviving reference.
    changed = [f for f in active if f in restored or (f in runtime.get("anchors", {})
               and abs(active[f] - runtime["anchors"][f]) > TOLERANCE
               and abs((active[f] - phase - (OFFSET if f == "claude_gpt" else 0) + PERIOD / 2) % PERIOD - PERIOD / 2) > TOLERANCE)]
    if changed:
        f = changed[-1]
        phase = active[f] - (OFFSET if f == "claude_gpt" else 0)
    retry = runtime.get("verification_retry")
    if retry:
        family = retry["family"]
        invalid = (
            "grace_expired" if now > retry["expires_at"] else
            "clock_moved_backwards" if now < retry["slot_at"] - TOLERANCE else
            "activity_changed_phase" if phase != retry["phase"] else
            "family_already_active" if family in active else
            "quota_blocked" if observations[family]["blocker"] else
            "cooldown_exceeds_grace" if (observations[family].get("not_before") or 0) > retry["expires_at"] else
            None
        )
        if invalid:
            _finish_verification_retry(runtime, "skipped", invalid, now)
            retry = None
    candidates, blockers = [], {}
    for f in FAMILIES:
        info = observations[f]
        if info["blocker"]:
            blockers[f] = info["blocker"]
            continue
        base = phase + (OFFSET if f == "claude_gpt" else 0)
        earliest = max(now - TOLERANCE, anchors.get(f, now - PERIOD) + PERIOD - TOLERANCE)
        normal_slot = base + max(0, math.ceil((earliest - base) / PERIOD)) * PERIOD
        minimum = info.get("not_before") or 0
        if f in anchors:
            minimum = max(minimum, anchors[f] + PERIOD)
        attempt = runtime.get("attempt")
        if attempt and attempt["family"] == f:
            minimum = max(minimum, attempt["at"] + PERIOD)
        slot = normal_slot
        if f in corrections:
            # This is a new phase start, not replay of a missed scheduled slot.
            peer = next(other for other in FAMILIES if other != f)
            minimum = max(minimum, corrections[f]["detected_at"],
                          anchors.get(peer, now - OFFSET) + OFFSET,
                          (observations[peer].get("not_before") or 0) - PERIOD + OFFSET)
            if attempt and attempt["family"] == peer:
                minimum = max(minimum, attempt["at"] + OFFSET)
            slot = now
        if retry and f == retry["family"]:
            if minimum > retry["expires_at"]:
                _finish_verification_retry(runtime, "skipped", "cooldown_exceeds_grace", now)
                retry = None
            else:
                slot = retry["slot_at"]
        candidates.append((max(now, slot, minimum), f))
    next_at, next_family = min(candidates) if candidates else (None, None)
    state = "degraded" if blockers else "aligned" if aligned else "realigning" if len(active) == 2 or corrections else "scheduled"
    return {**runtime, "anchors": anchors, "anchor_corrections": corrections, "phase": phase, "state": state,
            "observed_offset_seconds": offset, "next_at": next_at, "next_family": next_family,
            "reason": blockers or None, "evaluated_at": now}


def _verify_rollovers(runtime, observations, statuses, now):
    """Do not turn a provider's zero-use periodic rollover into an activity anchor.

    Stable paired reads alone are insufficient for this known ambiguous case.
    Waiting never authorizes a request; only the existing native idle proof can.
    """
    pending = runtime.setdefault("provisional_rollovers", {})
    for family, info in observations.items():
        anchor = info["anchor"]
        prior = runtime.get("anchors", {}).get(family)
        candidate = pending.get(family)
        used = native_window(statuses[family], family)["used_percent"]
        if candidate and now < candidate["detected_at"]:
            continue  # Clock rollback must not clear or renew the evidence guard.
        if candidate and anchor is None:
            # observation() admits None only with verified native sliding proof.
            if candidate["anchor"] <= now < candidate["anchor"] + PERIOD:
                runtime.setdefault("anchor_corrections", {})[family] = {
                    "anchor": candidate["anchor"], "detected_at": now,
                }
                runtime.get("anchors", {}).pop(family, None)
            del pending[family]
            continue
        periodic = (anchor is not None and prior is not None
                    and anchor - prior >= PERIOD - 1
                    and abs((anchor - prior + PERIOD / 2) % PERIOD - PERIOD / 2) <= 1)
        if candidate and (used > 0 or anchor is not None
                          and abs(anchor - candidate["anchor"]) > 1 and not periodic):
            del pending[family]  # Independent activity supersedes the idle hypothesis.
            candidate = None
        if used == 0 and periodic and anchor <= now < anchor + PERIOD:
            if candidate is None:
                pending[family] = {"anchor": anchor, "detected_at": now,
                                   "expires_at": now + ROLLOVER_CHECK_BUDGET}
            # Repeated/changed periodic rollovers never renew the fast-read budget.
    if not pending:
        runtime.pop("rollover_check_at", None)
        return False
    deadline = min(c["expires_at"] for c in pending.values())
    first_detection = min(c["detected_at"] for c in pending.values())
    interval = ROLLOVER_CHECK_INTERVAL if now < first_detection + 300 else 2 * ROLLOVER_CHECK_INTERVAL
    next_check = min(max(now, first_detection) + interval, deadline) if now < deadline else None
    runtime.update(state="verifying_rollover", next_at=None, next_family=None,
                   observed_offset_seconds=None,
                   rollover_check_at=next_check,
                   evaluated_at=now,
                   reason="Zero-use rollover is provisional; waiting for native activity or verified idle evidence.")
    return True


def status(*, directory=None, daemon_running=None):
    try:
        data = load(directory)
    except StaggerError as exc:
        return {"enabled": False, "state": "blocked", "reason": str(exc), "recurring": True,
                "daemon_running": daemon_running, "target_offset_minutes": 150}
    group, runtime = data["group"], data["runtime"]
    mode = group["mode"] if group else "disabled"
    state = runtime.get("state", "awaiting_evidence") if mode == "enabled" else mode
    reason = runtime.get("reason")
    if not group:
        try:
            saved = json.loads(models.CONFIG_FILE.read_text())
            marked = any(a.get("stagger_group_id") for a in saved.get("accounts", []) if isinstance(a, dict))
        except (FileNotFoundError, ValueError, OSError, AttributeError, TypeError):
            marked = False
        if marked:
            state, reason = "blocked", "Saved stagger ownership has no policy; reconfigure or explicitly disable it."
    max_age = 720
    age = None
    if runtime.get("evidence_observed_at") is not None:
        age = time.time() - runtime["evidence_observed_at"]
    if mode == "enabled":
        try:
            config = Config.load()
            max_age = config.poll_interval_minutes * 60 + 120
            selected = members(config, group)
            if any(not a.auto_kick or not a.session_auto_kick or not a.visible for a in selected.values()):
                state, reason = "blocked", "Member automation/session permission is off or account is hidden."
        except (StaggerError, OSError, ValueError) as exc:
            state, reason = "blocked", str(exc)
    return {"enabled": mode == "enabled", "mode": mode, "state": state,
            "recurring": True, "controller": "TokenKick daemon", "revision": data["revision"],
            "target_offset_minutes": 150, "members": group["members"] if group else {},
            "daemon_running": daemon_running, **{k: runtime.get(k) for k in (
                "reason", "next_at", "next_family", "observed_offset_seconds", "evaluated_at")}, "reason": reason,
            "verification_retry": runtime.get("verification_retry"),
            "last_recovery": runtime.get("last_recovery"),
            "last_request_recovery": runtime.get("last_request_recovery"),
            "request_outcome": runtime.get("attempt", {}).get("outcome"),
            "anchor_corrections": runtime.get("anchor_corrections", {}),
            "last_anchor_correction": runtime.get("last_anchor_correction"),
            "provisional_rollovers": runtime.get("provisional_rollovers", {}),
            "next_check_at": (runtime.get("verification_retry", {}).get("retry_at")
                              if state == "rechecking" else runtime.get("rollover_check_at")
                              if state == "verifying_rollover" else None),
            "observation_age_seconds": age,
            "evidence_fresh": age is not None and -30 <= age <= max_age,
            "operational": mode == "enabled" and daemon_running is True
            and age is not None and -30 <= age <= max_age
            and state not in {"blocked", "paused", "awaiting_evidence", "rechecking", "verifying_rollover"}}


def send_guard(account, *, automatic, controller=False, directory=None, status=None):
    """Called inside the group lock again immediately before dispatch."""
    if not automatic or not owns(account, directory):
        if controller:
            return "Stagger ownership changed."
        return None
    data = load(directory)
    if not controller:
        return "Account is stagger-managed; independent automatic requests are blocked."
    if not data["group"] or data["group"]["mode"] != "enabled":
        return "Stagger is paused or disabled."
    config = Config.load()
    selected = members(config, data["group"])
    if any(not a.auto_kick or not a.session_auto_kick or not a.visible for a in selected.values()):
        return "Member automation/session permission changed."
    if conflicts(config, selected):
        return "A competing schedule appeared during preflight."
    runtime = data["runtime"]
    attempt = runtime.get("attempt")
    if (runtime.get("next_family") != account.antigravity_family
            or not _number(runtime.get("next_at")) or runtime["next_at"] > time.time()
            or not attempt or attempt["family"] != account.antigravity_family
            or attempt["outcome"] != "attempting"):
        return "No due, claimed stagger slot for this family."
    recovery = runtime.get("last_recovery")
    if (recovery and recovery["outcome"] == "claimed"
            and recovery["family"] == account.antigravity_family
            and time.time() > recovery["expires_at"]):
        return "Recovery deadline passed during preflight; no stagger request sent."
    if status is None or not verified_unanchored(status, account.antigravity_family):
        return "Session changed during preflight; no stagger request sent."
    info = observation(selected[account.antigravity_family], status, time.time())
    return info["blocker"]


def read_pair(accounts, config, fetch, sleep=None):
    from .sources import polling_pass_cache
    sleep = sleep or time.sleep
    with polling_pass_cache():
        first = {f: fetch(a, config) for f, a in accounts.items()}
    sleep(6)
    with polling_pass_cache():
        second = {f: fetch(a, config) for f, a in accounts.items()}
    from .antigravity import antigravity_family_status
    return {f: antigravity_family_status(classify_session_clocks(first[f], second[f]), f)
            for f in FAMILIES}


def tick(*, directory=None, fetch=None, send=None, now=None, sleep=None):
    """One evaluation; durable state schedules all subsequent cycles automatically."""
    from . import cli
    fetch = fetch or cli._fetch_status
    send = send or cli._kick_and_notify
    clock = now or time.time
    sleep = sleep or time.sleep
    initial = load(directory)
    group = initial["group"]
    if not group or group["mode"] != "enabled":
        return status(directory=directory)
    # Slow native reads do not hold the control lock. A pause/edit during these
    # reads wins through the revision and permission checks below.
    read_error = None
    try:
        config = Config.load()
        accounts = members(config, group)
        statuses = read_pair(accounts, config, fetch, sleep)
    except Exception as exc:
        read_error = exc
    with lock(directory):
        data = load(directory)
        if data["revision"] != initial["revision"] or data["group"] != group:
            return status(directory=directory)
        runtime = data["runtime"]
        runtime["evidence_observed_at"] = None
        if read_error is None and all(
            statuses[f].label == accounts[f].label and observed_epoch(statuses[f]) is not None
            and native_window(statuses[f], f) is not None
            and native_window(statuses[f], f, "weekly") is not None for f in FAMILIES
        ):
            runtime["evidence_observed_at"] = min(observed_epoch(s) for s in statuses.values())
        try:
            if read_error is not None:
                raise read_error
            config = Config.load()
            accounts = members(config, group)
            if any(not a.auto_kick or not a.session_auto_kick or not a.visible for a in accounts.values()):
                raise StaggerError("Member automation/session permission is off or account is hidden.")
            if not config.has_auto_kick_consent("antigravity"):
                raise StaggerError("Antigravity automatic quota-use consent is missing.")
            if conflicts(config, accounts):
                raise StaggerError("Conflicting pending or work-window schedule; cancel it before resuming.")
            attempt = runtime.get("attempt")
            if attempt and attempt["outcome"] != "confirmed":
                from .stagger_recovery import confirmed_receipt
                proof = confirmed_receipt(
                    directory if directory is not None else models.CONFIG_DIR,
                    accounts[attempt["family"]], attempt, clock())
                if proof is None:
                    raise StaggerError("Previous request unconfirmed; inspect history and use explicit recovery.")
                attempt["outcome"] = "confirmed"
                runtime.update(last_request_recovery=proof, state="awaiting_evidence",
                               reason="Confirmed request recovered from history; rechecking native windows next poll.",
                               evaluated_at=clock(), next_at=None, next_family=None, recovery="next_poll")
                save(data, directory)
                return status(directory=directory)  # Reconciliation never sends.
            from .antigravity_automation import attempt_retry_at
            # Validate durable dispatch guards before deciding whether an evidence
            # failure is eligible for automatic read-only recovery.
            retry_limits = {f: attempt_retry_at(
                directory if directory is not None else models.CONFIG_DIR, a)
                for f, a in accounts.items()}
            observations = _observations(accounts, statuses, clock())
            for family, account in accounts.items():
                observations[family]["not_before"] = retry_limits[family]
            if _verify_rollovers(runtime, observations, statuses, clock()):
                save(data, directory)
                return status(directory=directory)
            runtime = decide(runtime, observations, clock())
            data["runtime"] = runtime
            save(data, directory)
            f = runtime["next_family"]
            if f and runtime["next_at"] <= clock() and observations[f]["anchor"] is None:
                # The normal executor reloads permissions/identity and quota under
                # ownership, checks eligibility and records its own durable guard.
                recovering = runtime.get("verification_retry")
                if recovering and recovering["family"] == f:
                    _finish_verification_retry(runtime, "claimed", "evidence_verified", clock())
                else:
                    recovering = None
                correcting = runtime.get("anchor_corrections", {}).get(f)
                runtime.update(state="attempting", attempt={"family": f, "at": clock(), "outcome": "attempting"})
                save(data, directory)
                event = send(accounts[f], config, statuses[f], automatic=True,
                             stagger_controller=True, kick_type="session", force=False,
                             send_notification=False, daemon_log=True)
                if event.dispatch_state == "not_sent":
                    runtime.pop("attempt", None)
                    if correcting:
                        runtime["anchor_corrections"].pop(f)
                        runtime["last_anchor_correction"] = {
                            "family": f, "previous_anchor": correcting["anchor"],
                            "finished_at": clock(), "outcome": "not_sent",
                        }
                    if recovering:
                        runtime["last_recovery"].update(outcome="skipped", reason="request_not_sent", finished_at=clock())
                    raise StaggerError("Request not sent; will recheck eligibility on the next poll: " + (event.error or "unavailable"))
                if not (event.success and event.confirmed):
                    runtime["attempt"]["outcome"] = "unconfirmed"
                    raise StaggerError("Stagger request failed or unconfirmed; inspect history before recovery.")
                runtime["attempt"]["outcome"] = "confirmed"
                runtime.update(state="verifying", next_at=None, next_family=None)
                save(data, directory)
                verified = read_pair(accounts, Config.load(), fetch, sleep)
                observations = _observations(accounts, verified, clock())
                if observations[f]["anchor"] is None:
                    raise StaggerError("Request confirmed, but its session anchor is no longer visible; rechecking native windows next poll.")
                # Bootstrap and late recovery use the actual confirmed anchor.
                if not runtime.get("anchors") or recovering or correcting:
                    runtime["phase"] = observations[f]["anchor"] - (OFFSET if f == "claude_gpt" else 0)
                if correcting:
                    runtime["anchor_corrections"].pop(f)
                    runtime["last_anchor_correction"] = {
                        "family": f, "previous_anchor": correcting["anchor"],
                        "finished_at": clock(), "outcome": "reanchored",
                    }
                if recovering:
                    runtime["last_recovery"].update(outcome="recovered", reason="anchor_confirmed",
                                                    finished_at=clock(), anchor=observations[f]["anchor"])
                if not _verify_rollovers(runtime, observations, verified, clock()):
                    runtime = decide(runtime, observations, clock())
            data["runtime"] = runtime
        except Exception as error:
            exc = error
            # Monitoring can fail after the executor has already confirmed and
            # durably recorded the request. Never downgrade that receipt. The
            # normal next-poll path retains all dispatch cooldowns and spacing.
            confirmed = runtime.get("attempt", {}).get("outcome") == "confirmed"
            if runtime.get("state") == "verifying" and confirmed:
                runtime["evidence_observed_at"] = None
                exc = StaggerError("Request confirmed; post-request quota refresh unavailable. Rechecking native windows next poll.")
            if runtime.get("last_recovery", {}).get("outcome") == "claimed":
                runtime["last_recovery"].update(
                    outcome="recovered" if confirmed else "unconfirmed",
                    reason="request_confirmed_monitoring_pending" if confirmed else "request_outcome_unconfirmed",
                    finished_at=clock())
            if isinstance(exc, WindowEvidencePending) and _schedule_verification_retry(runtime, exc, clock()):
                data["runtime"] = runtime
                save(data, directory)
                return status(directory=directory)
            _finish_verification_retry(runtime, "skipped", "recovery_blocked", clock())
            data["runtime"] = {**runtime, "state": "blocked", "reason": str(exc),
                               "next_at": None, "next_family": None, "evaluated_at": clock(),
                               "recovery": "explicit" if runtime.get("attempt", {}).get("outcome") in {"attempting", "unconfirmed"} else "next_poll"}
        save(data, directory)
        return status(directory=directory)


def execution_lock(account, directory=None):
    return lock(directory) if account.provider == "antigravity" else nullcontext()


def wake_seconds(interval, directory=None, now=None):
    data = status(directory=directory)
    if not data["enabled"]:
        return interval
    now = time.time() if now is None else now
    due = _next_deadline(data)
    # Wake to notice control edits without performing an expensive quota read on
    # every wake. The daemon adapter below selects when evaluation is necessary.
    return min(interval, 60, max(1, due - now) if due is not None else 60)


def _next_deadline(payload):
    deadlines = [payload.get(k) for k in ("next_at", "next_check_at")]
    return min((v for v in deadlines if _number(v)), default=None)


def daemon_step(config, *, directory=None):
    from . import cli
    current = status(directory=directory)
    if not current["enabled"]:
        return current
    now = time.time()
    due = _next_deadline(current)
    last = current.get("evaluated_at") or 0
    if now >= last and now - last < config.poll_interval_minutes * 60 and (due is None or due > now):
        return current
    result = tick(directory=directory)
    # Deduplicate state-change notifications, never announce every healthy cycle.
    kind = "aligned" if result["state"] == "aligned" else "blocked" if result["state"] in {"blocked", "degraded"} else None
    if kind:
        with lock(directory):
            data = load(directory)
            signature = json.dumps([kind, result.get("reason")], sort_keys=True)
            if data["runtime"].get("notified") != signature:
                from .notifier import notify_stagger
                selected = {account_key_string(a): a for a in config.accounts
                            if account_key_string(a) in data["group"]["members"].values()
                            or a.stagger_group_id == "antigravity-default"}
                message = "Antigravity stagger: " + result["state"] + (
                    " — " + str(result["reason"]) if result.get("reason") else " (target 2h30m).")
                # One group message per configured backend, respecting member routes.
                routes = {}
                for account in selected.values():
                    for route in cli._notification_configs_for_account(account, config.notifications):
                        routes.setdefault(route.backend, route)
                for route in routes.values():
                    notify_stagger(message, route)
                data["runtime"]["notified"] = signature
                save(data, directory)
    cli._daemon_log("stagger", state=result["state"], next_at=result.get("next_at"), reason=result.get("reason"),
                    next_check_at=result.get("next_check_at"), verification_retry=result.get("verification_retry"),
                    last_recovery=result.get("last_recovery"), anchor_corrections=result.get("anchor_corrections"),
                    provisional_rollovers=result.get("provisional_rollovers"),
                    last_anchor_correction=result.get("last_anchor_correction"))
    return result


def render(console, payload):
    if payload.get("mode") == "disabled" and not payload.get("members") and payload.get("state") != "blocked":
        return
    from rich.panel import Panel
    from rich.text import Text
    running = payload.get("daemon_running")
    runtime = "running" if running else "stopped" if running is False else "status unknown"
    lines = [f"{'Enabled' if payload['enabled'] else 'Off'} · {payload['state']} · daemon {runtime}",
             "Target offset: 2h30m"]
    if payload.get("observed_offset_seconds") is not None:
        lines.append(f"Observed offset: {payload['observed_offset_seconds'] / 60:.1f}m")
    if payload.get("next_at"):
        when = datetime.fromtimestamp(payload["next_at"], timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        lines.append(f"Next: {payload['next_family']} at {when}")
    retry = payload.get("verification_retry")
    if retry:
        deadline = datetime.fromtimestamp(retry["expires_at"], timezone.utc).astimezone().strftime("%H:%M:%S %Z")
        lines.append(f"Delayed slot: {retry['family']} · recovery closes {deadline}")
        if payload.get("next_check_at"):
            when = datetime.fromtimestamp(payload["next_check_at"], timezone.utc).astimezone().strftime("%H:%M:%S %Z")
            lines.append(f"Recheck {retry['blocked_family']} reset: {when} ({retry['retry_count']}/{len(EVIDENCE_RETRY_DELAYS)})")
    last = payload.get("last_recovery")
    if last:
        lines.append(f"Last slot recovery: {last['family']} · {last['outcome']} · {last['reason']}")
    for family in payload.get("anchor_corrections", {}):
        lines.append(f"Correcting {family}: previous anchor disproven; waiting for a safe new start.")
    correction = payload.get("last_anchor_correction")
    if correction:
        lines.append(f"Last anchor correction: {correction['family']} · {correction['outcome']}")
    receipt = payload.get("last_request_recovery")
    if receipt:
        lines.append(f"Request receipt recovered: {receipt['family']} · already confirmed · no replacement request")
    for family in payload.get("provisional_rollovers", {}):
        lines.append(f"Verifying {family} rollover: zero use; no new request confirmed.")
    if payload.get("provisional_rollovers"):
        if payload.get("next_check_at"):
            when = datetime.fromtimestamp(payload["next_check_at"], timezone.utc).astimezone().strftime("%H:%M:%S %Z")
            lines.append(f"Read-only rollover check: {when}")
        else:
            lines.append("Fast rollover checks exhausted; continuing normal polling.")
    if payload.get("reason"):
        lines.append("Waiting: " + str(payload["reason"]))
    if payload.get("evaluated_at") and payload.get("evidence_fresh") is False:
        lines.append("Evidence is stale; displayed alignment is last observed.")
    if payload.get("evaluated_at"):
        when = datetime.fromtimestamp(payload["evaluated_at"], timezone.utc).astimezone().strftime("%H:%M:%S %Z")
        lines.append("Last evaluation: " + when)
    console.print(Panel(Text("\n".join(lines)), title="Ongoing Antigravity stagger"))


def permission_lock(account):
    return state_file_lock(models.CONFIG_FILE) if account.provider == "antigravity" else nullcontext()


def wait_between_polls(seconds, directory=None):
    """Targeted deadline checks without increasing normal provider polling."""
    if not status(directory=directory)["enabled"]:
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        delay = min(remaining, 60, wake_seconds(remaining, directory))
        time.sleep(delay)
        try:
            daemon_step(Config.load(), directory=directory)
        except Exception as exc:
            from . import cli
            cli._daemon_log("stagger_error", error=str(exc))


def _recover_guard(data, config, directory):
    """Explicit read-only reconciliation; never send a replacement generation."""
    from . import cli, antigravity_automation
    from .action_state import account_action
    runtime = data["runtime"]
    attempt = runtime.get("attempt")
    if not attempt or attempt["outcome"] == "confirmed":
        raise StaggerError("No unresolved stagger attempt to recover.")
    accounts = members(config, data["group"])
    account = accounts[attempt["family"]]
    root = directory if directory is not None else models.CONFIG_DIR
    native_path = antigravity_automation._attempt_path(root, account)
    action_path = root / "actions" / (hashlib.sha256(account_key_string(account).encode()).hexdigest() + ".json")
    latest = attempt["at"]
    prior = None
    for file, field in ((native_path, "attempted_at"), (action_path, "started_at")):
        try:
            record = json.loads(file.read_text())
        except FileNotFoundError:
            continue
        value = record.get(field)
        if not _number(value):
            raise StaggerError("Invalid action evidence; inspect the state before recovery.")
        latest = max(latest, value)
        if file == native_path:
            prior = record
    if time.time() < latest + PERIOD:
        raise StaggerError("Recovery requires a full five-hour cooldown after the last possible request.")
    readings = read_pair(accounts, config, cli._fetch_status)
    observations = {f: observation(a, readings[f], time.time()) for f, a in accounts.items()}
    # Recover only the affected action; never clear another family's guards.
    with account_action(root, account_key_string(account), recover=True):
        if prior is not None:
            anchor = observations[account.antigravity_family]["anchor"]
            prior["retry_after"] = max(time.time(), latest + PERIOD, (anchor + PERIOD) if anchor else 0)
            atomic_write_text(native_path, json.dumps(prior))
    attempt["outcome"] = "confirmed"
    attempt["recovery"] = "explicit_cooldown_and_native_reconciliation"
    data["runtime"] = decide(runtime, observations, time.time())
