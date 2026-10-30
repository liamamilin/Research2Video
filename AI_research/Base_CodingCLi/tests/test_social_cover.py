from pathlib import Path
import subprocess

from PIL import Image, ImageDraw
import pytest

from core.social import cover
from web.services.bilibili import ffmpeg_binary

FFMPEG = ffmpeg_binary()


def test_wrap_never_starts_a_line_with_punctuation():
    draw = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    face = cover.font(100)
    for text in ('ChatGPT 订阅和 API 的账，到底怎么算才不吃亏', '这是一个很长很长的标题，用来检查换行后逗号不会出现在行首。'):
        lines = cover.wrap(draw, text, face, 900)
        assert ''.join(lines) == text
        assert all(line[0] not in cover.NO_LINE_START for line in lines[1:])


def test_wrap_keeps_model_names_and_prices_readable():
    draw = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    face = cover.font(86)
    for text in ('两粒磁珠植入肌肉，MIT想让义肢直接读懂肌肉的机械动作', 'DeepSeek新版Flash：约$0.2跑一个编程任务'):
        lines = cover.wrap(draw, text, face, 880)
        assert ''.join(lines) == text
        for token in ('MIT', 'DeepSeek', 'Flash', '$0.2'):
            if token in text:
                assert any(token in line for line in lines)
        assert all(draw.textlength(line, font=face) <= 880 for line in lines)


def test_compose_is_full_size_and_long_titles_stay_within_three_lines():
    frame = Image.new('RGB', (1920, 929), (200, 220, 210))
    image = cover.compose(frame, '很长的标题' * 30)
    assert image.size == cover.SIZE


@pytest.mark.parametrize('title', ['两粒磁珠植入肌肉，MIT想让义肢直接读懂肌肉的机械动作', 'DeepSeek新版Flash：约$0.2跑一个编程任务'])
def test_portrait_title_survives_profile_square_crop_and_frame_keeps_both_edges(title):
    frame = Image.new('RGB', (1920, 1080), (220, 230, 220))
    paint = ImageDraw.Draw(frame)
    paint.rectangle((0, 100, 250, 980), fill=(255, 0, 0))
    paint.rectangle((1670, 100, 1919, 980), fill=(0, 0, 255))
    image = cover.compose_portrait(frame, title)
    assert image.size == (1080, 1440)
    # Both content edges remain in a narrow profile tile; the text stays inside the central square too.
    square = image.crop((0, 180, 1080, 1260))
    colors = square.getcolors(square.width * square.height)
    assert any(color == (255, 0, 0) and count > 1000 for count, color in colors)
    assert any(color == (0, 0, 255) and count > 1000 for count, color in colors)
    ink = image.copy().convert('RGB')
    bounds = ink.point(lambda p: 255 if p < 100 else 0).getbbox()
    assert bounds and bounds[0] >= 80 and bounds[2] <= 1000 and bounds[1] >= 180 and bounds[3] <= 1260


def test_busiest_skips_black_and_flat_frames(tmp_path):
    black = Image.new('RGB', (320, 180), (0, 0, 0))
    flat = Image.new('RGB', (320, 180), (240, 240, 240))
    busy = Image.new('RGB', (320, 180), (240, 240, 240))
    ImageDraw.Draw(busy).rectangle((20, 20, 300, 90), fill=(30, 90, 60))
    paths = []
    for index, image in enumerate((black, flat, busy)):
        paths.append(tmp_path / f'{index}.jpg')
        image.save(paths[-1])
    chosen = cover.busiest(paths)
    assert chosen.getpixel((160, 50)) != (240, 240, 240)  # the frame with the green block, bottom strip cropped


@pytest.mark.skipif(not FFMPEG, reason='ffmpeg unavailable')
def test_make_cover_from_a_real_video_and_graceful_failures(tmp_path):
    video = tmp_path / 'video.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=10:duration=3', '-pix_fmt', 'yuv420p', str(video)], check=True)
    made = cover.make_cover(video, '标题测试：封面', tmp_path / 'out' / 'cover.jpg', FFMPEG)
    assert made and Image.open(made).size == cover.SIZE
    assert not list((tmp_path / 'out').glob('*.partial.jpg'))
    broken = tmp_path / 'broken.mp4'
    broken.write_bytes(b'not a video')
    assert cover.make_cover(broken, '标题', tmp_path / 'b.jpg', FFMPEG) is None
    assert cover.make_cover(video, '  ', tmp_path / 'c.jpg', FFMPEG) is None
    assert cover.make_cover(video, '标题', tmp_path / 'd.jpg', None) is None
    assert not (tmp_path / 'b.jpg').exists()


@pytest.mark.skipif(not FFMPEG, reason='ffmpeg unavailable')
def test_publish_cover_is_cached_per_title_and_prefers_a_shipped_cover(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from web.services import publish_cover as module
    video = tmp_path / 'video.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=10:duration=3', '-pix_fmt', 'yuv420p', str(video)], check=True)
    shipped = {'cover.jpg': None}
    monkeypatch.setattr(module.video_render, 'safe_file', lambda render, name: {'video.mp4': video}.get(name) or shipped.get(name))
    monkeypatch.setattr(module, 'get_settings', lambda: SimpleNamespace(paths=SimpleNamespace(state_dir=str(tmp_path / 'state'))))
    render = {'id': 'r1'}
    first = module.publish_cover(render, '第一个标题')
    assert Path(first).is_file() and first == module.publish_cover(render, ' 第一个标题 ')
    assert module.publish_cover(render, '另一个标题') != first
    shipped['cover.jpg'] = tmp_path / 'mine.jpg'
    assert module.publish_cover(render, '第一个标题') == str((tmp_path / 'mine.jpg').resolve())
    monkeypatch.setattr(module.video_render, 'safe_file', lambda render, name: None)
    assert module.publish_cover(render, '无视频') == ''


@pytest.mark.skipif(not FFMPEG, reason='ffmpeg unavailable')
def test_douyin_covers_have_separate_cache_and_share_video_sampling(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from web.services import publish_cover as module
    video = tmp_path / 'video.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=10:duration=3', '-pix_fmt', 'yuv420p', str(video)], check=True)
    monkeypatch.setattr(module.video_render, 'safe_file', lambda render, name: video if name == 'video.mp4' else None)
    monkeypatch.setattr(module, 'get_settings', lambda: SimpleNamespace(paths=SimpleNamespace(state_dir=str(tmp_path / 'state'))))
    calls = []
    original = cover.frames
    def sample(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(cover, 'frames', sample)
    result = module.publish_covers({'id': 'render'}, '测试标题', portrait=True)
    assert Image.open(result['cover']).size == cover.DOUYIN_LANDSCAPE_SIZE
    assert Image.open(result['cover_portrait']).size == cover.PORTRAIT_SIZE
    assert result['cover'] != result['cover_portrait']
    assert len(calls) == 1
    assert module.publish_covers({'id': 'render'}, '测试标题', portrait=True) == result and len(calls) == 1
    bilibili_cover = module.publish_cover({'id': 'render'}, '测试标题')
    assert Image.open(bilibili_cover).size == cover.SIZE and bilibili_cover != result['cover']
