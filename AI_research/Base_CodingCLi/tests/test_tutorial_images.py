"""Actual tutorial screenshots follow their report into the existing render worker."""
from io import BytesIO
import json
import pytest

from PIL import Image

from core import report_meta, user_materials
from core.llm import ChatResponse
from core.video import episodes, material_context, store, writer
from tests.test_video_scripts import REPORT, llm, script_json, video_env  # noqa: F401
from tests.test_video_visuals import Engine
from web.services import assistant_materials, video_render, video_workflow


def image(state, name='周报输入.png'):
    raw = BytesIO()
    Image.new('RGB', (640, 360), 'white').save(raw, 'PNG')
    return user_materials.save(str(state), 'matrix', name=name, raw=raw.getvalue(),
        text='项目周报输入，标题和已完成列表。',
        focus={'width': 640, 'height': 360, 'regions': [
            {'x1': 10, 'y1': 10, 'x2': 900, 'y2': 500, 'label': '标题和列表', 'style': 'box'}]})


def test_known_screenshot_description_and_regions_survive_without_an_extraction_call(tmp_path):
    row = image(tmp_path)
    assert row['status'] == 'ready'
    def unexpected(*args):
        raise AssertionError('the upstream description already belongs to the real screenshot')
    prepared = user_materials.prepare(str(tmp_path), row['id'], 'matrix', read_url=unexpected, describe_image=unexpected)
    assert prepared['text'] == row['text'] and prepared['focus'] == row['focus']


def test_tutorial_author_uses_step_chapters_in_the_same_existing_writer_call(llm):
    llm.replies = [script_json(300)]
    topic = {'title': '完成转换', 'content_mode': 'episode'}
    writer.write(report=REPORT, citation_check=None, topic=topic,
        column_style=episodes.brief(topic, tutorial=True), target_minutes=1, llm_config=object(), tutorial=True)
    system, request = [message['content'] for message in llm.calls[0]]
    assert '分别写成 section' in system and '按实际任务的准备、操作、结果等阶段分章' in request
    assert '不要把代码围栏' in system and '这些内容由实际截图呈现' in system
    assert '不要把每个解释步骤拆成新的内容条目' not in system + request
    assert len(llm.calls) == 1


@pytest.mark.parametrize('metadata_absolute', [False, True])
@pytest.mark.parametrize('empty_content', [False, True])
def test_report_images_freeze_with_the_script_and_reach_the_existing_durable_worker(video_env, llm, monkeypatch, metadata_absolute, empty_content):
    report = video_env / 'report.md'
    report.write_text(REPORT)
    row = image(video_env / 'state')
    context = {'ids': [row['id']], 'owner': 'matrix', 'goal': '展示实际输入与输出', 'revision': 1}
    metadata_path = str(report) if metadata_absolute else str(report.relative_to(video_env))
    report_meta.append_record(metadata_path, 'tutorial', extra={'user_materials': context})
    llm.replies = [script_json(300), script_json(300)]
    script = store.create_script('report_digest', {'title': '真实转换', 'from_report': str(report)},
                                 content_context={'column': 'tools', 'type': 'tutorial'})
    assert video_workflow.run(script['id'], llm_config=object()) == 'DRAFT'
    first = store.get_version(script['id'], 1)
    assert first['meta']['user_materials'] == context
    assert 'user_materials' not in store.get_script(script['id'])['topic']
    report_meta.append_record(metadata_path, 'tutorial', extra={'user_materials': {**context, 'ids': [], 'revision': 2}})
    assert video_workflow.run_rewrite(script['id'], llm_config=object()) == 'DRAFT'
    assert material_context.get(store.get_script(script['id']), first)['ids'] == [row['id']]
    assert store.get_version(script['id'], 2)['meta']['user_materials']['revision'] == 2
    if empty_content:
        for section in first['sections']:
            section['paragraphs'].insert(0, {'text': ' ', 'citations': []})
        first['sections'].insert(0, {'title': '空白章节', 'paragraphs': [{'text': ''}]})
        store.update_version_audit(script['id'], 1, sections=first['sections'], meta=first['meta'])
    store.update_script(script['id'], status='APPROVED', approved_version=1)
    engine = Engine()
    monkeypatch.setattr(video_render, 'get_client', engine.client)
    monkeypatch.setattr(video_render, '_video_config', lambda: {})
    seen = []
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def chat(self, messages, **kwargs):
            seen.append(messages)
            return ChatResponse(content=json.dumps({'caption': '项目周报输入', 'placements': [
                {'chapter_index': 0, 'paragraph_index': 0}], 'reason': '对应输入展示'}))
        def close(self):
            pass
    monkeypatch.setattr(assistant_materials, 'LLMClient', Model)
    render = video_render.create(store.get_script(script['id']))
    assert render['visuals']['input_kind'] == 'user_materials'
    assert render['visuals']['material_context']['revision'] == 1
    assert video_render.process(render['id']) == 'COMPLETED'
    payload = engine.posts[0]
    assert payload['production_recipe'] == 'mixed_tutorial' and 'video_form' not in payload
    assert payload['image_materials'][0]['focus'] == row['focus']
    assert engine.uploads == len(seen) == 1
    paragraphs = json.loads(seen[0][1]['content'])['paragraphs']
    assert paragraphs == [{'chapter_index': i, 'paragraph_index': j, 'text': text}
                          for i, chapter in enumerate(payload['chapters']) for j, text in enumerate(chapter['paragraphs'])]
    assert '输入输出对照段可同时展示两图' in seen[0][0]['content']
    frozen = store.get_render(render['id'])
    assert frozen['submission_request'] == payload
    assert video_render.process(render['id']) == 'COMPLETED'
    assert engine.uploads == len(seen) == 1
