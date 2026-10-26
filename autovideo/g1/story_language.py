"""Optional presentation roles and stable visual identities over existing content."""
from g1.scene_flow import COMPONENTS

ROLES = ['question', 'explain', 'compare', 'result', 'bridge', 'recap']
COLORS = ['#17784B', '#557DB2', '#C9813E', '#886CAD', '#408E94', '#AF5969']


def story_schema() -> dict:
    return {'type': 'object', 'additionalProperties': False,
        'required': ['version', 'role', 'importance', 'density', 'entities'], 'properties': {
            'version': {'const': 1}, 'role': {'enum': ROLES},
            'importance': {'enum': ['key', 'normal', 'support']},
            'density': {'enum': ['light', 'normal', 'dense']},
            'entities': {'type': 'array', 'maxItems': 12, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'name', 'color', 'targets'], 'properties': {
                    'id': {'type': 'string', 'pattern': '^e_[a-f0-9]{12}$'},
                    'name': {'type': 'string', 'minLength': 1, 'maxLength': 32},
                    'color': {'enum': COLORS}, 'targets': {'type': 'array', 'maxItems': 16,
                        'uniqueItems': True, 'items': {'type': 'string', 'maxLength': 80}}}}},
            'recap': {'type': 'array', 'minItems': 1, 'maxItems': 2, 'uniqueItems': True,
                      'items': {'type': 'string', 'minLength': 1, 'maxLength': 80}}}}


def object_labels(component: str, p: dict) -> dict[str, str]:
    if component in {'object_showcase', 'material_collage'}:
        labels = {f'image:{i}': image.get('caption', '') for i, image in enumerate(p['images'])}
        labels.update({f'region:{i}:{j}': region.get('label', '') for i, image in enumerate(p['images'])
                       for j, region in enumerate((image.get('focus') or {}).get('regions', []))})
        return labels
    if component == 'data_chart':
        return {**{f'series:{i}': s['name'] for i, s in enumerate(p['series'])},
                **{f'category:{i}': name for i, name in enumerate(p['categories'])}}
    if component == 'scatter_chart':
        return {f'point:{i}': point['name'] for i, point in enumerate(p['points'])}
    if component == 'flow_diagram':
        return {f"node:{node['id']}": node['label'] for node in p['nodes']}
    if component == 'comparison':
        return {f'side:{side}': p[side]['label'] for side in ('left', 'right')}
    if component == 'bar_chart':
        return {f'bar:{i}': name for i, name in enumerate(p['categories'])}
    if component == 'price_change':
        return {f'line:{i*3 if p.get("staged") else i}': row['label'] for i, row in enumerate(p['rows'])}
    if component == 'concept_reveal':
        return {f'line:{i}': line.split('：', 1)[0].split(':', 1)[0] for i, line in enumerate(p['lines'])}
    return {}


def validate_story(scene: dict, previous: list[dict]) -> None:
    story = scene['props'].get('story')
    if not story:
        return
    targets = object_labels(scene['component'], scene['props'])
    if any(target not in targets for entity in story['entities'] for target in entity['targets']):
        raise ValueError('视觉对象需要对应当前画面已有对象')
    available = {s['id'] for s in previous if not s['props'].get('story', {}).get('recap')}
    if not set(story.get('recap', [])) <= available:
        raise ValueError('视觉回顾需要引用前面已有的画面')
