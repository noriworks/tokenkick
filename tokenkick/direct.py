"""Direct provider identity and zero-side-effect auth helpers."""

from __future__ import annotations

import base64
import binascii
import json
import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CODEX_PROVIDER_USAGE_ENDPOINT = "https://chatgpt.com/backend-api/api/codex/usage"
CODEX_PROVIDER_USAGE_SOURCE_DETAIL = "codex-provider-usage"
LEGACY_CODEX_PROVIDER_USAGE_SOURCE_DETAIL = "codex-appserver-ratelimits"
CODEX_PROVIDER_USAGE_TRANSPORT = "codex-appserver-rpc"
CODEX_PROVIDER_USAGE_TIMEOUT_SECONDS = 10.0
CODEX_PROVIDER_USAGE_REQUEST_ID = 2


def normalize_source_detail(source_detail: str | None) -> str | None:
    if source_detail == LEGACY_CODEX_PROVIDER_USAGE_SOURCE_DETAIL:
        return CODEX_PROVIDER_USAGE_SOURCE_DETAIL
    return source_detail


@dataclass(frozen=True)
class DirectIdentity:
    """Stable local identity metadata for a provider account."""

    provider: str
    provider_account_id: str | None = None
    email: str | None = None
    organization_id: str | None = None
    source_detail: str | None = None


@dataclass(frozen=True)
class CodexProviderUsageRead:
    """Provider usage payload read from OpenAI through Codex's native transport."""

    response: dict[str, Any]
    endpoint: str
    transport: str
    elapsed_ms: int


@dataclass(frozen=True)
class ClaudeAuthStatus:
    """Claude CLI auth status without exposing credentials."""

    logged_in: bool | None
    auth_method: str | None = None
    api_provider: str | None = None
    message: str | None = None


class CodexProviderUsageError(RuntimeError):
    """Expected failure while reading Codex provider usage directly."""


def read_codex_identity(codex_home: Path) -> DirectIdentity | None:
    """Read Codex account identity from auth.json without exposing tokens."""
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        return None
    try:
        data = json.loads(auth_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        return None
    provider_id = _string_value(tokens.get("account_id")) or _string_value(
        data.get("providerAccountID")
    )
    email = email_from_id_token(_string_value(tokens.get("id_token")))
    if not provider_id and not email:
        return None
    return DirectIdentity(
        provider="codex",
        provider_account_id=provider_id,
        email=email,
        source_detail="codex-auth-json",
    )


def read_codex_provider_usage(
    codex_home: Path,
    *,
    timeout_seconds: float = CODEX_PROVIDER_USAGE_TIMEOUT_SECONDS,
) -> CodexProviderUsageRead:
    """Read Codex provider rate-limit state from OpenAI via Codex app-server.

    Plain Python HTTP clients currently receive a Cloudflare challenge for
    chatgpt.com. Codex's app-server uses the same authenticated endpoint
    (`GET /api/codex/usage`) through the native Codex HTTP client, which has the
    Cloudflare cookie handling needed for the direct provider read.
    """
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        raise CodexProviderUsageError(f"Codex auth.json not found at {auth_path}.")

    started_at = time.perf_counter()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = _start_codex_appserver(codex_home)
        response = _request_codex_appserver_usage(proc, timeout_seconds=timeout_seconds)
    finally:
        _stop_codex_appserver_process(proc)

    if response is None:
        raise CodexProviderUsageError(
            "Codex provider usage read timed out after "
            f"{timeout_seconds:.1f}s via {CODEX_PROVIDER_USAGE_TRANSPORT}."
        )
    return CodexProviderUsageRead(
        response=response,
        endpoint=CODEX_PROVIDER_USAGE_ENDPOINT,
        transport=CODEX_PROVIDER_USAGE_TRANSPORT,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


def _start_codex_appserver(codex_home: Path) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    try:
        return subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=codex_home,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CodexProviderUsageError(
            "Codex app-server is unavailable because the codex CLI could not be found."
        ) from exc
    except OSError as exc:
        raise CodexProviderUsageError(f"Codex app-server could not start: {exc}") from exc


def _request_codex_appserver_usage(
    proc: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    assert proc.stdin is not None
    messages = [
        {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "tokenkick",
                    "title": "TokenKick",
                    "version": "0.4.x",
                },
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "notifications/initialized"},
        {"method": "account/rateLimits/read", "id": CODEX_PROVIDER_USAGE_REQUEST_ID},
    ]
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise CodexProviderUsageError(f"Codex provider usage request failed: {exc}") from exc
    return _read_codex_appserver_response(proc, timeout_seconds)


def _read_codex_appserver_response(
    proc: subprocess.Popen[bytes],
    timeout_seconds: float,
) -> dict[str, Any] | None:
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    buffer = b""
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            events = selector.select(timeout=remaining)
            if not events:
                break
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                try:
                    payload = json.loads(raw_line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("id") == CODEX_PROVIDER_USAGE_REQUEST_ID
                ):
                    return payload
        return None
    finally:
        selector.close()


def _stop_codex_appserver_process(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
    else:
        try:
            proc.wait(timeout=0)
        except subprocess.TimeoutExpired:
            pass


def read_claude_identity(config_path: Path | None = None) -> DirectIdentity | None:
    """Read Claude Code account identity from ~/.claude.json."""
    path = config_path or (Path.home() / ".claude.json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    oauth = data.get("oauthAccount") if isinstance(data, dict) else None
    oauth = oauth if isinstance(oauth, dict) else {}
    provider_id = _string_value(oauth.get("accountUuid"))
    email = _string_value(oauth.get("emailAddress"))
    organization_id = _string_value(oauth.get("organizationUuid"))
    user_id = _string_value(data.get("userID")) if isinstance(data, dict) else None
    if not provider_id and not email and not user_id:
        return None
    return DirectIdentity(
        provider="claude",
        provider_account_id=provider_id or user_id,
        email=email,
        organization_id=organization_id,
        source_detail="claude-config-json",
    )


def codex_login_status() -> str:
    """Return a concise Codex auth status diagnostic."""
    try:
        result = subprocess.run(
            ["codex", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return "Codex CLI not found."
    except subprocess.TimeoutExpired:
        return "Codex auth status timed out."
    output = (result.stdout or result.stderr).strip()
    if result.returncode == 0:
        return output or "Codex is logged in."
    return output or f"codex auth status exited {result.returncode}."


def claude_auth_status(
    binary: str = "claude",
    *,
    timeout_seconds: float = 5.0,
) -> ClaudeAuthStatus | None:
    """Return Claude CLI auth state when `claude auth status` is available."""
    try:
        result = subprocess.run(
            [binary, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    output = (result.stdout or result.stderr or "").strip()
    data = _json_object_from_output(output)
    if data is None:
        if result.returncode != 0 and "not logged" in output.lower():
            return ClaudeAuthStatus(
                logged_in=False,
                message=_claude_auth_login_hint(),
            )
        return None

    logged_in = data.get("loggedIn")
    if not isinstance(logged_in, bool):
        return None
    return ClaudeAuthStatus(
        logged_in=logged_in,
        auth_method=_string_value(data.get("authMethod")),
        api_provider=_string_value(data.get("apiProvider")),
        message=None if logged_in else _claude_auth_login_hint(),
    )


def _claude_auth_login_hint() -> str:
    return (
        "Claude CLI is not logged in. Run `claude auth login --claudeai` as the "
        "same user that runs TokenKick, then run `tk status --refresh`."
    )


def _json_object_from_output(output: str) -> dict[str, Any] | None:
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        start = output.find("{")
        end = output.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(output[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def probe_claude_status() -> tuple[bool, str | None]:
    """Run the explicit Claude probe path. This consumes quota by design."""
    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "Reply with only the number 2: what is 1+1?",
                "--output-format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return False, "Claude CLI not found."
    except subprocess.TimeoutExpired:
        return False, "Claude probe timed out after 60s."
    if result.returncode == 0:
        return True, None
    error = (result.stderr or result.stdout).strip()
    return False, error[:240] or f"claude probe exited {result.returncode}."


def email_from_id_token(id_token: str | None) -> str | None:
    if not id_token:
        return None
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _string_value(claims.get("email"))


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
