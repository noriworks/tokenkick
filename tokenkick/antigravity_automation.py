"""Conservative, family-scoped Antigravity automation guards."""

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .antigravity import antigravity_family_status
from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    account_key_string,
    is_synthetic_status,
)
from .state_io import atomic_write_text


def usable_status(account: AccountConfig, status: AccountStatus) -> bool:
    try:
        observed = datetime.fromisoformat(status.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = time.time() - observed.timestamp()
    except (AttributeError, ValueError, TypeError):
        return False
    projected = antigravity_family_status(status, account.antigravity_family)
    return (
        account.antigravity_family in {"gemini", "claude_gpt"}
        and -30 <= age <= 120
        and not status.stale
        and not status.error
        and not is_synthetic_status(status)
        and status.source_detail == "antigravity-cli"
        and projected.state != AccountState.UNKNOWN
        and projected == status
    )


def _attempt_path(directory: Path, account: AccountConfig) -> Path:
    digest = hashlib.sha256(account_key_string(account).encode()).hexdigest()
    return directory / "antigravity-attempts" / f"{digest}.json"


def attempt_blocked(directory: Path, account: AccountConfig, status: AccountStatus) -> bool:
    """Unconfirmed attempts require manual recovery, even after history rotation."""
    try:
        prior = json.loads(_attempt_path(directory, account).read_text())
        retry_after = prior["retry_after"]
        if retry_after is None:
            return True
        retry_after, attempted_at = float(retry_after), float(prior["attempted_at"])
        if not math.isfinite(retry_after) or not math.isfinite(attempted_at) or retry_after < attempted_at + 300 * 60:
            return True
        return time.time() < retry_after
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError, KeyError):
        return True


def record_attempt(directory: Path, account: AccountConfig, status: AccountStatus) -> None:
    atomic_write_text(
        _attempt_path(directory, account),
        json.dumps(
            {
                "attempted_at": time.time(),
                "session_reset": status.session_resets_at,
                "retry_after": None,
            }
        ),
    )


def record_confirmation(directory: Path, account: AccountConfig, status: AccountStatus) -> None:
    """Permit a subsequent cycle only after the observed session has reset."""
    path = _attempt_path(directory, account)
    prior = json.loads(path.read_text())
    prior["retry_after"] = max(prior["attempted_at"] + 300 * 60, status.session_resets_at)
    atomic_write_text(path, json.dumps(prior))


def anchor_confirmed(before: AccountStatus, after: AccountStatus, *, session: bool) -> bool:
    """Require a newly advanced, future reset clock for the requested window."""
    old = before.session_resets_at if session else before.resets_at
    new = after.session_resets_at if session else after.resets_at
    return old is not None and new is not None and new > max(old + 60, time.time())
