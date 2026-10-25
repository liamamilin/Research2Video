import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import creative_forms as forms


def narration():
    return {'title': '理解缓存', 'segments': [{'id': f'n{i}', 'text': f'观众：第{i}条说明应当保留条件、限制与例外。'} for i in range(1, 9)],
            'chapters': [{'id': 'c1', 'title': '缓存用途', 'question': '缓存适合做什么？', 'narration_segment_ids': [f'n{i}' for i in range(1, 5)]},
                         {'id': 'c2', 'title': '缓存边界', 'question': '缓存有什么限制？', 'narration_segment_ids': [f'n{i}' for i in range(5, 9)]}]}


@pytest.mark.parametrize('form', [form for form in forms.FORMS if form != 'information_cards'])
def test_complete_narration_is_paged_in_order_without_three_page_limit(form):
    plan = forms.plan_storyboard(None, narration(), form)
    assert len(plan['scenes']) == 8
    assert [s['props']['text'] for s in plan['scenes']] == [s['text'] for s in narration()['segments']]
    assert [sid for s in plan['scenes'] for sid in s['narration_segment_ids']] == [f'n{i}' for i in range(1, 9)]
    if form == 'chat_dialogue':
        assert plan['scenes'][0]['props']['speaker'] == '观众'
        assert plan['scenes'][4]['props']['title'] == '缓存边界'
        assert plan['scenes'][4]['props']['question'] == '第5条说明应当保留条件、限制与例外。'


def creative_ir(tmp_path, form):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir.update(presentation={'preset': form})
    ir['style']['preset'] = 'minimal_light'
    board = forms.plan_storyboard(None, {'title': '测试', 'segments': [{'id': 'n1', 'text': '传输示例。'}]},
                                  'kinetic_typography' if form == 'information_cards' else form)
    ir['scenes'][0].update(board['scenes'][0])
    if form in {'image_slideshow','object_showcase','dynamic_collage'}:
        folder = root / 'assets/slideshow'; folder.mkdir()
        assets = [forms._save_image(folder, 0, b'image bytes', 'test')]
        ir['assets'].extend(assets)
        if form == 'image_slideshow':
            forms.attach_images({'scenes': ir['scenes']}, assets)
        else:
            forms.attach_materials({'scenes': ir['scenes']},assets,{'slideshow_images_resolved':[{'image_id':'fixture'}]})
    return ir, root


@pytest.mark.parametrize('form', forms.FORMS)
def test_only_explicit_trusted_presentation_allows_its_component(tmp_path, form):
    ir, root = creative_ir(tmp_path, form)
    assert validate_video_ir(ir, root)['presentation']['preset'] == form
    ir.pop('presentation')
    with pytest.raises(ValidationFault):
        validate_video_ir(ir, root)


def test_slideshow_rejects_remote_and_unregistered_image_paths(tmp_path):
    ir, root = creative_ir(tmp_path, 'image_slideshow')
    for change, code in [({'image_path': 'https://example.org/image.png'}, 'INVALID_COMPONENT_PROPS'),
                         ({'image_asset_id': 'slide_image_2'}, 'SLIDESHOW_ASSET_MISMATCH')]:
        bad = copy.deepcopy(ir); bad['scenes'][0]['props']['images'][0].update(change)
        with pytest.raises(ValidationFault) as exc:
            validate_video_ir(bad, root)
        assert exc.value.code == code
    (root / ir['assets'][1]['path']).write_bytes(b'changed')
    with pytest.raises(ValidationFault, match='ASSET_HASH_MISMATCH'):
        validate_video_ir(ir, root)


def test_generated_slideshow_uses_local_draw_things_and_pins_all_images(tmp_path, monkeypatch):
    from g2 import draw_things
    import base64
    from io import BytesIO
    buf = BytesIO(); Image.new('RGB', (1280, 720), 'green').save(buf, 'PNG')
    calls = []
    def request(url, **kwargs):
        calls.append((url, json.loads(kwargs['body'])))
        return {'images': [base64.b64encode(buf.getvalue()).decode()]}
    monkeypatch.setattr(draw_things, '_read_json', request)
    inp = tmp_path / 'input'; inp.mkdir(); (inp / 'input.json').write_text('{}')
    artifacts = tmp_path / 'artifacts'; artifacts.mkdir()
    pipeline = SimpleNamespace(PROJECT_INPUT=inp / 'source.txt', ASSETS=tmp_path / 'assets', ARTIFACTS=artifacts,
                               write_json=lambda p,v: p.write_text(json.dumps(v)))
    images = forms.prepare_images(pipeline, narration())
    assert len(images) == len(calls) == 3
    assert all(url == 'http://127.0.0.1:7860/sdapi/v1/txt2img' for url, _ in calls)
    assert all(hashlib.sha256((tmp_path / a['path']).read_bytes()).hexdigest() == a['sha256'] for a in images)


def test_more_images_than_phrases_preserves_every_image_in_order():
    board = {'scenes': [{'props': {}}, {'props': {}}]}
    images = [{'id': f'slide_image_{i}', 'path': f'assets/slideshow/image-{i:02d}.png'} for i in range(1, 13)]
    forms.attach_images(board, images)
    assert [a['image_asset_id'] for s in board['scenes'] for a in s['props']['images']] == [a['id'] for a in images]


def test_slideshow_explicit_picture_numbers_override_equal_distribution():
    texts = ['先看三张图片。', '第一张是小山。', '第二张是茶杯。', '第三张是河岸。', '它们都是原创插画。']
    board = {'scenes': [{'props': {'text': text}} for text in texts]}
    images = [{'id': f'slide_image_{i}', 'path': f'assets/slideshow/image-{i:02d}.png'} for i in range(1, 4)]
    forms.attach_images(board, images)
    assert [s['props']['images'][0]['image_asset_id'] for s in board['scenes']] == ['slide_image_1', 'slide_image_1', 'slide_image_2', 'slide_image_3', 'slide_image_3']


def test_dialogue_keeps_the_last_question_and_speaker_across_split_phrases():
    result = forms.plan_storyboard(None, {'title': '缓存', 'segments': [{'id': f'n{i}', 'text': text} for i, text in enumerate([
        '观众：缓存会过期吗？', '讲解：缓存可能过期。', '使用时应检查更新条件。', '观众：原始数据还需要吗？', '讲解：仍然需要保留。'
    ])]}, 'chat_dialogue')
    props = [s['props'] for s in result['scenes']]
    assert [p['speaker'] for p in props] == ['观众', '讲解', '讲解', '观众', '讲解']
    assert props[2]['question'] == '缓存会过期吗？'
    assert props[4]['question'] == '原始数据还需要吗？'


def test_creative_ir_rejects_text_rewrite_and_incomplete_phrase_coverage(tmp_path):
    ir, root = creative_ir(tmp_path, 'kinetic_typography')
    ir['scenes'][0]['props']['text'] = '擅自改写。'
    validate_video_ir(ir, root)
    ir['scenes'][0]['props']['text'] = '传输示例。'
    ir['scenes'][0]['narration_segment_ids'] = ['n2']
    with pytest.raises(ValidationFault, match='CREATIVE_NARRATION_COVERAGE'):
        validate_video_ir(ir, root)


@pytest.mark.parametrize('preset', [[], {}])
def test_invalid_presentation_types_raise_validation_fault_not_type_error(tmp_path, preset):
    ir, root = creative_ir(tmp_path, 'kinetic_typography')
    ir['presentation']['preset'] = preset
    with pytest.raises(ValidationFault, match='SCHEMA_VALIDATION'):
        validate_video_ir(ir, root)


@pytest.mark.parametrize('form',['object_showcase','dynamic_collage'])
def test_material_ir_rejects_unregistered_images_and_out_of_range_focus(tmp_path,form):
    ir,root=creative_ir(tmp_path,form)
    bad=copy.deepcopy(ir);bad['scenes'][0]['props']['images'][0]['image_asset_id']='slide_image_2'
    with pytest.raises(ValidationFault,match='SLIDESHOW_ASSET_MISMATCH'):validate_video_ir(bad,root)
    bad=copy.deepcopy(ir);bad['scenes'][0]['props']['focus_indices']=[1]
    with pytest.raises(ValidationFault,match='MATERIAL_IMAGE_INDEX'):validate_video_ir(bad,root)


def test_material_assignment_preserves_all_twelve_images_and_supplied_captions():
    board={'scenes':[{'props':{'text':'第一段。'}},{'props':{'text':'第二段。'}}]}
    assets=[{'id':f'slide_image_{i+1}','path':f'assets/slideshow/image-{i+1:02d}.png'} for i in range(12)]
    saved={'slideshow_images_resolved':[{'image_id':str(i)} for i in range(12)],
           'image_materials':[{'image_id':'0','caption':'用户提供说明','role':'overall'}]}
    result=forms.attach_materials(board,assets,saved)
    assert [index for scene in result['scenes'] for index in scene['props']['focus_indices']]==list(range(12))
    assert result['scenes'][0]['props']['images'][0]['caption']=='用户提供说明'
    assert result['scenes'][0]['props']['images'][1]['caption']==''


@pytest.mark.parametrize('form', ['object_showcase', 'dynamic_collage'])
def test_mapped_screenshot_appears_only_in_chosen_paragraph_after_sentence_splitting(form):
    from g2.authored_script import split_script
    paragraphs = ['先介绍背景。这一段没有配图。', '功能页面在这里展示。它支持逐句讲解。', '最后总结。']
    segments = [{'id': f'c1_n{i+1}', 'text': text} for i, text in enumerate(
        text for paragraph in paragraphs for text in split_script(paragraph))]
    board = forms.plan_storyboard(None, {'title': '功能说明', 'segments': segments}, form)
    assets = [{'id': 'slide_image_1', 'path': 'assets/slideshow/image-01.png'}]
    saved = {'input_mode': 'narration', 'chapters': [{'title': '功能说明', 'paragraphs': paragraphs}],
             'slideshow_images_resolved': [{'image_id': 'fixture'}], 'image_materials': [
                 {'image_id': 'fixture', 'caption': '官方功能页面', 'role': 'detail',
                  'placements': [{'chapter_index': 0, 'paragraph_index': 1}]}]}
    result = forms.attach_materials(board, assets, saved)
    assert [s['component'] for s in result['scenes']] == ['kinetic_text', 'kinetic_text', forms.FORMS[form], forms.FORMS[form], 'kinetic_text']
    assert result['scenes'][2]['props']['images'][0]['caption'] == '官方功能页面'
    assert 'images' not in result['scenes'][0]['props']
    assert [s['props']['text'] for s in result['scenes']] == [s['text'] for s in segments]
    bad = copy.deepcopy(saved); bad['chapters'][0]['paragraphs'][0] = '修改后分句数量不同。'
    with pytest.raises(ValueError, match='分句不一致'):
        forms.attach_materials(copy.deepcopy(board), assets, bad)


@pytest.mark.parametrize('form', ['object_showcase', 'dynamic_collage'])
def test_material_preset_allows_trusted_text_for_unassigned_paragraphs(tmp_path, form):
    ir, root = creative_ir(tmp_path, form)
    ir['scenes'][0].update(component='kinetic_text', props={'title': '未配图', 'text': '传输示例。'})
    validate_video_ir(ir, root)
    ir['presentation']['preset'] = 'image_slideshow'
    with pytest.raises(ValidationFault):
        validate_video_ir(ir, root)


@pytest.mark.parametrize('form', ['object_showcase', 'dynamic_collage'])
def test_focus_coordinates_pass_through_and_ir_rejects_invalid_rectangles(tmp_path, form):
    ir, root = creative_ir(tmp_path, form)
    focus = {'width': 640, 'height': 480, 'regions': [{'x1': 100, 'y1': 100, 'x2': 900, 'y2': 700, 'label': '重点', 'style': 'circle'}]}
    ir['scenes'][0]['props']['images'][0]['focus'] = focus
    validate_video_ir(ir, root)
    bad = copy.deepcopy(ir); bad['scenes'][0]['props']['images'][0]['focus']['regions'][0]['x2'] = 50
    with pytest.raises(ValidationFault, match='MATERIAL_FOCUS_BOUNDS'):
        validate_video_ir(bad, root)
    bad = copy.deepcopy(ir); bad['scenes'][0]['props']['images'][0]['focus']['regions'][0]['x1'] = -1
    with pytest.raises(ValidationFault, match='INVALID_COMPONENT_PROPS'):
        validate_video_ir(bad, root)
