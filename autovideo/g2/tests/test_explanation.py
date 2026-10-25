import copy
import json
from types import SimpleNamespace

import pytest
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import explanation, information_motion as motion


def chart(kind='trend'):
    return {'title': '月度使用量', 'kind': kind, 'unit': '万次', 'categories': ['一月', '二月', '三月'],
            'series': [{'name': '使用量', 'values': [12, 18, 16]}], 'takeaway': '二月更高', 'focus_index': 1,
            'reveal_phrases': ['一月', '二月', '三月']}


def test_planner_uses_upstream_copy_and_registered_visuals():
    groups = [{'id': 'card_1', 'text': '价格从20变成25，增加5。'}]
    payload = json.loads(explanation.messages({'title': '展示', 'groups': groups})[1]['content'])
    assert payload['groups'] == groups
    assert {'data_chart', 'price_change', 'bar_chart'} <= set(payload['component_props_schemas'])
    assert 'source' not in payload and 'facts' not in payload


def test_mixed_visuals_enabled_for_standard_matrix_forms_only():
    saved = {'visual_storytelling': True, 'chapters': [{'paragraphs': ['说明。']}]}
    for form in ('auto', 'information_motion', 'process_diagram', 'object_showcase', 'dynamic_collage', 'kinetic_typography'):
        assert explanation.enabled(saved, form)
    assert not explanation.enabled({**saved, 'production_mode': 'fast'}, 'information_cards')
    assert not explanation.enabled({**saved, 'visual_storytelling': False}, 'auto')
    assert not explanation.enabled({**saved, 'image_materials': [{'image_id': 'old-ordered-image'}]}, 'object_showcase')


def test_chart_cues_follow_spoken_months_across_phrases(tmp_path):
    speech = [{'id': 'n1', 'text': '先看一月12万次。', 'start_ms': 0, 'end_ms': 3000},
              {'id': 'n2', 'text': '二月18万次，三月16万次。', 'start_ms': 3300, 'end_ms': 8000}]
    scene = {'component': 'data_chart', 'props': chart(), 'narration_segment_ids': ['n1', 'n2']}
    cues = motion.automatic_cues(scene, {r['id']: r for r in speech})
    assert cues[1]['quote'] == '一月' and cues[1]['segment_id'] == 'n1'
    assert cues[2]['quote'] == '二月' and cues[2]['segment_id'] == 'n2'
    assert cues[3]['quote'] == '三月' and cues[3]['segment_id'] == 'n2'
    assert [c['target_id'] for c in cues] == ['title', 'line:0', 'line:1', 'line:2', 'line:3']


def test_paragraph_composition_keeps_images_and_native_charts_together(tmp_path):
    groups = [{'id': 'card_1', 'title': '说明', 'text': '页面说明。', 'segment_ids': ['n1'], 'image_indices': [0]},
              {'id': 'card_2', 'title': '数据', 'text': '每月变化。', 'segment_ids': ['n2'], 'image_indices': [0]}]
    plan = {'groups': groups, 'pages': [
        {'id': 'card_1', 'component': 'concept_reveal', 'props': {'title': '页面说明', 'lines': ['截图功能']}},
        {'id': 'card_2', 'component': 'data_chart', 'props': chart()}]}
    saved = {'image_materials': [{'image_id': 'picture', 'caption': '页面', 'role': 'detail'}]}
    assets = [{'id': 'slide_image_1', 'path': 'assets/slideshow/image-01.png'}]
    alignment = {'rows': [{'id': 'n1', 'text': '页面说明。', 'start_ms': 0, 'end_ms': 2000},
                          {'id': 'n2', 'text': '每月变化。', 'start_ms': 2100, 'end_ms': 9000}]}
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda *a: None)
    board = explanation.compile_storyboard(pipeline, plan, alignment, saved, assets)
    assert [s['component'] for s in board['scenes']] == ['object_showcase', 'data_chart']
    assert board['scenes'][0]['props']['images'][0]['image_asset_id'] == 'slide_image_1'
    assert board['presentation'] == 'explanation'
    assert [sid for s in board['scenes'] for sid in s['narration_segment_ids']] == ['n1', 'n2']


def test_chart_ir_compiles_and_rejects_only_unrenderable_array_shapes(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir['presentation'] = {'preset': 'explanation'}
    ir['style']['preset'] = 'minimal_light'
    ir['scenes'][0].update(component='data_chart', props=chart())
    ir['scenes'][0]['beats'][0]['target_id'] = 'scene'
    assert validate_video_ir(ir, root)['scenes'][0]['component'] == 'data_chart'
    broken = copy.deepcopy(ir)
    broken['scenes'][0]['props']['series'][0]['values'].pop()
    with pytest.raises(ValidationFault, match='CHART_CARDINALITY_MISMATCH'):
        validate_video_ir(broken, root)
    ir['scenes'][0]['props']['series'][0]['values'] = [-3, None, 2]
    assert validate_video_ir(ir, root)['scenes'][0]['props']['series'][0]['values'] == [-3, None, 2]


def test_flag_is_an_api_option_and_fast_mode_keeps_existing_executor():
    from g3.app import CreateJobRequest
    request = CreateJobRequest(input_mode='narration', duration_mode='content', video_form='information_motion',
                               chapters=[{'title': '数据', 'paragraphs': ['价格从20变成25，增加5。']}], visual_storytelling=True)
    assert request.model_dump()['visual_storytelling'] is True


def test_paragraph_grouping_preserves_original_mapping():
    saved = {'chapters': [{'title': '演示', 'paragraphs': ['第一句。第二句。', '第三句。']}],
             'image_materials': [{'placements': [{'chapter_index': 0, 'paragraph_index': 1}]}]}
    narration = {'title': '演示', 'segments': [{'id': f'c1_n{i+1}', 'text': text} for i, text in enumerate(['第一句。', '第二句。', '第三句。'])],
                 'chapters': [{'id': 'c1', 'title': '演示', 'narration_segment_ids': ['c1_n1', 'c1_n2', 'c1_n3']}]}
    result = explanation.paragraph_groups(None, narration, saved)
    assert [r['segment_ids'] for r in result] == [['c1_n1', 'c1_n2'], ['c1_n3']]
    assert [r['image_indices'] for r in result] == [[], [0]]


def test_old_new_and_delta_follow_their_spoken_values():
    props = {'title': '价格变化', 'staged': True, 'rows': [{'label': '订阅', 'before': '20', 'after': '25', 'change': '5', 'direction': 'up'}]}
    scene = {'component': 'price_change', 'props': props, 'narration_segment_ids': ['n1']}
    text = '订阅从20变成25，增加5。'
    cues = motion.automatic_cues(scene, {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': 5000}})
    assert [c['target_id'] for c in cues] == ['title', 'line:0', 'line:1', 'line:2']
    assert [c['quote'] for c in cues[1:]] == ['20', '25', '5']
    assert [c['character_index'] for c in cues[1:]] == [text.index('20'), text.index('25'), text.rindex('5')]


def test_explicit_process_presentation_and_narration_revisions_keep_their_intent():
    saved = {'visual_storytelling': True, 'chapters': [{'paragraphs': ['内容。']}], 'video_form': 'process_diagram'}
    assert not explanation.enabled(saved, 'process_diagram')
    assert explanation.enabled({'visual_storytelling': True, 'revision_mode': 'narration'}, 'information_motion')


def test_chart_screen_text_edit_preserves_upstream_data():
    from g2.scene_fields import apply_changes
    scene = {'component': 'data_chart', 'props': chart()}
    changed = apply_changes(scene, {'title': '随讲述展开', 'series.0.name': '月度次数'})
    assert changed['props']['series'][0]['values'] == scene['props']['series'][0]['values']
    assert changed['props']['title'] == '随讲述展开'
