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


COPY = {'big': '差十倍', 'figure': '$600 vs $60 / 月', 'topic': 'ChatGPT · Claude 订阅'}


def test_cover_copy_takes_numbers_only_from_the_narration_and_fits_the_layout():
    from core.social.cover_copy import accepted
    narration = '最高档 600 美元/月，20 美元档 60 美元/月，差十倍。'
    assert accepted(COPY, narration) == COPY
    assert accepted({**COPY, 'figure': '$700 vs $60 / 月'}, narration) is None  # 700 is not in the script
    assert accepted({**COPY, 'big': '订阅价格差了足足十倍'}, narration) is None  # too wide for the big line
    assert accepted({'big': '免费了', 'figure': '', 'topic': ''}, '') == {'big': '免费了', 'figure': '', 'topic': ''}


def test_cover_copy_is_made_once_and_a_failure_is_remembered(tmp_path, monkeypatch):
    from core.social import cover_copy
    calls = []
    monkeypatch.setattr(cover_copy, 'narration_of', lambda render: '600 美元和 60 美元，差十倍')
    monkeypatch.setattr(cover_copy, 'client', lambda: None)
    def generate(client, title, narration):
        calls.append(title)
        return COPY
    monkeypatch.setattr(cover_copy, 'generate', generate)
    render = {'id': 'r', 'script_id': 's', 'version': 1}
    assert cover_copy.for_render(render, '标题', tmp_path / 'a.json') == COPY
    assert cover_copy.for_render(render, '标题', tmp_path / 'a.json') == COPY and len(calls) == 1
    def broken(client, title, narration):
        calls.append(title)
        raise TimeoutError('slow model')
    monkeypatch.setattr(cover_copy, 'generate', broken)
    assert cover_copy.for_render(render, '标题', tmp_path / 'b.json') is None
    assert cover_copy.for_render(render, '标题', tmp_path / 'b.json') is None and len(calls) == 2
    assert 'slow model' in (tmp_path / 'b.json').read_text(encoding='utf-8')
    assert cover_copy.for_render({'id': 'local'}, '标题', tmp_path / 'c.json') is None  # no script: title cover


@pytest.mark.parametrize('copy', [COPY, {'big': '免费了', 'figure': '', 'topic': ''},
                                  {'big': 'GPT-6.2', 'figure': '一个非常非常长的对比说明文字放不下', 'topic': '很长很长的主题名称也要放得下'}])
def test_number_led_cover_keeps_text_inside_and_clear_of_the_feed_counters(copy):
    for image, area in ((cover.compose_figure(copy), None), (cover.compose_figure_portrait(copy), 'square')):
        assert image.size in (cover.SIZE, cover.PORTRAIT_SIZE)
        w, h = image.size
        bottom = (h - w) // 2 + int(w * .92) if area else int(h * (1 - cover.FEED_OVERLAY))
        top = (h - w) // 2 if area else 0
        ink = image.convert('L').point(lambda v: 255 if v < 200 else 0)
        box = ink.crop((0, 0, w, h - 20)).getbbox()  # the accent rule along the very bottom is not text
        assert box and box[1] >= top and box[3] <= bottom and box[2] <= w * .95


@pytest.mark.skipif(not FFMPEG, reason='ffmpeg unavailable')
def test_make_covers_with_copy_needs_no_frame_and_falls_back_without_it(tmp_path, monkeypatch):
    video = tmp_path / 'video.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=10:duration=3', '-pix_fmt', 'yuv420p', str(video)], check=True)
    sampled = []
    original = cover.frames
    monkeypatch.setattr(cover, 'frames', lambda *a: sampled.append(1) or original(*a))
    made = cover.make_covers(video, '标题', {'landscape': tmp_path / 'a.jpg', 'portrait': tmp_path / 'b.jpg'}, FFMPEG, COPY)
    assert set(made) == {'landscape', 'portrait'} and not sampled
    assert Image.open(made['portrait']).size == cover.PORTRAIT_SIZE
    monkeypatch.setattr(cover, 'compose_figure', lambda *a, **k: None)
    assert cover.make_covers(video, '标题', {'landscape': tmp_path / 'c.jpg'}, FFMPEG, COPY) and sampled


@pytest.mark.skipif(not FFMPEG, reason='ffmpeg unavailable')
def test_a_shipped_picture_replaces_the_sampled_frame(tmp_path, monkeypatch):
    video = tmp_path / 'video.mp4'
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=640x360:rate=10:duration=3', '-pix_fmt', 'yuv420p', str(video)], check=True)
    picture = tmp_path / 'cover-frame.png'
    Image.new('RGB', (1344, 768), (250, 120, 30)).save(picture)
    sampled, used = [], []
    original_frames, original_compose = cover.frames, cover.compose
    monkeypatch.setattr(cover, 'frames', lambda *a: sampled.append(1) or original_frames(*a))
    monkeypatch.setattr(cover, 'compose', lambda frame, title, size=cover.SIZE: used.append(frame.getpixel((10, 10))) or original_compose(frame, title, size))
    made = cover.make_covers(video, '标题', {'landscape': tmp_path / 'a.jpg'}, FFMPEG, None, picture)
    assert made and not sampled and used == [(250, 120, 30)]
    # a missing or unreadable picture falls back to a frame of the video
    (tmp_path / 'bad.png').write_bytes(b'not a png')
    for fallback in (tmp_path / 'none.png', tmp_path / 'bad.png'):
        sampled.clear()
        assert cover.make_covers(video, '标题', {'landscape': tmp_path / f'{fallback.stem}.jpg'}, FFMPEG, None, fallback) and sampled
