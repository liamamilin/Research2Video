"""Organize narrative roles and object continuity in the existing plan."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from g1.story_language import COLORS, object_labels

GUIDANCE = ('同时为每页填写story_role和importance：提出本期问题用question，展开说明用explain，'
    '并列比较用compare，给出结果用result，简短承接用bridge，回顾前面重点用recap。'
    'importance为key/normal/support，核心比较和结论优先key。不要把每页都标为key或recap。'
    'entities列出本页主要产品、方案或指标，每项name沿用讲稿和established_entities中的统一名称，'
    'targets可指向本页node:id、side:left/right、series:N、category:N、point:N、bar:N、line:N。'
    '网页或图片说明即使选了concept_reveal，也用image:N或region:N:J关联已有素材。'
    '同一个对象跨截图、图表和流程图保持相同name，不按页面重新起名。只安排呈现，不改变旁白。')


def canonical(name: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', name).casefold()).strip('“”"‘’')


def inferred_role(component: str, text: str, index: int, count: int) -> str:
    if re.search(r'回顾|总结一下|归纳一下|最后总结', text):
        return 'recap'
    if component == 'comparison' or re.search(r'相比|对比|比较|分别是', text):
        return 'compare'
    if re.search(r'结论|因此|所以|最终|结果是', text):
        return 'result'
    if index == 0 and re.search(r'[?？]|为什么|值不值得|如何选择', text):
        return 'question'
    if component in {'data_chart', 'scatter_chart', 'flow_diagram', 'object_showcase', 'material_collage'}:
        return 'explain'
    if len(text) < 26 and index not in {0, count-1}:
        return 'bridge'
    return 'explain'


def default_entities(component: str, labels: dict) -> list[dict]:
    candidates = labels.items()
    if component == 'data_chart':
        candidates = [(t, name) for t, name in candidates if t.startswith('series:')]
    elif component in {'object_showcase', 'material_collage'}:
        candidates = [(t, name) for t, name in candidates if t.startswith('image:')]
    return [{'name': name, 'targets': [target]} for target, name in candidates if 1 < len(name) <= 24]


def attach(board: dict, pages: list[dict], groups: list[dict], pipeline, seeds: list[dict] | None = None) -> None:
    registry, records = {}, []
    for entity in seeds or []:
        registry.setdefault(canonical(entity['name']), {k: entity[k] for k in ('id', 'name', 'color')})
    for i, (scene, page, group) in enumerate(zip(board['scenes'], pages, groups)):
        component, p = scene['component'], scene['props']
        labels = object_labels(component, p)
        fallback = default_entities(component, labels)
        if component == 'concept_reveal':
            fallback = [e for e in fallback if canonical(e['name']) in registry
                        or any(line.startswith(e['name']+mark) for line in p['lines'] for mark in ('：', ':'))]
        selected = page.get('entities') or fallback
        entities = {}
        for item in selected:
            key = canonical(item['name'])
            targets = [t for t in item.get('targets', []) if t in labels]
            if not targets:
                targets = [t for t, label in labels.items() if canonical(label) == key]
            if not key or not targets:
                continue
            if key not in registry:
                registry[key] = {'id': 'e_'+hashlib.sha256(key.encode()).hexdigest()[:12],
                                 'name': item['name'], 'color': COLORS[len(registry)%len(COLORS)]}
            entity = entities.setdefault(key, {**registry[key], 'targets': []})
            entity['targets'] = list(dict.fromkeys([*entity['targets'], *targets]))[:16]
        role = page.get('story_role') or inferred_role(component, group['text'], i, len(pages))
        if i > 0 and re.match(r'^(?:最后[，,：:]?\s*)?回顾', group['text']):
            role = 'recap'
        importance = page.get('importance') or ('key' if role in {'compare', 'result', 'recap'} else 'support' if role=='bridge' else 'normal')
        objects = len(labels)
        story = {'version': 1, 'role': role, 'importance': importance,
                 'density': 'dense' if objects>4 else 'light' if objects<2 else 'normal',
                 'entities': list(entities.values())[:12]}
        # The planned component already expresses this paragraph's summary.
        # Shared names (such as "context") and recency do not identify a visual
        # worth repeating; inferred thumbnails can replace it with another topic.
        # Keep the recap role for pacing, and keep its own scene and transitions.
        p['story'] = story
        records.append({'scene_id': scene['id'], 'component': component, **story})
    pipeline.write_json(pipeline.ARTIFACTS/'story-direction.json', {'version': 4,
        'extra_model_calls': 0, 'recap_policy': 'planned_scene',
        'entities': list(registry.values()), 'scenes': records})
