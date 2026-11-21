"""Replay a saved mechanism chapter with its existing diagram promoted to the main picture."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5] / 'autovideo'
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    old = ROOT / 'g3/data/acceptance-illustrated-explainer-20261109/jobs/ea20813e-1908-433f-a892-251234c2d941/attempts/1/g2-run'
    original = json.loads((old / 'fixtures/video-ir-v0.2.json').read_text())
    ir = copy.deepcopy(original)
    selected = [s for s in ir['scenes'] if s['props']['chapter_index'] == 1]
    for scene in selected:
        scene['props']['layout'] = 'diagram'
    left = round(selected[0]['start_ms'] * .03)
    right = round((selected[-1]['start_ms'] + selected[-1]['duration_ms']) * .03)
    os.environ['AUTOVIDEO_G2_RUN_DIR'] = str(out)
    os.environ['AUTOVIDEO_G2_RUN_ID'] = 'mechanism-primary-replay'
    from g2 import run_g2 as pipeline
    from g1.video_ir import validate_video_ir
    pipeline.ARTIFACTS.mkdir()
    pipeline.LOGS.mkdir()
    shutil.copytree(old / 'fixtures', pipeline.FIXTURE_ROOT)
    pipeline.PACKAGING_INPUT = json.loads((old.parents[2] / 'input/input.json').read_text())
    pipeline.write_json(pipeline.IR_PATH, ir)
    compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
    pipeline.write_json(pipeline.ARTIFACTS / 'compiled-timeline.json', compiled)
    for name in ('caption-alignment.json', 'captions.srt'):
        if (old / 'artifacts' / name).exists():
            shutil.copyfile(old / 'artifacts' / name, pipeline.ARTIFACTS / name)
    original_run = pipeline._run_fixed_command
    def render_range(label, argv, cwd, **kwargs):
        if label == 'remotion-render':
            argv = [*argv, f'--frames={left}-{right-1}']
        return original_run(label, argv, cwd, **kwargs)
    pipeline._run_fixed_command = render_range
    pipeline.VIDEO_PATH = out / 'after.mp4'
    pipeline.render(compiled, renderer_root=out / 'renderer')
    subprocess.run([str(pipeline.FFMPEG), '-v', 'error', '-n', '-ss', str(left/30), '-i',
        str(old/'artifacts/G2_自动生成.mp4'), '-t', str((right-left)/30), '-c:v', 'libx264',
        '-crf', '18', '-preset', 'veryfast', '-c:a', 'aac', str(out/'before.mp4')], check=True)
    decode = {}
    for name in ('before', 'after'):
        result = subprocess.run([str(pipeline.FFMPEG), '-v', 'error', '-xerror', '-i', str(out/(name+'.mp4')),
                                 '-f', 'null', '-'], capture_output=True, text=True)
        decode[name] = {'exit_code': result.returncode, 'stderr': result.stderr}
        assert result.returncode == 0 and not result.stderr
        for offset in (5, 15, 25):
            if offset < (right-left)/30:
                subprocess.run([str(pipeline.FFMPEG), '-v', 'error', '-ss', str(offset), '-i', str(out/(name+'.mp4')),
                    '-frames:v', '1', '-update', '1', str(out/f'{name}-{offset}.png')], check=True)
    audio = next(a for a in ir['assets'] if a['id'] == ir['narration']['audio_asset_id'])
    assert hashlib.sha256((pipeline.FIXTURE_ROOT/audio['path']).read_bytes()).hexdigest() == audio['sha256']
    assert ir['narration'] == original['narration']
    pipeline.write_json(out/'receipt.json', {'source': str(old), 'chapter': 2, 'seconds': (right-left)/30,
        'change': 'layout=diagram only; original diagram, image and narration reused', 'manual_trial': True,
        'new_model_calls': 0, 'narration_unchanged': True, 'decode': decode})
    print(json.dumps({'output': str(out), 'seconds': (right-left)/30}), flush=True)


if __name__ == '__main__':
    main()
