"""A complete configuration uses the real page handlers without discovery loops."""
from pathlib import Path

from core.config import load_job
from tests.test_assistant_api import login, tool
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import source_registry


def test_configure_matrix_source_job_and_recipe_in_one_tool(client, video_env, web_env, monkeypatch):
    import yaml
    path = Path(video_env) / 'config/video_recipes/deep_dive.yaml'
    data = yaml.safe_load(path.read_text()); data['topic']['whole_report'] = True
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    arguments = {'matrix_key': 'full', 'matrix_name': 'Full setup', 'domain': 'dev', 'job_name': 'research/full',
        'title': '浏览器 Python', 'prompt': 'Prepare material for a useful video about browser Python.',
        'output': 'output/full_{date}.md', 'sources': ['source:browser'], 'recipe': 'deep_dive',
        'registered_sources': [{'id': 'browser', 'name': 'Pyodide', 'spec': 'github:pyodide/pyodide', 'tags': ['dev']}],
        'render': {'video_speed': 1.25}}
    result = chat(client, monkeypatch, [tool('configure_matrix_task', **arguments)])
    assert result['actions'][0]['ok'], result
    job = load_job(str(web_env[0] / 'jobs'), 'research/full')
    assert job['name'] == '浏览器 Python' and job['sources'] == ['source:browser'] and job['keywords'] == []
    assert 'browser' in source_registry.snapshot()['sources']
    configs = client.get('/api/production', params={'job': 'research/full'}).json()
    assert len(configs) == 1 and configs[0]['auto'] == 0
    assert client.get('/api/scheduler').json()['jobs'] == [] or not any(r.get('job_name') == 'research/full' for r in client.get('/api/scheduler').json()['jobs'])
    again = chat(client, monkeypatch, [tool('configure_matrix_task', **arguments)])
    assert again['actions'][0]['ok'], again
    assert len(client.get('/api/production', params={'job': 'research/full'}).json()) == 1
    response = client.post('/api/assistant/batches', headers=login(client), json={'kind': 'produce', 'scope': {'jobs': ['research/full']}})
    assert response.status_code == 201 and len(response.json()['batch']['items']) == 2


def test_configuration_failure_keeps_truthful_partial_receipts(client, video_env, monkeypatch):
    result = chat(client, monkeypatch, [tool('configure_matrix_task', matrix_key='partial', matrix_name='Partial',
        job_name='research/partial', prompt='Public materials', recipe='missing',
        registered_sources=[{'id': 'kept', 'spec': 'github:org/repo'}])])
    assert not result['actions'][0]['ok']
    assert client.get('/api/sources/kept').status_code == 200
    assert client.get('/api/matrices/partial').status_code == 200
