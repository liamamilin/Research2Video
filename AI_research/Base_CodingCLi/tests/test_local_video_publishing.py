"""Local path → immutable media → the real account-bound outbox, with providers isolated."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from core.video import store
from tests.test_assistant_api import login, tool
from tests.test_assistant_workspace import chat
from tests.test_platforms import platform_env, native_credentials, render  # noqa: F401
from tests.test_social_distribution_links import social_env  # noqa: F401
from web.services import local_video as service, platform_manager, social_publishing, douyin, video_render

REAL_PROBE = service.probe_video


@pytest.fixture
def movie(social_env, platform_env, monkeypatch):
    from tests.golden_video_cells import install_recipes
    install_recipes(Path(platform_env) / 'config')
    path = Path(platform_env) / '我的演示视频.mp4'
    path.write_bytes(b'isolated-movie-snapshot')
    monkeypatch.setattr(service, 'probe_video', lambda _: {'duration': 2.5, 'width': 320, 'height': 180})
    monkeypatch.setattr(douyin, 'runtime', lambda: {'ready': True})
    monkeypatch.setattr(douyin, 'tick', lambda: None)
    platform_manager.connect(native_credentials())
    return path


def post(client, movie, **fields):
    return client.post('/api/assistant/local-video', headers=login(client),
                       json={'path': str(movie), 'platform': 'xiaohongshu', **fields})


def test_prepare_registers_completed_asset_and_never_generates_or_posts(client, movie, monkeypatch):
    from web.services import video_workflow
    monkeypatch.setattr(video_workflow, 'spawn', lambda *_: pytest.fail('no generation'))
    monkeypatch.setattr(social_publishing, 'enqueue', lambda *_: pytest.fail('preview must not upload'))
    response = post(client, movie)
    assert response.status_code == 200, response.text
    data = response.json()
    assert not data['submitted'] and data['local_video']['imported'] and not data['local_video']['reused']
    script, render_row = store.get_script(data['script_id']), store.get_render(data['render_id'])
    assert script['status'] == 'APPROVED' and render_row['status'] == 'COMPLETED'
    assert store.get_version(script['id'], 1)['sections'] == []
    assert store.get_version(script['id'], 1)['edit_type'] == 'IMPORTED'
    assert video_render.safe_file(render_row, 'video.mp4').read_bytes() == movie.read_bytes()
    assert data['fields']['title'] == '' and data['needs_input']
    assert data['missing_fields'] == ['title', 'description']
    pack = client.get(f'/api/video/scripts/{script["id"]}/publish').json()
    assert pack['chapters'] == [] and pack['cover']['badge'] == '本地视频'
    detail = client.get(f'/api/video/scripts/{script["id"]}').json()
    assert detail['topic']['local_video']['duration'] == 2.5
    assert detail['credibility']['text'] == '本地视频'
    assert client.post(f'/api/video/scripts/{script["id"]}/steps/render/rerun', headers=login(client)).status_code == 409


@pytest.mark.parametrize('platform', ['bilibili', 'douyin', 'kuaishou', 'xiaohongshu'])
def test_submit_uses_native_queue_and_tracks_exact_account(client, movie, platform):
    result = post(client, movie, action='submit', platform=platform, fields={'title': '本地视频演示', 'description': '发布说明', 'tags': ['演示'], **({'tid': 188} if platform == 'bilibili' else {})})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['ok'] and data['submitted'] and len(data['publication_tasks']) == 1, data
    task = data['publication_tasks'][0]
    if platform == 'bilibili':
        row = platform_manager.store().get_upload(task['id'])
    elif platform == 'douyin':
        row = douyin.store().get(task['id'])
    else:
        row = social_publishing.store(platform).get(task['id'])
    assert row['script_id'] == data['script_id'] and row['render_id'] == data['render_id'] and row['version'] == 1
    assert row['uid'] == data['account_uid'] and row['status'] == 'QUEUED'
    assert row['payload']['title'] == '本地视频演示'
    assert 'secret' not in json.dumps(data) and 'video_path' not in json.dumps(data)


def test_repeat_submit_keeps_original_task_and_changed_copy_becomes_a_new_asset(client, movie):
    first = post(client, movie, action='submit', fields={'title': '演示视频', 'description': '发布说明'}).json()
    second = post(client, movie, action='submit').json()
    assert second['existing_task'] and second['render_id'] == first['render_id']
    assert second['publication_tasks'] == first['publication_tasks']
    assert len(social_publishing.store('xiaohongshu').list(kind='UPLOAD')) == 1
    original = store.get_render(first['render_id'])
    copied = video_render.safe_file(original, 'video.mp4').read_bytes()
    movie.write_bytes(b'a-new-movie-at-the-same-path')
    third = post(client, movie).json()
    assert third['render_id'] != first['render_id']
    assert video_render.safe_file(original, 'video.mp4').read_bytes() == copied


def test_renamed_same_content_and_concurrent_imports_share_one_record(client, movie):
    other = movie.with_name('另一个文件名.mp4'); other.write_bytes(movie.read_bytes())
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda path: service.import_video(str(path), 'admin'), [movie, other, movie, other]))
    assert len({v['render_id'] for v in values}) == 1
    assert len(store.list_scripts()) == 1
    result = post(client, other).json()
    assert result['local_video']['file_name'] == other.name and result['local_video']['reused']


def test_managed_render_path_reuses_its_original_version_and_publishing_history(client, movie):
    row = render()
    path = video_render.safe_file(row, 'video.mp4')
    response = post(client, path)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['render_id'] == row['id'] and not value['local_video']['imported'] and value['local_video']['reused']
    assert len(store.list_scripts()) == 1


def test_published_asset_is_not_posted_again(client, movie):
    first = post(client, movie, action='submit', fields={'title': '演示视频', 'description': '发布说明'}).json()
    db = social_publishing.store('xiaohongshu')
    db.update(first['publication_tasks'][0]['id'], status='PUBLISHED', result={'public': True})
    again = post(client, movie, action='submit').json()
    assert not again['submitted'] and again['status'] == 'completed'
    assert len(db.list(kind='UPLOAD')) == 1


def test_platforms_and_accounts_are_independent_for_the_same_file(client, movie):
    fields = {'title': '演示视频', 'description': '发布说明'}
    xhs = post(client, movie, action='submit', fields=fields).json()
    ks = post(client, movie, action='submit', platform='kuaishou', fields=fields).json()
    assert xhs['render_id'] == ks['render_id'] and xhs['account_uid'] != ks['account_uid']
    assert xhs['publication_tasks'][0]['id'] != ks['publication_tasks'][0]['id']
    assert xhs['fields']['title'] == ks['fields']['title']


def test_invalid_publication_copy_returns_limits_and_can_be_fixed_without_reimport(client, movie):
    bad = post(client, movie, action='submit', fields={'title': '长' * 100, 'description': '发布说明'}).json()
    assert not bad['ok'] and not bad['submitted'] and not bad['check']['valid']
    assert social_publishing.store('xiaohongshu').list(kind='UPLOAD') == []
    good = post(client, movie, action='submit', fields={'title': '修正发布标题'}).json()
    assert good['ok'] and good['render_id'] == bad['render_id']


def test_prepared_fields_are_saved_per_platform_and_missing_bili_category_is_actionable(client, movie):
    ready = post(client, movie, fields={'title': '保存的本地发布标题', 'description': '真实保存的简介'}).json()
    draft = store.distribution_draft(store.get_render(ready['render_id']), 'xiaohongshu', ready['account_uid'])
    assert ready['draft_saved'] and draft['payload']['title'] == '保存的本地发布标题'
    submitted = post(client, movie, action='submit').json()
    assert submitted['fields']['description'] == '真实保存的简介'
    bili = post(client, movie, platform='bilibili', action='submit', fields={'title': '演示视频', 'description': '发布说明'}).json()
    assert not bili['submitted'] and not bili['check']['valid'] and 'tid' in bili['message']


@pytest.mark.parametrize('fields,missing', [({}, ['title', 'description']),
    ({'title': '用户的标题'}, ['description']), ({'description': '用户的简介'}, ['title'])])
def test_missing_copy_prompts_only_for_missing_fields_and_continues_saved_draft(client, movie, fields, missing):
    first = post(client, movie, action='submit', fields=fields).json()
    assert first['needs_input'] and first['missing_fields'] == missing and not first['submitted']
    assert social_publishing.store('xiaohongshu').list(kind='UPLOAD') == []
    again = post(client, movie, action='submit').json()
    assert again['missing_fields'] == missing and again['render_id'] == first['render_id']
    supplied = {key: {'title': '补充标题', 'description': '补充简介'}[key] for key in missing}
    completed = post(client, movie, action='submit', fields=supplied).json()
    assert completed['submitted'] and completed['render_id'] == first['render_id']
    assert all(completed['fields'][key] == value for key, value in fields.items())
    assert len(social_publishing.store('xiaohongshu').list(kind='UPLOAD')) == 1
    assert len(store.list_scripts()) == 1


def test_imported_video_does_not_leak_into_another_platforms_automatic_publishing(client, movie, monkeypatch):
    data = post(client, movie).json()
    assert next(c for c in platform_manager.candidates() if c['render_id'] == data['render_id'])['local_video']
    with pytest.raises(platform_manager.Error, match='本地视频'):
        platform_manager.eligible(data['render_id'], automatic=True)
    platform_manager.store().save_settings({'auto_upload': True, 'tid': 188, 'auto_since': 0})
    monkeypatch.setattr(platform_manager, 'enqueue', lambda *_args, **_kwargs: pytest.fail('must not auto-post local clips'))
    platform_manager.tick()
    assert platform_manager.store().uploads('123') == []


def test_real_mp4_is_probed_imported_and_remains_decodable(client, movie, monkeypatch):
    import platform
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[3]
    binary = root / 'autovideo/g0' / f'{sys.platform}_{platform.machine().lower()}' / 'ffmpeg'
    if not binary.is_file():
        pytest.skip('bundled ffmpeg unavailable')
    subprocess.run([str(binary), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=10',
                    '-t', '0.5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(movie)], check=True, timeout=30)
    monkeypatch.setattr(service, 'probe_video', REAL_PROBE)
    value = post(client, movie).json()
    assert value['local_video']['width'] == 320 and value['local_video']['height'] == 180
    saved = video_render.safe_file(store.get_render(value['render_id']), 'video.mp4')
    subprocess.run([str(binary), '-v', 'error', '-i', str(saved), '-f', 'null', '-'], check=True, timeout=30)
    invalid = movie.with_name('伪造的视频.mp4'); invalid.write_text('this is not a video')
    with pytest.raises(ValueError, match='无法读取'):
        REAL_PROBE(invalid)


@pytest.mark.parametrize('path', ['relative.mp4', '/definitely/missing.mp4', 'https://example.com/video.mp4'])
def test_unavailable_paths_do_not_create_records(client, movie, path):
    assert post(client, path).status_code == 409
    assert not store.list_scripts()


def test_invalid_media_directories_and_empty_files_are_rejected_without_partial_records(client, movie, monkeypatch):
    assert post(client, movie.parent).status_code == 409
    movie.write_bytes(b'')
    assert post(client, movie).status_code == 409
    movie.write_bytes(b'not-a-movie')
    monkeypatch.setattr(service, 'probe_video', lambda _: (_ for _ in ()).throw(ValueError('无法读取视频')))
    assert post(client, movie).status_code == 409
    assert not store.list_scripts()


def test_unknown_disconnected_changed_accounts_and_viewer_do_not_import(client, movie, monkeypatch):
    assert client.post('/api/assistant/local-video', json={'path': str(movie), 'platform': 'xiaohongshu'}).status_code == 401
    assert client.post('/api/assistant/local-video', headers=login(client, 'viewer'), json={'path': str(movie), 'platform': 'xiaohongshu'}).status_code == 403
    assert post(client, movie, platform='weixin_channels').status_code == 409
    assert post(client, movie, account_uid='another-account').status_code == 409
    monkeypatch.setattr(social_publishing, 'account', lambda _: {'connected': False})
    assert post(client, movie).status_code == 409
    assert not store.list_scripts()


def test_assistant_can_submit_from_path_and_workspace_tracks_original_upload(client, movie, monkeypatch):
    result = chat(client, monkeypatch, [tool('publish_local_video', action='submit', path=str(movie), platform='xiaohongshu',
                  fields={'title': '演示视频', 'description': '发布说明'})],
                  message='把这个本地视频发到小红书，标题“演示视频”，简介“发布说明”。')
    assert result['actions'][0]['ok'], result
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    assert workspace['objects'][0]['local_video']
    assert workspace['tasks'][0]['publications'][0]['status'] == 'QUEUED'
    assert workspace['tasks'][0]['publications'][0]['version'] == 1
