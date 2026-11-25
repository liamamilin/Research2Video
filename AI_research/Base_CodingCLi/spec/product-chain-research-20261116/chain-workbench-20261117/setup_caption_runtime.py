"""Install software locally; preserve externally stored models and generated data."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ENV = ROOT / 'runtime/deps/alignment-env'
PYTHON = ENV / 'bin/python'
if not PYTHON.is_file():
    subprocess.run([sys.executable, '-m', 'venv', str(ENV)], check=True)
env = {**os.environ, 'PIP_CACHE_DIR': str(ROOT / 'runtime/cache/pip')}
subprocess.run([str(PYTHON), '-m', 'pip', 'install', 'mlx-audio==0.5.9', 'soundfile==0.14.0'], env=env, check=True)
subprocess.run([str(PYTHON), '-c', 'import numpy, soundfile, scipy; from mlx_audio.stt.utils import load_model; print("Caption runtime imports ready")'], check=True, timeout=120)
link = ROOT / 'runtime/renderer/g3/data/caption-runtime'
if not link.exists() and not link.is_symlink():
    link.symlink_to(ENV, target_is_directory=True)
elif link.resolve() != ENV.resolve():
    raise RuntimeError(f'Existing caption runtime belongs elsewhere: {link}')
print('Dedicated caption runtime ready; original voice runtime unchanged')
