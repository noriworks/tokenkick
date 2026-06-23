"""Auto-kick provider risk-consent contract."""

from tokenkick import models
from tokenkick.consent import (
    AUTO_KICK_CONSENT_VERSION,
    AutoKickConsentRequired,
    auto_kick_consent_text,
    normalize_auto_kick_consents,
)
from tokenkick.models import Config


def test_approved_provider_consent_text():
    claude = auto_kick_consent_text("claude")
    codex = auto_kick_consent_text("codex")

    assert claude.startswith("Enabling auto-kick for Claude\n---------------------------------")
    assert "This is automated access to your account." in claude
    assert "Enabling this is very likely a breach of those terms." in claude
    assert "it may violate Claude's Terms of Service" in claude
    assert claude.endswith(
        "Type ENABLE to turn on auto-kick for Claude, or press Enter to cancel:"
    )

    assert codex.startswith("Enabling auto-kick for Codex\n---------------------------------")
    assert "Whether scheduled kicking falls under that is unsettled" in codex
    assert "treat it as a possible breach." in codex
    assert "it may violate Codex's Terms of Service" in codex
    assert codex.endswith(
        "Type ENABLE to turn on auto-kick for Codex, or press Enter to cancel:"
    )


def test_config_round_trips_versioned_provider_consent(tmp_path, monkeypatch):
    config_dir = tmp_path / "state"
    monkeypatch.setattr(models, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(models, "CONFIG_FILE", config_dir / "config.json")

    config = Config()
    assert config.has_auto_kick_consent("codex") is False
    config.record_auto_kick_consent("codex")
    config.save()

    restored = Config.load()
    assert restored.auto_kick_consents == {"codex": AUTO_KICK_CONSENT_VERSION}
    assert restored.has_auto_kick_consent("codex") is True
    assert restored.has_auto_kick_consent("claude") is False


def test_consent_normalization_rejects_unknown_and_invalid_versions():
    assert normalize_auto_kick_consents(
        {"codex": 1, "claude": True, "gemini": 1, "unknown": 4}
    ) == {"codex": 1}


def test_consent_required_exception_has_actionable_message():
    error = AutoKickConsentRequired("codex")

    assert str(error) == "Auto-kick consent is required for Codex."
    assert error.payload["consent"]["confirmation"] == "ENABLE"
