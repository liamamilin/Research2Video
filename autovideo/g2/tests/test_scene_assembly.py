import copy
import json
from types import SimpleNamespace

import pytest

from g2 import explanation
from g2.scene_assembly import arrange, connect
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import validate_video_ir


def material():
    return {'image_index': 0, 'caption': '工作台', 'regions': [{'region_index': 0, 'label': '文件区'}]}


def inputs(durations, paragraphs, images=None, chapters=None, texts=None):
    rows, at = [], 0
    for i, duration in enumerate(durations):
        rows.append({'id': f'n{i}', 'text': texts[i] if texts else f'讲解{i}。',
                     'start_ms': at, 'end_ms': at+duration*1000})
        at += duration*1000+100
    groups, offset = [], 0
    for i, count in enumerate(paragraphs):
        local = rows[offset:offset+count]
        groups.append({'id': f'card_{i}', 'chapter_id': chapters[i] if chapters else 'c1',
            'title': '工作台', 'segment_ids': [r['id'] for r in local],
            'text': ''.join(r['text'] for r in local), 'narration': local, 'overview': [],
            'image_indices': images[i] if images else [],
            'image_materials': [material()] if images and images[i] else []})
        offset += count
    return groups, {'rows': rows}


def test_long_paragraph_splits_at_existing_speech_boundary_without_rewriting():
    groups, speech = inputs([6]*8, [8], [[0]])
    original = copy.deepcopy(groups)
    units, report = arrange(groups, speech, {})
    assert len(units) == 3
    assert [sid for u in units for sid in u['segment_ids']] == groups[0]['segment_ids']
    assert ''.join(u['text'] for u in units) == groups[0]['text']
    assert all(u['image_indices'] == [0] for u in units)
    assert [u['part_index'] for u in units] == [1, 2, 3]
    assert all(u['part_count'] == 3 for u in units)
    assert groups == original
    assert {u['operation'] for u in report['units']} == {'split'}


def test_short_compatible_paragraphs_merge_and_keep_all_origins():
    groups, speech = inputs([2, 3, 2], [1, 1, 1])
    units, report = arrange(groups, speech, {})
    assert len(units) == 1
    assert units[0]['paragraph_ids'] == ['card_0', 'card_1', 'card_2']
    assert units[0]['segment_ids'] == ['n0', 'n1', 'n2']
    assert report['units'][0]['operation'] == 'merge'


@pytest.mark.parametrize('images,chapters', [([[0], [1]], ['c1', 'c1']),
                                           ([[], []], ['c1', 'c2'])])
def test_changes_of_material_or_chapter_keep_separate_units(images, chapters):
    groups, speech = inputs([2, 2], [1, 1], images, chapters)
    assert len(arrange(groups, speech, {})[0]) == 2


def test_split_parts_do_not_merge_into_neighboring_paragraphs():
    groups, speech = inputs([2]+[8]*4, [1, 4])
    units, _ = arrange(groups, speech, {})
    assert units[0]['paragraph_ids'] == ['card_0']
    assert all(u['paragraph_ids'] == ['card_1'] for u in units[1:])
    assert all(u['part_count'] == len(units)-1 for u in units[1:])


def test_very_long_single_tts_phrase_stays_whole_and_fast_short_phrases_do_not_flicker():
    groups, speech = inputs([35], [1])
    assert arrange(groups, speech, {})[0][0]['segment_ids'] == ['n0']
    groups, speech = inputs([1]*8, [8])
    assert len(arrange(groups, speech, {})[0]) == 1


def test_speed_and_available_objects_adjust_visual_budget():
    groups, speech = inputs([8]*4, [4], [[0]])
    regular = arrange(groups, speech, {})[0]
    fast = arrange(groups, speech, {'video_speed': 2})[0]
    assert len(regular) > len(fast) == 1
    groups, speech = inputs([5]*5, [5], [[0]])
    one = arrange(groups, speech, {})[0]
    groups[0]['image_materials'][0]['regions'].append({'region_index': 1, 'label': '提问区'})
    assert len(arrange(groups, speech, {})[0]) <= len(one)


def test_measured_planner_still_calls_existing_model_once_per_chapter(tmp_path, monkeypatch):
    groups, speech = inputs([2, 2]+[8]*4, [1, 1, 4], chapters=['c1', 'c1', 'c2'])
    monkeypatch.setattr(explanation, 'paragraph_groups', lambda *args: groups)
    calls = []
    def generate(label, messages, schema, check, context):
        calls.append((label, context))
        return {'pages': [{'id': g['id'], 'component': 'concept_reveal',
                          'props': {'title': '工作台', 'lines': ['先看对象']}} for g in context['groups']]}
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, generate_validated_plan=generate,
        write_json=lambda path, value: path.write_text(json.dumps(value)))
    plan = explanation.plan_storyboard(pipeline, {'title': '自动组织'}, {}, speech)
    assert [label for label, _ in calls] == ['c1-explanation', 'c2-explanation']
    assert calls[0][1]['groups'][0]['paragraph_ids'] == ['card_0', 'card_1']
    assert len(calls[1][1]['groups']) == 2
    assert calls[1][1]['chapter_context'][0]['text'] == groups[2]['text']
    assert (tmp_path/'scene-assembly.json').is_file()
    board = explanation.compile_storyboard(pipeline, plan, speech, {}, [])
    assert [s['props']['scene_flow']['entry'] for s in board['scenes']] == ['cut', 'cut', 'dissolve']


def test_only_same_picture_continues_camera_and_last_part_returns_to_overview():
    group = {'chapter_id': 'c1', 'paragraph_ids': ['card_1'], 'part_index': 1, 'part_count': 1}
    image = {'image_path': 'assets/page.png'}
    def scene(component='object_showcase', images=None):
        return {'component': component, 'props': {'images': images or [image], 'focus_indices': [0],
            'shot_plan': {'version': 1, 'profile': 'balanced', 'cues': []}}}
    board = {'scenes': [scene(), scene(), scene(images=[{'image_path': 'assets/other.png'}]), scene()]}
    connect(board, [group, group, group, {**group, 'chapter_id': 'c2'}], {})
    assert [s['props']['scene_flow']['entry'] for s in board['scenes']] == ['cut', 'carry', 'dissolve', 'cut']
    assert board['scenes'][0]['props']['shot_plan']['closing'] == 'hold'
    assert 'closing' not in board['scenes'][1]['props']['shot_plan']


def test_flow_is_an_optional_runtime_extension(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    flow = {'version': 1, 'profile': 'balanced', 'entry': 'dissolve',
            'paragraph_ids': ['card_1', 'card_2'], 'part_index': 1, 'part_count': 1}
    ir['scenes'][0]['props']['scene_flow'] = flow
    assert validate_video_ir(ir, root)['scenes'][0]['props']['scene_flow'] == flow
