"""Speech-led fast cards with trusted Remotion clips only where motion helps."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from g2 import fast_cards


def still_page(scene: dict) -> dict | None:
    """Keep meaningful actions, key concepts, pictures and recaps in Remotion."""
    props = scene['props']
    story = props.get('story', {})
    if props.get('visuals') or props.get('actions') or story.get('recap') or story.get('role') == 'recap':
        return None
    if scene['component'] == 'title_card':
        text = props.get('subtitle') or props['title']
        return {'layout': 'focus', 'points': [{'text': text}], 'targets': ['subtitle']}
    if scene['component'] == 'concept_reveal' and story.get('importance') != 'key':
        return {'layout': 'focus' if len(props['lines']) == 1 else 'notes',
                'points': [{'text': line} for line in props['lines']],
                'targets': [f'line:{i}' for i in range(len(props['lines']))]}
    return None


def windows_for(page: dict, scene: dict) -> list[dict]:
    start, end = scene['start_frame'], scene['end_frame']
    beats = {beat['target_id']: beat['frame'] for beat in scene['beats'] if beat['action'] == 'reveal'}
    onsets = [max(start, min(end-1, beats.get(target, start))) for target in page['targets']]
    boundaries = sorted({start, end, *onsets})
    return [{'start_frame': left, 'end_frame': right,
             'visible': [i for i, onset in enumerate(onsets) if onset <= left]}
            for left, right in zip(boundaries, boundaries[1:])]


def blocks_for(scenes: list[dict]) -> list[dict]:
    """Adjacent scenes share one clip; frame ranges stay on the original timeline."""
    blocks = []
    for scene in scenes:
        executor = 'ffmpeg_cards' if still_page(scene) else 'remotion'
        if blocks and blocks[-1]['executor'] == executor:
            blocks[-1]['end_frame'] = scene['end_frame']
            blocks[-1]['scene_ids'].append(scene['id'])
        else:
            blocks.append({'executor': executor, 'start_frame': scene['start_frame'],
                           'end_frame': scene['end_frame'], 'scene_ids': [scene['id']]})
    return blocks


def _concat(path: Path, rows: list[dict], *, images: bool = False) -> None:
    entries = ['ffconcat version 1.0']
    for row in rows:
        entries.append(f"file '{fast_cards._quote(row['path'])}'")
        if images:
            entries.extend(['option framerate 30', f"duration {row['frames']/30:.12f}"])
    if images:
        entries.extend([f"file '{fast_cards._quote(rows[-1]['path'])}'", 'option framerate 30'])
    path.write_text('\n'.join(entries)+'\n', encoding='utf-8')


def normalize_clip(pipeline, source: Path, output: Path, label: str) -> None:
    """Match timescale and color range so mixed encoders keep timing and paper colors."""
    pipeline._run_fixed_command(label, [pipeline.FFMPEG, '-nostdin', '-v', 'warning', '-n',
        '-i', source, '-map', '0:v:0', '-an', '-vf',
        'scale=in_range=auto:out_range=tv:out_color_matrix=bt709,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-profile:v', 'high',
        '-color_range', 'tv', '-colorspace', 'bt709', '-color_trc', 'bt709', '-color_primaries', 'bt709',
        '-video_track_timescale', '90000', output], pipeline.PROJECT, timeout=1200)


def join_clips(pipeline, compiled: dict, ir: dict, clips: list[dict], root: Path) -> tuple[str, list]:
    from g2.caption_alignment import verify_plan

    normalized = []
    for index, clip in enumerate(clips):
        output = root / f'clip-{index:03d}-normalized.mp4'
        normalize_clip(pipeline, clip['path'], output, f'hybrid-normalize-{index:03d}')
        normalized.append({'path': output})
    clips = normalized
    concat = root / 'clips.ffconcat'
    _concat(concat, clips)
    captions = json.loads((pipeline.ARTIFACTS / 'caption-alignment.json').read_text())
    verify_plan(ir, captions)
    ass = root / 'captions.ass'
    fast_cards.write_ass(ir, ass, captions['cues'], style=pipeline.PACKAGING_INPUT['production_plan']['style'])
    escaped = str(ass).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
    encoders = [('h264_videotoolbox', ['-b:v', '5500k', '-allow_sw', '0'])] if sys.platform == 'darwin' else []
    encoders.append(('libx264', ['-preset', 'veryfast', '-crf', '20']))
    failures = []
    for encoder, options in encoders:
        output = root / f'attempt-{encoder}.mp4'
        try:
            pipeline._run_fixed_command(f'hybrid-join-{encoder}', [pipeline.FFMPEG,
                '-nostdin', '-v', 'warning', '-n', '-safe', '0', '-f', 'concat', '-i', concat,
                '-i', pipeline.ASSETS / 'communications_noise.wav',
                '-vf', f"fps=30,ass=filename='{escaped}',format=yuv420p", '-map', '0:v:0', '-map', '1:a:0',
                '-frames:v', str(compiled['total_frames']), '-t', str(compiled['total_frames']/30),
                '-c:v', encoder, *options, '-profile:v', 'high', '-c:a', 'aac', '-b:a', '192k',
                '-ar', '48000', '-ac', '2', '-movflags', '+faststart', output], pipeline.PROJECT,
                timeout=max(1200, compiled['total_frames']//5))
            output.replace(pipeline.VIDEO_PATH)
            break
        except pipeline.G2Fault as exc:
            failures.append({'encoder': encoder, 'code': exc.code, 'message': str(exc)[:300]})
    else:
        raise pipeline.G2Fault('HYBRID_ENCODE_FAILED', '混合快速视频编码失败，已保留尝试与日志')
    return encoder, failures


def render(pipeline, compiled: dict, *, renderer_root: Path | None = None) -> None:
    from g2.production_plan import verify

    started = time.monotonic()
    saved = pipeline.PACKAGING_INPUT
    plan = verify(saved)
    if plan['executor']['id'] != 'hybrid_cards' or compiled.get('presentation', {}).get('preset') != 'explanation':
        raise pipeline.G2Fault('PRODUCTION_EXECUTOR_MISMATCH', '混合快速制作需要已编排的讲稿画面')
    ir = json.loads(pipeline.IR_PATH.read_text())
    root = pipeline.ARTIFACTS / 'hybrid-cards'
    root.mkdir(exist_ok=False)
    assets = pipeline.ASSETS / 'hybrid-cards'
    if assets.exists() and saved.get('parent_job_id'):
        # A media revision copied the parent's fixtures; keep them as evidence
        # and draw new card assets for the newly measured/retimed timeline.
        assets = pipeline.ASSETS / f'hybrid-cards-{pipeline.RUN_ID}'
    assets.mkdir(exist_ok=False)
    record = pipeline.packaging_record()
    badge = record['badge'] if record else {'text': '', 'color': plan['style']['accent']}
    backgrounds, cards, pages = {}, {}, []
    for index, (original, scene) in enumerate(zip(ir['scenes'], compiled['scenes'])):
        page = still_page(scene)
        card_scene = {'props': {'title': scene['props'].get('title', scene['purpose']),
                                'text': '\n'.join(p['text'] for p in page['points']) if page else ''}}
        chapters = {chapter['id'] for chapter in ir.get('content_structure', {}).get('chapters', [])
                    if set(chapter['narration_segment_ids']) & set(original['narration_segment_ids'])}
        notices = [row['message'] for row in saved.get('content_notices', [])
                   if row.get('chapter_index') is None or f"c{row['chapter_index']+1}" in chapters]
        windows = windows_for(page, scene) if page else [dict(start_frame=scene['start_frame'], end_frame=scene['end_frame'], visible=None)]
        rows = []
        for wi, window in enumerate(windows):
            path = assets / f'page-{index:04d}-{wi}.png'
            fast_cards.draw_card(card_scene, header=plan.get('brand_layout'), title=ir['video']['title'], index=index, count=len(ir['scenes']),
                style=plan['style'], badge=badge, notices=notices, output=path, editorial=page,
                visible_points=set(window['visible']) if window['visible'] is not None else None)
            rows.append({'path': path, 'frames': window['end_frame']-window['start_frame']})
        cards[scene['id']] = rows
        backgrounds[scene['id']] = {'path': rows[-1]['path'].relative_to(pipeline.FIXTURE_ROOT).as_posix(), 'still': page is not None}
        pages.append({'scene_id': scene['id'], 'component': scene['component'],
                      'executor': 'ffmpeg_cards' if page else 'remotion',
                      'start_frame': scene['start_frame'], 'end_frame': scene['end_frame'],
                      'narration_segment_ids': scene['narration_segment_ids'], 'reveal_windows': windows})
    blocks = blocks_for(compiled['scenes'])
    clips = []
    for index, block in enumerate(blocks):
        output = root / f'clip-{index:03d}.mp4'
        frames = block['end_frame']-block['start_frame']
        clip_started = time.monotonic()
        if block['executor'] == 'remotion':
            pipeline.render(compiled, renderer_root=renderer_root, hybrid_segment={**block,
                'output': output, 'backgrounds': backgrounds, 'label': f'hybrid-{index:03d}'})
        else:
            concat = root / f'cards-{index:03d}.ffconcat'
            _concat(concat, [row for sid in block['scene_ids'] for row in cards[sid]], images=True)
            pipeline._run_fixed_command(f'hybrid-cards-{index:03d}', [pipeline.FFMPEG,
                '-nostdin', '-v', 'warning', '-n', '-safe', '0', '-f', 'concat', '-i', concat,
                '-vf', 'fps=30,format=yuv420p', '-frames:v', str(frames), '-an',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-profile:v', 'high',
                '-video_track_timescale', '90000', output], pipeline.PROJECT, timeout=max(1200, frames//5))
        block['render_seconds'] = round(time.monotonic()-clip_started, 3)
        # Accurate clip lengths prevent a still stream's repeated closing image
        # or an inclusive Remotion range from shifting all following narration.
        probe = json.loads(pipeline._run_fixed_command(f'hybrid-probe-{index:03d}', [pipeline.FFPROBE,
            '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
            'stream=nb_read_frames', '-of', 'json', output], pipeline.PROJECT))
        if int(probe['streams'][0]['nb_read_frames']) != frames:
            raise pipeline.G2Fault('HYBRID_CLIP_DURATION_MISMATCH', '混合画面片段帧数与旁白时间线不一致')
        clips.append({'path': output})
    encoder, failures = join_clips(pipeline, compiled, ir, clips, root)
    pipeline.write_json(pipeline.ARTIFACTS / 'hybrid-card-plan.json', {
        'version': 5, 'pages': pages, 'blocks': blocks, 'manual_scene_selection': False,
        'production_plan_sha256': plan['sha256'], 'shared_voice_and_captions': True})
    pipeline.write_json(pipeline.ARTIFACTS / 'production-execution.json', {
        'schema_version': 1, 'production_plan_sha256': plan['sha256'], 'executor': 'hybrid_cards',
        'encoder': encoder, 'fallbacks': failures, 'render_seconds': round(time.monotonic()-started, 3),
        'normalized_track_timescale': 90000,
        'form': 'information_cards', 'style': plan['style'], 'music': plan['music'],
        'still_frames': sum(p['end_frame']-p['start_frame'] for p in pages if p['executor']=='ffmpeg_cards'),
        'motion_frames': sum(p['end_frame']-p['start_frame'] for p in pages if p['executor']=='remotion'),
        'scene_count': len(pages), 'block_count': len(blocks), 'caption_plan_version': 2,
        'packaging_owner': 'shared_package_stage', 'manual_scene_selection': False})
