"""Keep existing page boundaries with their explicitly planned spoken phrases.

The first duration-based assembly is a budget, not final semantic ownership.
After planning, move whole speech segments only where both adjacent pages have
unique, non-interleaved anchors. Leave ambiguous plans for another expression.
"""
from __future__ import annotations

import copy


def phrases(page: dict) -> list[str]:
    """Only authored timing anchors, never inferred labels or topic keywords."""
    props = page['props']
    result = [v.get(k, '') for v in props.get('visuals', []) for k in ('reveal_phrase', 'phrase')]
    result += props.get('reveal_phrases', [])
    result += [v.get('phrase', '') for v in props.get('focus_steps', [])]
    result += [props.get('takeaway_phrase', '')]
    if page['component'] == 'code_demo':
        result += [v.get('phrase', '') for v in props.get('lines', [])]
    if page['component'] == 'flow_diagram':
        result += [v.get('reveal_phrase', '') for v in props.get('nodes', []) + props.get('edges', [])]
    result += [v.get('phrase', '') for v in page.get('material_cues', [])]
    result += [page.get('material_summary_phrase', '')]
    return list(dict.fromkeys(p for p in result if p.strip()))


def reconcile(plan: dict, alignment: dict, speed: float = 1) -> tuple[dict, list[dict]]:
    updated = copy.deepcopy(plan)
    groups, pages = updated['groups'], updated['pages']
    rows = {r['id']: r for r in alignment['rows']}
    changes = []
    for i in range(1, len(groups)):
        left, right = groups[i - 1:i + 1]
        if (not left.get('paragraph_ids') or left.get('paragraph_ids') != right.get('paragraph_ids')
                or left.get('chapter_id') != right.get('chapter_id')
                or left.get('image_indices', []) != right.get('image_indices', [])):
            continue
        ids = left['segment_ids'] + right['segment_ids']
        text = ''.join(rows[sid]['text'] for sid in ids)
        ends, cursor = [], 0
        for sid in ids:
            cursor += len(rows[sid]['text'])
            ends.append(cursor)

        def anchors(page: dict) -> list[tuple[int, int]]:
            found = []
            for phrase in phrases(page):
                at = text.find(phrase)
                if at < 0 or text.find(phrase, at + 1) >= 0:
                    continue  # Missing or repeated wording does not determine ownership.
                first = next(j for j, end in enumerate(ends) if at < end)
                last = next(j for j, end in enumerate(ends) if at + len(phrase) <= end)
                found.append((first, last))
            return found

        a, b = anchors(pages[i - 1]), anchors(pages[i])
        if not a or not b:
            continue
        low, high = max(last for _, last in a) + 1, min(first for first, _ in b)
        if low > high:
            continue  # Interleaved explanations need a different page plan, not more timing tricks.
        old = len(left['segment_ids'])
        cut = min(max(old, low), high)
        if cut == old or not 0 < cut < len(ids):
            continue
        # Never make a flash page. This is a reading floor, not a target rhythm.
        parts = [ids[:cut], ids[cut:]]
        if any((rows[p[-1]]['end_ms'] - rows[p[0]]['start_ms']) / speed < 2000 for p in parts):
            continue
        changes.append({'left': left['id'], 'right': right['id'],
                        'before': [left['segment_ids'][:], right['segment_ids'][:]], 'after': parts,
                        'previous_boundary_ms': rows[ids[old]]['start_ms'],
                        'boundary_ms': rows[ids[cut]]['start_ms'],
                        'reason': 'unique explicit spoken anchors belong on their own page'})
        for group, part in zip((left, right), parts):
            group.update(segment_ids=part, text=''.join(rows[sid]['text'] for sid in part),
                         narration=[{'id': sid, 'text': rows[sid]['text']} for sid in part],
                         duration_ms=round((rows[part[-1]]['end_ms'] - rows[part[0]]['start_ms']) / speed))
    return updated, changes
