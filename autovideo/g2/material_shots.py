"""Bind existing image regions to spoken phrases in the existing storyboard pass."""
from __future__ import annotations

import hashlib


def describe(images: list[dict]) -> list[dict]:
    return [{'image_index': i, 'caption': image.get('caption', ''), 'role': image.get('role', 'reference'),
             'regions': [{'region_index': j, 'label': region.get('label', '')}
                         for j, region in enumerate((image.get('focus') or {}).get('regions', []))]}
            for i, image in enumerate(images)]


def compose(scene: dict, rows: list[dict], profile: str, hints: list[dict] | None = None,
            summary_phrase: str = '') -> dict:
    images = scene['props']['images']
    selected = scene['props']['focus_indices']
    text = ''.join(row['text'] for row in rows)
    valid = {f'image:{i}': images[i].get('caption', '') for i in selected}
    valid.update({f'region:{i}:{j}': region.get('label', '') for i in selected
                  for j, region in enumerate((images[i].get('focus') or {}).get('regions', []))})
    events, directed_images = [], set()
    cursor = 0
    for hint in hints or []:
        i = hint['image_index']
        target = (f"region:{i}:{hint['region_index']}" if hint.get('region_index') is not None else f'image:{i}')
        if target not in valid:
            continue
        at = text.find(hint['phrase'], cursor)
        if at < 0:
            at = text.find(hint['phrase'])
        if at >= 0:
            events.append((at, target))
            directed_images.add(i)
            cursor = at+len(hint['phrase'])
    defaults = [target for i in selected for target in (
        [f'region:{i}:{j}' for j in range(len(images[i]['focus']['regions']))]
        if (images[i].get('focus') or {}).get('regions') else [f'image:{i}'])]
    for ordinal, target in enumerate(defaults):
        if int(target.split(':')[1]) in directed_images:
            continue
        label = valid[target]
        at = text.find(label) if label else -1
        # Older callers have labels and coordinates, with no phrase hints. Their
        # fallback follows positions in the measured speech, rather than page progress.
        if at < 0:
            at = min(len(text)-1, len(text)*(ordinal+1)//(len(defaults)+1))
        events.append((at, target))
    events.sort(key=lambda event: event[0])
    events = events[:48]
    prefix = 'material_'+hashlib.sha256(scene['id'].encode()).hexdigest()[:12]+'_'
    scene['beats'] = [b for b in scene['beats'] if not b['id'].startswith(prefix)]

    def beat_at(character: int) -> str:
        for row in rows:
            if character < len(row['text']):
                beat_id = prefix+str(sum(b['id'].startswith(prefix) for b in scene['beats']))
                offset = int(character/len(row['text'])*(row['end_ms']-row['start_ms']))
                scene['beats'].append({'id': beat_id, 'action': 'reveal', 'target_id': 'scene',
                    'trigger': {'segment_id': row['id'], 'boundary': 'start', 'offset_ms': offset}})
                return beat_id
            character -= len(row['text'])
        raise ValueError('图片镜头需要对应本场景的旁白')

    cues = [{'beat_id': beat_at(at), 'targets': [target]} for at, target in events]
    plan = {'version': 1, 'profile': profile, 'cues': cues}
    summary_at = text.find(summary_phrase) if summary_phrase else -1
    if summary_at >= 0 and (not events or summary_at > events[-1][0]):
        plan['summary_beat_id'] = beat_at(summary_at)
    order = {r['id']: i for i, r in enumerate(rows)}
    scene['beats'].sort(key=lambda b: (order[b['trigger']['segment_id']], b['trigger'].get('offset_ms', 0)))
    return plan
