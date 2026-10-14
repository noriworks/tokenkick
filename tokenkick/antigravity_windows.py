"""Evidence for Antigravity's idle horizons versus fixed reset clocks."""

from dataclasses import replace
from datetime import datetime
import math

from .models import AccountState, AccountStatus, is_synthetic_status

OBSERVATION_DELAY_SECONDS = 6
MIN_OBSERVATION_SECONDS = 5
MAX_OBSERVATION_SECONDS = 120
CLOCK_TOLERANCE_SECONDS = 1
HORIZON_TOLERANCE_SECONDS = 3


def observed_epoch(status: AccountStatus) -> float | None:
    try:
        observed = datetime.fromisoformat(status.observed_at.replace("Z", "+00:00"))
        return observed.timestamp() if observed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def native_window(status: AccountStatus, family: str, kind: str = "session") -> dict | None:
    if (
        status.source_detail != "antigravity-cli"
        or is_synthetic_status(status)
        or status.stale
        or status.error
        or status.state == AccountState.UNKNOWN
        or not isinstance(status.quota_windows, list)
    ):
        return None
    matches = [
        w
        for w in status.quota_windows
        if isinstance(w, dict) and w.get("family") == family and w.get("window_kind") == kind
    ]
    if len(matches) != 1:
        return None
    window = matches[0]
    if window.get("reset_evidence") != "provider_timestamp":
        return None
    for field in ("resets_at", "used_percent", "window_minutes"):
        value = window.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return None
    if not 0 <= window["used_percent"] <= 100 or window["window_minutes"] != (
        300 if kind == "session" else 10080
    ):
        return None
    return window


def unused_full_horizon(status: AccountStatus, family: str) -> bool:
    window = native_window(status, family)
    observed = observed_epoch(status)
    return bool(
        window
        and observed is not None
        and window["used_percent"] == 0
        and abs(window["resets_at"] - observed - 18000) <= HORIZON_TOLERANCE_SECONDS
    )


def needs_idle_observation(status: AccountStatus) -> bool:
    return any(unused_full_horizon(status, family) for family in ("gemini", "claude_gpt"))


def pair_elapsed(before: AccountStatus, after: AccountStatus) -> float | None:
    start, end = observed_epoch(before), observed_epoch(after)
    if start is None or end is None:
        return None
    elapsed = end - start
    return elapsed if MIN_OBSERVATION_SECONDS <= elapsed <= MAX_OBSERVATION_SECONDS else None


def classify_session_clocks(before: AccountStatus, after: AccountStatus) -> AccountStatus:
    """Only two independent native reads can identify an idle sliding horizon."""
    elapsed = pair_elapsed(before, after)
    windows = [dict(w) for w in after.quota_windows or [] if isinstance(w, dict)]
    for window in windows:
        if window.get("window_kind") != "session":
            continue
        window["anchor_state"] = "unknown"
        window.pop("anchor_evidence", None)
        family = window.get("family")
        first, second = native_window(before, family), native_window(after, family)
        if elapsed is None or first is None or second is None:
            continue
        movement = second["resets_at"] - first["resets_at"]
        if (
            unused_full_horizon(before, family)
            and unused_full_horizon(after, family)
            and abs(movement - elapsed) <= CLOCK_TOLERANCE_SECONDS
        ):
            window["anchor_state"] = "available_unanchored"
            window["anchor_evidence"] = {
                "kind": "sliding_native_deadline",
                "previous_observed_at": before.observed_at,
                "observed_at": after.observed_at,
                "previous_reset": first["resets_at"],
            }
        elif abs(movement) <= CLOCK_TOLERANCE_SECONDS and second["resets_at"] > observed_epoch(
            after
        ):
            window["anchor_state"] = "anchored"
    # Clear aggregate evidence; projection selects only this account's session.
    return replace(after, quota_windows=windows, window_anchor_state=None)


def verified_unanchored(status: AccountStatus, family: str) -> bool:
    window = native_window(status, family)
    if window is None or window.get("anchor_state") != "available_unanchored":
        return False
    proof = window.get("anchor_evidence")
    if not isinstance(proof, dict) or proof.get("kind") != "sliding_native_deadline":
        return False
    previous_reset = proof.get("previous_reset")
    if (
        isinstance(previous_reset, bool)
        or not isinstance(previous_reset, (int, float))
        or not math.isfinite(previous_reset)
    ):
        return False
    previous_window = {**window, "resets_at": previous_reset}
    previous = replace(
        status, observed_at=proof.get("previous_observed_at"), quota_windows=[previous_window]
    )
    elapsed = pair_elapsed(previous, status)
    return (
        proof.get("observed_at") == status.observed_at
        and elapsed is not None
        and unused_full_horizon(previous, family)
        and unused_full_horizon(status, family)
        and abs(window["resets_at"] - previous_reset - elapsed) <= CLOCK_TOLERANCE_SECONDS
    )
