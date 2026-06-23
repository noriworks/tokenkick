"""User-facing recovery hints for stale provider status failures."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import AccountConfig, DataSource

STALE_REFRESH_RECOVERY_HINT_AFTER_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class CodexRefreshRecoveryHint:
    label: str
    provider_home: Path
    age_seconds: int
    age_text: str

    @property
    def codex_command(self) -> str:
        return f"CODEX_HOME={shlex.quote(str(self.provider_home))} codex"

    @property
    def force_kick_command(self) -> str:
        return f"tk kick {shlex.quote(self.label)} --force"

    @property
    def refresh_command(self) -> str:
        return f"tk status --refresh --account {shlex.quote(self.label)} --verbose"

    @property
    def doctor_fix(self) -> str:
        return (
            f"check `{self.codex_command}`; if Codex opens but refresh still fails, "
            f"run one-time recovery kick `{self.force_kick_command}`, then "
            f"`{self.refresh_command}`"
        )


def codex_refresh_recovery_hint(
    account: AccountConfig,
    cache_entry: dict | None,
    *,
    now: datetime | None = None,
) -> CodexRefreshRecoveryHint | None:
    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or not account.provider_home
        or cache_entry is None
        or not cache_entry.get("refresh_error")
    ):
        return None
    observed_at = _cache_entry_observed_at(cache_entry)
    if observed_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    age_seconds = max(0, int((now.astimezone(timezone.utc) - observed_at).total_seconds()))
    if age_seconds < STALE_REFRESH_RECOVERY_HINT_AFTER_SECONDS:
        return None
    return CodexRefreshRecoveryHint(
        label=account.label,
        provider_home=Path(account.provider_home),
        age_seconds=age_seconds,
        age_text=_format_recovery_age(age_seconds),
    )


def _cache_entry_observed_at(cache_entry: dict) -> datetime | None:
    for key in ("provider_observed_at", "cached_at"):
        value = cache_entry.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _format_recovery_age(seconds: int) -> str:
    minutes = max(0, int(seconds)) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remainder_minutes = minutes % 60
    if hours < 24:
        return f"{hours}h {remainder_minutes}m"
    days = hours // 24
    remainder_hours = hours % 24
    return f"{days}d {remainder_hours}h"
