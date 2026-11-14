"""Two cover slots reach one native upload; editing preserves the original archive."""
import copy
import io
import json
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from core.video import store
from tests.test_platforms import platform_env, render, PAYLOAD, BV, native_credentials  # noqa: F401
from tests.test_video_scripts import _login, _csrf
from web.services import bilibili, bilibili_covers as covers, platform_manager as manager, video_render


def png(size, color):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def pair():
    return {'4x3': png((800, 600), 'orange'), '16x9': png((960, 540), 'blue')}


def test_api_assets_are_independent_and_queued_files_do_not_change(client, platform_env):
    row = render(); _login(client)
    for ratio, data in pair().items():
        response = client.post(f'/api/platforms/bilibili/covers/{row["id"]}/{ratio}',
            data={'account_uid': '123'}, files={'file': ('cover.png', data, 'image/png')}, headers=_csrf(client))
        assert response.status_code == 200, response.text
    preview = client.get(f'/api/platforms/bilibili/covers/{row["id"]}').json()
    assert set(preview) == {'4x3', '16x9'}
    task = manager.enqueue(row['id'], PAYLOAD)
    assert Path(task['payload']['cover43']).read_bytes() == pair()['4x3']
    assert Path(task['payload']['cover']).read_bytes() == pair()['16x9']
    covers.save(row['id'], {'4x3': png((800, 600), 'green')})
    assert Path(manager.store().get_upload(task['id'])['payload']['cover43']).read_bytes() == pair()['4x3']
    assert len(manager.store().uploads('123')) == 1


def test_invalid_pair_does_not_replace_existing_pair(platform_env):
    row = render(); before = covers.save(row['id'], pair())
    with pytest.raises(bilibili.BilibiliError, match='4:3'):
        covers.save(row['id'], {'16x9': png((960, 540), 'red'), '4x3': pair()['16x9']})
    assert covers.preview(store.get_render(row['id'])) == before
    with pytest.raises(bilibili.BilibiliError):
        covers.save(row['id'], {'4x3': b'not-image'})


def test_cover_api_keeps_account_and_csrf_checks(client, platform_env):
    row = render(); _login(client)
    url = f'/api/platforms/bilibili/covers/{row["id"]}/4x3'
    args = {'data': {'account_uid': '999'}, 'files': {'file': ('cover.png', pair()['4x3'], 'image/png')}}
    assert client.post(url, **args).status_code == 403
    assert client.post(url, headers=_csrf(client), **args).status_code == 409
    assert covers.preview(store.get_render(row['id'])) == {}


def test_native_upload_submits_cover43_in_same_request_and_caches_image(platform_env, monkeypatch):
    row = render(); covers.save(row['id'], pair())
    task = manager.enqueue(row['id'], PAYLOAD)
    requests, commands = [], []
    monkeypatch.setattr(bilibili, 'prepare_cover', lambda video, folder, provided='': Path(provided))
    def request(self, path, **kwargs):
        assert path == '/x/vu/web/cover/up'
        assert kwargs['data']['cover'].startswith('data:image/png;base64,')
        requests.append(path)
        return {'url': 'https://archive.biliimg.com/bfs/archive/4x3.png'}, None
    monkeypatch.setattr(bilibili.BilibiliClient, 'request', request)
    def execute(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, b'', (f'ResponseData {{ code: 0, data: Some(Object {{"bvid": String("{BV}")}}) }}' if args[3] == 'upload' else '').encode())
    monkeypatch.setattr(subprocess, 'run', execute)
    for _ in range(2):
        assert bilibili.upload(native_credentials(), video_render.safe_file(row, 'video.mp4'), task['payload'], manager.directory()) == BV
    assert len(requests) == 1
    for args in [c for c in commands if c[3] == 'upload']:
        assert json.loads(args[args.index('--extra-fields') + 1]) == {'cover43': 'https://archive.biliimg.com/bfs/archive/4x3.png'}
        assert args[args.index('--cover') + 1] == task['payload']['cover']


def test_cover_failure_does_not_submit_video(platform_env, monkeypatch):
    row = render(); covers.save(row['id'], pair()); task = manager.enqueue(row['id'], PAYLOAD)
    def execute(args, **kwargs):
        assert args[3] == 'list'
        return subprocess.CompletedProcess(args, 0, b'', b'')
    monkeypatch.setattr(subprocess, 'run', execute)
    monkeypatch.setattr(bilibili.BilibiliClient, 'upload_cover', lambda *a: (_ for _ in ()).throw(bilibili.BilibiliError('network')))
    with pytest.raises(bilibili.UploadFailed, match='视频尚未提交'):
        bilibili.upload(native_credentials(), video_render.safe_file(row, 'video.mp4'), task['payload'], manager.directory())


def test_edit_endpoint_preserves_metadata_cids_and_is_idempotent(client, platform_env, monkeypatch):
    row = render(); covers.save(row['id'], pair()); task = manager.enqueue(row['id'], PAYLOAD)
    manager.store().update_upload(task['id'], status='PUBLISHED', bvid=BV)
    snapshot = {'archive': {'aid': 1, 'bvid': BV, 'mid': 123, 'title': '保留原标题', 'desc': '保留简介',
                'tag': '音乐', 'copyright': 1, 'tid': 265, 'no_reprint': 1, 'cover': 'old', 'cover43': 'old43',
                'limited_free': {}, 'is_only_self': 0,
                'recreate': {'auth': 1, 'editable': 1, 'switch': -1},
                'human_type2': {'id': 0, 'name': ''},
                'creation_statement': {'id': -1, 'content': '内容无需标注'}},
                'videos': [{'cid': 51, 'filename': 'one', 'title': 'P1', 'desc': ''},
                           {'cid': 52, 'filename': 'two', 'title': 'P2', 'desc': ''}],
                'reply': {'up_close_reply': True, 'up_selection': True},
                'subtitle': {'allow': True, 'lan': 'zh'}, 'act_reserve_create': False,
                'watermark': {'state': 1, 'id': 0}}
    original = copy.deepcopy(snapshot); edits = []
    def request(self, path, **kwargs):
        if path.endswith('/archive/view'):
            return copy.deepcopy(snapshot), None
        assert path == '/x/vu/web/edit'
        payload = kwargs['json_body']; edits.append(payload)
        assert payload['videos'] == original['videos']
        assert payload['title'] == '保留原标题' and payload['desc'] == '保留简介'
        assert payload['no_reprint'] == 1 and payload['up_close_reply'] is True
        assert 'limited_free' not in payload
        assert payload['recreate'] == -1 and payload['human_type2'] == 0
        assert payload['creation_statement'] == original['archive']['creation_statement']
        assert payload['watermark'] == {'state': 1} and payload['up_selection_reply'] is True
        assert payload['subtitle'] == {'open': 1, 'lan': 'zh'}
        assert 'act_reserve_create' not in payload and payload['csrf']
        snapshot['archive'].update(cover=payload['cover'], cover43=payload['cover43'])
        return {'aid': 1, 'bvid': BV}, None
    monkeypatch.setattr(bilibili.BilibiliClient, 'request', request)
    monkeypatch.setattr(bilibili.BilibiliClient, 'upload_cover', lambda self, path, folder: 'https://archive.biliimg.com/' + path.name)
    _login(client)
    for _ in range(2):
        result = client.post(f'/api/platforms/bilibili/uploads/{task["id"]}/covers', headers=_csrf(client), json={'account_uid': '123'})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'CONFIRMED'
    assert len(edits) == 1 and len(manager.store().uploads('123')) == 1
    assert manager.store().get_upload(task['id'])['status'] == 'PUBLISHED'


def test_readback_failure_keeps_submitted_receipt_and_retry_does_not_edit_again(platform_env, monkeypatch):
    row = render(); covers.save(row['id'], pair()); task = manager.enqueue(row['id'], PAYLOAD)
    manager.store().update_upload(task['id'], status='PUBLISHED', bvid=BV)
    snapshot = {'archive': {'cover': 'old', 'cover43': 'old43'}}
    reads, edits = [], []
    def read(self, bvid):
        reads.append(bvid)
        if len(reads) == 2:
            raise bilibili.BilibiliRejected('暂时无法回读', code=-412)
        return copy.deepcopy(snapshot)
    def edit(self, before, **remote):
        edits.append(remote)
        snapshot['archive'].update(remote)
    monkeypatch.setattr(bilibili.BilibiliClient, 'archive_for_edit', read)
    monkeypatch.setattr(bilibili.BilibiliClient, 'edit_covers', edit)
    monkeypatch.setattr(bilibili.BilibiliClient, 'upload_cover', lambda self, path, folder: 'https://archive.biliimg.com/' + path.name)
    with pytest.raises(bilibili.BilibiliRejected):
        covers.update_published(task['id'], '123')
    assert manager.store().get_upload(task['id'])['payload']['cover_update']['status'] == 'SUBMITTED'
    assert covers.update_published(task['id'], '123')['status'] == 'CONFIRMED'
    assert len(edits) == 1
