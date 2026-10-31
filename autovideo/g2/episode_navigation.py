"""Frame-timed programme navigation shared by standard and fast video production.

This compositor reads authored item/chapters and measured scene timing. It does
not choose content or change voice, captions, scenes or production selection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from g2.fast_cards import _ass_text, _stamp


def timeline(program: dict, ir: dict, compiled: dict) -> list[dict]:
    chapters = ir['content_structure']['chapters']
    items = []
    for index, item in enumerate(program['items']):
        ids = {sid for ci in item['chapter_indexes'] for sid in chapters[ci]['narration_segment_ids']}
        scenes = [s for s in compiled['scenes'] if ids.intersection(s['narration_segment_ids'])]
        if not scenes:
            raise ValueError('节目条目没有对应的实际画面时间')
        items.append({'index': index + 1, 'title': item['title'], 'chapter_indexes': item['chapter_indexes'],
                      'start_frame': min(s['start_frame'] for s in scenes),
                      'end_frame': max(s['end_frame'] for s in scenes)})
    return items


def ass(items: list[dict], total_frames: int, *, accent: str = '#238759') -> str:
    color = '&H00' + accent[5:7] + accent[3:5] + accent[1:3]
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Nav,Hiragino Sans GB,27,&H00FFFFFF,&H00FFFFFF,&H00202020,&H00202020,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    def event(layer, start, end, text):
        events.append(f"Dialogue: {layer},{_stamp(start*1000/30)},{_stamp(end*1000/30)},Nav,,0,0,0,,{text}")
    def rect(layer, start, end, x, y, width, height, fill, extra=''):
        event(layer, start, end, '{'+f'\\an7\\pos({x},{y})\\c{fill}\\p1'+extra+'}'+f'm 0 0 l {width} 0 l {width} {height} l 0 {height}')
    # Captions finish at y=1020. Navigation has its own 56-pixel footer.
    rect(0, 0, total_frames, 0, 1024, 1920, 56, '&H001C2520')
    rect(1, 0, total_frames, 80, 1072, 1760, 5, '&H00525C55')
    for item in items:
        start, end = item['start_frame'], item['end_frame']
        x = round(80 + 1760*start/total_frames)
        width = max(1, round(1760*(end-start)/total_frames)-3)
        rect(1, start, total_frames, x, 1072, width, 5, color)
        # Reveal the active segment continuously using the actual measured duration.
        rect(2, start, end, x, 1072, width, 5, '&H00B4FFD0',
             f'\\fscx0\\t(0,{round((end-start)*1000/30)},\\fscx100)')
        label = f"{item['index']:02d} / {len(items):02d}   {_ass_text(item['title'])}"
        event(3, start, end, r'{\an4\pos(80,1047)\b1}'+label)
        next_index = item['index']
        if next_index < len(items):
            following = _ass_text(items[next_index]['title'][:20] + ('…' if len(items[next_index]['title']) > 20 else ''))
            event(3, start, end, r'{\an6\pos(1840,1047)\fs22\c&H00C7D5CD}下一条 · '+following)
        rect(3, 0, total_frames, x, 1069, 2, 11, '&H00D0DDD6')
    if items[0]['start_frame'] > 0:
        event(3, 0, items[0]['start_frame'], r'{\an4\pos(80,1047)}本期导读')
    if items[-1]['end_frame'] < total_frames:
        event(3, items[-1]['end_frame'], total_frames, r'{\an4\pos(80,1047)}本期回顾')
    return header + '\n'.join(events) + '\n'


def apply(pipeline, compiled: dict) -> dict | None:
    input_path = pipeline.PROJECT_INPUT.parent / 'input.json'
    if not input_path.is_file():
        return None
    saved = json.loads(input_path.read_text())
    program = saved.get('episode')
    if not program:
        return None
    ir = json.loads(pipeline.IR_PATH.read_text())
    items = timeline(program, ir, compiled)
    folder = pipeline.ARTIFACTS / 'episode-navigation'
    folder.mkdir(exist_ok=True)
    report_path = pipeline.ARTIFACTS / 'episode-navigation.json'
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if report_path.exists():
        old = json.loads(report_path.read_text())
        if old.get('video_sha256') == digest(pipeline.VIDEO_PATH):
            return old
    style = ((pipeline.PACKAGING_INPUT or {}).get('production_plan') or {}).get('style') or {}
    script = folder / 'navigation.ass'
    script.write_text(ass(items, compiled['total_frames'], accent=style.get('accent', '#238759')), encoding='utf-8')
    escaped = str(script).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
    target = folder / 'navigation.mp4'
    pipeline._run_fixed_command('episode-navigation', [pipeline.FFMPEG, '-nostdin', '-v', 'warning', '-n',
        '-i', pipeline.VIDEO_PATH, '-vf', f"ass=filename='{escaped}',format=yuv420p", '-map', '0:v:0', '-map', '0:a:0',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-c:a', 'copy',
        '-frames:v', str(compiled['total_frames']), '-movflags', '+faststart', target], pipeline.PROJECT,
        timeout=max(1200, compiled['total_frames']//5))
    source = folder / 'original-content.mp4'
    if source.exists():
        raise pipeline.G2Fault('EPISODE_OUTPUT_EXISTS', '节目导航的原始成片已保留，请使用新的尝试目录')
    pipeline.VIDEO_PATH.replace(source)
    target.replace(pipeline.VIDEO_PATH)
    report = {'mode': 'episode', 'items': items, 'total_frames': compiled['total_frames'],
              'fps': 30, 'timing': 'measured narration chapters', 'footer': [0, 1024, 1920, 56],
              'audio': 'stream copy', 'source_preserved': str(source), 'video_sha256': digest(pipeline.VIDEO_PATH)}
    pipeline.write_json(report_path, report)
    return report
