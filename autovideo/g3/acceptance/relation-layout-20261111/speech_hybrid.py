"""Use production hybrid picture and join paths with the measured voice and captions."""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
source, out = a.source.resolve(), a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ['AUTOVIDEO_G2_RUN_DIR'] = str(out)
os.environ['AUTOVIDEO_G2_DURATION_MODE'] = 'content'
from g2 import run_g2 as engine, hybrid_cards

shutil.copytree(source / 'fixtures', engine.FIXTURE_ROOT)
for path in [engine.ARTIFACTS, engine.LOGS]:
    path.mkdir(parents=True, exist_ok=True)
for name in ['caption-alignment.json', 'timing-evidence.json', 'captions.srt']:
    shutil.copyfile(source / 'artifacts' / name, engine.ARTIFACTS / name)
props = json.loads((source / 'artifacts/render-props.json').read_text())
ir = props['ir']
scene = ir['scenes'][0]
im = Image.new('RGB', (1920, 1080), '#F7F8F5')
draw = ImageDraw.Draw(im)
font = '/System/Library/Fonts/Hiragino Sans GB.ttc'
draw.text((140, 70), '关系分解 · 真实旁白 · 混合卡片', font=ImageFont.truetype(font, 25), fill='#17784B')
draw.text((140, 155), scene['props']['title'], font=ImageFont.truetype(font, 58), fill='#202D28')
im.save(engine.ASSETS / 'relation-card.png')
root = engine.ARTIFACTS / 'hybrid'
root.mkdir()
clip = root / 'picture.mp4'
engine.render(ir, renderer_root=out / 'renderer', hybrid_segment={
    'start_frame': 0, 'end_frame': ir['total_frames'], 'output': clip, 'label': 'relation-hybrid',
    'backgrounds': {scene['id']: {'path': 'assets/relation-card.png', 'still': False}}})
# Local acceptance style only; no persisted production/account setting changes.
engine.PACKAGING_INPUT = {'production_plan': {'style': {'subtitle': '#202D28', 'background': '#F7F8F5'}}}
encoder, failures = hybrid_cards.join_clips(engine, ir, json.loads(engine.IR_PATH.read_text()), [{'path': clip}], root)
engine.write_json(engine.ARTIFACTS / 'hybrid-evidence.json', {'encoder': encoder, 'failures': failures, 'source': str(source)})
engine._run_fixed_command('decode', [engine.FFMPEG, '-v', 'error', '-xerror', '-i', engine.VIDEO_PATH, '-f', 'null', '-'], ROOT, timeout=120)
print(json.dumps({'video': str(engine.VIDEO_PATH), 'frames': ir['total_frames']}))
