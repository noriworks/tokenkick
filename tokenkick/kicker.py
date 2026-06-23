"""Kicker — sends a tiny request to anchor a fresh quota window."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    CODEX_SPARK_MODEL_ID,
    CODEX_SPARK_RATE_LIMIT_ID,
    CONFIG_DIR,
    AccountConfig,
    KickEvent,
    account_key_string,
    append_kick_event,
    codex_rate_limit_id_for_account,
)


KICKABLE_PROVIDERS = {"codex", "claude"}
GEMINI_MONITOR_ONLY_MESSAGE = (
    "Gemini is monitor-only: its quota uses daily reset at midnight Pacific time, "
    "not first-use-anchored windows. Kicking has no effect. See docs/PROVIDERS.md for details."
)
GEMINI_AUTO_KICK_DISABLED_MESSAGE = "Gemini is monitor-only; auto-kick cannot be enabled."
DEFAULT_KICK_MODELS: dict[str, str] = {}
CODEX_KICK_PROMPT = (
    "TokenKick quota anchor probe. Do not inspect files or run commands."
)
CODEX_PHANTOM_RECOVERY_KICK_PROMPT = (
    "TokenKick phantom session recovery probe. This account appears to have a tiny "
    "stale session artifact after a reset. Use no tools, inspect no files, and run "
    "no commands. Produce one minimal assistant response so the provider records a "
    "real Codex session anchor."
)
CODEX_PHANTOM_RECOVERY_MODEL_ENV = "TOKENKICK_CODEX_PHANTOM_RECOVERY_MODEL"
CODEX_PHANTOM_RECOVERY_MODELS_ENV = "TOKENKICK_CODEX_PHANTOM_RECOVERY_MODELS"
DEFAULT_CODEX_PHANTOM_RECOVERY_MODELS: tuple[str, ...] = ()
CODEX_NO_GENERATION_EVIDENCE_ERROR = "Codex completed without assistant output or token evidence"
CODEX_KICK_SURFACE_LEGACY = "legacy"
CODEX_KICK_SURFACE_REPO_SKIP = "repo-skip"
CODEX_KICK_SURFACE_REPO = "repo"
CODEX_KICK_SURFACE_INTERACTIVE_LIKE = "interactive-like"
CODEX_KICK_SURFACE_DEFAULT = CODEX_KICK_SURFACE_REPO
CODEX_KICK_SURFACES = (
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_LEGACY,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_SURFACE_INTERACTIVE_LIKE,
)
CLAUDE_KICK_PROMPT = "1+1"
PROVIDER_OUTPUT_EXCERPT_LIMIT = 12000


@dataclass(frozen=True)
class KickInvocation:
    """Prepared provider CLI invocation."""

    command: list[str]
    env: dict[str, str] | None = None
    cwd: Path | None = None
    workspace_git_present: bool | None = None


def kick_model_for_account(
    account: AccountConfig,
    *,
    phantom_recovery: bool = False,
    model_override: str | None = None,
) -> str | None:
    """Return the requested model for quota kicks."""
    if model_override is not None:
        return model_override.strip() or None
    if account.kick_model:
        return account.kick_model.strip() or None
    if account.provider == "codex" and codex_rate_limit_id_for_account(account) == CODEX_SPARK_RATE_LIMIT_ID:
        return CODEX_SPARK_MODEL_ID
    if phantom_recovery and account.provider == "codex":
        model = os.environ.get(CODEX_PHANTOM_RECOVERY_MODEL_ENV, "").strip()
        if model:
            return model
    return DEFAULT_KICK_MODELS.get(account.provider)


def codex_phantom_recovery_model_ladder(account: AccountConfig) -> list[str | None]:
    """Return a deterministic model escalation ladder for Codex phantom recovery."""
    candidates: list[str | None] = [
        kick_model_for_account(account),
        os.environ.get(CODEX_PHANTOM_RECOVERY_MODEL_ENV, "").strip() or None,
    ]
    configured = os.environ.get(CODEX_PHANTOM_RECOVERY_MODELS_ENV, "")
    candidates.extend(model.strip() or None for model in configured.split(","))
    candidates.extend(DEFAULT_CODEX_PHANTOM_RECOVERY_MODELS)

    deduped: list[str | None] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate or ""
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def codex_home_for_account(account: AccountConfig) -> Path:
    """Return the Codex home directory for an account."""
    if account.provider_home:
        return Path(account.provider_home)
    if account.session_path:
        session_path = Path(account.session_path)
        if session_path.name == "sessions":
            return session_path.parent
        return session_path
    return Path.home() / ".codex"


def codex_kick_workspace_for_account(
    account: AccountConfig,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Return the stable git workspace used by repo-surface Codex kicks."""
    root = base_dir or CONFIG_DIR / "codex-kick-workspaces"
    label_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", account.label.strip().lower()).strip("-")
    label_slug = label_slug[:48] or "codex"
    digest = hashlib.sha256(account_key_string(account).encode("utf-8")).hexdigest()[:12]
    return root / f"{label_slug}-{digest}"


def prepare_codex_kick_workspace(
    account: AccountConfig,
    *,
    base_dir: Path | None = None,
) -> tuple[Path, bool]:
    """Create a stable git repo for Codex repo-surface kicks.

    Git initialization is best-effort: TokenKick still attempts the kick from
    the stable workspace even if git is unavailable or refuses to initialize.
    """
    workspace = codex_kick_workspace_for_account(account, base_dir=base_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    readme = workspace / "README.md"
    if not readme.exists():
        readme.write_text(
            "# TokenKick Codex Kick Workspace\n\n"
            "This tiny repo lets TokenKick test Codex quota anchoring through a "
            "real workspace surface without touching user projects.\n",
            encoding="utf-8",
        )
    git_dir = workspace / ".git"
    if not git_dir.exists():
        try:
            subprocess.run(
                ["git", "init"],
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
    return workspace, git_dir.exists()


def kick_account(
    account: AccountConfig,
    *,
    record: bool = True,
    phantom_recovery: bool = False,
    model_override: str | None = None,
    codex_surface: str = CODEX_KICK_SURFACE_DEFAULT,
) -> KickEvent:
    """Send a minimal request to start the quota window.

    Uses the provider CLI in non-interactive mode to send a trivial prompt.
    This is the lightest possible touch: a single tiny completion.
    """
    if account.provider not in KICKABLE_PROVIDERS:
        error = (
            GEMINI_MONITOR_ONLY_MESSAGE
            if account.provider == "gemini"
            else f'Auto-kick only supports Codex and Claude accounts, not "{account.provider}"'
        )
        event = KickEvent(
            label=account.label,
            timestamp=time.time(),
            success=False,
            error=error,
        )
        if record:
            append_kick_event(event)
        return event

    invocation = _kick_invocation(
        account,
        phantom_recovery=phantom_recovery,
        model_override=model_override,
        codex_surface=codex_surface,
    )
    command = invocation.command
    env = invocation.env
    cli_name = command[0]
    prompt_text = _kick_prompt_text(account, command)
    requested_model = kick_model_for_account(
        account,
        phantom_recovery=phantom_recovery,
        model_override=model_override,
    )

    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=invocation.cwd,
        )

        success = result.returncode == 0
        error = None if success else _kick_error_message(cli_name, result)
        metadata = _kick_output_metadata(result.stdout)
        if account.provider == "codex":
            metadata["codex_surface"] = codex_surface
            metadata["evidence_response"] = bool(metadata.get("response_text"))
            metadata["evidence_tokens"] = _codex_token_evidence_seen(metadata)
            metadata["post_kick_status"] = "not_checked"
            excerpt = _provider_output_excerpt(result.stdout)
            if excerpt:
                metadata["provider_output_excerpt"] = excerpt
            if success and not _codex_generation_evidence_seen(metadata):
                error = CODEX_NO_GENERATION_EVIDENCE_ERROR

        event = KickEvent(
            label=account.label,
            timestamp=time.time(),
            success=success,
            confirmed=success and not (account.provider == "codex" and error),
            error=error,
            kick_model=requested_model,
            prompt_text=prompt_text,
            **metadata,
        )

    except FileNotFoundError:
        event = KickEvent(
            label=account.label,
            timestamp=time.time(),
            success=False,
            error=f"{cli_name} CLI not found",
            kick_model=requested_model,
            prompt_text=prompt_text,
        )
    except subprocess.TimeoutExpired:
        event = KickEvent(
            label=account.label,
            timestamp=time.time(),
            success=False,
            error=f"{cli_name} kick timed out after 60s",
            kick_model=requested_model,
            prompt_text=prompt_text,
        )
    except Exception as e:
        event = KickEvent(
            label=account.label,
            timestamp=time.time(),
            success=False,
            error=str(e),
            kick_model=requested_model,
            prompt_text=prompt_text,
        )

    if record:
        append_kick_event(event)
    return event


def kick_invocation_for_account(
    account: AccountConfig,
    *,
    phantom_recovery: bool = False,
    model_override: str | None = None,
    codex_surface: str = CODEX_KICK_SURFACE_DEFAULT,
) -> KickInvocation:
    """Return the provider CLI invocation TokenKick would run for an account."""
    return _kick_invocation(
        account,
        phantom_recovery=phantom_recovery,
        model_override=model_override,
        codex_surface=codex_surface,
    )


def _kick_command(
    account: AccountConfig,
    *,
    phantom_recovery: bool = False,
    model_override: str | None = None,
) -> tuple[list[str], dict[str, str] | None]:
    invocation = _kick_invocation(
        account,
        phantom_recovery=phantom_recovery,
        model_override=model_override,
    )
    return invocation.command, invocation.env


def _kick_invocation(
    account: AccountConfig,
    *,
    phantom_recovery: bool = False,
    model_override: str | None = None,
    codex_surface: str = CODEX_KICK_SURFACE_DEFAULT,
) -> KickInvocation:
    model = kick_model_for_account(
        account,
        phantom_recovery=phantom_recovery,
        model_override=model_override,
    )
    if account.provider == "claude":
        command = ["claude", "-p", CLAUDE_KICK_PROMPT, "--output-format", "json", "--tools", ""]
        if model:
            command.extend(["--model", model])
        return KickInvocation(command=command)

    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home_for_account(account))
    if codex_surface not in CODEX_KICK_SURFACES:
        raise ValueError(f"unknown Codex kick surface {codex_surface!r}")
    command = ["codex", "exec", "--json"]
    cwd: Path | None = None
    workspace_git_present: bool | None = None
    if codex_surface == CODEX_KICK_SURFACE_LEGACY:
        command.append("--skip-git-repo-check")
    elif codex_surface == CODEX_KICK_SURFACE_INTERACTIVE_LIKE:
        cwd = codex_home_for_account(account)
        command.append("--skip-git-repo-check")
        workspace_git_present = (cwd / ".git").exists()
    else:
        cwd, workspace_git_present = prepare_codex_kick_workspace(account)
        if codex_surface == CODEX_KICK_SURFACE_REPO_SKIP:
            command.append("--skip-git-repo-check")
    if model:
        command.extend(["--model", model])
    command.append(_codex_kick_prompt(account, phantom_recovery=phantom_recovery))
    return KickInvocation(
        command=command,
        env=env,
        cwd=cwd,
        workspace_git_present=workspace_git_present,
    )


def _kick_prompt_text(account: AccountConfig, command: list[str]) -> str | None:
    if account.provider == "codex":
        return command[-1] if command else None
    if account.provider == "claude":
        try:
            prompt_index = command.index("-p") + 1
        except ValueError:
            return None
        if prompt_index < len(command):
            return command[prompt_index]
    return None


def _codex_kick_prompt(account: AccountConfig, *, phantom_recovery: bool = False) -> str:
    nonce = time.time_ns()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(nonce / 1_000_000_000))
    base_prompt = (
        CODEX_PHANTOM_RECOVERY_KICK_PROMPT
        if phantom_recovery and account.provider == "codex"
        else CODEX_KICK_PROMPT
    )
    return (
        f"{base_prompt} "
        f"Anchor account label: {account.label}. "
        f"Anchor timestamp: {timestamp}. "
        f"Anchor nonce: {nonce}. "
        "Reply in exactly one short sentence confirming the TokenKick anchor probe completed."
    )


def _kick_output_metadata(stdout: str) -> dict[str, Any]:
    """Extract best-effort model and token usage from provider JSON output."""
    objects = _json_objects(stdout)
    metadata: dict[str, Any] = {}
    for obj in objects:
        if not metadata.get("reported_model"):
            model = _find_model(obj)
            if model:
                metadata["reported_model"] = model
        usage = _find_usage(obj)
        for key, value in usage.items():
            metadata.setdefault(key, value)
        if not metadata.get("response_text"):
            response_text = _find_response_text(obj)
            if response_text:
                metadata["response_text"] = response_text
    return metadata


def _codex_generation_evidence_seen(metadata: dict[str, Any]) -> bool:
    if metadata.get("response_text"):
        return True
    return _codex_token_evidence_seen(metadata)


def _codex_token_evidence_seen(metadata: dict[str, Any]) -> bool:
    return any(
        isinstance(metadata.get(key), int) and metadata[key] > 0
        for key in ("input_tokens", "output_tokens", "total_tokens")
    )


def _kick_error_message(cli_name: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = _kick_output_error(result.stdout) or result.stderr.strip() or result.stdout.strip()
    return f"{cli_name} exited {result.returncode}: {_truncate_error(detail)}"


def _kick_output_error(stdout: str) -> str | None:
    for obj in _json_objects(stdout):
        message = _find_error_message(obj)
        if message:
            return message
    return None


def _find_error_message(value: Any) -> str | None:
    if isinstance(value, str):
        nested = _json_object_from_string(value)
        if nested is not None:
            return _find_error_message(nested)
        return None
    if isinstance(value, dict):
        if value.get("type") == "error":
            message = _find_error_message(value.get("message"))
            if message:
                return message
        error = value.get("error")
        if error is not None:
            message = _find_error_message(error)
            if message:
                return message
        message = value.get("message")
        if isinstance(message, str):
            parsed = _json_object_from_string(message)
            if parsed is not None:
                nested = _find_error_message(parsed)
                if nested:
                    return nested
            value_type = str(value.get("type") or "")
            if (
                value_type in {"error", "turn.failed"}
                or value_type.endswith("_error")
                or "status" in value
            ):
                return message.strip() or None
        for item in value.values():
            found = _find_error_message(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_error_message(item)
            if found:
                return found
    return None


def _json_object_from_string(value: str) -> Any | None:
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _truncate_error(value: str, limit: int = 500) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _provider_output_excerpt(value: str, limit: int = PROVIDER_OUTPUT_EXCERPT_LIMIT) -> str | None:
    value = value.strip()
    if not value:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _json_objects(stdout: str) -> list[Any]:
    stdout = stdout.strip()
    if not stdout:
        return []
    try:
        return [json.loads(stdout)]
    except json.JSONDecodeError:
        objects: list[Any] = []
        for line in stdout.splitlines():
            try:
                objects.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return objects


def _find_model(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"model", "model_id", "modelId"} and isinstance(item, str):
                return item
        for item in value.values():
            found = _find_model(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_model(item)
            if found:
                return found
    return None


def _find_usage(value: Any) -> dict[str, int]:
    best: dict[str, int] = {}
    if isinstance(value, dict):
        best.update(_usage_from_dict(value))
        for item in value.values():
            for key, count in _find_usage(item).items():
                best.setdefault(key, count)
    elif isinstance(value, list):
        for item in value:
            for key, count in _find_usage(item).items():
                best.setdefault(key, count)
    return best


def _find_response_text(value: Any) -> str | None:
    """Extract the assistant's visible answer from provider CLI JSON output."""
    if isinstance(value, str):
        nested = _json_object_from_string(value)
        if nested is not None:
            return _find_response_text(nested)
        return None
    if isinstance(value, dict):
        value_type = str(value.get("type") or "")
        for key in (
            "output_text",
            "response_text",
            "assistant_response",
            "completion",
        ):
            text = value.get(key)
            if isinstance(text, str):
                cleaned = _clean_response_text(text)
                if cleaned:
                    return cleaned

        message = value.get("message")
        if isinstance(message, str):
            if value_type in {"agent_message", "assistant_message"}:
                cleaned = _clean_response_text(message)
                if cleaned:
                    return cleaned
            parsed = _json_object_from_string(message)
            if parsed is not None:
                found = _find_response_text(parsed)
                if found:
                    return found
        elif isinstance(message, dict):
            role = str(message.get("role") or value.get("role") or "").lower()
            found = _find_message_content(message, role=role)
            if found:
                return found

        if str(value.get("role") or "").lower() == "assistant":
            found = _find_message_content(value, role="assistant")
            if found:
                return found
        if value_type in {"agent_message", "assistant_message"}:
            text = value.get("text")
            if isinstance(text, str):
                cleaned = _clean_response_text(text)
                if cleaned:
                    return cleaned
            found = _find_message_content(value, role="assistant")
            if found:
                return found

        for item in value.values():
            found = _find_response_text(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_response_text(item)
            if found:
                return found
    return None


def _find_message_content(value: dict[str, Any], *, role: str) -> str | None:
    if role and role != "assistant":
        return None
    content = value.get("content")
    if isinstance(content, str):
        return _clean_response_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                cleaned = _clean_response_text(item)
            elif isinstance(item, dict):
                cleaned = _clean_response_text(
                    str(
                        item.get("text")
                        or item.get("output_text")
                        or item.get("content")
                        or ""
                    )
                )
            else:
                cleaned = ""
            if cleaned:
                parts.append(cleaned)
        if parts:
            return "\n".join(parts)
    return None


def _clean_response_text(value: str, limit: int = 1000) -> str | None:
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _usage_from_dict(value: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "input_tokens": ("input_tokens", "prompt_tokens", "promptTokenCount"),
        "output_tokens": ("output_tokens", "completion_tokens", "candidatesTokenCount"),
        "total_tokens": ("total_tokens", "totalTokenCount"),
    }
    usage: dict[str, int] = {}
    for output_key, source_keys in mapping.items():
        for source_key in source_keys:
            count = value.get(source_key)
            if isinstance(count, int) and count >= 0:
                usage[output_key] = count
                break
    return usage
