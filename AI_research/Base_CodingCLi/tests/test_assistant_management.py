"""Business workflows use native validation, preserve overrides and track exact uploads."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from core.video import store
from tests.test_assistant_api import tool, login, write_job
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services import assistant_management as management


def allow_whole_report(root):
    import yaml
    path = Path(root) / 'config/video_recipes/deep_dive.yaml'
    data = yaml.safe_load(path.read_text())
    data['topic']['whole_report'] = True
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    job = Path(root) / 'jobs/research/memory.yaml'
    job.write_text(job.read_text() + '\noutput: output/memory_{date}.md\n')


def result_for(response, name):
    from web.services.assistant_store import AssistantStore
    action = next(a for a in reversed(response['actions']) if a['tool'] == name)
    return AssistantStore().receipt(action['operation_id'], {'id': 1, 'username': 'admin'})['result']


def test_matrix_creation_members_and_field_merge_keep_overrides(client, video_env, web_env, monkeypatch):
    write_job(web_env)
    allow_whole_report(video_env)
    created = chat(client, monkeypatch, [tool('manage_matrix', action='create', key='management', fields={
        'name': '运营验收', 'domain': '开发者工具', 'keywords': ['Agent'], 'jobs': ['research/memory'],
        'render': {'video_speed': 1.25, 'voice_id': 'default'}})])
    assert created['actions'][0]['ok'], created
    matrix = result_for(created, 'manage_matrix')['matrix']
    assert matrix['members'][0]['production_options']
    assert 'point_short' not in [o['recipe'] for o in matrix['members'][0]['production_options']]
    recipe = matrix['members'][0]['production_options'][0]['recipe']
    config = Path(video_env) / 'config/production/override.yaml'
    config.parent.mkdir(exist_ok=True)
    config.write_text(f'job: research/memory\nrecipe: {recipe}\nrender:\n  video_speed: 1.5\n')
    read = chat(client, monkeypatch, [tool('query_matrices', key='management')], conversation_id=created['conversation_id'])
    before = result_for(read, 'query_matrices')
    assert before['production'][0]['effective_render']['video_speed'] == 1.5
    changed = chat(client, monkeypatch, [tool('manage_matrix', action='update', key='management',
        expected_revision=before['revision'], fields={'render': {'video_speed': 2}})], conversation_id=created['conversation_id'])
    assert changed['actions'][0]['ok'], changed
    after = result_for(changed, 'manage_matrix')['matrix']
    assert after['domain'] == '开发者工具' and after['keywords'] == ['Agent']
    assert after['render'] == {'video_speed': 2, 'voice_id': 'default'}
    assert after['production'][0]['overrides']['video_speed'] == after['production'][0]['effective_render']['video_speed'] == 1.5
    assert config.read_text().endswith('video_speed: 1.5\n')
    stale = chat(client, monkeypatch, [tool('manage_matrix', action='update', key='management',
        expected_revision=before['revision'], fields={'name': 'stale'})])
    assert not stale['actions'][0]['ok'] and client.get('/api/matrices/management').json()['name'] == '运营验收'
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': created['conversation_id']}).json()
    assert workspace['objects'][0]['members'][0]['job'] == 'research/memory'


def test_matrix_membership_does_not_steal_or_delete_jobs(client, web_env, monkeypatch):
    write_job(web_env)
    for key in ('one', 'two'):
        chat(client, monkeypatch, [tool('manage_matrix', action='create', key=key, fields={'name': key,
              'jobs': ['research/memory'] if key == 'one' else []})])
    revision = management.matrix_revision('two')
    failed = chat(client, monkeypatch, [tool('manage_matrix', action='add_jobs', key='two',
        expected_revision=revision, fields={'jobs': ['research/memory']})])
    assert not failed['actions'][0]['ok']
    removed = chat(client, monkeypatch, [tool('manage_matrix', action='remove_jobs', key='one',
        expected_revision=management.matrix_revision('one'), fields={'jobs': ['research/memory']})])
    assert removed['actions'][0]['ok']
    assert (web_env[0] / 'jobs/research/memory.yaml').exists()


@pytest.mark.parametrize('name,args', [
    ('manage_matrix', {'action': 'create', 'key': 'denied', 'fields': {'name': 'No'}}),
    ('manage_publishing', {'action': 'submit', 'platform': 'bilibili', 'account_uid': 'owner', 'items': []}),
    ('configure_production', {'job': 'research/memory', 'recipe': 'deep'}),
])
def test_viewer_cannot_forge_business_writes(client, monkeypatch, name, args):
    from tests.test_assistant_api import fake_llm
    from core.llm import ChatResponse
    fake_llm(monkeypatch, [tool(name, **args), ChatResponse(content='无权限')])
    response = client.post('/api/assistant', headers=login(client, 'viewer'), json={'message': '执行此操作'}).json()
    assert not response['actions'][0]['ok']


def test_task_video_overrides_can_restore_inheritance_without_recreating_config(client, video_env, web_env, monkeypatch):
    write_job(web_env)
    allow_whole_report(video_env)
    made = chat(client, monkeypatch, [tool('manage_matrix', action='create', key='inheritance', fields={
        'name': '继承测试', 'jobs': ['research/memory'], 'render': {'video_speed': 1.25}})])
    recipe = result_for(made, 'manage_matrix')['matrix']['members'][0]['production_options'][0]['recipe']
    linked = chat(client, monkeypatch, [tool('configure_production', job='research/memory', recipe=recipe,
        auto=0, render={'video_speed': 1.5, 'voice_id': 'default'})])
    assert linked['actions'][0]['ok'], linked
    key = result_for(linked, 'configure_production')['result']['key']
    old = client.get(f'/api/production/{key}/settings').json()
    restored = chat(client, monkeypatch, [tool('configure_production', config_key=key, expected_revision=old['revision'], inherit=['video_speed'])])
    assert restored['actions'][0]['ok'], restored
    receipt = result_for(restored, 'configure_production')
    assert receipt['changed_fields'] == ['render.video_speed']
    assert receipt['configuration_change']['changes'] == [{'field': 'render.video_speed', 'before': 1.5, 'after': None}]
    assert receipt['configuration_change']['next'] == '重新生成视频'
    new = client.get(f'/api/production/{key}/settings').json()
    assert new['render'] == {'voice_id': 'default'} and new['effective_render']['video_speed'] == 1.25 and new['auto'] == 0
    assert len(client.get('/api/production', params={'job': 'research/memory'}).json()) == 1
    stale = client.patch(f'/api/production/{key}/settings', headers=login(client), json={
        'expected_revision': old['revision'], 'render': {'video_speed': 2}})
    assert stale.status_code == 409 and client.get(f'/api/production/{key}/settings').json()['revision'] == new['revision']


def publishing(monkeypatch):
    from web.services import platform_manager
    monkeypatch.setattr(platform_manager, 'account', lambda: {'connected': True, 'uid': 'owner', 'name': '验收账号'})
    monkeypatch.setattr(platform_manager, 'overview', lambda: {'uploads': []})
    return platform_manager


def test_typed_auto_change_preserves_every_other_field_and_checks_revision(client, video_env, web_env, monkeypatch):
    write_job(web_env); allow_whole_report(video_env)
    path = Path(video_env) / 'config/production/auto-change.yaml'
    path.parent.mkdir(exist_ok=True)
    original = 'job: research/memory\nrecipe: deep_dive\nauto: 1\ncustom: keep\nrender:\n  video_speed: 1.5\n'
    path.write_text(original)
    settings = client.get('/api/production/auto-change/settings', headers=login(client)).json()
    changed = chat(client, monkeypatch, [tool('configure_production', config_key='auto-change',
        expected_revision=settings['revision'], auto=0)])
    assert changed['actions'][0]['ok'], changed
    import yaml
    saved = yaml.safe_load(path.read_text()); expected = yaml.safe_load(original); expected['auto'] = 0
    assert saved == expected
    stale = chat(client, monkeypatch, [tool('configure_production', config_key='auto-change',
        expected_revision=settings['revision'], auto=2)])
    assert not stale['actions'][0]['ok'] and yaml.safe_load(path.read_text()) == expected


def test_prepare_only_reads_exact_render_and_persists_review_card(client, video_env, monkeypatch):
    platform = publishing(monkeypatch)
    script = _approved_script()
    render = store.create_render(script['id'], 1, title='发布测试', source_text='稿', source_sha256='0' * 64, request={})
    monkeypatch.setattr(platform, 'preview', lambda key: {'title': '准备标题', 'description': '简介与来源', 'tags': ['工具'], 'tid': 201, 'warnings': ['参考信息']})
    monkeypatch.setattr(platform, 'enqueue', lambda *args: pytest.fail('prepare must not upload'))
    response = chat(client, monkeypatch, [tool('query_publishing', action='prepare', platform='bilibili', render_ids=[render['id']])])
    assert response['actions'][0]['ok']
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': response['conversation_id']}).json()
    card = workspace['tasks'][0]
    assert card['status'] == 'prepared' and not card['active']
    prepared = card['preparations'][0]
    assert prepared['version'] == 1 and prepared['render_id'] == render['id'] and prepared['account_uid'] == 'owner'
    assert prepared['fields']['title'] == '准备标题' and 'version=1' in prepared['href']


def test_partial_batch_keeps_success_and_live_task_never_claims_submitted_is_published(client, video_env, monkeypatch):
    platform = publishing(monkeypatch)
    calls = []
    row = {'id': 'upload-one', 'uid': 'owner', 'script_id': 'script', 'version': 2, 'render_id': 'good', 'title': '准确版本', 'status': 'QUEUED'}
    def enqueue(key, fields):
        calls.append((key, fields))
        if key == 'bad':
            raise HTTPException(409, '此版本已有任务')
        return row
    monkeypatch.setattr(platform, 'enqueue', enqueue)
    monkeypatch.setattr(platform, 'upload_task', lambda key: {**row})
    fields = {'title': '发布', 'description': '简介', 'tags': ['测试'], 'tid': 201}
    response = chat(client, monkeypatch, [tool('manage_publishing', action='submit', platform='bilibili', account_uid='owner',
        items=[{'render_id': 'good', 'fields': fields}, {'render_id': 'bad', 'fields': fields}])])
    assert not response['actions'][0]['ok'] and len(calls) == 2
    def card():
        return client.get('/api/assistant/workspace', params={'conversation_id': response['conversation_id']}).json()['tasks'][0]
    assert card()['publications'][0]['active'] and card()['publications'][0]['version'] == 2
    assert card()['publications'][0]['account_name'] == '验收账号'
    assert card()['failures'] and len(card()['publications']) == 1
    row['status'] = 'SUBMITTED'
    assert card()['publications'][0]['state'] == 'running' and card()['active']
    row['status'] = 'PUBLISHED'
    assert card()['publications'][0]['state'] == 'completed'
    assert 'task=upload-one' in card()['publications'][0]['href']
    assert len(calls) == 2  # Projection never resubmits.


def test_changed_account_and_deferred_platform_never_dispatch(client, monkeypatch):
    platform = publishing(monkeypatch)
    monkeypatch.setattr(platform, 'enqueue', lambda *args: pytest.fail('must not dispatch'))
    response = chat(client, monkeypatch, [tool('manage_publishing', action='submit', platform='bilibili', account_uid='other',
        items=[{'render_id': 'one', 'fields': {}}])])
    assert not response['actions'][0]['ok'] and '账号' in response['actions'][0]['message']
    with pytest.raises(ValueError):
        management.platform_path('../../settings')


def test_read_existing_publication_does_not_become_generation_task(client, monkeypatch):
    platform = publishing(monkeypatch)
    monkeypatch.setattr(platform, 'upload_task', lambda key: {
        'id': key, 'status': 'PUBLISHED', 'uid': 'owner', 'version': 3,
        'script_id': 'existing-script', 'render_id': 'existing-render', 'title': '已发布的视频'})
    response = chat(client, monkeypatch, [tool('query_publishing', action='task', platform='bilibili', task_id='existing-upload')])
    assert 'task=existing-upload' in result_for(response, 'query_publishing')['href']
    result = client.get('/api/assistant/workspace', params={'conversation_id': response['conversation_id']})
    assert result.status_code == 200
    card = result.json()['tasks'][0]
    assert card['videos'] == [] and card['status'] == 'completed' and not card['active']
    upload = card['publications'][0]
    assert upload['version'] == 3 and upload['account_name'] == '验收账号'
    assert 'task=existing-upload' in upload['href']


def test_repeated_publish_polling_returns_worker_handoff(client, monkeypatch):
    platform = publishing(monkeypatch)
    row = {'id': 'one', 'status': 'QUEUED', 'version': 1, 'script_id': 's'}
    monkeypatch.setattr(platform, 'enqueue', lambda *args: row)
    monkeypatch.setattr(platform, 'upload_task', lambda key: row)
    from tests.test_assistant_api import fake_llm
    fake_llm(monkeypatch, [tool('manage_publishing', action='submit', platform='bilibili', account_uid='owner', items=[
        {'render_id': 'r', 'fields': {'title': '稿', 'description': '', 'tags': ['工具'], 'tid': 201}}]),
        tool('query_publishing', action='task', platform='bilibili', task_id='one'),
        tool('query_publishing', action='task', platform='bilibili', task_id='one')])
    response = client.post('/api/assistant', headers=login(client), json={'message': '投稿并查询进度'}).json()
    assert len(response['actions']) == 2 and '工作区' in response['answer'] and not response.get('continuation')
