"""Shared auto-kick switch semantics for mutations and their previews."""

import hashlib
import json
from collections.abc import Mapping

FIELDS = ("auto_kick", "weekly_auto_kick", "session_auto_kick")
POLICY_VERSION = 2


def controls(value) -> dict[str, bool]:
    return {
        field: bool(value[field] if isinstance(value, Mapping) else getattr(value, field))
        for field in FIELDS
    }


def transition(value, scope: str, enabled: bool) -> dict[str, bool]:
    """Master pauses/resumes saved windows; enabling a window resumes master."""
    if scope not in {"all", "weekly", "session"}:
        raise ValueError("Unknown auto-kick scope")
    result = controls(value)
    if scope == "all":
        result["auto_kick"] = enabled
    else:
        result[f"{scope}_auto_kick"] = enabled
        if enabled:
            result["auto_kick"] = True
    return result


def window_state(master: bool, selected: bool) -> str:
    return "disabled" if not selected else "enabled" if master else "paused"


def fingerprint(target: dict) -> str:
    # Old previews must not authorize the new window-enables-master semantics.
    payload = {"policy_version": POLICY_VERSION, "account": target}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
