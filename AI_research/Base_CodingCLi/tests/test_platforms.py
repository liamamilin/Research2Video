"""Platform contracts use fake providers and isolated account/video stores; never publish real content."""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

from core.platform_store import PlatformStore
from core.video import store as video_store
from tests.test_video_scripts import _csrf, _login
from web.services import bilibili, platform_manager as manager, video_render

BV = "BV1xx411c7mD"
CREDENTIAL = {"sessdata": "test-session-secret", "bili_jct": "test-csrf-secret", "dedeuserid": "123"}
PAYLOAD = {"title": "测试视频", "description": "简介", "tags": ["科技"], "tid": 188}


@pytest.fixture
def platform_env(web_env, monkeypatch):
    tmp, _ = web_env
    monkeypatch.setattr(video_store, "_DB_PATH_OVERRIDE", str(tmp / "state/video.db"))
    video_store.init_db()
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda self: {"uid": "123", "name": "测试UP主", "verified_at": time.time()})
    monkeypatch.setattr(bilibili, "biliup_binary", lambda: "/tmp/fake-biliup")
    monkeypatch.setattr(bilibili, "prepare_cover", lambda video, folder, provided="": video)
    manager.connect(CREDENTIAL)
    return tmp


def render():
    script = video_store.create_script("flash", {"title": "测试视频"})
    video_store.add_version(script["id"], title="测试视频", sections=[], length_check={}, edit_type="GENERATED", meta={})
    video_store.update_script(script["id"], status="APPROVED", approved_version=1)
    row = video_store.create_render(script["id"], 1, title="测试视频", source_text="内容", source_sha256="a" * 64, request={})
    video_store.update_render(row["id"], status="COMPLETED", completed_at=datetime.now(timezone.utc).isoformat(), artifacts=[{"name": "video.mp4"}])
    folder = video_render.files_dir(row["id"])
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"test-video")
    return video_store.get_render(row["id"])


def comment(rpid="101", root="", mid="456"):
    return {"rpid": rpid, "root": root, "mid": mid, "member": {"uname": "观众"},
            "content": {"message": "请问怎么操作？"}, "ctime": 123, "like": 2}


def test_credentials_accept_both_upstream_formats_and_are_private(platform_env):
    data = bilibili.normalize_credentials(CREDENTIAL)
    assert bilibili.normalize_credentials(data) == data
    assert {c["name"] for c in data["cookie_info"]["cookies"]} == {"SESSDATA", "bili_jct", "DedeUserID"}
    path = manager.directory() / "credentials.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "secret" not in json.dumps(manager.account())
    assert not manager.account()["upload_authorized"]


def native_credentials():
    return {**bilibili.normalize_credentials(CREDENTIAL), "platform": "BiliTV",
            "token_info": {"access_token": "test-access-secret", "refresh_token": "test-refresh-secret", "expires_in": 3600, "mid": 123}}


def test_native_upload_authorization_is_preserved_and_bound_to_the_cookie_owner():
    data = native_credentials()
    assert bilibili.normalize_credentials(data) == data
    assert bilibili.upload_authorized(data)
    data["token_info"]["mid"] = 999
    with pytest.raises(bilibili.BilibiliError, match="账号 ID 不一致"):
        bilibili.normalize_credentials(data)


def test_tv_qr_login_returns_upload_tokens_without_exposing_them(platform_env, monkeypatch):
    original = httpx.Client
    requests = []

    def respond(req):
        from urllib.parse import parse_qs, urlencode
        import hashlib
        requests.append(req)
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        signature = form.pop("sign")
        assert signature == hashlib.md5((urlencode(sorted(form.items())) + bilibili.TV_APPSEC).encode()).hexdigest()
        assert form["appkey"] == bilibili.TV_APPKEY
        assert req.method == "POST"
        if req.url.path.endswith("auth_code"):
            return httpx.Response(200, json={"code": 0, "data": {"auth_code": "test-qr-secret", "url": "https://example.test/qr"}})
        assert form["auth_code"] == "test-qr-secret"
        if len(requests) == 2:
            return httpx.Response(200, json={"code": 86039})
        return httpx.Response(200, json={"code": 0, "data": native_credentials()})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    path = manager.directory() / "login.json"
    started = bilibili.start_login(path)
    assert set(started) == {"status", "image"} and started["status"] == "waiting"
    assert bilibili.poll_login(path) == {"status": "waiting"}
    finished = bilibili.poll_login(path)
    assert finished["status"] == "done" and finished["credentials"] == native_credentials()
    assert path.stat().st_mode & 0o777 == 0o600


def test_web_only_upload_fails_before_invoking_the_cli(platform_env, monkeypatch):
    task = manager.enqueue(render()["id"], PAYLOAD)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("no upload without native authorization"))
    manager.tick()
    row = manager.store().get_upload(task["id"])
    assert row["status"] == "FAILED" and "缺少上传授权" in row["error"]
    assert not video_store.list_publications(row["script_id"])
    evidence = json.loads((manager.directory() / "uploads" / task["id"] / "diagnostic.json").read_text())
    assert evidence["confirmed_not_submitted"] is True


@pytest.mark.parametrize("data", [{}, {**CREDENTIAL, "sessdata": "abc\nsecret"}, {**CREDENTIAL, "dedeuserid": "not-uid"}])
def test_bad_credentials_are_rejected(data):
    with pytest.raises(bilibili.BilibiliError):
        bilibili.normalize_credentials(data)


def test_enqueue_is_atomic_and_preserves_original_metadata(platform_env):
    row = render()
    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = list(executor.map(lambda _: manager.enqueue(row["id"], PAYLOAD), range(10)))
    assert len({job["id"] for job in jobs}) == 1
    same = manager.enqueue(row["id"], {**PAYLOAD, "title": "后改标题"})
    assert same["payload"]["title"] == "测试视频"
    assert manager.candidates() == []


def test_candidates_retain_render_version_and_title(platform_env):
    row = render()
    candidate = manager.candidates()[0]
    assert candidate["render_id"] == row["id"] and candidate["version"] == 1
    assert candidate["title"] == "测试视频"


def test_two_renders_of_the_same_version_create_only_one_publication_task(platform_env):
    first = render()
    second = video_store.create_render(first["script_id"], 1, title="重渲染", source_text="内容", source_sha256="a" * 64, request={})
    video_store.update_render(second["id"], status="COMPLETED", artifacts=[{"name": "video.mp4"}],
                              completed_at=datetime.now(timezone.utc).isoformat())
    folder = video_render.files_dir(second["id"])
    folder.mkdir()
    (folder / "video.mp4").write_bytes(b"second-video")
    assert len(manager.candidates()) == 1
    one = manager.enqueue(first["id"], PAYLOAD)
    two = manager.enqueue(second["id"], PAYLOAD)
    assert one["id"] == two["id"]


def test_account_change_during_a_worker_pass_is_refused(platform_env):
    with manager.account_lock():
        with pytest.raises(bilibili.BilibiliError, match="正在运行"):
            manager.disconnect()
    assert manager.account()["connected"]


def test_cover_failure_stays_retryable_without_starting_upload(platform_env, monkeypatch):
    task = manager.enqueue(render()["id"], PAYLOAD)
    monkeypatch.setattr(bilibili, "prepare_cover", lambda *args: (_ for _ in ()).throw(bilibili.BilibiliError("缺少 ffmpeg")))
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("must not submit without a cover"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["status"] == "FAILED"


def test_manual_upload_keeps_completed_videos_when_the_script_is_edited(platform_env):
    row = render()
    video_store.update_script(row["script_id"], status="DRAFT")
    assert manager.enqueue(row["id"], PAYLOAD)["status"] == "QUEUED"
    assert video_store.get_script(row["script_id"])["status"] == "DRAFT"
    with pytest.raises(bilibili.BilibiliError, match="自动投稿"):
        manager.enqueue(row["id"], PAYLOAD, automatic=True)
    (video_render.files_dir(row["id"]) / "video.mp4").unlink()
    with pytest.raises(bilibili.BilibiliError, match="成片文件"):
        manager.enqueue(row["id"], PAYLOAD)


def test_manual_catalog_includes_archived_and_historical_versions(platform_env):
    old = render()
    video_store.add_version(old["script_id"], title="后续新稿", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(old["script_id"], archived=1, status="DRAFT")
    choices, summary = manager.candidate_catalog()
    assert len(choices) == 1 and choices[0]["render_id"] == old["id"]
    assert choices[0]["archived"] == 1 and choices[0]["current_version"] == 2
    assert choices[0]["version"] == 1
    assert summary == {"completed_renders": 1, "available_versions": 1, "published_versions": 0,
                       "queued_versions": 0, "unavailable_versions": 0, "ready_versions": 1}
    assert manager.enqueue(old["id"], PAYLOAD)["status"] == "QUEUED"


def test_manual_catalog_explains_published_queued_and_missing_files(platform_env):
    published, queued, missing = render(), render(), render()
    video_store.add_publication(published["script_id"], version=1, render_id=published["id"],
                                url=f"https://www.bilibili.com/video/{BV}", published_on="2026-10-24")
    manager.enqueue(queued["id"], PAYLOAD)
    (video_render.files_dir(missing["id"]) / "video.mp4").unlink()
    choices, summary = manager.candidate_catalog()
    assert choices == []
    assert summary["published_versions"] == summary["queued_versions"] == summary["unavailable_versions"] == 1


def test_catalog_uses_an_older_available_render_if_latest_file_is_missing(platform_env):
    old = render()
    newer = video_store.create_render(old["script_id"], 1, title="重渲染", source_text="内容", source_sha256="a" * 64, request={})
    video_store.update_render(newer["id"], status="COMPLETED", completed_at="2099-01-01T00:00:00+00:00", artifacts=[{"name": "video.mp4"}])
    choices, summary = manager.candidate_catalog()
    assert len(choices) == 1 and choices[0]["render_id"] == old["id"]
    assert summary["completed_renders"] == 2 and summary["available_versions"] == 1


def test_manual_catalog_does_not_truncate_after_one_hundred_versions(platform_env):
    rows = [render() for _ in range(101)]
    assert {row["render_id"] for row in manager.candidates()} == {row["id"] for row in rows}


def test_archived_draft_and_old_videos_stay_out_of_automatic_publishing(platform_env, monkeypatch):
    archived, draft, old = render(), render(), render()
    video_store.update_script(archived["script_id"], archived=1)
    video_store.update_script(draft["script_id"], status="DRAFT")
    video_store.add_version(old["script_id"], title="新版", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(old["script_id"], status="APPROVED", approved_version=2)
    manager.store().save_settings({"auto_upload": True, "tid": 188, "auto_since": 0})
    monkeypatch.setattr(manager, "preview", lambda *args: pytest.fail("manual-only versions must not enter auto publishing"))
    assert len(manager.candidates()) == 3
    manager.tick()
    assert manager.store().uploads("123") == []


def test_automatic_queue_rechecks_the_current_version_before_upload(platform_env, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD, automatic=True)
    video_store.update_script(row["script_id"], status="DRAFT")
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("must not auto-upload an edited script"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["status"] == "FAILED"


def test_historical_preview_uses_its_actual_version_and_render(platform_env, monkeypatch):
    row = render()
    video_store.add_version(row["script_id"], title="新版标题", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(row["script_id"], status="DRAFT", archived=1)
    monkeypatch.setattr(manager, "column_for", lambda script: object())
    calls = []

    def pack(script, version, column, *, render_id):
        calls.append((version["version"], render_id))
        return {**PAYLOAD, "title": version["title"], "warnings": [], "freshness": {"warnings": []}}

    monkeypatch.setattr(video_render, "publish_pack", pack)
    preview = manager.preview(row["id"])
    assert calls == [(1, row["id"])] and preview["title"] == "测试视频"
    assert any("当前为 v2" in warning for warning in preview["warnings"])
    assert any("归档" in warning for warning in preview["warnings"])


def test_upload_worker_does_not_duplicate_unknown_submissions(platform_env, monkeypatch):
    row = render()
    job = manager.enqueue(row["id"], PAYLOAD)
    calls = []

    def interrupted(*args):
        calls.append(args)
        raise TimeoutError("private upstream output")

    monkeypatch.setattr(bilibili, "upload", interrupted)
    manager.tick()
    manager.tick()
    task = manager.store().get_upload(job["id"])
    assert task["status"] == "UNKNOWN" and len(calls) == 1
    assert "private upstream" not in task["error"]
    assert video_store.list_publications(row["script_id"]) == []


def test_interrupted_inflight_upload_is_not_resubmitted(platform_env, monkeypatch):
    job = manager.enqueue(render()["id"], PAYLOAD)
    manager.store().update_upload(job["id"], status="UPLOADING")
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("must not upload"))
    manager.tick()
    assert manager.store().get_upload(job["id"])["status"] == "UNKNOWN"


def test_submission_is_recorded_only_after_it_is_public_and_owned(platform_env, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD)
    monkeypatch.setattr(bilibili, "upload", lambda *args: BV)
    manager.tick()
    assert manager.store().get_upload(task["id"])["status"] == "SUBMITTED"
    assert not video_store.list_publications(row["script_id"])
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": "测试视频", "aid": 1, "state": -4})
    assert manager.reconcile(task["id"], BV)["status"] == "SUBMITTED"
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": "测试视频", "aid": 1, "state": 0})
    assert manager.reconcile(task["id"], BV)["status"] == "PUBLISHED"
    publications = video_store.list_publications(row["script_id"])
    assert len(publications) == 1 and publications[0]["render_id"] == row["id"]
    assert manager.store().videos("123")[0]["bvid"] == BV


def test_platform_publication_updates_the_document_and_list_once_with_platform_date(platform_env, client, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD)
    manager.store().update_upload(task["id"], status="SUBMITTED", bvid=BV)
    published_at = datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": "投稿时编辑的标题", "state": 0, "pubdate": published_at})
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: manager.reconcile(task["id"], BV), range(5)))
    assert all(result["status"] == "PUBLISHED" for result in results)
    _login(client)
    detail = client.get(f"/api/video/scripts/{row['script_id']}").json()
    assert len(detail["publications"]) == 1
    publication = detail["publications"][0]
    assert publication["version"] == 1 and publication["render_id"] == row["id"]
    assert publication["published_on"] == "2026-10-20" and publication["title"] == "投稿时编辑的标题"
    assert publication["recorded_by"] == "platform-manager"
    assert detail["platform_uploads"][0]["id"] == task["id"]
    assert client.get("/api/video/scripts").json()[0]["published"] is True
    # A manual repeat of the same video, with a tracking query, shares the record.
    again = video_store.add_publication(row["script_id"], version=1, url=f"https://www.bilibili.com/video/{BV}?from=manual", published_on="2026-10-24")
    assert again["id"] == publication["id"] and len(video_store.list_publications(row["script_id"])) == 1


def test_publishing_a_history_version_does_not_mark_the_current_draft_published(platform_env, client, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD)
    video_store.add_version(row["script_id"], title="新版草稿", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(row["script_id"], status="DRAFT")
    manager.store().update_upload(task["id"], status="SUBMITTED", bvid=BV)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"state": 0})
    manager.reconcile(task["id"], BV)
    _login(client)
    summary = client.get("/api/video/scripts").json()[0]
    assert summary["current_version"] == 2 and not summary["published"]
    assert video_store.get_script(row["script_id"])["status"] == "DRAFT"
    assert video_store.list_publications(row["script_id"])[0]["version"] == 1


def test_a_missing_publication_state_is_not_treated_as_public(platform_env, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD)
    manager.store().update_upload(task["id"], status="SUBMITTED", bvid=BV)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": "状态尚未确认"})
    assert manager.reconcile(task["id"], BV)["status"] == "SUBMITTED"
    assert not video_store.list_publications(row["script_id"])


def test_manual_publication_prevents_a_second_submission(platform_env):
    row = render()
    video_store.add_publication(row["script_id"], version=1, url=f"https://www.bilibili.com/video/{BV}", published_on="2026-10-24")
    with pytest.raises(bilibili.BilibiliError, match="已经记录发布"):
        manager.enqueue(row["id"], PAYLOAD)


def test_manual_record_added_after_queueing_stops_the_upload(platform_env, monkeypatch):
    row = render()
    task = manager.enqueue(row["id"], PAYLOAD)
    video_store.add_publication(row["script_id"], version=1, url=f"https://www.bilibili.com/video/{BV}", published_on="2026-10-24")
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("must not publish a manually recorded video again"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["status"] == "FAILED"


def test_manual_publication_uses_the_selected_render_version_and_rejects_mismatches(platform_env, client):
    row = render()
    video_store.add_version(row["script_id"], title="第二版", sections=[], length_check={}, edit_type="EDITED", meta={})
    video_store.update_script(row["script_id"], approved_version=2)
    _login(client)
    endpoint = f"/api/video/scripts/{row['script_id']}/publications"
    body = {"url": f"https://www.bilibili.com/video/{BV}", "render_id": row["id"]}
    assert client.post(endpoint, json={**body, "version": 2}, headers=_csrf(client)).status_code == 422
    assert not video_store.list_publications(row["script_id"])
    made = client.post(endpoint, json=body, headers=_csrf(client))
    assert made.status_code == 201 and made.json()["version"] == 1


def test_document_upload_queries_are_bound_to_account_and_not_the_overview_limit(platform_env, client):
    task = manager.enqueue(render()["id"], PAYLOAD)
    with manager.store().connect(write=True) as connection:
        for number in range(201):
            connection.execute("INSERT INTO uploads (id,uid,render_id,script_id,version,payload,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?, ?,?)",
                               (f"later{number}", "123", f"render{number}", f"script{number}", 1, json.dumps(PAYLOAD), "CANCELLED", time.time() + number, time.time()))
        connection.execute("INSERT INTO uploads (id,uid,render_id,script_id,version,payload,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           ("foreign", "456", "foreign", task["script_id"], 1, json.dumps(PAYLOAD), "QUEUED", time.time(), time.time()))
    assert task["id"] not in {row["id"] for row in manager.store().uploads("123")}
    assert [row["id"] for row in manager.store().pending_uploads("123")] == [task["id"]]
    assert [row["id"] for row in manager.script_uploads(task["script_id"])] == [task["id"]]
    _login(client)
    assert client.get(f"/api/platforms/bilibili/uploads/{task['id']}").json()["id"] == task["id"]
    assert client.get("/api/platforms/bilibili/uploads/foreign").status_code == 409


def test_switching_accounts_pauses_automation_and_keeps_outboxes_separate(platform_env, monkeypatch):
    job = manager.enqueue(render()["id"], PAYLOAD)
    manager.store().save_settings({"auto_upload": True, "sync_comments": True, "tid": 188})
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda self: {"uid": "999", "name": "另一个账号"})
    manager.connect({**CREDENTIAL, "dedeuserid": "999"})
    assert not manager.store().settings()["auto_upload"]
    assert not manager.store().settings()["sync_comments"]
    assert manager.overview()["uploads"] == []
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("old account must not upload"))
    manager.tick()
    assert manager.store().get_upload(job["id"])["status"] == "QUEUED"


def test_auto_upload_skips_existing_videos_and_quality_holds(platform_env, monkeypatch):
    old = render()
    manager.store().save_settings({"auto_upload": True, "tid": 188})
    made = render()
    manager.store().save_settings({"auto_since": time.time() - 1})
    video_store.update_render(old["id"], completed_at="2020-01-01T00:00:00+00:00")
    pack = {**PAYLOAD, "credibility": {"tier": "verify"}, "warnings": [], "freshness_warnings": []}
    monkeypatch.setattr(manager, "preview", lambda _: pack)
    calls = []
    monkeypatch.setattr(bilibili, "upload", lambda *args: calls.append(args) or BV)
    manager.tick()
    assert not manager.store().uploads("123")
    pack["credibility"] = {"tier": "trusted"}
    manager.tick()
    assert len(calls) == 1
    assert manager.store().uploads("123")[0]["render_id"] == made["id"]


def test_duplicate_sync_preserves_drafts_and_handled_status(platform_env):
    db = manager.store()
    db.watch("123", BV, "测试")
    assert db.save_comments("123", BV, [comment()]) == 1
    db.edit_comment("123", BV, "101", draft="稍后详细解释", handled=True)
    assert db.save_comments("123", BV, [comment()]) == 0
    db.save_comments("123", BV, [{**comment(), "like": 7}])
    row = db.get_comment("123", BV, "101")
    assert row["status"] == "HANDLED" and row["draft"] == "稍后详细解释"
    assert row["likes"] == 7


def test_one_broken_video_does_not_starve_other_comment_syncs(platform_env, monkeypatch):
    db = manager.store()
    db.watch("123", BV, "A坏视频")
    second = "BV1yy411c7mD"
    db.watch("123", second, "B正常视频")
    db.save_settings({"sync_comments": True})

    def owned(self, bvid, owner):
        if bvid == BV:
            raise bilibili.BilibiliError("视频暂不可用")
        return {"aid": 2, "title": "B正常视频"}

    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", owned)
    monkeypatch.setattr(bilibili.BilibiliClient, "comments", lambda *args: ([comment()], False))
    with pytest.raises(bilibili.BilibiliError):
        manager.tick()
    manager.tick()
    assert db.get_comment("123", second, "101")
    assert db.videos("123")[0]["sync_error"] == "视频暂不可用"


def test_sync_skips_self_comments_and_supports_older_pages(platform_env, monkeypatch):
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"aid": 1, "title": "测试"})
    monkeypatch.setattr(bilibili.BilibiliClient, "comments", lambda self, info, page: ([comment(str(page)), comment("999", mid="123")], True))
    assert manager.sync(BV, 2) == {"added": 1, "page": 2, "has_more": True, "root": ""}
    assert manager.store().get_comment("123", BV, "2")
    assert manager.store().get_comment("123", BV, "999") is None


def test_thread_reply_uses_root_and_parent_and_is_not_repeated(platform_env, monkeypatch):
    db = manager.store()
    db.save_comments("123", BV, [comment("102", root="101")])
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"aid": 777})
    requests = []

    def fake_request(self, path, **kwargs):
        requests.append((path, kwargs))
        return {"rpid": 999}, httpx.Cookies()

    monkeypatch.setattr(bilibili.BilibiliClient, "request", fake_request)
    assert manager.send_reply(BV, "102", "这是操作方法。")["status"] == "REPLIED"
    data = requests[0][1]["data"]
    assert data["root"] == "101" and data["parent"] == "102" and data["oid"] == 777
    with pytest.raises(bilibili.BilibiliError):
        manager.send_reply(BV, "102", "重复发送")
    assert len(requests) == 1


def test_unknown_reply_does_not_get_sent_again(platform_env, monkeypatch):
    db = manager.store()
    db.save_comments("123", BV, [comment()])
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"aid": 1})
    monkeypatch.setattr(bilibili.BilibiliClient, "reply", lambda *args: (_ for _ in ()).throw(TimeoutError("secret")))
    with pytest.raises(bilibili.BilibiliError, match="未确认"):
        manager.send_reply(BV, "101", "一次回复")
    assert db.get_comment("123", BV, "101")["status"] == "UNKNOWN"
    assert not db.claim_reply("123", BV, "101", "重复回复")


def test_stale_reply_does_not_block_the_whole_inbox(platform_env):
    db = manager.store()
    db.save_comments("123", BV, [comment(), comment("102")])
    assert db.claim_reply("123", BV, "101", "回复")
    with db.connect(write=True) as conn:
        conn.execute("UPDATE comments SET updated_at=? WHERE rpid='101'", (time.time() - 130,))
    assert db.claim_reply("123", BV, "102", "另一个回复")
    assert db.get_comment("123", BV, "101")["status"] == "UNKNOWN"


def test_qr_poll_never_returns_credentials_to_browser(platform_env, client, monkeypatch):
    _login(client, "admin", "admin-pass-123")
    monkeypatch.setattr(bilibili, "poll_login", lambda path: {"status": "done", "credentials": bilibili.normalize_credentials(CREDENTIAL)})
    response = client.post("/api/platforms/bilibili/login/poll", headers=_csrf(client))
    assert response.status_code == 200 and response.json() == {"status": "done"}
    assert "secret" not in response.text


def test_role_and_csrf_checks_cover_external_actions(platform_env, client, monkeypatch):
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/platforms/bilibili").status_code == 200
    assert client.post("/api/platforms/bilibili/videos", json={"bvid": BV}, headers=_csrf(client)).status_code == 403
    _login(client)
    assert client.post("/api/platforms/bilibili/credentials", json={"credentials": CREDENTIAL}, headers=_csrf(client)).status_code == 403
    assert client.put("/api/platforms/bilibili/settings", json={"auto_upload": False, "sync_comments": False, "tid": 188}, headers=_csrf(client)).status_code == 403
    assert client.post("/api/platforms/bilibili/sync", json={"bvid": BV}).status_code == 403


def test_api_rejects_empty_reply_and_foreign_task(platform_env, client):
    _login(client)
    response = client.post(f"/api/platforms/bilibili/comments/{BV}/101/reply", json={"text": "  "}, headers=_csrf(client))
    assert response.status_code == 422
    response = client.post("/api/platforms/bilibili/uploads/foreign/cancel", headers=_csrf(client))
    assert response.status_code == 409


def test_biliup_arguments_use_web_submission_and_a_private_cookie_snapshot(platform_env, monkeypatch):
    calls = []

    def execute(args, **kwargs):
        snapshot = json.loads(__import__("pathlib").Path(args[2]).read_text())
        assert snapshot["cookie_info"]["cookies"]
        assert "test-session-secret" not in str(args)
        assert kwargs["capture_output"]
        calls.append(args)
        if args[3] == "list":
            assert kwargs["timeout"] == 45
            return subprocess.CompletedProcess(args, 0, b'', b'')
        assert kwargs["timeout"] == 7200
        return subprocess.CompletedProcess(args, 0, b'', f'ResponseData {{ code: 0, data: Some(Object {{"bvid": String("{BV}")}}), message: "0" }}'.encode())

    monkeypatch.setattr(subprocess, "run", execute)
    row = render()
    result = bilibili.upload(native_credentials(), video_render.safe_file(row, "video.mp4"), PAYLOAD, manager.directory())
    assert result == BV
    assert calls[1][3:6] == ["upload", "--submit", "web"]
    assert "--" in calls[1] and "--tid" in calls[1]


def test_native_preflight_error_is_actionable_private_and_never_uploads(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task = manager.enqueue(render()["id"], PAYLOAD)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        assert args[3] == "list"
        return subprocess.CompletedProcess(args, 1, b'test-access-secret',
            b'Error: Unknown Error\ncredential validation failed with Bilibili code -400\ntest-session-secret')

    monkeypatch.setattr(subprocess, "run", execute)
    manager.tick()
    manager.tick()
    row = manager.store().get_upload(task["id"])
    assert row["status"] == "FAILED" and "-400" in row["error"] and len(calls) == 1
    evidence = (manager.directory() / "uploads" / task["id"] / "diagnostic.json").read_text()
    assert "secret" not in evidence + row["error"]
    assert json.loads(evidence)["phase"] == "preflight"


def test_cli_file_arguments_survive_changing_to_a_relative_work_directory(platform_env, monkeypatch):
    from pathlib import Path
    row = render()
    monkeypatch.chdir(platform_env)
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        assert Path(args[2]).is_absolute() and Path(args[2]).is_file()
        assert kwargs["cwd"].is_absolute()
        if args[3] == "list":
            return subprocess.CompletedProcess(args, 0, b'', b'')
        assert Path(args[args.index("--cover") + 1]).is_absolute()
        assert Path(args[-1]).is_absolute()
        return subprocess.CompletedProcess(args, 0, b'', f'ResponseData {{ code: 0, data: Some(Object {{"bvid": String("{BV}")}}) }}'.encode())

    monkeypatch.setattr(subprocess, "run", execute)
    bilibili.upload(native_credentials(), video_render.safe_file(row, "video.mp4"), PAYLOAD, Path("relative-task"))
    assert len(calls) == 2


def test_native_token_refresh_survives_a_later_read_only_failure(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task = manager.enqueue(render()["id"], PAYLOAD)

    def execute(args, **kwargs):
        from pathlib import Path
        assert args[3] == "list"
        refreshed = native_credentials()
        refreshed["token_info"]["access_token"] = "rotated-access-secret"
        refreshed["token_info"]["refresh_token"] = "rotated-refresh-secret"
        bilibili.private_json(Path(args[2]), refreshed)
        return subprocess.CompletedProcess(args, 1, b'', b'error sending request')

    monkeypatch.setattr(subprocess, "run", execute)
    manager.tick()
    assert manager.store().get_upload(task["id"])["status"] == "FAILED"
    assert manager.credentials()["token_info"]["refresh_token"] == "rotated-refresh-secret"


@pytest.mark.parametrize("output, expected", [
    ("Error: Unknown Error\n未知平台\ntest-access-secret", "未知平台"),
    ('ResponseData { code: -400, message: "test-access-secret" }', "错误码 -400"),
    ('open cookies file: /private/test-secret\nNo such file or directory (os error 2)', "找不到登录凭证文件"),
])
def test_cli_diagnostics_only_retain_known_reasons_and_codes(output, expected):
    reason = bilibili.cli_diagnostic(output)
    assert expected in reason and "secret" not in reason


def test_sanitized_diagnostics_preserve_previous_attempts(platform_env):
    folder = manager.directory()
    bilibili.upload_diagnostic(folder, {"phase": "preflight", "reason": "缺少授权"})
    bilibili.upload_diagnostic(folder, {"phase": "submitted", "bvid": BV})
    assert json.loads((folder / "diagnostic.json").read_text())["bvid"] == BV
    history = list(folder.glob("diagnostic-*.json"))
    assert len(history) == 1 and json.loads(history[0].read_text())["reason"] == "缺少授权"
    assert history[0].stat().st_mode & 0o777 == 0o600


def test_failure_after_native_upload_starts_remains_unknown(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task = manager.enqueue(render()["id"], PAYLOAD)
    calls = []

    def execute(args, **kwargs):
        calls.append(args[3])
        return subprocess.CompletedProcess(args, 0 if args[3] == "list" else 1, b'', b'error sending request test-refresh-secret')

    monkeypatch.setattr(subprocess, "run", execute)
    manager.tick()
    manager.tick()
    row = manager.store().get_upload(task["id"])
    assert row["status"] == "UNKNOWN" and calls == ["list", "upload"]
    assert "连接 B 站失败或超时" in row["error"] and "secret" not in row["error"]


def test_adapter_rejects_foreign_video_and_redacts_api_errors(monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"code": -101, "message": "secret-session"}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(bilibili.BilibiliError) as error:
        bilibili.BilibiliClient().request("/x/web-interface/nav")
    assert "-101" in str(error.value) and "secret-session" not in str(error.value)
    monkeypatch.setattr(bilibili.BilibiliClient, "request", lambda *args, **kwargs: ({"owner": {"mid": 999}}, httpx.Cookies()))
    with pytest.raises(bilibili.BilibiliError, match="当前登录账号"):
        bilibili.BilibiliClient().owned_video(BV, "123")


def test_categories_use_the_creator_catalog_and_only_expose_selectable_names(monkeypatch):
    def respond(request):
        assert str(request.url) == "https://member.bilibili.com/x/vupre/web/archive/pre"
        assert request.headers["referer"] == "https://member.bilibili.com/"
        return httpx.Response(200, json={"code": 0, "data": {"myinfo": {"private": "not-returned"}, "typelist": [
            {"id": 36, "name": "知识", "children": [
                {"id": 201, "name": "科学科普", "desc": "科学知识分享", "show": True},
                {"id": 202, "name": "已隐藏分区", "show": False},
                {"id": "bad", "name": "无效编号"},
            ]},
            {"id": 201, "name": "重复分区"},
        ]}})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    assert bilibili.BilibiliClient().categories() == [{"tid": 201, "name": "科学科普", "path": "知识 / 科学科普", "description": "科学知识分享"}]


def test_empty_category_response_is_actionable_and_redacted(monkeypatch):
    monkeypatch.setattr(bilibili.BilibiliClient, "request", lambda *args, **kwargs: ({"myinfo": {"secret": "private"}}, httpx.Cookies()))
    with pytest.raises(bilibili.BilibiliError, match="暂未返回可投稿分区") as error:
        bilibili.BilibiliClient().categories()
    assert "private" not in str(error.value)


def test_category_cache_survives_outages_without_crossing_accounts(platform_env, monkeypatch):
    calls = []
    rows = [{"tid": 201, "name": "科学科普", "path": "知识 / 科学科普", "description": "科学知识分享"}]
    monkeypatch.setattr(bilibili.BilibiliClient, "categories", lambda self: (calls.append(1) or rows))
    fresh = manager.categories()
    assert fresh["categories"] == rows and not fresh["stale"]
    assert manager.categories() == fresh and len(calls) == 1
    path = manager.directory() / "categories-123.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "secret" not in path.read_text()
    cached = json.loads(path.read_text())
    cached["updated_at"] = time.time() - 7 * 3600
    bilibili.private_json(path, cached)
    monkeypatch.setattr(bilibili.BilibiliClient, "categories", lambda self: (_ for _ in ()).throw(bilibili.BilibiliError("连接失败")))
    stale = manager.categories()
    assert stale["stale"] and stale["categories"] == rows
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda self: {"uid": self.cookies["DedeUserID"], "name": "新账号"})
    manager.connect({**CREDENTIAL, "dedeuserid": "456"})
    with pytest.raises(bilibili.BilibiliError, match="连接失败"):
        manager.categories()


def test_category_api_requires_login_and_returns_names(platform_env, client, monkeypatch):
    assert client.get("/api/platforms/bilibili/categories").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    rows = [{"tid": 201, "name": "科学科普", "path": "知识 / 科学科普", "description": ""}]
    monkeypatch.setattr(bilibili.BilibiliClient, "categories", lambda self: rows)
    response = client.get("/api/platforms/bilibili/categories")
    assert response.status_code == 200 and response.json()["categories"] == rows
    assert "secret" not in response.text
