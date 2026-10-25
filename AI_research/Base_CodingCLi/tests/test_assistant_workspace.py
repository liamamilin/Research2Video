"""Workspace and general application actions use real auth, APIs and isolated storage."""
import json

from core.llm import ChatResponse
from tests.test_assistant_api import login, fake_llm, tool, write_job
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401


def chat(client, monkeypatch, steps, message='请完成这个操作', **fields):
    fake_llm(monkeypatch, [*steps, ChatResponse(content='已处理。')])
    return client.post('/api/assistant', headers=login(client), json={'message': message, **fields}).json()


def test_catalog_exposes_real_schemas_without_credentials(client, monkeypatch):
    result = chat(client, monkeypatch, [tool('application_actions', action_id='PUT /api/video/scripts/{script_id}')])
    assert result['actions'][0]['ok']
    from web.services.assistant_store import AssistantStore
    receipt = AssistantStore().receipt(result['actions'][0]['operation_id'], {'username': 'admin', 'id': 1})
    schema = receipt['result']
    assert 'ScriptEditIn' in schema['schemas'] and schema['requestBody']


def test_matrix_create_read_update_through_real_api(client, monkeypatch):
    result = chat(client, monkeypatch, [
        tool('operate_application', action_id='POST /api/matrices', body={'key': 'test', 'name': '测试矩阵'}),
        tool('read_application', action_id='GET /api/matrices/{key}', path={'key': 'test'}),
        tool('operate_application', action_id='PUT /api/matrices/{key}', path={'key': 'test'},
             body={'name': '测试矩阵', 'render': {'video_speed': 1.25}}),
    ])
    assert all(a['ok'] for a in result['actions']), result
    assert client.get('/api/matrices/test').json()['name'] == '测试矩阵'
    assert any(ref['type'] == 'matrix' for a in result['actions'] for ref in a['references'])


def test_viewer_cannot_forge_general_write(client, monkeypatch):
    fake_llm(monkeypatch, [tool('operate_application', action_id='POST /api/matrices', body={'key': 'bad', 'name': 'bad'}), ChatResponse(content='无权限')])
    result = client.post('/api/assistant', headers=login(client, 'viewer'), json={'message': '创建矩阵'}).json()
    assert not result['actions'][0]['ok']
    assert client.get('/api/matrices/bad').status_code == 404


def test_general_tools_cannot_escape_catalog_or_write_with_read_tool(client, monkeypatch):
    result = chat(client, monkeypatch, [
        tool('read_application', action_id='POST /api/matrices', body={'key': 'bad', 'name': 'bad'}),
        tool('operate_application', action_id='POST /api/platforms/bilibili/credentials', body={'credentials': {'cookie': 'private'}}),
        tool('read_application', action_id='GET /api/matrices/{key}', path={'key': '../config'}),
    ])
    assert all(not a['ok'] for a in result['actions'])


def test_context_history_and_workspace_are_account_scoped(client, web_env, monkeypatch):
    write_job(web_env)
    result = chat(client, monkeypatch, [], context={'kind': 'job', 'id': 'research/memory'})
    cid = result['conversation_id']
    history = client.get('/api/assistant/conversations').json()
    assert history[0]['id'] == cid and history[0]['request_count'] == 1
    assert client.get(f'/api/assistant/conversations/{cid}').json()['context']['id'] == 'research/memory'
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': cid}).json()
    assert workspace['objects'][0]['href'] == '/jobs/research%2Fmemory'
    login(client, 'viewer')
    assert client.get('/api/assistant/conversations').json() == []
    assert client.get('/api/assistant/workspace', params={'conversation_id': cid}).status_code >= 400


def test_current_body_version_edit_and_live_render_projection(client, video_env, monkeypatch):
    script = _approved_script('原稿标题')
    sid = script['id']
    login(client)
    detail = client.get('/api/video/scripts/' + sid).json()
    sections = detail['version_shown']['sections']
    result = chat(client, monkeypatch, [
        tool('read_application', action_id='GET /api/video/scripts/{script_id}', path={'script_id': sid}),
        tool('operate_application', action_id='PUT /api/video/scripts/{script_id}', path={'script_id': sid},
             body={'title': '经过助手调整后的标题', 'sections': sections, 'base_version': detail['current_version']}),
    ], context={'kind': 'video', 'id': sid})
    assert all(a['ok'] for a in result['actions']), result
    cid = result['conversation_id']
    before = client.get('/api/assistant/workspace', params={'conversation_id': cid}).json()
    assert before['objects'][0]['version'] == detail['current_version'] + 1
    assert before['operations'][0]['status'] == 'completed'
    from core.video import store
    render = store.create_render(sid, detail['current_version'], title='test', source_text='test', source_sha256='0' * 64, request={})
    store.update_render(render['id'], status='COMPLETED')
    after = client.get('/api/assistant/workspace', params={'conversation_id': cid}).json()
    assert after['objects'][0]['video_version'] == detail['current_version']


def test_explicit_context_removal_survives_refresh(client, web_env, monkeypatch):
    write_job(web_env)
    first = chat(client, monkeypatch, [], context={'kind': 'job', 'id': 'research/memory'})
    chat(client, monkeypatch, [], conversation_id=first['conversation_id'], context=None)
    assert client.get('/api/assistant/conversations/' + first['conversation_id']).json()['context'] is None


def test_recipe_save_keeps_native_backups_and_conflict_checks(client, video_env, monkeypatch):
    login(client)
    original = client.get('/api/video/recipes/flash').json()
    changed = original['content'].replace('name:', 'name:', 1) + '\n# 助手迭代验证\n'
    result = chat(client, monkeypatch, [
        tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'},
             body={'content': changed, 'expected_mtime': original['mtime']}),
        tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'},
             body={'content': original['content'], 'expected_mtime': original['mtime']}),
    ])
    assert result['actions'][0]['ok'] and not result['actions'][1]['ok']
    assert client.get('/api/video/recipes/flash').json()['content'] == changed
    assert list((video_env / 'state' / 'backups').glob('video_recipe_flash_*.yaml'))


def test_rerun_uses_existing_workflow_and_reports_submission(client, video_env, monkeypatch):
    script = _approved_script()
    from web.services import video_executor
    called = []
    monkeypatch.setattr(video_executor, 'rerun', lambda sid, step, by: called.append((sid, step, by)))
    result = chat(client, monkeypatch, [tool('operate_application',
        action_id='POST /api/video/scripts/{script_id}/steps/{step}/rerun',
        path={'script_id': script['id'], 'step': 'write'})])
    assert called == [(script['id'], 'write', 'admin')]
    assert result['actions'][0]['ok'] and '已提交' in result['actions'][0]['message']


def test_continue_video_is_durable_deduplicated_and_uses_batch_worker(client, video_env, monkeypatch):
    from core.video import autopilot_store as ap, store
    from web.services import video_autopilot as pilot, video_render
    script = _approved_script('完整视频流程验证')
    started = []
    monkeypatch.setattr(video_render, 'start', lambda key, *args, **kwargs: started.append(key))
    result = chat(client, monkeypatch, [tool('continue_video', script_id=script['id']), tool('continue_video', script_id=script['id'])])
    assert all(action['ok'] for action in result['actions'])
    batches = [ref['id'] for action in result['actions'] for ref in action['references'] if ref['type'] == 'batch']
    assert len(batches) == 2 and batches[0] == batches[1]
    run = ap.get_run(batches[0])
    assert run['stage'] == 'ITEMS' and run['items'][0]['script_id'] == script['id']
    # The normal dispatcher advances this row; no assistant worker or model is involved.
    render = store.create_render(script['id'], 1, title='test', source_text='test', source_sha256='0' * 64, request={})
    store.update_render(render['id'], status='COMPLETED')
    pilot._tick_run(run)
    assert ap.get_run(batches[0])['items'][0]['state'] == 'DONE'
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    assert any(o['kind'] == 'batch' and o['status'] == 'DONE' for o in workspace['objects'])


def test_redaction_preserves_public_token_rates_and_hides_credentials():
    from web.services.assistant_safety import redact
    assert redact('本地模型每秒百 token /s') == '本地模型每秒百 token /s'
    assert redact('token=private-value') == 'token=[已隐藏]'
    assert redact({'cookies': {'session': 'private'}})['cookies'] == '[已配置]'


def test_clear_context_is_persistent_and_cannot_change_other_users(client, web_env, monkeypatch):
    write_job(web_env)
    result = chat(client, monkeypatch, [], context={'kind': 'job', 'id': 'research/memory'})
    cid = result['conversation_id']
    assert client.put(f'/api/assistant/conversations/{cid}/context', headers=login(client), json={'context': None}).status_code == 200
    assert client.get(f'/api/assistant/conversations/{cid}').json()['context'] is None
    assert client.put(f'/api/assistant/conversations/{cid}/context', headers=login(client, 'viewer'), json={'context': None}).status_code == 404


def test_publication_dispatch_uses_native_schema_and_service_without_external_publish(client, monkeypatch):
    from web.services import platform_manager
    calls = []
    monkeypatch.setattr(platform_manager, 'enqueue', lambda render_id, fields: calls.append((render_id, fields)) or {'id': 'publish-job', 'status': 'QUEUED'})
    result = chat(client, monkeypatch, [tool('operate_application', action_id='POST /api/platforms/bilibili/uploads', body={
        'render_id': 'render-one', 'title': '测试发布任务', 'description': '简介', 'tags': ['测试'], 'tid': 201,
    })])
    assert calls[0][0] == 'render-one' and calls[0][1]['title'] == '测试发布任务'
    assert result['actions'][0]['ok'] and '已提交' in result['actions'][0]['message']
    assert any(ref['type'] == 'platform' for ref in result['actions'][0]['references'])


def test_platform_workspace_follows_current_registered_platform_prefix(client, monkeypatch):
    from web.services import platform_manager
    monkeypatch.setattr(platform_manager, 'overview', lambda: {'uploads': [{'id': 'one', 'status': 'QUEUED', 'title': '待发布'}]})
    headers = login(client)
    cid = client.post('/api/assistant/conversations', headers=headers).json()['id']
    response = client.get('/api/assistant/workspace', params={'conversation_id': cid, 'context': json.dumps({'kind': 'platform', 'id': 'bilibili'})})
    assert response.status_code == 200
    item = response.json()['objects'][0]
    assert item['status'] == '平台管理' and item['tasks'][0]['id'] == 'one' and item['active']
