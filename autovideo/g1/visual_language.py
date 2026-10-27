"""Shared object vocabulary and additive component contracts for video visuals."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'schemas/visual-language-catalog.json'
COMPONENTS = {'concept_reveal', 'comparison', 'flow_diagram'}


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def visuals_schema() -> dict:
    vocabulary = catalog()
    return {'type': 'array', 'maxItems': 8, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['target', 'icon'],
        'properties': {
            'target': {'type': 'string', 'pattern': r'^(line:[0-3]|side:(left|right)|node:[a-z][a-z0-9_]{0,47})$'},
            'icon': {'enum': [row['id'] for row in vocabulary['objects']]},
            'action': {'enum': ['none', *vocabulary['actions']]},
            'phrase': {'type': 'string', 'minLength': 1, 'maxLength': 80}}}}


def component_schema(component: str) -> dict:
    """Extend copies at runtime; the frozen component schema files stay intact."""
    schema = json.loads((ROOT / f'schemas/components/{component}.schema.json').read_text(encoding='utf-8'))
    if component in COMPONENTS:
        schema['properties']['visuals'] = visuals_schema()
    if component == 'bar_chart':
        # The complete unit may be displayed once in the scene heading.
        schema['properties']['unit']['minLength'] = 0
    return schema


def validate_references(component: str, props: dict) -> None:
    targets = ({f'line:{i}' for i in range(len(props['lines']))} if component == 'concept_reveal'
               else {'side:left', 'side:right'} if component == 'comparison'
               else {f"node:{node['id']}" for node in props.get('nodes', [])})
    seen = set()
    for item in props.get('visuals', []):
        if item['target'] not in targets or item['target'] in seen:
            raise ValueError('图标需要对应本场景的一个独立对象')
        seen.add(item['target'])
