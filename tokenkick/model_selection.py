"""Economical model selection from provider-advertised capabilities.

Catalog reads never generate content. Unknown families fail closed instead of
silently inheriting an interactive, potentially expensive model.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


class ModelSelectionError(ValueError):
    """No verified economical invocation can be prepared."""


@dataclass(frozen=True)
class ModelSelection:
    model: str
    effort: str


def claude_economical_args(model: str = "haiku") -> list[str]:
    """Use the live Haiku alias and override interactive cost/tool settings."""
    return [
        "--tools", "", "--effort", "low", "--settings",
        '{"alwaysThinkingEnabled":false,"effortLevel":"low","fastMode":false}',
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands", "--model", model,
    ]


def claude_economical_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_CODE_EFFORT_LEVEL"] = "low"
    env["MAX_THINKING_TOKENS"] = "0"
    return env


def select_codex_model(models: list[dict], override: str | None = None) -> ModelSelection:
    available = {
        model["model"]: model for model in models
        if isinstance(model.get("model"), str) and model["model"]
    }
    if override:
        candidates = [override] if override in available else []
    else:
        candidates = [name for name, info in available.items()
                      if re.fullmatch(r"gpt-\d+(?:\.\d+)*-luna", name)
                      and not info.get("hidden", False)]
        candidates.sort(key=lambda name: (
            name == "gpt-6-luna", tuple(int(n) for n in name[4:-5].split("."))
        ), reverse=True)
    for name in candidates:
        levels = available[name].get("supportedReasoningEfforts")
        if not isinstance(levels, list):
            continue
        supported = {item.get("reasoningEffort") for item in levels
                     if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str)}
        # Never escalate to medium/high merely to get a request through.
        for effort in ("none", "minimal", "low"):
            if effort in supported:
                return ModelSelection(name, effort)
    raise ModelSelectionError(
        "No available Codex model with an economical effort setting. "
        "Refresh/update Codex or choose an available kick-model explicitly; no kick was sent."
    )


def read_antigravity_models(binary: str) -> list[str]:
    """Read agy's model labels, rejecting failed or empty catalog responses."""
    try:
        result = subprocess.run(
            [binary, "models"], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelSelectionError("Antigravity model catalog unavailable; no probe was sent.") from exc
    labels = [line.split("\t", 1)[1].strip() for line in result.stdout.splitlines()
              if "\t" in line]
    if result.returncode or not labels:
        raise ModelSelectionError("Antigravity model catalog unavailable; no probe was sent.")
    return labels


def select_antigravity_model(labels: list[str], family: str, override: str | None = None) -> str:
    """Choose Low Flash, or Low Haiku/Sonnet for the separate Claude quota."""
    if family not in {"gemini", "claude_gpt"}:
        raise ModelSelectionError("Unknown Antigravity quota family.")
    if override:
        if override not in labels:
            raise ModelSelectionError("Requested Antigravity model is not in the live catalog.")
        prefix = "Gemini " if family == "gemini" else "Claude "
        if not override.startswith(prefix) or not override.endswith(" (Low)"):
            raise ModelSelectionError("Choose a Low-effort model from the requested quota family.")
        return override
    patterns = ([r"Gemini (\d+(?:\.\d+)*) Flash \(Low\)"] if family == "gemini" else [
        r"Claude Haiku (\d+(?:\.\d+)*) \(Low\)",
        r"Claude Sonnet (\d+(?:\.\d+)*) \(Low\)",
    ])
    for pattern in patterns:
        candidates = [(tuple(int(n) for n in match[1].split(".")), label)
                      for label in labels if (match := re.fullmatch(pattern, label))]
        if candidates:
            return max(candidates)[1]
    raise ModelSelectionError(
        "No economical Low-effort Antigravity model in this quota family; no probe was sent."
    )
