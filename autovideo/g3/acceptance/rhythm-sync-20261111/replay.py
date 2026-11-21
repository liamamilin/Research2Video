"""Replay saved plans through normal G2 compilation, preserving original audio."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/rhythm-sync-20261111'
sys.path.insert(0, str(ROOT))
from g1.video_ir import validate_video_ir
from g2 import explanation, information_motion, run_g2
from boundary_candidate import reconcile

parser = argparse.ArgumentParser()
parser.add_argument('--case', default='news')
parser.add_argument('--variant', choices=['before', 'candidate', 'after'], required=True)
parser.add_argument('--frames', default='1420-2077')
parser.add_argument('--name', default='boundary')
parser.add_argument('--prepare-only', action='store_true')
args = parser.parse_args()
record = next(r for r in json.loads((BASE/'cases.json').read_text()) if r['name'] == args.case)
source = Path(record['props'])
fixtures = source.parents[1] / 'fixtures'
job = source.parents[4]
original = json.loads(source.read_text())
raw = json.loads((fixtures/'video-ir-v0.2.json').read_text())
plan = json.loads((source.parent/'explanation-plan.json').read_text())
saved = json.loads((job/'input/input.json').read_text())
saved['video_speed'] = 1  # The reused PCM/rows already include the original speed adjustment.
out = BASE/f'{args.name}-{args.case}'/args.variant
out.mkdir(parents=True, exist_ok=False)
artifacts = out/'artifacts'
artifacts.mkdir()
def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
def command(label, argv, cwd, timeout=60):
    result = subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True)
    (out/f'{label}.log').write_text(result.stdout+result.stderr)
    return result.stdout
pipeline = SimpleNamespace(ARTIFACTS=artifacts, PROJECT=ROOT, NODE='node', write_json=write,
                           _run_fixed_command=command, G2Fault=run_g2.G2Fault)
alignment = {'rows': raw['narration']['segments'], 'duration_ms': raw['video']['duration_ms'],
             'audio_path': fixtures/'assets/communications_noise.wav'}
if args.variant == 'candidate':
    plan, changes = reconcile(plan, alignment)
    write(out/'boundary-changes.json', changes)
def compile_board():
    return explanation.compile_storyboard(pipeline, plan, alignment, saved,
                                         [a for a in raw['assets'] if a['type'] == 'image'])
if args.variant == 'before':
    # Acceptance-only baseline switch; production always uses the integrated path.
    with patch('g2.scene_boundaries.reconcile', lambda p, a, s: (p, [])):
        board = compile_board()
else:
    board = compile_board()
run_g2.PROJECT_INPUT = job/'input/source.txt'
run_g2.CONTENT_MODE = False
compiled_ir = run_g2.compile_ir({'title': raw['video']['title'], 'segments': raw['narration']['segments']}, board, alignment)
updated = {**copy.deepcopy(raw), 'scenes': compiled_ir['scenes']}
captions = json.loads((source.parent/'caption-alignment.json').read_text())
timing = json.loads((artifacts/'information-timing.json').read_text())
information_motion.align_visual_beats(updated, captions, timing)
assert updated['narration'] == raw['narration']
assert updated['video'] == raw['video']
write(out/'video-ir.json', updated)
props = {**original, 'ir': {**original['ir'], **validate_video_ir(updated, fixtures)}}
write(out/'props.json', props)
write(out/'source.json', record)
renderer = out/'renderer'
shutil.copytree(BASE/'baseline-renderer/src', renderer/'src')
(renderer/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
cli = ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
argv = ['node', str(cli), 'render', 'src/index.ts', 'G1CommunicationNoise', str(out/'video.mp4'),
        '--codec=h264', '--crf=20', '--concurrency=3', f'--props={out/"props.json"}',
        f'--public-dir={fixtures}', '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '--bundle-cache=false']
if args.frames != 'all':
    argv.append(f'--frames={args.frames}')
write(out/'command.json', argv)
if not args.prepare_only:
    with (out/'render.log').open('w') as log:
        subprocess.run(argv, cwd=renderer, stdout=log, stderr=log, check=True, timeout=1200)
print(out, flush=True)
