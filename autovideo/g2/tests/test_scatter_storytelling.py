import copy
import json
from types import SimpleNamespace

import pytest
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import explanation, information_motion as motion
from g2.scene_fields import apply_changes


def scatter():
    return {'title': '成本和速度', 'x_label': '月成本', 'x_unit': '美元', 'y_label': '处理速度', 'y_unit': '次/秒',
            'points': [{'name': '方案A', 'x': 8, 'y': 40}, {'name': '方案B', 'x': 12, 'y': 75}, {'name': '方案C', 'x': 18, 'y': 90}],
            'reveal_phrases': ['方案A', '方案B', '方案C'],
            'focus_steps': [{'indices': [0, 1], 'phrase': '先比较A和B', 'caption': '看清成本与速度的取舍'}],
            'focus_indices': [1], 'takeaway': 'B更均衡', 'takeaway_phrase': '综合看B'}


def page(p=None):
    return {'pages': [{'id': 'card_1', 'component': 'scatter_chart', 'props': p or scatter()}]}


def test_planner_gets_scatter_and_uses_the_existing_upstream_groups():
    groups = [{'id': 'card_1', 'text': 'A的成本8、速度40，B的成本12、速度75。'}]
    payload = json.loads(explanation.messages({'title': '比较', 'groups': groups})[1]['content'])
    assert payload['groups'] == groups
    assert 'scatter_chart' in payload['component_props_schemas']
    assert 'focus_steps' in payload['component_props_schemas']['scatter_chart']['properties']
    explanation.validate(page(), {'groups': groups})


def test_scatter_reveals_then_revisits_objects_at_spoken_comparisons():
    text = '方案A成本8速度40；方案B成本12速度75；方案C成本18速度90。先比较A和B，综合看B更均衡。'
    scene = {'component': 'scatter_chart', 'props': scatter(), 'narration_segment_ids': ['n1']}
    cues = motion.automatic_cues(scene, {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': 16000}})
    assert [c['target_id'] for c in cues] == ['title', 'line:0', 'line:1', 'line:2', 'line:3', 'line:4']
    assert [c['character_index'] for c in cues[1:]] == [text.index(x) for x in ['方案A', '方案B', '方案C', '先比较A和B', '综合看B']]


def test_reveal_phrase_can_span_a_measured_tts_split():
    p = scatter()
    p['reveal_phrases'] = ['方案A成本', '方案B成本', '方案C成本']
    scene = {'component': 'scatter_chart', 'props': p, 'narration_segment_ids': ['n1', 'n2']}
    rows = {'n1': {'id': 'n1', 'text': '方案A成', 'start_ms': 0, 'end_ms': 1000},
            'n2': {'id': 'n2', 'text': '本8。方案B成本12，方案C成本18，先比较A和B，综合看B。', 'start_ms': 1100, 'end_ms': 9000}}
    cues = motion.automatic_cues(scene, rows)
    assert cues[1]['segment_id'] == 'n1' and cues[1]['character_index'] == 0
    assert cues[2]['segment_id'] == 'n2' and cues[2]['quote'] == '方案B成本'


def test_scatter_and_mapped_picture_stay_on_the_same_mixed_timeline(tmp_path):
    groups = [{'id': 'card_1', 'title': '比较', 'text': '方案A和B。', 'segment_ids': ['n1'], 'image_indices': [0]}]
    saved = {'image_materials': [{'caption': '页面', 'role': 'detail'}]}
    assets = [{'id': 'slide_image_1', 'path': 'assets/slideshow/image-01.png'}]
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda *args: None)
    board = explanation.compile_storyboard(pipeline, {**page(), 'groups': groups},
        {'rows': [{'id': 'n1', 'text': '方案A和B。', 'start_ms': 0, 'end_ms': 8000}]}, saved, assets)
    assert board['scenes'][0]['component'] == 'scatter_chart'
    assert board['presentation'] == 'explanation'
    assert len(board['scenes'][0]['beats']) == 6


def test_scatter_runtime_and_screen_edits_keep_numeric_pairs(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir.update(presentation={'preset': 'explanation'})
    ir['style']['preset'] = 'minimal_light'
    ir['scenes'][0].update(component='scatter_chart', props=scatter())
    ir['scenes'][0]['beats'][0]['target_id'] = 'line:4'
    assert validate_video_ir(ir, root)['scenes'][0]['component'] == 'scatter_chart'
    edited = apply_changes(ir['scenes'][0], {'points.0.name': '基础方案', 'focus_steps.0.caption': '比较两种选择'})
    assert edited['props']['points'][0] == {'name': '基础方案', 'x': 8, 'y': 40}
    broken = copy.deepcopy(ir)
    broken['scenes'][0]['props']['focus_steps'][0]['indices'] = [3]
    with pytest.raises(ValidationFault, match='CHART_LAYOUT_INVALID'):
        validate_video_ir(broken, root)


def test_equal_coordinates_and_negative_or_zero_values_are_displayable():
    p = scatter()
    p['points'] = [{'name': 'A', 'x': -3, 'y': 0}, {'name': 'B', 'x': -3, 'y': 0}, {'name': 'C', 'x': 0, 'y': 0}]
    explanation.validate(page(p), {'groups': [{'id': 'card_1'}]})
