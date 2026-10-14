"""Read-only provider evidence reconciles weekly history independently of sessions."""

import hashlib
import json
import math
import time
from datetime import datetime

from .models import (
    AccountConfig, AccountState, AccountStatus, DataSource, KickEvent,
    account_key_string, codex_rate_limit_id_for_account, is_synthetic_status,
    update_kick_history,
)

WEEK_SECONDS = 7 * 24 * 60 * 60
MATCH_SECONDS = 30
STABILITY_SECONDS = 30
FRESH_SECONDS = 120
PENDING_ERROR = "Weekly reset-clock confirmation pending; waiting for stable native observations."


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def epoch(value) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def fingerprint(account: AccountConfig) -> str:
    identity = [account_key_string(account), account.identity_provider_id, account.identity_email]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def native_observation(account: AccountConfig, status: AccountStatus, now: float):
    observed = epoch(status.observed_at)
    if (account.provider != "codex" or account.source != DataSource.CODEX_DIRECT
            or status.label != account.label or status.stale or status.error
            or status.state == AccountState.UNKNOWN or is_synthetic_status(status)
            or status.source_detail != "codex-provider-usage"
            or status.window_minutes != 10080
            or status.codex_rate_limit_id != codex_rate_limit_id_for_account(account)
            or observed is None or not 0 <= now - observed <= FRESH_SECONDS
            or not finite(status.resets_at) or status.resets_at <= now):
        return None
    return observed, status.resets_at


def prepare_attempt(account: AccountConfig, event: KickEvent, before: AccountStatus | None):
    event.codex_account_fingerprint = fingerprint(account)
    if before is not None and native_observation(account, before, time.time()):
        event.codex_pre_weekly_resets_at = before.resets_at


def interval(event: KickEvent):
    start, finish = event.codex_attempt_started_at, event.codex_attempt_finished_at
    if (not finite(start) or not finite(finish) or not finite(event.timestamp)
            or not 0 <= finish - start <= 120 or abs(finish - event.timestamp) > 1):
        return None
    return start, finish


def match_delta(event: KickEvent, anchor: float) -> float:
    bounds = interval(event)
    if bounds is None:
        return float("inf")
    start, finish = bounds
    return max(start - anchor, anchor - finish, 0)


def _weekly(event: KickEvent) -> bool:
    return (event.kick_type or event.kind) == "kick" and bool(event.codex_surface)


def _eligible(event: KickEvent, account: AccountConfig, observed: float, reset: float, now: float):
    bounds = interval(event)
    return bool(
        _weekly(event) and event.label == account.label and event.success
        and not event.confirmed and event.dispatch_state == "sent"
        and event.post_kick_status != "superseded"
        and (event.evidence_response or event.evidence_tokens or event.response_text)
        and bounds and now - WEEK_SECONDS <= bounds[0] <= bounds[1] <= observed
        and event.codex_account_fingerprint in {None, fingerprint(account)}
        and (event.codex_pre_weekly_resets_at is None
             or finite(event.codex_pre_weekly_resets_at)
             and abs(event.codex_pre_weekly_resets_at - reset) > 1)
        and match_delta(event, reset - WEEK_SECONDS) <= MATCH_SECONDS
    )


def observe_attempt(account: AccountConfig, event: KickEvent, status: AccountStatus, now: float) -> bool:
    """Record a first observation or confirm a stable deadline; no provider calls."""
    observation = native_observation(account, status, now)
    if observation is None:
        return False
    observed, reset = observation
    if not _eligible(event, account, observed, reset, now):
        return False
    first = epoch(event.codex_weekly_evidence_observed_at)
    prior_reset = event.codex_weekly_evidence_reset_at
    if first is not None and observed <= first:
        return False
    event.codex_account_fingerprint = fingerprint(account)
    if first is None or not finite(prior_reset) or abs(prior_reset - reset) > 1:
        event.codex_weekly_evidence_observed_at = status.observed_at
        event.codex_weekly_evidence_reset_at = reset
        return False
    if observed - first < STABILITY_SECONDS:
        return False
    event.confirmed = True
    event.evidence_provider_moved = True
    event.post_kick_status = "moved"
    event.codex_confirmation_method = "weekly_reset_clock"
    # This is timing evidence, not exclusive causal attribution of concurrent usage.
    event.codex_attribution = "timing_match"
    event.codex_provider_observed_at = status.observed_at
    event.codex_provider_stale = False
    event.codex_provider_weekly_resets_at = reset
    event.codex_weekly_inferred_anchor_at = reset - WEEK_SECONDS
    event.codex_weekly_anchor_match_delta_seconds = match_delta(event, reset - WEEK_SECONDS)
    event.error = None
    return True


def reconcile(account: AccountConfig, status: AccountStatus, *, now=None) -> list[KickEvent]:
    now = time.time() if now is None else now
    observation = native_observation(account, status, now)
    if observation is None:
        return []
    observed, reset = observation
    winners = []

    def update(events):
        changed = False
        # A native deadline that moves invalidates earlier stability evidence,
        # even when its new anchor no longer matches any of our requests.
        for event in events:
            first = epoch(event.codex_weekly_evidence_observed_at)
            prior_reset = event.codex_weekly_evidence_reset_at
            if (event.label == account.label and _weekly(event) and not event.confirmed
                    and event.codex_account_fingerprint == fingerprint(account)
                    and first is not None and observed > first and finite(prior_reset)
                    and abs(prior_reset - reset) > 1):
                event.codex_weekly_evidence_observed_at = None
                event.codex_weekly_evidence_reset_at = None
                changed = True
        # Never attribute the same weekly window to more than one attempt.
        if any(e.label == account.label and _weekly(e) and e.confirmed
               and finite(e.codex_provider_weekly_resets_at)
               and abs(e.codex_provider_weekly_resets_at - reset) <= 1 for e in events):
            return changed
        candidates = [e for e in events if _eligible(e, account, observed, reset, now)]
        candidates.sort(key=lambda e: match_delta(e, reset - WEEK_SECONDS))
        if not candidates:
            return changed
        if len(candidates) > 1 and abs(match_delta(candidates[0], reset - WEEK_SECONDS)
                                       - match_delta(candidates[1], reset - WEEK_SECONDS)) <= 1:
            return changed  # Overlapping requests cannot be attributed uniquely.
        winner = candidates[0]
        before = winner.to_dict()
        if observe_attempt(account, winner, status, now):
            winners.append(winner)
            for event in events:
                if (event is not winner and _weekly(event) and event.label == account.label
                        and winner.codex_cluster_id is not None
                        and event.codex_cluster_id == winner.codex_cluster_id
                        and event.success and not event.confirmed
                        and event.timestamp > winner.timestamp):
                    event.post_kick_status = "superseded"
        return changed or before != winner.to_dict()

    update_kick_history(update)
    return winners
