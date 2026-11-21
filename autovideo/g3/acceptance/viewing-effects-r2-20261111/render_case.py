"""Render unchanged real stories through the normal compiler for controlled A/B."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-r2-20261111'
sys.path.insert(0, str(ROOT))
from g1.video_ir import validate_video_ir

parser = argparse.ArgumentParser()
parser.add_argument('--case', required=True)
parser.add_argument('--scene', required=True)
parser.add_argument('--variant', choices=['before', 'after'], required=True)
parser.add_argument('--hybrid', action='store_true')
args = parser.parse_args()
record = next(r for r in json.loads((BASE / 'cases.json').read_text()) if r['name'] == args.case)
source = Path(record['props'])
fixtures = source.parents[1] / 'fixtures'
out = BASE / f'final-{"hybrid-" if args.hybrid else ""}{args.case}-{args.scene}' / args.variant
out.mkdir(parents=True, exist_ok=False)
props = json.loads(source.read_text())
original = copy.deepcopy(props)
raw = json.loads((fixtures / 'video-ir-v0.2.json').read_text())
raw_copy = copy.deepcopy(raw)
if args.variant == 'after':
    props['ir'] = {**props['ir'], **validate_video_ir(raw, fixtures)}
    assert raw == raw_copy
    assert props['ir']['narration'] == original['ir']['narration']
    assert props['ir']['caption_cues'] == original['ir']['caption_cues']
    for new, old in zip(props['ir']['scenes'], original['ir']['scenes']):
        assert {k: v for k, v in new.items() if k != 'image_reading'} == old
scene = next(s for s in props['ir']['scenes'] if s['id'] == args.scene)
if args.hybrid:
    from g2.fast_cards import draw_card
    from g2.production_plan import STYLES
    public = out / 'fixtures'
    assets = public / 'assets'
    assets.mkdir(parents=True)
    for item in (fixtures / 'assets').iterdir():
        (assets / item.name).symlink_to(item, target_is_directory=item.is_dir())
    backgrounds = assets / 'reading-qa'
    backgrounds.mkdir()
    mapping = {}
    for index, current in enumerate(props['ir']['scenes']):
        path = backgrounds / f'{index}.png'
        draw_card({'props': {'title': current['props'].get('title', current['purpose']), 'text': ''}},
                  title=props['ir']['video']['title'], index=index, count=len(props['ir']['scenes']),
                  style=STYLES['editorial_light'], badge={'text': '', 'color': '#1f7a4d'}, notices=[], output=path)
        mapping[current['id']] = {'path': path.relative_to(public).as_posix(), 'still': False}
    props['fastCardBackgrounds'] = mapping
    fixtures = public
renderer = out / 'renderer'
shutil.copytree(ROOT / 'g1/renderer/src' if args.variant == 'after' else BASE / 'baseline-renderer/src', renderer / 'src')
(renderer / 'node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
(out / 'props.json').write_text(json.dumps(props, ensure_ascii=False))
(out / 'source.json').write_text(json.dumps(record, ensure_ascii=False))
(out / 'evidence.json').write_text(json.dumps({'normal_compiler': args.variant == 'after',
    'authored_ir_unchanged': raw == raw_copy, 'image_reading': scene.get('image_reading'),
    'hybrid_picture_only': args.hybrid,
    'seconds': (scene['end_frame'] - scene['start_frame']) / 30}, ensure_ascii=False, indent=2))
cli = ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
command = ['node', str(cli), 'render', 'src/index.ts', 'G1CommunicationNoise', str(out / 'video.mp4'),
    f'--frames={scene["start_frame"]}-{scene["end_frame"] - 1}', '--codec=h264', '--crf=20', '--concurrency=3',
    f'--props={out / "props.json"}', f'--public-dir={fixtures}',
    '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
(out / 'command.json').write_text(json.dumps(command))
with (out / 'render.log').open('w') as log:
    subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True, timeout=600)
print(out, flush=True)
