import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        yield client


def test_save_persist_and_stale_update_preserves_work(client):
    created = client.post('/api/projects', json={'title': '明天再说', 'hook': '今天先睡个好觉'}).json()
    project_id = created['id']
    project = {**created['project'], 'premise': '一场拖延症的自我和解'}
    saved = client.put(f'/api/projects/{project_id}', json={'version': 1, 'project': project})
    assert saved.status_code == 200
    assert saved.json()['version'] == 2
    stale = client.put(f'/api/projects/{project_id}', json={'version': 1, 'project': created['project']})
    assert stale.status_code == 409
    # Open a fresh connection/store, ensuring the response wasn't just in-memory state.
    store = Store(client.app.state.store.path)
    assert store.get(project_id)['project']['premise'] == project['premise']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM revisions WHERE project_id=?', (project_id,)).fetchone()[0] == 2


def test_export_import_preserves_song_selection_and_video_binding(client):
    example = client.get('/api/example').json()
    project = example['project']
    project['videos'] = [{'id': 'mv-one', 'song_id': 'mureka-v95-69s', 'title': '蓝按钮 MV', 'treatment': '副歌切换大字'}]
    created = client.post('/api/import', json=example).json()
    exported = client.get(f"/api/projects/{created['id']}/export")
    assert 'attachment' in exported.headers['content-disposition']
    assert exported.json()['schema_version'] == 1
    copied = client.post('/api/import', json=exported.json()).json()
    assert copied['id'] != created['id']
    assert copied['project'] == created['project']
    # Selecting another version must not silently replace an existing MV's song.
    changed = copied['project']
    changed['selected_song_id'] = 'mureka-v95-48s'
    result = client.put(f"/api/projects/{copied['id']}", json={'version': 1, 'project': changed}).json()
    assert result['project']['videos'][0]['song_id'] == 'mureka-v95-69s'


def test_invalid_song_reference_and_unsafe_link_are_not_saved(client):
    assert client.post('/api/projects', json={'title': '测试', 'selected_song_id': 'missing'}).status_code == 422
    assert client.post('/api/projects', json={'title': '测试', 'songs': [{'label': '版本', 'source_url': 'javascript:alert(1)'}]}).status_code == 422
    assert client.post('/api/projects', json={'title': '测试', 'videos': [{'song_id': 'missing', 'title': 'MV'}]}).status_code == 422
    assert client.get('/api/projects').json() == []


def test_prompt_uses_real_project_and_does_not_require_ai_theme(client):
    row = client.post('/api/projects', json={'title': '妈妈的冰箱', 'inspiration': '每次回家冰箱都塞满了', 'emotion': '温暖'}).json()
    text = client.get(f"/api/projects/{row['id']}/prompt").json()['text']
    assert '每次回家冰箱都塞满了' in text
    assert '三个真正不同' in text
    assert '不限定为 AI 或科技题材' in text


def test_missing_and_foreign_origin(client):
    assert client.get('/api/projects/missing').status_code == 404
    result = client.post('/api/projects', json={'title': '不应创建'}, headers={'origin': 'https://example.com'})
    assert result.status_code == 403
    assert client.get('/api/projects').json() == []
