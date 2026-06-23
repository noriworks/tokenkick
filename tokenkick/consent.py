"""Versioned risk consent for provider auto-kick."""

from __future__ import annotations

AUTO_KICK_CONSENT_VERSION = 1
AUTO_KICK_CONSENT_TOKEN = "ENABLE"
AUTO_KICK_CONSENT_ERROR = "auto_kick_consent_required"

_PROVIDER_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
}

_PROVIDER_FACTS = {
    "claude": (
        "Anthropic's Consumer Terms restrict automated or scripted access to Claude.ai "
        "and Claude Pro outside of the API. Enabling this is very likely a breach of "
        "those terms."
    ),
    "codex": (
        "OpenAI's terms restrict certain automated use of their services. Whether "
        "scheduled kicking falls under that is unsettled — treat it as a possible breach."
    ),
}


def provider_display_name(provider: str) -> str:
    """Return the approved display name for a consent-gated provider."""
    try:
        return _PROVIDER_NAMES[provider]
    except KeyError as exc:
        raise ValueError(f"auto-kick consent is not defined for provider {provider!r}") from exc


def auto_kick_consent_text(provider: str) -> str:
    """Render the approved provider-specific auto-kick disclosure."""
    name = provider_display_name(provider)
    fact = _PROVIDER_FACTS[provider]
    return f"""Enabling auto-kick for {name}
---------------------------------
Auto-kick sends minimal requests to {name} automatically, on a schedule,
without you initiating each one. This is automated access to your account.

{fact}

By typing ENABLE you confirm that you understand and accept:
  - this is automated access to your {name} account
  - it may violate {name}'s Terms of Service
  - any consequence — rate limiting, suspension, loss of access — is yours alone
  - TokenKick and its authors are not responsible for the outcome

Auto-kick is off by default. Enable it only if you accept this risk.

Type ENABLE to turn on auto-kick for {name}, or press Enter to cancel:"""


def normalize_auto_kick_consents(value: object) -> dict[str, int]:
    """Keep only supported provider consent versions from persisted config."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for provider in _PROVIDER_NAMES:
        version = value.get(provider)
        if isinstance(version, int) and not isinstance(version, bool) and version > 0:
            normalized[provider] = version
    return normalized


class AutoKickConsentRequired(Exception):
    """Structured refusal used by non-interactive and app-facing commands."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return f"Auto-kick consent is required for {provider_display_name(self.provider)}."

    @property
    def payload(self) -> dict[str, object]:
        return {
            "consent": {
                "provider": self.provider,
                "version": AUTO_KICK_CONSENT_VERSION,
                "confirmation": AUTO_KICK_CONSENT_TOKEN,
                "text": auto_kick_consent_text(self.provider),
            }
        }
