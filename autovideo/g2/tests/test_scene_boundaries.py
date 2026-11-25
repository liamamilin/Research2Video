import copy
from types import SimpleNamespace

import pytest

from g2 import explanation
from g2.scene_boundaries import reconcile


def fixture(texts, cut, left, right):
    rows = [{'id': f'n{i}', 'text': text, 'start_ms': i * 4100, 'end_ms': i * 4100 + 4000}
            for i, text in enumerate(texts)]
    groups = []
    for i, part in enumerate((rows[:cut], rows[cut:])):
        groups.append({'id': f'p{i}', 'chapter_id': 'c1', 'title': '演示',
                       'paragraph_ids': ['paragraph'], 'part_index': i + 1, 'part_count': 2,
                       'image_indices': [], 'segment_ids': [r['id'] for r in part],
                       'text': ''.join(r['text'] for r in part), 'narration': copy.deepcopy(part)})
    return {'groups': groups, 'pages': [dict(id=f'p{i}', **page) for i, page in enumerate((left, right))]}, {'rows': rows}


def concept(phrase):
    return {'component': 'concept_reveal', 'props': {'title': '准备', 'lines': ['准备环境'],
            'visuals': [{'target': 'line:0', 'icon': 'settings', 'reveal_phrase': phrase}]}}


def code(phrase):
    return {'component': 'code_demo', 'props': {'title': '执行', 'lines': [
            {'text': 'python build.py', 'phrase': phrase}]}}


def test_early_code_instruction_moves_its_whole_sentence_without_editing_story_or_pages():
    plan, speech = fixture(['环境准备好了。', '运行构建命令，', '接着查看构建结果。'], 2,
                           concept('环境准备好了'), code('运行构建命令'))
    original = copy.deepcopy((plan, speech))
    updated, changes = reconcile(plan, speech)
    assert [g['segment_ids'] for g in updated['groups']] == [['n0'], ['n1', 'n2']]
    assert updated['pages'] == plan['pages']
    assert (plan, speech) == original
    assert ''.join(g['text'] for g in updated['groups']) == ''.join(r['text'] for r in speech['rows'])
    assert changes[0]['boundary_ms'] == 4100
    assert reconcile(updated, speech)[1] == []


def test_chart_explanation_finishes_before_following_page_even_when_that_delays_cut():
    chart = {'component': 'data_chart', 'props': {'reveal_phrases': ['第一季', '第二季']}}
    plan, speech = fixture(['第一季增长。', '第二季回落。', '开始执行构建。', '等它运行完。'], 1,
                           chart, code('开始执行构建'))
    updated, changes = reconcile(plan, speech)
    assert [g['segment_ids'] for g in updated['groups']] == [['n0', 'n1'], ['n2', 'n3']]
    assert changes[0]['boundary_ms'] > changes[0]['previous_boundary_ms']


def test_phrase_across_tts_segments_stays_on_one_page():
    plan, speech = fixture(['准备完毕。', '启动任务，', '检查日志。', '完成。'], 2,
                           concept('准备完毕'), code('启动任务，检查日志'))
    updated, _ = reconcile(plan, speech)
    assert updated['groups'][1]['segment_ids'] == ['n1', 'n2', 'n3']


@pytest.mark.parametrize('left,right', [
    ('环境准备好了', '未在原文出现的意译'),
    ('不存在', '运行构建命令'),
    ('查看结果', '运行构建命令'),  # Interleaved page requirements.
    ('环境准备好了', '准备'),  # Repeated, ambiguous anchor.
])
def test_missing_ambiguous_or_interleaved_anchors_do_not_force_a_cut(left, right):
    plan, speech = fixture(['环境准备好了。', '准备运行构建命令。', '查看结果。准备下一步。'], 2,
                           concept(left), code(right))
    assert reconcile(plan, speech) == (plan, [])


@pytest.mark.parametrize('field,value', [('chapter_id', 'c2'), ('paragraph_ids', ['another']),
                                        ('image_indices', [1])])
def test_no_migration_across_authored_paragraph_chapter_or_material_boundary(field, value):
    plan, speech = fixture(['准备完毕。', '运行构建命令。', '查看结果。'], 2,
                           concept('准备完毕'), code('运行构建命令'))
    plan['groups'][1][field] = value
    assert reconcile(plan, speech) == (plan, [])


def test_fast_speech_does_not_create_an_unreadable_flash_page():
    plan, speech = fixture(['准备完毕。', '运行构建命令。', '查看结果。'], 2,
                           concept('准备完毕'), code('运行构建命令'))
    speech['rows'][0]['end_ms'] = 2500
    assert reconcile(plan, speech, speed=2) == (plan, [])


def test_normal_compilation_assigns_the_beat_to_its_real_phrase(tmp_path):
    plan, speech = fixture(['环境准备好了。', '运行构建命令，', '查看结果。'], 2,
                           concept('环境准备好了'), code('运行构建命令'))
    original = copy.deepcopy(plan)
    outputs = {}
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda path, obj: outputs.update({path.name: obj}))
    board = explanation.compile_storyboard(pipeline, plan, speech, {}, [])
    assert [s['narration_segment_ids'] for s in board['scenes']] == [['n0'], ['n1', 'n2']]
    cue = next(b for b in board['scenes'][1]['beats'] if b['target_id'] == 'line:0')
    assert cue['trigger']['segment_id'] == 'n1'
    assert cue['trigger']['offset_ms'] == 0
    assert outputs['scene-boundaries.json']['changes']
    assert plan == original
