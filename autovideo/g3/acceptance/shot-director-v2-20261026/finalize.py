"""Render camera polish over the preserved mixed plan, with the same pictures and voice."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[3]
runpy.run_path(str(ROOT/'g3/acceptance/shot-director-20261026/finalize.py'), run_name='__main__')
