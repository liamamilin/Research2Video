import copy
import json
from types import SimpleNamespace

import pytest

from g1.story_language import validate_story
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import explanation, story_direction
from g2.tests.test_explanation import chart
from g2.tests.test_shot_director import camera_runtime, camera_scene


def pipeline(tmp_path):
    return SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda path, value: path.write_text(json.dumps(value)))


def scene(sid, component, props):
    return {'id': sid, 'component': component, 'props': props}


def apply(tmp_path, scenes, pages=None, texts=None, seeds=None):
    pages = pages or [{'id': s['id']} for s in scenes]
    groups = [{'text': text} for text in (texts or ['展开说明。']*len(scenes))]
    board = {'scenes': scenes}
    story_direction.attach(board, pages, groups, pipeline(tmp_path), seeds)
    return [s['props']['story'] for s in scenes]


def test_same_object_keeps_name_color_and_identity_when_series_order_changes(tmp_path):
    comparison = scene('a', 'comparison', {'left': {'label': '本地方案'}, 'right': {'label': '云端方案'}})
    p = chart('grouped');p['series'] = [{'name': '云端方案', 'values': [25, 28, 30]}, {'name': '本地方案', 'values': [20, 22, 24]}]
    plot = scene('b', 'data_chart', p)
    first, second = apply(tmp_path, [comparison, plot])
    original = {e['name']: e for e in first['entities']}
    assert original['本地方案']['color'] != original['云端方案']['color']
    for entity in second['entities']:
        assert (entity['id'], entity['color']) == (original[entity['name']]['id'], original[entity['name']]['color'])
    assert second['entities'][0]['targets'] == ['series:0']
    assert plot['props']['series'] == p['series']


def test_object_identity_keeps_meaningful_punctuation_and_version_numbers():
    assert story_direction.canonical('方案 Ａ') == story_direction.canonical('方案A')
    assert story_direction.canonical('C++') != story_direction.canonical('C#')
    assert story_direction.canonical('v1.2') != story_direction.canonical('v12')


def test_model_entities_connect_material_to_native_chart_and_revisions_use_existing_palette(tmp_path):
    picture = scene('a', 'object_showcase', {'images': [{'caption': '产品页面', 'image_asset_id': 'i1'}]})
    plot = scene('b', 'scatter_chart', {'points': [{'name': '方案 A'}, {'name': '方案 B'}]})
    pages = [{'entities': [{'name': '方案A', 'targets': ['image:0']}]}, {}]
    first, second = apply(tmp_path, [picture, plot], pages)
    assert first['entities'][0]['id'] == second['entities'][0]['id']
    assert first['entities'][0]['color'] == second['entities'][0]['color']
    assert second['entities'][0]['name'] == '方案A'
    seeds = second['entities']
    revised = scene('c', 'concept_reveal', {'lines': ['方案 B：处理文件']})
    result = apply(tmp_path, [revised], seeds=seeds)[0]
    assert result['entities'][0]['color'] == seeds[1]['color']


def test_recap_reuses_two_existing_visuals_prefers_key_and_avoids_nested_recaps(tmp_path):
    scenes = [scene('picture1', 'object_showcase', {'images': [{'caption': '本地方案', 'image_asset_id': 'i1'}]}),
              scene('picture2', 'object_showcase', {'images': [{'caption': '本地方案', 'image_asset_id': 'i1'}]}),
              scene('plot', 'data_chart', chart()),
              scene('recap', 'concept_reveal', {'lines': ['回顾已有重点']}),
              scene('recap2', 'concept_reveal', {'lines': ['总结已有重点']})]
    pages = [{}, {}, {'importance': 'key'}, {'story_role': 'recap'}, {'story_role': 'recap'}]
    stories = apply(tmp_path, scenes, pages)
    assert stories[3]['recap'] == ['picture2', 'plot']
    assert stories[4]['recap'] == ['picture2', 'plot']
    validate_story(scenes[3], scenes[:3])
    scenes[3]['props']['story']['recap'] = ['recap']
    with pytest.raises(ValueError, match='前面已有'):
        validate_story(scenes[3], scenes[:3])


def test_recap_prefers_shared_objects_and_references_follow_earlier_scenes(tmp_path):
    scenes = [scene('a', 'comparison', {'left': {'label': '本地方案'}, 'right': {'label': '云端方案'}}),
              scene('b', 'concept_reveal', {'lines': ['其他工具：搜索']}),
              scene('c', 'concept_reveal', {'lines': ['本地方案：已有结论']})]
    stories = apply(tmp_path, scenes, [{'importance': 'key'}, {'importance': 'key'}, {'story_role': 'recap'}])
    assert stories[-1]['recap'] == ['a']


def test_explicit_spoken_recap_keeps_the_visual_review_when_model_calls_it_a_result(tmp_path):
    scenes = [scene('a', 'comparison', {'left': {'label': '本地方案'}, 'right': {'label': '云端方案'}}),
              scene('b', 'concept_reveal', {'lines': ['本地方案：更省']})]
    stories = apply(tmp_path, scenes, [{'importance': 'key'}, {'story_role': 'result'}],
                    texts=['两个方案放在一起看。', '最后回顾两个方案的区别。'])
    assert stories[-1]['role'] == 'recap' and stories[-1]['recap'] == ['a']


def test_planner_exposes_roles_and_same_chapter_request_carries_object_names(tmp_path, monkeypatch):
    groups = [{'id': 'a', 'chapter_id': 'c1', 'text': '先看本地方案。'}, {'id': 'b', 'chapter_id': 'c2', 'text': '再看本地方案。'}]
    monkeypatch.setattr(explanation, 'paragraph_groups', lambda *args: groups)
    calls = []
    p = pipeline(tmp_path)
    def generate(label, messages, schema, check, context):
        calls.append(context)
        return {'pages': [{'id': context['groups'][0]['id'], 'entities': [{'name': '本地方案'}]}]}
    p.generate_validated_plan = generate
    plan = explanation.plan_storyboard(p, {'title': '比较'}, {})
    assert plan['direction_version'] == 4 and len(calls) == 2
    assert calls[1]['established_entities'] == ['本地方案']
    payload = json.loads(explanation.messages(calls[1])[1]['content'])
    assert payload['established_entities'] == ['本地方案']
    assert 'story_role' in payload['required_schema']['properties']['pages']['items']['properties']


def test_story_contract_is_optional_and_uses_existing_objects(tmp_path):
    root = make_root(tmp_path);ir = base_ir(root)
    s = ir['scenes'][0]
    story = apply(tmp_path, [s])[0]
    assert validate_video_ir(ir, root)['scenes'][0]['props']['story'] == story
    s['props']['story']['entities'][0]['targets'] = ['node:missing']
    with pytest.raises(ValidationFault, match='STORY_REFERENCE'):
        validate_video_ir(ir, root)


def test_roles_and_density_adjust_camera_hold_without_altering_scene_or_beats(camera_runtime):
    original = camera_scene(length=900)
    explain = copy.deepcopy(original)
    explain['props']['story'] = {'role': 'explain', 'importance': 'normal', 'density': 'normal', 'entities': []}
    compare = copy.deepcopy(explain)
    compare['props']['story'].update(role='compare', importance='key', density='dense')
    e = camera_runtime(explain, [])
    c = camera_runtime(compare, [])
    assert c['pacing']['hold'] > e['pacing']['hold']
    assert c['pacing']['summary'] > e['pacing']['summary']
    bridge = copy.deepcopy(compare);bridge['props']['story']['role'] = 'bridge'
    assert len(camera_runtime(bridge, [])['shots']) == 1
    assert explain['beats'] == compare['beats'] == original['beats']
    assert explain['end_frame'] == compare['end_frame'] == original['end_frame']
