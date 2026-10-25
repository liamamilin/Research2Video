"""Application harness acceptance: durable outcomes, domain rules and credentials."""
import json
import threading
import time
from pathlib import Path

import pytest
from core.llm import ChatResponse
from web.services import assistant_tools
from web.services.assistant_store import AssistantStore
from tests.test_assistant_api import login, tool, fake_llm, write_job


def command(client, monkeypatch, name, arguments, username='admin', **request):
    fake_llm(monkeypatch, [tool(name, **arguments), ChatResponse(content='已处理。')])
    response = client.post('/api/assistant', headers=login(client, username),
                           json={'message': '执行已明确指定的操作', **request})
    assert response.status_code == 200, response.text
    return response.json()


def test_recurring_job_followup_and_request_deduplication(client, web_env, monkeypatch):
    from web.services import managed_scheduler as managed
    monkeypatch.setattr(managed, 'ensure_online', lambda: {'online': True})
    first = command(client, monkeypatch, 'create_job', {'name': 'Memory', 'prompt': 'Research memory with citations',
                    'recurrence': {'schedule': '0 9 * * 1', 'timezone': 'Asia/Shanghai'}}, request_id='recurring-command-001')
    assert first['actions'][0]['detail']['next_runs']
    assert first['actions'][0]['detail']['timezone'] == 'Asia/Shanghai'
    assert first['actions'][0]['href'] == '/jobs/research%2Fmemory'
    plan = managed.store().list()[0]
    assert plan['job_name'] == 'research/memory'
    from web.services.assistant_store import AssistantStore
    receipt = AssistantStore().receipt(first['actions'][0]['operation_id'], {'id': 1, 'username': 'admin'})['result']
    assert receipt['schedule_href'] == '/scheduler?plan=' + plan['id']
    cid = first['conversation_id']
    changed = command(client, monkeypatch, 'manage_schedule', {'action': 'update', 'schedule_id': plan['id'], 'schedule': '0 9 * * 5'}, conversation_id=cid)
    assert changed['actions'][0]['ok']
    assert managed.store().get(plan['id'])['timezone'] == 'Asia/Shanghai'
    for action, enabled in [('pause', False), ('resume', True)]:
        command(client, monkeypatch, 'manage_schedule', {'action': action, 'schedule_id': plan['id']}, conversation_id=cid)
        assert bool(managed.store().get(plan['id'])['enabled']) is enabled
    # No LLM is constructed and no write is replayed for an identical ID.
    from web.routes import assistant
    monkeypatch.setattr(assistant, '_llm', lambda **_: pytest.fail('duplicate request ran the LLM'))
    replay = client.post('/api/assistant', headers=login(client), json={
        'message': '执行已明确指定的操作', 'request_id': 'recurring-command-001', 'conversation_id': cid})
    assert replay.json()['actions'] == first['actions']
    assert len(managed.store().list()) == 1
    restored = client.get(f'/api/assistant/conversations/{cid}').json()
    assert {r['type'] for r in restored['refs']} >= {'job', 'schedule'}
    assert len(restored['requests']) == 4


@pytest.mark.parametrize('username', ['viewer', 'editor'])
@pytest.mark.parametrize('name', ['query_schedules', 'manage_schedule', 'query_configuration', 'manage_configuration', 'manage_users', 'maintain_service', 'test_notification', 'diagnose_application'])
def test_forged_admin_tools_still_enforce_permissions(client, monkeypatch, username, name):
    result = command(client, monkeypatch, name, {}, username=username)
    assert not result['actions'][0]['ok']
    assert '权限' in result['actions'][0]['message']


def test_conversations_and_protected_cards_belong_to_current_user(client, monkeypatch):
    first = command(client, monkeypatch, 'request_protected_input', {'kind': 'create_token', 'token_name': 'personal'})
    cid = first['conversation_id']
    card = first['actions'][0]['input_card']['id']
    headers = login(client, 'editor')
    assert client.get(f'/api/assistant/conversations/{cid}').status_code == 404
    assert client.post(f'/api/assistant/inputs/{card}', headers=headers, json={}).status_code == 404
    assert client.post(f'/api/assistant/requests/{first["request_id"]}/stop', headers=headers).status_code == 404


def test_token_shown_once_and_absent_from_chat_or_receipts(client, web_env, monkeypatch):
    first = command(client, monkeypatch, 'request_protected_input', {'kind': 'create_token', 'token_name': 'private'})
    card = first['actions'][0]['input_card']['id']
    headers = login(client)
    result = client.post(f'/api/assistant/inputs/{card}', json={}, headers=headers)
    assert result.status_code == 200, result.text
    token = result.json()['token']
    assert token.startswith('air_')
    assert client.post(f'/api/assistant/inputs/{card}', json={}, headers=headers).status_code == 409
    assert token not in client.get('/api/assistant/conversation').text
    assert token not in client.get(f'/api/assistant/inputs/{card}').text
    assert token.encode() not in (web_env[0] / 'state' / 'assistant.db').read_bytes()


def test_create_user_password_bypasses_model_and_storage(client, web_env, monkeypatch):
    first = command(client, monkeypatch, 'request_protected_input', {'kind': 'create_user', 'username': 'new-person', 'role': 'editor'})
    card = first['actions'][0]['input_card']['id']
    secret = 'never-store-this-password-93482'
    result = client.post(f'/api/assistant/inputs/{card}', headers=login(client), json={'password': secret})
    assert result.status_code == 200, result.text
    assert result.json()['username'] == 'new-person'
    assert secret.encode() not in (web_env[0] / 'state' / 'assistant.db').read_bytes()
    assert secret not in client.get('/api/assistant/conversation').text
    assert client.post('/api/auth/login', json={'username': 'new-person', 'password': secret}).status_code == 200


def test_safe_configuration_projection_and_direct_rollback(client, web_env, monkeypatch):
    config = web_env[0] / 'config' / 'system.yaml'
    config.write_text('ai:\n  model: test\n  api_key: tiny\n  extra_headers:\n    Authorization: Bearer hidden-header\nbudget:\n  monthly_usd_limit: 12\n')
    first = command(client, monkeypatch, 'query_configuration', {'action': 'system'})
    # Verify the actual model context, rather than only visible summaries.
    from web.services.assistant_configuration import view
    from web.auth.db import get_user_by_username
    user = get_user_by_username('admin')
    projected = view(user)
    assert 'tiny' not in json.dumps(projected)
    assert 'hidden-header' not in json.dumps(projected)
    changed = command(client, monkeypatch, 'manage_configuration', {'action': 'apply', 'patch': {'budget': {'monthly_usd_limit': 18}}, 'expected_mtime': config.stat().st_mtime})
    key = changed['actions'][0]['rollback_id']
    assert '18' in config.read_text()
    # No model call needed to undo a configuration that breaks the model.
    response = client.post(f'/api/assistant/rollback/{key}', headers=login(client))
    assert response.status_code == 200, response.text
    assert 'monthly_usd_limit: 12' in config.read_text()


def test_failed_candidate_never_overwrites_active_config(client, web_env, monkeypatch):
    from web.services import assistant_configuration
    path = web_env[0] / 'config' / 'system.yaml'
    path.write_text('ai:\n  model: current\n')
    original = path.read_text()
    monkeypatch.setattr(assistant_configuration, 'test_candidate', lambda *args: {'ok': False, 'error': 'tool stream unsupported'})
    result = command(client, monkeypatch, 'manage_configuration', {'action': 'apply', 'patch': {'ai': {'model': 'candidate'}}, 'expected_mtime': path.stat().st_mtime})
    assert not result['actions'][0]['ok']
    assert path.read_text() == original


def test_reports_explicit_historical_scope_and_read_expansion(client, web_env, monkeypatch):
    from web.indexer import scanner
    from web.routes import qa
    monkeypatch.setattr(qa, '_embed_query', lambda *_: [])
    root = web_env[0] / 'output'
    old = 'practical_ai_intelligence/2026-08-01/07_opportunities.md'
    new = 'practical_ai_intelligence/2026-10-01/07_opportunities.md'
    for name, content in [(old, '# Older report\nMemory historical finding'), (new, '# Newer report\nMemory new finding')]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    scanner.full_scan(str(root))
    from web.services.assistant_tools.reports import ReportQuery, query
    data = query(ReportQuery(action='search', query='Memory', since='2026-08-01', until='2026-08-31'), None, {'role': 'viewer'})
    assert data['total_in_scope'] == 1
    assert {item['path'] for item in data['items']} == {old}
    assert data['scope']['latest_round'] is False
    assert '穷尽' in data['coverage']
    read = command(client, monkeypatch, 'query_reports', {'action': 'read', 'path': old}, username='viewer')
    assert read['actions'][0]['ok']
    updated = command(client, monkeypatch, 'update_report', {'path': old, 'favorite': True, 'tags': ['memory']})
    assert updated['actions'][0]['ok']
    assert client.get('/api/reports/meta', params={'path': old}).json()['favorite'] is True


def test_report_symlink_cannot_escape_output(client, web_env, monkeypatch):
    secret = web_env[0] / 'config' / 'private.md'
    secret.write_text('credential material')
    (web_env[0] / 'output' / 'escape.md').symlink_to(secret)
    result = command(client, monkeypatch, 'query_reports', {'action': 'read', 'path': 'escape.md'})
    assert not result['actions'][0]['ok']


def test_background_reindex_has_durable_progress(client, web_env, monkeypatch):
    (web_env[0] / 'output' / 'example.md').write_text('# Indexed evidence\nMemory update')
    result = command(client, monkeypatch, 'rebuild_index', {'include_vectors': False}, username='editor')
    key = result['actions'][0]['background_id']
    for _ in range(100):
        status = client.get(f'/api/assistant/background/{key}').json()
        if status['status'] in ('completed', 'failed'):
            break
        time.sleep(0.01)
    assert status['status'] == 'completed', status
    assert status['result']['indexed'] == 1


def test_recovered_request_exposes_unknown_operations_without_replaying(client, web_env):
    from web.auth.db import get_user_by_username
    user = get_user_by_username('admin')
    storage = AssistantStore()
    cid = storage.new_conversation(user)
    storage.begin(user, 'interrupted-operation', cid, 'create job')
    storage.operation('interrupted-operation', 'create_job', {'name': 'potentially-created'})
    with storage.db() as db:
        db.execute("UPDATE requests SET owner='previous-server' WHERE id='interrupted-operation'")
    login(client)
    record = client.get(f'/api/assistant/conversations/{cid}').json()['requests'][0]
    assert record['status'] == 'interrupted'
    assert '结果待核验' in record['result']['actions'][0]['message']


def test_scheduled_report_reference_is_verified_even_before_indexing(client, web_env, monkeypatch):
    from core.schedule_store import ScheduleStore
    from web.services import managed_scheduler as managed
    from web.indexer import db
    path = write_job(web_env)
    storage = managed.store()
    now = time.time()
    plan = storage.save('research/memory', '* * * * *', now=now-120)
    storage.enqueue_due(now)
    run = storage.claim(now)
    assert storage.start(run['id'], 123, now)
    output = web_env[0] / 'output' / 'new_report.md'
    output.write_text('# Actual generated report\nVerified report body')
    storage.finish(run['id'], 'success', output_path=str(output))
    assert db.get_report('new_report.md') is None
    result = command(client, monkeypatch, 'query_run', {'source': 'scheduled', 'run_id': run['id']})
    references = result['actions'][0]['references']
    assert any(r['type'] == 'report' and r['id'] == 'new_report.md' for r in references)
    deleted = command(client, monkeypatch, 'delete_job', {'job_name': 'research/memory'})
    assert deleted['actions'][0]['ok']
    assert output.is_file()


def test_report_metadata_does_not_create_a_fake_job_reference(client, web_env, monkeypatch):
    from web.indexer import scanner
    report = web_env[0] / 'output' / 'analysis' / '2026-10-01.md'
    report.parent.mkdir()
    report.write_text('# Report\nEvidence')
    scanner.full_scan(str(web_env[0] / 'output'))
    result = command(client, monkeypatch, 'update_report', {'path': 'analysis/2026-10-01.md', 'favorite': True})
    action = result['actions'][0]
    assert action['job_name'] is None
    assert all(r['type'] != 'job' for r in action['references'])


def test_create_job_keeps_verified_report_provenance(client, web_env, monkeypatch):
    import yaml
    report = web_env[0] / 'output' / 'source.md'
    report.write_text('# Evidence\nNeed a memory cost comparison')
    result = command(client, monkeypatch, 'create_job', {'name': 'evidence-job', 'prompt': 'Compare memory costs',
                     'source_references': [{'type': 'report', 'id': 'source.md'}]})
    assert result['actions'][0]['ok']
    saved = yaml.safe_load((web_env[0] / 'jobs/research/evidence-job.yaml').read_text())
    assert saved['source_references'] == [{'type': 'report', 'id': 'source.md'}]
    bad = command(client, monkeypatch, 'create_job', {'name': 'bad-provenance', 'prompt': 'Research',
                  'source_references': [{'type': 'report', 'id': 'missing.md'}]})
    assert not bad['actions'][0]['ok']
    assert not (web_env[0] / 'jobs/research/bad-provenance.yaml').exists()


def test_provider_switch_replaces_stale_headers_without_exposing_them(client, web_env, monkeypatch):
    import yaml
    from web.services import assistant_configuration
    path = web_env[0] / 'config/system.yaml'
    path.write_text('ai:\n  model: former-model\n  base_url: https://former.example/v1\n  extra_headers:\n    Authorization: stale-provider-secret\n    Old-Session: stale-session\n')
    monkeypatch.setattr(assistant_configuration, 'test_candidate', lambda *args: {'ok': True})
    result = command(client, monkeypatch, 'manage_configuration', {'action': 'apply', 'provider_id': 'open-design', 'expected_mtime': path.stat().st_mtime})
    assert result['actions'][0]['ok']
    cfg = yaml.safe_load(path.read_text())['ai']
    assert cfg['base_url'] == 'https://amr-link.open-design.ai/v1'
    assert cfg['model'] == 'deepseek-v4.1-flash'
    assert not cfg['extra_headers']
    assert 'stale-provider-secret' not in client.get('/api/assistant/conversation').text


def test_private_request_headers_bypass_chat_and_have_a_direct_rollback(client, web_env, monkeypatch):
    from web.services import assistant_configuration
    path = web_env[0] / 'config/system.yaml'
    path.write_text('ai:\n  model: test-model\n  extra_headers:\n    Old-Header: former\n')
    observed = []
    def probe(*args, **kwargs):
        observed.append(kwargs['private_headers'])
        return {'ok': True}
    monkeypatch.setattr(assistant_configuration, 'test_candidate', probe)
    first = command(client, monkeypatch, 'request_protected_input', {'kind': 'set_headers'})
    key = first['actions'][0]['input_card']['id']
    private = 'header-secret-not-in-the-chat-91234'
    result = client.post(f'/api/assistant/inputs/{key}', headers=login(client), json={'value': json.dumps({'X-Private': private})})
    assert result.status_code == 200, result.text
    assert observed == [{'X-Private': private}]
    assert private in path.read_text() and 'Old-Header' not in path.read_text()
    assert private not in client.get('/api/assistant/conversation').text
    assert private.encode() not in (web_env[0] / 'state/assistant.db').read_bytes()
    rollback = result.json()['rollback_id']
    assert client.post(f'/api/assistant/rollback/{rollback}', headers=login(client)).status_code == 200
    assert 'Old-Header' in path.read_text() and private not in path.read_text()


def test_clone_keeps_advanced_configuration_and_separate_output(client, web_env, monkeypatch):
    import yaml
    source = write_job(web_env)
    data = yaml.safe_load(source.read_text())
    data.update(sources=['https://example.com/evidence'], runtime={'timeout_seconds': 321})
    source.write_text(yaml.safe_dump(data))
    result = command(client, monkeypatch, 'clone_job', {'job_name': 'research/memory', 'name': 'memory-copy', 'category': 'analysis'})
    assert result['actions'][0]['ok']
    copied = yaml.safe_load((web_env[0] / 'jobs/analysis/memory-copy.yaml').read_text())
    assert copied['sources'] == data['sources']
    assert copied['runtime']['timeout_seconds'] == 321
    assert copied['custom_field'] == 'keep-me'
    assert copied['output'] == 'output/analysis_memory-copy_{date}.md'


def test_clone_conflict_reports_the_created_partial_object(client, web_env, monkeypatch):
    from fastapi import HTTPException
    from web.routes import jobs
    write_job(web_env)
    def conflict(*args, **kwargs):
        raise HTTPException(409, '任务被另一页面编辑')
    monkeypatch.setattr(jobs, 'update_yaml', conflict)
    result = command(client, monkeypatch, 'clone_job', {'job_name': 'research/memory', 'name': 'partial-copy'})
    action = result['actions'][0]
    assert not action['ok']
    assert 'research/partial-copy' in action['message']
    assert action['href'] == '/jobs/research%2Fpartial-copy'
    assert (web_env[0] / 'jobs/research/partial-copy.yaml').is_file()
