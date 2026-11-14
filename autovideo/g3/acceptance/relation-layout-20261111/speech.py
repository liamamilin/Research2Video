"""Authored new fork/merge: real local voice, word alignment and trusted renderer."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ['AUTOVIDEO_G2_RUN_DIR'] = str(out)
os.environ['AUTOVIDEO_G2_VOICE_ID'] = 'voxcpm_anchor'
os.environ['AUTOVIDEO_G2_DURATION_MODE'] = 'content'
from g2 import run_g2 as engine, caption_alignment, information_motion, process_diagram, shot_director, flow_layouts

for path in [engine.ARTIFACTS, engine.LOGS, engine.ASSETS]:
    path.mkdir(parents=True, exist_ok=True)
labels = [
    '汇集山地监测站当天采集的降雨与水位观察记录',
    '将降雨记录分配给不同流域的水量变化分析任务',
    '把水位记录交给沿线设备检查与变化趋势观察任务',
    '分别整理各流域出现的变化以及对应的时间范围',
    '保留设备检查结果并标出需要继续观察的河段',
    '合并两条分支的观察结果形成完整的当天工作简报',
]
plan = {'segments': [{'id': f'n{i}', 'text': text + '。'} for i, text in enumerate(labels)]}
engine.write_json(engine.ARTIFACTS / 'authored-narration.json', plan)
alignment = engine.synthesize_and_align(plan)
rows = {r['id']: r for r in alignment['rows']}
pairs = [(0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 5), (4, 2)]
props = {'title': '分开观察，再汇总到同一份简报',
         'takeaway': '编号对应同一对象，箭头保留分支、汇聚和回传关系',
         'nodes': [{'id': f'n{i}', 'label': text} for i, text in enumerate(labels)],
         'edges': [{'id': f'e{i}', 'from': f'n{a}', 'to': f'n{b}',
                    'label': '继续观察' if i == 6 else '汇总' if b == 5 else '分配',
                    'relation': 'flow'} for i, (a, b) in enumerate(pairs)], 'paths': []}
request = process_diagram.layout_request({'scenes': [{'id': 'river', **props}]})
request_path = engine.ARTIFACTS / 'layout-request.json'
engine.write_json(request_path, request)
layout = json.loads(engine._run_fixed_command('whole-layout', [engine.NODE, process_diagram.LAYOUT_SCRIPT, request_path], ROOT, timeout=60))
props['layout'] = layout['scenes'][0]['layout']
beats = [{'id': f'b{i}', 'action': 'reveal', 'target_id': f'node:n{i}',
          'trigger': {'segment_id': f'n{i}', 'boundary': 'start', 'offset_ms': 0}} for i in range(len(labels))]
for target in ['scene', 'title']:
    beats.insert(0, {'id': 'surface_' + target, 'action': 'reveal', 'target_id': target,
                     'trigger': {'segment_id': 'n0', 'boundary': 'start', 'offset_ms': 0}})
scene = {'id': 'river', 'component': 'flow_diagram', 'purpose': '长标签分支、汇聚与回传同步',
         'visual_intent': {}, 'props': props, 'narration_segment_ids': list(rows), 'beats': beats}
audio = engine.ASSETS / 'communications_noise.wav'
ir = {'schema_version': '0.2', 'video': {'title': props['title'], 'width': 1920, 'height': 1080,
      'fps': 30, 'duration_ms': alignment['duration_ms']},
      'assets': [{'id': 'audio', 'path': 'assets/communications_noise.wav', 'sha256': hashlib.sha256(audio.read_bytes()).hexdigest()}],
      'narration': {'audio_asset_id': 'audio', 'segments': [{k: r[k] for k in ['id', 'text', 'start_ms', 'end_ms']} for r in rows.values()]},
      'scenes': [scene], 'presentation': {'preset': 'explanation'}}
engine.write_json(engine.IR_PATH, ir)
captions = caption_alignment.prepare(engine, ir)
cues = [{'scene_id': 'river', 'beat_id': f'b{i}', 'segment_id': f'n{i}', 'character_index': 0, 'quote': text} for i, text in enumerate(labels)]
changes = information_motion.align_visual_beats(ir, captions, {'cues': cues})
scene.update(start_frame=0, end_frame=alignment['total_frames'])
for i, beat in enumerate(beats):
    beat.update(order=i, frame=round((rows[beat['trigger']['segment_id']]['start_ms'] + beat['trigger']['offset_ms']) * .03))
props['shot_plan'] = shot_director.camera_plan(scene, 'explain')
flow_layouts.attach(ir, engine)
engine.write_json(engine.ARTIFACTS / 'timing-evidence.json', {'cues': cues, 'changes': changes, 'beat_frames': beats})
compiled = {**ir, 'total_frames': alignment['total_frames']}
engine.render(compiled, renderer_root=out / 'renderer')
engine._run_fixed_command('decode', [engine.FFMPEG, '-v', 'error', '-xerror', '-i', engine.VIDEO_PATH, '-f', 'null', '-'], ROOT, timeout=120)
print(json.dumps({'video': str(engine.VIDEO_PATH), 'seconds': alignment['duration_seconds'], 'changes': changes}, ensure_ascii=False))
