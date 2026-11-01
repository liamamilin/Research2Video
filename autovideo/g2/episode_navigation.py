"""Frame-timed programme navigation shared by standard and fast video production.

This compositor reads authored item/chapters and measured scene timing. It does
not choose content or change voice, captions, scenes or production selection.
Besides the footer it draws the opening agenda and a short card where each item
starts, over the picture area only: the header, captions and footer stay visible.
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


#: The picture area the agenda and item cards cover; two-line captions start below it, the header sits above it.
STAGE = (0, 140, 1920, 870)
CARD_FRAMES = 54           # an item card shows for 1.8 s, never longer than a third of its item
FADE_MS = 220
PAPER = {'background': '#F7F8F5', 'text': '#202D28', 'muted': '#69756E', 'accent': '#17784B',
         'line': '#E1E8E1', 'surface': '#FFFFFF'}


def agenda(program: dict, ir: dict, compiled: dict, items: list[dict], paragraph_starts: list[str]) -> dict | None:
    """When the opening agenda shows and, where the briefing gives each item its own paragraph, when each lights up.

    The opening is everything before the first item. A briefing is an overview, optionally after the greeting, then
    one line per item; when the opening has exactly that many paragraphs, its last paragraphs are the items' lines and
    an item lights up as its line starts. Any other shape stays unlit: a highlight that may sit on an item other than
    the one being said is worse than none.
    """
    first = items[0]['start_frame'] if items else 0
    if first <= 0:
        return None
    chapters = ir['content_structure']['chapters']
    covered = {ci for item in program['items'] for ci in item['chapter_indexes']}
    opening = [sid for ci, chapter in enumerate(chapters) if ci not in covered and ci < min(covered, default=0)
               for sid in chapter['narration_segment_ids']]
    starts_ms = {s['id']: s['start_ms'] for s in compiled.get('narration', {}).get('segments', [])}
    to_frame = lambda ms: (ms * 30 + 500) // 1000
    paragraphs = [sid for sid in paragraph_starts if sid in opening and sid in starts_ms]
    start = to_frame(starts_ms[paragraphs[1]]) if len(paragraphs) > 1 else 0
    highlights = None
    if len(paragraphs) - len(items) in (1, 2):
        highlights = [to_frame(starts_ms[sid]) for sid in paragraphs[-len(items):]]
        start = min(start, highlights[0])
    start = max(0, min(start, first - 1))
    return {'start_frame': start, 'end_frame': first, 'highlights': highlights}


def cards(items: list[dict]) -> list[dict]:
    """The card at each item's start: its number and title, clamped to a third of the item."""
    return [{'index': item['index'], 'start_frame': item['start_frame'],
             'end_frame': item['start_frame'] + max(1, min(CARD_FRAMES, (item['end_frame'] - item['start_frame']) // 3))}
            for item in items]


def _bgr(color: str) -> str:
    return '&H00' + color[5:7] + color[3:5] + color[1:3]


def _fit(text: str, width: int, largest: int) -> int:
    """A font size at which ``text`` fits ``width``: ASS does not shrink text, so the size is chosen here."""
    return max(24, min(largest, width // max(1, len(text))))


def ass(items: list[dict], total_frames: int, *, accent: str = '#238759', opening: dict | None = None,
        item_cards: list[dict] | None = None, palette: dict | None = None, tutorial: bool = False) -> str:
    color = _bgr(accent)
    paper = {**PAPER, **{k: v for k, v in (palette or {}).items() if k in PAPER and isinstance(v, str)}}
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
            event(3, start, end, r'{\an6\pos(1840,1047)\fs22\c&H00C7D5CD}'+('下一步' if tutorial else '下一条')+' · '+following)
        rect(3, 0, total_frames, x, 1069, 2, 11, '&H00D0DDD6')
    x0, y0, x1, y1 = STAGE
    def cover(start, end):
        rect(4, start, end, x0, y0, x1 - x0, y1 - y0, _bgr(paper['background']), f'\\fad({FADE_MS},{FADE_MS})')
    def text(start, end, x, y, size, fill, value, align=4, bold=True):
        event(8, start, end, '{' + f'\\an{align}\\pos({x},{y})\\fs{size}\\c{fill}\\b{int(bold)}\\fad({FADE_MS},{FADE_MS})' + '}' + _ass_text(value))
    if opening:
        start, end = opening['start_frame'], opening['end_frame']
        cover(start, end)
        text(start, end, 140, 205, 44, _bgr(paper['text']), '操作步骤' if tutorial else '本期导读')
        rect(5, start, end, 140, 240, 84, 5, _bgr(accent), f'\\fad({FADE_MS},{FADE_MS})')
        columns = 1 if len(items) <= 5 else 2
        rows = -(-len(items) // columns)
        width = (x1 - 280 - 40 * (columns - 1)) // columns
        height = min(96, (y1 - 290 - 20 * (rows - 1)) // rows)
        lights = opening.get('highlights')
        top = 280 + max(0, (y1 - 300 - (rows * height + 20 * (rows - 1))) // 2)
        for i, item in enumerate(items):
            column, row = divmod(i, rows)
            x, y = 140 + column * (width + 40), top + row * (height + 20)
            size = _fit(item['title'], width - 150, 38 if columns == 1 else 32)
            windows = [(start, end, 'idle')] if lights is None else [
                (start, lights[i], 'next'), (lights[i], lights[i + 1] if i + 1 < len(items) else end, 'now'),
                (lights[i + 1] if i + 1 < len(items) else end, end, 'done')]
            for begin, until, state in windows:
                if until <= begin:
                    continue
                # Only the agenda's own edges fade, so a row does not flicker when its state changes.
                fade = f'\\fad({FADE_MS if begin == start else 0},{FADE_MS if until == end else 0})'
                border = _bgr(accent) if state == 'now' else _bgr(paper['line'])
                rect(5, begin, until, x, y, width, height, border, fade)
                rect(6, begin, until, x + 2, y + 2, width - 4, height - 4, _bgr(paper['surface']), fade)
                if state == 'now':
                    rect(7, begin, until, x, y, 8, height, _bgr(accent), fade)
                ink = _bgr(accent) if state == 'now' else _bgr(paper['muted']) if state == 'done' else _bgr(paper['text'])
                number = _bgr(accent) if state != 'done' else _bgr(paper['muted'])
                event(8, begin, until, '{' + f'\\an4\\pos({x + 36},{y + height // 2})\\fs30\\c{number}\\b1' + fade + '}' + f"{item['index']:02d}")
                event(8, begin, until, '{' + f'\\an4\\pos({x + 110},{y + height // 2})\\fs{size}\\c{ink}\\b{int(state == "now")}' + fade + '}' + _ass_text(item['title']))
    for card in item_cards or []:
        start, end = card['start_frame'], card['end_frame']
        item = items[card['index'] - 1]
        cover(start, end)
        text(start, end, 960, 400, 40, _bgr(accent), f"{item['index']:02d} / {len(items):02d}", align=5)
        rect(5, start, end, 918, 450, 84, 5, _bgr(accent), f'\\fad({FADE_MS},{FADE_MS})')
        text(start, end, 960, 540, _fit(item['title'], 1500, 72), _bgr(paper['text']), item['title'], align=5)
    if items[0]['start_frame'] > 0:
        event(3, 0, items[0]['start_frame'], r'{\an4\pos(80,1047)}'+('操作步骤' if tutorial else '本期导读'))
    if items[-1]['end_frame'] < total_frames:
        event(3, items[-1]['end_frame'], total_frames, r'{\an4\pos(80,1047)}'+('步骤回顾' if tutorial else '本期回顾'))
    return header + '\n'.join(events) + '\n'


def program_for(saved: dict, ir: dict) -> dict | None:
    """Reuse authored items, or the tutorial's already planned chapter roles."""
    if saved.get('episode'):
        return saved['episode']
    chapters = ir.get('content_structure', {}).get('chapters', [])
    if saved.get('production_recipe') != 'mixed_tutorial' or len(chapters) < 2:
        return None
    items = []
    for index, chapter in enumerate(chapters):
        ids = set(chapter['narration_segment_ids'])
        roles = {scene['props'].get('story', {}).get('role') for scene in ir['scenes']
                 if ids.intersection(scene['narration_segment_ids'])}
        if not roles or None in roles:
            return None
        if index == 0 and roles <= {'question', 'bridge'}:
            continue
        if index == len(chapters)-1 and roles <= {'recap', 'bridge'}:
            continue
        items.append({'title': chapter['title'][:32], 'chapter_indexes': [index]})
    return {'items': items} if items else None


def apply(pipeline, compiled: dict) -> dict | None:
    input_path = pipeline.PROJECT_INPUT.parent / 'input.json'
    if not input_path.is_file():
        return None
    saved = json.loads(input_path.read_text())
    if not saved.get('episode') and saved.get('production_recipe') != 'mixed_tutorial':
        return None
    ir = json.loads(pipeline.IR_PATH.read_text())
    program = program_for(saved, ir)
    if not program:
        return None
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
    starts_path = pipeline.ARTIFACTS / 'paragraph-starts.json'
    starts = json.loads(starts_path.read_text())['segment_ids'] if starts_path.is_file() else []
    opening = agenda(program, ir, compiled, items, starts)
    item_cards = cards(items)
    script = folder / 'navigation.ass'
    script.write_text(ass(items, compiled['total_frames'], accent=style.get('accent', '#238759'), opening=opening,
                          item_cards=item_cards, palette=style if style.get('palette_source') == 'brand' else None,
                          tutorial=(pipeline.PACKAGING_INPUT or {}).get('production_recipe') == 'mixed_tutorial'),
                      encoding='utf-8')
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
    covered = ([[opening['start_frame'], opening['end_frame']]] if opening else []) + [
        [c['start_frame'], c['end_frame']] for c in item_cards]
    report = {'mode': 'episode', 'items': items, 'total_frames': compiled['total_frames'],
              'agenda': opening, 'item_cards': item_cards, 'covered_frames': covered,
              'agenda_highlight': 'per item paragraph' if opening and opening['highlights'] else 'static list',
              'fps': 30, 'timing': 'measured narration chapters', 'footer': [0, 1024, 1920, 56],
              'audio': 'stream copy', 'source_preserved': str(source), 'video_sha256': digest(pipeline.VIDEO_PATH)}
    pipeline.write_json(report_path, report)
    return report
