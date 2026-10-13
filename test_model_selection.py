"""Regressions for economical model selection, retirement, and catalog transport."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from tokenkick.direct import CodexProviderUsageError, read_codex_model_catalog
from tokenkick.kicker import CODEX_KICK_SURFACES, kick_account, kick_invocation_for_account
from tokenkick.model_selection import (
    ModelSelectionError, read_antigravity_models, select_antigravity_model, select_codex_model,
)
from tokenkick.models import AccountConfig, Config


def model(name, *efforts, **extra):
    return {"model": name, "supportedReasoningEfforts": [
        {"reasoningEffort": effort} for effort in efforts
    ], **extra}


@pytest.mark.parametrize("catalog,expected", [
    ([model("gpt-5.6-luna", "low"), model("gpt-6-luna", "low", "medium")], "gpt-6-luna"),
    ([model("gpt-6.1-luna", "low"), model("gpt-6-luna", "low")], "gpt-6-luna"),
    ([model("gpt-5.6-luna", "low")], "gpt-5.6-luna"),
    ([model("gpt-7-luna", "low"), model("gpt-5.6-luna", "low")], "gpt-7-luna"),
    ([model("gpt-6-luna", "low", hidden=True), model("gpt-5.6-luna", "low")], "gpt-5.6-luna"),
])
def test_codex_preference_and_retirement_fallback(catalog, expected):
    selected = select_codex_model(catalog)
    assert (selected.model, selected.effort) == (expected, "low")


@pytest.mark.parametrize("catalog", [[], [model("gpt-6-sol", "low")],
    [model("gpt-6-luna", "high")], [model("gpt-6-luna", "unknown")],
    [{"model": "gpt-6-luna", "supportedReasoningEfforts": None}],
    [{"model": None}], [model("gpt-6-luna-expensive", "low")]])
def test_codex_no_implicit_expensive_or_unverified_fallback(catalog):
    with pytest.raises(ModelSelectionError):
        select_codex_model(catalog)


@pytest.mark.parametrize("efforts,expected", [(("high", "low"), "low"),
    (("minimal", "low"), "minimal"), (("none", "low"), "none")])
def test_codex_uses_lowest_advertised_effort(efforts, expected):
    assert select_codex_model([model("gpt-6-luna", *efforts)]).effort == expected


def test_codex_explicit_override_must_be_available():
    catalog = [model("gpt-6-luna", "low"), model("custom", "minimal")]
    assert select_codex_model(catalog, "custom").model == "custom"
    with pytest.raises(ModelSelectionError):
        select_codex_model(catalog, "retired")


@pytest.mark.parametrize("surface", CODEX_KICK_SURFACES)
def test_all_codex_surfaces_override_interactive_effort(monkeypatch, tmp_path, surface):
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path)
    homes = []
    def catalog(home):
        homes.append(home)
        return [model("gpt-6-luna", "low", "high")]
    monkeypatch.setattr("tokenkick.kicker.read_codex_model_catalog", catalog)
    account = AccountConfig(label="example", provider="codex", provider_home=str(tmp_path))
    invocation = kick_invocation_for_account(account, codex_surface=surface)
    assert homes == [tmp_path]
    assert invocation.model == "gpt-6-luna"
    assert 'model_reasoning_effort="low"' in invocation.command
    assert 'service_tier="default"' in invocation.command


def test_model_catalog_is_fresh_and_account_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path)
    catalogs = {
        "first": [[model("gpt-6-luna", "low")], [model("gpt-7-luna", "low")]],
        "second": [[model("gpt-5.6-luna", "low")]],
    }
    monkeypatch.setattr("tokenkick.kicker.read_codex_model_catalog", lambda home: catalogs[home.name].pop(0))
    first = AccountConfig(label="first", provider="codex", provider_home=str(tmp_path / "first"))
    second = AccountConfig(label="second", provider="codex", provider_home=str(tmp_path / "second"))
    assert kick_invocation_for_account(first).model == "gpt-6-luna"
    assert kick_invocation_for_account(second).model == "gpt-5.6-luna"
    assert kick_invocation_for_account(first).model == "gpt-7-luna"


@pytest.mark.parametrize("error", [CodexProviderUsageError("catalog unavailable"), OSError("secret details")])
def test_catalog_failure_records_failure_without_generation(monkeypatch, error):
    def fail(_home):
        raise error
    monkeypatch.setattr("tokenkick.kicker.read_codex_model_catalog", fail)
    monkeypatch.setattr("tokenkick.kicker.subprocess.run", lambda *_a, **_k: pytest.fail("must not generate"))
    events = []
    monkeypatch.setattr("tokenkick.kicker.append_kick_event", events.append)
    event = kick_account(AccountConfig(label="example", provider="codex"))
    assert not event.success
    assert "secret details" not in event.error
    assert events == [event]


def test_claude_economical_settings_override_environment(monkeypatch):
    monkeypatch.setenv("MAX_THINKING_TOKENS", "64000")
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")
    invocation = kick_invocation_for_account(AccountConfig(label="example", provider="claude"))
    assert invocation.model == "haiku"
    assert invocation.env["MAX_THINKING_TOKENS"] == "0"
    assert invocation.env["CLAUDE_CODE_EFFORT_LEVEL"] == "low"
    assert invocation.command[invocation.command.index("--effort") + 1] == "low"
    assert json.loads(invocation.command[invocation.command.index("--settings") + 1])["fastMode"] is False


@pytest.mark.parametrize("family,labels,expected", [
    ("gemini", ["Gemini 3.8 Flash (High)", "Gemini 3.8 Flash (Low)", "Gemini 3.5 Flash (Low)"], "Gemini 3.8 Flash (Low)"),
    ("gemini", ["Gemini 4 Flash (Low)"], "Gemini 4 Flash (Low)"),
    ("claude_gpt", ["Claude Opus 5.5 (Low)", "Claude Sonnet 5.5 (Low)"], "Claude Sonnet 5.5 (Low)"),
    ("claude_gpt", ["Claude Sonnet 5.5 (Low)", "Claude Haiku 5.5 (Low)"], "Claude Haiku 5.5 (Low)"),
])
def test_antigravity_selects_economical_family(labels, family, expected):
    assert select_antigravity_model(labels, family) == expected


@pytest.mark.parametrize("labels,family,override", [
    ([], "gemini", None), (["Claude Opus 5.5 (Low)"], "claude_gpt", None),
    (["Gemini 3.8 Flash (High)"], "gemini", None),
    (["Gemini 3.8 Flash (Low)"], "claude_gpt", "Gemini 3.8 Flash (Low)"),
    (["Claude Sonnet 5.5 (High)"], "claude_gpt", "Claude Sonnet 5.5 (High)"),
    (["Gemini 3.8 Flash (Low)"], "gemini", "retired"),
])
def test_antigravity_missing_or_wrong_family_fails_closed(labels, family, override):
    with pytest.raises(ModelSelectionError):
        select_antigravity_model(labels, family, override)


def test_antigravity_catalog_parses_actual_tabular_format(monkeypatch):
    def run(command, **kwargs):
        assert command == ["/fixture/agy", "models"]
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout="Fetching available models...\nid\tGemini 3.8 Flash (Low)\n")
    monkeypatch.setattr("tokenkick.model_selection.subprocess.run", run)
    assert read_antigravity_models("/fixture/agy") == ["Gemini 3.8 Flash (Low)"]


@pytest.mark.parametrize("failure", [OSError("private"), subprocess.TimeoutExpired("agy", 30), None])
def test_antigravity_catalog_failure_never_uses_old_default(monkeypatch, failure):
    def run(*_a, **_kw):
        if failure:
            raise failure
        return SimpleNamespace(returncode=1, stdout="id\tGemini 3.8 Flash (Low)\n")
    monkeypatch.setattr("tokenkick.model_selection.subprocess.run", run)
    with pytest.raises(ModelSelectionError, match="catalog unavailable"):
        read_antigravity_models("agy")


def test_retired_spark_cleanup_persists_without_touching_homes_history_or_other_accounts(monkeypatch, tmp_path):
    import tokenkick.models as models
    monkeypatch.setattr(models, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(models, "CONFIG_FILE", tmp_path / "config.json")
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text("synthetic credential placeholder")
    history = tmp_path / "history.jsonl"
    history.write_text("historical records")
    main = AccountConfig(label="main", provider="codex", provider_home=str(home))
    spark = AccountConfig(label="retired", provider="codex", provider_home=str(home), codex_rate_limit_id="codex_bengalfox")
    pinned = AccountConfig(label="old pin", provider="codex", kick_model="gpt-5.3-codex-spark")
    unrelated = AccountConfig(label="spark is just a label", provider="claude", kick_model="custom")
    Config(accounts=[main, spark, pinned, unrelated], poll_interval_minutes=11).save()
    result = Config.load()
    assert [a.label for a in result.accounts] == ["main", "old pin", "spark is just a label"]
    assert result.accounts[1].kick_model is None
    assert result.accounts[2].kick_model == "custom"
    assert result.poll_interval_minutes == 11
    saved = models.CONFIG_FILE.read_text()
    assert "codex_bengalfox" not in saved
    assert "gpt-5.3-codex-spark" not in saved
    Config.load()
    assert models.CONFIG_FILE.read_text() == saved
    assert (home / "auth.json").read_text() == "synthetic credential placeholder"
    assert history.read_text() == "historical records"


def test_codex_catalog_handshake_pagination_and_cleanup(monkeypatch, tmp_path):
    import tokenkick.direct as direct
    proc = SimpleNamespace(stdin=io.BytesIO())
    stopped = []
    monkeypatch.setattr(direct, "_start_codex_appserver", lambda home: proc if home == tmp_path else pytest.fail("wrong identity"))
    monkeypatch.setattr(direct, "_stop_codex_appserver_process", stopped.append)
    responses = [{"result": {}}, {"result": {"data": [model("gpt-5.6-luna", "low")], "nextCursor": "second"}},
                 {"result": {"data": [model("gpt-6-luna", "low")], "nextCursor": None}}]
    monkeypatch.setattr(direct, "_read_codex_appserver_response", lambda _proc, timeout, request_id: responses.pop(0))
    assert len(read_codex_model_catalog(tmp_path)) == 2
    messages = [json.loads(line) for line in proc.stdin.getvalue().splitlines()]
    assert [m["method"] for m in messages] == ["initialize", "notifications/initialized", "model/list", "model/list"]
    assert messages[-1]["params"]["cursor"] == "second"
    assert stopped == [proc]


@pytest.mark.parametrize("bad_result", [None, {"error": {"message": "private"}}, {"result": {"data": "bad"}},
    {"result": {"data": [], "nextCursor": "repeat"}}])
def test_codex_catalog_failure_and_loop_are_bounded_and_cleaned_up(monkeypatch, tmp_path, bad_result):
    import tokenkick.direct as direct
    proc = SimpleNamespace(stdin=io.BytesIO())
    stopped = []
    monkeypatch.setattr(direct, "_start_codex_appserver", lambda home: proc)
    monkeypatch.setattr(direct, "_stop_codex_appserver_process", stopped.append)
    monkeypatch.setattr(direct, "_read_codex_appserver_response", lambda _p, timeout, request_id: {"result": {}} if request_id == 1 else bad_result)
    with pytest.raises(CodexProviderUsageError) as failure:
        read_codex_model_catalog(tmp_path)
    assert "private" not in str(failure.value)
    assert stopped == [proc]


@pytest.mark.parametrize("path", ["probe", "usage-pipe"])
def test_all_claude_probe_paths_apply_cost_controls(monkeypatch, tmp_path, path):
    from tokenkick.direct import probe_claude_status
    from tokenkick.sources import _capture_claude_usage_pipe
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")
    monkeypatch.setattr("tokenkick.direct.subprocess.run", run)
    monkeypatch.setattr("tokenkick.sources._claude_probe_cwd", lambda: tmp_path)
    if path == "probe":
        assert probe_claude_status() == (True, None)
    else:
        assert _capture_claude_usage_pipe("claude") == "OK\n"
    command, kwargs = captured[0]
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--effort") + 1] == "low"
    assert kwargs["env"]["MAX_THINKING_TOKENS"] == "0"
    assert ("--disable-slash-commands" in command) is (path == "probe")
