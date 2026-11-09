import base64
import copy
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from g1.video_ir import ValidationFault, validate_video_ir
from g2 import creative_forms as forms
from g2 import illustration
from g2.tests.test_creative_forms import creative_ir, narration


def assets(count):
    return [{'id': f'slide_image_{i + 1}', 'type': 'image', 'path': f'assets/slideshow/image-{i + 1:02d}.png'} for i in range(count)]


def extras(*rows):
    return [{'keywords': list(r), 'overlay': None} for r in rows]


def test_one_illustration_per_chapter_in_chapter_order():
    board = forms.plan_storyboard(None, narration(), 'illustrated_explainer')
    overlay = {'kind': 'steps', 'style': 'number', 'items': ['先看用途', '再看边界']}
    rows = [{'keywords': ['用途'], 'overlay': None}, {'keywords': ['边界', '限制'], 'overlay': overlay}]
    result = forms.attach_illustrations(board, assets(2), narration(), rows)
    assert [s['props']['image']['image_asset_id'] for s in result['scenes']] == ['slide_image_1'] * 4 + ['slide_image_2'] * 4
    assert [s['props']['chapter_index'] for s in result['scenes']] == [0] * 4 + [1] * 4
    assert result['scenes'][5]['props']['keywords'] == ['边界', '限制']
    assert 'overlay' not in result['scenes'][0]['props'] and result['scenes'][5]['props']['overlay'] == overlay
    assert all(s['component'] == 'illustration_scene' for s in result['scenes'])
    assert [s['props']['text'] for s in result['scenes']] == [s['text'] for s in narration()['segments']]


def test_uploaded_keywords_are_optional_and_must_match_chapters():
    assert forms.chapter_extras({}, 2) == extras([], [])
    assert forms.chapter_extras({'chapter_keywords': [['a'], ['b']]}, 2) == extras(['a'], ['b'])
    with pytest.raises(ValueError, match='章节关键词'):
        forms.chapter_extras({'chapter_keywords': [['只有一章']]}, 2)


@pytest.mark.parametrize('count', [1, 3])
def test_image_count_must_equal_chapter_count(count):
    board = forms.plan_storyboard(None, narration(), 'illustrated_explainer')
    with pytest.raises(ValueError, match='每章一张插画'):
        forms.attach_illustrations(board, assets(count), narration(), extras([], [], [])[:2])


def test_uploaded_path_never_falls_back_to_generic_draw_things_prompt(tmp_path):
    (tmp_path / 'input').mkdir()
    (tmp_path / 'input/input.json').write_text(json.dumps({'video_form': 'illustrated_explainer'}))
    pipeline = SimpleNamespace(PROJECT_INPUT=tmp_path / 'input/source.txt', ASSETS=tmp_path / 'assets')
    with pytest.raises(ValueError, match='每章一张已保存的插画'):
        forms.prepare_images(pipeline, narration())


OVERLAYS = {
    'compare': {'kind': 'compare', 'glyph': 'pipe', 'relation': 'big_small', 'items': [{'label': '金属', 'note': '粗水管'}, {'label': '木头'}]},
    'composition': {'kind': 'composition', 'items': ['原料', '房租', '人工'], 'total': '一杯的价格'},
    'formula': {'kind': 'formula', 'terms': ['固定开支', '杯数'], 'operators': ['÷'], 'result': '每杯的固定成本',
                'bars': [{'label': '一天80杯', 'value': 80}, {'label': '一天300杯', 'value': 300}]},
    'steps': {'kind': 'steps', 'style': 'question', 'items': ['哪笔在动', '卖多少杯', '哪个渠道']},
    'flow': {'kind': 'flow', 'source': '手', 'target': '金属', 'label': '热往外流'},
}
TEXT = '一家一天卖三百杯，一家一天卖80杯，另一家300杯。'


@pytest.mark.parametrize('kind', OVERLAYS)
def test_each_overlay_part_is_accepted_and_valid_in_the_ir(tmp_path, kind):
    assert illustration.overlay_problem(OVERLAYS[kind], TEXT) is None
    ir, root = creative_ir(tmp_path, 'illustrated_explainer')
    ir['scenes'][0]['props']['overlay'] = OVERLAYS[kind]
    assert validate_video_ir(ir, root)['scenes'][0]['props']['overlay']['kind'] == kind


@pytest.mark.parametrize('change,reason', [
    (lambda o: o.update(kind='chart'), '格式不符'),
    (lambda o: o['items'].append({'label': '第三项'}), '格式不符'),
    (lambda o: o['items'][0].update(label='超过六个字的标签'), '格式不符'),
    (lambda o: o['items'][0].update(note='温差是17度'), '数字不在本章讲稿中'),
])
def test_invalid_overlays_are_rejected_with_a_reason(change, reason):
    overlay = copy.deepcopy(OVERLAYS['compare'])
    change(overlay)
    assert reason in illustration.overlay_problem(overlay, TEXT)


def test_formula_operator_count_and_bar_numbers_are_checked():
    bad = copy.deepcopy(OVERLAYS['formula']); bad['operators'] = ['÷', '+']
    assert '运算符' in illustration.overlay_problem(bad, TEXT)
    bad = copy.deepcopy(OVERLAYS['formula']); bad['bars'][1]['value'] = 500
    assert '500' in illustration.overlay_problem(bad, TEXT)


def test_ir_rejects_unregistered_illustration_and_bad_overlays(tmp_path):
    ir, root = creative_ir(tmp_path, 'illustrated_explainer')
    assert validate_video_ir(ir, root)['scenes'][0]['component'] == 'illustration_scene'
    for change, code in [(lambda p: p['image'].update(image_asset_id='slide_image_2'), 'SLIDESHOW_ASSET_MISMATCH'),
                         (lambda p: p['image'].update(image_path='https://example.org/a.png'), 'INVALID_COMPONENT_PROPS'),
                         (lambda p: p.update(keywords=['一', '二', '三']), 'INVALID_COMPONENT_PROPS'),
                         (lambda p: p.update(overlay={'kind': 'svg', 'code': '<script/>'}), 'INVALID_COMPONENT_PROPS')]:
        bad = copy.deepcopy(ir)
        change(bad['scenes'][0]['props'])
        with pytest.raises(ValidationFault) as exc:
            validate_video_ir(bad, root)
        assert exc.value.code == code


class Fault(Exception):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}'); self.code = code


def plan(overlay=None):
    return {'cast': {'A': 'a young woman with short black hair in a teal shirt and grey jeans'},
            'chapters': [{'chapter': 1, 'scene': '{A} wipes a quiet counter', 'keywords': ['用途'], 'overlay': overlay, 'note': '擦柜台'},
                         {'chapter': 2, 'scene': '{A} hands a bag to a customer', 'keywords': [], 'overlay': OVERLAYS['flow'], 'note': '递袋子'}]}


def context():
    return {'chapters': [{'title': '缓存用途', 'text': '第一章讲稿。'}, {'title': '缓存边界', 'text': '热从手流向金属。'}]}


def test_plan_check_requires_every_chapter_and_known_cast():
    illustration.check_plan(plan(), context(), Fault)
    missing = plan(); missing['chapters'].pop()
    with pytest.raises(Fault, match='ILLUSTRATION_PLAN_CHAPTERS'):
        illustration.check_plan(missing, context(), Fault)
    stranger = plan(); stranger['chapters'][0]['scene'] = '{B} waves'
    with pytest.raises(Fault, match='ILLUSTRATION_PLAN_CAST'):
        illustration.check_plan(stranger, context(), Fault)


def test_finalize_substitutes_cast_and_drops_bad_overlay_without_failing():
    bad = {'kind': 'compare', 'glyph': 'pipe', 'relation': 'same', 'items': [{'label': '99度'}, {'label': '木头'}]}
    chapters, dropped = illustration.finalize(plan(bad), context())
    assert chapters[0]['scene'].startswith('a young woman with short black hair') and '{A}' not in chapters[0]['scene']
    assert chapters[0]['overlay'] is None and chapters[0]['keywords'] == ['用途']
    assert dropped[0]['chapter'] == 1 and '99' in dropped[0]['reason']
    assert chapters[1]['overlay'] == OVERLAYS['flow']


def png():
    out = BytesIO(); Image.new('RGB', (1344, 768), 'orange').save(out, 'PNG'); return out.getvalue()


def fake_pipeline(tmp_path):
    written = {}
    return SimpleNamespace(
        G2Fault=Fault, ASSETS=tmp_path / 'assets', ARTIFACTS=tmp_path,
        generate_validated_plan=lambda stage, builder, schema, check, ctx: (check(plan(), ctx), plan())[1],
        write_json=lambda path, value: written.__setitem__(path.name, value)), written


def two_chapter_narration():
    n = narration()
    n['segments'][4]['text'] = '热从手流向金属。'
    return n


def test_generate_draws_one_picture_per_chapter_with_the_style_pack(tmp_path, monkeypatch):
    from g2 import draw_things
    sent = []
    monkeypatch.setattr(draw_things, 'probe_draw_things', lambda *a, **k: {'ready': True, 'endpoint': 'http://127.0.0.1:7860', 'error': None})
    monkeypatch.setattr(draw_things, '_read_json', lambda url, **k: (sent.append(json.loads(k['body'])), {'images': [base64.b64encode(png()).decode()]})[1])
    pipeline, written = fake_pipeline(tmp_path)
    made, rows = illustration.generate(pipeline, two_chapter_narration())
    assert [a['path'] for a in made] == ['assets/slideshow/image-01.png', 'assets/slideshow/image-02.png']
    assert all(b['prompt'].startswith('flat vector illustration') and 'teal shirt' in b['prompt'] for b in sent)
    assert len({b['seed'] for b in sent}) == 1 and sent[0]['width'] == 1344
    assert rows[1]['overlay'] == OVERLAYS['flow'] and rows[0]['overlay'] is None
    assert written['illustrations.json']['style']['key'] == 'flat'


def test_generate_fails_with_a_reason_when_draw_things_is_off(tmp_path, monkeypatch):
    from g2 import draw_things
    monkeypatch.setattr(draw_things, 'probe_draw_things', lambda *a, **k: {'ready': False, 'endpoint': None, 'error': '无法连接'})
    pipeline, _ = fake_pipeline(tmp_path)
    with pytest.raises(Fault, match='DRAW_THINGS_UNAVAILABLE'):
        illustration.generate(pipeline, two_chapter_narration())
    assert not (tmp_path / 'assets').exists()


def test_numbers_spoken_in_chinese_numerals_count_as_present():
    formula = copy.deepcopy(OVERLAYS['formula'])
    assert illustration.overlay_problem(formula, '一家一天卖三百杯，一家一天卖八十杯。') is None
    assert '300' in illustration.overlay_problem(formula, '一家一天卖八十杯。')
