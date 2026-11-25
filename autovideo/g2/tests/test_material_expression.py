import copy
from types import SimpleNamespace

import pytest

from g2.explanation import compile_storyboard
from g2.material_expression import keep_planned_explanation


def test_established_material_can_handover_to_an_existing_explanation():
    page = {'component': 'comparison'}
    assert keep_planned_explanation(page, {'image_indices': [3]}, {3})
    assert not keep_planned_explanation(page, {'image_indices': [3]}, set())


@pytest.mark.parametrize('images,shown,cues', [
    ([], {3}, []), ([3, 4], {3, 4}, []), ([4], {3}, []),
    ([3], {3}, [{'image_index': 0, 'region_index': 0, 'phrase': '这里的按钮'}]),
])
def test_real_picture_pairs_first_encounters_and_requested_regions_are_preserved(images, shown, cues):
    assert not keep_planned_explanation({'component': 'comparison', 'material_cues': cues},
                                        {'image_indices': images}, shown)


def test_no_new_explanation_is_invented_from_a_concept_placeholder():
    assert not keep_planned_explanation({'component': 'concept_reveal'}, {'image_indices': [0]}, {0})


def test_process_handover_stays_outside_the_validated_scope():
    assert not keep_planned_explanation({'component': 'flow_diagram'}, {'image_indices': [0]}, {0})


def plan_and_speech():
    groups = [{'id': f'p{i}', 'chapter_id': 'c1', 'title': '缓存页面', 'segment_ids': [f'n{i}'],
               'text': text, 'image_indices': [0]} for i, text in enumerate([
                   '先看缓存设置页面。', '关闭缓存时每次重算，开启后复用结果。'])]
    pages = [{'id': 'p0', 'component': 'concept_reveal', 'props': {'title': '设置页面', 'lines': ['缓存设置']}},
             {'id': 'p1', 'component': 'comparison', 'headline': '关闭与开启缓存的差别', 'props': {
                 'left': {'label': '关闭缓存', 'items': ['每次重算']},
                 'right': {'label': '开启缓存', 'items': ['复用结果']}}}]
    speech = {'rows': [{'id': g['segment_ids'][0], 'text': g['text'], 'start_ms': i*6100,
                        'end_ms': i*6100+6000} for i, g in enumerate(groups)]}
    return {'groups': groups, 'pages': pages}, speech


def test_normal_compiler_keeps_real_context_then_uses_planned_meaning(tmp_path):
    plan, speech = plan_and_speech()
    original = copy.deepcopy(plan)
    records = {}
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda p, v: records.update({p.name: v}))
    saved = {'image_materials': [{'caption': '缓存设置', 'role': 'reference'}]}
    assets = [{'id': 'slide_image_1', 'path': 'assets/slideshow/image-01.png'}]
    board = compile_storyboard(pipeline, plan, speech, saved, assets)
    assert [s['component'] for s in board['scenes']] == ['object_showcase', 'comparison']
    assert board['scenes'][0]['props']['images'][0]['image_asset_id'] == 'slide_image_1'
    for side in ('left', 'right'):
        assert board['scenes'][1]['props'][side] == plan['pages'][1]['props'][side]
    assert [sid for s in board['scenes'] for sid in s['narration_segment_ids']] == ['n0', 'n1']
    assert plan == original
    assert records['material-expression.json']['scenes'][1]['established_material_explanation']


def test_picture_assigned_to_an_earlier_chart_does_not_count_as_seen(tmp_path):
    plan, speech = plan_and_speech()
    plan['pages'][0] = {'id': 'p0', 'component': 'big_number', 'props': {'title': '缓存容量', 'value': '10', 'label': '条结果'}}
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda *args: None)
    board = compile_storyboard(pipeline, plan, speech, {'image_materials': [{'caption': '缓存设置'}]},
                              [{'id': 'slide_image_1', 'path': 'assets/slideshow/image-01.png'}])
    assert [s['component'] for s in board['scenes']] == ['big_number', 'object_showcase']
