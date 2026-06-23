from tokenkick.codex_surface_patterns import (
    _codex_surface_clusters,
    _verdict,
    build_codex_surface_patterns_report,
)
from tokenkick.kicker import (
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
)
from tokenkick.models import KickEvent


def _event(
    label: str,
    timestamp: float,
    surface: str,
    *,
    cluster_id: str | None = None,
    attempt: int | None = 1,
    strong: bool = True,
    confirmed: bool = True,
    success: bool = True,
    post: str | None = None,
    attribution: str | None = None,
    response: str | None = "ok",
) -> KickEvent:
    return KickEvent(
        label=label,
        timestamp=timestamp,
        success=success,
        confirmed=confirmed,
        kind="session",
        kick_type="session",
        response_text=response,
        codex_surface=surface,
        codex_attempt=attempt,
        codex_cluster_id=cluster_id,
        codex_attempt_started_at=timestamp,
        codex_attempt_finished_at=timestamp + 1,
        codex_attribution=attribution or ("strong" if strong else "timing_match"),
        post_kick_status=post,
    )


def _strong_cluster(index: int, label: str, surface: str) -> list[KickEvent]:
    timestamp = 10_000.0 + index * 100.0
    return [
        _event(
            label,
            timestamp,
            surface,
            cluster_id=f"cluster-{index}",
            attempt=1,
        )
    ]


def test_cluster_extraction_handles_cluster_id_and_reconstructs_old_rows():
    events = [
        _event("codex", 100.0, CODEX_KICK_SURFACE_REPO_SKIP, cluster_id="cluster-a"),
        _event("codex", 200.0, CODEX_KICK_SURFACE_LEGACY, cluster_id="cluster-a", attempt=2),
        _event("codex", 500.0, CODEX_KICK_SURFACE_REPO, cluster_id=None, attempt=1),
        _event("codex", 800.0, CODEX_KICK_SURFACE_LEGACY, cluster_id=None, attempt=2),
    ]

    clusters = _codex_surface_clusters(events)

    assert len(clusters) == 2
    assert [len(cluster.events) for cluster in clusters] == [2, 2]
    assert clusters[0].cluster_id == "cluster-a"
    assert clusters[1].cluster_id is None


def test_report_ignores_weak_and_noisy_rows():
    events = [
        *_strong_cluster(1, "codex", CODEX_KICK_SURFACE_REPO_SKIP),
        _event(
            "codex",
            20_000.0,
            CODEX_KICK_SURFACE_LEGACY,
            cluster_id="timing",
            strong=False,
        ),
        _event(
            "codex",
            21_000.0,
            CODEX_KICK_SURFACE_REPO,
            cluster_id="pending",
            confirmed=False,
            attribution=None,
        ),
        _event(
            "codex",
            22_000.0,
            CODEX_KICK_SURFACE_REPO,
            cluster_id="failed",
            success=False,
            confirmed=False,
            attribution=None,
            response=None,
        ),
    ]

    report = build_codex_surface_patterns_report(events)

    assert report["eligible_clusters"] == 1
    assert report["ignored"]["timing_match"] == 1
    assert report["ignored"]["generated_unconfirmed"] == 1
    assert report["ignored"]["failed_or_no_generation"] == 1


def test_walk_forward_uses_prior_events_only():
    events = [
        *_strong_cluster(1, "codex", CODEX_KICK_SURFACE_REPO_SKIP),
        *_strong_cluster(2, "codex", CODEX_KICK_SURFACE_LEGACY),
    ]

    report = build_codex_surface_patterns_report(events)

    assert report["evaluated_samples"] == 1
    assert report["candidates"]["global_recency"]["top1_hits"] == 0


def test_low_sample_report_is_insufficient_data():
    events = [
        event
        for index in range(10)
        for event in _strong_cluster(index, "codex", CODEX_KICK_SURFACE_REPO_SKIP)
    ]

    report = build_codex_surface_patterns_report(events)

    assert report["evaluated_samples"] == 9
    assert report["verdict"]["status"] == "insufficient_data"


def test_mid_sample_report_never_allows_lift_verdict():
    baseline = {"top1_hits": 10, "top1_rate": 0.25, "top2_rate": 0.6}
    candidates = {
        "per_account_majority": {
            "top1_hits": 30,
            "top1_rate": 0.75,
            "top2_rate": 0.8,
            "top1_lift_hits": 20,
            "top1_lift_rate": 0.5,
        }
    }

    verdict = _verdict(40, baseline, candidates)

    assert verdict["status"] == "no_significant_lift"
    assert "require at least 50" in verdict["message"]


def test_sequence_features_have_stricter_positive_threshold():
    baseline = {"top1_hits": 25, "top1_rate": 0.5, "top2_rate": 0.7}
    candidates = {
        "per_account_majority": {
            "top1_hits": 31,
            "top1_rate": 0.62,
            "top2_rate": 0.7,
            "top1_lift_hits": 6,
            "top1_lift_rate": 0.12,
        },
        "sequence_features": {
            "top1_hits": 32,
            "top1_rate": 0.64,
            "top2_rate": 0.7,
            "top1_lift_hits": 7,
            "top1_lift_rate": 0.14,
        },
    }

    verdict = _verdict(50, baseline, candidates)

    assert verdict["status"] == "candidate_lift_observed"
    assert verdict["winner"] == "per_account_majority"
    assert "preliminary lift observed" in verdict["message"]
    assert "not a signal to change live ranking" in verdict["message"]


def test_no_significant_lift_when_candidate_top2_is_worse():
    baseline = {"top1_hits": 20, "top1_rate": 0.4, "top2_rate": 0.9}
    candidates = {
        "per_account_majority": {
            "top1_hits": 30,
            "top1_rate": 0.6,
            "top2_rate": 0.8,
            "top1_lift_hits": 10,
            "top1_lift_rate": 0.2,
        }
    }

    verdict = _verdict(50, baseline, candidates)

    assert verdict["status"] == "no_significant_lift"

