"""Replay saved production scenes through shared compilation, with original speech."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['models-1', 'business-1'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    index_path = ROOT.parent/'AI_research/Base_CodingCLi/state/validation/ai-matrix-productization-20261027/validation-index.json'
    index = json.loads(index_path.read_text())
    media = index['media'][args.case]
    old = Path(media['engine_data'])/'jobs'/media['engine_job_id']/'attempts/1/g2-run'
    saved = json.loads((old.parents[2]/'input/input.json').read_text())
    original = json.loads((old/'fixtures/video-ir-v0.2.json').read_text())
    plan = json.loads((old/'artifacts/explanation-plan.json').read_text())
    ids = ['card_3_p1', 'card_3_p2'] if args.case == 'models-1' else ['card_5_p2']
    interval = [s for s in original['scenes'] if s['id'] in ids]
    left = round(interval[0]['start_ms']*.03)
    right = round((interval[-1]['start_ms']+interval[-1]['duration_ms'])*.03)
    os.environ['AUTOVIDEO_G2_RUN_DIR'] = str(out)
    os.environ['AUTOVIDEO_G2_RUN_ID'] = 'focus-sync-replay-'+args.case
    from g2 import run_g2 as pipeline, information_motion, shot_director, scene_assembly
    from g1.video_ir import validate_video_ir
    from g2.caption_alignment import verify_plan
    pipeline.ARTIFACTS.mkdir()
    pipeline.LOGS.mkdir()
    shutil.copytree(old/'fixtures', pipeline.FIXTURE_ROOT)
    pipeline.PACKAGING_INPUT = saved
    scenes = [{'id': s['id'], 'component': s['component'], 'props': copy.deepcopy(s['props']),
               'headline': s['purpose'], 'narration_segment_ids': s['narration_segment_ids']} for s in original['scenes']]
    alignment = {'rows': copy.deepcopy(original['narration']['segments'])}
    board = information_motion.compile_storyboard({'scenes': scenes}, alignment, pipeline)
    shot_director.attach(board, saved, pipeline, alignment, plan['pages'])
    scene_assembly.connect(board, plan['groups'], saved)
    ir = copy.deepcopy(original)
    for scene, compiled_scene in zip(ir['scenes'], board['scenes']):
        scene.update(compiled_scene)
    captions = json.loads((old/'artifacts/caption-alignment.json').read_text())
    verify_plan(ir, captions)
    changes = information_motion.align_visual_beats(ir, captions,
        json.loads((pipeline.ARTIFACTS/'information-timing.json').read_text()))
    save(pipeline.ARTIFACTS/'visual-cue-alignment.json', changes)
    save(pipeline.IR_PATH, ir)
    compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
    save(pipeline.ARTIFACTS/'compiled-timeline.json', compiled)
    for name in ['caption-alignment.json', 'captions.srt']:
        shutil.copyfile(old/'artifacts'/name, pipeline.ARTIFACTS/name)
    run = pipeline._run_fixed_command

    def render_range(label, argv, cwd, **kwargs):
        if label == 'remotion-render':
            argv = [*argv, f'--frames={left}-{right-1}']
        return run(label, argv, cwd, **kwargs)

    pipeline._run_fixed_command = render_range
    pipeline.VIDEO_PATH = out/'after.mp4'
    pipeline.render(compiled, renderer_root=out/'renderer')
    before = Path(index['review'][args.case]['content'])
    subprocess.run([str(pipeline.FFMPEG), '-nostdin', '-v', 'error', '-n', '-ss', str(left/30),
        '-i', str(before), '-t', str((right-left)/30), '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast',
        '-c:a', 'aac', str(out/'before.mp4')], check=True)
    checks = {}
    for name in ['before.mp4', 'after.mp4']:
        result = subprocess.run([str(pipeline.FFMPEG), '-nostdin', '-v', 'error', '-xerror',
            '-i', str(out/name), '-f', 'null', '-'], capture_output=True, text=True)
        checks[name] = {'decode_exit_code': result.returncode, 'stderr': result.stderr}
        assert result.returncode == 0 and not result.stderr, checks[name]
    audio = next(a for a in ir['assets'] if a['id'] == ir['narration']['audio_asset_id'])
    digest = hashlib.sha256((pipeline.FIXTURE_ROOT/audio['path']).read_bytes()).hexdigest()
    assert digest == audio['sha256']
    assert ir['narration'] == original['narration']
    probes = [45.789, 58.007] if args.case == 'models-1' else [123.3, 125.857, 130.029]
    for at in probes:
        for name in ['before', 'after']:
            subprocess.run([str(pipeline.FFMPEG), '-v', 'error', '-ss', str(at-left/30), '-i', str(out/(name+'.mp4')),
                '-frames:v', '1', '-update', '1', str(out/f'{name}-{at}.png')], check=True)
    save(out/'receipt.json', {'case': args.case, 'source_attempt': str(old), 'scene_ids': ids,
        'original_start_frame': left, 'original_end_frame': right, 'video_speed': saved['video_speed'],
        'narration_unchanged': True, 'audio_sha256': digest, 'decode': checks,
        'new_model_calls': 0, 'manual_scene_or_beat_edits': 0})
    print(json.dumps({'case': args.case, 'output': str(out), 'frames': right-left}), flush=True)


if __name__ == '__main__':
    main()
