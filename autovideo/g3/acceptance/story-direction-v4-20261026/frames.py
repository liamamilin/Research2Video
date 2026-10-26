"""Inspect overview, local focus, comparisons, and summary from the real MP4."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
args = parser.parse_args()
source = args.source.resolve()
location = json.loads((source/'location.json').read_text())
job = Path(location['engine_data'])/'jobs'/location['job_id']
attempt = (Path(location['render_run']) if location.get('render_run') else
           max((job/'attempts').iterdir(), key=lambda p: int(p.name))/'g2-run')
timeline = attempt/'artifacts/compiled-timeline.json'
entry = source/'camera-report.ts'
entry.write_text('''
import fs from 'node:fs';
import {shotsFor,linkScenes,transitionAt,pacingFor} from CAMERA;
const ir=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
console.log(JSON.stringify(linkScenes(ir.scenes).map(s=>({
  id:s.id,component:s.component,start:s.start_frame,end:s.end_frame,shots:shotsFor(s),story:s.props.story,pacing:pacingFor(s),flow:s.props.scene_flow,transition:transitionAt(s,s.start_frame)}))));
'''.replace('CAMERA', json.dumps(str(ROOT/'g1/renderer/src/ShotDirector.tsx'))))
bundle = source/'camera-report.cjs'
env = {**os.environ, 'NODE_PATH': str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}
subprocess.run([str(ROOT/'g0/vendor/remotion-audiogram/node_modules/.bin/esbuild'), str(entry),
    '--bundle', '--platform=node', '--format=cjs', '--jsx=automatic', f'--outfile={bundle}'], env=env, check=True)
report = json.loads(subprocess.check_output([shutil.which('node'), str(bundle), str(timeline)], text=True))
out = source/'frames'
out.mkdir(exist_ok=False)
frames = []
ffmpeg = str(ROOT/'g0/darwin_arm64/ffmpeg')
for scene in report:
    for i, shot in enumerate(scene['shots']):
        next_frame = scene['shots'][i+1]['frame'] if i+1<len(scene['shots']) else scene['end']
        at = min(shot['frame']+30, next_frame-1)
        name = f"{scene['id']}-{i:02}-{shot['kind']}.png"
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(at/30), '-i', str(source/'content.mp4'),
            '-frames:v', '1', '-vf', 'scale=960:-1', '-update', '1', str(out/name)], check=True)
        frames.append({'scene': scene['id'], 'component': scene['component'], **shot,
                       'capture_frame': at, 'file': name})
for scene in report:
    if scene.get('story', {}).get('recap'):
        for label,at in [('middle',(scene['start']+scene['end'])//2),('ending',scene['end']-30)]:
            name=f"{scene['id']}-recap-{label}.png"
            subprocess.run([ffmpeg,'-v','error','-ss',str(at/30),'-i',str(source/'content.mp4'),'-frames:v','1','-vf','scale=960:-1','-update','1',str(out/name)],check=True)
            frames.append({'scene':scene['id'],'capture_frame':at,'recap':scene['story']['recap'],'file':name})
for scene in report[1:]:
    for label, at in [('before', scene['start']-1), ('entry', scene['start']),
                      ('middle', scene['start']+scene['transition']['length']//2),
                      ('after', scene['start']+scene['transition']['length'])]:
        name=f"{scene['id']}-boundary-{label}.png"
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(at/30), '-i', str(source/'content.mp4'),
            '-frames:v', '1', '-vf', 'scale=960:-1', '-update', '1', str(out/name)], check=True)
        frames.append({'scene': scene['id'], 'capture_frame': at, 'entry': scene['transition']['entry'], 'file': name})
(source/'shots.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
(source/'frames.json').write_text(json.dumps(frames, ensure_ascii=False, indent=2))
decodes = {}
for name in ('content.mp4', 'video.mp4'):
    result = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(source/name), '-f', 'null', '-'],
                            capture_output=True, text=True)
    decodes[name] = {'returncode': result.returncode, 'stderr': result.stderr}
    assert result.returncode == 0 and not result.stderr, decodes[name]
(source/'decode.json').write_text(json.dumps(decodes, indent=2))
print(json.dumps({'scenes': len(report), 'frames': len(frames), 'decode': decodes}), flush=True)
