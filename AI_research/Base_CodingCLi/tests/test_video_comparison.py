"""Compare real stored versions and attempts without starting or modifying work."""
import pytest

from core.video import store
from tests.test_assistant_api import tool, login
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401


def test_version_comparison_preserves_source_text_and_has_no_write_side_effect(client, video_env):
    login(client, 'viewer')
    script = _approved_script()
    sid = script['id']
    old = store.get_version(sid, 1)
    sections = [{'title': '新的开场', 'paragraphs': [{'text': '<script>literal only</script>', 'citations': []}]}, old['sections'][1]]
    store.add_version(sid, title='新的标题', sections=sections, length_check={}, edit_type='GENERATED',
                      meta={'creative_direction': '讲清楚观众能得到什么', 'cell': {'recipe': 'flash'}})
    response = client.get(f'/api/video/scripts/{sid}/compare', params={'from_version': 1, 'to_version': 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['changes']['title'] and data['changes']['changed_sections'] == 1
    assert data['before']['sections'] == old['sections'] and data['after']['sections'] == sections
    assert 'creative_direction' in data['changes']['configuration_fields']
    assert data['before']['render'] is None and data['after']['render'] is None
    assert len(store.list_versions(sid)) == 2 and store.get_script(sid)['status'] == 'APPROVED'
    assert store.list_renders(sid) == [] and store.list_publications(sid) == []


def test_same_version_comparison_pins_distinct_attempts_and_rejects_unrelated_render(client, video_env):
    login(client, 'viewer')
    sid = _approved_script()['id']
    ids = []
    for status in ('COMPLETED', 'FAILED'):
        render = store.create_render(sid, 1, title='same', source_text='text', source_sha256='0' * 64, request={})
        store.update_render(render['id'], status=status)
        ids.append(render['id'])
    path = f'/api/video/scripts/{sid}/compare'
    response = client.get(path, params={'from_version': 1, 'to_version': 1, 'before_render': ids[0], 'after_render': ids[1]})
    data = response.json()
    assert response.status_code == 200 and data['changes']['changed_sections'] == 0
    assert data['before']['render']['id'] == ids[0] and data['after']['render']['id'] == ids[1]
    other = _approved_script()['id']
    assert client.get(f'/api/video/scripts/{other}/compare', params={'from_version': 1, 'to_version': 1, 'before_render': ids[0]}).status_code == 422
    assert client.get(path, params={'from_version': 1, 'to_version': 999}).status_code == 404


def test_assistant_can_read_actual_comparison(client, video_env, monkeypatch):
    sid = _approved_script()['id']
    result = chat(client, monkeypatch, [tool('query_video', action='compare', script_id=sid, from_version=1, to_version=1)])
    assert result['actions'][0]['ok']
    assert '?tab=compare' in result['actions'][0]['href']


def test_assistant_publish_read_works_for_isolated_sample_and_keeps_exact_version(client, video_env, monkeypatch):
    from web.services import distributions
    from web.services.assistant_tools.video import VideoQuery, query
    sid = _approved_script()['id']
    store.update_script(sid, created_by='chain-validation')
    old = store.get_version(sid, 1)
    store.add_version(sid, title='Later draft', sections=old['sections'], length_check={}, edit_type='GENERATED')
    monkeypatch.setattr(distributions, 'overview', lambda scripts: {sid: [
        {'version': 1, 'status': 'UNPUBLISHED', 'task': None},
        {'version': 2, 'status': 'PUBLISHED', 'task': {'id': 'unrelated'}}]})
    data = query(VideoQuery(action='publish', script_id=sid, version=1), None, {'role': 'viewer'})
    assert data['version'] == 1 and data['package']['title'] == old['title']
    assert len(data['targets']) == 1 and data['targets'][0]['task'] is None
    assert '验收样片' in data['publishing_block']
    assert 'version=1' in data['href']
    assert len(store.list_versions(sid)) == 2 and store.list_publications(sid) == []


@pytest.mark.parametrize(('area', 'path', 'values', 'label', 'scope'), [
    ('recipes', '/api/video/recipes/{key}', {'key': 'flash'}, '文稿配方', '所有使用此配方'),
    ('recipes', '/api/video/recipes/parts/{kind}/{name}', {'kind': 'writing', 'name': 'common'}, '共用部件', '所有引用此部件'),
    ('matrices', '/api/matrices/{key}', {'key': 'ai'}, '矩阵共用设置', '未单独覆盖'),
    ('production', '/api/production/{key}', {'key': 'flash'}, '任务视频设置', '该研究任务'),
])
def test_configuration_receipt_explains_level_impact_and_values(area, path, values, label, scope):
    from web.services.assistant_application import configuration_change, configuration_diff
    changes = configuration_diff({'content': 'writing:\n  style: old\n'}, {'content': 'writing:\n  style: new\n'})
    data = configuration_change(area, path, values, {}, changes)
    assert data['label'] == label and scope in data['scope']
    assert data['changes'][0] == {'field': 'writing.style', 'before': 'old', 'after': 'new'}
    assert data['next'] and data['existing_outputs']


def test_actual_config_conflict_then_retry_completes_request_and_preserves_failed_receipt(client, video_env, monkeypatch):
    login(client)
    old = client.get('/api/video/recipes/flash').json()
    content = old['content'] + '\n# retry acceptance\n'
    result = chat(client, monkeypatch, [
        tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'}, body={'content': content, 'expected_mtime': 0}),
        tool('read_application', action_id='GET /api/video/recipes/{key}', path={'key': 'flash'}),
        tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'}, body={'content': content, 'expected_mtime': old['mtime']}),
    ])
    assert not result['actions'][0]['ok'] and result['actions'][2]['ok']
    data = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    card = data['tasks'][0]
    assert card['status'] == 'completed' and card['changes'][0]['recovered_by'] == card['changes'][1]['id']
    from web.services.assistant_store import AssistantStore
    assert AssistantStore().receipt(result['actions'][0]['operation_id'], {'id': 1, 'username': 'admin'})['status'] == 'failed'


def test_read_only_history_is_not_reported_as_a_configuration_change(client, web_env, monkeypatch):
    from tests.test_assistant_api import write_job
    write_job(web_env)
    result = chat(client, monkeypatch, [tool('job_history', job_name='research/memory')])
    data = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    assert data['operations'] == [] and data['tasks'] == []
