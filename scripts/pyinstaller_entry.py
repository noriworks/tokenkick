"""PyInstaller entry point for the bundled `tk` binary.

Kept separate from the console-script entry so PyInstaller has a real
file to analyze; behavior must stay identical to running `tk`.
"""

from tokenkick.cli import cli

if __name__ == "__main__":
    cli()
