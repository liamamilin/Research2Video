"""Extract picture states and executor boundaries from the actual joined MP4."""
import argparse
import json
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
out = args.directory.resolve()
ir = json.loads((out/'video-ir.json').read_text())
plan = json.loads((out/'hybrid-card-plan.json').read_text())
folder = out/'frames'
folder.mkdir(exist_ok=False)
targets = {}
for page in plan['pages']:
    start, end = page['start_frame'], page['end_frame']
    targets[max(start, end-18)] = {'scene': page['scene_id'], 'component': page['component'], 'state': 'settled'}
    if page['executor'] == 'remotion':
        targets[min(end-1, start+36)] = {'scene': page['scene_id'], 'component': page['component'], 'state': 'opening'}
for block in plan['blocks'][1:]:
    for delta in (-1, 0, 8):
        targets[block['start_frame']+delta] = {'scene': block['scene_ids'][0], 'state': f'boundary{delta:+d}'}
targets = dict(sorted(targets.items()))
expression = '+'.join(f'eq(n\\,{frame})' for frame in targets)
subprocess.run([str(ROOT/'g0/darwin_arm64/ffmpeg'), '-nostdin', '-v', 'error', '-i', str(out/'content.mp4'),
    '-vf', f'select={expression},scale=960:-1', '-vsync', '0', str(folder/'frame-%03d.png')], check=True)
images = sorted(folder.glob('*.png'))
assert len(images) == len(targets)
rows = []
sheet = Image.new('RGB', (1920, ((len(images)+3)//4)*294), '#edf1ec')
draw = ImageDraw.Draw(sheet)
for i, (path, (frame, metadata)) in enumerate(zip(images, targets.items())):
    rows.append({'file': path.name, 'frame': frame, 'seconds': round(frame/30, 3), **metadata})
    picture = Image.open(path).convert('RGB').resize((480, 270))
    x, y = i%4*480, i//4*294
    sheet.paste(picture, (x,y))
    draw.text((x+8,y+273), f"{frame} | {metadata['scene']} | {metadata['state']}", fill='#182720')
sheet.save(out/'contact.png')
(out/'frames.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print(json.dumps({'frames': len(rows), 'contact': str(out/'contact.png')}))
