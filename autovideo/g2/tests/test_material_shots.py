import copy
import json
from types import SimpleNamespace

import pytest

from g1.video_ir import ValidationFault, validate_video_ir
from g2 import explanation, material_shots, shot_director
from g2.tests.test_creative_forms import creative_ir
from g2.tests.test_shot_director import camera_runtime


def image(caption='页面', index=1):
    return {'image_path': f'assets/slideshow/image-{index:02d}.png', 'image_asset_id': f'slide_image_{index}',
        'caption': caption, 'role': 'detail', 'focus': {'width': 1440, 'height': 900, 'regions': [
            {'x1': 100, 'y1': 200, 'x2': 500, 'y2': 400, 'label': '文件区', 'style': 'box'},
            {'x1': 100, 'y1': 500, 'x2': 500, 'y2': 800, 'label': '提问区', 'style': 'underline'}]}}


def pipeline(tmp_path):
    return SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda path, data: path.write_text(json.dumps(data)))


def scene():
    return {'id': 'page_1', 'component': 'object_showcase', 'props': {'title': '工作台', 'text': '先看文件，再看问题。',
        'images': [image()], 'focus_indices': [0]}, 'narration_segment_ids': ['n1', 'n2'],
        'beats': [{'id': 'entry', 'action': 'reveal', 'target_id': 'scene',
                   'trigger': {'segment_id': 'n1', 'boundary': 'start', 'offset_ms': 0}}]}


def speech():
    return [{'id': 'n1', 'text': '先看整体，文件区显示研究报告。接着看提', 'start_ms': 100, 'end_ms': 6100},
            {'id': 'n2', 'text': '问区，这里填写问题。最后回到整体。', 'start_ms': 6500, 'end_ms': 13000}]


def test_planner_receives_existing_material_labels_with_no_extra_planning_call():
    group = {'id': 'p1', 'text': '先看文件区，再看提问区。', 'image_materials': material_shots.describe([image()])}
    messages = explanation.messages({'title': '页面', 'groups': [group]})
    payload = json.loads(messages[1]['content'])
    assert payload['groups'][0]['image_materials'][0]['regions'][1] == {'region_index': 1, 'label': '提问区'}
    assert 'image_path' not in json.dumps(payload['groups'])
    assert 'material_cues' in payload['required_schema']['properties']['pages']['items']['properties']
    assert '不改写旁白' in messages[0]['content']


def test_regions_follow_phrases_across_tts_splits_and_recompile_without_duplicate_beats():
    s, rows = scene(), speech()
    hints = [{'image_index': 0, 'region_index': 0, 'phrase': '文件区显示'},
             {'image_index': 0, 'region_index': 1, 'phrase': '提问区'}]
    plan = material_shots.compose(s, rows, 'explain', hints, '最后回到整体')
    cues = {c['targets'][0]: next(b for b in s['beats'] if b['id'] == c['beat_id']) for c in plan['cues']}
    assert cues['region:0:1']['trigger'] == {'segment_id': 'n1', 'boundary': 'start',
        'offset_ms': int(rows[0]['text'].index('提')/len(rows[0]['text'])*6000)}
    assert next(b for b in s['beats'] if b['id'] == plan['summary_beat_id'])['trigger']['segment_id'] == 'n2'
    before = copy.deepcopy(s['beats'])
    assert material_shots.compose(s, rows, 'explain', hints, '最后回到整体') == plan
    assert s['beats'] == before


def test_older_materials_use_existing_labels_or_spoken_position_without_model_calls(tmp_path):
    s = scene()
    before = copy.deepcopy(s['props']['images'])
    shot_director.attach({'scenes': [s]}, {'production_recipe': 'ai_news'}, pipeline(tmp_path), {'rows': speech()})
    assert s['props']['images'] == before
    assert [c['targets'] for c in s['props']['shot_plan']['cues']] == [['region:0:0'], ['region:0:1']]
    assert s['props']['shot_plan']['profile'] == 'brief'
    assert json.loads((tmp_path/'shot-director.json').read_text())['extra_model_calls'] == 0


def test_mixed_scene_compilation_preserves_images_and_binds_model_hints(tmp_path):
    pic = image()
    text = ''.join(row['text'] for row in speech())
    groups = [{'id': 'p1', 'title': '工作台', 'text': text, 'segment_ids': ['n1', 'n2'], 'image_indices': [0]}]
    pages = [{'id': 'p1', 'component': 'concept_reveal', 'props': {'title': '工作台', 'lines': ['文件与问题']},
              'material_cues': [{'image_index': 0, 'region_index': 1, 'phrase': '提问区'}]}]
    board = explanation.compile_storyboard(pipeline(tmp_path), {'groups': groups, 'pages': pages},
        {'rows': speech()}, {'image_materials': [pic]}, [{'id': pic['image_asset_id'], 'path': pic['image_path']}])
    result = board['scenes'][0]
    assert result['component'] == 'object_showcase'
    assert result['props']['images'][0] == pic
    assert [cue['targets'] for cue in result['props']['shot_plan']['cues']] == [['region:0:1']]
    assert result['narration_segment_ids'] == ['n1', 'n2']


def test_ir_accepts_only_existing_image_regions_and_retiming_keeps_beat_references(tmp_path):
    ir, root = creative_ir(tmp_path, 'object_showcase')
    s = ir['scenes'][0]
    s['props']['images'][0]['focus'] = image()['focus']
    rows = ir['narration']['segments']
    shot_director.attach({'scenes': [s]}, {}, pipeline(tmp_path), {'rows': rows})
    original = copy.deepcopy(s['props']['shot_plan'])
    compiled = validate_video_ir(ir, root)
    for beat in s['beats'][1:]:
        beat['trigger']['offset_ms'] = 100
    after = validate_video_ir(ir, root)
    assert after['scenes'][0]['props']['shot_plan'] == original
    assert after['scenes'][0]['beats'] != compiled['scenes'][0]['beats']
    s['props']['shot_plan']['cues'][0]['targets'] = ['region:0:2']
    with pytest.raises(ValidationFault, match='SHOT_REFERENCE'):
        validate_video_ir(ir, root)


def test_replanning_a_material_scene_keeps_its_registered_pictures(tmp_path):
    p = pipeline(tmp_path)
    s = scene()
    s['purpose'] = '工作台'
    s['props']['shot_plan'] = {'version': 1, 'profile': 'brief', 'closing': 'hold', 'cues': []}
    s['props']['scene_flow'] = {'version': 1, 'profile': 'brief', 'entry': 'carry',
        'paragraph_ids': ['card_1'], 'part_index': 2, 'part_count': 3}
    captured = []
    def generate(stage, messages, schema, check, context):
        captured.append(context)
        return {'pages': [{'id': s['id'], 'component': 'concept_reveal', 'props': {'title': '工作台', 'lines': ['文件与提问']},
                          'material_cues': [{'image_index': 0, 'region_index': 1, 'phrase': '提问区'}]}]}
    p.generate_validated_plan = generate
    result = explanation.replan_scene(p, s, {'title': '页面'}, {'narration': {'segments': speech()}}, '先看提问区')
    assert result['props']['images'] == s['props']['images']
    assert result['props']['shot_plan']['cues']
    assert result['props']['scene_flow'] == s['props']['scene_flow']
    assert result['props']['shot_plan']['closing'] == 'hold'
    assert len(captured) == 1
    assert captured[0]['groups'][0]['image_materials'][0]['regions']


def test_a_reused_page_only_visits_the_regions_selected_for_this_paragraph():
    s = scene()
    plan = material_shots.compose(s, speech(), 'balanced',
        [{'image_index': 0, 'region_index': 1, 'phrase': '提问区'}])
    assert [cue['targets'] for cue in plan['cues']] == [['region:0:1']]
    # A stale phrase still permits automatic use of the older material labels.
    fallback = material_shots.compose(s, speech(), 'balanced',
        [{'image_index': 0, 'region_index': 1, 'phrase': '未使用的旧讲稿短语'}])
    assert len(fallback['cues']) == 2


def material_scene(pair=False):
    return {'component': 'material_collage' if pair else 'object_showcase', 'start_frame': 0, 'end_frame': 600,
        'props': {'images': [image('左图'), image('右图', 2)], 'focus_indices': [0, 1],
            'shot_plan': {'version': 1, 'profile': 'balanced', 'cues': [
                {'beat_id': 'left', 'targets': ['region:0:0']}, {'beat_id': 'right', 'targets': ['region:1:1']}]}},
        'beats': [{'id': 'left', 'frame': 80}, {'id': 'right', 'frame': 300}]}


def test_image_switch_crossfades_and_summary_keeps_last_picture(camera_runtime):
    result = camera_runtime(material_scene(), [80, 300, 306, 320, 590, 320])
    material = result['material']
    assert material[0]['images'] == [0]
    assert material[1]['previous'] == [0] and material[1]['images'] == [1]
    assert 0 < material[2]['blend'] < 1
    assert material[3]['blend'] == 1
    assert material[4]['shot']['kind'] == 'summary' and material[4]['images'] == [1]
    assert material[3] == material[-1]


def test_comparison_keeps_both_pictures_in_view_while_only_spoken_region_zooms(camera_runtime):
    s = material_scene(pair=True)
    rects = {'region:0:0': {'x': 150, 'y': 150, 'width': 180, 'height': 100}}
    result = camera_runtime(s, [110, 330, 590], rects)
    assert all(state['images'] == [0, 1] for state in result['material'])
    assert result['material'][0]['weights'][0] > result['material'][0]['weights'][1]
    assert result['material'][1]['weights'][0] < result['material'][1]['weights'][1]
    assert result['material'][-1]['weights'] == [.5, .5]
    assert all(abs(sum(state['weights'])-1)<1e-6 for state in result['material'])
    assert result['cameras'][0]['pose']['zoom'] > 1
    assert result['cameras'][1]['pose']['zoom'] == 1
    assert result['cameras'][-1]['pose'] == {'zoom': 1, 'x': 0, 'y': 0}


def test_dom_region_projection_preserves_image_aspect_ratio_and_letterbox(camera_runtime):
    result = camera_runtime(material_scene(), [], image=image())['image']
    assert result['w'] == 800 and result['h'] == 500
    assert result['x'] == 100 and result['y'] == 0
    box = result['rects']['region:0:0']
    assert box == {'x': 180, 'y': 100, 'width': 320, 'height': 100}


def test_switch_from_one_picture_to_a_dense_pair_keeps_the_stage_filled(camera_runtime):
    s = material_scene()
    s['props']['images'].append(image('第三张', 3))
    s['props']['focus_indices'] = [0, 1, 2]
    s['props']['shot_plan']['cues'][1]['targets'] = ['region:1:0', 'region:2:0']
    states = camera_runtime(s, [300, 306, 320])['material']
    assert all(state['images'] == [1, 2] and sum(state['weights']) == 1 for state in states)
