#!/usr/bin/env python3
"""Render the synthetic README demo assets."""

from __future__ import annotations

import os
import io
import re
import sys
import tempfile
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
SVG_PATH = ASSET_DIR / "readme-status-demo.svg"
TEXT_PATH = ASSET_DIR / "readme-status-demo.txt"
PLAN_SVG_PATH = ASSET_DIR / "readme-plan-demo.svg"
PLAN_TEXT_PATH = ASSET_DIR / "readme-plan-demo.txt"
MACOS_MAIN_PATH = ASSET_DIR / "readme-macos-main.svg"
MACOS_POPOVER_PATH = ASSET_DIR / "readme-macos-popover.svg"


def _sanitize_svg(svg: str) -> str:
    """Keep README demo SVG self-contained and independent of CDN fonts."""
    svg = re.sub(r"\s*@font-face\s*\{[^{}]*\}", "", svg, flags=re.DOTALL)
    svg = svg.replace(
        "font-family: Fira Code, monospace",
        'font-family: "SFMono-Regular", Menlo, Consolas, monospace',
    ).replace(
        "font-family: Fira Code, monospace;",
        'font-family: "SFMono-Regular", Menlo, Consolas, monospace;',
    )
    return _add_svg_dimensions(svg)


def _add_svg_dimensions(svg: str) -> str:
    """Give GitHub a real intrinsic size for Rich SVG screenshots."""
    svg_tag = re.search(r"<svg\b[^>]*>", svg)
    if svg_tag is None or ' width="' in svg_tag.group(0):
        return svg
    viewbox = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg_tag.group(0))
    if viewbox is None:
        return svg
    width = round(float(viewbox.group(1)))
    height = round(float(viewbox.group(2)))
    return svg.replace("<svg ", f'<svg width="{width}" height="{height}" ', 1)


def _demo_console(*, width: int = 144, height: int = 40):
    from rich.console import Console

    return Console(
        color_system="truecolor",
        force_terminal=True,
        record=True,
        file=io.StringIO(),
        width=width,
        height=height,
        _environ={"COLUMNS": str(width), "LINES": str(height), "TERM": "xterm-256color"},
    )


def _write_console_asset(console, *, svg_path: Path, text_path: Path, title: str) -> None:
    text_path.write_text(console.export_text(styles=False, clear=False), encoding="utf-8")
    svg_path.write_text(_sanitize_svg(console.export_svg(title=title)), encoding="utf-8")


def _render_plan_demo() -> None:
    from rich import box
    from rich.table import Table

    console = _demo_console(width=132, height=34)
    table = Table(
        title='TokenKick plan --work-window 18:30-23:30',
        box=box.ROUNDED,
        show_lines=False,
        expand=True,
    )
    table.add_column("Use", style="bold")
    table.add_column("Account")
    table.add_column("State")
    table.add_column("Best action")
    table.add_column("Coverage")
    table.add_column("Reason")
    table.add_row(
        "1",
        "codex (work)",
        "[blue]Active[/blue]",
        "Use now",
        "18:30-20:55",
        "Session already counting down; avoid spending a fresh window.",
    )
    table.add_row(
        "2",
        "codex-spark (lab)",
        "[green]Fresh[/green]",
        "Kick at 20:55",
        "20:55-22:25",
        "Short Spark window fills the middle gap after explicit tier config.",
    )
    table.add_row(
        "3",
        "claude (personal)",
        "[yellow]Waiting[/yellow]",
        "Kick at 22:25",
        "22:25-23:30",
        "Reset lands late; pending kick is scheduled inside the work window.",
    )
    console.print(table)
    console.print()
    console.print("[bold green]Plan result[/bold green]  3 accounts cover 5h 0m with 0m projected waste.")
    console.print(
        "[dim]Apply only after review: "
        "TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-23:30 --apply --yes --json-output[/dim]"
    )
    console.print("[dim]Synthetic demo data. Planning reads cached state and does not kick by itself.[/dim]")
    _write_console_asset(
        console,
        svg_path=PLAN_SVG_PATH,
        text_path=PLAN_TEXT_PATH,
        title="TokenKick synthetic plan demo",
    )


def _text(x: int, y: int, value: str, *, size: int = 16, weight: int = 400, fill: str = "#1d1d1f") -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">'
        f"{escape(value)}</text>"
    )


def _pill(x: int, y: int, width: int, text: str, *, fill: str, stroke: str = "none", color: str = "#1d1d1f") -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="30" rx="15" fill="{fill}" stroke="{stroke}"/>'
        + _text(x + 15, y + 21, text, size=13, weight=600, fill=color)
    )


def _render_macos_main() -> None:
    svg = f'''<svg width="1440" height="920" viewBox="0 0 1440 920" xmlns="http://www.w3.org/2000/svg">
  <rect width="1440" height="920" rx="18" fill="#f5f5f7"/>
  <rect x="1" y="1" width="1438" height="918" rx="18" fill="none" stroke="#c7c7cc"/>
  <rect x="0" y="0" width="1440" height="58" rx="18" fill="#f2f2f4"/>
  <circle cx="28" cy="29" r="7" fill="#ff5f57"/>
  <circle cx="52" cy="29" r="7" fill="#febc2e"/>
  <circle cx="76" cy="29" r="7" fill="#28c840"/>
  {_text(120, 37, "TokenKick", size=17, weight=700)}
  <rect x="0" y="58" width="260" height="862" fill="#ececf0"/>
  <rect x="18" y="86" width="224" height="38" rx="8" fill="#d9e8ff"/>
  {_text(42, 111, "Status", size=15, weight=700, fill="#0b57d0")}
  {_text(42, 161, "Kick", size=15, fill="#47474f")}
  {_text(42, 207, "Planner", size=15, fill="#47474f")}
  {_text(42, 253, "Schedule", size=15, fill="#47474f")}
  {_text(42, 299, "Accounts", size=15, fill="#47474f")}
  {_text(42, 345, "History", size=15, fill="#47474f")}
  {_text(42, 391, "Diagnostics", size=15, fill="#47474f")}
  {_text(42, 855, "Synthetic demo data", size=13, fill="#6e6e73")}
  <rect x="260" y="58" width="1180" height="862" fill="#fbfbfd"/>
  {_text(308, 126, "Status", size=32, weight=750)}
  {_text(308, 158, "Local quota windows, daemon state, and next safe action.", size=15, fill="#6e6e73")}
  {_pill(1024, 103, 150, "Daemon running", fill="#e7f6ec", color="#137333")}
  {_pill(1190, 103, 154, "Cache current", fill="#e8f0fe", color="#185abc")}
  <rect x="308" y="198" width="1036" height="426" rx="12" fill="#ffffff" stroke="#d7d7dc"/>
  {_text(334, 236, "Account", size=13, weight=700, fill="#6e6e73")}
  {_text(604, 236, "State", size=13, weight=700, fill="#6e6e73")}
  {_text(792, 236, "Resets", size=13, weight=700, fill="#6e6e73")}
  {_text(1012, 236, "Used", size=13, weight=700, fill="#6e6e73")}
  {_text(1130, 236, "Action", size=13, weight=700, fill="#6e6e73")}
  <line x1="308" y1="258" x2="1344" y2="258" stroke="#e1e1e6"/>
  <circle cx="342" cy="307" r="8" fill="#34c759"/>{_text(364, 313, "codex-spark (lab)", size=16, weight=650)}
  {_text(604, 313, "Weekly ready", size=16, fill="#137333")}{_text(792, 301, "weekly in 6d 7h", size=14, fill="#6e6e73")}{_text(792, 325, "session ready", size=14, fill="#137333")}
  {_text(1012, 313, "8%", size=16, weight=650, fill="#137333")}{_text(1130, 313, "Kick now", size=16, weight=650, fill="#1d1d1f")}
  <line x1="334" y1="354" x2="1318" y2="354" stroke="#eeeeF2"/>
  <circle cx="342" cy="405" r="8" fill="#ff9f0a"/>{_text(364, 411, "claude (personal)", size=16, weight=650)}
  {_text(604, 411, "Session exhausted", size=16, fill="#a15c00")}{_text(792, 399, "weekly in 2d 4h", size=14, fill="#6e6e73")}{_text(792, 423, "session in 42m", size=14, fill="#6e6e73")}
  {_text(1012, 411, "76%", size=16, weight=650, fill="#a15c00")}{_text(1130, 411, "Wait for session", size=16, fill="#6e6e73")}
  <line x1="334" y1="452" x2="1318" y2="452" stroke="#eeeeF2"/>
  <circle cx="342" cy="503" r="8" fill="#0a84ff"/>{_text(364, 509, "codex (work)", size=16, weight=650)}
  {_text(604, 509, "Active", size=16, fill="#185abc")}{_text(792, 497, "weekly in 5d 0h", size=14, fill="#6e6e73")}{_text(792, 521, "session in 2h 35m", size=14, fill="#6e6e73")}
  {_text(1012, 509, "42%", size=16, weight=650, fill="#185abc")}{_text(1130, 509, "Use if needed", size=16, fill="#6e6e73")}
  <rect x="308" y="660" width="492" height="170" rx="12" fill="#ffffff" stroke="#d7d7dc"/>
  {_text(334, 699, "Next action", size=18, weight=750)}
  {_text(334, 731, "Kick codex-spark (lab) now, or let the daemon schedule it.", size=15, fill="#3a3a3c")}
  {_pill(334, 762, 132, "Kick now", fill="#1d1d1f", color="#ffffff")}
  {_pill(482, 762, 170, "Open planner", fill="#f2f2f4", stroke="#d1d1d6")}
  <rect x="832" y="660" width="512" height="170" rx="12" fill="#ffffff" stroke="#d7d7dc"/>
  {_text(858, 699, "Agent contract", size=18, weight=750)}
  {_text(858, 731, "Cached status is safe for repeated reads.", size=15, fill="#3a3a3c")}
  {_text(858, 759, "Live refreshes are explicit; kicks require opt-in consent.", size=15, fill="#3a3a3c")}
</svg>
'''
    MACOS_MAIN_PATH.write_text(svg, encoding="utf-8")


def _render_macos_popover() -> None:
    svg = f'''<svg width="760" height="620" viewBox="0 0 760 620" xmlns="http://www.w3.org/2000/svg">
  <rect width="760" height="620" rx="22" fill="#f7f7fa"/>
  <rect x="1" y="1" width="758" height="618" rx="22" fill="none" stroke="#c7c7cc"/>
  <path d="M369 0 L389 0 L379 12 Z" fill="#f7f7fa"/>
  <rect x="34" y="32" width="692" height="556" rx="18" fill="#ffffff" stroke="#d7d7dc"/>
  {_text(66, 78, "TokenKick", size=24, weight=760)}
  {_pill(552, 52, 132, "Ready", fill="#e7f6ec", color="#137333")}
  {_text(66, 112, "3 visible accounts · daemon polling every 5 minutes", size=14, fill="#6e6e73")}
  <line x1="66" y1="140" x2="694" y2="140" stroke="#ececf0"/>
  <circle cx="82" cy="188" r="8" fill="#34c759"/>{_text(104, 194, "codex-spark (lab)", size=17, weight=650)}
  {_text(104, 220, "Weekly ready · session ready", size=14, fill="#137333")}
  {_text(548, 194, "Kick now", size=16, weight=700, fill="#1d1d1f")}
  <line x1="66" y1="250" x2="694" y2="250" stroke="#ececf0"/>
  <circle cx="82" cy="298" r="8" fill="#ff9f0a"/>{_text(104, 304, "claude (personal)", size=17, weight=650)}
  {_text(104, 330, "Session resets in 42m", size=14, fill="#6e6e73")}
  {_text(548, 304, "Wait", size=16, fill="#6e6e73")}
  <line x1="66" y1="360" x2="694" y2="360" stroke="#ececf0"/>
  <circle cx="82" cy="408" r="8" fill="#0a84ff"/>{_text(104, 414, "codex (work)", size=17, weight=650)}
  {_text(104, 440, "Active · 2h 35m left", size=14, fill="#185abc")}
  {_text(548, 414, "Use", size=16, fill="#6e6e73")}
  <rect x="66" y="482" width="628" height="1" fill="#ececf0"/>
  {_pill(66, 514, 158, "Open app", fill="#1d1d1f", color="#ffffff")}
  {_pill(242, 514, 160, "Refresh", fill="#f2f2f4", stroke="#d1d1d6")}
  {_pill(420, 514, 182, "Show history", fill="#f2f2f4", stroke="#d1d1d6")}
  {_text(66, 574, "Synthetic demo data", size=13, fill="#8e8e93")}
</svg>
'''
    MACOS_POPOVER_PATH.write_text(svg, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="tokenkick-readme-demo-") as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["TK_NO_INTERACTIVE"] = "1"
        sys.path.insert(0, str(ROOT))

        from tokenkick import cli as cli_mod
        from tokenkick.models import AccountConfig, AccountState, AccountStatus, Config, DataSource

        console = _demo_console(width=144, height=40)
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
        _write_console_asset(
            console,
            svg_path=SVG_PATH,
            text_path=TEXT_PATH,
            title="TokenKick synthetic status demo",
        )
        _render_plan_demo()
        _render_macos_main()
        _render_macos_popover()

    print(f"Wrote {SVG_PATH}")
    print(f"Wrote {TEXT_PATH}")
    print(f"Wrote {PLAN_SVG_PATH}")
    print(f"Wrote {PLAN_TEXT_PATH}")
    print(f"Wrote {MACOS_MAIN_PATH}")
    print(f"Wrote {MACOS_POPOVER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
