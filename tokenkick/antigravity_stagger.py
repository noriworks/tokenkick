"""Guarded one-time second-family kick; no recurring controller or setting edits.

Run via ``python -m tokenkick.antigravity_stagger --gemini-reset EPOCH`` to
validate. ``--execute`` authorizes one normal Claude/GPT kick at the due time.
"""

import argparse
import json
import math
import time

from .antigravity_automation import usable_status
from .antigravity_windows import native_window, verified_unanchored
from .models import Config, account_key_string
from .scheduling import schedule_for_account

OFFSET_SECONDS = 150 * 60
MAX_LATENESS_SECONDS = 10 * 60


def timing_blocker(gemini_reset, now):
    if not math.isfinite(gemini_reset):
        return "invalid_anchor"
    due = gemini_reset - OFFSET_SECONDS
    if now < due:
        return "not_due"
    if now > due + MAX_LATENESS_SECONDS:
        return "missed_window"
    return None


def prepare(config, gemini_reset, *, now, fetch, eligibility, pending):
    """Return a target only when the original Gemini anchor is still valid."""
    blocker = timing_blocker(gemini_reset, now)
    if blocker:
        return None, blocker
    accounts = {}
    for family in ("gemini", "claude_gpt"):
        matches = [
            a
            for a in config.accounts
            if a.provider == "antigravity" and a.antigravity_family == family
        ]
        if len(matches) != 1:
            return None, "ambiguous_accounts"
        a = accounts[family] = matches[0]
        if a.auto_kick or a.weekly_auto_kick or a.session_auto_kick:
            return None, "auto_controls_changed"
        if schedule_for_account(config.schedule, a.label) or account_key_string(a) in pending:
            return None, "competing_schedule"
    gemini = fetch(accounts["gemini"], config)
    claude = fetch(accounts["claude_gpt"], config)
    if not all(
        usable_status(accounts[f], s) for f, s in (("gemini", gemini), ("claude_gpt", claude))
    ):
        return None, "fresh_native_quota_required"
    window = native_window(gemini, "gemini")
    if window is None or abs(window["resets_at"] - gemini_reset) > 1:
        return None, "gemini_anchor_changed"
    if not verified_unanchored(claude, "claude_gpt"):
        return None, "claude_already_started_or_unknown"
    decision = eligibility(accounts["claude_gpt"], claude)
    if not decision.kickable:
        return None, decision.reason
    return (accounts["claude_gpt"], claude), None


def run_once(gemini_reset, *, execute=False):
    from . import cli

    blocker = timing_blocker(gemini_reset, time.time())
    if blocker:
        return {"result": "skipped", "reason": blocker, "generation_sent": False}
    config = Config.load()
    target, blocker = prepare(
        config,
        gemini_reset,
        now=time.time(),
        fetch=cli._fetch_status,
        eligibility=cli._kick_eligibility,
        pending=cli.load_pending_kicks(),
    )
    if blocker:
        return {"result": "skipped", "reason": blocker, "generation_sent": False}
    if not execute:
        return {"result": "would_kick", "generation_sent": False}
    # Recheck time after native reads; never catch up a missed window by forcing.
    blocker = timing_blocker(gemini_reset, time.time())
    if blocker:
        return {"result": "skipped", "reason": blocker, "generation_sent": False}
    account, status = target
    event = cli._kick_and_notify(
        account, config, status, kick_type="session", force=False, send_notification=False
    )
    return {
        "result": "confirmed" if event.success and event.confirmed else "unconfirmed_or_failed",
        "request_success": event.success,
        "error": event.error,
        "model": event.kick_model,
        "followup_scheduled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gemini-reset", type=float, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_once(args.gemini_reset, execute=args.execute)))


if __name__ == "__main__":
    main()
