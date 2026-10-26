import copy

import pytest

from g2.hybrid_cards import blocks_for, still_page, windows_for
from g2.production_plan import ProductionPlanError, digest, hybrid_enabled, prepare_request, verify


def test_fast_bridge_does_not_gain_icons_from_ordinary_keywords():
    from g2.explanation import validate
    page = {'id': 'bridge', 'component': 'concept_reveal', 'props': {'title': '观看顺序', 'lines': ['先看文件，再看成本']},
            'importance': 'support', 'story_role': 'bridge'}
    validate({'pages': [page]}, {'fast_cards': True, 'groups': [{'id': 'bridge'}]})
    assert page['props']['visuals'] == []
    assert still_page(scene(**page['props']))


def scene(identity='card_1', component='concept_reveal', **props):
    return {'id': identity, 'component': component, 'props': {'title': '讲解', 'lines': ['先看对象', '再看过程'], **props},
            'start_frame': 0, 'end_frame': 180, 'beats': [
                {'action': 'reveal', 'target_id': 'line:0', 'frame': 12},
                {'action': 'reveal', 'target_id': 'line:1', 'frame': 75}]}


def test_card_reveals_follow_compiled_speech_and_keep_every_frame():
    current = scene()
    windows = windows_for(still_page(current), current)
    assert windows == [dict(start_frame=0, end_frame=12, visible=[]),
                       dict(start_frame=12, end_frame=75, visible=[0]),
                       dict(start_frame=75, end_frame=180, visible=[0, 1])]
    assert sum(w['end_frame']-w['start_frame'] for w in windows) == 180


@pytest.mark.parametrize('props', [{'visuals': [{'icon': 'file'}]}, {'actions': [{}]},
                                  {'story': {'importance': 'key'}}, {'story': {'role': 'recap'}}])
def test_visual_objects_key_concepts_and_recaps_keep_motion(props):
    assert still_page(scene(**props)) is None


def test_adjacent_executor_ranges_preserve_timeline_and_minimize_browser_runs():
    scenes = [scene('a'), scene('b'), scene('c', 'price_change'), scene('d', 'flow_diagram'), scene('e')]
    for i, current in enumerate(scenes):
        current.update(start_frame=i*180, end_frame=(i+1)*180)
    blocks = blocks_for(scenes)
    assert [(b['executor'], b['start_frame'], b['end_frame']) for b in blocks] == [
        ('ffmpeg_cards', 0, 360), ('remotion', 360, 720), ('ffmpeg_cards', 720, 900)]
    assert blocks[1]['scene_ids'] == ['c', 'd']


def test_real_clips_with_different_track_timescales_join_on_their_original_frames(tmp_path):
    import json
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from g2.hybrid_cards import _concat, normalize_clip

    root = Path(__file__).resolve().parents[2]
    ffmpeg, ffprobe = root/'g0/darwin_arm64/ffmpeg', root/'g0/darwin_arm64/ffprobe'
    if not ffmpeg.exists():
        pytest.skip('local FFmpeg unavailable')

    def run(label, argv, cwd, **kwargs):
        return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, check=True).stdout

    pipeline = SimpleNamespace(FFMPEG=ffmpeg, PROJECT=root, _run_fixed_command=run)
    rows = []
    for index, timescale in enumerate((15360, 90000)):
        source, target = tmp_path/f'raw-{index}.mp4', tmp_path/f'normalized-{index}.mp4'
        run('fixture', [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
            f'color=c={"red" if index==0 else "blue"}:s=160x90:r=30:d=1', '-c:v', 'libx264',
            '-pix_fmt', 'yuvj420p' if index else 'yuv420p', '-color_range', 'pc' if index else 'tv',
            '-video_track_timescale', timescale, source], root)
        normalize_clip(pipeline, source, target, f'normalize-{index}')
        rows.append({'path': target})
    concat = tmp_path/'clips.ffconcat'
    _concat(concat, rows)
    joined = tmp_path/'joined.mp4'
    run('join', [ffmpeg, '-v', 'error', '-safe', '0', '-f', 'concat', '-i', concat, '-c', 'copy', joined], root)
    probe = json.loads(run('probe', [ffprobe, '-v', 'error', '-show_streams', '-of', 'json', joined], root))['streams'][0]
    assert probe['nb_frames'] == '60'
    assert probe['color_range'] == 'tv' and probe['color_space'] == 'bt709'
    assert float(probe['duration']) == pytest.approx(2, abs=.001)
    pixels = subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(joined), '-vf', 'select=eq(n\\,31)',
        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
    assert pixels[2] > 200 and pixels[0] < 30  # The second clip starts at frame 30.


def test_opt_in_executor_freezes_materials_and_old_fast_jobs_keep_their_route():
    request = {'source_text': '先讲对象，再比较成本。', 'production_mode': 'fast', 'visual_storytelling': True,
               'chapters': [{'title': '对象', 'paragraphs': ['先讲对象，再比较成本。']}],
               'image_materials': [{'image_id': 'test', 'placements': [{'chapter_index': 0, 'paragraph_index': 0}]}]}
    saved = prepare_request(request)
    assert verify(saved)['executor']['id'] == 'hybrid_cards'
    revision = {**saved, 'operation_type': 'media_revision', 'parent_job_id': 'parent', 'video_speed': 1.2}
    revision.pop('chapters')
    assert verify(prepare_request(revision))['executor']['id'] == 'hybrid_cards'
    altered = copy.deepcopy(saved)
    altered['image_materials'][0]['placements'][0]['paragraph_index'] = 1
    with pytest.raises(ProductionPlanError):
        verify(altered)
    old = prepare_request({**request, 'visual_storytelling': False, 'image_materials': []})
    old['visual_storytelling'] = True  # Historical plans did not bind this flag.
    assert not hybrid_enabled(old)
    assert verify(old)['executor']['id'] == 'ffmpeg_cards'
    assert old['production_plan']['sha256'] == digest({k: v for k, v in old['production_plan'].items() if k != 'sha256'})
