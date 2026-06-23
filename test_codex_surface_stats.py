import json

from tokenkick.codex_surface_stats import (
    CODEX_ATTRIBUTION_TIMING_MATCH,
    CODEX_ATTRIBUTION_STRONG,
    codex_surface_order_for_account,
    codex_surface_stats_for_account,
    reintroduce_codex_surfaces_after_miss,
    reset_codex_surface_learning_stats,
    update_codex_surface_stats,
)
from tokenkick.kicker import (
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_NO_GENERATION_EVIDENCE_ERROR,
)
from tokenkick.models import AccountConfig, KickEvent, account_key_string


def _strong_event(account: AccountConfig, surface: str, index: int) -> KickEvent:
    return KickEvent(
        label=account.label,
        timestamp=1000.0 + index,
        success=True,
        confirmed=True,
        codex_surface=surface,
        codex_cluster_id=f"cluster-{index}",
        codex_attempt=1,
        codex_max_attempts=4,
        codex_attempt_started_at=1000.0 + index,
        codex_attempt_finished_at=1001.0 + index,
        codex_attribution=CODEX_ATTRIBUTION_STRONG,
        response_text="ok",
    )


def test_new_account_uses_repo_skip_default_order(tmp_path):
    account = AccountConfig(label="codex", provider="codex")

    assert codex_surface_order_for_account(account, tmp_path / "stats.json") == (
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    )


def test_confirmed_surface_moves_ahead_for_only_that_account(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex", provider_home="/tmp/one")
    other = AccountConfig(label="codex other", provider="codex", provider_home="/tmp/two")

    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_REPO,
                response_text="ok",
            )
        ],
    )

    assert codex_surface_order_for_account(account, stats_file)[0] == CODEX_KICK_SURFACE_REPO
    assert codex_surface_order_for_account(other, stats_file) == (
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    )


def test_timing_match_confirmation_does_not_move_surface_score(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex")

    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_LEGACY,
                response_text="ok",
                codex_attribution=CODEX_ATTRIBUTION_TIMING_MATCH,
            )
        ],
    )

    assert codex_surface_order_for_account(account, stats_file) == (
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    )
    legacy = next(
        surface
        for surface in codex_surface_stats_for_account(account, stats_file)["surfaces"]
        if surface["surface"] == CODEX_KICK_SURFACE_LEGACY
    )
    assert legacy["score"] == 0.0
    assert legacy["confirmed"] == 0
    assert legacy["timing_matches"] == 1


def test_reset_surface_learning_stats_preserves_demotion_evidence(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex", codex_surface_auto_demote=True)

    update_codex_surface_stats(
        stats_file,
        account,
        [_strong_event(account, CODEX_KICK_SURFACE_REPO, 1)],
    )

    reset_codex_surface_learning_stats(stats_file, account)

    report = codex_surface_stats_for_account(account, stats_file)
    repo = next(surface for surface in report["surfaces"] if surface["surface"] == CODEX_KICK_SURFACE_REPO)
    assert repo["score"] == 0.0
    assert repo["attempts"] == 0
    assert repo["confirmed"] == 0
    assert report["demotion"]["strong_cluster_count"] == 1


def test_superseded_attempt_does_not_move_surface_score(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex")

    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=False,
                codex_surface=CODEX_KICK_SURFACE_REPO,
                response_text="ok",
                post_kick_status="superseded",
            )
        ],
    )

    repo = next(
        surface
        for surface in codex_surface_stats_for_account(account, stats_file)["surfaces"]
        if surface["surface"] == CODEX_KICK_SURFACE_REPO
    )
    assert repo["score"] == 0.0


def test_no_generation_penalty_never_removes_fallback_surface(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex")

    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=False,
                error=CODEX_NO_GENERATION_EVIDENCE_ERROR,
                codex_surface=CODEX_KICK_SURFACE_REPO_SKIP,
            )
        ],
    )

    order = codex_surface_order_for_account(account, stats_file)
    assert set(order) == {
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    }
    assert order[-1] == CODEX_KICK_SURFACE_REPO_SKIP


def test_confirmed_interactive_like_surface_can_move_first(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex")

    update_codex_surface_stats(
        stats_file,
        account,
        [
            KickEvent(
                label=account.label,
                success=True,
                confirmed=True,
                codex_surface=CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
                response_text="ok",
            )
        ],
    )

    assert (
        codex_surface_order_for_account(account, stats_file)[0]
        == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    )


def test_auto_demotes_redundant_tail_surface_after_strong_clusters(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex", codex_surface_auto_demote=True)

    demotions = []
    for index in range(5):
        demotions.extend(
            update_codex_surface_stats(
                stats_file,
                account,
                [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
            )
        )

    assert len(demotions) == 1
    assert demotions[0]["surface"] == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE not in codex_surface_order_for_account(account, stats_file)
    report = codex_surface_stats_for_account(account, stats_file)
    interactive = next(
        surface for surface in report["surfaces"]
        if surface["surface"] == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    )
    assert interactive["state"] == "demoted"


def test_auto_demotion_report_includes_cluster_evidence(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex", codex_surface_auto_demote=True)

    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            account,
            [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )

    report = codex_surface_stats_for_account(account, stats_file)
    evidence = report["demotion_evidence"]

    assert len(evidence) == 1
    item = evidence[0]
    assert item["surface"] == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    assert item["surface_label"] == "Codex home, skip check"
    assert item["decision"] == "skip_for_now"
    assert item["eligible_clusters"] == 5
    assert item["surface_strong_wins"] == 0
    assert item["kept_ahead_wins"] == 5
    assert item["kept_ahead_rate"] == 1.0
    assert item["policy"]["after_strong_clusters"] == 5
    assert len(item["recent_clusters"]) == 5
    assert item["recent_clusters"][0]["winner"] == CODEX_KICK_SURFACE_REPO_SKIP
    assert item["recent_clusters"][0]["surface_active"] is True
    assert item["recent_clusters"][0]["winner_kept_ahead"] is True


def test_auto_demotion_ignores_non_strong_clusters(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(label="codex", provider="codex", codex_surface_auto_demote=True)

    for index in range(10):
        event = _strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)
        event.codex_attribution = CODEX_ATTRIBUTION_TIMING_MATCH
        update_codex_surface_stats(stats_file, account, [event])

    assert codex_surface_order_for_account(account, stats_file) == (
        CODEX_KICK_SURFACE_REPO_SKIP,
        CODEX_KICK_SURFACE_LEGACY,
        CODEX_KICK_SURFACE_REPO,
        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
    )
    report = codex_surface_stats_for_account(account, stats_file)
    assert report["demotion"]["strong_cluster_count"] == 0


def test_auto_demotion_respects_active_surface_floor(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_auto_demote=True,
        codex_surface_demote_min_active_surfaces=4,
    )

    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            account,
            [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )

    assert len(codex_surface_order_for_account(account, stats_file)) == 4


def test_auto_demotion_measurement_window_lets_old_candidate_anchor_age_out(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_auto_demote=True,
        codex_surface_demote_after_strong_clusters=5,
        codex_surface_demote_measurement_clusters=5,
    )

    stats_file.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    account_key_string(account): {
                        "demotion": {
                            "demoted": {},
                            "rescues": {},
                            "strong_cluster_count": 1,
                            "strong_clusters": [
                                {
                                    "winner": CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
                                    "timestamp": 1000.0,
                                    "cluster_id": "old",
                                    "attempted_surfaces": [
                                        CODEX_KICK_SURFACE_REPO_SKIP,
                                        CODEX_KICK_SURFACE_LEGACY,
                                        CODEX_KICK_SURFACE_REPO,
                                        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
                                    ],
                                    "active_surfaces": [
                                        CODEX_KICK_SURFACE_REPO_SKIP,
                                        CODEX_KICK_SURFACE_LEGACY,
                                        CODEX_KICK_SURFACE_REPO,
                                        CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
                                    ],
                                    "strong_cluster_count": 1,
                                }
                            ],
                        }
                    }
                },
            }
        )
        + "\n"
    )
    for index in range(1, 6):
        update_codex_surface_stats(
            stats_file,
            account,
            [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )

    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE not in codex_surface_order_for_account(account, stats_file)


def test_rescue_cooldown_prevents_immediate_redemotion(tmp_path):
    stats_file = tmp_path / "stats.json"
    account = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_auto_demote=True,
        codex_surface_rescue_cooldown_strong_clusters=20,
    )
    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            account,
            [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )
    reintroduce_codex_surfaces_after_miss(stats_file, account, reason="miss", now=2000.0)

    for index in range(5, 10):
        update_codex_surface_stats(
            stats_file,
            account,
            [_strong_event(account, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )

    report = codex_surface_stats_for_account(account, stats_file)
    interactive = next(
        surface for surface in report["surfaces"]
        if surface["surface"] == CODEX_KICK_SURFACE_INTERACTIVE_LIKE
    )
    assert interactive["state"] == "active_rescue_cooldown"
    assert interactive["rescue_cooldown_remaining_strong_clusters"] == 15
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE in codex_surface_order_for_account(account, stats_file)


def test_force_keep_and_force_prune_override_auto_demotion(tmp_path):
    stats_file = tmp_path / "stats.json"
    force_kept = AccountConfig(
        label="codex",
        provider="codex",
        codex_surface_auto_demote=True,
        codex_surface_force_keep=[CODEX_KICK_SURFACE_INTERACTIVE_LIKE],
    )
    for index in range(5):
        update_codex_surface_stats(
            stats_file,
            force_kept,
            [_strong_event(force_kept, CODEX_KICK_SURFACE_REPO_SKIP, index)],
        )
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE in codex_surface_order_for_account(force_kept, stats_file)

    force_pruned = AccountConfig(
        label="codex prune",
        provider="codex",
        codex_surface_force_prune=[CODEX_KICK_SURFACE_INTERACTIVE_LIKE],
    )
    assert CODEX_KICK_SURFACE_INTERACTIVE_LIKE not in codex_surface_order_for_account(force_pruned, stats_file)
