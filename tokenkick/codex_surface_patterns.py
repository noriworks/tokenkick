"""Read-only Codex surface pattern analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .codex_surface_stats import DEFAULT_CODEX_SURFACE_ORDER
from .kicker import CODEX_KICK_SURFACES
from .models import KickEvent

CODEX_ATTRIBUTION_STRONG = "strong"
SCHEMA_VERSION = 1
MIN_REPORT_SAMPLES = 30
MIN_LIFT_SAMPLES = 50


@dataclass(frozen=True)
class SurfaceCluster:
    label: str
    started_at: float
    events: tuple[KickEvent, ...]
    cluster_id: str | None = None

    @property
    def winner(self) -> KickEvent | None:
        winners = [
            event
            for event in self.events
            if _strong_winner_event(event)
        ]
        if not winners:
            return None
        return min(winners, key=_event_time)

    @property
    def winner_surface(self) -> str | None:
        winner = self.winner
        return winner.codex_surface if winner else None


def build_codex_surface_patterns_report(
    events: list[KickEvent],
    *,
    label: str | None = None,
) -> dict[str, Any]:
    """Build a read-only walk-forward surface pattern report from history."""
    clusters = _codex_surface_clusters(events)
    if label:
        clusters = [cluster for cluster in clusters if cluster.label == label]
    ignored = _ignored_counts(clusters)
    eligible = [cluster for cluster in clusters if _eligible_cluster(cluster)]
    evaluated = _walk_forward_backtest(clusters, eligible)
    baseline = evaluated["baseline"]
    candidates = evaluated["candidates"]
    verdict = _verdict(evaluated["samples"], baseline, candidates)
    hints = _sequence_hints(eligible, verdict)
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "experimental": True,
        "scope_label": label,
        "eligible_clusters": len(eligible),
        "evaluated_samples": evaluated["samples"],
        "ignored": ignored,
        "baseline": baseline,
        "candidates": candidates,
        "verdict": verdict,
        "sequence_hints": hints,
    }


def _codex_surface_clusters(events: list[KickEvent]) -> list[SurfaceCluster]:
    session_events = [
        event
        for event in events
        if _is_codex_session_surface_event(event)
    ]
    session_events.sort(key=_event_time)
    grouped: dict[str, list[KickEvent]] = defaultdict(list)
    reconstructed: list[list[KickEvent]] = []
    active_by_label: dict[str, list[KickEvent]] = {}
    for event in session_events:
        if event.codex_cluster_id:
            grouped[event.codex_cluster_id].append(event)
            continue
        active = active_by_label.get(event.label)
        if (
            active is None
            or event.codex_attempt == 1
            or _event_time(event) - _event_time(active[-1]) > 20 * 60
        ):
            active = []
            reconstructed.append(active)
            active_by_label[event.label] = active
        active.append(event)

    clusters = [
        SurfaceCluster(
            label=cluster_events[0].label,
            started_at=min(_event_time(event) for event in cluster_events),
            events=tuple(sorted(cluster_events, key=_event_time)),
            cluster_id=cluster_id,
        )
        for cluster_id, cluster_events in grouped.items()
        if cluster_events
    ]
    clusters.extend(
        SurfaceCluster(
            label=cluster_events[0].label,
            started_at=min(_event_time(event) for event in cluster_events),
            events=tuple(sorted(cluster_events, key=_event_time)),
        )
        for cluster_events in reconstructed
        if cluster_events
    )
    clusters.sort(key=lambda cluster: cluster.started_at)
    return clusters


def _is_codex_session_surface_event(event: KickEvent) -> bool:
    if event.codex_surface not in CODEX_KICK_SURFACES:
        return False
    return (event.kick_type or event.kind) == "session"


def _eligible_cluster(cluster: SurfaceCluster) -> bool:
    return cluster.winner_surface in CODEX_KICK_SURFACES


def _strong_winner_event(event: KickEvent) -> bool:
    return (
        event.success
        and event.confirmed
        and event.codex_surface in CODEX_KICK_SURFACES
        and event.codex_attribution == CODEX_ATTRIBUTION_STRONG
    )


def _ignored_counts(clusters: list[SurfaceCluster]) -> dict[str, int]:
    counts = {
        "timing_match": 0,
        "external_possible": 0,
        "superseded": 0,
        "generated_unconfirmed": 0,
        "failed_or_no_generation": 0,
        "no_strong_winner": 0,
    }
    for cluster in clusters:
        if _eligible_cluster(cluster):
            continue
        counts["no_strong_winner"] += 1
        for event in cluster.events:
            if event.codex_attribution == "timing_match":
                counts["timing_match"] += 1
            elif event.codex_attribution == "external_possible":
                counts["external_possible"] += 1
            if event.post_kick_status == "superseded":
                counts["superseded"] += 1
            elif event.success and not event.confirmed and _has_generation_evidence(event):
                counts["generated_unconfirmed"] += 1
            elif not event.success or not _has_generation_evidence(event):
                counts["failed_or_no_generation"] += 1
    return counts


def _walk_forward_backtest(
    all_clusters: list[SurfaceCluster],
    eligible: list[SurfaceCluster],
) -> dict[str, Any]:
    predictors = {
        "baseline_per_account_score": [],
        "per_account_majority": [],
        "global_recency": [],
        "sequence_features": [],
    }
    eligible_by_start = {id(cluster): index for index, cluster in enumerate(eligible)}
    for target in eligible:
        eligible_index = eligible_by_start[id(target)]
        if eligible_index == 0:
            continue
        prior_eligible = eligible[:eligible_index]
        prior_all = [cluster for cluster in all_clusters if cluster.started_at < target.started_at]
        actual = target.winner_surface
        if actual is None:
            continue
        predictors["baseline_per_account_score"].append(
            _prediction_result(
                _baseline_order(prior_all, target.label),
                actual,
            )
        )
        predictors["per_account_majority"].append(
            _prediction_result(
                _per_account_majority_order(prior_eligible, target.label),
                actual,
            )
        )
        predictors["global_recency"].append(
            _prediction_result(
                _global_recency_order(prior_eligible),
                actual,
            )
        )
        predictors["sequence_features"].append(
            _prediction_result(
                _sequence_feature_order(prior_eligible, target),
                actual,
            )
        )

    metrics = {name: _metrics(results) for name, results in predictors.items()}
    baseline = metrics.pop("baseline_per_account_score")
    for candidate in metrics.values():
        _attach_lift(candidate, baseline)
    return {
        "samples": baseline["samples"],
        "baseline": baseline,
        "candidates": metrics,
    }


def _baseline_order(prior_clusters: list[SurfaceCluster], label: str) -> list[str]:
    scores_by_label: dict[str, dict[str, float]] = {}
    default_index = {surface: index for index, surface in enumerate(DEFAULT_CODEX_SURFACE_ORDER)}
    for cluster in prior_clusters:
        scores = scores_by_label.setdefault(
            cluster.label,
            {surface: 0.0 for surface in DEFAULT_CODEX_SURFACE_ORDER},
        )
        for surface in DEFAULT_CODEX_SURFACE_ORDER:
            scores[surface] = _clamp(scores[surface] * 0.90)
        for event in cluster.events:
            surface = event.codex_surface
            if surface not in DEFAULT_CODEX_SURFACE_ORDER:
                continue
            scores[surface] = _clamp(scores[surface] + _baseline_delta(event))
    scores = scores_by_label.get(label, {})
    return sorted(
        DEFAULT_CODEX_SURFACE_ORDER,
        key=lambda surface: (-scores.get(surface, 0.0), default_index[surface]),
    )


def _baseline_delta(event: KickEvent) -> float:
    if event.post_kick_status == "superseded":
        return 0.0
    if event.success and event.confirmed and event.codex_attribution not in {None, "strong"}:
        return 0.0
    if event.success and event.confirmed:
        return 5.0
    if not event.success:
        return -4.0
    if not _has_generation_evidence(event):
        return -2.0
    return 0.25


def _per_account_majority_order(prior: list[SurfaceCluster], label: str) -> list[str]:
    counts: Counter[str] = Counter()
    last_seen: dict[str, float] = {}
    for cluster in prior:
        if cluster.label != label:
            continue
        surface = cluster.winner_surface
        if surface is None:
            continue
        counts[surface] += 1
        last_seen[surface] = cluster.started_at
    return _order_from_votes(counts, last_seen)


def _global_recency_order(prior: list[SurfaceCluster]) -> list[str]:
    if not prior:
        return list(DEFAULT_CODEX_SURFACE_ORDER)
    surface = prior[-1].winner_surface
    if surface not in DEFAULT_CODEX_SURFACE_ORDER:
        return list(DEFAULT_CODEX_SURFACE_ORDER)
    return [surface, *[candidate for candidate in DEFAULT_CODEX_SURFACE_ORDER if candidate != surface]]


def _sequence_feature_order(prior: list[SurfaceCluster], target: SurfaceCluster) -> list[str]:
    target_features = set(_features_for_cluster(prior, target))
    if not target_features:
        return list(DEFAULT_CODEX_SURFACE_ORDER)
    votes: Counter[str] = Counter()
    last_seen: dict[str, float] = {}
    for index, cluster in enumerate(prior):
        if index == 0:
            continue
        surface = cluster.winner_surface
        if surface is None:
            continue
        features = set(_features_for_cluster(prior[:index], cluster))
        if not features:
            continue
        matches = len(target_features & features)
        if matches <= 0:
            continue
        votes[surface] += matches
        last_seen[surface] = cluster.started_at
    return _order_from_votes(votes, last_seen)


def _features_for_cluster(prior: list[SurfaceCluster], target: SurfaceCluster) -> list[tuple[Any, ...]]:
    features: list[tuple[Any, ...]] = []
    previous_global = prior[-1] if prior else None
    previous_same = next(
        (cluster for cluster in reversed(prior) if cluster.label == target.label),
        None,
    )
    if previous_same and previous_same.winner_surface:
        features.append(("previous_same_account_surface", target.label, previous_same.winner_surface))
    if previous_global and previous_global.winner_surface:
        features.append(("previous_global_surface", previous_global.winner_surface))
        features.append(("previous_global_account", previous_global.label))
        features.append(("account_transition", previous_global.label, target.label))
        features.append(("surface_for_account", previous_global.winner_surface, target.label))
    if previous_same:
        since = [
            cluster.winner_surface
            for cluster in prior
            if cluster.started_at > previous_same.started_at and cluster.winner_surface
        ]
        if since:
            features.append(("surfaces_since_account", target.label, tuple(since[-4:])))
    return features


def _order_from_votes(votes: Counter[str], last_seen: dict[str, float]) -> list[str]:
    default_index = {surface: index for index, surface in enumerate(DEFAULT_CODEX_SURFACE_ORDER)}
    return sorted(
        DEFAULT_CODEX_SURFACE_ORDER,
        key=lambda surface: (
            -votes.get(surface, 0),
            -last_seen.get(surface, 0.0),
            default_index[surface],
        ),
    )


def _prediction_result(order: list[str], actual: str) -> dict[str, Any]:
    return {
        "actual": actual,
        "order": order,
        "top1_hit": bool(order and order[0] == actual),
        "top2_hit": actual in order[:2],
    }


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    samples = len(results)
    top1_hits = sum(1 for result in results if result["top1_hit"])
    top2_hits = sum(1 for result in results if result["top2_hit"])
    return {
        "samples": samples,
        "top1_hits": top1_hits,
        "top1_rate": _rate(top1_hits, samples),
        "top2_hits": top2_hits,
        "top2_rate": _rate(top2_hits, samples),
    }


def _attach_lift(candidate: dict[str, Any], baseline: dict[str, Any]) -> None:
    candidate["top1_lift_hits"] = candidate["top1_hits"] - baseline["top1_hits"]
    candidate["top1_lift_rate"] = round(candidate["top1_rate"] - baseline["top1_rate"], 4)
    candidate["top2_lift_hits"] = candidate["top2_hits"] - baseline["top2_hits"]
    candidate["top2_lift_rate"] = round(candidate["top2_rate"] - baseline["top2_rate"], 4)


def _verdict(
    samples: int,
    baseline: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if samples < MIN_REPORT_SAMPLES:
        return {
            "status": "insufficient_data",
            "message": (
                f"insufficient data: {samples} evaluated clusters; need at least "
                f"{MIN_REPORT_SAMPLES}. No live ranking changes recommended."
            ),
            "winner": None,
        }
    if samples < MIN_LIFT_SAMPLES:
        return {
            "status": "no_significant_lift",
            "message": (
                f"no significant lift verdict: {samples} evaluated clusters; lift "
                f"verdicts require at least {MIN_LIFT_SAMPLES}. Keep collecting data."
            ),
            "winner": None,
        }
    passing = [
        name
        for name, candidate in candidates.items()
        if _candidate_passes_threshold(name, candidate, baseline)
    ]
    if not passing:
        return {
            "status": "no_significant_lift",
            "message": (
                "no significant lift: no candidate beat the baseline by the required "
                "margin. No live ranking changes recommended."
            ),
            "winner": None,
        }
    winner = max(
        passing,
        key=lambda name: (
            candidates[name]["top1_lift_hits"],
            candidates[name]["top1_lift_rate"],
        ),
    )
    return {
        "status": "candidate_lift_observed",
        "message": (
            f"preliminary lift observed for {winner}; keep collecting data, not a "
            "signal to change live ranking."
        ),
        "winner": winner,
    }


def _candidate_passes_threshold(
    name: str,
    candidate: dict[str, Any],
    baseline: dict[str, Any],
) -> bool:
    if candidate["top2_rate"] < baseline["top2_rate"]:
        return False
    if name == "sequence_features":
        return candidate["top1_lift_hits"] >= 8 and candidate["top1_lift_rate"] >= 0.15
    return candidate["top1_lift_hits"] >= 5 and candidate["top1_lift_rate"] >= 0.10


def _sequence_hints(
    eligible: list[SurfaceCluster],
    verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    if verdict.get("winner") != "sequence_features":
        return []
    counts: Counter[tuple[tuple[Any, ...], str]] = Counter()
    for index, cluster in enumerate(eligible):
        if index == 0 or not cluster.winner_surface:
            continue
        for feature in _features_for_cluster(eligible[:index], cluster):
            counts[(feature, cluster.winner_surface)] += 1
    hints = []
    for (feature, surface), count in counts.most_common(5):
        if count < 8:
            continue
        hints.append({"feature": list(feature), "surface": surface, "support": count})
    return hints


def _has_generation_evidence(event: KickEvent) -> bool:
    if event.response_text:
        return True
    return any(
        isinstance(value, int) and value > 0
        for value in (event.input_tokens, event.output_tokens, event.total_tokens)
    )


def _event_time(event: KickEvent) -> float:
    return event.codex_attempt_finished_at or event.timestamp or 0.0


def _rate(hits: int, samples: int) -> float:
    if samples <= 0:
        return 0.0
    return round(hits / samples, 4)


def _clamp(value: float) -> float:
    return max(-10.0, min(10.0, value))
