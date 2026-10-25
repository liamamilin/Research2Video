"""Shared publisher boundary tests. Providers, browsers and persistent state are all isolated."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError
from core.social.registry import PLATFORMS
from core.social.store import SocialStore
from core.video import store as video_store
from scripts import social_publish_worker as worker
from tests.test_platforms import render
from tests.test_video_scripts import _csrf, _login
from web.services import social_publishing as manager


OWNER = {"uid": "90071992547409931234", "name": "测试作者"}
WORK = "3xwn3kkerxj6g9n"
PAYLOAD = {"title": "一个测试视频标题", "description": "投稿简介", "tags": ["科普"]}
REAL_TICK = manager.tick


@pytest.fixture
def social_env(web_env, monkeypatch):
    root, _ = web_env
    monkeypatch.setattr(video_store, "_DB_PATH_OVERRIDE", str(root / "state/video.db"))
    video_store.init_db()
    monkeypatch.setattr(manager, "runtime", lambda: {
        "ready": True, "source": root / "fixed-runtime", "python": Path("/fake/python"), "browsers": root / "browsers",
    })
    monkeypatch.setattr(manager, "tick", lambda platform: None)
    for platform in PLATFORMS:
        worker.private_json(manager.directory(platform) / "account.json", {**OWNER, "verified_at": time.time()})
        worker.private_json(manager.credential(manager.directory(platform), OWNER["uid"]), {
            "cookies": [{"name": "session", "value": "private-cookie-secret"}], "origins": [],
        })
    return root


def enqueue(platform, row, **extra):
    return manager.enqueue(platform, row["id"], {**PAYLOAD, "account_uid": manager.uid(platform), **extra})


def test_store_platform_account_and_version_targets_are_independent(tmp_path):
    target = {"id": "r", "script_id": "s", "version": 1}
    left = SocialStore(str(tmp_path), "kuaishou")
    right = SocialStore(str(tmp_path), "xiaohongshu")
    one = left.create("UPLOAD", OWNER["uid"], PAYLOAD, render=target)
    same = left.create("UPLOAD", OWNER["uid"], {**PAYLOAD, "title": "后改"}, render={**target, "id": "r2"})
    other_owner = left.create("UPLOAD", "123", PAYLOAD, render=target)
    other_version = left.create("UPLOAD", OWNER["uid"], PAYLOAD, render={**target, "version": 2})
    other_platform = right.create("UPLOAD", OWNER["uid"], PAYLOAD, render=target)
    assert one["id"] == same["id"] and same["payload"]["title"] == PAYLOAD["title"]
    assert len({one["id"], other_owner["id"], other_version["id"], other_platform["id"]}) == 4
    assert left.path != right.path
    assert len(left.list()) == 3 and len(right.list()) == 1
    assert Path(left.path).stat().st_mode & 0o777 == 0o600


def test_store_concurrent_creation_and_claim_allow_only_one_submission(tmp_path):
    db = SocialStore(str(tmp_path), "kuaishou")
    target = {"id": "r", "script_id": "s", "version": 1}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: db.create("UPLOAD", OWNER["uid"], PAYLOAD, render=target), range(16)))
    assert len({row["id"] for row in jobs}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: db.claim(), range(16)))
    assert sum(row is not None for row in claimed) == 1
    assert db.get(jobs[0]["id"])["status"] == "RUNNING"


def test_queue_deduplicates_concurrent_requests_and_keeps_original_metadata(social_env):
    row = render()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: enqueue("kuaishou", row), range(12)))
    assert len({job["id"] for job in jobs}) == 1
    assert enqueue("kuaishou", row, title="后改标题")["payload"]["title"] == PAYLOAD["title"]
    assert len(manager.store("kuaishou").list(kind="UPLOAD")) == 1


def test_same_video_can_be_queued_on_each_platform_after_bilibili_publication(social_env):
    row = render()
    video_store.add_publication(row["script_id"], version=1, platform="bilibili",
        url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25", account_uid="123")
    jobs = [enqueue(platform, row) for platform in PLATFORMS]
    assert len({job["id"] for job in jobs}) == len(PLATFORMS)
    for platform, job in zip(PLATFORMS, jobs):
        assert job["platform"] == platform and job["status"] == "QUEUED"
        assert manager.store(platform).get(job["id"])["render_id"] == row["id"]


def test_public_dto_omits_credentials_internal_paths_and_provider_payload(social_env):
    row = render(); job = enqueue("kuaishou", row)
    db = manager.store("kuaishou")
    original = db.get(job["id"])
    db.update(job["id"], payload={**original["payload"], "storage": "private-storage-secret", "token": "private-token-secret"},
              result={"cookies": "private-cookie-secret", "raw": {"token": "private-token-secret"},
                      "storage": "private-storage-secret", "url": f"https://www.kuaishou.com/short-video/{WORK}"})
    output = manager.overview("kuaishou")
    data = json.dumps(output)
    assert not any(value in data for value in ("private-cookie-secret", "private-token-secret", "private-storage-secret", str(social_env)))
    assert "video_path" not in data and "storage_state" not in data
    assert output["uploads"][0]["payload"] == {**PAYLOAD, 'first_comment':{'enabled':False,'text':'','pin':False}}


def test_credentials_hash_uid_instead_of_using_provider_id_as_path(social_env):
    root = manager.directory("kuaishou")
    malicious_uid = "../../outside/设备:账号"
    expected = root / "accounts" / hashlib.sha256(malicious_uid.encode()).hexdigest() / "storage.json"
    assert manager.credential(root, malicious_uid) == expected
    assert worker.credential(root, malicious_uid) == expected
    assert ".." not in expected.relative_to(root).parts


@pytest.mark.parametrize("initial,work_id,expected,safe", [
    ({}, "", "FAILED", True),
    ({"invocation_started": True}, "", "UNKNOWN", False),
    ({"submission_started": True}, "", "UNKNOWN", False),
    ({"invocation_started": True}, WORK, "SUBMITTED", False),
])
def test_restart_recovery_preserves_uncertainty_and_durable_ids(tmp_path, initial, work_id, expected, safe):
    db = SocialStore(str(tmp_path), "kuaishou")
    row = db.create("UPLOAD", OWNER["uid"], PAYLOAD, render={"id": "r", "script_id": "s", "version": 1})
    db.claim(); db.update(row["id"], result=initial, work_id=work_id, heartbeat=time.time() - 100)
    db.recover()
    recovered = db.get(row["id"])
    assert recovered["status"] == expected
    assert recovered["result"]["confirmed_not_submitted"] is safe
    assert recovered["work_id"] == work_id
    assert db.claim() is None
    db.recover()
    assert db.get(row["id"])["status"] == expected


def test_unknown_tasks_cannot_be_retried_or_replaced_by_another_enqueue(social_env):
    row = render(); job = enqueue("kuaishou", row)
    db = manager.store("kuaishou")
    db.update(job["id"], status="UNKNOWN", result={"invocation_started": True, "submission_started": True})
    with pytest.raises(AdapterError):
        manager.action("kuaishou", job["id"], "retry")
    same = enqueue("kuaishou", row)
    assert same["id"] == job["id"] and same["status"] == "UNKNOWN" and not same["can_retry"]


def test_explicit_rejection_can_retry_same_target_but_not_after_manual_publication(social_env):
    row = render(); job = enqueue("kuaishou", row)
    db = manager.store("kuaishou")
    db.update(job["id"], status="FAILED", result={"confirmed_not_submitted": True, "code": 42})
    retried = manager.action("kuaishou", job["id"], "retry")
    assert retried["id"] == job["id"] and retried["status"] == "QUEUED"
    assert db.get(job["id"])["result"] == {}
    db.update(job["id"], status="FAILED", result={"confirmed_not_submitted": True})
    video_store.add_publication(row["script_id"], version=1, platform="kuaishou",
        url=f"https://www.kuaishou.com/short-video/{WORK}", published_on="2026-10-25", account_uid=OWNER["uid"])
    with pytest.raises(AdapterError):
        manager.action("kuaishou", job["id"], "retry")


@pytest.mark.parametrize('platform', ['kuaishou', 'xiaohongshu'])
def test_human_absence_confirmation_is_audited_and_does_not_submit(social_env, monkeypatch, platform):
    row = render(); job = enqueue(platform, row); db = manager.store(platform)
    result = {'invocation_started': True, 'submission_started': True, 'failure': {'error_type': 'TimeoutError'}}
    db.update(job['id'], status='UNKNOWN', result=result, error='此前调用失败')
    original = db.get(job['id'])
    monkeypatch.setattr(manager, 'tick', lambda *a: pytest.fail('Confirmation must not launch a publisher'))
    output = manager.confirm_absent(platform, job['id'], OWNER['uid'], original['updated_at'], 'editor')
    assert output['status'] == 'FAILED' and output['can_retry'] and not output['can_confirm_absent']
    saved = db.get(job['id'])
    assert saved['result']['manual_confirmation']['actor'] == 'editor'
    assert saved['result']['submission_started'] is True
    assert saved['result']['confirmed_not_submitted'] is True
    assert saved['payload'] == original['payload']
    with db.connect() as conn:
        events = conn.execute('select * from job_events where job_id=?', (job['id'],)).fetchall()
    assert len(events) == 1 and events[0]['kind'] == 'confirmed_absent'
    assert json.loads(events[0]['detail']) == original
    assert db.claim() is None


def test_retry_archives_the_confirmed_attempt_before_resetting_guards(social_env):
    row = render(); job = enqueue('kuaishou', row); db = manager.store('kuaishou')
    db.update(job['id'], status='UNKNOWN', result={'invocation_started': True}, error='原错误')
    current = db.get(job['id'])
    manager.confirm_absent('kuaishou', job['id'], OWNER['uid'], current['updated_at'], 'editor')
    confirmed = db.get(job['id'])
    manager.action('kuaishou', job['id'], 'retry')
    assert db.get(job['id'])['status'] == 'QUEUED' and db.get(job['id'])['result'] == {}
    with db.connect() as conn:
        events = conn.execute('select * from job_events where job_id=? order by id', (job['id'],)).fetchall()
    assert [event['kind'] for event in events] == ['confirmed_absent', 'retry_requested']
    assert json.loads(events[1]['detail']) == confirmed


@pytest.mark.parametrize('status,work_id,result', [
    ('RUNNING', '', {}), ('QUEUED', '', {}), ('SUBMITTED', WORK, {}), ('UNKNOWN', WORK, {}),
    ('UNKNOWN', '', {'accepted': True}), ('FAILED', '', {'confirmed_not_submitted': True}),
])
def test_absence_confirmation_cannot_override_running_or_accepted_tasks(social_env, status, work_id, result):
    row = render(); job = enqueue('kuaishou', row); db = manager.store('kuaishou')
    db.update(job['id'], status=status, work_id=work_id, result=result)
    original = db.get(job['id'])
    with pytest.raises(AdapterError):
        manager.confirm_absent('kuaishou', job['id'], OWNER['uid'], original['updated_at'], 'editor')
    assert db.get(job['id']) == original


def test_absence_confirmation_rejects_stale_account_state_and_publications(social_env):
    row = render(); job = enqueue('kuaishou', row); db = manager.store('kuaishou')
    db.update(job['id'], status='UNKNOWN'); current = db.get(job['id'])
    for owner, updated in [('other-owner', current['updated_at']), (OWNER['uid'], current['updated_at']-1)]:
        with pytest.raises(AdapterError):
            manager.confirm_absent('kuaishou', job['id'], owner, updated, 'editor')
    video_store.add_publication(row['script_id'], version=1, platform='kuaishou',
        url=f'https://www.kuaishou.com/short-video/{WORK}', published_on='2026-10-25', account_uid=OWNER['uid'])
    with pytest.raises(AdapterError):
        manager.confirm_absent('kuaishou', job['id'], OWNER['uid'], current['updated_at'], 'editor')
    assert db.get(job['id']) == current


def test_human_confirmation_cannot_override_a_recoverable_acceptance_receipt(social_env):
    row = render(); job = enqueue('xiaohongshu', row); db = manager.store('xiaohongshu')
    db.update(job['id'], status='UNKNOWN', result={'submission_response': {
        'http_status': 200, 'body': {'result': 0, 'data': {'id': '64477a32000000000800db6b'}}}})
    current = db.get(job['id'])
    with pytest.raises(AdapterError):
        manager.confirm_absent('xiaohongshu', job['id'], OWNER['uid'], current['updated_at'], 'editor')
    assert db.get(job['id']) == current


def test_concurrent_human_decisions_and_manual_retries_only_transition_once(social_env):
    row = render(); job = enqueue('kuaishou', row); db = manager.store('kuaishou')
    db.update(job['id'], status='UNKNOWN'); current = db.get(job['id'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: db.confirm_absent(job['id'], OWNER['uid'], 'editor', current['updated_at']), range(8)))
    assert outcomes.count(True) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        retries = list(pool.map(lambda _: db.requeue_upload(job['id'], OWNER['uid'], current['payload']), range(8)))
    assert retries.count(True) == 1


def test_absence_confirmation_api_requires_explicit_authenticated_fresh_decision(social_env, client):
    row = render(); job = enqueue('kuaishou', row); db = manager.store('kuaishou')
    db.update(job['id'], status='UNKNOWN'); current = db.get(job['id'])
    endpoint=f"/api/platforms/social/kuaishou/uploads/{job['id']}/confirm-absent"
    body={'confirmed_absent': True, 'account_uid': OWNER['uid'], 'expected_updated_at': current['updated_at']}
    _login(client, 'viewer', 'viewer-pass-123')
    assert client.post(endpoint, headers=_csrf(client), json=body).status_code == 403
    _login(client, 'editor', 'editor-pass-123')
    assert client.post(endpoint, json=body).status_code == 403
    for value in (False, 'true', 1):
        assert client.post(endpoint, headers=_csrf(client), json={**body, 'confirmed_absent': value}).status_code == 422
    assert client.post(endpoint, headers=_csrf(client), json={**body,'account_uid':'other-owner'}).status_code == 409
    response=client.post(endpoint, headers=_csrf(client), json=body)
    assert response.status_code == 200 and response.json()['status'] == 'FAILED'
    assert db.get(job['id'])['result']['manual_confirmation']['actor'] == 'editor'
    assert client.post(endpoint, headers=_csrf(client), json=body).status_code == 409


def test_account_switch_does_not_expose_or_mutate_other_account_jobs(social_env):
    row = render(); job = enqueue("kuaishou", row)
    db = manager.store("kuaishou")
    db.update(job["id"], status="SUBMITTED", work_id=WORK)
    worker.private_json(manager.directory("kuaishou") / "account.json", {"uid": "123", "name": "另一个账号"})
    assert manager.overview("kuaishou")["uploads"] == []
    with pytest.raises(AdapterError):
        manager.upload_task("kuaishou", job["id"])
    other = enqueue("kuaishou", row)
    assert other["id"] != job["id"]
    assert db.get(job["id"])["uid"] == OWNER["uid"]


def test_preview_resolves_new_platform_account_without_bilibili_credentials(social_env, monkeypatch):
    from web.services import video_render
    row = render()
    monkeypatch.setattr(manager.platform_manager, "column_for", lambda script: SimpleNamespace())
    monkeypatch.setattr(video_render, "publish_pack", lambda *args, **kwargs: {**PAYLOAD, "warnings": [], "freshness": {}})
    for platform in PLATFORMS:
        output = manager.preview(platform, row["id"])
        assert output["account_uid"] == OWNER["uid"]
        assert output["render_id"] == row["id"]
        assert output["definition"]["id"] == platform


def test_preview_limits_default_topics_per_platform_without_mutating_shared_pack(social_env, monkeypatch):
    from web.services import video_render
    row = render()
    topics = ["报告速览", "开发者", "AI", "model", "arena", "名次差", "价格差"]
    pack = {**PAYLOAD, "tags": topics, "warnings": ["来源提醒"], "freshness": {"warnings": ["时效提醒"]}}
    monkeypatch.setattr(manager.platform_manager, "column_for", lambda script: SimpleNamespace())
    monkeypatch.setattr(video_render, "publish_pack", lambda *args, **kwargs: pack)

    output = manager.preview("kuaishou", row["id"])
    assert output["tags"] == topics[:3]
    assert output["warnings"][:2] == ["来源提醒", "时效提醒"]
    assert "已默认保留通用发布包的前 3 个" in output["warnings"][2]
    assert manager.clean_payload("kuaishou", {key: output[key] for key in PAYLOAD})["tags"] == topics[:3]
    assert manager.preview("xiaohongshu", row["id"])["tags"] == topics
    assert pack["tags"] == topics and len(topics) == 7
    assert pack["warnings"] == ["来源提醒"]
    assert manager.store("kuaishou").list(kind="UPLOAD") == []


def test_preview_preserves_saved_topics_even_if_draft_exceeds_current_limit(social_env, monkeypatch):
    from web.services import video_render
    row = render()
    topics = ["报告速览", "开发者", "AI", "model", "arena", "名次差", "价格差"]
    saved = {**PAYLOAD, "title": "用户手动保存的标题", "tags": topics}
    video_store.save_distribution_draft(row, "kuaishou", OWNER["uid"], saved)
    monkeypatch.setattr(manager.platform_manager, "column_for", lambda script: SimpleNamespace())
    monkeypatch.setattr(video_render, "publish_pack", lambda *args, **kwargs: {**PAYLOAD, "tags": topics})

    output = manager.preview("kuaishou", row["id"])
    assert output["draft"]["payload"] == saved
    assert output["tags"] == topics[:3]
    assert not output["warnings"]
    assert video_store.distribution_draft(row, "kuaishou", OWNER["uid"])["payload"] == saved
    assert manager.store("kuaishou").list(kind="UPLOAD") == []


def test_login_reuse_and_credential_adoption_is_private_and_idempotent(social_env):
    first = manager.start_login("kuaishou")
    assert manager.start_login("kuaishou")["id"] == first["id"]
    temporary = manager.directory("kuaishou") / "jobs" / first["id"] / "storage.json"
    worker.private_json(temporary, {"cookies": [{"value": "private-cookie-secret"}]})
    db = manager.store("kuaishou")
    db.update(first["id"], status="DONE", result=OWNER)
    manager.settle("kuaishou", db)
    saved = manager.credential(manager.directory("kuaishou"), OWNER["uid"])
    assert saved.is_file() and saved.stat().st_mode & 0o777 == 0o600
    assert not temporary.exists() and not (manager.directory("kuaishou") / "login.json").exists()
    output = manager.login_status("kuaishou", first["id"])
    assert output["connected"] and "private-cookie-secret" not in json.dumps(output)
    manager.settle("kuaishou", db)
    assert manager.account("kuaishou")["uid"] == OWNER["uid"]


def test_login_without_saved_storage_never_adopts_account(social_env):
    job = manager.start_login("kuaishou")
    db = manager.store("kuaishou")
    db.update(job["id"], status="DONE", result={"uid": "123", "name": "未保存账号"})
    manager.settle("kuaishou", db)
    assert db.get(job["id"])["status"] == "FAILED"
    assert manager.account("kuaishou")["uid"] == OWNER["uid"]


def test_worker_login_saves_only_verified_identity_and_qr_with_private_mode(tmp_path, monkeypatch):
    db = SocialStore(str(tmp_path), "kuaishou")
    db.create("LOGIN"); job = db.claim()
    folder = tmp_path / "job"; folder.mkdir()

    async def save_storage(path):
        Path(path).write_text('{"cookies":[{"value":"private-cookie-secret"}]}')

    async def qr(page, path):
        path.write_bytes(b"fake-qr")
        return True

    context = SimpleNamespace(storage_state=AsyncMock(side_effect=save_storage))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    provider = SimpleNamespace(prepare_login=AsyncMock(return_value=SimpleNamespace()), capture_qr=AsyncMock(side_effect=qr),
                               identity=AsyncMock(return_value=OWNER))
    monkeypatch.setattr(worker, "launch", AsyncMock(return_value=browser))
    asyncio.run(worker.login(db, job, provider, SimpleNamespace(), folder))
    assert db.get(job["id"])["result"] == OWNER and db.get(job["id"])["status"] == "DONE"
    assert (folder / "storage.json").stat().st_mode & 0o777 == 0o600
    assert (folder / "qrcode.png").stat().st_mode & 0o777 == 0o600
    browser.close.assert_awaited_once()


def test_worker_login_rejects_cookie_only_identity_without_saving_storage(tmp_path, monkeypatch):
    db = SocialStore(str(tmp_path), "kuaishou")
    db.create("LOGIN"); job = db.claim()
    folder = tmp_path / "job"; folder.mkdir()
    context = SimpleNamespace(storage_state=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    provider = SimpleNamespace(prepare_login=AsyncMock(return_value=SimpleNamespace()), capture_qr=AsyncMock(return_value=False),
                               identity=AsyncMock(return_value={"cookies": ["secret"]}))
    monkeypatch.setattr(worker, "launch", AsyncMock(return_value=browser))
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock(side_effect=AdapterError("stop waiting")))
    with pytest.raises(AdapterError):
        asyncio.run(worker.login(db, job, provider, SimpleNamespace(), folder))
    context.storage_state.assert_not_awaited()
    assert not (folder / "storage.json").exists()
    browser.close.assert_awaited_once()


def worker_upload(tmp_path, monkeypatch, *, outcome="accepted", fail_identity=False, mutation=True, http_status=200, ack_error=False,
                  platform="kuaishou", response_body=None, final_endpoints=None):
    """Exercise the production guard, with fake provider/page/network endpoints."""
    db = SocialStore(str(tmp_path), platform)
    target_id = "64477a32000000000800db6b" if platform == 'xiaohongshu' else WORK
    db.create("UPLOAD", OWNER["uid"], PAYLOAD, render={"id": "r", "script_id": "s", "version": 1})
    job = db.claim()
    events = []
    routes = []

    class Response:
        status = http_status

        async def json(self):
            if outcome == "malformed":
                raise ValueError("private-cookie-secret")
            return response_body if response_body is not None else {"response": outcome}

    class Route:
        request = SimpleNamespace(method="POST", url="https://edith.xiaohongshu.com/web_api/sns/v2/note" if platform == 'xiaohongshu'
                                  else "https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit")

        async def fetch(self, **kwargs):
            assert kwargs == {"timeout": 60000, "max_redirects": 0, "max_retries": 0}
            assert db.get(job["id"])["result"]["submission_started"]
            events.append("fetch")
            return Response()

        async def fulfill(self, **kwargs):
            row = db.get(job["id"])
            if outcome == "accepted":
                assert row["work_id"] == target_id and row["result"]["work_id"] == target_id
                assert row["result"]["public"] is False
                events.append("durable_id_before_fulfill")
            elif outcome == 'kuaishou_pending_id':
                assert row['status'] == 'SUBMITTED' and row['result']['accepted'] is True
                assert row['result']['awaiting_work_id'] is True and not row['work_id']
                events.append('durable_acceptance_before_fulfill')
            events.append("fulfill")
            if ack_error:
                raise RuntimeError("The page closed after provider acceptance")

        async def abort(self):
            events.append("abort")

        async def continue_(self):
            events.append("continue")

    class Context:
        async def route(self, pattern, callback):
            assert pattern == "**/*"
            self.callback = callback

    class Browser:
        async def new_context(self, **options):
            assert options["storage_state"] == str(tmp_path / "private-storage.json")
            assert "permissions" not in options
            assert options["service_workers"] == "block"
            return Context()

        async def close(self):
            events.append("close")

    class Provider:
        async def identity(self, context):
            return {**OWNER, "uid": "123"} if fail_identity else OWNER

        def is_submit(self, url):
            if final_endpoints:
                from core.social.adapters.kuaishou import KuaishouAdapter
                return KuaishouAdapter().is_submit(url)
            return mutation and url == Route.request.url

        def submission_receipt(self, data):
            if platform == 'xiaohongshu':
                from core.social.adapters.xiaohongshu import XiaohongshuAdapter
                return XiaohongshuAdapter().submission_receipt(data)
            if outcome == 'kuaishou_pending_id':
                from core.social.adapters.kuaishou import KuaishouAdapter
                return KuaishouAdapter().submission_receipt(data)
            if outcome == "accepted":
                return {"accepted": True, "work_id": WORK, "url": f"https://www.kuaishou.com/short-video/{WORK}", "token": "private-token-secret"}
            if outcome == "rejected":
                return {"accepted": False, "confirmed_not_submitted": True, "code": 42, "token": "private-token-secret"}
            if outcome == "no_id":
                return {"accepted": True}
            raise AdapterError("没有可靠回执，不会自动重投。")

        async def upload(self, pw, payload, storage):
            browser = await pw.chromium.launch(executable_path="ignored", headless=True)
            context = await browser.new_context(storage_state="must-be-overridden", permissions=["camera"])
            if outcome == "window_closed":
                return
            for index in range(2):
                route = Route(); routes.append(route)
                if final_endpoints:
                    route.request = SimpleNamespace(method='POST', url=final_endpoints[index])
                await context.callback(route)
            if not mutation:
                return
            await asyncio.Event().wait()

    monkeypatch.setattr(worker, "launch", AsyncMock(return_value=Browser()))
    asyncio.run(worker.upload(db, job, Provider(), SimpleNamespace(), tmp_path / "private-storage.json"))
    return db, db.get(job["id"]), events


@pytest.mark.parametrize("outcome,expected", [("accepted", "SUBMITTED"), ("rejected", "FAILED"),
    ("no_id", "UNKNOWN"), ("malformed", "UNKNOWN"), ("window_closed", "UNKNOWN"), ("unknown", "UNKNOWN")])
def test_actual_worker_guard_is_once_and_preserves_receipt_before_ack(tmp_path, monkeypatch, outcome, expected):
    db, row, events = worker_upload(tmp_path, monkeypatch, outcome=outcome)
    assert row["status"] == expected
    assert events.count("fetch") == (0 if outcome == "window_closed" else 1)
    assert events.count("close") >= 1
    assert "private-cookie-secret" not in row["error"] and "private-token-secret" not in json.dumps(row["result"])
    if outcome == "accepted":
        assert row["work_id"] == WORK
        assert events.index("durable_id_before_fulfill") < events.index("fulfill")
        assert not row["result"]["public"]
    elif outcome == "rejected":
        assert row["result"]["confirmed_not_submitted"] is True
    else:
        assert not row["result"].get("confirmed_not_submitted")
    assert db.claim() is None


def test_worker_does_not_submit_under_different_actual_account(tmp_path, monkeypatch):
    _, row, events = worker_upload(tmp_path, monkeypatch, fail_identity=True)
    assert events.count("fetch") == 0
    assert row["status"] == "UNKNOWN" and not row["result"].get("confirmed_not_submitted")


@pytest.mark.parametrize('paths', [('isolation/submit', 'submit'), ('submit', 'isolation/submit'),
                                  ('isolation/submit', 'isolation/submit')])
def test_current_and_legacy_kuaishou_submissions_share_one_attempt_and_durable_response(tmp_path, monkeypatch, paths):
    endpoints = tuple('https://cp.kuaishou.com/rest/cp/works/v2/video/pc/' + path for path in paths)
    _, row, events = worker_upload(tmp_path, monkeypatch, outcome='unknown', response_body={'result': 1},
                                  final_endpoints=endpoints)
    assert events.count('fetch') == 1 and events.count('abort') == 1
    assert 'continue' not in events
    assert row['status'] == 'UNKNOWN'
    assert row['result']['submission_started'] is True
    assert row['result']['submission_response']['body'] == {'result': 1}


def test_unrecognized_final_endpoint_never_becomes_success_or_safe_retry(tmp_path, monkeypatch):
    _, row, events = worker_upload(tmp_path, monkeypatch, mutation=False)
    assert events.count("fetch") == 0 and events.count("continue") == 2
    assert row["status"] == "UNKNOWN" and not row["result"].get("confirmed_not_submitted")


@pytest.mark.parametrize("status", [302, 400, 500])
def test_ambiguous_http_response_is_unknown_without_retry_or_redirect(tmp_path, monkeypatch, status):
    _, row, events = worker_upload(tmp_path, monkeypatch, http_status=status)
    assert events.count("fetch") == 1
    assert row["status"] == "UNKNOWN" and not row["result"].get("confirmed_not_submitted")


def test_page_ack_failure_after_durable_receipt_does_not_allow_a_second_submission(tmp_path, monkeypatch):
    _, row, events = worker_upload(tmp_path, monkeypatch, ack_error=True)
    assert row["status"] == "SUBMITTED" and row["work_id"] == WORK
    assert events.count("fetch") == 1 and events.count("fulfill") == 1


@pytest.mark.parametrize('ack_error', [False, True])
def test_kuaishou_real_acceptance_envelope_is_saved_without_requiring_or_inventing_an_id(tmp_path, monkeypatch, ack_error):
    db, row, events = worker_upload(tmp_path, monkeypatch, outcome='kuaishou_pending_id',
        response_body={'result': 1, 'data': {}}, ack_error=ack_error)
    assert row['status'] == 'SUBMITTED' and row['result']['awaiting_work_id'] is True
    assert not row['work_id'] and not row['result']['url'] and not row['result']['public']
    assert events.count('fetch') == 1 and events.count('abort') == 1
    assert events.index('durable_acceptance_before_fulfill') < events.index('fulfill')
    assert db.claim() is None


def test_kuaishou_acceptance_without_id_survives_restart_and_worker_reentry(tmp_path, monkeypatch):
    db = SocialStore(str(tmp_path), 'kuaishou')
    job = db.create('UPLOAD', OWNER['uid'], PAYLOAD, render={'id':'r','script_id':'s','version':1})
    db.claim()
    receipt = {'accepted': True, 'awaiting_work_id': True, 'public': False}
    db.update(job['id'], result=receipt, heartbeat=time.time()-100)
    db.recover()
    assert db.get(job['id'])['status'] == 'SUBMITTED'
    assert db.get(job['id'])['result']['confirmed_not_submitted'] is False
    monkeypatch.setattr(worker,'launch',AsyncMock(side_effect=AssertionError('Never upload an accepted job again')))
    asyncio.run(worker.upload(db, db.get(job['id']), SimpleNamespace(), SimpleNamespace(), tmp_path/'storage'))
    assert db.get(job['id'])['status'] == 'SUBMITTED'
    worker.launch.assert_not_awaited()


def test_creator_receipt_is_saved_before_ack_and_allows_no_second_submission(tmp_path, monkeypatch):
    note = '64477a32000000000800db6b'
    _, row, events = worker_upload(tmp_path, monkeypatch, platform='xiaohongshu', response_body={
        'result': 0, 'data': {'id': note, 'xsec_token': 'private-token-secret'}, 'headers': 'private-cookie-secret',
    })
    assert row['status'] == 'SUBMITTED' and row['work_id'] == note
    assert row['result']['submission_response']['body'] == {'result': 0, 'data': {'id': note}}
    assert events.count('fetch') == 1
    assert events.index('durable_id_before_fulfill') < events.index('fulfill')
    assert 'private-' not in json.dumps(row['result'])


def test_unknown_response_is_durably_summarized_and_delivered_to_page_without_retry(tmp_path, monkeypatch):
    _, row, events = worker_upload(tmp_path, monkeypatch, outcome='unknown', platform='xiaohongshu', response_body={
        'code': 0, 'success': True, 'data': {'new_id': '64477a32000000000800db6b', 'xsec_token': 'private-token-secret'},
        'msg': 'private-cookie-secret',
    })
    assert row['status'] == 'UNKNOWN' and not row['work_id']
    assert row['result']['submission_response']['body'] == {'code': 0, 'success': True, 'data': {}}
    assert row['result']['failure']['stage'] == '解析发布回执'
    assert events.count('fetch') == 1 and events.count('fulfill') == 1 and events.count('abort') == 1
    assert 'private-' not in json.dumps(row['result']) and 'private-' not in row['error']


def test_safe_failure_details_recovers_wrapped_cover_timeout_without_private_message():
    from core.social.adapter import upload_failure_details

    def set_thumbnail():
        raise TimeoutError('https://provider.example/private?token=private-cookie-secret')

    def wrapped():
        try:
            set_thumbnail()
        except Exception:
            raise AdapterError('上传流程未完成') from None

    try:
        wrapped()
    except AdapterError as error:
        details = upload_failure_details(error)
    assert details == {'stage': '设置视频封面', 'error_type': 'TimeoutError'}
    assert 'private-' not in json.dumps(details)


@pytest.mark.parametrize('status_field,value', [('code', False), ('result', '0'), ('success', 1)])
def test_sanitized_evidence_preserves_invalid_status_as_invalid(status_field, value):
    from core.social.adapter import submission_evidence
    from core.social.adapters.xiaohongshu import XiaohongshuAdapter
    original = {'result': 0, 'code': 0, 'success': True, 'data': {'id': '64477a32000000000800db6b'}, status_field: value}
    body = submission_evidence('xiaohongshu', 200, original)['body']
    assert body[status_field] is None
    with pytest.raises(AdapterError):
        XiaohongshuAdapter().submission_receipt(body)


def test_saved_creator_receipt_can_recover_after_fix_without_uploading_or_claiming_public(social_env, monkeypatch):
    from core.social.adapter import submission_evidence
    row = render(); task = enqueue('xiaohongshu', row); db = manager.store('xiaohongshu')
    note = '64477a32000000000800db6b'
    result = {'invocation_started': True, 'submission_started': True,
              'submission_response': submission_evidence('xiaohongshu', 200, {'result': 0, 'data': {'id': note}})}
    db.update(task['id'], status='UNKNOWN', result=result, error='旧解析失败')
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Recovery must not start a worker'))
    manager.settle('xiaohongshu', db)
    recovered = db.get(task['id'])
    assert recovered['status'] == 'SUBMITTED' and recovered['work_id'] == note and not recovered['error']
    assert not recovered['result']['public']
    assert recovered['result']['submission_response'] == result['submission_response']
    assert len(db.list(kind='UPLOAD')) == 1
    assert not video_store.list_publications(row['script_id'])
    manager.settle('xiaohongshu', db)
    assert db.get(task['id'])['status'] == 'SUBMITTED'


def test_unknown_without_saved_receipt_is_preserved_even_after_parser_fix(social_env):
    task = enqueue('xiaohongshu', render()); db = manager.store('xiaohongshu')
    result = {'invocation_started': True, 'submission_started': True}
    db.update(task['id'], status='UNKNOWN', result=result)
    manager.settle('xiaohongshu', db)
    actual = db.get(task['id'])
    assert actual['status'] == 'UNKNOWN' and actual['result'] == result and not actual['work_id']


def test_saved_kuaishou_acceptance_is_recovered_without_reposting_or_requiring_a_public_id(social_env, monkeypatch):
    row=render(); task=enqueue('kuaishou',row); db=manager.store('kuaishou')
    result={'submission_started':True,'submission_response':{'http_status':200,'body':{'result':1,'data':{}}}}
    db.update(task['id'],status='UNKNOWN',result=result,error='旧解析器缺少作品ID')
    monkeypatch.setattr(manager.subprocess,'Popen',lambda *a, **kw: pytest.fail('No reupload during recovery'))
    manager.settle('kuaishou',db)
    recovered=db.get(task['id'])
    assert recovered['status']=='SUBMITTED' and recovered['result']['awaiting_work_id']
    assert not recovered['work_id'] and not recovered['error']
    assert not manager.public_job('kuaishou',recovered)['can_confirm_absent']
    assert not video_store.list_publications(row['script_id'])
    with pytest.raises(AdapterError):
        manager.confirm_absent('kuaishou',task['id'],OWNER['uid'],recovered['updated_at'],'editor')
    with pytest.raises(AdapterError):
        manager.action('kuaishou',task['id'],'retry')


def test_dispatch_uses_fixed_runtime_and_does_not_inherit_provider_or_model_secrets(social_env, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "private-model-secret")
    monkeypatch.setenv("KUAISHOU_COOKIE", "private-cookie-secret")
    spawned = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda args, **kwargs: spawned.append((args, kwargs)))
    job = manager.start_login("kuaishou")
    REAL_TICK("kuaishou")
    args, options = spawned[0]
    assert args[0] == "/fake/python" and args[-1] == job["id"]
    assert "--platform" in args and "kuaishou" in args
    assert not any("secret" in str(value) for value in options["env"].values())
    assert options["start_new_session"] and options["stdin"] == subprocess.DEVNULL
    REAL_TICK("kuaishou")
    assert len(spawned) == 1


def test_manual_record_after_queue_prevents_real_dispatch(social_env, monkeypatch):
    row = render(); job = enqueue("kuaishou", row)
    video_store.add_publication(row["script_id"], version=1, platform="kuaishou", account_uid=OWNER["uid"],
        url=f"https://www.kuaishou.com/short-video/{WORK}", published_on="2026-10-25")
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Must not submit twice"))
    REAL_TICK("kuaishou")
    assert manager.store("kuaishou").get(job["id"])["status"] == "FAILED"
    with pytest.raises(AdapterError):
        manager.action("kuaishou", job["id"], "retry")


def test_social_worker_bootstrap_does_not_require_research_or_model_dependencies(tmp_path):
    output = subprocess.run([sys.executable, "-S", str(Path(worker.__file__).resolve()), "--help"],
                            cwd=tmp_path, env={"PYTHONPATH": str(Path(worker.__file__).resolve().parents[1])},
                            capture_output=True, text=True, timeout=10)
    assert output.returncode == 0, output.stderr
    assert "--platform" in output.stdout and "--state-dir" in output.stdout


def test_api_roles_csrf_and_allowlisted_platforms(social_env, client):
    base = "/api/platforms/social/kuaishou"
    assert client.get(base).status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get(base).status_code == 200
    catalog = client.get("/api/platforms/catalog")
    assert catalog.status_code == 200
    assert {p['id'] for p in catalog.json()['platforms']} == {'bilibili','douyin','kuaishou','xiaohongshu'}
    assert client.post(base + "/login", headers=_csrf(client)).status_code == 403
    assert client.post(base + "/uploads", headers=_csrf(client), json={"render_id": "missing", **PAYLOAD}).status_code == 403
    _login(client, "editor", "editor-pass-123")
    assert client.post(base + "/login", headers=_csrf(client)).status_code == 403
    _login(client, "admin", "admin-pass-123")
    assert client.get('/api/platforms/social/weixin_channels').status_code == 409
    assert client.post('/api/platforms/social/weixin_channels/login', headers=_csrf(client)).status_code == 409
    assert manager.store('weixin_channels').list(kind='LOGIN') == []
    assert client.post(base + "/login").status_code == 403
    result = client.post(base + "/login", headers=_csrf(client))
    assert result.status_code == 202 and "private-cookie-secret" not in result.text
    assert client.get(base + "/login/invalid").status_code in (409, 422)
    assert client.get("/api/platforms/social/not-a-platform").status_code in (404, 409)


def test_editor_can_queue_but_needs_csrf_and_exact_platform_account(social_env, client):
    row = render(); base = "/api/platforms/social/kuaishou"
    _login(client, "editor", "editor-pass-123")
    payload = {"render_id": row["id"], "account_uid": OWNER["uid"], **PAYLOAD}
    assert client.post(base + "/uploads", json=payload).status_code == 403
    assert client.post(base + "/uploads", headers=_csrf(client), json={**payload, "account_uid": "123"}).status_code == 409
    accepted = client.post(base + "/uploads", headers=_csrf(client), json=payload)
    assert accepted.status_code == 202, accepted.text
    assert not any(value in accepted.text for value in ("private-cookie-secret", "video_path", str(social_env)))
    assert client.post(base + "/uploads", headers=_csrf(client), json={**payload, "storage_state": "secret"}).status_code == 422


def test_task_read_api_is_platform_scoped_and_viewer_has_no_mutation_permission(social_env, client):
    job = enqueue("kuaishou", render())
    _login(client, "viewer", "viewer-pass-123")
    base = f"/api/platforms/social/kuaishou/uploads/{job['id']}"
    result = client.get(base)
    assert result.status_code == 200 and result.json()["id"] == job["id"]
    assert client.get(f"/api/platforms/social/xiaohongshu/uploads/{job['id']}").status_code == 409
    for action in ("cancel", "retry", "reconcile"):
        assert client.post(base + "/" + action, headers=_csrf(client), json={}).status_code == 403


def test_queue_reconciliation_does_not_accept_other_account_receipts_or_rewrite_existing_id(social_env):
    job = enqueue("kuaishou", render()); db = manager.store("kuaishou")
    db.update(job["id"], status="UNKNOWN", result={"invocation_started": True})
    check = manager.reconcile("kuaishou", job["id"], WORK)
    db.update(check["id"], status="DONE", result={"uid": "123", "owned": True, "work_id": WORK, "public": True})
    manager.settle("kuaishou", db)
    assert db.get(job["id"])["status"] == "UNKNOWN" and not db.get(job["id"])["work_id"]
    check2 = manager.reconcile("kuaishou", job["id"], WORK)
    db.update(check2["id"], status="DONE", result={"uid": OWNER["uid"], "owned": True, "work_id": WORK,
        "public": False, "url": f"https://www.kuaishou.com/short-video/{WORK}", "check_reason": "尚未确认公开"})
    manager.settle("kuaishou", db)
    assert db.get(job["id"])["status"] == "SUBMITTED" and db.get(job["id"])["work_id"] == WORK
    assert not video_store.list_publications(job["script_id"])
    with pytest.raises(AdapterError):
        manager.reconcile("kuaishou", job["id"], "3xdifferent0000")


@pytest.mark.parametrize("prior,work_id,expected", [
    ({"invocation_started": True}, "", "UNKNOWN"),
    ({"submission_started": True}, "", "UNKNOWN"),
    ({"invocation_started": True}, WORK, "SUBMITTED"),
])
def test_running_worker_reentry_uses_latest_durable_budget_without_invoking_provider(tmp_path, prior, work_id, expected):
    db = SocialStore(str(tmp_path), "kuaishou")
    db.create("UPLOAD", OWNER["uid"], PAYLOAD, render={"id": "r", "script_id": "s", "version": 1})
    stale_job = db.claim()
    db.update(stale_job["id"], result=prior, work_id=work_id)
    provider = SimpleNamespace(upload=AsyncMock(side_effect=AssertionError("Must not invoke provider again")))
    asyncio.run(worker.upload(db, stale_job, provider, SimpleNamespace(), tmp_path / "storage.json"))
    provider.upload.assert_not_awaited()
    assert db.get(stale_job["id"])["status"] == expected
    assert not db.get(stale_job["id"])["result"].get("confirmed_not_submitted")


def test_invalid_combined_package_is_rejected_before_job_creation(social_env):
    row = render()
    with pytest.raises(AdapterError, match="合计"):
        enqueue("kuaishou", row, description="长" * 495)
    assert manager.store("kuaishou").list(kind="UPLOAD") == []


def test_old_invalid_saved_payload_is_not_dispatched_or_marked_unknown(social_env, monkeypatch):
    row = render()
    db = manager.store("kuaishou")
    task = db.create("UPLOAD", OWNER["uid"], {**PAYLOAD, "description": "长" * 495}, render=row)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Invalid payload must not reach worker"))
    REAL_TICK("kuaishou")
    actual = db.get(task["id"])
    assert actual["status"] == "FAILED"
    assert actual["result"]["confirmed_not_submitted"] is True
    assert not actual["result"].get("invocation_started")


def test_active_reconciliation_cannot_silently_keep_an_old_target(social_env):
    task = enqueue("kuaishou", render()); db = manager.store("kuaishou")
    db.update(task["id"], status="UNKNOWN", result={"invocation_started": True})
    first = manager.reconcile("kuaishou", task["id"], WORK)
    assert manager.reconcile("kuaishou", task["id"], WORK)["id"] == first["id"]
    with pytest.raises(AdapterError):
        manager.reconcile("kuaishou", task["id"], "3xdifferent0000")
    assert db.get(first["id"])["payload"]["work_id"] == WORK


def test_queued_cancel_retains_target_and_requires_explicit_safe_retry(social_env):
    row = render(); task = enqueue("kuaishou", row)
    cancelled = manager.action("kuaishou", task["id"], "cancel")
    assert cancelled["status"] == "CANCELLED" and cancelled["can_retry"]
    assert enqueue("kuaishou", row)["status"] == "CANCELLED"
    assert manager.action("kuaishou", task["id"], "retry")["status"] == "QUEUED"
    assert len(manager.store("kuaishou").list(kind="UPLOAD")) == 1


def test_pending_task_prevents_disconnect_or_replacing_its_account(social_env):
    enqueue("kuaishou", render())
    with pytest.raises(AdapterError):
        manager.disconnect("kuaishou")
    with pytest.raises(AdapterError):
        manager.start_login("kuaishou")
    assert manager.account("kuaishou")["uid"] == OWNER["uid"]
    assert manager.credential(manager.directory("kuaishou"), OWNER["uid"]).is_file()


def test_runtime_hash_check_rejects_changed_source_before_import_or_execution(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("raise AssertionError('untrusted source was executed')")
    monkeypatch.setattr(worker.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(source)))
    with pytest.raises(AdapterError, match="已停止"):
        worker.verify_upstream("kuaishou")
