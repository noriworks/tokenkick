from __future__ import annotations

import json
import os
import stat
import textwrap

import pytest

from tokenkick.mcp_core import TokenKickMCPCore


def _fake_tk(
    tmp_path,
    *,
    malformed: bool = False,
    fail_prefix: list[str] | None = None,
    empty_run: bool = False,
):
    log_path = tmp_path / "fake-tk-log.jsonl"
    script = tmp_path / "fake-tk"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import os
            import sys

            argv = sys.argv[1:]
            with open(os.environ["FAKE_TK_LOG"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps({{
                    "argv": argv,
                    "tk_no_interactive": os.environ.get("TK_NO_INTERACTIVE"),
                    "tk_app_mode": os.environ.get("TK_APP_MODE"),
                }}) + "\\n")

            fail_prefix = {fail_prefix!r}
            if fail_prefix and argv[:len(fail_prefix)] == fail_prefix:
                print(json.dumps({{"error": "blocked", "argv": argv}}))
                sys.exit(2)

            if {malformed!r}:
                print("not-json")
                sys.exit(0)

            if argv[:2] == ["app", "snapshot"]:
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{
                        "snapshot": True,
                        "core": {{
                            "version": "1.14.1",
                            "executable": "/Users/example/dev/tokenkick/.venv/bin/tk",
                            "python_executable": "/Users/example/dev/tokenkick/.venv/bin/python",
                        }},
                        "runtime": {{
                            "external_tk": {{
                                "path": "/Users/example/.local/bin/tk",
                                "version": "1.14.1",
                            }},
                        }},
                        "paths": {{
                            "config_file": "/Users/example/.tokenkick/config.json",
                            "daemon_log_file": "/Users/example/.tokenkick/daemon.log",
                        }},
                        "pending_kicks": [],
                    }},
                }}))
            elif argv[:2] == ["app", "doctor"]:
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{
                        "doctor": {{
                            "config_file": "/Users/example/.tokenkick/config.json",
                            "daemon_log_file": "/Users/example/.tokenkick/daemon.log",
                            "accounts": [],
                        }},
                    }},
                }}))
            elif argv and argv[0] == "doctor":
                label = argv[-1] if len(argv) > 2 else ""
                accounts = []
                if label == "codex (stale)":
                    accounts = [{{
                        "label": label,
                        "checks": [{{
                            "code": "account_refresh_error",
                            "message": "provider refresh failed",
                            "fix": "tk kick \\"codex (stale)\\" --force --yes",
                        }}],
                    }}]
                print(json.dumps({{"accounts": accounts}}))
            elif argv and argv[0] == "status":
                print(json.dumps({{"accounts": [], "refresh": "--refresh" in argv}}))
            elif argv[:2] == ["plan", "cancel"]:
                payload = {{
                    "read_only": "--yes" not in argv,
                    "applied": "--yes" in argv,
                    "message": "not cancelled; --json-output requires --yes to mutate",
                    "result": {{"removed": [], "kept_count": 2, "unmatched_account_labels": []}},
                    "matching": [{{
                        "account_label": "codex (work)",
                        "account_key": "codex-home|codex|work",
                        "reason": "orchestrated",
                        "purpose": "coverage",
                        "kick_at": "2026-06-15T19:00:00+00:00",
                    }}],
                }}
                print(json.dumps(payload))
                sys.exit(0 if "--yes" in argv else 1)
            elif argv and argv[0] == "plan":
                applied = "--apply" in argv
                print(json.dumps({{
                    "schema_version": 1,
                    "read_only": not applied,
                    "applied": applied,
                    "planned_kicks": [{{
                        "account_label": "codex (work)",
                        "account_key": "codex-home|codex|work",
                        "kick_at": "2026-06-15T19:00:00+00:00",
                        "purpose": "coverage",
                    }}],
                    "diff": {{
                        "adds": [{{"account_label": "codex (work)"}}],
                        "replaces_orchestrated": [],
                        "unchanged_orchestrated": [],
                        "conflicts_unmanaged": [],
                        "removes_orchestrated": [],
                    }},
                    "applied": applied,
                }}))
            elif argv[:2] == ["schedule", "show"]:
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{
                        "enabled": True,
                        "default": {{"enabled": True, "weekdays": "09:00-17:00", "weekends": None}},
                        "accounts": {{
                            "codex (work)": {{"enabled": True, "weekdays": "10:00-18:00", "weekends": None}},
                        }},
                        "pending_kicks": [
                            {{
                                "account_label": "codex (work)",
                                "reason": "smart_schedule",
                                "purpose": "coverage",
                                "kick_at": "2026-06-15T10:00:00+00:00",
                            }},
                            {{
                                "account_label": "codex (work)",
                                "reason": "orchestrated",
                                "purpose": "coverage",
                                "kick_at": "2026-06-15T19:00:00+00:00",
                            }},
                        ],
                    }},
                }}))
            elif argv and argv[0] == "schedule":
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{"schedule": argv}},
                }}))
            elif argv and argv[0] == "daemon":
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{
                        "daemon": argv,
                        "running": "--status" in argv,
                        "owner": "app",
                        "executable": "/Users/example/.local/bin/tk",
                        "pidfile": "/Users/example/.tokenkick/daemon.pid",
                        "log_file": "/Users/example/.tokenkick/daemon.log",
                    }},
                }}))
            elif argv and argv[0] == "run":
                kicked = [] if {empty_run!r} else [{{"label": "codex (work)", "reason": "would kick"}}]
                print(json.dumps({{"run": True, "dry_run": "--dry-run" in argv, "kicked": kicked}}))
            elif argv and argv[0] == "kick":
                dry_run = "--dry-run" in argv
                decision = "skipped" if "codex (skip)" in argv else "would_kick"
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{"kick": argv, "dry_run": dry_run, "decision": decision}},
                }}))
            elif argv[:2] == ["accounts", "list"]:
                print(json.dumps({{
                    "schema_version": 1,
                    "ok": True,
                    "error_code": None,
                    "message": None,
                    "warnings": [],
                    "payload": {{"accounts": []}},
                }}))
            elif argv and argv[0] == "history":
                print(json.dumps([{{
                    "label": "codex (work)",
                    "timestamp": "2026-06-15T12:00:00+00:00",
                    "kind": "session",
                    "confirmed": True,
                    "prompt_text": "say token=abc123",
                    "response_text": "provider answered sk-secret1234567890",
                    "provider_output_excerpt": "Bearer abc.def.ghi",
                    "error": None,
                }}]))
            elif argv and argv[0] in {{"calendar", "reset-log", "codex-strategy", "codex-surfaces"}}:
                print(json.dumps({{"ok": True, "argv": argv}}))
            else:
                print(json.dumps({{"error": "unexpected", "argv": argv}}))
                sys.exit(2)
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log_path


def _core(
    tmp_path,
    *,
    malformed: bool = False,
    fail_prefix: list[str] | None = None,
    empty_run: bool = False,
):
    script, log_path = _fake_tk(
        tmp_path,
        malformed=malformed,
        fail_prefix=fail_prefix,
        empty_run=empty_run,
    )
    os.environ["FAKE_TK_LOG"] = str(log_path)
    return TokenKickMCPCore(tk_path=str(script)), log_path


def _calls(log_path):
    return [json.loads(line) for line in log_path.read_text().splitlines()]


def test_snapshot_uses_app_mode_and_normalizes_envelope(tmp_path):
    core, log_path = _core(tmp_path)

    result = core.tokenkick_snapshot()

    assert result["ok"] is True
    assert result["risk"] == "diagnostic_read"
    assert result["may_read_environment"] is True
    assert result["payload"]["snapshot"] is True
    assert result["payload"]["pending_kicks"] == []
    assert result["payload"]["core"]["executable"] == "[redacted]"
    assert result["payload"]["runtime"]["external_tk"]["path"] == "[redacted]"
    assert result["payload"]["paths"]["config_file"] == "[redacted]"
    assert result["command_summary"] == "tk app snapshot"
    assert "app_envelope" not in result
    call = _calls(log_path)[0]
    assert call["argv"] == ["app", "snapshot"]
    assert call["tk_no_interactive"] == "1"
    assert call["tk_app_mode"] == "1"


def test_snapshot_can_include_paths_for_diagnostics(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_snapshot(include_paths=True)

    assert result["ok"] is True
    assert result["payload"]["paths"]["config_file"] == "/Users/example/.tokenkick/config.json"
    assert result["command_summary"].endswith("fake-tk app snapshot")


def test_status_cached_does_not_use_app_mode_or_provider_refresh(tmp_path):
    core, log_path = _core(tmp_path)

    result = core.tokenkick_status(account="codex (work)")

    assert result["risk"] == "cached_read"
    assert result["provider_refresh"] is False
    call = _calls(log_path)[0]
    assert call["argv"] == ["status", "--json-output", "--account", "codex (work)"]
    assert call["tk_app_mode"] is None


def test_refresh_status_is_live_provider_read(tmp_path):
    core, log_path = _core(tmp_path)

    result = core.tokenkick_refresh_status(codex=True)

    assert result["risk"] == "live_provider_read"
    assert result["provider_refresh"] is True
    assert _calls(log_path)[0]["argv"] == ["status", "--refresh", "--json-output", "--codex"]


def test_doctor_is_diagnostic_read(tmp_path):
    core, log_path = _core(tmp_path)

    result = core.tokenkick_doctor(label="codex (work)")

    assert result["risk"] == "diagnostic_read"
    assert result["provider_refresh"] is False
    assert result["may_read_environment"] is True
    assert _calls(log_path)[0]["argv"] == ["doctor", "--json-output", "codex (work)"]


def test_doctor_and_daemon_redact_paths_by_default(tmp_path):
    core, _log_path = _core(tmp_path)

    doctor = core.tokenkick_doctor()
    daemon = core.tokenkick_daemon_status()

    assert doctor["payload"]["doctor"]["config_file"] == "[redacted]"
    assert doctor["payload"]["doctor"]["daemon_log_file"] == "[redacted]"
    assert doctor["command_summary"] == "tk app doctor"
    assert daemon["payload"]["executable"] == "[redacted]"
    assert daemon["payload"]["pidfile"] == "[redacted]"
    assert daemon["payload"]["log_file"] == "[redacted]"
    assert daemon["command_summary"] == "tk daemon --status --json-output"

    verbose_daemon = core.tokenkick_daemon_status(include_paths=True)
    assert verbose_daemon["payload"]["pidfile"] == "/Users/example/.tokenkick/daemon.pid"
    assert verbose_daemon["command_summary"].endswith("fake-tk daemon --status --json-output")


def test_history_summarizes_detail_fields_by_default_and_verbose_keeps_redacted_details(tmp_path):
    core, _log_path = _core(tmp_path)

    summary = core.tokenkick_history(limit=1)
    event = summary["payload"][0]

    assert "prompt_text" not in event
    assert "response_text" not in event
    assert "provider_output_excerpt" not in event
    assert event["prompt_text_redacted"] is True
    assert event["response_text_redacted"] is True
    assert event["provider_output_excerpt_redacted"] is True

    verbose = core.tokenkick_history(limit=1, include_details=True)
    detailed = verbose["payload"][0]
    assert detailed["prompt_text"] == "say token=<redacted>"
    assert detailed["response_text"] == "provider answered sk-<redacted>"
    assert detailed["provider_output_excerpt"] == "Bearer <redacted>"


def test_plan_apply_requires_preview_token_and_exact_arguments(tmp_path):
    core, _log_path = _core(tmp_path)
    preview = core.tokenkick_plan_preview(work_window="21:00-02:00", date="2026-06-15")
    token = preview["preview_token"]["token"]

    with pytest.raises(ValueError, match="confirm=true"):
        core.tokenkick_plan_apply(
            work_window="21:00-02:00",
            date="2026-06-15",
            preview_token=token,
            confirm=False,
        )

    with pytest.raises(ValueError, match="does not match"):
        core.tokenkick_plan_apply(
            work_window="21:30-02:00",
            date="2026-06-15",
            preview_token=token,
            confirm=True,
        )

    preview = core.tokenkick_plan_preview(work_window="21:00-02:00", date="2026-06-15")
    result = core.tokenkick_plan_apply(
        work_window="21:00-02:00",
        date="2026-06-15",
        preview_token=preview["preview_token"]["token"],
        confirm=True,
    )

    assert result["risk"] == "low_risk_mutation"
    assert result["payload"]["applied"] is True


def test_failed_plan_preview_does_not_create_preview_token(tmp_path):
    core, _log_path = _core(tmp_path, fail_prefix=["plan"])

    result = core.tokenkick_plan_preview(work_window="21:00-02:00")

    assert result["ok"] is False
    assert "preview_token" not in result


def test_malformed_preview_output_does_not_create_preview_token(tmp_path):
    core, _log_path = _core(tmp_path, malformed=True)

    result = core.tokenkick_plan_preview(work_window="21:00-02:00")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_json"
    assert "preview_token" not in result


def test_plan_preview_accepts_core_minute_precision_and_rejects_equal_window(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_plan_preview(
        work_window="21:15-02:45",
        usage=["codex (personal)=150m", "claude (work)=2h"],
    )

    assert result["ok"] is True
    assert result["preview_token"]
    assert result["payload"]["mcp_preview"]["can_execute_with_preview_token"] is True
    with pytest.raises(ValueError, match="start and end must differ"):
        core.tokenkick_plan_preview(work_window="21:15-21:15")
    with pytest.raises(ValueError, match="Use forms like"):
        core.tokenkick_plan_preview(work_window="21:15-02:45", usage=["codex (work)=soon"])


def test_plan_cancel_preview_lists_removable_orchestrated_kicks(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_plan_cancel_preview(accounts=["codex (work)"])

    assert result["ok"] is True
    assert result["preview_token"]
    matching = result["payload"]["cancel_preview"]["matching"]
    assert matching[0]["account_label"] == "codex (work)"
    assert matching[0]["reason"] == "orchestrated"


def test_schedule_preview_includes_current_schedule_and_pending_impact(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_schedule_set_preview(
        account="codex (work)",
        weekdays="21:15-02:45",
    )

    assert result["ok"] is True
    assert result["preview_token"]
    assert result["payload"]["current_schedule"]["accounts"]["codex (work)"]["weekdays"] == "10:00-18:00"
    impact = result["payload"]["pending_kick_impact"]
    assert impact["would_remove_smart_schedule_pending_kicks"][0]["reason"] == "smart_schedule"
    assert impact["kept_orchestrated_pending_kicks"][0]["reason"] == "orchestrated"


def test_daemon_preview_includes_current_daemon_status(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_daemon_control_preview(action="restart")

    assert result["ok"] is True
    assert result["preview_token"]
    assert result["payload"]["daemon_status"]["owner"] == "app"


def test_run_apply_is_dangerous_and_requires_quota_ack(tmp_path):
    core, log_path = _core(tmp_path)
    preview = core.tokenkick_run_dry_run(codex=True)
    token = preview["preview_token"]["token"]

    with pytest.raises(ValueError, match="quota_ack=true"):
        core.tokenkick_run_apply(preview_token=token, confirm=True, quota_ack=False, codex=True)

    preview = core.tokenkick_run_dry_run(codex=True)
    result = core.tokenkick_run_apply(
        preview_token=preview["preview_token"]["token"],
        confirm=True,
        quota_ack=True,
        codex=True,
    )

    assert result["risk"] == "dangerous_quota_operational"
    assert result["provider_refresh"] is True
    assert _calls(log_path)[-1]["argv"] == ["run", "--json-output", "--codex"]


def test_run_dry_run_without_kicks_does_not_create_preview_token(tmp_path):
    core, _log_path = _core(tmp_path, empty_run=True)

    result = core.tokenkick_run_dry_run(codex=True)

    assert result["ok"] is True
    assert "preview_token" not in result
    assert result["payload"]["mcp_preview"]["can_execute_with_preview_token"] is False
    assert result["payload"]["mcp_preview"]["requires_quota_ack"] is True


def test_kick_preview_skipped_decision_does_not_create_preview_token(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_kick_preview(label="codex (skip)")

    assert result["ok"] is True
    assert "preview_token" not in result
    assert result["payload"]["mcp_preview"]["can_execute_with_preview_token"] is False


def test_force_recovery_requires_force_ack_and_uses_exact_force_command(tmp_path):
    core, log_path = _core(tmp_path)
    preview = core.tokenkick_force_recovery_preview(label="codex (stale)")
    token = preview["preview_token"]["token"]

    with pytest.raises(ValueError, match="force_ack=true"):
        core.tokenkick_force_recovery_kick(
            label="codex (stale)",
            preview_token=token,
            confirm=True,
            quota_ack=True,
            force_ack=False,
        )

    preview = core.tokenkick_force_recovery_preview(label="codex (stale)")
    result = core.tokenkick_force_recovery_kick(
        label="codex (stale)",
        preview_token=preview["preview_token"]["token"],
        confirm=True,
        quota_ack=True,
        force_ack=True,
    )

    assert result["risk"] == "dangerous_recovery"
    assert _calls(log_path)[-1]["argv"] == [
        "kick",
        "codex (stale)",
        "--force",
        "--json-output",
        "--yes",
    ]


def test_force_recovery_preview_requires_recovery_context(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_force_recovery_preview(label="codex (work)")

    assert result["ok"] is True
    assert "preview_token" not in result
    assert result["payload"]["recommended"] is False
    assert result["payload"]["recovery_context"]["hints"] == []


def test_force_recovery_preview_includes_recovery_context(tmp_path):
    core, _log_path = _core(tmp_path)

    result = core.tokenkick_force_recovery_preview(label="codex (stale)")

    assert result["ok"] is True
    assert result["preview_token"]
    assert result["payload"]["recommended"] is True
    assert result["payload"]["recovery_context"]["hints"][0]["fix"].startswith("tk kick")


def test_plan_tool_docstring_documents_usage_format():
    from tokenkick import mcp_server

    assert "['codex (personal)=150m', 'claude (work)=2h']" in (
        mcp_server.tokenkick_plan_preview.__doc__ or ""
    )


def test_rejects_unsafe_labels_before_subprocess(tmp_path):
    core, log_path = _core(tmp_path)

    with pytest.raises(ValueError, match="must not start"):
        core.tokenkick_status(account="--help")

    assert not log_path.exists()


def test_malformed_json_returns_safe_error(tmp_path):
    core, _log_path = _core(tmp_path, malformed=True)

    result = core.tokenkick_status()

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_json"
    assert result["payload"]["stdout_preview"] == "not-json"
