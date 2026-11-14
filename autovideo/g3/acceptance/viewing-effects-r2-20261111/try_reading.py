"""Isolated image presentation comparisons; original jobs are never edited."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-r2-20261111'
parser = argparse.ArgumentParser()
parser.add_argument('--case', required=True)
parser.add_argument('--scene', required=True)
parser.add_argument('--method', choices=['original', 'reading', 'trim'], required=True)
parser.add_argument('--video', action='store_true')
args = parser.parse_args()
record = next(r for r in json.loads((ROOT / 'g3/data/validation/viewing-effects-20261111/baseline.json').read_text()) if r['name'] == args.case)
source = Path(record['props'])
fixtures = source.parents[1] / 'fixtures'
out = BASE / f'{args.case}-{args.scene}-{args.method}{"-video" if args.video else ""}'
out.mkdir(exist_ok=False)
props = json.loads(source.read_text())
scene = next(s for s in props['ir']['scenes'] if s['id'] == args.scene)
renderer = out / 'renderer'
shutil.copytree(BASE / 'baseline-renderer/src', renderer / 'src')
(renderer / 'node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
path = renderer / 'src/DirectedMaterials.tsx'
code = path.read_text()
if args.method != 'original':
    code = code.replace('const detailed=p.images.some((image:any)=>image.focus?.regions?.length);', 'const detailed=true;')
    code = code.replace('stageHeight=detailed?510:570', 'stageHeight=detailed?630:570')
    code = code.replace('pair?(detailed?400:450):(detailed?488:530)', 'pair?(detailed?530:450):(detailed?608:530)')
    code = code.replace('left:140,top:300,width:1640,height:570', 'left:140,top:270,width:1640,height:670')
    code = code.replace('top:528,', 'top:638,')
    code = code.replace('{detailed?<div', '{detailed&&!p.images.some((image:any)=>image.focus?.regions?.length)?null:detailed?<div')
if args.method == 'trim':
    for image in scene['props']['images']:
        picture = Image.open(fixtures / image['image_path']).convert('RGBA')
        backdrop = Image.new('RGBA', picture.size, 'white')
        picture = Image.alpha_composite(backdrop, picture).convert('RGB')
        bounds = ImageChops.difference(picture, Image.new('RGB', picture.size, 'white')).getbbox()
        width, height = picture.size
        pad = round(min(width, height) * .025)
        if bounds:
            x1, y1, x2, y2 = bounds
            bounds = [max(0, x1-pad), max(0, y1-pad), min(width, x2+pad), min(height, y2+pad)]
        image['reading_bounds'] = {'width': width, 'height': height, 'box': bounds or [0,0,width,height]}
    start = code.index('  const focus=image.focus;')
    end = code.index('  const rects:', start)
    code = code[:start] + '''  const focus=image.focus, reading=image.reading_bounds;
  const bounds=reading?.box;
  const fit=bounds?Math.min(width/(bounds[2]-bounds[0]),height/(bounds[3]-bounds[1])):
    focus?Math.min(width/focus.width,height/focus.height):1;
  const w=reading?reading.width*fit:focus?focus.width*fit:width;
  const h=reading?reading.height*fit:focus?focus.height*fit:height;
  const x=bounds?(width-(bounds[2]-bounds[0])*fit)/2-bounds[0]*fit:(width-w)/2;
  const y=bounds?(height-(bounds[3]-bounds[1])*fit)/2-bounds[1]*fit:(height-h)/2;
''' + code[end:]
path.write_text(code)
(out / 'props.json').write_text(json.dumps(props, ensure_ascii=False))
(out / 'source.json').write_text(json.dumps(record, ensure_ascii=False))
cli = ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
common = [f'--props={out / "props.json"}', f'--public-dir={fixtures}', '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
if args.video:
    command = ['node', str(cli), 'render', 'src/index.ts', 'G1CommunicationNoise', str(out / 'video.mp4'),
               f'--frames={scene["start_frame"]}-{scene["end_frame"] - 1}', '--codec=h264', '--crf=20', '--concurrency=3', *common]
else:
    frame = (scene['start_frame'] + scene['end_frame']) // 2
    command = ['node', str(cli), 'still', 'src/index.ts', 'G1CommunicationNoise', str(out / 'frame.png'), f'--frame={frame}', *common]
(out / 'command.json').write_text(json.dumps(command))
with (out / 'render.log').open('w') as log:
    subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True, timeout=600)
print(out, flush=True)
