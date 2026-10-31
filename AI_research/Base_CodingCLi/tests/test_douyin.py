"""Douyin contracts and queue verification; no real accounts or publishing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import subprocess
import time
from types import ModuleType, SimpleNamespace

import pytest

from core.douyin import DouyinError, accepted_receipt, profile, public_receipt, work_id
from core.douyin_store import DouyinStore
from core.video import store as video_store
from scripts import douyin_worker as worker
from tests.test_platforms import render
from tests.test_video_scripts import _csrf, _login
from web.services import douyin as manager

WORK = "7559876543210123456"
OWNER = {"uid": "123", "name": "测试创作者", "sec_uid": "sec-test"}
PAYLOAD = {"title": "投稿时的标题", "description": "简介", "tags": ["科技"]}


def detail(**status):
    return {"status_code": 0, "aweme_detail": {"aweme_id": WORK, "author": OWNER,
            "status": {"private_status": 0, "is_delete": False, "is_prohibited": False, "reviewed": 1, **status},
            "create_time": 1792454400, "desc": "作品"}}


@pytest.mark.parametrize("value", [WORK, f"https://www.douyin.com/video/{WORK}?from=test", f"https://www.iesdouyin.com/share/video/{WORK}/", f"https://www.douyin.com/?modal_id={WORK}"])
def test_full_work_ids_stay_strings(value):
    assert work_id(value) == WORK


@pytest.mark.parametrize("value", ["https://v.douyin.com/abc", f"https://evil.test/video/{WORK}", "../../storage.json", "123"])
def test_work_links_do_not_accept_arbitrary_hosts(value):
    with pytest.raises(DouyinError):
        work_id(value)


@pytest.mark.parametrize("data", [None, [], {}, {"status_code": False}, {"status_code": 0.0}, {"status_code": 0, "data": []}, {"status_code": 0, "user": ["secret"]}])
def test_profile_requires_a_verified_identity(data):
    with pytest.raises(DouyinError):
        profile(data)


def test_profile_filters_credentials():
    assert profile({"status_code": 0, "user": {**OWNER, "nickname": OWNER["name"], "token": "secret"}}) == OWNER
    assert profile({"status_code": 0, "data": {"user": {**OWNER, "nickname": OWNER["name"]}}}) == OWNER


def test_standalone_worker_bootstrap_needs_no_research_dependencies(tmp_path):
    # -S removes site-packages, including PyYAML and the application's model SDKs.
    result = subprocess.run([sys.executable, "-S", str(Path(worker.__file__).resolve()), "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--state-dir" in result.stdout


@pytest.mark.parametrize("data", [None, [], {}, {"status_code": False}, {"status_code": 0, "item_id": float(WORK)}, {"status_code": 0, "aweme": []}])
def test_receipt_never_infers_success_from_process_exit_or_inexact_ids(data):
    with pytest.raises(DouyinError):
        accepted_receipt(data)


def test_receipt_is_accepted_but_not_public():
    assert accepted_receipt({"status_code": 0, "aweme": {"aweme_id": WORK}})["public"] is False
    assert accepted_receipt({"status_code": 0, "item_id": int(WORK)})["work_id"] == WORK
    assert accepted_receipt({"status_code": 42, "message": "secret"}) == {"accepted": False, "code": 42}


@pytest.mark.parametrize("status", [{"private_status": 1}, {"reviewed": 0}, {"is_delete": True}, {"is_prohibited": True}, {"private_status": False}, {"reviewed": True}])
def test_private_or_unreviewed_video_cannot_write_publication(status):
    assert public_receipt(detail(**status), WORK, OWNER) is None


def test_public_receipt_requires_exact_work_owner_and_explicit_visibility():
    assert public_receipt(detail(), WORK, OWNER)["evidence"] == "anonymous_video_detail"
    assert public_receipt(detail(), "7559876543210123457", OWNER) is None
    unknown = detail(); unknown["aweme_detail"].pop("status")
    assert public_receipt(unknown, WORK, OWNER) is None
    with pytest.raises(DouyinError, match="不属于"):
        public_receipt(detail(), WORK, {"uid": "456"})


@pytest.fixture
def douyin_env(web_env, monkeypatch):
    tmp, _ = web_env
    monkeypatch.setattr(video_store, "_DB_PATH_OVERRIDE", str(tmp / "state/video.db"))
    video_store.init_db()
    worker.private_json(manager.directory() / "account.json", {**OWNER, "verified_at": time.time()})
    worker.private_json(manager.directory() / "accounts/123/storage.json", {"cookies": [{"name": "sessionid", "value": "secret"}]})
    monkeypatch.setattr(manager, "runtime", lambda: {"ready": True, "source": tmp, "python": Path("/fake/python"), "browsers": tmp})
    monkeypatch.setattr(manager, "tick", lambda: None)
    return tmp


def test_queue_deduplicates_per_account_version_and_claims_once(douyin_env):
    row = render()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: manager.enqueue(row["id"], PAYLOAD), range(10)))
    assert len({j["id"] for j in jobs}) == 1
    db = manager.store()
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: db.claim(), range(6)))
    assert sum(r is not None for r in claims) == 1
    assert "secret" not in json.dumps(manager.overview())
    assert "video_path" not in json.dumps(jobs)
    db.update(jobs[0]["id"], heartbeat=time.time() - 100)
    db.recover()
    assert db.get(jobs[0]["id"])["status"] == "UNKNOWN"
    with pytest.raises(DouyinError, match="结果不明"):
        manager.action(jobs[0]["id"], "retry")


def test_upload_snapshots_both_cover_orientations_for_worker(douyin_env, monkeypatch):
    covers = {'cover': '/test/landscape.jpg', 'cover_portrait': '/test/portrait.jpg'}
    def prepare_covers(render, title, *, portrait):
        assert portrait is True and title == PAYLOAD['title']
        return covers
    monkeypatch.setattr(manager, 'publish_covers', prepare_covers)
    task = manager.enqueue(render()['id'], PAYLOAD)
    stored = manager.store().get(task['id'])
    assert {k: stored['payload'][k] for k in covers} == covers
    assert 'cover_portrait' not in task['payload']  # File paths stay in the local worker payload.


def test_recovery_preserves_a_receipt_and_scope(douyin_env):
    row = render(); db = manager.store()
    job = manager.enqueue(row["id"], PAYLOAD)
    db.claim(); db.update(job["id"], heartbeat=time.time() - 100, work_id=WORK)
    db.recover()
    assert db.get(job["id"])["status"] == "SUBMITTED"
    worker.private_json(manager.directory() / "account.json", {"uid": "456", "name": "另一账号"})
    assert manager.overview()["uploads"] == []
    with pytest.raises(DouyinError):
        manager.upload_task(job["id"])


def test_login_is_reused_and_adopted_without_exposing_storage(douyin_env):
    first = manager.start_login()
    assert manager.start_login()["id"] == first["id"]
    credential = manager.directory() / "jobs" / first["id"] / "storage.json"
    worker.private_json(credential, {"cookies": [{"value": "secret"}]})
    manager.store().update(first["id"], status="DONE", result=OWNER)
    manager._settle(manager.store())
    saved = manager.directory() / "accounts/123/storage.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert manager.login_status(first["id"])["connected"]
    assert not credential.exists()
    assert not (manager.directory() / "login.json").exists()
    assert "secret" not in json.dumps(manager.login_status(first["id"]))
    manager._settle(manager.store())  # idempotent adoption


def test_only_public_receipt_records_the_exact_render_version_once(douyin_env):
    row = render(); job = manager.enqueue(row["id"], PAYLOAD); db = manager.store()
    video_store.add_version(row["script_id"], title="新草稿", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(row["script_id"], status="DRAFT")
    db.update(job["id"], status="SUBMITTED", work_id=WORK, result={"accepted": True, "public": False})
    manager._settle(db)
    assert not video_store.list_publications(row["script_id"])
    check = manager.reconcile(job["id"])
    assert manager.reconcile(job["id"])["id"] == check["id"]
    receipt = public_receipt(detail(), WORK, OWNER)
    db.update(check["id"], status="DONE", result=receipt)
    manager._settle(db); manager._settle(db)
    publications = video_store.list_publications(row["script_id"])
    assert len(publications) == 1
    assert publications[0]["version"] == 1 and publications[0]["platform"] == "douyin"
    assert publications[0]["render_id"] == row["id"]
    assert publications[0]["title"] == PAYLOAD["title"]
    assert video_store.get_script(row["script_id"])["status"] == "DRAFT"
    assert db.get(job["id"])["status"] == "PUBLISHED"


def test_bilibili_published_videos_remain_douyin_candidates(douyin_env):
    row = render()
    video_store.add_publication(row["script_id"], version=1, platform="bilibili", url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-24")
    assert manager.candidates()[0]["render_id"] == row["id"]
    video_store.add_publication(row["script_id"], version=1, platform="douyin", url=f"https://www.douyin.com/video/{WORK}", published_on="2026-10-24")
    assert manager.candidates() == []
    with pytest.raises(DouyinError, match="已记录"):
        manager.enqueue(row["id"], PAYLOAD)


def test_api_roles_csrf_and_private_fields(douyin_env, client):
    assert client.get("/api/platforms/douyin").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/platforms/douyin").status_code == 200
    assert client.post("/api/platforms/douyin/login", headers=_csrf(client)).status_code == 403
    _login(client, "editor", "editor-pass-123")
    assert client.get("/api/platforms/douyin/login/invalid").status_code == 403
    _login(client, "admin", "admin-pass-123")
    assert client.post("/api/platforms/douyin/login").status_code == 403
    result = client.post("/api/platforms/douyin/login", headers=_csrf(client))
    assert result.status_code == 202 and "secret" not in result.text
    assert client.get("/api/platforms/douyin/login/../storage").status_code == 404
    assert client.get("/api/platforms/douyin/login/invalid").status_code == 409


@pytest.mark.parametrize("provider,expected", [({"status_code": 0, "item_id": WORK}, "SUBMITTED"), ({"status_code": 42}, "FAILED"), ({"status_code": 0}, "UNKNOWN"), (None, "FAILED")])
def test_worker_submits_once_persists_receipt_and_closes_its_browser(tmp_path, monkeypatch, provider, expected):
    """Use the real guard around a fake upstream uploader, browser, and responses."""
    db = DouyinStore(str(tmp_path))
    job = db.create("UPLOAD", OWNER["uid"], {**PAYLOAD, "video_path": "/test/video.mp4", "cover": "/test/landscape.jpg",
        "cover_portrait": "/test/portrait.jpg"}, render={"id": "r", "script_id": "s", "version": 1})
    job = db.claim()
    calls = {"fetch": 0, "abort": 0, "close": 0}

    class Response:
        status = 200
        async def json(self):
            return provider

    class Route:
        request = SimpleNamespace(method="POST", url="https://creator.douyin.com/web/api/media/aweme/create_v2/")
        async def fetch(self, **kwargs):
            assert kwargs["max_retries"] == 0
            calls["fetch"] += 1
            return Response()
        async def fulfill(self, **kwargs):
            if provider and provider.get("item_id"):
                assert db.get(job["id"])["work_id"] == WORK
        async def abort(self):
            calls["abort"] += 1

    class Context:
        async def route(self, pattern, callback):
            self.callback = callback

    class Browser:
        async def new_context(self, **options):
            assert options["storage_state"] == "/test/storage.json"
            return Context()
        async def close(self):
            calls["close"] += 1

    async def fake_launch(*a, **kw):
        return Browser()

    async def fake_identity(*a):
        return OWNER

    class Video:
        def __init__(self, **kwargs):
            assert kwargs["declaration"] == "内容由AI生成"
            assert kwargs['thumbnail_portrait_path'] == '/test/portrait.jpg'
            assert kwargs['thumbnail_landscape_path'] == '/test/landscape.jpg'
        async def upload(self, pw):
            browser = await pw.chromium.launch()
            context = await browser.new_context()
            if provider is None:
                return  # A successful process return is not a receipt.
            await context.callback(Route())
            await context.callback(Route())  # Upstream retries cannot send a second POST.
            await asyncio.Event().wait()

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace()

    patchright = ModuleType("patchright.async_api"); patchright.async_playwright = playwright
    upstream = ModuleType("uploader.douyin_uploader.main"); upstream.DouYinVideo = Video
    package = ModuleType("uploader.douyin_uploader"); package.main = upstream
    logger = SimpleNamespace(remove=lambda: None)
    for name, module in {"patchright": ModuleType("patchright"), "patchright.async_api": patchright,
                         "uploader": ModuleType("uploader"), "uploader.douyin_uploader": package,
                         "uploader.douyin_uploader.main": upstream, "loguru": SimpleNamespace(logger=logger)}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker, "launch", fake_launch)
    monkeypatch.setattr(worker, "identity", fake_identity)
    asyncio.run(worker.upload(db, job, Path("/test/storage.json")))
    row = db.get(job["id"])
    assert row["status"] == expected
    assert calls["fetch"] == (0 if provider is None else 1)
    assert calls["close"] == 1
    if provider is not None:
        assert calls["abort"] >= 1
    if expected == "SUBMITTED":
        assert row["work_id"] == WORK and not row["result"]["public"]
    if expected == "UNKNOWN":
        assert not row["result"].get("confirmed_not_submitted")
    if expected == "FAILED":
        assert row["result"]["confirmed_not_submitted"]


REAL_TICK = manager.tick


def test_dispatch_uses_isolated_python_and_no_provider_secrets(douyin_env, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "must-not-inherit")
    spawned = []
    monkeypatch.setattr(manager.subprocess, "Popen", lambda args, **kwargs: spawned.append((args, kwargs)))
    login = manager.start_login()
    REAL_TICK()
    args, options = spawned[0]
    assert args[0] == "/fake/python" and args[-1] == login["id"]
    assert "must-not-inherit" not in json.dumps(options["env"])
    assert options["start_new_session"] and options["stdin"] == manager.subprocess.DEVNULL
    assert manager.store().get(login["id"])["status"] == "RUNNING"
    REAL_TICK()
    assert len(spawned) == 1


def test_manual_record_after_queueing_prevents_actual_dispatch(douyin_env, monkeypatch):
    row = render(); job = manager.enqueue(row["id"], PAYLOAD)
    video_store.add_publication(row["script_id"], version=1, platform="douyin", url=f"https://www.douyin.com/video/{WORK}", published_on="2026-10-24")
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not publish twice"))
    REAL_TICK()
    assert manager.store().get(job["id"])["status"] == "FAILED"
    with pytest.raises(DouyinError, match="已记录"):
        manager.action(job["id"], "retry")


def test_account_lock_prevents_disconnect_and_wrong_reconciliation(douyin_env):
    job = manager.enqueue(render()["id"], PAYLOAD)
    with pytest.raises(DouyinError, match="正在运行"):
        manager.disconnect()
    manager.store().update(job["id"], status="SUBMITTED", work_id=WORK)
    with pytest.raises(DouyinError, match="不能替换"):
        manager.reconcile(job["id"], "7559876543210123457")
    check = manager.reconcile(job["id"])
    manager.store().update(check["id"], status="DONE", result={"uid": "another", "work_id": WORK, "public": True})
    manager._settle(manager.store())
    assert not video_store.list_publications(job["script_id"])


@pytest.mark.parametrize("identity_ok", [True, False])
def test_login_owns_and_closes_browser_and_saves_only_verified_accounts(tmp_path, monkeypatch, identity_ok):
    db = DouyinStore(str(tmp_path)); job = db.create("LOGIN"); job = db.claim()
    folder = tmp_path / "jobs" / job["id"]; folder.mkdir(parents=True)
    upstream_image = folder / "upstream.png"
    closed = []

    class Context:
        async def new_page(self):
            async def goto(url, **kwargs):
                assert url == "https://creator.douyin.com/"
            return SimpleNamespace(goto=goto)
        async def storage_state(self, path):
            Path(path).write_text('{"cookies":[{"value":"test-secret"}]}')

    class Browser:
        async def new_context(self):
            return Context()
        async def close(self):
            closed.append(True)

    async def launch(*a, **kwargs):
        assert kwargs["headless"] is False
        return Browser()

    async def init(context):
        return context

    async def qr(page, path, qrcode_callback):
        upstream_image.write_bytes(b"test-png")
        await qrcode_callback({"image_path": str(upstream_image)})
        return {"image_path": str(upstream_image)}

    async def wait(*a, **kwargs):
        assert kwargs["max_checks"] == 100
        return {"success": True}

    async def identity(*a):
        if not identity_ok:
            raise DouyinError("实际账号未能验证")
        return OWNER

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace()

    upstream = ModuleType("uploader.douyin_uploader.main")
    upstream.set_init_script = init; upstream._save_douyin_qrcode = qr; upstream._wait_for_douyin_login = wait
    package = ModuleType("uploader.douyin_uploader"); package.main = upstream
    api = ModuleType("patchright.async_api"); api.async_playwright = playwright
    for name, module in {"uploader": ModuleType("uploader"), "uploader.douyin_uploader": package,
                         "uploader.douyin_uploader.main": upstream, "patchright": ModuleType("patchright"),
                         "patchright.async_api": api, "loguru": SimpleNamespace(logger=SimpleNamespace(remove=lambda: None))}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker, "launch", launch); monkeypatch.setattr(worker, "identity", identity)
    if identity_ok:
        asyncio.run(worker.login(db, job, folder))
        assert db.get(job["id"])["result"] == OWNER
        assert db.get(job["id"])["status"] == "DONE"
        assert (folder / "storage.json").stat().st_mode & 0o777 == 0o600
    else:
        with pytest.raises(DouyinError, match="实际账号"):
            asyncio.run(worker.login(db, job, folder))
        assert not (folder / "storage.json").exists()
    assert len(closed) == 1 and not upstream_image.exists()
    assert (folder / "qrcode.png").stat().st_mode & 0o777 == 0o600


def test_missing_worker_dependency_leaves_a_durable_failure(tmp_path):
    db = DouyinStore(str(tmp_path)); job = db.create("LOGIN"); db.claim()
    result = subprocess.run([sys.executable, "-S", str(Path(worker.__file__).resolve()),
                             "--state-dir", str(tmp_path), "--job", job["id"]],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    row = db.get(job["id"])
    assert row["status"] == "FAILED" and "ModuleNotFoundError" in row["error"]
