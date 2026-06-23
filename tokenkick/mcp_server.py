"""TokenKick MCP stdio server."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from .mcp_core import TokenKickMCPCore

mcp = FastMCP("TokenKick")
_core = TokenKickMCPCore()


@mcp.tool()
def tokenkick_snapshot(include_paths: bool = False) -> dict:
    """Return a TokenKick app snapshot. Risk: diagnostic_read; paths redacted by default."""
    return _core.tokenkick_snapshot(include_paths=include_paths)


@mcp.tool()
def tokenkick_status(
    account: str | None = None,
    codex: bool = False,
    all_accounts: bool = False,
) -> dict:
    """Return cached TokenKick status. Risk: cached_read."""
    return _core.tokenkick_status(account=account, codex=codex, all_accounts=all_accounts)


@mcp.tool()
def tokenkick_refresh_status(
    account: str | None = None,
    codex: bool = False,
    all_accounts: bool = False,
) -> dict:
    """Fetch live provider status. Risk: live_provider_read."""
    return _core.tokenkick_refresh_status(account=account, codex=codex, all_accounts=all_accounts)


@mcp.tool()
def tokenkick_doctor(label: str | None = None, include_paths: bool = False) -> dict:
    """Run read-only diagnostics. Risk: diagnostic_read; paths redacted unless include_paths=true."""
    return _core.tokenkick_doctor(label=label, include_paths=include_paths)


@mcp.tool()
def tokenkick_accounts(view: Literal["list", "planning", "notifications"] = "list") -> dict:
    """Read account status/planning/notification views. Risk: cached_read."""
    return _core.tokenkick_accounts(view=view)


@mcp.tool()
def tokenkick_calendar(
    account: str | None = None,
    codex: bool = False,
    all_accounts: bool = False,
    days: int | None = None,
) -> dict:
    """Read reset calendar from cached state. Risk: cached_read."""
    return _core.tokenkick_calendar(
        account=account,
        codex=codex,
        all_accounts=all_accounts,
        days=days,
    )


@mcp.tool()
def tokenkick_schedule_show(account: str | None = None) -> dict:
    """Read smart schedule and pending kicks. Risk: cached_read."""
    return _core.tokenkick_schedule_show(account=account)


@mcp.tool()
def tokenkick_history(
    account: str | None = None,
    anchored: bool = False,
    limit: int | None = None,
    include_details: bool = False,
    verbose: bool = False,
) -> dict:
    """Read recent kick history. Risk: cached_read; full text details require include_details=true."""
    return _core.tokenkick_history(
        account=account,
        anchored=anchored,
        limit=limit,
        include_details=include_details,
        verbose=verbose,
    )


@mcp.tool()
def tokenkick_reset_log(
    detail_id: str | None = None,
    provider: str | None = None,
    unacknowledged: bool = False,
) -> dict:
    """Read reset log events. Risk: cached_read."""
    return _core.tokenkick_reset_log(
        detail_id=detail_id,
        provider=provider,
        unacknowledged=unacknowledged,
    )


@mcp.tool()
def tokenkick_daemon_status(include_paths: bool = False) -> dict:
    """Read daemon status. Risk: cached_read; executable/log/pid paths redacted by default."""
    return _core.tokenkick_daemon_status(include_paths=include_paths)


@mcp.tool()
def tokenkick_codex_strategy_status() -> dict:
    """Read Codex strategy status. Risk: cached_read."""
    return _core.tokenkick_codex_strategy_status()


@mcp.tool()
def tokenkick_codex_surfaces(label: str) -> dict:
    """Read Codex surface stats for one account. Risk: cached_read."""
    return _core.tokenkick_codex_surfaces(label=label)


@mcp.tool()
def tokenkick_plan_preview(
    work_window: str,
    date: str | None = None,
    timezone: str | None = None,
    usage: list[str] | None = None,
) -> dict:
    """Preview orchestration plan and return a token for exact matching apply.

    usage format: list of '<account label>=<duration>' strings, e.g.
    ['codex (personal)=150m', 'claude (work)=2h']. Durations accept
    180, 180m, 3h, 2.5h, or 1h30m.
    """
    return _core.tokenkick_plan_preview(
        work_window=work_window,
        date=date,
        timezone=timezone,
        usage=usage,
    )


@mcp.tool()
def tokenkick_plan_apply(
    work_window: str,
    preview_token: str,
    confirm: bool,
    date: str | None = None,
    timezone: str | None = None,
    usage: list[str] | None = None,
) -> dict:
    """Apply a previewed orchestration plan. Risk: low_risk_mutation.

    usage must exactly match the preview arguments. Format examples:
    ['codex (personal)=150m', 'claude (work)=2h'].
    """
    return _core.tokenkick_plan_apply(
        work_window=work_window,
        date=date,
        timezone=timezone,
        usage=usage,
        preview_token=preview_token,
        confirm=confirm,
    )


@mcp.tool()
def tokenkick_plan_cancel_preview(accounts: list[str] | None = None) -> dict:
    """Preview orchestration cancellation and return a token."""
    return _core.tokenkick_plan_cancel_preview(accounts=accounts)


@mcp.tool()
def tokenkick_plan_cancel(
    preview_token: str,
    confirm: bool,
    accounts: list[str] | None = None,
) -> dict:
    """Cancel applied orchestration pending kicks. Risk: low_risk_mutation."""
    return _core.tokenkick_plan_cancel(
        accounts=accounts,
        preview_token=preview_token,
        confirm=confirm,
    )


@mcp.tool()
def tokenkick_schedule_set_preview(
    account: str | None = None,
    weekdays: str | None = None,
    weekends: str | None = None,
    timezone: str | None = None,
    default: bool = False,
) -> dict:
    """Preview smart schedule set and return a token."""
    return _core.tokenkick_schedule_set_preview(
        account=account,
        weekdays=weekdays,
        weekends=weekends,
        timezone=timezone,
        default=default,
    )


@mcp.tool()
def tokenkick_schedule_set(
    preview_token: str,
    confirm: bool,
    account: str | None = None,
    weekdays: str | None = None,
    weekends: str | None = None,
    timezone: str | None = None,
    default: bool = False,
) -> dict:
    """Set smart schedule after preview. Risk: low_risk_mutation."""
    return _core.tokenkick_schedule_set(
        account=account,
        weekdays=weekdays,
        weekends=weekends,
        timezone=timezone,
        default=default,
        preview_token=preview_token,
        confirm=confirm,
    )


@mcp.tool()
def tokenkick_schedule_clear_or_disable_preview(
    action: Literal["clear", "disable"],
    account: str | None = None,
    default: bool = False,
) -> dict:
    """Preview smart schedule clear/disable and return a token."""
    return _core.tokenkick_schedule_clear_or_disable_preview(
        action=action,
        account=account,
        default=default,
    )


@mcp.tool()
def tokenkick_schedule_clear_or_disable(
    action: Literal["clear", "disable"],
    preview_token: str,
    confirm: bool,
    account: str | None = None,
    default: bool = False,
) -> dict:
    """Clear or disable smart schedule after preview. Risk: low_risk_mutation."""
    return _core.tokenkick_schedule_clear_or_disable(
        action=action,
        account=account,
        default=default,
        preview_token=preview_token,
        confirm=confirm,
    )


@mcp.tool()
def tokenkick_daemon_control_preview(action: Literal["background", "stop", "restart"]) -> dict:
    """Preview daemon start/stop/restart and return a token."""
    return _core.tokenkick_daemon_control_preview(action=action)


@mcp.tool()
def tokenkick_daemon_control(
    action: Literal["background", "stop", "restart"],
    preview_token: str,
    confirm: bool,
) -> dict:
    """Start, stop, or restart daemon after preview. Risk: low_risk_mutation."""
    return _core.tokenkick_daemon_control(
        action=action,
        preview_token=preview_token,
        confirm=confirm,
    )


@mcp.tool()
def tokenkick_run_dry_run(codex: bool = False) -> dict:
    """Preview tk run. Performs live provider refresh and returns token for run_apply."""
    return _core.tokenkick_run_dry_run(codex=codex)


@mcp.tool()
def tokenkick_run_apply(
    preview_token: str,
    confirm: bool,
    quota_ack: bool,
    codex: bool = False,
) -> dict:
    """Execute tk run. Risk: dangerous_quota_operational."""
    return _core.tokenkick_run_apply(
        preview_token=preview_token,
        confirm=confirm,
        quota_ack=quota_ack,
        codex=codex,
    )


@mcp.tool()
def tokenkick_kick_preview(label: str) -> dict:
    """Preview one account kick and return token. Risk: cached_read."""
    return _core.tokenkick_kick_preview(label=label)


@mcp.tool()
def tokenkick_kick_account(
    label: str,
    preview_token: str,
    confirm: bool,
    quota_ack: bool,
) -> dict:
    """Kick one account after preview. Risk: quota_consuming."""
    return _core.tokenkick_kick_account(
        label=label,
        preview_token=preview_token,
        confirm=confirm,
        quota_ack=quota_ack,
    )


@mcp.tool()
def tokenkick_force_recovery_preview(label: str) -> dict:
    """Preview force recovery kick and return token. Risk: dangerous_recovery."""
    return _core.tokenkick_force_recovery_preview(label=label)


@mcp.tool()
def tokenkick_force_recovery_kick(
    label: str,
    preview_token: str,
    confirm: bool,
    quota_ack: bool,
    force_ack: bool,
) -> dict:
    """Force recovery kick after preview. Risk: dangerous_recovery."""
    return _core.tokenkick_force_recovery_kick(
        label=label,
        preview_token=preview_token,
        confirm=confirm,
        quota_ack=quota_ack,
        force_ack=force_ack,
    )


@mcp.tool()
def tokenkick_recovery_hint(label: str) -> dict:
    """Read stale Codex refresh recovery guidance. Risk: diagnostic_read."""
    return _core.tokenkick_recovery_hint(label=label)


@mcp.resource("tokenkick://snapshot")
def resource_snapshot() -> str:
    return _json_text(_core.tokenkick_snapshot())


@mcp.resource("tokenkick://status")
def resource_status() -> str:
    return _json_text(_core.tokenkick_status())


@mcp.resource("tokenkick://accounts")
def resource_accounts() -> str:
    return _json_text(_core.tokenkick_accounts())


@mcp.resource("tokenkick://calendar")
def resource_calendar() -> str:
    return _json_text(_core.tokenkick_calendar())


@mcp.resource("tokenkick://pending-kicks")
def resource_pending_kicks() -> str:
    snapshot = _core.tokenkick_snapshot()
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else {}
    return _json_text((payload or {}).get("pending_kicks", []))


@mcp.resource("tokenkick://doctor")
def resource_doctor() -> str:
    return _json_text(_core.tokenkick_doctor())


@mcp.resource("tokenkick://history/recent")
def resource_history_recent() -> str:
    return _json_text(_core.tokenkick_history(limit=50))


@mcp.resource("tokenkick://agent-playbook")
def resource_agent_playbook() -> str:
    return _core.resource_agent_playbook()


@mcp.resource("tokenkick://commands")
def resource_commands() -> str:
    return _core.resource_commands()


@mcp.prompt()
def plan_coding_session() -> str:
    return _core.prompt_plan_coding_session()


@mcp.prompt()
def check_tokenkick_health() -> str:
    return _core.prompt_check_health()


@mcp.prompt()
def recover_stale_codex_account() -> str:
    return _core.prompt_recover_stale_codex()


@mcp.prompt()
def explain_pending_kicks() -> str:
    return _core.prompt_explain_pending_kicks()


@mcp.prompt()
def prepare_accounts_for_tonight() -> str:
    return _core.prompt_prepare_accounts_for_tonight()


@mcp.prompt()
def audit_daemon_and_schedule() -> str:
    return _core.prompt_audit_daemon_and_schedule()


def _json_text(value: object) -> str:
    import json

    return json.dumps(value, indent=2, default=str)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
