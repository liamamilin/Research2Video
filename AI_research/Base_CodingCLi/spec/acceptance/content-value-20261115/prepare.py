"""Create an isolated baseline and apply the candidate overlay without changing production."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
BASE = Path('/tmp/content-value-20261115')

if __name__ == '__main__':
    for variant in ('baseline', 'candidate'):
        dest = BASE / variant
        if dest.exists():
            raise FileExistsError(f'Preserve previous experiments; choose a fresh directory: {dest}')
        dest.mkdir(parents=True)
        for name in ('core', 'utils', 'web', 'config', 'jobs', 'tests', 'scripts'):
            shutil.copytree(ROOT/name, dest/name,
                            ignore=shutil.ignore_patterns('__pycache__', 'node_modules', '.DS_Store'))
        for p in ROOT.glob('*.py'):
            shutil.copy2(p, dest/p.name)
        for name in ('state', 'logs', 'results'):
            (dest/name).mkdir()
    overlay = Path(__file__).resolve().parent/'overlay'
    shutil.copytree(overlay, BASE/'candidate', dirs_exist_ok=True)
