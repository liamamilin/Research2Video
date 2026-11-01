import copy
import json
from types import SimpleNamespace

import pytest

from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import validate_video_ir
from g2 import explanation, information_motion, hybrid_cards


def props():
    return {'title': '先建环境，再启动', 'label': '终端命令', 'lines': [
        {'text': 'python -m venv .venv', 'phrase': '创建独立环境', 'annotation': '先建立项目自己的环境'},
        {'text': 'source .venv/bin/activate', 'phrase': '激活环境', 'annotation': '后续命令使用这个环境'},
        {'text': 'python app.py --port 8011', 'phrase': '启动应用', 'annotation': '启动本地应用'}]}


def test_tutorial_code_preserves_literal_commands_and_uses_the_shared_compiler(tmp_path):
    p = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda path, data: path.write_text(json.dumps(data)))
    speech = [{'id': 'n1', 'text': '先创建独立环境，再激活环', 'start_ms': 0, 'end_ms': 4500},
              {'id': 'n2', 'text': '境，最后启动应用。', 'start_ms': 4700, 'end_ms': 9000}]
    group = {'id': 'code1', 'title': '启动', 'text': ''.join(s['text'] for s in speech),
             'segment_ids': ['n1', 'n2'], 'image_indices': []}
    page = {'id': 'code1', 'component': 'code_demo', 'props': props()}
    plan = {'pages': [page], 'groups': [group]}
    original = copy.deepcopy(page['props'])
    explanation.validate({'pages': plan['pages']}, {'title': '教程', 'groups': [group]})
    board = explanation.compile_storyboard(p, plan, {'rows': speech}, {}, [])
    scene = board['scenes'][0]
    assert scene['component'] == 'code_demo' and scene['props'] == original
    assert scene['beats'][2]['trigger']['segment_id'] == 'n1'
    assert scene['beats'][2]['trigger']['offset_ms'] == int(speech[0]['text'].index('激活') / len(speech[0]['text']) * 4500)
    assert scene['beats'][3]['trigger']['segment_id'] == 'n2'
    assert hybrid_cards.still_page(scene) is None


def test_code_scene_is_a_registered_explanation_and_keeps_diff_text(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir['presentation'] = {'preset': 'explanation'}
    ir['style']['preset'] = 'minimal_light'
    p = props()
    p['lines'][0]['change'] = 'remove'
    p['lines'][1]['change'] = 'add'
    ir['scenes'][0].update(component='code_demo', props=p)
    ir['scenes'][0]['beats'] = [
        {'id': 'line1', 'action': 'reveal', 'target_id': 'line:0',
         'trigger': {'segment_id': ir['narration']['segments'][0]['id'], 'boundary': 'start', 'offset_ms': 0}}]
    assert validate_video_ir(ir, root)['scenes'][0]['props'] == p


def test_long_code_is_split_by_the_existing_layout_feedback():
    p = props()
    p['lines'] = [{'text': 'x' * 100, 'phrase': '先看'} for _ in range(6)]
    with pytest.raises(information_motion.InformationFault, match='fewer lines'):
        information_motion.validate_text_fit({'id': 'code', 'component': 'code_demo', 'props': p})


def test_existing_matrix_planning_request_exposes_code_without_an_extra_stage():
    messages = explanation.messages({'title': '教程', 'groups': [{'id': 'p1', 'text': '执行 python app.py。'}]})
    payload = json.loads(messages[1]['content'])
    assert 'code_demo' in payload['component_props_schemas']
    assert '不为装饰编写新代码' in messages[0]['content']


def test_code_text_can_be_revised_without_changing_the_spoken_anchor():
    from g2.scene_fields import apply_changes

    original = {'component': 'code_demo', 'props': props()}
    changed = apply_changes(original, {'lines.2.text': 'python app.py --port 8012'})
    assert original['props']['lines'][2]['text'].endswith('8011')
    assert changed['props']['lines'][2]['text'].endswith('8012')
    assert changed['props']['lines'][2]['phrase'] == original['props']['lines'][2]['phrase']


def test_assignment_change_uses_the_original_code_instead_of_a_price_card():
    text = '原来写的是 `PORT = 8010`，本次改成 `PORT = 8011`。'
    page = {'id': 'p1', 'component': 'price_change', 'props': {'title': '端口修改',
        'rows': [{'label': 'PORT', 'before': '8010', 'after': '8011', 'change': '1', 'direction': 'up'}]}}
    explanation.validate({'pages': [page]}, {'title': '教程', 'groups': [{'id': 'p1', 'text': text}]})
    assert page['component'] == 'code_demo'
    assert [line['text'] for line in page['props']['lines']] == ['PORT = 8010', 'PORT = 8011']
    assert [line['change'] for line in page['props']['lines']] == ['remove', 'add']
    assert 'rows' not in page['props']


def test_next_step_keeps_earlier_commands_without_repeating_their_focus(tmp_path):
    p = SimpleNamespace(ARTIFACTS=tmp_path, write_json=lambda path, data: path.write_text(json.dumps(data)))
    first, second = props()['lines'][:2]
    pages = [{'id': f'p{i}', 'component': 'code_demo', 'props': {'title': title, 'lines': [line]},
              'entities': [{'name': title, 'targets': ['line:0']}]} for i, title, line in
             [(1, '创建环境', first), (2, '激活环境', second)]]
    groups = [{'id': 'p1', 'chapter_id': 'c1', 'title': '创建环境', 'text': '第一步创建独立环境。',
               'segment_ids': ['n1'], 'image_indices': []},
              {'id': 'p2', 'chapter_id': 'c1', 'title': '激活环境', 'text': '第二步激活环境，保留刚才的命令。',
               'segment_ids': ['n2'], 'image_indices': []}]
    board = explanation.compile_storyboard(p, {'pages': pages, 'groups': groups, 'direction_version': 4},
        {'rows': [{'id': 'n1', 'text': groups[0]['text'], 'start_ms': 0, 'end_ms': 4000},
                  {'id': 'n2', 'text': groups[1]['text'], 'start_ms': 4100, 'end_ms': 9000}]}, {}, [])
    scene = board['scenes'][1]
    assert [line['text'] for line in scene['props']['lines']] == [first['text'], second['text']]
    assert 'phrase' not in scene['props']['lines'][0]
    assert [beat['target_id'] for beat in scene['beats']] == ['title', 'line:1']
    assert scene['props']['story']['entities'][0]['targets'] == ['line:1']
    assert pages[1]['entities'][0]['targets'] == ['line:0']


def test_media_revision_uses_new_word_anchors_before_render(tmp_path, monkeypatch):
    import shutil
    from g2 import audience_copy, caption_alignment, scene_revision
    from g3.tests.test_scene_media import ir_for

    parent = tmp_path / 'parent' / 'g2-run'
    (parent / 'artifacts').mkdir(parents=True)
    ir = ir_for(parent / 'fixtures')
    (parent / 'fixtures/video-ir-v0.2.json').write_text(json.dumps(ir))
    (parent / 'artifacts/final-narration-plan.json').write_text(json.dumps({'segments': ir['narration']['segments']}))
    (parent / 'artifacts/information-timing.json').write_text(json.dumps({'cues': [
        {'scene_id': 's1', 'beat_id': 'b2', 'segment_id': 'n1', 'character_index': 1}]}))
    settings = {'operation_type': 'media_revision', 'voice_id': 'zf_xiaoxiao',
                'parent_voice_id': 'zf_xiaoxiao', 'parent_job_id': 'parent', 'video_form': 'information_motion'}
    intent = tmp_path / 'intent.json'
    intent.write_text(json.dumps(settings))
    monkeypatch.setenv('AUTOVIDEO_G2_INTENT_FILE', str(intent))
    monkeypatch.setenv('AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR', str(parent.parent))
    monkeypatch.setattr(audience_copy, 'require_copy', lambda *args: None)
    monkeypatch.setattr(caption_alignment, 'prepare', lambda *args: {'word_evidence': [
        {'segment_id': 'n1', 'items': [{'text': word, 'start_ms': start, 'end_ms': start + 100}
                                    for word, start in zip('测试旁白', (0, 900, 1100, 1500))]}]})
    child = tmp_path / 'child'
    rendered = []
    p = SimpleNamespace(RUN_ROOT=child, ARTIFACTS=child / 'artifacts', LOGS=child / 'logs',
        FIXTURE_ROOT=child / 'fixtures', ASSETS=child / 'fixtures/assets',
        IR_PATH=child / 'fixtures/video-ir-v0.2.json', OUTPUT_SPEED=1, VOICE='zf_xiaoxiao',
        CALL_RECORDS=[], _copy_tree_without_symlinks=shutil.copytree,
        write_json=lambda path, data: path.write_text(json.dumps(data)), skip_step=lambda *args: None,
        run_step=lambda label, fn, *args: fn(*args),
        render=lambda compiled, **kwargs: rendered.append(compiled), verify_media=lambda *args: {'status': 'PASS'},
        write_call_summary=lambda: None, write_manifest=lambda **kwargs: None)
    scene_revision.run_revision(p)
    beat = next(beat for beat in rendered[0]['scenes'][0]['beats'] if beat['id'] == 'b2')
    assert beat['frame'] == 33  # 200ms segment start + the measured 900ms word offset.
    assert json.loads((parent / 'fixtures/video-ir-v0.2.json').read_text())['scenes'][0]['beats'][1]['trigger']['offset_ms'] == 300
