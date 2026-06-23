#!/usr/bin/env python3
"""Render the synthetic README status demo asset."""

from __future__ import annotations

import os
import io
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
SVG_PATH = ASSET_DIR / "readme-status-demo.svg"
TEXT_PATH = ASSET_DIR / "readme-status-demo.txt"


def _sanitize_svg(svg: str) -> str:
    """Keep README demo SVG self-contained and independent of CDN fonts."""
    svg = re.sub(r"\s*@font-face\s*\{[^{}]*\}", "", svg, flags=re.DOTALL)
    return svg.replace(
        "font-family: Fira Code, monospace",
        'font-family: "SFMono-Regular", Menlo, Consolas, monospace',
    ).replace(
        "font-family: Fira Code, monospace;",
        'font-family: "SFMono-Regular", Menlo, Consolas, monospace;',
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="tokenkick-readme-demo-") as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["TK_NO_INTERACTIVE"] = "1"
        sys.path.insert(0, str(ROOT))

        from rich.console import Console

        from tokenkick import cli as cli_mod
        from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, DataSource

        console = Console(
            color_system="truecolor",
            force_terminal=True,
            record=True,
            file=io.StringIO(),
            width=144,
            height=40,
            _environ={"COLUMNS": "144", "LINES": "40", "TERM": "xterm-256color"},
        )
        cli_mod.console = console

        accounts = [
            AccountConfig(
                label="codex (work)",
                provider="codex",
                source=DataSource.CODEX_DIRECT,
                provider_home="/tmp/tokenkick-demo/codex-work",
                usable_session_minutes=240,
            ),
            AccountConfig(
                label="claude (personal)",
                provider="claude",
                source=DataSource.CLAUDE_DIRECT,
                provider_home="/tmp/tokenkick-demo/claude-personal",
                usable_session_minutes=180,
            ),
            AccountConfig(
                label="codex-spark (lab)",
                provider="codex",
                source=DataSource.CODEX_DIRECT,
                provider_home="/tmp/tokenkick-demo/codex-work",
                codex_rate_limit_id="codex_bengalfox",
                codex_rate_limit_name="GPT-5.3-Codex-Spark",
                usable_session_minutes=90,
            ),
        ]
        statuses = [
            AccountStatus(
                label="codex (work)",
                state=AccountState.ACTIVE,
                used_percent=42.0,
                resets_in_seconds=5 * 24 * 60 * 60,
                window_minutes=7 * 24 * 60,
                session_used_percent=18.0,
                session_resets_in_seconds=2 * 60 * 60 + 35 * 60,
                session_window_minutes=300,
                source_detail="codex-appserver-ratelimits",
                codex_rate_limit_id="codex",
            ),
            AccountStatus(
                label="claude (personal)",
                state=AccountState.WAITING,
                used_percent=76.0,
                resets_in_seconds=2 * 24 * 60 * 60 + 4 * 60 * 60,
                window_minutes=7 * 24 * 60,
                session_used_percent=100.0,
                session_resets_in_seconds=42 * 60,
                session_window_minutes=300,
                source_detail="claude-cli-usage",
            ),
            AccountStatus(
                label="codex-spark (lab)",
                state=AccountState.FRESH,
                used_percent=8.0,
                resets_in_seconds=6 * 24 * 60 * 60 + 7 * 60 * 60,
                window_minutes=7 * 24 * 60,
                session_used_percent=0.0,
                session_resets_in_seconds=0,
                session_window_minutes=300,
                source_detail="codex-appserver-ratelimits",
                codex_rate_limit_id="codex_bengalfox",
                codex_rate_limit_name="GPT-5.3-Codex-Spark",
            ),
        ]

        cli_mod._render_status_table(statuses, accounts, Config(accounts=accounts), {})
        console.print("[dim]Synthetic demo data. Auto-kick is off until explicitly enabled.[/dim]")

        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        TEXT_PATH.write_text(console.export_text(styles=False, clear=False), encoding="utf-8")
        SVG_PATH.write_text(
            _sanitize_svg(console.export_svg(title="TokenKick synthetic status demo")),
            encoding="utf-8",
        )

    print(f"Wrote {SVG_PATH}")
    print(f"Wrote {TEXT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
