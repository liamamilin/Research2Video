"""User materials survive chat, script authoring, durable handoff and image submission."""
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from core import user_materials
from core.llm import ChatResponse
from core.video import store, writer
from tests.test_assistant_api import login, fake_llm, tool
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import assistant_materials, video_workflow
from web.services.assistant_store import identity


def test_input_is_owner_scoped_and_failed_extraction_can_resume(tmp_path):
    row = user_materials.save(str(tmp_path), 'one', url='https://public.example/pricing')
    with pytest.raises(ValueError, match='无权'):
        user_materials.load(str(tmp_path), row['id'], 'two')
    def blocked(url):
        raise ValueError('temporary 403')
    with pytest.raises(ValueError, match='403'):
        user_materials.prepare(str(tmp_path), row['id'], 'one', read_url=blocked, describe_image=None)
    assert user_materials.load(str(tmp_path), row['id'], 'one')['status'] == 'error'
    got = user_materials.prepare(str(tmp_path), row['id'], 'one', read_url=lambda u: ('Plans', 'Annual $17, monthly $20'), describe_image=None)
    assert got['status'] == 'ready' and 'monthly $20' in got['text']
    assert user_materials.path(str(tmp_path), got).read_text() == row['url']


def test_chat_upload_reload_reuse_and_request_identity(client, video_env, monkeypatch):
    headers = login(client)
    original = '提供的长正文。' * 1500
    uploaded = client.post('/api/assistant/materials', headers=headers, data={'text': original, 'name': '素材'})
    assert uploaded.status_code == 200, uploaded.text
    material = uploaded.json()
    assert material['chars'] == len(original) > 8000
    def received(messages, kwargs):
        assert material['id'] in str(messages)
        return ChatResponse(content='已收到材料。')
    fake_llm(monkeypatch, [received])
    request = {'message': '先读这份材料', 'material_ids': [material['id']], 'request_id': 'material_request_1'}
    reply = client.post('/api/assistant', headers=headers, json=request).json()
    history = client.get('/api/assistant/conversations/' + reply['conversation_id']).json()
    assert history['requests'][0]['materials'][0]['id'] == material['id']
    assert client.get(material['file_url']).content.decode() == original
    # Reusing an operation key with different attachments cannot replay another request.
    different = client.post('/api/assistant', headers=headers, json={**request, 'material_ids': []})
    assert different.status_code == 409
    client.post('/api/auth/logout', headers=headers)
    login(client, 'editor')
    assert client.get(material['file_url']).status_code == 404


def test_assistant_creates_direct_narration_and_hands_off_without_research(client, video_env, monkeypatch):
    headers = login(client)
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    prose = '这是一份完整旁白，价格从二十变成二十五。\n\n增加五。条件与结论按用户原稿保留。'
    fake_llm(monkeypatch, [tool('create_video', column='deep_dive', title='提供的原稿', text=prose,
                               mode='narration', goal='按原稿生成视频', make_video=True), ChatResponse(content='已提交。')])
    reply = client.post('/api/assistant', headers=headers, json={'message': '按照提供的正文完成视频'}).json()
    assert reply['actions'][0]['ok'], reply
    refs = reply['actions'][0]['references']
    sid = next(r['id'] for r in refs if r['type'] == 'video')
    batch = next(r['id'] for r in refs if r['type'] == 'batch')
    result = video_workflow.run(sid, started_by='admin')
    assert result == 'DRAFT'
    script = store.get_script(sid)
    version = store.get_version(sid, 1)
    assert '\n\n'.join(p['text'] for s in version['sections'] for p in s['paragraphs']) == prose
    assert not script['report_path'] and not script['research_job']
    assert version['meta']['checks']['mode'] == 'on_request'
    assert client.get('/api/video/scripts/' + sid).json()['steps'][0]['state'] == 'SKIPPED'
    from core.video import autopilot_store
    assert autopilot_store.get_run(batch)['status'] == 'RUNNING'


def test_adapt_uses_complete_material_and_goal_without_forcing_article_audit(client, video_env, monkeypatch):
    from web.services.assistant_tools.video import VideoCreate
    login(client)
    user = {'username': 'admin', 'id': 1}
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    seen = {}
    def author(**kwargs):
        seen.update(kwargs)
        return writer.WrittenScript('产品套餐变化', [{'title': '说明', 'paragraphs': [{'text': '完整讲述这些材料。', 'citations': []}]}], {}, {})
    monkeypatch.setattr(writer, 'write', author)
    made = assistant_materials.create(VideoCreate(column='deep_dive', text='完整产品内容和条件', goal='面向普通用户，讲清套餐区别'), None, user)
    assert video_workflow.run(made['script_id'], started_by='admin') == 'DRAFT'
    assert '完整产品内容和条件' in seen['report'] and '面向普通用户' in seen['column_style']
    assert store.get_version(made['script_id'], 1)['meta']['input_kind'] == 'user_materials'
    # Direct input can be regenerated using the existing write step.
    monkeypatch.setattr('threading.Thread.start', lambda self: None)
    assert video_workflow.rewrite(made['script_id'])['status'] == 'WRITING'


def test_screenshot_original_is_reused_and_paragraphs_are_bound(client, video_env, monkeypatch):
    from web.settings import get_settings
    from core.video.visual_materials import attach, intent
    headers = login(client)
    buffer = BytesIO()
    Image.new('RGB', (640, 360), 'white').save(buffer, format='PNG')
    original = buffer.getvalue()
    response = client.post('/api/assistant/materials', headers=headers,
                           files={'file': ('套餐截图.png', original, 'image/png')})
    assert response.status_code == 200, response.text
    key = response.json()['id']
    owner = identity({'id': 1, 'username': 'admin'})
    user_materials.prepare(get_settings().paths.state_dir, key, owner,
                          read_url=None, describe_image=lambda raw: 'Pro 年付17美元，月付20美元。')
    calls = []
    class Model:
        def __init__(self, *a, **k):
            pass
        def chat(self, *a, **k):
            calls.append('placement')
            return ChatResponse(content='{"caption":"Claude 套餐与付款周期","placements":[{"chapter_index":0,"paragraph_index":0}]}')
        def close(self):
            pass
    monkeypatch.setattr(assistant_materials, 'LLMClient', Model)
    engine = SimpleNamespace(base_url='http://engine:8011', upload_creative_image=lambda raw, name: calls.append(raw) or {'image_id': 'image1'})
    script = {'id': 'b' * 32, 'topic': {'user_materials': {'ids': [key], 'owner': owner}}}
    version = {'version': 1, 'meta': {}, 'sections': [{'title': '价格', 'paragraphs': [{'text': '先看Pro的套餐价格。'}]}]}
    materials = assistant_materials.render_images(script, version, engine)
    repeated = assistant_materials.render_images(script, version, engine)
    assert repeated == materials and len(calls) == 2
    assert materials['image_materials'][0]['placements'] == [{'chapter_index': 0, 'paragraph_index': 0}]
    assert materials['image_materials'][0]['caption'] == 'Claude 套餐与付款周期'
    assert materials['image_materials'][0]['source']['final_url'] == response.json()['file_url']
    request = {'visual_storytelling': True, 'chapters': [{}], 'production_mode': 'fast', 'video_form': 'information_cards'}
    attach(request, materials)
    assert request['production_mode'] == 'fast' and request['video_form'] == 'information_cards'
    assert intent(script, version, request, {'enabled': True})['state'] == 'manual'
    assert client.get(response.json()['file_url']).content == original


def test_viewer_cannot_upload_or_submit_other_users_materials(client, video_env):
    headers = login(client, 'viewer')
    assert client.post('/api/assistant/materials', headers=headers, data={'text': 'body'}).status_code == 403
    assert client.post('/api/assistant', headers=headers, json={'message': '读取材料', 'material_ids': ['a' * 32]}).status_code == 409
