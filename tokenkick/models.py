"""Core models for TokenKick."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field, fields, asdict, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from .consent import AUTO_KICK_CONSENT_VERSION, normalize_auto_kick_consents
from .state_io import locked_atomic_write_text, locked_update_text


class StateFileError(Exception):
    """Raised when a TokenKick state file cannot be loaded safely."""

    def __init__(
        self,
        path: Path,
        summary: str,
        detail: str,
        recovery: str,
        *,
        line: int | None = None,
    ) -> None:
        self.path = path
        self.summary = summary
        self.detail = detail
        self.recovery = recovery
        self.line = line
        super().__init__(self._message())

    def _message(self) -> str:
        location = f"{self.path}"
        if self.line is not None:
            location = f"{location}:{self.line}"
        return (
            f"{self.summary}\n"
            f"Path: {location}\n"
            f"Detail: {self.detail}\n"
            f"Recovery: {self.recovery}"
        )


def format_local_timestamp(timestamp: float) -> str:
    """Format a Unix timestamp in the local system timezone."""
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def format_notification_timestamp(timestamp: float) -> str:
    """Format a local timestamp for concise user-facing notifications."""
    dt = datetime.fromtimestamp(timestamp).astimezone()
    now = datetime.now(dt.tzinfo)
    if dt.date() == now.date():
        return dt.strftime("%H:%M %Z")
    return f"{dt.day} {dt.strftime('%b, %H:%M %Z')}"


# ---------------------------------------------------------------------------
# Account state machine
# ---------------------------------------------------------------------------

class AccountState(str, Enum):
    """Possible states for a monitored account."""

    FRESH = "fresh"          # Reset available, not yet used
    ACTIVE = "active"        # Window is open and counting down
    WAITING = "waiting"      # Resets in the future
    UNKNOWN = "unknown"      # Can't determine status

    @property
    def emoji(self) -> str:
        return {
            self.FRESH: "🟢",
            self.ACTIVE: "🔵",
            self.WAITING: "🟡",
            self.UNKNOWN: "⚪",
        }[self]

    @property
    def action(self) -> str:
        return {
            self.FRESH: "Kick now",
            self.ACTIVE: "Use if needed",
            self.WAITING: "Wait",
            self.UNKNOWN: "Check login",
        }[self]


# ---------------------------------------------------------------------------
# Data source enum
# ---------------------------------------------------------------------------

class DataSource(str, Enum):
    """Where TokenKick reads rate-limit data from."""

    ANTIGRAVITY_CLI = "antigravity-cli"
    CODEXBAR_CLI = "codexbar-cli"
    CODEXBAR_HTTP = "codexbar-http"
    CODEX_DIRECT = "codex-direct"
    CLAUDE_DIRECT = "claude-direct"
    CODEX_SESSION_FILE = "codex-session-file"
    MANUAL = "manual"


NOTIFICATION_BACKENDS = ("ntfy", "telegram")
ORCHESTRATION_ROLES = ("use_first", "normal", "backup", "specialist", "excluded")
DEFAULT_ORCHESTRATION_ROLE = "normal"


def normalize_orchestration_role(value: object) -> str:
    """Normalize persisted account orchestration role values."""
    role = (
        str(value or DEFAULT_ORCHESTRATION_ROLE)
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    aliases = {
        "first": "use_first",
        "usefirst": "use_first",
        "backup_only": "backup",
        "exclude": "excluded",
    }
    role = aliases.get(role, role)
    if role not in ORCHESTRATION_ROLES:
        raise ValueError(
            "orchestration_role must be one of: "
            + ", ".join(ORCHESTRATION_ROLES)
        )
    return role


def normalize_notification_backends(value: object) -> Optional[list[str]]:
    """Normalize persisted notification backend route lists."""
    if value is None:
        return None
    if isinstance(value, str):
        candidates: list[object] = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        return None
    normalized: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        backend = candidate.strip().lower()
        if backend in NOTIFICATION_BACKENDS and backend not in normalized:
            normalized.append(backend)
    return normalized


# ---------------------------------------------------------------------------
# Account configuration
# ---------------------------------------------------------------------------

@dataclass
class AccountConfig:
    """Configuration for a single monitored account."""

    label: str
    provider: str = "codex"
    source: DataSource = DataSource.MANUAL
    auto_kick: bool = False
    weekly_auto_kick: Optional[bool] = None
    session_auto_kick: Optional[bool] = None
    visible: bool = True
    notifications_enabled: bool = True
    notification_backends: Optional[list[str]] = None

    # Source-specific settings
    codexbar_provider: Optional[str] = None
    codexbar_url: Optional[str] = None
    codexbar_account: Optional[str] = None
    session_path: Optional[str] = None
    provider_home: Optional[str] = None
    identity_provider_id: Optional[str] = None
    identity_email: Optional[str] = None
    identity_org_id: Optional[str] = None
    label_origin: Optional[str] = "user"
    status_probe_enabled: bool = False
    direct_usage_enabled: bool = True
    codex_rate_limit_id: Optional[str] = None
    codex_rate_limit_name: Optional[str] = None
    kick_model: Optional[str] = None
    plan_tier: Optional[str] = None
    usable_session_minutes: Optional[int] = None
    orchestration_role: str = DEFAULT_ORCHESTRATION_ROLE
    weekly_reserve_threshold_percent: Optional[int] = None
    codex_surface_auto_demote: bool = False
    codex_surface_demote_after_strong_clusters: int = 5
    codex_surface_demote_min_active_surfaces: int = 2
    codex_surface_demote_min_kept_anchor_rate: float = 0.95
    codex_surface_demote_measurement_clusters: int = 20
    codex_surface_rescue_cooldown_strong_clusters: int = 20
    codex_surface_force_keep: list[str] = field(default_factory=list)
    codex_surface_force_prune: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.weekly_auto_kick is None:
            self.weekly_auto_kick = self.auto_kick
        if self.session_auto_kick is None:
            self.session_auto_kick = self.auto_kick
        self.notification_backends = normalize_notification_backends(self.notification_backends)
        self.orchestration_role = normalize_orchestration_role(self.orchestration_role)
        if self.weekly_reserve_threshold_percent is not None:
            threshold = int(self.weekly_reserve_threshold_percent)
            if not 1 <= threshold <= 99:
                raise ValueError("weekly_reserve_threshold_percent must be between 1 and 99")
            self.weekly_reserve_threshold_percent = threshold

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["label_origin"] = d.get("label_origin") or "user"
        if d.get("weekly_auto_kick") == self.auto_kick:
            d.pop("weekly_auto_kick", None)
        if not d.get("session_auto_kick") and not self.auto_kick:
            d.pop("session_auto_kick", None)
        if d.get("direct_usage_enabled", True):
            d.pop("direct_usage_enabled", None)
        if d.get("notifications_enabled", True):
            d.pop("notifications_enabled", None)
        if d.get("notification_backends") is None:
            d.pop("notification_backends", None)
        if d.get("orchestration_role") == DEFAULT_ORCHESTRATION_ROLE:
            d.pop("orchestration_role", None)
        if d.get("weekly_reserve_threshold_percent") is None:
            d.pop("weekly_reserve_threshold_percent", None)
        if not d.get("codex_surface_auto_demote", False):
            d.pop("codex_surface_auto_demote", None)
        if d.get("codex_surface_demote_after_strong_clusters") == 5:
            d.pop("codex_surface_demote_after_strong_clusters", None)
        if d.get("codex_surface_demote_min_active_surfaces") == 2:
            d.pop("codex_surface_demote_min_active_surfaces", None)
        if d.get("codex_surface_demote_min_kept_anchor_rate") == 0.95:
            d.pop("codex_surface_demote_min_kept_anchor_rate", None)
        if d.get("codex_surface_demote_measurement_clusters") == 20:
            d.pop("codex_surface_demote_measurement_clusters", None)
        if d.get("codex_surface_rescue_cooldown_strong_clusters") == 20:
            d.pop("codex_surface_rescue_cooldown_strong_clusters", None)
        if not d.get("codex_surface_force_keep"):
            d.pop("codex_surface_force_keep", None)
        if not d.get("codex_surface_force_prune"):
            d.pop("codex_surface_force_prune", None)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> AccountConfig:
        if not isinstance(data, dict):
            raise ValueError("account entry must be an object")
        data = data.copy()
        if "provider_home" not in data and "codex_home" in data:
            data["provider_home"] = data["codex_home"]
        if "source" in data:
            if data["source"] == "codex-ratelimit":
                data["source"] = DataSource.CODEX_SESSION_FILE.value
            try:
                data["source"] = DataSource(data["source"])
            except ValueError as exc:
                raise ValueError(f"unknown account source {data['source']!r}") from exc
        if data.get("label_origin") is None:
            data["label_origin"] = "user"
        if "weekly_auto_kick" not in data:
            data["weekly_auto_kick"] = bool(data.get("auto_kick", False))
        if "session_auto_kick" not in data:
            data["session_auto_kick"] = bool(data.get("auto_kick", False))
        data.setdefault("direct_usage_enabled", True)
        data["codex_surface_force_keep"] = _normalize_codex_surface_list(
            data.get("codex_surface_force_keep", [])
        )
        data["codex_surface_force_prune"] = _normalize_codex_surface_list(
            data.get("codex_surface_force_prune", [])
        )
        allowed_fields = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed_fields})


def account_key(account: AccountConfig) -> tuple[str, str, str]:
    """Return the stable identity tuple used for persisted account state."""
    if account.provider == "codex" and account.source == DataSource.CODEX_DIRECT:
        rate_limit_id = codex_rate_limit_id_for_account(account)
        bucket_suffix = "" if rate_limit_id == CODEX_DEFAULT_RATE_LIMIT_ID else f"#{rate_limit_id}"
        if account.provider_home:
            return ("codex-home", "codex", f"{Path(account.provider_home)}{bucket_suffix}")
        if account.session_path:
            session_path = Path(account.session_path)
            home_path = session_path.parent if session_path.name == "sessions" else session_path
            return ("codex-home", "codex", f"{home_path}{bucket_suffix}")
    if account.identity_provider_id:
        if account.provider == "claude" and account.identity_org_id:
            return (
                "identity",
                "claude",
                f"{account.identity_org_id}:{account.identity_provider_id}",
            )
        return ("identity", account.provider, account.identity_provider_id)
    if account.identity_email:
        return ("identity", account.provider, account.identity_email.lower())
    if account.provider == "codex" and account.codexbar_account:
        return ("account", "codex", account.codexbar_account.lower())
    if account.source == DataSource.CODEXBAR_CLI:
        identity = account.codexbar_account or account.codexbar_provider or account.label
        return (account.source.value, account.provider, identity)
    if account.source == DataSource.CODEX_SESSION_FILE:
        session_path = account.session_path or str(Path.home() / ".codex" / "sessions")
        return (account.source.value, account.provider, session_path)
    return (account.source.value, account.provider, account.label)


def account_key_string(account: AccountConfig) -> str:
    """Return the persisted string form of an account identity."""
    return "|".join(account_key(account))


def codex_rate_limit_id_for_account(account: AccountConfig) -> str:
    """Return the Codex provider bucket id for a direct Codex account."""
    value = (account.codex_rate_limit_id or CODEX_DEFAULT_RATE_LIMIT_ID).strip()
    return value or CODEX_DEFAULT_RATE_LIMIT_ID


# ---------------------------------------------------------------------------
# Rediscovery merge ownership
# ---------------------------------------------------------------------------
#
# Every AccountConfig field must appear in exactly one of the four sets below.
# A test enforces the inventory, so adding a field without classifying it
# fails fast instead of silently resetting user settings on rediscovery.

# Provider/source metadata that fresh discovery is authoritative for.
ACCOUNT_DISCOVERY_OWNED_FIELDS = frozenset(
    {
        "provider",
        "source",
        "codexbar_provider",
        "codexbar_url",
        "codexbar_account",
        "session_path",
        "provider_home",
        "identity_provider_id",
        "identity_email",
        "identity_org_id",
        "codex_rate_limit_id",
        "codex_rate_limit_name",
    }
)

# User-owned fields where discovery supplies the value only while unset
# (for example the Spark kick model and plan tier seeded at discovery).
ACCOUNT_USER_OWNED_DISCOVERY_FALLBACK_FIELDS = frozenset(
    {
        "kick_model",
        "plan_tier",
        "usable_session_minutes",
        "weekly_reserve_threshold_percent",
    }
)

# The label pair follows label-origin rules: preserved on merge, with
# display-label adjustments applied by the discovery labeling pass.
ACCOUNT_LABEL_FIELDS = frozenset({"label", "label_origin"})

# Settings the user controls; rediscovery must never reset them.
ACCOUNT_USER_OWNED_FIELDS = frozenset(
    {
        "auto_kick",
        "weekly_auto_kick",
        "session_auto_kick",
        "visible",
        "notifications_enabled",
        "notification_backends",
        "status_probe_enabled",
        "direct_usage_enabled",
        "orchestration_role",
        "codex_surface_auto_demote",
        "codex_surface_demote_after_strong_clusters",
        "codex_surface_demote_min_active_surfaces",
        "codex_surface_demote_min_kept_anchor_rate",
        "codex_surface_demote_measurement_clusters",
        "codex_surface_rescue_cooldown_strong_clusters",
        "codex_surface_force_keep",
        "codex_surface_force_prune",
    }
)


def merge_discovered_account(
    existing: AccountConfig,
    discovered: AccountConfig,
    *,
    preserve_label: bool = True,
) -> AccountConfig:
    """Merge a freshly discovered account onto its saved configuration.

    Starts from the saved account so user-owned fields are preserved by
    default, then overlays discovery-owned provider metadata from the
    discovered account. Fallback fields keep the saved value unless it is
    unset. With ``preserve_label=False`` the discovered label pair wins,
    for callers whose input labels already went through display-label rules.
    """
    overrides: dict[str, object] = {
        name: getattr(discovered, name) for name in ACCOUNT_DISCOVERY_OWNED_FIELDS
    }
    for name in ACCOUNT_USER_OWNED_DISCOVERY_FALLBACK_FIELDS:
        existing_value = getattr(existing, name)
        overrides[name] = (
            existing_value if existing_value is not None else getattr(discovered, name)
        )
    if not preserve_label:
        overrides["label"] = discovered.label
        overrides["label_origin"] = discovered.label_origin
    return replace(existing, **overrides)


# ---------------------------------------------------------------------------
# Account status (runtime observation)
# ---------------------------------------------------------------------------

@dataclass
class AccountStatus:
    """Observed status of an account at a point in time."""

    label: str
    state: AccountState
    used_percent: Optional[float] = None
    resets_in_seconds: Optional[int] = None
    resets_at: Optional[float] = None
    window_minutes: Optional[int] = None
    session_used_percent: Optional[float] = None
    session_resets_in_seconds: Optional[int] = None
    session_resets_at: Optional[float] = None
    session_window_minutes: Optional[int] = None
    balance_remaining: Optional[float] = None
    balance_limit: Optional[float] = None
    balance_spent_percent: Optional[float] = None
    last_kicked: Optional[float] = None  # Unix timestamp
    error: Optional[str] = None
    observed_at: Optional[str] = None
    source_detail: Optional[str] = None
    codex_rate_limit_id: Optional[str] = None
    codex_rate_limit_name: Optional[str] = None
    window_anchor_state: Optional[str] = None
    quota_windows: Optional[list[dict[str, Any]]] = None
    source_diagnostics: Optional[dict[str, Any]] = None
    stale: bool = False
    stale_seconds: Optional[int] = None

    @property
    def resets_in_human(self) -> str:
        seconds = self._effective_resets_in_seconds()
        if seconds is None:
            return "—"
        if seconds <= 0:
            return "reset ready"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def resets_at_local(self) -> str:
        if self.resets_at is not None:
            reset_at = self.resets_at
        elif self.resets_in_seconds is not None:
            reset_at = time.time() + self.resets_in_seconds
        else:
            return "—"
        if reset_at <= time.time():
            return "reset ready"
        return format_local_timestamp(reset_at)

    def _effective_resets_in_seconds(self) -> Optional[int]:
        if self.resets_at is not None:
            return max(0, int(self.resets_at - time.time()))
        return self.resets_in_seconds

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["resets_in_human"] = self.resets_in_human
        d["resets_at_local"] = self.resets_at_local
        return {k: v for k, v in d.items() if v is not None}


SYNTHETIC_STATUS_REASON_ATTR = "_tokenkick_synthetic_status_reason"


def mark_synthetic_status(status: AccountStatus, reason: str) -> AccountStatus:
    setattr(status, SYNTHETIC_STATUS_REASON_ATTR, reason)
    return status


def synthetic_status_reason(status: AccountStatus) -> str | None:
    reason = getattr(status, SYNTHETIC_STATUS_REASON_ATTR, None)
    return reason if isinstance(reason, str) else None


def is_synthetic_status(status: AccountStatus) -> bool:
    return synthetic_status_reason(status) is not None


def weekly_quota_exhausted(status: AccountStatus) -> bool:
    return (
        status.state != AccountState.FRESH
        and status.used_percent is not None
        and status.used_percent >= 100.0
        and _weekly_quota_window_active(status)
    )


def _weekly_quota_window_active(status: AccountStatus) -> bool:
    if status.resets_in_seconds is not None:
        return status.resets_in_seconds > 0
    if status.resets_at is not None:
        return status.resets_at > time.time()
    return True


class ClaudeProbeErrorCategory(str, Enum):
    """Stable Claude direct /usage error categories for cache and doctor output."""

    BINARY_MISSING = "binary_missing"
    NOT_AUTHENTICATED = "not_authenticated"
    TIMEOUT = "timeout"
    PARSE_FAILED = "parse_failed"
    RATE_LIMITED = "rate_limited"
    IDENTITY_MISMATCH = "identity_mismatch"
    IDENTITY_UNREADABLE = "identity_unreadable"
    DISABLED = "disabled"
    PROVIDER_ERROR = "provider_error"


@dataclass
class ClaudeProbeError:
    """Structured Claude direct /usage probe failure metadata."""

    category: ClaudeProbeErrorCategory
    message: str
    raw: Optional[str] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["category"] = self.category.value
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, data: dict | None) -> Optional[ClaudeProbeError]:
        if not isinstance(data, dict):
            return None
        category = data.get("category")
        message = data.get("message")
        if not isinstance(category, str) or not isinstance(message, str):
            return None
        try:
            parsed_category = ClaudeProbeErrorCategory(category)
        except ValueError:
            parsed_category = ClaudeProbeErrorCategory.PROVIDER_ERROR
        raw = data.get("raw")
        return cls(
            category=parsed_category,
            message=message,
            raw=raw if isinstance(raw, str) else None,
        )


@dataclass
class ClaudeProbeContext:
    """Cache metadata available to the Claude direct /usage source."""

    direct_usage_enabled: bool = True
    last_direct_probe_at: Optional[str] = None
    last_direct_probe_error: Optional[ClaudeProbeError] = None
    last_direct_success_at: Optional[str] = None
    last_direct_success_status: Optional[AccountStatus] = None


@dataclass
class ClaudeConfig:
    """Global Claude provider settings."""

    direct_usage_enabled: bool = True
    direct_usage_explicit: bool = False

    def to_dict(self) -> dict:
        data = {"direct_usage_enabled": self.direct_usage_enabled}
        if self.direct_usage_explicit:
            data["direct_usage_explicit"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> ClaudeConfig:
        if not isinstance(data, dict):
            return cls()
        direct_usage_enabled = bool(data.get("direct_usage_enabled", True))
        direct_usage_explicit = bool(data.get("direct_usage_explicit", False))
        return cls(
            direct_usage_enabled=direct_usage_enabled,
            direct_usage_explicit=direct_usage_explicit,
        )


# ---------------------------------------------------------------------------
# Kick history entry
# ---------------------------------------------------------------------------

@dataclass
class KickEvent:
    """Record of a single kick action."""

    label: str
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    error: Optional[str] = None
    confirmed: bool = True
    kind: str = "kick"
    kick_type: Optional[str] = None
    kick_model: Optional[str] = None
    reported_model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    prompt_text: Optional[str] = None
    response_text: Optional[str] = None
    provider_output_excerpt: Optional[str] = None
    codex_surface: Optional[str] = None
    codex_attempt: Optional[int] = None
    codex_max_attempts: Optional[int] = None
    codex_cluster_id: Optional[str] = None
    codex_cluster_origin: Optional[str] = None
    codex_attempt_started_at: Optional[float] = None
    codex_attempt_finished_at: Optional[float] = None
    codex_inferred_anchor_at: Optional[float] = None
    codex_anchor_match_delta_seconds: Optional[float] = None
    codex_confirmation_method: Optional[str] = None
    codex_attribution: Optional[str] = None
    codex_provider_observed_at: Optional[str] = None
    codex_provider_session_resets_at: Optional[float] = None
    codex_provider_session_used_percent: Optional[float] = None
    codex_provider_stale: Optional[bool] = None
    evidence_response: Optional[bool] = None
    evidence_tokens: Optional[bool] = None
    evidence_provider_moved: Optional[bool] = None
    post_kick_status: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kick_type"] = self.kick_type or self.kind
        d["timestamp_local"] = format_local_timestamp(self.timestamp)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> KickEvent:
        data = data.copy()
        data.pop("timestamp_local", None)
        data.setdefault("confirmed", True)
        if "kick_type" in data and "kind" not in data:
            data["kind"] = data["kick_type"]
        data.setdefault("kind", "kick")
        allowed_fields = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed_fields})


# ---------------------------------------------------------------------------
# Notification config
# ---------------------------------------------------------------------------

@dataclass
class NotifyConfig:
    """Notification settings."""

    enabled: bool = False
    backend: str = "ntfy"
    policy: str = "all"
    ntfy_topic: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    enabled_backends: Optional[list[str]] = None

    def __post_init__(self) -> None:
        self.enabled_backends = normalize_notification_backends(self.enabled_backends)
        self.policy = _normalize_notification_policy(self.policy)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> NotifyConfig:
        if not isinstance(data, dict):
            return cls()
        allowed_fields = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed_fields})


def _normalize_notification_policy(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in {"all", "errors"}:
        return value.strip().lower()
    return "all"


# ---------------------------------------------------------------------------
# Smart scheduling config
# ---------------------------------------------------------------------------

@dataclass
class WorkSchedule:
    """Configured deep-work windows for smart kick scheduling."""

    enabled: bool = False
    weekdays: Optional[str] = None
    weekends: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict | None) -> WorkSchedule:
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            weekdays=data.get("weekdays"),
            weekends=data.get("weekends"),
        )

    def is_default(self) -> bool:
        return not self.enabled and self.weekdays is None and self.weekends is None


@dataclass
class ScheduleConfig:
    """Top-level smart scheduling configuration."""

    enabled: bool = False
    timezone: Optional[str] = None
    scheduling_target: str = "auto"
    default: WorkSchedule = field(default_factory=WorkSchedule)
    accounts: dict[str, WorkSchedule] = field(default_factory=dict)
    usable_session_tier_defaults: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "enabled": self.enabled,
            "timezone": self.timezone,
            "scheduling_target": self.scheduling_target,
            "default": self.default.to_dict(),
            "accounts": {
                label: schedule.to_dict()
                for label, schedule in self.accounts.items()
            },
            "usable_session_tier_defaults": self.usable_session_tier_defaults,
        }
        if data["timezone"] is None:
            data.pop("timezone")
        if data["scheduling_target"] == "auto":
            data.pop("scheduling_target")
        if not data["usable_session_tier_defaults"]:
            data.pop("usable_session_tier_defaults")
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> ScheduleConfig:
        if not isinstance(data, dict):
            return cls()
        accounts = {
            str(label): WorkSchedule.from_dict(schedule)
            for label, schedule in data.get("accounts", {}).items()
            if isinstance(schedule, dict)
        }
        scheduling_target = data.get("scheduling_target", "auto")
        if scheduling_target not in {"auto", "primary", "session"}:
            scheduling_target = "auto"
        usable_defaults = {
            str(key): int(value)
            for key, value in data.get("usable_session_tier_defaults", {}).items()
            if isinstance(key, str)
        }
        return cls(
            enabled=bool(data.get("enabled", False)),
            timezone=data.get("timezone"),
            scheduling_target=scheduling_target,
            default=WorkSchedule.from_dict(data.get("default")),
            accounts=accounts,
            usable_session_tier_defaults=usable_defaults,
        )

    def is_default(self) -> bool:
        return (
            not self.enabled
            and self.timezone is None
            and self.scheduling_target == "auto"
            and self.default.is_default()
            and not self.accounts
            and not self.usable_session_tier_defaults
        )


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".tokenkick"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
GEMINI_MONITOR_ONLY_MIGRATION_KEY = "gemini-monitor-only-v1"
GEMINI_MONITOR_ONLY_NOTICE = (
    "Note: Gemini auto-kick has been disabled (Gemini is now monitor-only). "
    "See docs/PROVIDERS.md."
)
CODEX_FIRE_ALL_SURFACE_NAMES = (
    "legacy",
    "repo-skip",
    "repo",
    "interactive-like",
)
CODEX_DEFAULT_RATE_LIMIT_ID = "codex"
CODEX_SPARK_RATE_LIMIT_ID = "codex_bengalfox"
CODEX_SPARK_MODEL_ID = "gpt-5.3-codex-spark"
CODEX_BURST_LADDER_DEFAULT_GAP_SECONDS = 90
CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS = 30


@dataclass
class Config:
    """Top-level TokenKick configuration."""

    accounts: list[AccountConfig] = field(default_factory=list)
    notifications: NotifyConfig = field(default_factory=NotifyConfig)
    poll_interval_minutes: int = 5
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    codexbar_staleness_threshold_seconds: int = 900
    codexbar_rejection_threshold_seconds: int = 86400
    codex_surface_retry_backoff_seconds: int = 900
    codex_burst_ladder_enabled: bool = False
    codex_burst_ladder_gap_seconds: int = CODEX_BURST_LADDER_DEFAULT_GAP_SECONDS
    codex_burst_ladder_surface_order: list[str] = field(default_factory=list)
    codex_fire_all_surfaces: bool = False
    codex_fire_all_surface_gap_seconds: int = CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS
    codex_fire_all_surface_order: list[str] = field(default_factory=list)
    global_reset_notify_min_confidence: str = "likely"
    telegram_remote_enabled: bool = False
    auto_kick_consents: dict[str, int] = field(default_factory=dict)
    migrations: dict[str, bool] = field(default_factory=dict)
    loaded_from_file: bool = False

    def __post_init__(self) -> None:
        self.auto_kick_consents = normalize_auto_kick_consents(self.auto_kick_consents)
        self.global_reset_notify_min_confidence = _normalize_global_reset_confidence(
            self.global_reset_notify_min_confidence
        )
        self.codex_burst_ladder_surface_order = _normalize_codex_fire_all_surface_order(
            self.codex_burst_ladder_surface_order
        )
        self.codex_fire_all_surface_order = _normalize_codex_fire_all_surface_order(
            self.codex_fire_all_surface_order
        )
        if self.codex_fire_all_surfaces:
            self.codex_burst_ladder_enabled = True
        if not self.codex_burst_ladder_surface_order and self.codex_fire_all_surface_order:
            self.codex_burst_ladder_surface_order = list(self.codex_fire_all_surface_order)
        if (
            self.codex_burst_ladder_gap_seconds == CODEX_BURST_LADDER_DEFAULT_GAP_SECONDS
            and self.codex_fire_all_surface_gap_seconds != CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS
        ):
            self.codex_burst_ladder_gap_seconds = self.codex_fire_all_surface_gap_seconds
        self.codex_fire_all_surfaces = self.codex_burst_ladder_enabled
        self.codex_fire_all_surface_gap_seconds = self.codex_burst_ladder_gap_seconds
        self.codex_fire_all_surface_order = list(self.codex_burst_ladder_surface_order)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "accounts": [a.to_dict() for a in self.accounts],
            "notifications": self.notifications.to_dict(),
            "poll_interval_minutes": self.poll_interval_minutes,
            "claude": self.claude.to_dict(),
            "codexbar_staleness_threshold_seconds": self.codexbar_staleness_threshold_seconds,
            "codexbar_rejection_threshold_seconds": self.codexbar_rejection_threshold_seconds,
            "codex_surface_retry_backoff_seconds": self.codex_surface_retry_backoff_seconds,
            "codex_burst_ladder_enabled": self.codex_burst_ladder_enabled,
            "codex_burst_ladder_gap_seconds": self.codex_burst_ladder_gap_seconds,
            "codex_burst_ladder_surface_order": self.codex_burst_ladder_surface_order,
            "codex_fire_all_surfaces": self.codex_fire_all_surfaces,
            "codex_fire_all_surface_gap_seconds": self.codex_fire_all_surface_gap_seconds,
            "codex_fire_all_surface_order": self.codex_fire_all_surface_order,
            "global_reset_notify_min_confidence": self.global_reset_notify_min_confidence,
            "telegram_remote_enabled": self.telegram_remote_enabled,
        }
        if self.auto_kick_consents:
            data["auto_kick_consents"] = self.auto_kick_consents
        if self.migrations:
            data["migrations"] = self.migrations
        if not self.schedule.is_default():
            data["schedule"] = self.schedule.to_dict()
        locked_atomic_write_text(CONFIG_FILE, json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_FILE.exists():
            return cls()
        try:
            raw = CONFIG_FILE.read_text()
        except OSError as exc:
            raise StateFileError(
                CONFIG_FILE,
                "TokenKick config could not be read.",
                str(exc),
                "Check file permissions, then rerun the command.",
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateFileError(
                CONFIG_FILE,
                "TokenKick config is not valid JSON.",
                f"{exc.msg} at line {exc.lineno}, column {exc.colno}.",
                "Repair the JSON or move the file aside and run tk setup to recreate it.",
                line=exc.lineno,
            ) from exc
        if not isinstance(data, dict):
            raise StateFileError(
                CONFIG_FILE,
                "TokenKick config has an invalid shape.",
                "Top-level config must be a JSON object.",
                "Repair the config file or move it aside and run tk setup to recreate it.",
            )
        try:
            accounts = _load_account_configs(data.get("accounts", []))
            config = cls(
                accounts=accounts,
                notifications=NotifyConfig.from_dict(data.get("notifications", {})),
                poll_interval_minutes=data.get("poll_interval_minutes", 5),
                schedule=ScheduleConfig.from_dict(data.get("schedule")),
                claude=ClaudeConfig.from_dict(data.get("claude")),
                codexbar_staleness_threshold_seconds=int(
                    data.get("codexbar_staleness_threshold_seconds", 900)
                ),
                codexbar_rejection_threshold_seconds=int(
                    data.get("codexbar_rejection_threshold_seconds", 86400)
                ),
                codex_surface_retry_backoff_seconds=int(
                    data.get("codex_surface_retry_backoff_seconds", 900)
                ),
                codex_burst_ladder_enabled=bool(
                    _canonical_codex_burst_ladder_enabled(data)
                ),
                codex_burst_ladder_gap_seconds=int(
                    _canonical_codex_burst_ladder_gap_seconds(data)
                ),
                codex_burst_ladder_surface_order=_normalize_codex_fire_all_surface_order(
                    _canonical_codex_burst_ladder_surface_order(data)
                ),
                codex_fire_all_surfaces=bool(
                    _legacy_codex_fire_all_surfaces(data)
                ),
                codex_fire_all_surface_gap_seconds=int(
                    _legacy_codex_fire_all_surface_gap_seconds(data)
                ),
                codex_fire_all_surface_order=_normalize_codex_fire_all_surface_order(
                    _legacy_codex_fire_all_surface_order(data)
                ),
                global_reset_notify_min_confidence=data.get(
                    "global_reset_notify_min_confidence",
                    "likely",
                ),
                telegram_remote_enabled=bool(data.get("telegram_remote_enabled", False)),
                auto_kick_consents=normalize_auto_kick_consents(
                    data.get("auto_kick_consents", {})
                ),
                migrations={
                    key: bool(value)
                    for key, value in data.get("migrations", {}).items()
                    if isinstance(key, str)
                },
                loaded_from_file=True,
            )
        except StateFileError:
            raise
        except (TypeError, ValueError) as exc:
            raise StateFileError(
                CONFIG_FILE,
                "TokenKick config has invalid values.",
                str(exc),
                "Repair the config file or move it aside and run tk setup to recreate it.",
            ) from exc
        config._disable_gemini_auto_kick_if_needed()
        return config

    def _disable_gemini_auto_kick_if_needed(self) -> None:
        updated_accounts: list[AccountConfig] = []
        changed = False
        for account in self.accounts:
            if account.provider == "gemini" and (account.auto_kick or account.session_auto_kick):
                updated_accounts.append(
                    replace(
                        account,
                        auto_kick=False,
                        weekly_auto_kick=False,
                        session_auto_kick=False,
                    )
                )
                changed = True
            else:
                updated_accounts.append(account)
        if not changed:
            return

        emit_notice = not self.migrations.get(GEMINI_MONITOR_ONLY_MIGRATION_KEY)
        self.accounts = updated_accounts
        self.migrations = {**self.migrations, GEMINI_MONITOR_ONLY_MIGRATION_KEY: True}
        self.save()
        if emit_notice:
            print(GEMINI_MONITOR_ONLY_NOTICE, file=sys.stderr)

    def has_auto_kick_consent(self, provider: str) -> bool:
        return self.auto_kick_consents.get(provider) == AUTO_KICK_CONSENT_VERSION

    def record_auto_kick_consent(self, provider: str) -> None:
        self.auto_kick_consents = {
            **self.auto_kick_consents,
            provider: AUTO_KICK_CONSENT_VERSION,
        }


def _canonical_codex_burst_ladder_enabled(data: dict) -> object:
    return data.get(
        "codex_burst_ladder_enabled",
        data.get("codex_fire_all_surfaces", False),
    )


def _canonical_codex_burst_ladder_gap_seconds(data: dict) -> object:
    if "codex_burst_ladder_gap_seconds" in data:
        return data["codex_burst_ladder_gap_seconds"]
    legacy_gap = data.get(
        "codex_fire_all_surface_gap_seconds",
        CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS,
    )
    if legacy_gap == CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS:
        return CODEX_BURST_LADDER_DEFAULT_GAP_SECONDS
    return legacy_gap


def _canonical_codex_burst_ladder_surface_order(data: dict) -> object:
    return data.get(
        "codex_burst_ladder_surface_order",
        data.get("codex_fire_all_surface_order", []),
    )


def _legacy_codex_fire_all_surfaces(data: dict) -> object:
    return data.get(
        "codex_fire_all_surfaces",
        data.get("codex_burst_ladder_enabled", False),
    )


def _legacy_codex_fire_all_surface_gap_seconds(data: dict) -> object:
    return data.get(
        "codex_fire_all_surface_gap_seconds",
        data.get(
            "codex_burst_ladder_gap_seconds",
            CODEX_FIRE_ALL_LEGACY_DEFAULT_GAP_SECONDS,
        ),
    )


def _legacy_codex_fire_all_surface_order(data: dict) -> object:
    return data.get(
        "codex_fire_all_surface_order",
        data.get("codex_burst_ladder_surface_order", []),
    )


def _normalize_codex_fire_all_surface_order(raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('"codex_burst_ladder_surface_order" must be a list of surface names')
    order: list[str] = []
    seen: set[str] = set()
    valid = set(CODEX_FIRE_ALL_SURFACE_NAMES)
    for item in raw:
        if not isinstance(item, str):
            raise ValueError('"codex_burst_ladder_surface_order" entries must be strings')
        surface = item.strip()
        if not surface:
            raise ValueError('"codex_burst_ladder_surface_order" contains an empty surface name')
        if surface not in valid:
            raise ValueError(
                f'unknown Codex surface "{surface}"; valid surfaces are '
                f"{', '.join(CODEX_FIRE_ALL_SURFACE_NAMES)}"
            )
        if surface in seen:
            raise ValueError(f'duplicate Codex surface "{surface}"')
        seen.add(surface)
        order.append(surface)
    return order


def _normalize_global_reset_confidence(raw: object) -> str:
    if raw is None:
        return "likely"
    value = str(raw).strip().lower()
    if value not in {"possible", "likely", "confirmed"}:
        raise ValueError(
            '"global_reset_notify_min_confidence" must be one of: possible, likely, confirmed'
        )
    return value


def _normalize_codex_surface_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Codex surface override entries must be a list of surface names")
    surfaces: list[str] = []
    seen: set[str] = set()
    valid = set(CODEX_FIRE_ALL_SURFACE_NAMES)
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("Codex surface override entries must be strings")
        surface = item.strip()
        if surface not in valid:
            raise ValueError(
                f'unknown Codex surface "{surface}"; valid surfaces are '
                f"{', '.join(CODEX_FIRE_ALL_SURFACE_NAMES)}"
            )
        if surface in seen:
            raise ValueError(f'duplicate Codex surface override "{surface}"')
        seen.add(surface)
        surfaces.append(surface)
    return surfaces


def _load_account_configs(raw_accounts: object) -> list[AccountConfig]:
    if raw_accounts is None:
        return []
    if not isinstance(raw_accounts, list):
        raise StateFileError(
            CONFIG_FILE,
            "TokenKick config has an invalid accounts section.",
            '"accounts" must be a list.',
            "Repair the accounts section or move the config aside and run tk setup.",
        )
    accounts: list[AccountConfig] = []
    for index, account_data in enumerate(raw_accounts):
        try:
            accounts.append(AccountConfig.from_dict(account_data))
        except (TypeError, ValueError) as exc:
            raise StateFileError(
                CONFIG_FILE,
                "TokenKick config has an invalid account entry.",
                f"accounts[{index}]: {exc}",
                "Repair this account entry or remove it, then rerun the command.",
            ) from exc
    return accounts


def append_kick_event(event: KickEvent) -> None:
    """Append a kick event to the history file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event.to_dict()) + "\n"

    def append_line(current: str) -> str:
        if current and not current.endswith("\n"):
            current = f"{current}\n"
        return f"{current}{line}"

    locked_update_text(HISTORY_FILE, append_line)


def update_kick_history(update: Callable[[list[KickEvent]], bool]) -> bool:
    """Atomically rewrite kick history if ``update`` mutates loaded events."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    changed = False

    def apply_update(current: str) -> str:
        nonlocal changed
        lines = current.splitlines()
        events = _parse_kick_history_lines(lines)
        changed = bool(update(events))
        if not changed:
            return current
        return "".join(json.dumps(event.to_dict()) + "\n" for event in events)

    locked_update_text(HISTORY_FILE, apply_update)
    return changed


def load_kick_history(limit: int = 50) -> list[KickEvent]:
    """Load recent kick history."""
    if not HISTORY_FILE.exists():
        return []
    try:
        lines = HISTORY_FILE.read_text().splitlines()
    except OSError as exc:
        raise StateFileError(
            HISTORY_FILE,
            "TokenKick history could not be read.",
            str(exc),
            "Check file permissions, then rerun the command.",
        ) from exc
    if limit <= 0:
        selected: list[tuple[int, str]] = []
    else:
        start = max(0, len(lines) - limit)
        selected = list(enumerate(lines[start:], start=start + 1))
    return _parse_kick_history_lines(selected)


def _parse_kick_history_lines(lines: list[str] | list[tuple[int, str]]) -> list[KickEvent]:
    events: list[KickEvent] = []
    normalized: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        if isinstance(line, tuple):
            normalized.append(line)
        else:
            normalized.append((index, line))
    for line_number, line in normalized:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StateFileError(
                HISTORY_FILE,
                "TokenKick history contains malformed JSON.",
                f"{exc.msg} at line {line_number}, column {exc.colno}.",
                "Repair or remove the malformed history line, then rerun the command.",
                line=line_number,
            ) from exc
        if not isinstance(data, dict):
            raise StateFileError(
                HISTORY_FILE,
                "TokenKick history has an invalid event entry.",
                f"line {line_number}: event must be a JSON object.",
                "Repair or remove this history line, then rerun the command.",
                line=line_number,
            )
        try:
            events.append(KickEvent.from_dict(data))
        except (TypeError, ValueError) as exc:
            raise StateFileError(
                HISTORY_FILE,
                "TokenKick history has an invalid event entry.",
                f"line {line_number}: {exc}",
                "Repair or remove this history line, then rerun the command.",
                line=line_number,
            ) from exc
    return events
