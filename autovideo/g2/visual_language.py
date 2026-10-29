"""Choose visual objects in the existing plan and bind actions to measured speech."""
from __future__ import annotations

import re

from g1.visual_language import COMPONENTS, catalog

GUIDANCE = ('为概念卡、对比卡和流程节点选择能解释对象的统一图标：props.visuals中用target对应line:N、side:left/right或node:id，'
            'icon从visual_vocabulary.objects选择。只有确实有对象时使用图标，抽象判断或文学段落可以省略。'
            '每个target只选一个主要图标；同一行同时提到时间和成本时，也不要重复绑定该行。'
            '有明确动作时填写action：send传递、process处理、complete结果生成、disconnect连接中断；'
            'phrase填写讲稿中动作开始处的短语，使动作跟随旁白。静态对象省略action；不添加循环装饰。')


def object_rows(component: str, props: dict) -> list[tuple[str, str]]:
    if component == 'concept_reveal':
        return [(f'line:{i}', line) for i, line in enumerate(props['lines'])]
    if component == 'comparison':
        return [(f'side:{side}', props[side]['label']) for side in ('left', 'right')]
    if component == 'flow_diagram':
        return [(f"node:{n['id']}", n['label']) for n in props['nodes']]
    return []


def enrich(component: str, props: dict) -> dict:
    """Older plans get literal object icons; explicit planner choices take precedence."""
    if component not in COMPONENTS:
        return props
    if isinstance(props.get('visuals'), list):
        # One object slot has one icon. Repeated decorative choices must not
        # waste a model call or fail a video; keep the first complete choice.
        # Unknown references/objects still reach the ordinary schema checks.
        try:
            targets = {target for target, _ in object_rows(component, props)}
        except (KeyError, TypeError):
            return props  # Leave malformed component props to the schema.
        seen, selected = set(), []
        for item in props['visuals']:
            target = item.get('target') if isinstance(item, dict) else None
            if isinstance(target, str) and target in targets:
                if target in seen:
                    continue
                seen.add(target)
            selected.append(item)
        return {**props, 'visuals': selected} if len(selected) != len(props['visuals']) else props
    if 'visuals' in props:
        return props
    visuals = []
    for target, text in object_rows(component, props):
        # Prefer a specific compound (文件夹) to a generic substring (文件).
        matches = [(len(word), -i, row['id']) for i, row in enumerate(catalog()['objects'])
                   for word in row['keywords'] if re.search(re.escape(word), text, re.IGNORECASE)]
        if not matches:
            continue
        item = {'target': target, 'icon': max(matches)[2]}
        for action, pattern in [('disconnect', r'连接中断|断开连接|连接断开'),
                                ('complete', r'结果生成|生成结果|处理完成|返回结果'),
                                ('send', r'发送请求|发出请求|上传文件|传递文件'),
                                ('process', r'正在处理|处理文件|解析文件|检索数据库')]:
            if match := re.search(pattern, text):
                item.update(action=action, phrase=match[0])
                break
        visuals.append(item)
    return {**props, 'visuals': visuals} if visuals else props


def action_beats(scene: dict, rows: dict) -> list[dict]:
    """A separate reveal beat moves the focus without shifting the object's first appearance."""
    speech = [rows[sid] for sid in scene['narration_segment_ids']]
    text = ''.join(row['text'] for row in speech)
    beats = []
    for i, item in enumerate(scene['props'].get('visuals', [])):
        if item.get('action', 'none') == 'none' or not item.get('phrase'):
            continue
        at = text.find(item['phrase'])
        if at < 0:
            continue
        for row in speech:
            if at < len(row['text']):
                beats.append({'id': f"{scene['id']}_action_{i}", 'action': 'reveal', 'target_id': item['target'],
                              'trigger': {'segment_id': row['id'], 'boundary': 'start',
                                          'offset_ms': int(at / len(row['text']) * (row['end_ms'] - row['start_ms']))}})
                break
            at -= len(row['text'])
    return beats


def flow_cues(scene: dict, rows: dict) -> list[dict]:
    """Reveal objects at their spoken names and send links after both endpoints appear."""
    props = scene['props']
    speech = [rows[sid] for sid in scene['narration_segment_ids']]
    text = ''.join(row['text'] for row in speech)
    positions = {}
    selected = {v['target']: v for v in props.get('visuals', [])}
    words = {v['id']: v['keywords'] for v in catalog()['objects']}
    for i, node in enumerate(props['nodes']):
        visual = selected.get(f"node:{node['id']}", {})
        candidates = [node['label'], visual.get('phrase', '')]
        candidates += words.get(visual.get('icon'), [])
        at = next((text.index(word) for word in candidates if word and word in text), None)
        positions[f"node:{node['id']}"] = at if at is not None else len(text)*i//len(props['nodes'])
    for edge in props['edges']:
        positions[f"edge:{edge['id']}"] = max(positions[f"node:{edge['from']}"], positions[f"node:{edge['to']}"])
    result = []
    for target, position in [('scene', 0), *sorted(positions.items(), key=lambda p: p[1])]:
        for row in speech:
            if position < len(row['text']):
                result.append({'target_id': target, 'segment_id': row['id'],
                               'quote': row['text'][position:position+1], 'character_index': position})
                break
            position -= len(row['text'])
    return result


def align_object_cues(scene: dict, cues: list[dict], speech: list[dict]) -> None:
    """Named object phrases can replace the default evenly spaced card reveals."""
    text = ''.join(row['text'] for row in speech)
    for visual in scene['props'].get('visuals', []):
        phrase = visual.get('phrase', '')
        at = text.find(phrase) if phrase else -1
        cue = next((c for c in cues if c['target_id'] == visual['target']), None)
        if at < 0 or cue is None:
            continue
        for row in speech:
            if at < len(row['text']):
                cue.update(segment_id=row['id'], quote=row['text'][at:at+1], character_index=at)
                break
            at -= len(row['text'])
