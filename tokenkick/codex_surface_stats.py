"""Adaptive Codex exec surface ordering."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .kicker import (
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
)
from .models import AccountConfig, KickEvent, account_key_string
from .state_io import locked_atomic_write_text

DEFAULT_CODEX_SURFACE_ORDER = (
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
)
CODEX_SURFACE_STATS_VERSION = 1
CODEX_SURFACE_SCORE_DECAY = 0.90
CODEX_SURFACE_SCORE_MIN = -10.0
CODEX_SURFACE_SCORE_MAX = 10.0
CODEX_ATTRIBUTION_STRONG = "strong"
CODEX_ATTRIBUTION_TIMING_MATCH = "timing_match"
CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE = "external_possible"
CODEX_SURFACE_STATE_ACTIVE = "active"
CODEX_SURFACE_STATE_DEMOTED = "demoted"
CODEX_SURFACE_STATE_FORCE_KEPT = "force-kept"
CODEX_SURFACE_STATE_FORCE_PRUNED = "force-pruned"
CODEX_SURFACE_STATE_RESCUE_COOLDOWN = "active_rescue_cooldown"
CODEX_SURFACE_DISPLAY = {
    CODEX_KICK_SURFACE_LEGACY: {
        "label": "Plain Codex exec",
        "description": "Codex exec with the git-repo check skipped.",
    },
    CODEX_KICK_SURFACE_REPO_SKIP: {
        "label": "Test repo, skip check",
        "description": "TokenKick's stable test repo with the git-repo check skipped.",
    },
    CODEX_KICK_SURFACE_REPO: {
        "label": "Test repo, repo check",
        "description": "TokenKick's stable test repo with the normal repo check.",
    },
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE: {
        "label": "Codex home, skip check",
        "description": "The account's Codex home with the git-repo check skipped.",
    },
}


def load_codex_surface_stats(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"version": CODEX_SURFACE_STATS_VERSION, "accounts": {}}
    if not isinstance(data, dict) or data.get("version") != CODEX_SURFACE_STATS_VERSION:
        return {"version": CODEX_SURFACE_STATS_VERSION, "accounts": {}}
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        data["accounts"] = {}
    return data


def save_codex_surface_stats(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    locked_atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _surface_display(surface: str) -> dict[str, str]:
    display = CODEX_SURFACE_DISPLAY.get(surface)
    if isinstance(display, dict):
        return {
            "label": str(display.get("label") or surface),
            "description": str(display.get("description") or surface),
        }
    return {"label": surface, "description": surface}


def codex_surface_order_for_account(account: AccountConfig, path: Path) -> tuple[str, ...]:
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account)
    return _codex_surface_order_from_stats(account, account_stats)


def _codex_surface_order_from_stats(
    account: AccountConfig,
    account_stats: dict[str, Any],
) -> tuple[str, ...]:
    ranked = _ranked_surface_order(account_stats)
    force_pruned = _surface_override_set(account.codex_surface_force_prune)
    force_kept = _surface_override_set(account.codex_surface_force_keep) - force_pruned
    demoted = _auto_demoted_surfaces(account_stats) if account.codex_surface_auto_demote else set()
    active = [
        surface
        for surface in ranked
        if surface not in force_pruned and (surface not in demoted or surface in force_kept)
    ]
    for surface in DEFAULT_CODEX_SURFACE_ORDER:
        if surface in force_kept and surface not in active:
            active.append(surface)
    return tuple(active)


def _ranked_surface_order(account_stats: dict[str, Any]) -> tuple[str, ...]:
    default_index = {surface: index for index, surface in enumerate(DEFAULT_CODEX_SURFACE_ORDER)}
    return tuple(
        sorted(
            DEFAULT_CODEX_SURFACE_ORDER,
            key=lambda surface: (
                -_surface_entry(account_stats, surface).get("score", 0.0),
                default_index[surface],
            ),
        )
    )


def update_codex_surface_stats(
    path: Path,
    account: AccountConfig,
    events: list[KickEvent],
) -> list[dict[str, Any]]:
    if not events:
        return []
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account, create=True)
    active_surfaces_before = list(_codex_surface_order_from_stats(account, account_stats))
    now = time.time()
    for surface in DEFAULT_CODEX_SURFACE_ORDER:
        entry = _surface_entry(account_stats, surface, create=True)
        entry["score"] = _clamp(float(entry.get("score", 0.0)) * CODEX_SURFACE_SCORE_DECAY)
    for event in events:
        surface = event.codex_surface
        if surface not in DEFAULT_CODEX_SURFACE_ORDER:
            continue
        entry = _surface_entry(account_stats, surface, create=True)
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_attempt_at"] = event.codex_attempt_finished_at or event.timestamp or now
        delta = _event_score_delta(event)
        if event.success and event.confirmed and _event_updates_surface_score(event):
            entry["confirmed"] = int(entry.get("confirmed", 0)) + 1
            entry["last_confirmed_at"] = event.codex_attempt_finished_at or event.timestamp or now
        elif event.success and event.confirmed:
            _record_weak_confirmation(entry, event, now)
        elif event.success and not _event_has_generation_evidence(event):
            entry["no_generation"] = int(entry.get("no_generation", 0)) + 1
        elif not event.success:
            entry["failures"] = int(entry.get("failures", 0)) + 1
        entry["score"] = _clamp(float(entry.get("score", 0.0)) + delta)
    demotion_events: list[dict[str, Any]] = []
    if account.codex_surface_auto_demote:
        _record_strong_cluster_for_demotion(account_stats, events, active_surfaces_before, now)
        demotion = _maybe_demote_surface(account, account_stats, now)
        if demotion is not None:
            demotion_events.append(demotion)
    save_codex_surface_stats(path, data)
    return demotion_events


def apply_codex_surface_late_confirmation(
    path: Path,
    account: AccountConfig,
    event: KickEvent,
) -> None:
    surface = event.codex_surface
    if surface not in DEFAULT_CODEX_SURFACE_ORDER:
        return
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account, create=True)
    entry = _surface_entry(account_stats, surface, create=True)
    attempts = int(entry.get("attempts", 0))
    if not _event_updates_surface_score(event):
        _record_weak_confirmation(entry, event, time.time())
        save_codex_surface_stats(path, data)
        return
    if attempts <= 0:
        entry["attempts"] = 1
        score_delta = 5.0
    else:
        score_delta = 4.75
    entry["confirmed"] = int(entry.get("confirmed", 0)) + 1
    entry["last_confirmed_at"] = event.codex_attempt_finished_at or event.timestamp or time.time()
    entry["score"] = _clamp(float(entry.get("score", 0.0)) + score_delta)
    save_codex_surface_stats(path, data)


def learned_codex_surface_order_summary(account: AccountConfig, path: Path) -> str:
    order = codex_surface_order_for_account(account, path)
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account)
    parts = []
    for surface in order:
        entry = _surface_entry(account_stats, surface)
        state = _surface_state(account, account_stats, surface)["state"]
        parts.append(
            f"{surface} {state} score={float(entry.get('score', 0.0)):.2f} "
            f"confirmed={int(entry.get('confirmed', 0))}/{int(entry.get('attempts', 0))}"
        )
    return ", ".join(parts)


def codex_surface_stats_for_account(account: AccountConfig, path: Path) -> dict[str, Any]:
    """Return read-only adaptive surface stats for one Codex account."""
    order = codex_surface_order_for_account(account, path)
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account)
    ranked = {surface: index + 1 for index, surface in enumerate(order)}
    demotion_state = _demotion_state(account_stats)
    surfaces = []
    for surface in DEFAULT_CODEX_SURFACE_ORDER:
        entry = _surface_entry(account_stats, surface)
        state = _surface_state(account, account_stats, surface)
        display = _surface_display(surface)
        surfaces.append(
            {
                "surface": surface,
                "surface_label": display["label"],
                "surface_description": display["description"],
                "rank": ranked.get(surface),
                "state": state["state"],
                "demotion_reason": state.get("demotion_reason"),
                "rescue_cooldown_remaining_strong_clusters": state.get(
                    "rescue_cooldown_remaining_strong_clusters"
                ),
                "score": float(entry.get("score", 0.0)),
                "attempts": int(entry.get("attempts", 0)),
                "confirmed": int(entry.get("confirmed", 0)),
                "timing_matches": int(entry.get("timing_matches", 0)),
                "external_possible": int(entry.get("external_possible", 0)),
                "no_generation": int(entry.get("no_generation", 0)),
                "failures": int(entry.get("failures", 0)),
                "last_attempt_at": entry.get("last_attempt_at"),
                "last_confirmed_at": entry.get("last_confirmed_at"),
            }
        )
    surfaces.sort(key=lambda item: item["rank"] if item["rank"] is not None else 999)
    return {
        "schema_version": CODEX_SURFACE_STATS_VERSION,
        "read_only": True,
        "label": account.label,
        "account_key": account_key_string(account),
        "order": list(order),
        "demotion": {
            "enabled": account.codex_surface_auto_demote,
            "after_strong_clusters": account.codex_surface_demote_after_strong_clusters,
            "min_active_surfaces": account.codex_surface_demote_min_active_surfaces,
            "min_kept_anchor_rate": account.codex_surface_demote_min_kept_anchor_rate,
            "measurement_clusters": account.codex_surface_demote_measurement_clusters,
            "rescue_cooldown_strong_clusters": account.codex_surface_rescue_cooldown_strong_clusters,
            "force_keep": list(account.codex_surface_force_keep),
            "force_prune": list(account.codex_surface_force_prune),
            "strong_cluster_count": int(demotion_state.get("strong_cluster_count", 0)),
            "demoted": demotion_state.get("demoted", {}),
            "rescues": demotion_state.get("rescues", {}),
            "last_reintroduction": demotion_state.get("last_reintroduction"),
        },
        "surface_descriptions": {
            surface: _surface_display(surface)
            for surface in DEFAULT_CODEX_SURFACE_ORDER
        },
        "demotion_evidence": _demotion_evidence_report(account, account_stats),
        "surfaces": surfaces,
    }


def _demotion_evidence_report(
    account: AccountConfig,
    account_stats: dict[str, Any],
) -> list[dict[str, Any]]:
    demotion_state = _demotion_state(account_stats)
    demoted = demotion_state.get("demoted")
    if not isinstance(demoted, dict) or not demoted:
        return []

    evidence: list[dict[str, Any]] = []
    for surface in DEFAULT_CODEX_SURFACE_ORDER:
        raw_entry = demoted.get(surface)
        if not isinstance(raw_entry, dict):
            continue
        measurement = int(
            raw_entry.get("measurement_clusters")
            or account.codex_surface_demote_measurement_clusters
            or 1
        )
        clusters = _recent_strong_clusters_for_surface(demotion_state, surface, measurement)
        demoted_at_count = raw_entry.get("strong_cluster_count")
        if isinstance(demoted_at_count, int):
            clusters = [
                cluster
                for cluster in clusters
                if int(cluster.get("strong_cluster_count") or 0) <= demoted_at_count
            ][-measurement:]

        cluster_reports = [
            _demotion_cluster_evidence(surface, index, cluster)
            for index, cluster in enumerate(clusters, start=1)
        ]
        surface_wins = sum(1 for item in cluster_reports if item["surface_won"])
        kept_ahead_wins = sum(1 for item in cluster_reports if item["winner_kept_ahead"])
        eligible_clusters = len(cluster_reports)
        kept_rate = kept_ahead_wins / eligible_clusters if eligible_clusters else 0.0
        kept_ahead_surfaces = raw_entry.get("kept_ahead_surfaces")
        if not isinstance(kept_ahead_surfaces, list):
            kept_ahead_surfaces = _kept_ahead_surfaces_from_clusters(surface, clusters)
        display = _surface_display(surface)
        evidence.append(
            {
                "surface": surface,
                "surface_label": display["label"],
                "surface_description": display["description"],
                "decision": "skip_for_now",
                "reason": raw_entry.get("reason"),
                "demoted_at": raw_entry.get("timestamp"),
                "demoted_at_strong_cluster_count": raw_entry.get("strong_cluster_count"),
                "eligible_clusters": eligible_clusters,
                "surface_strong_wins": surface_wins,
                "kept_ahead_wins": kept_ahead_wins,
                "kept_ahead_rate": kept_rate,
                "kept_ahead_surfaces": [
                    candidate
                    for candidate in kept_ahead_surfaces
                    if candidate in DEFAULT_CODEX_SURFACE_ORDER
                ],
                "policy": {
                    "after_strong_clusters": account.codex_surface_demote_after_strong_clusters,
                    "min_active_surfaces": account.codex_surface_demote_min_active_surfaces,
                    "min_kept_anchor_rate": account.codex_surface_demote_min_kept_anchor_rate,
                    "measurement_clusters": account.codex_surface_demote_measurement_clusters,
                },
                "recent_clusters": cluster_reports,
            }
        )
    return evidence


def _demotion_cluster_evidence(
    surface: str,
    index: int,
    cluster: dict[str, Any],
) -> dict[str, Any]:
    active = _cluster_ordered_surfaces(cluster, "active_surfaces")
    attempted = _cluster_ordered_surfaces(cluster, "attempted_surfaces")
    winner = cluster.get("winner")
    kept_ahead = active[: active.index(surface)] if surface in active else []
    return {
        "index": index,
        "cluster_id": cluster.get("cluster_id"),
        "timestamp": cluster.get("timestamp"),
        "strong_cluster_count": cluster.get("strong_cluster_count"),
        "winner": winner if winner in DEFAULT_CODEX_SURFACE_ORDER else None,
        "surface_active": surface in active,
        "surface_attempted": surface in attempted,
        "surface_won": winner == surface,
        "kept_ahead_surfaces": kept_ahead,
        "winner_kept_ahead": winner in kept_ahead,
        "active_surfaces": active,
        "attempted_surfaces": attempted,
    }


def _cluster_ordered_surfaces(cluster: dict[str, Any], key: str) -> list[str]:
    values = cluster.get(key)
    if not isinstance(values, list):
        return []
    return [
        surface
        for surface in values
        if surface in DEFAULT_CODEX_SURFACE_ORDER
    ]


def _kept_ahead_surfaces_from_clusters(
    surface: str,
    clusters: list[dict[str, Any]],
) -> list[str]:
    for cluster in reversed(clusters):
        active = _cluster_ordered_surfaces(cluster, "active_surfaces")
        if surface in active:
            return active[: active.index(surface)]
    return []


def reintroduce_codex_surfaces_after_miss(
    path: Path,
    account: AccountConfig,
    *,
    reason: str,
    now: float | None = None,
) -> list[dict[str, Any]]:
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account, create=True)
    demotion_state = _demotion_state(account_stats, create=True)
    demoted = demotion_state.get("demoted")
    if not isinstance(demoted, dict) or not demoted:
        return []
    timestamp = now or time.time()
    strong_count = int(demotion_state.get("strong_cluster_count", 0))
    surfaces = sorted(demoted)
    rescues = demotion_state.setdefault("rescues", {})
    if not isinstance(rescues, dict):
        rescues = {}
        demotion_state["rescues"] = rescues
    for surface in surfaces:
        rescues[surface] = {
            "rescued_at": timestamp,
            "rescued_at_strong_cluster_count": strong_count,
            "reason": reason,
        }
    demotion_state["demoted"] = {}
    demotion_state["last_reintroduction"] = {
        "surfaces": surfaces,
        "reason": reason,
        "timestamp": timestamp,
        "strong_cluster_count": strong_count,
    }
    save_codex_surface_stats(path, data)
    return [
        {
            "account_label": account.label,
            "account_key": account_key_string(account),
            "surfaces": surfaces,
            "reason": reason,
            "timestamp": timestamp,
            "strong_cluster_count": strong_count,
        }
    ]


def reset_codex_surface_demotion_evidence(path: Path, account: AccountConfig) -> None:
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account, create=True)
    account_stats["demotion"] = {
        "demoted": {},
        "strong_clusters": [],
        "strong_cluster_count": 0,
        "rescues": {},
    }
    save_codex_surface_stats(path, data)


def reset_codex_surface_learning_stats(path: Path, account: AccountConfig) -> None:
    data = load_codex_surface_stats(path)
    account_stats = _account_stats(data, account, create=True)
    for surface in DEFAULT_CODEX_SURFACE_ORDER:
        account_stats.pop(surface, None)
    save_codex_surface_stats(path, data)


def _record_strong_cluster_for_demotion(
    account_stats: dict[str, Any],
    events: list[KickEvent],
    active_surfaces: list[str],
    now: float,
) -> None:
    winner = _strong_cluster_winner(events)
    if winner is None:
        return
    demotion_state = _demotion_state(account_stats, create=True)
    strong_count = int(demotion_state.get("strong_cluster_count", 0)) + 1
    demotion_state["strong_cluster_count"] = strong_count
    clusters = demotion_state.setdefault("strong_clusters", [])
    if not isinstance(clusters, list):
        clusters = []
        demotion_state["strong_clusters"] = clusters
    clusters.append(
        {
            "winner": winner.codex_surface,
            "timestamp": winner.codex_attempt_finished_at or winner.timestamp or now,
            "cluster_id": winner.codex_cluster_id,
            "attempted_surfaces": [
                event.codex_surface
                for event in events
                if event.codex_surface in DEFAULT_CODEX_SURFACE_ORDER
            ],
            "active_surfaces": [
                surface for surface in active_surfaces if surface in DEFAULT_CODEX_SURFACE_ORDER
            ],
            "strong_cluster_count": strong_count,
        }
    )
    demotion_state["strong_clusters"] = clusters[-100:]


def _strong_cluster_winner(events: list[KickEvent]) -> KickEvent | None:
    winners = [
        event
        for event in events
        if event.success
        and event.confirmed
        and event.codex_surface in DEFAULT_CODEX_SURFACE_ORDER
        and event.post_kick_status != "superseded"
        and _event_updates_surface_score(event)
    ]
    if len(winners) != 1:
        return None
    return winners[0]


def _maybe_demote_surface(
    account: AccountConfig,
    account_stats: dict[str, Any],
    now: float,
) -> dict[str, Any] | None:
    demotion_state = _demotion_state(account_stats, create=True)
    active = list(_codex_surface_order_from_stats(account, account_stats))
    min_active = max(1, int(account.codex_surface_demote_min_active_surfaces))
    if len(active) <= min_active:
        return None
    candidate = active[-1]
    if candidate in _surface_override_set(account.codex_surface_force_keep):
        return None
    if _rescue_cooldown_remaining(account, demotion_state, candidate) > 0:
        return None
    if len(active) - 1 < min_active:
        return None

    measurement = max(1, int(account.codex_surface_demote_measurement_clusters))
    threshold = max(1, int(account.codex_surface_demote_after_strong_clusters))
    clusters = _recent_strong_clusters_for_surface(demotion_state, candidate, measurement)
    if len(clusters) < threshold:
        return None
    if any(cluster.get("winner") == candidate for cluster in clusters):
        return None

    candidate_index = active.index(candidate)
    kept_ahead = set(active[:candidate_index])
    if not kept_ahead:
        return None
    kept_hits = sum(1 for cluster in clusters if cluster.get("winner") in kept_ahead)
    kept_rate = kept_hits / len(clusters)
    if kept_hits < threshold or kept_rate < float(account.codex_surface_demote_min_kept_anchor_rate):
        return None

    demoted = demotion_state.setdefault("demoted", {})
    if not isinstance(demoted, dict):
        demoted = {}
        demotion_state["demoted"] = demoted
    strong_count = int(demotion_state.get("strong_cluster_count", 0))
    reason = (
        f"redundant: {candidate} had no strong anchors in {len(clusters)} eligible "
        f"strong clusters; kept-ahead anchor rate {kept_rate:.0%}"
    )
    demoted[candidate] = {
        "reason": reason,
        "timestamp": now,
        "strong_cluster_count": strong_count,
        "measurement_clusters": len(clusters),
        "kept_anchor_rate": kept_rate,
        "active_surfaces_before": active,
        "kept_ahead_surfaces": list(active[:candidate_index]),
        "kept_ahead_wins": kept_hits,
    }
    return {
        "account_label": account.label,
        "account_key": account_key_string(account),
        "surface": candidate,
        "reason": reason,
        "timestamp": now,
        "strong_cluster_count": strong_count,
        "active_surfaces_before": active,
        "active_surfaces_after": [surface for surface in active if surface != candidate],
    }


def _recent_strong_clusters_for_surface(
    demotion_state: dict[str, Any],
    surface: str,
    measurement: int,
) -> list[dict[str, Any]]:
    clusters = demotion_state.get("strong_clusters")
    if not isinstance(clusters, list):
        return []
    relevant = [
        cluster
        for cluster in clusters
        if isinstance(cluster, dict)
        and surface in _cluster_active_surfaces(cluster)
    ]
    return relevant[-measurement:]


def _cluster_active_surfaces(cluster: dict[str, Any]) -> set[str]:
    active = cluster.get("active_surfaces")
    if not isinstance(active, list):
        return set(DEFAULT_CODEX_SURFACE_ORDER)
    return {surface for surface in active if surface in DEFAULT_CODEX_SURFACE_ORDER}


def _demotion_state(
    account_stats: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any]:
    existing = account_stats.get("demotion")
    if isinstance(existing, dict):
        existing.setdefault("demoted", {})
        existing.setdefault("strong_clusters", [])
        existing.setdefault("strong_cluster_count", 0)
        existing.setdefault("rescues", {})
        return existing
    if not create:
        return {
            "demoted": {},
            "strong_clusters": [],
            "strong_cluster_count": 0,
            "rescues": {},
        }
    created = {
        "demoted": {},
        "strong_clusters": [],
        "strong_cluster_count": 0,
        "rescues": {},
    }
    account_stats["demotion"] = created
    return created


def _auto_demoted_surfaces(account_stats: dict[str, Any]) -> set[str]:
    demoted = _demotion_state(account_stats).get("demoted")
    if not isinstance(demoted, dict):
        return set()
    return {surface for surface in demoted if surface in DEFAULT_CODEX_SURFACE_ORDER}


def _surface_state(
    account: AccountConfig,
    account_stats: dict[str, Any],
    surface: str,
) -> dict[str, Any]:
    force_pruned = _surface_override_set(account.codex_surface_force_prune)
    force_kept = _surface_override_set(account.codex_surface_force_keep)
    demotion_state = _demotion_state(account_stats)
    if surface in force_pruned:
        return {"state": CODEX_SURFACE_STATE_FORCE_PRUNED}
    if surface in force_kept:
        return {"state": CODEX_SURFACE_STATE_FORCE_KEPT}
    demoted = demotion_state.get("demoted")
    if account.codex_surface_auto_demote and isinstance(demoted, dict) and surface in demoted:
        entry = demoted.get(surface) if isinstance(demoted.get(surface), dict) else {}
        return {
            "state": CODEX_SURFACE_STATE_DEMOTED,
            "demotion_reason": entry.get("reason"),
        }
    cooldown = _rescue_cooldown_remaining(account, demotion_state, surface)
    if cooldown > 0:
        return {
            "state": CODEX_SURFACE_STATE_RESCUE_COOLDOWN,
            "rescue_cooldown_remaining_strong_clusters": cooldown,
        }
    return {"state": CODEX_SURFACE_STATE_ACTIVE}


def _rescue_cooldown_remaining(
    account: AccountConfig,
    demotion_state: dict[str, Any],
    surface: str,
) -> int:
    rescues = demotion_state.get("rescues")
    if not isinstance(rescues, dict):
        return 0
    rescue = rescues.get(surface)
    if not isinstance(rescue, dict):
        return 0
    rescued_at = rescue.get("rescued_at_strong_cluster_count")
    if not isinstance(rescued_at, int):
        return 0
    strong_count = int(demotion_state.get("strong_cluster_count", 0))
    cooldown = max(0, int(account.codex_surface_rescue_cooldown_strong_clusters))
    return max(0, cooldown - (strong_count - rescued_at))


def _surface_override_set(values: list[str]) -> set[str]:
    return {surface for surface in values if surface in DEFAULT_CODEX_SURFACE_ORDER}


def _account_stats(
    data: dict[str, Any],
    account: AccountConfig,
    *,
    create: bool = False,
) -> dict[str, Any]:
    accounts = data.setdefault("accounts", {}) if create else data.get("accounts", {})
    if not isinstance(accounts, dict):
        if not create:
            return {}
        accounts = {}
        data["accounts"] = accounts
    key = account_key_string(account)
    existing = accounts.get(key)
    if isinstance(existing, dict):
        return existing
    if not create:
        return {}
    created: dict[str, Any] = {}
    accounts[key] = created
    return created


def _surface_entry(
    account_stats: dict[str, Any],
    surface: str,
    *,
    create: bool = False,
) -> dict[str, Any]:
    existing = account_stats.get(surface)
    if isinstance(existing, dict):
        return existing
    if not create:
        return {}
    created = {
        "score": 0.0,
        "attempts": 0,
        "confirmed": 0,
        "no_generation": 0,
        "failures": 0,
    }
    account_stats[surface] = created
    return created


def _event_score_delta(event: KickEvent) -> float:
    if event.post_kick_status == "superseded":
        return 0.0
    if event.success and event.confirmed and not _event_updates_surface_score(event):
        return 0.0
    if event.success and event.confirmed:
        return 5.0
    if not event.success:
        return -4.0
    if not _event_has_generation_evidence(event):
        return -2.0
    return 0.25


def _event_updates_surface_score(event: KickEvent) -> bool:
    return event.codex_attribution in {None, CODEX_ATTRIBUTION_STRONG}


def _record_weak_confirmation(entry: dict[str, Any], event: KickEvent, now: float) -> None:
    attribution = event.codex_attribution or CODEX_ATTRIBUTION_TIMING_MATCH
    key = (
        "external_possible"
        if attribution == CODEX_ATTRIBUTION_EXTERNAL_POSSIBLE
        else "timing_matches"
    )
    entry[key] = int(entry.get(key, 0)) + 1
    entry["last_weak_confirmed_at"] = event.codex_attempt_finished_at or event.timestamp or now


def _event_has_generation_evidence(event: KickEvent) -> bool:
    if event.response_text:
        return True
    return any(
        isinstance(value, int) and value > 0
        for value in (event.input_tokens, event.output_tokens, event.total_tokens)
    )


def _clamp(value: float) -> float:
    return max(CODEX_SURFACE_SCORE_MIN, min(CODEX_SURFACE_SCORE_MAX, value))
