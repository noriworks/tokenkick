"""Create a source export in a new directory; never delete the destination."""

from pathlib import Path
import shutil
import subprocess
import sys


def export(root: Path, destination: Path) -> None:
    root, destination = root.resolve(), destination.resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError("Export destination overlaps the source repository")
    if destination.exists():
        raise FileExistsError(destination)
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"])
    if dirty.strip():
        raise ValueError("Export requires a clean, verified commit; commit or isolate the current batch first")
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    destination.mkdir(parents=True, exist_ok=False)
    omitted = {"docs/APP_UX_PLAN.md", "docs/RELEASE.md"}
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode())
        if str(relative) in omitted:
            continue
        source, target = root / relative, destination / relative
        if source.is_symlink():
            raise ValueError(f"Refusing to export a symbolic link: {relative}")
        if root not in source.resolve().parents:
            raise ValueError("Tracked path escapes the source repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


if __name__ == "__main__":
    try:
        export(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f"Export failed: {exc}")
    print(Path(sys.argv[2]).resolve())
