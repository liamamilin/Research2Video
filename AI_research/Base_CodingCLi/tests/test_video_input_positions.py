"""Every paragraph consumer must use the same positions as spoken chapters."""
import copy

import pytest

from core.video import episodes, screenshots, store, visual_materials
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import video_render


@pytest.mark.parametrize('gap', ['section', 'paragraph', 'both'])
def test_empty_authored_content_does_not_shift_navigation_or_visual_positions(video_env, gap):
    url = 'https://docs.example.com/updates'
    sections = [{'title': title, 'episode_role': 'item', 'paragraphs': [
        {'text': text, 'citations': [url]}]} for title, text in [('内容甲', '讲解第一个变化。'), ('内容乙', '讲解第二个变化。')]]
    if gap in {'paragraph', 'both'}:
        for section in sections:
            section['paragraphs'].insert(0, {'text': '  ', 'citations': []})
    if gap in {'section', 'both'}:
        sections.insert(0, {'title': '空导读', 'episode_role': 'opening', 'paragraphs': [{'text': ' '}]})
        sections.insert(2, {'title': '空条目', 'episode_role': 'item', 'paragraphs': []})
    before = copy.deepcopy(sections)
    topic = {'title': '栏目更新', 'content_mode': 'episode'}
    script = store.create_script('deep_dive', topic)
    store.add_version(script['id'], title=topic['title'], sections=sections, length_check={}, edit_type='EDITED')
    store.update_script(script['id'], status='APPROVED', approved_version=1)
    script = store.get_script(script['id'])
    version = store.get_version(script['id'], 1)
    request = video_render.create(script)['request']
    assert request['chapters'] == screenshots.chapters(version)
    assert request['episode']['items'] == [
        {'title': '内容甲', 'chapter_indexes': [0]}, {'title': '内容乙', 'chapter_indexes': [1]}]
    assert [(r['title'], r['chapter_index'], r['paragraphs']) for r in episodes.manifest(topic, sections)['items']] == [
        ('内容甲', 0, 1), ('内容乙', 1, 1)]
    assert [(n['chapter_index'], n['paragraph_index']) for n in visual_materials.needs_for(version)] == [(0, 0), (1, 0)]
    assert sections == before
