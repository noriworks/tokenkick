"""Keep all imported runtime state paths out of the developer's real installation."""
from pathlib import Path
import sys
import pytest

_RUNTIME_ROOT = Path.home() / '.tokenkick'


@pytest.fixture(autouse=True)
def isolate_runtime_paths(tmp_path, monkeypatch):
    root = tmp_path / 'runtime-state'
    for name, module in list(sys.modules.items()):
        if not name.startswith('tokenkick.') or module is None:
            continue
        for key, value in list(vars(module).items()):
            if isinstance(value, Path) and value.is_relative_to(_RUNTIME_ROOT):
                monkeypatch.setattr(module, key, root / value.relative_to(_RUNTIME_ROOT))
