"""Arrange existing narration phrases into visual units using measured speech."""
from __future__ import annotations

import re

from g2.shot_director import profile_for

RHYTHM = {
    'brief': {'minimum': 4, 'maximum': 14, 'units': 3},
    'balanced': {'minimum': 5, 'maximum': 20, 'units': 4},
    'explain': {'minimum': 6, 'maximum': 24, 'units': 4},
}
TURN = re.compile(r'^(?:接着|再看|然后|最后|另一方面|换一个|回到)')


def arrange(groups: list[dict], alignment: dict, saved: dict) -> tuple[list[dict], dict]:
    rows = {r['id']: r for r in alignment['rows']}
    profile = profile_for(saved)
    rhythm = RHYTHM[profile]
    speed = float(saved.get('video_speed', 1))

    def duration(ids: list[str]) -> float:
        return (rows[ids[-1]]['end_ms']-rows[ids[0]]['start_ms'])/speed

    def capacity(group: dict) -> tuple[float, int]:
        objects = sum(max(1, len(m.get('regions', []))) for m in group.get('image_materials', []))
        extra = min(2, max(0, objects-1))
        return (rhythm['maximum']+extra*3)*1000, rhythm['units']+extra

    units = []
    for group in groups:
        maximum, count = capacity(group)
        chunks, current = [], []
        long = duration(group['segment_ids']) > maximum
        for sid in group['segment_ids']:
            candidate = [*current, sid]
            new_topic = (long and current and TURN.match(rows[sid]['text'])
                         and duration(current) >= maximum*.5)
            full = len(candidate) > count and current and duration(current) >= maximum*.65
            if current and (duration(candidate) > maximum or full or new_topic):
                chunks.append(current)
                current = []
            current.append(sid)
        if current:
            chunks.append(current)
        # Keep a short tail with its context when the whole final unit still fits.
        if len(chunks) > 1 and duration(chunks[-1]) < rhythm['minimum']*1000:
            joined = chunks[-2]+chunks[-1]
            if duration(joined) <= maximum*1.25 and len(joined) <= count+1:
                chunks[-2:] = [joined]
        for i, ids in enumerate(chunks):
            unit = {**group, 'id': group['id'] if len(chunks)==1 else f"{group['id']}_p{i+1}",
                'segment_ids': ids, 'text': ''.join(rows[sid]['text'] for sid in ids),
                'narration': [{'id': sid, 'text': rows[sid]['text']} for sid in ids],
                'paragraph_ids': [group['id']], 'part_index': i+1, 'part_count': len(chunks),
                'duration_ms': round(duration(ids))}
            units.append(unit)
    result = []
    for unit in units:
        previous = result[-1] if result else None
        compatible = (previous and previous['chapter_id'] == unit['chapter_id']
                      and previous['image_indices'] == unit['image_indices']
                      and not previous.get('overview') and not unit.get('overview')
                      and previous['part_count'] == unit['part_count'] == 1
                      and set(previous['paragraph_ids']) != set(unit['paragraph_ids']))
        if compatible:
            ids = previous['segment_ids']+unit['segment_ids']
            origins = list(dict.fromkeys(previous['paragraph_ids']+unit['paragraph_ids']))
            maximum, count = capacity(unit)
            short = min(duration(previous['segment_ids']), duration(unit['segment_ids'])) < rhythm['minimum']*1000
            if short and len(origins)<=3 and len(ids)<=count and duration(ids)<=maximum:
                previous.update(segment_ids=ids, text=previous['text']+unit['text'],
                    narration=previous['narration']+unit['narration'], paragraph_ids=origins,
                    part_index=1, part_count=1, duration_ms=round(duration(ids)))
                continue
        result.append(unit)
    report = {'version': 3, 'profile': profile, 'timing': 'measured narration; existing phrase boundaries',
        'video_speed': speed, 'input_paragraphs': len(groups), 'visual_units': len(result),
        'extra_model_calls': 0, 'units': [{
            'id': unit['id'], 'chapter_id': unit['chapter_id'], 'paragraph_ids': unit['paragraph_ids'],
            'part_index': unit['part_index'], 'part_count': unit['part_count'],
            'narration_segment_ids': unit['segment_ids'], 'duration_ms': round(duration(unit['segment_ids'])),
            'image_indices': unit['image_indices'],
            'operation': 'merge' if len(unit['paragraph_ids'])>1 else 'split' if unit['part_count']>1 else 'keep',
        } for unit in result]}
    return result, report


def connect(board: dict, groups: list[dict], saved: dict) -> None:
    profile = profile_for(saved)
    for i, (scene, group) in enumerate(zip(board['scenes'], groups)):
        previous = board['scenes'][i-1] if i else None
        entry = 'cut'
        if previous and groups[i-1]['chapter_id'] == group['chapter_id']:
            entry = 'dissolve'
            same_picture = (scene['component'] in {'object_showcase', 'material_collage'}
                and scene['component'] == previous['component']
                and scene['props']['images'] == previous['props']['images']
                and scene['props']['focus_indices'] == previous['props']['focus_indices'])
            if same_picture and scene['props'].get('shot_plan') and previous['props'].get('shot_plan'):
                entry = 'carry'
                previous['props']['shot_plan']['closing'] = 'hold'
        scene['props']['scene_flow'] = {'version': 1, 'profile': profile, 'entry': entry,
            'paragraph_ids': group['paragraph_ids'],
            'part_index': group['part_index'], 'part_count': group['part_count']}
