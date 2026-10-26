"""Recompile the preserved model plan and render current camera polish with the original voice."""
import argparse
import copy
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
source, out = args.source.resolve(), args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
location = json.loads((source/'location.json').read_text())
job = Path(location['engine_data'])/'jobs'/location['job_id']
original = max((job/'attempts').iterdir(), key=lambda p: int(p.name))/'g2-run'
run = out/'render-run'
shutil.copytree(original/'fixtures', run/'fixtures')
(run/'artifacts').mkdir()
for path in (original/'artifacts').iterdir():
    if path.is_file() and path.suffix in {'.json', '.txt', '.srt'}:
        shutil.copyfile(path, run/'artifacts'/path.name)
shutil.copytree(job/'input', run/'input')
saved = json.loads((run/'input/input.json').read_text())
os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(run), AUTOVIDEO_G2_RUN_ID=out.name,
    AUTOVIDEO_G2_SOURCE_FILE=str(run/'input/source.txt'), AUTOVIDEO_G2_INTENT_FILE=str(run/'input/input.json'),
    AUTOVIDEO_G2_DURATION_MODE='content', AUTOVIDEO_G2_VIDEO_FORM=saved['video_form'],
    AUTOVIDEO_G2_USE_DRAW_THINGS='0', AUTOVIDEO_G2_VOICE_ID=saved['voice_id'])
from g2 import run_g2 as pipeline, explanation
from g1.video_ir import validate_video_ir

ir = json.loads(pipeline.IR_PATH.read_text())
before = copy.deepcopy(ir)
alignment = json.loads((original/'artifacts/alignment.json').read_text())
alignment['rows'] = alignment['segments']
plan = json.loads((original/'artifacts/explanation-plan.json').read_text())
assets = [a for a in ir['assets'] if a['type'] == 'image']
board = explanation.compile_storyboard(pipeline, plan, alignment, saved, assets)
for scene, revised in zip(ir['scenes'], board['scenes']):
    assert scene['id'] == revised['id']
    unchanged = lambda props: {k: v for k, v in props.items() if k not in {'shot_plan', 'layout'}}
    assert unchanged(scene['props']) == unchanged(revised['props'])
    scene['props']['shot_plan'] = revised['props']['shot_plan']
    scene['beats'] = revised['beats']
assert ir['narration'] == before['narration']
assert ir['assets'] == before['assets']
pipeline.write_json(pipeline.IR_PATH, ir)
compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
pipeline.write_json(pipeline.ARTIFACTS/'compiled-timeline.json', compiled)
pipeline.render(compiled, renderer_root=run/'renderer')
qa = pipeline.verify_media(compiled, alignment)
qa.update(real_llm=False, real_tts=False, reused_real_model=True, reused_real_tts=True,
          new_model_calls=0, new_tts_calls=0, original_narration_and_audio=True)
pipeline.write_json(pipeline.ARTIFACTS/'qa.json', qa)
pipeline.run_package_stage(pipeline.package_request_from_intent())
pipeline.write_manifest(renderer_root=run/'renderer')
shutil.copyfile(pipeline.VIDEO_PATH, out/'content.mp4')
for name in ('video.mp4', 'package-qa.json'):
    shutil.copyfile(pipeline.ARTIFACTS/'package'/name, out/name)
shutil.copyfile(pipeline.ARTIFACTS/'qa.json', out/'qa.json')
shutil.copyfile(pipeline.IR_PATH, out/'video-ir.json')
(out/'location.json').write_text(json.dumps({**location, 'render_run': str(run)}, indent=2))
result = {'original_job_id': location['job_id'], 'render_run': str(run),
          'new_model_calls': 0, 'new_tts_calls': 0, 'original_narration_and_audio': True,
          'components': [s['component'] for s in ir['scenes']], 'media': qa['video']}
(out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False), flush=True)
