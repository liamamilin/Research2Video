"""Render a saved production screenshot scene with its original picture and speech."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    location = json.loads((ROOT / 'g3/acceptance/hybrid-cards-v5-20261026/release/location.json').read_text())
    old = Path(location['engine_data']) / 'jobs' / location['job_id'] / 'attempts/1/g2-run'
    original = json.loads((old / 'fixtures/video-ir-v0.2.json').read_text())
    planning = json.loads((old / 'artifacts/explanation-plan.json').read_text())
    saved = json.loads((old.parents[2] / 'input/input.json').read_text())
    from g1.video_ir import validate_video_ir
    from g2 import information_motion, shot_director
    from g2.caption_alignment import verify_plan

    artifacts = out / 'artifacts'
    artifacts.mkdir()
    pipeline = SimpleNamespace(ARTIFACTS=artifacts, write_json=save)
    scenes = [{'id': s['id'], 'component': s['component'], 'props': copy.deepcopy(s['props']),
               'headline': s['purpose'], 'narration_segment_ids': s['narration_segment_ids']}
              for s in original['scenes']]
    alignment = {'rows': copy.deepcopy(original['narration']['segments'])}
    board = information_motion.compile_storyboard({'scenes': scenes}, alignment, pipeline)
    shot_director.attach(board, saved, pipeline, alignment, planning['pages'])
    ir = copy.deepcopy(original)
    for scene, planned in zip(ir['scenes'], board['scenes']):
        scene.update(planned)
    captions = json.loads((old / 'artifacts/caption-alignment.json').read_text())
    verify_plan(ir, captions)
    changes = information_motion.align_visual_beats(ir, captions,
        json.loads((artifacts / 'information-timing.json').read_text()))
    compiled = validate_video_ir(ir, old / 'fixtures')
    selected = next(s for s in compiled['scenes'] if s['id'] == 'card_3')
    start, end = selected['start_frame'], selected['end_frame']
    props = json.loads((old / 'artifacts/render-props.json').read_text())
    props.pop('fastCardBackgrounds', None)
    props['ir'].update(compiled)
    renderer = out / 'renderer'
    shutil.copytree(ROOT / 'g1/renderer/src', renderer / 'src')
    shutil.copytree(old / 'fixtures/assets', renderer / 'public/assets')
    deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
    (renderer / 'node_modules').symlink_to(deps, target_is_directory=True)
    save(out / 'props.json', props)
    save(out / 'compiled-timeline.json', compiled)
    save(out / 'visual-cue-alignment.json', changes)
    save(out / 'video-ir.json', ir)
    command = ['node', str(deps / '@remotion/cli/remotion-cli.js'), 'render', 'src/index.ts',
        'G1CommunicationNoise', str(out / 'video.mp4'), '--props=' + str(out / 'props.json'),
        f'--frames={start}-{end-1}', '--codec=h264', '--crf=18', '--concurrency=2', '--log=error',
        '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    with (out / 'render.log').open('w') as log:
        subprocess.run(command, cwd=renderer, stdout=log, stderr=subprocess.STDOUT, check=True)
    audio = next(a for a in ir['assets'] if a['id'] == ir['narration']['audio_asset_id'])
    save(out / 'receipt.json', {'source_attempt': str(old), 'scene_id': selected['id'],
        'start_frame': start, 'end_frame': end, 'frames': end-start,
        'narration_unchanged': ir['narration'] == original['narration'],
        'audio_sha256': hashlib.sha256((old / 'fixtures' / audio['path']).read_bytes()).hexdigest(),
        'new_model_calls': 0, 'manual_scene_or_beat_edits': 0,
        'renderer_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (renderer / 'src').iterdir() if p.is_file()}})
    print(json.dumps({'output': str(out), 'frames': end-start}), flush=True)


if __name__ == '__main__':
    main()
