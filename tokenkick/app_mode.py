"""TK_APP_MODE support and the app JSON contract.

The native macOS app sets ``TK_APP_MODE=1`` before invoking the bundled
``tk``. App mode keeps stdout reserved for JSON/JSON-lines: interactive
prompts and TUI entry are disabled, Rich rendering goes to stderr, and
errors are emitted as JSON envelopes instead of tracebacks.

Every app-facing command answers with one envelope::

    {
      "schema_version": 1,
      "ok": true,
      "error_code": null,
      "message": null,
      "warnings": [],
      "payload": {...}
    }

JSON-lines streams (``tk app setup --json-lines``) emit one compact JSON
object per line with an ``event`` field, ending with a final record that
embeds the same envelope keys.
"""

from __future__ import annotations

import json
import os
import sys

APP_SCHEMA_VERSION = 1
APP_MODE_ENV = "TK_APP_MODE"

ERROR_ABORTED = "aborted"
ERROR_CANCELLED = "cancelled"
ERROR_COMMAND = "command_error"
ERROR_INTERNAL = "internal_error"
ERROR_STATE_FILE = "state_file_error"
ERROR_USAGE = "usage_error"


def app_mode_enabled() -> bool:
    value = os.environ.get(APP_MODE_ENV, "").strip().lower()
    return bool(value) and value not in {"0", "false", "no"}


def app_envelope(
    *,
    ok: bool,
    payload: object = None,
    error_code: str | None = None,
    message: str | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "schema_version": APP_SCHEMA_VERSION,
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "warnings": list(warnings or []),
        "payload": payload,
    }


def emit_app_json(envelope: dict, *, compact: bool = False) -> None:
    indent = None if compact else 2
    sys.stdout.write(json.dumps(envelope, indent=indent, default=str) + "\n")
    sys.stdout.flush()


def emit_app_success(
    payload: object = None,
    *,
    message: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    emit_app_json(app_envelope(ok=True, payload=payload, message=message, warnings=warnings))


def emit_app_error(
    error_code: str,
    message: str,
    *,
    payload: object = None,
    warnings: list[str] | None = None,
) -> None:
    emit_app_json(
        app_envelope(
            ok=False,
            error_code=error_code,
            message=message,
            payload=payload,
            warnings=warnings,
        )
    )


def emit_app_event(event: str, **fields: object) -> None:
    record: dict[str, object] = {"schema_version": APP_SCHEMA_VERSION, "event": event}
    record.update(fields)
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()
