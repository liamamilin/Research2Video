"""Prepare a fresh isolated comparison; refuse to overwrite earlier attempts."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = Path('/tmp/content-value-scope-20261115')


def main():
    if BASE.exists():
        raise FileExistsError(f'Preserve earlier attempts; choose a fresh BASE: {BASE}')
    for relative, expected in json.loads((HERE/'source-hashes.json').read_text()).items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Production input changed; freeze a new comparison explicitly: {relative}')
    for variant in ('baseline', 'candidate'):
        dest = BASE/variant
        dest.mkdir(parents=True)
        for name in ('core', 'utils', 'web', 'config', 'jobs', 'tests', 'scripts'):
            shutil.copytree(ROOT/name, dest/name,
                            ignore=shutil.ignore_patterns('__pycache__', 'node_modules', '.DS_Store'))
        for path in ROOT.glob('*.py'):
            shutil.copy2(path, dest/path.name)
        for name in ('state', 'logs'):
            (dest/name).mkdir()
        shutil.copy2(HERE.parent/'overlay/core/video/audience_strategy.py', dest/'core/video/audience_strategy.py')
    shutil.copytree(HERE/'overlay', BASE/'candidate', dirs_exist_ok=True)
    shutil.copytree(ROOT/'state/acceptance-content-value-20261115/inputs', BASE/'inputs')
    for channel in ('flash', 't05', 't06'):
        dest = BASE/'results'/channel/'candidate'
        dest.mkdir(parents=True)
        shutil.copy2(HERE.parent/'results'/channel/'selection.json', dest/'selection.json')


if __name__ == '__main__':
    main()
