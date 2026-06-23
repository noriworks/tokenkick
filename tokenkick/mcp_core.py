"""Safety-first TokenKick MCP tool core.

This module intentionally wraps the public ``tk`` JSON/app-mode command surface
instead of importing TokenKick internals that mutate state directly.  The MCP
transport adapter in ``tokenkick.mcp_server`` is deliberately thin; validation,
preview-token gates, command construction, subprocess execution, JSON
normalization, and redaction live here so they can be tested with a fake ``tk``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Literal

RiskLevel = Literal[
    "cached_read",
    "diagnostic_read",
    "live_provider_read",
    "low_risk_mutation",
    "quota_consuming",
    "dangerous_quota_operational",
    "dangerous_recovery",
]

TOKEN_TTL_SECONDS = 10 * 60
DEFAULT_TIMEOUT_SECONDS = 20.0
LIVE_PROVIDER_TIMEOUT_SECONDS = 90.0
MUTATION_TIMEOUT_SECONDS = 60.0
QUOTA_TIMEOUT_SECONDS = 180.0

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_WORK_WINDOW_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOKEN_RE = re.compile(r"(?i)(token|secret|authorization|api[_-]?key)=([^ \n\t]+)")
_BEARER_RE = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_TEMP_PATH_RE = re.compile(r"(/(?:var/folders|tmp|private/tmp)/[^ \n\t]+)")
_LOCAL_PATH_RE = re.compile(r"^(?:/|~)")
_PATH_KEY_PARTS = (
    "path",
    "file",
    "dir",
    "home",
    "executable",
    "pidfile",
)
_HISTORY_DETAIL_FIELDS = {
    "prompt_text",
    "response_text",
    "provider_output_excerpt",
    "provider_output",
    "stdout",
    "stderr",
    "raw_output",
}


@dataclass(frozen=True)
class PreviewToken:
    token: str
    tool_name: str
    argv_hash: str
    risk: RiskLevel
    created_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "tool_name": self.tool_name,
            "risk": self.risk,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "expires_in_seconds": max(0, int(self.expires_at - time.time())),
        }


class PreviewTokenStore:
    def __init__(self, *, ttl_seconds: int = TOKEN_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, PreviewToken] = {}

    def create(self, *, tool_name: str, argv: list[str], risk: RiskLevel) -> PreviewToken:
        self._prune()
        now = time.time()
        token = secrets.token_urlsafe(32)
        preview = PreviewToken(
            token=token,
            tool_name=tool_name,
            argv_hash=_argv_hash(argv),
            risk=risk,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._tokens[token] = preview
        return preview

    def consume(self, token: str, *, tool_name: str, argv: list[str], risk: RiskLevel) -> None:
        self._prune()
        preview = self._tokens.pop(token, None)
        if preview is None:
            raise ValueError("preview_token is missing, expired, invalid, or already used")
        if preview.tool_name != tool_name:
            raise ValueError("preview_token was created for a different tool")
        if preview.risk != risk:
            raise ValueError("preview_token was created for a different risk level")
        if preview.argv_hash != _argv_hash(argv):
            raise ValueError("preview_token does not match the requested command arguments")

    def _prune(self) -> None:
        now = time.time()
        expired = [token for token, preview in self._tokens.items() if preview.expires_at <= now]
        for token in expired:
            self._tokens.pop(token, None)


class TokenKickMCPCore:
    def __init__(
        self,
        *,
        tk_path: str | None = None,
        token_store: PreviewTokenStore | None = None,
    ) -> None:
        self.tk_path = tk_path or os.environ.get("TOKENKICK_TK_PATH") or shutil.which("tk") or "tk"
        self.token_store = token_store or PreviewTokenStore()

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    def tokenkick_snapshot(self, *, include_paths: bool = False) -> dict[str, Any]:
        result = self._run(
            ["app", "snapshot"],
            risk="diagnostic_read",
            app_mode=True,
            may_read_environment=True,
            include_paths=include_paths,
        )
        if result.get("ok") is True and not include_paths:
            result["payload"] = _redact_path_sensitive_payload(result.get("payload"))
            result.setdefault("warnings", []).append(
                "Local runtime paths are redacted by default; call tokenkick_snapshot(include_paths=true) "
                "when path diagnostics are required."
            )
        return result

    def tokenkick_status(
        self,
        *,
        account: str | None = None,
        codex: bool = False,
        all_accounts: bool = False,
    ) -> dict[str, Any]:
        argv = ["status", "--json-output"]
        if codex:
            argv.append("--codex")
        if all_accounts:
            argv.append("--all")
        if account:
            argv.extend(["--account", _safe_label(account)])
        return self._run(argv, risk="cached_read")

    def tokenkick_refresh_status(
        self,
        *,
        account: str | None = None,
        codex: bool = False,
        all_accounts: bool = False,
    ) -> dict[str, Any]:
        argv = ["status", "--refresh", "--json-output"]
        if codex:
            argv.append("--codex")
        if all_accounts:
            argv.append("--all")
        if account:
            argv.extend(["--account", _safe_label(account)])
        return self._run(argv, risk="live_provider_read", provider_refresh=True)

    def tokenkick_doctor(self, *, label: str | None = None, include_paths: bool = False) -> dict[str, Any]:
        if label is None:
            result = self._run(
                ["app", "doctor"],
                risk="diagnostic_read",
                app_mode=True,
                may_read_environment=True,
                provider_refresh=False,
                include_paths=include_paths,
            )
            return _redact_paths_by_default(result, include_paths=include_paths)
        argv = ["doctor", "--json-output"]
        argv.append(_safe_label(label))
        result = self._run(
            argv,
            risk="diagnostic_read",
            may_read_environment=True,
            provider_refresh=False,
            include_paths=include_paths,
        )
        return _redact_paths_by_default(result, include_paths=include_paths)

    def tokenkick_accounts(
        self,
        *,
        view: Literal["list", "planning", "notifications"] = "list",
    ) -> dict[str, Any]:
        _require_choice("view", view, {"list", "planning", "notifications"})
        return self._run(["accounts", view, "--json-output"], risk="cached_read", app_mode=True)

    def tokenkick_calendar(
        self,
        *,
        account: str | None = None,
        codex: bool = False,
        all_accounts: bool = False,
        days: int | None = None,
    ) -> dict[str, Any]:
        argv = ["calendar", "--json-output"]
        if account:
            argv.extend(["--account", _safe_label(account)])
        if codex:
            argv.append("--codex")
        if all_accounts:
            argv.append("--all")
        if days is not None:
            if days < 1 or days > 60:
                raise ValueError("days must be 1..60")
            argv.extend(["--days", str(days)])
        return self._run(argv, risk="cached_read")

    def tokenkick_schedule_show(self, *, account: str | None = None) -> dict[str, Any]:
        argv = ["schedule", "show", "--json-output"]
        if account:
            argv.extend(["--account", _safe_label(account)])
        return self._run(argv, risk="cached_read", app_mode=True)

    def tokenkick_history(
        self,
        *,
        account: str | None = None,
        anchored: bool = False,
        limit: int | None = None,
        include_details: bool = False,
        verbose: bool = False,
    ) -> dict[str, Any]:
        argv = ["history", "--json-output"]
        if account:
            argv.extend(["--account", _safe_label(account)])
        if anchored:
            argv.append("--anchored")
        if limit is not None:
            if limit < 1 or limit > 500:
                raise ValueError("limit must be 1..500")
            argv.extend(["--limit", str(limit)])
        if include_details or verbose:
            argv.append("--verbose")
        result = self._run(argv, risk="cached_read")
        if result.get("ok") is True:
            result["payload"] = _redact_history_payload(
                result.get("payload"),
                include_details=include_details or verbose,
            )
            if not (include_details or verbose):
                result.setdefault("warnings", []).append(
                    "History details are summarized by default; call "
                    "tokenkick_history(include_details=true) for full audit fields."
                )
        return result

    def tokenkick_reset_log(
        self,
        *,
        detail_id: str | None = None,
        provider: str | None = None,
        unacknowledged: bool = False,
    ) -> dict[str, Any]:
        argv = ["reset-log", "--json-output"]
        if detail_id:
            argv.extend(["--detail", _safe_simple(detail_id, "detail_id")])
        if provider:
            argv.extend(["--provider", _safe_simple(provider, "provider")])
        if unacknowledged:
            argv.append("--unacknowledged")
        return self._run(argv, risk="cached_read")

    def tokenkick_daemon_status(self, *, include_paths: bool = False) -> dict[str, Any]:
        result = self._run(
            ["daemon", "--status", "--json-output"],
            risk="cached_read",
            app_mode=True,
            include_paths=include_paths,
        )
        return _redact_paths_by_default(result, include_paths=include_paths)

    def tokenkick_codex_strategy_status(self) -> dict[str, Any]:
        return self._run(["codex-strategy", "status", "--json-output"], risk="cached_read")

    def tokenkick_codex_surfaces(self, *, label: str) -> dict[str, Any]:
        return self._run(
            ["codex-surfaces", _safe_label(label), "--json-output"],
            risk="cached_read",
        )

    def tokenkick_plan_preview(
        self,
        *,
        work_window: str,
        date: str | None = None,
        timezone: str | None = None,
        usage: list[str] | None = None,
    ) -> dict[str, Any]:
        apply_argv = self._plan_argv(
            work_window=work_window,
            date=date,
            timezone=timezone,
            usage=usage,
            apply=True,
        )
        preview_argv = self._plan_argv(
            work_window=work_window,
            date=date,
            timezone=timezone,
            usage=usage,
            apply=False,
        )
        result = self._run(preview_argv, risk="cached_read")
        return self._attach_preview_token(
            result,
            tool_name="tokenkick_plan_apply",
            argv=apply_argv,
            risk="low_risk_mutation",
            can_execute=_plan_preview_can_apply(result),
            no_token_reason=_plan_preview_no_token_reason(result),
        )

    def tokenkick_plan_apply(
        self,
        *,
        work_window: str,
        preview_token: str,
        confirm: bool,
        date: str | None = None,
        timezone: str | None = None,
        usage: list[str] | None = None,
    ) -> dict[str, Any]:
        argv = self._plan_argv(
            work_window=work_window,
            date=date,
            timezone=timezone,
            usage=usage,
            apply=True,
        )
        self._require_confirm(confirm)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_plan_apply",
            argv=argv,
            risk="low_risk_mutation",
        )
        return self._run(argv, risk="low_risk_mutation", app_mode=True, timeout=MUTATION_TIMEOUT_SECONDS)

    def tokenkick_plan_cancel_preview(self, *, accounts: list[str] | None = None) -> dict[str, Any]:
        argv = self._plan_cancel_argv(accounts=accounts, execute=True)
        preview_argv = self._plan_cancel_argv(accounts=accounts, execute=False)
        current = self._run(preview_argv, risk="cached_read")
        if not _read_preview_available(current):
            return current
        current_payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
        matching = current_payload.get("matching") if isinstance(current_payload, dict) else None
        return self._preview_only(
            tool_name="tokenkick_plan_cancel",
            argv=argv,
            risk="low_risk_mutation",
            summary="Cancel applied orchestration pending kicks.",
            context={"cancel_preview": current_payload},
            can_execute=bool(matching),
            no_token_reason="No matching orchestrated pending kicks would be cancelled.",
        )

    def tokenkick_plan_cancel(
        self,
        *,
        preview_token: str,
        confirm: bool,
        accounts: list[str] | None = None,
    ) -> dict[str, Any]:
        argv = self._plan_cancel_argv(accounts=accounts, execute=True)
        self._require_confirm(confirm)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_plan_cancel",
            argv=argv,
            risk="low_risk_mutation",
        )
        return self._run(argv, risk="low_risk_mutation", timeout=MUTATION_TIMEOUT_SECONDS)

    def tokenkick_schedule_set_preview(
        self,
        *,
        account: str | None = None,
        weekdays: str | None = None,
        weekends: str | None = None,
        timezone: str | None = None,
        default: bool = False,
    ) -> dict[str, Any]:
        argv = self._schedule_set_argv(
            account=account,
            weekdays=weekdays,
            weekends=weekends,
            timezone=timezone,
            default=default,
        )
        current = self.tokenkick_schedule_show(account=account)
        if not _read_preview_available(current):
            return current
        current_payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
        return self._preview_only(
            tool_name="tokenkick_schedule_set",
            argv=argv,
            risk="low_risk_mutation",
            summary="Set smart schedule configuration and invalidate affected smart-schedule pending kicks.",
            context={
                "current_schedule": current_payload,
                "pending_kick_impact": _schedule_pending_impact(
                    current_payload,
                    account=account,
                    default=default,
                ),
            },
        )

    def tokenkick_schedule_set(
        self,
        *,
        preview_token: str,
        confirm: bool,
        account: str | None = None,
        weekdays: str | None = None,
        weekends: str | None = None,
        timezone: str | None = None,
        default: bool = False,
    ) -> dict[str, Any]:
        argv = self._schedule_set_argv(
            account=account,
            weekdays=weekdays,
            weekends=weekends,
            timezone=timezone,
            default=default,
        )
        self._require_confirm(confirm)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_schedule_set",
            argv=argv,
            risk="low_risk_mutation",
        )
        return self._run(argv, risk="low_risk_mutation", app_mode=True, timeout=MUTATION_TIMEOUT_SECONDS)

    def tokenkick_schedule_clear_or_disable_preview(
        self,
        *,
        action: Literal["clear", "disable"],
        account: str | None = None,
        default: bool = False,
    ) -> dict[str, Any]:
        argv = self._schedule_clear_disable_argv(action=action, account=account, default=default)
        current = self.tokenkick_schedule_show(account=account)
        if not _read_preview_available(current):
            return current
        current_payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
        return self._preview_only(
            tool_name="tokenkick_schedule_clear_or_disable",
            argv=argv,
            risk="low_risk_mutation",
            summary=f"{action.title()} smart schedule configuration and invalidate affected smart-schedule pending kicks.",
            context={
                "current_schedule": current_payload,
                "pending_kick_impact": _schedule_pending_impact(
                    current_payload,
                    account=account,
                    default=default,
                ),
            },
        )

    def tokenkick_schedule_clear_or_disable(
        self,
        *,
        action: Literal["clear", "disable"],
        preview_token: str,
        confirm: bool,
        account: str | None = None,
        default: bool = False,
    ) -> dict[str, Any]:
        argv = self._schedule_clear_disable_argv(action=action, account=account, default=default)
        self._require_confirm(confirm)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_schedule_clear_or_disable",
            argv=argv,
            risk="low_risk_mutation",
        )
        return self._run(argv, risk="low_risk_mutation", app_mode=True, timeout=MUTATION_TIMEOUT_SECONDS)

    def tokenkick_daemon_control_preview(
        self,
        *,
        action: Literal["background", "stop", "restart"],
    ) -> dict[str, Any]:
        argv = self._daemon_control_argv(action)
        current = self.tokenkick_daemon_status()
        if not _read_preview_available(current):
            return current
        return self._preview_only(
            tool_name="tokenkick_daemon_control",
            argv=argv,
            risk="low_risk_mutation",
            summary=f"Run daemon {action}.",
            context={"daemon_status": current.get("payload")},
        )

    def tokenkick_daemon_control(
        self,
        *,
        action: Literal["background", "stop", "restart"],
        preview_token: str,
        confirm: bool,
    ) -> dict[str, Any]:
        argv = self._daemon_control_argv(action)
        self._require_confirm(confirm)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_daemon_control",
            argv=argv,
            risk="low_risk_mutation",
        )
        return self._run(argv, risk="low_risk_mutation", app_mode=True, timeout=MUTATION_TIMEOUT_SECONDS)

    def tokenkick_run_dry_run(self, *, codex: bool = False) -> dict[str, Any]:
        apply_argv = ["run", "--json-output"]
        preview_argv = ["run", "--dry-run", "--json-output"]
        if codex:
            apply_argv.append("--codex")
            preview_argv.append("--codex")
        result = self._run(
            preview_argv,
            risk="live_provider_read",
            provider_refresh=True,
            timeout=LIVE_PROVIDER_TIMEOUT_SECONDS,
        )
        return self._attach_preview_token(
            result,
            tool_name="tokenkick_run_apply",
            argv=apply_argv,
            risk="dangerous_quota_operational",
            can_execute=_run_preview_can_apply(result),
            no_token_reason=_run_preview_no_token_reason(result),
        )

    def tokenkick_run_apply(
        self,
        *,
        preview_token: str,
        confirm: bool,
        quota_ack: bool,
        codex: bool = False,
    ) -> dict[str, Any]:
        argv = ["run", "--json-output"]
        if codex:
            argv.append("--codex")
        self._require_confirm(confirm)
        self._require_quota_ack(quota_ack)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_run_apply",
            argv=argv,
            risk="dangerous_quota_operational",
        )
        return self._run(
            argv,
            risk="dangerous_quota_operational",
            provider_refresh=True,
            timeout=QUOTA_TIMEOUT_SECONDS,
        )

    def tokenkick_kick_preview(self, *, label: str) -> dict[str, Any]:
        label = _safe_label(label)
        apply_argv = ["kick", label, "--json-output", "--yes"]
        preview_argv = ["kick", label, "--dry-run", "--json-output"]
        result = self._run(preview_argv, risk="cached_read", app_mode=True)
        return self._attach_preview_token(
            result,
            tool_name="tokenkick_kick_account",
            argv=apply_argv,
            risk="quota_consuming",
            can_execute=_kick_preview_can_apply(result),
            no_token_reason=_kick_preview_no_token_reason(result),
        )

    def tokenkick_kick_account(
        self,
        *,
        label: str,
        preview_token: str,
        confirm: bool,
        quota_ack: bool,
    ) -> dict[str, Any]:
        argv = ["kick", _safe_label(label), "--json-output", "--yes"]
        self._require_confirm(confirm)
        self._require_quota_ack(quota_ack)
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_kick_account",
            argv=argv,
            risk="quota_consuming",
        )
        return self._run(argv, risk="quota_consuming", app_mode=True, timeout=QUOTA_TIMEOUT_SECONDS)

    def tokenkick_force_recovery_preview(self, *, label: str) -> dict[str, Any]:
        label = _safe_label(label)
        argv = ["kick", label, "--force", "--json-output", "--yes"]
        recovery = self.tokenkick_recovery_hint(label=label)
        if not _read_preview_available(recovery):
            return recovery
        recovery_payload = recovery.get("payload") if isinstance(recovery.get("payload"), dict) else {}
        hints = recovery_payload.get("hints") if isinstance(recovery_payload, dict) else None
        return self._preview_only(
            tool_name="tokenkick_force_recovery_kick",
            argv=argv,
            risk="dangerous_recovery",
            summary=(
                "Force a one-time Codex recovery kick. This bypasses local guards "
                "and consumes a small amount of provider usage."
            ),
            context={
                "recovery_context": recovery_payload,
                "recommended": bool(hints),
            },
            can_execute=bool(hints),
            no_token_reason=(
                "No stale-refresh recovery hint was found for this account, so MCP did not create "
                "a force-recovery token. Use the CLI directly if you intentionally need to override this."
            ),
        )

    def tokenkick_force_recovery_kick(
        self,
        *,
        label: str,
        preview_token: str,
        confirm: bool,
        quota_ack: bool,
        force_ack: bool,
    ) -> dict[str, Any]:
        argv = ["kick", _safe_label(label), "--force", "--json-output", "--yes"]
        self._require_confirm(confirm)
        self._require_quota_ack(quota_ack)
        if not force_ack:
            raise ValueError("force_ack=true is required for force recovery kicks")
        self.token_store.consume(
            preview_token,
            tool_name="tokenkick_force_recovery_kick",
            argv=argv,
            risk="dangerous_recovery",
        )
        return self._run(argv, risk="dangerous_recovery", app_mode=True, timeout=QUOTA_TIMEOUT_SECONDS)

    def tokenkick_recovery_hint(self, *, label: str) -> dict[str, Any]:
        doctor = self.tokenkick_doctor(label=label)
        hints = []
        for account in _doctor_accounts(doctor.get("payload")):
            for check in account.get("checks", []):
                if check.get("code") == "account_refresh_error" and check.get("fix"):
                    hints.append(
                        {
                            "account": account.get("label"),
                            "message": check.get("message"),
                            "fix": check.get("fix"),
                        }
                    )
        return {
            "ok": True,
            "risk": "diagnostic_read",
            "provider_refresh": False,
            "may_read_environment": True,
            "command_summary": "derived from tokenkick_doctor",
            "payload": {"account": label, "hints": hints},
            "warnings": [],
            "error": None,
        }

    # ------------------------------------------------------------------
    # Resources and prompt text
    # ------------------------------------------------------------------

    def resource_agent_playbook(self) -> str:
        return _read_repo_text("docs/AGENT_PLAYBOOK.md")

    def resource_commands(self) -> str:
        return _read_repo_text("docs/TOKENKICK_COMMANDS.md")

    def prompt_plan_coding_session(self) -> str:
        return (
            "Use TokenKick MCP safely to plan a coding session. Start with cached snapshot/status, "
            "refresh provider status only if freshness matters, preview `tokenkick_plan_preview`, "
            "show the user the plan, and apply only with an explicit preview token and confirmation."
        )

    def prompt_check_health(self) -> str:
        return (
            "Use `tokenkick_snapshot`, `tokenkick_doctor`, and `tokenkick_daemon_status`. "
            "Treat doctor as diagnostic_read: it may inspect environment but must not refresh providers."
        )

    def prompt_recover_stale_codex(self) -> str:
        return (
            "Use `tokenkick_recovery_hint` and explain the recovery steps. Do not run force recovery "
            "unless the user explicitly approves the preview token with confirm, quota_ack, and force_ack."
        )

    def prompt_explain_pending_kicks(self) -> str:
        return (
            "Use `tokenkick_snapshot` and `tokenkick_schedule_show` to explain pending kicks, reasons, "
            "purposes, retry state, and whether orchestration or smart schedule owns them."
        )

    def prompt_prepare_accounts_for_tonight(self) -> str:
        return (
            "Use cached status, accounts planning, calendar, and plan preview. Prefer previews and "
            "avoid quota-consuming tools unless the user approves exact MCP calls."
        )

    def prompt_audit_daemon_and_schedule(self) -> str:
        return (
            "Use daemon status, schedule show, snapshot, and doctor. Mutate daemon/schedule only after "
            "creating a preview token and receiving explicit confirmation."
        )

    # ------------------------------------------------------------------
    # Command construction helpers
    # ------------------------------------------------------------------

    def _plan_argv(
        self,
        *,
        work_window: str,
        date: str | None,
        timezone: str | None,
        usage: list[str] | None,
        apply: bool,
    ) -> list[str]:
        argv = ["plan", "--work-window", _safe_work_window(work_window)]
        if date:
            argv.extend(["--date", _safe_date(date)])
        if timezone:
            argv.extend(["--timezone", _safe_simple(timezone, "timezone")])
        for item in usage or []:
            argv.extend(["--usage", _safe_usage(item)])
        if apply:
            argv.extend(["--apply", "--yes", "--json-output"])
        else:
            argv.append("--json-output")
        return argv

    def _plan_cancel_argv(self, *, accounts: list[str] | None, execute: bool) -> list[str]:
        argv = ["plan", "cancel", "--json-output"]
        for account in accounts or []:
            argv.extend(["--account", _safe_label(account)])
        if execute:
            argv.append("--yes")
        return argv

    def _schedule_set_argv(
        self,
        *,
        account: str | None,
        weekdays: str | None,
        weekends: str | None,
        timezone: str | None,
        default: bool,
    ) -> list[str]:
        if bool(account) == bool(default):
            raise ValueError("provide exactly one of account or default=true")
        if weekdays is None and weekends is None and timezone is None:
            raise ValueError("provide weekdays, weekends, or timezone")
        argv = ["schedule", "set", "--json-output"]
        if account:
            argv.extend(["--account", _safe_label(account)])
        else:
            argv.append("--default")
        if weekdays:
            argv.extend(["--weekdays", _safe_work_window(weekdays)])
        if weekends:
            argv.extend(["--weekends", _safe_work_window(weekends)])
        if timezone:
            argv.extend(["--timezone", _safe_simple(timezone, "timezone")])
        return argv

    def _schedule_clear_disable_argv(
        self,
        *,
        action: Literal["clear", "disable"],
        account: str | None,
        default: bool,
    ) -> list[str]:
        _require_choice("action", action, {"clear", "disable"})
        if bool(account) == bool(default):
            raise ValueError("provide exactly one of account or default=true")
        argv = ["schedule", action, "--json-output"]
        if account:
            argv.extend(["--account", _safe_label(account)])
        else:
            argv.append("--default")
        return argv

    def _daemon_control_argv(self, action: Literal["background", "stop", "restart"]) -> list[str]:
        _require_choice("action", action, {"background", "stop", "restart"})
        return ["daemon", f"--{action}", "--json-output"]

    def _preview_only(
        self,
        *,
        tool_name: str,
        argv: list[str],
        risk: RiskLevel,
        summary: str,
        context: dict[str, Any] | None = None,
        can_execute: bool = True,
        no_token_reason: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "summary": summary,
            "execute_command": _command_summary([self.tk_path, *argv]),
        }
        if context:
            payload.update(context)
        result = {
            "ok": True,
            "risk": risk,
            "provider_refresh": False,
            "may_read_environment": False,
            "command_summary": _command_summary([self.tk_path, *argv]),
            "payload": payload,
            "warnings": [],
            "error": None,
        }
        return self._attach_preview_token(
            result,
            tool_name=tool_name,
            argv=argv,
            risk=risk,
            can_execute=can_execute,
            no_token_reason=no_token_reason,
        )

    def _attach_preview_token(
        self,
        result: dict[str, Any],
        *,
        tool_name: str,
        argv: list[str],
        risk: RiskLevel,
        can_execute: bool,
        no_token_reason: str | None = None,
    ) -> dict[str, Any]:
        if result.get("ok") is not True:
            return result
        payload = result.get("payload")
        if isinstance(payload, dict):
            payload["mcp_preview"] = {
                "can_execute_with_preview_token": bool(can_execute),
                "requires_preview_token": bool(can_execute),
                "requires_confirm": bool(can_execute),
                "requires_quota_ack": risk in {
                    "quota_consuming",
                    "dangerous_quota_operational",
                    "dangerous_recovery",
                },
                "requires_force_ack": risk == "dangerous_recovery",
                "no_token_reason": None if can_execute else no_token_reason,
            }
        if not can_execute:
            if no_token_reason:
                result.setdefault("warnings", []).append(no_token_reason)
            return result
        preview = self.token_store.create(tool_name=tool_name, argv=argv, risk=risk)
        result["preview_token"] = preview.to_dict()
        return result

    def _require_confirm(self, confirm: bool) -> None:
        if confirm is not True:
            raise ValueError("confirm=true is required")

    def _require_quota_ack(self, quota_ack: bool) -> None:
        if quota_ack is not True:
            raise ValueError("quota_ack=true is required because this may consume provider usage")

    def _run(
        self,
        argv: list[str],
        *,
        risk: RiskLevel,
        app_mode: bool = False,
        provider_refresh: bool = False,
        may_read_environment: bool = False,
        timeout: float | None = None,
        include_paths: bool = False,
    ) -> dict[str, Any]:
        command = [self.tk_path, *argv]
        env = dict(os.environ)
        env["TK_NO_INTERACTIVE"] = "1"
        if app_mode:
            env["TK_APP_MODE"] = "1"
        timeout = timeout or _timeout_for_risk(risk)
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _error_result(
                risk=risk,
                provider_refresh=provider_refresh,
                may_read_environment=may_read_environment,
                command=command,
                include_paths=include_paths,
                error_code="timeout",
                message=f"TokenKick command timed out after {timeout:g}s.",
                stderr=exc.stderr if isinstance(exc.stderr, str) else None,
            )
        except OSError as exc:
            return _error_result(
                risk=risk,
                provider_refresh=provider_refresh,
                may_read_environment=may_read_environment,
                command=command,
                include_paths=include_paths,
                error_code="exec_error",
                message=f"{exc.__class__.__name__}: {exc}",
            )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        try:
            decoded = json.loads(stdout) if stdout else None
        except json.JSONDecodeError:
            return _error_result(
                risk=risk,
                provider_refresh=provider_refresh,
                may_read_environment=may_read_environment,
                command=command,
                include_paths=include_paths,
                error_code="invalid_json",
                message="TokenKick command did not return valid JSON.",
                stderr=stderr,
                stdout=stdout,
                returncode=completed.returncode,
            )

        return _normalize_json_result(
            decoded,
            risk=risk,
            provider_refresh=provider_refresh,
            may_read_environment=may_read_environment,
            command=command,
            include_paths=include_paths,
            stderr=stderr,
            returncode=completed.returncode,
        )


def _normalize_json_result(
    decoded: Any,
    *,
    risk: RiskLevel,
    provider_refresh: bool,
    may_read_environment: bool,
    command: list[str],
    include_paths: bool,
    stderr: str,
    returncode: int,
) -> dict[str, Any]:
    warnings: list[str] = []
    if stderr:
        warnings.append(_redact(stderr))
    if _is_app_envelope(decoded):
        ok = bool(decoded.get("ok")) and returncode == 0
        return {
            "ok": ok,
            "risk": risk,
            "provider_refresh": provider_refresh,
            "may_read_environment": may_read_environment,
            "command_summary": _command_summary(command, include_paths=include_paths),
            "payload": decoded.get("payload"),
            "warnings": [*_string_list(decoded.get("warnings")), *warnings],
            "error": None
            if ok
            else {
                "code": decoded.get("error_code") or "command_failed",
                "message": _redact(str(decoded.get("message") or "TokenKick command failed.")),
                "returncode": returncode,
            },
        }
    ok = returncode == 0
    return {
        "ok": ok,
        "risk": risk,
        "provider_refresh": provider_refresh,
        "may_read_environment": may_read_environment,
        "command_summary": _command_summary(command, include_paths=include_paths),
        "payload": decoded,
        "warnings": warnings,
        "error": None
        if ok
        else {
            "code": "command_failed",
            "message": "TokenKick command exited with a non-zero status.",
            "returncode": returncode,
        },
    }


def _error_result(
    *,
    risk: RiskLevel,
    provider_refresh: bool,
    may_read_environment: bool,
    command: list[str],
    include_paths: bool = False,
    error_code: str,
    message: str,
    stderr: str | None = None,
    stdout: str | None = None,
    returncode: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if stdout:
        payload["stdout_preview"] = _redact(stdout[:1000])
    if stderr:
        payload["stderr_preview"] = _redact(stderr[:1000])
    return {
        "ok": False,
        "risk": risk,
        "provider_refresh": provider_refresh,
        "may_read_environment": may_read_environment,
        "command_summary": _command_summary(command, include_paths=include_paths),
        "payload": payload,
        "warnings": [],
        "error": {
            "code": error_code,
            "message": _redact(message),
            "returncode": returncode,
        },
    }


def _is_app_envelope(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and "schema_version" in value
        and "ok" in value
        and "payload" in value
        and "error_code" in value
    )


def _doctor_accounts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        doctor = payload.get("doctor", payload)
        accounts = doctor.get("accounts") if isinstance(doctor, dict) else None
        if isinstance(accounts, list):
            return [item for item in accounts if isinstance(item, dict)]
    return []


def _read_preview_available(result: dict[str, Any]) -> bool:
    if result.get("ok") is True:
        return True
    payload = result.get("payload")
    # Some legacy read-only previews intentionally exit nonzero to signal
    # "requires --yes to mutate" while still returning the useful preview body.
    return isinstance(payload, dict) and payload.get("read_only") is True


def _plan_preview_can_apply(result: dict[str, Any]) -> bool:
    if result.get("ok") is not True:
        return False
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return False
    diff = payload.get("diff")
    if isinstance(diff, dict):
        if diff.get("conflicts_unmanaged"):
            return False
        return any(
            diff.get(key)
            for key in (
                "adds",
                "replaces_orchestrated",
                "removes_orchestrated",
            )
        )
    return bool(payload.get("planned_kicks"))


def _plan_preview_no_token_reason(result: dict[str, Any]) -> str | None:
    if result.get("ok") is not True:
        return None
    payload = result.get("payload")
    if isinstance(payload, dict):
        diff = payload.get("diff")
        if isinstance(diff, dict) and diff.get("conflicts_unmanaged"):
            return "The plan has unmanaged pending-kick conflicts, so MCP did not create an apply token."
    return "The preview has no pending-kick changes to apply, so MCP did not create an apply token."


def _run_preview_can_apply(result: dict[str, Any]) -> bool:
    if result.get("ok") is not True:
        return False
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return False
    kicked = payload.get("kicked")
    return isinstance(kicked, list) and bool(kicked)


def _run_preview_no_token_reason(result: dict[str, Any]) -> str | None:
    if result.get("ok") is not True:
        return None
    return "The dry run did not identify any kicks to execute, so MCP did not create a run token."


def _kick_preview_can_apply(result: dict[str, Any]) -> bool:
    if result.get("ok") is not True:
        return False
    payload = result.get("payload")
    return isinstance(payload, dict) and payload.get("decision") == "would_kick"


def _kick_preview_no_token_reason(result: dict[str, Any]) -> str | None:
    if result.get("ok") is not True:
        return None
    payload = result.get("payload")
    if isinstance(payload, dict) and payload.get("decision"):
        return f"Kick preview decision is {payload['decision']!r}, so MCP did not create a kick token."
    return "Kick preview did not report a kickable account, so MCP did not create a kick token."


def _schedule_pending_impact(
    payload: Any,
    *,
    account: str | None,
    default: bool,
) -> dict[str, list[dict[str, Any]]]:
    pending = payload.get("pending_kicks") if isinstance(payload, dict) else None
    if not isinstance(pending, list):
        pending = []

    def in_scope(item: dict[str, Any]) -> bool:
        if default:
            return True
        return item.get("account_label") == account

    smart = [
        item
        for item in pending
        if isinstance(item, dict) and in_scope(item) and item.get("reason") != "orchestrated"
    ]
    orchestrated = [
        item
        for item in pending
        if isinstance(item, dict) and in_scope(item) and item.get("reason") == "orchestrated"
    ]
    return {
        "would_remove_smart_schedule_pending_kicks": smart,
        "kept_orchestrated_pending_kicks": orchestrated,
    }


def _redact_paths_by_default(result: dict[str, Any], *, include_paths: bool) -> dict[str, Any]:
    if result.get("ok") is True and not include_paths:
        result["payload"] = _redact_path_sensitive_payload(result.get("payload"))
        result.setdefault("warnings", []).append(
            "Local runtime paths are redacted by default; pass include_paths=true "
            "when path diagnostics are required."
        )
    return result


def _redact_path_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            redacted[key] = _redact_path_sensitive_value(key, item)
        return redacted
    if isinstance(value, list):
        return [_redact_path_sensitive_payload(item) for item in value]
    return value


def _redact_path_sensitive_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {
            child_key: _redact_path_sensitive_value(child_key, child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_path_sensitive_value(key, item) for item in value]
    if isinstance(value, str) and _path_key_is_sensitive(key) and _LOCAL_PATH_RE.match(value):
        return "[redacted]"
    return value


def _path_key_is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _PATH_KEY_PARTS)


def _redact_history_payload(value: Any, *, include_details: bool) -> Any:
    if isinstance(value, list):
        return [_redact_history_payload(item, include_details=include_details) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if not include_details and key in _HISTORY_DETAIL_FIELDS:
                if item not in (None, "", [], {}):
                    redacted[f"{key}_redacted"] = True
                continue
            redacted[key] = _redact_history_payload(item, include_details=include_details)
        return redacted
    if isinstance(value, str):
        return _redact(value)
    return value


def _timeout_for_risk(risk: RiskLevel) -> float:
    if risk == "live_provider_read":
        return LIVE_PROVIDER_TIMEOUT_SECONDS
    if risk in {"quota_consuming", "dangerous_quota_operational", "dangerous_recovery"}:
        return QUOTA_TIMEOUT_SECONDS
    if "mutation" in risk:
        return MUTATION_TIMEOUT_SECONDS
    return DEFAULT_TIMEOUT_SECONDS


def _safe_label(value: str) -> str:
    return _safe_text(value, "label", allow_equals=True)


def _safe_simple(value: str, field: str) -> str:
    return _safe_text(value, field, allow_equals=False)


def _safe_text(value: str, field: str, *, allow_equals: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value.startswith("-"):
        raise ValueError(f"{field} must not start with '-'")
    if _CONTROL_CHAR_RE.search(value):
        raise ValueError(f"{field} contains control characters")
    if not allow_equals and "=" in value:
        raise ValueError(f"{field} must not contain '='")
    return value


def _safe_work_window(value: str) -> str:
    value = _safe_simple(value, "work_window")
    if not _WORK_WINDOW_RE.match(value):
        raise ValueError("work_window must be HH:MM-HH:MM")
    start_text, end_text = value.split("-", 1)
    if start_text == end_text:
        raise ValueError("work_window start and end must differ")
    for part in (start_text, end_text):
        hour, minute = [int(piece) for piece in part.split(":")]
        if hour > 23 or minute > 59:
            raise ValueError("work_window times must be valid HH:MM values within 00:00..23:59")
    return value


def _safe_date(value: str) -> str:
    value = _safe_simple(value, "date")
    if not _DATE_RE.match(value):
        raise ValueError("date must be YYYY-MM-DD")
    return value


def _safe_usage(value: str) -> str:
    value = _safe_text(value, "usage", allow_equals=True)
    if "=" not in value:
        raise ValueError(
            "usage must be '<account label>=<duration>', for example "
            "'codex (personal)=150m' or 'claude (work)=2h'"
        )
    label, duration = value.rsplit("=", 1)
    if not label.strip():
        raise ValueError("usage account label must not be empty")
    _parse_usage_duration_minutes(duration)
    return value


def _parse_usage_duration_minutes(value: str) -> int:
    raw = re.sub(r"\s+", "", value.strip().lower())
    minutes: int
    if re.fullmatch(r"\d+", raw):
        minutes = int(raw)
    elif match := re.fullmatch(r"(\d+)m", raw):
        minutes = int(match.group(1))
    elif match := re.fullmatch(r"(\d+(?:\.\d+)?)h", raw):
        minutes = int(round(float(match.group(1)) * 60))
    elif match := re.fullmatch(r"(\d+)h(\d+)m", raw):
        minutes = int(match.group(1)) * 60 + int(match.group(2))
    else:
        raise ValueError(
            f'Invalid usage duration "{value}". Use forms like 180, 180m, 3h, 2.5h, or 1h30m.'
        )
    if not 1 <= minutes <= 1440:
        raise ValueError("Usage duration must be between 1 and 1440 minutes.")
    return minutes


def _require_choice(field: str, value: str, choices: set[str]) -> None:
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(choices))}")


def _argv_hash(argv: list[str]) -> str:
    raw = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _command_summary(command: list[str], *, include_paths: bool = False) -> str:
    if include_paths or not command:
        return _redact(shlex.join(command), redact_paths=False)
    display = list(command)
    if _LOCAL_PATH_RE.match(display[0]):
        display[0] = "tk"
    return _redact(shlex.join(display))


def _redact(value: str, *, redact_paths: bool = True) -> str:
    value = _TOKEN_RE.sub(r"\1=<redacted>", value)
    value = _BEARER_RE.sub("Bearer <redacted>", value)
    value = _OPENAI_KEY_RE.sub("sk-<redacted>", value)
    if redact_paths:
        value = _TEMP_PATH_RE.sub("<temp-path>", value)
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_redact(str(item)) for item in value]


def _read_repo_text(relative_path: str) -> str:
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, relative_path)
    with open(path, encoding="utf-8") as handle:
        return handle.read()
