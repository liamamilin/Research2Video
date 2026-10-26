"""Use the common frame-driven camera sampler for the V2 mixed-material video."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[3]
runpy.run_path(str(ROOT/'g3/acceptance/shot-director-20261026/frames.py'), run_name='__main__')
