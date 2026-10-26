"""Compose camera intent from the existing storyboard, without another model call."""
from __future__ import annotations

import json

from g1.shot_language import COMPONENTS, MATERIAL_COMPONENTS


def profile_for(saved: dict) -> str:
    if saved.get('_shot_profile') in {'brief', 'balanced', 'explain'}:
        return saved['_shot_profile']
    recipe = saved.get('production_recipe', '')
    context = ((saved.get('production_context') or {}).get('content_recipe') or {}).get('id', '')
    if recipe == 'ai_news' or any(word in context for word in ('flash', 'quick_take')):
        return 'brief'
    if recipe in {'science_explainer', 'how_to'} or any(word in context for word in ('deep_dive', 'tutorial')):
        return 'explain'
    return 'balanced'


def camera_plan(scene: dict, profile: str) -> dict:
    props, component = scene['props'], scene['component']
    cues = []

    def add(target: str, objects: list[str]) -> None:
        beat = next((b for b in scene['beats'] if b['target_id'] == target and b['action'] == 'reveal'
                     and '_action_' not in b['id']), None)
        if beat is not None:
            cues.append({'beat_id': beat['id'], 'targets': objects})

    summary = None
    if component == 'flow_diagram':
        nodes = props['nodes']
        for i, node in enumerate(nodes):
            # Keep the relationship in view while discussing an individual node.
            preceding = {n['id'] for n in nodes[:i]}
            incoming = [e['from'] for e in props['edges'] if e['to'] == node['id'] and e['from'] in preceding]
            outgoing = [e['to'] for e in props['edges'] if e['from'] == node['id'] and e['to'] not in preceding]
            neighbor = incoming[-1] if incoming else outgoing[0] if outgoing else None
            ids = [neighbor, node['id']] if incoming else [node['id'], neighbor]
            pair = [n for key in ids for n in nodes if n['id'] == key]
            add(f"node:{node['id']}", [f"node:{n['id']}" for n in pair])
    elif component == 'data_chart':
        count = len(props['categories'] if props['kind'] == 'trend' else props['series'])
        for i in range(count):
            objects = ([f'category:{j}' for j in range(max(0, i-1), i+1)]
                       if props['kind'] == 'trend' else [f'series:{i}'])
            add(f'line:{i}', objects)
        summary = f'line:{count}' if props.get('takeaway') else None
    elif component == 'scatter_chart':
        count = len(props['points'])
        for i in range(count):
            add(f'line:{i}', [f'point:{i}'])
        for i, step in enumerate(props.get('focus_steps', [])):
            add(f'line:{count+i}', [f'point:{j}' for j in step['indices']])
        if props.get('takeaway') or props.get('focus_indices'):
            summary = f"line:{count+len(props.get('focus_steps', []))}"
    plan = {'version': 1, 'profile': profile, 'cues': cues}
    if summary:
        beat = next((b for b in scene['beats'] if b['target_id'] == summary), None)
        if beat:
            plan['summary_beat_id'] = beat['id']
    return plan


def attach(board: dict, saved: dict, pipeline, alignment: dict | None = None,
           pages: list[dict] | None = None) -> None:
    profile = profile_for(saved)
    records = []
    for scene in board['scenes']:
        if scene['component'] not in COMPONENTS:
            continue
        if scene['component'] in MATERIAL_COMPONENTS:
            if not alignment:
                continue
            from g2.material_shots import compose
            page = next((p for p in pages or [] if p['id'] == scene['id']), {})
            rows = {r['id']: r for r in alignment['rows']}
            speech = [rows[sid] for sid in scene['narration_segment_ids']]
            plan = compose(scene, speech, profile, page.get('material_cues'), page.get('material_summary_phrase', ''))
        else:
            plan = camera_plan(scene, profile)
        scene['props'] = {**scene['props'], 'shot_plan': plan}
        records.append({'scene_id': scene['id'], 'component': scene['component'], **plan})
    if records:
        pipeline.write_json(pipeline.ARTIFACTS / 'shot-director.json', {
            'version': 1, 'profile': profile, 'scenes': records,
            'timing': 'Measured speech and phrase cues; frame-driven overview, sustained focus, and summary',
            'extra_model_calls': 0})


def saved_request(pipeline) -> dict:
    path = getattr(pipeline, 'PROJECT_INPUT', None)
    path = path.parent / 'input.json' if path else None
    return json.loads(path.read_text()) if path and path.is_file() else {}
