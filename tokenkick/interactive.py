"""Basic interactive helper for TokenKick."""

from __future__ import annotations

import errno
import os
import signal
import shlex
import shutil
import subprocess
import sys
import termios
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import click

from .kicker import KICKABLE_PROVIDERS
from .models import AccountConfig, AccountState, AccountStatus, Config, DataSource, account_key_string
from .orchestration import effective_orchestration_role, usable_session_minutes_for_account
from .scheduling import cancel_orchestrated_pending_kicks, local_timezone, parse_work_window


MENU_EXIT = "exit"
CODEX_SURFACE_ORDER_RESET = "__reset_to_default__"
RESET_GO_LOGO = (
    r"  ____                 _  ___       ____        ",
    r" |  _ \ ___  ___  ___ | ||__ \     / ___| ___   ",
    r" | |_) / _ \/ __|/ _ \| __|/ /    | |  _ / _ \  ",
    r" |  _ <  __/\__ \  __/| |_ |_|    | |_| | (_) | _",
    r" |_| \_\___||___/\___| \__|(_)     \____|\___/ (_)",
)
SETUP_DISCOVERY_PHASES = (
    "Reading saved TokenKick config",
    "Checking saved account migrations",
    "Discovering accounts and reading status",
    "Discovering local account homes",
    "Checking direct provider homes",
    "Reading provider status",
    "Checking account snapshots",
    "Checking session-file fallbacks",
    "Merging discovered accounts",
    "Checking duplicate and unhealthy homes",
    "Preparing setup summary",
)


@dataclass(frozen=True)
class MenuChoice:
    name: str
    value: str
    enabled: bool = True

    def to_inquirer(self) -> dict[str, Any]:
        choice: dict[str, Any] = {"name": self.name, "value": self.value}
        if not self.enabled:
            choice["enabled"] = False
        return choice


def run_command_center(ctx: click.Context, *, first_run_setup: bool = False) -> None:
    """Run the basic interactive TokenKick helper."""
    _print_banner()
    if first_run_setup:
        _maybe_open_first_run_setup(ctx)
    while True:
        action = _select(
            "What would you like to do?",
            [
                MenuChoice("Status        View cached or live provider status", "status"),
                MenuChoice("Kick          Anchor a ready window", "kick"),
                MenuChoice("Schedule      Orchestration plans and smart kick windows", "schedule"),
                MenuChoice("Configure     Accounts, auto-kick, Codex, notifications", "configure"),
                MenuChoice("History       Recent kicks, account filters, verbose evidence", "history"),
                MenuChoice("Diagnostics   Doctor, reset log, Codex buckets", "diagnostics"),
                MenuChoice("Daemon        Start, stop, restart, or inspect daemon", "daemon"),
                MenuChoice("Exit", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        _dispatch_action(ctx, action)


def _maybe_open_first_run_setup(ctx: click.Context) -> None:
    if Config.load().accounts:
        return
    click.echo(click.style("No saved accounts found. Opening setup first.", fg="yellow"))
    _setup_menu(ctx)


def _dispatch_action(ctx: click.Context, action: str) -> None:
    handlers = {
        "status": _status_menu,
        "kick": _kick_menu,
        "schedule": _schedule_menu,
        "history": _history_menu,
        "configure": _configure_menu,
        "setup": _setup_menu,
        "accounts": _accounts_menu,
        "auto": _auto_menu,
        "codex_settings": _codex_settings_menu,
        "notifications": _notifications_menu,
        "remote_telegram": _remote_telegram_menu,
        "mcp": _mcp_menu,
        "diagnostics": _diagnostics_menu,
        "daemon": _daemon_menu,
    }
    handler = handlers.get(action)
    if handler is not None:
        handler(ctx)


def _print_banner() -> None:
    icon = click.style("→", fg="green", bold=True)
    click.echo(
        icon
        + " "
        + click.style("Token", fg="white", bold=True)
        + click.style("Kick", fg="green", bold=True)
    )
    for line in RESET_GO_LOGO:
        click.echo(
            click.style(line[:34], fg="white", bold=True)
            + click.style(line[34:], fg="green", bold=True)
        )
    click.echo()


def _inquirer_style_kwargs() -> dict[str, Any]:
    try:
        from InquirerPy.utils import InquirerPyStyle
    except ImportError:
        return {}

    style = {
        "questionmark": "#e5c07b",
        "question": "bold",
        "answer": "#61afef",
        "pointer": "#61afef",
        "highlighted": "#61afef",
        "selected": "#98c379",
        "separator": "#abb2bf",
        "instruction": "#abb2bf",
        "long_instruction": "#abb2bf",
        "input": "",
        "validator": "",
    }
    try:
        return {"style": InquirerPyStyle.from_dict(style)}
    except AttributeError:
        return {"style": InquirerPyStyle(style)}


def _back_keybinding_kwargs() -> dict[str, Any]:
    return {
        "keybindings": {"skip": [{"key": "escape"}]},
        "mandatory": False,
    }


def _select(
    message: str,
    choices: list[MenuChoice | dict[str, Any] | str],
    *,
    default: str | None = None,
) -> str:
    from InquirerPy import inquirer

    normalized = [
        choice.to_inquirer() if isinstance(choice, MenuChoice) else choice
        for choice in choices
    ]
    result = _execute_inquirer_prompt(inquirer.select(
        message=message,
        choices=normalized,
        default=default,
        pointer=">",
        **_inquirer_style_kwargs(),
        **_back_keybinding_kwargs(),
    ))
    return MENU_EXIT if result is None else str(result)


def _checkbox(message: str, choices: list[MenuChoice | dict[str, Any] | str]) -> list[str]:
    from InquirerPy import inquirer

    normalized = [
        choice.to_inquirer() if isinstance(choice, MenuChoice) else choice
        for choice in choices
    ]
    result = _execute_inquirer_prompt(inquirer.checkbox(
        message=message,
        choices=normalized,
        pointer=">",
        **_inquirer_style_kwargs(),
        **_back_keybinding_kwargs(),
    ))
    return [MENU_EXIT] if result is None else list(result)


def _execute_inquirer_prompt(prompt: Any) -> Any:
    """Run an Inquirer prompt with foreground TTY protection and EINTR retry."""
    for attempt in range(2):
        tty_fd, previous_pgrp = _claim_foreground_terminal()
        try:
            return prompt.execute()
        except termios.error as exc:
            if _is_interrupted_system_call(exc) and attempt == 0:
                continue
            if _is_interrupted_system_call(exc):
                click.echo(click.style("Terminal prompt was interrupted; returning to menu.", fg="yellow"))
                return None
            raise
        finally:
            _restore_foreground_terminal(tty_fd, previous_pgrp)
    return None


def _is_interrupted_system_call(exc: termios.error) -> bool:
    return bool(exc.args) and exc.args[0] == errno.EINTR


def _claim_foreground_terminal() -> tuple[int | None, int | None]:
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return None, None
    try:
        process_group = os.getpgrp()
        tty_process_group = os.tcgetpgrp(tty_fd)
        if process_group == tty_process_group:
            return tty_fd, None
        old_handler = signal.getsignal(signal.SIGTTOU)
        try:
            signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            os.tcsetpgrp(tty_fd, process_group)
        finally:
            signal.signal(signal.SIGTTOU, old_handler)
        return tty_fd, tty_process_group
    except OSError:
        os.close(tty_fd)
        return None, None


def _restore_foreground_terminal(tty_fd: int | None, previous_pgrp: int | None) -> None:
    if tty_fd is None:
        return
    try:
        if previous_pgrp is not None:
            old_handler = signal.getsignal(signal.SIGTTOU)
            try:
                signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                os.tcsetpgrp(tty_fd, previous_pgrp)
            except OSError:
                pass
            finally:
                signal.signal(signal.SIGTTOU, old_handler)
    finally:
        os.close(tty_fd)


def _confirm(message: str, *, default: bool = False) -> bool:
    choices = [
        MenuChoice("Yes", "yes"),
        MenuChoice("No", "no"),
        MenuChoice("Back", MENU_EXIT),
    ]
    default_value = "yes" if default else "no"
    return _select(message, choices, default=default_value) == "yes"


def _text(message: str, *, default: str = "") -> str:
    from InquirerPy import inquirer

    result = _execute_inquirer_prompt(inquirer.text(
        message=message,
        default=default,
        instruction="(Esc to go back)",
        **_inquirer_style_kwargs(),
        **_back_keybinding_kwargs(),
    ))
    return "" if result is None else str(result).strip()


def _text_action(
    message: str,
    *,
    default: str = "",
    action_label: str | None = None,
) -> str | None:
    set_label = action_label or (f"Set value ({default})" if default else "Set value")
    if action_label and default:
        set_label = f"{action_label} ({default})"
    action = _select(
        message,
        [
            MenuChoice(set_label, "set"),
            MenuChoice("Back", MENU_EXIT),
        ],
    )
    if action == MENU_EXIT:
        return None
    return _text(message, default=default)


def _secret(message: str) -> str:
    from InquirerPy import inquirer

    result = _execute_inquirer_prompt(inquirer.secret(
        message=message,
        instruction="(Esc to go back)",
        **_inquirer_style_kwargs(),
        **_back_keybinding_kwargs(),
    ))
    return "" if result is None else str(result).strip()


def _secret_action(message: str, *, action_label: str | None = None) -> str | None:
    action = _select(
        message,
        [
            MenuChoice(action_label or "Set secret", "set"),
            MenuChoice("Back", MENU_EXIT),
        ],
    )
    if action == MENU_EXIT:
        return None
    return _secret(message)


def _invoke(ctx: click.Context, command: Any, **kwargs: Any) -> None:
    try:
        ctx.invoke(command, **kwargs)
    except (SystemExit, click.exceptions.Exit) as exc:
        code = getattr(exc, "code", None)
        if code not in (None, 0):
            click.echo(click.style(f"Command exited with status {code}.", fg="yellow"))


def _tk_subprocess_command() -> str:
    return shutil.which("tk") or (sys.argv[0] if sys.argv and sys.argv[0] else "tk")


def _path_looks_like_tokenkick_pipx(value: str | None) -> bool:
    if not value:
        return False
    try:
        resolved = Path(value).expanduser().resolve(strict=False)
    except OSError:
        resolved = Path(value).expanduser()
    parts = [part.lower() for part in resolved.parts]
    return "pipx" in parts and "venvs" in parts and "tokenkick" in parts


def _running_from_tokenkick_pipx() -> bool:
    candidates = [sys.prefix, sys.executable]
    if sys.argv and sys.argv[0]:
        candidates.append(sys.argv[0])
        resolved_argv = shutil.which(sys.argv[0])
        if resolved_argv:
            candidates.append(resolved_argv)
    return any(_path_looks_like_tokenkick_pipx(candidate) for candidate in candidates)


def _pipx_upgrade_command() -> list[str] | None:
    pipx = shutil.which("pipx")
    if pipx is None or not _running_from_tokenkick_pipx():
        return None
    return [pipx, "upgrade", "tokenkick"]


def _run_visible_command(args: Sequence[str]) -> int:
    click.echo(click.style(f"$ {shlex.join(list(args))}", fg="blue"))
    try:
        completed = subprocess.run(list(args))
    except OSError as exc:
        click.echo(click.style(f"Command failed to start: {exc}", fg="red"))
        return 1
    if completed.returncode != 0:
        click.echo(click.style(f"Command exited with status {completed.returncode}.", fg="yellow"))
    return int(completed.returncode)


def _print_manual_upgrade_command() -> None:
    click.echo(
        "This TokenKick runtime does not look like a pipx-managed install, "
        "or pipx is not available on PATH."
    )
    click.echo("For pipx installs, run:")
    click.echo(click.style("pipx upgrade tokenkick", fg="blue"))
    click.echo(click.style("tk update", fg="blue"))


def _background_process_status_before_upgrade() -> dict[str, bool]:
    from . import cli as cli_module

    return cli_module._write_background_process_upgrade_state({
        "daemon": bool(cli_module._daemon_status_payload().get("running")),
        "telegram_remote": bool(cli_module._telegram_remote_status_payload().get("running")),
    })


def _clear_background_process_upgrade_state() -> None:
    from . import cli as cli_module

    cli_module._clear_background_process_upgrade_state()


def _restore_previously_running_background_processes(previous: dict[str, bool]) -> None:
    tk = _tk_subprocess_command()
    if previous.get("daemon"):
        _run_visible_command([tk, "daemon", "--background"])
    if previous.get("telegram_remote"):
        _run_visible_command([tk, "remote", "telegram", "--background"])


def _run_pipx_upgrade(*, update_after: bool) -> None:
    command = _pipx_upgrade_command()
    if command is None:
        _print_manual_upgrade_command()
        return
    previous = _background_process_status_before_upgrade()
    if _run_visible_command(command) != 0:
        _clear_background_process_upgrade_state()
        return
    if update_after:
        _run_visible_command([_tk_subprocess_command(), "update", "--yes"])
        _restore_previously_running_background_processes(previous)


def _status_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        action = _select(
            "Status view",
            [
                MenuChoice("Cached status", "cached"),
                MenuChoice("Refresh provider status", "refresh"),
                MenuChoice("Codex only", "codex"),
                MenuChoice("All accounts", "all"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        _invoke(
            ctx,
            cli_module.status,
            as_json=False,
            codex_only=action == "codex",
            show_all=action == "all",
            account_label=None,
            refresh=action == "refresh",
            verbose=False,
        )


def _kick_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = _load_visible_accounts(config)
        choices = [MenuChoice("All ready enabled accounts", "all")]
        choices.extend(_account_menu_choices(accounts, only_kickable=False))
        choices.append(MenuChoice("Advanced: force one account", "force"))
        choices.append(MenuChoice("Back", MENU_EXIT))
        action = _select("Kick target", choices)
        if action == MENU_EXIT:
            return
        if action == "all":
            if _confirm("Kick or smart-schedule all eligible enabled accounts?", default=False):
                _invoke(
                    ctx,
                    cli_module.kick,
                    label=None,
                    kick_all=True,
                    auto_mode=False,
                    force=False,
                    dry_run=False,
                    enable_label=None,
                    disable_label=None,
                )
            continue
        if action == "force":
            label = _select_account(accounts, message="Force-kick which account?", only_kickable=True)
            if not label:
                continue
            if _confirm(f'Force kick "{label}" now?', default=False):
                _invoke(
                    ctx,
                    cli_module.kick,
                    label=label,
                    kick_all=False,
                    auto_mode=False,
                    force=True,
                    dry_run=False,
                    enable_label=None,
                    disable_label=None,
                )
            continue
        account = _account_by_label(accounts, action)
        if account is None:
            continue
        if account.provider not in KICKABLE_PROVIDERS:
            click.echo(f'"{account.label}" is monitor-only and cannot be kicked.')
            continue
        if _confirm(f'Kick "{account.label}" now if eligible?', default=False):
            _invoke(
                ctx,
                cli_module.kick,
                label=account.label,
                kick_all=False,
                auto_mode=False,
                force=False,
                dry_run=False,
                enable_label=None,
                disable_label=None,
            )


def _configure_menu(ctx: click.Context) -> None:
    while True:
        action = _select(
            "Configure",
            [
                MenuChoice("Setup / rediscover accounts", "setup"),
                MenuChoice("Accounts", "accounts"),
                MenuChoice("Auto-kick", "auto"),
                MenuChoice("Codex surface strategy", "codex_settings"),
                MenuChoice("Notifications", "notifications"),
                MenuChoice("Remote status (Telegram)", "remote_telegram"),
                MenuChoice("Agent tools (MCP)", "mcp"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        _dispatch_action(ctx, action)


def _mcp_menu(ctx: click.Context) -> None:
    del ctx
    from .mcp_setup import MCPSetupError, MCPSetupManager

    while True:
        manager = MCPSetupManager()
        _print_mcp_status(manager.status(client="all"))
        action = _select(
            "Agent tools (MCP)",
            [
                MenuChoice("Show MCP status", "status"),
                MenuChoice("Install / repair Codex", "install-codex"),
                MenuChoice("Install / repair Claude Desktop (Mac app)", "install-claude-desktop"),
                MenuChoice("Install / repair Claude Code (CLI)", "install-claude-code"),
                MenuChoice("Remove TokenKick MCP config", "remove"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        try:
            if action == "status":
                _print_mcp_status(manager.status(client="all"))
                _select("MCP status", [MenuChoice("Back", MENU_EXIT)])
            elif action.startswith("install-"):
                client = action.removeprefix("install-")
                if not _confirm_mcp_action(f"Install or repair TokenKick MCP for {client}?", default=True):
                    continue
                result = manager.install(client=client, repair_only=False)
                _print_mcp_result(result)
            elif action == "remove":
                client = _select(
                    "Remove TokenKick MCP config from which client?",
                    [
                        MenuChoice("Codex", "codex"),
                        MenuChoice("Claude Desktop (Mac app)", "claude-desktop"),
                        MenuChoice("Claude Code (CLI)", "claude-code"),
                        MenuChoice("Back", MENU_EXIT),
                    ],
                )
                if client == MENU_EXIT:
                    continue
                if not _confirm_mcp_action(
                    f"Remove TokenKick MCP config from {client}?",
                    default=False,
                ):
                    continue
                result = manager.remove(client=client)
                _print_mcp_result(result)
        except MCPSetupError as exc:
            click.echo(click.style(str(exc), fg="red"))


def _confirm_mcp_action(message: str, *, default: bool) -> bool:
    choices = (
        [MenuChoice("Yes", "yes"), MenuChoice("No", "no"), MenuChoice("Back", MENU_EXIT)]
        if default
        else [MenuChoice("No", "no"), MenuChoice("Yes", "yes"), MenuChoice("Back", MENU_EXIT)]
    )
    answer = _select(message, choices, default="yes" if default else "no")
    return answer == "yes"


def _print_mcp_status(payload: dict[str, Any]) -> None:
    from rich.align import Align
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(width=120)
    title = Text()
    title.append("Token", style="bold white")
    title.append("Kick", style="bold green")
    title.append(" Agent Tools (MCP)", style="bold white")
    console.print(Align.center(title))

    table = Table(show_header=True)
    table.add_column("Client")
    table.add_column("State")
    table.add_column("Config")
    table.add_column("Action")
    for client in payload.get("clients", []):
        table.add_row(
            str(client.get("client_display") or client.get("client")),
            _mcp_state_display(str(client.get("state") or "unknown")),
            str(client.get("config_path") or client.get("config_method") or "-"),
            str(client.get("recommended_action") or client.get("message") or "-"),
        )
    console.print(table)


def _print_mcp_result(payload: dict[str, Any]) -> None:
    from rich.align import Align
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(width=120)
    title = Text()
    title.append("Token", style="bold white")
    title.append("Kick", style="bold green")
    title.append(" MCP Result", style="bold white")
    console.print(Align.center(title))

    table = Table(show_header=True)
    table.add_column("Client")
    table.add_column("Changed")
    table.add_column("State")
    table.add_column("Backup")
    table.add_column("Message")
    for client in payload.get("clients", []):
        table.add_row(
            str(client.get("client")),
            "yes" if client.get("changed") else "no",
            str(client.get("state") or client.get("status", {}).get("state") or "-"),
            str(client.get("backup_path") or "-"),
            str(client.get("message") or "-"),
        )
    console.print(table)


def _mcp_state_display(state: str) -> str:
    mapping = {
        "configured": "[green]✅ configured[/green]",
        "missing": "[red]❌ missing[/red]",
        "unsupported": "[dim]⚪ unsupported[/dim]",
        "needs_repair": "[yellow]⚠ needs repair[/yellow]",
        "malformed": "[yellow]⚠ malformed[/yellow]",
        "unknown": "[yellow]? unknown[/yellow]",
        "skipped": "[dim]· skipped[/dim]",
        "failed": "[red]❌ failed[/red]",
        "removed": "[green]✅ removed[/green]",
    }
    return mapping.get(state, state)


def _setup_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        action = _select(
            "Setup",
            [
                MenuChoice("Run account discovery", "run"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        _run_setup_discovery(ctx, cli_module)
        _setup_next_step_menu(ctx)


def _run_setup_discovery(ctx: click.Context, cli_module: Any) -> None:
    with _setup_progress_context(cli_module):
        _invoke(ctx, cli_module.setup, rename_labels=(), dry_run=False, no_daemon_prompt=True)


def _setup_next_step_menu(ctx: click.Context) -> None:
    while True:
        next_step = _select(
            "Next setup step",
            [
                MenuChoice("Review & enable auto-kick", "auto"),
                MenuChoice("Configure notifications", "notifications"),
                MenuChoice("Start background daemon", "daemon"),
                MenuChoice("Schedule & orchestration info", "schedule_info"),
                MenuChoice("Codex strategy info", "codex_info"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if next_step == "schedule_info":
            _print_setup_schedule_info()
        elif next_step == "codex_info":
            _print_setup_codex_strategy_info()
        elif next_step == MENU_EXIT:
            return
        elif next_step != MENU_EXIT:
            _dispatch_action(ctx, next_step)


@contextmanager
def _setup_progress_context(cli_module: Any) -> Iterator[None]:
    if not _setup_progress_live_enabled():
        yield
        return
    progress = _PhasedSetupProgress()
    previous = getattr(cli_module, "_SETUP_PROGRESS_CALLBACK", None)
    setattr(cli_module, "_SETUP_PROGRESS_CALLBACK", progress)
    try:
        with progress:
            yield
    finally:
        progress.finish()
        setattr(cli_module, "_SETUP_PROGRESS_CALLBACK", previous)


def _setup_progress_live_enabled() -> bool:
    try:
        return click.get_text_stream("stdout").isatty()
    except Exception:
        return False


class _PhasedSetupProgress:
    _spinner_frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self) -> None:
        self._seen: list[str] = []
        self._completed: set[str] = set()
        self._current: str | None = None
        self._live: Any | None = None
        self._started_at = time.monotonic()
        self._stopped = False

    def __enter__(self) -> "_PhasedSetupProgress":
        from rich.live import Live

        self._live = Live(
            self,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.finish()

    def __call__(self, message: str | None) -> None:
        if message is None:
            self.finish()
            return
        if self._current and self._current != message:
            self._completed.add(self._current)
        self._current = message
        if message not in self._seen:
            self._seen.append(message)
        self._refresh()

    def __rich_console__(self, _console: Any, _options: Any) -> Any:
        from rich.text import Text

        yield Text("Discovering accounts", style="bold")
        yield Text("This can take 30-60s while TokenKick checks local accounts and provider status.", style="dim")
        phases = [phase for phase in SETUP_DISCOVERY_PHASES if phase in self._seen]
        for phase in phases:
            if phase in self._completed:
                yield Text(f"✓ {phase}", style="green")
            elif phase == self._current:
                yield Text(f"{self._spinner_frame()} {phase}", style="cyan")
            else:
                yield Text(f"· {phase}", style="dim")
        if self._stopped:
            yield Text("✓ Account discovery complete", style="green")

    def finish(self) -> None:
        if self._stopped:
            return
        if self._current:
            self._completed.add(self._current)
        self._current = None
        self._stopped = True
        self._refresh()
        if self._live is not None:
            self._live.stop()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self, refresh=True)

    def _spinner_frame(self) -> str:
        elapsed = max(0.0, time.monotonic() - self._started_at)
        return self._spinner_frames[int(elapsed * 8) % len(self._spinner_frames)]


def _print_setup_schedule_info() -> None:
    _print_setup_info_panel(
        "Schedule & Orchestration",
        [
            ("Smart schedule", "Recurring work windows."),
            ("Orchestration plan", "Preview/apply multi-account coverage for a specific work window."),
            ("Where", "Main menu -> Schedule."),
        ],
    )


def _print_setup_codex_strategy_info() -> None:
    _print_setup_info_panel(
        "Codex Surface Strategy",
        [
            ("Burst ladder", "Fast serialized Codex surfaces."),
            ("Surface order", "Choose which surfaces are tried, and in what order."),
            ("Auto-demotion", "Optional per-account pruning of redundant surfaces."),
            ("Safe default", "Leave unchanged unless you want advanced Codex tuning."),
        ],
    )


def _print_setup_info_panel(title: str, rows: list[tuple[str, str]]) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="white")
    for label, description in rows:
        table.add_row(label, description)
    Console(width=100).print(Panel(table, title=title, border_style="cyan"))


def _accounts_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = config.accounts
        if not accounts:
            click.echo("No saved accounts. Run setup after logging in.")
            return
        action = _select(
            "Accounts",
            [
                MenuChoice("Show account status", "status"),
                MenuChoice("Show account notification routes", "notifications"),
                MenuChoice("Planning defaults", "planning_defaults"),
                MenuChoice("Orchestration roles", "orchestration_roles"),
                MenuChoice("Hide selected accounts", "hide_selected"),
                MenuChoice("Show selected accounts", "show_selected"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(ctx, cli_module.accounts_list)
            continue
        if action == "notifications":
            _invoke(ctx, cli_module.accounts_notifications)
            continue
        if action == "planning_defaults":
            _account_planning_defaults_menu(ctx)
            continue
        if action == "orchestration_roles":
            _account_orchestration_roles_menu(ctx)
            continue
        if action == "hide_selected":
            selected = _checkbox(
                "Choose account(s) with Space, then Enter",
                [
                    *_account_menu_choices([account for account in accounts if account.visible], only_kickable=False),
                    MenuChoice("Back", MENU_EXIT),
                ],
            )
            if MENU_EXIT in selected:
                continue
            if not selected:
                click.echo(
                    click.style(
                        "No accounts selected; press Space to select account(s), then Enter.",
                        fg="yellow",
                    )
                )
                continue
            selected_names = ", ".join(selected)
            if _confirm(f"Hide {len(selected)} account(s): {selected_names}?", default=False):
                for label in selected:
                    _invoke(ctx, cli_module.accounts_hide, label=label)
            continue
        if action == "show_selected":
            selected = _checkbox(
                "Choose account(s) with Space, then Enter",
                [
                    *_account_menu_choices([account for account in accounts if not account.visible], only_kickable=False),
                    MenuChoice("Back", MENU_EXIT),
                ],
            )
            if MENU_EXIT in selected:
                continue
            if not selected:
                click.echo(
                    click.style(
                        "No accounts selected; press Space to select account(s), then Enter.",
                        fg="yellow",
                    )
                )
                continue
            selected_names = ", ".join(selected)
            if _confirm(f"Show {len(selected)} account(s): {selected_names}?", default=True):
                for label in selected:
                    _invoke(ctx, cli_module.accounts_show, label=label)


def _account_planning_defaults_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = [
            account
            for account in config.accounts
            if account.visible and account.provider in KICKABLE_PROVIDERS
        ]
        if not accounts:
            click.echo("No visible kickable accounts found.")
            return
        _print_account_planning_defaults(config, accounts)
        action = _select(
            "Planning defaults",
            [
                MenuChoice("Set account planning default", "set"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        label = _select_account(
            accounts,
            message="Set planning default for which account?",
            only_kickable=True,
        )
        if not label:
            continue
        account = _account_by_label(accounts, label)
        if account is None:
            continue
        current_minutes = usable_session_minutes_for_account(account, config)
        minutes = _pick_planning_minutes(
            f'Planning default for "{account.label}"',
            current_minutes,
            cli_module,
        )
        if minutes is None:
            continue
        if minutes == account.usable_session_minutes:
            click.echo(click.style("Planning default unchanged.", fg="yellow"))
            continue
        _invoke(ctx, cli_module.accounts_set_usable, label=account.label, minutes=minutes)


def _print_account_planning_defaults(config: Config, accounts: list[AccountConfig]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="TokenKick Account Planning Defaults", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Planning default")
    table.add_column("Source")

    for account in accounts:
        minutes = usable_session_minutes_for_account(account, config)
        source = "saved account setting" if account.usable_session_minutes is not None else "tier/default fallback"
        table.add_row(
            account.label,
            account.provider,
            f"{minutes}m ({_format_usage_minutes(minutes)})",
            source,
        )

    Console(width=100).print(table)


def _pick_planning_minutes(message: str, current_minutes: int, cli_module: Any) -> int | None:
    while True:
        selected = _select(
            message,
            _usage_choice_options(current_minutes, keep_label="Keep current"),
            default="default",
        )
        if selected in {MENU_EXIT, "default"}:
            return None
        if selected == "custom":
            raw_value = _text(
                message,
                default=str(current_minutes),
            )
            if not raw_value:
                return None
            try:
                return cli_module._parse_plan_usage_duration_minutes(raw_value)
            except click.ClickException as exc:
                click.echo(click.style(str(exc), fg="red"))
            continue
        return int(selected)


def _account_orchestration_roles_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = config.accounts
        if not accounts:
            click.echo("No saved accounts. Run setup after logging in.")
            return
        _print_account_orchestration_roles(config, accounts, cli_module)
        action = _select(
            "Orchestration roles",
            [
                MenuChoice("Edit account role", "role"),
                MenuChoice("Edit weekly reserve threshold", "threshold"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        label = _select_account(
            accounts,
            message="Configure which account?",
            only_kickable=False,
        )
        if not label:
            continue
        account = _account_by_label(accounts, label)
        if account is None:
            continue
        if action == "role":
            _edit_account_orchestration_role(ctx, account, accounts, cli_module)
        elif action == "threshold":
            _edit_account_weekly_reserve_threshold(ctx, account, cli_module)


def _print_account_orchestration_roles(
    config: Config,
    accounts: list[AccountConfig],
    cli_module: Any,
) -> None:
    from rich.console import Console
    from rich.table import Table

    cached = cli_module._load_status_cache(config)
    statuses_by_key: dict[str, AccountStatus] = {}
    if cached is not None:
        cached_accounts, statuses, _entries = cached
        statuses_by_key = {
            account_key_string(account): status
            for account, status in zip(cached_accounts, statuses, strict=False)
        }

    table = Table(title="TokenKick Account Orchestration Roles", show_header=True)
    table.add_column("Account", style="bold")
    table.add_column("Provider")
    table.add_column("Visible")
    table.add_column("Auto/session")
    table.add_column("Expected usage")
    table.add_column("Role")
    table.add_column("Effective")
    table.add_column("Reserve")

    for account in accounts:
        status = statuses_by_key.get(account_key_string(account))
        usage_minutes = usable_session_minutes_for_account(account, config)
        threshold = (
            f"{account.weekly_reserve_threshold_percent}%"
            if account.weekly_reserve_threshold_percent is not None
            else "-"
        )
        table.add_row(
            account.label,
            account.provider,
            "yes" if account.visible else "no",
            _account_auto_session_label(account),
            f"{usage_minutes}m",
            _display_role(account.orchestration_role),
            _display_role(effective_orchestration_role(account, status)),
            threshold,
        )

    Console(width=120).print(table)


def _edit_account_orchestration_role(
    ctx: click.Context,
    account: AccountConfig,
    accounts: list[AccountConfig],
    cli_module: Any,
) -> None:
    current = account.orchestration_role
    choices = [
        MenuChoice(f"Keep current ({_display_role(current)})", "default"),
        *[
            MenuChoice(_display_role(role), role)
            for role in ("use_first", "normal", "backup", "specialist", "excluded")
            if role != current
        ],
        MenuChoice("Back", MENU_EXIT),
    ]
    selected = _select(
        f'Orchestration role for "{account.label}"',
        choices,
        default="default",
    )
    if selected in {MENU_EXIT, "default"}:
        return
    demoted_labels = [
        candidate.label
        for candidate in accounts
        if selected == "use_first"
        and candidate.label != account.label
        and candidate.orchestration_role == "use_first"
    ]
    confirm_message = (
        f'Save orchestration role for "{account.label}" as {_display_role(selected)}?'
    )
    if demoted_labels:
        confirm_message += (
            " This will demote "
            + ", ".join(f'"{label}"' for label in demoted_labels)
            + " to Normal."
        )
    if _confirm(
        confirm_message,
        default=True,
    ):
        _invoke(ctx, cli_module.accounts_set_role, label=account.label, role=selected)


def _edit_account_weekly_reserve_threshold(
    ctx: click.Context,
    account: AccountConfig,
    cli_module: Any,
) -> None:
    current = account.weekly_reserve_threshold_percent
    current_label = f"{current}%" if current is not None else "none"
    action = _select(
        f'Weekly reserve threshold for "{account.label}"',
        [
            MenuChoice(f"Keep current ({current_label})", "default"),
            MenuChoice("Set threshold", "set"),
            MenuChoice("Clear threshold", "clear"),
            MenuChoice("Back", MENU_EXIT),
        ],
        default="default",
    )
    if action in {MENU_EXIT, "default"}:
        return
    if action == "clear":
        if current is None:
            click.echo(click.style("Weekly reserve threshold already clear.", fg="yellow"))
            return
        if _confirm(f'Clear weekly reserve threshold for "{account.label}"?', default=False):
            _invoke(ctx, cli_module.accounts_clear_weekly_reserve, label=account.label)
        return

    selected = _select(
        f'Set weekly reserve threshold for "{account.label}"',
        [
            MenuChoice("Keep current", "default"),
            *[MenuChoice(f"{threshold}%", str(threshold)) for threshold in (50, 60, 70, 80, 90)],
            MenuChoice("Custom percent", "custom"),
            MenuChoice("Back", MENU_EXIT),
        ],
        default=str(current) if current in {50, 60, 70, 80, 90} else "default",
    )
    if selected in {MENU_EXIT, "default"}:
        return
    if selected == "custom":
        raw_value = _text(
            f'Custom weekly reserve threshold for "{account.label}"',
            default=str(current or 70),
        )
        if not raw_value:
            return
        try:
            threshold = int(raw_value)
        except ValueError:
            click.echo(click.style("Weekly reserve threshold must be a number from 1 to 99.", fg="red"))
            return
    else:
        threshold = int(selected)
    if not 1 <= threshold <= 99:
        click.echo(click.style("Weekly reserve threshold must be between 1 and 99.", fg="red"))
        return
    _invoke(ctx, cli_module.accounts_set_weekly_reserve, label=account.label, threshold=threshold)


def _account_auto_session_label(account: AccountConfig) -> str:
    if account.provider not in KICKABLE_PROVIDERS:
        return "monitor-only"
    auto = "yes" if account.auto_kick else "no"
    session = "yes" if account.session_auto_kick else "no"
    return f"{auto}/{session}"


def _display_role(role: str) -> str:
    labels = {
        "use_first": "Use first",
        "normal": "Normal",
        "backup": "Backup",
        "specialist": "Specialist",
        "excluded": "Excluded",
    }
    return labels.get(role, role)


def _auto_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = [account for account in _load_visible_accounts(config) if account.provider in KICKABLE_PROVIDERS]
        status_by_label = _auto_menu_status_by_label(config, cli_module)
        if not accounts:
            click.echo("No kickable accounts found.")
            return
        action = _select(
            "Auto-kick",
            [
                MenuChoice("Show auto-kick status", "status"),
                MenuChoice("Enable all usable accounts", "enable_all"),
                MenuChoice("Disable all visible kickable accounts", "disable_all"),
                MenuChoice("Enable selected accounts", "enable_selected"),
                MenuChoice("Disable selected accounts", "disable_selected"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(ctx, cli_module.auto_status)
            continue
        if action.endswith("_selected"):
            selected = _checkbox(
                "Choose account(s)",
                [*_account_menu_choices(accounts, only_kickable=True), MenuChoice("Back", MENU_EXIT)],
            )
            if not selected:
                continue
            if MENU_EXIT in selected:
                continue
            skipped: list[tuple[AccountConfig, str]] = []
        elif action == "enable_all":
            usable_accounts, skipped = _usable_auto_enable_accounts(accounts, status_by_label)
            selected = [account.label for account in usable_accounts]
            if not selected:
                click.echo("No usable accounts found for bulk auto-kick enable.")
                _print_auto_enable_skipped(skipped)
                continue
        else:
            selected = [account.label for account in accounts]
            skipped = []
        enabled = action.startswith("enable")
        scope = _select(
            "Window scope",
            [
                MenuChoice("All windows", "all"),
                MenuChoice("Weekly only", "weekly"),
                MenuChoice("Session only", "session"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if scope == MENU_EXIT:
            continue
        operation = "enable" if enabled else "disable"
        summary = f"{operation} {scope} auto-kick for {len(selected)} account(s)?"
        if not _confirm(summary, default=enabled):
            continue
        for label in selected:
            if scope == "all":
                command = cli_module.auto_enable if enabled else cli_module.auto_disable
            elif scope == "weekly":
                command = (
                    cli_module.auto_weekly_enable
                    if enabled
                    else cli_module.auto_weekly_disable
                )
            else:
                command = (
                    cli_module.auto_session_enable
                    if enabled
                    else cli_module.auto_session_disable
                )
            _invoke(ctx, command, label=label)
        if enabled and action == "enable_all":
            _print_auto_enable_skipped(skipped)


def _auto_menu_status_by_label(config: Config, cli_module: Any) -> dict[str, AccountStatus]:
    cached = cli_module._load_status_cache(config)
    if cached is None:
        return {}
    _accounts, statuses, _entries = cached
    return {status.label: status for status in statuses}


def _usable_auto_enable_accounts(
    accounts: list[AccountConfig],
    status_by_label: dict[str, AccountStatus],
) -> tuple[list[AccountConfig], list[tuple[AccountConfig, str]]]:
    usable: list[AccountConfig] = []
    skipped: list[tuple[AccountConfig, str]] = []
    for account in accounts:
        status = status_by_label.get(account.label)
        reason = _auto_enable_skip_reason(status)
        if reason is None:
            usable.append(account)
        else:
            skipped.append((account, reason))
    return usable, skipped


def _auto_enable_skip_reason(status: AccountStatus | None) -> str | None:
    if status is None:
        return "no cached status; run tk status --refresh, then enable manually if it looks correct"
    if status.stale:
        return "stale status; run tk status --refresh after re-auth or opening the provider"
    if status.state == AccountState.UNKNOWN:
        return _trim_auto_enable_reason(status.error or "status unknown")
    if status.error:
        return _trim_auto_enable_reason(status.error)
    return None


def _trim_auto_enable_reason(reason: str) -> str:
    reason = " ".join(reason.split())
    return f"{reason[:157]}..." if len(reason) > 160 else reason


def _print_auto_enable_skipped(skipped: list[tuple[AccountConfig, str]]) -> None:
    if not skipped:
        return
    click.echo("Skipped auto-kick enable for accounts that are not currently usable:")
    for account, reason in skipped:
        click.echo(f'- {account.label}: {reason}')
    click.echo(
        'Re-auth the account, then run tk status --refresh; or use tk auto disable "<label>" '
        'or tk accounts hide "<label>".'
    )


def _codex_settings_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        action = _select(
            "Codex surface strategy",
            [
                MenuChoice("Strategy status", "status"),
                MenuChoice("Enable burst ladder", "enable"),
                MenuChoice("Disable burst ladder (patient adaptive ladder)", "disable"),
                MenuChoice("Burst surface order/subset", "order"),
                MenuChoice("Burst inter-surface gap", "gap"),
                MenuChoice("Surface demotion", "demotion"),
                MenuChoice("Advanced surface stats", "surface_stats"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(ctx, cli_module.codex_strategy_status, as_json=False)
            continue
        if action == "enable":
            if _confirm("Enable Codex burst ladder for auto/scheduled Codex kicks?", default=False):
                _invoke(ctx, cli_module.codex_strategy_enable)
            continue
        if action == "disable":
            if _confirm("Disable burst ladder and use the patient adaptive ladder?", default=False):
                _invoke(ctx, cli_module.codex_strategy_disable)
            continue
        if action == "order":
            current_order = cli_module._codex_burst_ladder_surface_order(config)
            surfaces = pick_codex_surface_order(
                current_order,
                cli_module.CODEX_FIRE_ALL_DEFAULT_SURFACES,
            )
            if surfaces is None:
                click.echo(click.style("Burst ladder surface order unchanged.", fg="yellow"))
                continue
            if surfaces == CODEX_SURFACE_ORDER_RESET:
                if _confirm("Reset Codex burst ladder surface order to default?", default=False):
                    _invoke(ctx, cli_module.codex_strategy_order, surfaces=(), reset_order=True)
                continue
            if surfaces == tuple(current_order):
                click.echo(click.style("Burst ladder surface order already matches saved order.", fg="yellow"))
                continue
            if _confirm(f"Save burst ladder order: {', '.join(surfaces)}?", default=True):
                _invoke(ctx, cli_module.codex_strategy_order, surfaces=surfaces, reset_order=False)
            continue
        if action == "demotion":
            _codex_surface_demotion_menu(ctx)
            continue
        if action == "surface_stats":
            _codex_surface_stats_menu(ctx)
            continue
        if action != "gap":
            continue
        raw_gap = _text_action("Burst inter-surface gap seconds", default=str(config.codex_burst_ladder_gap_seconds))
        if raw_gap is None:
            continue
        try:
            seconds = float(raw_gap)
        except ValueError:
            click.echo(click.style("Gap must be a number.", fg="red"))
            continue
        if _confirm(f"Set burst ladder gap to {seconds:.0f}s?", default=True):
            _invoke(ctx, cli_module.codex_strategy_gap, seconds=seconds)


def _codex_surface_demotion_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = [
            account
            for account in _load_visible_accounts(config)
            if account.provider == "codex"
        ]
        action = _select(
            "Surface demotion",
            [
                MenuChoice("Show demotion evidence for one account", "show"),
                MenuChoice("Enable auto-demotion for all", "enable_all"),
                MenuChoice("Disable auto-demotion for all", "disable_all"),
                MenuChoice("Enable auto-demotion for one account", "enable"),
                MenuChoice("Disable auto-demotion for one account", "disable"),
                MenuChoice("Force-keep surfaces for one account", "force_keep"),
                MenuChoice("Force-prune surfaces for one account", "force_prune"),
                MenuChoice("Clear force overrides for one account", "clear"),
                MenuChoice("Reset demotion evidence for one account", "reset"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "enable_all":
            if _confirm("Enable surface auto-demotion for all Codex accounts?", default=False):
                count = cli_module._set_all_codex_surface_demotion_config(True)
                click.echo(click.style(f"Codex surface auto-demotion enabled for {count} Codex accounts.", fg="green"))
            continue
        elif action == "disable_all":
            if _confirm("Disable surface auto-demotion for all Codex accounts?", default=False):
                count = cli_module._set_all_codex_surface_demotion_config(False)
                click.echo(click.style(f"Codex surface auto-demotion disabled for {count} Codex accounts.", fg="green"))
            continue
        if action == "show":
            label = _select_account(accounts, message="Show demotion evidence for which account?", only_kickable=False)
            if label:
                ctx.ensure_object(dict)
                ctx.obj["codex_surfaces_label"] = label
                _invoke(ctx, cli_module.codex_surfaces_demotion_evidence, as_json=False)
            continue
        label = _select_account(accounts, message="Configure surface demotion for which account?", only_kickable=False)
        if not label:
            continue
        account = next((candidate for candidate in accounts if candidate.label == label), None)
        if account is None:
            continue
        if action == "enable":
            if _confirm(f'Enable surface auto-demotion for "{label}"?', default=False):
                cli_module._set_codex_surface_demotion_config(label, codex_surface_auto_demote=True)
                click.echo(click.style(f'Codex surface auto-demotion enabled for "{label}".', fg="green"))
        elif action == "disable":
            if _confirm(f'Disable surface auto-demotion for "{label}"?', default=False):
                cli_module._set_codex_surface_demotion_config(label, codex_surface_auto_demote=False)
                click.echo(click.style(f'Codex surface auto-demotion disabled for "{label}".', fg="green"))
        elif action == "force_keep":
            surfaces = pick_codex_surface_order(
                tuple(account.codex_surface_force_keep or cli_module.CODEX_FIRE_ALL_DEFAULT_SURFACES),
                cli_module.CODEX_FIRE_ALL_DEFAULT_SURFACES,
            )
            if surfaces and surfaces != CODEX_SURFACE_ORDER_RESET:
                if _confirm(f"Force-keep surfaces: {', '.join(surfaces)}?", default=True):
                    cli_module._set_codex_surface_demotion_config(
                        label,
                        codex_surface_force_keep=list(surfaces),
                    )
                    click.echo(click.style(f'Force-keep set for "{label}".', fg="green"))
        elif action == "force_prune":
            surfaces = pick_codex_surface_order(
                tuple(account.codex_surface_force_prune or ()),
                cli_module.CODEX_FIRE_ALL_DEFAULT_SURFACES,
            )
            if surfaces and surfaces != CODEX_SURFACE_ORDER_RESET:
                click.echo(
                    click.style(
                        "Force-pruned surfaces are not auto-reintroduced on a miss.",
                        fg="yellow",
                    )
                )
                if len(surfaces) > len(cli_module.CODEX_FIRE_ALL_DEFAULT_SURFACES) - 2:
                    click.echo(
                        click.style(
                            "This leaves fewer than 2 active surfaces; a miss has no automatic rescue path.",
                            fg="red",
                        )
                    )
                if _confirm(f"Force-prune surfaces: {', '.join(surfaces)}?", default=False):
                    cli_module._set_codex_surface_demotion_config(
                        label,
                        codex_surface_force_prune=list(surfaces),
                    )
                    click.echo(click.style(f'Force-prune set for "{label}".', fg="green"))
        elif action == "clear":
            if _confirm(f'Clear force overrides for "{label}"?', default=True):
                cli_module._set_codex_surface_demotion_config(
                    label,
                    codex_surface_force_keep=[],
                    codex_surface_force_prune=[],
                )
                click.echo(click.style(f'Force overrides cleared for "{label}".', fg="green"))
        elif action == "reset":
            if _confirm(f'Reset demotion evidence for "{label}"?', default=False):
                account = next((candidate for candidate in _load_visible_accounts(Config.load()) if candidate.label == label), None)
                if account is not None:
                    cli_module.reset_codex_surface_demotion_evidence(
                        cli_module._codex_surface_stats_file(),
                        account,
                    )
                    click.echo(click.style(f'Demotion evidence reset for "{label}".', fg="green"))


def _codex_surface_stats_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = [
            account
            for account in _load_visible_accounts(config)
            if account.provider == "codex"
        ]
        action = _select(
            "Advanced surface stats",
            [
                MenuChoice("Show surface stats for one account", "show"),
                MenuChoice("Reset learned surface stats for one account", "reset_one"),
                MenuChoice("Reset learned surface stats for all Codex accounts", "reset_all"),
                MenuChoice("Reset learned stats + demotion evidence for one account", "reset_account_all"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "show":
            label = _select_account(accounts, message="Show surface stats for which account?", only_kickable=False)
            if label:
                _invoke(ctx, cli_module.codex_surfaces, label=label, as_json=False)
            continue
        if action == "reset_all":
            if _confirm(
                "Reset learned surface stats for all Codex accounts? Kick history, demotion settings, force overrides, "
                "and demotion evidence stay unchanged.",
                default=False,
            ):
                for account in accounts:
                    cli_module.reset_codex_surface_learning_stats(
                        cli_module._codex_surface_stats_file(),
                        account,
                    )
                click.echo(click.style(f"Learned surface stats reset for {len(accounts)} Codex accounts.", fg="green"))
            continue
        label = _select_account(accounts, message="Reset surface stats for which account?", only_kickable=False)
        if not label:
            continue
        account = next((candidate for candidate in accounts if candidate.label == label), None)
        if account is None:
            continue
        if action == "reset_one":
            if _confirm(
                "This clears learned surface scores/order for this account. It does not delete kick history and does "
                "not change demotion settings, force-keep/prune overrides, or demotion evidence. Continue?",
                default=False,
            ):
                cli_module.reset_codex_surface_learning_stats(
                    cli_module._codex_surface_stats_file(),
                    account,
                )
                click.echo(click.style(f'Learned surface stats reset for "{label}".', fg="green"))
        elif action == "reset_account_all":
            if _confirm(
                "This clears learned surface scores/order and demotion evidence for this account. It does not delete "
                "kick history and does not change demotion settings or force-keep/prune overrides. Continue?",
                default=False,
            ):
                cli_module.reset_codex_surface_learning_stats(
                    cli_module._codex_surface_stats_file(),
                    account,
                )
                cli_module.reset_codex_surface_demotion_evidence(
                    cli_module._codex_surface_stats_file(),
                    account,
                )
                click.echo(click.style(f'Surface stats and demotion evidence reset for "{label}".', fg="green"))


def _schedule_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        choices = [
            MenuChoice("Orchestration plan      Plan multi-account coverage for a work window", "orchestration"),
        ]
        if cli_module._orchestrated_pending_kicks(now=datetime.now(timezone.utc)):
            choices.append(MenuChoice("Cancel orchestration plan", "cancel_orchestration"))
        choices.extend(
            [
                MenuChoice("Smart schedule          Configure recurring smart kick windows", "smart"),
                MenuChoice("Schedule status         Show configured smart schedules", "status"),
                MenuChoice("Back", MENU_EXIT),
            ]
        )
        action = _select(
            "Schedule",
            choices,
        )
        if action == MENU_EXIT:
            return
        if action == "orchestration":
            _orchestration_plan_menu()
            continue
        if action == "cancel_orchestration":
            _orchestration_cancel_menu()
            continue
        if action == "smart":
            _smart_schedule_menu(ctx)
            continue
        if action == "status":
            _invoke(ctx, cli_module.schedule_show, account=None)


def _orchestration_plan_menu() -> None:
    while _orchestration_plan_flow():
        pass


def _orchestration_cancel_menu() -> None:
    from . import cli as cli_module

    while True:
        now = datetime.now(timezone.utc)
        pending = cli_module._orchestrated_pending_kicks(now=now)
        if not pending:
            click.echo(click.style("No applied orchestration pending kicks found.", fg="yellow"))
            return
        cli_module._render_orchestrated_pending_kicks(pending, title="Applied orchestration pending kicks")
        action = _select(
            "Cancel orchestration plan",
            [
                MenuChoice("Cancel all orchestration pending kicks", "all"),
                MenuChoice("Cancel selected accounts", "selected"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "all":
            if not _confirm(f"Cancel {len(pending)} orchestration pending kick(s)?", default=False):
                continue
            result = cancel_orchestrated_pending_kicks(now=datetime.now(timezone.utc))
            click.echo(click.style(f"Cancelled {len(result.removed)} orchestration pending kick(s).", fg="green"))
            continue

        account_labels = sorted({item.account_label for item in pending})
        selected = _checkbox(
            "Choose account(s)",
            [MenuChoice(label, label) for label in account_labels],
        )
        if selected == [MENU_EXIT]:
            continue
        if not selected:
            click.echo(click.style("No accounts selected.", fg="yellow"))
            continue
        selected_pending_count = sum(1 for item in pending if item.account_label in set(selected))
        if not _confirm(
            f"Cancel {selected_pending_count} orchestration pending kick(s)?",
            default=False,
        ):
            continue
        result = cancel_orchestrated_pending_kicks(
            account_labels=set(selected),
            now=datetime.now(timezone.utc),
        )
        click.echo(click.style(f"Cancelled {len(result.removed)} orchestration pending kick(s).", fg="green"))


def _orchestration_plan_flow(*, recovery_event_id: str | None = None) -> bool:
    from . import cli as cli_module

    while True:
        config = Config.load()
        tz = local_timezone(config.schedule)
        today = datetime.now(tz).date()
        date_choice = _select(
            "Orchestration date",
            [
                MenuChoice(f"Today ({today.isoformat()})", "today"),
                MenuChoice(f"Tomorrow ({(today + timedelta(days=1)).isoformat()})", "tomorrow"),
                MenuChoice("Custom date", "custom"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if date_choice == MENU_EXIT:
            return False
        if date_choice == "today":
            plan_date = today
        elif date_choice == "tomorrow":
            plan_date = today + timedelta(days=1)
        else:
            raw_date = _text_action("Plan date (YYYY-MM-DD)", default=today.isoformat())
            if raw_date is None:
                continue
            try:
                plan_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                click.echo(click.style("Invalid date: use YYYY-MM-DD.", fg="red"))
                continue

        window = pick_work_window(title="Orchestration work window", base_date=plan_date)
        if not window:
            continue
        usage_overrides = _orchestration_usage_overrides(config, cli_module)
        if usage_overrides is None:
            continue
        try:
            plan, now = cli_module._build_plan_from_options(
                work_window=window,
                date_text=plan_date.isoformat(),
                timezone_text=None,
                usage_overrides=usage_overrides,
            )
        except click.ClickException as exc:
            click.echo(click.style(str(exc), fg="red"))
            continue

        cli_module._render_plan(plan)
        if recovery_event_id:
            cli_module.record_reset_event_recovery_action(
                recovery_event_id,
                "orchestration_previewed",
            )
        refreshed = _maybe_refresh_stale_specialist_orchestration_plan(
            plan,
            cli_module,
            work_window=window,
            date_text=plan_date.isoformat(),
            usage_overrides=usage_overrides,
        )
        if refreshed is None:
            return False
        if refreshed[0] is not plan:
            plan, now = refreshed
            cli_module._render_plan(plan)
        if not plan.planned_kicks:
            return True
        if plan.diff.conflicts_unmanaged:
            click.echo(click.style("not applied; resolve unmanaged pending-kick conflicts first", fg="yellow"))
            return True
        if not _confirm("Apply this orchestration plan?", default=False):
            return True
        applied = cli_module.apply_orchestration_plan(
            plan,
            now=now,
            current_time=cli_module._status_cache_now().astimezone(timezone.utc),
        )
        cli_module._render_plan(applied)
        cli_module._render_current_reservation_advisories()
        if recovery_event_id and applied.applied:
            cli_module.record_reset_event_recovery_action(
                recovery_event_id,
                "orchestration_applied",
            )
        return True


def _maybe_refresh_stale_specialist_orchestration_plan(
    plan,
    cli_module,
    *,
    work_window: str,
    date_text: str,
    usage_overrides: tuple[str, ...],
):
    labels = _stale_specialist_skip_labels(plan)
    if not labels:
        return plan, None
    label_text = ", ".join(labels)
    action = _select(
        f"{label_text} status is stale and could not be prepared. Refresh provider status and rebuild the plan?",
        [
            MenuChoice("Refresh and rebuild", "refresh"),
            MenuChoice("Continue without specialist", "continue"),
            MenuChoice("Back", MENU_EXIT),
        ],
        default="refresh",
    )
    if action == MENU_EXIT:
        return None
    if action != "refresh":
        return plan, None
    click.echo(click.style("Refreshing provider status...", fg="cyan"))
    try:
        with cli_module.claude_cli_usage_refresh_allowed():
            cli_module._refresh_status_cache_fast(Config.load())
        refreshed_plan, refreshed_now = cli_module._build_plan_from_options(
            work_window=work_window,
            date_text=date_text,
            timezone_text=None,
            usage_overrides=usage_overrides,
        )
    except Exception as exc:  # pragma: no cover - defensive TUI guard
        click.echo(click.style(f"Refresh failed: {exc}", fg="red"))
        return plan, None
    return refreshed_plan, refreshed_now


def _stale_specialist_skip_labels(plan) -> list[str]:
    specialist_keys = {
        str(row.get("account_key"))
        for row in getattr(plan, "accounts_considered", [])
        if row.get("effective_orchestration_role") == "specialist"
    }
    labels: list[str] = []
    for item in getattr(plan, "skipped_accounts", []):
        if (
            getattr(item, "reason", None) == "stale_status"
            and getattr(item, "account_key", None) in specialist_keys
        ):
            labels.append(getattr(item, "account_label", "specialist"))
    return labels


def _orchestration_usage_overrides(config: Config, cli_module) -> tuple[str, ...] | None:
    accounts = _orchestration_usage_accounts(config)
    choice = _select(
        "Usage assumptions:",
        [
            MenuChoice(_usage_defaults_menu_label(accounts, config), "default"),
            MenuChoice("Custom", "custom"),
            MenuChoice("Back", MENU_EXIT),
        ],
        default="default",
    )
    if choice == MENU_EXIT:
        return None
    if choice != "custom":
        return ()

    entries: list[str] = []
    for account in accounts:
        default_minutes = usable_session_minutes_for_account(account, config)
        while True:
            selected = _select(
                f'Expected usage for "{account.label}"',
                _usage_choice_options(default_minutes),
                default="default",
            )
            if selected == MENU_EXIT:
                return None
            if selected == "default":
                break
            if selected == "custom":
                raw_value = _text(
                    f'Custom expected usage for "{account.label}"',
                    default=str(default_minutes),
                )
                if not raw_value:
                    break
                try:
                    minutes = cli_module._parse_plan_usage_duration_minutes(raw_value)
                except click.ClickException as exc:
                    click.echo(click.style(str(exc), fg="red"))
                    continue
            else:
                minutes = int(selected)
            if minutes != default_minutes:
                entries.append(f"{account.label}={minutes}m")
            break
    return tuple(entries)


def _orchestration_usage_accounts(config: Config) -> list[AccountConfig]:
    return [
        account
        for account in config.accounts
        if account.visible
        and account.provider in KICKABLE_PROVIDERS
        and account.auto_kick
        and account.session_auto_kick
    ]


def _usage_defaults_menu_label(
    accounts: Sequence[AccountConfig],
    config: Config,
    *,
    max_items: int = 4,
) -> str:
    if not accounts:
        return "Default"
    items = [
        f"{_usage_summary_account_name(account, accounts)} "
        f"{_format_usage_minutes(usable_session_minutes_for_account(account, config))}"
        for account in accounts[:max_items]
    ]
    remaining = len(accounts) - max_items
    if remaining > 0:
        items.append(f"+{remaining} more")
    return f"Default ({', '.join(items)})"


def _usage_summary_account_name(account: AccountConfig, accounts: Sequence[AccountConfig]) -> str:
    owner = _account_label_owner_component(account)
    owner_label = _compact_owner_label(owner or account.label)
    if account.provider == "claude":
        claude_accounts = [candidate for candidate in accounts if candidate.provider == "claude"]
        if len(claude_accounts) == 1:
            return "Claude"
        return f"Claude {owner_label}"
    if account.provider == "codex-spark":
        return f"Spark {owner_label}"
    return owner_label


def _account_label_owner_component(account: AccountConfig) -> str | None:
    prefix = f"{account.provider} ("
    if account.label.startswith(prefix) and account.label.endswith(")"):
        return account.label[len(prefix):-1].strip()
    return None


def _compact_owner_label(value: str) -> str:
    aliases = {
        "personal": "Personal",
        "work": "Work",
        "reserve": "Reserve",
    }
    normalized = value.strip().lower()
    if normalized in aliases:
        return aliases[normalized]
    words = [word for word in value.replace("_", " ").replace("-", " ").split() if word]
    if words:
        return words[0][:12].capitalize()
    return "Account"


def _usage_choice_options(default_minutes: int, *, keep_label: str = "Keep default") -> list[MenuChoice]:
    preset_minutes = [30, 60, 90, 120, 150, 180, 240, 300]
    if default_minutes not in preset_minutes:
        preset_minutes.append(default_minutes)
    preset_minutes = sorted(minute for minute in preset_minutes if 1 <= minute <= 1440)
    return [
        MenuChoice(f"{keep_label} ({_format_usage_minutes(default_minutes)})", "default"),
        *[
            MenuChoice(_format_usage_minutes(minutes), str(minutes))
            for minutes in preset_minutes
            if minutes != default_minutes
        ],
        MenuChoice("Custom minutes/hours", "custom"),
        MenuChoice("Back", MENU_EXIT),
    ]


def _format_usage_minutes(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes > 60:
        hours, remainder = divmod(minutes, 60)
        return f"{hours}h{remainder:02d}m"
    return f"{minutes}m"


def _smart_schedule_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        target = _select(
            "Schedule target",
            [
                MenuChoice("Default schedule", "__default__"),
                *_account_menu_choices(_load_visible_accounts(config), only_kickable=False),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if target == MENU_EXIT:
            return
        period_choice = _select(
            "Configure which periods?",
            [
                MenuChoice("Weekdays", "weekdays"),
                MenuChoice("Weekends", "weekends"),
                MenuChoice("Both weekdays and weekends", "both"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if period_choice == MENU_EXIT:
            continue
        periods = ["weekdays", "weekends"] if period_choice == "both" else [period_choice]

        tz = local_timezone(config.schedule)
        updates: dict[str, str | None] = {"weekdays": None, "weekends": None}
        cancelled = False
        for period in periods:
            window = pick_work_window(title=f"{period.title()} window")
            if not window:
                cancelled = True
                break
            try:
                parse_work_window(window, datetime.now(tz).date(), tz)
            except ValueError as exc:
                click.echo(click.style(f"Invalid schedule window: {exc}", fg="red"))
                cancelled = True
                break
            updates[period] = window
        if cancelled:
            continue

        scope = "default" if target == "__default__" else target
        detail = ", ".join(f"{key}={value}" for key, value in updates.items() if value)
        if not _confirm(f"Save schedule for {scope}: {detail}?", default=False):
            continue
        _invoke(
            ctx,
            cli_module.schedule_set,
            account=None if target == "__default__" else target,
            set_default=target == "__default__",
            weekdays=updates["weekdays"],
            weekends=updates["weekends"],
            timezone_name=None,
        )


def pick_work_window(title: str = "Work window", *, base_date: date | None = None) -> str | None:
    """Pick a start/end work window from 30-minute slots."""
    try:
        from prompt_toolkit import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import DynamicContainer, HSplit, VSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except ImportError as exc:
        raise click.ClickException(
            "prompt_toolkit is required for the interactive schedule picker."
        ) from exc

    slots = _time_slots()
    columns = [slots[0:12], slots[12:24], slots[24:36], slots[36:48]]
    initial_col, initial_row = _time_slot_position(
        _initial_work_window_start_slot(base_date, datetime.now().astimezone())
    )
    pane_labels = _work_window_day_labels(base_date)

    class PickerState:
        day = 0
        col = initial_col
        row = initial_row
        start: str | None = None
        end: str | None = None
        cancelled = False

        @property
        def current(self) -> str:
            return columns[self.col][self.row]

        @property
        def phase(self) -> str:
            return "end" if self.start else "start"

        @property
        def done(self) -> bool:
            return bool(self.start and self.end)

    state = PickerState()
    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        state.row = max(0, state.row - 1)

    @kb.add("down")
    def _down(event):
        state.row = min(11, state.row + 1)

    @kb.add("left")
    def _left(event):
        if state.phase == "end" and state.day == 1 and state.col == 0:
            state.day = 0
            state.col = 3
            return
        state.col = max(0, state.col - 1)

    @kb.add("right")
    def _right(event):
        if state.phase == "end" and state.day == 0 and state.col == 3:
            state.day = 1
            state.col = 0
            return
        state.col = min(3, state.col + 1)

    @kb.add("space")
    @kb.add("enter")
    def _choose(event):
        if state.start is None:
            state.start = state.current
            state.day, state.col, state.row = _initial_work_window_end_position(state.start)
        else:
            if not _work_window_end_slot_enabled(state.start, state.current, day_offset=state.day):
                return
            state.end = state.current
            event.app.exit()

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event):
        state.cancelled = True
        event.app.exit()

    def render_header():
        summary = _work_window_picker_summary(state.start, state.end)
        return FormattedText(
            [
                ("bold", f"{title}\n"),
                (
                    "fg:ansigreen",
                    "Arrows: navigate | Space/Enter: choose | Esc: cancel\n",
                ),
                ("", f"Choose {state.phase}. {summary}\n\n"),
            ]
        )

    def render_column(index: int, *, day_offset: int = 0):
        fragments = []
        for row, slot in enumerate(columns[index]):
            cursor = day_offset == state.day and index == state.col and row == state.row
            marker = "[ ]"
            enabled = state.phase == "start" or _work_window_end_slot_enabled(
                state.start,
                slot,
                day_offset=day_offset,
            )
            if day_offset == 0 and slot == state.start:
                marker = "[S]"
            elif slot == state.end:
                marker = "[E]"
            if cursor:
                style = "bold reverse" if enabled else "reverse fg:ansiblack"
            elif not enabled:
                style = "fg:ansiblack"
            elif marker != "[ ]":
                style = "bold fg:ansicyan"
            elif state.phase == "end" and day_offset == 1 and state.day != 1:
                style = "fg:ansibrightblack"
            else:
                style = ""
            fragments.append((style, f" {marker} {slot} \n"))
        return FormattedText(fragments)

    def render_start_grid():
        return VSplit(
            [
                Window(content=FormattedTextControl(lambda i=i: render_column(i)), width=12)
                for i in range(4)
            ]
        )

    def render_end_pane(day_offset: int):
        return HSplit(
            [
                Window(
                    content=FormattedTextControl(
                        lambda day_offset=day_offset: FormattedText(
                            [("bold" if day_offset == state.day else "fg:ansibrightblack", f"{pane_labels[day_offset]}\n")]
                        )
                    ),
                    height=1,
                ),
                VSplit(
                    [
                        Window(
                            content=FormattedTextControl(
                                lambda i=i, day_offset=day_offset: render_column(i, day_offset=day_offset)
                            ),
                            width=12,
                        )
                        for i in range(4)
                    ]
                ),
            ]
        )

    def render_picker_body():
        if state.phase == "start":
            return render_start_grid()
        return VSplit(
            [
                render_end_pane(0),
                Window(width=2),
                render_end_pane(1),
            ]
        )

    layout = Layout(
        HSplit(
            [
                Window(content=FormattedTextControl(render_header), height=4),
                DynamicContainer(render_picker_body),
            ]
        )
    )
    Application(layout=layout, key_bindings=kb, full_screen=False).run()
    if state.cancelled or not state.done:
        return None
    return _work_window_picker_value(state.start, state.end)


def _time_slots() -> list[str]:
    return [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)]


def _next_half_hour_slot(value: datetime) -> str:
    if value.minute in {0, 30} and value.second == 0 and value.microsecond == 0:
        return value.strftime("%H:%M")
    total_minutes = value.hour * 60 + value.minute
    next_total = ((total_minutes // 30) + 1) * 30
    hour = (next_total // 60) % 24
    minute = next_total % 60
    return f"{hour:02d}:{minute:02d}"


def _initial_work_window_start_slot(base_date: date | None, now: datetime) -> str:
    if base_date is not None and base_date > now.date():
        return "07:00"
    return _next_half_hour_slot(now)


def _time_slot_position(slot: str) -> tuple[int, int]:
    index = _time_slots().index(slot)
    return index // 12, index % 12


def _work_window_day_labels(base_date: date | None) -> tuple[str, str]:
    if base_date is None:
        return ("Start day", "Next day")
    return (f"Today, {base_date.isoformat()}", f"Tomorrow, {(base_date + timedelta(days=1)).isoformat()}")


def _work_window_end_slot_enabled(start: str | None, slot: str, *, day_offset: int) -> bool:
    if start is None:
        return True
    if day_offset == 0:
        return _slot_minutes(slot) > _slot_minutes(start)
    if day_offset == 1:
        # An equal next-day end would render as HH:MM-HH:MM, which the parser
        # rejects; the longest pickable window is 23.5 hours.
        return _slot_minutes(slot) < _slot_minutes(start)
    return False


def _initial_work_window_end_position(start: str) -> tuple[int, int, int]:
    start_index = _time_slots().index(start)
    if start_index < len(_time_slots()) - 1:
        col, row = _time_slot_position(_time_slots()[start_index + 1])
        return 0, col, row
    col, row = _time_slot_position("00:00")
    return 1, col, row


def _work_window_picker_summary(start: str | None, end: str | None) -> str:
    start_text = start or "-"
    end_text = end or "-"
    if start is not None and end is not None and _slot_minutes(end) <= _slot_minutes(start):
        end_text = f"{end} (+1 day)"
    return f"Start: {start_text} End: {end_text}"


def _work_window_picker_value(start: str, end: str) -> str:
    return f"{start}-{end}"


def _slot_minutes(value: str) -> int:
    hour_text, minute_text = value.split(":", 1)
    return int(hour_text) * 60 + int(minute_text)


def pick_codex_surface_order(
    current_order: Sequence[str],
    default_order: Sequence[str],
) -> tuple[str, ...] | str | None:
    """Pick an ordered Codex burst ladder surface subset."""
    try:
        from prompt_toolkit import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except ImportError as exc:
        raise click.ClickException(
            "prompt_toolkit is required for the interactive Codex surface picker."
        ) from exc

    class PickerState:
        rows = _surface_order_candidates(current_order, default_order)
        selected = set(current_order)
        cursor = 0
        cancelled = False
        reset_requested = False
        error = ""

        @property
        def reset_index(self) -> int:
            return len(self.rows)

        @property
        def back_index(self) -> int:
            return len(self.rows) + 1

        @property
        def on_reset(self) -> bool:
            return self.cursor == self.reset_index

        @property
        def on_back(self) -> bool:
            return self.cursor == self.back_index

        @property
        def chosen(self) -> tuple[str, ...]:
            return tuple(surface for surface in self.rows if surface in self.selected)

    state = PickerState()
    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        state.cursor = max(0, state.cursor - 1)
        state.error = ""

    @kb.add("down")
    def _down(event):
        state.cursor = min(state.back_index, state.cursor + 1)
        state.error = ""

    @kb.add("left")
    def _move_up(event):
        if state.on_back or state.on_reset or state.cursor <= 0:
            return
        state.rows[state.cursor - 1], state.rows[state.cursor] = (
            state.rows[state.cursor],
            state.rows[state.cursor - 1],
        )
        state.cursor -= 1
        state.error = ""

    @kb.add("right")
    def _move_down(event):
        if state.on_back or state.on_reset or state.cursor >= len(state.rows) - 1:
            return
        state.rows[state.cursor + 1], state.rows[state.cursor] = (
            state.rows[state.cursor],
            state.rows[state.cursor + 1],
        )
        state.cursor += 1
        state.error = ""

    @kb.add("space")
    def _toggle(event):
        if state.on_back:
            state.cancelled = True
            event.app.exit()
            return
        if state.on_reset:
            state.reset_requested = True
            event.app.exit()
            return
        surface = state.rows[state.cursor]
        if surface in state.selected:
            state.selected.remove(surface)
        else:
            state.selected.add(surface)
        state.error = ""

    @kb.add("enter")
    def _done(event):
        if state.on_back:
            state.cancelled = True
            event.app.exit()
            return
        if state.on_reset:
            state.reset_requested = True
            event.app.exit()
            return
        if not state.chosen:
            state.error = "Choose at least one surface."
            return
        event.app.exit()

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event):
        state.cancelled = True
        event.app.exit()

    def render():
        saved = ", ".join(current_order) if current_order else "-"
        fragments: list[tuple[str, str]] = [
            ("bold", "Codex burst ladder surface order\n"),
            (
                "fg:ansigreen",
                "Up/Down: select | Space: include/exclude | Left/Right: reorder | "
                "Enter: review save/reset | Back/Esc: discard draft\n\n",
            ),
            ("", f"Saved order: {saved}\n"),
            ("", "Draft order: "),
            *_surface_order_draft_fragments(state.chosen, current_order),
            ("", "\n"),
            ("fg:ansiyellow", "Press Enter to review and save this draft on the next prompt.\n\n"),
        ]
        for index, surface in enumerate(state.rows):
            cursor = index == state.cursor
            marker = "[x]" if surface in state.selected else "[ ]"
            changed = _surface_order_changed_at(state.chosen, current_order, surface)
            if cursor:
                style = "bold reverse"
            elif surface in state.selected and changed:
                style = "bold fg:ansiyellow"
            elif surface in state.selected:
                style = "bold fg:ansicyan"
            else:
                style = ""
            fragments.append((style, f" {marker} {surface}\n"))
        reset_style = "bold reverse" if state.on_reset else "fg:ansiyellow"
        fragments.append((reset_style, " Reset to default order\n"))
        back_style = "bold reverse" if state.on_back else ""
        fragments.append((back_style, " Back (discard draft)\n"))
        if state.error:
            fragments.append(("fg:ansired bold", f"{state.error}\n"))
        return FormattedText(fragments)

    layout = Layout(HSplit([Window(content=FormattedTextControl(render))]))
    Application(layout=layout, key_bindings=kb, full_screen=False).run()
    if state.cancelled:
        return None
    if state.reset_requested:
        return CODEX_SURFACE_ORDER_RESET
    return state.chosen


def _surface_order_draft_fragments(
    draft_order: Sequence[str],
    current_order: Sequence[str],
) -> list[tuple[str, str]]:
    if not draft_order:
        return [("", "-")]
    fragments: list[tuple[str, str]] = []
    for index, surface in enumerate(draft_order):
        if index:
            fragments.append(("", ", "))
        style = "bold fg:ansiyellow" if _surface_order_changed_index(draft_order, current_order, index) else ""
        fragments.append((style, surface))
    return fragments


def _surface_order_changed_index(
    draft_order: Sequence[str],
    current_order: Sequence[str],
    index: int,
) -> bool:
    return index >= len(current_order) or draft_order[index] != current_order[index]


def _surface_order_changed_at(
    draft_order: Sequence[str],
    current_order: Sequence[str],
    surface: str,
) -> bool:
    return any(
        candidate == surface and _surface_order_changed_index(draft_order, current_order, index)
        for index, candidate in enumerate(draft_order)
    )


def _surface_order_candidates(
    current_order: Sequence[str],
    default_order: Sequence[str],
) -> list[str]:
    rows: list[str] = []
    for surface in [*current_order, *default_order]:
        if surface not in rows:
            rows.append(surface)
    return rows


def _notifications_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        action = _select(
            "Notifications",
            [
                MenuChoice("Configure ntfy", "ntfy"),
                MenuChoice("Configure Telegram", "telegram"),
                MenuChoice("Disable Telegram notifications", "disable_telegram"),
                MenuChoice("Account notification toggles", "accounts"),
                MenuChoice("Send test notification (all enabled)", "test"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "accounts":
            _notification_accounts_menu(ctx)
            continue
        if action == "test":
            if _confirm("Send a test notification?", default=True):
                _invoke(
                    ctx,
                    cli_module.notify,
                    ntfy_topic=None,
                    telegram=None,
                    telegram_remote=None,
                    disable_backend=None,
                    test_backend="all",
                    action="test",
                )
            continue
        if action == "disable_telegram":
            if _confirm("Disable Telegram push notifications? Remote commands will still work.", default=True):
                _invoke(
                    ctx,
                    cli_module.notify,
                    ntfy_topic=None,
                    telegram=None,
                    telegram_remote=None,
                    disable_backend="telegram",
                    test_backend="all",
                    action=None,
                )
            continue
        if action == "ntfy":
            topic = _text_action("ntfy topic name", action_label="Enter ntfy topic name")
            if topic and _confirm(f'Enable ntfy topic name "{topic}"?', default=True):
                _invoke(
                    ctx,
                    cli_module.notify,
                    ntfy_topic=topic,
                    telegram=None,
                    telegram_remote=None,
                    disable_backend=None,
                    test_backend="all",
                    action=None,
                )
            continue
        token = _secret_action("Telegram bot token", action_label="Enter Telegram bot token")
        if not token:
            continue
        chat_id = _text_action("Telegram chat ID", action_label="Enter Telegram chat ID")
        if token and chat_id and _confirm("Enable Telegram notifications?", default=True):
            _invoke(
                ctx,
                cli_module.notify,
                ntfy_topic=None,
                telegram=(token, chat_id),
                telegram_remote=None,
                disable_backend=None,
                test_backend="all",
                action=None,
            )


def _remote_telegram_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        action = _select(
            "Remote status (Telegram)",
            [
                MenuChoice("Status", "status"),
                MenuChoice("Enable background listener", "start"),
                MenuChoice("Disable background listener", "stop"),
                MenuChoice("Restart background listener", "restart"),
                MenuChoice("Configure remote credentials", "configure"),
                MenuChoice("Use remote only (disable notifications)", "remote_only"),
                MenuChoice("Send Telegram test notification", "test"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(
                ctx,
                cli_module.remote_telegram,
                background=False,
                stop_remote=False,
                remote_status=True,
                restart_remote=False,
            )
        elif action == "start" and _confirm("Start Telegram remote listener?", default=True):
            _invoke(
                ctx,
                cli_module.remote_telegram,
                background=True,
                stop_remote=False,
                remote_status=False,
                restart_remote=False,
            )
        elif action == "stop" and _confirm("Stop Telegram remote listener?", default=False):
            _invoke(
                ctx,
                cli_module.remote_telegram,
                background=False,
                stop_remote=True,
                remote_status=False,
                restart_remote=False,
            )
        elif action == "restart" and _confirm("Restart Telegram remote listener?", default=True):
            _invoke(
                ctx,
                cli_module.remote_telegram,
                background=False,
                stop_remote=False,
                remote_status=False,
                restart_remote=True,
            )
        elif action == "configure":
            token = _secret_action("Telegram bot token", action_label="Enter Telegram bot token")
            if not token:
                continue
            chat_id = _text_action("Telegram chat ID", action_label="Enter Telegram chat ID")
            if token and chat_id and _confirm("Save Telegram remote credentials?", default=True):
                _invoke(
                    ctx,
                    cli_module.notify,
                    ntfy_topic=None,
                    telegram=None,
                    telegram_remote=(token, chat_id),
                    disable_backend=None,
                    test_backend="all",
                    action=None,
                )
        elif action == "remote_only" and _confirm(
            "Disable Telegram push notifications? Remote commands will still work.",
            default=True,
        ):
            _invoke(
                ctx,
                cli_module.notify,
                ntfy_topic=None,
                telegram=None,
                telegram_remote=None,
                disable_backend="telegram",
                test_backend="all",
                action=None,
            )
        elif action == "test" and _confirm("Send a test notification?", default=True):
            _invoke(
                ctx,
                cli_module.notify,
                ntfy_topic=None,
                telegram=None,
                telegram_remote=None,
                disable_backend=None,
                test_backend="telegram",
                action="test",
            )


def _notification_accounts_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        accounts = config.accounts
        if not accounts:
            click.echo("No saved accounts found.")
            return
        action = _select(
            "Account notifications",
            [
                MenuChoice("Show account notification status", "status"),
                MenuChoice("Set one account route", "set_route"),
                MenuChoice("Enable one account", "enable_one"),
                MenuChoice("Disable one account", "disable_one"),
                MenuChoice("Enable multiple accounts", "enable_selected"),
                MenuChoice("Disable multiple accounts", "disable_selected"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(ctx, cli_module.accounts_notifications)
            continue
        if action == "set_route":
            _notification_account_route_flow(ctx, cli_module, accounts)
            continue
        if action.endswith("_one"):
            label = _select(
                "Choose account",
                [*_notification_account_menu_choices(accounts), MenuChoice("Back", MENU_EXIT)],
            )
            if label == MENU_EXIT:
                continue
            enabled = action == "enable_one"
            command = (
                cli_module.accounts_enable_notifications
                if enabled
                else cli_module.accounts_disable_notifications
            )
            _invoke(ctx, command, label=label)
            continue
        selected = _checkbox(
            "Choose account(s) with Space, then Enter",
            [*_notification_account_menu_choices(accounts), MenuChoice("Back", MENU_EXIT)],
        )
        if MENU_EXIT in selected:
            continue
        if not selected:
            click.echo(
                click.style(
                    "No accounts selected; press Space to select account(s), then Enter.",
                    fg="yellow",
                )
            )
            continue
        enabled = action == "enable_selected"
        operation = "enable" if enabled else "disable"
        if not _confirm(
            f"{operation.capitalize()} notifications for {len(selected)} account(s)?",
            default=True,
        ):
            continue
        command = cli_module.accounts_enable_notifications if enabled else cli_module.accounts_disable_notifications
        for label in selected:
            _invoke(ctx, command, label=label)


def _notification_account_route_flow(
    ctx: click.Context,
    cli_module: Any,
    accounts: list[AccountConfig],
) -> None:
    label = _select(
        "Choose account",
        [*_notification_account_menu_choices(accounts), MenuChoice("Back", MENU_EXIT)],
    )
    if label == MENU_EXIT:
        return
    route = _select(
        "Notification route",
        [
            MenuChoice("Global default", "global_default"),
            MenuChoice("ntfy", "ntfy"),
            MenuChoice("Telegram", "telegram"),
            MenuChoice("ntfy + Telegram", "both"),
            MenuChoice("Disabled", "none"),
            MenuChoice("Back", MENU_EXIT),
        ],
    )
    if route == MENU_EXIT:
        return
    _invoke(
        ctx,
        cli_module.accounts_set_notifications,
        label=label,
        ntfy=route in {"ntfy", "both"},
        telegram=route in {"telegram", "both"},
        global_default=route == "global_default",
        none=route == "none",
    )


def _notification_account_menu_choices(accounts: list[AccountConfig]) -> list[MenuChoice]:
    result = []
    for account in accounts:
        marker = _notification_account_route_marker(account)
        result.append(MenuChoice(f"{account.label} ({account.provider}, {marker})", account.label))
    return result


def _notification_account_route_marker(account: AccountConfig) -> str:
    if not account.notifications_enabled:
        return "❌ disabled"
    if account.notification_backends is None:
        return "✅ global default"
    if not account.notification_backends:
        return "❌ disabled"
    return "✅ " + "+".join(account.notification_backends)


def _history_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        action = _select(
            "History",
            [
                MenuChoice("All recent compact", "all_recent_compact"),
                MenuChoice("All recent verbose", "all_recent_verbose"),
                MenuChoice("Account recent compact", "account_recent_compact"),
                MenuChoice("Account recent verbose", "account_recent_verbose"),
                MenuChoice("Custom limit compact", "custom_limit_compact"),
                MenuChoice("Custom limit verbose", "custom_limit_verbose"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        verbose = action.endswith("verbose")
        account_label = None
        limit = 20
        anchored_only = False
        if action.startswith("account_recent"):
            account_label = _select_account(
                _load_visible_accounts(config),
                message="Show history for which account?",
                only_kickable=False,
            )
            if not account_label:
                continue
        elif action.startswith("custom_limit"):
            scope = _select(
                "History scope",
                [
                    MenuChoice("All accounts", "all"),
                    MenuChoice("One account", "account"),
                    MenuChoice("Back", MENU_EXIT),
                ],
            )
            if scope == MENU_EXIT:
                continue
            if scope == "account":
                account_label = _select_account(
                    _load_visible_accounts(config),
                    message="Show history for which account?",
                    only_kickable=False,
                )
                if not account_label:
                    continue
            limit = _pick_history_limit()
            if limit is None:
                continue
            anchored_choice = _select(
                "History filter",
                [
                    MenuChoice("Show all kicks", "all"),
                    MenuChoice("Only anchored (succeeded)", "anchored"),
                    MenuChoice("Back", MENU_EXIT),
                ],
            )
            if anchored_choice == MENU_EXIT:
                continue
            anchored_only = anchored_choice == "anchored"
        _invoke(
            ctx,
            cli_module.history,
            limit=limit,
            account_label=account_label,
            as_json=False,
            verbose=verbose,
            anchored_only=anchored_only,
        )


def _pick_history_limit() -> int | None:
    action = _select(
        "History limit",
        [
            MenuChoice("20 events", "20"),
            MenuChoice("50 events", "50"),
            MenuChoice("100 events", "100"),
            MenuChoice("Custom", "custom"),
            MenuChoice("Back", MENU_EXIT),
        ],
    )
    if action == MENU_EXIT:
        return None
    if action != "custom":
        return int(action)
    raw_limit = _text_action("History limit", default="20")
    if raw_limit is None:
        return None
    try:
        limit = int(raw_limit)
    except ValueError:
        click.echo(click.style("History limit must be a number.", fg="red"))
        return None
    if limit <= 0:
        click.echo(click.style("History limit must be greater than 0.", fg="red"))
        return None
    return limit


def _diagnostics_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        config = Config.load()
        action = _select(
            "Diagnostics",
            [
                MenuChoice("Doctor", "doctor"),
                MenuChoice("Doctor repair", "doctor_repair"),
                MenuChoice("Status details", "status_details"),
                MenuChoice("Reset log", "reset_log"),
                MenuChoice("Reset recovery", "reset_recovery"),
                MenuChoice("Codex usage buckets", "codex_usage"),
                MenuChoice("Codex surface stats", "codex_surfaces"),
                MenuChoice("Surface patterns (experimental, read-only)", "surface_patterns"),
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "doctor":
            _invoke(ctx, cli_module.doctor_cmd, label=None, json_output=False, repair=False)
        elif action == "doctor_repair":
            if _confirm("Run doctor repair?", default=False):
                _invoke(ctx, cli_module.doctor_cmd, label=None, json_output=False, repair=True)
        elif action == "status_details":
            _invoke(
                ctx,
                cli_module.status,
                as_json=False,
                codex_only=False,
                show_all=False,
                account_label=None,
                refresh=False,
                verbose=True,
            )
        elif action == "reset_log":
            _invoke(ctx, cli_module.reset_log, since=None, provider=None, as_json=False, as_csv=False, detail_id=None)
        elif action == "reset_recovery":
            _global_reset_recovery_menu(ctx)
        elif action == "codex_usage":
            _invoke(ctx, cli_module.codex_usage, label=None, as_json=False)
        elif action == "codex_surfaces":
            accounts = [
                account
                for account in _load_visible_accounts(config)
                if account.provider == "codex"
            ]
            label = _select_account(accounts, message="Show Codex surface stats for which account?", only_kickable=False)
            if label:
                _invoke(ctx, cli_module.codex_surfaces, label=label, as_json=False)
        elif action == "surface_patterns":
            _invoke(ctx, cli_module.codex_surface_patterns, label=None, as_json=False)


def _global_reset_recovery_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        events = cli_module.filter_reset_events(
            cli_module.load_reset_events(limit=20),
            unacknowledged=True,
        )
        if not events:
            click.echo("No unacknowledged reset events or provider observations.")
            return
        choices = [
            MenuChoice(_reset_recovery_event_label(event), event.id)
            for event in reversed(events)
        ]
        choices.append(MenuChoice("Back", MENU_EXIT))
        event_id = _select("Reset recovery", choices)
        if event_id == MENU_EXIT:
            return
        event = next((candidate for candidate in events if candidate.id == event_id), None)
        if event is None:
            click.echo(click.style("Reset event no longer exists.", fg="yellow"))
            continue
        cli_module._render_reset_event_detail(event)
        while True:
            choices = [
                MenuChoice("Acknowledge event", "ack"),
                MenuChoice("Open reset log detail", "detail"),
            ]
            if not cli_module.is_provider_reset_observation(event):
                choices.append(MenuChoice("Create orchestration plan", "plan"))
            choices.append(MenuChoice("Back", MENU_EXIT))
            action = _select("Reset recovery action", choices)
            if action == MENU_EXIT:
                break
            if action == "ack":
                updated = cli_module.acknowledge_reset_events(
                    event_ids=[event.id],
                    acknowledged_by="tui",
                )
                if updated:
                    click.echo(click.style(f"Acknowledged reset event {event.id}.", fg="green"))
                else:
                    click.echo(click.style("Reset event was already acknowledged.", fg="yellow"))
                break
            if action == "detail":
                _invoke(
                    ctx,
                    cli_module.reset_log,
                    since=None,
                    provider=None,
                    as_json=False,
                    as_csv=False,
                    detail_id=event.id,
                    unacknowledged=False,
                    ack_latest=False,
                    ack_all=False,
                    action=(),
                )
            elif action == "plan":
                _orchestration_plan_flow(recovery_event_id=event.id)


def _reset_recovery_event_label(event: Any) -> str:
    detected = event.detected_at
    parsed = None
    try:
        parsed = datetime.fromisoformat(detected.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        pass
    stamp = parsed.strftime("%Y-%m-%d %H:%M") if parsed else detected
    from . import cli as cli_module

    kind = "observation" if cli_module.is_provider_reset_observation(event) else event.confidence
    return f"{event.provider} {kind} {stamp} ({', '.join(event.affected_accounts)})"


def _daemon_menu(ctx: click.Context) -> None:
    from . import cli as cli_module

    while True:
        pipx_upgrade_supported = _pipx_upgrade_command() is not None
        upgrade_choices = (
            [
                MenuChoice("Upgrade TokenKick via pipx", "pipx_upgrade"),
                MenuChoice("Upgrade TokenKick via pipx + tk update", "pipx_upgrade_update"),
            ]
            if pipx_upgrade_supported
            else [MenuChoice("Show manual upgrade command", "upgrade_help")]
        )
        action = _select(
            "Daemon",
            [
                MenuChoice("Status", "status"),
                MenuChoice("Start background daemon", "start"),
                MenuChoice("Stop background daemon", "stop"),
                MenuChoice("Restart background daemon", "restart"),
                MenuChoice("Run tk update", "update"),
                *upgrade_choices,
                MenuChoice("Back", MENU_EXIT),
            ],
        )
        if action == MENU_EXIT:
            return
        if action == "status":
            _invoke(ctx, cli_module.daemon, background=False, stop_daemon=False, daemon_status=True, restart_daemon=False)
        elif action == "start" and _confirm("Start TokenKick daemon in the background?", default=True):
            _invoke(ctx, cli_module.daemon, background=True, stop_daemon=False, daemon_status=False, restart_daemon=False)
        elif action == "stop" and _confirm("Stop TokenKick daemon?", default=False):
            _invoke(ctx, cli_module.daemon, background=False, stop_daemon=True, daemon_status=False, restart_daemon=False)
        elif action == "restart" and _confirm("Restart TokenKick daemon?", default=True):
            _invoke(ctx, cli_module.daemon, background=False, stop_daemon=False, daemon_status=False, restart_daemon=True)
        elif action == "update" and _confirm("Run tk update now?", default=True):
            _run_visible_command([_tk_subprocess_command(), "update"])
        elif action == "pipx_upgrade" and _confirm("Run pipx upgrade tokenkick?", default=True):
            _run_pipx_upgrade(update_after=False)
        elif action == "pipx_upgrade_update" and _confirm(
            "Run pipx upgrade tokenkick, then tk update?",
            default=True,
        ):
            _run_pipx_upgrade(update_after=True)
        elif action == "upgrade_help":
            _print_manual_upgrade_command()


def _load_visible_accounts(config: Config) -> list[AccountConfig]:
    return [account for account in config.accounts if account.visible]


def _account_by_label(accounts: list[AccountConfig], label: str) -> AccountConfig | None:
    return next((account for account in accounts if account.label == label), None)


def _select_account(
    accounts: list[AccountConfig],
    *,
    message: str,
    only_kickable: bool,
) -> str | None:
    choices = _account_menu_choices(accounts, only_kickable=only_kickable)
    if not choices:
        click.echo("No matching accounts found.")
        return None
    selected = _select(message, choices + [MenuChoice("Back", MENU_EXIT)])
    return None if selected == MENU_EXIT else selected


def _account_menu_choices(accounts: list[AccountConfig], *, only_kickable: bool) -> list[MenuChoice]:
    result = []
    for account in accounts:
        if only_kickable and account.provider not in KICKABLE_PROVIDERS:
            continue
        marker = "kickable" if account.provider in KICKABLE_PROVIDERS else "monitor-only"
        source = _account_source_label(account)
        result.append(MenuChoice(f"{account.label} ({account.provider}, {marker}, {source})", account.label))
    return result


def _account_source_label(account: AccountConfig) -> str:
    if account.source == DataSource.CODEX_DIRECT and account.provider_home:
        return "direct home"
    return account.source.value


__all__ = [
    "MENU_EXIT",
    "MenuChoice",
    "pick_work_window",
    "run_command_center",
]
