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


def test_compose_is_full_size_and_long_titles_stay_within_three_lines():
    frame = Image.new('RGB', (1920, 929), (200, 220, 210))
    image = cover.compose(frame, '很长的标题' * 30)
    assert image.size == cover.SIZE


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
