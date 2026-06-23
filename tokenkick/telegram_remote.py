"""Read-only Telegram remote status listener for TokenKick."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Callable, Protocol

from .models import Config
from .state_io import atomic_write_text, state_file_lock

TELEGRAM_LONG_POLL_TIMEOUT_SECONDS = 30
TELEGRAM_HTTP_TIMEOUT_SECONDS = 10
TELEGRAM_STATUS_TIMEOUT_SECONDS = 180
TELEGRAM_REPLY_LIMIT = 3900
TELEGRAM_STATUS_ACCOUNT_LIMIT = 12
TELEGRAM_STATUS_LABEL_WIDTH = 44


class TelegramRemoteError(RuntimeError):
    """Base error for Telegram remote status."""


class TelegramRemoteConfigError(TelegramRemoteError):
    """Raised when Telegram remote status cannot start safely."""


class TelegramRemoteAPIError(TelegramRemoteError):
    """Raised for Telegram Bot API failures."""


@dataclass(frozen=True)
class TelegramRemoteCredentials:
    token: str
    chat_id: str


@dataclass(frozen=True)
class StatusCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class TelegramRemoteClientProtocol(Protocol):
    def get_webhook_info(self) -> dict:
        ...

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        ...

    def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None) -> None:
        ...


def telegram_remote_credentials(config: Config) -> TelegramRemoteCredentials:
    token = (config.notifications.telegram_bot_token or "").strip()
    chat_id = (config.notifications.telegram_chat_id or "").strip()
    if not token or not chat_id:
        raise TelegramRemoteConfigError(
            "Telegram remote status is not configured. "
            "Run `tk notify --telegram TOKEN CHAT_ID` first."
        )
    return TelegramRemoteCredentials(token=token, chat_id=chat_id)


class TelegramRemoteClient:
    """Small Bot API client using TokenKick's existing httpx dependency."""

    def __init__(self, token: str) -> None:
        self.token = token

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def get_webhook_info(self) -> dict:
        import httpx

        response = httpx.get(
            self._url("getWebhookInfo"),
            timeout=TELEGRAM_HTTP_TIMEOUT_SECONDS,
        )
        result = _telegram_result(response, "getWebhookInfo")
        return result if isinstance(result, dict) else {}

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        import httpx

        params: dict[str, object] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            params["offset"] = offset
        response = httpx.get(
            self._url("getUpdates"),
            params=params,
            timeout=timeout + TELEGRAM_HTTP_TIMEOUT_SECONDS,
        )
        result = _telegram_result(response, "getUpdates")
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None) -> None:
        import httpx

        payload = {"chat_id": chat_id, "text": truncate_telegram_reply(text)}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        response = httpx.post(
            self._url("sendMessage"),
            json=payload,
            timeout=TELEGRAM_HTTP_TIMEOUT_SECONDS,
        )
        _telegram_result(response, "sendMessage")


def _telegram_result(response, method: str):
    status_code = getattr(response, "status_code", None)
    if status_code is None or not (200 <= int(status_code) < 300):
        detail = _response_text(response)
        raise TelegramRemoteAPIError(f"Telegram {method} HTTP {status_code}: {detail}")
    try:
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - response shape is external input
        raise TelegramRemoteAPIError(f"Telegram {method} returned invalid JSON.") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        detail = data.get("description") if isinstance(data, dict) else None
        raise TelegramRemoteAPIError(f"Telegram {method} failed: {detail or 'unknown error'}")
    return data.get("result")


def _response_text(response) -> str:
    text = getattr(response, "text", "")
    text = " ".join(str(text).split())
    return text[:240] if text else "empty response"


def load_last_update_id(path: Path) -> int | None:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    value = data.get("last_update_id") if isinstance(data, dict) else None
    return value if isinstance(value, int) else None


def save_last_update_id(path: Path, update_id: int) -> None:
    with state_file_lock(path):
        atomic_write_text(path, json.dumps({"last_update_id": update_id}, indent=2) + "\n")


def parse_telegram_command(text: str | None, *, bot_username: str | None = None) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    first = stripped.split()[0]
    if first.startswith("/"):
        command = first[1:]
        if "@" in command:
            command, suffix = command.split("@", 1)
            if bot_username and suffix.lower() != bot_username.lower():
                return None
        command = command.lower()
    else:
        command = first.lower()
    if command in {"status", "refresh", "ping", "help"}:
        return command
    return "unknown"


def update_chat_id(update: dict) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return str(chat_id) if chat_id is not None else None


def update_text(update: dict) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    return text if isinstance(text, str) else None


def run_status_cached_command(executable: str) -> StatusCommandResult:
    return _run_status_command(
        executable,
        refresh=False,
        timeout_message="TokenKick status timed out.",
        start_error_prefix="TokenKick status could not start",
    )


def run_status_refresh_command(executable: str) -> StatusCommandResult:
    return _run_status_command(
        executable,
        refresh=True,
        timeout_message="TokenKick status refresh timed out.",
        start_error_prefix="TokenKick status refresh could not start",
    )


def _run_status_command(
    executable: str,
    *,
    refresh: bool,
    timeout_message: str,
    start_error_prefix: str,
) -> StatusCommandResult:
    command = [executable, "status", "--json-output"]
    if refresh:
        command.insert(2, "--refresh")
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TELEGRAM_STATUS_TIMEOUT_SECONDS,
            check=False,
        )
        return StatusCommandResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return StatusCommandResult(
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=timeout_message,
        )
    except OSError as exc:
        return StatusCommandResult(
            exit_code=127,
            stdout="",
            stderr=f"{start_error_prefix}: {exc}",
        )


def format_status_reply(
    result: StatusCommandResult,
    *,
    now: datetime | None = None,
    refreshed: bool = True,
) -> str:
    checked = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M:%S")
    mode_icon = "🔄" if refreshed else "📦"
    mode_label = "live refresh" if refreshed else "cached status"
    if result.exit_code != 0:
        return truncate_telegram_reply(
            "<b>TokenKick status failed</b>\n"
            f"{mode_icon} {mode_label} · <code>{html_escape(checked)}</code>\n\n"
            f"⚠️ <i>{html_escape(_status_failure_detail(result))}</i>"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return truncate_telegram_reply(
            "<b>TokenKick status failed</b>\n"
            f"{mode_icon} {mode_label} · <code>{html_escape(checked)}</code>\n\n"
            "⚠️ <i>status command returned invalid JSON</i>"
        )
    if not isinstance(payload, dict):
        return truncate_telegram_reply(
            "<b>TokenKick status failed</b>\n"
            f"{mode_icon} {mode_label} · <code>{html_escape(checked)}</code>\n\n"
            "⚠️ <i>status command returned an invalid payload</i>"
        )
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        accounts = []

    rows: list[dict] = []
    for row in accounts:
        if not isinstance(row, dict):
            continue
        rows.append(row)

    ready = [row for row in rows if _row_ready(row)]
    warnings = [row for row in rows if _row_warning(row)]
    current = [row for row in rows if not _row_ready(row) and not _row_warning(row)]

    lines = [
        "<b>TokenKick status</b>",
        f"{mode_icon} {mode_label} · <code>{html_escape(checked)}</code>",
    ]
    _append_status_section(lines, "🟢", "Ready", ready)
    _append_status_section(lines, "📊", "Accounts", current)
    _append_status_section(lines, "⚠️", "Warnings", warnings)
    omitted = max(0, len(rows) - TELEGRAM_STATUS_ACCOUNT_LIMIT)
    if omitted > 0:
        lines.extend(["", f"… {omitted} more {_plural(omitted, 'account')} omitted"])
    refresh_error = payload.get("refresh_error")
    if not accounts:
        lines.extend(
            [
                "",
                "<i>No accounts returned. Run tk setup on the server after logging in.</i>",
            ]
        )
    elif isinstance(refresh_error, str) and refresh_error:
        lines.extend(["", f"⚠️ <i>{html_escape(_fit_cell(refresh_error, 800))}</i>"])
    return truncate_telegram_reply("\n".join(lines).rstrip())


def _status_failure_detail(result: StatusCommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}"
    return " ".join(detail.split())[:800]


def _append_status_section(lines: list[str], icon: str, title: str, rows: list[dict]) -> None:
    if not rows:
        return
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(f"{icon} <b>{title}</b>: {len(rows)}")
    already_shown = _visible_account_count(lines)
    remaining = max(0, TELEGRAM_STATUS_ACCOUNT_LIMIT - already_shown)
    for row in rows[:remaining]:
        lines.append(_format_account_status(row))


def _visible_account_count(lines: list[str]) -> int:
    return sum(1 for line in lines if "\n   " in line)


def _format_account_status(row: dict) -> str:
    label = html_escape(_fit_cell(_display_label(row), TELEGRAM_STATUS_LABEL_WIDTH))
    detail = html_escape(_account_detail(row))
    return f"{_state_icon(row)} <b>{label}</b>\n   {detail}"


def _account_detail(row: dict) -> str:
    parts = [_state_text(row), _usage_text(row)]
    next_text = _next_text(row)
    if next_text:
        parts.append(next_text)
    if _row_warning(row):
        warning = _warning_detail(row)
        if warning:
            parts.append(warning)
    return " · ".join(part for part in parts if part)


def _usage_text(row: dict) -> str:
    weekly = _percent_text(row.get("weekly_used_percent", row.get("used_percent")))
    session = _percent_text(row.get("session_used_percent"))
    return f"W {weekly} · S {session}"


def _percent_text(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{round(value)}%"
    return "?"


def _fit_cell(value: str, width: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3].rstrip() + "..."


def _row_warning(row: dict) -> bool:
    return bool(row.get("stale") or row.get("error") or row.get("refresh_error"))


def _row_ready(row: dict) -> bool:
    return bool(row.get("kickable")) and not _row_warning(row)


def _state_text(row: dict) -> str:
    if _row_warning(row):
        return "warning"
    if _row_ready(row):
        return "ready"
    state = row.get("state")
    return str(state) if state else "unknown"


def _next_text(row: dict) -> str:
    if _row_warning(row):
        return "check needed"
    if _row_ready(row):
        kick_type = row.get("kick_type") or "kick"
        return f"{kick_type} ready"
    session_seconds = row.get("session_resets_in_seconds")
    if isinstance(session_seconds, int):
        return f"next session {_compact_relative_seconds(session_seconds)}"
    weekly_seconds = row.get("resets_in_seconds")
    if isinstance(weekly_seconds, int):
        return f"next weekly {_compact_relative_seconds(weekly_seconds)}"
    weekly_human = row.get("resets_in_human")
    if isinstance(weekly_human, str) and weekly_human:
        return f"next weekly {weekly_human}"
    return "unknown"


def _label(row: dict) -> str:
    label = row.get("label")
    return str(label) if label else "unknown"


def _display_label(row: dict) -> str:
    label = _label(row)
    if label.endswith(")") and " (" in label:
        provider, suffix = label.split(" (", 1)
        account = suffix[:-1].strip()
        if provider.strip() and account:
            return f"{provider.strip()} · {account}"
    return label


def _state_icon(row: dict) -> str:
    if _row_warning(row):
        return "⚠️"
    if _row_ready(row):
        return "🟢"
    state = str(row.get("state") or "").lower()
    if state == "active":
        return "🔵"
    if state == "waiting":
        return "🟡"
    return "⚪"


def _warning_detail(row: dict) -> str:
    for key in ("refresh_error", "error", "stale_reason"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _fit_cell(value, 140)
    if row.get("stale"):
        return "stale status"
    return ""


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"


def _compact_relative_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "ready"
    minutes_total = seconds // 60
    if minutes_total < 60:
        return f"{minutes_total}m"
    hours_total = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours_total < 24:
        return f"{hours_total}h{minutes:02d}m"
    days = hours_total // 24
    return f"{days}d{hours_total % 24}h"


def truncate_telegram_reply(text: str, *, limit: int = TELEGRAM_REPLY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n... truncated"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def help_reply() -> str:
    return (
        "TokenKick remote status commands:\n"
        "/status - show cached TokenKick status\n"
        "/refresh - refresh providers and summarize accounts\n"
        "/ping - check listener health\n"
        "/help - show this help\n"
        "\nRead-only: remote kick, wake, and plan commands are not supported."
    )


def ping_reply(*, allowed_chat_id: str) -> str:
    return f"TokenKick Telegram remote is running. Allowed chat: {allowed_chat_id}."


class TelegramRemoteListener:
    def __init__(
        self,
        *,
        client: TelegramRemoteClientProtocol,
        allowed_chat_id: str,
        state_file: Path,
        status_runner: Callable[[], StatusCommandResult],
        refresh_runner: Callable[[], StatusCommandResult] | None = None,
        logger: Callable[[str, dict], None] | None = None,
        bot_username: str | None = None,
    ) -> None:
        self.client = client
        self.allowed_chat_id = allowed_chat_id
        self.state_file = state_file
        self.status_runner = status_runner
        self.refresh_runner = refresh_runner or status_runner
        self.logger = logger or (lambda _event, _fields: None)
        self.bot_username = bot_username

    def check_webhook(self) -> None:
        try:
            info = self.client.get_webhook_info()
        except TelegramRemoteAPIError as exc:
            self._log("telegram_remote_webhook_check_failed", error=str(exc))
            return
        url = info.get("url")
        if isinstance(url, str) and url.strip():
            raise TelegramRemoteConfigError(
                "Telegram webhook is configured for this bot. "
                "Long polling cannot run until the webhook is removed."
            )

    def run_forever(self) -> None:
        self.check_webhook()
        while True:
            offset = self.next_offset()
            try:
                updates = self.client.get_updates(
                    offset=offset,
                    timeout=TELEGRAM_LONG_POLL_TIMEOUT_SECONDS,
                )
            except TelegramRemoteAPIError as exc:
                self._log("telegram_remote_poll_error", error=str(exc))
                continue
            for update in updates:
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                self.handle_update(update)
                save_last_update_id(self.state_file, update_id)

    def next_offset(self) -> int | None:
        last_update_id = load_last_update_id(self.state_file)
        return None if last_update_id is None else last_update_id + 1

    def handle_update(self, update: dict) -> None:
        chat_id = update_chat_id(update)
        if chat_id != self.allowed_chat_id:
            self._log("telegram_remote_unauthorized", chat_id=chat_id)
            return
        command = parse_telegram_command(update_text(update), bot_username=self.bot_username)
        if command is None:
            self._log("telegram_remote_ignored")
            return
        if command == "unknown":
            self._send(chat_id, "Unknown command.\n\n" + help_reply())
            return
        if command == "status":
            self._send(
                chat_id,
                format_status_reply(self.status_runner(), refreshed=False),
                parse_mode="HTML",
            )
            self._log("telegram_remote_status_sent", command=command)
            return
        if command == "refresh":
            self._send(
                chat_id,
                format_status_reply(self.refresh_runner(), refreshed=True),
                parse_mode="HTML",
            )
            self._log("telegram_remote_status_sent", command=command)
            return
        if command == "ping":
            self._send(chat_id, ping_reply(allowed_chat_id=self.allowed_chat_id))
            return
        self._send(chat_id, help_reply())

    def _send(self, chat_id: str, text: str, *, parse_mode: str | None = None) -> None:
        try:
            self.client.send_message(chat_id, text, parse_mode=parse_mode)
        except TelegramRemoteAPIError as exc:
            self._log("telegram_remote_send_error", error=str(exc))

    def _log(self, event: str, **fields: object) -> None:
        self.logger(event, fields)
