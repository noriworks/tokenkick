"""Conservative, family-scoped Antigravity automation guards."""

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .antigravity import antigravity_family_status
from .antigravity_windows import (
    CLOCK_TOLERANCE_SECONDS, native_window, observed_epoch, pair_elapsed, verified_unanchored,
)
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
        and status.label == account.label
        and -30 <= age <= 120
        and not status.stale
        and not status.error
        and not is_synthetic_status(status)
        and status.source_detail == "antigravity-cli"
        and projected.state != AccountState.UNKNOWN
        and projected == status
        and native_window(status, account.antigravity_family, "weekly") is not None
        and native_window(status, account.antigravity_family, "session") is not None
    )


def _attempt_path(directory: Path, account: AccountConfig) -> Path:
    digest = hashlib.sha256(account_key_string(account).encode()).hexdigest()
    return directory / "antigravity-attempts" / f"{digest}.json"


def attempt_retry_at(directory: Path, account: AccountConfig) -> float | None:
    """Return the durable earliest next attempt; uncertainty has no automatic retry."""
    try:
        prior = json.loads(_attempt_path(directory, account).read_text())
        retry_after = prior["retry_after"]
        if retry_after is None:
            raise ValueError("Previous Antigravity request is unconfirmed; explicit recovery required.")
        if isinstance(retry_after, bool) or isinstance(prior["attempted_at"], bool):
            raise ValueError("Invalid Antigravity attempt guard.")
        retry_after, attempted_at = float(retry_after), float(prior["attempted_at"])
        if not math.isfinite(retry_after) or not math.isfinite(attempted_at) or retry_after < attempted_at + 300 * 60:
            raise ValueError("Invalid Antigravity attempt guard.")
        return retry_after
    except FileNotFoundError:
        return None
    except (OSError, TypeError, KeyError) as exc:
        raise ValueError("Antigravity attempt guard cannot be read safely.") from exc


def attempt_blocked(directory: Path, account: AccountConfig, status: AccountStatus) -> bool:
    """Unconfirmed attempts require manual recovery, even after history rotation."""
    try:
        retry_at = attempt_retry_at(directory, account)
        return retry_at is not None and time.time() < retry_at
    except ValueError:
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


def anchor_confirmed(
    before: AccountStatus, after: AccountStatus, *, session: bool,
    verification: AccountStatus | None = None, family: str | None = None,
) -> bool:
    """Require a new deadline that stays fixed across independent native reads."""
    if verification is None or family not in {"gemini", "claude_gpt"}:
        return False
    if pair_elapsed(after, verification) is None:
        return False
    kind = "session" if session else "weekly"
    first, second = native_window(after, family, kind), native_window(verification, family, kind)
    if first is None or second is None:
        return False
    new = second["resets_at"]
    if (new <= max(time.time(), observed_epoch(verification))
            or abs(new - first["resets_at"]) > CLOCK_TOLERANCE_SECONDS):
        return False
    old = before.session_resets_at if session else before.resets_at
    if session and verified_unanchored(before, family):
        return new >= observed_epoch(before) + 18000 - 3
    return old is not None and new > old + 60
