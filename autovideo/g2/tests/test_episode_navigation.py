import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from pydantic import ValidationError
from g2.episode_navigation import timeline, ass, apply
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
