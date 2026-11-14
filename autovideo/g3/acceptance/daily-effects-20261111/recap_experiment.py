"""Local comparison only: keep the automatic plan instead of inferred thumbnails."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--job', required=True)
parser.add_argument('--frames', required=True)
args = parser.parse_args()
run = ROOT / 'g3/data/jobs' / args.job / 'attempts/1/g2-run'
out = ROOT / 'g3/data/validation/daily-effects-20261111/recap-experiment' / args.job
out.mkdir(parents=True, exist_ok=False)
props = json.loads((run / 'artifacts/render-props.json').read_text())


def strip_inferred_recap(value):
    if isinstance(value, dict):
        if isinstance(value.get('story'), dict):
            value['story'].pop('recap', None)
        for item in value.values():
            strip_inferred_recap(item)
    elif isinstance(value, list):
        for item in value:
            strip_inferred_recap(item)


strip_inferred_recap(props)
(out / 'props.json').write_text(json.dumps(props, ensure_ascii=False))
shutil.copytree(ROOT / 'g1/renderer/src', out / 'renderer/src')
(out / 'renderer/node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
command = ['node', str(ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'),
           'render', 'src/index.ts', 'G1CommunicationNoise', str(out / 'native-plan.mp4'),
           f'--props={out / "props.json"}', f'--public-dir={run / "fixtures"}',
           '--codec=h264', '--crf=20', '--concurrency=3', f'--frames={args.frames}',
           '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
(out / 'command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2))
with (out / 'render.log').open('w') as log:
    subprocess.run(command, cwd=out / 'renderer', stdout=log, stderr=log, check=True)
print(out, flush=True)
