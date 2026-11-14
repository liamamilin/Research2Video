"""Same real narration/plan, isolated renderer alternatives; not E2E acceptance."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT/'g3/data/validation/daily-effects-20261111'
parser = argparse.ArgumentParser()
parser.add_argument('--variant', choices=['before', 'all', 'labels'], required=True)
parser.add_argument('--case', type=int, default=4)
parser.add_argument('--frames', default='384-1288')
args = parser.parse_args()
record = json.loads((BASE/'inventory.json').read_text())[args.case]
source = Path(record['props'])
out = BASE/'experiments'/f'{args.case}-{args.variant}'
out.mkdir(parents=True, exist_ok=False)
shutil.copy2(source, out/'props.json')
shutil.copytree(BASE/'baseline-renderer/src', out/'renderer/src')
(out/'renderer/node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
path = out/'renderer/src/Root.tsx'
code = path.read_text()
if args.variant != 'before':
    old = '...panelStyle,...motion(target),height:compact ? 250 : 440'
    assert code.count(old) == 1
    code = code.replace(old, '...panelStyle,opacity:ease((frame-scene.start_frame)/12),height:compact ? 250 : 440')
    if args.variant == 'labels':
        old = "{body && <div style={{...textStyle,color:paper.muted,fontSize:S(compact ? 28 : p.lines.length === 3 ? 30 : 34)"
        assert code.count(old) == 1
        code = code.replace(old, "{body && <div style={{...textStyle,...motion(target),color:paper.muted,fontSize:S(compact ? 28 : p.lines.length === 3 ? 30 : 34)")
path.write_text(code)
argv = ['node', str(ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'),
        'render', 'src/index.ts', 'G1CommunicationNoise', str(out/'video.mp4'), '--codec=h264',
        '--crf=20', '--concurrency=3', f'--props={out/"props.json"}',
        f'--public-dir={source.parents[1]/"fixtures"}',
        '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '--bundle-cache=false', f'--frames={args.frames}']
(out/'command.json').write_text(json.dumps(argv, ensure_ascii=False, indent=2))
(out/'source.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
with (out/'render.log').open('w') as log:
    subprocess.run(argv, cwd=out/'renderer', stdout=log, stderr=log, check=True)
print(out, flush=True)
