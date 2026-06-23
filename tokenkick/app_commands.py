"""`tk app` — JSON-first commands consumed by the native macOS app.

These commands always reserve stdout for JSON (envelopes or JSON-lines),
regardless of TK_APP_MODE; human-readable side output goes to stderr.
Provider logic stays in the core helpers — this module only assembles
payloads.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console

from .app_mode import (
    ERROR_STATE_FILE,
    ERROR_USAGE,
    app_envelope,
    app_mode_enabled,
    emit_app_error,
    emit_app_event,
    emit_app_json,
    emit_app_success,
)
from . import models as _models
from .mcp_setup import MCPSetupError, MCPSetupManager
from .models import StateFileError
from .versioning import installed_version

SNAPSHOT_RESET_EVENT_HOURS = 48
EXTERNAL_TK_VERSION_TIMEOUT_SECONDS = 10
PROVIDER_CLI_NAMES = ("codex", "claude", "gemini")


def _cli():
    from . import cli

    return cli


@contextmanager
def _stdout_reserved_for_json():
    """Route the shared console to stderr so app command stdout stays JSON-only."""
    cli = _cli()
    previous = cli.console
    if not app_mode_enabled():
        cli.console = Console(width=120, stderr=True)
    try:
        yield
    finally:
        cli.console = previous


@click.group("app")
def app_group():
    """JSON-first commands for the native TokenKick app."""


# ---------------------------------------------------------------------------
# tk app snapshot
# ---------------------------------------------------------------------------

def _external_tk_info(*, probe_version: bool = True) -> dict | None:
    path = shutil.which("tk")
    if not path:
        return None
    resolved = Path(path).resolve()
    current = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    is_current = current is not None and resolved == current
    info: dict = {
        "path": str(resolved),
        "is_current_runtime": is_current,
        "version": installed_version() if is_current else None,
    }
    if is_current or not probe_version:
        return info
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=EXTERNAL_TK_VERSION_TIMEOUT_SECONDS,
            env={**os.environ, "TK_NO_INTERACTIVE": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return info
    match = re.search(r"version\s+(\S+)", result.stdout or "")
    if match:
        info["version"] = match.group(1)
    return info


def _snapshot_status_section(cli, config) -> tuple[dict, list, dict, list[str]]:
    warnings: list[str] = []
    cached = cli._load_status_cache(config)
    if cached is None:
        warnings.append("No readable status cache; run setup or a status refresh.")
        empty = {
            "cached": False,
            "cached_at": None,
            "refresh_error": None,
            "refresh_in_progress": cli._status_refresh_lock_active(),
            "schema_version": 1,
            "accounts": [],
        }
        return empty, [], {}, warnings
    accounts, statuses, cache_entries = cached
    payload = cli._status_json_payload(
        accounts=accounts,
        statuses=statuses,
        metadata_accounts=accounts,
        metadata_statuses=statuses,
        cached=True,
        refresh_error=None,
        config=config,
        cache_entries=cache_entries,
    )
    statuses_by_key = cli._cache_statuses_by_key_from_pairs(accounts, statuses)
    return payload, accounts, statuses_by_key, warnings


def build_app_snapshot() -> tuple[dict, list[str]]:
    cli = _cli()
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    config = cli.Config.load()

    daemon = cli._daemon_status_payload(config)
    if daemon["stale_pidfile"]:
        warnings.append("Daemon pidfile is stale; the daemon is not running.")
    if daemon["running"] and daemon["version_match"] is False:
        warnings.append(
            f"Daemon runs v{daemon['version']} but v{daemon['installed_version']} is installed; "
            "restart the daemon."
        )
    if daemon["running"] and daemon.get("executable_match") is False:
        warnings.append(
            "Daemon is running from a different TokenKick executable; "
            "use TokenKick.app daemon management to take over or repair it."
        )

    external_tk = _external_tk_info()
    if (
        external_tk is not None
        and not external_tk["is_current_runtime"]
        and external_tk["version"] is not None
        and external_tk["version"] != installed_version()
    ):
        warnings.append(
            f"External tk v{external_tk['version']} on PATH differs from this runtime "
            f"v{installed_version()}."
        )

    status_payload, accounts, statuses_by_key, status_warnings = _snapshot_status_section(cli, config)
    warnings.extend(status_warnings)

    pending = cli.load_pending_kicks(now)

    try:
        advisories = [
            advisory.to_dict()
            for advisory in cli.build_reservation_advisories(
                list(accounts) or list(config.accounts),
                statuses_by_key,
                pending,
                now=now,
            )
        ]
    except Exception as exc:  # noqa: BLE001 — advisories must not break the snapshot
        advisories = []
        warnings.append(f"Reservation advisories unavailable: {exc}")

    reset_observations = [
        event.to_dict() for event in cli.recent_reset_events(hours=SNAPSHOT_RESET_EVENT_HOURS)
    ]

    notifications = {
        "enabled": config.notifications.enabled,
        "backends": cli._configured_notification_backends(config.notifications),
        "destination": cli._notification_destination_display(config.notifications),
        "accounts": [
            {
                "label": account.label,
                "provider": account.provider,
                "notifications_enabled": account.notifications_enabled,
                "backends": account.notification_backends,
                "route": cli._notification_route_display(account, config.notifications),
            }
            for account in config.accounts
        ],
    }

    schedule = {
        "enabled": config.schedule.enabled,
        "timezone": config.schedule.timezone,
        "scheduling_target": config.schedule.scheduling_target,
        "default": config.schedule.default.to_dict(),
        "accounts": {
            label: value.to_dict()
            for label, value in sorted(config.schedule.accounts.items())
        },
    }

    update = cli._update_status_payload()

    payload = {
        "generated_at": now.isoformat(),
        "core": {
            "version": installed_version(),
            "executable": sys.argv[0] if sys.argv else None,
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "app_mode": app_mode_enabled(),
        },
        "runtime": {
            "external_tk": external_tk,
        },
        "paths": {
            "config_dir": str(cli.CONFIG_DIR),
            "config_file": str(cli.CONFIG_FILE),
            "status_cache_file": str(cli.STATUS_CACHE_FILE),
            "daemon_pidfile": str(cli.DAEMON_PID_FILE),
            "daemon_log_file": str(cli.DAEMON_LOG_FILE),
            "history_file": str(_models.HISTORY_FILE),
        },
        "daemon": daemon,
        "status": status_payload,
        "pending_kicks": cli._pending_kicks_payload(pending),
        "schedule": schedule,
        "advisories": advisories,
        "reset_observations": reset_observations,
        "notifications": notifications,
        "codex_strategy": cli._codex_burst_ladder_status_payload(config),
        "update": update,
    }
    return payload, warnings


@app_group.command("snapshot")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_snapshot(as_json: bool):
    """One-call state snapshot for the native app."""
    del as_json  # snapshot output is always JSON
    with _stdout_reserved_for_json():
        payload, warnings = build_app_snapshot()
    emit_app_success(payload, warnings=warnings)


# ---------------------------------------------------------------------------
# tk app setup
# ---------------------------------------------------------------------------

def _run_app_setup(emit) -> tuple[dict, list[str]]:
    """Non-interactive setup mirroring `tk setup`: discover, save, no prompts, no daemon."""
    cli = _cli()
    warnings: list[str] = []
    emit("setup_started", version=installed_version())

    existing = cli.Config.load()
    emit("config_loaded", accounts=len(existing.accounts))

    emit("progress", message="Checking saved account migrations")
    existing = cli._repair_codex_home_identity_drift_if_needed(
        cli._migrate_codex_home_keys_if_needed(existing)
    )

    emit("progress", message="Discovering accounts and reading status")
    accounts, statuses, _discovered, summary, new_accounts = cli._load_account_status_pairs(
        existing,
        prepare_claude_setup=True,
    )
    emit("discovery_completed", summary=summary, accounts=len(accounts))

    if not accounts:
        warnings.append(summary)
        warnings.append("Log in with Codex/CodexBar, then run setup again.")
        return (
            {
                "summary": summary,
                "config_saved": False,
                "config_path": str(cli.CONFIG_FILE),
                "accounts": [],
                "new_accounts": [],
                "hidden_duplicate_labels": [],
                "status": None,
            },
            warnings,
        )

    emit("progress", message="Checking duplicate and unhealthy homes")
    setup_accounts = cli._with_setup_auto_kick_defaults(accounts, existing)
    setup_accounts, hidden_duplicate_labels = cli._hide_unusable_duplicate_codex_homes(
        setup_accounts,
        statuses,
        existing,
    )
    cli._apply_claude_direct_usage_setup_default(existing, accounts)

    config = replace(existing, accounts=setup_accounts)
    cli._migrate_pending_kick_keys(existing.accounts, setup_accounts)
    config.save()
    cli._save_status_cache(
        setup_accounts,
        cli._cache_statuses_by_key_from_pairs(setup_accounts, statuses),
    )
    emit("config_saved", path=str(cli.CONFIG_FILE), accounts=len(setup_accounts))

    for identity, _group in cli._duplicate_codex_home_groups(setup_accounts):
        warnings.append(
            f"Multiple Codex homes found for {identity}; only usable homes should auto-kick."
        )
    if hidden_duplicate_labels:
        warnings.append(
            "Hidden unusable duplicate Codex home(s): "
            + ", ".join(hidden_duplicate_labels)
            + ". They remain saved."
        )

    status_payload = cli._status_json_payload(
        accounts=setup_accounts,
        statuses=statuses,
        metadata_accounts=setup_accounts,
        metadata_statuses=statuses,
        cached=False,
        refresh_error=None,
        config=config,
        cache_entries={},
    )
    payload = {
        "summary": summary,
        "config_saved": True,
        "config_path": str(cli.CONFIG_FILE),
        "accounts": [cli._account_detail_payload(account) for account in setup_accounts],
        "new_accounts": [account.label for account in new_accounts],
        "hidden_duplicate_labels": hidden_duplicate_labels,
        "status": status_payload,
    }
    return payload, warnings


@app_group.command("setup")
@click.option("--json-lines", "json_lines", is_flag=True, default=False, help="Stream JSON-lines (always on)")
def app_setup(json_lines: bool):
    """Non-interactive setup with JSON-lines progress for the native app."""
    del json_lines  # setup output is always JSON-lines
    cli = _cli()
    previous_callback = cli._SETUP_PROGRESS_CALLBACK

    def progress_callback(message: str | None) -> None:
        if message:
            emit_app_event("progress", message=message)

    def emit(event: str, **fields) -> None:
        emit_app_event(event, **fields)

    cli._SETUP_PROGRESS_CALLBACK = progress_callback
    try:
        with _stdout_reserved_for_json():
            payload, warnings = _run_app_setup(emit)
    except KeyboardInterrupt:
        emit_app_json(
            {
                "event": "setup_cancelled",
                **app_envelope(
                    ok=False,
                    error_code="cancelled",
                    message="Setup was cancelled before completion.",
                ),
            },
            compact=True,
        )
        sys.exit(130)
    except StateFileError as exc:
        emit_app_json(
            {
                "event": "setup_failed",
                **app_envelope(ok=False, error_code=ERROR_STATE_FILE, message=str(exc)),
            },
            compact=True,
        )
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 — setup must end with a JSON record
        emit_app_json(
            {
                "event": "setup_failed",
                **app_envelope(
                    ok=False,
                    error_code="setup_failed",
                    message=f"{exc.__class__.__name__}: {exc}",
                ),
            },
            compact=True,
        )
        sys.exit(1)
    finally:
        cli._SETUP_PROGRESS_CALLBACK = previous_callback
    emit_app_json(
        {
            "event": "setup_completed",
            **app_envelope(ok=True, payload=payload, warnings=warnings),
        },
        compact=True,
    )


# ---------------------------------------------------------------------------
# tk app doctor
# ---------------------------------------------------------------------------

def _state_dir_writable(config_dir: Path) -> tuple[bool, str | None]:
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        probe = config_dir / ".tk-app-doctor-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, None


def build_app_doctor() -> tuple[dict, list[str]]:
    cli = _cli()
    warnings: list[str] = []

    provider_clis = {}
    for name in PROVIDER_CLI_NAMES:
        path = shutil.which(name)
        provider_clis[name] = {"found": path is not None, "path": path}
    if not provider_clis["codex"]["found"] and not provider_clis["claude"]["found"]:
        warnings.append(
            "Neither codex nor claude was found on PATH; provider discovery will fail "
            "from this environment."
        )

    config_dir = Path(str(cli.CONFIG_DIR))
    writable, write_error = _state_dir_writable(config_dir)
    if not writable:
        warnings.append(f"State directory is not writable: {write_error}")

    config_loadable = True
    config_error: str | None = None
    config = None
    try:
        config = cli.Config.load()
    except StateFileError as exc:
        config_loadable = False
        config_error = str(exc)
        warnings.append(f"Config could not be loaded: {exc}")

    daemon = cli._daemon_status_payload(config) if config is not None else None

    doctor_report = None
    if config_loadable:
        try:
            report = cli.build_doctor_report()
            doctor_report = report.to_dict()
            for check in report.checks:
                if check.level == "FAIL":
                    warnings.append(f"doctor: {check.code}: {check.message}")
        except (StateFileError, ValueError) as exc:
            warnings.append(f"Doctor report unavailable: {exc}")

    payload = {
        "environment": {
            "executable": sys.argv[0] if sys.argv else None,
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "cwd": os.getcwd(),
            "path_env": os.environ.get("PATH", ""),
            "app_mode": app_mode_enabled(),
            "core_version": installed_version(),
        },
        "provider_clis": provider_clis,
        "state": {
            "config_dir": str(config_dir),
            "config_dir_exists": config_dir.exists(),
            "config_dir_writable": writable,
            "config_file": str(cli.CONFIG_FILE),
            "config_file_exists": Path(str(cli.CONFIG_FILE)).exists(),
            "config_loadable": config_loadable,
            "config_error": config_error,
        },
        "daemon": daemon,
        "doctor": doctor_report,
    }
    return payload, warnings


@app_group.command("doctor")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_doctor(as_json: bool):
    """App-environment diagnosis for the native app."""
    del as_json  # doctor output is always JSON
    with _stdout_reserved_for_json():
        payload, warnings = build_app_doctor()
    emit_app_success(payload, warnings=warnings)


# ---------------------------------------------------------------------------
# tk app mcp-* — JSON-first MCP setup commands
# ---------------------------------------------------------------------------

def _app_mcp_manager() -> MCPSetupManager:
    return MCPSetupManager()


def _app_mcp_mutation_error(operation: str) -> None:
    emit_app_error(
        ERROR_USAGE,
        f"`tk app mcp-{operation}` requires --yes before it writes client config.",
        payload={"operation": operation, "read_only": True},
    )
    sys.exit(2)


@app_group.command("mcp-status")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_mcp_status(client: str, as_json: bool):
    """Read MCP client setup status for the native app."""
    del as_json
    emit_app_success(_app_mcp_manager().status(client=client))


@app_group.command("mcp-install")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--use-helper", is_flag=True, help="Use the stable TokenKick helper path")
@click.option("--yes", is_flag=True, help="Confirm writing MCP client config")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_mcp_install(client: str, use_helper: bool, yes: bool, as_json: bool):
    """Install MCP client config from app mode."""
    del as_json
    if not yes:
        _app_mcp_mutation_error("install")
    try:
        emit_app_success(_app_mcp_manager().install(client=client, use_helper=use_helper))
    except MCPSetupError as exc:
        emit_app_error("mcp_setup_error", str(exc))
        sys.exit(1)


@app_group.command("mcp-repair")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--use-helper", is_flag=True, help="Use the stable TokenKick helper path")
@click.option("--yes", is_flag=True, help="Confirm writing MCP client config")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_mcp_repair(client: str, use_helper: bool, yes: bool, as_json: bool):
    """Repair MCP client config from app mode."""
    del as_json
    if not yes:
        _app_mcp_mutation_error("repair")
    try:
        emit_app_success(
            _app_mcp_manager().install(client=client, use_helper=use_helper, repair_only=True)
        )
    except MCPSetupError as exc:
        emit_app_error("mcp_setup_error", str(exc))
        sys.exit(1)


@app_group.command("mcp-remove")
@click.option("--client", type=click.Choice(["all", "auto", "codex", "claude-desktop", "claude-code"]), default="all")
@click.option("--yes", is_flag=True, help="Confirm removing MCP client config")
@click.option("--json-output", "as_json", is_flag=True, default=False, help="Output as JSON (always on)")
def app_mcp_remove(client: str, yes: bool, as_json: bool):
    """Remove MCP client config from app mode."""
    del as_json
    if not yes:
        _app_mcp_mutation_error("remove")
    try:
        emit_app_success(_app_mcp_manager().remove(client=client))
    except MCPSetupError as exc:
        emit_app_error("mcp_setup_error", str(exc))
        sys.exit(1)
