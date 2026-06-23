"""Tests for Telegram remote status listener behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tokenkick.telegram_remote import (
    TELEGRAM_LONG_POLL_TIMEOUT_SECONDS,
    TelegramRemoteAPIError,
    TelegramRemoteClient,
    TelegramRemoteConfigError,
    TelegramRemoteListener,
    StatusCommandResult,
    format_status_reply,
    load_last_update_id,
    parse_telegram_command,
    run_status_cached_command,
    run_status_refresh_command,
)


class StopLoop(Exception):
    pass


class FakeClient:
    def __init__(self, updates=None, *, webhook_url=""):
        self.updates = list(updates or [])
        self.webhook_url = webhook_url
        self.messages: list[tuple[str, str, str | None]] = []
        self.get_updates_calls: list[tuple[int | None, int]] = []

    def get_webhook_info(self) -> dict:
        return {"url": self.webhook_url}

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict]:
        self.get_updates_calls.append((offset, timeout))
        if not self.updates:
            raise StopLoop()
        next_update = self.updates.pop(0)
        if isinstance(next_update, BaseException):
            raise next_update
        return [next_update]

    def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None) -> None:
        self.messages.append((chat_id, text, parse_mode))


def test_parse_telegram_commands():
    assert parse_telegram_command("/status") == "status"
    assert parse_telegram_command("/status@TokenKickBot", bot_username="tokenkickbot") == "status"
    assert parse_telegram_command("/refresh") == "refresh"
    assert parse_telegram_command("status") == "status"
    assert parse_telegram_command("/ping") == "ping"
    assert parse_telegram_command("/help") == "help"
    assert parse_telegram_command("/kick") == "unknown"
    assert parse_telegram_command("/status@OtherBot", bot_username="TokenKickBot") is None


def test_unauthorized_chat_is_ignored_and_offset_advances(tmp_path):
    state_file = tmp_path / "telegram-state.json"
    client = FakeClient(
        [
            {
                "update_id": 123,
                "message": {"chat": {"id": 999}, "text": "/status"},
            }
        ]
    )
    logs = []
    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="42",
        state_file=state_file,
        status_runner=lambda: pytest.fail("unauthorized update must not refresh status"),
        logger=lambda event, fields: logs.append((event, fields)),
    )

    with pytest.raises(StopLoop):
        listener.run_forever()

    assert client.messages == []
    assert load_last_update_id(state_file) == 123
    assert logs == [("telegram_remote_unauthorized", {"chat_id": "999"})]


def test_status_command_uses_cached_runner_and_advances_offset(tmp_path):
    state_file = tmp_path / "telegram-state.json"
    state_file.write_text('{"last_update_id": 41}\n')
    client = FakeClient(
        [
            {
                "update_id": 42,
                "message": {"chat": {"id": 42}, "text": "/status"},
            }
        ]
    )
    payload = {
        "accounts": [
            {
                "label": "codex",
                "kickable": True,
                "kick_type": "session",
                "weekly_used_percent": 20,
                "session_used_percent": 0,
            }
        ]
    }
    calls = []

    def cached_runner() -> StatusCommandResult:
        calls.append("cached")
        return StatusCommandResult(0, json.dumps(payload), "")

    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="42",
        state_file=state_file,
        status_runner=cached_runner,
        refresh_runner=lambda: pytest.fail("/status must not refresh providers"),
    )

    with pytest.raises(StopLoop):
        listener.run_forever()

    assert client.get_updates_calls[0] == (42, TELEGRAM_LONG_POLL_TIMEOUT_SECONDS)
    chat_id, text, parse_mode = client.messages[0]
    assert chat_id == "42"
    assert parse_mode == "HTML"
    assert "<pre>" not in text
    assert "cached status" in text
    assert "live refresh" not in text
    assert "codex" in text
    assert "🟢" in text
    assert "session ready" in text
    assert calls == ["cached"]
    assert load_last_update_id(state_file) == 42


def test_refresh_command_uses_refresh_runner(tmp_path):
    client = FakeClient()
    payload = {
        "accounts": [
            {
                "label": "codex",
                "kickable": False,
                "state": "active",
                "weekly_used_percent": 0,
                "session_used_percent": 1,
            }
        ]
    }
    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="42",
        state_file=tmp_path / "telegram-state.json",
        status_runner=lambda: pytest.fail("/refresh must use the refresh runner"),
        refresh_runner=lambda: StatusCommandResult(0, json.dumps(payload), ""),
    )

    listener.handle_update({"message": {"chat": {"id": 42}, "text": "/refresh"}})

    chat_id, text, parse_mode = client.messages[0]
    assert chat_id == "42"
    assert parse_mode == "HTML"
    assert "live refresh" in text
    assert "cached status" not in text
    assert "codex" in text


def test_unknown_command_returns_help(tmp_path):
    client = FakeClient()
    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="42",
        state_file=tmp_path / "telegram-state.json",
        status_runner=lambda: pytest.fail("unknown command must not refresh status"),
    )

    listener.handle_update({"message": {"chat": {"id": 42}, "text": "/kick"}})

    assert "Unknown command" in client.messages[0][1]
    assert "/status" in client.messages[0][1]
    assert "cached TokenKick status" in client.messages[0][1]
    assert client.messages[0][2] is None


def test_status_runners_use_distinct_cli_args(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = '{"accounts":[]}'
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr("tokenkick.telegram_remote.subprocess.run", fake_run)

    assert run_status_cached_command("/bin/tk").exit_code == 0
    assert run_status_refresh_command("/bin/tk").exit_code == 0
    assert calls == [
        ["/bin/tk", "status", "--json-output"],
        ["/bin/tk", "status", "--refresh", "--json-output"],
    ]


def test_format_status_reply_groups_ready_accounts_and_warnings():
    payload = {
        "accounts": [
            {
                "label": "ready",
                "kickable": True,
                "kick_type": "session",
                "weekly_used_percent": 10.0,
                "session_used_percent": 0.0,
            },
            {
                "label": "waiting",
                "kickable": False,
                "state": "waiting",
                "session_resets_in_seconds": 7200,
                "weekly_used_percent": 80.0,
            },
            {
                "label": "stale",
                "kickable": False,
                "stale": True,
                "refresh_error": "provider unavailable",
            },
        ]
    }

    reply = format_status_reply(
        StatusCommandResult(0, json.dumps(payload), ""),
        now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert "<b>TokenKick status</b>" in reply
    assert "live refresh · <code>2026-06-20 12:00:00</code>" in reply
    assert "<pre>" not in reply
    assert "-----" not in reply
    assert "🟢 <b>Ready</b>: 1" in reply
    assert "📊 <b>Accounts</b>: 1" in reply
    assert "⚠️ <b>Warnings</b>: 1" in reply
    assert "ready" in reply
    assert "session ready" in reply
    assert "waiting" in reply
    assert "next session 2h00m" in reply
    assert "stale" in reply
    assert "warning" in reply


def test_format_status_reply_hides_empty_ready_and_warning_sections():
    payload = {
        "accounts": [
            {
                "label": "codex (reserve)",
                "kickable": False,
                "state": "active",
                "session_resets_in_seconds": 180,
                "weekly_used_percent": 0.0,
                "session_used_percent": 1.0,
            }
        ]
    }

    reply = format_status_reply(StatusCommandResult(0, json.dumps(payload), ""))

    assert "<b>Ready</b>" not in reply
    assert "<b>Warnings</b>" not in reply
    assert "📊 <b>Accounts</b>: 1" in reply
    assert "codex · reserve" in reply
    assert "active · W 0% · S 1% · next session 3m" in reply


def test_format_status_reply_reports_failed_status_command():
    reply = format_status_reply(
        StatusCommandResult(1, "", "auth expired\nrerun login"),
        now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert "<b>TokenKick status failed</b>" in reply
    assert "auth expired rerun login" in reply


def test_format_status_reply_notes_omitted_accounts():
    payload = {
        "accounts": [
            {
                "label": f"account-{index}",
                "kickable": False,
                "stale": True,
                "refresh_error": "provider unavailable",
            }
            for index in range(14)
        ]
    }

    reply = format_status_reply(StatusCommandResult(0, json.dumps(payload), ""))

    assert "more accounts omitted" in reply


def test_format_status_reply_escapes_html_and_caps_long_global_errors():
    payload = {
        "refresh_error": "<boom & retry>" * 500,
        "accounts": [
            {
                "label": "account <one> & two",
                "kickable": False,
                "stale": True,
                "refresh_error": "provider <unavailable> & stale",
            }
        ]
    }

    reply = format_status_reply(StatusCommandResult(0, json.dumps(payload), ""))

    assert len(reply) <= 3900
    assert "account &lt;one&gt; &amp; two" in reply
    assert "provider &lt;unavailable&gt; &amp; stale" in reply
    assert "&lt;boom &amp; retry&gt;" in reply
    assert reply.endswith("</i>")


def test_poll_api_failure_logs_and_continues(tmp_path):
    client = FakeClient([TelegramRemoteAPIError("Telegram getUpdates HTTP 500")])
    logs = []
    listener = TelegramRemoteListener(
        client=client,
        allowed_chat_id="42",
        state_file=tmp_path / "telegram-state.json",
        status_runner=lambda: pytest.fail("poll failure must not refresh status"),
        logger=lambda event, fields: logs.append((event, fields)),
    )

    with pytest.raises(StopLoop):
        listener.run_forever()

    assert logs[0][0] == "telegram_remote_poll_error"


def test_webhook_conflict_blocks_long_polling(tmp_path):
    listener = TelegramRemoteListener(
        client=FakeClient(webhook_url="https://example.com/hook"),
        allowed_chat_id="42",
        state_file=tmp_path / "telegram-state.json",
        status_runner=lambda: StatusCommandResult(0, '{"accounts":[]}', ""),
    )

    with pytest.raises(TelegramRemoteConfigError, match="webhook"):
        listener.run_forever()


def test_telegram_client_get_updates_uses_long_poll_params(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true, "result": []}'

        def json(self):
            return {"ok": True, "result": []}

    def fake_get(url, *, params=None, timeout=None):
        calls.append((url, params, timeout))
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)

    updates = TelegramRemoteClient("tok").get_updates(offset=7, timeout=30)

    assert updates == []
    _url, params, timeout = calls[0]
    assert params["offset"] == 7
    assert params["timeout"] == 30
    assert json.loads(params["allowed_updates"]) == ["message"]
    assert timeout == 40


def test_telegram_client_send_message_sets_html_parse_mode(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true, "result": {}}'

        def json(self):
            return {"ok": True, "result": {}}

    def fake_post(url, *, json=None, timeout=None):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)

    TelegramRemoteClient("tok").send_message("42", "<b>hi</b>", parse_mode="HTML")

    _url, payload, _timeout = calls[0]
    assert payload["chat_id"] == "42"
    assert payload["text"] == "<b>hi</b>"
    assert payload["parse_mode"] == "HTML"
