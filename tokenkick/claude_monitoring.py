"""Pure policy and safe diagnostics for Claude usage monitoring."""

from datetime import datetime, timedelta, timezone

from .models import ClaudeProbeError, ClaudeProbeErrorCategory


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def failure_count(value) -> int:
    return min(value, 5) if type(value) is int and value > 0 else 0


def retry_at(entry: dict) -> datetime | None:
    error = entry.get("last_direct_probe_error")
    if not isinstance(error, ClaudeProbeError) or error.category != ClaudeProbeErrorCategory.RATE_LIMITED:
        return None
    attempted = parse_time(entry.get("last_direct_probe_at"))
    if attempted is None:
        return None
    count = max(1, failure_count(entry.get("direct_rate_limit_failures")))
    try:
        return attempted + timedelta(minutes=min(60, 5 * 2 ** (count - 1)))
    except OverflowError:
        return None


def cooling_down(entry: dict, now: datetime) -> bool:
    retry = retry_at(entry)
    # A corrupt/future attempt timestamp must not create an indefinite lockout.
    attempted = parse_time(entry.get("last_direct_probe_at"))
    return retry is not None and attempted <= now < retry


def diagnostics(entry: dict) -> dict:
    error = entry.get("last_direct_probe_error")
    if not isinstance(error, ClaudeProbeError):
        if not entry.get("refresh_error"):
            return {}
        category = "provider_error"
        message = "Claude usage refresh unavailable"
    else:
        category = error.category.value
        message = {
            "rate_limited": "Claude usage refresh rate-limited",
            "not_authenticated": "Claude CLI authentication failed",
            "identity_mismatch": "Claude CLI account identity mismatch",
            "identity_unreadable": "Claude CLI account identity unavailable",
            "binary_missing": "Claude CLI unavailable",
            "timeout": "Claude usage refresh timed out",
            "parse_failed": "Claude usage response could not be parsed",
            "disabled": "Claude direct usage monitoring disabled",
        }.get(category, "Claude usage refresh unavailable")
    status = entry.get("status")
    source = getattr(status, "source_detail", None) or "unknown"
    retry = retry_at(entry)
    return {
        "error_category": category,
        "message": message,
        "observation_source": source,
        "next_retry_at": retry.isoformat().replace("+00:00", "Z") if retry else None,
    }


def diagnostic_text(entry: dict) -> str | None:
    details = diagnostics(entry)
    if not details:
        return None
    source = details["observation_source"]
    status = entry.get("status")
    usable = getattr(getattr(status, "state", None), "value", "unknown") != "unknown"
    if not usable:
        retained = "no usable usage observation"
    elif source == "claude-cli-usage":
        retained = "showing cached direct data"
    elif "codexbar" in source:
        retained = "showing CodexBar fallback data"
    else:
        retained = "showing cached usage data"
    message = f'{details["message"]}; {retained}'
    if details["next_retry_at"]:
        message += f'; local retry allowed after {details["next_retry_at"]}'
    return message
