"""Tests for multi-account orchestration planning."""

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tokenkick.models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    ScheduleConfig,
    account_key_string,
)
from tokenkick.orchestration import (
    AccountPlanInput,
    apply_orchestration_plan,
    build_orchestration_plan,
    usable_session_minutes_for_account,
)
from tokenkick.scheduling import (
    PendingKick,
    PendingKickStateError,
    ScheduleReason,
    SchedulingWindowBasis,
    load_pending_kicks,
    save_pending_kicks,
    to_utc_iso,
)


UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")


def _account(
    label: str,
    *,
    usable: int | None = None,
    tier: str | None = None,
    auto: bool = True,
) -> AccountConfig:
    return AccountConfig(
        label=label,
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=auto,
        session_auto_kick=auto,
        provider_home=f"/tmp/{label}",
        usable_session_minutes=usable,
        plan_tier=tier,
    )


def _status(
    label: str,
    state: AccountState = AccountState.FRESH,
    *,
    session_used: float | None = 0.0,
    session_reset: datetime | None = None,
    weekly_used: float = 1.0,
) -> AccountStatus:
    return AccountStatus(
        label=label,
        state=state,
        used_percent=weekly_used,
        resets_in_seconds=6 * 24 * 60 * 60,
        window_minutes=10080,
        session_used_percent=session_used,
        session_resets_at=session_reset.timestamp() if session_reset is not None else None,
        session_window_minutes=300,
    )


def _duration_minutes(segment) -> int:
    return int((segment.end - segment.start).total_seconds() // 60)


def _plan_signature(plan) -> list[tuple[str | None, str, datetime, datetime]]:
    return [
        (segment.account_label, segment.source, segment.start, segment.end)
        for segment in plan.segments
    ]


def test_usable_session_resolution_order():
    config = Config(
        schedule=ScheduleConfig(
            usable_session_tier_defaults={
                "plus": 80,
                "custom": 210,
            }
        )
    )

    assert usable_session_minutes_for_account(_account("override", usable=42), config) == 42
    assert usable_session_minutes_for_account(_account("tier", tier="custom"), config) == 210
    assert usable_session_minutes_for_account(_account("builtin", tier="pro_5x"), config) == 240
    assert usable_session_minutes_for_account(_account("spark", tier="spark"), config) == 120
    assert usable_session_minutes_for_account(_account("unknown", tier="unknown"), config) == 120


def test_orchestration_skips_spark_without_measured_usable_minutes():
    now = datetime(2026, 6, 6, 8, 0, tzinfo=UTC)
    spark = _account("codex-spark", tier="spark")
    spark = replace(
        spark,
        codex_rate_limit_id="codex_bengalfox",
        kick_model="gpt-5.3-codex-spark",
    )

    plan = build_orchestration_plan(
        config=Config(accounts=[spark]),
        inputs=[AccountPlanInput(account=spark, status=_status(spark.label), cache_stale=False)],
        work_start=now + timedelta(hours=1),
        work_end=now + timedelta(hours=2),
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert not plan.planned_kicks
    assert plan.skipped_accounts[0].reason == "spark_usable_session_unmeasured"


def test_orchestration_can_choose_spark_account_with_explicit_usable_minutes():
    now = datetime(2026, 6, 6, 8, 0, tzinfo=UTC)
    spark = _account("codex-spark", usable=45, tier="spark")
    spark = replace(
        spark,
        codex_rate_limit_id="codex_bengalfox",
        kick_model="gpt-5.3-codex-spark",
    )

    plan = build_orchestration_plan(
        config=Config(accounts=[spark]),
        inputs=[AccountPlanInput(account=spark, status=_status(spark.label), cache_stale=False)],
        work_start=now + timedelta(hours=1),
        work_end=now + timedelta(hours=2),
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert plan.planned_kicks
    assert plan.planned_kicks[0].account_label == "codex-spark"
    assert plan.accounts_considered[0]["usable_session_minutes"] == 45


def test_active_session_coverage_uses_remaining_percent_and_caps_reset():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    active = _account("active", usable=150)
    fresh = _account("fresh", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[active, fresh]),
        inputs=[
            AccountPlanInput(
                active,
                _status(
                    "active",
                    AccountState.ACTIVE,
                    session_used=80.0,
                    session_reset=work_start + timedelta(hours=2),
                ),
            ),
            AccountPlanInput(fresh, _status("fresh")),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.account_label for segment in plan.segments] == ["fresh", "active"]
    assert [segment.source for segment in plan.segments] == [
        "planned_fresh_session",
        "natural_reset_reuse",
    ]
    assert plan.segments[1].start == work_start + timedelta(minutes=150)
    assert plan.planned_kicks[1].account_label == "active"
    assert plan.planned_kicks[1].kick_at == plan.segments[1].start


def test_active_session_coverage_is_capped_by_provider_reset():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    account = _account("active", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[
            AccountPlanInput(
                account,
                _status(
                    "active",
                    AccountState.ACTIVE,
                    session_used=0.0,
                    session_reset=work_start + timedelta(minutes=20),
                ),
            )
        ],
        work_start=work_start,
        work_end=work_start + timedelta(hours=2),
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.source for segment in plan.segments] == [
        "active_session",
        "natural_reset_reuse",
    ]
    assert plan.segments[0].end == work_start + timedelta(minutes=20)
    assert plan.segments[1].start == work_start + timedelta(minutes=20)
    assert not plan.coverage_gaps
    assert plan.segments[0].note == "active now; session used 0%; estimated 150m remaining"


def test_active_session_note_handles_missing_used_percent():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    account = _account("active", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[
            AccountPlanInput(
                account,
                _status(
                    "active",
                    AccountState.ACTIVE,
                    session_used=None,
                    session_reset=work_start + timedelta(hours=3),
                ),
            )
        ],
        work_start=work_start,
        work_end=work_start + timedelta(hours=1),
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert plan.segments[0].note == "active now; estimated 150m remaining from planning default"


def test_active_session_that_resets_before_future_work_window_can_be_planned_fresh():
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 19, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 9, 21, 30, tzinfo=UTC)
    account = _account("codex", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[
            AccountPlanInput(
                account,
                _status(
                    "codex",
                    AccountState.ACTIVE,
                    session_used=10.0,
                    session_reset=now + timedelta(hours=1),
                ),
            )
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert len(plan.segments) == 1
    assert plan.segments[0].source == "planned_fresh_session"
    assert plan.segments[0].start == work_start
    assert plan.segments[0].end == work_end
    assert plan.planned_kicks[0].kick_at == work_start
    assert plan.diff.adds[0]["account_label"] == "codex"
    assert not plan.coverage_gaps


def test_scenario_planner_prefers_sky_first_and_nori_after_natural_reset():
    now = datetime(2026, 6, 9, 20, 50, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 2, 30, tzinfo=UTC)
    personal = replace(
        _account("codex (personal)", usable=150),
        orchestration_role="use_first",
    )
    work = _account("codex (work)", usable=150)
    reserve = _account("codex (reserve)", usable=60)
    plan = build_orchestration_plan(
        config=Config(accounts=[personal, work, reserve]),
        inputs=[
            AccountPlanInput(
                personal,
                _status(
                    personal.label,
                    AccountState.ACTIVE,
                    session_used=70.0,
                    session_reset=work_start + timedelta(minutes=9),
                    weekly_used=55.0,
                ),
            ),
            AccountPlanInput(work, _status(work.label, weekly_used=25.0)),
            AccountPlanInput(reserve, _status(reserve.label, weekly_used=1.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.account_label for segment in plan.segments] == [
        "codex (work)",
        "codex (personal)",
        "codex (reserve)",
    ]
    assert [segment.source for segment in plan.segments] == [
        "planned_fresh_session",
        "natural_reset_reuse",
        "planned_fresh_session",
    ]
    assert plan.segments[0].start == work_start
    assert all(_duration_minutes(segment) >= 15 for segment in plan.segments)
    nori_segment = next(segment for segment in plan.segments if segment.account_label == personal.label)
    nori_kick = next(kick for kick in plan.planned_kicks if kick.account_label == personal.label)
    assert nori_kick.kick_at == nori_segment.start
    assert nori_kick.kick_at > work_start + timedelta(minutes=9)
    assert not plan.coverage_gaps


def test_rolling_reset_boundary_preanchors_later_account_and_closes_gap():
    now = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 10, 8, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 17, 0, tzinfo=UTC)
    personal = replace(
        _account("codex (personal)", usable=150),
        orchestration_role="use_first",
    )
    work = _account("codex (work)", usable=150)
    reserve = _account("codex (reserve)", usable=60)
    plan = build_orchestration_plan(
        config=Config(accounts=[personal, work, reserve]),
        inputs=[
            AccountPlanInput(personal, _status(personal.label, weekly_used=55.0)),
            AccountPlanInput(work, _status(work.label, weekly_used=25.0)),
            AccountPlanInput(reserve, _status(reserve.label, weekly_used=1.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.account_label for segment in plan.segments] == [
        "codex (personal)",
        "codex (personal)",
        "codex (work)",
        "codex (work)",
    ]
    assert [segment.source for segment in plan.segments] == [
        "planned_early_anchor",
        "expected_reset_reuse",
        "planned_early_anchor",
        "expected_reset_reuse",
    ]
    assert [kick.account_label for kick in plan.planned_kicks] == [
        "codex (personal)",
        "codex (work)",
    ]
    assert [kick.kick_at for kick in plan.planned_kicks] == [
        datetime(2026, 6, 10, 5, 30, tzinfo=UTC),
        datetime(2026, 6, 10, 10, 30, tzinfo=UTC),
    ]
    assert "codex (reserve)" not in [segment.account_label for segment in plan.segments]
    assert not plan.coverage_gaps


def test_large_equivalent_account_plan_is_deterministic_and_bounded(monkeypatch):
    import tokenkick.orchestration as orchestration_mod

    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = now + timedelta(hours=2)
    work_end = work_start + timedelta(hours=10)
    accounts = [_account(f"a{i}", usable=150) for i in range(10)]
    calls = 0
    original_choices = orchestration_mod._scenario_choices

    def counted_choices(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_choices(*args, **kwargs)

    monkeypatch.setattr(orchestration_mod, "_scenario_choices", counted_choices)
    started = time.perf_counter()
    plan = build_orchestration_plan(
        config=Config(accounts=accounts),
        inputs=[
            AccountPlanInput(account, _status(account.label, weekly_used=index))
            for index, account in enumerate(accounts)
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )
    elapsed = time.perf_counter() - started

    assert _plan_signature(plan) == [
        ("a9", "planned_fresh_session", work_start, work_start + timedelta(minutes=150)),
        (
            "a8",
            "planned_fresh_session",
            work_start + timedelta(minutes=150),
            work_start + timedelta(minutes=300),
        ),
        (
            "a7",
            "planned_early_anchor",
            work_start + timedelta(minutes=300),
            work_start + timedelta(minutes=450),
        ),
        (
            "a7",
            "expected_reset_reuse",
            work_start + timedelta(minutes=450),
            work_end,
        ),
    ]
    assert [kick.account_label for kick in plan.planned_kicks] == ["a9", "a7", "a8"]
    assert not plan.coverage_gaps
    assert calls <= 400
    assert elapsed < 3.0


def test_natural_reset_reuse_does_not_overlap_or_create_duplicate_kicks():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 7, 0, tzinfo=UTC)
    account = _account("active", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[
            AccountPlanInput(
                account,
                _status(
                    "active",
                    AccountState.ACTIVE,
                    session_used=0.0,
                    session_reset=work_start + timedelta(minutes=20),
                ),
            )
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    for previous, current in zip(plan.segments, plan.segments[1:], strict=False):
        assert previous.end <= current.start
        if previous.account_key == current.account_key:
            assert previous.end == current.start
    assert [kick.account_label for kick in plan.planned_kicks] == ["active"]
    assert plan.planned_kicks[0].kick_at == plan.segments[1].start


def test_equal_short_plan_preserves_use_first_for_continuation():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 6, 0, tzinfo=UTC)
    normal = _account("normal", usable=90)
    first = replace(_account("first", usable=90), orchestration_role="use_first")
    plan = build_orchestration_plan(
        config=Config(accounts=[normal, first]),
        inputs=[
            AccountPlanInput(normal, _status("normal", weekly_used=50.0)),
            AccountPlanInput(first, _status("first", weekly_used=10.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.account_label for segment in plan.segments] == ["normal"]
    assert not plan.coverage_gaps


def test_reset_boundary_reuse_preserves_nori_for_continuation():
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 21, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 3, 0, tzinfo=UTC)
    personal = replace(
        _account("codex (personal)", usable=150),
        orchestration_role="use_first",
    )
    work = _account("codex (work)", usable=150)
    reserve = _account("codex (reserve)", usable=60)
    plan = build_orchestration_plan(
        config=Config(accounts=[personal, work, reserve]),
        inputs=[
            AccountPlanInput(personal, _status(personal.label, weekly_used=55.0)),
            AccountPlanInput(work, _status(work.label, weekly_used=25.0)),
            AccountPlanInput(reserve, _status(reserve.label, weekly_used=1.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [segment.account_label for segment in plan.segments] == [
        "codex (work)",
        "codex (work)",
        "codex (reserve)",
    ]
    assert [segment.source for segment in plan.segments] == [
        "planned_early_anchor",
        "expected_reset_reuse",
        "planned_fresh_session",
    ]
    assert plan.segments[0].end == datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
    assert plan.segments[1].start == datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
    assert [kick.account_label for kick in plan.planned_kicks] == [
        "codex (work)",
        "codex (reserve)",
    ]
    assert plan.planned_kicks[0].kick_at == datetime(2026, 6, 9, 19, 0, tzinfo=UTC)
    assert all(kick.account_label != personal.label for kick in plan.planned_kicks)
    assert not plan.coverage_gaps


def test_late_rolling_reset_boundary_is_skipped_after_required_kick_time():
    now = datetime(2026, 6, 10, 11, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 10, 8, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 17, 0, tzinfo=UTC)
    personal = replace(
        _account("codex (personal)", usable=150),
        orchestration_role="use_first",
    )
    work = _account("codex (work)", usable=150)
    reserve = _account("codex (reserve)", usable=60)
    plan = build_orchestration_plan(
        config=Config(accounts=[personal, work, reserve]),
        inputs=[
            AccountPlanInput(personal, _status(personal.label, weekly_used=55.0)),
            AccountPlanInput(work, _status(work.label, weekly_used=25.0)),
            AccountPlanInput(reserve, _status(reserve.label, weekly_used=1.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert all(kick.kick_at >= now for kick in plan.planned_kicks)
    assert [segment.account_label for segment in plan.segments].count("codex (work)") <= 1


def test_active_account_not_rolling_preanchored_before_observed_reset():
    now = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 10, 8, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    active = _account("active", usable=150)
    other = _account("other", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[active, other]),
        inputs=[
            AccountPlanInput(
                active,
                _status(
                    active.label,
                    AccountState.ACTIVE,
                    session_used=10.0,
                    session_reset=work_start,
                    weekly_used=10.0,
                ),
            ),
            AccountPlanInput(other, _status(other.label, weekly_used=20.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert all(
        not (
            segment.account_label == "active"
            and segment.source == "planned_early_anchor"
        )
        for segment in plan.segments
    )


def test_normal_bridge_beats_backup_bridge_when_preserving_nori():
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 21, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 3, 0, tzinfo=UTC)
    personal = replace(
        _account("codex (personal)", usable=150),
        orchestration_role="use_first",
    )
    work = _account("codex (work)", usable=150)
    normal_bridge = _account("normal bridge", usable=60)
    backup_bridge = replace(_account("backup bridge", usable=60), orchestration_role="backup")
    plan = build_orchestration_plan(
        config=Config(accounts=[personal, work, backup_bridge, normal_bridge]),
        inputs=[
            AccountPlanInput(personal, _status(personal.label, weekly_used=55.0)),
            AccountPlanInput(work, _status(work.label, weekly_used=25.0)),
            AccountPlanInput(backup_bridge, _status(backup_bridge.label, weekly_used=1.0)),
            AccountPlanInput(normal_bridge, _status(normal_bridge.label, weekly_used=1.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert "normal bridge" in [segment.account_label for segment in plan.segments]
    assert "backup bridge" not in [segment.account_label for segment in plan.segments]


def test_backup_used_when_it_improves_coverage_quality():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    normal = _account("normal", usable=90)
    backup = replace(_account("backup", usable=240), orchestration_role="backup")
    plan = build_orchestration_plan(
        config=Config(accounts=[normal, backup]),
        inputs=[
            AccountPlanInput(normal, _status("normal", weekly_used=10.0)),
            AccountPlanInput(backup, _status("backup", weekly_used=90.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert plan.segments[0].account_label == "backup"
    assert not plan.coverage_gaps


def test_fresh_accounts_can_use_early_anchor_when_it_reduces_account_spread():
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 10, 0, tzinfo=UTC)
    accounts = [_account("a", usable=150), _account("b", usable=150)]
    plan = build_orchestration_plan(
        config=Config(accounts=accounts),
        inputs=[
            AccountPlanInput(accounts[0], _status("a")),
            AccountPlanInput(accounts[1], _status("b", weekly_used=20.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [kick.kick_at for kick in plan.planned_kicks] == [
        work_start,
        work_start,
    ]
    assert [segment.source for segment in plan.segments] == [
        "planned_fresh_session",
        "planned_early_anchor",
        "expected_reset_reuse",
    ]
    assert not plan.coverage_gaps


def test_prefers_single_account_early_anchor_when_reset_reuse_covers_window():
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 2, 0, tzinfo=UTC)
    pro = _account("pro", usable=150, tier="pro")
    plus = _account("plus", usable=120, tier="plus")
    plan = build_orchestration_plan(
        config=Config(accounts=[plus, pro]),
        inputs=[
            AccountPlanInput(plus, _status("plus", weekly_used=1.0)),
            AccountPlanInput(pro, _status("pro", weekly_used=50.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert len(plan.planned_kicks) == 1
    assert plan.planned_kicks[0].account_label == "pro"
    assert plan.planned_kicks[0].kick_at == datetime(2026, 6, 9, 18, 30, tzinfo=UTC)
    assert [segment.source for segment in plan.segments] == [
        "planned_early_anchor",
        "expected_reset_reuse",
    ]
    assert [segment.account_label for segment in plan.segments] == ["pro", "pro"]
    assert plan.segments[0].start == work_start
    assert plan.segments[0].end == datetime(2026, 6, 9, 23, 30, tzinfo=UTC)
    assert plan.segments[1].start == datetime(2026, 6, 9, 23, 30, tzinfo=UTC)
    assert plan.segments[1].end == work_end
    assert not plan.coverage_gaps


def test_plan_usage_override_beats_account_setting_and_changes_early_anchor():
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 21, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 2, 0, tzinfo=UTC)
    account = _account("pro", usable=60)
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[AccountPlanInput(account, _status("pro", weekly_used=50.0))],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
        usage_overrides_by_key={account_key_string(account): 180},
    )

    assert plan.accounts_considered[0]["usable_session_minutes"] == 180
    assert plan.accounts_considered[0]["usage_source"] == "plan_override"
    assert len(plan.planned_kicks) == 1
    assert plan.planned_kicks[0].kick_at == datetime(2026, 6, 9, 19, 0, tzinfo=UTC)
    assert [segment.source for segment in plan.segments] == [
        "planned_early_anchor",
        "expected_reset_reuse",
    ]
    assert plan.segments[0].end == datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
    assert "usage=180m" in (plan.segments[0].note or "")


def test_multi_account_plan_uses_partial_early_anchor_before_adding_accounts():
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 9, 20, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 10, 2, 0, tzinfo=UTC)
    short = _account("short", usable=60)
    first = _account("first", usable=150)
    finisher = _account("finisher", usable=150)
    plan = build_orchestration_plan(
        config=Config(accounts=[short, first, finisher]),
        inputs=[
            AccountPlanInput(short, _status("short", weekly_used=1.0)),
            AccountPlanInput(first, _status("first", weekly_used=30.0)),
            AccountPlanInput(finisher, _status("finisher", weekly_used=40.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert [kick.account_label for kick in plan.planned_kicks] == ["finisher", "short"]
    assert [kick.kick_at for kick in plan.planned_kicks] == [
        datetime(2026, 6, 9, 18, 30, tzinfo=UTC),
        datetime(2026, 6, 9, 20, 0, tzinfo=UTC),
    ]
    assert [segment.account_label for segment in plan.segments] == [
        "short",
        "finisher",
        "finisher",
    ]
    assert plan.segments[1].start == datetime(2026, 6, 9, 21, 0, tzinfo=UTC)
    assert plan.segments[1].end == datetime(2026, 6, 9, 23, 30, tzinfo=UTC)
    assert plan.segments[2].start == datetime(2026, 6, 9, 23, 30, tzinfo=UTC)
    assert plan.segments[2].end == work_end
    assert not plan.coverage_gaps


def test_small_usable_account_creates_earlier_next_handoff():
    now = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 30, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)
    small = _account("small", usable=90)
    large = _account("large", usable=240)
    plan = build_orchestration_plan(
        config=Config(accounts=[small, large]),
        inputs=[
            AccountPlanInput(small, _status("small", weekly_used=1.0)),
            AccountPlanInput(large, _status("large", weekly_used=50.0)),
        ],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert plan.planned_kicks[0].account_label == "small"
    assert plan.planned_kicks[1].kick_at == work_start + timedelta(minutes=90)


def test_skips_stale_unknown_and_non_auto_accounts():
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    stale = _account("stale")
    unknown = _account("unknown")
    disabled = _account("disabled", auto=False)
    plan = build_orchestration_plan(
        config=Config(accounts=[stale, unknown, disabled]),
        inputs=[
            AccountPlanInput(stale, _status("stale"), cache_stale=True),
            AccountPlanInput(unknown, _status("unknown", AccountState.UNKNOWN)),
            AccountPlanInput(disabled, _status("disabled")),
        ],
        work_start=now,
        work_end=now + timedelta(hours=1),
        now=now,
        timezone_name="UTC",
        pending={},
    )

    assert {item.reason for item in plan.skipped_accounts} == {
        "stale_status",
        "unknown_status",
        "auto_kick_disabled",
    }


def test_apply_adds_replaces_unchanged_and_preserves_unmanaged_conflicts(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", pending_file)
    monkeypatch.setattr("tokenkick.orchestration.load_pending_kicks", load_pending_kicks)
    monkeypatch.setattr("tokenkick.orchestration.save_pending_kicks", save_pending_kicks)
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 7, 0, tzinfo=UTC)
    plan_work_end = work_end + timedelta(hours=8)
    add = _account("add")
    replace = _account("replace")
    unchanged = _account("unchanged")
    manual = _account("manual")
    accounts = [add, replace, unchanged, manual]
    old_replace = PendingKick(
        account_key="codex-home|codex|/tmp/replace",
        account_label="replace",
        provider="codex",
        kick_at=to_utc_iso(work_start + timedelta(minutes=10)),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(work_start),
        work_end=to_utc_iso(plan_work_end),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    old_unchanged = PendingKick(
        account_key="codex-home|codex|/tmp/unchanged",
        account_label="unchanged",
        provider="codex",
        kick_at=to_utc_iso(work_start + timedelta(minutes=210)),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.ORCHESTRATED.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(work_start),
        work_end=to_utc_iso(plan_work_end),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    old_manual = PendingKick(
        account_key="codex-home|codex|/tmp/manual",
        account_label="manual",
        provider="codex",
        kick_at=to_utc_iso(work_start),
        created_at=to_utc_iso(now),
        reason=ScheduleReason.OPTIMAL.value,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(work_start),
        work_end=to_utc_iso(plan_work_end),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )
    save_pending_kicks(
        {
            old_replace.account_key: old_replace,
            old_unchanged.account_key: old_unchanged,
            old_manual.account_key: old_manual,
        }
    )
    plan = build_orchestration_plan(
        config=Config(accounts=accounts),
        inputs=[
            AccountPlanInput(add, _status("add", weekly_used=1.0)),
            AccountPlanInput(replace, _status("replace", weekly_used=2.0)),
            AccountPlanInput(unchanged, _status("unchanged", weekly_used=3.0)),
            AccountPlanInput(manual, _status("manual", weekly_used=4.0)),
        ],
        work_start=work_start,
        work_end=plan_work_end,
        now=now,
        timezone_name="UTC",
        pending=load_pending_kicks(now),
    )

    assert len(plan.diff.adds) == 1
    assert len(plan.diff.replaces_orchestrated) >= 1
    assert len(plan.diff.conflicts_unmanaged) == 1

    applied = apply_orchestration_plan(plan, now=now)

    assert applied.applied is False
    assert load_pending_kicks(now)[old_manual.account_key].reason == ScheduleReason.OPTIMAL.value


def _orchestration_pending_kick(label, *, reason, kick_at, window_start, window_end, now):
    return PendingKick(
        account_key=f"codex-home|codex|/tmp/{label}",
        account_label=label,
        provider="codex",
        kick_at=to_utc_iso(kick_at),
        created_at=to_utc_iso(now),
        reason=reason,
        windows_needed=1,
        expected_waste_minutes=0,
        waste_location="none",
        work_start=to_utc_iso(window_start),
        work_end=to_utc_iso(window_end),
        window_basis=SchedulingWindowBasis.SESSION.value,
    )


def test_apply_removes_stale_orchestrated_kicks_absent_from_new_plan(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 7, 0, tzinfo=UTC)
    stale = _orchestration_pending_kick(
        "stale",
        reason=ScheduleReason.ORCHESTRATED.value,
        kick_at=work_start + timedelta(minutes=10),
        window_start=work_start,
        window_end=work_end + timedelta(hours=5),
        now=now,
    )
    # No plan id exists in the pending-kick schema, so window overlap is the
    # ownership signal: this evening plan's kick must survive a morning replan.
    other_window = _orchestration_pending_kick(
        "evening",
        reason=ScheduleReason.ORCHESTRATED.value,
        kick_at=datetime(2026, 6, 5, 18, 0, tzinfo=UTC),
        window_start=datetime(2026, 6, 5, 18, 0, tzinfo=UTC),
        window_end=datetime(2026, 6, 5, 22, 0, tzinfo=UTC),
        now=now,
    )
    smart = _orchestration_pending_kick(
        "smart",
        reason=ScheduleReason.OPTIMAL.value,
        kick_at=work_start,
        window_start=work_start,
        window_end=work_end,
        now=now,
    )
    save_pending_kicks(
        {item.account_key: item for item in (stale, other_window, smart)}
    )

    account = _account("solo")
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[AccountPlanInput(account, _status("solo"))],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending=load_pending_kicks(now),
    )

    removal_labels = [
        item["existing"]["account_label"] for item in plan.diff.removes_orchestrated
    ]
    assert removal_labels == ["stale"]
    assert {
        item["reason"] for item in plan.diff.removes_orchestrated
    } == {"stale_orchestrated_not_in_plan"}
    # Preview must not mutate pending state.
    assert sorted(load_pending_kicks(now)) == sorted(
        [stale.account_key, other_window.account_key, smart.account_key]
    )

    applied = apply_orchestration_plan(plan, now=now, current_time=now)

    assert applied.applied is True
    assert "removed 1 stale orchestrated pending kick" in applied.message
    remaining = load_pending_kicks(now)
    assert stale.account_key not in remaining
    assert other_window.account_key in remaining
    assert smart.account_key in remaining
    assert smart.account_key in remaining and remaining[smart.account_key].reason == (
        ScheduleReason.OPTIMAL.value
    )
    for kick in plan.planned_kicks:
        assert kick.account_key in remaining


def test_apply_with_unmanaged_conflict_does_not_remove_stale_orchestrated(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 7, 0, tzinfo=UTC)
    account = _account("solo")
    conflict = _orchestration_pending_kick(
        "solo",
        reason=ScheduleReason.OPTIMAL.value,
        kick_at=work_start,
        window_start=work_start,
        window_end=work_end,
        now=now,
    )
    conflict.account_key = account_key_string(account)
    stale = _orchestration_pending_kick(
        "stale",
        reason=ScheduleReason.ORCHESTRATED.value,
        kick_at=work_start + timedelta(minutes=10),
        window_start=work_start,
        window_end=work_end,
        now=now,
    )
    save_pending_kicks({conflict.account_key: conflict, stale.account_key: stale})

    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[AccountPlanInput(account, _status("solo"))],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending=load_pending_kicks(now),
    )
    assert plan.diff.conflicts_unmanaged

    applied = apply_orchestration_plan(plan, now=now)

    assert applied.applied is False
    assert "resolve unmanaged pending-kick conflicts" in applied.message
    remaining = load_pending_kicks(now)
    assert sorted(remaining) == sorted([conflict.account_key, stale.account_key])


def _solo_plan(monkeypatch, tmp_path, *, now, work_start, work_end):
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    account = _account("solo")
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[AccountPlanInput(account, _status("solo"))],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )
    assert plan.planned_kicks
    return plan


def test_apply_refuses_when_planned_kick_is_stale(monkeypatch, tmp_path):
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    plan = _solo_plan(
        monkeypatch,
        tmp_path,
        now=now,
        work_start=datetime(2026, 6, 5, 4, 0, tzinfo=UTC),
        work_end=datetime(2026, 6, 5, 7, 0, tzinfo=UTC),
    )
    first_kick_at = min(kick.kick_at for kick in plan.planned_kicks)
    # Keep the plan age inside the threshold so the kick-overdue check is
    # exercised on its own.
    plan.built_at = first_kick_at

    applied = apply_orchestration_plan(
        plan,
        now=now,
        current_time=first_kick_at + timedelta(minutes=10),
    )

    assert applied.applied is False
    assert applied.read_only is True
    assert applied.message.startswith("not applied; plan is stale, rebuild the plan")
    assert "has already passed" in applied.message
    assert load_pending_kicks(now) == {}


def test_apply_refuses_old_plan_even_with_future_kicks(monkeypatch, tmp_path):
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    plan = _solo_plan(
        monkeypatch,
        tmp_path,
        now=now,
        work_start=datetime(2026, 6, 5, 4, 0, tzinfo=UTC),
        work_end=datetime(2026, 6, 5, 7, 0, tzinfo=UTC),
    )
    assert all(kick.kick_at > now + timedelta(minutes=30) for kick in plan.planned_kicks)

    applied = apply_orchestration_plan(
        plan,
        now=now,
        current_time=now + timedelta(minutes=20),
    )

    assert applied.applied is False
    assert applied.message.startswith("not applied; plan is stale, rebuild the plan")
    assert "minutes ago" in applied.message
    assert load_pending_kicks(now) == {}


def test_apply_within_grace_and_age_still_applies(monkeypatch, tmp_path):
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    plan = _solo_plan(
        monkeypatch,
        tmp_path,
        now=now,
        work_start=datetime(2026, 6, 5, 4, 0, tzinfo=UTC),
        work_end=datetime(2026, 6, 5, 7, 0, tzinfo=UTC),
    )

    applied = apply_orchestration_plan(
        plan,
        now=now,
        current_time=now + timedelta(minutes=5),
    )

    assert applied.applied is True
    remaining = load_pending_kicks(now)
    for kick in plan.planned_kicks:
        assert kick.account_key in remaining


def test_apply_reports_not_applied_when_pending_save_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.scheduling.PENDING_KICKS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr("tokenkick.scheduling.CONFIG_DIR", tmp_path)
    now = datetime(2026, 6, 5, 2, 0, tzinfo=UTC)
    work_start = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    work_end = datetime(2026, 6, 5, 7, 0, tzinfo=UTC)
    account = _account("solo")
    plan = build_orchestration_plan(
        config=Config(accounts=[account]),
        inputs=[AccountPlanInput(account, _status("solo"))],
        work_start=work_start,
        work_end=work_end,
        now=now,
        timezone_name="UTC",
        pending={},
    )
    assert plan.planned_kicks

    def refuse_save(_data):
        raise PendingKickStateError("disk full")

    monkeypatch.setattr("tokenkick.orchestration.save_pending_kicks", refuse_save)

    applied = apply_orchestration_plan(plan, now=now, current_time=now)

    assert applied.applied is False
    assert applied.read_only is True
    assert applied.message.startswith("not applied;")
    assert "disk full" in applied.message
    assert load_pending_kicks(now) == {}
