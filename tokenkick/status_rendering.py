"""Status table and JSON rendering helpers for TokenKick CLI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rich.table import Table
from rich.text import Text

from .kicker import KICKABLE_PROVIDERS
from .models import (
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    account_key_string,
    weekly_quota_exhausted,
)
from .recovery_hints import codex_refresh_recovery_hint
from .scheduling import (
    PendingKick,
    from_utc_iso,
    load_pending_kicks,
    pending_kick_next_action_at,
    schedule_for_account,
)
from .status_cache import _read_dormant_hint_state, _write_dormant_hint_state


def _cli():
    from . import cli as cli_mod

    return cli_mod


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_weekly_reset(seconds: int | None) -> str:
    return f"[dim]weekly[/dim]  {_format_relative_reset(seconds, include_days=True)}"


def _format_session_reset(seconds: int | None) -> str:
    return f"[cyan]session {_format_relative_reset(seconds, include_days=False)}[/cyan]"


def _format_session_reset_for_status(
    status: AccountStatus,
    *,
    phantom_session: bool = False,
    codex_unconfirmed_session: bool = False,
) -> str:
    if _status_weekly_exhausted(status):
        return "[cyan]session blocked[/cyan]"
    if phantom_session:
        return "[cyan]session phantom[/cyan]"
    if codex_unconfirmed_session:
        return "[cyan]session unconfirmed[/cyan]"
    return _format_session_reset(status.session_resets_in_seconds)


def _format_relative_reset(seconds: int | None, include_days: bool) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "reset ready"

    seconds = int(seconds)
    if not include_days:
        hours_total = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"in {hours_total}h {minutes}m"

    minutes_total = seconds // 60
    if minutes_total < 60:
        return f"in {minutes_total}m"

    hours_total = seconds // 3600
    days = hours_total // 24
    hours = hours_total % 24
    if days > 0:
        return f"in {days}d {hours}h"
    return f"in {hours_total}h"


def _format_used_percent(value: float | None) -> str:
    if value is None:
        return "—"
    rounded = round(value)
    color = _usage_color(value)
    return f"[{color}]{rounded:>3}%[/{color}]"


def _format_used_cell(
    status: AccountStatus,
    provider: str,
    *,
    session: bool = False,
) -> str:
    if provider == "openrouter":
        return "—" if session else _format_openrouter_balance(status)
    value = status.session_used_percent if session else status.used_percent
    return _format_used_percent(value)


def _format_used_labeled_cell(
    status: AccountStatus,
    provider: str,
    *,
    session: bool = False,
) -> str:
    label = "s" if session else "w"
    value = _format_used_cell(status, provider, session=session)
    return f"[dim]{label}[/dim] {value}"


def _format_antigravity_quota_label(window: dict) -> str:
    family = window.get("family")
    kind = window.get("window_kind")
    if family == "gemini":
        family_text = "Gemini"
    elif family == "claude_gpt":
        family_text = "Claude/GPT"
    else:
        family_text = str(window.get("title") or "Antigravity")
    kind_text = "weekly" if kind == "weekly" else "5h"
    return f"{family_text} {kind_text}"


def _format_antigravity_quota_reset(window: dict) -> str:
    kind = window.get("window_kind")
    seconds = _numeric_int(window.get("resets_in_seconds"))
    label = "weekly" if kind == "weekly" else "session"
    include_days = kind == "weekly"
    return f"[dim]{label}[/dim]  {_format_relative_reset(seconds, include_days=include_days)}"


def _numeric_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _numeric_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _format_openrouter_balance(status: AccountStatus) -> str:
    if status.balance_remaining is None or status.balance_limit is None:
        return _format_used_percent(status.used_percent)
    spent_percent = status.balance_spent_percent
    color = _usage_color(spent_percent if spent_percent is not None else status.used_percent or 0)
    return (
        f"[{color}]"
        f"${status.balance_remaining:.2f}/${status.balance_limit:.2f} left"
        f"[/{color}]"
    )


def _usage_color(value: float) -> str:
    if value <= 25:
        return "green"
    if value <= 50:
        return "yellow"
    if value <= 75:
        return "dark_orange"
    return "red"


def _pending_by_label() -> dict[str, PendingKick]:
    return {
        pending.account_label: pending
        for pending in load_pending_kicks(datetime.now(timezone.utc)).values()
    }


def _format_pending_kick_cell(pending: PendingKick | None, *, verbose: bool = False) -> str:
    if pending is None:
        return "—"
    if pending.gave_up_at:
        rendered = f"failed ({pending.attempt_count} attempts)"
        if verbose and pending.last_error:
            rendered = f"{rendered}: {pending.last_error[:40]}"
        return rendered
    try:
        action_at = pending_kick_next_action_at(pending)
        if action_at is None:
            return "—"
        kick_at = action_at.astimezone()
    except ValueError:
        return "—"
    now = datetime.now(kick_at.tzinfo)
    time_text = kick_at.strftime("%H:%M %Z")
    if kick_at.date() == now.date():
        rendered = time_text
    elif kick_at.date() == (now + timedelta(days=1)).date():
        rendered = f"+1d {time_text}"
    else:
        rendered = f"{kick_at.strftime('%a')} {time_text}"
    if pending.next_retry_at:
        rendered = f"retry {rendered}"
    if verbose:
        parts = [pending.window_basis]
        if pending.purpose != "coverage":
            parts.append(pending.purpose.replace("_", " "))
        if pending.attempt_count:
            parts.append(f"attempt {pending.attempt_count}")
        if pending.last_error:
            parts.append(pending.last_error[:40])
        rendered = f"{rendered} ({', '.join(parts)})"
    return rendered


def _status_rows_as_dict(
    statuses: list[AccountStatus],
    accounts: list[AccountConfig],
    config: Config | None = None,
    cache_entries: dict[str, dict] | None = None,
) -> list[dict]:
    pending = _pending_by_label()
    accounts_by_label = {account.label: account for account in accounts}
    cache_entries = cache_entries or {}
    history = _cli().load_kick_history(limit=200)
    rows = []
    for status in _sort_statuses(statuses, accounts):
        row = status.to_dict()
        account = accounts_by_label.get(status.label)
        row.setdefault("observed_at", _cli()._status_cache_observed_at())
        row.setdefault("source_detail", account.source.value if account is not None else "unknown")
        row.setdefault("stale", False)
        row.setdefault("stale_seconds", None)
        if account is not None:
            row["provider"] = account.provider
            row["account_key"] = account_key_string(account)
            row["auto_kick"] = account.auto_kick
            row["weekly_auto_kick"] = bool(account.weekly_auto_kick)
            row["session_auto_kick"] = bool(account.session_auto_kick)
            row["monitor_only"] = _cli()._is_monitor_only_provider(account.provider)
            row["visible"] = account.visible
            schedule = schedule_for_account(config.schedule, account.label) if config is not None else None
            row["schedule_enabled"] = bool(schedule and schedule.enabled)
            row["schedule_weekdays"] = schedule.weekdays if schedule is not None else None
            row["schedule_weekends"] = schedule.weekends if schedule is not None else None
        if isinstance(row.get("used_percent"), (int, float)):
            row["weekly_used_percent"] = row["used_percent"]
            row["weekly_headroom_percent"] = max(0.0, 100.0 - float(row["used_percent"]))
        cache_entry = cache_entries.get(account_key_string(account)) if account is not None else None
        refresh_error = cache_entry.get("refresh_error") if cache_entry else None
        if isinstance(refresh_error, str) and refresh_error:
            row["refresh_error"] = refresh_error
            row["stale"] = True
        pending_kick = pending.get(status.label)
        eligibility = _cli()._kick_eligibility(
            account,
            status,
            history=history,
            pending_kick=pending_kick,
        )
        row["kickable"] = eligibility.kickable
        row["kick_type"] = eligibility.kick_type
        row["kick_blocked_reason"] = eligibility.reason
        row["kick_cooldown_remaining_seconds"] = eligibility.cooldown_remaining
        if pending_kick is not None:
            next_action_at = pending_kick_next_action_at(pending_kick)
            row["next_kick_at"] = (
                next_action_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if next_action_at is not None
                else pending_kick.kick_at
            )
            row["pending_kick"] = {
                "account_key": pending_kick.account_key,
                "account_label": pending_kick.account_label,
                "provider": pending_kick.provider,
                "kick_at": pending_kick.kick_at,
                "next_action_at": row["next_kick_at"],
                "reason": pending_kick.reason,
                "window_basis": pending_kick.window_basis,
                "work_start": pending_kick.work_start,
                "work_end": pending_kick.work_end,
                "attempt_count": pending_kick.attempt_count,
                "last_attempt_at": pending_kick.last_attempt_at,
                "last_error": pending_kick.last_error,
                "next_retry_at": pending_kick.next_retry_at,
                "gave_up_at": pending_kick.gave_up_at,
            }
            row["pending_attempt_count"] = pending_kick.attempt_count
            row["pending_last_error"] = pending_kick.last_error
            row["pending_next_retry_at"] = pending_kick.next_retry_at
            row["pending_gave_up_at"] = pending_kick.gave_up_at
            try:
                row["next_kick_at_local"] = from_utc_iso(
                    row["next_kick_at"]
                ).astimezone().strftime("%Y-%m-%d %H:%M %Z")
            except ValueError:
                pass
        rows.append(row)
    return rows


def _status_json_payload(
    *,
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    metadata_accounts: list[AccountConfig],
    metadata_statuses: list[AccountStatus],
    cached: bool,
    refresh_error: str | None,
    config: Config | None = None,
    cache_entries: dict[str, dict] | None = None,
) -> dict:
    account_rows = _status_rows_as_dict(statuses, accounts, config, cache_entries)
    return {
        "cached": cached,
        "cached_at": _oldest_status_observed_at(account_rows),
        "refresh_error": refresh_error,
        "refresh_in_progress": _cli()._status_refresh_lock_active(),
        "schema_version": 1,
        "accounts": account_rows,
    }


def _oldest_status_observed_at(rows: list[dict]) -> str | None:
    observed = [
        row["observed_at"]
        for row in rows
        if isinstance(row.get("observed_at"), str)
    ]
    return min(observed) if observed else None




def _render_status_table(
    statuses: list[AccountStatus],
    accounts: list[AccountConfig] | None = None,
    config: Config | None = None,
    cache_entries: dict[str, dict] | None = None,
    *,
    verbose: bool = False,
) -> None:
    providers_by_label = {account.label: account.provider for account in accounts or []}
    accounts_by_label = {account.label: account for account in accounts or []}
    cache_entries = cache_entries or {}
    pending = _pending_by_label()
    sorted_statuses = _sort_statuses(statuses, accounts)
    history = _cli().load_kick_history(limit=200)
    queued_by_label: dict[str, str] = {}
    show_queued_column = False
    for status in sorted_statuses:
        account = accounts_by_label.get(status.label)
        queued = _format_pending_kick_cell(pending.get(status.label), verbose=verbose)
        if (
            queued == "—"
            and config is not None
            and account is not None
            and schedule_for_account(config.schedule, account.label) is not None
        ):
            queued = "scheduled"
        queued_by_label[status.label] = queued
        if queued != "—":
            show_queued_column = True

    title = Text.assemble(
        ("Token", "bold white"),
        ("Kick", "bold green"),
        (" — ", "bold white"),
        ("Reset?", "bold white"),
        (" ", "bold white"),
        ("Go.", "bold green"),
    )
    table = Table(title=title, show_header=True, expand=True)
    table.add_column("Account", style="bold", no_wrap=True, min_width=24, max_width=34, ratio=1)
    table.add_column("State", no_wrap=True, width=20)
    table.add_column("Resets", no_wrap=True, width=18)
    table.add_column("Used", justify="right", no_wrap=True, width=8)
    if show_queued_column:
        table.add_column("Queued", no_wrap=True, style="dim", min_width=8, max_width=26 if verbose else 16)
    table.add_column("Action", style="italic", min_width=20, ratio=2, overflow="fold")

    for s in sorted_statuses:
        provider = _cli()._status_provider(s, providers_by_label)
        account = accounts_by_label.get(s.label)
        cache_entry = cache_entries.get(account_key_string(account)) if account is not None else None
        refresh_failed = bool(cache_entry and cache_entry.get("refresh_error"))
        claude_cached_refresh_unavailable = bool(
            refresh_failed and account is not None and account.provider == "claude"
        )
        blocking_refresh_failed = _refresh_failure_blocks_auto_kicks(account, s, cache_entry)
        fallback_source = _cli()._auto_kick_blocked_by_codexbar_fallback(account, s)
        phantom_session_display = _status_phantom_session_display(
            account,
            s,
            _cli()._status_provider(s, providers_by_label),
            history,
        )
        codex_unconfirmed_session = _status_codex_unconfirmed_session_display(
            account,
            s,
            history,
        )
        state_display = _status_state_display(
            s,
            provider=provider,
            stale=s.stale or blocking_refresh_failed,
            indirect=fallback_source,
            phantom_session=phantom_session_display,
            codex_unconfirmed_session=codex_unconfirmed_session,
            cached_refresh_unavailable=claude_cached_refresh_unavailable,
        )
        row = [
            s.label,
            state_display,
            _format_weekly_reset(s.resets_in_seconds),
            _format_used_labeled_cell(s, provider),
        ]
        if show_queued_column:
            row.append(queued_by_label.get(s.label, "—"))
        row.append(
            _status_table_action(
                s,
                providers_by_label,
                account,
                refresh_failed=refresh_failed,
            )
        )
        table.add_row(*row)
        session_row = [
            "",
            "",
            _format_session_reset_for_status(
                s,
                phantom_session=phantom_session_display,
                codex_unconfirmed_session=codex_unconfirmed_session,
            ),
            _format_used_labeled_cell(s, provider, session=True),
        ]
        if show_queued_column:
            session_row.append("")
        session_row.append("")
        table.add_row(*session_row)
        if verbose and provider == "antigravity" and isinstance(s.quota_windows, list):
            for window in s.quota_windows:
                if not isinstance(window, dict):
                    continue
                quota_row = [
                    "",
                    _format_antigravity_quota_label(window),
                    _format_antigravity_quota_reset(window),
                    _format_used_percent(_numeric_float(window.get("used_percent"))),
                ]
                if show_queued_column:
                    quota_row.append("")
                quota_row.append("")
                table.add_row(*quota_row)

    _cli().console.print(table)

    fresh = []
    for s in sorted_statuses:
        account = accounts_by_label.get(s.label)
        cache_entry = (
            cache_entries.get(account_key_string(account)) if account is not None else None
        )
        if cache_entry and cache_entry.get("refresh_error"):
            continue
        if _cli()._status_actionable_now(
            account,
            s,
            _cli()._status_provider(s, providers_by_label),
            history=history,
            pending_kick=pending.get(s.label),
        ):
            fresh.append(s)
    if fresh:
        labels = ", ".join(s.label for s in fresh)
        prompt_label = (
            "Weekly ready windows"
            if all(_cli()._long_kick_eligible(s) for s in fresh)
            else "Kick-ready windows"
        )
        _cli().console.print(f"\n[green bold]→ {prompt_label}:[/green bold] {labels}")
        auto_enabled = [
            s
            for s in fresh
            if (account := accounts_by_label.get(s.label)) is not None
            and _cli()._status_auto_enabled_for_action(
                account,
                s,
                _cli()._status_provider(s, providers_by_label),
            )
        ]
        if len(auto_enabled) == len(fresh):
            all_fresh_long = all(_cli()._long_kick_eligible(s) for s in fresh)
            action = "anchor them now" if all_fresh_long else "kick them now"
            _cli().console.print(f"[dim]  Run [bold]tk kick --all[/bold] to {action}.[/dim]")
        elif len(fresh) == 1:
            label = fresh[0].label
            account = accounts_by_label.get(label)
            provider = _cli()._status_provider(fresh[0], providers_by_label)
            if _cli()._session_kick_eligible(account, fresh[0], provider):
                auto_command = (
                    "auto session enable" if account is not None and account.auto_kick else "auto enable"
                )
            elif account is not None and account.auto_kick and not account.weekly_auto_kick:
                auto_command = "auto weekly enable"
            else:
                auto_command = "auto enable"
            _cli().console.print(
                f'  Run [bold]tk kick "{label}"[/bold] now, or '
                f'[bold]tk {auto_command} "{label}"[/bold] for automatic kicks.'
            )
        else:
            _cli().console.print(
                "  Run [bold]tk kick \"<label>\"[/bold] for one account, or enable "
                "auto-kick before using [bold]tk kick --all[/bold]."
            )
    stale_statuses = [s for s in sorted_statuses if s.stale]
    if stale_statuses:
        stale_labels = ", ".join(s.label for s in stale_statuses)
        _cli().console.print(
            f"\n[yellow]Stale CodexBar data blocks automatic kicks:[/yellow] {stale_labels}"
        )
    refresh_failed_statuses = [
        s
        for s in sorted_statuses
        if (account := accounts_by_label.get(s.label)) is not None
        and (cache_entry := cache_entries.get(account_key_string(account))) is not None
        and _refresh_failure_blocks_auto_kicks(account, s, cache_entry)
    ]
    if refresh_failed_statuses:
        failed_labels = ", ".join(s.label for s in refresh_failed_statuses)
        _cli().console.print(
            "\n[yellow]Refresh failed; automatic kicks are blocked until fresh data "
            "is available:[/yellow] "
            f"{failed_labels}"
        )
        for status in refresh_failed_statuses:
            account = accounts_by_label.get(status.label)
            cache_entry = (
                cache_entries.get(account_key_string(account)) if account is not None else None
            )
            hint = (
                codex_refresh_recovery_hint(account, cache_entry)
                if account is not None
                else None
            )
            if hint is None:
                continue
            _cli().console.print(
                f"[yellow]Recovery hint:[/yellow] {hint.label} has not refreshed for "
                f"{hint.age_text}."
            )
            _cli().console.print(f"  1. Check the Codex home: [bold]{hint.codex_command}[/bold]")
            _cli().console.print(
                "  2. If Codex opens but TokenKick still cannot refresh, run a "
                f"one-time recovery kick: [bold]{hint.force_kick_command}[/bold]"
            )
            _cli().console.print(f"  3. Then refresh: [bold]{hint.refresh_command}[/bold]")
            _cli().console.print(
                "[dim]  The recovery kick consumes a small amount of Codex usage; "
                "TokenKick will not run it automatically.[/dim]"
            )
    codexbar_fallback_statuses = [
        s
        for s in sorted_statuses
        if _cli()._auto_kick_blocked_by_codexbar_fallback(accounts_by_label.get(s.label), s)
    ]
    if codexbar_fallback_statuses:
        fallback_labels = ", ".join(s.label for s in codexbar_fallback_statuses)
        _cli().console.print(
            "\n[dim]* indirect Codex data via CodexBar fallback; automatic kicks are "
            f"blocked until direct provider usage is available:[/dim] {fallback_labels}"
        )
    _surface_dormant_account_hints(sorted_statuses, accounts_by_label, config)
    codexbar_errors = [
        (s.label, s.error)
        for s in sorted_statuses
        if s.error and _is_user_facing_codexbar_error(s.error)
    ]
    if codexbar_errors:
        _cli().console.print("\n[yellow]CodexBar diagnostics:[/yellow]")
        for label, error in codexbar_errors:
            _cli().console.print(f"[yellow]- {label}: {error}[/yellow]")


def _surface_dormant_account_hints(
    statuses: list[AccountStatus],
    accounts_by_label: dict[str, AccountConfig],
    config: Config | None,
) -> None:
    if config is None:
        return
    state = _read_dormant_hint_state()
    changed = False
    for status in statuses:
        account = accounts_by_label.get(status.label)
        if account is None:
            continue
        key = account_key_string(account)
        previous = state.get(key)
        if status.state == AccountState.FRESH and status.stale:
            if not (isinstance(previous, dict) and previous.get("hinted")):
                _cli().console.print(
                    f'[yellow]Account {status.label} is dormant. Run `tk wake "{status.label}"` '
                    "to bootstrap it.[/yellow]"
                )
                _cli().notify_dormant_account_for_account(account, config)
                state[key] = {
                    "label": status.label,
                    "state": status.state.value,
                    "stale": True,
                    "hinted": True,
                }
                changed = True
            continue
        if previous is not None:
            state.pop(key, None)
            changed = True
    if changed:
        try:
            _write_dormant_hint_state(state)
        except OSError:
            return


def _sort_statuses(
    statuses: list[AccountStatus],
    accounts: list[AccountConfig] | None = None,
) -> list[AccountStatus]:
    accounts_by_label = {account.label: account for account in accounts or []}
    providers_by_label = {account.label: account.provider for account in accounts or []}
    return sorted(
        statuses,
        key=lambda status: _status_sort_key(
            status,
            providers_by_label,
            accounts_by_label.get(status.label),
        ),
    )


def _filter_status_pairs_by_provider(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    provider: str | None,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    if provider is None or not accounts:
        return accounts, statuses

    filtered_pairs = [
        (account, status)
        for account, status in zip(accounts, statuses, strict=False)
        if account.provider == provider
    ]
    return (
        [account for account, _status in filtered_pairs],
        [status for _account, status in filtered_pairs],
    )


def _status_sort_key(
    status: AccountStatus,
    providers_by_label: dict[str, str],
    account: AccountConfig | None = None,
) -> tuple[int, int, float, str]:
    provider = _cli()._status_provider(status, providers_by_label)
    return (
        _status_action_sort_bucket(status, provider),
        _status_family_sort_bucket(status, provider, account),
        _status_session_sort_value(status),
        status.label,
    )


def _status_action_sort_bucket(status: AccountStatus, provider: str) -> int:
    if provider in KICKABLE_PROVIDERS and (
        status.state == AccountState.FRESH
        or _cli()._long_kick_eligible(status)
        or _status_has_unanchored_session_artifact(status)
    ):
        return 0
    if status.state == AccountState.UNKNOWN or status.stale:
        return 2
    return 1


def _status_family_sort_bucket(
    status: AccountStatus,
    provider: str,
    account: AccountConfig | None,
) -> int:
    if provider == "claude":
        return 0
    if provider == "codex":
        if _status_is_codex_spark(status, account):
            return 2
        return 1
    if provider == "gemini":
        return 3
    if provider == "openrouter":
        return 4
    return 5


def _status_is_codex_spark(status: AccountStatus, account: AccountConfig | None) -> bool:
    if account is not None and account.codex_rate_limit_id not in {None, "codex"}:
        return True
    return status.label.startswith("codex-spark ")


def _status_session_sort_value(status: AccountStatus) -> float:
    if status.session_resets_in_seconds is not None:
        return max(0.0, float(status.session_resets_in_seconds))
    if status.resets_in_seconds is not None:
        return max(0.0, float(status.resets_in_seconds))
    return float("inf")


def _status_action(
    status: AccountStatus,
    providers_by_label: dict[str, str] | None = None,
    account: AccountConfig | None = None,
) -> str:
    provider = _cli()._status_provider(status, providers_by_label)
    if _status_weekly_exhausted(status):
        return "Weekly exhausted"
    if _status_session_exhausted(status):
        return "Wait for session"
    if status.stale:
        return "Stale data - blocked"
    if _cli()._auto_kick_blocked_by_codexbar_fallback(account, status):
        return "CodexBar fallback"
    history = _cli().load_kick_history(limit=200)
    confirmed_phantom_session = (
        account is not None
        and _cli()._phantom_session_ready(account, status, record_observation=False)
    )
    if account is not None and (
        confirmed_phantom_session or _cli()._is_phantom_session_candidate(status)
    ):
        recovery_action = _cli()._phantom_recovery_status_action(account)
        if recovery_action is not None:
            return recovery_action
    if _cli()._codex_unconfirmed_current_session_candidate(account, status, history) is not None:
        return "Confirming session"
    if (
        account is not None
        and _cli()._is_phantom_session_candidate(status)
        and _cli()._was_kicked_in_current_session_window(account, status, history)
    ):
        if _cli()._phantom_session_ready(account, status, record_observation=False):
            return "Provider unchanged"
        return "Session anchored"
    eligibility = _cli()._kick_eligibility(account, status, provider, history=history)
    if eligibility.reason == "phantom_unresolved":
        return "Phantom unresolved"
    if eligibility.reason == "already_session_kicked":
        return "Session anchored"
    if eligibility.reason == "already_kicked":
        return "Already kicked"
    if eligibility.reason == "phantom_backoff" and account is not None:
        backoff_until = _cli()._ambiguous_phantom_kick_backoff_until(account, history)
        if backoff_until is not None:
            retry_at = datetime.fromtimestamp(backoff_until, timezone.utc).astimezone()
            return f"Retry after {retry_at.strftime('%H:%M %Z')}"
    if eligibility.reason == "provider_unchanged_backoff" and account is not None:
        backoff_until = _cli()._provider_unchanged_phantom_kick_backoff_until(
            account,
            status,
            history,
        )
        if backoff_until is not None:
            retry_at = datetime.fromtimestamp(backoff_until, timezone.utc).astimezone()
            return f"Retry after {retry_at.strftime('%H:%M %Z')}"
    if eligibility.reason == "provider_unchanged":
        return "Provider unchanged"
    if eligibility.reason == "weekly_exhausted":
        return "Weekly exhausted"
    if eligibility.reason == "session_exhausted":
        return "Wait for session"
    if eligibility.cooldown_remaining is not None:
        return "Session cooling down"
    if eligibility.kickable and eligibility.kick_type == "kick":
        return "Kick now"
    if confirmed_phantom_session and status.state != AccountState.FRESH:
        return "Kick session"
    if eligibility.kickable and eligibility.kick_type == "session":
        return "Kick session"
    if eligibility.kickable:
        return status.state.action
    if _cli()._is_monitor_only_provider(provider) and status.state != AccountState.UNKNOWN:
        return "Monitor only"
    if (
        status.state == AccountState.FRESH
        and provider not in KICKABLE_PROVIDERS
    ):
        return "Monitor only"
    if status.state == AccountState.UNKNOWN and status.error:
        if status.error.startswith("No session data"):
            return "No session data"
        return status.error[:24]
    return status.state.action


def _status_table_action(
    status: AccountStatus,
    providers_by_label: dict[str, str] | None,
    account: AccountConfig | None,
    *,
    refresh_failed: bool,
) -> str:
    if _status_weekly_exhausted(status):
        return "Weekly exhausted"
    if _status_session_exhausted(status):
        return "Wait for session"
    if refresh_failed:
        if account is not None and account.provider == "claude":
            return "Cached Claude status"
        if account is not None and _cli()._is_monitor_only_provider(account.provider):
            return "Monitor stale"
        return "Refresh failed"
    return _status_action(status, providers_by_label, account)


def _refresh_failure_blocks_auto_kicks(
    account: AccountConfig | None,
    status: AccountStatus,
    cache_entry: dict | None,
) -> bool:
    return (
        account is not None
        and account.provider in KICKABLE_PROVIDERS
        and account.provider != "claude"
        and cache_entry is not None
        and bool(cache_entry.get("refresh_error"))
        and not _status_weekly_exhausted(status)
        and not _status_session_exhausted(status)
    )


def _status_weekly_exhausted(status: AccountStatus) -> bool:
    return weekly_quota_exhausted(status)


def _status_session_exhausted(status: AccountStatus) -> bool:
    return (
        not _status_weekly_exhausted(status)
        and status.state != AccountState.FRESH
        and status.session_used_percent is not None
        and status.session_used_percent >= 100.0
        and status.session_resets_in_seconds is not None
        and status.session_resets_in_seconds > 0
    )


def _status_session_cooldown_remaining(status: AccountStatus) -> int | None:
    if status.window_anchor_state == "available_unanchored":
        return None
    if status.session_resets_in_seconds is None or status.session_resets_in_seconds <= 0:
        return None
    if status.session_used_percent == 0.0:
        return None
    return status.session_resets_in_seconds


def _status_phantom_session_display(
    account: AccountConfig | None,
    status: AccountStatus,
    provider: str,
    history,
) -> bool:
    if account is None or not _status_has_session_artifact(status):
        return False
    if (
        _cli()._is_phantom_session_candidate(status)
        and _cli()._phantom_recovery_state_for(account) is not None
    ):
        return True
    if _cli()._phantom_session_ready(account, status, record_observation=False):
        return True
    eligibility = _cli()._kick_eligibility(account, status, provider, history=history)
    return eligibility.reason in {
        "phantom_backoff",
        "phantom_unresolved",
        "provider_unchanged",
        "provider_unchanged_backoff",
    }


def _status_codex_unconfirmed_session_display(
    account: AccountConfig | None,
    status: AccountStatus,
    history,
) -> bool:
    return _cli()._codex_unconfirmed_current_session_candidate(account, status, history) is not None


def _status_has_session_artifact(status: AccountStatus) -> bool:
    return (
        status.state in {AccountState.FRESH, AccountState.ACTIVE}
        and status.session_used_percent is not None
        and status.session_used_percent > 0.0
        and status.session_resets_in_seconds is not None
        and status.session_resets_in_seconds > 0
    )


def _status_has_unanchored_session_artifact(status: AccountStatus) -> bool:
    session_window = status.session_window_minutes or 0
    session_resets = status.session_resets_in_seconds or 0
    return (
        status.state == AccountState.ACTIVE
        and session_window == 300
        and status.session_used_percent == 0.0
        and session_resets >= int(session_window * 60 * _cli().PHANTOM_SESSION_FULL_RESET_RATIO)
    )


def _is_user_facing_codexbar_error(error: str) -> bool:
    return (
        error.startswith("CodexBar not installed.")
        or error.startswith("CodexBar not running.")
        or error.startswith("CodexBar snapshot is stale beyond the configured threshold.")
        or error.startswith("CodexBar data schema version mismatch:")
    )


def _status_state_display(
    status: AccountStatus,
    *,
    provider: str | None = None,
    stale: bool = False,
    indirect: bool = False,
    phantom_session: bool = False,
    codex_unconfirmed_session: bool = False,
    cached_refresh_unavailable: bool = False,
) -> str:
    if _status_weekly_exhausted(status):
        display = "🔴 Weekly exhausted"
        return f"{display}*" if indirect else display
    if _status_session_exhausted(status):
        display = "🟠 Session exhausted"
        return f"{display}*" if indirect else display
    if stale and status.state == AccountState.FRESH:
        display = "🟡 Weekly ready (stale)"
        return f"{display}*" if indirect else display
    if status.state == AccountState.FRESH:
        if status.window_anchor_state == "available_unanchored":
            display = "🟢 Weekly ready"
            return f"{display}*" if indirect else display
        if phantom_session and _status_has_session_artifact(status):
            display = "🟡 Phantom session"
            return f"{display}*" if indirect else display
        if codex_unconfirmed_session:
            display = "🟡 Codex unconfirmed"
            return f"{display}*" if indirect else display
        display = "🟢 Weekly ready"
        return f"{display}*" if indirect else display
    if _cli()._long_kick_eligible(status):
        display = "🟢 Weekly ready"
        if stale:
            display = f"{display} ⚠️"
        elif cached_refresh_unavailable:
            display = f"{display} ⏱"
        return f"{display}*" if indirect else display
    display = f"{status.state.emoji} {status.state.value.capitalize()}"
    if phantom_session and _status_has_session_artifact(status):
        display = "🟡 Phantom session"
    elif codex_unconfirmed_session:
        display = "🟡 Codex unconfirmed"
    elif (
        (provider is None or provider in KICKABLE_PROVIDERS)
        and provider != "claude"
        and _status_has_unanchored_session_artifact(status)
    ):
        display = "🟢 Session ready"
    if stale:
        display = f"{display} ⚠️"
    elif cached_refresh_unavailable:
        display = f"{display} ⏱"
    return f"{display}*" if indirect else display


def _stale_status_reason(status: AccountStatus) -> str:
    source = status.source_detail or "provider data"
    if status.stale_seconds is None:
        return f"{source} is stale; automatic kick is blocked until fresh data is available."
    return (
        f"{source} is stale ({_format_duration(status.stale_seconds)} old); "
        "automatic kick is blocked until fresh data is available."
    )
