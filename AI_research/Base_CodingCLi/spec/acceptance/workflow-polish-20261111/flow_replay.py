"""Repair the saved branching scene, reuse all speech, then verify and package the full video."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5] / 'autovideo'
sys.path.insert(0, str(ROOT))
old = Path('/Volumes/个人资料/2026/视频生成工作流/AI创作/视频输出/制作数据/jobs/72e5fba3-b873-4f8b-8151-b4f65f22acf4/attempts/1/g2-run')
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--reuse-scene', type=Path)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
intent = old.parents[2] / 'input/input.json'
saved = json.loads(intent.read_text())
os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(out), AUTOVIDEO_G2_RUN_ID='workflow-polish-flow',
                  AUTOVIDEO_G2_INTENT_FILE=str(intent), AUTOVIDEO_G2_VIDEO_SPEED=str(saved['video_speed']),
                  AUTOVIDEO_G2_DURATION_MODE='content', AUTOVIDEO_G2_VOICE_ID=saved['voice_id'])
from g2 import run_g2 as pipeline
from g1.video_ir import validate_video_ir
pipeline.ARTIFACTS.mkdir(); pipeline.LOGS.mkdir()
shutil.copytree(old/'fixtures', pipeline.FIXTURE_ROOT)
for path in (old/'artifacts').iterdir():
    if path.is_file() and path.suffix in {'.json', '.srt', '.txt'}:
        shutil.copy2(path, pipeline.ARTIFACTS/path.name)
pipeline.PACKAGING_INPUT = saved
ir = json.loads(pipeline.IR_PATH.read_text())
compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
scene = next(s for s in compiled['scenes'] if s['id'] == 'card_13')
left, right = scene['start_frame'], scene['end_frame']
full_path = pipeline.VIDEO_PATH
pipeline.VIDEO_PATH = out/'corrected-scene.mp4'
runner = pipeline._run_fixed_command

def limited(label, argv, cwd, **kwargs):
    if label == 'remotion-render':
        argv = [*argv, f'--frames={left}-{right-1}']
    return runner(label, argv, cwd, **kwargs)

pipeline._run_fixed_command = limited
if args.reuse_scene:
    shutil.copy2(args.reuse_scene, pipeline.VIDEO_PATH)
    shutil.copy2(args.reuse_scene.parent/'artifacts/render-props.json', pipeline.ARTIFACTS/'render-props.json')
else:
    pipeline.run_step('render-flow-scene', lambda: pipeline.render(compiled, renderer_root=out/'renderer'))
pipeline._run_fixed_command = runner
pipeline.VIDEO_PATH = full_path
original = old/'artifacts/G2_自动生成.mp4'
# Only this scene's picture is replaced. The original complete audio stream is copied unchanged.
filters = (f'[0:v]trim=end_frame={left},setpts=PTS-STARTPTS[a];'
           '[1:v]setpts=PTS-STARTPTS[b];'
           f'[0:v]trim=start_frame={right},setpts=PTS-STARTPTS[c];'
           '[a][b][c]concat=n=3:v=1:a=0,fps=30[v]')
runner('replace-flow-scene', [pipeline.FFMPEG, '-nostdin', '-v', 'error', '-n', '-i', original,
    '-i', out/'corrected-scene.mp4', '-filter_complex', filters, '-map', '[v]', '-map', '0:a:0',
    '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast', '-r', '30', '-pix_fmt', 'yuv420p', '-c:a', 'copy', full_path], ROOT)
alignment = json.loads((old/'artifacts/alignment.json').read_text())
qa = pipeline.run_step('verify-full-video', pipeline.verify_media, compiled, alignment)
qa['replay'] = {'source_attempt': str(old), 'scene_id': 'card_13', 'new_model_calls': 0, 'new_voice_calls': 0,
                'narration_unchanged': True, 'storyboard_unchanged': True}
pipeline.write_json(pipeline.ARTIFACTS/'qa.json', qa)
config = pipeline.package_request_from_intent()
if config:
    pipeline.run_step('package', pipeline.run_package_stage, config)
assert not (saved.get('background_music') or {}).get('enabled')
from g2.brand_stage import run as brand
pipeline.run_step('brand', lambda: brand(pipeline, saved))
final = pipeline.ARTIFACTS/'brand/video.mp4'
checks = {}
for name, path in [('content',full_path), ('final',final)]:
    result = subprocess.run([str(pipeline.FFMPEG), '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
        '-f','null','-'], text=True, capture_output=True)
    assert result.returncode == 0 and not result.stderr
    checks[name] = {'path': str(path), 'decode': 'PASS'}
for at in [166, 176.7, 181]:
    for label,path in [('before',original),('after',full_path)]:
        subprocess.run([str(pipeline.FFMPEG),'-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-update','1',
            str(out/f'{label}-{at}.png')],check=True)
audio = pipeline.ASSETS/'communications_noise.wav'
assert hashlib.sha256(audio.read_bytes()).digest() == hashlib.sha256((old/'fixtures/assets/communications_noise.wav').read_bytes()).digest()
pipeline.write_json(out/'receipt.json', {'source_job':'72e5fba3-b873-4f8b-8151-b4f65f22acf4',
    'source_attempt':str(old),'scene_id':'card_13','replaced_frames':[left,right], 'full_video':str(final),
    'new_model_calls':0,'new_voice_calls':0,'narration_unchanged':True,'storyboard_unchanged':True,
    'decode':checks,'qa':qa})
print(json.dumps({'output':str(out),'video':str(final),'status':'PASS'}),flush=True)
