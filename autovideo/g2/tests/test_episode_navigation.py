import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from pydantic import ValidationError
from g2.episode_navigation import timeline, ass, apply, agenda, cards
from g3.app import CreateJobRequest


def request(**kw):
    return CreateJobRequest.model_validate({'title': '本期观察', 'input_mode': 'narration', 'duration_mode': 'content',
        'video_form': 'auto', 'chapters': [{'title': title, 'paragraphs': ['这是原始完整讲稿。']} for title in ['导读', '内容甲', '内容乙', '回顾']],
        'episode': {'items': [{'title': '内容甲', 'chapter_indexes': [1]}, {'title': '内容乙', 'chapter_indexes': [2]}]}, **kw})


def test_programme_request_accepts_multiple_paragraphs_and_ordered_content():
    assert len(request().episode.items) == 2


@pytest.mark.parametrize('indexes', [[-1], [4], [2, 1], [1, 1]])
def test_programme_request_rejects_ambiguous_chapter_placement(indexes):
    with pytest.raises(ValidationError):
        request(episode={'items': [{'title': '条目', 'chapter_indexes': indexes}]})


def test_navigation_uses_measured_frames_and_leaves_opening_and_closing_separate():
    ir = {'content_structure': {'chapters': [{'narration_segment_ids': [f'n{i}']} for i in range(4)]}}
    compiled = {'scenes': [{'narration_segment_ids': [f'n{i}'], 'start_frame': start, 'end_frame': end}
        for i, (start, end) in enumerate([(0, 60), (60, 210), (210, 510), (510, 570)])]}
    items = timeline(request().episode.model_dump(), ir, compiled)
    assert [(i['start_frame'], i['end_frame']) for i in items] == [(60, 210), (210, 510)]
    text = ass(items, 570)
    assert '本期导读' in text and '本期回顾' in text and '02 / 02' in text
    assert '\\t(0,5000,\\fscx100)' in text and '\\t(0,10000,\\fscx100)' in text
    assert '\\pos(80,1047)' in text and '1024' in text


def test_individual_mode_does_no_compositor_work(tmp_path):
    (tmp_path / 'input.json').write_text('{}')
    assert apply(SimpleNamespace(PROJECT_INPUT=tmp_path / 'source.txt'), {}) is None


def _programme(paragraph_ids):
    """An opening of four segments, then two items; segment n<i> starts at i seconds."""
    ir = {'content_structure': {'chapters': [{'narration_segment_ids': ['n0', 'n1', 'n2', 'n3']},
                                             {'narration_segment_ids': ['n4']}, {'narration_segment_ids': ['n5']},
                                             {'narration_segment_ids': ['n6']}]}}
    spans = [(120, 300), (300, 600), (600, 690)]
    compiled = {'narration': {'segments': [{'id': f'n{i}', 'start_ms': i * 1000} for i in range(7)]},
                'scenes': [{'narration_segment_ids': ['n0', 'n1', 'n2', 'n3'], 'start_frame': 0, 'end_frame': 120}]
                + [{'narration_segment_ids': [f'n{i + 4}'], 'start_frame': a, 'end_frame': b} for i, (a, b) in enumerate(spans)]}
    program = request().episode.model_dump()
    items = timeline(program, ir, compiled)
    return agenda(program, ir, compiled, items, paragraph_ids), items


def test_agenda_lights_each_item_when_the_briefing_gives_it_its_own_paragraph():
    opening, items = _programme(['n0', 'n1', 'n2', 'n3'])
    assert opening == {'start_frame': 30, 'end_frame': 120, 'highlights': [60, 90]}
    text = ass(items, 690, opening=opening, item_cards=cards(items))
    assert text.count('内容甲') >= 4 and '\\fad(0,0)' in text  # next, now, done windows without inner fades
    assert '0:00:02.00' in text and '0:00:03.00' in text


def test_a_long_opening_is_not_read_as_a_briefing():
    """Seven opening paragraphs for two items: the last two are not necessarily the items' lines."""
    ids = [f'n{i}' for i in range(10)]
    ir = {'content_structure': {'chapters': [{'narration_segment_ids': ids[:7]}, {'narration_segment_ids': ['n7']},
                                             {'narration_segment_ids': ['n8']}, {'narration_segment_ids': ['n9']}]}}
    spans = [(240, 300), (300, 360), (360, 400)]
    compiled = {'narration': {'segments': [{'id': sid, 'start_ms': i * 1000} for i, sid in enumerate(ids)]},
                'scenes': [{'narration_segment_ids': ids[:7], 'start_frame': 0, 'end_frame': 240}]
                + [{'narration_segment_ids': [f'n{i + 7}'], 'start_frame': a, 'end_frame': b} for i, (a, b) in enumerate(spans)]}
    program = request().episode.model_dump()
    items = timeline(program, ir, compiled)
    opening = agenda(program, ir, compiled, items, ids[:7])
    assert opening['highlights'] is None and opening['start_frame'] == 30


@pytest.mark.parametrize('paragraphs', [['n0'], ['n0', 'n2']])
def test_agenda_stays_unlit_when_items_and_briefing_lines_do_not_match(paragraphs):
    opening, items = _programme(paragraphs)
    assert opening['highlights'] is None and opening['end_frame'] == 120
    text = ass(items, 690, opening=opening)
    assert '本期导读' in text and text.count('内容甲') == 2  # one unlit row plus the footer label


def test_item_cards_number_items_and_stay_within_a_third_of_a_short_item():
    items = [{'index': 1, 'title': '甲', 'start_frame': 100, 'end_frame': 400},
             {'index': 2, 'title': '乙', 'start_frame': 400, 'end_frame': 460}]
    assert [(c['start_frame'], c['end_frame']) for c in cards(items)] == [(100, 154), (400, 420)]
    text = ass(items, 500, item_cards=cards(items))
    assert '01 / 02' in text and '02 / 02' in text


def test_footer_is_unchanged_without_agenda_or_cards():
    items = [{'index': 1, 'title': '甲', 'start_frame': 60, 'end_frame': 210}]
    assert ass(items, 300) == ass(items, 300, opening=None, item_cards=None)
    assert 'fad' not in ass(items, 300)
