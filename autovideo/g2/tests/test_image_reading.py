import copy
import hashlib

from PIL import Image, ImageDraw

from g1.image_reading import reading_bounds
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import validate_video_ir


def test_trim_keeps_faint_marks_and_only_removes_blank_exterior(tmp_path):
    image = Image.new('RGB', (400, 300), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 60, 280, 180), fill='#222222')
    draw.line((30, 240, 360, 240), fill='#fefefe')
    path = tmp_path / 'document.png'
    image.save(path)
    original = path.read_bytes()
    result = reading_bounds(path)
    x1, y1, x2, y2 = result['box']
    assert 0 < x1 < 30 and 240 < y2 < 300
    assert y1 < 60 and x2 > 360
    for y in range(image.height):
        for x in range(image.width):
            if image.getpixel((x, y)) != (255, 255, 255):
                assert x1 <= x < x2 and y1 <= y < y2
    assert path.read_bytes() == original


def test_full_bleed_and_blank_images_keep_their_whole_frame(tmp_path):
    for color in ('white', '#202020'):
        path = tmp_path / (color.replace('#', '') + '.png')
        Image.new('RGB', (173, 91), color).save(path)
        assert reading_bounds(path)['box'] == [0, 0, 173, 91]
    assert reading_bounds(tmp_path / 'missing.png') is None


def test_transparency_uses_the_renderers_white_backdrop(tmp_path):
    image = Image.new('RGBA', (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((60, 50, 150, 140), fill=(0, 80, 30, 255))
    path = tmp_path / 'cutout.png'
    image.save(path)
    x1, y1, x2, y2 = reading_bounds(path)['box']
    assert 0 < x1 < 60 and 0 < y1 < 50 and 150 < x2 < 200 and 140 < y2 < 200


def test_compiler_derives_reading_frame_without_changing_authored_props(tmp_path):
    root = make_root(tmp_path)
    path = root / 'assets/slideshow/image-01.png'
    path.parent.mkdir()
    image = Image.new('RGB', (200, 200), 'white')
    ImageDraw.Draw(image).rectangle((20, 25, 180, 130), fill='#202020')
    image.save(path)
    ir = base_ir(root)
    ir.update(presentation={'preset': 'explanation'})
    ir['style']['preset'] = 'minimal_light'
    ir['assets'].append({'id': 'slide_image_1', 'type': 'image', 'path': 'assets/slideshow/image-01.png',
                         'origin': 'test', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    scene = ir['scenes'][0]
    scene.update(component='object_showcase', props={'title': '页面', 'text': '讲解不必重复铺满画面',
        'images': [{'image_asset_id': 'slide_image_1', 'image_path': 'assets/slideshow/image-01.png', 'caption': '页面', 'role': 'reference'}], 'focus_indices': [0]})
    scene['beats'][0]['target_id'] = 'scene'
    original = copy.deepcopy(ir)
    compiled = validate_video_ir(ir, root)
    assert ir == original
    assert compiled['scenes'][0]['image_reading'] == [reading_bounds(path)]
    assert compiled['scenes'][0]['props'] == scene['props']
    scene['props']['images'][0]['focus'] = {'width': 200, 'height': 200, 'regions': [
        {'x1': 100, 'y1': 100, 'x2': 800, 'y2': 700, 'label': '指定区域', 'style': 'box'}]}
    compiled = validate_video_ir(ir, root)
    assert 'image_reading' not in compiled['scenes'][0]
    scene['props']['images'][0].pop('focus')
    scene['props']['images'] *= 3
    assert 'image_reading' not in validate_video_ir(ir, root)['scenes'][0]
