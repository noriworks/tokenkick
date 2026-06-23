"""Tests for direct provider integrations."""

import pytest

from tokenkick.direct import (
    CODEX_PROVIDER_USAGE_ENDPOINT,
    CODEX_PROVIDER_USAGE_TRANSPORT,
    CodexProviderUsageError,
    read_codex_provider_usage,
)


def test_read_codex_provider_usage_requires_auth_json(tmp_path):
    with pytest.raises(CodexProviderUsageError, match="auth.json not found"):
        read_codex_provider_usage(tmp_path)


def test_read_codex_provider_usage_uses_codex_home_transport(monkeypatch, tmp_path):
    (tmp_path / "auth.json").write_text("{}\n")
    started: list[object] = []
    stopped: list[object] = []
    fake_proc = object()

    def fake_start(codex_home):
        started.append(codex_home)
        return fake_proc

    def fake_request(proc, *, timeout_seconds):
        assert proc is fake_proc
        assert timeout_seconds == 1.5
        return {"result": {"rateLimits": {"limitId": "codex"}}}

    monkeypatch.setattr("tokenkick.direct._start_codex_appserver", fake_start)
    monkeypatch.setattr("tokenkick.direct._request_codex_appserver_usage", fake_request)
    monkeypatch.setattr("tokenkick.direct._stop_codex_appserver_process", stopped.append)

    usage = read_codex_provider_usage(tmp_path, timeout_seconds=1.5)

    assert started == [tmp_path]
    assert stopped == [fake_proc]
    assert usage.response["result"]["rateLimits"]["limitId"] == "codex"
    assert usage.endpoint == CODEX_PROVIDER_USAGE_ENDPOINT
    assert usage.transport == CODEX_PROVIDER_USAGE_TRANSPORT
    assert usage.elapsed_ms >= 0
