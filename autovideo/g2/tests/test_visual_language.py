import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from g1.visual_language import component_schema
from g1.video_ir import ValidationFault, validate_video_ir
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g2 import explanation, information_motion as motion, process_diagram, visual_language as visual


def concept():
    return {'title': '文件的三个状态', 'lines': ['文件处理：解析内容', '结果生成：输出就绪', '连接中断：请求停止'],
            'visuals': [{'target': 'line:0', 'icon': 'file', 'action': 'process', 'phrase': '处理文件时'},
                        {'target': 'line:1', 'icon': 'result', 'action': 'complete', 'phrase': '生成结果时'},
                        {'target': 'line:2', 'icon': 'network', 'action': 'disconnect', 'phrase': '连接中断时'}]}


def test_visual_choices_are_in_the_existing_model_plan_not_an_extra_request():
    messages = explanation.messages({'title': '文件查询', 'groups': [{'id': 'card_1', 'text': '用户上传文件。'}]})
    payload = json.loads(messages[1]['content'])
    assert payload['groups'][0]['text'] == '用户上传文件。'
    assert 'visuals' in payload['component_props_schemas']['concept_reveal']['properties']
    assert 'flow_diagram' in payload['component_props_schemas']
    assert 'layout' not in payload['component_props_schemas']['flow_diagram']['properties']
    assert any(row['id'] == 'database' for row in payload['visual_vocabulary']['objects'])
    assert '人工确认' not in messages[0]['content']


def test_optional_icons_preserve_old_props_and_unknown_objects_cannot_reach_renderer(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir.update(presentation={'preset': 'explanation'})
    ir['style']['preset'] = 'minimal_light'
    ir['scenes'][0].update(component='concept_reveal', props=concept())
    ir['scenes'][0]['beats'][0]['target_id'] = 'line:0'
    assert validate_video_ir(ir, root)['scenes'][0]['props']['visuals'][0]['icon'] == 'file'
    broken = copy.deepcopy(ir)
    broken['scenes'][0]['props']['visuals'][0]['target'] = 'node:missing'
    with pytest.raises(ValidationFault, match='VISUAL_OBJECT_REFERENCE'):
        validate_video_ir(broken, root)
    broken['scenes'][0]['props']['visuals'][0]['icon'] = 'arbitrary-component'
    with pytest.raises(ValidationFault, match='INVALID_COMPONENT_PROPS'):
        validate_video_ir(broken, root)
    old = {'title': '观点', 'lines': ['选择适合自己的方式']}
    Draft202012Validator(component_schema('concept_reveal')).validate(old)
    assert visual.enrich('concept_reveal', old) == old


def test_literal_fallback_prefers_specific_objects_and_respects_explicit_choices():
    p = {'title': '对象', 'lines': ['文件夹：整理素材', '数据库：存储记录', '选择适合自己的方式']}
    assert [v['icon'] for v in visual.enrich('concept_reveal', p)['visuals']] == ['folder', 'database']
    assert 'visuals' not in p
    selected = {**p, 'visuals': [{'target': 'line:0', 'icon': 'book'}]}
    assert visual.enrich('concept_reveal', selected) is selected
    assert visual.enrich('concept_reveal', {**p, 'visuals': []})['visuals'] == []


def test_actions_follow_phrases_across_tts_splits_and_survive_as_normal_beats(tmp_path):
    p = concept()
    scene = {'id': 'card_1', 'component': 'concept_reveal', 'props': p,
             'headline': p['title'], 'narration_segment_ids': ['n1', 'n2']}
    rows = [{'id': 'n1', 'text': '先看三个状态。处理文', 'start_ms': 0, 'end_ms': 3000},
            {'id': 'n2', 'text': '件时正在解析。生成结果时输出就绪。连接中断时请求停止。', 'start_ms': 3100, 'end_ms': 10000}]
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda *args: None)
    before = copy.deepcopy(scene)
    board = motion.compile_storyboard({'scenes': [scene]}, {'rows': rows}, pipeline)
    actions = [b for b in board['scenes'][0]['beats'] if '_action_' in b['id']]
    assert len(actions) == 3
    first = next(b for b in actions if b['target_id'] == 'line:0')
    assert first['trigger']['segment_id'] == 'n1'
    assert first['trigger']['offset_ms'] == int(len('先看三个状态。')/len(rows[0]['text'])*3000)
    assert scene == before
    assert board['scenes'][0]['props'] == p


def test_object_flow_waits_for_both_endpoints_and_uses_spoken_object_names():
    props = {'title': '查询', 'takeaway': '返回结果', 'nodes': [
        {'id': 'person', 'label': '用户'}, {'id': 'db', 'label': '数据库'}, {'id': 'out', 'label': '结果'}],
        'edges': [{'id': 'query', 'from': 'person', 'to': 'db'}, {'id': 'return', 'from': 'db', 'to': 'out'}], 'paths': []}
    props = visual.enrich('flow_diagram', props)
    text = '用户发起请求，数据库进行检索，结果返回。'
    cues = motion.automatic_cues({'component': 'flow_diagram', 'props': props, 'narration_segment_ids': ['n1']},
        {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': 5000}})
    positions = {c['target_id']: c['character_index'] for c in cues}
    assert positions['edge:query'] == positions['node:db'] == text.index('数据库')
    assert positions['edge:return'] == positions['node:out'] == text.index('结果')
    assert cues[0]['target_id'] == 'scene'


def test_existing_process_layout_reserves_room_for_icons_without_changing_labels():
    scene = {'id': 's1', 'nodes': [{'id': 'db', 'label': '数据库'}, {'id': 'answer', 'label': '答案'}],
             'edges': [{'id': 'go', 'from': 'db', 'to': 'answer', 'label': '查询'}]}
    request = process_diagram.layout_request({'scenes': [scene]})['scenes'][0]
    assert request['nodes'][0]['height'] >= 160
    assert request['nodes'][1]['height'] == 100
    assert request['nodes'][0]['label_lines'] == ['数据库']


def test_visual_schema_does_not_accept_external_animation_paths_or_code():
    props = concept()
    props['visuals'][0]['animation_url'] = 'https://example.test/arbitrary.json'
    with pytest.raises(ValidationError):
        Draft202012Validator(component_schema('concept_reveal')).validate(props)


def test_static_comparison_objects_reveal_at_their_own_narration_phrases():
    props = {'left': {'label': '本地服务器', 'items': ['掌控环境']},
             'right': {'label': '云端服务', 'items': ['多端访问']},
             'visuals': [{'target': 'side:left', 'icon': 'server', 'phrase': '本地服务器便于'},
                         {'target': 'side:right', 'icon': 'cloud', 'phrase': '云端服务便于'}]}
    text = '先看两种方式。本地服务器便于掌控环境，云端服务便于多端访问。'
    cues = motion.automatic_cues({'component': 'comparison', 'props': props, 'narration_segment_ids': ['n1']},
        {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': 7000}})
    assert [c['character_index'] for c in cues] == [text.index('本地服务器便于'), text.index('云端服务便于')]


def test_spoken_phrase_matching_preserves_decimal_values_and_source_character_positions():
    assert visual.phrase_at('先看。max档成本 5.98 美元，接着看另一档。', 'MAX 档成本5.98美元。') == 3
    assert visual.phrase_at('成本59.8美元。', '成本5.98美元。') == -1
    assert visual.phrase_at('方案A，方案A。', '方案 A。', 4) == 4


def test_cyclic_object_flow_keeps_narration_order_and_uses_the_mixed_runtime(tmp_path):
    from g2.tests.test_process_diagram import actual_layout
    props = {'title': '查询流程', 'takeaway': '结果返回给用户',
             'nodes': [{'id': 'user', 'label': '用户'}, {'id': 'server', 'label': '服务器'},
                       {'id': 'db', 'label': '数据库'}, {'id': 'result', 'label': '结果'}],
             'edges': [{'id': f'e{i}', 'from': a, 'to': b, 'label': ''}
                       for i, (a, b) in enumerate([('user','server'),('server','db'),('db','result'),('result','user')])],
             'paths': []}
    props = visual.enrich('flow_diagram', props)
    layout = actual_layout(tmp_path, {'scenes': [{'id': 's1', **props}]})['scenes'][0]['layout']
    axis = 'x' if layout['direction'] == 'RIGHT' else 'y'
    assert [n['id'] for n in sorted(layout['nodes'], key=lambda n: n[axis])] == ['user','server','db','result']
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir.update(presentation={'preset': 'explanation'})
    ir['style']['preset'] = 'minimal_light'
    ir['scenes'][0]['props'] = {**props, 'layout': layout}
    ir['scenes'][0]['beats'][0]['target_id'] = 'scene'
    assert validate_video_ir(ir, root)['scenes'][0]['component'] == 'flow_diagram'
