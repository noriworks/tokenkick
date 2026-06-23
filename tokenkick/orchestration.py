"""Read-only multi-account session orchestration planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .kicker import KICKABLE_PROVIDERS
from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    CODEX_DEFAULT_RATE_LIMIT_ID,
    DEFAULT_ORCHESTRATION_ROLE,
    Config,
    account_key_string,
    codex_rate_limit_id_for_account,
    normalize_orchestration_role,
    weekly_quota_exhausted,
)
from .scheduling import (
    PENDING_KICK_PURPOSE_COVERAGE,
    PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
    PendingKick,
    PendingKickStateError,
    ScheduleReason,
    SchedulingWindowBasis,
    from_utc_iso,
    load_pending_kicks,
    save_pending_kicks,
    to_utc_iso,
)

ORCHESTRATION_SCHEMA_VERSION = 1
ORCHESTRATION_OVERLAP_MINUTES = 15
ORCHESTRATION_APPLY_KICK_GRACE_SECONDS = 120
ORCHESTRATION_APPLY_MAX_PLAN_AGE_SECONDS = 15 * 60
ORCHESTRATION_ROLE_RANK = {
    "backup": 0,
    "normal": 1,
    "use_first": 2,
}
ORCHESTRATION_TINY_ACTIVE_SEGMENT_MINUTES = 15
BUILTIN_USABLE_SESSION_TIER_DEFAULTS = {
    "plus": 90,
    "pro": 150,
    "pro_5x": 240,
    "max": 300,
    "spark": 120,
    "default": 120,
}


@dataclass(frozen=True)
class AccountPlanInput:
    account: AccountConfig
    status: AccountStatus | None
    cache_stale: bool = False


@dataclass(frozen=True)
class PlannedSegment:
    account_key: str | None
    account_label: str | None
    provider: str | None
    start: datetime
    end: datetime
    source: str
    usable_session_minutes: int | None = None
    kick_at: datetime | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        data = {
            "account_key": self.account_key,
            "account_label": self.account_label,
            "provider": self.provider,
            "start": to_utc_iso(self.start),
            "end": to_utc_iso(self.end),
            "source": self.source,
            "usable_session_minutes": self.usable_session_minutes,
            "kick_at": to_utc_iso(self.kick_at) if self.kick_at is not None else None,
            "note": self.note,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class PlannedKick:
    account_key: str
    account_label: str
    provider: str
    kick_at: datetime
    work_start: datetime
    work_end: datetime
    segment_start: datetime
    segment_end: datetime
    usable_session_minutes: int
    purpose: str = PENDING_KICK_PURPOSE_COVERAGE

    def to_pending_kick(self, *, now: datetime, current: PendingKick | None = None) -> PendingKick:
        same_kick = (
            current is not None
            and current.kick_at == to_utc_iso(self.kick_at)
            and current.purpose == self.purpose
        )
        return PendingKick(
            account_key=self.account_key,
            account_label=self.account_label,
            provider=self.provider,
            kick_at=to_utc_iso(self.kick_at),
            created_at=current.created_at if current is not None else to_utc_iso(now),
            reason=ScheduleReason.ORCHESTRATED.value,
            windows_needed=1,
            expected_waste_minutes=0,
            waste_location="none",
            work_start=to_utc_iso(self.work_start),
            work_end=to_utc_iso(self.work_end),
            window_basis=SchedulingWindowBasis.SESSION.value,
            purpose=self.purpose,
            notified=current.notified if same_kick else False,
            attempt_count=current.attempt_count if same_kick else 0,
            last_attempt_at=current.last_attempt_at if same_kick else None,
            last_error=current.last_error if same_kick else None,
            next_retry_at=current.next_retry_at if same_kick else None,
            gave_up_at=current.gave_up_at if same_kick else None,
        )

    def to_dict(self) -> dict:
        return {
            "account_key": self.account_key,
            "account_label": self.account_label,
            "provider": self.provider,
            "kick_at": to_utc_iso(self.kick_at),
            "work_start": to_utc_iso(self.work_start),
            "work_end": to_utc_iso(self.work_end),
            "segment_start": to_utc_iso(self.segment_start),
            "segment_end": to_utc_iso(self.segment_end),
            "usable_session_minutes": self.usable_session_minutes,
            "reason": ScheduleReason.ORCHESTRATED.value,
            "window_basis": SchedulingWindowBasis.SESSION.value,
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class SkippedAccount:
    account_key: str
    account_label: str
    provider: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "account_key": self.account_key,
            "account_label": self.account_label,
            "provider": self.provider,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PendingKickDiff:
    adds: list[dict] = field(default_factory=list)
    replaces_orchestrated: list[dict] = field(default_factory=list)
    unchanged_orchestrated: list[dict] = field(default_factory=list)
    conflicts_unmanaged: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    removes_orchestrated: list[dict] = field(default_factory=list)

    def has_conflicts(self) -> bool:
        return bool(self.conflicts_unmanaged)

    def to_dict(self) -> dict:
        return {
            "adds": self.adds,
            "replaces_orchestrated": self.replaces_orchestrated,
            "unchanged_orchestrated": self.unchanged_orchestrated,
            "conflicts_unmanaged": self.conflicts_unmanaged,
            "skipped": self.skipped,
            "removes_orchestrated": self.removes_orchestrated,
        }


@dataclass
class OrchestrationPlan:
    read_only: bool
    applied: bool
    work_start: datetime
    work_end: datetime
    timezone: str
    accounts_considered: list[dict]
    segments: list[PlannedSegment]
    planned_kicks: list[PlannedKick]
    skipped_accounts: list[SkippedAccount]
    coverage_gaps: list[dict]
    diff: PendingKickDiff
    limitations: list[str]
    cache_age_seconds: int | None = None
    message: str | None = None
    built_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "read_only": self.read_only,
            "applied": self.applied,
            "built_at": to_utc_iso(self.built_at) if self.built_at is not None else None,
            "work_window": {
                "start": to_utc_iso(self.work_start),
                "end": to_utc_iso(self.work_end),
                "timezone": self.timezone,
            },
            "cache_age_seconds": self.cache_age_seconds,
            "accounts_considered": self.accounts_considered,
            "segments": [segment.to_dict() for segment in self.segments],
            "planned_kicks": [kick.to_dict() for kick in self.planned_kicks],
            "coverage_gaps": self.coverage_gaps,
            "diff": self.diff.to_dict(),
            "skipped_accounts": [item.to_dict() for item in self.skipped_accounts],
            "limitations": self.limitations,
            "message": self.message,
        }


@dataclass
class _Candidate:
    account: AccountConfig
    status: AccountStatus
    usable_minutes: int
    usage_source: str
    available_at: datetime
    active: bool
    remaining_minutes: int
    weekly_headroom: float
    orchestration_role: str
    effective_orchestration_role: str

    @property
    def key(self) -> str:
        return account_key_string(self.account)


@dataclass(frozen=True)
class _ScenarioPlan:
    segments: tuple[PlannedSegment, ...] = ()
    planned_kicks: tuple[PlannedKick, ...] = ()
    gaps: tuple[dict, ...] = ()
    active_used: frozenset[str] = frozenset()
    pending_used: frozenset[str] = frozenset()
    early_anchor_used: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _ScenarioChoice:
    candidate: _Candidate
    start: datetime
    end: datetime
    segments: tuple[PlannedSegment, ...]
    planned_kick: PlannedKick | None
    uses_active: bool = False
    uses_pending: bool = False
    uses_early_anchor: bool = False


def usable_session_minutes_for_account(account: AccountConfig, config: Config) -> int:
    """Resolve the rough usable-session planning estimate for an account."""
    minutes, _source = _usable_session_minutes_and_source(account, config)
    return minutes


def effective_orchestration_role(
    account: AccountConfig,
    status: AccountStatus | None = None,
) -> str:
    """Resolve the account role after applying quota-preservation demotion."""
    role = normalize_orchestration_role(
        getattr(account, "orchestration_role", DEFAULT_ORCHESTRATION_ROLE)
    )
    if role in {"excluded", "specialist"}:
        return role
    threshold = account.weekly_reserve_threshold_percent
    if threshold is None or status is None or status.used_percent is None:
        return role
    if float(status.used_percent) >= float(threshold):
        return "backup"
    return role


def _usable_session_minutes_and_source(account: AccountConfig, config: Config) -> tuple[int, str]:
    if account.usable_session_minutes is not None and account.usable_session_minutes > 0:
        return int(account.usable_session_minutes), "account_setting"

    tier = (account.plan_tier or "default").strip().lower()
    configured = config.schedule.usable_session_tier_defaults
    if tier in configured and configured[tier] > 0:
        return int(configured[tier]), "tier_default"
    if tier in BUILTIN_USABLE_SESSION_TIER_DEFAULTS:
        return BUILTIN_USABLE_SESSION_TIER_DEFAULTS[tier], "tier_default"
    return BUILTIN_USABLE_SESSION_TIER_DEFAULTS["default"], "tier_default"


def build_orchestration_plan(
    *,
    config: Config,
    inputs: list[AccountPlanInput],
    work_start: datetime,
    work_end: datetime,
    now: datetime,
    timezone_name: str,
    pending: dict[str, PendingKick] | None = None,
    cache_age_seconds: int | None = None,
    usage_overrides_by_key: dict[str, int] | None = None,
) -> OrchestrationPlan:
    pending = pending if pending is not None else load_pending_kicks(now)
    candidates, specialist_candidates, skipped, considered = _build_candidates(
        config,
        inputs,
        now,
        usage_overrides_by_key=usage_overrides_by_key or {},
    )
    segments, planned_kicks, gaps = _plan_segments(
        candidates=candidates,
        work_start=work_start.astimezone(timezone.utc),
        work_end=work_end.astimezone(timezone.utc),
        now=now.astimezone(timezone.utc),
    )
    specialist_kicks, specialist_skipped = _plan_specialist_readiness(
        candidates=specialist_candidates,
        work_start=work_start.astimezone(timezone.utc),
        work_end=work_end.astimezone(timezone.utc),
        now=now.astimezone(timezone.utc),
    )
    if specialist_skipped:
        skipped.extend(specialist_skipped)
        _mark_considered_specialist_skips(considered, specialist_skipped)
    if specialist_kicks:
        planned_kicks.extend(specialist_kicks)
        _mark_considered_specialist_planned(considered, specialist_kicks)
    diff = build_pending_kick_diff(
        planned_kicks,
        pending,
        skipped,
        work_start=work_start.astimezone(timezone.utc),
        work_end=work_end.astimezone(timezone.utc),
    )
    limitations = _plan_limitations(gaps)
    return OrchestrationPlan(
        read_only=True,
        applied=False,
        work_start=work_start.astimezone(timezone.utc),
        work_end=work_end.astimezone(timezone.utc),
        timezone=timezone_name,
        accounts_considered=considered,
        segments=segments,
        planned_kicks=planned_kicks,
        skipped_accounts=skipped,
        coverage_gaps=gaps,
        diff=diff,
        limitations=limitations,
        cache_age_seconds=cache_age_seconds,
        message="read-only plan; no pending kicks were changed",
        built_at=now.astimezone(timezone.utc),
    )


def build_pending_kick_diff(
    planned_kicks: list[PlannedKick],
    pending: dict[str, PendingKick],
    skipped_accounts: list[SkippedAccount] | None = None,
    *,
    work_start: datetime | None = None,
    work_end: datetime | None = None,
) -> PendingKickDiff:
    adds: list[dict] = []
    replaces: list[dict] = []
    unchanged: list[dict] = []
    conflicts: list[dict] = []
    for kick in planned_kicks:
        current = pending.get(kick.account_key)
        planned_payload = kick.to_dict()
        if current is None:
            adds.append(planned_payload)
            continue
        current_payload = _pending_summary(current)
        if current.reason != ScheduleReason.ORCHESTRATED.value:
            conflicts.append(
                {
                    "reason": "conflict_unmanaged_pending",
                    "planned": planned_payload,
                    "existing": current_payload,
                }
            )
            continue
        if _pending_matches_planned(current, kick):
            unchanged.append(planned_payload)
        else:
            replaces.append(
                {
                    "planned": planned_payload,
                    "existing": current_payload,
                }
            )
    return PendingKickDiff(
        adds=adds,
        replaces_orchestrated=replaces,
        unchanged_orchestrated=unchanged,
        conflicts_unmanaged=conflicts,
        skipped=[item.to_dict() for item in skipped_accounts or []],
        removes_orchestrated=_stale_orchestrated_removals(
            planned_kicks,
            pending,
            work_start=work_start,
            work_end=work_end,
        ),
    )


def _plan_stale_reason(plan: OrchestrationPlan, current: datetime) -> str | None:
    """Return why a plan is too stale to apply, or None when fresh enough."""
    if plan.built_at is not None:
        age_seconds = (current - plan.built_at).total_seconds()
        if age_seconds > ORCHESTRATION_APPLY_MAX_PLAN_AGE_SECONDS:
            return f"plan was built {int(age_seconds // 60)} minutes ago"
    for kick in plan.planned_kicks:
        overdue_seconds = (current - kick.kick_at).total_seconds()
        if overdue_seconds > ORCHESTRATION_APPLY_KICK_GRACE_SECONDS:
            return (
                f'planned kick for "{kick.account_label}" at '
                f"{kick.kick_at.astimezone().strftime('%H:%M %Z')} has already passed"
            )
    return None


def _stale_orchestrated_removals(
    planned_kicks: list[PlannedKick],
    pending: dict[str, PendingKick],
    *,
    work_start: datetime | None,
    work_end: datetime | None,
) -> list[dict]:
    """Identify orchestrated pending kicks superseded by the new plan.

    Pending kicks carry no plan id, so the plan's work window is the ownership
    signal: an orchestrated kick whose work window overlaps the new plan's
    window but whose account is absent from the new plan is a stale leftover
    of an earlier plan for the same time. Orchestrated kicks for
    non-overlapping windows belong to other applied plans and are kept, as are
    kicks whose window timestamps cannot be parsed.
    """
    if work_start is None or work_end is None:
        return []
    planned_keys = {kick.account_key for kick in planned_kicks}
    removals: list[dict] = []
    for key, item in pending.items():
        if key in planned_keys:
            continue
        if item.reason != ScheduleReason.ORCHESTRATED.value:
            continue
        if not _pending_window_overlaps(item, work_start, work_end):
            continue
        removals.append(
            {
                "reason": "stale_orchestrated_not_in_plan",
                "existing": _pending_summary(item),
            }
        )
    return sorted(
        removals,
        key=lambda payload: (
            payload["existing"].get("kick_at") or "",
            payload["existing"].get("account_label") or "",
        ),
    )


def _pending_window_overlaps(
    pending: PendingKick,
    work_start: datetime,
    work_end: datetime,
) -> bool:
    try:
        pending_start = from_utc_iso(pending.work_start)
        pending_end = from_utc_iso(pending.work_end)
    except ValueError:
        return False
    return pending_start < work_end and pending_end > work_start


def apply_orchestration_plan(
    plan: OrchestrationPlan,
    *,
    now: datetime,
    current_time: datetime | None = None,
) -> OrchestrationPlan:
    # Callers pass the plan's build-time clock as ``now``; staleness must be
    # judged against the real clock at apply time (a user can sit at the
    # confirmation prompt long enough for planned kick times to pass).
    current = (current_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pending = load_pending_kicks(now)
    diff = build_pending_kick_diff(
        plan.planned_kicks,
        pending,
        plan.skipped_accounts,
        work_start=plan.work_start,
        work_end=plan.work_end,
    )
    if diff.has_conflicts():
        plan.diff = diff
        plan.read_only = True
        plan.applied = False
        plan.message = "not applied; resolve unmanaged pending-kick conflicts first"
        return plan

    stale_reason = _plan_stale_reason(plan, current)
    if stale_reason is not None:
        plan.diff = diff
        plan.read_only = True
        plan.applied = False
        plan.message = f"not applied; plan is stale, rebuild the plan ({stale_reason})"
        return plan

    updated = dict(pending)
    for removal in diff.removes_orchestrated:
        key = removal["existing"].get("account_key")
        current = updated.get(key)
        if current is not None and current.reason == ScheduleReason.ORCHESTRATED.value:
            del updated[key]
    for kick in plan.planned_kicks:
        current = updated.get(kick.account_key)
        if current is not None and current.reason != ScheduleReason.ORCHESTRATED.value:
            continue
        updated[kick.account_key] = kick.to_pending_kick(now=now, current=current)
    try:
        save_pending_kicks(updated)
    except PendingKickStateError as exc:
        plan.diff = diff
        plan.read_only = True
        plan.applied = False
        plan.message = f"not applied; {exc}"
        return plan
    plan.diff = diff
    plan.read_only = False
    plan.applied = True
    plan.message = "applied; orchestrated pending session kicks were written"
    if diff.removes_orchestrated:
        count = len(diff.removes_orchestrated)
        noun = "kick" if count == 1 else "kicks"
        plan.message += f"; removed {count} stale orchestrated pending {noun}"
    return plan


def _build_candidates(
    config: Config,
    inputs: list[AccountPlanInput],
    now: datetime,
    usage_overrides_by_key: dict[str, int],
) -> tuple[list[_Candidate], list[_Candidate], list[SkippedAccount], list[dict]]:
    candidates: list[_Candidate] = []
    specialist_candidates: list[_Candidate] = []
    skipped: list[SkippedAccount] = []
    considered: list[dict] = []
    for item in inputs:
        account = item.account
        key = account_key_string(account)
        has_usage_override = key in usage_overrides_by_key
        if has_usage_override:
            usable = int(usage_overrides_by_key[key])
            usage_source = "plan_override"
        else:
            usable, usage_source = _usable_session_minutes_and_source(account, config)
        role = normalize_orchestration_role(
            getattr(account, "orchestration_role", DEFAULT_ORCHESTRATION_ROLE)
        )
        effective_role = effective_orchestration_role(account, item.status)
        base = {
            "account_key": key,
            "account_label": account.label,
            "provider": account.provider,
            "usable_session_minutes": usable,
            "usage_source": usage_source,
            "plan_tier": account.plan_tier,
            "auto_kick": account.auto_kick,
            "session_auto_kick": account.session_auto_kick,
            "orchestration_role": role,
            "effective_orchestration_role": effective_role,
            "weekly_reserve_threshold_percent": account.weekly_reserve_threshold_percent,
        }
        if effective_role == "excluded":
            reason = "orchestration_excluded"
            skipped.append(
                SkippedAccount(
                    account_key=key,
                    account_label=account.label,
                    provider=account.provider,
                    reason=reason,
                )
            )
            considered.append({**base, "included": False, "reason": reason})
            continue
        status = item.status
        reason = _skip_reason(
            account,
            status,
            item.cache_stale,
            has_usage_override=has_usage_override,
        )
        if reason is not None:
            skipped.append(
                SkippedAccount(
                    account_key=key,
                    account_label=account.label,
                    provider=account.provider,
                    reason=reason,
                )
            )
            considered.append({**base, "included": False, "reason": reason})
            continue
        assert status is not None
        available_at = _session_available_at(status, now)
        active = _session_active_for_planning(status, now)
        remaining = _remaining_session_minutes(status, usable) if active else usable
        if remaining <= 0:
            reason = "session_exhausted"
            skipped.append(
                SkippedAccount(
                    account_key=key,
                    account_label=account.label,
                    provider=account.provider,
                    reason=reason,
                )
            )
            considered.append({**base, "included": False, "reason": reason})
            continue
        weekly_headroom = 100.0 - float(status.used_percent or 0.0)
        candidate = _Candidate(
            account=account,
            status=status,
            usable_minutes=usable,
            usage_source=usage_source,
            available_at=available_at,
            active=active,
            remaining_minutes=remaining,
            weekly_headroom=weekly_headroom,
            orchestration_role=role,
            effective_orchestration_role=effective_role,
        )
        if effective_role == "specialist":
            specialist_candidates.append(candidate)
        else:
            candidates.append(candidate)
        included_reason = "specialist_readiness" if effective_role == "specialist" else None
        considered_row = {
            **base,
            "included": True,
            "state": status.state.value,
            "available_at": to_utc_iso(available_at),
            "active_session": active,
            "remaining_usable_minutes": remaining,
            "weekly_headroom_percent": weekly_headroom,
        }
        if included_reason is not None:
            considered_row["reason"] = included_reason
            considered_row["specialist_readiness_planned"] = False
        considered.append(considered_row)
    return candidates, specialist_candidates, skipped, considered


def _skip_reason(
    account: AccountConfig,
    status: AccountStatus | None,
    cache_stale: bool,
    *,
    has_usage_override: bool = False,
) -> str | None:
    if not account.visible:
        return "hidden"
    if account.provider not in KICKABLE_PROVIDERS:
        return "provider_not_kickable"
    if not account.auto_kick:
        return "auto_kick_disabled"
    if not account.session_auto_kick:
        return "session_auto_kick_disabled"
    if (
        account.provider == "codex"
        and codex_rate_limit_id_for_account(account) != CODEX_DEFAULT_RATE_LIMIT_ID
        and account.usable_session_minutes is None
        and not has_usage_override
    ):
        return "spark_usable_session_unmeasured"
    if status is None:
        return "no_cached_status"
    if cache_stale or status.stale:
        return "stale_status"
    if status.state == AccountState.UNKNOWN:
        return "unknown_status"
    if weekly_quota_exhausted(status):
        return "weekly_exhausted"
    if status.session_used_percent is not None and status.session_used_percent >= 100.0:
        return "session_exhausted"
    if status.session_window_minutes is None:
        return "no_session_window"
    return None


def _session_active_for_planning(status: AccountStatus, now: datetime) -> bool:
    return status.state == AccountState.ACTIVE and (
        status.session_resets_at is None
        or status.session_resets_at > now.timestamp()
    )


def _session_available_at(status: AccountStatus, now: datetime) -> datetime:
    if status.state in {AccountState.FRESH, AccountState.ACTIVE}:
        return now
    if status.session_resets_at is not None:
        return datetime.fromtimestamp(status.session_resets_at, tz=timezone.utc)
    if status.session_resets_in_seconds is not None:
        return now + timedelta(seconds=max(0, status.session_resets_in_seconds))
    return now


def _remaining_session_minutes(status: AccountStatus, usable_minutes: int) -> int:
    used = status.session_used_percent
    if used is None:
        return usable_minutes
    remaining = usable_minutes * max(0.0, 1.0 - (float(used) / 100.0))
    return int(round(remaining))


def _plan_segments(
    *,
    candidates: list[_Candidate],
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> tuple[list[PlannedSegment], list[PlannedKick], list[dict]]:
    best = _best_scenario_plan(
        candidates=candidates,
        work_start=work_start,
        work_end=work_end,
        now=now,
    )
    planned_kicks = sorted(
        best.planned_kicks,
        key=lambda item: (item.kick_at, item.account_label, item.purpose),
    )
    return list(best.segments), planned_kicks, list(best.gaps)


def _best_scenario_plan(
    *,
    candidates: list[_Candidate],
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> _ScenarioPlan:
    if not candidates:
        return _finalize_scenario(
            _ScenarioPlan(),
            cursor=max(work_start, now),
            work_end=work_end,
        )

    candidates_by_key = {candidate.key: candidate for candidate in candidates}
    best: _ScenarioPlan | None = None
    best_score: tuple | None = None
    visited_bounds: dict[tuple, tuple] = {}

    def record_terminal(plan: _ScenarioPlan, cursor: datetime) -> None:
        nonlocal best, best_score
        terminal = _finalize_scenario(plan, cursor=cursor, work_end=work_end)
        score = _scenario_score(
            terminal,
            work_start=work_start,
            work_end=work_end,
            candidates_by_key=candidates_by_key,
        )
        if best_score is None or score > best_score:
            best = terminal
            best_score = score

    def search(plan: _ScenarioPlan, cursor: datetime) -> None:
        if cursor >= work_end:
            record_terminal(plan, cursor)
            return
        if best_score is not None:
            bound = _scenario_dominant_score_bound(plan, cursor=cursor, work_end=work_end)
            if bound < best_score[: len(bound)]:
                return
            state_key = _scenario_search_state_key(plan, cursor)
            previous_bound = visited_bounds.get(state_key)
            if previous_bound is not None and previous_bound > bound:
                return
            if previous_bound is None or bound > previous_bound:
                visited_bounds[state_key] = bound
        choices = _scenario_choices(
            candidates=candidates,
            plan=plan,
            cursor=cursor,
            work_start=work_start,
            work_end=work_end,
            now=now,
        )
        if not choices:
            record_terminal(plan, cursor)
            return
        for choice in choices:
            next_plan = _apply_scenario_choice(plan, choice, cursor)
            if choice.end <= cursor:
                continue
            search(next_plan, choice.end)

    search(_ScenarioPlan(), max(work_start, now))
    if best is None:
        return _finalize_scenario(
            _ScenarioPlan(),
            cursor=max(work_start, now),
            work_end=work_end,
        )
    return best


def _scenario_dominant_score_bound(
    plan: _ScenarioPlan,
    *,
    cursor: datetime,
    work_end: datetime,
) -> tuple:
    account_labels = [segment.account_label or "" for segment in plan.segments]
    covered = sum(_minutes(segment.start, segment.end) for segment in plan.segments)
    covered_bound = covered + _minutes(cursor, work_end)
    gap_minutes = sum(_gap_minutes(gap) for gap in plan.gaps)
    distinct_accounts = len({label for label in account_labels if label})
    switches = sum(
        1
        for previous, current in zip(account_labels, account_labels[1:], strict=False)
        if previous and current and previous != current
    )
    tiny_segments = sum(1 for segment in plan.segments if _is_tiny_segment(segment))
    return (
        covered_bound,
        -gap_minutes,
        True,
        -len(plan.planned_kicks),
        -distinct_accounts,
        -switches,
        -tiny_segments,
    )


def _scenario_search_state_key(plan: _ScenarioPlan, cursor: datetime) -> tuple:
    last_label = plan.segments[-1].account_label if plan.segments else None
    return (
        to_utc_iso(cursor),
        tuple(sorted(plan.active_used)),
        tuple(sorted(plan.pending_used)),
        tuple(sorted(plan.early_anchor_used)),
        last_label,
    )


def _scenario_choices(
    *,
    candidates: list[_Candidate],
    plan: _ScenarioPlan,
    cursor: datetime,
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> list[_ScenarioChoice]:
    choices: list[_ScenarioChoice] = []
    for candidate in candidates:
        if candidate.key in plan.early_anchor_used:
            continue
        reset_boundary = _reset_boundary_choice(
            candidate,
            plan=plan,
            cursor=cursor,
            work_start=work_start,
            work_end=work_end,
            now=now,
        )
        if reset_boundary is not None:
            choices.append(reset_boundary)
        active = _active_scenario_choice(
            candidate,
            plan=plan,
            cursor=cursor,
            work_start=work_start,
            work_end=work_end,
            now=now,
        )
        if active is not None:
            choices.append(active)
        fresh = _fresh_scenario_choice(
            candidate,
            plan=plan,
            cursor=cursor,
            work_start=work_start,
            work_end=work_end,
            now=now,
        )
        if fresh is not None:
            choices.append(fresh)
    sorted_choices = sorted(
        choices,
        key=lambda choice: (
            choice.start,
            choice.end,
            choice.candidate.account.label,
            choice.segments[0].source if choice.segments else "",
        ),
    )
    return _prune_equivalent_scenario_choices(
        sorted_choices,
        work_start=work_start,
        work_end=work_end,
    )


def _prune_equivalent_scenario_choices(
    choices: list[_ScenarioChoice],
    *,
    work_start: datetime,
    work_end: datetime,
) -> list[_ScenarioChoice]:
    if not choices:
        return choices
    work_minutes = max(1, _minutes(work_start, work_end))
    grouped: dict[tuple, list[_ScenarioChoice]] = {}
    ordered_keys: list[tuple] = []
    for choice in choices:
        key = _scenario_choice_equivalence_key(choice)
        if key not in grouped:
            ordered_keys.append(key)
            grouped[key] = []
        grouped[key].append(choice)

    pruned: list[_ScenarioChoice] = []
    for key in ordered_keys:
        group = grouped[key]
        if len(group) == 1:
            pruned.extend(group)
            continue
        min_usable = max(1, min(choice.candidate.usable_minutes for choice in group))
        keep = min(len(group), (work_minutes + min_usable - 1) // min_usable + 2)
        pruned.extend(
            sorted(group, key=_scenario_choice_equivalent_use_rank, reverse=True)[:keep]
        )
    return sorted(
        pruned,
        key=lambda choice: (
            choice.start,
            choice.end,
            choice.candidate.account.label,
            choice.segments[0].source if choice.segments else "",
        ),
    )


def _scenario_choice_equivalence_key(choice: _ScenarioChoice) -> tuple:
    planned_offset = None
    if choice.planned_kick is not None:
        planned_offset = _minutes(choice.planned_kick.kick_at, choice.start)
    return (
        to_utc_iso(choice.start),
        to_utc_iso(choice.end),
        planned_offset,
        choice.uses_active,
        choice.uses_pending,
        choice.uses_early_anchor,
        tuple(
            (
                segment.source,
                _minutes(segment.start, segment.end),
                _minutes(segment.start, segment.kick_at) if segment.kick_at is not None else None,
            )
            for segment in choice.segments
        ),
    )


def _scenario_choice_equivalent_use_rank(choice: _ScenarioChoice) -> tuple:
    candidate = choice.candidate
    uses_backup = (
        _candidate_role_rank(candidate) <= ORCHESTRATION_ROLE_RANK["backup"]
    )
    return (
        not uses_backup,
        -_candidate_reserve_value(candidate),
        candidate.weekly_headroom,
        candidate.account.label,
    )


def _reset_boundary_choice(
    candidate: _Candidate,
    *,
    plan: _ScenarioPlan,
    cursor: datetime,
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> _ScenarioChoice | None:
    if candidate.key in plan.pending_used or _scenario_account_used(plan, candidate.key):
        return None
    session_window = candidate.status.session_window_minutes
    if session_window is None or session_window <= 0:
        return None
    reset_at = cursor + timedelta(minutes=candidate.usable_minutes)
    if not cursor < reset_at < work_end:
        return None
    coverage_end = min(work_end, reset_at + timedelta(minutes=candidate.usable_minutes))
    plain_fresh_end = min(work_end, cursor + timedelta(minutes=candidate.usable_minutes))
    if coverage_end <= plain_fresh_end:
        return None
    kick_at = reset_at - timedelta(minutes=session_window)
    fresh_at = _fresh_kick_available_at(candidate, now)
    if fresh_at is None or kick_at < max(now, fresh_at):
        return None
    planned = PlannedKick(
        account_key=candidate.key,
        account_label=candidate.account.label,
        provider=candidate.account.provider,
        kick_at=kick_at,
        work_start=work_start,
        work_end=work_end,
        segment_start=cursor,
        segment_end=coverage_end,
        usable_session_minutes=candidate.usable_minutes,
    )
    return _ScenarioChoice(
        candidate=candidate,
        start=cursor,
        end=coverage_end,
        segments=(
            PlannedSegment(
                account_key=candidate.key,
                account_label=candidate.account.label,
                provider=candidate.account.provider,
                start=cursor,
                end=reset_at,
                source="planned_early_anchor",
                usable_session_minutes=candidate.usable_minutes,
                kick_at=kick_at,
                note=_candidate_note(
                    candidate,
                    f"pre-anchor places the expected reset at {_format_reset_boundary_time(reset_at)}",
                ),
            ),
            PlannedSegment(
                account_key=candidate.key,
                account_label=candidate.account.label,
                provider=candidate.account.provider,
                start=reset_at,
                end=coverage_end,
                source="expected_reset_reuse",
                usable_session_minutes=candidate.usable_minutes,
                note=_candidate_note(
                    candidate,
                    "same account should be fresh again at the reset boundary",
                ),
            ),
        ),
        planned_kick=planned,
        uses_pending=True,
        uses_early_anchor=True,
    )


def _scenario_account_used(plan: _ScenarioPlan, key: str) -> bool:
    return any(segment.account_key == key for segment in plan.segments)


def _active_scenario_choice(
    candidate: _Candidate,
    *,
    plan: _ScenarioPlan,
    cursor: datetime,
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> _ScenarioChoice | None:
    if not candidate.active or candidate.key in plan.active_used:
        return None
    if candidate.key in plan.pending_used:
        return None
    start = max(cursor, work_start, now, candidate.available_at)
    end = _active_segment_end(candidate, start, work_end)
    if end <= start:
        return None
    if _minutes(start, end) < ORCHESTRATION_TINY_ACTIVE_SEGMENT_MINUTES:
        return None
    return _ScenarioChoice(
        candidate=candidate,
        start=start,
        end=end,
        segments=(
            PlannedSegment(
                account_key=candidate.key,
                account_label=candidate.account.label,
                provider=candidate.account.provider,
                start=start,
                end=end,
                source="active_session",
                usable_session_minutes=candidate.usable_minutes,
                note=_candidate_note(candidate, _active_session_note(candidate)),
            ),
        ),
        planned_kick=None,
        uses_active=True,
    )


def _fresh_scenario_choice(
    candidate: _Candidate,
    *,
    plan: _ScenarioPlan,
    cursor: datetime,
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> _ScenarioChoice | None:
    if candidate.key in plan.pending_used:
        return None
    earliest, source, note = _fresh_scenario_availability(candidate, work_start, work_end, now)
    if earliest is None:
        return None
    start = max(cursor, work_start, now, earliest)
    end = min(work_end, start + timedelta(minutes=candidate.usable_minutes))
    if end <= start:
        return None
    planned = PlannedKick(
        account_key=candidate.key,
        account_label=candidate.account.label,
        provider=candidate.account.provider,
        kick_at=start,
        work_start=work_start,
        work_end=work_end,
        segment_start=start,
        segment_end=end,
        usable_session_minutes=candidate.usable_minutes,
    )
    return _ScenarioChoice(
        candidate=candidate,
        start=start,
        end=end,
        segments=(
            PlannedSegment(
                account_key=candidate.key,
                account_label=candidate.account.label,
                provider=candidate.account.provider,
                start=start,
                end=end,
                source=source,
                usable_session_minutes=candidate.usable_minutes,
                kick_at=start,
                note=_candidate_note(candidate, note),
            ),
        ),
        planned_kick=planned,
        uses_pending=True,
    )


def _fresh_scenario_availability(
    candidate: _Candidate,
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> tuple[datetime | None, str, str | None]:
    if not candidate.active:
        return max(now, candidate.available_at), "planned_fresh_session", None
    if candidate.status.session_resets_at is None:
        return None, "planned_fresh_session", None
    reset_at = datetime.fromtimestamp(candidate.status.session_resets_at, tz=timezone.utc)
    if reset_at <= work_start:
        return max(now, work_start, reset_at), "planned_fresh_session", None
    if reset_at >= work_end:
        return None, "natural_reset_reuse", None
    return (
        max(now, reset_at),
        "natural_reset_reuse",
        "account should be fresh after provider reset",
    )


def _apply_scenario_choice(
    plan: _ScenarioPlan,
    choice: _ScenarioChoice,
    cursor: datetime,
) -> _ScenarioPlan:
    gaps = list(plan.gaps)
    if choice.start > cursor:
        gaps.append(
            {
                "start": to_utc_iso(cursor),
                "end": to_utc_iso(choice.start),
                "reason": "account_not_ready_until_planned_kick",
            }
        )
    planned_kicks = list(plan.planned_kicks)
    pending_used = set(plan.pending_used)
    if choice.planned_kick is not None:
        planned_kicks = [
            item for item in planned_kicks if item.account_key != choice.planned_kick.account_key
        ]
        planned_kicks.append(choice.planned_kick)
        pending_used.add(choice.planned_kick.account_key)
    active_used = set(plan.active_used)
    if choice.uses_active:
        active_used.add(choice.candidate.key)
    early_anchor_used = set(plan.early_anchor_used)
    if choice.uses_early_anchor:
        early_anchor_used.add(choice.candidate.key)
    return _ScenarioPlan(
        segments=plan.segments + choice.segments,
        planned_kicks=tuple(planned_kicks),
        gaps=tuple(gaps),
        active_used=frozenset(active_used),
        pending_used=frozenset(pending_used),
        early_anchor_used=frozenset(early_anchor_used),
    )


def _finalize_scenario(
    plan: _ScenarioPlan,
    *,
    cursor: datetime,
    work_end: datetime,
) -> _ScenarioPlan:
    if cursor >= work_end:
        return plan
    return _ScenarioPlan(
        segments=plan.segments,
        planned_kicks=plan.planned_kicks,
        gaps=plan.gaps
        + (
            {
                "start": to_utc_iso(cursor),
                "end": to_utc_iso(work_end),
                "reason": "insufficient_accounts_or_usable_minutes",
            },
        ),
        active_used=plan.active_used,
        pending_used=plan.pending_used,
        early_anchor_used=plan.early_anchor_used,
    )


def _scenario_score(
    plan: _ScenarioPlan,
    *,
    work_start: datetime,
    work_end: datetime,
    candidates_by_key: dict[str, _Candidate],
) -> tuple:
    covered = sum(_minutes(segment.start, segment.end) for segment in plan.segments)
    gap_minutes = sum(_gap_minutes(gap) for gap in plan.gaps)
    finishes = bool(plan.segments) and max(segment.end for segment in plan.segments) >= work_end
    account_labels = [segment.account_label or "" for segment in plan.segments]
    distinct_accounts = len({label for label in account_labels if label})
    switches = sum(
        1
        for previous, current in zip(account_labels, account_labels[1:], strict=False)
        if previous and current and previous != current
    )
    tiny_segments = sum(1 for segment in plan.segments if _is_tiny_segment(segment))
    average_segment = covered / len(plan.segments) if plan.segments else 0.0
    recovery_score = _recovery_score(plan, candidates_by_key, work_end)
    continuation_score = _continuation_score(plan, candidates_by_key)
    role_score = sum(
        _segment_role_score(segment, candidates_by_key) * _minutes(segment.start, segment.end)
        for segment in plan.segments
    )
    preserve_use_first = sum(
        segment.start.timestamp() * _minutes(segment.start, segment.end)
        for segment in plan.segments
        if _segment_role_score(segment, candidates_by_key) == ORCHESTRATION_ROLE_RANK["use_first"]
    )
    weekly_headroom = sum(_segment_weekly_headroom(segment, candidates_by_key) for segment in plan.segments)
    stable = tuple(
        (segment.account_label or "", segment.provider or "", to_utc_iso(segment.start))
        for segment in plan.segments
    )
    return (
        covered,
        -gap_minutes,
        finishes,
        -len(plan.planned_kicks),
        -distinct_accounts,
        -switches,
        -tiny_segments,
        average_segment,
        recovery_score,
        continuation_score,
        role_score,
        preserve_use_first,
        weekly_headroom,
        stable,
    )


def _minutes(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _gap_minutes(gap: dict) -> int:
    try:
        return _minutes(from_utc_iso(gap["start"]), from_utc_iso(gap["end"]))
    except (KeyError, ValueError):
        return 0


def _is_tiny_segment(segment: PlannedSegment) -> bool:
    return _minutes(segment.start, segment.end) < ORCHESTRATION_TINY_ACTIVE_SEGMENT_MINUTES


def _segment_role_score(
    segment: PlannedSegment,
    candidates_by_key: dict[str, _Candidate],
) -> int:
    candidate = candidates_by_key.get(segment.account_key or "")
    return _candidate_role_rank(candidate) if candidate is not None else 0


def _segment_weekly_headroom(
    segment: PlannedSegment,
    candidates_by_key: dict[str, _Candidate],
) -> float:
    candidate = candidates_by_key.get(segment.account_key or "")
    return candidate.weekly_headroom if candidate is not None else 0.0


def _continuation_score(
    plan: _ScenarioPlan,
    candidates_by_key: dict[str, _Candidate],
) -> tuple[float, float]:
    used_keys = {
        segment.account_key
        for segment in plan.segments
        if segment.account_key is not None
    }
    backup_minutes_used = sum(
        _minutes(segment.start, segment.end)
        for segment in plan.segments
        if _segment_role_score(segment, candidates_by_key) == ORCHESTRATION_ROLE_RANK["backup"]
    )
    reserve_value = sum(
        _candidate_reserve_value(candidate)
        for key, candidate in candidates_by_key.items()
        if key not in used_keys
    )
    return (-float(backup_minutes_used), reserve_value)


def _recovery_score(
    plan: _ScenarioPlan,
    candidates_by_key: dict[str, _Candidate],
    work_end: datetime,
) -> tuple[int, int]:
    final_gap = _final_gap(plan, work_end)
    if final_gap is None:
        return (0, 0)
    gap_start = from_utc_iso(final_gap["start"])
    gap_minutes = _gap_minutes(final_gap)
    next_ready = min(
        (
            _candidate_next_ready_at(candidate, plan, gap_start)
            for candidate in candidates_by_key.values()
        ),
        default=gap_start + timedelta(days=365),
    )
    delay_minutes = _minutes(gap_start, max(gap_start, next_ready))
    return (-gap_minutes, -delay_minutes)


def _final_gap(plan: _ScenarioPlan, work_end: datetime) -> dict | None:
    for gap in reversed(plan.gaps):
        try:
            if from_utc_iso(gap["end"]) >= work_end:
                return gap
        except (KeyError, ValueError):
            continue
    return None


def _candidate_next_ready_at(
    candidate: _Candidate,
    plan: _ScenarioPlan,
    fallback: datetime,
) -> datetime:
    planned = next(
        (kick for kick in plan.planned_kicks if kick.account_key == candidate.key),
        None,
    )
    if planned is not None and candidate.status.session_window_minutes is not None:
        return planned.kick_at + timedelta(minutes=candidate.status.session_window_minutes)
    if candidate.status.session_resets_at is not None:
        return datetime.fromtimestamp(candidate.status.session_resets_at, tz=timezone.utc)
    return max(candidate.available_at, fallback)


def _candidate_reserve_value(candidate: _Candidate) -> float:
    role_rank = _candidate_role_rank(candidate)
    if role_rank <= ORCHESTRATION_ROLE_RANK["backup"]:
        return 0.0
    return (
        (role_rank * 10_000.0)
        + (candidate.usable_minutes * 10.0)
        + max(0.0, candidate.weekly_headroom)
    )


def _active_session_note(candidate: _Candidate) -> str:
    remaining = max(0, candidate.remaining_minutes)
    if candidate.status.session_used_percent is None:
        return f"active now; estimated {remaining}m remaining from planning default"
    used = max(0.0, min(100.0, float(candidate.status.session_used_percent)))
    return f"active now; session used {_format_percent(used)}; estimated {remaining}m remaining"


def _format_percent(value: float) -> str:
    if value.is_integer():
        return f"{int(value)}%"
    return f"{value:.1f}%"


def _plan_specialist_readiness(
    *,
    candidates: list[_Candidate],
    work_start: datetime,
    work_end: datetime,
    now: datetime,
) -> tuple[list[PlannedKick], list[SkippedAccount]]:
    planned: list[PlannedKick] = []
    skipped: list[SkippedAccount] = []
    for candidate in candidates:
        session_window = candidate.status.session_window_minutes
        if session_window is None or session_window <= 0:
            skipped.append(_candidate_skip(candidate, "no_session_window"))
            continue
        reset_at = work_start + timedelta(minutes=candidate.usable_minutes)
        kick_at = reset_at - timedelta(minutes=session_window)
        if kick_at < now:
            skipped.append(_candidate_skip(candidate, "specialist_early_kick_window_missed"))
            continue
        if kick_at > work_start:
            skipped.append(_candidate_skip(candidate, "specialist_early_kick_after_work_start"))
            continue
        fresh_at = _fresh_kick_available_at(candidate, now)
        if fresh_at is None or fresh_at > kick_at:
            skipped.append(_candidate_skip(candidate, "specialist_not_available_for_early_kick"))
            continue
        planned.append(
            PlannedKick(
                account_key=candidate.key,
                account_label=candidate.account.label,
                provider=candidate.account.provider,
                kick_at=kick_at,
                work_start=work_start,
                work_end=work_end,
                segment_start=work_start,
                segment_end=min(work_end, reset_at + timedelta(minutes=candidate.usable_minutes)),
                usable_session_minutes=candidate.usable_minutes,
                purpose=PENDING_KICK_PURPOSE_SPECIALIST_READINESS,
            )
        )
    return planned, skipped


def _candidate_skip(candidate: _Candidate, reason: str) -> SkippedAccount:
    return SkippedAccount(
        account_key=candidate.key,
        account_label=candidate.account.label,
        provider=candidate.account.provider,
        reason=reason,
    )


def _mark_considered_specialist_skips(
    considered: list[dict],
    skipped: list[SkippedAccount],
) -> None:
    reasons = {item.account_key: item.reason for item in skipped}
    for row in considered:
        reason = reasons.get(row.get("account_key"))
        if reason is not None:
            row["included"] = False
            row["reason"] = reason
            row["specialist_readiness_planned"] = False


def _mark_considered_specialist_planned(
    considered: list[dict],
    planned: list[PlannedKick],
) -> None:
    keys = {kick.account_key for kick in planned}
    for row in considered:
        if row.get("account_key") in keys:
            row["specialist_readiness_planned"] = True


def _fresh_kick_available_at(candidate: _Candidate, now: datetime) -> datetime | None:
    if candidate.active:
        if candidate.status.session_resets_at is None:
            return None
        reset_at = datetime.fromtimestamp(candidate.status.session_resets_at, tz=timezone.utc)
        return max(now, reset_at)
    return max(now, candidate.available_at)


def _format_reset_boundary_time(value: datetime) -> str:
    return value.astimezone().strftime("%H:%M")


def _candidate_note(candidate: _Candidate, base: str | None = None) -> str | None:
    parts = [base] if base else []
    if candidate.usage_source == "plan_override":
        parts.append(f"usage={candidate.usable_minutes}m")
    return "; ".join(parts) if parts else None


def _candidate_role_rank(candidate: _Candidate) -> int:
    return ORCHESTRATION_ROLE_RANK.get(candidate.effective_orchestration_role, 0)


def _active_segment_end(candidate: _Candidate, cursor: datetime, work_end: datetime) -> datetime:
    end = cursor + timedelta(minutes=candidate.remaining_minutes)
    if candidate.status.session_resets_at is not None:
        reset_end = datetime.fromtimestamp(candidate.status.session_resets_at, tz=timezone.utc)
        end = min(end, reset_end)
    return min(work_end, end)


def _pending_matches_planned(pending: PendingKick, planned: PlannedKick) -> bool:
    return (
        pending.kick_at == to_utc_iso(planned.kick_at)
        and pending.purpose == planned.purpose
        and pending.work_start == to_utc_iso(planned.work_start)
        and pending.work_end == to_utc_iso(planned.work_end)
        and pending.window_basis == SchedulingWindowBasis.SESSION.value
    )


def _pending_summary(pending: PendingKick) -> dict:
    data = pending.to_dict()
    for field_name in ("created_at", "kick_at", "work_start", "work_end"):
        try:
            data[f"{field_name}_epoch"] = from_utc_iso(data[field_name]).timestamp()
        except (KeyError, ValueError):
            pass
    return data


def _plan_limitations(gaps: list[dict]) -> list[str]:
    limitations = [
        "uses cached provider state only; due-pending execution rechecks before kicking",
        "usable_session_minutes and tier defaults are rough planning estimates, not measured caps",
        "active-session remaining coverage is estimated from cached session_used_percent",
        "TokenKick prints the account timeline but cannot force the user to switch accounts",
        "manual app usage can change the plan before pending kicks become due",
        "v1 requires explicit --apply; there is no full autonomous mode",
    ]
    if gaps:
        limitations.insert(0, "plan has uncovered gaps with current eligible accounts")
    return limitations
