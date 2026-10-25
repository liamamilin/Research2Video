"""Document contract sanity checks ONLY; not a video renderer or G1 pass.
Run: python -m pytest tests/test_design_contracts.py -q
Requires: pip install jsonschema pytest
"""
import json
import copy
import math
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / 'schemas'

def read(rel):
    return json.loads((ROOT / rel).read_text())

@pytest.fixture
def schema():
    return read('schemas/video-ir-v0.2.schema.json')

@pytest.fixture
def example():
    return read('examples/video-ir-beats-v0.2.json')

def test_schema_and_fixture_are_syntactically_valid(schema, example):
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)

@pytest.mark.parametrize('name', ['title_card','concept_reveal','flow_diagram','comparison','timeline','bar_chart'])
def test_props_schemas_valid(name):
    Draft202012Validator.check_schema(read(f'schemas/components/{name}.schema.json'))

def test_flow_diagram_props_match_schema(example):
    props_schema = read('schemas/components/flow_diagram.schema.json')
    Draft202012Validator(props_schema).validate(example['scenes'][0]['props'])

def test_action_registry_allows_example_beats(example):
    registry=read('schemas/component-registry-v0.2.json')['components']
    scene=example['scenes'][0]
    actions=registry[scene['component']]['actions']
    props=scene['props']
    node_ids={n['id'] for n in props['nodes']}
    edge_ids={e['id'] for e in props['edges']}
    path_ids={p['id'] for p in props['paths']}
    segment_ids={s['id'] for s in example['narration']['segments']}
    for b in scene['beats']:
        target=b['target_id']
        patterns=actions[b['action']]
        def match(p):
            return target==p or (p.endswith('*') and target.startswith(p[:-1]))
        assert any(match(p) for p in patterns)
        if target.startswith('node:'): assert target[5:] in node_ids
        if target.startswith('edge:'): assert target[5:] in edge_ids
        if target.startswith('path:'): assert target[5:] in path_ids
        assert b['trigger']['segment_id'] in segment_ids
    for e in props['edges']:
        assert e['from'] in node_ids and e['to'] in node_ids
    for path in props['paths']:
        assert all(e in edge_ids for e in path['edge_ids'])

def test_schema_rejects_unknown_fields(schema, example):
    bad=copy.deepcopy(example)
    bad['scenes'][0]['beats'][0]['arbitrary_javascript']='alert(1)'
    assert list(Draft202012Validator(schema).iter_errors(bad))

def test_schema_rejects_parent_directory_path(schema, example):
    bad=copy.deepcopy(example)
    bad['assets'][0]['path']='assets/../../private/key.txt'
    assert list(Draft202012Validator(schema).iter_errors(bad))

def test_example_exposes_placeholder_not_real_tts(example):
    assert 'PLACEHOLDER' in example['assets'][0]['origin']
    assert example['assets'][0]['sha256']=='0'*64

def test_timing_anchors_inside_scene(example):
    fps=example['video']['fps']
    frame_at=lambda ms:(ms*fps+500)//1000
    segments={s['id']:s for s in example['narration']['segments']}
    for scene in example['scenes']:
        a=frame_at(scene['start_ms'])
        b=frame_at(scene['start_ms']+scene['duration_ms'])
        for beat in scene['beats']:
            t=beat['trigger'];seg=segments[t['segment_id']]
            ts=seg[f"{t['boundary']}_ms"]+t['offset_ms']
            assert a <= frame_at(ts) < b
    assert frame_at(example['video']['duration_ms'])==315


def test_cross_language_half_up_rounding():
    assert (150*30+500)//1000 == 5  # Python round(4.5)==4; JS Math.round(4.5)==5
