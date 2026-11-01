"""Replay a saved real matrix plan through the shared compiler and renderer.

No model or TTS call: the source attempt, narration and audio remain untouched.
"""
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
    parser.add_argument('--source-attempt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scene')
    args = parser.parse_args()
    old, out = args.source_attempt.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    artifacts = out / 'artifacts'
    artifacts.mkdir()
    original = json.loads((old / 'fixtures/video-ir-v0.2.json').read_text())
    plan = json.loads((old / 'artifacts/explanation-plan.json').read_text())
    saved = json.loads((old.parents[2] / 'input/input.json').read_text())
    from g1.video_ir import validate_video_ir
    from g2 import explanation
    from g2.caption_alignment import verify_plan
    from g2.information_motion import align_visual_beats

    def command(label, argv, cwd, **kwargs):
        return subprocess.check_output([str(v) for v in argv], cwd=cwd, text=True)

    pipeline = SimpleNamespace(ARTIFACTS=artifacts, write_json=save, PROJECT=ROOT,
                               NODE='node', _run_fixed_command=command)
    for page in plan['pages']:
        for name in ('shot_plan', 'scene_flow', 'story'):
            page['props'].pop(name, None)
    explanation.validate({'pages': plan['pages']}, {'title': original['video']['title'],
                                                  'groups': plan['groups']})
    board = explanation.compile_storyboard(pipeline, plan,
        {'rows': original['narration']['segments']}, saved, [])
    ir = copy.deepcopy(original)
    assert [s['id'] for s in ir['scenes']] == [s['id'] for s in board['scenes']]
    for scene, planned in zip(ir['scenes'], board['scenes']):
        scene.update(planned)
    captions = json.loads((old / 'artifacts/caption-alignment.json').read_text())
    verify_plan(ir, captions)
    changes = align_visual_beats(ir, captions,
        json.loads((artifacts / 'information-timing.json').read_text()))
    save(artifacts / 'visual-cue-alignment.json', {'cues': changes})
    compiled = validate_video_ir(ir, old / 'fixtures')
    props = json.loads((old / 'artifacts/render-props.json').read_text())
    assert 'fastCardBackgrounds' not in props, 'This replay preserves standard production layout'
    props['ir'].update(compiled)
    renderer = out / 'renderer'
    shutil.copytree(ROOT / 'g1/renderer/src', renderer / 'src')
    shutil.copytree(old / 'fixtures/assets', renderer / 'public/assets')
    deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
    (renderer / 'node_modules').symlink_to(deps, target_is_directory=True)
    save(out / 'props.json', props)
    save(out / 'compiled-timeline.json', compiled)
    save(out / 'video-ir.json', ir)
    argv = ['node', str(deps / '@remotion/cli/remotion-cli.js'), 'render', 'src/index.ts',
        'G1CommunicationNoise', str(out / 'video.mp4'), '--props=' + str(out / 'props.json'),
        '--codec=h264', '--crf=18', '--concurrency=2', '--log=error',
        '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    if args.scene:
        scene = next(s for s in compiled['scenes'] if s['id'] == args.scene)
        argv.append(f"--frames={scene['start_frame']}-{scene['end_frame']-1}")
    with (out / 'render.log').open('w') as log:
        subprocess.run(argv, cwd=renderer, stdout=log, stderr=subprocess.STDOUT, check=True)
    audio = next(a for a in ir['assets'] if a['id'] == ir['narration']['audio_asset_id'])
    save(out / 'receipt.json', {'source_attempt': str(old), 'scene_id': args.scene,
        'narration_unchanged': ir['narration'] == original['narration'],
        'audio_sha256': hashlib.sha256((old / 'fixtures' / audio['path']).read_bytes()).hexdigest(),
        'new_model_calls': 0, 'new_tts_calls': 0, 'manual_scene_or_beat_edits': 0,
        'source_plan_sha256': hashlib.sha256((old / 'artifacts/explanation-plan.json').read_bytes()).hexdigest(),
        'renderer_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                            for f in (renderer / 'src').glob('*') if f.is_file()}})
    print(json.dumps({'output': str(out), 'scene_id': args.scene}), flush=True)


if __name__ == '__main__':
    main()
