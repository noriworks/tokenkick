"""Tests for provider kick commands."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenkick.kicker import (
    CODEX_KICK_SURFACE_REPO,
    CODEX_KICK_SURFACE_REPO_SKIP,
    CODEX_KICK_PROMPT,
    CODEX_NO_GENERATION_EVIDENCE_ERROR,
    CODEX_PHANTOM_RECOVERY_KICK_PROMPT,
    CODEX_PHANTOM_RECOVERY_MODEL_ENV,
    GEMINI_MONITOR_ONLY_MESSAGE,
    KICKABLE_PROVIDERS,
    _codex_kick_prompt,
    _kick_command,
    kick_invocation_for_account,
    codex_phantom_recovery_model_ladder,
    codex_home_for_account,
    kick_account,
    kick_model_for_account,
)
from tokenkick.models import AccountConfig, DataSource


@pytest.fixture(autouse=True)
def isolate_kicker_config(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path / "config")


def test_codex_home_for_session_account_uses_session_parent():
    account = AccountConfig(
        label="managed",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/codex-home/sessions",
    )

    assert codex_home_for_account(account) == Path("/tmp/codex-home")


def test_kick_commands_do_not_request_unverified_provider_default_models(tmp_path):
    claude_command, _env = _kick_command(AccountConfig(label="claude", provider="claude"))
    assert claude_command == [
        "claude",
        "-p",
        "1+1",
        "--output-format",
        "json",
        "--tools",
        "",
    ]

    codex_command, env = _kick_command(
        AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))
    )
    assert codex_command[:3] == [
        "codex",
        "exec",
        "--json",
    ]
    assert "--skip-git-repo-check" not in codex_command
    assert len(codex_command) == 4
    assert codex_command[-1].startswith(CODEX_KICK_PROMPT)
    assert "Anchor account label: codex." in codex_command[-1]
    assert "Anchor nonce:" in codex_command[-1]
    assert env is not None
    assert env["CODEX_HOME"] == str(tmp_path)


def test_claude_kick_model_override_wins():
    account = AccountConfig(label="claude", provider="claude", kick_model="haiku")

    command, _env = _kick_command(account)

    assert kick_model_for_account(account) == "haiku"
    assert command[-2:] == ["--model", "haiku"]


def test_gemini_is_not_kickable():
    assert "gemini" not in KICKABLE_PROVIDERS


def test_codex_kick_model_override_wins(tmp_path):
    account = AccountConfig(
        label="codex",
        provider="codex",
        provider_home=str(tmp_path),
        kick_model="codex-custom",
    )

    command, _env = _kick_command(account)

    assert "--model" in command
    assert command[command.index("--model") + 1] == "codex-custom"


def test_codex_spark_bucket_uses_spark_model_by_default(tmp_path):
    account = AccountConfig(
        label="codex-spark",
        provider="codex",
        provider_home=str(tmp_path),
        codex_rate_limit_id="codex_bengalfox",
    )

    command, _env = _kick_command(account)

    assert kick_model_for_account(account) == "gpt-5.3-codex-spark"
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gpt-5.3-codex-spark"


def test_codex_kick_default_uses_repo_workspace_without_skip(tmp_path):
    account = AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))

    invocation = kick_invocation_for_account(account)

    assert "--skip-git-repo-check" not in invocation.command
    assert invocation.cwd is not None
    assert invocation.cwd.parent.name == "codex-kick-workspaces"


def test_codex_repo_surface_uses_stable_git_workspace_without_skip(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path / "config")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["git", "init"]:
            (kwargs["cwd"] / ".git").mkdir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)
    account = AccountConfig(label="codex (debug)", provider="codex", provider_home=str(tmp_path))

    invocation = kick_invocation_for_account(account, codex_surface=CODEX_KICK_SURFACE_REPO)

    assert invocation.cwd is not None
    assert invocation.cwd.parent == tmp_path / "config" / "codex-kick-workspaces"
    assert (invocation.cwd / "README.md").exists()
    assert invocation.workspace_git_present is True
    assert "--skip-git-repo-check" not in invocation.command
    assert calls[0][0] == ["git", "init"]


def test_codex_repo_skip_surface_keeps_skip_but_sets_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr("tokenkick.kicker.CONFIG_DIR", tmp_path / "config")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["git", "init"]:
            (kwargs["cwd"] / ".git").mkdir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)
    account = AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))

    event = kick_account(account, record=False, codex_surface=CODEX_KICK_SURFACE_REPO_SKIP)

    codex_call = next(call for call in calls if call[0][0] == "codex")
    assert event.success is True
    assert "--skip-git-repo-check" in codex_call[0]
    assert codex_call[1]["cwd"] is not None
    assert codex_call[1]["cwd"].parent == tmp_path / "config" / "codex-kick-workspaces"


def test_codex_kick_prompt_is_not_trivial_arithmetic(tmp_path):
    command, _env = _kick_command(
        AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))
    )

    assert "anchor probe" in command[-1]
    assert "Anchor account label: codex." in command[-1]
    assert "Anchor nonce:" in command[-1]
    assert command[-1] != "Reply with only the number 2: what is 1+1?"


def test_codex_kick_prompt_varies_per_invocation(monkeypatch):
    values = iter([1_779_900_000_000_000_001, 1_779_900_000_000_000_002])
    monkeypatch.setattr("tokenkick.kicker.time.time_ns", lambda: next(values))
    account = AccountConfig(label="codex (test)", provider="codex")

    first = _codex_kick_prompt(account)
    second = _codex_kick_prompt(account)

    assert first != second
    assert "Anchor account label: codex (test)." in first
    assert "Anchor nonce: 1779900000000000001." in first
    assert "Anchor nonce: 1779900000000000002." in second


def test_codex_phantom_recovery_uses_stronger_prompt_and_optional_model(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_PHANTOM_RECOVERY_MODEL_ENV, "codex-stronger")
    account = AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))

    command, env = _kick_command(account, phantom_recovery=True)

    assert "--model" in command
    assert command[command.index("--model") + 1] == "codex-stronger"
    assert command[-1].startswith(CODEX_PHANTOM_RECOVERY_KICK_PROMPT)
    assert CODEX_KICK_PROMPT not in command[-1]
    assert env is not None
    assert env["CODEX_HOME"] == str(tmp_path)


def test_codex_phantom_recovery_model_does_not_affect_normal_kicks(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_PHANTOM_RECOVERY_MODEL_ENV, "codex-stronger")
    account = AccountConfig(label="codex", provider="codex", provider_home=str(tmp_path))

    command, _env = _kick_command(account)

    assert "--model" not in command
    assert command[-1].startswith(CODEX_KICK_PROMPT)


def test_codex_phantom_recovery_model_ladder_dedupes_configured_models(monkeypatch):
    monkeypatch.setenv(CODEX_PHANTOM_RECOVERY_MODEL_ENV, "gpt-5-codex")
    monkeypatch.setenv("TOKENKICK_CODEX_PHANTOM_RECOVERY_MODELS", "gpt-5-codex, custom-alt")
    account = AccountConfig(label="codex", provider="codex", kick_model="account-model")

    assert codex_phantom_recovery_model_ladder(account) == [
        "account-model",
        "gpt-5-codex",
        "custom-alt",
    ]


def test_kick_account_records_requested_reported_model_and_tokens(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "model": "haiku",
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 1,
                        "total_tokens": 8,
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="claude", provider="claude"), record=False)

    assert "--model" not in captured["command"]
    assert event.success is True
    assert event.kick_model is None
    assert event.reported_model == "haiku"
    assert event.input_tokens == 7
    assert event.output_tokens == 1
    assert event.total_tokens == 8
    assert event.prompt_text == "1+1"


def test_kick_account_parses_codex_jsonl_metadata(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"event":{"model":"gpt-5.2-codex-mini"}}\n'
            '{"event":{"usage":{"prompt_tokens":12,"completion_tokens":1,"total_tokens":13}}}\n'
            '{"type":"agent_message","message":{"role":"assistant","content":[{"type":"output_text","text":"TokenKick anchor probe completed."}]}}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.kick_model is None
    assert event.reported_model == "gpt-5.2-codex-mini"
    assert event.input_tokens == 12
    assert event.output_tokens == 1
    assert event.total_tokens == 13
    assert event.prompt_text is not None
    assert event.prompt_text.startswith(CODEX_KICK_PROMPT)
    assert "Anchor account label: codex." in event.prompt_text
    assert event.response_text == "TokenKick anchor probe completed."
    assert event.confirmed is True
    assert event.codex_surface == CODEX_KICK_SURFACE_REPO
    assert event.evidence_response is True
    assert event.evidence_tokens is True
    assert event.post_kick_status == "not_checked"
    assert "agent_message" in (event.provider_output_excerpt or "")


def test_kick_account_parses_codex_agent_message_string(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"msg":{"type":"agent_message","message":"TokenKick anchor probe completed."}}\n'
            '{"event":{"usage":{"prompt_tokens":12,"completion_tokens":1}}}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.input_tokens == 12
    assert event.output_tokens == 1
    assert event.response_text == "TokenKick anchor probe completed."
    assert event.confirmed is True
    assert event.evidence_response is True
    assert event.evidence_tokens is True


def test_kick_account_parses_codex_completed_agent_item(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"TokenKick anchor probe completed for codex."}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":11008,"cached_input_tokens":9600,"output_tokens":18,"reasoning_output_tokens":0}}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.input_tokens == 11008
    assert event.output_tokens == 18
    assert event.prompt_text is not None
    assert "Anchor nonce:" in event.prompt_text
    assert event.response_text == "TokenKick anchor probe completed for codex."
    assert event.confirmed is True
    assert event.evidence_response is True
    assert event.evidence_tokens is True
    assert "item.completed" in (event.provider_output_excerpt or "")


def test_kick_account_marks_codex_exec_without_generation_unconfirmed(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"turn.completed","last_agent_message":null}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.success is True
    assert event.confirmed is False
    assert event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR
    assert event.response_text is None
    assert event.input_tokens is None
    assert event.output_tokens is None
    assert event.evidence_response is False
    assert event.evidence_tokens is False
    assert event.post_kick_status == "not_checked"
    assert "last_agent_message" in (event.provider_output_excerpt or "")


def test_kick_account_marks_codex_exec_failure_unconfirmed(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout='{"type":"error","message":"unsupported model"}\n',
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.success is False
    assert event.confirmed is False
    assert event.error == "codex exited 1: unsupported model"


def test_kick_account_records_bounded_codex_output_excerpt(monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"event":"start"}\n' + ("x" * 13000),
            stderr="",
        )

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)

    event = kick_account(AccountConfig(label="codex", provider="codex"), record=False)

    assert event.provider_output_excerpt is not None
    assert event.provider_output_excerpt.startswith('{"event":"start"}')
    assert event.provider_output_excerpt.endswith("…")
    assert len(event.provider_output_excerpt) == 12000
    assert event.confirmed is False
    assert event.error == CODEX_NO_GENERATION_EVIDENCE_ERROR


def test_kick_account_sets_codex_home_env(monkeypatch):
    calls = []
    events = []
    account = AccountConfig(
        label="managed",
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        session_path="/tmp/codex-home/sessions",
    )

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tokenkick.kicker.subprocess.run", fake_run)
    monkeypatch.setattr("tokenkick.kicker.append_kick_event", lambda event: events.append(event))

    event = kick_account(account)

    codex_call = next(call for call in calls if call[0][0][0] == "codex")
    assert event.success is True
    assert codex_call[1]["env"]["CODEX_HOME"] == "/tmp/codex-home"
    assert events == [event]


def test_kick_account_rejects_non_kickable_provider(monkeypatch):
    events = []
    account = AccountConfig(
        label="openrouter",
        provider="openrouter",
        source=DataSource.CODEXBAR_CLI,
    )
    monkeypatch.setattr("tokenkick.kicker.append_kick_event", lambda event: events.append(event))

    event = kick_account(account)

    assert event.success is False
    assert 'not "openrouter"' in event.error
    assert events == [event]


def test_kick_account_rejects_gemini_monitor_only(monkeypatch):
    events = []
    account = AccountConfig(label="gemini", provider="gemini", source=DataSource.CODEXBAR_CLI)

    monkeypatch.setattr(
        "tokenkick.kicker.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not kick Gemini")),
    )
    monkeypatch.setattr("tokenkick.kicker.append_kick_event", lambda event: events.append(event))

    event = kick_account(account)

    assert event.success is False
    assert event.error == GEMINI_MONITOR_ONLY_MESSAGE
    assert events == [event]
