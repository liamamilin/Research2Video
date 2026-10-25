"""Source edits use native auth, revisions, backups and real reference resolution."""
from tests.test_assistant_api import login, write_job, tool
from tests.test_assistant_workspace import chat
from web.services import source_registry


def test_crud_preserves_unknown_fields_and_rejects_stale_write(client, web_env):
    headers = login(client)
    path = web_env[0] / 'config/sources.yaml'
    path.write_text('kept:\n  spec: github:org/repo\n  tags: [dev]\n  custom: keep-me\n')
    revision = client.get('/api/sources').json()['revision']
    made = client.post('/api/sources', headers=headers, json={'id': 'feed', 'expected_revision': revision,
        'fields': {'name': 'Feed', 'spec': 'rss:https://example.org/feed', 'tags': ['dev']}})
    assert made.status_code == 201, made.text
    updated = client.patch('/api/sources/feed', headers=headers, json={'expected_revision': made.json()['revision'], 'fields': {'note': 'public'}})
    assert updated.status_code == 200
    assert client.patch('/api/sources/feed', headers=headers, json={'expected_revision': revision, 'fields': {'name': 'lost'}}).status_code == 409
    current = client.get('/api/sources/feed').json()
    assert current['name'] == 'Feed' and current['note'] == 'public'
    assert 'keep-me' in path.read_text()
    backups = list((web_env[0] / 'state/backups').glob('sources_*.yaml'))
    assert len(backups) == 2 and backups[0].read_text() != backups[1].read_text()
    assert client.delete('/api/sources/feed', headers=headers, params={'expected_revision': current['revision']}).status_code == 200
    assert client.get('/api/sources/feed').status_code == 404


def test_direct_and_tag_references_prevent_breaking_jobs(client, web_env):
    headers = login(client)
    write_job(web_env)
    job = web_env[0] / 'jobs/research/memory.yaml'
    job.write_text(job.read_text() + "\nsources: ['tag:dev']\n")
    path = web_env[0] / 'config/sources.yaml'
    path.write_text('feed:\n  spec: github:org/repo\n  tags: [dev]\n')
    current = client.get('/api/sources/feed').json()
    assert current['used_by'][0]['references'] == ['tag:dev']
    response = client.delete('/api/sources/feed', headers=headers, params={'expected_revision': current['revision']})
    assert response.status_code == 409 and 'research/memory' in response.text
    assert client.patch('/api/sources/feed', headers=headers, json={'expected_revision': current['revision'], 'fields': {'tags': []}}).status_code == 409
    assert client.patch('/api/sources/feed', headers=headers, json={'expected_revision': current['revision'], 'fields': {'note': 'new'}}).status_code == 200
    job.write_text(job.read_text().replace('tag:dev', 'source:feed'))
    revision = client.get('/api/sources/feed').json()['revision']
    assert client.delete('/api/sources/feed', headers=headers, params={'expected_revision': revision}).status_code == 409


def test_auth_validation_and_assistant_source_tools(client, web_env, monkeypatch):
    headers = login(client, 'viewer')
    assert client.post('/api/sources', headers=headers, json={'id': 'no', 'expected_revision': 'a', 'fields': {}}).status_code == 403
    headers = login(client)
    revision = client.get('/api/sources').json()['revision']
    for key, spec in [('bad/id', 'rss:https://example.org/feed'), ('bad', 'ftp:foo'), ('secret', 'rss:https://user:password@example.org/feed')]:
        assert client.post('/api/sources', headers=headers, json={'id': key, 'expected_revision': revision, 'fields': {'spec': spec}}).status_code == 422
    result = chat(client, monkeypatch, [tool('manage_source', action='create', id='public', expected_revision=revision,
                                           fields={'spec': 'github:org/repo', 'name': 'Public'})])
    assert result['actions'][0]['ok'] and source_registry.snapshot()['sources']['public']['name'] == 'Public'
    result = chat(client, monkeypatch, [tool('query_sources', id='public')])
    assert result['actions'][0]['ok']


def test_sources_with_same_address_keep_separate_identity_and_safe_tag_delete(client, web_env):
    headers = login(client)
    write_job(web_env)
    job = web_env[0] / 'jobs/research/memory.yaml'
    job.write_text(job.read_text() + "\nsources: ['source:first', 'tag:dev']\n")
    (web_env[0] / 'config/sources.yaml').write_text(
        'first:\n  spec: github:org/repo\n  tags: [dev]\nsecond:\n  spec: github:org/repo\n  tags: [dev]\n')
    data = client.get('/api/sources').json()
    assert {r['id'] for r in data['sources']} == {'first', 'second'}
    assert client.delete('/api/sources/second', headers=headers, params={'expected_revision': data['revision']}).status_code == 200
    assert client.get('/api/sources/first').json()['used_by'][0]['job'] == 'research/memory'
