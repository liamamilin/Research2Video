"""Controlled same-version A/B: original cues versus retained presentation fixes."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-20261111'
sys.path.insert(0, str(ROOT))
from g2 import information_motion as motion
from g1.video_ir import validate_video_ir

parser = argparse.ArgumentParser()
parser.add_argument('--case', required=True)
parser.add_argument('--scene', required=True)
parser.add_argument('--resume-after-failure', action='store_true')
args = parser.parse_args()
record = next(r for r in json.loads((BASE / 'baseline.json').read_text()) if r['name'] == args.case)
source = Path(record['props'])
fixtures = source.parents[1] / 'fixtures'
folder = BASE / f'final-{args.case}-{args.scene}'
folder.mkdir(exist_ok=args.resume_after_failure)
if args.resume_after_failure:
    for filename in ('after-props.json', 'after-render.log', 'after-command.json', 'after.mp4'):
        path = folder / filename
        if path.exists():
            path.rename(folder / ('attempt1-' + filename))

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))

original = json.loads(source.read_text())
raw = json.loads((fixtures / 'video-ir-v0.2.json').read_text())
untouched = copy.deepcopy(raw)
scene = next(s for s in raw['scenes'] if s['id'] == args.scene)
old_beats = copy.deepcopy(scene['beats'])
pipeline = SimpleNamespace(ARTIFACTS=folder, write_json=write)
board = motion.compile_storyboard({'scenes': [{**scene, 'headline': scene['purpose']}]},
                                 {'rows': raw['narration']['segments']}, pipeline)
new_beats = {b['target_id']: b for b in board['scenes'][0]['beats'] if b['action'] == 'reveal'}
timing = json.loads((folder / 'information-timing.json').read_text())
for beat in scene['beats']:
    replacement = new_beats.get(beat['target_id']) if beat['action'] == 'reveal' else None
    if replacement:
        beat['trigger'] = replacement['trigger']
        for cue in timing['cues']:
            if cue['beat_id'] == replacement['id']:
                cue['beat_id'] = beat['id']
captions = json.loads((source.parent / 'caption-alignment.json').read_text())
alignment = motion.align_visual_beats(raw, captions, timing)
scene['beats'].sort(key=lambda b: next(r['start_ms'] for r in raw['narration']['segments']
                                    if r['id'] == b['trigger']['segment_id']) + b['trigger'].get('offset_ms', 0))
new_props = {**original, 'ir': {**original['ir'], **validate_video_ir(raw, fixtures)}}
assert raw['narration'] == untouched['narration'] and raw['assets'] == untouched['assets']
assert scene['props'] == next(s for s in untouched['scenes'] if s['id'] == args.scene)['props']
write(folder / 'evidence.json', {'source': str(source), 'original_video': record['video'],
    'narration_and_assets_unchanged': True, 'scene_props_unchanged': True,
    'before_beats': old_beats, 'after_beats': scene['beats'], 'word_alignment': alignment})
write(folder / 'after-ir.json', raw)
for variant, props, src in [('before', original, BASE / 'baseline-renderer/src'),
                            ('after', new_props, ROOT / 'g1/renderer/src')]:
    if args.resume_after_failure and variant == 'before':
        continue
    renderer = folder / f'{variant}-renderer'
    shutil.copytree(src, renderer / 'src', dirs_exist_ok=args.resume_after_failure)
    if not (renderer / 'node_modules').exists():
        (renderer / 'node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
    write(folder / f'{variant}-props.json', props)
    compiled = next(s for s in props['ir']['scenes'] if s['id'] == args.scene)
    command = ['node', str(ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'),
        'render', 'src/index.ts', 'G1CommunicationNoise', str(folder / f'{variant}.mp4'),
        f'--frames={compiled["start_frame"]}-{compiled["end_frame"] - 1}', '--codec=h264', '--crf=20',
        '--concurrency=3', f'--props={folder / f"{variant}-props.json"}', f'--public-dir={fixtures}',
        '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
    write(folder / f'{variant}-command.json', command)
    with (folder / f'{variant}-render.log').open('w') as log:
        subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True, timeout=600)
    print(args.case, variant, 'rendered', flush=True)
