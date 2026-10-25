"""Opt-in first comments use isolated stores and fake APIs; no live comments are sent."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest

from core.platform_store import PlatformStore
from tests.test_platforms import BV, CREDENTIAL, PAYLOAD, comment, platform_env, render
from tests.test_video_scripts import _csrf, _login
from web.services import bilibili, platform_manager as manager, video_render

FIRST = {"enabled": True, "text": "固定说明\n欢迎交流。", "pin": True}


def make_task(monkeypatch, *, published=True, first=FIRST):
    task = manager.enqueue(render()["id"], {**PAYLOAD, "first_comment": first})
    manager.store().update_upload(task["id"], status="PUBLISHED" if published else "SUBMITTED", bvid=BV)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"aid": 1234, "state": 0})
    return manager.store().get_upload(task["id"])


def test_first_comment_is_opt_in_and_snapshot_does_not_follow_template_changes(platform_env, monkeypatch):
    old = make_task(monkeypatch, first={})
    task = make_task(monkeypatch)
    manager.store().save_settings({"first_comment": {**FIRST, "text": "后续新内容"}})
    same = manager.enqueue(task["render_id"], {**PAYLOAD, "first_comment": {**FIRST, "text": "重复请求"}})
    assert same["id"] == task["id"] and same["payload"]["first_comment"] == FIRST
    assert old["first_comment_status"] == "DISABLED"
    assert task["first_comment_status"] == "WAITING"
    assert task["first_comment_pin_status"] == "WAITING"
    assert len(manager.store().first_comment_tasks("123")) == 1


@pytest.mark.parametrize("state", [-30, -4, None])
def test_comments_wait_for_publication_even_when_a_bvid_is_known(platform_env, monkeypatch, state):
    task = make_task(monkeypatch, published=False)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"aid": 1, "state": state})
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("must wait for public video"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "WAITING"


def test_comment_then_pin_and_repeated_worker_passes_never_resend(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    calls = []

    def post(self, info, text):
        assert manager.store().get_upload(task["id"])["first_comment_status"] == "SENDING"
        calls.append(("send", text))
        return "987654321123456789"

    def pin(self, info, rpid):
        saved = manager.store().get_upload(task["id"])
        assert saved["first_comment_status"] == "SENT" and saved["first_comment_rpid"] == rpid
        calls.append(("pin", rpid))

    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", post)
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", pin)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: manager.tick(), range(4)))
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert saved["status"] == "PUBLISHED" and saved["first_comment_status"] == "SENT"
    assert saved["first_comment_pin_status"] == "PINNED"
    assert calls == [("send", FIRST["text"]), ("pin", "987654321123456789")]


def test_comment_can_be_sent_without_pinning(platform_env, monkeypatch):
    task = make_task(monkeypatch, first={**FIRST, "pin": False})
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: "101")
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", lambda *args: pytest.fail("pin not selected"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "SENT"
    assert manager.store().get_upload(task["id"])["first_comment_pin_status"] == "DISABLED"


def test_unknown_send_has_no_retry_and_preserves_publication(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    calls = []

    def post(*args):
        calls.append(1)
        raise TimeoutError("private-session-secret")

    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", post)
    manager.tick()
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert saved["status"] == "PUBLISHED" and saved["first_comment_status"] == "UNKNOWN"
    assert len(calls) == 1 and "secret" not in saved["first_comment_error"]
    with pytest.raises(manager.Error, match="待核对"):
        manager.retry_first_comment(task["id"])


def test_explicit_rejection_can_be_retried_only_by_request(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    calls = []

    def post(*args):
        calls.append(1)
        raise bilibili.BilibiliRejected("B 站接口返回 12015")

    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", post)
    manager.tick()
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "FAILED" and len(calls) == 1
    manager.retry_first_comment(task["id"])
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: "101")
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", lambda *args: None)
    with manager.store().connect(write=True) as conn:
        conn.execute("UPDATE uploads SET first_comment_updated_at=0 WHERE id=?", (task["id"],))
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "SENT"


@pytest.mark.parametrize("failure", [bilibili.BilibiliRejected("B 站接口返回 12029"), TimeoutError("private")])
def test_pin_failure_retry_never_resends_confirmed_comment(platform_env, monkeypatch, failure):
    task = make_task(monkeypatch)
    calls = []

    def pin(*args):
        calls.append("pin")
        if len(calls) == 2:
            raise failure

    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: calls.append("send") or "101")
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", pin)
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert saved["first_comment_status"] == "SENT" and saved["first_comment_rpid"] == "101"
    assert saved["first_comment_pin_status"] in ("FAILED", "UNKNOWN")
    manager.tick()
    assert calls == ["send", "pin"]
    manager.retry_first_comment(task["id"], pin=True)
    manager.tick()
    assert calls == ["send", "pin", "pin"]
    assert manager.store().get_upload(task["id"])["first_comment_pin_status"] == "PINNED"


def test_recovery_does_not_resend_a_comment_interrupted_after_remote_write(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    assert manager.store().claim_first_comment(task["id"], "123")
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("unknown outcome"))
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "UNKNOWN"


def test_recovery_after_comment_id_saved_only_pins(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    db = manager.store()
    assert db.claim_first_comment(task["id"], "123")
    db.finish_first_comment(task["id"], status="SENT", rpid="101")
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("already sent"))
    calls = []
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", lambda *args: calls.append(args[-1]))
    manager.tick()
    assert calls == ["101"]


def test_first_comment_and_viewer_replies_share_cooldown(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    db = manager.store()
    db.save_comments("123", BV, [comment()])
    assert db.claim_first_comment(task["id"], "123")
    assert not db.claim_reply("123", BV, "101", "回复")
    db.finish_first_comment(task["id"], status="SENT", rpid="999")
    assert not db.claim_reply("123", BV, "101", "回复")
    with db.connect(write=True) as conn:
        conn.execute("UPDATE uploads SET first_comment_updated_at=0")
    assert db.claim_reply("123", BV, "101", "回复")
    another = make_task(monkeypatch)
    assert not db.claim_first_comment(another["id"], "123")
    db.finish_reply("123", BV, "101", reply_rpid="888")
    assert not db.claim_first_comment(another["id"], "123")


def test_templates_are_cleared_on_account_switch(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    manager.store().save_settings({"first_comment": FIRST})
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda *args: {"uid": "999", "name": "另一账号"})
    manager.connect({**CREDENTIAL, "dedeuserid": "999"})
    assert manager.store().settings()["first_comment"]["enabled"] is False
    assert not manager.store().first_comment_tasks("999")
    with pytest.raises(manager.Error):
        manager.retry_first_comment(task["id"], pin=True)


@pytest.mark.parametrize("first", [{**FIRST, "text": "  "}, {**FIRST, "text": "字" * 1001}])
def test_invalid_first_comment_is_rejected_before_upload(platform_env, first):
    with pytest.raises(manager.Error, match="1–1000"):
        manager.enqueue(render()["id"], {**PAYLOAD, "first_comment": first})
    assert not manager.store().uploads("123")


def test_template_save_does_not_send_or_change_existing_tasks(platform_env, client, monkeypatch):
    task = make_task(monkeypatch, first={})
    _login(client, "admin", "admin-pass-123")
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("saving is not sending"))
    response = client.put("/api/platforms/bilibili/first-comment-template", json=FIRST, headers=_csrf(client))
    assert response.status_code == 200
    monkeypatch.setattr(video_render, "publish_pack", lambda *args, **kwargs: {**PAYLOAD, "warnings": []})
    monkeypatch.setattr(manager, "column_for", lambda *args: object())
    assert manager.preview(render()["id"])["first_comment"] == FIRST
    manager.tick()
    assert manager.store().get_upload(task["id"])["first_comment_status"] == "DISABLED"
    # Older clients updating unrelated settings do not discard the saved template.
    result = client.put("/api/platforms/bilibili/settings", json={"auto_upload": False, "sync_comments": False, "tid": 95}, headers=_csrf(client))
    assert result.status_code == 200 and result.json()["first_comment"] == FIRST


def test_first_comment_endpoints_enforce_roles_csrf_and_no_send_on_save(platform_env, client):
    _login(client, "viewer", "viewer-pass-123")
    endpoint = "/api/platforms/bilibili/first-comment-template"
    assert client.put(endpoint, json=FIRST, headers=_csrf(client)).status_code == 403
    _login(client)
    assert client.put(endpoint, json=FIRST, headers=_csrf(client)).status_code == 403
    _login(client, "admin", "admin-pass-123")
    assert client.put(endpoint, json=FIRST).status_code == 403
    assert client.put(endpoint, json={**FIRST, "text": " "}, headers=_csrf(client)).status_code == 409


def test_post_comment_and_pin_send_the_confirmed_resource_and_csrf_only(platform_env, monkeypatch):
    original = httpx.Client
    requests = []

    def respond(req):
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        assert req.method == "POST" and form["csrf"] == "test-csrf-secret"
        assert form["oid"] == "1234" and form["type"] == "1"
        requests.append(req.url.path)
        if req.url.path.endswith("add"):
            assert form["message"] == FIRST["text"] and "parent" not in form and "root" not in form
            return httpx.Response(200, json={"code": 0, "data": {"reply": {"rpid": 987654321123456789}}})
        assert form["rpid"] == "987654321123456789" and form["action"] == "1"
        return httpx.Response(200, json={"code": 0})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    api = manager.client("123")
    rpid = api.post_comment({"aid": 1234}, FIRST["text"])
    api.pin_comment({"aid": 1234}, rpid)
    assert requests == ["/x/v2/reply/add", "/x/v2/reply/top"]


def test_existing_database_migration_never_enables_comments_for_old_uploads(tmp_path):
    path = tmp_path / "platforms.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE uploads (id TEXT PRIMARY KEY,uid TEXT,render_id TEXT,script_id TEXT,version INTEGER,"
                     "payload TEXT,status TEXT,bvid TEXT,error TEXT,created_at REAL,updated_at REAL)")
        conn.execute("INSERT INTO uploads VALUES ('old','123','r','s',1,?,'PUBLISHED',?,'',1,1)", (json.dumps(PAYLOAD), BV))
    db = PlatformStore(str(tmp_path))
    assert db.get_upload("old")["first_comment_status"] == "DISABLED"
    assert db.first_comment_tasks("123") == []


def test_automatic_upload_snapshots_the_explicit_common_comment(platform_env, monkeypatch):
    manager.store().save_settings({"first_comment": FIRST, "auto_upload": True, "tid": 188, "auto_since": 0})
    render()
    monkeypatch.setattr(manager, "column_for", lambda *args: object())
    monkeypatch.setattr(video_render, "publish_pack", lambda *args, **kwargs: {**PAYLOAD, "warnings": []})
    monkeypatch.setattr(manager.credibility, "label", lambda *args: {"tier": "good", "text": "已核实"})
    monkeypatch.setattr(bilibili, "upload", lambda *args: BV)
    manager.tick()
    task = manager.store().uploads("123")[0]
    assert task["payload"]["automatic"] and task["payload"]["first_comment"] == FIRST
    assert task["status"] == "SUBMITTED" and task["first_comment_status"] == "WAITING"


def test_crash_during_pin_is_unknown_and_only_pin_can_be_retried(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    db = manager.store()
    assert db.claim_first_comment(task["id"], "123")
    db.finish_first_comment(task["id"], status="SENT", rpid="101")
    assert db.claim_first_comment_pin(task["id"], "123")
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("already sent"))
    monkeypatch.setattr(bilibili.BilibiliClient, "pin_comment", lambda *args: pytest.fail("must not automatically retry"))
    manager.tick()
    task = db.get_upload(task["id"])
    assert task["first_comment_status"] == "SENT" and task["first_comment_pin_status"] == "UNKNOWN"
    assert manager.retry_first_comment(task["id"], pin=True)["first_comment_pin_status"] == "WAITING"


def test_first_comment_must_recheck_owned_public_video_before_sending(platform_env, monkeypatch):
    task = make_task(monkeypatch)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: (_ for _ in ()).throw(manager.Error("账号归属未确认")))
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("owner not confirmed"))
    manager.tick()
    task = manager.store().get_upload(task["id"])
    assert task["first_comment_status"] == "WAITING" and "归属" in task["first_comment_error"]


def test_api_queues_the_comment_but_never_sends_in_the_request(platform_env, client, monkeypatch):
    row = render()
    _login(client)
    monkeypatch.setattr(bilibili.BilibiliClient, "post_comment", lambda *args: pytest.fail("request only enqueues"))
    endpoint = "/api/platforms/bilibili/uploads"
    body = {**PAYLOAD, "render_id": row["id"], "first_comment": FIRST}
    assert client.post(endpoint, json=body, headers=_csrf(client)).status_code == 202
    task = manager.store().uploads("123")[0]
    assert task["first_comment_status"] == "WAITING" and not task["first_comment_rpid"]
    retry = f"{endpoint}/{task['id']}/first-comment/retry"
    assert client.post(retry, headers=_csrf(client)).status_code == 409
    _login(client, "viewer", "viewer-pass-123")
    assert client.post(retry, headers=_csrf(client)).status_code == 403


def test_database_migration_is_safe_for_concurrent_first_open(tmp_path):
    # A separate database is intentionally opened by concurrent API/worker constructors.
    path = tmp_path / "db"
    path.mkdir()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: PlatformStore(str(path)).settings(), range(4)))
    assert all(row["first_comment"]["enabled"] is False for row in results)


def test_disconnected_account_can_save_disabled_automation_policy(platform_env, client):
    manager.disconnect()
    _login(client, "admin", "admin-pass-123")
    result = client.put("/api/platforms/bilibili/settings", headers=_csrf(client), json={
        "auto_upload": False, "sync_comments": False, "tid": 0,
        "first_comment": {"enabled": False, "text": "", "pin": False},
    })
    assert result.status_code == 200
