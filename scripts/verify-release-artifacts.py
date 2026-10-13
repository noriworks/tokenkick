"""Validate the exact wheel/sdist metadata against canonical source and tag."""
import argparse
import ast
import email
from pathlib import Path
import tarfile
import zipfile
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 verification job
    import tomli as tomllib


def verify(tag, paths, root=Path(__file__).resolve().parents[1]):
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    tree = ast.parse((root / 'tokenkick/__init__.py').read_text())
    assignments = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    # Production derives its runtime version from the installed distribution metadata.
    derives_metadata = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id == 'version' and len(n.args) == 1
                           and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'tokenkick'
                           for n in assignments)
    source_version = version if derives_metadata else next(
        (n.value for n in assignments if isinstance(n, ast.Constant)), None)
    if tag != version or source_version != version:
        raise ValueError('Tag, project version and runtime version must match exactly')
    kinds = set()
    for path in map(Path, paths):
        if path.suffix == '.whl':
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
                if len(names) != 1:
                    raise ValueError('Wheel must contain one distribution')
                data = archive.read(names[0])
            kinds.add('wheel')
        elif path.name.endswith('.tar.gz'):
            with tarfile.open(path) as archive:
                names = [n for n in archive.getnames() if n.count('/') == 1 and n.endswith('/PKG-INFO')]
                if len(names) != 1:
                    raise ValueError('sdist must contain one root metadata file')
                data = archive.extractfile(names[0]).read()
            kinds.add('sdist')
        else:
            raise ValueError(f'Unexpected release artifact: {path.name}')
        metadata = email.message_from_bytes(data)
        if metadata['Name'] != 'tokenkick' or metadata['Version'] != version:
            raise ValueError(f'Artifact metadata mismatch: {path.name}')
    if kinds != {'wheel', 'sdist'}:
        raise ValueError('Both wheel and sdist must be verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    verify(args.tag, args.paths)
    print('Exact release artifact versions: passed')
