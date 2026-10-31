"""Multi-platform targets preserve work identity, accounts and immutable posting history."""
import json
import pytest

from core.video import store
from tests.test_platforms import platform_env, render, BV, PAYLOAD
from tests.test_video_scripts import _csrf, _login
from web.services import distributions, douyin, platform_manager as bili

WORK = '7700436961890209034'


def profiles(monkeypatch):
    monkeypatch.setattr(douyin, 'account', lambda: {'connected': True, 'uid': '456', 'name': '抖音账号'})


def test_one_render_has_independent_platform_targets_and_detail(platform_env, client, monkeypatch):
    profiles(monkeypatch)
    r = render()
    store.add_publication(r['script_id'], version=1, render_id=r['id'], account_uid='123', platform='bilibili',
                          url=f'https://www.bilibili.com/video/{BV}', published_on='2026-10-24')
    task = douyin.store().create('UPLOAD', '456', {'title': '抖音标题', 'video_path': '/secret'}, render=r)
    douyin.store().update(task['id'], status='SUBMITTED', work_id=WORK)
    _login(client)
    data = client.get(f"/api/video/scripts/{r['script_id']}").json()
    rows = {d['platform']: d for d in data['distributions'] if d['current_account']}
    assert rows['bilibili']['status'] == 'PUBLISHED'
    assert rows['douyin']['status'] == 'SUBMITTED' and rows['douyin']['task']['work_id'] == WORK
    assert '/secret' not in json.dumps(data['distributions'])
    assert 'sessdata' not in json.dumps(data['distributions'])
    assert len(data['publications']) == 1


def test_other_platform_publication_does_not_remove_douyin_candidate(platform_env, monkeypatch):
    profiles(monkeypatch)
    r = render()
    store.add_publication(r['script_id'], version=1, url=f'https://www.bilibili.com/video/{BV}', published_on='2026-10-24')
    assert [c['render_id'] for c in douyin.candidates()] == [r['id']]
    rows = distributions.overview([store.get_script(r['script_id'])])[r['script_id']]
    target = next(d for d in rows if d['platform'] == 'douyin')
    assert target['can_prepare'] and target['status'] == 'UNPUBLISHED'


def test_platform_drafts_are_account_version_scoped_and_shared_with_preview(platform_env, monkeypatch):
    profiles(monkeypatch)
    r = render()
    distributions.save_draft(r['id'], 'bilibili', '123', PAYLOAD)
    distributions.save_draft(r['id'], 'douyin', '456', {'title': '抖音独立标题', 'description': '短简介', 'tags': ['科技']})
    assert distributions.preview_draft(r, 'douyin')['draft']['payload']['title'] == '抖音独立标题'
    assert distributions.preview_draft(r, 'bilibili')['draft']['payload']['title'] == PAYLOAD['title']
    monkeypatch.setattr(douyin, 'account', lambda: {'connected': True, 'uid': '789'})
    assert distributions.preview_draft(r, 'douyin')['draft'] is None
    with pytest.raises(ValueError, match='账号已改变'):
        distributions.save_draft(r['id'], 'douyin', '456', {'title': '过期页面'})
    with pytest.raises(ValueError):
        distributions.save_draft(r['id'], 'douyin', '789', {'storage_path': '/credentials'})
    rows = distributions.overview([store.get_script(r['script_id'])])[r['script_id']]
    assert next(d for d in rows if d['platform'] == 'douyin' and d['account_uid'] == '456')['status'] == 'DRAFT'
    assert next(d for d in rows if d['platform'] == 'douyin' and d['account_uid'] == '789')['can_prepare']


@pytest.mark.parametrize('option,enabled', [({'text': '补充说明'}, True),
                                          ({'text': '补充说明', 'enabled': False}, False)])
def test_partial_comment_draft_is_materialized_for_native_editor(platform_env, monkeypatch, option, enabled):
    profiles(monkeypatch)
    r = render()
    distributions.save_draft(r['id'], 'douyin', '456', {'title': '标题', 'first_comment': option})
    comment = distributions.preview_draft(r, 'douyin')['draft']['payload']['first_comment']
    assert comment == {'enabled': enabled, 'text': '补充说明', 'pin': False}


def test_manual_and_automatic_same_work_merge_provenance_but_distinct_works_remain(platform_env):
    r = render()
    kwargs = dict(version=1, platform='douyin', published_on='2026-10-24')
    manual = store.add_publication(r['script_id'], url=f'https://www.douyin.com/video/{WORK}?from=share', **kwargs)
    auto = store.add_publication(r['script_id'], url=f'https://www.iesdouyin.com/share/video/{WORK}/', account_uid='456',
                                 recorded_by='platform-manager', render_id=r['id'], **kwargs)
    assert manual['id'] == auto['id'] and auto['work_id'] == WORK
    assert set(json.loads(auto['sources'])) == {'manual', 'automatic'} and auto['confirmed_at']
    assert auto['render_id'] == r['id'] and auto['account_uid'] == '456'
    store.add_publication(r['script_id'], url='https://www.douyin.com/video/7700436961890209035', **kwargs)
    assert len(store.list_publications(r['script_id'])) == 2
    with pytest.raises(ValueError, match='另一个平台账号'):
        store.add_publication(r['script_id'], url=auto['url'], account_uid='789', **kwargs)


def test_removing_local_record_never_reopens_duplicate_submission(platform_env):
    r = render()
    p = store.add_publication(r['script_id'], version=1, platform='bilibili', account_uid='123',
                              url=f'https://www.bilibili.com/video/{BV}', published_on='2026-10-24')
    assert store.delete_publication(p['id'])
    assert store.list_publications(r['script_id']) == []
    assert store.has_publication(r['script_id'], 1, 'bilibili', '123')
    with pytest.raises(bili.Error, match='已经记录发布'):
        bili.enqueue(r['id'], PAYLOAD)
    assert bili.candidates() == []
    assert not store.has_publication(r['script_id'], 1, 'bilibili', '999')
    restored = store.add_publication(r['script_id'], version=1, platform='bilibili', account_uid='123',
                                     url=p['url'], published_on='2026-10-24')
    assert restored['id'] == p['id'] and not restored['removed_at']
    assert len(store.list_publications(r['script_id'])) == 1


def test_old_version_and_account_do_not_mark_new_targets_published(platform_env, monkeypatch):
    profiles(monkeypatch)
    r = render()
    store.add_publication(r['script_id'], version=1, platform='douyin', account_uid='old',
                          url=f'https://www.douyin.com/video/{WORK}', published_on='2026-10-24')
    store.add_version(r['script_id'], title='新版', sections=[], length_check={}, edit_type='EDITED', meta={})
    rows = distributions.overview([store.get_script(r['script_id'])])[r['script_id']]
    assert next(d for d in rows if d['version'] == 2 and d['platform'] == 'douyin')['status'] == 'UNPUBLISHED'
    assert next(d for d in rows if d['version'] == 1 and d['platform'] == 'douyin' and d['current_account'])['can_prepare']
    assert next(d for d in rows if d['account_uid'] == 'old')['status'] == 'PUBLISHED'


@pytest.mark.parametrize('platform,url', [('douyin', f'https://www.bilibili.com/video/{BV}'),
                                           ('bilibili', f'https://www.douyin.com/video/{WORK}'),
                                           ('douyin', f'https://douyin.com.evil.test/video/{WORK}'),
                                           ('douyin', 'https://www.douyin.com/user/example')])
def test_manual_api_rejects_mismatched_hosts_and_non_work_links(platform_env, client, platform, url):
    r = render(); _login(client)
    response = client.post(f"/api/video/scripts/{r['script_id']}/publications", headers=_csrf(client),
                           json={'platform': platform, 'url': url, 'version': 1})
    assert response.status_code == 422
    assert not store.list_publications(r['script_id'])


def test_manual_api_records_platform_account_and_canonical_work(platform_env, client, monkeypatch):
    profiles(monkeypatch); r = render(); _login(client)
    response = client.post(f"/api/video/scripts/{r['script_id']}/publications", headers=_csrf(client),
                           json={'platform': 'douyin', 'account_uid': '456', 'url': f'https://www.douyin.com/?modal_id={WORK}&from=x', 'version': 1})
    assert response.status_code == 201, response.text
    p = response.json()
    assert p['url'] == f'https://www.douyin.com/video/{WORK}' and p['account_uid'] == '456'
    assert p['sources'] == '["manual"]' and not p['confirmed_at']


def test_migration_only_assigns_accounts_from_exact_confirmed_work_receipts(platform_env):
    r = render()
    p = store.add_publication(r['script_id'], version=1, url=f'https://www.bilibili.com/video/{BV}', published_on='2026-10-24')
    assert distributions.migrate_confirmed_accounts() == 0  # known ID alone does not prove account
    t = bili.store().enqueue('123', r, PAYLOAD)
    bili.store().update_upload(t['id'], status='PUBLISHED', bvid=BV)
    distributions.migrate_confirmed_accounts()
    migrated = store.get_publication(p['id'])
    assert migrated['account_uid'] == '123' and migrated['confirmed_at']
    assert distributions.migrate_confirmed_accounts() == 0
    store.init_db()
    assert store.get_publication(p['id']) == migrated


def test_stale_account_cannot_submit_either_platform(platform_env, monkeypatch):
    profiles(monkeypatch); r = render()
    with pytest.raises(bili.Error, match='账号已改变'):
        bili.enqueue(r['id'], {**PAYLOAD, 'account_uid': 'old'})
    with pytest.raises(douyin.DouyinError, match='账号已改变'):
        douyin.enqueue(r['id'], {'title': '抖音', 'description': '', 'tags': [], 'account_uid': 'old'})
    assert not bili.store().uploads('123')
    assert not douyin.store().list(kind='UPLOAD')


def test_draft_endpoint_uses_editor_role_and_preserves_target_after_reload(platform_env, client, monkeypatch):
    profiles(monkeypatch); r = render()
    endpoint = '/api/video/distributions/douyin/draft'
    body = {'render_id': r['id'], 'account_uid': '456', 'payload': {'title': '暂存抖音', 'description': '', 'tags': []}}
    assert client.put(endpoint, json=body).status_code in (401,403)
    _login(client)
    response = client.put(endpoint, headers=_csrf(client), json=body)
    assert response.status_code == 200, response.text
    data = client.get(f"/api/video/scripts/{r['script_id']}").json()
    target = next(d for d in data['distributions'] if d['platform'] == 'douyin')
    assert target['status'] == 'DRAFT' and target['can_prepare']
    assert not douyin.store().list(kind='UPLOAD')
    t = douyin.store().create('UPLOAD','456',{},render=r)
    assert client.put(endpoint, headers=_csrf(client), json=body).status_code == 409
    assert douyin.store().get(t['id'])['status'] == 'QUEUED'
