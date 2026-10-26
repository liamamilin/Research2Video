"""Rejoin preserved model-directed clips through the current production assembler."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

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
shutil.copytree(job/'input', run/'input')
(run/'artifacts').mkdir()
for path in (original/'artifacts').iterdir():
    if path.is_file() and path.suffix in {'.json', '.txt', '.srt'}:
        shutil.copyfile(path, run/'artifacts'/path.name)
saved = json.loads((run/'input/input.json').read_text())
os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(run), AUTOVIDEO_G2_RUN_ID=out.name,
    AUTOVIDEO_G2_SOURCE_FILE=str(run/'input/source.txt'), AUTOVIDEO_G2_INTENT_FILE=str(run/'input/input.json'),
    AUTOVIDEO_G2_DURATION_MODE='content', AUTOVIDEO_G2_VIDEO_FORM=saved['video_form'],
    AUTOVIDEO_G2_USE_DRAW_THINGS='0', AUTOVIDEO_G2_VOICE_ID=saved['voice_id'])
from g2 import run_g2 as pipeline, hybrid_cards

pipeline.package_request_from_intent()
ir = json.loads(pipeline.IR_PATH.read_text())
compiled = json.loads((pipeline.ARTIFACTS/'compiled-timeline.json').read_text())
plan = json.loads((original/'artifacts/hybrid-card-plan.json').read_text())
blocks = hybrid_cards.blocks_for(compiled['scenes'])
assert [(b['start_frame'], b['end_frame'], b['scene_ids']) for b in blocks] == [
    (b['start_frame'], b['end_frame'], b['scene_ids']) for b in plan['blocks']]
clips = [{'path': original/'artifacts/hybrid-cards'/f'clip-{i:03d}.mp4'} for i in range(len(blocks))]
root = pipeline.ARTIFACTS/'hybrid-cards'
root.mkdir()
started = time.monotonic()
encoder, failures = hybrid_cards.join_clips(pipeline, compiled, ir, clips, root)
execution = json.loads((pipeline.ARTIFACTS/'production-execution.json').read_text())
execution.update(encoder=encoder, fallbacks=failures, normalized_track_timescale=90000,
    assembly_seconds=round(time.monotonic()-started, 3),
    render_seconds=round(sum(b['render_seconds'] for b in plan['blocks'])+time.monotonic()-started, 3))
pipeline.write_json(pipeline.ARTIFACTS/'production-execution.json', execution)
qa = pipeline.verify_media(compiled, {})
qa.update(reused_real_model=True, reused_real_tts=True, new_model_calls=0, new_tts_calls=0,
          original_narration_and_scenes=True)
pipeline.write_json(pipeline.ARTIFACTS/'qa.json', qa)
pipeline.run_package_stage(pipeline.package_request_from_intent())
pipeline.write_manifest(renderer_root=original/'renderer')
shutil.copyfile(pipeline.VIDEO_PATH, out/'content.mp4')
for name in ('video.mp4', 'package-qa.json'):
    shutil.copyfile(pipeline.ARTIFACTS/'package'/name, out/name)
for name in ('qa.json', 'production-execution.json', 'hybrid-card-plan.json', 'explanation-plan.json', 'scene-assembly.json', 'story-direction.json'):
    shutil.copyfile(pipeline.ARTIFACTS/name, out/name)
shutil.copyfile(pipeline.IR_PATH, out/'video-ir.json')
(out/'location.json').write_text(json.dumps({**location, 'render_run': str(run)}, indent=2))
result = {'job_id': location['job_id'], 'render_run': str(run), 'new_model_calls': 0, 'new_tts_calls': 0,
    'original_narration_and_scenes': True, 'execution': execution, 'media': qa['video']}
(out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False), flush=True)
