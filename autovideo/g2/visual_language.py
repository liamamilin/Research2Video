"""Choose visual objects in the existing plan and bind actions to measured speech."""
from __future__ import annotations

import re

from g1.visual_language import COMPONENTS, catalog

GUIDANCE = ('为概念卡、对比卡和流程节点选择能解释对象的统一图标：props.visuals中用target对应line:N、side:left/right或node:id，'
            'icon从visual_vocabulary.objects选择。只有确实有对象时使用图标，抽象判断或文学段落可以省略。'
            '每个target只选一个主要图标；同一行同时提到时间和成本时，也不要重复绑定该行。'
            '有明确动作时填写action：send传递、process处理、complete结果生成、disconnect连接中断；'
            'reveal_phrase填写讲稿首次引出当前对象的原话，对象在开始谈到它时就出现，不等句末才显示。'
            'phrase填写随后强调结论或动作开始处的原话；对象出现与后续强调可以是两个时刻。'
            '静态对象省略action；不添加循环装饰。')


def phrase_at(text: str, phrase: str, cursor: int = 0) -> int:
    """Locate spoken words despite spacing or sentence punctuation in a cue."""
    return phrase_span(text, phrase, cursor)[0]


def phrase_span(text: str, phrase: str, cursor: int = 0) -> tuple[int, int]:
    """Return original transcript boundaries even when punctuation is folded."""
    exact = text.find(phrase, cursor)
    if exact >= 0:
        return exact, exact + len(phrase)

    def folded(value: str, start: int = 0) -> tuple[str, list[int]]:
        chars, positions = [], []
        for i in range(start, len(value)):
            char = value[i]
            decimal = char == '.' and i > 0 and i+1 < len(value) and value[i-1].isdigit() and value[i+1].isdigit()
            if char.isspace() or (char in '，,。.;；:：！？!?、“”‘’\"\'' and not decimal):
                continue
            for letter in char.casefold():
                chars.append(letter)
                positions.append(i)
        return ''.join(chars), positions

    haystack, positions = folded(text, cursor)
    needle, _ = folded(phrase)
    at = haystack.find(needle) if needle else -1
    return (positions[at], positions[at+len(needle)-1]+1) if at >= 0 else (-1, -1)


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


def action_beats(scene: dict, rows: dict, timing: list[dict] | None = None) -> list[dict]:
    """A separate reveal beat moves the focus without shifting the object's first appearance."""
    speech = [rows[sid] for sid in scene['narration_segment_ids']]
    text = ''.join(row['text'] for row in speech)
    beats = []
    for i, item in enumerate(scene['props'].get('visuals', [])):
        separate_focus = item.get('reveal_phrase') and item.get('reveal_phrase') != item.get('phrase')
        if not item.get('phrase') or (item.get('action', 'none') == 'none' and not separate_focus):
            continue
        introduced, introduction_end = phrase_span(text, item['reveal_phrase']) if item.get('reveal_phrase') else (-1, -1)
        # A repeated conclusion belongs to this object's introduction, not an
        # earlier paragraph that happened to use the same words.
        at = phrase_at(text, item['phrase'], introduction_end
                       if introduced >= 0 and separate_focus else 0)
        if at < 0:
            continue
        if introduced >= 0 and at <= introduced:
            continue  # An emphasis cue must not pre-empt the object's introduction.
        for row in speech:
            if at < len(row['text']):
                beats.append({'id': f"{scene['id']}_action_{i}", 'action': 'reveal', 'target_id': item['target'],
                              'trigger': {'segment_id': row['id'], 'boundary': 'start',
                                          'offset_ms': int(at / len(row['text']) * (row['end_ms'] - row['start_ms']))}})
                if timing is not None:
                    timing.append({'scene_id': scene['id'], 'beat_id': beats[-1]['id'],
                                   'target_id': item['target'], 'segment_id': row['id'],
                                   'quote': row['text'][at:at+1], 'character_index': at,
                                   'automatic': True, 'purpose': 'emphasis',
                                   'estimated_start_ms': row['start_ms'] + beats[-1]['trigger']['offset_ms']})
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
        candidates = [visual.get('reveal_phrase', ''), node['label'], visual.get('phrase', '')]
        candidates += words.get(visual.get('icon'), [])
        at = next((position for word in candidates if word
                   if (position := phrase_at(text, word)) >= 0), None)
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


def _place(cue: dict, at: int, speech: list[dict]) -> None:
    for row in speech:
        if at < len(row['text']):
            cue.update(segment_id=row['id'], quote=row['text'][at:at+1], character_index=at)
            return
        at -= len(row['text'])


def _global_position(cue: dict, speech: list[dict]) -> int:
    """Convert a segment-local character offset to the concatenated speech coordinate."""
    offset = 0
    for row in speech:
        if row['id'] == cue['segment_id']:
            return offset + cue.get('character_index', 0)
        offset += len(row['text'])
    return 0


def align_object_cues(scene: dict, cues: list[dict], speech: list[dict]) -> set[str]:
    """Named object phrases can replace the default evenly spaced card reveals; returns the targets so placed."""
    text = ''.join(row['text'] for row in speech)
    placed = set()
    for visual in scene['props'].get('visuals', []):
        phrase = visual.get('reveal_phrase') or visual.get('phrase', '')
        at = text.find(phrase) if phrase else -1
        if at < 0 and phrase:
            at = phrase_at(text, phrase)  # the same words with other spacing or punctuation
        cue = next((c for c in cues if c['target_id'] == visual['target']), None)
        if at < 0 or cue is None:
            continue
        _place(cue, at, speech)
        placed.add(visual['target'])
    return placed


#: The cards whose own words can anchor them: what the viewer reads on the card is usually said aloud.
LABELLED = ('comparison', 'concept_reveal', 'bar_chart', 'big_number')


def target_label(scene: dict, target: str) -> list[str]:
    """What a card target shows, most specific first: a side's label before its "·", a line, a bar's category,
    a big number's value and label."""
    props, component = scene['props'], scene['component']
    kind, _, index = target.partition(':')
    if component == 'comparison' and kind == 'side':
        label = str((props.get('left' if index == 'left' else 'right') or {}).get('label') or '')
        head = label.split('·')[0].strip()
        return [head, label, *(head.split()[:1] if ' ' in head else [])]  # "ChatGPT Plus" is often said "ChatGPT"
    if component == 'concept_reveal' and kind == 'line':
        line = str(props['lines'][int(index)])
        return [line, re.split(r'[、，,：:（(；;—]', line)[0].strip()]
    if component == 'bar_chart' and kind == 'bar':
        return [str(props['categories'][int(index)])]
    if component == 'big_number' and kind == 'line':
        field = {'0': 'value', '1': 'label', '2': 'note'}.get(index, '')
        value = str(props.get(field) or '')
        return [value, re.split(r'[·（(，,]', value)[0].strip()]
    return []


def align_label_cues(scene: dict, cues: list[dict], speech: list[dict], placed: set[str]) -> None:
    """A card nobody gave a spoken phrase reveals where its own label is first said, in card order, instead of
    at an evenly spaced point: the published 同样20美元 showed Claude's side 5.8 s after "Claude" was said (11-10)."""
    if scene['component'] not in LABELLED:
        return
    text = ''.join(row['text'] for row in speech)
    cursor = 0
    for cue in cues:
        target = cue['target_id']
        if target == 'title':
            continue
        if target in placed:
            cursor = max(cursor, _global_position(cue, speech))
            continue
        for n, label in enumerate(target_label(scene, target)):
            if len(label) < (2 if n == 0 else 3):  # a shortened label must still be specific ("结果" is not)
                continue
            at = phrase_at(text, label, cursor)
            if at < 0:
                at = phrase_at(text, label)
            if at >= 0:
                _place(cue, at, speech)
                cursor = max(cursor, at)
                break
