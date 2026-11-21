"""Exercise the normal planner on real narration, without changing a saved job."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-20261111'
OUT = BASE / 'planned-02'
os.environ['AUTOVIDEO_G2_RUN_DIR'] = str(OUT)
os.environ['AUTOVIDEO_G2_DURATION_MODE'] = 'content'
sys.path.insert(0, str(ROOT))
from g2 import explanation, run_g2 as pipeline
from g2.information_motion import align_visual_beats
from g1.video_ir import validate_video_ir

GUIDANCE = ('先确定本页观众需要记住的一件事，再选择最简单的表达。'
            '画面承担重点与关系，旁白承担展开解释；不要逐句复制旁白作为条目。'
            '对比两侧优先各保留一到两条真正不同的内容，共同前提放到标题或上下文，不在两侧重复。'
            '数值变化的核心结果要成为视觉主体，不能埋在计算前提和解释列表中；'
            '必要的对象、单位和适用条件仍要清楚。不同选择的优缺点继续用对比，不为了动画强行改成数值或流程。')

original_messages = explanation.messages
def candidate_messages(context, correction=None):
    messages = original_messages(context, correction)
    messages[0]['content'] += GUIDANCE
    return messages

explanation.messages = candidate_messages
records = json.loads((BASE / 'baseline.json').read_text())
for name, scene_id in [('news', 'card_3'), ('comparison', 'card_6'), ('tutorial', 'card_13')]:
    output = OUT / f'{name}-{scene_id}'
    output.mkdir(parents=True, exist_ok=False)
    pipeline.ARTIFACTS = output
    pipeline.CALLS_DIR = output / 'llm_calls'
    pipeline.LOGS = output / 'logs'
    record = next(row for row in records if row['name'] == name)
    source = Path(record['props'])
    fixtures = source.parents[1] / 'fixtures'
    props = json.loads(source.read_text())
    raw = json.loads((fixtures / 'video-ir-v0.2.json').read_text())
    old = next(scene for scene in raw['scenes'] if scene['id'] == scene_id)
    revised = explanation.replan_scene(pipeline, copy.deepcopy(old), {'title': record['title']}, raw, '')
    raw['scenes'] = [revised if s['id'] == scene_id else s for s in raw['scenes']]
    captions = json.loads((source.parent / 'caption-alignment.json').read_text())
    timing = json.loads((output / 'information-timing.json').read_text())
    changes = align_visual_beats(raw, captions, timing)
    compiled = validate_video_ir(raw, fixtures)
    props['ir'] = compiled
    pipeline.write_json(output / 'props.json', props)
    pipeline.write_json(output / 'ir.json', raw)
    pipeline.write_json(output / 'evidence.json', {'source': str(source), 'fixtures': str(fixtures),
        'video': record['video'], 'scene': scene_id, 'word_alignment': changes,
        'narration_unchanged': raw['narration'] == json.loads((fixtures / 'video-ir-v0.2.json').read_text())['narration']})
    print(name, scene_id, revised['component'], json.dumps(revised['props'], ensure_ascii=False), flush=True)
