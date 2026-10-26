"""Optional camera cues reference existing beats and visual objects, never a wall clock."""
from __future__ import annotations

MATERIAL_COMPONENTS = {'object_showcase', 'material_collage'}
COMPONENTS = {'flow_diagram', 'data_chart', 'scatter_chart', *MATERIAL_COMPONENTS}


def shot_schema() -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': ['version', 'profile', 'cues'],
            'properties': {
                'version': {'const': 1}, 'profile': {'enum': ['brief', 'balanced', 'explain']},
                'summary_beat_id': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                'closing': {'enum': ['overview', 'hold']},
                'cues': {'type': 'array', 'maxItems': 48, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['beat_id', 'targets'],
                    'properties': {'beat_id': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                        'targets': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'uniqueItems': True,
                                    'items': {'type': 'string', 'pattern': r'^(node:[a-z][a-z0-9_]{0,47}|category:[0-7]|series:[0-2]|point:[0-9]|image:([0-9]|1[01])|region:([0-9]|1[01]):[0-2])$'}}}}}}}


def validate_shots(scene: dict) -> None:
    plan, props = scene['props'].get('shot_plan'), scene['props']
    if plan is None:
        return
    beats = {beat['id'] for beat in scene['beats']}
    if scene['component'] == 'flow_diagram':
        targets = {f"node:{node['id']}" for node in props['nodes']}
    elif scene['component'] == 'data_chart':
        targets = ({f'category:{i}' for i in range(len(props['categories']))}
                   | {f'series:{i}' for i in range(len(props['series']))})
    elif scene['component'] in MATERIAL_COMPONENTS:
        targets = {f'image:{i}' for i in range(len(props['images']))}
        targets |= {f'region:{i}:{j}' for i, image in enumerate(props['images'])
                    for j in range(len((image.get('focus') or {}).get('regions', [])))}
    else:
        targets = {f'point:{i}' for i in range(len(props['points']))}
    if plan.get('summary_beat_id') and plan['summary_beat_id'] not in beats:
        raise ValueError('总结镜头需要引用本场景已有时间点')
    for cue in plan['cues']:
        if cue['beat_id'] not in beats or not set(cue['targets']) <= targets:
            raise ValueError('镜头需要引用本场景已有时间点和对象')
